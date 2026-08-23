#!/usr/bin/env python3
"""
Surge Scanner — 22:00 KST (13:00 UTC) 실행용 데이터 수집기.
급등주 기준 (2026-08-23, 위쿠 지정):
  - 하루(프리장/본장/데이장/앱장 무관) +30% 이상 상승
  - 거래량이 평소(20일 평균) 대비 비정상적으로 높음 (≥5배)
  - 거래대금(가격×거래량)도 평소 대비 비정상적으로 높음 (≥5배)
1. 메인 소스: stockanalysis.com/markets/gainers/ 당일 상승 TOP (세션 무관)
2. 보조 소스: TradingView 스크리너 (+30%)
3. Yahoo 3mo 차트로 vol_ratio / value_ratio 계산 (평소 대비 배수)
4. regsho 스캐너(.regsho_state.json)가 지목했던 종목과 대조 → "맞춘 것" 확인
5. stdout JSON 출력 → LLM 에이전트가 원인 분석 후 Discord 보고
"""
import json, os, urllib.request, urllib.parse, re, html as html_mod, datetime, time, sys

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
REG_SHO_STATE = os.path.expanduser("~/.hermes/scripts/.regsho_state.json")
SCAN_URL = "https://scanner.tradingview.com/america/scan"
GAINERS_URL = "https://stockanalysis.com/markets/gainers/"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{tk}?range=3mo&interval=1d"

MIN_CHG_PCT = 30.0       # 하루 +30% 이상 (세션 무관)
MIN_VOL_RATIO = 5.0      # 거래량 평소(20일 평균) 대비 5배 이상
MIN_VALUE_RATIO = 5.0    # 거래대금 평소 대비 5배 이상
MIN_PRICE = 0.10         # 저가주 포함
MIN_CAP = 0              # 시총 하한 없음
RATIO_WIN = 20           # 비율 비교 윈도우 (최근 20일 평균)
PREMARKET_COLUMNS = ["name", "close", "change", "premarket_close", "premarket_change", "premarket_volume", "volume", "market_cap_basic", "sector", "exchange"]


