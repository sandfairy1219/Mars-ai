#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ticker_character.py — 종목 특성(개성) 프로파일링

sp 지시(2026-10-02): "애플보면 다른 기술주들 다 떨어질때 자기만 오르는 날이 꽤나 많았거든?
그거처럼 종목 특성도 잘 활용하고 이해햇으면 좋겠어"

→ 섹터/피어와 '같이 움직이는가, 따로 움직이는가'를 숫자로 뽑아 봇이 쓰게 한다.

측정(최근 120 거래일):
  · corr_sector  : 섹터 프록시 ETF와의 일간 상관계수
  · beta_spy     : SPY 대비 베타
  · decouple_pct  : **섹터가 하락한 날 그 종목이 상승한 비율** (sp 관찰의 정량화)
  · sec_down_ret : 섹터 하락일의 평균 초과수익(%p) — "남들 빠질 때 나는 얼마나 버텼나"
  · up_capture / down_capture : 섹터 상승/하락 참여도
분류:
  · 역상관형(corr < 0.15)          — 헤지 성격
  · 독립형(decouple ≥ 55% & corr < 0.55) — sp가 말한 애플형. 섹터 급락 완충
  · 방어형(down_capture < 0.75)     — 하락 참여는 낮고 상승은 따라감
  · 동조형(corr ≥ 0.75)             — 섹터와 한 몸 (분산 효과 없음)
  · 보통                            — 그 외

