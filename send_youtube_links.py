import json
import os
import random
import re
import sys
import time
from pathlib import Path

import requests

SENT_IDS_FILE = Path(__file__).parent / "sent_video_ids.json"
MAX_HISTORY = 5000
LINKS_PER_RUN = 5
MAX_DURATION_SECONDS = 20
SEND_DELAY_SECONDS = 1.0

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]

QUERIES = [
    "nature adventure shorts",
    "extreme nature clip",
    "wildlife amazing moment shorts",
    "canyoneering shorts",
    "mountain climbing extreme shorts",
    "waterfall jump shorts",
    "epic nature drone shorts",
    "wingsuit nature shorts",
    "ocean wave extreme shorts",
    "cave exploration shorts",
]

ISO8601_DURATION_RE = re.compile(
    r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?"
)


def parse_duration_seconds(iso_duration: str) -> int:
    m = ISO8601_DURATION_RE.match(iso_duration)
    if not m:
        return 9999
    h = int(m.group("hours") or 0)
    mnt = int(m.group("minutes") or 0)
    s = int(m.group("seconds") or 0)
    return h * 3600 + mnt * 60 + s


def load_sent_ids() -> set:
    if SENT_IDS_FILE.exists():
        return set(json.loads(SENT_IDS_FILE.read_text()))
    return set()


def save_sent_ids(ids: set) -> None:
    trimmed = list(ids)[-MAX_HISTORY:]
    SENT_IDS_FILE.write_text(json.dumps(trimmed))


def search_candidates(query: str):
    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "q": query,
            "type": "video",
            "videoDuration": "short",  # YouTube's own bucket: under 4 minutes
            "order": "viewCount",
            "maxResults": 25,
            "key": YOUTUBE_API_KEY,
        },
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return [item["id"]["videoId"] for item in items if "videoId" in item.get("id", {})]


def get_video_details(video_ids):
    if not video_ids:
        return []
    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(video_ids),
            "key": YOUTUBE_API_KEY,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def send_link_to_telegram(video_id: str, title: str, views: str) -> None:
    url = f"https://youtu.be/{video_id}"
    text = f"{title}\n\ud83d\udc41 {views} views\n{url}"
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
        timeout=15,
    )
    resp.raise_for_status()


def main():
    sent_ids = load_sent_ids()
    sent_this_run = 0

    queries = QUERIES[:]
    random.shuffle(queries)

    candidates = []
    for query in queries:
        if len(candidates) >= 100:
            break
        try:
            candidates.extend(search_candidates(query))
        except requests.HTTPError as e:
            print(f"search failed for '{query}': {e}", file=sys.stderr)

    seen = set()
    unique_candidates = []
    for vid in candidates:
        if vid not in seen:
            seen.add(vid)
            unique_candidates.append(vid)

    for i in range(0, len(unique_candidates), 50):
        if sent_this_run >= LINKS_PER_RUN:
            break
        batch = unique_candidates[i : i + 50]
        details = get_video_details(batch)
        details.sort(key=lambda v: int(v.get("statistics", {}).get("viewCount", 0)), reverse=True)

        for v in details:
            if sent_this_run >= LINKS_PER_RUN:
                break
            vid = v["id"]
            if vid in sent_ids:
                continue
            duration = parse_duration_seconds(v.get("contentDetails", {}).get("duration", "PT9999S"))
            if duration > MAX_DURATION_SECONDS:
                continue
            title = v.get("snippet", {}).get("title", "بدون عنوان")
            views = v.get("statistics", {}).get("viewCount", "?")
            try:
                send_link_to_telegram(vid, title, views)
            except requests.HTTPError as e:
                print(f"failed to send {vid}: {e}", file=sys.stderr)
                continue
            sent_ids.add(vid)
            sent_this_run += 1
            save_sent_ids(sent_ids)
            print(f"sent {vid} ({sent_this_run}/{LINKS_PER_RUN})")
            time.sleep(SEND_DELAY_SECONDS)

    if sent_this_run < LINKS_PER_RUN:
        print(
            f"only found {sent_this_run}/{LINKS_PER_RUN} unique sub-20s clips this run",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
