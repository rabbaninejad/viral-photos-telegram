import json
import os
import random
import sys
from pathlib import Path

import requests

SENT_IDS_FILE = Path(__file__).parent / "sent_ids.json"
MAX_HISTORY = 5000  # how many past IDs to remember before trimming

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")  # optional, add later


def load_sent_ids() -> set:
    if SENT_IDS_FILE.exists():
        return set(json.loads(SENT_IDS_FILE.read_text()))
    return set()


def save_sent_ids(ids: set) -> None:
    trimmed = list(ids)[-MAX_HISTORY:]
    SENT_IDS_FILE.write_text(json.dumps(trimmed))


def fetch_from_unsplash():
    """Returns (unique_id, image_url, source_label, credit) or None."""
    resp = requests.get(
        "https://api.unsplash.com/photos/random",
        params={"count": 10},
        headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
        timeout=15,
    )
    resp.raise_for_status()
    photos = resp.json()
    random.shuffle(photos)
    for p in photos:
        yield (
            f"unsplash:{p['id']}",
            p["urls"]["regular"],
            "Unsplash",
            p["user"]["name"],
        )


def fetch_from_pexels():
    if not PEXELS_API_KEY:
        return
    resp = requests.get(
        "https://api.pexels.com/v1/curated",
        params={"per_page": 15, "page": random.randint(1, 50)},
        headers={"Authorization": PEXELS_API_KEY},
        timeout=15,
    )
    resp.raise_for_status()
    photos = resp.json().get("photos", [])
    random.shuffle(photos)
    for p in photos:
        yield (
            f"pexels:{p['id']}",
            p["src"]["large"],
            "Pexels",
            p["photographer"],
        )


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

    sources = [fetch_from_unsplash]
    if PEXELS_API_KEY:
        sources.append(fetch_from_pexels)
    random.shuffle(sources)

    for source_fn in sources:
        for uid, url, source_label, credit in source_fn():
            if uid in sent_ids:
                continue
            send_to_telegram(url, f"{source_label} | عکاس: {credit}")
            sent_ids.add(uid)
            save_sent_ids(sent_ids)
            print(f"sent {uid}")
            return

    print("no new unique photo found this run", file=sys.stderr)


if __name__ == "__main__":
    main()
