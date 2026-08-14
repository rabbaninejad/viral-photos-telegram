import json
import os
import random
import sys
import time
from pathlib import Path

import requests

SENT_IDS_FILE = Path(__file__).parent / "sent_ids.json"
MAX_HISTORY = 8000  # how many past IDs to remember before trimming
PHOTOS_PER_RUN = 100
SEND_DELAY_SECONDS = 1.2  # spacing between sends to stay under Telegram's rate limit
MAX_CAPTION_LEN = 1000  # Telegram's hard cap is 1024 chars

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
UNSPLASH_ACCESS_KEY = os.environ["UNSPLASH_ACCESS_KEY"]

TOPIC_QUERIES = [
    # nature / adventure
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
    "glacier",
    "volcano",
    "jungle",
    "coastline cliffs",
    "autumn forest",
    "snow mountains",
    "cave",
    "safari animals",
    "tropical island",
    "northern lights",
    # technology
    "technology",
    "futuristic technology",
    "robotics",
    "space technology",
    "gadgets",
    # thrill / excitement
    "extreme sports",
    "adrenaline action sports",
    "skydiving",
    "base jumping",
    "motocross action",
    # newest vehicles / machinery / aircraft
    "latest supercar",
    "new fighter jet",
    "heavy machinery",
    "construction equipment",
    "concept car",
    "cargo airplane",
    "industrial engineering",
    # cars & motorcycles
    "sports car",
    "luxury car",
    "classic car",
    "sports motorcycle",
    "motorcycle stunt",
    "custom car",
    # funny / humor
    "funny photo",
    "comedy moment",
    "funny animal",
    "silly expression",
    # debate / reply-bait / instagram-worthy
    "optical illusion",
    "unbelievable moment photo",
    "mind blowing photo",
    "epic fail funny",
    "satisfying oddly",
    "controversial art",
    "before and after transformation",
    "rare unusual photo",
]


def load_sent_ids() -> set:
    if SENT_IDS_FILE.exists():
        return set(json.loads(SENT_IDS_FILE.read_text()))
    return set()


def save_sent_ids(ids: set) -> None:
    trimmed = list(ids)[-MAX_HISTORY:]
    SENT_IDS_FILE.write_text(json.dumps(trimmed))


def translate_to_fa(text: str) -> str:
    if not text:
        return ""
    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "en",
                "tl": "fa",
                "dt": "t",
                "q": text,
            },
            timeout=10,
        )
        resp.raise_for_status()
        segments = resp.json()[0]
        return "".join(seg[0] for seg in segments if seg[0])
    except Exception as e:
        print(f"translation failed: {e}", file=sys.stderr)
        return ""


def fetch_from_unsplash(query: str, page: int, count: int = 30):
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
                "credit": p["user"]["name"],
                "description": description,
                "location": location_name,
                "popularity": p.get("likes", 0),
            }
        )
    return candidates


def build_caption(c: dict) -> str:
    lines = [f"Unsplash | عکاس: {c['credit']}"]
    if c.get("description"):
        lines.append(f"🖼 {c['description']}")
        fa_desc = translate_to_fa(c["description"])
        if fa_desc:
            lines.append(f"🇮🇷 {fa_desc}")
    if c.get("location"):
        lines.append(f"📍 {c['location']}")
        fa_loc = translate_to_fa(c["location"])
        if fa_loc and fa_loc.strip().lower() != c["location"].strip().lower():
            lines.append(f"📍(فارسی) {fa_loc}")
    caption = "\n".join(lines)
    if len(caption) > MAX_CAPTION_LEN:
        caption = caption[: MAX_CAPTION_LEN - 1] + "…"
    return caption


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
        # Random pages so repeat runs don't keep re-fetching the same
        # deterministic "relevant" results that are already marked sent.
        pages = random.sample(range(1, 15), 3)
        for page in pages:
            try:
                batch = fetch_from_unsplash(query, page)
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
        if len(all_candidates) >= PHOTOS_PER_RUN * 2:
            break

    print(f"total unique candidates collected: {len(all_candidates)}")
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
