# Mars AI

**Hermes Agent** — AI-powered automated US stock monitoring and trading bot running on Discord.

## Environment

| Item | Detail |
|---|---|
| Host | Oracle Cloud VM (Ubuntu 22.04) |
| Agent | Hermes Agent (Nous Research) |
| Model | DeepSeek V4 Flash / Pro |
| Python | 3.10+ |

## Tech Stack

| Category | Usage |
|---|---|
| Runtime | Hermes Agent, Node.js, Python 3.10+ |
| Data | Yahoo Finance (yfinance), Binance WebSocket, SaveTicker |
| Charts | mplfinance, matplotlib |
| Alerts | Discord Webhook |
| Deployment | Oracle Cloud, GitHub |
| Auth | GitHub PAT, Discord Bot Token |

## Directory Structure

```
├── etf-alarm/        # ETF alarm bot (Node.js)
├── paper-trading/    # Paper trading system (swing-trader, universe, heatmap)
├── trading/          # swing-trader.py (live version)
├── scanners/         # Market scanners (BB Touch Scanner, Premarket Scanner)
├── tickers/          # Live tickers (Crypto, Nasdaq, Watchdog)
├── heatmaps/         # Heatmap generators (S&P 500, Daily)
└── crawlers/         # Data fetchers (SaveTicker)
```

## License

MIT
