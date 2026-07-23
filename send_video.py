import json
import os
import random
import sys
import time
from pathlib import Path

import requests

SENT_IDS_FILE = Path(__file__).parent / "sent_video_ids.json"
MAX_HISTORY = 10000
VIDEOS_PER_RUN = 100
MIN_DURATION = 10
MAX_DURATION = 30
SEND_DELAY_SECONDS = 1.5
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
    # Only real video files (no gif/image previews), only story-shaped
    # (portrait, height > width) variants, smallest that's still under
    # MAX_FILE_WIDTH to keep upload size reasonable.
    real_videos = [f for f in video_files if (f.get("file_type") or "").startswith("video/")]
    real_videos = [f for f in real_videos if not f.get("link", "").lower().endswith(".gif")]
    portrait_videos = [
        f for f in real_videos
        if f.get("width") and f.get("height") and f["height"] > f["width"]
    ]
    if not portrait_videos:
        return None
    candidates = [f for f in portrait_videos if f.get("width") and f["width"] <= MAX_FILE_WIDTH]
    pool = candidates or portrait_videos
    return min(pool, key=lambda f: f.get("width") or 999999)


def extract_candidates(videos: list):
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


def fetch_popular(page: int):
    resp = requests.get(
        "https://api.pexels.com/videos/popular",
        params={"per_page": 80, "page": page, "orientation": "portrait"},
        headers={"Authorization": PEXELS_API_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    return extract_candidates(resp.json().get("videos", []))


def fetch_search(query: str, page: int):
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        params={"query": query, "per_page": 30, "page": page, "orientation": "portrait"},
        headers={"Authorization": PEXELS_API_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    return extract_candidates(resp.json().get("videos", []))


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
    all_candidates = []
    seen_uids = set()

    # Pull from Pexels' own "popular" (trending) feed first — several pages.
    for page in random.sample(range(1, 20), 6):
        try:
            batch = fetch_popular(page)
        except requests.HTTPError as e:
            print(f"popular fetch failed page {page}: {e}", file=sys.stderr)
            continue
        new_count = 0
        for c in batch:
            if c["uid"] in sent_ids or c["uid"] in seen_uids:
                continue
            seen_uids.add(c["uid"])
            all_candidates.append(c)
            new_count += 1
        print(f"popular page {page}: {new_count} new / {len(batch)} fetched")

    # Then top up with topic searches for variety.
    queries = TOPIC_QUERIES[:]
    random.shuffle(queries)
    for query in queries:
        if len(all_candidates) >= VIDEOS_PER_RUN * 2:
            break
        for page in random.sample(range(1, 10), 2):
            try:
                batch = fetch_search(query, page)
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

    print(f"total unique candidates collected: {len(all_candidates)}")
    random.shuffle(all_candidates)

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
