import json
import os
import random
import sys
import time
from pathlib import Path

import requests

SENT_IDS_FILE = Path(__file__).parent / "sent_video_ids.json"
MAX_HISTORY = 20000
VIDEOS_PER_RUN = 30
SEND_DELAY_SECONDS = 1.2
MAX_CAPTION_LEN = 1000
MAX_DURATION_SECONDS = 30
MAX_FILE_SIZE_BYTES = 4 * 1024 * 1024  # 4MB cap

TELEGRAM_BOT_TOKEN = os.environ["WOLF_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["WOLF_CHAT_ID"]
PEXELS_API_KEY = os.environ["PEXELS_API_KEY"]
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY")  # optional -- second source, adds itself once set

TOPIC_QUERIES = [
    "canyoneering",
    "cliff jumping",
    "waterfall drone",
    "mountain hiking drone",
    "skydiving nature",
    "base jumping cliff",
    "paragliding mountains",
    "surfing big waves",
    "whitewater rafting",
    "rock climbing action",
    "wildlife action",
    "lion hunting",
    "eagle flying",
    "avalanche",
    "lightning storm nature",
    "volcano eruption",
    "northern lights timelapse",
    "desert drone",
    "snowboarding powder",
    "freediving ocean",
    "drone forest flight",
    "wingsuit flying",
    "shark ocean",
    "storm chasing nature",
    "downhill mountain bike forest",
    "ice climbing",
    "hot air balloon mountains",
    "tornado nature",
    "underwater cave diving",
    "kitesurfing ocean",
    "cave exploration",
    "glacier drone",
    "jungle waterfall",
    "canyon river",
    "safari animals action",
    "mountain summit hiking",
    "sea cliffs drone",
    "trekking wilderness",
    "coral reef diving",
    "forest waterfall drone",
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


# ---------- Pexels ----------

def normalize_pexels(v: dict, query: str):
    duration = v.get("duration", 9999)
    width = v.get("width") or 0
    height = v.get("height") or 0
    if width and height and width >= height:
        return None  # not portrait
    files = [
        {"link": f["link"], "width": f.get("width", 0), "height": f.get("height", 0)}
        for f in v.get("video_files", [])
        if f.get("file_type") == "video/mp4" and (f.get("height") or 0) > (f.get("width") or 0)
    ]
    if not files:
        return None
    return {
        "uid": f"pexels:{v['id']}",
        "source": "Pexels",
        "duration": duration,
        "credit": (v.get("user") or {}).get("name", "Pexels"),
        "files": files,
        "query": query,
    }


def fetch_pexels_search(query: str, page: int, count: int = 80):
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        params={
            "query": query, "per_page": count, "page": page,
            "orientation": "portrait", "size": "medium",
            "max_duration": MAX_DURATION_SECONDS,
        },
        headers={"Authorization": PEXELS_API_KEY}, timeout=20,
    )
    resp.raise_for_status()
    out = []
    for v in resp.json().get("videos", []):
        if v.get("duration", 9999) > MAX_DURATION_SECONDS:
            continue
        c = normalize_pexels(v, query)
        if c:
            out.append(c)
    return out


def fetch_pexels_popular(page: int, count: int = 80):
    resp = requests.get(
        "https://api.pexels.com/videos/popular",
        params={"per_page": count, "page": page, "orientation": "portrait", "max_duration": MAX_DURATION_SECONDS},
        headers={"Authorization": PEXELS_API_KEY}, timeout=20,
    )
    resp.raise_for_status()
    out = []
    for v in resp.json().get("videos", []):
        if v.get("duration", 9999) > MAX_DURATION_SECONDS:
            continue
        c = normalize_pexels(v, "پرطرفدار")
        if c:
            out.append(c)
    return out


# ---------- Pixabay (optional second source) ----------

def normalize_pixabay(v: dict, query: str):
    duration = v.get("duration", 9999)
    files = []
    for size_name in ("large", "medium", "small", "tiny"):
        f = (v.get("videos") or {}).get(size_name)
        if not f:
            continue
        w, h = f.get("width", 0), f.get("height", 0)
        if not (h > w):  # keep portrait only
            continue
        link = f.get("url", "")
        if not link or link.lower().endswith(".gif"):
            continue
        files.append({"link": link, "width": w, "height": h})
    if not files:
        return None
    return {
        "uid": f"pixabay:{v['id']}",
        "source": "Pixabay",
        "duration": duration,
        "credit": v.get("user", "Pixabay"),
        "files": files,
        "query": query,
    }


def fetch_pixabay_search(query: str, page: int, count: int = 50):
    if not PIXABAY_API_KEY:
        return []
    resp = requests.get(
        "https://pixabay.com/api/videos/",
        params={
            "key": PIXABAY_API_KEY, "q": query, "per_page": count, "page": page,
            "video_type": "film", "safesearch": "true",
        },
        timeout=20,
    )
    resp.raise_for_status()
    out = []
    for v in resp.json().get("hits", []):
        if v.get("duration", 9999) > MAX_DURATION_SECONDS:
            continue
        c = normalize_pixabay(v, query)
        if c:
            out.append(c)
    return out


def pick_best_file(files: list):
    """files already portrait-only and non-gif; pick highest quality under the size cap."""
    ordered = sorted(files, key=lambda f: f["width"] * f["height"], reverse=True)
    for f in ordered:
        link = f["link"]
        try:
            head = requests.head(link, timeout=10, allow_redirects=True)
            size = int(head.headers.get("Content-Length", 0))
        except Exception:
            size = 0
        if size and size > MAX_FILE_SIZE_BYTES:
            continue
        return f, size
    return None, 0


def build_caption(c: dict) -> str:
    lines = [f"{c['source']} | فیلمبردار: {c['credit']}", f"⏱ {c['duration']} ثانیه"]
    if c["query"] != "پرطرفدار":
        fa_query = translate_to_fa(c["query"])
        if fa_query:
            lines.append(f"🎬 {fa_query}")
    else:
        lines.append("🔥 پرطرفدار")
    caption = "\n".join(lines)
    if len(caption) > MAX_CAPTION_LEN:
        caption = caption[: MAX_CAPTION_LEN - 1] + "…"
    return caption


def send_to_telegram(video_url: str, caption: str, width: int, height: int, duration: int) -> None:
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAnimation",
        data={
            "chat_id": TELEGRAM_CHAT_ID, "animation": video_url, "caption": caption,
            "width": width, "height": height, "duration": duration,
        },
        timeout=60,
    )
    resp.raise_for_status()


