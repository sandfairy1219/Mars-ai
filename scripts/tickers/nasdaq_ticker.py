#!/usr/bin/env python3
"""
Nasdaq / US Tech 100 Real-Time Ticker for Discord
Fetches ^NDX (Nasdaq-100 Index) + NQ=F (Nasdaq-100 E-mini Futures)
Outputs a clean Discord-formatted message to stdout.
"""
import yfinance as yf
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

SYMBOLS = {
    "^NDX": "🇺🇸 나스닥100",
    "NQ=F": "📈 나스닥 선물",
}

def fetch_ticker(sym):
    try:
        t = yf.Ticker(sym)
        info = t.info
        price = info.get("regularMarketPrice") or info.get("currentPrice") or None
        prev = info.get("previousClose")
        chg = info.get("regularMarketChange")
        chg_pct = info.get("regularMarketChangePercent")
        day_high = info.get("regularMarketDayHigh") or info.get("dayHigh")
        day_low = info.get("regularMarketDayLow") or info.get("dayLow")
        volume = info.get("regularMarketVolume") or info.get("volume")
        market_state = info.get("marketState", "")

        return {
            "price": price,
            "prev": prev,
            "chg": chg,
            "chg_pct": chg_pct,
            "high": day_high,
            "low": day_low,
            "volume": volume,
            "state": market_state,
        }
    except Exception as e:
        return {"error": str(e)}

def format_change(chg, chg_pct, price):
    """Format change with direction emoji"""
    if chg is None or chg_pct is None:
        return "—"
    emoji = "🟢" if chg > 0 else ("🔴" if chg < 0 else "⚪")
    sign = "+" if chg > 0 else ""
    return f"{emoji} {sign}{chg:,.2f} ({chg_pct:+.2f}%)"

def fmt_num(n):
    """Format number with commas, handle None"""
    if n is None:
        return "—"
    if n >= 100:
        return f"{n:,.2f}"
    return f"{n:,.4f}"

def main():
    now = datetime.now(KST)
    lines = [f"**🖥️ US TECH 100 실시간**  |  {now.strftime('%m/%d %H:%M')} KST", ""]

    for sym, label in SYMBOLS.items():
        data = fetch_ticker(sym)
        if "error" in data:
            lines.append(f"**{label}** ({sym}): ⚠️ {data['error']}")
            continue

        price = data["price"]
        if price is None:
            lines.append(f"**{label}** ({sym}): 데이터 없음")
            continue

        chg_str = format_change(data["chg"], data["chg_pct"], price)
        hi = fmt_num(data["high"])
        lo = fmt_num(data["low"])
        vol = f"{data['volume']:,.0f}" if data.get("volume") else "—"
        state = data.get("state", "").replace("REGULAR", "정규장").replace("PRE", "프리마켓").replace("POST", "애프터").replace("CLOSED", "마감")

        lines.append(f"**{label}** `{sym}`")
        lines.append(f"> 💰 **{fmt_num(price)}**  {chg_str}")
        lines.append(f"> 📊 고가: {hi}  |  저가: {lo}  |  거래량: {vol}")
        if state:
            lines.append(f"> 🕐 {state}")
        lines.append("")

    # Add a sparkline-like direction bar based on ^NDX
    ndx_data = fetch_ticker("^NDX")
    if "error" not in ndx_data and ndx_data.get("chg") is not None:
        chg_pct = ndx_data.get("chg_pct", 0)
        # Simple visual bar
        bar_len = 20
        if chg_pct > 0:
            filled = min(int(chg_pct * 10), bar_len)
            bar = "🟩" * filled + "⬜" * (bar_len - filled)
        elif chg_pct < 0:
            filled = min(int(abs(chg_pct) * 10), bar_len)
            bar = "⬜" * (bar_len - filled) + "🟥" * filled
        else:
            bar = "⬜" * bar_len
        lines.append(f"📉 {bar}")

    lines.append("-# powered by yfinance | 약 15분 지연")

    print("\n".join(lines))

if __name__ == "__main__":
    main()
