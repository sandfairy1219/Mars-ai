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

STATE_FILE = os.path.expanduser("~/.hermes/scripts/.saveticker_summary_state.json")
API_URL = "https://saveticker.com/api/news/list?page=1&page_size=100&sort=created_at_desc"


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_post_id": None, "sent_titles": []}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read().decode("utf-8"))


def download_image(img_url, post_id, idx):
    """Download image from saveticker media server to /tmp. Returns local path or None."""
    try:
        if img_url.startswith("/"):
            img_url = "https://saveticker.com" + img_url

        # HEAD request to get final URL + content-type
        req = urllib.request.Request(
            img_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://saveticker.com/",
            },
            method="HEAD",
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            final_url = res.geturl()
            ct = res.headers.get("Content-Type", "")

        if not final_url:
            return None

        # Extension
        if "png" in ct:
            ext = ".png"
        elif "jpeg" in ct or "jpg" in ct:
            ext = ".jpg"
        elif "gif" in ct:
            ext = ".gif"
        elif "webp" in ct:
            ext = ".webp"
        else:
            ext = ".png"

        local_path = f"/tmp/saveticker_img_{post_id}_{idx}{ext}"

        dl_req = urllib.request.Request(
            final_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://saveticker.com/",
            },
        )
        with urllib.request.urlopen(dl_req, timeout=15) as src:
            with open(local_path, "wb") as f:
                f.write(src.read())

        if os.path.getsize(local_path) > 2000:
            return local_path
        return None
    except Exception as e:
        print(f"  [WARN] Image download failed for {img_url}: {e}", file=sys.stderr)
        return None


def fetch_article_full(post_id):
    """Fetch post HTML and extract text + images."""
    url = f"https://saveticker.com/news/{post_id}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        html_text = res.read().decode("utf-8")

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

    # 2) Filter: title contains 미국 증시 요약 or SAVE Daily
    summary_posts = []
    for item in news_list:
        title = item.get("title", "")
        if "미국 증시 요약" in title or "SAVE Daily" in title:
            # Prefer 원문 over 리포트 (if both exist, 원문 has text content)
            summary_posts.append(item)

    if not summary_posts:
        print(json.dumps({"status": "NO_NEW_POSTS", "message": "종합탭에서 미국 증시 요약 글을 찾지 못했습니다."}, ensure_ascii=False))
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
        # Prefer 원문, only pick 리포트 if no 원문 for that date
        is_original = "원문" in title
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

    # 7) Update state
    state["last_post_id"] = post_id
    sent_titles.append(title)
    if len(sent_titles) > 20:
        sent_titles = sent_titles[-20:]
    state["sent_titles"] = sent_titles
    save_state(state)


if __name__ == "__main__":
    main()
