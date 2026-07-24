#!/usr/bin/env python3
"""
Premarket GAINERS scanner — ETF EXCLUDED.
Runs at KST 21:00 (UTC 12:00) = ET ~08:00 during pre-market.
Focus: price spike ≥10% OR volume explosion (0 → millions).
Outputs JSON to stdout for the LLM agent to analyze.
"""
import sys
import json
import time
from datetime import datetime, timezone, timedelta

import requests
import yfinance as yf
import pandas as pd
import numpy as np


# ── Config ──────────────────────────────────────────────
USER_AGENT = "Mozilla/5.0 (compatible; HermesBot/1.0)"
PRE_MARKET_START = pd.Timestamp("04:00:00").time()
PRE_MARKET_END = pd.Timestamp("09:29:59").time()

# Thresholds — 진짜 급등만 잡는다
GAINER_PRICE_THRESHOLD = 10.0    # 상승 10% 이상
LOSER_PRICE_THRESHOLD = 10.0     # 하락 10% 이상 (참고용)
VOLUME_EXPLOSION_MIN = 500_000   # 50만주 이상 갑자기 터져야
VOLUME_EARLY_MAX = 10_000        # early session 평균 거래량이 거의 0

# User watchlist — 개별주 only (ETF/ETN 제외)
WATCHLIST = [
    # Major tech
    "AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AMD",
    "AVGO", "QCOM", "MU", "PLTR", "CRWD", "NET", "SNOW",
    # Quantum / AI
    "IONQ", "RGTI", "QBTS", "QUBT",
    # Crypto proxies
    "MSTR", "COIN", "CLSK", "IREN", "RIOT", "MARA",
    # Other interest
    "CEG", "ASPN", "FWRD", "APLD", "SPCE", "NU", "VCX", "SHIM",
]

# Known ETFs to exclude from most_actives (in case quoteType misses them)
ETF_EXCLUDE_SET = {
    "SOXL", "SOXS", "TQQQ", "SQQQ", "UPRO", "SPXU", "LABU", "LABD",
    "FNGU", "FNGD", "YINN", "YANG", "NVDX", "NVDL", "AMZU", "METU",
    "AAPU", "TSLZ", "TSLL", "BITX", "GGLL", "DFEN", "QTEX", "MSTZ",
}


def fetch_most_actives(count=100):
    """Get most active tickers from Yahoo, excluding ETFs."""
    url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
    params = {
        "formatted": "true", "lang": "en-US", "region": "US",
        "scrIds": "most_actives", "count": count,
        "corsDomain": "finance.yahoo.com"
    }
    try:
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        results = data.get("finance", {}).get("result", [])
        if not results:
            return []
        quotes = results[0].get("quotes", [])
        tickers = []
        for q in quotes:
            sym = q.get("symbol", "")
            qtype = q.get("quoteType", "")
            if sym and qtype == "EQUITY" and sym not in ETF_EXCLUDE_SET:
                tickers.append(sym)
        return tickers
    except Exception as e:
        print(f"⚠️ most_actives fetch failed: {e}", file=sys.stderr)
        return []


def fetch_premarket_data(tickers, batch_size=50):
    """Download 1m pre/post data for a list of tickers in batches."""
    all_data = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i+batch_size]
        try:
            df = yf.download(
                batch, period="1d", interval="1m",
                prepost=True, group_by="ticker",
                progress=False, threads=False
            )
            if df is None or df.empty:
                continue
            if not isinstance(df.columns, pd.MultiIndex):
                all_data[batch[0]] = df
            else:
                for t in batch:
                    if t in df.columns.get_level_values(0):
                        all_data[t] = df[t].copy()
        except Exception as e:
            print(f"⚠️ Batch {batch[0]}..{batch[-1]} failed: {e}", file=sys.stderr)
            continue
        time.sleep(0.5)
    return all_data


