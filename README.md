# Mars AI

**Hermes Agent** 기반 AI 어시스턴트 — Discord에서 작동하는 자동화된 미국 주식 모니터링/트레이딩 봇입니다.

## 환경

| 항목 | 내용 |
|---|---|
| 호스트 | Oracle Cloud VM (Ubuntu 22.04) |
| 에이전트 | Hermes Agent (Nous Research) |
| 모델 | DeepSeek V4 Flash / Pro |
| Python | 3.10+ |

## 기술 스택

| 구분 | 사용 |
|---|---|
| 런타임 | Hermes Agent, Node.js, Python 3.10+ |
| 데이터 | Yahoo Finance (yfinance), Binance WebSocket, SaveTicker |
| 차트 | mplfinance, matplotlib |
| 알림 | Discord Webhook |
| 배포 | Oracle Cloud, GitHub |
| 인증 | GitHub PAT, Discord Bot Token |

## 디렉토리 구조

```
├── etf-alarm/        # ETF 알람 봇 (Node.js)
├── paper-trading/    # 모의투자 시스템 (swing-trader, universe, heatmap)
├── trading/          # swing-trader.py (실행중인 버전)
├── scanners/         # 시장 스캐너 (BB Touch Scanner, Premarket Scanner)
├── tickers/          # 실시간 티커 (Crypto, Nasdaq, Watchdog)
├── heatmaps/         # 히트맵 생성 (S&P 500, Daily)
└── crawlers/         # 데이터 페처 (SaveTicker)
```

## 라이선스

MIT
