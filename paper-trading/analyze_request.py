import urllib.request, json, math

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
        return None

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

def analyze_ticker(ticker):
    prices = fetch_chart(ticker)
    if not prices or len(prices) < 30:
        return None
    rsi = calc_rsi(prices)
    ma20 = calc_ma(prices, 20)
    ma50 = calc_ma(prices, 50)
    if rsi is None or ma20 is None or ma50 is None:
        return None
    change_5d = (prices[-1] - prices[-6]) / prices[-6] * 100 if len(prices) >= 6 else 0.0
    change_20d = (prices[-1] - prices[-21]) / prices[-21] * 100 if len(prices) >= 21 else 0.0
    vol = math.sqrt(sum(((p - sum(prices[-20:])/20)**2 for p in prices[-20:])) / 20)
    vol_pct = vol / prices[-1] * 100 if prices[-1] > 0 else 0
    
    # Determine trend & signal
    trend = "BULLISH" if ma20 > ma50 else "BEARISH"
    rec = "HOLD"
    reason = ""
    if rsi < 35 and ma20 > ma50 and change_5d < -5:
        rec = "STRONG LONG"
        reason = f"쿀매도 회복 + 추세 상승 + 급락"
    elif rsi < 40 and change_5d < -3:
        rec = "LONG"
        reason = f"쿀매도 구간, 반등 노림"
    elif rsi > 70 and ma20 < ma50 and change_5d > 5:
        rec = "STRONG SHORT"
        reason = f"쿀매수 장기화 + 데드크로스 + 급승"
    elif rsi > 65 and change_5d > 3:
        rec = "SHORT"
        reason = f"쿀매수 근접, 조정 기대"
    elif trend == "BULLISH" and rsi > 50 and change_5d > 0:
        rec = "HOLD LONG"
        reason = f"추세 상승 유지 중, 동그 보유"
    elif trend == "BEARISH" and rsi < 50 and change_5d < 0:
        rec = "HOLD SHORT"
        reason = f"추세 하락 유지 중, 숨트 보유"
    else:
        rec = "HOLD"
        reason = f"중립 구간 (RSI {rsi:.1f}), 대기 전략"
    
    return {
        "ticker": ticker,
        "price": round(prices[-1], 2),
        "rsi": round(rsi, 1),
        "ma20": round(ma20, 2),
        "ma50": round(ma50, 2),
        "trend": trend,
        "change_5d": round(change_5d, 2),
        "change_20d": round(change_20d, 2),
        "vol_20d": round(vol_pct, 2),
        "rec": rec,
        "reason": reason
    }

def fetch_market_summary():
    tickers = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "VIX": "VIX"}
    summary = {}
    for t, name in tickers.items():
        prices = fetch_chart(t)
        if prices and len(prices) >= 6:
            change_5d = (prices[-1] - prices[-6]) / prices[-6] * 100
            change_20d = (prices[-1] - prices[-21]) / prices[-21] * 100 if len(prices) >= 21 else 0.0
            summary[name] = {
                "price": round(prices[-1], 2),
                "5d": round(change_5d, 2),
                "20d": round(change_20d, 2)
            }
        else:
            summary[name] = {"price": None, "5d": None, "20d": None}
    return summary

# Analyze requested tickers
requested = ["AMPG", "NOK", "CEG", "TAC"]
results = []
for t in requested:
    r = analyze_ticker(t)
    if r:
        results.append(r)
    else:
        results.append({"ticker": t, "error": "Data fetch failed or insufficient history"})

# Market summary
market = fetch_market_summary()

# Print report
print("📊 **INDIVIDUAL TICKER ANALYSIS**\n")
for r in results:
    if "error" in r:
        print(f"**{r['ticker']}**: {r['error']}\n")
        continue
    emoji = "🟢" if "LONG" in r['rec'] else ("🔴" if "SHORT" in r['rec'] else "⚪")
    print(f"{emoji} **{r['ticker']}** @ ${r['price']}")
    print(f"   추천: **{r['rec']}** — {r['reason']}")
    print(f"   RSI: {r['rsi']} | 20MA: ${r['ma20']} | 50MA: ${r['ma50']} | 추세: {r['trend']}")
    print(f"   5일 변동: {r['change_5d']}% | 20일 변동: {r['change_20d']}% | 보동률(20일): {r['vol_20d']}%")
    print()

print("🌍 **MARKET CONTEXT**")
for name, data in market.items():
    if data["price"]:
        print(f"   {name}: ${data['price']} (5일: {data['5d']}%, 20일: {data['20d']}%)")
    else:
        print(f"   {name}: Data unavailable")

# Trading strategy recommendation
print("\n🎯 **CURRENT TRADING STRATEGY RECOMMENDATION**\n")
spy = market.get("S&P 500", {})
vix = market.get("VIX", {})
qqq = market.get("Nasdaq 100", {})

if spy.get("5d", 0) < -2 and vix.get("price") and vix.get("price", 20) > 25:
    print("⚠️ 시장 근환 구간 — 단기 순스 위주, 방어적 전략")
    print("   → **전략**: SQQQ 또는 VIXY 등 방어 티켓 유지")
    print("   → 온던 개별주 순스 새 다운 공방 강화")
    print("   → 일단 축 매를 유지하며 지속 바이구망 가능성 대비")
elif spy.get("5d", 0) > 2 and qqq.get("5d", 0) > 3:
    print("🚀 시장 모먼텈 상승 — 단기 순환성 지속")
    print("   → **전략**: TQQQ 또는 강한 선도 지수 레버리지 추가")
    print("   → 과매수 근접 종목 수익 궤망 추가 매도")
    print("   → RSI > 70 + 상승 분위기 종목은 방어 조절 대상")
else:
    print("🔄 시장 중립 구간 — 방향성 부재")
    print("   → **전략**: 추세 모먼텀 발생 시 방향 공략")
    print("   → 과매도(35미만) 개별주 반등 로링")
    print("   → 과매수(65이상) 개별주 순스 로링")
    print("   → 조정 배수 확대(VIX 상승 시) 계속 적용")

print("\n❗ 모든 분석은 기술적 지표 기반이며, 투자 차이는 개인의 책임입니다.")
