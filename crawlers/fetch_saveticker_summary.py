#!/usr/bin/python3
"""
SaveTicker 종합탭 '미국 증시 요약' 게시물을 감지하고 본문 + 이미지를 추출합니다.
출력: JSON (content + images array + url)
"""
import json
import os
import sys
import urllib.request
import re
import html as html_mod
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import saveticker_fetch as stf  # CF 우회 fetch 레이어 (2026-09-23): 직접 → 프록시/SSH → microlink

STATE_FILE = os.path.expanduser("~/.hermes/scripts/.saveticker_summary_state.json")
# 2026-09-23: 신 API는 기본 피드가 '전체'(15분에 100건)라 요약글이 안 잡힘 →
# label_group=2&label_name=1(큐레이션 피드, 100건 ≈ 29시간)로 조회해야 마감 리포트가 나온다.
API_URL = ("https://saveticker.com/api/news/list?page=1&page_size=100&sort=created_at_desc"
           "&label_group=2&label_name=1")


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_post_id": None, "sent_titles": []}


def fetch_json(url):
    """CF 우회 레이어 경유 (직접 → 프록시/SSH → microlink)."""
    return stf.fetch_json(url, {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    })


def download_image(img_url, post_id, idx):
    """Download image via CF-bypass layer. Returns local path or None.

    NOTE: microlink 폴백은 바이너리를 못 받아오므로, 이미지는
    SAVETICKER_SSH / SAVETICKER_PROXY 가 설정돼 있어야 실제로 받아진다.
    """
    try:
        if img_url.startswith("/"):
            img_url = "https://saveticker.com" + img_url
        elif img_url.startswith("//"):
            img_url = "https:" + img_url

        ext = ".png"
        low = img_url.split("?")[0].lower()
        for cand in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            if low.endswith(cand):
                ext = ".jpg" if cand == ".jpeg" else cand
                break

        data = stf.fetch_image(img_url)
        if not data:
            return None

        local_path = f"/tmp/saveticker_img_{post_id}_{idx}{ext}"
        with open(local_path, "wb") as f:
            f.write(data)

        if os.path.getsize(local_path) > 2000:
            return local_path
        return None
    except Exception as e:
        print(f"  [WARN] Image download failed for {img_url}: {e}", file=sys.stderr)
        return None


