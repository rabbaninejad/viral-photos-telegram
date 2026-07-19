import json
import os
import random
import sys
import time
from pathlib import Path

import requests

SENT_IDS_FILE = Path(__file__).parent / "sent_ids.json"
MAX_HISTORY = 5000  # how many past IDs to remember before trimming
PHOTOS_PER_RUN = 100
SEND_DELAY_SECONDS = 1.2  # spacing between sends to stay under Telegram's rate limit

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")  # optional, add later

TOPIC_QUERIES = [
    "nature",
    "mountains",
    "canyon",
    "hiking",
    "waterfall",
    "wildlife",
    "forest",
    "desert landscape",
    "adventure travel",
    "rock climbing",
    "river",
    "sunset landscape",
    "lake",
    "wilderness",
    "national park",
]


def load_sent_ids() -> set:
    if SENT_IDS_FILE.exists():
        return set(json.loads(SENT_IDS_FILE.read_text()))
    return set()


def save_sent_ids(ids: set) -> None:
    trimmed = list(ids)[-MAX_HISTORY:]
    SENT_IDS_FILE.write_text(json.dumps(trimmed))


def fetch_from_unsplash(query: str, page: int, count: int = 30):
    """Returns a list of candidate dicts sorted by nothing yet (caller sorts by popularity)."""
    resp = requests.get(
        "https://api.unsplash.com/search/photos",
        params={"query": query, "per_page": count, "page": page, "order_by": "relevant"},
        headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    candidates = []
    for p in results:
        loc = p.get("location") or {}
        location_name = loc.get("name") or ", ".join(
            filter(None, [loc.get("city"), loc.get("country")])
        )
        description = p.get("description") or p.get("alt_description")
        candidates.append(
            {
                "uid": f"unsplash:{p['id']}",
                "url": p["urls"]["regular"],
                "source_label": "Unsplash",
                "credit": p["user"]["name"],
                "description": description,
                "location": location_name,
                "popularity": p.get("likes", 0),
            }
        )
    return candidates


def fetch_from_pexels(query: str, page: int, count: int = 30):
    if not PEXELS_API_KEY:
        return []
    resp = requests.get(
        "https://api.pexels.com/v1/search",
        params={"query": query, "per_page": count, "page": page},
        headers={"Authorization": PEXELS_API_KEY},
        timeout=15,
    )
    resp.raise_for_status()
    photos = resp.json().get("photos", [])
    candidates = []
    for p in photos:
        candidates.append(
            {
                "uid": f"pexels:{p['id']}",
                "url": p["src"]["large"],
                "source_label": "Pexels",
                "credit": p["photographer"],
                "description": p.get("alt"),
                "location": None,
                "popularity": 0,  # Pexels API doesn't expose view/like counts
            }
        )
    return candidates


def build_caption(c: dict) -> str:
    lines = [f"{c['source_label']} | عکاس: {c['credit']}"]
    if c.get("description"):
        lines.append(f"🖼 {c['description']}")
    if c.get("location"):
        lines.append(f"📍 {c['location']}")
    return "\n".join(lines)


def send_to_telegram(image_url: str, caption: str) -> None:
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": image_url,
            "caption": caption,
        },
        timeout=15,
    )
    resp.raise_for_status()


def main():
    sent_ids = load_sent_ids()

    queries = TOPIC_QUERIES[:]
    random.shuffle(queries)

    all_candidates = []
    seen_uids = set()
    for query in queries:
        for page in (1, 2):
            for fetch_fn in (fetch_from_unsplash, fetch_from_pexels):
                try:
                    batch = fetch_fn(query, page)
                except requests.HTTPError as e:
                    print(f"search failed for '{query}' page {page}: {e}", file=sys.stderr)
                    continue
                for c in batch:
                    if c["uid"] in sent_ids or c["uid"] in seen_uids:
                        continue
                    seen_uids.add(c["uid"])
                    all_candidates.append(c)
        if len(all_candidates) >= PHOTOS_PER_RUN * 3:
            break

    # Send the most popular (highest-liked) unsent photos first
    all_candidates.sort(key=lambda c: c["popularity"], reverse=True)

    sent_this_run = 0
    for c in all_candidates:
        if sent_this_run >= PHOTOS_PER_RUN:
            break
        try:
            send_to_telegram(c["url"], build_caption(c))
        except requests.HTTPError as e:
            print(f"failed to send {c['uid']}: {e}", file=sys.stderr)
            continue
        sent_ids.add(c["uid"])
        sent_this_run += 1
        save_sent_ids(sent_ids)
        print(f"sent {c['uid']} ({sent_this_run}/{PHOTOS_PER_RUN})")
        time.sleep(SEND_DELAY_SECONDS)

    if sent_this_run < PHOTOS_PER_RUN:
        print(
            f"only found {sent_this_run}/{PHOTOS_PER_RUN} unique photos this run",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
