#!/usr/bin/env python3
"""
Reg SHO Prediction Learning Engine — 주간 실행 (learning-weekly cron).
1. 누적 outcomes(.regsho_state.json) 분석: 맞춘 것 vs 못 맞춘 것
2. 신호별 예측력(lift) 계산: 각 전조신호가 있었던 종목의 적중률 vs 전체 적중률
3. 베이즈 스타일 가중치 갱신 → .regsho_weights.json (regsho 스캐너가 다음 스캔부터 사용)
4. 학습 리포트 stdout 출력 → Discord 전송

가중치 수식 (베이즈 축소):
  w_new = w_old * (0.5 + 0.5 * lift)  , lift = 신호조건 적중률 / 전체 적중률
  클램프: [0.25, 4.0]  — 과적합 방지 (최소 샘플 4 미만이면 변경 안 함)
"""
import json, os, sys, datetime

STATE_FILE = os.path.expanduser("~/.hermes/scripts/.regsho_state.json")
WEIGHTS_FILE = os.path.expanduser("~/.hermes/scripts/.regsho_weights.json")

DEFAULT_WEIGHTS = {
    "vol_dry": 2.0, "vol_low": 1.0,
    "bb_squeeze": 2.0, "bb_tight": 1.0,
    "near_high": 2.0, "near_high_15": 1.0,
    "quiet5": 2.0, "quiet25": 1.0,
    "pop50": 2.0, "pop20": 1.0,
    "price_ok": 1.0,
}
SIGNAL_NAMES = {
    "vol_dry": "거래량말림", "vol_low": "거래량저조",
    "bb_squeeze": "BB스퀴즈", "bb_tight": "BB수축",
    "near_high": "고점근접", "near_high_15": "고점근처",
    "quiet5": "5일조용", "quiet25": "5일잠잠",
    "pop50": "폭발이력↑", "pop20": "폭발이력",
    "price_ok": "가격충족",
}
MIN_SAMPLES = 4       # 이보다 적은 샘플은 가중치 변경 금지
W_CLAMP = (0.25, 4.0) # 가중치 상하한
HIT_PCT = 20.0       # +20% 이상 = 적중 (surge/regsho 스캐너 기준과 통일 — 8/23 기준)
# 2026-08-23: 스캐너 기준이 +20%로 변경됨에 따라 학습 엔진도 통일 (기존 +100% → 일괄 재분류)
# ── 적응형 임계값 (후보 수 자동 조절) ──
DEFAULT_THRESHOLD = 5.0   # 기본 발사대기 최소 점수
TARGET_HIT_RATE = 0.30    # 목표 적중률 30% — 학습이 성숙해지면 이걸 달성하도록 후보를 좁힘
TH_MAX = 8.0              # 임계값 상한 (너무 좁히면 후보 0 — 상한으로 방지)

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"picks": {}, "outcomes": {}}

def load_weights():
    try:
        with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": 1, "updated": None, "weights": dict(DEFAULT_WEIGHTS), "history": []}

def save_weights(data):
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def outcome_is_hit(o):
    """+100% 이상만 진짜 급등 적중으로 인정. 과거 +5% 기준 데이터도 재분류한다."""
    return float(o.get("chg_pct", 0) or 0) >= HIT_PCT

def analyze(outcomes):
    """outcomes 분석 → (전체통계, 신호별통계, 카테고리별통계, 점수대별통계)."""
    total = len(outcomes)
    hits = sum(1 for o in outcomes.values() if outcome_is_hit(o))
    base_rate = (hits / total) if total else 0

    # 신호별: 신호 있었던 종목 중 적중 비율
    sig_stats = {}
    for o in outcomes.values():
        for s in o.get("signals", []):
            st = sig_stats.setdefault(s, {"n": 0, "hit": 0})
            st["n"] += 1
            if outcome_is_hit(o):
                st["hit"] += 1

    # 카테고리별
    cat_stats = {}
    for o in outcomes.values():
        c = o.get("cat", "?")
        ct = cat_stats.setdefault(c, {"n": 0, "hit": 0})
        ct["n"] += 1
        if outcome_is_hit(o):
            ct["hit"] += 1

    # 점수대별
    score_stats = {}
    for o in outcomes.values():
        sc = o.get("score")
        if sc is None:
            continue
        bucket = sc if sc <= 9 else 9
        st = score_stats.setdefault(bucket, {"n": 0, "hit": 0})
        st["n"] += 1
        if outcome_is_hit(o):
            st["hit"] += 1

    return {
        "total": total, "hits": hits, "base_rate": base_rate,
        "signals": sig_stats, "cats": cat_stats, "scores": score_stats,
    }

