"""Fetch Stockholm events from Luma (all category pages), Meetup (keyword searches) and the Gamedev Events Sweden
Google Calendar embedded on gametown.se, then write events.json."""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

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

# The "Gamedev Events Sweden" Google Calendar embedded on gametown.se (made by Link in Bar).
GAMETOWN_ICS = ("https://calendar.google.com/calendar/ical/"
                "4790ad3d567207c637423fec56105687ab0de07eae891bafd5766b7cdc5592bb%40group.calendar.google.com/public/basic.ics")
GAMETOWN_HORIZON_DAYS = 180  # how far ahead recurring events are expanded
WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


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
                    "sources": ["luma"],
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
                "start": iso(start),
                "end": None,
                "url": ev["eventUrl"],
                "cover": (photo.get("highResUrl") or "").replace("/highres_", "/600_") or None,
                "place": "",
                "online": ev.get("eventType") == "ONLINE",
                "hosts": [group["name"]] if group.get("name") else [],
                "sources": ["meetup"],
                "categories": [],
            })
            if category not in item["categories"]:
                item["categories"].append(category)
        print("meetup", kw, file=sys.stderr)
        time.sleep(1)
    return events


# --- Gametown (iCal) --------------------------------------------------------------------------------------------

def stockholm_offset(utc):
    """UTC offset of Europe/Stockholm at a naive UTC datetime (EU rule: last Sunday of March to October, 01:00 UTC).
    Hand-rolled because Windows Python ships no tz database."""
    def last_sunday(year, month):
        d = date(year, month + 1, 1) - timedelta(days=1)
        return datetime.combine(d - timedelta(days=(d.weekday() + 1) % 7), datetime.min.time()) + timedelta(hours=1)
    summer = last_sunday(utc.year, 3) <= utc < last_sunday(utc.year, 10)
    return timedelta(hours=2 if summer else 1)


def local_to_utc(local):
    for hours in (2, 1):
        utc = local - timedelta(hours=hours)
        if stockholm_offset(utc) == timedelta(hours=hours):
            return utc
    return local - timedelta(hours=1)


def parse_ics(text):
    """Yield each VEVENT as {property: [(params, value), ...]}, with folded lines joined."""
    text = text.replace("\r\n", "\n").replace("\n ", "")
    for block in text.split("BEGIN:VEVENT")[1:]:
        props = {}
        for line in block.split("END:VEVENT")[0].splitlines():
            if ":" in line:
                head, value = line.split(":", 1)
                name, *params = head.split(";")
                props.setdefault(name, []).append((params, value))
        yield props


def ics_time(prop):
    """(params, value) -> (naive UTC datetime, is_all_day)."""
    params, value = prop
    if "VALUE=DATE" in params:
        return local_to_utc(datetime.strptime(value, "%Y%m%d")), True
    dt = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    return (dt if value.endswith("Z") else local_to_utc(dt)), False


def occurrences(props, start, horizon):
    """The start times of an event: itself, or its RRULE repeats (DAILY, WEEKLY, MONTHLY by nth weekday)."""
    if "RRULE" not in props:
        return [start]
    rule = dict(part.split("=", 1) for part in props["RRULE"][0][1].split(";"))
    last = min(ics_time(([], rule["UNTIL"]))[0], horizon) if "UNTIL" in rule else horizon
    freq, interval = rule.get("FREQ"), int(rule.get("INTERVAL", 1))
    first = start + stockholm_offset(start)  # local wall-clock start
    out, day = [], first.date()
    while day <= (last + timedelta(days=1)).date():
        if freq == "DAILY":
            hit = (day - first.date()).days % interval == 0
        elif freq == "WEEKLY":
            wanted = [WEEKDAYS.index(d[-2:]) for d in rule.get("BYDAY", WEEKDAYS[first.weekday()]).split(",")]
            hit = day.weekday() in wanted and ((day - first.date()).days // 7) % interval == 0
        elif freq == "MONTHLY" and "BYDAY" in rule:
            n, wd = int(rule["BYDAY"][:-2]), WEEKDAYS.index(rule["BYDAY"][-2:])
            months = (day.year - first.year) * 12 + day.month - first.month
            nth = (day.day - 1) // 7 + 1 if n > 0 else -((date(day.year + day.month // 12, day.month % 12 + 1, 1) - day).days - 1) // 7 - 1
            hit = day.weekday() == wd and nth == n and months % interval == 0
        else:
            hit = False
        if hit:
            occ = local_to_utc(datetime.combine(day, first.time()))
            if occ <= last:
                out.append(occ)
        day += timedelta(days=1)
    return out


def gametown():
    props_list = list(parse_ics(get(GAMETOWN_ICS)))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    horizon = now + timedelta(days=GAMETOWN_HORIZON_DAYS)
    # A RECURRENCE-ID event replaces one occurrence of its recurring series.
    replaced = {(p["UID"][0][1], ics_time(p["RECURRENCE-ID"][0])[0]) for p in props_list if "RECURRENCE-ID" in p}
    events = {}
    for p in props_list:
        field = lambda name: re.sub(r"\\(.)", r"\1", p[name][0][1]) if name in p else ""
        if field("STATUS") == "CANCELLED":
            continue
        uid = p["UID"][0][1]
        start, all_day = ics_time(p["DTSTART"][0])
        length = ics_time(p["DTEND"][0])[0] - start if "DTEND" in p else None
        excluded = {ics_time((params, v))[0] for params, val in p.get("EXDATE", []) for v in val.split(",")}
        link = re.search(r"https?://[^\s<>\"]+", field("DESCRIPTION"))
        for occ in occurrences(p, start, horizon):
            if occ in excluded or ("RECURRENCE-ID" not in p and (uid, occ) in replaced):
                continue
            end = occ + length if length else None
            if (end or occ) < now:
                continue
            events[uid + iso(occ)] = {
                "id": "gametown-" + uid.split("@")[0] + "-" + occ.strftime("%Y%m%d"),
                "name": field("SUMMARY"),
                "start": iso(occ),
                "end": iso(end) if end else None,
                "allDay": all_day,
                "url": link.group(0).rstrip(".,)") if link else "https://www.gametown.se/",
                "cover": None,
                "place": ", ".join(field("LOCATION").split(", ")[:2]),
                "online": False,
                "hosts": [],
                "sources": ["gametown"],
                "categories": ["games"],
            }
    print("gametown", len(events), file=sys.stderr)
    return events


def norm(name):
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def main():
    merged = {}
    for source in (luma, meetup, gametown):
        try:
            found = source()
        except Exception as e:  # one source failing should not wipe the others
            print(source.__name__, "FAILED:", e, file=sys.stderr)
            continue
        for ev in found.values():
            key = (norm(ev["name"]), ev["start"])
            if key in merged:  # same event listed on several sites
                m = merged[key]
                for field in ("categories", "sources"):
                    m[field] += [x for x in ev[field] if x not in m[field]]
                m["cover"] = m["cover"] or ev["cover"]
                m["alt_url"] = ev["url"]
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
