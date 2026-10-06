#!/usr/bin/env python3
"""
Mars Paper-Trading Dashboard — 모의투자 실시간 대시보드
데이터 소스: ~/.hermes/scripts/swing-portfolio.json (cron이 갱신) + Finnhub 실시간 시세
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(os.environ.get("DASH_PORT", "8443"))
PORTFOLIO_PATH = os.environ.get(
    "PORTFOLIO_PATH", "/home/ubuntu/marsAI/paper-trading/swing-portfolio.json"
)
SEED = 100_000.0
ET_TZ = timezone(-__import__("datetime").timedelta(hours=4))  # EDT (DST 대략 처리)

# ---------------------------------------------------------------- Finnhub

_FINNHUB_KEY = None
_price_cache = {}          # ticker -> {"price": float, "dp": float, "ts": float}
PRICE_TTL = 45             # 초
FINNHUB_ENV_CANDIDATES = [
    os.path.expanduser("~/.hermes/scripts/.env"),
    os.path.expanduser("~/etf-alarm/.env"),
]


def _finnhub_key():
    global _FINNHUB_KEY
    if _FINNHUB_KEY:
        return _FINNHUB_KEY
    for p in FINNHUB_ENV_CANDIDATES:
        try:
            with open(p) as f:
                for line in f:
                    if line.startswith("FINNHUB") and "=" in line:
                        k, v = line.strip().split("=", 1)
                        v = v.strip().strip('"').strip("'")
                        if v:
                            _FINNHUB_KEY = v
                            return _FINNHUB_KEY
        except OSError:
            continue
    return None


def _http_json(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_quote(ticker):
    """Finnhub /quote → {price, dp(일간 %, 이미 %값)}. 45초 캐시. 실패 시 None."""
    now = time.time()
    c = _price_cache.get(ticker)
    if c and now - c["ts"] < PRICE_TTL:
        return c
    key = _finnhub_key()
    price = dp = None
    if key:
        try:
            q = _http_json(f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={key}")
            price = q.get("c") or None
            dp = q.get("dp")  # 이미 % 값 — ×100 금지
            if price == 0:
                price = None
        except Exception:
            pass
    # yfinance 폴백 (regularMarketPrice 보정)
    if price is None:
        try:
            import yfinance as yf

            info = yf.Ticker(ticker).fast_info
            price = float(info.last_price) if info.last_price else None
        except Exception:
            pass
    result = {"price": price, "dp": dp, "ts": now}
    if price is not None:
        _price_cache[ticker] = result
    return result


# ---------------------------------------------------------------- 일봉 시계열

SERIES_TTL = 3600                     # 1시간 (일봉은 자주 안 바뀜)
SERIES_RANGE = "6mo"
_HERE = os.path.dirname(os.path.abspath(__file__))
SERIES_CACHE_PATH = os.path.join(_HERE, ".series_cache.json")
_series_cache = {}
_series_lock = threading.Lock()


def _load_series_cache():
    with _series_lock:
        if not _series_cache:
            try:
                with open(SERIES_CACHE_PATH) as f:
                    _series_cache.update(json.load(f))
            except Exception:
                pass
        return _series_cache


def _save_series_cache():
    try:
        with _series_lock:
            data = dict(_series_cache)
        tmp = SERIES_CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, SERIES_CACHE_PATH)
    except Exception:
        pass


def _num(lst, i):
    try:
        v = lst[i]
        return None if v is None else round(float(v), 2)
    except Exception:
        return None


def get_series(ticker, rng=SERIES_RANGE):
    """Yahoo 일봉 → {d:[날짜],c:[종가],o,h,l,v:[거래량]}. 1시간 디스크 캐시."""
    now = time.time()
    cache = _load_series_cache()
    key = f"{ticker}|{rng}"
    ent = cache.get(key)
    if ent and now - ent.get("ts", 0) < SERIES_TTL:
        return ent.get("data")
    data = None
    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               + urllib.parse.quote(ticker) + f"?range={rng}&interval=1d")
        raw = _http_json(url, timeout=8)
        res = ((raw.get("chart") or {}).get("result") or [None])[0]
        if res:
            ts = res.get("timestamp") or []
            ind = res.get("indicators") or {}
            q = (ind.get("quote") or [{}])[0]
            closes = ((ind.get("adjclose") or [{}])[0] or {}).get("adjclose") or q.get("close") or []
            d, c, o, h, l, v = [], [], [], [], [], []
            for i, t in enumerate(ts):
                if i >= len(closes) or closes[i] is None:
                    continue
                d.append(datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"))
                c.append(round(float(closes[i]), 2))
                o.append(_num(q.get("open") or [], i))
                h.append(_num(q.get("high") or [], i))
                l.append(_num(q.get("low") or [], i))
                v.append(int((q.get("volume") or [0])[i] or 0) if i < len(q.get("volume") or []) else 0)
            if d:
                data = {"d": d, "c": c, "o": o, "h": h, "l": l, "v": v}
    except Exception:
        data = None
    if data:
        with _series_lock:
            _series_cache[key] = {"ts": now, "data": data}
        _save_series_cache()
        return data
    return ent.get("data") if ent else None


# ---------------------------------------------------------------- Portfolio


def load_portfolio():
    with open(PORTFOLIO_PATH) as f:
        return json.load(f)


def is_us_market_open():
    now = datetime.now(ET_TZ)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= hm < 16 * 60


def build_payload():
    pf = load_portfolio()
    positions = pf.get("positions", [])
    history = pf.get("history", [])
    watchlist = pf.get("watchlist", [])

    # 실시간 가격 (포지션 + 워치리스트 전체)
    tickers = list(dict.fromkeys(
        [p["ticker"] for p in positions] + [w["ticker"] for w in watchlist]
    ))
    quotes = {t: get_quote(t) for t in tickers}

    total_unrealized = 0.0
    invested = 0.0
    market_value = 0.0
    for p in positions:
        q = quotes.get(p["ticker"]) or {}
        live = q.get("price")
        p["live_price"] = live if live else p.get("current_price")
        p["live_dp"] = q.get("dp")
        direction = 1 if p["side"] == "LONG" else -1
        p["unrealized_live"] = round((p["live_price"] - p["entry_price"]) * p["shares"] * direction, 2)
        p["unrealized_live_pct"] = round(
            (p["live_price"] / p["entry_price"] - 1) * 100 * direction, 2
        )
        total_unrealized += p["unrealized_live"]
        invested += p["entry_price"] * p["shares"]
        market_value += p["live_price"] * p["shares"]

    # 총자산 = 현금 + 보유종목 시가평가 (실현수익은 이미 현금에 반영됨)
    # = SEED + 실현누적 + 미실현 → 크론 swing-trader.py portfolio_summary()와 동일한 식
    cash = pf.get("cash", SEED)
    equity = cash + market_value
    realized = sum(h.get("pnl", 0) for h in history)

    def bench(prefix, name):
        base = pf.get(f"{prefix}_baseline_price")
        cur = pf.get(f"{prefix}_current_price") or base
        if not base or not cur:
            return {"name": name, "baseline": None, "current": None, "return_pct": None}
        return {
            "name": name,
            "baseline": base,
            "current": cur,
            "return_pct": round((cur - base) / base * 100, 2),
        }

    benchmarks = [
        bench("spy", "S&P 500"),
        bench("qqq", "Nasdaq 100"),
        bench("nasdaq", "NASDAQ Comp"),
        bench("dia", "Dow Jones"),
        bench("iwm", "Russell 2000"),
    ]
    equity_return = round((equity - SEED) / SEED * 100, 2)

    watch_out = []
    for w in watchlist:
        q = quotes.get(w["ticker"]) or {}
        watch_out.append({
            **w,
            "live_price": q.get("price") or w.get("price"),
            "live_dp": q.get("dp"),
        })

    # 일봉 시계열 (차트용) — 보유 + 워치리스트 + 벤치마크, 6개월
    ser_tickers = list(dict.fromkeys(
        [p["ticker"] for p in positions] + [w["ticker"] for w in watchlist] + ["SPY", "QQQ"]
    ))[:20]
    series = {}
    try:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for _t, _s in zip(ser_tickers, ex.map(lambda x: get_series(x), ser_tickers)):
                if _s:
                    series[_t] = _s
    except Exception:
        pass

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "market_open": is_us_market_open(),
        "seed": SEED,
        "series": series,
        "series_range": SERIES_RANGE,
        "equity_curve": pf.get("equity_curve", []),
        "cash": round(cash, 2),
        "invested": round(invested, 2),
        "equity": round(equity, 2),
        "equity_return_pct": equity_return,
        "unrealized": round(total_unrealized, 2),
        "realized": round(realized, 2),
        "positions": positions,
        "history": history,
        "watchlist": watch_out,
        "benchmarks": benchmarks,
        "vs_spy": round(equity_return - (benchmarks[0]["return_pct"] or 0), 2),
        "trade_count": len(history),
        "win_rate": (
            round(sum(1 for h in history if h.get("pnl", 0) > 0) / len(history) * 100, 1)
            if history else None
        ),
    }


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 조용히

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/" or path == "/index.html":
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/api/summary":
                body = json.dumps(build_payload(), ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            elif path == "/api/health":
                self._send(200, b'{"ok":true}', "application/json")
            else:
                self._send(404, b"not found", "text/plain")
        except FileNotFoundError:
            self._send(500, b"portfolio file missing", "text/plain")
        except Exception as e:
            self._send(500, str(e).encode(), "text/plain")


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    # 시계열 캐시 예열 (첫 요청이 14초 걸리는 것 방지) — 실패해도 서버는 뜬다
    threading.Thread(target=lambda: _safe_warm(), daemon=True).start()
    print(f"dashboard on :{PORT}", flush=True)
    srv.serve_forever()


def _safe_warm():
    try:
        build_payload()
    except Exception:
        pass


if __name__ == "__main__":
    main()
