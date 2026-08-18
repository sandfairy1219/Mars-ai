#!/usr/bin/env python3
"""
Reg SHO Threshold Squeeze Scanner
- Pulls latest Nasdaq Reg SHO threshold list (FTD / naked-short accumulation names)
- Keeps only S-category small caps (skips leveraged ETFs = G category noise)
- Scores precursor (전조) signals: volume dry-up, BB squeeze, base proximity, freshness, pop history
- Self-learning: signal weights auto-adjusted by learning_engine.py based on hit/miss outcomes
- Outputs a formatted Discord message to stdout (no_agent cron mode)
"""
import urllib.request, json, re, time, datetime, sys, os

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MAX_DAYS_BACK = 10
# 지난 실행에서 지목한 종목의 실제 성과를 다음 실행에서 보고하는 추적 기능
STATE_FILE = os.path.expanduser("~/.hermes/scripts/.regsho_state.json")
WEIGHTS_FILE = os.path.expanduser("~/.hermes/scripts/.regsho_weights.json")
TRACK_DAYS = 7        # 이 기간(일) 안의 지목만 비교 대상
RECORD_LIMIT = 30     # 상태에 저장할 지목 종목 상한

# 신호 기본 가중치 (learning_engine.py가 .regsho_weights.json으로 학습 조정)
DEFAULT_WEIGHTS = {
    "vol_dry": 2.0, "vol_low": 1.0,
    "bb_squeeze": 2.0, "bb_tight": 1.0,
    "near_high": 2.0, "near_high_15": 1.0,
    "quiet5": 2.0, "quiet25": 1.0,
    "pop50": 2.0, "pop20": 1.0,
    "price_ok": 1.0,
}
WEIGHTS = None

def load_weights():
    """학습된 신호 가중치 + 적응형 임계값 로드 (없으면 기본값). 전역 WEIGHTS에 캐시."""
    global WEIGHTS
    if WEIGHTS is not None:
        return WEIGHTS
    w = dict(DEFAULT_WEIGHTS)
    try:
        with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.get("weights", {}).items():
            if k in w and isinstance(v, (int, float)) and v > 0:
                w[k] = float(v)
        w["_threshold"] = float(data.get("adaptive_threshold", 5.0))
    except Exception:
        w["_threshold"] = 5.0
    WEIGHTS = w
    return w

def get_signal_weight(sig):
    return load_weights().get(sig, 1.0)

def get_threshold():
    """적응형 발사대기 최소 점수 (학습 엔진이 조정, 5.0~8.0)."""
    return load_weights().get("_threshold", 5.0)

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"picks": {}, "outcomes": {}}

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

def finnhub_quote(ticker):
    """Finnhub 실시간 quote — Yahoo 종가 None/API 실패 시 폴백 소스.
    Returns {c: 현재가, pc: 전일종가, dp: 변동%, h, l, o} or None.
    키는 .env(FINNHUB_KEY)에서 읽는다 — PUBLIC 레포에 하드코딩 금지."""
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


