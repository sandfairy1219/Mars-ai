#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alpha_pipeline.py — 공급망 병목 헌터 + 산업 깔때기 → 봇 알파 트랙

스킬 직결:
  · supply-chain-bottleneck-hunter — 2차/3차 병목 발굴(1차는 이미 가격 반영), 트렌드 4기준 검증
  · industry-funnel-screen        — 4단계 깔때기(전시장 스캔 → 5대 가치지표 → 정밀분석 → 4대가 심층)
  · quality-stock-screen          — 3단계에서 fundamentals.quality_report()로 7지표
  · financial-data-rigor          — 핵심 수치는 2소스 교차검증(fundamentals 내부)

출력: ~/.hermes/scripts/alpha_watchlist.json
  → swing-trader.py가 읽어 **거래대금 상위 250 밖이어도 항상 스캔**하고 점수 가산한다.
    (구 워크플로는 정적 UNIVERSE dict를 직접 고쳤지만, 지금 유니버스는 동적이므로 '항상 스캔 목록'이 등가물)

사용:
  python3 alpha_pipeline.py --run [--theme ai-infra]   # 수집·거르기·저장
  python3 alpha_pipeline.py --show                      # 저장된 알파 리스트
  python3 alpha_pipeline.py --run --include-theme ai-infra,power-grid
