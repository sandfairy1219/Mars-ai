// 앱 자신의 .env를 절대경로로 로드 (pm2 cwd가 달라도 동작) — 2026-09-23 fix
require('dotenv').config({ path: require('path').join(__dirname, '.env') });
const fs = require('fs');
const path = require('path');
const cheerio = require('cheerio');
const os = require('os');
const { execFileSync } = require('child_process');

// CF 우회 fetch (2026-09-23): saveticker.com 이 Cloudflare 대화형 챌린지로 서버 직접 요청을 403 차단.
// → 파이썬 우회 레이어(SSH 릴레이 → 프록시 → 직접 → microlink)로 본문을 받아온다.
function fetchViaCfBypass(url) {
  const script = path.join(os.homedir(), '.hermes/scripts/saveticker_fetch.py');
  const out = execFileSync('python3', [script, url], {
    maxBuffer: 64 * 1024 * 1024,
    timeout: 150000,
    // 요약 크론 몫(6회/24h)을 남기고 microlink 무료 한도 안에서만 쓴다
    env: { ...process.env, SAVETICKER_QUOTA_RESERVE: '6' },
  });
  return out.toString('utf8');
}

// 주거용 IP 릴레이(sp PC) 가용 여부 — 켜져 있으면 10분 주기, 아니면 기본(75분)
const RELAY_CONF = path.join(os.homedir(), '.hermes/scripts/.saveticker_relay.json');
let _relayCache = { at: 0, up: false };
function relayUp() {
  if (Date.now() - _relayCache.at < 5 * 60 * 1000) return _relayCache.up;
  let up = false;
  try {
    const conf = JSON.parse(fs.readFileSync(RELAY_CONF, 'utf8'));
    if (conf.enabled !== false && conf.ssh) {
      execFileSync('ssh', ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
        '-o', 'StrictHostKeyChecking=accept-new', conf.ssh, 'echo', 'ok'],
        { timeout: 15000, stdio: ['ignore', 'pipe', 'ignore'] });
      up = true;
    }
  } catch (e) {
    up = false;
  }
  _relayCache = { at: Date.now(), up };
  return up;
}

// 외부 스크래핑 API 키가 설정돼 있으면 실시간(10분) 주기 사용 (건당 1크레딧, 무료 10,000/월)
const SCRAPERS_CONF = path.join(os.homedir(), '.hermes/scripts/.saveticker_scrapers.json');
function scraperConfigured() {
  try {
    const c = JSON.parse(fs.readFileSync(SCRAPERS_CONF, 'utf8'));
    return ['scrapingant', 'scrapedo', 'scrapingbee'].some(k => String(c[k] || '').trim().length > 0);
  } catch (e) {
    return false;
  }
}

// 소스별 fetch 시각 기록 (주기 제한용)
function recordFetchTime(state, source) {
  if (!source.minIntervalMinutes) return;
  state.__meta = state.__meta || {};
  state.__meta[`${source.name}_last_fetch`] = Date.now();
  saveState(state);
}

// ==================== 설정 ====================
const DEFAULT_WEBHOOK_URL = process.env.DEFAULT_WEBHOOK_URL || process.env.DISCORD_WEBHOOK_URL;
const CHECK_INTERVAL = (parseInt(process.env.CHECK_INTERVAL_MINUTES) || 10) * 60 * 1000;
const KEYWORDS = (process.env.KEYWORDS || 'launch,etf,list,new')
  .split(',')
  .map(k => k.trim().toLowerCase());