def compute_new_weights(stats, weights_data):
    """신호별 lift 기반 가중치 갱신. (new_weights, changes) 반환."""
    old = weights_data.get("weights", dict(DEFAULT_WEIGHTS))
    new = dict(old)
    base_rate = stats["base_rate"] or 0.05  # 0 방지
    changes = []
    for sig, base_w in DEFAULT_WEIGHTS.items():
        st = stats["signals"].get(sig)
        if not st or st["n"] < MIN_SAMPLES:
            continue
        sig_rate = st["hit"] / st["n"]
        lift = sig_rate / base_rate if base_rate > 0 else 1.0
        # 베이즈 축소: lift를 0.5~1.5 범위로 부드럽게 (샘플이 많을수록 원래 lift에 가깝게)
        shrink = min(1.0, st["n"] / 20.0)  # 20샘플 이상이면 완전 반영
        adj = 1.0 + (lift - 1.0) * shrink
        w_new = base_w * max(0.5, adj)  # 가중치는 기본값 대비 0.5x~ 조정 (클램프 별도)
        w_new = max(W_CLAMP[0], min(W_CLAMP[1], w_new))
        if abs(w_new - old.get(sig, base_w)) > 0.05:
            changes.append({
                "sig": sig, "name": SIGNAL_NAMES.get(sig, sig),
                "n": st["n"], "hit": st["hit"], "rate": sig_rate,
                "lift": round(lift, 2), "old": round(old.get(sig, base_w), 2),
                "new": round(w_new, 2),
            })
        new[sig] = round(w_new, 2)
    return new, changes

def compute_adaptive_threshold(stats, weights_data):
    """점수대별 적중률 기반 '목표 적중률 달성 최소 점수' 계산 → 이동평균 스무딩.
    학습이 성숙해지면 후보 수를 줄여 적중률을 높이는 핵심 로직.
    - 누적(점수 이상) 적중률이 TARGET_HIT_RATE 이상이 되는 최소 점수 = new_th
    - 스무딩: new = 0.6*old + 0.4*new_th (급변 방지 — '조금씩' 줄여가기)
    - 상한 TH_MAX (후보 0 방지), 샘플 부족 시 기존 값 유지
    Returns (threshold, meta)."""
    scores = stats["scores"]
    total = stats["total"]
    if not scores or total < 20:  # 샘플 20 미만이면 임계값 조정 안 함
        old = weights_data.get("adaptive_threshold", DEFAULT_THRESHOLD)
        return old, {"n": total, "changed": False, "reason": "샘플 부족"}

    # 점수 이상 누적 적중률 계산
    cum = {}
    for sc in sorted(scores.keys(), reverse=True):
        above_n = sum(scores[s]["n"] for s in scores if s >= sc)
        above_hit = sum(scores[s]["hit"] for s in scores if s >= sc)
        if above_n >= 5:  # 최소 5샘플
            cum[sc] = above_hit / above_n

    # 목표 적중률 미달인 최고 점수 찾기 → 그 위로 임계값 상향 (후보 좁히기)
    # 예: 6점 이상이 28%(미달), 7점 이상이 60%(달성) → 임계값 7
    # 원칙: 후보를 줄여가는 방향(단조 증가)만 허용 — 내리면 적중률을 버림
    below = [sc for sc in sorted(cum.keys(), reverse=True) if cum[sc] < TARGET_HIT_RATE]
    highest = max(cum.keys())
    old = weights_data.get("adaptive_threshold", DEFAULT_THRESHOLD)
    if below and cum[highest] >= TARGET_HIT_RATE:
        new_th = max(below) + 1
        new_th = max(DEFAULT_THRESHOLD, min(TH_MAX, new_th))
    else:
        # 전부 목표 이상 (충분히 좋음) 또는 전부 미달 (상향 무의미) → 현재 값 유지
        new_th = old

    # 스무딩: 조금씩 움직이기 (한 번에 0.5점 이상 안 변함) + 단조 증가 보장
    smoothed = round(0.6 * old + 0.4 * new_th, 1)
    smoothed = max(old, smoothed)  # 내려가지 않음
    changed = abs(smoothed - old) > 0.05
    return smoothed, {"n": total, "changed": changed, "new_raw": new_th, "cum": cum}

