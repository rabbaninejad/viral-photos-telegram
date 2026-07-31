import json
import os
import random
import sys
import time
from pathlib import Path

import requests

SENT_IDS_FILE = Path(__file__).parent / "sent_video_ids.json"
MAX_HISTORY = 4000
VIDEOS_PER_RUN = 6
SEND_DELAY_SECONDS = 2.0
MAX_CAPTION_LEN = 1000
MAX_DURATION_SECONDS = 30
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # Telegram's cap for sending by URL

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PEXELS_API_KEY = os.environ["PEXELS_API_KEY"]

TOPIC_QUERIES = [
    "canyoneering",
    "extreme sports action",
    "cliff jumping",
    "waterfall drone",
    "mountain hiking drone",
    "skydiving",
    "base jumping",
    "paragliding",
    "surfing big waves",
    "whitewater rafting",
    "rock climbing action",
    "motorcycle stunt",
    "supercar drifting",
    "off road driving",
    "fighter jet flying",
    "wildlife action",
    "lion hunting",
    "eagle flying",
    "avalanche",
    "lightning storm",
    "volcano eruption",
    "northern lights timelapse",
    "desert drone",
    "snowboarding powder",
    "freediving ocean",
    "drone forest flight",
    "fireworks night",
    "space rocket launch",
    "formula 1 racing",
    "wingsuit flying",
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
            params={"client": "gtx", "sl": "en", "tl": "fa", "dt": "t", "q": text},
            timeout=10,
        )
        resp.raise_for_status()
        segments = resp.json()[0]
        return "".join(seg[0] for seg in segments if seg[0])
    except Exception as e:
        print(f"translation failed: {e}", file=sys.stderr)
        return ""


def pick_best_file(video_files: list):
    """Pick the highest-quality file that should still fit Telegram's 20MB URL cap."""
    candidates = [f for f in video_files if f.get("file_type") == "video/mp4"]
    # sort by resolution descending, try largest first, fall back smaller
    candidates.sort(key=lambda f: (f.get("width") or 0) * (f.get("height") or 0), reverse=True)
    # prefer files capped at 1280 width to keep size reasonable, but keep full list as fallback
    preferred = [f for f in candidates if (f.get("width") or 0) <= 1280]
    ordered = preferred + [f for f in candidates if f not in preferred]
    for f in ordered:
        link = f.get("link")
        if not link:
            continue
        try:
            head = requests.head(link, timeout=10, allow_redirects=True)
            size = int(head.headers.get("Content-Length", 0))
        except Exception:
            size = 0
        if size and size > MAX_FILE_SIZE_BYTES:
            continue
        return f, size
    return None, 0


def fetch_from_pexels(query: str, page: int, count: int = 15):
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        params={
            "query": query,
            "per_page": count,
            "page": page,
            "size": "medium",
            "max_duration": MAX_DURATION_SECONDS,
        },
        headers={"Authorization": PEXELS_API_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    results = resp.json().get("videos", [])
    candidates = []
    for v in results:
        duration = v.get("duration", 9999)
        if duration > MAX_DURATION_SECONDS:
            continue
        candidates.append(
            {
                "uid": f"pexels:{v['id']}",
                "duration": duration,
                "credit": (v.get("user") or {}).get("name", "Pexels"),
                "video_files": v.get("video_files", []),
                "query": query,
            }
        )
    return candidates


def build_caption(c: dict) -> str:
    lines = [f"Pexels | فیلمبردار: {c['credit']}", f"⏱ {c['duration']} ثانیه"]
    fa_query = translate_to_fa(c["query"])
    if fa_query:
        lines.append(f"🎬 {fa_query}")
    caption = "\n".join(lines)
    if len(caption) > MAX_CAPTION_LEN:
        caption = caption[: MAX_CAPTION_LEN - 1] + "…"
    return caption


def send_to_telegram(video_url: str, caption: str, width: int, height: int, duration: int) -> None:
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAnimation",
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "animation": video_url,
            "caption": caption,
            "width": width,
            "height": height,
            "duration": duration,
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
        pages = random.sample(range(1, 6), 2)
        for page in pages:
            try:
                batch = fetch_from_pexels(query, page)
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
        if len(all_candidates) >= VIDEOS_PER_RUN * 4:
            break

    print(f"total unique candidates collected: {len(all_candidates)}")
    random.shuffle(all_candidates)

    sent_this_run = 0
    for c in all_candidates:
        if sent_this_run >= VIDEOS_PER_RUN:
            break
        best_file, size = pick_best_file(c["video_files"])
        if not best_file:
            continue
        try:
            send_to_telegram(
                best_file["link"],
                build_caption(c),
                best_file.get("width", 0),
                best_file.get("height", 0),
                c["duration"],
            )
        except requests.HTTPError as e:
            print(f"failed to send {c['uid']}: {e}", file=sys.stderr)
            continue
        sent_ids.add(c["uid"])
        sent_this_run += 1
        save_sent_ids(sent_ids)
        mb = size / 1024 / 1024 if size else 0
        print(f"sent {c['uid']} ({sent_this_run}/{VIDEOS_PER_RUN}) ~{mb:.1f}MB")
        time.sleep(SEND_DELAY_SECONDS)

    if sent_this_run < VIDEOS_PER_RUN:
        print(f"only found {sent_this_run}/{VIDEOS_PER_RUN} unique clips this run", file=sys.stderr)


if __name__ == "__main__":
    main()
