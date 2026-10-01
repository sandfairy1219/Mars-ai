#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""swing_rotate.py — 진입기준 미달 보유 종목 정리 (점수 기반 로테이션)

배경 (2026-10-01, sp 지시 "포트폴리오에 기술주를 좀 들고가는게 맞다"):
  score_long이 평균회귀 항만 가져서 포트폴리오가 방어주로만 채워졌다. score_long에
  모멘텀/상대강도 항을 추가했지만, 슬롯(9/10)과 현금(min_cash 바닥)이 막혀 있어
  신규 진입 여력이 없었다. 그래서 "현재 진입 기준(score_min) 미달"인 보유 종목을
  정리해 슬롯과 현금을 확보한다 — 임의 청산이 아니라 봇 자신의 기준으로 정리하는 것.

사용:
  python3 swing_rotate.py            # 드라이런 (계획만 출력)
  python3 swing_rotate.py --apply    # 실제 청산 + 저장 (백업 생성)
옵션:
  --max N     최대 정리 건수 (기본 3)
"""
import sys, os, json, shutil, datetime, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
PORTFOLIO_PATH = "/home/ubuntu/marsAI/paper-trading/swing-portfolio.json"

spec = importlib.util.spec_from_file_location("st", os.path.join(HERE, "swing-trader.py"))
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)


def main():
    apply = "--apply" in sys.argv
    max_n = 3
    if "--max" in sys.argv:
        max_n = int(sys.argv[sys.argv.index("--max") + 1])

    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    portfolio = st.load_portfolio()
    score_min = st.params().get("score_min", 5)

    # 벤치마크(SPY 20일) 세팅 — score_long의 상대강도 항
    spy = st.fetch_chart("SPY")
    if spy and len(spy) >= 21:
        st._BENCH_20D = (spy[-1] - spy[-21]) / spy[-21] * 100

    print(f"진입 기준 score_min = {score_min}/8 | SPY 20일 {st._BENCH_20D:+.2f}%")
    print("-" * 78)

    rows = []
    for p in portfolio["positions"]:
        prices = st.fetch_chart(p["ticker"])
        if not prices or len(prices) < 50:
            continue
        rsi = st.calc_rsi(prices)
        ma20 = st.calc_ma(prices, 20)
        ma50 = st.calc_ma(prices, 50)
        atr = st.calc_atr(prices)
        if rsi is None or ma20 is None or ma50 is None:
            continue
        sc = st.score_long(prices, rsi, ma20, ma50, atr)
        if st._is_premarket_or_afterhours():
            live = st.fetch_live_price(p["ticker"])
            cur = live if live is not None else prices[-1]
        else:
            cur = prices[-1]
        rows.append({"pos": p, "score": sc, "cur": round(cur, 2)})

    rows.sort(key=lambda r: r["score"])
    print("전체 보유 점수 (낮은 순):")
    for r in rows:
        p = r["pos"]
        pnl = (r["cur"] - p["entry_price"]) * p["shares"]
        flag = "← 정리 대상" if (r["score"] < score_min and not p.get("long_term")) else ""
        print(f"  {p['ticker']:6s} {r['score']}/8 | 매수가 {p['entry_price']:>8.2f} 현재 {r['cur']:>8.2f} | "
              f"P&L {pnl:+9.2f} | {p['sector'][:14]:14s} {flag}")

    picks = [r for r in rows if r["score"] < score_min and not r["pos"].get("long_term")][:max_n]
    print("-" * 78)
    if not picks:
        print("정리 대상 없음 — 모든 보유 종목이 진입 기준을 충족.")
        return
    freed = sum(r["pos"]["shares"] * r["cur"] for r in picks)
    print(f"정리 대상 {len(picks)}건 (진입 기준 {score_min} 미달) | 회수 예상 현금 ${freed:,.2f}")
    for r in picks:
        p = r["pos"]
        print(f"  → {p['ticker']} ({r['score']}/8) {p['shares']}주 @ ${r['cur']} "
              f"= ${p['shares']*r['cur']:,.2f}")

    if not apply:
        print("\n[드라이런] 실제 청산은 --apply 를 붙여 실행.")
        return

    shutil.copyfile(PORTFOLIO_PATH, PORTFOLIO_PATH + f".bak_rotate_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}")
    closed = []
    keep = []
    for p in portfolio["positions"]:
        match = next((r for r in picks if r["pos"]["ticker"] == p["ticker"]), None)
        if not match:
            keep.append(p)
            continue
        cur = match["cur"]
        pnl = (cur - p["entry_price"]) * p["shares"]
        days_held = (datetime.datetime.strptime(today, "%Y-%m-%d")
                     - datetime.datetime.strptime(p["date"], "%Y-%m-%d")).days
        rec = {**p, "exit_price": round(cur, 2), "exit_date": today, "pnl": round(pnl, 2),
               "exit_reason": f"로테이션 정리 ({match['score']}/8 < 진입기준 {score_min}) — 상대강도/모멘텀 항 도입 후 기준 미달"}
        closed.append(rec)
        portfolio["cash"] += p["shares"] * cur
        print(f"청산 {p['ticker']}: P&L ${pnl:+,.2f} ({pnl/(p['entry_price']*p['shares'])*100:+.2f}%)")
    portfolio["positions"] = keep
    portfolio["history"].extend(closed)
    st.save_portfolio(portfolio)
    print(f"\n✅ {len(closed)}건 청산 완료 | 현금 ${portfolio['cash']:,.2f} | 보유 {len(portfolio['positions'])}종목")


if __name__ == "__main__":
    main()