const SOURCES = [
  {
    name: 'SaveTicker',
    enabled: process.env.SAVETICKER_ENABLED !== 'false',
    type: 'json',
    url: process.env.SAVETICKER_URL || 'https://saveticker.com/api/news/list?page=1&page_size=100&sort=created_at_desc&label_group=2&label_name=3',
    jsonListPath: 'news_list',
    jsonTitleField: 'title',
    jsonDateField: 'created_at',
    jsonUrlBuilder: (item) => `https://saveticker.com/news/${item.id}`,
    jsonFilter: (item) => Array.isArray(item.tag_names) && item.tag_names.includes('속보'),
    skipKeywordCheck: true,
    webhookUrl: process.env.SAVETICKER_WEBHOOK_URL,   // #세이브-속보 (1504492705340981309)
    cfBypass: true,          // 직접 fetch 실패 시 파이썬 우회 레이어 경유
    minIntervalMinutes: 85,  // microlink 무료 한도(25회/24h) 안에서 돌리기 위한 주기 (요약 몫 6회 보존)
    relayIntervalMinutes: 10,// sp PC 릴레이가 켜져 있으면 실시간 10분 주기
    maxPostsPerRun: 3,       // 폭주 방지
    maxAgeMinutes: 120,      // 신선도 가드: 2시간 넘은 글(쌓인 백로그)은 전송 안 함
    maxSeen: 200,            // 피드가 100건이라 최근 200개 제목까지 기억
  },
  {
    name: 'TossInvest',
    enabled: process.env.TOSSINVEST_ENABLED !== 'false',
    type: 'json',
    url: process.env.TOSSINVEST_URL || 'https://docs-api.tossinvest.com/api/v1/post/search?categoryId=45&searchTitleKeyword=&page=0&size=10&type=NOTICE',
    jsonListPath: 'result.list',
    jsonTitleField: 'title',
    jsonDateField: 'displayDt',
    jsonUrlBuilder: (item) => `https://corp.tossinvest.com/ko/post?type=notice&id=${item.id}&category=45`,
    jsonFilter: (item) => item.displayYn === 'Y',
    skipKeywordCheck: true,
    webhookUrl: process.env.TOSSINVEST_WEBHOOK_URL,
    webhookUsername: '토스 공지봇',
    webhookAvatar: 'https://raw.githubusercontent.com/sandfairy1219/Mars-ai/main/etf-alarm/assets/Toss_Symbol_Primary.png',
    webhookFooter: 'Toss Invest Notice Bot',
    extraHeaders: {
      'Referer': 'https://corp.tossinvest.com/',
      'Origin': 'https://corp.tossinvest.com'
    }
  },
  {
    name: 'Defiance',
    enabled: process.env.DEFIANCE_ENABLED !== 'false',
    url: process.env.DEFIANCE_URL || 'https://www.defianceetfs.com/in-the-news/',
    titleSelector: process.env.DEFIANCE_SELECTOR || '.news-item h2 a',
    dateSelector: process.env.DEFIANCE_DATE_SELECTOR || '.news-date',
  },
  {
    name: 'TRADR',
    enabled: process.env.TRADR_ENABLED !== 'false',
    url: process.env.TRADR_URL || 'https://www.tradretfs.com/news-and-media',
    titleSelector: process.env.TRADR_SELECTOR || '.news-item-title',
    dateSelector: process.env.TRADR_DATE_SELECTOR || '.date',
  },
  {
    name: 'Leverage Shares',
    enabled: process.env.LEVERAGE_SHARES_ENABLED !== 'false',
    url: process.env.LEVERAGE_SHARES_URL || 'https://leverageshares.com/en-eu/in-the-press/',
    titleSelector: process.env.LEVERAGE_SHARES_SELECTOR || '.news-item h2 a',
    dateSelector: process.env.LEVERAGE_SHARES_DATE_SELECTOR || '.date',
  },
  {
    name: 'GraniteShares',
    enabled: process.env.GRANITESHARES_ENABLED !== 'false',
    url: process.env.GRANITESHARES_URL || 'https://graniteshares.com/press/',
    titleSelector: process.env.GRANITESHARES_SELECTOR || '.news-article-title',
    dateSelector: process.env.GRANITESHARES_DATE_SELECTOR || '.news-article-date',
  },
  {
    name: 'T-Rex',
    enabled: process.env.TREX_ENABLED !== 'false',
    url: process.env.TREX_URL || 'https://www.rexshares.com/news-insights/',
    titleSelector: process.env.TREX_SELECTOR || '.blog-single-title',
    dateSelector: process.env.TREX_DATE_SELECTOR || '.post-date',
  }
];

// 상태 파일 경로
const STATE_FILE = path.join(__dirname, 'state.json');

// ==================== 유틸 ====================
function loadState() {
  try {
    if (fs.existsSync(STATE_FILE)) {
      return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    }
  } catch (e) {
    console.error('state.json 로드 오류:', e.message);
  }
  return {};
}

function saveState(state) {
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
}

function hasKeyword(text) {
  const lower = text.toLowerCase();
  return KEYWORDS.some(k => lower.includes(k));
}

