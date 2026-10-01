#!/usr/bin/env node
// swing_ai.js — 스윙트레이더 AI 결정 엔진
// 1) swing-trader.py 실행 (기존 리포트 생성)  2) LLM이 결정 JSON 생성
// 3) 검증·클램프·브레이크 후 swing_ai_overrides.json 저장  4) 최종 출력 (리포트 + AI 판단 + 결정 이력)
//
// 2026-10-01 개조 (래칫 사고 대응):
//   · 래칫 브레이크 — 모든 조정은 "주간학습 기준값" 대비 ±20%(부호 파라미터 ±0.15) 이내로 제한.
//     기존엔 이전 틱 값을 기준으로 ±30%를 반복 허용 → max_hold_days가 16일→3일까지 단조 하향,
//     되돌아온 적 없음(실측). 하루 6틱 × 수일 누적 = 체제 편향 래칫.
//   · 클램프/브레이크 발동을 출력에 ⚠️로 명시 (AI가 자기 결정이 잘린 걸 모르고 재요청하던 문제).
//   · 결정 이력(history) 저장 → 어떤 틱이 무엇을 왜 바꿨는지 감사 가능.
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const SCRIPTS = path.join(os.homedir(), '.hermes/scripts');
const REPORTER = path.join(SCRIPTS, 'swing-trader.py');
const OVERRIDES = path.join(SCRIPTS, 'swing_ai_overrides.json');
const WEEKLY_PARAMS = path.join(SCRIPTS, 'swing_params.json');
const BASE_URL = process.env.OPENCODEGO_API_BASE_URL || 'https://opencode.ai/zen/go/v1/chat/completions';
const MODEL = 'deepseek-v4.1-flash'; // 크론 결정 엔진: 추론 0 → 항상 응답 확보. K3 금지, k2.6은 추론 폭주

// 파라미터 클램프 범위 (AI 폭주 방지 — 절대 안전 상한/하한)
const CLAMPS = {
  score_min: [1, 15],
  price_min: [0.5, 50],
  max_hold_days: [3, 60],
  stop_mult: [0.5, 3.0],
  target_mult: [0.5, 5.0],
  base_position_mult: [0.3, 5.0],
  aggression_bias: [-1.0, 1.0],
  vix_floor: [10, 25],
  vix_ceiling: [20, 50],
  aggr_5d_bull: [0, 10],
  aggr_5d_bear: [-10, 0],
};

// ─── 래칫 브레이크 설정 ───
const BRAKE_PCT = 0.20;   // 주간학습 기준값 대비 최대 ±20% (곱셈형 파라미터)
const BRAKE_ABS = 0.15;   // 부호 파라미터(aggression_bias 등) 절대 ±0.15
const ABS_PARAMS = new Set(['aggression_bias', 'aggr_5d_bull', 'aggr_5d_bear']);

function apiKey() {
  if (process.env.OPENCODEGO_API_KEY) return process.env.OPENCODEGO_API_KEY;
  try {
    const env = fs.readFileSync(path.join(os.homedir(), '.hermes/.env'), 'utf8');
    const m = env.match(/^OPENCODE_GO_API_KEY=(.+)$/m);
    if (m) return m[1].trim();
  } catch {}
  try {
    const env = fs.readFileSync('/home/ubuntu/hermesbot/.env', 'utf8');
    const m = env.match(/^OPENCODEGO_API_KEY=(.+)$/m);
    if (m) return m[1].trim();
  } catch {}
  return '';
}

function todayUTC() {
  return new Date().toISOString().slice(0, 10);
}

// 주간학습 기준값 (swing_params.json) — 브레이크의 기준점
function readWeeklyParams() {
  try {
    const j = JSON.parse(fs.readFileSync(WEEKLY_PARAMS, 'utf8'));
    return j.params || {};
  } catch {
    return {};
  }
}

function readOverridesFile() {
  try {
    return JSON.parse(fs.readFileSync(OVERRIDES, 'utf8')) || {};
  } catch {
    return {};
  }
}

