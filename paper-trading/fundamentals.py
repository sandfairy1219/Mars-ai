#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fundamentals.py — 펀더멘털/퀄리티 레이어 (모의투자 스윙 봇용)

스킬 직결:
  · quality-stock-screen   — 7대 지표 Pass/Fail + 종합 점수(7점)
  · financial-data-rigor   — 핵심 데이터 2개 독립 소스 교차검증(편차 >1% 경고 / >5% 위험),
                             계산은 Decimal, 시총은 주가×주식수 수동검산, 통화 표기
  · value-investing-checklist — 정성 항목(능력권·해자 등)은 에이전트가 판단할 몫이므로
                             여기서는 정량 게이트(좋은 사업: ROE·FCF)만 계산해 근거로 넘긴다

설계 메모:
  · 스윙 유니버스(최대 250종목)를 매 틱 훑어야 하므로 **캐시 필수**. 펀더멘털은 분기 단위로만
    바뀌므로 기본 TTL 7일. 첫 실행만 네트워크 비용(yfinance + Finnhub), 이후는 캐시 히트.
  · Finnhub 무료 티어 60콜/분 → 호출 간 0.15s 간격.
  · 10년/5년 평균은 런타임 예산상 TTM으로 대체하고 `basis` 필드에 'TTM'으로 명시한다
    (스킬의 이상치는 다년 평균이나, 스윙 스캔에서는 TTM 허용).

사용:
  python3 fundamentals.py MSFT MU KR            # 종목 지정
  python3 fundamentals.py --positions           # 보유 종목 전체
  python3 fundamentals.py --json MSFT           # JSON 출력
  from fundamentals import quality_report       # 모듈로 사용 (봇이 이 경로로 쓴다)
