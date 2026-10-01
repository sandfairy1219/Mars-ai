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
  // ─── 배분 파라미터 (2026-10-01: 하드코딩 제한 → AI 결정) ───
  sector_limit: [1, 10],
  max_positions: [3, 15],
  min_cash_pct: [0.0, 0.6],
  cap_min_mid: [0, 4],
  cap_min_small: [0, 4],
  max_new_per_tick: [1, 15],
  min_position_size: [0, 20000],
  min_candidate_score: [0, 8],
};

// ─── 래칫 브레이크 설정 ───
const BRAKE_PCT = 0.20;   // 주간학습 기준값 대비 최대 ±20% (곱셈형 파라미터)
const BRAKE_ABS = 0.15;   // 부호 파라미터(aggression_bias 등) 절대 ±0.15
const ABS_PARAMS = new Set(['aggression_bias', 'aggr_5d_bull', 'aggr_5d_bear']);

// ─── 소유권 분리 (2026-10-01) ───
// "포지션 구조" 파라미터(무엇을 사고 · 얼마나 버티고 · 어디서 자르는가)는 주간학습
// (weekly_learning.py)이 실현 성과로 튜닝한다. LLM이 요청해도 거부된다.
// 래칫의 근본 원인이 LLM이 구조를 흔든 것이었으므로, LLM에게 남기는 것은
// "오늘 얼마나 실어 나를까"(리스크 노브)뿐이다.
const LLM_LOCKED = new Set(['max_hold_days', 'score_min', 'price_min', 'stop_mult', 'target_mult']);
const LLM_KNOBS = new Set([
  'aggression_bias', 'base_position_mult', 'vix_floor', 'vix_ceiling',
  // 배분(집중도) 노브 — LLM 전용. 주간학습이 소유하지 않으므로 브레이크 없이 CLAMPS 안에서 자유.
  'sector_limit', 'max_positions', 'min_cash_pct', 'cap_min_mid', 'cap_min_small',
  'max_new_per_tick', 'min_position_size', 'min_candidate_score',
]);
// 브레이크(하루 변동 제한)는 주간학습과 소유권이 겹치는 노브에만 건다.
// LLM 전용 배분 노브는 CLAMPS 범위 안에서 자유롭게 움직인다 (래칫 사고는 구조 파라미터에서 났다).
const NO_BRAKE = new Set(['sector_limit', 'max_positions', 'min_cash_pct', 'cap_min_mid', 'cap_min_small',
  'max_new_per_tick', 'min_position_size', 'min_candidate_score']);

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

// ─── AI 배분 지시 검증 (섹터 틸트 · 종목 지정/제외) ───
const KNOWN_SECTORS = new Set(['Technology', 'Communication Services', 'Consumer Discretionary',
  'Consumer Staples', 'Financials', 'Health Care', 'Industrials', 'Energy', 'Utilities',
  'Materials', 'Real Estate', 'Unknown']);

function validateAlloc(alloc) {
  if (!alloc || typeof alloc !== 'object') return null;
  const out = {};
  const tilt = {};
  for (const [k, v] of Object.entries(alloc.sector_tilt || {})) {
    if (!KNOWN_SECTORS.has(k)) continue;
    const n = Number(v);
    if (!Number.isFinite(n) || n === 0) continue;
    tilt[k] = Math.max(-3, Math.min(3, Math.round(n * 10) / 10));
  }
  if (Object.keys(tilt).length) out.sector_tilt = tilt;
  const tick = (arr) => (Array.isArray(arr) ? Array.from(new Set(arr
    .map(x => String(x).toUpperCase().trim())
    .filter(x => /^[A-Z][A-Z.\-]{0,5}$/.test(x)))).slice(0, 8) : []);
  const f = tick(alloc.focus_tickers); if (f.length) out.focus_tickers = f;
  const a = tick(alloc.avoid_tickers); if (a.length) out.avoid_tickers = a;
  const x = tick(alloc.exit_tickers).slice(0, 4); if (x.length) out.exit_tickers = x;
  return Object.keys(out).length ? out : null;
}

function clampAndValidate(params) {
  const out = {};
  const clamped = [];
  const rejected = [];
  for (const [k, [lo, hi]] of Object.entries(CLAMPS)) {
    if (params[k] === undefined || params[k] === null) continue;
    const raw = Number(params[k]);
    if (!Number.isFinite(raw)) continue;
    if (LLM_LOCKED.has(k)) {
      // 구조 파라미터는 주간학습 전용 — LLM 요청은 거부하고 그 사실을 출력에 남긴다
      rejected.push(`${k}: 요청 ${raw} 거부 (주간학습 전용 — LLM 수정 금지)`);
      continue;
    }
    const v = Math.min(hi, Math.max(lo, raw));
    out[k] = v;
    if (v !== raw) clamped.push(`${k}: 요청 ${raw} → 클램프 ${v} (허용 ${lo}~${hi})`);
  }
  return { clean: out, clamped, rejected };
}

