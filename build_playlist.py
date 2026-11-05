#!/usr/bin/env python3
"""Build a broad free-to-access Russian IPTV playlist for Moscow time."""

from urllib.request import Request, urlopen
from datetime import datetime, timezone
from pathlib import Path
import json
import re

SOURCES = {
    "national": "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "spb": "https://iptv-org.github.io/iptv/subdivisions/ru-spe.m3u",
    "len": "https://iptv-org.github.io/iptv/subdivisions/ru-len.m3u",
}
API = "https://iptv-org.github.io/api"
EPG_URL = "http://epg.one/ru.xml.gz"
OUTPUT = Path("playlist.m3u")

# Only nationwide feeds and feeds explicitly assigned to Saint Petersburg
# or Leningrad Oblast are accepted. Every accepted feed must be Moscow-time.
ALLOWED_AREAS = {"c/RU", "s/RU-SPE", "s/RU-LEN"}
REQUIRED_TIMEZONE = "Europe/Moscow"
MIN_UNIQUE_CHANNELS = 30

FAVOURITES = {
    "Первый канал", "Россия 1", "НТВ", "СТС", "ТНТ",
    "Пятница!", "Россия 24", "Матч ТВ",
}

CATEGORY_NAMES = {
    "news": "Новости",
    "sports": "Спорт",
    "kids": "Детские",
    "animation": "Детские",
    "music": "Музыка",
    "movies": "Фильмы",
    "series": "Сериалы",
    "entertainment": "Развлекательные",
    "documentary": "Познавательные",
    "education": "Познавательные",
    "science": "Познавательные",
    "lifestyle": "Развлекательные",
    "general": "Развлекательные",
    "religious": "Познавательные",
}

def download_text(url):
    request = Request(url, headers={"User-Agent": "iptv-russia-spb-playlist/2.0"})
    with urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8-sig", errors="replace")

def download_json(name):
    return json.loads(download_text(f"{API}/{name}.json"))

def parse_m3u(text):
    pending = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            pending = line
        elif not line.startswith("#") and pending:
            yield pending, line
            pending = None

def attrs(extinf):
    return dict(re.findall(r'([\w-]+)="([^"]*)"', extinf))

def display_name(extinf, metadata):
    return metadata.get("tvg-name") or extinf.rsplit(",", 1)[-1].strip()

def clean_extinf(extinf, channel_id, group, title):
    head, comma, _ = extinf.partition(",")
    head = re.sub(r'\s+group-title="[^"]*"', "", head)
    if re.search(r'tvg-id="[^"]*"', head):
        head = re.sub(r'tvg-id="[^"]*"', f'tvg-id="{channel_id}"', head)
    else:
        head += f' tvg-id="{channel_id}"'
    head += f' group-title="{group}"'
    return head + "," + title

def quality_score(value):
    match = re.search(r'(\d{3,4})\s*([pi])?', str(value or "").lower())
    if not match:
        return 0
    height = int(match.group(1))
    progressive = 1 if match.group(2) == "p" else 0
    return height * 10 + progressive

def classify(categories):
    # Prefer a specific category if a channel has several tags.
    priority = [
        "news", "sports", "kids", "animation", "music", "movies", "series",
        "documentary", "education", "science", "entertainment", "lifestyle",
        "general", "religious",
    ]
    tags = set(categories or [])
    for tag in priority:
        if tag in tags:
            return CATEGORY_NAMES[tag]
    return "Развлекательные"

def feed_allowed(feed):
    areas = set(feed.get("broadcast_area") or [])
    zones = set(feed.get("timezones") or [])
    # Reject multi-time-zone feeds: they cannot guarantee Moscow-time scheduling.
    return bool(areas & ALLOWED_AREAS) and zones == {REQUIRED_TIMEZONE}

