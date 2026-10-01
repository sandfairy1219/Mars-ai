#!/usr/bin/env python3
"""
GitHub Pages 동기화 — 모의투자 대시보드를 15분 주기로 gh-pages 브랜치에 배포.
- swing-portfolio.json + Finnhub 실시간 시세 → summary.json
- index.html (fetch 경로를 summary.json 상대경로로 수정) + .nojekyll
- 성공 시 조용함(출력 없음), 실패 시에만 stderr 출력 (watchdog 패턴)
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, "/home/ubuntu/marsAI/paper-trading")
from dashboard import build_payload  # noqa: E402

REPO_URL = "https://github.com/sandfairy1219/Mars-ai.git"
SRC_HTML = "/home/ubuntu/marsAI/paper-trading/index.html"


def main():
    payload = build_payload()
    payload["source_note"] = "VM(mars)에서 15분 주기로 생성 — 가격은 생성 시점 Finnhub 실시간"

    html = open(SRC_HTML, encoding="utf-8").read()
    html = html.replace("fetch('/api/summary')", "fetch('summary.json')")
    html = html.replace("자동 새로고침 60초", "데이터 15분 주기 갱신 (GitHub Pages)")

    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        def run(*cmd):
            r = subprocess.run(cmd, cwd=td, env=env, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"{' '.join(cmd[:3])}... failed: {r.stderr.strip()[:300]}")
            return r

        run("git", "init", "-b", "gh-pages")
        run("git", "remote", "add", "origin", REPO_URL)
        run("git", "config", "user.email", "mars-agent@users.noreply.github.com")
        run("git", "config", "user.name", "Mars Dashboard Bot")

        # wordle/ 등 대시보드 외 정적 자산 보존 — 원격 gh-pages에서 wordle 폴더만 골라 복사(force push 유실 방지)
        run("git", "fetch", "--depth", "1", "origin", "gh-pages")
        r = subprocess.run(["git", "ls-tree", "origin/gh-pages", "--name-only"], cwd=td, capture_output=True, text=True)
        if "wordle" in r.stdout.split():
            run("git", "checkout", "origin/gh-pages", "--", "wordle")

        with open(os.path.join(td, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        with open(os.path.join(td, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)
        open(os.path.join(td, ".nojekyll"), "w").close()

        run("git", "add", "-A")
        run("git", "commit", "-m", f"dashboard data {payload['updated_at'][:16]} (auto)")
        run("git", "push", "-f", "origin", "gh-pages")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[pages-sync] FAIL: {e}", file=sys.stderr)
        sys.exit(1)