def main():
    sent_ids = load_sent_ids()
    seen_uids = set()
    all_candidates = []

    # Pexels popular/trending
    for page in random.sample(range(1, 8), 2):
        try:
            batch = fetch_pexels_popular(page)
        except requests.HTTPError as e:
            print(f"pexels popular page {page} failed: {e}", file=sys.stderr)
            batch = []
        new_count = 0
        for c in batch:
            if c["uid"] in sent_ids or c["uid"] in seen_uids:
                continue
            seen_uids.add(c["uid"])
            all_candidates.append(c)
            new_count += 1
        print(f"pexels popular page {page}: {new_count} new / {len(batch)} fetched")

    queries = TOPIC_QUERIES[:]
    random.shuffle(queries)

    for query in queries:
        if len(all_candidates) >= VIDEOS_PER_RUN * 4:
            break
        page = random.randint(1, 5)
        try:
            batch = fetch_pexels_search(query, page)
        except requests.HTTPError as e:
            print(f"pexels search '{query}' page {page} failed: {e}", file=sys.stderr)
            batch = []
        new_count = 0
        for c in batch:
            if c["uid"] in sent_ids or c["uid"] in seen_uids:
                continue
            seen_uids.add(c["uid"])
            all_candidates.append(c)
            new_count += 1
        print(f"pexels '{query}' page {page}: {new_count} new / {len(batch)} fetched")

        if PIXABAY_API_KEY:
            try:
                pbatch = fetch_pixabay_search(query, random.randint(1, 3))
            except requests.HTTPError as e:
                print(f"pixabay search '{query}' failed: {e}", file=sys.stderr)
                pbatch = []
            new_count2 = 0
            for c in pbatch:
                if c["uid"] in sent_ids or c["uid"] in seen_uids:
                    continue
                seen_uids.add(c["uid"])
                all_candidates.append(c)
                new_count2 += 1
            print(f"pixabay '{query}': {new_count2} new / {len(pbatch)} fetched")

    if not PIXABAY_API_KEY:
        print("PIXABAY_API_KEY not set -- running with Pexels only", file=sys.stderr)

    print(f"total unique portrait candidates collected: {len(all_candidates)}")
    random.shuffle(all_candidates)

    sent_this_run = 0
    for c in all_candidates:
        if sent_this_run >= VIDEOS_PER_RUN:
            break
        best_file, size = pick_best_file(c["files"])
        if not best_file:
            continue
        try:
            send_to_telegram(best_file["link"], build_caption(c), best_file["width"], best_file["height"], c["duration"])
        except requests.HTTPError as e:
            print(f"failed to send {c['uid']}: {e}", file=sys.stderr)
            continue
        sent_ids.add(c["uid"])
        sent_this_run += 1
        save_sent_ids(sent_ids)
        mb = size / 1024 / 1024 if size else 0
        print(f"sent {c['uid']} ({sent_this_run}/{VIDEOS_PER_RUN}) ~{mb:.1f}MB")
        time.sleep(SEND_DELAY_SECONDS)

    if sent_this_run < 30:
        print(f"WARNING: only sent {sent_this_run}/30 target this run", file=sys.stderr)


if __name__ == "__main__":
    main()