프록시: 업종 우선 → 섹터 폴백 (Semiconductors→SMH, Software→IGV ...)
"""
import os, json, time, math

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, ".character_cache.json")
LOOKBACK = 120

# 업종 → 프록시 ETF (있는 것만)
INDUSTRY_PROXY = {
    "Semiconductors": "SMH", "Semiconductor Equipment & Materials": "SMH",
    "Software - Infrastructure": "IGV", "Software - Application": "IGV",
    "Consumer Electronics": "XLK", "Computer Hardware": "XLK",
    "Information Technology Services": "XLK", "Scientific & Technical Instruments": "XLK",
    "Communication Equipment": "XLK", "Electronic Components": "XLK",
    "Banks - Diversified": "KRE", "Banks - Regional": "KRE", "Capital Markets": "IAI",
    "Oil & Gas E&P": "XOP", "Oil & Gas Drilling": "XOP", "Oil & Gas Integrated": "XLE",
    "Oil & Gas Equipment & Services": "XOP", "Uranium": "URA",
    "Biotechnology": "XBI", "Drug Manufacturers - General": "XLV",
    "Medical Devices": "IHI", "Diagnostics & Research": "XLV", "Healthcare Plans": "XLV",
    "Aerospace & Defense": "ITA", "Airlines": "JETS", "Railroads": "XLI",
    "Gold Miners": "GDX", "Other Industrial Metals & Mining": "XME", "Copper": "CPER",
    "Homebuilders": "XHB", "Retail - Apparel & Specialty": "XRT", "Internet Retail": "XRT",
    "Grocery Stores": "XLP", "Beverages - Non-Alcoholic": "XLP",
    "Internet Content & Information": "XLC", "Telecom Services": "XLC",
    "REIT - Specialty": "VNQ", "REIT - Retail": "VNQ",
    "Solar": "TAN", "Utilities - Regulated Electric": "XLU",
    "Specialty Chemicals": "XLB", "Steel": "XME",
}
# 섹터 → 프록시 ETF (폴백)
SECTOR_PROXY = {
    "Technology": "XLK", "Financials": "XLF", "Energy": "XLE", "Healthcare": "XLV",
    "Industrials": "XLI", "Basic Materials": "XLB", "Consumer Defensive": "XLP",
    "Consumer Cyclical": "XLY", "Utilities": "XLU", "Communication Services": "XLC",
    "Real Estate": "VNQ", "Digital Assets": "IBIT", "Thematic ETF": "QQQ",
    "Broad Market Index": "SPY", "International": "EFA", "Bond": "TLT", "Commodity": "GLD",
}


def pick_proxy(sector, industry, ticker=None):
    """업종 프록시 우선, 없으면 섹터 프록시. ticker를 주면 자기참조(프록시=자기)면 SPY로 대체."""
    p = None
    if industry and industry in INDUSTRY_PROXY:
        p = INDUSTRY_PROXY[industry]
    elif sector and sector in SECTOR_PROXY:
        p = SECTOR_PROXY[sector]
    if p is None or (ticker and p.upper() == ticker.upper()):
        return "SPY"
    return p


def _pct_series(prices):
    return [(prices[i] - prices[i - 1]) / prices[i - 1] * 100.0 for i in range(1, len(prices))]


def _corr(a, b):
    n = min(len(a), len(b))
    if n < 20:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / math.sqrt(va * vb)


def _beta(y, x):
    n = min(len(x), len(y))
    if n < 20:
        return None
    x, y = x[-n:], y[-n:]
    mx, my = sum(x) / n, sum(y) / n
    vx = sum((v - mx) ** 2 for v in x)
    if vx <= 0:
        return None
    return sum((x[i] - mx) * (y[i] - my) for i in range(n)) / vx


def compute(ticker, prices, proxy_prices, proxy_name, spy_prices=None):
    """일봉 배열(최소 60개)로 종목 특성 계산. 네트워크 없음."""
    if not prices or not proxy_prices or len(prices) < 60 or len(proxy_prices) < 60:
        return None
    n = min(len(prices), len(proxy_prices), LOOKBACK + 1)
    px, pr = prices[-n:], proxy_prices[-n:]
    rs, rr = _pct_series(px), _pct_series(pr)
    corr = _corr(rs, rr)
    if corr is None:
        return None
    beta = _beta(rs, _pct_series(spy_prices[-n:])) if spy_prices and len(spy_prices) >= 60 else None

    down_days = [i for i, v in enumerate(rr) if v < 0]
    up_days = [i for i, v in enumerate(rr) if v > 0]
    decouple = (sum(1 for i in down_days if rs[i] > 0) / len(down_days) * 100.0) if down_days else None
    sec_down_ret = (sum(rs[i] - rr[i] for i in down_days) / len(down_days)) if down_days else None

    def capture(days):
        if not days:
            return None
        denom = sum(abs(rr[i]) for i in days)
        if denom <= 0:
            return None
        return sum(rs[i] * (1 if rr[i] > 0 else -1) for i in days) / denom
    up_cap, down_cap = capture(up_days), capture(down_days)

    if corr >= 0.75:
        label = "동조형"                      # 섹터와 한 몸 — 분산 효과 없음이 최우선 판정
    elif corr < 0.15:
        label = "역상관형" if (down_cap is None or abs(down_cap) <= 1.0) else "탈동조형"
    elif decouple is not None and decouple >= 55.0 and corr < 0.55:
        label = "독립형"                      # sp가 말한 애플형
    elif down_cap is not None and down_cap < 0.75:
        label = "방어형"
    else:
        label = "보통"

    return {
        "proxy": proxy_name,
        "corr_sector": round(corr, 2),
        "beta_spy": None if beta is None else round(beta, 2),
        "decouple_pct": None if decouple is None else round(decouple, 1),
        "sec_down_ret": None if sec_down_ret is None else round(sec_down_ret, 2),
        "up_capture": None if up_cap is None else round(up_cap, 2),
        "down_capture": None if down_cap is None else round(down_cap, 2),
        "label": label,
    }


def describe(ch):
    """리포트용 한 줄 요약."""
    if not ch:
        return "특성 데이터 부족"
    s = f"{ch['label']}(섹터상관 {ch['corr_sector']}"
    if ch.get("decouple_pct") is not None:
        s += f"·하락일 독립 {ch['decouple_pct']}%"
    if ch.get("down_capture") is not None:
        s += f"·하방참여 {ch['down_capture']}"
    return s + ")"


# ─── 캐시 ───
def load_cache():
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(d):
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def cached(ticker, sector, industry, build):
    """build()는 특성 dict를 반환하는 콜러블. 하루 1회만 계산."""
    today = time.strftime("%Y-%m-%d")
    cache = load_cache()
    e = cache.get(ticker)
    proxy = pick_proxy(sector, industry, ticker)
    if e and e.get("date") == today and e.get("proxy") == proxy:
        return e.get("character")
    ch = build(proxy)
    if ch:
        cache[ticker] = {"date": today, "proxy": proxy, "character": ch}
        save_cache(cache)
    return ch


if __name__ == "__main__":
    import sys, importlib.util
    spec = importlib.util.spec_from_file_location("st", os.path.join(HERE, "swing-trader.py"))
    st = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(st)
    tk = sys.argv[1:] or ["AAPL", "MSFT", "NVDA", "MU", "KO", "GLD", "XOM"]
    spy = st.fetch_chart("SPY")
    for t in tk:
        tax = st.resolve_taxonomy(t)
        px = st.fetch_chart(t)
        pr_name = pick_proxy(tax.get("sector"), tax.get("industry"), t)
        pr = st.fetch_chart(pr_name)
        ch = compute(t, px, pr, pr_name, spy)
        print(f"{t:6s} [{tax.get('sector')}/{tax.get('industry')}] → {describe(ch)}")