// ─── 래칫 브레이크: 주간학습 기준값 대비 ±20%(부호 파라미터 ±0.15) ───
function applyBrake(clean, baseline) {
  const applied = {};
  const adjustments = [];
  for (const [k, v] of Object.entries(clean)) {
    if (NO_BRAKE.has(k)) { applied[k] = v; continue; }
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
  // 1) 판단용 컨텍스트 리포트 — --context-only: 매매/전체스캔/저장 없이 현재 상태만 수집.
  //    기존 순서(매매 실행 → LLM 판단)는 AI 결정이 항상 "다음 틱"에야 적용됐다.
  //    이제 판단 → 파라미터 반영 → 매매 실행이 같은 틱 안에서 끝난다.
  let contextReport = '';
  try {
    contextReport = execFileSync('python3', [REPORTER, '--context-only'], { cwd: SCRIPTS, timeout: 600000, encoding: 'utf8' }).trim();
  } catch (e) {
    contextReport = `(컨텍스트 리포트 생성 실패: ${e.message.split('\n')[0]})`;
  }
  if (!contextReport) contextReport = '(컨텍스트 출력 없음)';

  // 2) LLM 결정 요청
  const baseline = readWeeklyParams();
  const effective = currentEffective();
  const prompt = `너는 모의투자 스윙트레이더의 AI 결정권자다. 아래 리포트를 분석해서 (A) 해석과 (B) 파라미터 결정을 낸다.

[현재 파라미터 — 이 숫자가 기준이다]
· 주간학습 기준값(브레이크 기준점): ${fmtParams(baseline)}
· 현재 적용값(오늘 AI 조정까지 반영): ${fmtParams(effective)}

[조정 권한 — 소유권이 분리돼 있다. 반드시 지켜라]
· LLM이 바꿀 수 있는 것 = "오늘 얼마나 실어 나를까"(리스크 노브) 4개뿐이다:
  - aggression_bias (기준값 ±0.15 절대) — 현금 비중/진입 강도
  - base_position_mult (±20%) — 1회 진입 크기
  - vix_floor / vix_ceiling (±20%) — 변동성 밴드
· 아래는 주간학습(weekly_learning.py)이 실현 성과로 튜닝하는 "포지션 구조" 파라미터다.
  LLM은 바꿀 수 없다 — 요청해도 시스템이 거부하고 거부 사실을 기록한다:
  max_hold_days · score_min · price_min · stop_mult · target_mult
  이 값들에 대한 의견은 판단문(텍스트)에만 쓰고 JSON에는 절대 넣지 마라.
  (과거 이 5개를 매 틱 흔들어 max_hold_days가 16일→3일까지 내려간 사고가 있었다.)

[배분 권한 — 제한을 네가 정한다 (고정 제한 없음)]
아래는 과거 코드에 상수로 박혀 있던 배분 제한이다. 이제 전부 네가 국면에 맞게 정한다.
· sector_limit (1~10, 기본 3) — 한 섹터에 최대 몇 종목. 기술 주도처럼 좁고 강한 장세면
  올려서 집중해라. 집중은 [성과 지표]·브레드스(QQQ vs IWM 20일)로 정당화할 것.
· max_positions (3~15, 기본 10) — 총 보유 상한.
· min_cash_pct (0.0~0.6, null=공격도 자동) — 현금 하한을 직접 지정. null이면 기존 공격도 규칙.
· cap_min_mid / cap_min_small (0~4, 기본 0) — 중형·소형 최소 보유 수. 0이면 강제 혼합 없음.
· max_new_per_tick (1~15, null=공격도 자동) — 한 틱에 새로 살 종목 수.
· min_position_size (0~20000, 기본 3000) — 이보다 작은 포지션은 건너뜀 ($).
· min_candidate_score (0~8, 기본 4) — 워치리스트에 올릴 최소 점수(후보 스크린). 낮추면 후보가 늘고,
  올리면 상위만 본다. 남용하면 스캔이 무거워지니 근거가 있을 때만.
그리고 alloc 블록으로 후보 점수에 직접 개입할 수 있다:
· sector_tilt — {"Technology": 2, "Utilities": -1} 처럼 섹터 점수 가감(-3~+3)
· focus_tickers / avoid_tickers — 종목 최대 8개 지정(가산 +2)/제외
· exit_tickers — 보유 중인 종목을 최대 4개까지 청산 지시(틱당 최대 3건 집행, long_term 제외).
  섹터 전환을 실제로 집행할 수단이다. 남발 금지 — 배분 전환이라는 근거가 있을 때만.
배분은 네 판단이지만, 조정 사유(note)에 근거를 남겨라.

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
{"params": {"aggression_bias": <float>, "base_position_mult": <float>, "vix_floor": <float>, "vix_ceiling": <float>, "sector_limit": <int>, "max_positions": <int>, "min_cash_pct": <float|null>, "cap_min_mid": <int>, "cap_min_small": <int>, "max_new_per_tick": <int|null>, "min_position_size": <number>, "min_candidate_score": <int>, "note": "<조정 사유 한 줄>"},
 "alloc": {"sector_tilt": {"<섹터>": <float>}, "focus_tickers": ["<TICKER>"], "avoid_tickers": ["<TICKER>"], "exit_tickers": ["<청산할 보유종목>"]}}
\`\`\`
바꿀 게 없으면 현재 값 그대로 넣어라. 리포트가 비었거나 결정 불가면 {"params":{}}만 출력해라.

[컨텍스트 리포트 — 매매 실행 전 상태]
${contextReport.slice(0, 12000)}`;

  let aiText = '';
  try {
    aiText = await askLLM(prompt);
  } catch (e) {
    aiText = `🤖 AI 판단: (AI 호출 실패 — ${e.message}) 결정 없이 기존 파라미터 유지.`;
  }

  // 3) 결정 추출·검증·클램프·브레이크·저장
  let applied = [];
  let brakeNotes = [];
  let allocApplied = null;
  const decision = extractDecision(aiText);
  if (decision && decision.params) {
    const { clean, clamped, rejected } = clampAndValidate(decision.params);
    const { applied: braked, adjustments } = applyBrake(clean, baseline);
    brakeNotes = [...rejected, ...clamped, ...adjustments];
    allocApplied = validateAlloc(decision.alloc);
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
        alloc: allocApplied,
        note: String(decision.params.note || '').slice(0, 200),
        ai_summary: aiText.replace(/\s+/g, ' ').slice(0, 300),
      });
      const prevAlloc = (old.date === todayUTC() && old.alloc) ? old.alloc : null;
      const mergedAlloc = allocApplied || prevAlloc;
      const merged = { date: todayUTC(), params: { ...prev, ...braked }, history: hist };
      if (mergedAlloc) merged.alloc = mergedAlloc;
      if (old._note) merged._note = old._note;
      fs.writeFileSync(OVERRIDES, JSON.stringify(merged, null, 2));
      applied = Object.entries(braked).map(([k, v]) => `${k}=${v}`);
    }
  }

  // 4) 파라미터 반영 후 실제 매매 실행 (같은 틱에서 즉시 적용)
  let report = '';
  try {
    report = execFileSync('python3', [REPORTER], { cwd: SCRIPTS, timeout: 600000, encoding: 'utf8' }).trim();
  } catch (e) {
    report = `(매매 리포트 생성 실패: ${e.message.split('\n')[0]})`;
  }
  if (!report) report = contextReport;

  // 5) 최종 출력
  console.log(buildTail(report, aiText, applied, brakeNotes, allocApplied));
}

