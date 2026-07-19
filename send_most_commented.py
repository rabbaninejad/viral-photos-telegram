import json
import os
import sys
import time
from pathlib import Path

import requests

SENT_IDS_FILE = Path(__file__).parent / "sent_reddit_ids.json"
MAX_HISTORY = 5000
POSTS_PER_RUN = 5
SEND_DELAY_SECONDS = 1.0

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SUBREDDITS = [
    "EarthPorn",
    "awesomenature",
    "hiking",
    "CampingandHiking",
    "Waterfalls",
    "NatureIsFuckingLit",
    "MostBeautiful",
    "outdoors",
    "Mountains",
]

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif")
HEADERS = {"User-Agent": "nature-comment-bot/1.0"}


def load_sent_ids() -> set:
    if SENT_IDS_FILE.exists():
        return set(json.loads(SENT_IDS_FILE.read_text()))
    return set()


def save_sent_ids(ids: set) -> None:
    trimmed = list(ids)[-MAX_HISTORY:]
    SENT_IDS_FILE.write_text(json.dumps(trimmed))


def is_direct_image(url: str) -> bool:
    return url.lower().endswith(IMAGE_EXTENSIONS)


def translate_to_fa(text: str) -> str:
    if not text:
        return ""
    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "fa", "dt": "t", "q": text},
            timeout=10,
        )
        resp.raise_for_status()
        segments = resp.json()[0]
        return "".join(seg[0] for seg in segments if seg[0])
    except Exception as e:
        print(f"translation failed: {e}", file=sys.stderr)
        return ""


def fetch_top_of_day(subreddit: str):
    resp = requests.get(
        f"https://www.reddit.com/r/{subreddit}/top.json",
        params={"t": "day", "limit": 50},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    children = resp.json().get("data", {}).get("children", [])
    posts = []
    for child in children:
        d = child.get("data", {})
        url = d.get("url_overridden_by_dest") or d.get("url", "")
        if d.get("over_18") or not is_direct_image(url):
            continue
        posts.append(
            {
                "uid": f"reddit:{d.get('id')}",
                "url": url,
                "title": d.get("title", "بدون عنوان"),
                "subreddit": subreddit,
                "num_comments": d.get("num_comments", 0),
                "permalink": f"https://reddit.com{d.get('permalink', '')}",
            }
        )
    return posts


def send_to_telegram(post: dict) -> None:
    fa_title = translate_to_fa(post["title"])
    lines = [post["title"]]
    if fa_title:
        lines.append(f"🇮🇷 {fa_title}")
    lines.append(f"r/{post['subreddit']} | 💬 {post['num_comments']} کامنت")
    lines.append(post["permalink"])
    caption = "\n".join(lines)
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
        data={"chat_id": TELEGRAM_CHAT_ID, "photo": post["url"], "caption": caption},
        timeout=15,
    )
    resp.raise_for_status()


def main():
    sent_ids = load_sent_ids()
    seen_uids = set()
    all_posts = []

    for sub in SUBREDDITS:
        try:
            posts = fetch_top_of_day(sub)
        except requests.HTTPError as e:
            print(f"fetch failed for r/{sub}: {e}", file=sys.stderr)
            continue
        for p in posts:
            if p["uid"] in sent_ids or p["uid"] in seen_uids:
                continue
            seen_uids.add(p["uid"])
            all_posts.append(p)

    all_posts.sort(key=lambda p: p["num_comments"], reverse=True)

    sent_this_run = 0
    for p in all_posts:
        if sent_this_run >= POSTS_PER_RUN:
            break
        try:
            send_to_telegram(p)
        except requests.HTTPError as e:
            print(f"failed to send {p['uid']}: {e}", file=sys.stderr)
            continue
        sent_ids.add(p["uid"])
        sent_this_run += 1
        save_sent_ids(sent_ids)
        print(f"sent {p['uid']} ({sent_this_run}/{POSTS_PER_RUN}, {p['num_comments']} comments)")
        time.sleep(SEND_DELAY_SECONDS)

    if sent_this_run < POSTS_PER_RUN:
        print(
            f"only found {sent_this_run}/{POSTS_PER_RUN} unique most-commented posts this run",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
