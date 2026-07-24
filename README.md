# Mars AI — Discord 챗봇 + 자동 트레이딩 시스템

Discord 기반 AI 챗봇 + 미국 주식 자동 분석/트레이딩 봇입니다.

## 📂 프로젝트 구조

```
marsAI/
├── index.js                    # Discord.js 기반 AI 챗봇 (슬래시 커맨드, DM, 멘션)
├── package.json
├── etf-alarm/                  # ETF 알람 봇 (Node.js)
│   ├── index.js
│   └── .env                    # (git에 포함되지 않음)
├── paper-trading/              # 모의투자 (Paper Trading) 시스템
│   ├── swing-trader.py         #   롱온리 스윙 트레이더 (6회/일 스캔, 포트폴리오 관리)
│   ├── daily_heatmap.py        #   일간 섹터 히트맵 생성
│   ├── build-universe.py       #   S&P 500 상위 200종목 유니버스 빌드
│   ├── trader.py               #   초기 트레이딩 엔진 (v1)
│   ├── analyze_request.py      #   분석 요청 처리
│   ├── universe.json           #   스톡 유니버스 데이터
│   ├── cap-classification.json #   시가총액 분류
│   ├── cap-data.json           #   시가총액 데이터
│   ├── portfolio.json          #   포트폴리오 상태
│   └── swing-portfolio.json    #   스윙 포트폴리오 상태
└── scripts/                    # 헤르메스 에이전트 크론 스크립트
    ├── trading/                # 트레이딩 봇
    │   └── swing-trader.py     #   paper-trading 버전과 동기화된 스윙 트레이더
    ├── scanners/               # 시장 스캐너
    │   ├── bb_touch_scanner.py #   볼린저밴드 터치 스캐너 (일 2픽 웹훅 전송)
    │   └── premarket_scanner.py#   프리마켓 거래량 급등 스캐너
    ├── tickers/                # 실시간 시세 티커
    │   ├── crypto_live_ticker.py   #   실시간 암호화폐+지수 티커 (1초 갱신)
    │   ├── crypto_ticker.py    #   암호화폐 시세 조회
    │   ├── nasdaq_ticker.py    #   나스닥 실시간 티커
    │   └── watchdog_crypto_ticker.sh # 암호화폐 워치독
    ├── heatmaps/               # 히트맵 생성
    │   ├── sp500_heatmap.py    #   S&P 500 핀비즈 스타일 히트맵
    │   └── daily_heatmap.py    #   일간 섹터 히트맵 (paper-trading 버전과 동기화)
    └── crawlers/               # 데이터 페처
        └── fetch_saveticker_summary.py # SaveTicker 미국증시요약 자동 페치
```

---

## 🤖 Discord AI 챗봇 (`index.js`)

Discord.js + OpenCodeGo API를 활용한 AI 챗봇.

### 기능
- `/chat [message] [model?]` — AI에게 질문
- `/models` — 사용 가능한 모델 목록
- `/model [name]` — 기본 모델 변경 (사용자별 저장)
- `/reset` — 대화 기록 초기화
- `@봇멘션` — 서버에서 멘션하여 대화
- **DM 지원** — 개인 메시지로 대화
- **쓰레드 답변** — 질문에 ✅ 반응 후 새 쓰레드 생성

### 설치 및 실행
```bash
cp .env.example .env   # 환경변수 설정
npm install
npm start              # 또는 npm run dev (핫리로드)
```

### 필수 환경변수
| 변수 | 설명 |
|---|---|
| `DISCORD_TOKEN` | Discord Developer Portal 토큰 |
| `CLIENT_ID` | Discord 애플리케이션 ID |
| `OPENCODEGO_API_KEY` | OpenCodeGo API 키 |
| `MODELS` | 사용할 모델 목록 (형식: `provider\|modelId\|표시이름`) |

---

## 📈 모의투자 시스템 (`paper-trading/`)

Hermes Agent 크론잡으로 6회/일 자동 실행되는 롱온리 스윙 트레이더.

