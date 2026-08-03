#!/usr/bin/env python3
"""
Reg SHO Threshold Squeeze Scanner
- Pulls latest Nasdaq Reg SHO threshold list (FTD / naked-short accumulation names)
- Keeps only S-category small caps (skips leveraged ETFs = G category noise)
- Scores precursor (전조) signals: volume dry-up, BB squeeze, base proximity, freshness, pop history
- Outputs a formatted Discord message to stdout (no_agent cron mode)
"""
import urllib.request, json, re, time, datetime, sys, os

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MAX_DAYS_BACK = 10
# 지난 실행에서 지목한 종목의 실제 성과를 다음 실행에서 보고하는 추적 기능
STATE_FILE = os.path.expanduser("~/.hermes/scripts/.regsho_state.json")
TRACK_DAYS = 7        # 이 기간(일) 안의 지목만 비교 대상
RECORD_LIMIT = 30     # 상태에 저장할 지목 종목 상한

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"picks": {}}

def save_state(s):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)

def http_get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", errors="replace")

def get_latest_list():
    """Return (file_url, text) for the freshest nasdaqth*.txt file."""
    try:
        html = http_get("https://www.nasdaqtrader.com/trader.aspx?id=RegSHOThreshold")
        m = re.findall(r'href="([^"]*nasdaqth\d{8}\.txt)"', html, re.I)
        if m:
            url = m[0]
            if not url.startswith("http"):
                url = "https://www.nasdaqtrader.com" + url
            return url, http_get(url)
    except Exception:
        pass
    for i in range(MAX_DAYS_BACK):
        d = (datetime.date.today() - datetime.timedelta(days=i)).strftime("%Y%m%d")
        url = f"https://www.nasdaqtrader.com/dynamic/symdir/regsho/nasdaqth{d}.txt"
        try:
            txt = http_get(url)
            if "Symbol" in txt:
                return url, txt
        except Exception:
            continue
    return None, None

