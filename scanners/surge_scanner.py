#!/usr/bin/env python3
"""
Surge Scanner — 22:00 KST (13:00 UTC) 실행용 데이터 수집기.
22시 실행은 미국 프리장 중이므로 premarket 데이터를 기준으로 판정한다.
기준: 프리장 +20% 이상 상승 + 거래대금(가격×거래량) $1M 이상 (거래량 급증 검증).
1. 메인 소스: stockanalysis.com/markets/premarket/ 프리장 상승 TOP10 (거래량 급증 내장)
2. 보조 소스: TradingView 프리장 스크리너 (+20%, 거래대금 $1M+)
3. regsho 스캐너(.regsho_state.json)가 지목했던 종목과 대조 → "맞춘 것" 확인
4. stdout JSON 출력 → LLM 에이전트가 원인 분석 후 Discord 보고
"""
import json, os, urllib.request, urllib.parse, re, html as html_mod, datetime, time, sys

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
REG_SHO_STATE = os.path.expanduser("~/.hermes/scripts/.regsho_state.json")
SCAN_URL = "https://scanner.tradingview.com/america/scan"
STOCKANALYSIS_URL = "https://stockanalysis.com/markets/premarket/"

MIN_CHG_PCT = 20.0          # 급등 기준: 프리장 +20% 이상
MIN_TRADE_VALUE = 1_000_000  # 거래대금(USD = 프리장가 × 프리장거래량) 최소 $1M
MIN_VOLUME = 100_000        # 프리장 거래량 하한 (초소형주 누락 방지)
MIN_PRICE = 0.10            # 저가주 포함
MIN_CAP = 0                  # 시총 하한 없음 — 급등률·거래대금으로 선별
PREMARKET_COLUMNS = ["name", "close", "change", "premarket_close", "premarket_change", "premarket_volume", "volume", "market_cap_basic", "sector", "exchange"]


