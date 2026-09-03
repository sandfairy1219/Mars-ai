#!/usr/bin/env python3
"""
Mars Paper-Trading Dashboard — 모의투자 실시간 대시보드
데이터 소스: ~/.hermes/scripts/swing-portfolio.json (cron이 갱신) + Finnhub 실시간 시세
"""

import json
import os
import time
import urllib.request
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

    cash = pf.get("cash", SEED)
    equity = SEED + total_unrealized

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
    equity_return = round(total_unrealized / SEED * 100, 2)

    watch_out = []
    for w in watchlist:
        q = quotes.get(w["ticker"]) or {}
        watch_out.append({
            **w,
            "live_price": q.get("price") or w.get("price"),
            "live_dp": q.get("dp"),
        })

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "market_open": is_us_market_open(),
        "seed": SEED,
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
    print(f"dashboard on :{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