"""
import sys, os, json, time, datetime, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "alpha_watchlist.json")
sys.path.insert(0, HERE)

# ─── 1단계: 전시장 스캔 (테마별 후보 + 병목 분류) ───
# tier: 1차=이미 가격 반영 / 2차=업계 전문가만 앎 / 3차=극소수 인지
# bottleneck: 어느 지점이 먼저 막히는가
THEMES = {
    "ai-infra": {
        "name": "AI 인프라 공급망",
        "trend_check": {
            "지속성": "3~5년 CAPEX 확정 (하이퍼스케일러 데이터센터 증설 가이던스)",
            "물리성": "실물 증설 — 서버·전력·냉각 하드웨어",
            "규모성": "글로벌 데이터센터 CAPEX 연 $400B+ (하이퍼스케일러 4사 합산 기준)",
            "가속성": "수요 증가속도 > 공급 증설속도 (광모듈/기판 리드타임 연장)",
        },
        "candidates": [
            # 광모듈·트랜시버 (2차)
            ("COHR", "2차", "광모듈/트랜시버"),
            ("LITE", "2차", "광모듈/레이저"),
            ("AAOI", "2차", "광트랜시버"),
            ("FN", "2차", "광모듈 위탁생산"),
            ("CRDO", "2차", "AI 인터커넥트(AEC/DSP)"),
            ("ALAB", "2차", "PCIe/CXL 리타이머"),
            ("MTSI", "2차", "광통신 아날로그 반도체"),
            ("POET", "3차", "광인터포저(광엔진 집적)"),
            ("SMTC", "2차", "아날로그/광 신호"),
            # 화합물 반도체·기판 (3차)
            ("AXTI", "3차", "InP/GaAs 기판(원재료)"),
            ("IQEPF", "3차", "화합물 반도체 웨이퍼"),
            ("SOI.PA", "3차", "SOI 웨이퍼(실리콘 온 인슐레이터)"),
            # IC 기판·특수소재 (3차)
            ("ATS.VI", "3차", "IC 기판(ABF)"),
            ("3110.T", "3차", "특수유리섬유(저유전)"),
            ("4063.T", "3차", "실리콘웨이퍼/특수가스"),
            # 테스트·장비 (2차)
            ("TER", "2차", "반도체 테스트"),
            ("FORM", "2차", "웨이퍼레벨 테스트/프로브카드"),
            ("ONTO", "2차", "검사·계측"),
            ("CAMT", "2차", "검사"),
            ("ACLS", "2차", "이온주입 장비"),
            ("KLIC", "2차", "웨이퍼 본딩/어드밴스드 패키징"),
            ("COHU", "3차", "테스트 핸들러"),
            # 전력·냉각 (2차)
            ("VRT", "2차", "데이터센터 전력·냉각"),
            ("MOD", "3차", "데이터센터 냉각"),
            ("POWL", "3차", "전력 인프라 장비"),
            ("ETN", "2차", "전력관리"),
            ("HUBB", "2차", "전력기기"),
            # 네트워킹 (2차)
            ("CIEN", "2차", "광전송 네트워크"),
            ("ANET", "2차", "데이터센터 스위치"),
            ("INFN", "3차", "광네트워크 장비"),
            # 메모리·스토리지 인접
            ("WDC", "2차", "HDD/스토리지"),
            ("SNDK", "2차", "NAND"),
            # 대형(1차) — 비교 기준으로만 유지
            ("NVDA", "1차", "GPU"),
            ("AVGO", "1차", "커스텀 ASIC/네트워크"),
            ("MU", "1차", "HBM/DRAM"),
            ("TSM", "1차", "파운드리"),
        ],
    },
}

VALUE = [
    ("ROE", lambda m: m["roe_pct"], lambda v: v > 10.0, "ROE>10%"),
    ("영업이익률", lambda m: m.get("op_margin_pct"), lambda v: v > 10.0, "영업이익률>10%"),
    ("부채비율", lambda m: m.get("de_pct"), lambda v: v < 200.0, "부채비율<200%"),
    ("매출성장", lambda m: m.get("rev_growth_pct"), lambda v: v > 0.0, "매출성장>0%"),
    ("시가총액", lambda m: m.get("market_cap"), lambda v: v > 5e8, "시총>$500M"),
]


def _yf_batch(tickers):
    import yfinance as yf
    out = {}
    for t in tickers:
        try:
            i = yf.Ticker(t).info or {}
        except Exception:
            out[t] = {}
            continue
        def pct(v):
            if v is None:
                return None
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return v * 100.0 if abs(v) <= 1.5 else v
        out[t] = {
            "price": i.get("currentPrice") or i.get("regularMarketPrice"),
            "market_cap": i.get("marketCap"),
            "roe_pct": pct(i.get("returnOnEquity")),
            "op_margin_pct": pct(i.get("operatingMargins")),
            "de_pct": i.get("debtToEquity"),
            "rev_growth_pct": pct(i.get("revenueGrowth")),
            "gm_pct": pct(i.get("grossMargins")),
            "nm_pct": pct(i.get("profitMargins")),
            "pe": i.get("trailingPE"),
            "name": i.get("shortName") or i.get("longName"),
        }
        time.sleep(0.05)
    return out


def run(theme_keys):
    from fundamentals import quality_report
    results = {"generated": datetime.datetime.utcnow().strftime("%Y-%m-%d"),
               "themes": {}, "candidates": [], "tickers": []}
    for key in theme_keys:
        theme = THEMES.get(key)
        if not theme:
            print(f"[skip] 알 수 없는 테마: {key}")
            continue
        cands = theme["candidates"]
        tickers = [c[0] for c in cands]
        print(f"\n══ {theme['name']} ══  1단계 전시장 스캔: {len(cands)}종목")
        info = _yf_batch(tickers)

        stage2, dropped = [], []
        for tk, tier, bottleneck in cands:
            m = info.get(tk) or {}
            checks, fails = [], []
            for _, getv, rule, label in VALUE:
                v = getv(m)
                if v is None:
                    checks.append(0)
                    fails.append(f"{label}(데이터없음)")
                elif rule(v):
                    checks.append(1)
                else:
                    checks.append(0)
                    fails.append(label)
            passed = sum(checks)
            rec = {"ticker": tk, "tier": tier, "bottleneck": bottleneck, "name": m.get("name"),
                   "value_checks": passed, "value_fails": fails,
                   "market_cap": m.get("market_cap"), "pe": m.get("pe"),
                   "roe_pct": None if m.get("roe_pct") is None else round(m["roe_pct"], 2),
                   "rev_growth_pct": None if m.get("rev_growth_pct") is None else round(m["rev_growth_pct"], 2)}
            (stage2 if passed >= 5 else dropped).append(rec)
        print(f"  2단계 5대 가치지표: 통과 {len(stage2)} / 탈락 {len(dropped)}")
        for d in dropped[:12]:
            print(f"    ✗ {d['ticker']:9s} [{d['tier']}] {d['bottleneck']:18s} 미달: {','.join(d['value_fails'][:2])}")

        # 3단계: 퀄리티 스크린(7지표)
        print(f"  3단계 퀄리티 스크린(7지표) …")
        for r in stage2:
            try:
                q = quality_report(r["ticker"], sector=None)
                r["quality"] = q.get("score")
                r["quality_measured"] = q.get("measured")
                r["quality_checks"] = q.get("checks")
            except Exception:
                r["quality"], r["quality_measured"] = None, None
        stage2.sort(key=lambda x: (-(x.get("quality") or 0), -(x.get("value_checks") or 0)))
        for r in stage2:
            print(f"    ✓ {r['ticker']:9s} [{r['tier']}] {r['bottleneck']:18s} 가치 {r['value_checks']}/5 · 퀄리티 {r.get('quality')}/{r.get('quality_measured')} · 시총 {r['market_cap']}")

        results["themes"][key] = {"name": theme["name"], "trend_check": theme["trend_check"],
                                 "scanned": len(cands), "survived": len(stage2), "dropped": dropped}
        results["candidates"].extend(stage2)

    # 최종: 2·3차 병목 중 퀄리티 상위 = 알파 트랙
    tiers = {"2차": 0, "3차": 1, "1차": 2}
    results["candidates"].sort(key=lambda x: (tiers.get(x.get("tier", "1차"), 3),
                                              -(x.get("quality") or 0), -(x.get("value_checks") or 0)))
    results["tickers"] = [c["ticker"] for c in results["candidates"] if c.get("tier") in ("2차", "3차")]
    with open(OUT, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n✅ 저장: {OUT}")
    print(f"   알파 트랙(2·3차 병목) {len(results['tickers'])}종목: {', '.join(results['tickers'])}")
    return results


def show():
    try:
        with open(OUT) as f:
            d = json.load(f)
    except Exception:
        print("알파 리스트 없음 — --run 먼저"); return
    print(f"생성 {d['generated']} | 테마 {list(d['themes'].keys())} | 알파 {len(d['tickers'])}종목")
    for c in d["candidates"]:
        print(f"  [{c['tier']}] {c['ticker']:9s} {str(c.get('name'))[:26]:26s} {c['bottleneck']:18s} "
              f"퀄리티 {c.get('quality')}/{c.get('quality_measured')} 가치 {c['value_checks']}/5")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--theme", default="ai-infra")
    a = ap.parse_args()
    if a.run:
        run([t.strip() for t in a.theme.split(",")])
    elif a.show:
        show()
    else:
        ap.print_help()