def analyze_ticker(symbol, df):
    """
    Analyze pre-market data for a single ticker.
    Detects: (1) massive price swing ≥10%, OR (2) volume explosion from zero.
    Returns dict or None.
    """
    if df is None or df.empty:
        return None

    premarket = df[
        (df.index.time >= PRE_MARKET_START) &
        (df.index.time <= PRE_MARKET_END)
    ].dropna(subset=["Close"])

    if premarket.empty or len(premarket) < 10:
        return None

    total_vol = int(premarket["Volume"].sum())

    # ── Price metrics ──
    first_price = float(premarket["Close"].iloc[0])
    last_price = float(premarket["Close"].iloc[-1])
    price_change_pct = ((last_price - first_price) / first_price) * 100
    high_price = float(premarket["High"].max())
    low_price = float(premarket["Low"].min())

    # ── Volume explosion: 0 → millions ──
    mid_point = len(premarket) // 3  # first 1/3 vs rest
    early_third = premarket.iloc[:mid_point]
    later_two_thirds = premarket.iloc[mid_point:]

    early_vol_avg = float(early_third["Volume"].mean()) if len(early_third) > 0 else 0.0
    later_vol_avg = float(later_two_thirds["Volume"].mean()) if len(later_two_thirds) > 0 else 0.0
    max_vol = int(premarket["Volume"].max())
    max_vol_time = premarket["Volume"].idxmax()

    # 5-min rolling max for spike detection
    if len(premarket) >= 5:
        rolling_max = premarket["Volume"].rolling(5).sum().max()
        rolling_max = int(rolling_max) if not pd.isna(rolling_max) else 0
    else:
        rolling_max = 0

    # Recent 15-min volume
    if len(premarket) >= 15:
        recent_15m = premarket.iloc[-15:]
    else:
        recent_15m = premarket
    recent_vol_avg = float(recent_15m["Volume"].mean())

    # Volume explosion detection
    vol_explosion = False
    vol_explosion_detail = ""
    if max_vol >= VOLUME_EXPLOSION_MIN and early_vol_avg <= VOLUME_EARLY_MAX:
        vol_explosion = True
        vol_explosion_detail = f"0→{max_vol//1000}K"

    # ── Classification ──
    is_notable = False
    direction = "flat"
    reasons = []

    if price_change_pct >= GAINER_PRICE_THRESHOLD:
        is_notable = True
        direction = "up"
        reasons.append(f"price_{price_change_pct:.1f}%")

    elif price_change_pct <= -LOSER_PRICE_THRESHOLD:
        is_notable = True
        direction = "down"
        reasons.append(f"price_{abs(price_change_pct):.1f}%")

    if vol_explosion:
        if not is_notable:
            # Volume explosion alone is notable
            is_notable = True
            direction = "up" if price_change_pct > 0 else "down"
        reasons.append(f"vol_{vol_explosion_detail}")

    if not is_notable:
        return None

    return {
        "symbol": symbol,
        "direction": direction,
        "price_first": round(float(first_price), 2),
        "price_last": round(float(last_price), 2),
        "price_change_pct": round(float(price_change_pct), 2),
        "price_high": round(float(high_price), 2),
        "price_low": round(float(low_price), 2),
        "total_vol": total_vol,
        "max_vol": max_vol,
        "max_vol_time": max_vol_time.strftime("%H:%M") if not pd.isna(max_vol_time) else "N/A",
        "rolling_5min_max_vol": rolling_max,
        "early_vol_avg": round(early_vol_avg, 1),
        "later_vol_avg": round(later_vol_avg, 1),
        "vol_explosion": vol_explosion,
        "reasons": reasons,
    }


def main():
    start_time = datetime.now(timezone.utc)

    # 1. Get most_actives (already ETF-filtered)
    most_active = fetch_most_actives(100)

    # 2. Combine with watchlist, deduplicate
    seen = set()
    all_tickers = []
    for t in WATCHLIST + most_active:
        if t not in seen and isinstance(t, str) and t.strip():
            if t in ETF_EXCLUDE_SET:
                continue
            seen.add(t)
            all_tickers.append(t)

    # 3. Fetch pre-market data
    print(f"🔍 Scanning {len(all_tickers)} tickers (ETF excluded) for pre-market surges...", file=sys.stderr)
    data = fetch_premarket_data(all_tickers, batch_size=50)

    # 4. Analyze each ticker
    results = []
    for symbol, df in data.items():
        analysis = analyze_ticker(symbol, df)
        if analysis:
            results.append(analysis)

    # 5. Separate gainers/losers
    gainers = [r for r in results if r["direction"] == "up"]
    losers = [r for r in results if r["direction"] == "down"]

    gainers.sort(key=lambda r: r["price_change_pct"], reverse=True)
    losers.sort(key=lambda r: r["price_change_pct"])

    # 6. Output JSON
    output = {
        "scan_time_utc": start_time.isoformat(),
        "scan_time_et": (start_time - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M ET"),
        "scan_time_kst": (start_time + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST"),
        "tickers_scanned": len(all_tickers),
        "tickers_with_data": len(data),
        "notable_count": len(results),
        "gainer_count": len(gainers),
        "loser_count": len(losers),
        "gainers": gainers[:20],
        "losers": losers[:5],
    }

    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
