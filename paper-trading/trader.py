import urllib.request, json, os, datetime, math

# ─── Config ───
PORTFOLIO_PATH = "/home/ubuntu/marsAI/paper-trading/portfolio.json"
ENV_PATH       = "/home/ubuntu/marsAI/etf-alarm/.env"
TICKERS = [
    "AAPL","MSFT","AMZN","GOOGL","NVDA","TSLA","META","AVGO",
    "NFLX","AMD","ADBE","INTU","COST","QCOM","PEP","AMGN",
    "TMUS","CSCO","CMCSA","INTC"
]
INITIAL_CASH = 100_000.0
MAX_POSITIONS = 5
ALLOCATION    = 20_000.0   # 20% each

# ─── Read Discord webhook from existing .env ───
webhook_url = None
if os.path.exists(ENV_PATH):
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line.startswith("DISCORD_WEBHOOK"):
                webhook_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

# ─── Yahoo Finance data fetch ───
def fetch_chart(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=3mo"
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        result = data["chart"]["result"][0]
        close = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
        return close
    except Exception as e:
        print(f"[ERROR] {ticker}: {e}")
        return None

# ─── Technical indicators ───
def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

def calc_ma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

# ─── Scan & generate signals ───
signals = []
for t in TICKERS:
    prices = fetch_chart(t)
    if not prices or len(prices) < 50:
        continue
    rsi   = calc_rsi(prices)
    ma20  = calc_ma(prices, 20)
    ma50  = calc_ma(prices, 50)
    if rsi is None or ma20 is None or ma50 is None:
        continue
    change_5d = (prices[-1] - prices[-6]) / prices[-6] * 100 if len(prices) >= 6 else 0.0
    signal = None
    reason = ""
    if rsi < 42 and ma20 > ma50 and change_5d < -1.5:
        signal = "LONG"
        reason = f"RSI {rsi:.1f} (과매도 회복), 20MA>50MA, 5일 {change_5d:.1f}% 하락"
    elif rsi > 65 and ma20 < ma50 and change_5d > 2.0:
        signal = "SHORT"
        reason = f"RSI {rsi:.1f} (과매수 근접), 20MA<50MA, 5일 +{change_5d:.1f}% 상승"
    signals.append({
        "ticker": t,
        "price": round(prices[-1], 2),
        "rsi": round(rsi, 1),
        "ma20": round(ma20, 2),
        "ma50": round(ma50, 2),
        "change_5d": round(change_5d, 2),
        "signal": signal,
        "reason": reason
    })

# ─── Select top 5 most extreme signals ───
active = [s for s in signals if s["signal"]]
active.sort(key=lambda x: abs(x["rsi"] - 50), reverse=True)
selected = active[:MAX_POSITIONS]

# ─── Load or init portfolio ───
portfolio = {"cash": INITIAL_CASH, "positions": [], "history": []}
if os.path.exists(PORTFOLIO_PATH):
    with open(PORTFOLIO_PATH) as f:
        portfolio = json.load(f)

today = datetime.datetime.now().strftime("%Y-%m-%d")

# Reset weekly positions (paper-trader demo: fresh start every scan)
portfolio["positions"] = []
for s in selected:
    nominal = ALLOCATION
    shares = math.floor(nominal / s["price"])
    pos = {
        "ticker": s["ticker"],
        "side": s["signal"],
        "entry_price": s["price"],
        "shares": shares,
        "date": today,
        "reason": s["reason"]
    }
    portfolio["positions"].append(pos)
    portfolio["history"].append({**pos, "action": "OPEN"})
    portfolio["cash"] -= shares * s["price"]

os.makedirs(os.path.dirname(PORTFOLIO_PATH), exist_ok=True)
with open(PORTFOLIO_PATH, "w") as f:
    json.dump(portfolio, f, indent=2, ensure_ascii=False)

# ─── Build Discord alert ───
lines = [f"🤖 **AI PAPER TRADER** — Weekly Signal\n📅 {today}\n"]
if not selected:
    lines.append("📭 **No signals this week.** 모든 종목이 중립 구간입니다.")
else:
    for s in selected:
        emoji = "🟢" if s["signal"] == "LONG" else "🔴"
        lines.append(f"{emoji} **[{s['signal']}] {s['ticker']}** — ${s['price']:.2f}")
        lines.append(f"→ {s['reason']}")
        lines.append("")
lines.append(f"💰 **Cash:** `${portfolio['cash']:,.2f}` | **Positions:** {len(portfolio['positions'])}/{MAX_POSITIONS}")
lines.append(f"📁 Log: `{PORTFOLIO_PATH}`")

message = "\n".join(lines)

# ─── Send to Discord via webhook ───
if webhook_url:
    try:
        payload = {"content": message}
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
        print("✅ Discord webhook 전송 성공")
    except Exception as e:
        print(f"⚠️ Discord webhook 실패: {e}")
else:
    print("⚠️ Webhook URL not found in .env")

print("\n" + "="*50)
print(message)
print("="*50)
print(f"\n💾 Portfolio saved: {PORTFOLIO_PATH}")
