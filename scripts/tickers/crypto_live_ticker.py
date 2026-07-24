#!/usr/bin/env python3
"""
Crypto + Index Live Ticker — 1s updates, single-message edit via Discord webhook.
Mobile-friendly: uses emoji instead of ANSI (ANSI doesn't render on mobile).
"""
import urllib.request, json, time, sys, os, signal
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK", "")
INTERVAL = 1
UA = "Mozilla/5.0 (compatible; HermesBot/1.0)"
PID_FILE = "/tmp/crypto_ticker.pid"

# Module-level var for signal handler
_current_mid = None

SYMBOLS = [
    ("BTCUSDT",  "₿ BTC", "spot"),
    ("ETHUSDT",  "Ξ ETH", "spot"),
    ("QQQUSDT",  "📈 QQQ", "futures"),
    ("SPYUSDT",  "📊 SPY", "futures"),
]

API_SPOT    = "https://api.binance.com/api/v3/ticker/24hr?symbol="
API_FUTURES = "https://fapi.binance.com/fapi/v1/ticker/24hr?symbol="

def fetch(sym, api_type):
    base = API_FUTURES if api_type == "futures" else API_SPOT
    try:
        with urllib.request.urlopen(base + sym, timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

def fp(p):
    if p is None: return "—"
    if p >= 1000: return "${:,.0f}".format(p)
    elif p >= 1:  return "${:,.2f}".format(p)
    else:         return "${:,.6f}".format(p)

def bar(pct, width=10):
    """Emoji bar: green for up, red for down, works on mobile"""
    filled = min(int(abs(pct) * 2), width)
    if filled > width: filled = width
    if pct > 0.05:
        return "🟥" * filled + "⬛" * (width - filled)
    elif pct < -0.05:
        return "⬛" * (width - filled) + "🟦" * filled
    else:
        return "⬛" * width

def build_msg():
    now = datetime.now(KST)

    lines = [
        "**🔴 LIVE TICKER**  ─  %s KST" % now.strftime("%m/%d %H:%M:%S"),
        "",
    ]

    for sym, label, api_type in SYMBOLS:
        d = fetch(sym, api_type)
        if "error" in d:
            lines.append("### %s  ⚠️ %s" % (label, d["error"]))
            lines.append("")
            continue

        p   = float(d["lastPrice"])
        pct = float(d["priceChangePercent"])
        chg = float(d["priceChange"])
        hi  = float(d["highPrice"])
        lo  = float(d["lowPrice"])
        vol = float(d.get("quoteVolume", 0) or 0)

        if pct > 0:
            arrow, sign = "🔴 ▲", "+"
        elif pct < 0:
            arrow, sign = "🔵 ▼", ""
        else:
            arrow, sign = "⚪ ◆", ""

        lines.append("**%s**  %s  **%s**  %s%.2f (%s%.2f%%)" % (
            label, arrow, fp(p), sign, chg, sign, pct))
        lines.append("> 고가 %s  |  저가 %s  |  거래량 $%.1fB" % (fp(hi), fp(lo), vol/1e9))
        lines.append("> %s" % bar(pct))
        lines.append("")

    lines.append("-# 1s refresh · Binance API · QQQ/SPY = 24/7 무기한선물")

    return "\n".join(lines)

def api(method, path_suffix, data=None):
    url = WEBHOOK_URL + path_suffix
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            if method == "DELETE":
                return True  # 204 No Content
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print("[API %s] %s" % (method, e.code), file=sys.stderr)
        return None

def cleanup_now(signum=None, frame=None):
    """Signal handler: delete current message + PID file on exit."""
    global _current_mid
    if _current_mid:
        print("live-ticker: cleanup on signal %s, deleting msg %s" % (signum, _current_mid), file=sys.stderr)
        api("DELETE", "/messages/" + _current_mid)
        _current_mid = None
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    if signum:
        sys.exit(0)

def delete_old_messages(keep_mid):
    """Delete all known old messages except the one we're keeping."""
    try:
        with open(MSG_ID_FILE) as f:
            old_ids = [x.strip() for x in f.read().strip().split("\n") if x.strip()]
    except:
        old_ids = []

    deleted = 0
    for mid in old_ids:
        if mid == keep_mid:
            continue
        res = api("DELETE", "/messages/" + mid)
        if res:
            print("live-ticker: deleted old msg_id=%s" % mid, file=sys.stderr)
            deleted += 1
        else:
            print("live-ticker: delete failed for %s (already gone?)" % mid, file=sys.stderr)

    # Save only the current ID
    if keep_mid:
        with open(MSG_ID_FILE, "w") as f:
            f.write(keep_mid)

    return deleted

MSG_ID_FILE = "/tmp/crypto_ticker_msg_id"

def main():
    global _current_mid
    print("live-ticker: starting...", file=sys.stderr)

    # Register signal handlers for graceful cleanup
    signal.signal(signal.SIGTERM, cleanup_now)
    signal.signal(signal.SIGINT, cleanup_now)
    signal.signal(signal.SIGHUP, cleanup_now)

    # Write PID file for watchdog
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    # Try to resume editing existing message
    mid = None
    try:
        with open(MSG_ID_FILE) as f:
            old_mid = f.read().strip()
        if old_mid:
            msg = build_msg()
            test = api("PATCH", "/messages/" + old_mid, {"content": msg})
            if test:
                mid = old_mid
                _current_mid = mid
                delete_old_messages(mid)
                print("live-ticker: resumed msg_id=%s" % mid, file=sys.stderr)
    except:
        pass

    # Create new message if resume failed
    if not mid:
        msg = build_msg()
        res = api("POST", "?wait=true", {"content": msg})
        if not res:
            print("FATAL", file=sys.stderr)
            sys.exit(1)
        mid = res["id"]
        _current_mid = mid
        delete_old_messages(mid)
        print("live-ticker: new msg_id=%s" % mid, file=sys.stderr)

    # Save ID
    with open(MSG_ID_FILE, "w") as f:
        f.write(mid)

    seq = 0
    while True:
        time.sleep(INTERVAL)
        seq += 1
        msg = build_msg()
        result = api("PATCH", "/messages/" + mid, {"content": msg})
        if result:
            if seq % 60 == 0:
                print("live-ticker: #%d ok" % seq, file=sys.stderr)
        else:
            print("live-ticker: #%d FAIL" % seq, file=sys.stderr)
            new_res = api("POST", "?wait=true", {"content": msg})
            if new_res:
                delete_old_messages(new_res["id"])
                mid = new_res["id"]
                _current_mid = mid
                with open(MSG_ID_FILE, "w") as f:
                    f.write(mid)
                print("live-ticker: recovered msg_id=%s" % mid, file=sys.stderr)
            else:
                time.sleep(5)

if __name__ == "__main__":
    main()