def render_report(stats, changes, weights_data, new_weights, th_meta):
    today = datetime.date.today().strftime("%Y-%m-%d")
    lines = [f"🧠 **급등주예측기 학습 리포트** ({today})", "━━━━━━━━━━━━━━━━━━━━━━━"]

    # 1) 누적 성과
    t, h = stats["total"], stats["hits"]
    br = stats["base_rate"] * 100
    lines.append(f"\n**📊 누적 성과** — {t}회 지목, {h}회 적중 (**{br:.0f}%**)")
    for c, st in sorted(stats["cats"].items(), key=lambda x: -x[1]["n"]):
        r = st["hit"] / st["n"] * 100 if st["n"] else 0
        lines.append(f"> {c}: {st['hit']}/{st['n']} ({r:.0f}%)")

    # 2) 신호별 예측력
    lines.append("\n**🔬 신호별 예측력** (샘플/적중/적중률 vs 전체)")
    sig_rows = []
    for sig, st in stats["signals"].items():
        r = st["hit"] / st["n"] * 100 if st["n"] else 0
        lift = r / br if br else 1.0  # 배수 (1.0 = 전체 평균)
        sig_rows.append((SIGNAL_NAMES.get(sig, sig), st["n"], st["hit"], r, lift, sig))
    sig_rows.sort(key=lambda x: -x[3])
    for name, n, hit, r, lift, sig in sig_rows:
        mark = "🔥" if lift >= 1.3 else "⬇️" if lift <= 0.7 else "•"
        lines.append(f"{mark} {name:<7} {n:>3}개/{hit:>3} ({r:>3.0f}%) vs 전체 {br:.0f}%  (x{lift:.1f})")

    # 3) 점수대별
    if stats["scores"]:
        lines.append("\n**🎯 점수대별 적중률**")
        for sc in sorted(stats["scores"], reverse=True):
            st = stats["scores"][sc]
            r = st["hit"] / st["n"] * 100 if st["n"] else 0
            lines.append(f"> {sc}점: {st['hit']}/{st['n']} ({r:.0f}%)")

    # 3-1) 적응형 임계값 (후보 수 조절)
    th = weights_data.get("adaptive_threshold", DEFAULT_THRESHOLD)
    lines.append("\n**🎯 후보 필터링 임계값**")
    if th_meta.get("changed"):
        arrow = "▲ (후보 줄임)" if th > DEFAULT_THRESHOLD else "▼"
        lines.append(f"> 발사대기 최소점: {DEFAULT_THRESHOLD} → **{th}점** {arrow}")
        lines.append(f"> 목표 적중률 {TARGET_HIT_RATE*100:.0f}% 달성 위해 후보를 조금씩 좁히는 중")
    else:
        reason = th_meta.get("reason", "")
        if reason == "샘플 부족":
            lines.append(f"> 현재 **{th}점** (샘플 {th_meta.get('n', 0)}건 — 20건 쌓이면 자동 조정 시작)")
        else:
            lines.append(f"> 현재 **{th}점** 유지 (학습이 목표 적중률에 도달하면 상향)")

    # 4) 가중치 변경
    if changes:
        lines.append("\n**⚙️ 가중치 업데이트** (다음 스캔부터 적용)")
        for c in sorted(changes, key=lambda x: -abs(x["new"] - x["old"])):
            arrow = "▲" if c["new"] > c["old"] else "▼"
            lines.append(f"> {c['name']}: {c['old']} → {c['new']} {arrow} (lift {c['lift']:.0f}%, n={c['n']})")
    else:
        lines.append("\n**⚙️ 가중치 변경 없음** — 샘플 부족(각 신호 4+ 필요) 또는 변화 미미")

    lines.append("")
    lines.append("— 학습: 맞춘/못맞춘 outcomes 기반 신호별 lift 반영. 4샘플 미만은 보수적으로 유지.")
    return "\n".join(lines)

def main():
    state = load_state()
    outcomes = state.get("outcomes", {})
    if not outcomes:
        print("🧠 학습 리포트 — 아직 outcomes 데이터가 없어. regsho 지목 + 다음날 surge 확인이 쌓여야 학습 시작해.")
        return

    weights_data = load_weights()
    stats = analyze(outcomes)
    new_weights, changes = compute_new_weights(stats, weights_data)
    new_th, th_meta = compute_adaptive_threshold(stats, weights_data)

    # 갱신 이력 기록
    weights_data["weights"] = new_weights
    weights_data["adaptive_threshold"] = new_th
    weights_data["updated"] = datetime.date.today().strftime("%Y-%m-%d")
    hist = weights_data.setdefault("history", [])
    hist.append({
        "date": weights_data["updated"],
        "total": stats["total"], "hits": stats["hits"],
        "base_rate": round(stats["base_rate"], 3),
        "n_changes": len(changes),
        "threshold": new_th,
    })
    hist = hist[-30:]
    weights_data["history"] = hist
    save_weights(weights_data)

    report = render_report(stats, changes, weights_data, new_weights, th_meta)
    print(report)
    print(f"\n(outcomes {stats['total']}건 / weights saved {len(changes)} changes / threshold {new_th})", file=sys.stderr)

if __name__ == "__main__":
    main()