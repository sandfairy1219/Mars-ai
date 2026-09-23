#!/usr/bin/python3
"""
Mark a saveticker summary post as sent in the dedup state file.
Run by the cron agent AFTER producing the summary text (before final delivery),
so a failed agent run (Broken pipe / credits error) leaves the state untouched
and the RETRY job can still re-send the same post.

Usage: saveticker_mark_sent.py <post_id> <title>
"""
import json
import os
import sys

STATE_FILE = os.path.expanduser("~/.hermes/scripts/.saveticker_summary_state.json")


def main():
    if len(sys.argv) < 3:
        print("usage: saveticker_mark_sent.py <post_id> <title>", file=sys.stderr)
        sys.exit(1)
    post_id = sys.argv[1]
    title = sys.argv[2]

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {"last_post_id": None, "sent_titles": []}

    state["last_post_id"] = post_id
    sent = state.get("sent_titles", [])
    if title not in sent:
        sent.append(title)
    if len(sent) > 20:
        sent = sent[-20:]
    state["sent_titles"] = sent

    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"marked {post_id} as sent")


if __name__ == "__main__":
    main()