### 실행 스케줄 (KST)
| 시간 | 스캔 | 설명 |
|---|---|---|
| 09:00 | `pre_market_1` | 오버나이트 스캔 + 신규 진입 |
| 13:30 | `pre_market_2` | 프리장 스캔 |
| 15:30 | `regular_1` | 오전 스캔 + 신규 진입 |
| 17:30 | `regular_2` | 중간 스캔 |
| 20:00 | `regular_3` | 장마감 스캔 |
| 21:30 | `after_hours` | 데이장 마감 요약 |

### 주요 기능
- **거래대금 필터**: Yahoo Finance screener API로 거래대금 상위 250종목 동적 스캔
- **레버리지 ETF 필터**: 70+ 종목명/심볼 패턴 기반 자동 제외
- **뉴스 심리 분석**: 실시간 뉴스 헤드라인 감성 분석 → 시장 공격도 조정
- **벤치마크 비교**: 포트폴리오 수익률 vs S&P 500 / Nasdaq 100 / Dow Jones / Russell 2000
- **동적 포지션 사이징**: 시장 변동성/공격도 기반 종목당 $3K~$11K 차등 배분

---

## 🔍 주요 스크립트 상세 (`scripts/`)

### Trading (`scripts/trading/`)
#### Swing Trader (`swing-trader.py`)
- paper-trading 버전과 동기화된 롱온리 스윙 트레이더
- 거래대금 필터 + 레버리지 ETF 제외 + 뉴스 심리 분석 + 벤치마크 4종 비교
- 자세한 설명은 [모의투자 시스템](#-모의투자-system-paper-trading) 참고

### Scanners (`scripts/scanners/`)
#### BB Touch Scanner (`bb_touch_scanner.py`)
- Yahoo Finance 거래대금 상위 종목 스캔 → 볼린저밴드(20,2) 터치 감지
- 6개 차원 복합 스코어링 (BB 터치 강도, RSI, 모멘텀, 거래량, 섹터 로테이션, 시장 레짐)
- 매일 22:00 KST 전 BEST + S&P 500 BEST 2픽 웹훅 전송 (차트 이미지 포함)

#### Premarket Scanner (`premarket_scanner.py`)
- 프리마켓(04:00~09:30 ET) 거래량 급등 종목 감지
- 전일 대비 거래량 비율 기반 스캔

### Tickers (`scripts/tickers/`)
#### Crypto Live Ticker (`crypto_live_ticker.py`)
- 주요 암호화폐 + 주가지수 실시간 시세 (1초 갱신)
- 단일 메시지 PATCH 패턴 (메시지 생성 후 내용만 수정)

#### Crypto Ticker (`crypto_ticker.py`)
- 암호화폐 시세 조회

#### Nasdaq Ticker (`nasdaq_ticker.py`)
- 나스닥 실시간 티커

#### Watchdog (`watchdog_crypto_ticker.sh`)
- 암호화폐 워치독 — 크론 기반 상태 모니터링

### Heatmaps (`scripts/heatmaps/`)
#### S&P 500 Heatmap (`sp500_heatmap.py`)
- Finviz 스타일 S&P 500 트리맵 히트맵 생성
- 섹터별 색상 + 등락률 기반 셀 크기/색상

#### Daily Heatmap (`daily_heatmap.py`)
- 일간 섹터 히트맵 (paper-trading 버전과 동기화)

### Crawlers (`scripts/crawlers/`)
#### SaveTicker Fetcher (`fetch_saveticker_summary.py`)
- SaveTicker 오선 작성자의 미국 증시 요약 원문 자동 페치
- 제목 형식 자동 감지 (`【미국 증시 요약】` / `SAVE Daily`)
- 원문(텍스트+이미지) vs 리포트(이미지 전용) 구분 로직

---

## 🔐 보안

- `.env` 파일은 `.gitignore`에 포함되어 Git에 커밋되지 않습니다
- 모든 스크립트는 Discord 웹훅 URL을 `.env` 또는 환경변수에서 읽음
- API 키/토큰이 코드 내에 하드코딩되지 않도록 유지

---

## ⚙️ 환경

- **서버**: Oracle Cloud VM (Ubuntu 22.04)
- **Python**: 3.10+
- **Node.js**: 최신 LTS
- **스케줄러**: Hermes Agent Cron
- **데이터 소스**: Yahoo Finance (yfinance), Binance WebSocket, SaveTicker