def main():
    channels = {item["id"]: item for item in download_json("channels")}
    feeds_data = download_json("feeds")
    feeds = {}
    for item in feeds_data:
        feeds[(item.get("channel"), item.get("id"))] = item

    eligible_by_channel = {}
    for (channel_id, feed_id), feed in feeds.items():
        if channel_id and feed_allowed(feed):
            eligible_by_channel.setdefault(channel_id, []).append(feed)

    source_entries = []
    for label, url in SOURCES.items():
        parsed = list(parse_m3u(download_text(url)))
        print(f"Loaded {len(parsed)} entries from {label}")
        source_entries.extend(parsed)

    # Build one best-quality entry per channel. No regional feeds outside
    # Saint Petersburg/Leningrad and no feeds with non-Moscow time zones.
    chosen = {}
    skipped_no_metadata = 0
    skipped_time_area = 0

    for extinf, url in source_entries:
        if not url.startswith(("http://", "https://")):
            continue
        if any(marker in (extinf + " " + url).lower()
               for marker in ("[geo-blocked]", "[not 24/7]", "[offline]", "[broken]")):
            continue

        meta = attrs(extinf)
        raw_id = meta.get("tvg-id", "").strip()
        if not raw_id:
            skipped_no_metadata += 1
            continue

        channel_id, separator, feed_id = raw_id.partition("@")
        channel = channels.get(channel_id)
        if not channel or channel.get("country") != "RU" or channel.get("is_nsfw"):
            skipped_no_metadata += 1
            continue

        eligible = eligible_by_channel.get(channel_id, [])
        if separator:
            matches = [feed for feed in eligible
                       if str(feed.get("id", "")).lower() == feed_id.lower()]
        else:
            matches = [feed for feed in eligible if feed.get("is_main")]
            if not matches and len(eligible) == 1:
                matches = eligible

        if not matches:
            skipped_time_area += 1
            continue

        feed = matches[0]
        title = display_name(extinf, meta)
        if not title:
            title = channel.get("name") or channel_id

        # Use channel categories from the maintained database, not a short
        # hand-written whitelist of channel names.
        group = classify(channel.get("categories"))
        score = quality_score(feed.get("format"))
        candidate = {
            "channel_id": channel_id,
            "extinf": extinf,
            "url": url,
            "title": title,
            "group": group,
            "score": score,
            "main": bool(feed.get("is_main")),
            "area": set(feed.get("broadcast_area") or []),
        }
        current = chosen.get(channel_id)
        if current is None or (candidate["score"], candidate["main"]) > (current["score"], current["main"]):
            chosen[channel_id] = candidate

    if len(chosen) < MIN_UNIQUE_CHANNELS:
        raise RuntimeError(
            f"Only {len(chosen)} unique eligible channels found; refusing to replace "
            f"the published playlist. Check source metadata and Moscow-time filters. "
            f"Skipped for area/time-zone mismatch: {skipped_time_area}."
        )

    output = [f'#EXTM3U x-tvg-url="{EPG_URL}" url-tvg="{EPG_URL}"']
    for channel_id, item in sorted(chosen.items(), key=lambda pair: (pair[1]["group"], pair[1]["title"].casefold())):
        extinf = clean_extinf(item["extinf"], channel_id, item["group"], item["title"])
        output.extend((extinf, item["url"]))

    # Favourites are a convenience category; the main channel entries remain
    # unique and are not duplicated in different quality variants.
    for channel_id, item in sorted(chosen.items(), key=lambda pair: pair[1]["title"].casefold()):
        if item["title"] in FAVOURITES:
            output.extend((
                clean_extinf(item["extinf"], channel_id, "Избранное", item["title"]),
                item["url"],
            ))

    OUTPUT.write_text("\n".join(output) + "\n", encoding="utf-8")
    print(
        f"Generated {len(chosen)} unique channels "
        f"({len(output[1:]) // 2} playlist entries); "
        f"skipped without metadata: {skipped_no_metadata}; "
        f"skipped by area/time zone: {skipped_time_area}; "
        f"at {datetime.now(timezone.utc).isoformat()}"
    )

if __name__ == "__main__":
    main()
