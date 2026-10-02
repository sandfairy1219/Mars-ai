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
        "flow_proxy": "SMH",
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
    "quantum": {
        "flow_proxy": "QTUM",
        "name": "퀀텀컴퓨팅",
        "trend_check": {
            "지속성": "국가·빅테크 양자 로드맵 5~10년 (오류정정 단계 진입)",
            "물리성": "극저온 냉동기·제어전자·광자칩 = 실물 장비 필수",
            "규모성": "정부+민간 양자 투자 누적 $100B 규모(추정) — 단 상업매출은 아직 소규모",
            "가속성": "큐비트 수요 증가 > 냉동·측정 장비 공급 증설 속도",
        },
        "candidates": [
            # 순수 양자 하드웨어 (3차 — 매출 미미, 투기적)
            ("IONQ", "3차", "이온트랩 양자컴퓨터"),
            ("RGTI", "3차", "초전도 큐비트"),
            ("QBTS", "3차", "양자어닐링"),
            ("QUBT", "3차", "광자 양자"),
            ("ARQQ", "3차", "양자암호(QKD)"),
            ("QMCO", "3차", "양자 저장/컴퓨팅"),
            # 인에이블러 (2차 — 극저온·측정·제어)
            ("FORM", "2차", "극저온 프로브/측정"),
            ("KEYS", "2차", "계측·양자제어"),
            ("MKSI", "2차", "진공·광학 부품"),
            ("OXIG.L", "2차", "극저온 냉동기·장비"),
            ("3501.T", "2차", "극저온·전력 시스템"),
            ("4063.T", "3차", "실리콘/광자 소재"),
            # 인접 대형 (1차 비교군)
            ("IBM", "1차", "초전도 양자(대형)"),
            ("HON", "1차", "Quantinuum 보유"),
            ("NVDA", "1차", "양자 시뮬레이션"),
        ],
    },
    "power-grid": {
        "flow_proxy": "GRID",
        "name": "전력망·변압기·전력기기",
        "trend_check": {
            "지속성": "5년+ 확정 — 데이터센터·전기화 동시 수요",
            "물리성": "변압기·케이블·스위치기어는 실물 제조 (리드타임 수년)",
            "규모성": "글로벌 전력망 CAPEX 연 $400B+ (IEA 기준선)",
            "가속성": "변압기 리드타임 2~4년 — 공급 증설이 수요를 못 따라감",
        },
        "candidates": [
            ("ETN", "2차", "전력관리·변압기"),
            ("PWR", "2차", "전력망 시공"),
            ("GEV", "2차", "가스터빈·그리드"),
            ("HUBB", "2차", "변압기·전력기기"),
            ("VRT", "2차", "데이터센터 전력·냉각"),
            ("POWL", "3차", "전력 인프라 스위치기어"),
            ("NVT", "3차", "전기 인클로저·열관리(구 nVent)"),
            ("WCC", "2차", "전기 부품 유통"),
            ("AZZ", "3차", "아연도금(전신주·구조물)"),
            ("MYRG", "3차", "전력 인프라 시공"),
            ("CEG", "2차", "원전 전력판매"),
            ("VST", "2차", "발전·소매전력"),
            ("TLN", "3차", "원전·데이터센터 PPA"),
            ("NRG", "2차", "발전·소매"),
            ("OKLO", "3차", "SMR 원자로"),
            ("SMR", "3차", "SMR(NuScale)"),
            ("LEU", "3차", "HALEU 농축(병목)"),
            ("CCJ", "3차", "우라늄"),
            ("UUUU", "3차", "우라늄/희토류"),
            ("UEC", "3차", "우라늄"),
            ("HYUNDAIELECTRIC.KS", "3차", "변압기(한국)"),
            ("ENR.DE", "2차", "Siemens Energy — 그리드"),
            ("SU.PA", "2차", "Schneider — 그리드 소프트웨어"),
            ("ABBN.SW", "2차", "ABB — 전력장비"),
            ("APH", "2차", "커넥터·전력"),
            ("NEE", "1차", "유틸리티(비교군)"),
        ],
    },
    "defense": {
        "flow_proxy": "ITA",
        "name": "방산·드론·우주",
        "trend_check": {
            "지속성": "5년+ — 각국 국방예산 증액 추세",
            "물리성": "미사일·드론·탄약 = 실물 생산 (탄약 병목)",
            "규모성": "글로벌 국방비 연 $2T+",
            "가속성": "드론·요격탄 소모 속도 > 생산 증설 속도",
        },
        "candidates": [
            ("LMT", "1차", "미사일·전투기(대형)"),
            ("RTX", "1차", "미사일·방공"),
            ("NOC", "1차", "전략자산"),
            ("GD", "1차", "육상·잠수함"),
            ("LHX", "1차", "전자전·통신"),
            ("HII", "1차", "조선"),
            ("KTOS", "2차", "무인기·타겟드론·엔진"),
            ("AVAV", "2차", "무인기·탄약(로이터)"),
            ("PLTR", "2차", "국방 소프트웨어"),
            ("LDOS", "2차", "국방 IT"),
            ("BAH", "2차", "국방 컨설팅"),
            ("CACI", "2차", "국방 IT/전자"),
            ("RKLB", "3차", "발사체·위성부품"),
            ("ASTS", "3차", "위성통신(직접연결)"),
            ("LUNR", "3차", "달 착륙선"),
            ("RDW", "3차", "우주 인프라"),
            ("RHM.DE", "2차", "Rheinmetall — 탄약·차량"),
            ("BA.L", "2차", "BAE Systems"),
            ("HO.PA", "2차", "Thales — 전자전"),
            ("SAF.PA", "2차", "Safran — 엔진"),
            ("NOC가 아닌 012450.KS", "3차", "한화에어로 — 항공엔진(한국)"),
            ("OLN", "3차", "탄약용 화학(클로르알칼리)"),
        ],
    },
    "crypto-proxy": {
        "flow_proxy": "WGMI",
        "name": "암호화폐 프록시·채굴·전력",
        "trend_check": {
            "지속성": "3~5년 — 기관화·ETF 자금 유입, 반감기 사이클",
            "물리성": "채굴은 실제 전력·하드웨어 소비(전력 병목과 중첩)",
            "규모성": "암호화폐 시총 $2T+ / 채굴 CAPEX 수십억$",
            "가속성": "전력 확보 경쟁 — 전력 PPA 확보가 채굴 성패를 가름",
        },
        "candidates": [
            ("COIN", "2차", "거래소(수수료)"),
            ("MSTR", "2차", "비트코인 보유 프록시"),
            ("HOOD", "2차", "소매 브로커·크립토"),
            ("MARA", "3차", "채굴"),
            ("RIOT", "3차", "채굴·전력"),
            ("CLSK", "3차", "채굴"),
            ("CIFR", "3차", "채굴"),
            ("WULF", "3차", "채굴·HPC 전환"),
            ("IREN", "3차", "채굴·AI 데이터센터"),
            ("CORZ", "3차", "채굴·HPC 호스팅"),
            ("APLD", "3차", "AI 데이터센터·전력"),
            ("BTBT", "3차", "채굴"),
            ("HUT", "3차", "채굴·전력"),
            ("XYZ", "2차", "Block — 결제·크립토"),
            ("GLXY", "3차", "Galaxy Digital"),
            ("CME", "1차", "선물거래(비교군)"),
        ],
    },
}

