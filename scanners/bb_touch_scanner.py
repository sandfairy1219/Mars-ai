#!/usr/bin/env python3
"""
BB Touch Scanner v4 — Market Regime + Sector Rotation + Composite Scoring.
Fetches Yahoo Finance most_actives, detects BB(20,2) touches, scores each
ticker on 6 dimensions with market regime / sector rotation context.

Modes:
- Default (stdout): Human-readable tables + JSON to /tmp/bb_scan_result.json
- --charts: Generate BB charts for touch candidates
- --json-only: Suppress stdout tables

Output JSON schema (v2):
  market_regime: current market context (VIX, rotation theme, sector rankings)
  upper_touches / lower_touches: touch candidates with composite scores
  top_picks: ranked by composite score
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings, sys, re, json, time, os, argparse, math
import urllib.request

warnings.filterwarnings('ignore')
pd.set_option('display.max_colwidth', 30)

# ── Discord Webhook: 전용 변수 우선 + 고정 배정 검증 가드 ─────
# 종목 추천 채널 전용. 절대 다른 채널로 전송 불가 (ID 검증 강제).
_TS_WEBHOOK_ID = "1517552970387034112"  # 치즈종목 추천 (고정)
WEBHOOK_URL = os.environ.get("BB_TOUCH_WEBHOOK") or os.environ.get("DISCORD_WEBHOOK") or os.environ.get("DISCORD_WEBHOOK_URL") or ""
if not WEBHOOK_URL:
    for env_path in [".env", "/home/ubuntu/marsAI/etf-alarm/.env"]:
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    for _k in ("BB_TOUCH_WEBHOOK=", "DISCORD_WEBHOOK_URL=", "DISCORD_WEBHOOK="):
                        if line.startswith(_k):
                            val = line.split("=", 1)[1].strip().strip("\"'")
                            if val:
                                WEBHOOK_URL = val
                                break
        except Exception:
            pass
        if WEBHOOK_URL:
            break
# ── 하드 가드: 웹훅 ID가 추천 채널이 아니면 즉시 중단 ──
_wid = WEBHOOK_URL.split("webhooks/")[1].split("/")[0] if "/webhooks/" in WEBHOOK_URL else ""
if _wid != _TS_WEBHOOK_ID:
    raise SystemExit(f"[bb_touch_scanner] 웹훅 가드 발동: {_wid} != {_TS_WEBHOOK_ID} — 전송 중단")

def send_discord(message, image_path=None):
    if not WEBHOOK_URL:
        print("⚠️ WEBHOOK_URL not configured", file=sys.stderr)
        return False
    try:
        if image_path and os.path.exists(image_path):
            boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
            with open(image_path, 'rb') as f:
                img_data = f.read()
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="content"\r\n\r\n'
                f"{message}\r\n"
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="files[0]"; filename="{os.path.basename(image_path)}"\r\n'
                f"Content-Type: image/png\r\n\r\n"
            ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
            headers = {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Mozilla/5.0 (compatible; HermesBot/1.0)",
            }
        else:
            body = json.dumps({"content": message}, ensure_ascii=False).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; HermesBot/1.0)",
            }
        req = urllib.request.Request(WEBHOOK_URL, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15):
            pass
        return True
    except Exception as e:
        print(f"⚠️ Discord send failed: {e}", file=sys.stderr)
        return False

# ── Config ──────────────────────────────────────────────────
BB_PERIOD = 20
BB_STD = 2.0
RSI_PERIOD = 14
SCREENER_COUNT = 250
TRADING_VALUE_TOP_N = 100
FETCH_TIMEOUT = 10
MAX_CHARTS = 20
CHART_DIR = '/tmp/bb_charts'

# Touch thresholds
UPPER_TOUCH_THRESHOLD = 0.90   # %B ≥ 0.90 → upper touch
LOWER_TOUCH_THRESHOLD = 0.10   # %B ≤ 0.10 → lower touch

# ── S&P 500 Constituents ──────────────────────────────────
SP500_TICKERS = set()
SP500_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sp500_tickers.txt')
if os.path.exists(SP500_PATH):
    with open(SP500_PATH) as f:
        SP500_TICKERS = set(line.strip() for line in f if line.strip())
    print(f"   ✓ S&P 500 tickers loaded: {len(SP500_TICKERS)}", file=sys.stderr)
else:
    print(f"   ⚠️ S&P 500 list not found at {SP500_PATH}", file=sys.stderr)

# Scoring weights (total = 100)
W_BB_TOUCH = 25
W_RSI = 15
W_LIQUIDITY = 10
W_MOMENTUM = 15
W_TREND = 15
W_SECTOR = 20

# ── Leveraged ETF filter ─────────────────────────────────────
LEVERAGED_PATTERNS = [
    r'\b(2X|3X|4X|-[123]X)\b',
    r'Ultra\s*(Bull|Bear|Pro|Short)?',
    r'Daily\s+(Bull|Bear|Inverse)',
    r'Leveraged\s+(Bull|Bear)?',
    r'Direxion\s+Daily',
    r'ProShares\s+Ultra',
]

LEVERAGED_TICKERS = {
    'TQQQ','SQQQ','UPRO','SPXU','TMF','TMV','SOXL','SOXS','LABU','LABD',
    'FNGU','FNGD','NAIL','TPOR','DFEN','NUGT','DUST','JNUG','JDST',
    'YINN','YANG','CWEB','CHAU','BOIL','KOLD','UCO','SCO',
    'DRN','DRV','TNA','TZA','SDOW','UDOW','SAA','FAS','FAZ',
    'AGQ','ZSL','BIB','BIS','CURE','DPK','EDC','EDZ',
    'GLL','DIG','DUG','RXL','RXD','ERX','ERY','UYG','SKF','USD','SSG',
    'SPXS','MIDU','URE','SRS','EET','EEV','EFO','EFU','EZJ','EUO',
    'UCC','SCC','UGE','UTSL','ROM','REW','UXI','SIJ','UYM','SMN',
    'NVDL','NVDX','TSLL','TSLZ','AMZU','AMZD','METU','GGLL','AAPU','BITX',
    'CONL','MSFL','GDXU','KMLM',
}

# ── Sector ETFs for rotation detection ───────────────────────
SECTOR_ETF_MAP = {
    'XLK': 'Technology',
    'XLC': 'Communication Services',
    'XLY': 'Consumer Discretionary',
    'XLP': 'Consumer Staples',
    'XLE': 'Energy',
    'XLF': 'Financials',
    'XLV': 'Health Care',
    'XLI': 'Industrials',
    'XLB': 'Materials',
    'XLRE': 'Real Estate',
    'XLU': 'Utilities',
}

# Sector → XL? mapping for ticker scoring
SECTOR_TO_ETF = {v: k for k, v in SECTOR_ETF_MAP.items()}

# ── Helpers ──────────────────────────────────────────────────

def is_leveraged_etf(name: str, ticker: str) -> bool:
    ticker_upper = ticker.upper().strip()
    if ticker_upper in LEVERAGED_TICKERS:
        return True
    if not isinstance(name, str) or not name.strip():
        return False
    for pat in LEVERAGED_PATTERNS:
        if re.search(pat, name, re.IGNORECASE):
            return True
    return False

def calc_rsi(series: pd.Series, period: int = 14) -> float:
    if len(series) < period + 1:
        return float('nan')
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.iloc[1:period+1].mean()
    avg_loss = loss.iloc[1:period+1].mean()
    if avg_loss == 0:
        return 100.0
    for i in range(period + 1, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gain.iloc[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss.iloc[i]) / period
    rs = (avg_gain / avg_loss) if avg_loss > 0 else float('inf')
    return 100.0 - (100.0 / (1.0 + rs)) if rs != float('inf') else 100.0

def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default

def safe_pct(val):
    """Format percentage with sign."""
    if val is None or math.isnan(val):
        return "N/A"
    return f"{val:+.1f}%"

def clamp(val, lo, hi):
    return max(lo, min(hi, val))

# ── Phase 0: Market Regime Detection ────────────────────────

REGIME_TICKERS = ['SPY', 'QQQ', 'IWM', 'DIA', '^VIX'] + list(SECTOR_ETF_MAP.keys())

def fetch_market_regime() -> dict:
    """
    Download regime tickers (indices + sector ETFs + VIX),
    compute returns, VIX zone, rotation theme.
    """
    print("🔄 시장 레짐 데이터 수집 중...", file=sys.stderr)
    try:
        data = yf.download(REGIME_TICKERS, period='1mo', interval='1d',
                           progress=False, group_by='ticker', threads=True, timeout=FETCH_TIMEOUT + 5)
    except Exception as e:
        print(f"   ⚠️ Regime data failed: {e}", file=sys.stderr)
        return _empty_regime()

    if data.empty:
        return _empty_regime()

    regime = {}
    now = datetime.utcnow()

    # Helper to extract close series
    def get_close(ticker):
        try:
            if isinstance(data.columns, pd.MultiIndex):
                return data[ticker]['Close'].dropna() if ticker in data.columns.get_level_values(0) else pd.Series(dtype=float)
            else:
                return data['Close'].dropna() if 'Close' in data.columns else pd.Series(dtype=float)
        except:
            return pd.Series(dtype=float)

    # ── VIX ──
    vix_series = get_close('^VIX')
    vix_now = safe_float(vix_series.iloc[-1]) if len(vix_series) > 0 else 20.0
    if vix_now < 15:
        vix_zone = 'low'
        risk_appetite = 'risk_on'
    elif vix_now < 25:
        vix_zone = 'normal'
        risk_appetite = 'cautious'
    elif vix_now < 35:
        vix_zone = 'elevated'
        risk_appetite = 'risk_off'
    else:
        vix_zone = 'panic'
        risk_appetite = 'panic'

    # ── Major Indices ──
    indices = {}
    for idx in ['SPY', 'QQQ', 'IWM', 'DIA']:
        s = get_close(idx)
        if len(s) >= 21:
            indices[idx] = {
                'close': safe_float(s.iloc[-1]),
                'ret_5d': safe_float((s.iloc[-1] / s.iloc[-6] - 1) * 100) if len(s) >= 6 else None,
                'ret_20d': safe_float((s.iloc[-1] / s.iloc[-21] - 1) * 100) if len(s) >= 21 else None,
            }
        elif len(s) > 0:
            indices[idx] = {'close': safe_float(s.iloc[-1]), 'ret_5d': None, 'ret_20d': None}

    # SPY trend vs MA50 (fetch separately if needed)
    spy_ma50 = None
    spy_s = get_close('SPY')
    if len(spy_s) >= 50:
        spy_ma50_val = spy_s.rolling(50).mean().dropna().iloc[-1]
        spy_ma50 = safe_float((spy_s.iloc[-1] / spy_ma50_val - 1) * 100)

    # ── Sector ETFs ranking ──
    sectors = {}
    for ticker, sector_name in SECTOR_ETF_MAP.items():
        s = get_close(ticker)
        if len(s) >= 6:
            ret_5d = safe_float((s.iloc[-1] / s.iloc[-6] - 1) * 100)
            ret_20d = safe_float((s.iloc[-1] / s.iloc[-21] - 1) * 100) if len(s) >= 21 else None
            sectors[sector_name] = {
                'etf': ticker,
                'close': safe_float(s.iloc[-1]),
                'ret_5d': round(ret_5d, 2),
                'ret_20d': round(ret_20d, 2) if ret_20d is not None else None,
            }
        else:
            sectors[sector_name] = {'etf': ticker, 'ret_5d': 0, 'ret_20d': None}

    # Rank sectors by 5d return
    ranked = sorted(sectors.items(), key=lambda x: x[1]['ret_5d'], reverse=True)
    top_sectors = [{'sector': k, **v} for k, v in ranked[:4]]
    bottom_sectors = [{'sector': k, **v} for k, v in ranked[-3:]]

    # Rotation theme detection
    tech_sectors = ['Technology', 'Communication Services', 'Consumer Discretionary']
    defensive_sectors = ['Utilities', 'Consumer Staples', 'Health Care', 'Real Estate']
    cyclical_sectors = ['Financials', 'Industrials', 'Materials', 'Energy']

    top_sector_names = [s['sector'] for s in top_sectors[:3]]
    bottom_sector_names = [s['sector'] for s in bottom_sectors]

    tech_in_top = any(s in tech_sectors for s in top_sector_names)
    def_in_top = any(s in defensive_sectors for s in top_sector_names)
    cyc_in_top = any(s in cyclical_sectors for s in top_sector_names)
    tech_in_bottom = any(s in tech_sectors for s in bottom_sector_names)

    if tech_in_top and not def_in_top and not cyc_in_top:
        rotation_theme = 'tech_leadership'
        theme_desc = '기술주 강세 (Growth 주도)'
    elif def_in_top and tech_in_bottom:
        rotation_theme = 'defensive_flight'
        theme_desc = '방어주 강세 (안전자산 선호)'
    elif cyc_in_top and not def_in_top:
        rotation_theme = 'cyclical_rotation'
        theme_desc = '경기순환주 강세 (리오프닝/경기회복)'
    elif all(s['ret_5d'] > 0 for s in top_sectors[:3]) and all(s['ret_5d'] > -1 for s in bottom_sectors):
        rotation_theme = 'broad_based'
        theme_desc = '전반적 강세 (Broad rally)'
    elif all(s['ret_5d'] < 0 for s in top_sectors[:3]):
        rotation_theme = 'broad_weakness'
        theme_desc = '전반적 약세 (Broad sell-off)'
    else:
        rotation_theme = 'mixed_rotation'
        theme_desc = '섹터 순환장 (Rotation)'

    # QQQ/SPY relative strength
    qqq_ret = indices.get('QQQ', {}).get('ret_5d', 0) or 0
    spy_ret = indices.get('SPY', {}).get('ret_5d', 0) or 0
    qqq_vs_spy = round(qqq_ret - spy_ret, 2)

    regime = {
        'vix': round(vix_now, 1),
        'vix_zone': vix_zone,
        'risk_appetite': risk_appetite,
        'indices': indices,
        'spy_vs_ma50_pct': round(spy_ma50, 2) if spy_ma50 is not None else None,
        'qqq_vs_spy_5d': qqq_vs_spy,
        'sector_rotation': {
            'theme': rotation_theme,
            'theme_desc': theme_desc,
            'top_sectors': top_sectors,
            'bottom_sectors': bottom_sectors,
        },
        'sectors_raw': sectors,
    }
    return regime

def _empty_regime():
    return {
        'vix': 20.0, 'vix_zone': 'unknown', 'risk_appetite': 'unknown',
        'indices': {}, 'spy_vs_ma50_pct': None, 'qqq_vs_spy_5d': 0,
        'sector_rotation': {'theme': 'unknown', 'theme_desc': '데이터 없음',
                             'top_sectors': [], 'bottom_sectors': []},
        'sectors_raw': {},
    }

# ── Phase 1: Screener ────────────────────────────────────────

def fetch_screener_top(count: int = 250) -> list[dict]:
    url = (
        f'https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved'
        f'?formatted=false&lang=en-US&region=US&scrIds=most_actives&count={count}'
    )
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        quotes = data.get('finance', {}).get('result', [{}])[0].get('quotes', [])
    except Exception as e:
        print(f"⚠️ Screener API failed: {e}", file=sys.stderr)
        return []

    results = []
    for q in quotes:
        sym = q.get('symbol', '').strip()
        if not sym:
            continue
        name = q.get('shortName') or q.get('longName') or sym
        price = q.get('regularMarketPrice') or q.get('regularMarketPreviousClose') or 0
        avg_vol = q.get('averageDailyVolume3Month') or 0
        dollar_vol = safe_float(price) * safe_float(avg_vol) / 1_000_000

        if is_leveraged_etf(str(name), sym):
            continue
        if dollar_vol <= 0:
            continue

        # Extract sector from screener API if available
        sector = q.get('sector') or None
        industry = q.get('industry') or None

        results.append({
            'ticker': sym,
            'name': str(name),
            'price': safe_float(price),
            'avg_vol_3m': int(safe_float(avg_vol)),
            'dollar_vol_m': dollar_vol,
            'sector': sector,
            'industry': industry,
        })

    results.sort(key=lambda x: x['dollar_vol_m'], reverse=True)
    return results

# ── Phase 2: Download batches ────────────────────────────────

def download_batch(tickers: list[str]) -> dict:
    try:
        ticker_str = ' '.join(tickers)
        df = yf.download(ticker_str, period='2mo', interval='1d', progress=False,
                         group_by='ticker', threads=True, timeout=FETCH_TIMEOUT + 5)
        result = {}
        if isinstance(df.columns, pd.MultiIndex):
            for t in tickers:
                if t in df.columns.get_level_values(0):
                    sub = df[t].copy()
                    if not sub.empty:
                        result[t] = sub
        else:
            if not df.empty and len(tickers) == 1:
                result[tickers[0]] = df
        return result
    except Exception:
        return {}

def fetch_premarket_prices(tickers: list[str]) -> dict:
    """Fetch live/pre-market prices via 5m bars with prepost=True.
    Returns {ticker: last_price}. Empty dict on failure — caller falls back to close."""
    if not tickers:
        return {}
    result = {}
    try:
        batch_size = 25
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i+batch_size]
            ticker_str = ' '.join(batch)
            df = yf.download(ticker_str, period='1d', interval='5m', prepost=True,
                             progress=False, group_by='ticker', threads=True,
                             timeout=FETCH_TIMEOUT + 5)
            if df is None or df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                for t in batch:
                    if t in df.columns.get_level_values(0):
                        sub = df[t]['Close'].dropna()
                        if not sub.empty:
                            result[t] = float(sub.iloc[-1])
            else:
                closes = df['Close'].dropna()
                if not closes.empty and len(batch) == 1:
                    result[batch[0]] = float(closes.iloc[-1])
            if i + batch_size < len(tickers):
                time.sleep(0.3)
    except Exception:
        pass
    return result

# ── Phase 3: BB Metrics + Enhanced Analysis ──────────────────

def compute_enhanced_metrics(ticker: str, df: pd.DataFrame, name: str,
                              sector: str | None, regime: dict,
                              premarket_price: float | None = None) -> dict | None:
    """Compute BB metrics + momentum/trend + composite score.
    If premarket_price is given (>0), %B / band distance / touch flags are
    computed against the live pre-market price instead of the last close,
    so the 22:00 KST scan reflects pre-market action."""
    try:
        closes = df['Close'].dropna()
        volumes = df['Volume'].dropna()
        if len(closes) < BB_PERIOD:
            return None

        avg_dollar_vol = (closes * volumes).mean() / 1_000_000

        # BB calculation (daily closes — the band itself stays close-based)
        ma20 = closes.rolling(BB_PERIOD).mean()
        std20 = closes.rolling(BB_PERIOD).std()
        upper = ma20 + BB_STD * std20
        lower = ma20 - BB_STD * std20

        last_close = closes.iloc[-1]
        last_upper = upper.dropna().iloc[-1]
        last_lower = lower.dropna().iloc[-1]
        last_ma20 = ma20.dropna().iloc[-1]
        band_width = last_upper - last_lower

        # Live price: pre-market when available, else last close
        live = premarket_price if premarket_price is not None and premarket_price > 0 else last_close
        pct_b = (live - last_lower) / band_width if band_width > 0 else float('nan')
        rsi_val = calc_rsi(closes, RSI_PERIOD)

        # Returns (close-based — momentum context)
        ret_5d = safe_float((closes.iloc[-1] / closes.iloc[-6] - 1) * 100) if len(closes) >= 6 else None
        ret_20d = safe_float((closes.iloc[-1] / closes.iloc[-21] - 1) * 100) if len(closes) >= 21 else None
        ret_1d = safe_float((closes.iloc[-1] / closes.iloc[-2] - 1) * 100) if len(closes) >= 2 else None

        # Volume ratio (current vol vs 20d avg)
        vol_20d_avg = volumes.rolling(20).mean().dropna()
        vol_ratio = safe_float(volumes.iloc[-1] / vol_20d_avg.iloc[-1]) if len(vol_20d_avg) > 0 else 1.0

        # MA position (vs MA5, MA20, MA50)
        ma5 = closes.rolling(5).mean().dropna()
        ma50 = closes.rolling(50).mean().dropna()
        dist_ma5 = safe_float((last_close / ma5.iloc[-1] - 1) * 100) if len(ma5) > 0 else None
        dist_ma20 = safe_float((last_close / last_ma20 - 1) * 100)
        dist_ma50 = safe_float((last_close / ma50.iloc[-1] - 1) * 100) if len(ma50) > 0 else None

        # Trend state
        trend_alignment = 0
        if dist_ma5 is not None and dist_ma5 > 0: trend_alignment += 1
        if dist_ma20 > 0: trend_alignment += 1
        if dist_ma50 is not None and dist_ma50 > 0: trend_alignment += 1
        # -3 (all bear) to +3 (all bull)

        # Distance from band (live-price based)
        dist_upper_pct = safe_float((live - last_upper) / last_upper * 100) if last_upper > 0 else None
        dist_lower_pct = safe_float((live - last_lower) / last_lower * 100) if last_lower > 0 else None
        band_width_pct = safe_float(band_width / last_ma20 * 100) if last_ma20 > 0 and band_width > 0 else None

        # Determine side (lower/upper/neutral) — live price
        is_upper_touch = pct_b >= UPPER_TOUCH_THRESHOLD
        is_lower_touch = pct_b <= LOWER_TOUCH_THRESHOLD

        # ── Composite Score ──
        score = compute_composite_score(
            pct_b=pct_b, rsi=rsi_val, dollar_vol_m=avg_dollar_vol,
            ret_5d=ret_5d, ret_1d=ret_1d,
            vol_ratio=vol_ratio, trend_alignment=trend_alignment,
            dist_ma20=dist_ma20, dist_ma50=dist_ma50,
            sector=sector, regime=regime,
            is_upper_touch=is_upper_touch, is_lower_touch=is_lower_touch,
        )

        return {
            'ticker': ticker,
            'name': name,
            'sector': sector,
            'close': round(safe_float(last_close), 2),
            'premarket': round(safe_float(live), 2) if premarket_price is not None and premarket_price > 0 else None,
            'premarket_pct': round(safe_float((live / last_close - 1) * 100), 2) if premarket_price is not None and premarket_price > 0 and last_close > 0 else None,
            'upper': round(safe_float(last_upper), 2),
            'lower': round(safe_float(last_lower), 2),
            'ma20': round(safe_float(last_ma20), 2),
            'pct_b': round(safe_float(pct_b), 3),
            'rsi': round(safe_float(rsi_val), 1),
            'avg_dollar_vol_m': round(avg_dollar_vol, 1),
            'ret_1d_pct': round(ret_1d, 2) if ret_1d is not None else None,
            'ret_5d_pct': round(ret_5d, 2) if ret_5d is not None else None,
            'ret_20d_pct': round(ret_20d, 2) if ret_20d is not None else None,
            'vol_ratio': round(vol_ratio, 2),
            'dist_ma20_pct': round(dist_ma20, 2),
            'dist_ma50_pct': round(dist_ma50, 2) if dist_ma50 is not None else None,
            'band_width_pct': round(band_width_pct, 2) if band_width_pct is not None else None,
            'trend_alignment': trend_alignment,
            'composite_score': round(score['total'], 1),
            'score_breakdown': score['breakdown'],
            'score_detail': score['detail'],
        }
    except Exception as e:
        print(f"⚠️ {ticker} metrics failed: {e}", file=sys.stderr)
        return None

# ── Phase 4: Composite Scoring Engine ────────────────────────

def compute_composite_score(pct_b, rsi, dollar_vol_m, ret_5d, ret_1d,
                             vol_ratio, trend_alignment, dist_ma20, dist_ma50,
                             sector, regime, is_upper_touch, is_lower_touch) -> dict:
    """
    6-dimension composite scoring (100pts):
      1. BB Touch Quality (25pts)
      2. RSI Context (15pts)
      3. Liquidity (10pts)
      4. Momentum (15pts)
      5. Trend Quality (15pts)
      6. Sector Regime Alignment (20pts)
    """
    pct_b = safe_float(pct_b)
    rsi = safe_float(rsi)
    dollar_vol_m = safe_float(dollar_vol_m)

    breakdown = {}
    detail = {}

    # ── 1. BB Touch Quality (25pts) ──
    if is_lower_touch:
        # %B 0.00 → 25pts, %B 0.10 → 15pts
        bb_score = 25 - (pct_b / 0.10) * 10
        bb_score = clamp(bb_score, 10, 25)
        bb_label = '하단터치'
    elif is_upper_touch:
        # %B 1.00 → 22pts, %B 0.90 → 12pts, capped so breakouts don't overscore
        if pct_b >= 1.0:
            bb_score = 22
        else:
            bb_score = 12 + ((pct_b - 0.90) / 0.10) * 10
        bb_score = clamp(bb_score, 10, 22)
        bb_label = '상단터치'
    else:
        bb_score = 0
        bb_label = '중립'

    breakdown['bb_touch'] = round(bb_score, 1)
    detail['bb_touch'] = f'{bb_label} (pct_b={pct_b:.3f}) → {bb_score:.1f}/{W_BB_TOUCH}'

    # ── 2. RSI Context (15pts) ──
    if is_lower_touch:
        # Oversold bounce: RSI < 30 → high score
        if rsi <= 25:
            rsi_score = 15
        elif rsi <= 35:
            rsi_score = 12 + (35 - rsi) / 10 * 3
        elif rsi <= 50:
            rsi_score = 5 + (50 - rsi) / 15 * 7
        else:
            rsi_score = 2
        rsi_label = '과매도 바운스 기대'
    elif is_upper_touch:
        # Momentum: RSI 60-80 → good, >80 → overbought caution
        if 60 <= rsi <= 75:
            rsi_score = 12
        elif 75 < rsi <= 85:
            rsi_score = 8
        elif rsi > 85:
            rsi_score = 3  # too extended
        elif 45 <= rsi < 60:
            rsi_score = 7
        else:
            rsi_score = 2
        rsi_label = '모멘텀 지속'
    else:
        rsi_score = 0
        rsi_label = ''

    rsi_score = clamp(rsi_score, 0, 15)
    breakdown['rsi'] = round(rsi_score, 1)
    detail['rsi'] = f'RSI={rsi:.1f} ({rsi_label}) → {rsi_score:.1f}/{W_RSI}'

    # ── 3. Liquidity (10pts) ──
    if dollar_vol_m >= 20000:
        liq_score = 10
    elif dollar_vol_m >= 10000:
        liq_score = 9
    elif dollar_vol_m >= 5000:
        liq_score = 8
    elif dollar_vol_m >= 2000:
        liq_score = 7
    elif dollar_vol_m >= 1000:
        liq_score = 6
    elif dollar_vol_m >= 500:
        liq_score = 4
    elif dollar_vol_m >= 200:
        liq_score = 2
    else:
        liq_score = 0
    breakdown['liquidity'] = liq_score
    detail['liquidity'] = f'거래대금 ${dollar_vol_m:.0f}M → {liq_score}/{W_LIQUIDITY}'

    # ── 4. Momentum (15pts) ──
    mom_score = 0
    mom_factors = []

    # Volume surge
    vol_ratio = safe_float(vol_ratio)
    if vol_ratio >= 2.0:
        mom_score += 5
        mom_factors.append('거래량 2배↑')
    elif vol_ratio >= 1.5:
        mom_score += 3
        mom_factors.append('거래량 증가')
    elif vol_ratio >= 1.0:
        mom_score += 1
        mom_factors.append('거래량 보통')

    # 5d return context
    ret_5d = safe_float(ret_5d) if ret_5d is not None else 0
    ret_1d = safe_float(ret_1d) if ret_1d is not None else 0

    if is_lower_touch:
        # For lower touches: recent sharp drop + today stabilizing → bounce setup
        if ret_5d < -5:
            mom_score += 5  # deep pullback → mean reversion setup
            mom_factors.append(f'5일 {ret_5d:.1f}% 급락')
        elif ret_5d < -2:
            mom_score += 4
            mom_factors.append(f'5일 {ret_5d:.1f}% 하락')
        else:
            mom_score += 1

        # Today: slight green or red-light → stabilization
        if ret_1d > 0:
            mom_score += 3  # 반등시도
            mom_factors.append('당일 반등')
        elif ret_1d > -1:
            mom_score += 1  # 소폭하락 유지
        else:
            mom_score += 0  # 계속 하락
            mom_factors.append('당일 추가하락')

    elif is_upper_touch:
        # For upper touches: consistent positive momentum
        if ret_5d > 10:
            mom_score += 5  # strong momentum
            mom_factors.append(f'5일 {ret_5d:.1f}% 급등')
        elif ret_5d > 5:
            mom_score += 4
            mom_factors.append(f'5일 {ret_5d:.1f}% 상승')
        elif ret_5d > 2:
            mom_score += 3
            mom_factors.append(f'5일 {ret_5d:.1f}% 상승')
        else:
            mom_score += 1

        if ret_1d > 2:
            mom_score += 4
            mom_factors.append('당일 강한 상승')
        elif ret_1d > 0:
            mom_score += 3
            mom_factors.append('당일 상승')
        else:
            mom_score += 1
            mom_factors.append('당일 숨고르기')

    mom_score = clamp(mom_score, 0, 15)
    breakdown['momentum'] = mom_score
    detail['momentum'] = f'Momentum ({", ".join(mom_factors)}) → {mom_score}/{W_MOMENTUM}'

    # ── 5. Trend Quality (15pts) ──
    trend_score = 0
    trend_factors = []

    trend_alignment = int(trend_alignment) if trend_alignment is not None else 0

    if is_lower_touch:
        # For lower touches: how extended below MA?
        dist_ma20 = safe_float(dist_ma20)
        if dist_ma20 < -5:
            trend_score += 7  # deeply below MA20 → stretched
            trend_factors.append(f'MA20 -{abs(dist_ma20):.1f}% 이격')
        elif dist_ma20 < -3:
            trend_score += 5
            trend_factors.append(f'MA20 -{abs(dist_ma20):.1f}% 이격')
        elif dist_ma20 < 0:
            trend_score += 3
        else:
            trend_score += 1

        dist_ma50 = safe_float(dist_ma50) if dist_ma50 is not None else 0
        if dist_ma50 < -10:
            trend_score += 6
            trend_factors.append(f'MA50 -{abs(dist_ma50):.1f}% 이격')
        elif dist_ma50 < -5:
            trend_score += 4
            trend_factors.append(f'MA50 이하')
        elif dist_ma50 < 0:
            trend_score += 2
        else:
            trend_score += 1

        # Bandwidth squeeze → explosive potential
        # (handled in bb_touch already)

    elif is_upper_touch:
        # For upper touches: riding the trend
        dist_ma20 = safe_float(dist_ma20)
        if dist_ma20 > 5:
            trend_score += 5  # strong uptrend
            trend_factors.append(f'MA20 +{dist_ma20:.1f}% 이격')
        elif dist_ma20 > 3:
            trend_score += 4
            trend_factors.append(f'MA20 +{dist_ma20:.1f}% 이격')
        elif dist_ma20 > 0:
            trend_score += 3
        else:
            trend_score += 1
            trend_factors.append('MA20 이하 위험')

        dist_ma50 = safe_float(dist_ma50) if dist_ma50 is not None else 0
        if dist_ma50 > 10:
            trend_score += 4
            trend_factors.append(f'MA50 위')
        elif dist_ma50 > 5:
            trend_score += 3
        elif dist_ma50 > 0:
            trend_score += 2
        else:
            trend_score += 0
            trend_factors.append('MA50 이하 약세')

        # Bandwidth expansion → momentum
        # (handled elsewhere)

    trend_score = clamp(trend_score, 0, 15)
    breakdown['trend'] = trend_score
    detail['trend'] = f'Trend ({", ".join(trend_factors) if trend_factors else "neutral"}) → {trend_score}/{W_TREND}'

    # ── 6. Sector Regime Alignment (20pts) ──
    sector_score = 0
    sector_note = ''

    if sector and regime.get('sector_rotation'):
        rot = regime['sector_rotation']
        top_sectors = [s['sector'] for s in rot.get('top_sectors', [])]
        bottom_sectors = [s['sector'] for s in rot.get('bottom_sectors', [])]

        if sector in top_sectors[:2]:
            sector_score = 18
            sector_note = f'주도섹터 ({sector})'
        elif sector in top_sectors[:4]:
            sector_score = 14
            sector_note = f'강세섹터 ({sector})'
        elif sector in top_sectors:
            sector_score = 10
            sector_note = f'상대우위 ({sector})'
        elif sector in bottom_sectors:
            sector_score = 3
            sector_note = f'약세섹터 ({sector})'
        else:
            sector_score = 7
            sector_note = f'중립섹터 ({sector})'

        # Regime context bonus
        theme = rot.get('theme', '')
        if theme == 'tech_leadership' and sector in ('Technology', 'Communication Services', 'Consumer Discretionary'):
            sector_score = min(sector_score + 4, 20)
            sector_note += ' + 기술주 강세'
        elif theme == 'defensive_flight' and sector in ('Utilities', 'Consumer Staples', 'Health Care'):
            sector_score = min(sector_score + 4, 20)
            sector_note += ' + 방어주 선호'
        elif theme == 'cyclical_rotation' and sector in ('Financials', 'Industrials', 'Materials', 'Energy'):
            sector_score = min(sector_score + 4, 20)
            sector_note += ' + 순환주 강세'
        elif theme == 'broad_weakness':
            sector_score = max(sector_score - 3, 0)
            sector_note += ' (약세장 할인)'

        # Risk appetite bonus for appropriate touches
        risk = regime.get('risk_appetite', '')
        if risk == 'risk_on' and is_upper_touch:
            sector_score = min(sector_score + 3, 20)
            sector_note += ' + 위험선호'
        elif risk == 'risk_off' and is_lower_touch:
            sector_score = min(sector_score + 3, 20)
            sector_note += ' + 방어적'

    # If no sector info, give neutral
    if not sector:
        sector_score = 8
        sector_note = '섹터정보 없음'

    sector_score = clamp(sector_score, 0, 20)
    breakdown['sector_regime'] = sector_score
    detail['sector_regime'] = f'{sector_note} → {sector_score}/{W_SECTOR}'

    total = bb_score + rsi_score + liq_score + mom_score + trend_score + sector_score
    total = clamp(total, 0, 100)

    return {
        'total': total,
        'breakdown': breakdown,
        'detail': detail,
    }

# ── Chart Generation ──────────────────────────────────────────

def generate_bb_chart(ticker: str, df: pd.DataFrame, output_path: str) -> str | None:
    try:
        import matplotlib
        matplotlib.use('Agg')
        import mplfinance as mpf

        closes = df['Close'].dropna()
        if len(closes) < BB_PERIOD:
            return None

        ma20 = closes.rolling(BB_PERIOD).mean()
        std20 = closes.rolling(BB_PERIOD).std()
        upper = ma20 + BB_STD * std20
        lower = ma20 - BB_STD * std20

        last_close = closes.iloc[-1]
        last_upper = upper.dropna().iloc[-1]
        last_lower = lower.dropna().iloc[-1]
        pct_b = (last_close - last_lower) / (last_upper - last_lower) if (last_upper - last_lower) > 0 else 0

        ap_upper = mpf.make_addplot(upper, color='#f23645', width=0.8, linestyle='--')
        ap_lower = mpf.make_addplot(lower, color='#22ab94', width=0.8, linestyle='--')
        ap_ma20 = mpf.make_addplot(ma20, color='#f7d44a', width=1.0)

        mc = mpf.make_marketcolors(
            up='#26a69a', down='#ef5350',
            edge='inherit', wick='inherit',
            volume={'up': '#26a69a55', 'down': '#ef535055'}
        )
        s = mpf.make_mpf_style(
            marketcolors=mc,
            facecolor='#131722',
            figcolor='#131722',
            gridcolor='#2a2e39',
            gridstyle='--',
            y_on_right=True,
        )

        band_label = '↑BREAKOUT' if pct_b > 1.0 else ('↓BREAKDOWN' if pct_b < 0 else f'%B {pct_b:.2f}')
        title = f'{ticker} — BB(20,2σ) | O=${last_close:.2f} | {band_label}'

        fig, axes = mpf.plot(
            df, type='candle', volume=True,
            addplot=[ap_upper, ap_lower, ap_ma20],
            style=s,
            title=f'\n{title}',
            figsize=(12, 7),
            warn_too_much_data=1000,
            returnfig=True,
            tight_layout=False,
        )

        fig.subplots_adjust(left=0.08, right=0.92, top=0.93, bottom=0.08)
        fig.savefig(output_path, dpi=150, facecolor='#131722')
        import matplotlib.pyplot as plt
        plt.close(fig)
        return output_path
    except Exception as e:
        print(f"   ⚠️ Chart failed for {ticker}: {e}", file=sys.stderr)
        return None

# ── Sector Name Resolution ─────────────────────────────────────

# Static mapping for common tickers (faster than yfinance .info)
KNOWN_SECTORS = {
    'AAPL': 'Technology', 'MSFT': 'Technology', 'GOOGL': 'Communication Services',
    'GOOG': 'Communication Services', 'AMZN': 'Consumer Discretionary', 'NVDA': 'Technology',
    'META': 'Communication Services', 'TSLA': 'Consumer Discretionary', 'BRK-B': 'Financials',
    'BRK.A': 'Financials', 'JPM': 'Financials', 'V': 'Financials', 'JNJ': 'Health Care',
    'WMT': 'Consumer Staples', 'PG': 'Consumer Staples', 'MA': 'Financials',
    'UNH': 'Health Care', 'HD': 'Consumer Discretionary', 'DIS': 'Communication Services',
    'PYPL': 'Financials', 'ADBE': 'Technology', 'NFLX': 'Communication Services',
    'CMCSA': 'Communication Services', 'PEP': 'Consumer Staples', 'KO': 'Consumer Staples',
    'CSCO': 'Technology', 'INTC': 'Technology', 'AMD': 'Technology', 'CRM': 'Technology',
    'TMO': 'Health Care', 'ABT': 'Health Care', 'NKE': 'Consumer Discretionary',
    'VZ': 'Communication Services', 'QCOM': 'Technology', 'TXN': 'Technology',
    'MRK': 'Health Care', 'BA': 'Industrials', 'CAT': 'Industrials', 'GE': 'Industrials',
    'MMM': 'Industrials', 'HON': 'Industrials', 'UPS': 'Industrials', 'FDX': 'Industrials',
    'XOM': 'Energy', 'CVX': 'Energy', 'COP': 'Energy', 'SLB': 'Energy',
    'OXY': 'Energy', 'EOG': 'Energy', 'PSX': 'Energy', 'VLO': 'Energy',
    'C': 'Financials', 'BAC': 'Financials', 'WFC': 'Financials', 'GS': 'Financials',
    'MS': 'Financials', 'USB': 'Financials', 'PNC': 'Financials', 'AXP': 'Financials',
    'COST': 'Consumer Staples', 'TGT': 'Consumer Discretionary', 'LOW': 'Consumer Discretionary',
    'MCD': 'Consumer Discretionary', 'SBUX': 'Consumer Discretionary',
    'NEE': 'Utilities', 'DUK': 'Utilities', 'SO': 'Utilities', 'D': 'Utilities',
    'AEP': 'Utilities', 'SRE': 'Utilities', 'EXC': 'Utilities',
    'AMT': 'Real Estate', 'PLD': 'Real Estate', 'CCI': 'Real Estate', 'EQIX': 'Real Estate',
    'LLY': 'Health Care', 'PFE': 'Health Care', 'ABBV': 'Health Care',
    'GILD': 'Health Care', 'AMGN': 'Health Care', 'BSX': 'Health Care',
    'MU': 'Technology', 'AVGO': 'Technology', 'ASML': 'Technology', 'AMAT': 'Technology',
    'LRCX': 'Technology', 'KLAC': 'Technology', 'TSM': 'Technology',
    'PLTR': 'Technology', 'SNOW': 'Technology', 'DDOG': 'Technology', 'MDB': 'Technology',
    'NOW': 'Technology', 'PANW': 'Technology', 'CRWD': 'Technology', 'ZS': 'Technology',
    'NET': 'Technology', 'S': 'Technology', 'RDDT': 'Communication Services',
    'SPCE': 'Industrials', 'RKLB': 'Industrials', 'ASTS': 'Communication Services',
    'QTEX': 'Technology', 'SHIM': 'Technology', 'APLD': 'Technology',
    'NU': 'Financials', 'VCX': 'Real Estate', 'ASPN': 'Industrials',
    'FWRD': 'Industrials', 'MSTR': 'Technology',
}

def resolve_sector(ticker: str, screener_sector: str | None) -> str | None:
    """Resolve sector: screener data > static mapping > yfinance fallback."""
    if screener_sector and screener_sector.strip():
        return screener_sector.strip()
    if ticker.upper() in KNOWN_SECTORS:
        return KNOWN_SECTORS[ticker.upper()]
    # yfinance fallback (lazy, only for unknown tickers)
    try:
        info = yf.Ticker(ticker).info
        sec = info.get('sector')
        if sec:
            # Cache it
            KNOWN_SECTORS[ticker.upper()] = sec
            return sec
    except:
        pass
    return None

# ── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--charts', action='store_true', help='Generate BB charts')
    parser.add_argument('--json-only', action='store_true', help='Only output JSON')
    args = parser.parse_args()

    now_utc = datetime.utcnow()
    now_kst = now_utc + timedelta(hours=9)
    date_str = now_kst.strftime('%Y-%m-%d %H:%M KST')

    # ══════════════════════════════════════════════════════════
    # PHASE 0: Market Regime
    # ══════════════════════════════════════════════════════════
    regime = fetch_market_regime()
    if not args.json_only:
        print(f"📊 **컴프리헨시브 BB 터치 스캔 v4** | {date_str}")
        print(f"BB({BB_PERIOD},{BB_STD}σ) | 거래대금 상위 {TRADING_VALUE_TOP_N} | 레버리지 제외")
        print()
        rot = regime.get('sector_rotation', {})
        print(f"🌡️ **시장 레짐**: VIX {regime.get('vix', '?')} ({regime.get('vix_zone', '?')}) | "
              f"위험선호: {regime.get('risk_appetite', '?')}")
        print(f"🔄 **순환테마**: {rot.get('theme_desc', '?')}")
        spy_idx = regime.get('indices', {}).get('SPY', {})
        qqq_idx = regime.get('indices', {}).get('QQQ', {})
        print(f"📈 SPY 5d: {safe_pct(spy_idx.get('ret_5d'))} | QQQ 5d: {safe_pct(qqq_idx.get('ret_5d'))} | "
              f"QQQ-SPY: {regime.get('qqq_vs_spy_5d', 0):+.1f}%")
        tops = rot.get('top_sectors', [])
        if tops:
            top_str = ' | '.join([f"{s['sector']} {s['ret_5d']:+.1f}%" for s in tops[:3]])
            print(f"🏆 주도섹터: {top_str}")
        bottoms = rot.get('bottom_sectors', [])
        if bottoms:
            bot_str = ' | '.join([f"{s['sector']} {s['ret_5d']:+.1f}%" for s in bottoms])
            print(f"🚫 약세섹터: {bot_str}")
        print()

    # ══════════════════════════════════════════════════════════
    # PHASE 1: Screener
    # ══════════════════════════════════════════════════════════
    print("🔄 Yahoo Finance 거래대금 상위 종목 스크리닝 중...", file=sys.stderr)
    screener_results = fetch_screener_top(SCREENER_COUNT)
    if not screener_results:
        print("❌ 스크리너 데이터를 가져올 수 없습니다.")
        sys.exit(1)
    print(f"   ✓ {len(screener_results)} 종목 확보 (레버리지 제외)", file=sys.stderr)

    top_tickers = screener_results[:TRADING_VALUE_TOP_N]
    ticker_list = [r['ticker'] for r in top_tickers]
    ticker_meta = {r['ticker']: r for r in top_tickers}

    # ══════════════════════════════════════════════════════════
    # PHASE 2: Download data
    # ══════════════════════════════════════════════════════════
    print(f"🔄 상위 {len(ticker_list)} 종목 데이터 다운로드 중...", file=sys.stderr)
    batch_size = 25
    all_data = {}
    for i in range(0, len(ticker_list), batch_size):
        batch = ticker_list[i:i+batch_size]
        batch_data = download_batch(batch)
        all_data.update(batch_data)
        print(f"   ... {min(i+batch_size, len(ticker_list))}/{len(ticker_list)}", file=sys.stderr)
        if i + batch_size < len(ticker_list):
            time.sleep(0.5)
    print(f"   ✓ {len(all_data)} 종목 데이터 확보", file=sys.stderr)

    if len(all_data) < 10:
        print("❌ 충분한 데이터를 가져올 수 없습니다.")
        sys.exit(1)

    # ══════════════════════════════════════════════════════════
    # PHASE 2.5: Live pre-market prices
    # ══════════════════════════════════════════════════════════
    print("🔄 프리장/실시간 가격 수집 중 (5m prepost)...", file=sys.stderr)
    premarket_prices = fetch_premarket_prices(ticker_list)
    if premarket_prices:
        print(f"   ✓ {len(premarket_prices)} 종목 프리장 가격 확보", file=sys.stderr)
    else:
        print("   ⚠️ 프리장 가격 없음 — 종가 기준으로 판정", file=sys.stderr)

    # ══════════════════════════════════════════════════════════
    # PHASE 3: Compute enhanced metrics
    # ══════════════════════════════════════════════════════════
    print("🔄 볼린저밴드 + 종합 스코어 계산 중...", file=sys.stderr)
    results = []
    for ticker, df in all_data.items():
        meta = ticker_meta.get(ticker, {})
        name = meta.get('name', ticker)
        sector = resolve_sector(ticker, meta.get('sector'))
        metrics = compute_enhanced_metrics(ticker, df, name, sector, regime,
                                           premarket_prices.get(ticker))
        if metrics:
            results.append(metrics)

    results.sort(key=lambda x: x['composite_score'], reverse=True)
    print(f"   ✓ {len(results)} 종목 분석 완료", file=sys.stderr)

    # ══════════════════════════════════════════════════════════
    # PHASE 4: Classify touches
    # ══════════════════════════════════════════════════════════
    upper_touches = [r for r in results if r['pct_b'] >= UPPER_TOUCH_THRESHOLD]
    lower_touches = [r for r in results if r['pct_b'] <= LOWER_TOUCH_THRESHOLD]
    upper_touches.sort(key=lambda x: x['composite_score'], reverse=True)
    lower_touches.sort(key=lambda x: x['composite_score'], reverse=True)

    above_band = sum(1 for r in results if r['pct_b'] > 1.0)
    below_band = sum(1 for r in results if r['pct_b'] < 0.0)

    # Top picks (top 10 by composite score, cross-section)
    top_picks = sorted(results, key=lambda x: x['composite_score'], reverse=True)[:10]

    # Top lower touch picks (for bounce plays)
    top_lower_picks = lower_touches[:5] if lower_touches else []

    # Top upper touch picks (for momentum plays)
    top_upper_picks = upper_touches[:5] if upper_touches else []

    # ══════════════════════════════════════════════════════════
    # PHASE 5: Charts (optional)
    # ══════════════════════════════════════════════════════════
    chart_map = {}
    if args.charts:
        os.makedirs(CHART_DIR, exist_ok=True)
        all_touches = lower_touches + upper_touches
        chart_count = 0
        print(f"🔄 차트 생성 중... (최대 {MAX_CHARTS}개)", file=sys.stderr)
        for r in all_touches:
            if chart_count >= MAX_CHARTS:
                break
            ticker = r['ticker']
            if ticker in all_data:
                chart_path = os.path.join(CHART_DIR, f"{ticker.lower()}_bb.png")
                result_path = generate_bb_chart(ticker, all_data[ticker], chart_path)
                if result_path:
                    chart_map[ticker] = result_path
                    chart_count += 1
        print(f"   ✓ {chart_count}개 차트 생성 완료", file=sys.stderr)

    # ══════════════════════════════════════════════════════════
    # PHASE 6: JSON Output
    # ══════════════════════════════════════════════════════════
    # Strip verbose score_detail, add S&P 500 flag
    def _json_result(r):
        jr = {k: v for k, v in r.items() if k != 'score_detail'}
        jr['in_sp500'] = r['ticker'] in SP500_TICKERS
        return jr

    json_results = [_json_result(r) for r in results]

    json_upper = [_json_result(r) for r in upper_touches]

    json_lower = [_json_result(r) for r in lower_touches]

    json_top_picks = [_json_result(r) for r in top_picks]

    json_output = {
        'version': 4,
        'scan_time_kst': date_str,
        'scan_time_utc': now_utc.isoformat(),
        'config': {
            'bb_period': BB_PERIOD, 'bb_std': BB_STD,
            'upper_threshold': UPPER_TOUCH_THRESHOLD,
            'lower_threshold': LOWER_TOUCH_THRESHOLD,
            'scoring_weights': {
                'bb_touch': W_BB_TOUCH, 'rsi': W_RSI,
                'liquidity': W_LIQUIDITY, 'momentum': W_MOMENTUM,
                'trend': W_TREND, 'sector_regime': W_SECTOR,
            },
        },
        'market_regime': regime,
        'summary': {
            'total_analyzed': len(results),
            'upper_touches': len(upper_touches),
            'lower_touches': len(lower_touches),
            'breakouts_above': above_band,
            'breakdowns_below': below_band,
        },
        'upper_touches': json_upper,
        'lower_touches': json_lower,
        'top_picks': json_top_picks,
        'top_lower_picks': [_json_result(r) for r in top_lower_picks],
        'top_upper_picks': [_json_result(r) for r in top_upper_picks],
        'chart_map': chart_map,
    }

    json_path = '/tmp/bb_scan_result.json'
    with open(json_path, 'w') as f:
        json.dump(json_output, f, ensure_ascii=False, indent=2)
    print(f"   ✓ JSON 저장: {json_path}", file=sys.stderr)

    # ══════════════════════════════════════════════════════════
    # PHASE 7: Discord Message
    # ══════════════════════════════════════════════════════════

    # ── AUTO-SEND MODE (no LLM): build top-2 picks, send via webhook ──
    if getattr(args, 'auto_send', False):
        out = auto_send_picks(results, chart_map, regime)
        # out이 비어있으면 성공(silent). 비어있지 않으면 웹훅 실패 → stdout 백업.
        if out:
            print(out)
        return

    msg_lines = []
    msg_lines.append(f"📊 **컴프리헨시브 BB 터치 스캔** | {date_str}")
    pm_badge = f" | ⚡ 프리장 반영" if premarket_prices else ""
    msg_lines.append(f"BB({BB_PERIOD},{BB_STD}σ) | 종합스코어링 v4 | 거래대금 상위 {len(results)}종목{pm_badge}")
    msg_lines.append("")

    # Market regime summary
    rot = regime.get('sector_rotation', {})
    theme = rot.get('theme_desc', '')
    vix_str = f"VIX {regime.get('vix', '?')}"
    risk_str = regime.get('risk_appetite', '?')
    msg_lines.append(f"🌡️ **{vix_str}** | {risk_str} | {theme}")

    # Sector leaders
    tops = rot.get('top_sectors', [])[:3]
    if tops:
        leader_str = ' | '.join([f"**{s['sector']}** {s['ret_5d']:+.1f}%" for s in tops])
        msg_lines.append(f"🏆 {leader_str}")
    msg_lines.append("")

    # Summary counts
    touch_emoji = "🟡" if len(upper_touches) > len(lower_touches) else "🟢"
    msg_lines.append(f"📊 총 {len(upper_touches)}개 상단터치 / {len(lower_touches)}개 하단터치 | "
                     f"돌파 {above_band} / 붕괴 {below_band}")
    msg_lines.append("")

    # ── Top Lower Picks (Bounce Plays) ──
    if top_lower_picks:
        msg_lines.append(f"🟢 **하단터치 베스트** (과매도 바운스) | 점수: {W_BB_TOUCH}+{W_RSI}+{W_LIQUIDITY}+{W_MOMENTUM}+{W_TREND}+{W_SECTOR}")
        msg_lines.append("```")
        price_hdr = '현재가' if premarket_prices else '종가'
        msg_lines.append(f"{'순위':<4} {'티커':<7} {price_hdr:>7} {'%B':>5} {'RSI':>5} {'거래대금':>9} {'5일':>7} {'스코어':>6}")
        msg_lines.append("─" * 55)
        for i, r in enumerate(top_lower_picks[:5], 1):
            vol_str = f"{r['avg_dollar_vol_m']:.0f}M"
            ret5 = f"{r['ret_5d_pct']:+.1f}%" if r['ret_5d_pct'] is not None else "N/A"
            score_str = f"{r['composite_score']:.0f}"
            if r.get('premarket') is not None:
                px_str = f"${r['premarket']:>6.2f}*"
            else:
                px_str = f"${r['close']:>6.2f}"
            msg_lines.append(f"{i:<4} {r['ticker']:<7} {px_str:>7} {r['pct_b']:>4.2f} {r['rsi']:>4.0f} {vol_str:>9} {ret5:>7} {score_str:>6}")
        msg_lines.append("```")
        msg_lines.append("")

    # ── Top Upper Picks (Momentum Plays) ──
    if top_upper_picks:
        msg_lines.append(f"🚀 **상단터치 베스트** (모멘텀 지속)")
        msg_lines.append("```")
        price_hdr = '현재가' if premarket_prices else '종가'
        msg_lines.append(f"{'순위':<4} {'티커':<7} {price_hdr:>7} {'%B':>5} {'RSI':>5} {'거래대금':>9} {'5일':>7} {'스코어':>6}")
        msg_lines.append("─" * 55)
        for i, r in enumerate(top_upper_picks[:5], 1):
            vol_str = f"{r['avg_dollar_vol_m']:.0f}M"
            ret5 = f"{r['ret_5d_pct']:+.1f}%" if r['ret_5d_pct'] is not None else "N/A"
            score_str = f"{r['composite_score']:.0f}"
            if r.get('premarket') is not None:
                px_str = f"${r['premarket']:>6.2f}*"
            else:
                px_str = f"${r['close']:>6.2f}"
            msg_lines.append(f"{i:<4} {r['ticker']:<7} {px_str:>7} {r['pct_b']:>4.2f} {r['rsi']:>4.0f} {vol_str:>9} {ret5:>7} {score_str:>6}")
        msg_lines.append("```")
        msg_lines.append("")

    if premarket_prices:
        msg_lines.append("⚡ * = 프리장 현재가 | %B·터치판정 프리장 가격 기준")

    # ── Score Detail for Top Picks Overall ──
    msg_lines.append(f"📋 **종합 TOP 10** (100점 만점)")
    msg_lines.append("```")
    msg_lines.append(f"{'순위':<4} {'티커':<7} {'섹터':<16} {'BB':<5} {'RSI':<5} {'유동':<5} {'모멘텀':<6} {'추세':<5} {'섹터':<5} {'총점':<5}")
    msg_lines.append("─" * 65)
    for i, r in enumerate(top_picks[:10], 1):
        bd = r.get('score_breakdown', {})
        sec = (r.get('sector') or '?')[:14]
        bb_s = f"{bd.get('bb_touch', 0):.0f}"
        rsi_s = f"{bd.get('rsi', 0):.0f}"
        liq_s = f"{bd.get('liquidity', 0):.0f}"
        mom_s = f"{bd.get('momentum', 0):.0f}"
        tr_s = f"{bd.get('trend', 0):.0f}"
        sec_s = f"{bd.get('sector_regime', 0):.0f}"
        total_s = f"{r['composite_score']:.0f}"
        msg_lines.append(f"{i:<4} {r['ticker']:<7} {sec:<16} {bb_s:<5} {rsi_s:<5} {liq_s:<5} {mom_s:<6} {tr_s:<5} {sec_s:<5} {total_s:<5}")
    msg_lines.append("```")

    # Touch opportunity summary
    avg_score = sum(r['composite_score'] for r in results) / len(results) if results else 0
    msg_lines.append(f"📊 평균 스코어: **{avg_score:.1f}** | 70↑: {sum(1 for r in results if r['composite_score'] >= 70)} | "
                     f"50↓: {sum(1 for r in results if r['composite_score'] <= 50)}")

    discord_msg = "\n".join(msg_lines)

    # Send to Discord (skip if json-only — cron agent handles posting)
    if not args.json_only:
        print("🔄 Discord 웹후크 전송 중...", file=sys.stderr)
        success = send_discord(discord_msg)
        if success:
            print("   ✓ Discord 전송 성공", file=sys.stderr)
        else:
            print("   ⚠️ Discord 전송 실패", file=sys.stderr)

    # Send charts (skip if json-only — cron agent handles)
    if not args.json_only and args.charts and chart_map:
        sent_charts = 0
        for r in top_lower_picks[:3] + top_upper_picks[:3]:
            t = r['ticker']
            if t in chart_map and sent_charts < 5:
                chart_path = chart_map[t]
                side = "하단터치" if r['pct_b'] <= LOWER_TOUCH_THRESHOLD else "상단터치"
                chart_msg = (f"📊 {t} — {side} | 점수 {r['composite_score']:.0f}/100 | "
                             f"%B {r['pct_b']:.2f} | RSI {r['rsi']:.1f} | "
                             f"5d {r.get('ret_5d_pct', 0):+.1f}%")
                if send_discord(chart_msg, image_path=chart_path):
                    sent_charts += 1
                    time.sleep(0.5)
        print(f"   ✓ {sent_charts}개 차트 전송", file=sys.stderr)

    # Also print to stdout
    if not args.json_only:
        print(discord_msg)
        print()
        # Print score detail for top picks
        print("📋 **스코어 상세 (TOP 5)**")
        for r in top_picks[:5]:
            sd = r.get('score_detail', {})
            print(f"\n**{r['ticker']}** ({r.get('sector', '?')}) — 총점 {r['composite_score']:.1f}/100")
            for k, v in sd.items():
                print(f"   • {v}")

if __name__ == '__main__':
    main()
