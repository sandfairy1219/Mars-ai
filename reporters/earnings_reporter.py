#!/usr/bin/env python3
"""
Earnings Report Automation — fetches 실적발표 posts from SaveTicker,
enriches with price action + technical analysis, delivers to Discord webhook.
"""
import json, os, sys, re, html as html_mod, time, urllib.request
import numpy as np
import yfinance as yf
from datetime import datetime, timezone, timedelta

# ─── CONFIG ─────────────────────────────────────────────────────────
# Webhook URL: env var first, then .env fallback (never hardcode secrets)
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK") or os.environ.get("DISCORD_WEBHOOK_URL") or ""
if not WEBHOOK_URL:
    for env_path in [".env", "/home/ubuntu/etf-alarm/.env", "/home/ubuntu/marsAI/etf-alarm/.env"]:
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DISCORD_WEBHOOK_URL=") or line.startswith("DISCORD_WEBHOOK="):
                        val = line.split("=", 1)[1].strip().strip("\"'")
                        if val:
                            WEBHOOK_URL = val
                            break
        except Exception:
            pass
        if WEBHOOK_URL:
            break
STATE_FILE = os.path.expanduser("~/.hermes/scripts/.earnings_reporter_state.json")
MAX_POSTS_PER_RUN = 10
KST = timezone(timedelta(hours=9))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://saveticker.com/",
}

