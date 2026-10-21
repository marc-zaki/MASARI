"""
YouTube CS course importer.

Pulls CS course playlists and long-form tutorial videos from the YouTube Data API v3,
filters them to Computer Science only, and merges them into the course catalog
(datasets/CS_Dataset_Phase2.json + MongoDB `cs_recommender.courses`).

Quota note: search.list costs 100 units and the default daily quota is 10,000,
so each query below (1 playlist + 2 video searches) costs ~300 units. Run this
periodically (CLI or admin button), never per user request.

Usage: python youtube_importer.py --max-queries 20
"""
import os
import json
import math
import argparse

import httpx
from dotenv import load_dotenv

from cs_filter import is_cs_related

load_dotenv(override=True)

API_BASE = "https://www.googleapis.com/youtube/v3"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "datasets", "CS_Dataset_Phase2.json")
CACHE_PATH = os.path.join(BASE_DIR, "datasets", "tfidf_cache.pkl")
SKILLS_PATH = os.path.join(BASE_DIR, "interview_analyzer", "storage", "skills_keywords.json")

# YouTube category IDs: 28 = Science & Technology, 27 = Education.
# Only applicable to type=video searches (ignored by the API for playlists).
VIDEO_CATEGORIES = ["28", "27"]

# Channels whose content is always trusted as CS (skips the keyword filter).
TRUSTED_CHANNELS = {
    "UC8butISFwT-Wl7EV0hUK0BQ",  # freeCodeCamp.org
    "UCcabW7890RKJzL968QWEykA",  # CS50
    "UCEBb1b_L6zDS3xTUrIALZOw",  # MIT OpenCourseWare
    "UC29ju8bIPH5as8OGnQzwJyA",  # Traversy Media
    "UCsBjURrPoezykLs9EqgamOA",  # Fireship
    "UC_mYaQAE6-71rjSN6CeCA-g",  # NeetCode
    "UCWv7vMbMWH4-V0ZXdmDpPBA",  # Programming with Mosh
}

# Fallback topics if the skills vocabulary file is missing.
DEFAULT_TOPICS = [
    "python", "javascript", "java", "c++", "data structures", "algorithms",
    "machine learning", "deep learning", "sql", "docker", "kubernetes", "react",
    "operating systems", "computer networks", "system design", "cybersecurity",
    "git", "linux", "devops", "web development",
]

MIN_PLAYLIST_ITEMS = 5


def build_queries(max_queries):
    """Builds '<topic> full course' queries from the existing skills vocabulary."""
    topics = []
    try:
        with open(SKILLS_PATH) as f:
            skills = json.load(f)
        # Category names (e.g. "data_structures") make good broad course queries
        topics = [k.replace("_", " ") for k in skills.keys()]
    except Exception:
        pass
    for t in DEFAULT_TOPICS:
        if t not in topics:
            topics.append(t)
    return [f"{t} full course" for t in topics[:max_queries]]


def estimate_stars(views, likes):
    """YouTube has no star ratings: map like ratio + popularity onto 3.5–5.0."""
    if not views:
        return 0.0
    like_ratio = (likes or 0) / views
    ratio_score = min(like_ratio / 0.04, 1.0)          # ~4% likes is excellent
    popularity = min(math.log10(max(views, 1)) / 7, 1.0)  # 10M views caps it
    return round(min(3.5 + ratio_score * 1.0 + popularity * 0.5, 5.0), 1)


class YouTubeClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.http = httpx.Client(timeout=20)

    def get(self, endpoint, **params):
        params["key"] = self.api_key
        r = self.http.get(f"{API_BASE}/{endpoint}", params=params)
        r.raise_for_status()
        return r.json()

    def video_stats(self, video_ids):
        if not video_ids:
            return {}
        data = self.get("videos", part="statistics", id=",".join(video_ids), maxResults=50)
        return {v["id"]: v.get("statistics", {}) for v in data.get("items", [])}

    def fetch_playlists(self, query, max_results=25):
        hits = self.get("search", part="snippet", type="playlist", q=query,
                        maxResults=max_results, relevanceLanguage="en")
        ids = [h["id"]["playlistId"] for h in hits.get("items", [])]
        if not ids:
            return []
        details = self.get("playlists", part="snippet,contentDetails", id=",".join(ids), maxResults=50)

        playlists = [p for p in details.get("items", [])
                     if p.get("contentDetails", {}).get("itemCount", 0) >= MIN_PLAYLIST_ITEMS]

        # Use each playlist's first video as a proxy for its engagement stats
        first_videos = {}
        for p in playlists:
            try:
                items = self.get("playlistItems", part="contentDetails", playlistId=p["id"], maxResults=1)
                first_videos[p["id"]] = items["items"][0]["contentDetails"]["videoId"]
            except Exception:
                pass
        stats = self.video_stats(list(first_videos.values()))

        courses = []
        for p in playlists:
            s = stats.get(first_videos.get(p["id"]), {})
            courses.append(to_course(
                p["snippet"], f"https://www.youtube.com/playlist?list={p['id']}", s,
                extra=f" ({p['contentDetails']['itemCount']} videos)",
            ))
        return courses

    def fetch_long_videos(self, query, category_id, max_results=25):
        hits = self.get("search", part="snippet", type="video", q=query, videoDuration="long",
                        videoCategoryId=category_id, maxResults=max_results, relevanceLanguage="en")
        ids = [h["id"]["videoId"] for h in hits.get("items", [])]
        if not ids:
            return []
        details = self.get("videos", part="snippet,statistics", id=",".join(ids), maxResults=50)
        return [to_course(v["snippet"], f"https://www.youtube.com/watch?v={v['id']}", v.get("statistics", {}))
                for v in details.get("items", [])]


