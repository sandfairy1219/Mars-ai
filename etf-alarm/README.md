# ETF Alarm Bot

미국 ETF 운용사(Direxion, Defiance 등)의 새 ETF 출시 소식을 **Discord 웹훅**으로 자동 알려주는 크롤러입니다.

---

## 기능

- 🕐 **1시간마다 자동 체크** (주기 변경 가능)
- 🎯 **키워드 필터링** (launch, etf, list, new 등)
- 🔗 **Defiance, TRADR, Leverage Shares, GraniteShares, T-Rex** 기본 지원 (추가 가능)
- 💾 **중복 알림 방지** (이미 본 글은 다시 안 볼 수 있음)
- 🎨 **Discord 임베드** 알림 (이쁘게 표시)

---

## 설치 방법

### 1. 의존성 설치

```bash
cd etf-alarm
npm install
```

### 2. .env 파일 설정

```bash
cp .env.example .env
nano .env
```

**필수 설정**:

| 변수 | 설명 |
|------|------|
| `DISCORD_WEBHOOK_URL` | Discord 채널 웹훅 URL |
| `CHECK_INTERVAL_MINUTES` | 체크 주기 (분) |
| `KEYWORDS` | 알림 키워드 (쉼표 구분) |

### 3. 웹훅 URL 받는 법

1. Discord 채널 설정 → **연동(Integrations)**
2. **웹후크(Webhooks)** → **새 웹후크**
3. 이름: `ETF 알리미` → URL 복사 → `.env`에 붙여넣기

---

## 실행 방법

### 로컬 테스트
```bash
node index.js
```

### PM2로 백그라운드 실행 (권장)

```bash
# marsAI 폴와 같은 서버에서
pm2 start ~/marsAI/etf-alarm/index.js --name etf-alarm
pm2 save
```

**봇과 함께 확인**:
```bash
pm2 status
# discord-bot  (online)
# etf-alarm    (online) ← 새로 추가됨
```

---

## 크롤링 소스 추가/수정

### CSS Selector 찾는 법

1. Chrome/Firefox에서 해당 웹사이트 열기
2. F12 → 개발자 도구 → Ctrl+Shift+C (요소 선택)
3. 원하는 기사 제목 클릭
4. 코드에서 해당 HTML 태그의 `class` 또는 `id` 확인

### 예시 수정

`.env` 파일에서:

```env
# Direxion (기본값)
DIREXION_SELECTOR=.press-release-item h3 a
DIREXION_DATE_SELECTOR=.date

# 만약 웹사이트 구조가 바뀌었다면:
DIREXION_SELECTOR=.news-list .title a
DIREXION_DATE_SELECTOR=.published-date
```

---

## 키워드 커스터마이징

`.env`의 `KEYWORDS`를 수정하세요:

```env
# 기본 (ETF 출시 관련)
KEYWORDS=launch,etf,list,new,debut,introduced,listing

# 더 넓게 (모든 새 소식)
KEYWORDS=etf,fund,new,announces,introduces
```

---

## 주의사항

- 웹사이트 구조가 변경되면 **Selector 수정**이 필요할 수 있습니다.
- `state.json` 파일에 이미 확인한 글 제목이 저장됩니다. (삭제하면 중복 알림 가능)
- 물리 서버(Oracle Cloud 등)에서 24시간 실행하는 것을 권장합니다.

---

## 파일 구조

```
etf-alarm/
├── index.js          # 메인 크롤러
├── package.json      # 의존성
├── .env              # 환경 변수
├── .env.example      # 환경 변수 예시
├── state.json        # 확인한 글 목록 (자동 생성)
└── README.md         # 이 파일
```

---

## 라이선스

MIT
