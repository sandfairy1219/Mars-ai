#!/usr/bin/env python3
"""
BTC/ETH Real-Time Ticker for Discord
Fetches from Binance public API (no key, real-time)
Uses ANSI colors: green for up, red for down
"""
import urllib.request
import json
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
BINANCE_API = "https://api.binance.com/api/v3/ticker/24hr"

# ANSI color codes (work in Discord ```ansi code blocks)
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"

SYMBOLS = {
    "BTCUSDT": "₿ BTC",
    "ETHUSDT": "Ξ ETH",
}

def fetch_ticker(symbol):
    try:
        url = f"{BINANCE_API}?symbol={symbol}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}

def fmt_price(p):
    if p >= 1000:
        return f"${p:,.0f}"
    elif p >= 1:
        return f"${p:,.2f}"
    else:
        return f"${p:,.6f}"

def main():
    now = datetime.now(KST)
    lines = [
        "```ansi",
        f"{BOLD}{CYAN}🪙 CRYPTO TICKER{RESET}  {now.strftime('%m/%d %H:%M:%S')} KST",
        "",
    ]

    for sym, name in SYMBOLS.items():
        data = fetch_ticker(sym)
        if "error" in data:
            lines.append(f"{RED}⚠ {name}: {data['error']}{RESET}")
            continue

        price = float(data["lastPrice"])
        chg_pct = float(data["priceChangePercent"])
        chg = float(data["priceChange"])
        high = float(data["highPrice"])
        low = float(data["lowPrice"])
        vol = float(data["quoteVolume"])

        if chg_pct > 0:
            color = GREEN
            arrow = "▲"
        elif chg_pct < 0:
            color = RED
            arrow = "▼"
        else:
            color = YELLOW
            arrow = "◆"

        lines.append(f"{BOLD}{name}{RESET}")
        lines.append(f"  {color}{arrow} {fmt_price(price)}  {chg:+,.2f} ({chg_pct:+.2f}%){RESET}")
        lines.append(f"  {CYAN}H:{RESET} {fmt_price(high)}  {CYAN}L:{RESET} {fmt_price(low)}  {CYAN}V:{RESET} ${vol:,.0f}")
        lines.append("")

    # BTC/ETH relative strength
    btc = fetch_ticker("BTCUSDT")
    eth = fetch_ticker("ETHUSDT")
    if "error" not in btc and "error" not in eth:
        btc_chg = float(btc["priceChangePercent"])
        eth_chg = float(eth["priceChangePercent"])
        diff = eth_chg - btc_chg
        if diff > 0.3:
            lines.append(f"{YELLOW}⚡ ETH outperforming BTC by {diff:+.1f}%p{RESET}")
        elif diff < -0.3:
            lines.append(f"{YELLOW}⚡ BTC outperforming ETH by {abs(diff):.1f}%p{RESET}")

    lines.append("```")

    print("\n".join(lines))

if __name__ == "__main__":
    main()