// 최종 출력 조립 (테스트 가능하도록 분리)
function buildTail(report, aiText, applied, brakeNotes, allocApplied) {
  let out = report;
  if (!out.includes('🤖 AI 판단')) {
    out += '\n\n---\n' + aiText.trim();
  }
  if (applied.length) {
    out += `\n\n⚙️ **AI 결정 반영 완료** (${todayUTC()}): ${applied.join(', ')}`;
  } else if (!aiText.includes('실패')) {
    out += '\n\n⚙️ AI 결정: 파라미터 유지 (변경 없음)';
  }
  if (allocApplied) {
    const bits = [];
    if (allocApplied.sector_tilt) bits.push('틸트 ' + Object.entries(allocApplied.sector_tilt).map(([k, v]) => `${k}${v > 0 ? '+' : ''}${v}`).join(','));
    if (allocApplied.focus_tickers) bits.push('지정 ' + allocApplied.focus_tickers.join(','));
    if (allocApplied.avoid_tickers) bits.push('제외 ' + allocApplied.avoid_tickers.join(','));
    if (allocApplied.exit_tickers) bits.push('청산지시 ' + allocApplied.exit_tickers.join(','));
    if (bits.length) out += `\n\n🎛️ **AI 배분 지시 반영**: ${bits.join(' | ')}`;
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

module.exports = { clampAndValidate, applyBrake, validateAlloc, fmtParams, readWeeklyParams, buildTail, LLM_LOCKED, LLM_KNOBS, NO_BRAKE, BRAKE_PCT, BRAKE_ABS };
