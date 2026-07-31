import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

SENT_IDS_FILE = Path(__file__).parent / "sent_video_ids.json"
MAX_HISTORY = 20000
VIDEOS_PER_RUN = 30
SEND_DELAY_SECONDS = 0.8
MAX_CAPTION_LEN = 1000
MAX_DURATION_SECONDS = 30
TARGET_MAX_BYTES = int(3.8 * 1024 * 1024)  # stay under the 4MB cap after re-encode
OUT_WIDTH = 720
OUT_HEIGHT = 1280

TELEGRAM_BOT_TOKEN = os.environ["WOLF_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["WOLF_CHAT_ID"]
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
PIXABAY_API_KEY = os.environ["PIXABAY_API_KEY"]

TOPIC_QUERIES = [
    "canyoneering",
    "waterfall rappelling",
    "cliff jumping",
    "extreme rock climbing",
    "free solo climbing",
    "skydiving",
    "base jumping",
    "wingsuit flying",
    "paragliding mountains",
    "big wave surfing",
    "whitewater rafting",
    "avalanche",
    "lightning storm",
    "volcano eruption",
    "shark ocean",
    "crocodile",
    "bear wild",
    "eagle hunting",
    "lion wild",
    "ice climbing",
    "storm chasing",
    "downhill mountain bike",
    "freediving",
    "cave diving",
    "kitesurfing",
    "cave exploration",
    "glacier",
    "jungle waterfall",
    "canyon river",
    "safari wild animals",
    "mountain summit",
    "sea cliffs",
    "trekking wilderness",
    "coral reef diving",
    "forest waterfall",
    "extreme sports",
    "adventure travel",
    "hiking mountains",
    "rock climbing",
    "northern lights",
    "desert dunes",
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


# ---------- Pixabay (primary source) ----------

def normalize_pixabay(v: dict, query: str):
    duration = v.get("duration", 9999)
    best = None
    for size_name in ("medium", "large", "small", "tiny"):
        f = (v.get("videos") or {}).get(size_name)
        if f and f.get("url"):
            best = f
            break
    if not best:
        return None
    return {
        "uid": f"pixabay:{v['id']}",
        "source": "Pixabay",
        "duration": duration,
        "credit": v.get("user", "Pixabay"),
        "download_url": best["url"],
        "query": query,
    }


def fetch_pixabay_search(query: str, page: int, count: int = 60, editors_choice: bool = False, order: str = "popular"):
    params = {
        "key": PIXABAY_API_KEY, "q": query, "per_page": count, "page": page,
        "video_type": "film", "safesearch": "true", "order": order,
    }
    if editors_choice:
        params["editors_choice"] = "true"
    resp = requests.get("https://pixabay.com/api/videos/", params=params, timeout=20)
    resp.raise_for_status()
    out = []
    for v in resp.json().get("hits", []):
        if v.get("duration", 9999) > MAX_DURATION_SECONDS:
            continue
        c = normalize_pixabay(v, query)
        if c:
            out.append(c)
    return out


# ---------- Pexels (secondary, supplements when Pixabay is short) ----------

def normalize_pexels(v: dict, query: str):
    duration = v.get("duration", 9999)
    files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")]
    if not files:
        return None
    files.sort(key=lambda f: (f.get("width") or 0) * (f.get("height") or 0), reverse=True)
    mid = files[len(files) // 2]
    return {
        "uid": f"pexels:{v['id']}",
        "source": "Pexels",
        "duration": duration,
        "credit": (v.get("user") or {}).get("name", "Pexels"),
        "download_url": mid["link"],
        "query": query,
    }


def fetch_pexels_search(query: str, page: int, count: int = 40):
    if not PEXELS_API_KEY:
        return []
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        params={"query": query, "per_page": count, "page": page, "max_duration": MAX_DURATION_SECONDS},
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


# ---------- download + crop-to-story + compress ----------

def process_to_portrait(download_url: str, duration: float, workdir: str):
    src_path = os.path.join(workdir, "src.mp4")
    out_path = os.path.join(workdir, "out.mp4")

    r = requests.get(download_url, timeout=60, stream=True)
    r.raise_for_status()
    with open(src_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 16):
            f.write(chunk)

    dur = max(1.0, min(duration or MAX_DURATION_SECONDS, MAX_DURATION_SECONDS))
    audio_kbps = 64
    video_kbps = max(300, int(((TARGET_MAX_BYTES * 8) / dur / 1000) - audio_kbps))

    vf = f"crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9),scale={OUT_WIDTH}:{OUT_HEIGHT}"
    cmd = [
        "ffmpeg", "-y", "-i", src_path, "-t", str(dur),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps*1.2)}k", "-bufsize", f"{video_kbps*2}k",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        "-movflags", "+faststart",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if result.returncode != 0 or not os.path.exists(out_path):
        print(f"ffmpeg failed: {result.stderr[-500:]}", file=sys.stderr)
        return None, 0

    size = os.path.getsize(out_path)
    if size > TARGET_MAX_BYTES * 1.15:
        return None, size
    return out_path, size


def build_caption(c: dict) -> str:
    lines = [f"{c['source']} | فیلمبردار: {c['credit']}", f"⏱ {c['duration']} ثانیه"]
    fa_query = translate_to_fa(c["query"])
    if fa_query:
        lines.append(f"🎬 {fa_query}")
    caption = "\n".join(lines)
    if len(caption) > MAX_CAPTION_LEN:
        caption = caption[: MAX_CAPTION_LEN - 1] + "…"
    return caption


def send_to_telegram(file_path: str, caption: str, duration: int) -> None:
    with open(file_path, "rb") as fh:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "width": OUT_WIDTH,
                "height": OUT_HEIGHT,
                "duration": duration,
                "supports_streaming": True,
            },
            files={"video": ("clip.mp4", fh, "video/mp4")},
            timeout=120,
        )
    resp.raise_for_status()


