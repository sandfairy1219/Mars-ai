require('dotenv').config();
const fs = require('fs');
const path = require('path');
const cheerio = require('cheerio');

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
    url: process.env.SAVETICKER_URL || 'https://saveticker.com/api/news/list?page=1&page_size=20&sort=created_at_desc&label_group=2&label_name=1',
    jsonListPath: 'news_list',
    jsonTitleField: 'title',
    jsonDateField: 'created_at',
    jsonUrlBuilder: (item) => `https://saveticker.com/news/${item.id}`,
    jsonFilter: (item) => Array.isArray(item.tag_names) && item.tag_names.includes('속보'),
    skipKeywordCheck: true,
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

    const res = await fetch(source.url, {
      redirect: 'follow',
      headers
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
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
      return;
    }

    console.log(`🆕 [${source.name}] ${newTitles.length}개 새 글 발견!`);

    for (const article of newTitles) {
      await sendWebhook(source, article.title, article.url, article.date);
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
console.log(`📡 기본 Discord Webhook: ${DEFAULT_WEBHOOK_URL ? '설정됨' : '미설정'}`);
console.log(`⏱️ 체크 주기: ${CHECK_INTERVAL / 60000}분`);
console.log(`🔑 키워드: ${KEYWORDS.join(', ')}\n`);

// 즉시 한 번 실행
runAll();

// 주기 실행
setInterval(runAll, CHECK_INTERVAL);