def fetch_daily(ticker):
    """Fetch ~3mo daily closes+volumes from Yahoo chart API.
    Returns (closes, vols, highs) or (closes, vols, highs, splits) with split events.
    splits: dict {date_str: ratio} — ratio<1 = 리버스 스플릿(주식 병합), ratio>1 = 정방향 스플릿."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=3mo&interval=1d&events=split"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode())
    res = d["chart"]["result"][0]
    meta = res.get("meta", {}) or {}
    quote = res["indicators"]["quote"][0]
    ts = res["timestamp"]
    closes, vols, highs = [], [], []
    for i in range(len(ts)):
        c, v, h = quote["close"][i], quote["volume"][i], quote["high"][i]
        if c is not None and h is not None:
            closes.append(c); highs.append(h)
            vols.append(v if v is not None else 0)
    # Yahoo 버그 보정: 최근 거래일 close가 None으로 오는 경우(급등일 Vol만 있고 close 누락)
    # meta.regularMarketPrice(정규장 현재가/직전 종가)를 마지막 유효 봉에 반영한다.
    # 예: IVF 8/17 — bars 마지막 close=None, vol=113M, meta.regularMarketPrice=1.53
    rmp = meta.get("regularMarketPrice")
    if rmp and closes:
        closes[-1] = rmp
    else:
        # Yahoo 종가/현재가가 없으면 Finnhub 실시간 quote로 보정 (교차검증+폴백)
        fq = finnhub_quote(ticker)
        if fq and closes:
            closes[-1] = fq["c"]
            print(f"  finnhub fallback {ticker}: close={fq['c']} (Yahoo close None)", file=sys.stderr)
    # 데이터 신선도 가드: 마지막 봉이 일주일 이상 지났고 meta 현재가도 없으면
    # 오래된 종가로 잘못된 변동률을 계산하지 않도록 스킵 (STALE 데이터 방지)
    if ts:
        last_bar_ts = ts[-1]
        stale_days = (time.time() - last_bar_ts) / 86400.0
        if stale_days > 7 and not rmp:
            print(f"  stale data skip {ticker}: last bar {stale_days:.0f}d old", file=sys.stderr)
            return None
    if len(closes) < 25:
        return None
    # Split events (리버스 스플릿 = 주식 병합, 주가 점프의 원인)
    splits = {}
    events = res.get("events", {}) or {}
    for ev in events.get("splits", {}).values():
        try:
            edate = datetime.datetime.utcfromtimestamp(ev["date"]).strftime("%Y-%m-%d")
            splits[edate] = ev.get("numerator", 1) / ev.get("denominator", 1)
        except Exception:
            continue
    return closes, vols, highs, splits

def score_ticker(t):
    closes, vols, highs = t[:3]
    splits = t[3] if len(t) > 3 else {}
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

    # ── 리버스 스플릿(주식 병합) 감지 — 주가 점프는 실제 상승이 아님 ──
    # 병합 후 30일 내 지표(max5/max1d_30)가 오염되므로 해당 기간은 후보에서 제외
    rev_split_recent = False
    rev_split_ratio = None
    for edate, ratio in splits.items():
        if ratio < 1:  # 리버스 스플릿
            try:
                ed = datetime.datetime.strptime(edate, "%Y-%m-%d").date()
                if (datetime.date.today() - ed).days <= 30:
                    rev_split_recent = True
                    rev_split_ratio = ratio
            except Exception:
                continue

    # ── 신호 코드 판정 (학습용) ──
    signals = []
    if vol_ratio <= 0.7: signals.append("vol_dry")
    elif vol_ratio <= 1.0: signals.append("vol_low")
    if bbw < 12: signals.append("bb_squeeze")
    elif bbw < 18: signals.append("bb_tight")
    if dist_high <= 8: signals.append("near_high")
    elif dist_high <= 15: signals.append("near_high_15")
    if max5 < 10: signals.append("quiet5")
    elif max5 < 25: signals.append("quiet25")
    if max1d_30 >= 50: signals.append("pop50")
    elif max1d_30 >= 20: signals.append("pop20")
    if last >= 0.5: signals.append("price_ok")

    # ── 학습 가중치 적용 점수 ──
    w = load_weights()
    score = 0
    reasons = []
    for sig in signals:
        sw = w.get(sig, 1.0)
        score += sw
    score = round(score)
    if "vol_dry" in signals: reasons.append(f"거래량말림 {vol_ratio:.2f}x")
    elif "vol_low" in signals: reasons.append(f"거래량 {vol_ratio:.2f}x")
    if "bb_squeeze" in signals: reasons.append(f"BB스퀴즈 {bbw:.0f}%")
    elif "bb_tight" in signals: reasons.append(f"BB {bbw:.0f}%")
    if "near_high" in signals: reasons.append("고점근접")
    if "quiet5" in signals: reasons.append("5일조용")
    if "pop50" in signals: reasons.append(f"폭발이력+{max1d_30:.0f}%")
    elif "pop20" in signals: reasons.append(f"폭발이력+{max1d_30:.0f}%")
    # 리버스 스플릿(병합) 종목은 실제 상승이 아니므로 별도 플래그 + 사유 표기
    if rev_split_recent:
        reasons.append(f"⚠️병합(1:{int(1/rev_split_ratio) if rev_split_ratio else '?'})")
    return {
        "ticker": None, "last": last, "score": score, "max1d_30": max1d_30,
        "max5": max5, "vol_ratio": vol_ratio, "bbw": bbw, "dist_high": dist_high,
        "tot30": tot30, "reasons": reasons, "signals": signals,
        "rev_split": rev_split_recent, "rev_split_ratio": rev_split_ratio
    }

def fmt_pct(x):
    return f"{x:+.0f}%"

def record_picks(state, date_str, fresh, watch):
    """이 실행에서 지목한 종목들을 상태에 기록 (이후 실행에서 실제 성과와 비교 + 학습용).
    리버스 스플릿(병합) 종목은 제외 — 가짜 상승이 학습 데이터를 오염시킴."""
    picks = {}
    for r in fresh:
        if r.get("rev_split"):
            continue
        picks[r["ticker"]] = {"cat": "발사대기", "last": r["last"], "score": r["score"],
                              "signals": r.get("signals", []),
                              "ts": date_str}
    for r in watch:
        # 발사대기 우선 — 이미 있는 종목은 발사대기로 유지
        if r.get("rev_split"):
            continue
        if r["ticker"] not in picks:
            picks[r["ticker"]] = {"cat": "관찰", "last": r["last"], "score": r["score"],
                                  "signals": r.get("signals", []),
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
            # 오늘 리스트에 없으면 신규로 현재가 조회 — Finnhub(우선, 종가 None 버그 없음) → Yahoo
            try:
                fq = finnhub_quote(tk)
                if fq:
                    r = {"last": fq["c"], "max5": None, "score": None, "rev_split": False}
                else:
                    d = fetch_daily(tk)
                    if d:
                        closes, vols, highs = d
                        if len(closes) < 5:
                            continue
                        last = closes[-1]
                        rets5 = [(closes[i]/(closes[i-1])-1)*100 for i in range(max(1,len(closes)-4), len(closes))]
                        mx5 = max(rets5)
                        r = {"last": last, "max5": mx5, "score": None, "rev_split": False}
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
        mx5 = r.get("max5") or 0  # Finnhub 경로는 max5가 없을 수 있음 → None-safe
        if r.get("rev_split"):
            status = "🚫 병합"
        elif mx5 >= 35:
            status = "✅ 터짐🚀"
        elif chg >= 20:
            status = "✅ 상승"
        elif chg <= -20:
            status = "❌ 하락"
        else:
            status = "⏳ 대기"
        sc = f"{v['score']}" if r["score"] is None else f"{v['score']}"
        rows.append((tk, age, v["last"], r["last"], chg, status, v["score"], mx5))
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

    # 리버스 스플릿(병합) 종목 분리 — 주가 점프는 실제 상승이 아니므로 '터짐'에서 제외
    merged_out = [r for r in results if r.get("rev_split")]
    real = [r for r in results if not r.get("rev_split")]

    # 적응형 임계값 (학습 엔진이 적중률 기반으로 조정 — 후보 수 자동 조절)
    th = get_threshold()
    fresh = sorted([r for r in real if r["score"] >= th and r["max5"] < 35], key=lambda r: -r["score"])
    watch = sorted([r for r in real if (th - 2) <= r["score"] < th and r["max5"] < 35], key=lambda r: -r["score"])
    popped = sorted([r for r in real if r["max5"] >= 35], key=lambda r: -r["max5"])

    out = []
    out.append(f"📋 **Reg SHO 급등 전조 스캔** (리스트 {fdate}, S종목 {len(symbols)}개)")
    out.append(f"출처: nasdaqtrader.com — FTD 5일 연속 누적 = 나체숏 축적 종목")
    if th > 5.0:
        out.append(f"🧠 학습 임계값 {th:.1f}점↑ — 후보 좁혀서 적중률 최적화 중")
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
    if merged_out:
        # 리버스 스플릿(병합) 종목 — 주가 점프는 실제 상승 아님, 표시만 하고 후보에서 제외
        out.append(f"🚫 **주식병합 제외** (리버스 스플릿 — 가격 점프는 실제 상승 아님)")
        out.append("`" + "  ".join(f"{r['ticker']} 1:{int(1/(r.get('rev_split_ratio') or 0.1))}" for r in merged_out[:8]) + "`")
        out.append("")
    out.append("— 전조점수: 거래량말림+BB스퀴즈+고점근접+5일조용+폭발이력. S종목만, ETF 제외.")
    out.append("— 룰: 발사는 캐탈리스트(뉴스)와 함께. 프리마켓 RVOL>3 확인 후 진입.")
    out.append("")
    # ticker-only summary at the end, grouped by score (병합 종목 제외, 임계값 적용)
    pool = [r for r in real if r["max5"] < 35 and r["score"] >= (th - 2)]
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