ALPHA_CAP = 24   # 알파 가산 대상 최대 종목 수 (가산 희석 방지)

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


FLOW_PROXY_MIN_20D = 3.0    # 테마 프록시 ETF 20일 수익률이 이 이상이면 '섹터 흐름 살아있음'
FLOW_LAG_TOLERANCE = 2.0    # 종목이 프록시보다 이 %p 이상 뒤처지면 흐름 이탈로 간주
FLOW_CAP = 12               # 흐름 트랙 상한


def _momentum_batch(tickers):
    """일괄 가격 이력으로 20일 수익률 / MA20·MA50 상회 여부 계산 (yf.download 1회)."""
    import yfinance as yf
    out = {}
    try:
        data = yf.download(list(tickers), period="3mo", interval="1d",
                           progress=False, auto_adjust=True, threads=True)
        closes = data["Close"] if "Close" in data else data
    except Exception:
        return out
    for t in tickers:
        try:
            s = closes[t].dropna()
            if len(s) < 21:
                continue
            c_last = float(s.iloc[-1])
            r20 = (c_last - float(s.iloc[-21])) / float(s.iloc[-21]) * 100
            ma20 = float(s.tail(20).mean())
            ma50 = float(s.tail(50).mean()) if len(s) >= 50 else None
            out[t] = {"r20": round(r20, 2), "above_ma20": c_last > ma20,
                      "above_ma50": (None if ma50 is None else c_last > ma50), "px": round(c_last, 2)}
        except Exception:
            continue
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

        # ── 2.5단계: 섹터 흐름 트랙 (sp 지시: 섹터 흐름 따라가는 건 남긴다 — 수익률이 중요) ──
        #    펀더멘털 게이트를 못 넘어도, 테마 자체가 강하고 그 종목이 흐름에 올라타 있으면 관찰·진입 후보로 유지.
        _all_tk = [c_[0] for c_ in cands]
        _proxy = theme.get("flow_proxy")
        _mom = _momentum_batch(_all_tk + ([_proxy] if _proxy else []))
        _pm = _mom.get(_proxy or "", {})
        _flow_alive = bool(_proxy and _pm and _pm.get("r20", -99) >= FLOW_PROXY_MIN_20D
                           and _pm.get("above_ma20"))
        print(f"  프록시 {_proxy}: 20일 {_pm.get('r20','?')}% · MA20 상회 {_pm.get('above_ma20')} "
              f"→ 섹터 흐름 {'살아있음 🔥' if _flow_alive else '둔함'}")
        theme["flow_alive"] = _flow_alive
        theme["flow_proxy_20d"] = _pm.get("r20")
        flow_added = []
        if _flow_alive:
            for d in dropped:
                mm = _mom.get(d["ticker"])
                if not mm or not mm.get("above_ma20"):
                    continue
                if mm["r20"] < (_pm.get("r20", 0) - FLOW_LAG_TOLERANCE):
                    continue
                d = dict(d)
                d["lane"] = "flow"
                d["r20"] = mm["r20"]
                d["flow_note"] = f"테마 20일 {_pm.get('r20')}% · 종목 20일 {mm['r20']}% (흐름 동조)"
                flow_added.append(d)
            flow_added.sort(key=lambda x: -x.get("r20", 0))
            flow_added = flow_added[:FLOW_CAP]
            # 퀄리티도 붙여서 참고용으로 남긴다(게이트는 적용하지 않음)
            for d in flow_added:
                try:
                    q = quality_report(d["ticker"])
                    d["quality"] = q.get("score")
                    d["quality_measured"] = q.get("measured")
                except Exception:
                    d["quality"], d["quality_measured"] = None, None
            print(f"    🔥 흐름 트랙 {len(flow_added)}종목: "
                  + ", ".join(f"{x['ticker']}({x['r20']:+.1f}%)" for x in flow_added[:10]))
        results.setdefault("flow", {})[key] = flow_added

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
            r["lane"] = "quality"
            print(f"    ✓ {r['ticker']:9s} [{r['tier']}] {r['bottleneck']:18s} 가치 {r['value_checks']}/5 · 퀄리티 {r.get('quality')}/{r.get('quality_measured')} · 시총 {r['market_cap']}")

        results["themes"][key] = {"name": theme["name"], "trend_check": theme["trend_check"],
                                 "scanned": len(cands), "survived": len(stage2), "dropped": dropped}
        results["candidates"].extend(stage2)

    # 최종: 2·3차 병목 중 퀄리티 상위 = 알파 트랙
    tiers = {"2차": 0, "3차": 1, "1차": 2}
    results["candidates"].sort(key=lambda x: (tiers.get(x.get("tier", "1차"), 3),
                                              -(x.get("quality") or 0), -(x.get("value_checks") or 0)))
    # 중복 제거(여러 테마에 걸친 종목: ETN·VRT·FORM 등) + 상위 N개 제한
    #   전 종목에 +1을 주면 알파 가산이 희석되므로 상한을 둔다(정렬은 이미 tier→quality 순)
    seen, dedup = set(), []
    for c_ in results["candidates"]:
        if c_["ticker"] in seen:
            continue
        seen.add(c_["ticker"])
        dedup.append(c_)
    results["candidates"] = dedup
    results["tickers"] = [c["ticker"] for c in dedup if c.get("tier") in ("2차", "3차")][:ALPHA_CAP]
    # 흐름 트랙(품질 미달 + 섹터 흐름 동조) — 중복 제거 + 상한
    _flow_seen, _flow = set(results["tickers"]), []
    for key in results.get("flow", {}):
        for f in results["flow"][key]:
            if f["ticker"] in _flow_seen:
                continue
            _flow_seen.add(f["ticker"])
            _flow.append(f)
    results["flow_candidates"] = _flow[:20]
    results["flow_tickers"] = [f["ticker"] for f in results["flow_candidates"]]
    with open(OUT, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n✅ 저장: {OUT}")
    print(f"   알파 트랙(품질·2·3차 병목) {len(results['tickers'])}종목: {', '.join(results['tickers'])}")
    if results.get("flow_tickers"):
        print(f"   🔥 흐름 트랙(품질 미달·모멘텀) {len(results['flow_tickers'])}종목: {', '.join(results['flow_tickers'])}")
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