def http_json(url, body=None):
    req = urllib.request.Request(url, data=body,
        headers={**UA, "Content-Type": "application/json"} if body else UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def parse_cap(cap_s):
    cap = 0
    m = re.match(r"([\d.]+)([MBT])", cap_s)
    if m:
        cap = int(float(m.group(1)) * {"M": 1e6, "B": 1e9, "T": 1e12}[m.group(2)])
    return cap


def fetch_stockanalysis_gainers():
    """stockanalysis.com/markets/gainers/ — 당일 상승 TOP (프리장/본장 무관).
    NCM/초소형주까지 커버. 파싱 실패 시 빈 리스트."""
    req = urllib.request.Request(GAINERS_URL, headers=UA)
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
            break  # 상승 섹션 끝
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
        out.append({
            "ticker": sym, "price": round(price, 2), "chg_pct": round(chg, 1),
            "volume": vol, "trade_value": int(price * vol),
            "market_cap": parse_cap(cap_s), "name": name[:60], "sector": "",
            "source": "STOCKANALYSIS",
        })
    return out


def fetch_tv_gainers(limit=100):
    """TradingView 스크리너 보조 (+30% 상승)."""
    body = {
        "symbols": {"tickers": [], "query": {"types": ["stock"]}},
        "filter": [
            {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE", "AMEX"]},
            {"left": "market_cap_basic", "operation": "greater", "right": MIN_CAP},
            {"left": "premarket_close", "operation": "greater", "right": MIN_PRICE},
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
        out.append({
            "ticker": sym, "price": round(pm_price, 2), "chg_pct": round(pm_chg, 1),
            "volume": int(pm_vol or 0), "trade_value": int((pm_price or 0) * int(pm_vol or 0)),
            "market_cap": int(cap or 0), "name": (n or "")[:60], "sector": sector or "",
            "source": "TradingView",
        })
    return out


def fetch_ratios(ticker):
    """Yahoo 3mo 일봉으로 오늘 거래량·거래대금의 평소 대비 배수 계산.
    Returns (vol_ratio, value_ratio, chg_today, vol_today, value_today) or None.
    - vol_ratio = 오늘 거래량 / 최근 20일 평균 거래량
    - value_ratio = 오늘 거래대금(px×vol) / 최근 20일 평균
    - chg_today = 최근 종가 vs 전일 종가 (Yahoo 종가 None → meta 현재가로 보정)"""
    try:
        url = YAHOO_CHART.format(tk=ticker)
        d = http_json(url)
        res = d["chart"]["result"][0]
        meta = res.get("meta", {}) or {}
        quote = res["indicators"]["quote"][0]
        ts = res["timestamp"]
        closes, vols = [], []
        for i in range(len(ts)):
            c, v = quote["close"][i], quote["volume"][i]
            if c is not None and v is not None and v > 0:
                closes.append(c); vols.append(v)
        if len(closes) < 6:
            return None
        # Yahoo 종가 None 버그 보정 (급등일 close 누락 → meta 현재가)
        rmp = meta.get("regularMarketPrice")
        if rmp and closes:
            closes[-1] = rmp
        vol_today = vols[-1]
        avg_vol = sum(vols[-RATIO_WIN-1:-1]) / max(1, len(vols[-RATIO_WIN-1:-1]))
        vals = [c * v for c, v in zip(closes, vols)]
        val_today = vals[-1]
        avg_val = sum(vals[-RATIO_WIN-1:-1]) / max(1, len(vals[-RATIO_WIN-1:-1]))
        vol_ratio = vol_today / avg_vol if avg_vol > 0 else 0
        val_ratio = val_today / avg_val if avg_val > 0 else 0
        chg = (closes[-1] / closes[-2] - 1) * 100 if closes[-2] else 0
        return vol_ratio, val_ratio, chg, vol_today, val_today
    except Exception:
        return None


def finnhub_quote(ticker):
    """Finnhub 실시간 quote — Yahoo 종가 None/API 실패 시 폴백 소스.
    Returns {c: 현재가, pc: 전일종가, dp: 변동%, ...} or None."""
    key = os.environ.get("FINNHUB_KEY", "")
    if not key:
        for p in (os.path.expanduser("~/.hermes/scripts/.env"),
                  os.path.expanduser("~/marsAI/.env"), ".env"):
            try:
                for line in open(p, encoding="utf-8"):
                    if line.startswith("FINNHUB_KEY="):
                        key = line.strip().split("=", 1)[1]
                        break
            except Exception:
                continue
            if key:
                break
    if not key:
        return None
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={key}"
        req = urllib.request.Request(url, headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
        if d.get("c") is None or d.get("pc") is None:
            return None
        return d
    except Exception:
        return None


def enrich_and_filter(candidates):
    """후보별 vol_ratio/value_ratio 계산 후 기준 통과분만 반환.
    Yahoo 실패 시 Finnhub 변동%로만 판정하고 비율은 None."""
    out = []
    for g in candidates:
        r = fetch_ratios(g["ticker"])
        if r:
            vol_ratio, val_ratio, chg, vol_today, val_today = r
            g = dict(g)
            g["chg_pct"] = round(chg, 1)      # Yahoo 기준 하루 변동% (현재가 보정 포함)
            g["volume"] = vol_today
            g["trade_value"] = int(val_today)
            g["vol_ratio"] = round(vol_ratio, 1)
            g["value_ratio"] = round(val_ratio, 1)
            if chg >= MIN_CHG_PCT and vol_ratio >= MIN_VOL_RATIO and val_ratio >= MIN_VALUE_RATIO:
                out.append(g)
        else:
            # Yahoo 실패 → Finnhub 폴백 (기준 변동%만 적용, 비율은 미검증이므로 제외)
            fq = finnhub_quote(g["ticker"])
            if fq and fq.get("dp") is not None and fq["dp"] >= MIN_CHG_PCT:
                g2 = dict(g)
                g2["chg_pct"] = round(fq["dp"], 1)
                g2["price"] = fq.get("c")
                g2["vol_ratio"] = None
                g2["value_ratio"] = None
                out.append(g2)
        time.sleep(0.12)
    out.sort(key=lambda x: -x["chg_pct"])
    return out


def fetch_gainers(limit=40):
    """stockanalysis(메인) + TradingView(보조) 후보 → 비율 검증 후 병합."""
    candidates = []
    seen = set()
    try:
        for g in fetch_stockanalysis_gainers():
            candidates.append(g)
            seen.add(g["ticker"])
    except Exception as e:
        print(f"  stockanalysis fail: {e}", file=sys.stderr)
    try:
        for g in fetch_tv_gainers(max(limit, 100)):
            if g["ticker"] not in seen:
                candidates.append(g)
    except Exception as e:
        print(f"  tradingview fail: {e}", file=sys.stderr)
    return enrich_and_filter(candidates)


def fetch_ticker_quotes(tickers):
    """regsho 지목 종목들의 오늘 변동률 조회. Finnhub(우선) → Yahoo(폴백)."""
    if not tickers:
        return {}
    result = {}
    for tk in tickers:
        fq = finnhub_quote(tk)
        if fq:
            result[tk] = {
                "ticker": tk,
                "price": fq.get("c"),
                "chg_pct": round(fq["dp"], 2) if fq.get("dp") is not None else None,
                "volume": 0,
            }
            time.sleep(0.15)
            continue
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?range=5d&interval=1d"
            d = http_json(url)
            res = d["chart"]["result"][0]
            meta = res.get("meta", {}) or {}
            quote = res["indicators"]["quote"][0]
            ts = res["timestamp"]
            closes = [quote["close"][i] for i in range(len(ts)) if quote["close"][i] is not None]
            vols = [quote["volume"][i] for i in range(len(ts)) if quote["volume"][i] is not None]
            rmp = meta.get("regularMarketPrice")
            if rmp and closes:
                closes[-1] = rmp
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
    (hits, all_results) 반환."""
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

    gainer_map = {g["ticker"]: g for g in gainers}
    remaining = [tk for tk in picks if tk not in gainer_map]
    quotes = fetch_ticker_quotes(remaining)

    all_results = {}
    today = datetime.date.today().strftime("%Y-%m-%d")
    for tk, v in picks.items():
        g = gainer_map.get(tk) or quotes.get(tk)
        if not g or g.get("chg_pct") is None:
            continue
        all_results[tk] = {"chg_pct": g["chg_pct"], "price": g.get("price")}
        if g["chg_pct"] >= MIN_CHG_PCT:
            hits.append({
                "ticker": tk,
                "cat": v.get("cat", "?"),
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
            if abs(g["chg_pct"]) >= abs(prev.get("chg_pct", 0)):
                outcomes[tk] = entry
        else:
            outcomes[tk] = entry
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