// 지금 실제로 적용 중인 값 = 주간 기준값 + 당일 오버라이드
function currentEffective() {
  const base = readWeeklyParams();
  const ov = readOverridesFile();
  const out = { ...base };
  if (ov.date === todayUTC() && ov.params) Object.assign(out, ov.params);
  return out;
}

async function askLLM(prompt) {
  const key = apiKey();
  if (!key) throw new Error('API key not found');
  let lastErr;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const res = await fetch(BASE_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${key}`,
          // OpenCode Go 세션 어피니티 (MissingSessionID 400 방지)
          'x-opencode-session': 'hermes-cron-swing-ai',
        },
        body: JSON.stringify({
          model: MODEL,
          messages: [
            { role: 'system', content: '너는 모의투자 파라미터 결정 AI다. 추론 과정은 출력하지 말고, 바로 결정만 출력해라. JSON 응답만 생성한다.' },
            { role: 'user', content: prompt }
          ],
          max_tokens: 8000,
          temperature: 0.3,
          reasoning_effort: 'medium',
        }),
      });
      if (!res.ok) throw new Error(`LLM HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
      const data = await res.json();
      const content = data.choices?.[0]?.message?.content || '';
      if (!content) {
        const u = data.usage || {};
        const r = u.completion_tokens_details?.reasoning_tokens ?? '-';
        throw new Error(`빈 응답 (추론만 출력됨: ${u.completion_tokens}토큰, 추론 ${r})`);
      }
      return content;
    } catch (e) {
      lastErr = e;
      if (attempt < 3) await new Promise(r => setTimeout(r, 3000 * attempt));
    }
  }
  throw lastErr;
}

