"""Fetch the Stockholm "nearby" events from Luma's category pages and write events.json."""
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CATEGORIES = ["games", "ai", "tech"]
LAT, LNG = 59.32938, 18.06871  # the coordinates luma.com uses for Stockholm
API = "https://api.luma.com/discover/get-paginated-events"


def fetch(slug, cursor=None):
    params = {"latitude": LAT, "longitude": LNG, "pagination_limit": 50, "slug": slug}
    if cursor:
        params["pagination_cursor"] = cursor
    req = urllib.request.Request(
        API + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    events = {}
    for slug in CATEGORIES:
        cursor = None
        while True:
            page = fetch(slug, cursor)
            for entry in page["entries"]:
                ev = entry["event"]
                geo = ev.get("geo_address_info") or {}
                loc = (geo.get("localized") or {}).get("en-GB") or geo
                item = events.setdefault(ev["api_id"], {
                    "id": ev["api_id"],
                    "name": ev["name"],
                    "start": ev["start_at"],
                    "end": ev.get("end_at"),
                    "tz": ev.get("timezone") or "Europe/Stockholm",
                    "url": "https://luma.com/" + ev["url"],
                    "place": loc.get("short_address") or loc.get("city") or "",
                    "online": ev.get("location_type") == "online",
                    "hosts": [h["name"] for h in entry.get("hosts") or [] if h.get("name")],
                    "guests": entry.get("guest_count"),
                    "categories": [],
                })
                if slug not in item["categories"]:
                    item["categories"].append(slug)
            if not page.get("has_more"):
                break
            cursor = page["next_cursor"]
            time.sleep(0.5)
        print(slug, "done", file=sys.stderr)

    out = sorted(events.values(), key=lambda e: e["start"])
    with open("events.json", "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "events": out}, f, ensure_ascii=False, indent=1)
    print(len(out), "events", file=sys.stderr)


if __name__ == "__main__":
    main()