def fetch_article_full(post_id):
    """Fetch post HTML and extract text + images."""
    url = f"https://saveticker.com/news/{post_id}"
    html_text = stf.fetch_html(url, {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    # Extract text from <p> tags
    text_pattern = re.compile(
        r'<p[^>]*class="[^"]*whitespace-pre-wrap[^"]*"[^>]*>(.*?)</p>', re.DOTALL | re.IGNORECASE
    )
    text_matches = text_pattern.findall(html_text)
    clean_texts = []
    tag_re = re.compile(r"<[^>]+>")
    for m in text_matches:
        text = tag_re.sub("", m)
        text = html_mod.unescape(text)
        text = text.strip()
        if text:
            clean_texts.append(text)

    full_text = "\n\n".join(clean_texts) if clean_texts else ""

    # Extract images (skip logo/icon)
    img_pattern = re.compile(r'<img[^>]+src="([^"]+)"[^>]*>', re.DOTALL)
    img_matches = img_pattern.findall(html_text)
    local_images = []
    for idx, src in enumerate(img_matches):
        if "Logo" in src or "icon" in src or ".webp" in src:
            continue
        local_path = download_image(src, post_id, idx)
        if local_path:
            local_images.append(local_path)

    return {"text": full_text, "images": local_images, "url": url}


def process_weekly(weekly_posts, state):
    """Handle SAVE 주간 리포트 (weekly) — images only, no text body."""
    # Pick latest
    latest = max(weekly_posts, key=lambda x: x.get("created_at", ""))
    post_id = str(latest["id"])
    title = latest["title"]

    weekly_state_file = os.path.expanduser("~/.hermes/scripts/.saveticker_weekly_state.json")
    try:
        with open(weekly_state_file, "r", encoding="utf-8") as f:
            wstate = json.load(f)
    except Exception:
        wstate = {"last_post_id": None, "sent_titles": []}

    # Dedup
    if post_id == str(wstate.get("last_post_id")) or title in wstate.get("sent_titles", []):
        print(json.dumps({"type": "weekly", "status": "NO_NEW_POSTS", "message": "이미 전송한 주간 리포트입니다."}, ensure_ascii=False))
        return

    # Fetch article (images only for weekly)
    try:
        article = fetch_article_full(post_id)
    except Exception as e:
        print(json.dumps({"type": "weekly", "status": "ERROR", "message": f"Full article fetch failed: {e}"}, ensure_ascii=False))
        return

    # Output JSON
    output = {
        "type": "weekly",
        "status": "NEW_POST",
        "post_id": post_id,
        "title": title,
        "url": article["url"],
        "created_at": latest.get("created_at", ""),
        "content": article["text"],
        "images": article["images"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    # NOTE: state is saved by saveticker_mark_sent.py (same pattern as daily)
    # so a failed agent run can be retried.


def main():
    state = load_state()
    last_id = state.get("last_post_id")
    sent_titles = state.get("sent_titles", [])

    # TEST MODE
    test_post_id = os.environ.get("TEST_POST_ID")
    if test_post_id:
        try:
            article = fetch_article_full(test_post_id)
            output = {
                "status": "NEW_POST",
                "post_id": test_post_id,
                "title": f"[테스트] 미국 증시 요약 | Post {test_post_id}",
                "url": article["url"],
                "created_at": datetime.now().isoformat(),
                "author": "오선",
                "content": article["text"],
                "images": article["images"],
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return
        except Exception as e:
            print(json.dumps({"status": "ERROR", "message": f"TEST mode fetch failed: {e}"}, ensure_ascii=False))
            return

    # 1) Fetch latest posts
    try:
        data = fetch_json(API_URL)
    except Exception as e:
        print(json.dumps({"status": "ERROR", "message": f"API fetch failed: {e}"}, ensure_ascii=False))
        return

    news_list = data.get("news_list", [])

    # 2) Filter: daily (미국 증시 요약/SAVE Daily) + weekly (SAVE 주간 리포트)
    summary_posts = []
    weekly_posts = []
    for item in news_list:
        title = item.get("title", "")
        if "SAVE 주간 리포트" in title:
            weekly_posts.append(item)
        elif "미국 증시 요약" in title or "SAVE Daily" in title or "SAVE 마감 리포트" in title or "SAVE 마감리포트" in title:
            summary_posts.append(item)

    # Prefer daily if a NEW one exists, else fall back to weekly
    if summary_posts:
        pass  # process daily below
    elif weekly_posts:
        process_weekly(weekly_posts, state)
        return
    else:
        print(json.dumps({"status": "NO_NEW_POSTS", "message": "종합탭에서 미국 증시 요약/주간 리포트 글을 찾지 못했습니다."}, ensure_ascii=False))
        return

    # 3) Pick latest, preferring 원문 over 리포트
    # Group by date to find duplicates
    from collections import defaultdict
    by_date = defaultdict(list)
    for post in summary_posts:
        by_date[post.get("id")].append(post)
    
    # Sort by created_at desc
    summary_posts.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    # Pick the best: prefer 원문, fallback to 리포트
    chosen = None
    seen_dates = set()
    for post in summary_posts:
        title = post.get("title", "")
        # Prefer 원문 or 텍스트 variant, only pick 리포트 if no 원문 for that date
        is_original = "원문" in title or "텍스트" in title
        date_part = title.split("｜")[-1].strip() if "｜" in title else title
        date_key = date_part[:15]  # approximate date grouping
        
        if is_original:
            chosen = post
            break
        elif chosen is None:
            chosen = post
    
    latest = chosen if chosen else (summary_posts[0] if summary_posts else None)
    
    if not latest:
        print(json.dumps({"status": "NO_NEW_POSTS", "message": "종합탭에서 미국 증시 요약 글을 찾지 못했습니다."}, ensure_ascii=False))
        return
    
    post_id = str(latest["id"])
    title = latest["title"]

    # 4) Dedup
    if post_id == str(last_id) or title in sent_titles:
        # Daily already sent — fall back to weekly if a new one exists
        if weekly_posts:
            process_weekly(weekly_posts, state)
            return
        print(json.dumps({"status": "NO_NEW_POSTS", "message": "이미 전송한 게시물입니다."}, ensure_ascii=False))
        return

    # 5) Fetch full article (text + images)
    try:
        article = fetch_article_full(post_id)
    except Exception as e:
        print(json.dumps({"status": "ERROR", "message": f"Full article fetch failed: {e}"}, ensure_ascii=False))
        return

    # 6) Output JSON
    output = {
        "type": "daily",
        "status": "NEW_POST",
        "post_id": post_id,
        "title": title,
        "url": article["url"],
        "created_at": latest.get("created_at", ""),
        "author": latest.get("author_name", ""),
        "content": article["text"],
        "images": article["images"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    # NOTE: state is NOT saved here anymore. The cron agent runs
    # saveticker_mark_sent.py AFTER producing the summary, so that a failed
    # agent run (e.g. Broken pipe) leaves state untouched and the RETRY job
    # can still re-send the same post. Saving here made retries impossible.


if __name__ == "__main__":
    main()