function formatDate(dateText) {
  if (!dateText || dateText === 'N/A') return 'N/A';
  try {
    const date = new Date(dateText);
    if (isNaN(date.getTime())) return dateText;
    const year = date.getFullYear();
    const month = date.getMonth() + 1;
    const day = date.getDate();
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    return `${year}년 ${month}월 ${day}일 ${hours}:${minutes}`;
  } catch {
    return dateText;
  }
}

// ==================== Discord 웹훅 ====================
async function sendWebhook(source, title, url, dateText) {
  const webhookUrl = source.webhookUrl || DEFAULT_WEBHOOK_URL;
  if (!webhookUrl) {
    console.warn(`⚠️ [${source.name}] 웹훅 URL이 설정되지 않았습니다.`);
    return;
  }

  const embed = {
    title: `🚀 ${source.name} - 새 뉴스`,
    description: `**${title}**`,
    color: 0x00ff88,
    fields: [
      { name: '출처', value: source.name, inline: true },
      { name: '날짜', value: formatDate(dateText), inline: true }
    ],
    footer: { text: source.webhookFooter || 'Save news bot' },
    timestamp: new Date().toISOString()
  };

  if (url) {
    embed.url = url;
  }

  const payload = {
    username: source.webhookUsername || '세이브 속보봇',
    avatar_url: source.webhookAvatar || 'https://cdn-icons-png.flaticon.com/512/4222/4222019.png',
    embeds: [embed]
  };

  try {
    const res = await fetch(webhookUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    console.log(`✅ [${source.name}] 웹훅 전송 완료: ${title.substring(0, 50)}...`);
  } catch (err) {
    console.error(`❌ [${source.name}] 웹훅 전송 실패:`, err.message);
  }
}

// ==================== 크롤링 ====================
async function checkSource(source) {
  if (!source.enabled) {
    console.log(`⏭️ [${source.name}] 비활성화됨, 걸러냄`);
    return;
  }

  // 주기 제한(소스별): SaveTicker는 CF 우회 폴백(microlink 무료 한도) 때문에 75분 주기로만 조회
  const _preState = loadState();
  if (source.minIntervalMinutes) {
    const useRelay = !!(source.relayIntervalMinutes && relayUp());
    const useScraper = !!(source.relayIntervalMinutes && !useRelay && scraperConfigured());
    const fast = useRelay || useScraper;
    const interval = fast ? source.relayIntervalMinutes : source.minIntervalMinutes;
    const last = (_preState.__meta && _preState.__meta[`${source.name}_last_fetch`]) || 0;
    const elapsedMin = (Date.now() - last) / 60000;
    if (last && elapsedMin < interval) {
      const why = useRelay ? '릴레이 ON' : (useScraper ? '스크래퍼 ON' : '');
      console.log(`⏳ [${source.name}] 주기 대기 (${elapsedMin.toFixed(0)}/${interval}분${why ? ', ' + why : ''}) — 건너뜀`);
      return;
    }
  }

  console.log(`🔍 [${source.name}] 크롤링 시작: ${source.url}`);

  try {
    const isJson = source.type === 'json';
    const headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
      'Accept': isJson ? 'application/json, text/plain, */*' : 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
      'Accept-Language': 'en-US,en;q=0.9,ko;q=0.8',
      'Accept-Encoding': 'gzip, deflate, br',
      'Connection': 'keep-alive',
      'Upgrade-Insecure-Requests': '1',
      'Sec-Fetch-Dest': isJson ? 'empty' : 'document',
      'Sec-Fetch-Mode': isJson ? 'cors' : 'navigate',
      'Sec-Fetch-Site': 'none',
      'Cache-Control': 'max-age=0'
    };
    
    if (source.extraHeaders) {
      Object.assign(headers, source.extraHeaders);
    }

    let res;
    try {
      res = await fetch(source.url, { redirect: 'follow', headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (err) {
      if (!source.cfBypass) throw err;
      console.log(`🛡️ [${source.name}] 직접 요청 실패(${err.message}) → CF 우회 레이어 경유`);
      const body = fetchViaCfBypass(source.url);
      res = { ok: true, text: async () => body, json: async () => JSON.parse(body) };
    }

    const state = loadState();
    const prevTitles = state[source.name] || [];
    let newTitles = [];

    if (isJson) {
      const json = await res.json();
      let items = json;
      if (source.jsonListPath) {
        const paths = source.jsonListPath.split('.');
        for (const p of paths) {
          items = items?.[p];
        }
      }
      if (!Array.isArray(items)) {
        throw new Error(`JSON response did not contain array at path ${source.jsonListPath || 'root'}`);
      }

      for (const item of items) {
        if (source.jsonFilter && !source.jsonFilter(item)) continue;

        const title = item[source.jsonTitleField || 'title'];
        if (!title) continue;

        const dateText = item[source.jsonDateField || 'date'] || '';

        // 신선도 가드: 너무 오래된 글(과거 백로그)은 새 소식으로 취급하지 않는다
        if (source.maxAgeMinutes && dateText) {
          const ageMin = (Date.now() - new Date(dateText).getTime()) / 60000;
          if (Number.isFinite(ageMin) && ageMin > source.maxAgeMinutes) continue;
        }
        let articleUrl = item[source.jsonUrlField || 'url'] || '';
        if (!articleUrl && source.jsonUrlBuilder) {
          articleUrl = source.jsonUrlBuilder(item);
        }

        const shouldAlert = source.skipKeywordCheck || hasKeyword(title);
        if (!prevTitles.includes(title) && shouldAlert) {
          newTitles.push({ title, url: articleUrl, date: dateText });
        }
      }
    } else {
      const html = await res.text();
      const $ = cheerio.load(html);

      $(source.titleSelector).each((i, el) => {
        const title = $(el).text().trim();
        const href = $(el).attr('href') 
          || $(el).closest('a').attr('href')
          || $(el).parent('a').attr('href')
          || $(el).siblings('a').first().attr('href')
          || '';
        const articleUrl = href.startsWith('http') ? href : new URL(href, source.url).href;
        
        const dateEl = source.dateSelector 
          ? $(el).closest('article, li, div, tr').find(source.dateSelector).first()
          : null;
        const dateText = dateEl ? dateEl.text().trim() : '';

        // 중복 제거 (제목 기준)
        if (!prevTitles.includes(title) && hasKeyword(title)) {
          newTitles.push({ title, url: articleUrl, date: dateText });
        }
      });
    }

    if (newTitles.length === 0) {
      console.log(`✔️ [${source.name}] 새 소식 없음`);
      recordFetchTime(state, source);
      return;
    }

    // 오랜 공백 후 백로그 폭주 방지: 최신 N건만 전송하고 나머지는 '본 것으로' 기록
    const allNewTitles = newTitles.map(a => a.title);
    if (source.maxPostsPerRun && newTitles.length > source.maxPostsPerRun) {
      console.log(`🚧 [${source.name}] 신규 ${newTitles.length}건 중 최신 ${source.maxPostsPerRun}건만 전송 (나머지는 기록만)`);
      newTitles = newTitles.slice(0, source.maxPostsPerRun);
    }

    console.log(`🆕 [${source.name}] ${newTitles.length}개 새 글 발견!`);

    for (const article of newTitles) {
      await sendWebhook(source, article.title, article.url, article.date);
      // 웹훅 rate limit 방지 (1초 대기)
      await new Promise(r => setTimeout(r, 1000));
    }

    // 상태 업데이트
    state[source.name] = [...prevTitles, ...allNewTitles];
    // 오래된 제목 정리 (소스별 유지 개수)
    const seenLimit = source.maxSeen || 50;
    if (state[source.name].length > seenLimit) {
      state[source.name] = state[source.name].slice(-seenLimit);
    }
    saveState(state);
    recordFetchTime(state, source);

  } catch (err) {
    console.error(`❌ [${source.name}] 크롤링 오류:`, err.message);
  }
}

async function runAll() {
  console.log(`\n⏰ ${new Date().toLocaleString('ko-KR')} - ETF 크롤링 실행`);
  for (const source of SOURCES) {
    await checkSource(source);
    await new Promise(r => setTimeout(r, 2000)); // 사이트 간 2초 대기
  }
  console.log(`✅ 완료. 다음 실행: ${new Date(Date.now() + CHECK_INTERVAL).toLocaleString('ko-KR')}\n`);
}

// ==================== 메인 ====================
console.log('🚀 ETF Alarm Bot 시작');
console.log(`📡 기본 Discord Webhook: ${DEFAULT_WEBHOOK_URL ? '설정됨' : '미설정'}`);
console.log(`⏱️ 체크 주기: ${CHECK_INTERVAL / 60000}분`);
console.log(`🔑 키워드: ${KEYWORDS.join(', ')}\n`);

// 즉시 한 번 실행
runAll();

// 주기 실행
setInterval(runAll, CHECK_INTERVAL);