def to_course(snippet, url, stats, extra=""):
    views = int(stats.get("viewCount", 0) or 0)
    likes = int(stats.get("likeCount", 0) or 0)
    thumbs = snippet.get("thumbnails", {})
    thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
    return {
        "provider": f"YouTube · {snippet.get('channelTitle', 'Unknown')}",
        "title": snippet.get("title", "").strip(),
        "content_text": (snippet.get("description", "").strip() or "No description provided.") + extra,
        "url": url,
        "raw_reviews": [],
        "review_summary": "",
        "stars": estimate_stars(views, likes),
        "ratings_count": likes,
        "source": "youtube",
        "thumbnail": thumb,
        "_channel_id": snippet.get("channelId", ""),
    }


def keep(course):
    if course["_channel_id"] in TRUSTED_CHANNELS:
        return True
    return bool(course["title"]) and is_cs_related(course["title"], course["content_text"])


def merge_courses(new_courses):
    """Merges into the local JSON catalog (dedupe by URL, then title) and upserts into MongoDB."""
    try:
        with open(DB_PATH) as f:
            current = json.load(f)
    except Exception:
        current = []

    seen_urls = {str(c.get("url", "")).strip() for c in current}
    seen_titles = {str(c.get("title", "")).lower().strip() for c in current}
    added = []
    for c in new_courses:
        if c["url"] in seen_urls or c["title"].lower() in seen_titles:
            continue
        seen_urls.add(c["url"])
        seen_titles.add(c["title"].lower())
        added.append(c)

    if not added:
        return 0

    with open(DB_PATH, "w") as f:
        json.dump(current + added, f, indent=4)

    try:
        import pymongo
        client = pymongo.MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"),
                                     serverSelectionTimeoutMS=2000)
        col = client["cs_recommender"]["courses"]
        for c in added:
            col.update_one({"url": c["url"]}, {"$set": c}, upsert=True)
        print(f"Upserted {len(added)} courses into MongoDB.")
    except Exception as e:
        print(f"MongoDB sync skipped ({e}). Saved to local JSON only.")

    # Invalidate TF-IDF cache so the next load re-indexes the new courses
    if os.path.exists(CACHE_PATH):
        os.remove(CACHE_PATH)
    return len(added)


def run_import(max_queries=10, log=print):
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        raise RuntimeError("YOUTUBE_API_KEY is not set.")

    yt = YouTubeClient(api_key)
    queries = build_queries(max_queries)
    collected, skipped = [], 0

    for i, q in enumerate(queries, 1):
        log(f"[YouTube] ({i}/{len(queries)}) Searching: {q}")
        try:
            batch = yt.fetch_playlists(q)
            for cat in VIDEO_CATEGORIES:
                batch += yt.fetch_long_videos(q, cat, max_results=10)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                log("[YouTube] Quota exceeded or key rejected — stopping early.")
                break
            log(f"[YouTube] Request failed: {e}")
            continue

        for c in batch:
            if keep(c):
                c.pop("_channel_id", None)
                collected.append(c)
            else:
                skipped += 1

    log(f"[YouTube] Fetched {len(collected)} CS items (skipped {skipped} non-CS).")
    inserted = merge_courses(collected)
    log(f"[YouTube] Inserted {inserted} new courses.")
    return {"found": len(collected), "skipped": skipped, "inserted": inserted}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import CS courses from YouTube.")
    parser.add_argument("--max-queries", type=int, default=10,
                        help="Number of topic queries (~300 quota units each).")
    args = parser.parse_args()
    run_import(args.max_queries)
