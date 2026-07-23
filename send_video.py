import json
import os
import random
import sys
import time
from pathlib import Path

import requests

SENT_IDS_FILE = Path(__file__).parent / "sent_video_ids.json"
MAX_HISTORY = 5000
VIDEOS_PER_RUN = 5
MIN_DURATION = 10
MAX_DURATION = 30
SEND_DELAY_SECONDS = 2.0
MAX_FILE_WIDTH = 1280  # keep file size reasonable for Telegram

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PEXELS_API_KEY = os.environ["PEXELS_API_KEY"]

TOPIC_QUERIES = [
    "extreme sports",
    "adventure travel",
    "mountain climbing",
    "canyon",
    "waterfall",
    "skydiving",
    "surfing",
    "motorcycle",
    "supercar",
    "wildlife action",
    "off road",
    "base jumping",
    "downhill mountain bike",
    "cliff jumping",
    "snowboarding",
]


def load_sent_ids() -> set:
    if SENT_IDS_FILE.exists():
        return set(json.loads(SENT_IDS_FILE.read_text()))
    return set()


def save_sent_ids(ids: set) -> None:
    trimmed = list(ids)[-MAX_HISTORY:]
    SENT_IDS_FILE.write_text(json.dumps(trimmed))


def pick_video_file(video_files: list):
    # Prefer the smallest file at or under MAX_FILE_WIDTH so it stays a
    # reasonable size to send; fall back to the smallest available.
    candidates = [f for f in video_files if f.get("width") and f["width"] <= MAX_FILE_WIDTH]
    pool = candidates or video_files
    if not pool:
        return None
    return min(pool, key=lambda f: f.get("width") or 999999)


def fetch_candidates(query: str, page: int):
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        params={"query": query, "per_page": 20, "page": page},
        headers={"Authorization": PEXELS_API_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    videos = resp.json().get("videos", [])
    candidates = []
    for v in videos:
        duration = v.get("duration", 0)
        if not (MIN_DURATION <= duration <= MAX_DURATION):
            continue
        vf = pick_video_file(v.get("video_files", []))
        if not vf:
            continue
        candidates.append(
            {
                "uid": f"pexels:{v['id']}",
                "url": vf["link"],
                "credit": v.get("user", {}).get("name", "Pexels"),
                "duration": duration,
            }
        )
    return candidates


def build_caption(c: dict) -> str:
    return f"🎬 {c['duration']}s | Pexels | فیلمبردار: {c['credit']}"


def send_video_to_telegram(c: dict) -> None:
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo",
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "video": c["url"],
            "caption": build_caption(c),
            "supports_streaming": True,
        },
        timeout=60,
    )
    resp.raise_for_status()


def main():
    sent_ids = load_sent_ids()
    queries = TOPIC_QUERIES[:]
    random.shuffle(queries)

    all_candidates = []
    seen_uids = set()
    for query in queries:
        pages = random.sample(range(1, 10), 2)
        for page in pages:
            try:
                batch = fetch_candidates(query, page)
            except requests.HTTPError as e:
                print(f"search failed for '{query}' page {page}: {e}", file=sys.stderr)
                continue
            new_count = 0
            for c in batch:
                if c["uid"] in sent_ids or c["uid"] in seen_uids:
                    continue
                seen_uids.add(c["uid"])
                all_candidates.append(c)
                new_count += 1
            print(f"'{query}' page {page}: {new_count} new / {len(batch)} fetched")
        if len(all_candidates) >= VIDEOS_PER_RUN * 3:
            break

    random.shuffle(all_candidates)
    print(f"total unique candidates collected: {len(all_candidates)}")

    sent_this_run = 0
    for c in all_candidates:
        if sent_this_run >= VIDEOS_PER_RUN:
            break
        try:
            send_video_to_telegram(c)
        except requests.HTTPError as e:
            print(f"failed to send {c['uid']}: {e}", file=sys.stderr)
            continue
        sent_ids.add(c["uid"])
        sent_this_run += 1
        save_sent_ids(sent_ids)
        print(f"sent {c['uid']} ({sent_this_run}/{VIDEOS_PER_RUN})")
        time.sleep(SEND_DELAY_SECONDS)

    if sent_this_run < VIDEOS_PER_RUN:
        print(
            f"only found {sent_this_run}/{VIDEOS_PER_RUN} unique clips this run",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
