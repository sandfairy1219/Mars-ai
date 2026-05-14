require('dotenv').config();
const fs = require('fs');
const path = require('path');
const cheerio = require('cheerio');

// ==================== 설정 ====================
const WEBHOOK_URL = process.env.DISCORD_WEBHOOK_URL;
const CHECK_INTERVAL = (parseInt(process.env.CHECK_INTERVAL_MINUTES) || 60) * 60 * 1000;
const KEYWORDS = (process.env.KEYWORDS || 'launch,etf,list,new')
  .split(',')
  .map(k => k.trim().toLowerCase());

const SOURCES = [
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
    titleSelector: process.env.TRADR_SELECTOR || '.news-item h2 a',
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
    titleSelector: process.env.GRANITESHARES_SELECTOR || '.news-item h2 a',
    dateSelector: process.env.GRANITESHARES_DATE_SELECTOR || '.date',
  },
  {
    name: 'T-Rex',
    enabled: process.env.TREX_ENABLED !== 'false',
    url: process.env.TREX_URL || 'https://www.rexshares.com/news-insights/',
    titleSelector: process.env.TREX_SELECTOR || '.news-item h2 a',
    dateSelector: process.env.TREX_DATE_SELECTOR || '.date',
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

// ==================== Discord 웹훅 ====================
async function sendWebhook(sourceName, title, url, dateText) {
  if (!WEBHOOK_URL) {
    console.warn('⚠️ DISCORD_WEBHOOK_URL이 설정되지 않았습니다.');
    return;
  }

  const payload = {
    username: 'ETF 알리미',
    avatar_url: 'https://cdn-icons-png.flaticon.com/512/4222/4222019.png',
    embeds: [{
      title: `🚀 ${sourceName} - 새 ETF 관련 소식`,
      description: `**${title}**`,
      url: url,
      color: 0x00ff88,
      fields: [
        { name: '출처', value: sourceName, inline: true },
        { name: '날짜', value: dateText || 'N/A', inline: true }
      ],
      footer: { text: 'ETF Alarm Bot' },
      timestamp: new Date().toISOString()
    }]
  };

  try {
    const res = await fetch(WEBHOOK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    console.log(`✅ [${sourceName}] 웹훅 전송 완료: ${title.substring(0, 50)}...`);
  } catch (err) {
    console.error(`❌ [${sourceName}] 웹훅 전송 실패:`, err.message);
  }
}

// ==================== 크롤링 ====================
async function checkSource(source) {
  if (!source.enabled) {
    console.log(`⏭️ [${source.name}] 비활성화됨, 걸러냄`);
    return;
  }

  console.log(`🔍 [${source.name}] 크롤링 시작: ${source.url}`);

  try {
    const res = await fetch(source.url, {
      redirect: 'follow',
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,ko;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0'
      }
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const html = await res.text();
    const $ = cheerio.load(html);
    const state = loadState();
    const prevTitles = state[source.name] || [];
    const newTitles = [];

    $(source.titleSelector).each((i, el) => {
      const title = $(el).text().trim();
      const href = $(el).attr('href') || '';
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

    if (newTitles.length === 0) {
      console.log(`✔️ [${source.name}] 새 소식 없음`);
      return;
    }

    console.log(`🆕 [${source.name}] ${newTitles.length}개 새 글 발견!`);

    for (const article of newTitles) {
      await sendWebhook(source.name, article.title, article.url, article.date);
      // 웹훅 rate limit 방지 (1초 대기)
      await new Promise(r => setTimeout(r, 1000));
    }

    // 상태 업데이트
    state[source.name] = [...prevTitles, ...newTitles.map(a => a.title)];
    // 오래된 제목 정리 (최근 50개만 유지)
    if (state[source.name].length > 50) {
      state[source.name] = state[source.name].slice(-50);
    }
    saveState(state);

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
console.log(`📡 Discord Webhook: ${WEBHOOK_URL ? '설정됨' : '미설정'}`);
console.log(`⏱️ 체크 주기: ${CHECK_INTERVAL / 60000}분`);
console.log(`🔑 키워드: ${KEYWORDS.join(', ')}\n`);

// 즉시 한 번 실행
runAll();

// 주기 실행
setInterval(runAll, CHECK_INTERVAL);
