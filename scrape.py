"""Fetch Stockholm events from Luma (all category pages) and Meetup (keyword searches), write events.json."""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

LUMA_CATEGORIES = ["ai", "tech", "games", "arts", "books", "climate", "crypto", "family", "fitness", "food", "running", "wellness"]
LAT, LNG = 59.32938, 18.06871  # the coordinates luma.com uses for Stockholm
LUMA_API = "https://api.luma.com/discover/get-paginated-events"

# Meetup search keyword -> the category it is filed under. Each search returns only ~12-30 events, so use many.
MEETUP_KEYWORDS = {
    "ai": "ai", "machine learning": "ai", "llm": "ai", "data": "ai",
    "game": "games", "game dev": "games", "unity": "games",
    "tech": "tech", "developer": "tech", "programming": "tech", "python": "tech", "software": "tech",
    "cloud": "tech", "startup": "tech", "hackathon": "tech", "security": "tech", "robotics": "tech",
}
MEETUP_FIND = "https://www.meetup.com/find/"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def luma():
    events = {}
    for slug in LUMA_CATEGORIES:
        cursor = None
        while True:
            params = {"latitude": LAT, "longitude": LNG, "pagination_limit": 50, "slug": slug}
            if cursor:
                params["pagination_cursor"] = cursor
            page = json.loads(get(LUMA_API + "?" + urllib.parse.urlencode(params)))
            for entry in page["entries"]:
                ev = entry["event"]
                geo = ev.get("geo_address_info") or {}
                loc = (geo.get("localized") or {}).get("en-GB") or geo
                item = events.setdefault(ev["api_id"], {
                    "id": ev["api_id"],
                    "name": ev["name"],
                    "start": ev["start_at"],
                    "end": ev.get("end_at"),
                    "url": "https://luma.com/" + ev["url"],
                    "cover": ev.get("cover_url"),
                    "place": loc.get("short_address") or loc.get("city") or "",
                    "online": ev.get("location_type") == "online",
                    "hosts": [h["name"] for h in entry.get("hosts") or [] if h.get("name")],
                    "source": "luma",
                    "categories": [],
                })
                if slug not in item["categories"]:
                    item["categories"].append(slug)
            if not page.get("has_more"):
                break
            cursor = page["next_cursor"]
            time.sleep(0.5)
        print("luma", slug, file=sys.stderr)
    return events


def meetup():
    events = {}
    for kw, category in MEETUP_KEYWORDS.items():
        params = {"location": "se--Stockholm", "source": "EVENTS", "keywords": kw, "distance": "tenMiles"}
        html = get(MEETUP_FIND + "?" + urllib.parse.urlencode(params))
        data = json.loads(re.search(r'__NEXT_DATA__[^>]*>(.*?)</script>', html, re.S).group(1))
        state = data["props"]["pageProps"]["__APOLLO_STATE__"]
        for key, ev in state.items():
            if not key.startswith("Event:") or not ev.get("title"):
                continue  # the state also holds partial Event stubs
            group = state.get(ev["group"]["__ref"], {}) if ev.get("group") else {}
            photo = state.get(ev["featuredEventPhoto"]["__ref"], {}) if ev.get("featuredEventPhoto") else {}
            start = datetime.fromisoformat(ev["dateTime"]).astimezone(timezone.utc)
            item = events.setdefault(ev["id"], {
                "id": "meetup-" + ev["id"],
                "name": ev["title"],
                "start": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "end": None,
                "url": ev["eventUrl"],
                "cover": (photo.get("highResUrl") or "").replace("/highres_", "/600_") or None,
                "place": "",
                "online": ev.get("eventType") == "ONLINE",
                "hosts": [group["name"]] if group.get("name") else [],
                "source": "meetup",
                "categories": [],
            })
            if category not in item["categories"]:
                item["categories"].append(category)
        print("meetup", kw, file=sys.stderr)
        time.sleep(1)
    return events


def norm(name):
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def main():
    merged = {}
    for source in (luma, meetup):
        try:
            found = source()
        except Exception as e:  # one source failing should not wipe the other
            print(source.__name__, "FAILED:", e, file=sys.stderr)
            continue
        for ev in found.values():
            key = (norm(ev["name"]), ev["start"])
            if key in merged:  # same event listed on both sites
                for c in ev["categories"]:
                    if c not in merged[key]["categories"]:
                        merged[key]["categories"].append(c)
                merged[key]["alt_url"] = ev["url"]
            else:
                merged[key] = ev

    if not merged:
        sys.exit("no events fetched; keeping the old events.json")
    out = sorted(merged.values(), key=lambda e: e["start"])
    with open("events.json", "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "events": out}, f, ensure_ascii=False, indent=1)
    print(len(out), "events", file=sys.stderr)


if __name__ == "__main__":
    main()
