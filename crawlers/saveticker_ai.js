#!/usr/bin/env node
// saveticker_ai.js — SaveTicker 요약 AI 래퍼
// 1) fetch_saveticker_summary.py 실행 → JSON  2) NO_NEW_POSTS면 silent 종료
// 3) 새 글이면 LLM(flash) 요약 생성  4) LLM 실패 시 silent (RETRY job이 보완)
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const SCRIPTS = path.join(os.homedir(), '.hermes/scripts');
const COLLECTOR = path.join(SCRIPTS, 'fetch_saveticker_summary.py');
const PROMPT_FILE = path.join(SCRIPTS, 'saveticker_prompt.txt');
const BASE_URL = process.env.OPENCODEGO_API_BASE_URL || 'https://opencode.ai/zen/go/v1/chat/completions';
// 2026-09-23: 추론 토큰이 max_tokens를 먹어 content가 빈 응답으로 오는 경우가 있어
//  ① 토큰 상한을 넉넉히(4000) ② 모델 폴백(4.1 → v4) 순으로 재시도한다.
const MODELS = ['deepseek-v4.1-flash', 'deepseek-v4-flash'];
const MAX_TOKENS = 4000;

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

async function askLLM(prompt) {
  const key = apiKey();
  if (!key) throw new Error('API key not found');
  let lastErr;
  for (const model of MODELS) {
    for (let attempt = 1; attempt <= 2; attempt++) {
      try {
        const res = await fetch(BASE_URL, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${key}`,
            // OpenCode Go 세션 어피니티 (MissingSessionID 400 방지)
            'x-opencode-session': 'hermes-cron-saveticker-ai',
          },
          body: JSON.stringify({
            model,
            messages: [{ role: 'system', content: prompt }],
            max_tokens: MAX_TOKENS,
          }),
        });
        if (!res.ok) throw new Error(`LLM HTTP ${res.status}`);
        const data = await res.json();
        const msg = (data.choices || [{}])[0].message || {};
        const content = msg.content || '';
        if (!content) throw new Error(`${model}: 빈 응답 (finish=${(data.choices || [{}])[0].finish_reason})`);
        return content;
      } catch (e) {
        lastErr = e;
        if (attempt < 2) await new Promise(r => setTimeout(r, 3000 * attempt));
      }
    }
  }
  throw lastErr;
}

async function main() {
  // 1) 수집기 실행
  let raw = '';
  try {
    raw = execFileSync('python3', [COLLECTOR], { cwd: SCRIPTS, timeout: 120000, encoding: 'utf8' }).trim();
  } catch (e) {
    return; // 수집 실패 = silent (RETRY가 보완)
  }

  // NO_NEW_POSTS/ERROR → silent (stdout 비움 → no_agent 전송 스킵)
  if (!raw || raw.includes('NO_NEW_POSTS') || raw.includes('"status": "ERROR"')) return;

  // 2) LLM 요약
  const basePrompt = fs.readFileSync(PROMPT_FILE, 'utf8');
  const prompt = basePrompt + '\n\n[스크립트 출력]\n' + raw;
  let reply;
  try {
    reply = await askLLM(prompt);
  } catch (e) {
    console.error(`[saveticker_ai] LLM 실패: ${e.message} — silent 처리 (RETRY 대기)`);
    return;
  }
  const out = reply.trim();
  if (!out || out === '[SILENT]') return;

  // 3) 전송 확정 — state 마킹 (RETRY 중폭 방지)
  // 메인(no_agent)은 agent가 없어 mark_sent를 못 돌리므로, 여기서 직접 마킹한다.
  // 마킹 성공 → RETRY가 NO_NEW_POSTS로 silent. LLM/수집 실패 시 마킹 안 함 → RETRY가 대신 전송.
  try {
    const parsed = JSON.parse(raw);
    if (parsed && parsed.post_id) {
      const kind = parsed.type === 'weekly' ? 'weekly' : 'daily';
      execFileSync('python3',
        [path.join(SCRIPTS, 'saveticker_mark_sent.py'), String(parsed.post_id), parsed.title || '', kind],
        { cwd: SCRIPTS, timeout: 15000, encoding: 'utf8' });
    }
  } catch (e) {
    console.error(`[saveticker_ai] mark_sent 실패: ${e.message} — RETRY가 중복 전송할 수 있음`);
  }

  // 이미지 첨부는 LLM 지시에 맡기지 않고 결정적으로 붙인다 (LLM이 MEDIA 줄을 자주 빠뜨림)
  let images = [];
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed.images)) {
      images = parsed.images.filter(p => typeof p === 'string' && fs.existsSync(p)).slice(0, 10);
    }
  } catch (e) { /* 무시 */ }
  const body = out.split('\n')
    .filter(l => !/^\s*MEDIA:/i.test(l))                      // LLM이 넣은 MEDIA는 아래에서 결정적으로 다시 붙임
    .filter(l => !/saveticker_mark_sent\.py/i.test(l))        // LLM이 에코한 명령어 제거
    .filter(l => !/^\s*(python3|bash|node|sh)\s+\S/i.test(l)) // 셸 명령 줄 제거
    .join('\n').trimEnd();
  const finalOut = images.length ? `${body}\n\n${images.map(p => `MEDIA:${p}`).join('\n')}` : body;
  if (!finalOut.trim()) return;
  console.log(finalOut);
}

main().catch(e => {
  console.error(`saveticker_ai 오류: ${e.message}`);
  process.exit(1);
});