# ─── HELPERS ────────────────────────────────────────────────────────

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"sent_ids": [], "last_date": None}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    # Keep only last 200 IDs to prevent state file bloat
    if len(state.get("sent_ids", [])) > 200:
        state["sent_ids"] = state["sent_ids"][-200:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

def fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def fetch_article(post_id):
    url = f"https://saveticker.com/news/{post_id}"
    req = urllib.request.Request(url, headers={**HEADERS, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read().decode("utf-8")
    tp = re.compile(r'<p[^>]*class="[^"]*whitespace-pre-wrap[^"]*"[^>]*>(.*?)</p>', re.DOTALL)
    texts, tag_re = [], re.compile(r"<[^>]+>")
    for m in tp.findall(html):
        t = tag_re.sub("", m); t = html_mod.unescape(t).strip()
        if t: texts.append(t)
    return "\n\n".join(texts)

def get_price_action(ticker):
    """Return dict with pre-earnings close, current price, after-hours change."""
    stock = yf.Ticker(ticker)
    info = stock.info
    result = {
        "current_price": info.get("regularMarketPrice") or info.get("currentPrice"),
        "prev_close": info.get("regularMarketPreviousClose"),
        "post_price": info.get("postMarketPrice"),
        "post_change": info.get("postMarketChangePercent"),
    }
    # Get daily OHLC for recent days
    hist = stock.history(period="5d")
    if hist is not None and not hist.empty:
        result["hist"] = {
            str(d.date()): {"close": round(float(r["Close"]), 2), "open": round(float(r["Open"]), 2),
                           "high": round(float(r["High"]), 2), "low": round(float(r["Low"]), 2)}
            for d, r in hist.iterrows()
        }
    return result

def get_technical(ticker):
    """Return dict with MA, RSI, support/resistance."""
    stock = yf.Ticker(ticker)
    hist = stock.history(period="6mo")
    if hist is None or hist.empty:
        return {}
    close = hist["Close"]
    cp = close.iloc[-1]

    ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
    ma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    ma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None

    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = (gain / loss).iloc[-1] if len(loss) > 0 else None
    rsi = round(100 - 100 / (1 + rs), 1) if rs and rs > 0 else None

    # 52w range
    yh, yl = close.max(), close.min()
    rh, rl = close.tail(20).max(), close.tail(20).min()

    # Distance from MAs
    d20 = round((cp - ma20) / ma20 * 100, 1) if ma20 else None
    d50 = round((cp - ma50) / ma50 * 100, 1) if ma50 else None
    d200 = round((cp - ma200) / ma200 * 100, 1) if ma200 else None

    # MA alignment
    ma_status = ""
    if ma20 and ma50 and ma200:
        if cp > ma20 > ma50 > ma200: ma_status = "정배열 🔥"
        elif cp < ma20 < ma50 < ma200: ma_status = "역배열 💀"
        else: ma_status = "혼조"

    rsi_status = ""
    if rsi:
        rsi_status = f"RSI {rsi:.0f}" + (" 과매수 🔴" if rsi > 70 else " 과매도 🟢" if rsi < 30 else "")

    return {
        "price": round(cp, 2),
        "ma20": round(ma20, 2) if ma20 else None, "d20": d20,
        "ma50": round(ma50, 2) if ma50 else None, "d50": d50,
        "ma200": round(ma200, 2) if ma200 else None, "d200": d200,
        "rsi": rsi, "ma_status": ma_status, "rsi_status": rsi_status,
        "year_high": round(yh, 2), "year_low": round(yl, 2),
        "recent_high": round(rh, 2), "recent_low": round(rl, 2),
    }

def parse_earnings(text, title):
    """Extract EPS, revenue and key data lines from article text."""
    lines = text.split("\n")
    data_lines = [l.strip() for l in lines if l.strip() and (l.startswith("- ") or l.startswith("·"))]
    eps_match = re.search(r"EPS:\s*([\d,]+\\.?\\d*)", text)
    eps = eps_match.group(1).replace(",", "") if eps_match else None
    return {"eps": eps, "lines": data_lines[:8]}

def send_webhook(payload):
    """Send JSON payload to Discord webhook. No username override."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(WEBHOOK_URL, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 204
    except urllib.error.HTTPError as e:
        print(f"  WEBHOOK ERROR: {e.code} {e.read().decode()[:200]}", file=sys.stderr)
        return False

# ─── MAIN ──────────────────────────────────────────────────────────

def fetch_earnings_and_calls(lookback_dates):
    """Fetch 실적발표 + 어닝콜(질의응답) posts in lookback window.
    Returns (earnings_posts, call_posts_by_ticker)."""
    earnings_posts = []
    call_posts = []

    for page in [1, 2, 3]:
        try:
            url = f"https://saveticker.com/api/news/list?page={page}&page_size=100&sort=created_at_desc"
            data = fetch_json(url)
            for item in data.get("news_list", []):
                title = item.get("title", "")
                created = item.get("created_at", "")
                if created[:10] not in lookback_dates:
                    continue
                if "실적발표" in title:
                    earnings_posts.append(item)
                elif "어닝콜" in title or "질의응답" in title:
                    call_posts.append(item)
        except Exception as e:
            print(f"  Page {page} error: {e}", file=sys.stderr)
        time.sleep(0.5)

    # Map call posts by ticker (first ticker)
    calls_by_ticker = {}
    for cp in call_posts:
        tickers = [t["symbol"] for t in cp.get("tickers", [])]
        if tickers:
            key = tickers[0].upper()
            if key not in calls_by_ticker:
                calls_by_ticker[key] = cp

    return earnings_posts, calls_by_ticker

def main():
    today = datetime.now(KST).strftime("%Y-%m-%d")
    print(f"\n📊 Earnings Report — {today}", flush=True)

    # Load state: skip already-sent posts
    state = load_state()
    sent_ids = set(state.get("sent_ids", []))
    last_date = state.get("last_date")

    # Lookback window
    lookback_dates = {today}
    if last_date:
        from datetime import timedelta
        d = datetime.strptime(today, "%Y-%m-%d")
        ld = datetime.strptime(last_date, "%Y-%m-%d") if isinstance(last_date, str) else d
        days_gap = (d - ld).days
        if days_gap <= 2:
            lookback_dates.add(last_date)
        else:
            for i in range(1, 3):
                lookback_dates.add((d - timedelta(days=i)).strftime("%Y-%m-%d"))
    else:
        from datetime import timedelta
        lookback_dates.add((datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d"))

    print(f"  Lookback: {sorted(lookback_dates)} | Already sent: {len(sent_ids)}", flush=True)

    # 1) Fetch 실적발표 + 어닝콜 posts
    earnings_posts, calls_by_ticker = fetch_earnings_and_calls(lookback_dates)

    # Dedup + filter already sent
    seen = set()
    posts = []
    for p in sorted(earnings_posts, key=lambda x: x.get("created_at", ""), reverse=True):
        pid = str(p["id"])
        if pid not in seen and pid not in sent_ids:
            seen.add(pid)
            posts.append(p)

    print(f"  New 실적발표 posts: {len(posts)} | 어닝콜 posts found: {len(calls_by_ticker)}", flush=True)
    if not posts:
        print(json.dumps({"status": "NO_NEW_POSTS", "count": 0, "lookback": sorted(lookback_dates)}))
        return

    # 2) Process each post
    sent = 0
    new_sent_ids = []
    for p in posts[:MAX_POSTS_PER_RUN]:
        pid = str(p["id"])
        title = p["title"].replace(" - 실적발표", "").strip()
        tickers = [t["symbol"] for t in p.get("tickers", [])]
        if not tickers:
            continue
        ticker = tickers[0]

        print(f"  → {title} ({ticker})", flush=True)

        # Get article text
        try:
            text = fetch_article(pid)
        except Exception as e:
            print(f"    Article fetch failed: {e}", file=sys.stderr)
            continue

        # Parse earnings
        earnings = parse_earnings(text, title)

        # Fetch earnings call (어닝콜) content if available
        call_text = None
        call_post = calls_by_ticker.get(ticker.upper())
        if call_post:
            try:
                call_text = fetch_article(str(call_post["id"]))
            except Exception as e:
                print(f"    Call fetch failed: {e}", file=sys.stderr)

        # Get price action
        pa = get_price_action(ticker)

        # Get technical
        ta = get_technical(ticker)

        # ── Build message ──
        lines = [f"📊 **{title} ({ticker})** — 실적 상세", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]

        # Core earnings
        core_lines = [l for l in earnings["lines"][:5] if any(w in l for w in ["EPS", "매출", "영업", "순이익", "EBITDA", "이익률"])]
        if core_lines:
            lines.append("\n**📋 핵심 실적**")
            for cl in core_lines[:4]:
                lines.append(f"> {cl}")

        # Additional data
        extra = [l for l in earnings["lines"] if l not in core_lines][:4]
        if extra:
            lines.append("")
            for ex in extra[:3]:
                lines.append(f"> {ex}")

        # Price action
        if pa:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n**📈 주가 반응**")

            # Build market reaction comment
            comments = []
            chg = None
            reaction_type = "neutral"

            if pa.get("prev_close") and pa.get("current_price"):
                curr = pa["current_price"]
                prev = pa["prev_close"]
                chg = (curr - prev) / prev * 100

            post_chg = pa.get("post_change")

            # Determine if after-hours reporter or pre-market reporter
            is_ah = post_chg is not None

            # Check earnings beats
            earnings_beat = any("예상:" in l and ("상회" in l or "+" in l) for l in earnings.get("lines", []))
            earnings_miss = any("예상:" in l and ("하회" in l or "-" in l) for l in earnings.get("lines", []) if "EPS" in l or "매출" in l or "순매출" in l)

            if is_ah:
                # After-hours reporter
                if post_chg and post_chg > 5:
                    comments.append("🔥 **폭발적 반응** — 시장이 결과를 극적으로 환영")
                    reaction_type = "very_positive"
                elif post_chg and post_chg > 2:
                    comments.append("🟢 **긍정적** — 무난한 서프라이즈에 매수세")
                    reaction_type = "positive"
                elif post_chg and post_chg > 0:
                    comments.append("☀️ **소폭 긍정** — 예상 부합, 무난한 반응")
                    reaction_type = "mild_positive"
                elif post_chg and post_chg > -3:
                    comments.append("☁️ **소폭 부정** — 기대치 대비 실망감")
                    reaction_type = "mild_negative"
                elif post_chg and post_chg > -6:
                    comments.append("🔴 **부정적** — 실적은 괜찮았으나 전망/가이던스에 실망")
                    reaction_type = "negative"
                elif post_chg and post_chg <= -6:
                    comments.append("💀 **급락** — 시장의 강한 실망, 가이던스 또는 AI 전략 의문")
                    reaction_type = "very_negative"
            else:
                # Pre-market reporter (reaction visible in regular session)
                if chg and chg > 5:
                    comments.append("🔥 **폭등** — 강력한 실적에 매수세 집중")
                    reaction_type = "very_positive"
                elif chg and chg > 2:
                    comments.append("🟢 **상승** — 실적 서프라이즈 반영")
                    reaction_type = "positive"
                elif chg and chg > 0:
                    comments.append("☀️ **소폭 상승** — 무난한 흐름")
                    reaction_type = "mild_positive"
                elif chg and chg > -2:
                    comments.append("☁️ **소폭 하락** — 실적 발표 후 차익 실현")
                    reaction_type = "mild_negative"
                elif chg and chg > -5:
                    comments.append("🔴 **하락** — 시장 기대치 하회")
                    reaction_type = "negative"
                elif chg and chg <= -5:
                    comments.append("💀 **급락** — 실망스러운 결과에 매도세")
                    reaction_type = "very_negative"

            # Nuanced comment based on earnings vs price divergence
            if earnings_beat and reaction_type in ("negative", "very_negative", "mild_negative"):
                comments.append("⚠️ EPS/매출은 상회했으나 시장은 다른 요소(마진·가이던스·AI 전략 등)에 실망")
            elif earnings_miss and reaction_type in ("positive", "very_positive"):
                comments.append("⚠️ 매출은 예상 하회했으나 AI·비용 효율 등이 긍정적으로 작용")

            # Show price data
            if pa["prev_close"]:
                lines.append(f"• 발표 전 종가: **${pa['prev_close']:.2f}**")
            if pa["current_price"]:
                lines.append(f"• 현재가: **${pa['current_price']:.2f}**")
                if chg is not None:
                    emoji = "🔥" if chg > 3 else "🟢" if chg > 0 else "🔴" if chg < -3 else "☁️" if chg < 0 else ""
                    lines.append(f"• 반응: **{chg:+.1f}%** {emoji}")
            if pa["post_price"] and pa["post_change"]:
                emoji2 = "🚀" if pa["post_change"] > 3 else "🟢" if pa["post_change"] > 0 else "🔴" if pa["post_change"] < -3 else ""
                lines.append(f"• 시간외: **${pa['post_price']:.2f}** ({pa['post_change']:+.2f}% {emoji2})")

            # Add market reaction comment
            for c in comments:
                lines.append(f"💬 {c}")

        # Technical
        if ta and ta.get("ma20"):
            lines.append("\n**📐 기술적 분석**")
            lines.append(f"• MA20 ${ta['ma20']:.2f} ({ta['d20']:+.1f}%) | MA50 ${ta['ma50']:.2f} ({ta['d50']:+.1f}%)")
            if ta.get("ma200"):
                lines.append(f"• MA200 ${ta['ma200']:.2f} ({ta['d200']:+.1f}%)")
            lines.append(f"• {ta['ma_status']} | {ta['rsi_status']}")
            lines.append(f"• 52W: ${ta['year_low']:.2f}~${ta['year_high']:.2f}")
            lines.append(f"• 최근 지지 ${ta['recent_low']:.2f} | 저항 ${ta['recent_high']:.2f}")

        # Earnings call (어닝콜) highlights
        if call_text:
            call_lines = [l.strip() for l in call_text.split("\n") if l.strip() and l.startswith("-")]
            if call_lines:
                lines.append("\n**🎙️ 어닝콜 핵심**")
                for cl in call_lines[:4]:
                    # Truncate long lines
                    display = cl if len(cl) <= 150 else cl[:147] + "..."
                    lines.append(f"> {display}")

        msg = "\n".join(lines)

        # 3) Send (truncate if too long, prioritizing key sections)
        if len(msg) > 1950:
            # If call highlights made it too long, drop them first
            call_marker = "\n**🎙️ 어닝콜 핵심**"
            if call_marker in msg:
                msg = msg.split(call_marker)[0]
            # Still too long? truncate tech section
            if len(msg) > 1950:
                tech_marker = "\n**📐 기술적 분석**"
                if tech_marker in msg:
                    msg = msg.split(tech_marker)[0]
                msg = msg[:1947] + "..."

        payload = {"content": msg}
        ok = send_webhook(payload)
        if ok:
            sent += 1
            new_sent_ids.append(pid)
            print(f"    ✅ Sent ({len(msg)} chars)", flush=True)
        else:
            print(f"    ❌ Send failed", file=sys.stderr)

        time.sleep(0.8)  # rate limit

    # Update state
    if new_sent_ids:
        state["sent_ids"] = list(set(state.get("sent_ids", []) + new_sent_ids))
        state["last_date"] = today
        save_state(state)
        print(f"  State updated: +{len(new_sent_ids)} IDs", flush=True)

    print(f"\n✅ Done. Sent {sent}/{len(posts)} messages", flush=True)
    print(json.dumps({"status": "OK", "date": today, "sent": sent, "total_available": len(posts)}))

if __name__ == "__main__":
    main()