function extractDecision(text) {
  // ```json ... ``` 블록 찾기 (없으면 마지막 { ... } 시도)
  let m = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  let raw = m ? m[1] : null;
  if (!raw) {
    const start = text.lastIndexOf('{');
    const end = text.lastIndexOf('}');
    if (start >= 0 && end > start) raw = text.slice(start, end + 1);
  }
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function clampAndValidate(params) {
  const out = {};
  const clamped = [];
  for (const [k, [lo, hi]] of Object.entries(CLAMPS)) {
    if (params[k] === undefined || params[k] === null) continue;
    const raw = Number(params[k]);
    if (!Number.isFinite(raw)) continue;
    const v = Math.min(hi, Math.max(lo, raw));
    out[k] = v;
    if (v !== raw) clamped.push(`${k}: 요청 ${raw} → 클램프 ${v} (허용 ${lo}~${hi})`);
  }
  return { clean: out, clamped };
}

// ─── 래칫 브레이크: 주간학습 기준값 대비 ±20%(부호 파라미터 ±0.15) ───
function applyBrake(clean, baseline) {
  const applied = {};
  const adjustments = [];
  for (const [k, v] of Object.entries(clean)) {
    const rawBase = baseline ? baseline[k] : undefined;
    if (rawBase === undefined || rawBase === null || !Number.isFinite(Number(rawBase))) {
      applied[k] = v;
      continue;
    }
    const bn = Number(rawBase);
    const isAbs = ABS_PARAMS.has(k);
    const lo = isAbs ? bn - BRAKE_ABS : bn * (1 - BRAKE_PCT);
    const hi = isAbs ? bn + BRAKE_ABS : bn * (1 + BRAKE_PCT);
    if (v < lo || v > hi) {
      let nv = Math.min(hi, Math.max(lo, v));
      nv = Number.isInteger(v) ? Math.round(nv) : Number(nv.toFixed(3));
      applied[k] = nv;
      adjustments.push(`${k}: 요청 ${v} → 적용 ${nv} (기준값 ${bn}, 한계 ${isAbs ? `±${BRAKE_ABS}` : `±${BRAKE_PCT * 100}%`})`);
    } else {
      applied[k] = v;
    }
  }
  return { applied, adjustments };
}

function fmtParams(p) {
  if (!p) return '(없음)';
  const keys = ['max_hold_days', 'score_min', 'price_min', 'stop_mult', 'target_mult', 'base_position_mult', 'aggression_bias', 'vix_floor', 'vix_ceiling'];
  return keys.filter(k => p[k] !== undefined)
    .map(k => `${k}=${typeof p[k] === 'number' ? Number(p[k].toFixed ? p[k].toFixed(3) : p[k]) : p[k]}`)
    .join(', ');
}

async function main() {
  // 1) 기존 리포트 생성 (= 이 시점 매매 집행 완료)
  let report = '';
  try {
    report = execFileSync('python3', [REPORTER], { cwd: SCRIPTS, timeout: 360000, encoding: 'utf8' }).trim();
  } catch (e) {
    report = `(리포트 생성 실패: ${e.message.split('\n')[0]})`;
  }
  if (!report) report = '(리포트 출력 없음)';

  // 2) LLM 결정 요청
  const baseline = readWeeklyParams();
  const effective = currentEffective();
  const prompt = `너는 모의투자 스윙트레이더의 AI 결정권자다. 아래 리포트를 분석해서 (A) 해석과 (B) 파라미터 결정을 낸다.

[현재 파라미터 — 이 숫자가 기준이다]
· 주간학습 기준값(브레이크 기준점): ${fmtParams(baseline)}
· 현재 적용값(오늘 AI 조정까지 반영): ${fmtParams(effective)}

[조정 한계 — 하드 룰, 위반하면 시스템이 자동으로 잘라낸다]
- 모든 조정은 "주간학습 기준값" 대비 ±20% 이내. aggression_bias는 ±0.15 절대 이내.
- 이전 틱 값 대비가 아니다. 하루에 6번 실행돼도 누적 조정은 기준값 ±20%를 넘을 수 없다.
- 무리한 값을 요청하지 말 것. 잘려서 적용되므로 의미가 없고, 이력을 오염시킨다.

[분석 프레임워크 — 반드시 적용]
1) 6요소 확률 스코어링 (각 /10, 가중합 → 확률%):
   - 추세(MA20/50/120) 25% · 모멘텀(RSI/MACD) 20% · 위치(BB%) 15%
   - 볼륨(5d/20d) 10% · 이벤트(실적/뉴스) 15% · 섹터 15%
2) 실적 직전 종목(earnings within 2w): score_min ↑, aggression_bias ↓, max_hold_days ↓
   ⚠️ 실적 임박 판단은 리포트의 [실적 일정] 섹션만 근거로 삼을 것. 뉴스 헤드라인("어닝콜 하이라이트" 등)은
   이미 끝난 발표의 후기일 수 있으므로 임박 근거로 절대 사용 금지. [실적 일정]에 ⚠️실적 임박 표기가 없으면
   실적 이벤트 리스크는 '없음'으로 간주하고 그 이유로 파라미터를 바꾸지 말 것.
3) 섹터 순환매 감지 시: 해당 섹터 exposure 파라미터 조정
4) 레버리지 ETF underlying: p>50% 확인, 아니면 관련 파라미터 보수 전환
5) MA5/20 cross = 스윙 타임프레임 신호. 50/200은 무시.

[판단 근거 — 반드시 [성과 지표] 섹션을 인용할 것]
- 리포트의 [성과 지표]에 전체/최근 승률·PF·기대값·청산사유·평균보유가 있다. 이걸 인용해서 판단해라.
- 최근 10건 거래내역만 보고 판단하지 말 것 (표본 편향). 전체 지표와 최근 20건 지표를 함께 본다.
- 파라미터를 조일 때는 "이 조정이 기대값/PF를 개선할 근거"를 명시해야 한다.

[금기 — 과거 오판 재발 방지]
- "청산이 시간 초과 때문이다"라는 이유로 max_hold_days를 더 줄이지 말 것. 보유기간은 결과이고,
  원인은 진입 품질(RSI 저점 롱 + 시장 폭 약화)일 수 있다. 시간 초과 비중이 높다는 사실만으로는
  max_hold_days 조정 근거가 되지 않는다.
- 승률·기대값이 기준값(주간학습)보다 좋은데도 이유 없이 보수화하지 말 것.

[출력 형식 — 반드시 이 순서, 600자 이내]
1) "🤖 AI 판단:" 섹션 — 핵심 요약, 동의/반대, 리스크 체크 (한국어 반말). [성과 지표] 수치를 최소 1회 인용.
2) 마지막에 \`\`\`json 블록으로 결정 (키 이름 정확히 "params"):
\`\`\`json
{"params": {"max_hold_days": <int>, "stop_mult": <float>, "target_mult": <float>, "score_min": <int>, "base_position_mult": <float>, "aggression_bias": <float>, "vix_floor": <float>, "vix_ceiling": <float>, "note": "<조정 사유 한 줄>"}}
\`\`\`
바꿀 게 없으면 현재 값 그대로 넣어라. 리포트가 비었거나 결정 불가면 {"params":{}}만 출력해라.

[리포트]
${report.slice(0, 9000)}`;

  let aiText = '';
  try {
    aiText = await askLLM(prompt);
  } catch (e) {
    aiText = `🤖 AI 판단: (AI 호출 실패 — ${e.message}) 결정 없이 기존 파라미터 유지.`;
  }

  // 3) 결정 추출·검증·클램프·브레이크·저장
  let applied = [];
  let brakeNotes = [];
  const decision = extractDecision(aiText);
  if (decision && decision.params) {
    const { clean, clamped } = clampAndValidate(decision.params);
    const { applied: braked, adjustments } = applyBrake(clean, baseline);
    brakeNotes = [...clamped, ...adjustments];
    if (Object.keys(braked).length > 0) {
      const old = readOverridesFile();
      const prev = {};
      if (old.date === todayUTC()) Object.assign(prev, old.params || {});
      const hist = Array.isArray(old.history) ? old.history.slice(-299) : [];
      hist.push({
        ts: new Date().toISOString(),
        model: MODEL,
        requested: clean,
        applied: braked,
        adjusted: brakeNotes,
        note: String(decision.params.note || '').slice(0, 200),
        ai_summary: aiText.replace(/\s+/g, ' ').slice(0, 300),
      });
      const merged = { date: todayUTC(), params: { ...prev, ...braked }, history: hist };
      if (old._note) merged._note = old._note;
      fs.writeFileSync(OVERRIDES, JSON.stringify(merged, null, 2));
      applied = Object.entries(braked).map(([k, v]) => `${k}=${v}`);
    }
  }

  // 4) 최종 출력
  console.log(buildTail(report, aiText, applied, brakeNotes));
}

// 최종 출력 조립 (테스트 가능하도록 분리)
function buildTail(report, aiText, applied, brakeNotes) {
  let out = report;
  if (!out.includes('🤖 AI 판단')) {
    out += '\n\n---\n' + aiText.trim();
  }
  if (applied.length) {
    out += `\n\n⚙️ **AI 결정 반영 완료** (${todayUTC()}): ${applied.join(', ')}`;
  } else if (!aiText.includes('실패')) {
    out += '\n\n⚙️ AI 결정: 파라미터 유지 (변경 없음)';
  }
  if (brakeNotes.length) {
    out += `\n\n⚠️ **브레이크 발동 — 요청이 잘렸다** (래칫 방지): ${brakeNotes.join(' | ')}`;
  }
  return out;
}

if (require.main === module) {
  main().catch(e => {
    console.error(`swing_ai 오류: ${e.message}`);
    process.exit(1);
  });
}

module.exports = { clampAndValidate, applyBrake, fmtParams, readWeeklyParams, buildTail, BRAKE_PCT, BRAKE_ABS };
