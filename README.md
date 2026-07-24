# Mars AI

**Hermes Agent** 기반 AI 어시스턴트 — Discord에서 작동하는 자동화된 미국 주식 모니터링/트레이딩 봇입니다.

## 환경

| 항목 | 내용 |
|---|---|
| 호스트 | Oracle Cloud VM (Ubuntu 22.04) |
| 에이전트 | Hermes Agent (Nous Research) |
| 모델 | DeepSeek V4 Flash / Pro |
| Python | 3.10+ |

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

## 크론잡 스케줄 (KST)

모든 작업은 Hermes Agent Cron을 통해 자동 실행됩니다.

- **07:30** — 미국 증시 요약 (#미국증시요약 채널)
- **09:00~21:30** — 모의투자 스윙 트레이더 (6회 스캔)
- **13:00** — BB Touch Scanner (전체 BEST + S&P 500 BEST 2픽 웹훅)
- **21:30** — 섹터 히트맵
- **5분 간격** — 암호화폐 실시간 티커

## 라이선스

MIT
