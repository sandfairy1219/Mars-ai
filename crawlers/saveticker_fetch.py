#!/usr/bin/python3
"""SaveTicker CF 우회 fetch 레이어 (2026-09-23)

saveticker.com 은 2026-09-15경부터 Cloudflare **대화형 Turnstile managed challenge**를
걸어 서버(데이터센터 IP)에서의 직접 요청을 403으로 막는다.
  - 실측 실패: urllib/requests, cloudscraper 1.2.71, Playwright headless,
    실제 Chrome 154 + xvfb headed, patchright(스텔스) — 전부 cf_clearance 발급 실패
  - 통과 성공: 외부 브라우저 렌더링 서비스(microlink) — 로그인 불필요, JSON/HTML 원문 확보

따라서 fetch 경로 우선순위:
  1) SAVETICKER_SSH  = "user@host"      → 주거용 IP PC에서 ssh + curl.exe 로 대신 받아옴 (전체 기능: 텍스트+이미지)
  2) SAVETICKER_PROXY= "http://host:port" → HTTP 프록시 경유 (전체 기능)
  3) 직접 요청                            → 차단 안 됐을 때만 (현재는 항상 403)
  4) microlink 폴백                       → 텍스트/HTML만 가능, 이미지 불가 (무료 25회/24h)

이미지가 필요하면 1) 또는 2)를 설정해야 한다. 4)만 있으면 텍스트 요약만 나간다.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
MICROLINK = "https://api.microlink.io/"
MICROLINK_LIMIT = 25          # 무료 플랜 실측 한도 (x-rate-limit-limit 헤더, 24h 창)
_QUOTA_FILE = os.path.expanduser("~/.hermes/scripts/.saveticker_quota.json")


def _quota_used() -> int:
    """최근 24h microlink 호출 수."""
    try:
        with open(_QUOTA_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - float(d.get("window_start", 0)) > 86400:
            return 0
        return int(d.get("used", 0))
    except Exception:
        return 0


def _quota_add(n: int = 1, provider: str = "microlink") -> None:
    now = time.time()
    try:
        with open(_QUOTA_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    if now - float(d.get("window_start", 0)) > 86400:
        d = {"window_start": now, "used": 0, "providers": {}}
    d["used"] = int(d.get("used", 0)) + (n if provider == "microlink" else 0)
    provs = d.setdefault("providers", {})
    provs[provider] = int(provs.get(provider, 0)) + n
    d.setdefault("window_start", now)
    try:
        with open(_QUOTA_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


# ---------- 외부 스크래핑 API (진짜 브라우저로 CF 챌린지 통과) ----------
_SCRAPERS_CONF = os.path.expanduser("~/.hermes/scripts/.saveticker_scrapers.json")


def _load_scrapers() -> dict:
    """{provider: key} — 설정 파일 또는 환경변수에서 읽는다."""
    try:
        with open(_SCRAPERS_CONF, encoding="utf-8") as f:
            conf = json.load(f) or {}
    except Exception:
        conf = {}
    for prov, env in (("scrapingant", "SAVETICKER_SCRAPINGANT_KEY"),
                      ("scrapedo", "SAVETICKER_SCRAPEDO_KEY"),
                      ("scrapingbee", "SAVETICKER_SCRAPINGBEE_KEY")):
        key = os.environ.get(env, "").strip() or str(conf.get(prov) or "").strip()
        if key:
            conf[prov] = key
    return conf


def _via_scraper(url: str, kind: str = "text", timeout: int = 120,
                 browser: bool = True) -> bytes:
    """설정된 외부 스크래핑 API를 순서대로 시도."""
    conf = _load_scrapers()
    errs = []
    render = "true" if (kind == "html" or browser) else "false"   # 기사 HTML은 렌더링 필요할 수 있음

    if conf.get("scrapingant"):
        try:
            q = urllib.parse.urlencode({"url": url, "x-api-key": conf["scrapingant"],
                                        "browser": "true" if browser else "false"})
            with urllib.request.urlopen(f"https://api.scrapingant.com/v2/general?{q}", timeout=timeout) as res:
                body = res.read()
            _quota_add(provider="scrapingant")
            if body and not _looks_blocked(body):
                return body
            errs.append("scrapingant:blocked")
        except Exception as e:
            errs.append(f"scrapingant:{type(e).__name__}")

    if conf.get("scrapedo"):
        try:
            q = urllib.parse.urlencode({"token": conf["scrapedo"], "url": url, "render": render})
            with urllib.request.urlopen(f"https://api.scrape.do/?{q}", timeout=timeout) as res:
                body = res.read()
            _quota_add(provider="scrapedo")
            if body and not _looks_blocked(body):
                return body
            errs.append("scrapedo:blocked")
        except Exception as e:
            errs.append(f"scrapedo:{type(e).__name__}")

    if conf.get("scrapingbee"):
        try:
            q = urllib.parse.urlencode({"api_key": conf["scrapingbee"], "url": url,
                                        "render_js": render})
            with urllib.request.urlopen(f"https://app.scrapingbee.com/api/v1/?{q}", timeout=timeout) as res:
                body = res.read()
            _quota_add(provider="scrapingbee")
            if body and not _looks_blocked(body):
                return body
            errs.append("scrapingbee:blocked")
        except Exception as e:
            errs.append(f"scrapingbee:{type(e).__name__}")

    raise RuntimeError("scrapers: " + (", ".join(errs) if errs else "none configured"))


SSH_TARGET = os.environ.get("SAVETICKER_SSH", "").strip()
PROXY = os.environ.get("SAVETICKER_PROXY", "").strip()

# 환경변수가 없으면 릴레이 설정 파일을 읽는다 (Node 봇·크론 공용)
_RELAY_CONF = os.path.expanduser("~/.hermes/scripts/.saveticker_relay.json")
if not SSH_TARGET and os.path.exists(_RELAY_CONF):
    try:
        with open(_RELAY_CONF, encoding="utf-8") as _f:
            _conf = json.load(_f)
        if _conf.get("enabled", True):
            SSH_TARGET = (_conf.get("ssh") or "").strip()
    except Exception:
        pass


def _looks_blocked(body: bytes) -> bool:
    head = body[:800]
    low = head.lower()
    try:
        text = head.decode("utf-8", "ignore")
    except Exception:
        text = ""
    return (b"just a moment" in low or b"__cf_chl" in low
            or b"performing security verification" in low or "보안 확인" in text)


def _direct(url: str, headers: dict, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    if PROXY:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    else:
        opener = urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as res:
        return res.read()


def _via_ssh(url: str, timeout: int) -> bytes:
    """주거용 IP PC(Windows)의 내장 curl.exe 로 대신 받아온다."""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "StrictHostKeyChecking=accept-new", SSH_TARGET,
           "curl.exe", "-s", "-L", "--max-time", "25", "-A", UA, url]
    out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"ssh fetch rc={out.returncode} stderr={out.stderr[:150]!r}")
    return out.stdout


def _via_microlink(url: str, kind: str = "text", timeout: int = 60) -> bytes:
    # 무료 한도(25회/24h) 원장 — 요약 크론 몫을 남기고 속보봇이 초과하지 않게 한다.
    # SAVETICKER_QUOTA_RESERVE=6 이면 19회까지만 사용(요약용 6회 보존).
    reserve = int(os.environ.get("SAVETICKER_QUOTA_RESERVE", "0") or 0)
    used = _quota_used()
    if used + 1 > MICROLINK_LIMIT - reserve:
        raise RuntimeError(f"microlink quota guard: used={used} limit={MICROLINK_LIMIT} reserve={reserve}")

    params = {"url": url, "meta": "false"}
    if kind == "html":
        params["data.html.selector"] = "body"
        params["data.html.type"] = "html"
    else:
        params["data.raw.selector"] = "body"
        params["data.raw.type"] = "text"
    with urllib.request.urlopen(f"{MICROLINK}?{urllib.parse.urlencode(params)}", timeout=timeout) as res:
        payload = json.loads(res.read().decode("utf-8"))
    _quota_add()
    data = payload.get("data") or {}
    val = data.get("html" if kind == "html" else "raw") or ""
    if kind == "text":
        # Chrome JSON 뷰어가 남기는 <pre> 래퍼 + 뒤따르는 HTML 잔여물 제거
        val = val.strip()
        if val.startswith("<pre>"):
            val = val[5:]
        cut = val.find("</pre>")
        if cut != -1:
            val = val[:cut]
        val = val.strip()
    return val.encode("utf-8")


def fetch_bytes(url: str, headers: dict | None = None, timeout: int = 25,
                kind: str = "text", binary: bool = False) -> bytes:
    """경로 우선순위대로 시도해 raw bytes 반환. 전부 실패하면 예외."""
    headers = headers or {"User-Agent": UA, "Accept": "*/*"}
    errors = []

    if SSH_TARGET:
        try:
            body = _via_ssh(url, timeout)
            if body and not _looks_blocked(body):
                return body
            errors.append("ssh:blocked")
        except Exception as e:
            errors.append(f"ssh:{type(e).__name__}")

    try:
        body = _direct(url, headers, timeout)
        if not _looks_blocked(body):
            return body
        errors.append("direct:cloudflare")
    except Exception as e:
        errors.append(f"direct:{type(e).__name__}")

    # 외부 스크래핑 API (설정돼 있으면 microlink보다 우선 — 진짜 브라우저로 통과)
    if _load_scrapers():
        try:
            body = _via_scraper(url, kind, timeout=max(timeout, 120), browser=not binary)
            if body and not _looks_blocked(body):
                return body
            errors.append("scraper:blocked")
        except Exception as e:
            errors.append(f"scraper:{type(e).__name__}")

    if not binary:
        for k in (kind, "text" if kind != "text" else "html"):
            try:
                body = _via_microlink(url, k)
                if body and not _looks_blocked(body):
                    return body
                errors.append(f"microlink({k}):blocked")
            except Exception as e:
                errors.append(f"microlink({k}):{type(e).__name__}")

    raise RuntimeError("fetch failed: " + ", ".join(errors))


def fetch_json(url: str, headers: dict | None = None, timeout: int = 25):
    return json.loads(fetch_bytes(url, headers, timeout, kind="text").decode("utf-8"))


def fetch_text(url: str, headers: dict | None = None, timeout: int = 25) -> str:
    return fetch_bytes(url, headers, timeout, kind="text").decode("utf-8", "replace")


def fetch_html(url: str, headers: dict | None = None, timeout: int = 40) -> str:
    """기사 HTML. microlink 폴백은 body HTML을 반환."""
    return fetch_bytes(url, headers, timeout, kind="html").decode("utf-8", "replace")


def fetch_image(url: str, timeout: int = 25) -> bytes | None:
    """이미지(바이너리) 가져오기.

    1순위 SSH 릴레이/프록시(원본 그대로) → 실패하면
    2순위 microlink 스크린샷(무료): 원본 파일 대신 그 이미지를 렌더한 PNG를 받는다.
    (2026-09-23 실측: saveticker 이미지 URL도 microlink 스크린샷으로 정상 확보됨)
    """
    try:
        data = fetch_bytes(url, {"User-Agent": UA, "Referer": "https://saveticker.com/"},
                           timeout, binary=True)
        if data and len(data) > 2000:
            return data
    except Exception:
        pass

    try:
        q = urllib.parse.urlencode({
            "url": url, "meta": "false", "screenshot": "true",
            "screenshot.type": "png", "waitUntil": "load",
        })
        with urllib.request.urlopen(f"{MICROLINK}?{q}", timeout=90) as res:
            payload = json.loads(res.read().decode("utf-8"))
        _quota_add()
        shot = ((payload.get("data") or {}).get("screenshot") or {}).get("url")
        if shot:
            with urllib.request.urlopen(shot, timeout=45) as res:
                return res.read()
    except Exception:
        pass
    return None


if __name__ == "__main__":
    # CLI 모드: 다른 언어(Node 등)에서 호출해 본문을 stdout으로 받아간다.
    #   python3 saveticker_fetch.py "<url>" [--kind text|html]
    import argparse
    ap = argparse.ArgumentParser(description="SaveTicker CF 우회 fetch (stdout 출력)")
    ap.add_argument("url")
    ap.add_argument("--kind", default="text", choices=["text", "html"])
    args = ap.parse_args()
    try:
        sys.stdout.buffer.write(fetch_bytes(args.url, kind=args.kind))
    except Exception as exc:
        print(f"fetch failed: {exc}", file=sys.stderr)
        sys.exit(1)