def http_json(url, body=None):
    req = urllib.request.Request(url, data=body,
        headers={**UA, "Content-Type": "application/json"} if body else UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def fetch_stockanalysis_premarket():
    """stockanalysis.com 프리장 상승 TOP10 — 가격·거래량·거래대금 파싱.
    이 페이지는 '상승률 + 거래량 급증' 기준 상위 목록이라 TV가 놓치는 NCM/초소형주까지 커버."""
    req = urllib.request.Request(STOCKANALYSIS_URL, headers=UA)
    t = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
    rows = re.findall(r"<tr[^>]*>.*?</tr>", t, re.S)
    out = []
    for r in rows[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        if len(cells) < 6:
            continue
        vals = [re.sub(r"<[^>]+>", "", html_mod.unescape(c)).strip() for c in cells]
        try:
            rank, sym, name, chg_s, price_s, vol_s, cap_s = vals[:7]
        except ValueError:
            continue
        if rank == "1" and chg_s.startswith("-"):
            break  # 상승 섹션 끝 → 하락 섹션
        if not chg_s.endswith("%"):
            continue
        try:
            chg = float(chg_s.rstrip("%").replace(",", ""))
        except ValueError:
            continue
        if chg < MIN_CHG_PCT:
            continue
        try:
            price = float(price_s.replace(",", ""))
        except ValueError:
            continue
        try:
            vol = int(vol_s.replace(",", ""))
        except ValueError:
            vol = 0
        cap = 0
        m = re.match(r"([\d.]+)([MBT])", cap_s)
        if m:
            mult = {"M": 1e6, "B": 1e9, "T": 1e12}[m.group(2)]
            cap = int(float(m.group(1)) * mult)
        trade_value = price * vol
        if trade_value < MIN_TRADE_VALUE:
            continue  # 거래대금(USD) 미달 — 거래량만 많고 금액 작은 초저가주 제외
        out.append({
            "ticker": sym, "price": round(price, 2), "chg_pct": round(chg, 1),
            "volume": vol, "trade_value": int(trade_value),
            "regular_price": None, "regular_chg_pct": None,
            "market_cap": cap, "name": name[:60], "sector": "",
            "session": "PREMARKET", "source": "STOCKANALYSIS",
        })
    return out


def fetch_tv_gainers(limit=100):
    """TradingView 프리장 스크리너 — stockanalysis가 놓친 종목 보조 + 교차검증."""
    body = {
        "symbols": {"tickers": [], "query": {"types": ["stock"]}},
        "filter": [
            {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE", "AMEX"]},
            {"left": "market_cap_basic", "operation": "greater", "right": MIN_CAP},
            {"left": "premarket_close", "operation": "greater", "right": MIN_PRICE},
            {"left": "premarket_volume", "operation": "greater", "right": MIN_VOLUME},
            {"left": "premarket_change", "operation": "greater", "right": MIN_CHG_PCT},
        ],
        "columns": PREMARKET_COLUMNS,
        "sort": {"sortBy": "premarket_change", "sortOrder": "desc"},
        "range": [0, max(limit, 100)],
        "options": {"lang": "en"},
    }
    d = http_json(SCAN_URL, json.dumps(body).encode())
    out = []
    for it in d.get("data", []):
        s = it["s"]
        if ":" in s:
            exch, sym = s.split(":", 1)
        else:
            exch, sym = "", s
        if exch and exch not in ("NASDAQ", "NYSE", "AMEX"):
            continue
        n, close, chg, pm_price, pm_chg, pm_vol, vol, cap, sector, ex = it["d"]
        if pm_chg is None or pm_price is None or pm_chg < MIN_CHG_PCT:
            continue
        trade_value = (pm_price or 0) * int(pm_vol or 0)
        if trade_value < MIN_TRADE_VALUE:
            continue  # 거래대금 미달 제외
        out.append({
            "ticker": sym,
            "price": round(pm_price, 2),
            "chg_pct": round(pm_chg, 1),
            "volume": int(pm_vol or 0),
            "trade_value": int(trade_value),
            "regular_price": round(close, 2) if close is not None else None,
            "regular_chg_pct": round(chg, 1) if chg is not None else None,
            "market_cap": int(cap or 0),
            "name": (n or "")[:60],
            "sector": sector or "",
            "session": "PREMARKET",
            "source": "TradingView",
        })
    return out


def fetch_gainers(limit=40):
    """stockanalysis(메인) + TradingView(보조) 병합, 티커 중복 제거 후 상승률 정렬."""
    out = []
    seen = {}
    try:
        for g in fetch_stockanalysis_premarket():
            out.append(g)
            seen[g["ticker"]] = True
    except Exception as e:
        print(f"  stockanalysis fail: {e}", file=sys.stderr)
    try:
        for g in fetch_tv_gainers(max(limit, 100)):
            if g["ticker"] not in seen:
                out.append(g)
    except Exception as e:
        print(f"  tradingview fail: {e}", file=sys.stderr)
    out.sort(key=lambda x: -x["chg_pct"])
    return out


def fetch_ticker_quotes(tickers):
    """regsho 지목 종목들의 오늘 변동률을 Yahoo chart API(5d)로 직접 조회.
    소형주는 surge 필터(시총/가격)에 안 걸리므로 별도 조회가 필요. dict[ticker] = quote 반환."""
    if not tickers:
        return {}
    result = {}
    for tk in tickers:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?range=5d&interval=1d"
            d = http_json(url)
            res = d["chart"]["result"][0]
            meta = res.get("meta", {}) or {}
            quote = res["indicators"]["quote"][0]
            ts = res["timestamp"]
            closes = [quote["close"][i] for i in range(len(ts)) if quote["close"][i] is not None]
            vols = [quote["volume"][i] for i in range(len(ts)) if quote["volume"][i] is not None]
            # Yahoo 버그 보정: 최근 거래일 close가 None이면 meta 현재가로 대체
            # (예: IVF 8/17 close=None vol=113M → regularMarketPrice=1.53 사용)
            rmp = meta.get("regularMarketPrice")
            if rmp and closes:
                closes[-1] = rmp
            # 신선도 가드: 마지막 봉이 7일+ 지났고 meta 현재가도 없으면 스킵
            if ts:
                stale_days = (time.time() - ts[-1]) / 86400.0
                if stale_days > 7 and not rmp:
                    print(f"  stale quote skip {tk}: {stale_days:.0f}d old", file=sys.stderr)
                    continue
            if len(closes) < 2:
                continue
            today_c, prev_c = closes[-1], closes[-2]
            if prev_c and prev_c > 0:
                chg = (today_c - prev_c) / prev_c * 100
            else:
                chg = None
            result[tk] = {
                "ticker": tk,
                "price": round(today_c, 4) if today_c else None,
                "chg_pct": round(chg, 2) if chg is not None else None,
                "volume": int(vols[-1]) if vols else 0,
            }
        except Exception as e:
            print(f"  quote fail {tk}: {e}", file=sys.stderr)
        time.sleep(0.15)
    return result


def check_regsho_hits(gainers):
    """regsho 지목 종목이 오늘 급등했는지 대조 + 학습용 outcomes 누적 기록.
    오늘 스캔 상위 급등주(gainers)에 있는 경우 + 지목 종목 직접 조회 후 변동률 확인.
    (hits, all_results) 반환. all_results: 지목 전 종목의 오늘 변동률 dict (hit/miss 무관)."""
    hits = []
    if not os.path.exists(REG_SHO_STATE):
        return hits, {}
    try:
        state = json.load(open(REG_SHO_STATE, encoding="utf-8"))
    except Exception:
        return hits, {}
    picks = state.get("picks", {})
    if not picks:
        return hits, {}

    # 1) 오늘 급등주 상위 리스트에 지목 종목이 포함됐는지
    gainer_map = {g["ticker"]: g for g in gainers}
    # 2) 나머지 지목 종목은 직접 조회해 오늘 변동률 확인 (소형주 포함)
    remaining = [tk for tk in picks if tk not in gainer_map]
    quotes = fetch_ticker_quotes(remaining)

    all_results = {}
    today = datetime.date.today().strftime("%Y-%m-%d")
    for tk, v in picks.items():
        g = gainer_map.get(tk) or quotes.get(tk)
        if not g or g.get("chg_pct") is None:
            continue
        all_results[tk] = {"chg_pct": g["chg_pct"], "price": g.get("price")}
        if g["chg_pct"] >= MIN_CHG_PCT:  # +20% 이상만 '맞춤'으로 인정
            hits.append({
                "ticker": tk,
                "cat": v.get("cat", "?"),          # 발사대기 / 관찰
                "picked_score": v.get("score"),
                "picked_price": v.get("last"),
                "pick_date": v.get("ts"),
                "now_price": g["price"],
                "chg_pct": g["chg_pct"],
                "volume": g.get("volume"),
                "name": g.get("name", ""),
                "sector": g.get("sector", ""),
            })
    hits.sort(key=lambda x: -x["chg_pct"])

    # ── 학습용 outcomes 누적 (이미 기록된 종목은 갱신, 신규는 추가) ──
    outcomes = state.setdefault("outcomes", {})
    for tk, g in all_results.items():
        v = picks[tk]
        prev = outcomes.get(tk)
        entry = {
            "pick_date": v.get("ts", today),
            "cat": v.get("cat", "?"),
            "score": v.get("score"),
            "signals": v.get("signals", []),
            "picked_price": v.get("last"),
            "result_date": today,
            "chg_pct": g["chg_pct"],
            "hit": g["chg_pct"] >= MIN_CHG_PCT,
        }
        if prev and prev.get("pick_date") == entry["pick_date"]:
            # 같은 지목이 여러 번 결과 확인되면 최신(가장 큰 변동)으로 갱신
            if abs(g["chg_pct"]) >= abs(prev.get("chg_pct", 0)):
                outcomes[tk] = entry
        else:
            outcomes[tk] = entry
    # outcomes 상한 (최근 300건 유지)
    if len(outcomes) > 300:
        for tk in sorted(outcomes, key=lambda t: outcomes[t].get("pick_date", ""))[:len(outcomes)-300]:
            del outcomes[tk]
    try:
        with open(REG_SHO_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        print(f"  outcomes save fail: {e}", file=sys.stderr)

    return hits, all_results


def main():
    try:
        gainers = fetch_gainers(40)
    except Exception as e:
        print(json.dumps({"status": "ERROR", "error": str(e)}, ensure_ascii=False))
        return
    if not gainers:
        print(json.dumps({"status": "NO_GAINERS", "date": datetime.date.today().strftime("%Y-%m-%d")}, ensure_ascii=False))
        return
    hits, _ = check_regsho_hits(gainers)
    try:
        state = json.load(open(REG_SHO_STATE, encoding="utf-8"))
        pick_count = len(state.get("picks", {}))
    except Exception:
        pick_count = 0
    today = datetime.date.today().strftime("%Y-%m-%d")
    print(json.dumps({
        "status": "OK",
        "date": today,
        "scan_time_kst": "22:00",
        "gainers": gainers[:25],
        "regsho_hits": hits,
        "regsho_pick_count": pick_count,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()