def fetch_daily(ticker):
    """Fetch ~3mo daily closes+volumes from Yahoo chart API."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=3mo&interval=1d"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode())
    res = d["chart"]["result"][0]
    quote = res["indicators"]["quote"][0]
    ts = res["timestamp"]
    closes, vols, highs = [], [], []
    for i in range(len(ts)):
        c, v, h = quote["close"][i], quote["volume"][i], quote["high"][i]
        if c is not None and h is not None:
            closes.append(c); highs.append(h)
            vols.append(v if v is not None else 0)
    if len(closes) < 25:
        return None
    return closes, vols, highs

def score_ticker(t):
    closes, vols, highs = t
    n = len(closes)
    last = closes[-1]
    # daily returns over last 30 sessions
    rets30 = [(closes[i]/closes[i-1]-1)*100 for i in range(max(1,n-30), n)]
    max1d_30 = max(rets30) if rets30 else 0.0
    # last 5 sessions
    rets5 = [(closes[i]/closes[i-1]-1)*100 for i in range(max(1,n-5), n)]
    max5 = max(rets5) if rets5 else 0.0
    # volume dry-up: 5d avg / 20d avg
    v5 = sum(vols[-5:])/5 if vols else 0
    v20 = sum(vols[-20:])/20 if vols else 1
    vol_ratio = v5/v20 if v20 > 0 else 1.0
    # BB (20,2) on closes
    import statistics
    win = closes[-20:]
    mid = sum(win)/20
    sd = statistics.stdev(win) if len(win) > 1 else 0
    bbw = (4*sd/mid*100) if mid else 0
    # distance from 20d high
    hi20 = max(highs[-20:])
    dist_high = (hi20 - last)/hi20*100
    # 30d total
    tot30 = (last/closes[-min(31,n)]-1)*100

    score = 0
    reasons = []
    if vol_ratio <= 0.7:
        score += 2; reasons.append(f"거래량말림 {vol_ratio:.2f}x")
    elif vol_ratio <= 1.0:
        score += 1; reasons.append(f"거래량 {vol_ratio:.2f}x")
    if bbw < 12:
        score += 2; reasons.append(f"BB스퀴즈 {bbw:.0f}%")
    elif bbw < 18:
        score += 1; reasons.append(f"BB {bbw:.0f}%")
    if dist_high <= 8:
        score += 2; reasons.append("고점근접")
    elif dist_high <= 15:
        score += 1
    if max5 < 10:
        score += 2; reasons.append("5일조용")
    elif max5 < 25:
        score += 1
    if max1d_30 >= 50:
        score += 2; reasons.append(f"폭발이력+{max1d_30:.0f}%")
    elif max1d_30 >= 20:
        score += 1
    if last >= 0.5:
        score += 1
    return {
        "ticker": None, "last": last, "score": score, "max1d_30": max1d_30,
        "max5": max5, "vol_ratio": vol_ratio, "bbw": bbw, "dist_high": dist_high,
        "tot30": tot30, "reasons": reasons
    }

def fmt_pct(x):
    return f"{x:+.0f}%"

def record_picks(state, date_str, fresh, watch):
    """이 실행에서 지목한 종목들을 상태에 기록 (이후 실행에서 실제 성과와 비교용)."""
    picks = {}
    for r in fresh:
        picks[r["ticker"]] = {"cat": "발사대기", "last": r["last"], "score": r["score"],
                              "ts": date_str}
    for r in watch:
        # 발사대기 우선 — 이미 있는 종목은 발사대기로 유지
        if r["ticker"] not in picks:
            picks[r["ticker"]] = {"cat": "관찰", "last": r["last"], "score": r["score"],
                                  "ts": date_str}
    # 기존 저장과 병합 + 오래된 지목 정리(TRACK_DAYS 초과 제거) + 상한
    merged = state.setdefault("picks", {})
    cutoff = (datetime.date.today() - datetime.timedelta(days=TRACK_DAYS)).strftime("%Y-%m-%d")
    for tk in list(merged.keys()):
        if merged[tk].get("ts", "") < cutoff:
            del merged[tk]
    for tk, v in picks.items():
        merged[tk] = v
    # 상한 유지 — 최신(ts desc) 기준으로
    if len(merged) > RECORD_LIMIT:
        for tk in sorted(merged, key=lambda t: merged[t].get("ts", ""))[: len(merged) - RECORD_LIMIT]:
            del merged[tk]
    state["last_scan"] = date_str

def build_tracking_lines(merged, results):
    """지목 종목들을 최신 현재가(오늘 리스트 또는 신규 조회)와 대비해 성과 라인 생성.
    오늘 리스트(results)에 지목 종목이 있으면 그 데이터를, 없으면 fetch_daily로 현재가를 조회한다."""
    if not merged:
        return []
    lookup = {r["ticker"]: r for r in results}
    now = datetime.date.today()
    rows = []
    for tk, v in list(merged.items()):
        r = lookup.get(tk)
        if not r:
            # 오늘 리스트에 없으면 신규로 현재가 조회
            try:
                d = fetch_daily(tk)
                if d:
                    closes, vols, highs = d
                    if len(closes) < 5:
                        continue
                    last = closes[-1]
                    rets5 = [(closes[i]/(closes[i-1])-1)*100 for i in range(max(1,len(closes)-4), len(closes))]
                    mx5 = max(rets5)
                    r = {"last": last, "max5": mx5, "score": None}
                else:
                    continue
            except Exception:
                continue
        try:
            vdate = datetime.datetime.strptime(v["ts"], "%Y-%m-%d").date()
        except Exception:
            vdate = now
        age = (now - vdate).days
        chg = (r["last"] - v["last"]) / v["last"] * 100 if v["last"] else 0
        if r["max5"] >= 35:
            status = "✅ 터짐🚀"
        elif chg >= 20:
            status = "✅ 상승"
        elif chg <= -20:
            status = "❌ 하락"
        else:
            status = "⏳ 대기"
        sc = f"{v['score']}" if r["score"] is None else f"{v['score']}"
        rows.append((tk, age, v["last"], r["last"], chg, status, v["score"], r["max5"]))
        if len(rows) >= 10:
            break

    if not rows:
        return []
    lines = ["📊 **이전 지목 종목 대비 실제 성과**", "",
             "`티커  지목후  지목시점  현재가   변동%   상태     점수 5일최대`"]
    for tk, age, vp, rp, chg, status, score, mx5 in rows:
        lines.append(f"`{tk:<6} {age}d    ${vp:<7.2f}${rp:<7.2f}{chg:+.1f}%  {status:<7}{score:<4}{mx5:.0f}%`")
        _ = score
    return lines

def main():
    url, txt = get_latest_list()
    if not txt:
        print("⚠️ Reg SHO 리스트를 가져오지 못했어 (nasdaqtrader.com 다운?). 다음 실행에서 재시도할게.")
        return
    # file date from URL
    m = re.search(r'nasdaqth(\d{8})\.txt', url)
    file_date = m.group(1) if m else "?"
    fdate = f"{file_date[4:6]}/{file_date[6:8]}"
    lines = txt.strip().splitlines()
    symbols = []
    for ln in lines[1:]:
        parts = ln.split("|")
        if len(parts) >= 3 and parts[2].strip() == "S":
            symbols.append(parts[0].strip())
    if not symbols:
        print("⚠️ 리스트에 S(소형주) 카테고리 종목이 없어.")
        return

    results = []
    for sym in symbols:
        try:
            d = fetch_daily(sym)
            if not d:
                continue
            r = score_ticker(d)
            r["ticker"] = sym
            results.append(r)
        except Exception:
            pass
        time.sleep(0.25)
    if not results:
        print("⚠️ 스캔 결과 없음 (데이터 문제).")
        return

    fresh = sorted([r for r in results if r["score"] >= 5 and r["max5"] < 35], key=lambda r: -r["score"])
    watch = sorted([r for r in results if 3 <= r["score"] < 5 and r["max5"] < 35], key=lambda r: -r["score"])
    popped = sorted([r for r in results if r["max5"] >= 35], key=lambda r: -r["max5"])

    out = []
    out.append(f"📋 **Reg SHO 급등 전조 스캔** (리스트 {fdate}, S종목 {len(symbols)}개)")
    out.append(f"출처: nasdaqtrader.com — FTD 5일 연속 누적 = 나체숏 축적 종목")
    out.append("")

    # 0) 이전 실행에서 지목했던 종목들의 실제 성과 먼저 보고 (있다면)
    state = load_state()
    track_lines = build_tracking_lines(state.get("picks", {}), results)
    if track_lines:
        out.append("━━━━━━━━━━━━━━━━━━━━━━━")
        out.extend(track_lines)
        out.append("")
        out.append("")

    if fresh:
        out.append(f"🔥 **발사 대기 후보** (전조점수 {fresh[0]['score']}~{fresh[-1]['score']})")
        out.append("`티커  가격    점수 30d최대 5일최대 BB밴드 거래량  사유`")
        for r in fresh[:8]:
            out.append(f"`{r['ticker']:<6}${r['last']:<7.2f}{r['score']:<5}{fmt_pct(r['max1d_30']):<7}{fmt_pct(r['max5']):<7}{r['bbw']:.0f}%    {r['vol_ratio']:.2f}x  {' '.join(r['reasons'][:2])}`")
        out.append("")
    if watch:
        out.append(f"⏰ **관찰** (점수 3-4)")
        out.append("`" + "  ".join(f"{r['ticker']} ${r['last']:.2f} ({r['score']})" for r in watch[:10]) + "`")
        out.append("")
    if popped:
        out.append(f"💥 **이미 터짐** (최근5일 +35%↑, 숏 아직 안풀림)")
        out.append("`" + "  ".join(f"{r['ticker']}+{r['max5']:.0f}%" for r in popped[:10]) + "`")
        out.append("")
    out.append("— 전조점수: 거래량말림+BB스퀴즈+고점근접+5일조용+폭발이력. S종목만, ETF 제외.")
    out.append("— 룰: 발사는 캐탈리스트(뉴스)와 함께. 프리마켓 RVOL>3 확인 후 진입.")
    out.append("")
    # ticker-only summary at the end, grouped by score
    pool = [r for r in results if r["max5"] < 35 and r["score"] >= 3]
    pool.sort(key=lambda r: -r["score"])
    if pool:
        out.append("📌 **티커 정리 (점수별)**")
        for s in range(10, 2, -1):
            grp = [r["ticker"] for r in pool if r["score"] == s]
            if grp:
                out.append(f"`{s}점  {' '.join(grp)}`")

    msg = "\n".join(out)
    # Discord 2000 char cap
    if len(msg) > 1950:
        msg = msg[:1950] + "\n…(일부 생략)"
    print(msg)

    # 이번 지목 종목 저장 (다음 실행에서 실제 성과와 비교)
    record_picks(state, datetime.date.today().strftime("%Y-%m-%d"), fresh, watch)
    save_state(state)
    print(f"  (tracking saved {len(fresh)+len(watch)} picks)", file=sys.stderr)

if __name__ == "__main__":
    main()