"""
import sys, os, json, time, datetime, urllib.request
from decimal import Decimal, getcontext

getcontext().prec = 28

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(SCRIPT_DIR, ".fundamentals_cache.json")
PORTFOLIO_PATH = "/home/ubuntu/marsAI/paper-trading/swing-portfolio.json"
CACHE_TTL_DAYS = 7
FINNHUB_SLEEP = 0.16

# quality-stock-screen 임계값
Q = {
    "roe_min": 8.0,            # 10년 평균 ROE — TTM 대체
    "gm_min": 15.0,            # 장기 매출총이익률
    "cfo_ni_min": 0.7,         # 영업CF/순이익 (이익 질)
    "nm_min": 5.0,             # 장기 순이익률
    "interest_cov_min": 2.0,   # 이자보상배율
    "netdebt_ebitda_max": 4.0, # 순차입금/EBITDA
}
FINANCIAL_SECTORS = {"Financials", "Financial", "Financial Services"}


def _load_cache():
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    try:
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass


def _finnhub_key():
    for p in ["/home/ubuntu/.hermes/scripts/.env", "/home/ubuntu/marsAI/etf-alarm/.env",
              "/home/ubuntu/etf-alarm/.env"]:
        try:
            for line in open(p):
                if line.strip().startswith("FINNHUB"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return ""


def _pct(v):
    """yfinance 비율(0.679) → %(67.9). 이미 %스케일(>1.5)이면 그대로."""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v * 100.0 if abs(v) <= 1.5 else v


def _source_yf(ticker):
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        i = tk.info or {}
    except Exception:
        return {}
    if not i:
        return {}
    # 이자보상배율: info에 없는 경우가 많아 손익계산서에서 EBIT/|이자비용| 계산
    interest_cov = i.get("interestCoverage")
    if interest_cov is None:
        try:
            fin = tk.financials
            ebit = fin.loc["EBIT"].iloc[0] if "EBIT" in fin.index else None
            ie = fin.loc["Interest Expense"].iloc[0] if "Interest Expense" in fin.index else None
            if ebit is not None and ie not in (None, 0):
                interest_cov = float(Decimal(str(ebit)) / Decimal(str(abs(float(ie)))))
        except Exception:
            pass
    price = i.get("currentPrice") or i.get("regularMarketPrice")
    shares = i.get("sharesOutstanding")
    mcap_manual = None
    if price and shares:
        mcap_manual = float(Decimal(str(price)) * Decimal(str(shares)))  # 수동검산
    return {
        "src": "yfinance",
        "currency": i.get("currency") or "USD",
        "price": price,
        "shares": shares,
        "market_cap": i.get("marketCap"),
        "market_cap_manual": mcap_manual,
        "roe": _pct(i.get("returnOnEquity")),
        "gm": _pct(i.get("grossMargins")),
        "nm": _pct(i.get("profitMargins")),
        "de": i.get("debtToEquity"),
        "cfo": i.get("operatingCashflow"),
        "ni": i.get("netIncomeToCommon"),
        "fcf": i.get("freeCashflow"),
        "ebitda": i.get("ebitda"),
        "total_debt": i.get("totalDebt"),
        "total_cash": i.get("totalCash"),
        "rev_growth": _pct(i.get("revenueGrowth")),
        "interest_cov": interest_cov,
        "trailing_pe": i.get("trailingPE"),
    }


def _source_finnhub(ticker, key):
    if not key:
        return {}
    try:
        url = f"https://finnhub.io/api/v1/stock/metric?symbol={ticker}&metric=all&token={key}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            m = (json.loads(r.read().decode("utf-8")) or {}).get("metric", {}) or {}
        time.sleep(FINNHUB_SLEEP)
    except Exception:
        return {}
    # Finnhub의 %필드는 이미 %값 (×100 금지)
    de_ratio = m.get("totalDebt/totalEquityQuarterly")
    return {
        "src": "finnhub",
        "roe": m.get("roeTTM"),
        "gm": m.get("grossMarginTTM"),
        "nm": m.get("netProfitMarginTTM"),
        "de": (float(de_ratio) * 100.0) if isinstance(de_ratio, (int, float)) else None,
        "rev_growth": m.get("revenueGrowthTTMYoy"),
        "current_ratio": m.get("currentRatioQuarterly"),
        "pe": m.get("peTTM"),
        "high52": m.get("52WeekHigh"),
        "low52": m.get("52WeekLow"),
    }


def _cross(a, b, label, out):
    """financial-data-rigor: 2개 독립 소스 편차 검증. |편차|>1% 경고, >5% 위험."""
    if a is None or b is None:
        return
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return
    base = max(abs(a), abs(b), 1e-9)
    rel = abs(a - b) / base * 100.0
    rec = {"metric": label, "a": round(a, 2), "b": round(b, 2), "diff_pct": round(rel, 2)}
    if rel > 5.0:
        rec["level"] = "위험(>5%)"
    elif rel > 1.0:
        rec["level"] = "경고(>1%)"
    if rel > 1.0:
        out.append(rec)


def quality_report(ticker, sector=None, use_cache=True):
    """종목 1개의 퀄리티 리포트. 캐시(TTL 7일) 사용."""
    ticker = ticker.upper()
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    cache = _load_cache()
    hit = cache.get(ticker)
    if use_cache and isinstance(hit, dict) and hit.get("fetched"):
        age = (datetime.datetime.strptime(today, "%Y-%m-%d") -
               datetime.datetime.strptime(hit["fetched"], "%Y-%m-%d")).days
        if age <= CACHE_TTL_DAYS:
            return hit

    y = _source_yf(ticker)
    f = _source_finnhub(ticker, _finnhub_key())
    flags, checks = [], {}

    # ── 교차검증 (core = ROE·GM·NM·D/E) ──
    _cross(y.get("roe"), f.get("roe"), "ROE(%)", flags)
    _cross(y.get("gm"), f.get("gm"), "매출총이익률(%)", flags)
    _cross(y.get("nm"), f.get("nm"), "순이익률(%)", flags)
    _de_before = len(flags)
    _cross(y.get("de"), f.get("de"), "부채비율 D/E(%) — 기준 상이(연간 vs 분기)", flags)
    for _e in flags[_de_before:]:
        _e["basis_mismatch"] = True   # 표에서만 제외, 데이터는 보존
    if y.get("market_cap") and y.get("market_cap_manual"):
        _cross(y["market_cap"], y["market_cap_manual"], "시가총액(수동검산)", flags)

    is_fin = (sector or "").split("/")[0].strip() in FINANCIAL_SECTORS

    # 1) ROE
    checks["roe"] = None if y.get("roe") is None else (y["roe"] >= Q["roe_min"])
    # 2) 잉여현금흐름 (TTM) — 누적 5년이 이상적이나 TTM으로 대체
    checks["fcf"] = None if y.get("fcf") is None else (y["fcf"] > 0)
    # 3) 이자보상배율 (금융주 미적용)
    if is_fin:
        checks["interest"] = None
        flags.append({"metric": "이자보상배율", "a": None, "b": None, "diff_pct": None,
                      "level": "미적용(금융주 — BIS/지급여력 별도 확인)"})
    elif y.get("interest_cov") is None:
        checks["interest"] = None
    else:
        checks["interest"] = (y["interest_cov"] >= Q["interest_cov_min"])
    # 4) 매출총이익률
    checks["gm"] = None if y.get("gm") is None else (y["gm"] >= Q["gm_min"])
    # 5) 영업CF/순이익
    cfo, ni = y.get("cfo"), y.get("ni")
    checks["cfo_ni"] = None if (not cfo or not ni or ni <= 0) else ((cfo / ni) >= Q["cfo_ni_min"])
    # 6) 순이익률
    checks["nm"] = None if y.get("nm") is None else (y["nm"] >= Q["nm_min"])
    # 7) 순차입금/EBITDA
    if y.get("total_debt") is not None and y.get("total_cash") is not None and y.get("ebitda"):
        try:
            net_debt = float(y["total_debt"]) - float(y["total_cash"])
            ratio = net_debt / float(y["ebitda"])
            checks["netdebt_ebitda"] = (ratio <= Q["netdebt_ebitda_max"])
        except Exception:
            checks["netdebt_ebitda"] = None
    else:
        checks["netdebt_ebitda"] = None

    measured = [v for v in checks.values() if v is not None]
    passed = sum(1 for v in measured if v)
    rec = {
        "ticker": ticker,
        "fetched": today,
        "basis": "TTM (10년/5년 평균 대체)",
        "currency": y.get("currency") or "USD",
        "sector": sector,
        "score": passed,
        "measured": len(measured),
        "checks": {k: ("P" if v else "F") if v is not None else "N/A" for k, v in checks.items()},
        "metrics": {
            "roe_pct": None if y.get("roe") is None else round(y["roe"], 2),
            "gm_pct": None if y.get("gm") is None else round(y["gm"], 2),
            "nm_pct": None if y.get("nm") is None else round(y["nm"], 2),
            "fcf": y.get("fcf"), "cfo": cfo, "ni": ni, "ebitda": y.get("ebitda"),
            "interest_cov": None if y.get("interest_cov") is None else round(y["interest_cov"], 2),
            "de_pct": None if y.get("de") is None else round(y["de"], 2),
            "rev_growth_pct": None if y.get("rev_growth") is None else round(y["rev_growth"], 2),
            "trailing_pe": y.get("trailing_pe"),
        },
        "cross_check": flags,
        "src_used": [s for s in [y.get("src"), f.get("src")] if s],
    }
    cache[ticker] = rec
    _save_cache(cache)
    return rec


def summary_line(rec):
    if not rec:
        return "품질 ?/7"
    fails = [k for k, v in rec["checks"].items() if v == "F"]
    warn = [c for c in rec["cross_check"]
            if c.get("level", "").startswith("위험") and not c.get("basis_mismatch")]
    tail = ""
    if warn:
        tail += " ⚠️교차검증 " + ",".join(w["metric"] for w in warn)
    return f"품질 {rec['score']}/{rec['measured']}" + (f" (미달: {','.join(fails)})" if fails else "") + tail


def main():
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if "--positions" in args or not args:
        pf = json.load(open(PORTFOLIO_PATH))
        tickers = [(p["ticker"], p.get("sector")) for p in pf.get("positions", [])]
    else:
        tickers = [(t.upper(), None) for t in args]

    out = []
    for t, sec in tickers:
        rec = quality_report(t, sector=sec)
        out.append(rec)
        if not as_json:
            print(f"{t:6s} {summary_line(rec)} | ROE {rec['metrics']['roe_pct']}% "
                  f"GM {rec['metrics']['gm_pct']}% NM {rec['metrics']['nm_pct']}% "
                  f"| checks {rec['checks']}")
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