def main():
    sent_ids = load_sent_ids()
    seen_uids = set()
    all_candidates = []

    # Pixabay editors_choice pool -- curated high quality, small but reliable
    for page in (1, 2):
        try:
            batch = fetch_pixabay_search("nature adventure", page, editors_choice=True)
        except requests.HTTPError as e:
            print(f"pixabay editors_choice page {page} failed: {e}", file=sys.stderr)
            batch = []
        for c in batch:
            if c["uid"] in sent_ids or c["uid"] in seen_uids:
                continue
            seen_uids.add(c["uid"])
            all_candidates.append(c)
    print(f"pixabay editors_choice pool: {len(all_candidates)}")

    # Pixabay topic search -- the main pool (this is what was asked for)
    queries = TOPIC_QUERIES[:]
    random.shuffle(queries)
    for query in queries:
        page = random.randint(1, 6)
        try:
            batch = fetch_pixabay_search(query, page)
        except requests.HTTPError as e:
            print(f"pixabay search '{query}' page {page} failed: {e}", file=sys.stderr)
            batch = []
        new_count = 0
        for c in batch:
            if c["uid"] in sent_ids or c["uid"] in seen_uids:
                continue
            seen_uids.add(c["uid"])
            all_candidates.append(c)
            new_count += 1
        print(f"pixabay '{query}' page {page}: {new_count} new / {len(batch)} fetched")

    # Pexels as a supplement only if Pixabay didn't give enough
    if len(all_candidates) < VIDEOS_PER_RUN * 3 and PEXELS_API_KEY:
        for query in queries[:15]:
            page = random.randint(1, 4)
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

    print(f"total unique candidates collected: {len(all_candidates)}")
    random.shuffle(all_candidates)

    sent_this_run = 0
    for c in all_candidates:
        if sent_this_run >= VIDEOS_PER_RUN:
            break
        with tempfile.TemporaryDirectory() as workdir:
            try:
                out_path, size = process_to_portrait(c["download_url"], c["duration"], workdir)
            except Exception as e:
                print(f"processing failed for {c['uid']}: {e}", file=sys.stderr)
                continue
            if not out_path:
                print(f"skip {c['uid']}: could not fit under size cap", file=sys.stderr)
                continue
            try:
                send_to_telegram(out_path, build_caption(c), int(min(c["duration"], MAX_DURATION_SECONDS)))
            except requests.HTTPError as e:
                print(f"failed to send {c['uid']}: {e}", file=sys.stderr)
                continue
        sent_ids.add(c["uid"])
        sent_this_run += 1
        save_sent_ids(sent_ids)
        mb = size / 1024 / 1024
        print(f"sent {c['uid']} ({sent_this_run}/{VIDEOS_PER_RUN}) ~{mb:.1f}MB")
        time.sleep(SEND_DELAY_SECONDS)

    if sent_this_run < 30:
        print(f"WARNING: only sent {sent_this_run}/30 target this run", file=sys.stderr)


if __name__ == "__main__":
    main()
