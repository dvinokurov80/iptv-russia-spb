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

NAME_OVERRIDES = {
    "Ani.ru": "Ани",
    "Nauka.ru": "Наука 2.0",
    "InvestigationDiscovery.ru": "Investigation Discovery",
    "BabyTime.ru": "Бэби Тайм",
    "GulliGirl.ru": "Гулли Гёрл",
    "CartoonClassics.ru": "Картун Классикс",
    "15PlusMusic.ru": "15+ Музыка",
    "BRIDGE.ru": "Бридж",
    "BRIDGEDeluxe.ru": "Бридж Делюкс",
    "BRIDGEClassic.ru": "Бридж Классик",
    "BRIDGERock.ru": "Бридж Рок",
    "BRIDGEHits.ru": "Бридж Хитс",
    "VIVARussia.ru": "Вива Россия",
    "DanceHits80.ru": "Дэнс Хитс 80",
    "EuropaPlusTV.ru": "Европа Плюс ТВ",
    "ClassicMusic.ru": "Классическая музыка",
    "LiveMusicChannel.ru": "Лайв Мьюзик Ченнел HD",
    "RussianMusicBox.ru": "Русский Музыкальный Бокс",
    "SONGTVRussia.ru": "Сонг ТВ Россия",
    "Domashniy.ru": "Домашний",
    "Zoopark.ru": "Зоопарк",
    "red.ru": "Ред HD",
    "Nickelodeon.ru": "Nickelodeon Россия",
    "STSkids.ru": "СТС Kids",
    "NationalGeographicWild.ru": "National Geographic Wild Россия",
    "NationalGeographic.ru": "National Geographic Россия",
    "MuzykaLive.ru": "Музыка Live",
    "StranaFM.ru": "Страна FM",
    "FONMusic.ru": "ТНТ Music",
    "VestiFM.ru": "Вести FM",
    "RTDocumentary.ru": "RTД",
    "OkkoPrajmSport.ru": "Okko Прайм Спорт",
    "OkkoFutbol.ru": "Okko Футбол",
    "Bollywood.ru": "Болливуд HD",
}

FAVOURITES = {
    "Первый канал", "Россия 1", "НТВ", "СТС", "ТНТ",
    "Пятница!", "Россия 24", "Матч ТВ",
}

# Categories are deliberately broad and user-facing. General federal and
# nationwide broadcasters are grouped together; thematic channels get their
# own category before the general fallback.
CATEGORY_NAMES = {
    "news": "Общероссийские",
    "sports": "Спорт",
    "music": "Музыка",
    "movies": "Фильмы",
    "series": "Сериалы",
    "documentary": "Познавательные",
    "education": "Познавательные",
    "science": "Познавательные",
    "culture": "Познавательные",
    "travel": "Познавательные",
    "outdoor": "Познавательные",
    "lifestyle": "Познавательные",
    "auto": "Познавательные",
    "cooking": "Познавательные",
    "general": "Общероссийские",
    "entertainment": "Общероссийские",
}

CATEGORY_OVERRIDES = {
    "T24.ru": "Познавательные",
    "Nauka.ru": "Познавательные",
    "NationalGeographic.ru": "Познавательные",
    "NationalGeographicWild.ru": "Познавательные",
    "InvestigationDiscovery.ru": "Познавательные",
}

CHILD_NAME_RE = re.compile(
    r"(детск|мульт|baby|tiji|nickelodeon|cartoon|kids|junior|"
    r"капитан фантастика|супергеро|рыжий|карусел)", re.I
)
RELIGIOUS_NAME_RE = re.compile(
    r"(^|[^а-я])(спас|союз|вера|надежда|православ|христиан|"
    r"миллет|ислам|мусульман)([^а-я]|$)", re.I
)

def is_excluded_channel(channel, title):
    tags = set(channel.get("categories") or [])
    if "kids" in tags or CHILD_NAME_RE.search(title or ""):
        return True
    if "religious" in tags or RELIGIOUS_NAME_RE.search(title or ""):
        return True
    return False

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

def has_cyrillic(value):
    return bool(re.search(r"[А-Яа-яЁё]", value or ""))

def transliterate_to_russian(value):
    # Fallback only when the maintained channel metadata has no Russian alias.
    text = (value or "").strip()
    replacements = [
        ("shch", "щ"), ("sch", "щ"), ("yo", "ё"), ("zh", "ж"),
        ("kh", "х"), ("ts", "ц"), ("ch", "ч"), ("sh", "ш"),
        ("yu", "ю"), ("ya", "я"), ("ye", "е"),
    ]
    lower = text.lower()
    for source, target in replacements:
        lower = lower.replace(source, target)
    table = str.maketrans({
        "a":"а", "b":"б", "c":"к", "d":"д", "e":"е", "f":"ф",
        "g":"г", "h":"х", "i":"и", "j":"дж", "k":"к", "l":"л",
        "m":"м", "n":"н", "o":"о", "p":"п", "q":"к", "r":"р",
        "s":"с", "t":"т", "u":"у", "v":"в", "w":"в", "x":"кс",
        "y":"ы", "z":"з",
    })
    return lower.translate(table)

def display_name(extinf, metadata, channel):
    candidates = []
    native = channel.get("native_name")
    if native:
        candidates.append(native)
    candidates.extend(channel.get("alt_names") or [])
    candidates.extend([
        metadata.get("tvg-name") or "",
        extinf.rsplit(",", 1)[-1].strip(),
        channel.get("name") or "",
    ])

    def remove_quality(value):
        value = re.sub(r"\s*[\[(](?:\d{3,4}\s*[pi]|4k|uhd|fhd|hd|sd)[\])]\s*$", "", value, flags=re.I)
        return re.sub(r"\s+\d{3,4}\s*[pi]\s*$", "", value, flags=re.I).strip()

    candidates = [remove_quality(item.strip()) for item in candidates if item and item.strip()]
    title = next((item for item in candidates if has_cyrillic(item)), "")
    if not title:
        title = next(iter(candidates), channel.get("id", ""))
        title = transliterate_to_russian(title).title()
        # Restore common broadcast abbreviations after transliteration.
        title = re.sub(r"\bТв\b", "ТВ", title)
        title = re.sub(r"\bХд\b", "HD", title)
        title = re.sub(r"\bФм\b", "FM", title)
        title = re.sub(r"\bРу\b", "RU", title)
    return remove_quality(title)

def clean_extinf(extinf, channel_id, group, title):
    # Rebuild the attribute list instead of carrying arbitrary source attributes.
    # Some upstream entries contain malformed/unclosed http-user-agent quotes,
    # which can make IPTV players reject the entire M3U document.
    metadata = attrs(extinf)
    logo = metadata.get("tvg-logo", "")
    head = f'#EXTINF:-1 tvg-id="{channel_id}"'
    if logo:
        head += f' tvg-logo="{logo}"'
    head += f' group-title="{group}"'
    return head + "," + title

def quality_score(value):
    match = re.search(r'(\d{3,4})\s*([pi])?', str(value or "").lower())
    if not match:
        return 0
    height = int(match.group(1))
    progressive = 1 if match.group(2) == "p" else 0
    return height * 10 + progressive

def classify(channel_id, categories, feed):
    # Explicit editorial corrections take priority over imperfect source tags.
    if channel_id in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[channel_id]

    tags = set(categories or [])
    if "movies" in tags:
        return "Фильмы"
    if tags & {"documentary", "education", "science", "culture", "travel",
               "outdoor", "lifestyle", "auto", "cooking"}:
        return "Познавательные"
    if "sports" in tags:
        return "Спорт"
    if "music" in tags:
        return "Музыка"
    if "series" in tags:
        return "Сериалы"

    areas = set(feed.get("broadcast_area") or [])
    if not (areas & {"c/RU"}):
        return "Санкт-Петербург и Ленобласть"
    # News, general and entertainment broadcasters with nationwide coverage
    # belong together instead of being scattered across miscellaneous groups.
    return "Общероссийские"

def feed_allowed(feed):
    areas = set(feed.get("broadcast_area") or [])
    zones = set(feed.get("timezones") or [])
    languages = set(feed.get("languages") or [])
    # Reject multi-time-zone feeds and feeds whose only declared language is Ukrainian.
    language_ok = not ("ukr" in languages and "rus" not in languages)
    return bool(areas & ALLOWED_AREAS) and zones == {REQUIRED_TIMEZONE} and language_ok

def guide_allowed(channel_id, guide_languages):
    langs = guide_languages.get(channel_id, set())
    # Exclude guides explicitly marked Ukrainian unless a Russian guide is also
    # available. English/other guide metadata alone is not evidence of Ukrainian EPG.
    has_ukrainian = bool(langs & {"uk", "ukr"})
    has_russian = "ru" in langs
    return not has_ukrainian or has_russian

def main():
    channels = {item["id"]: item for item in download_json("channels")}
    feeds_data = download_json("feeds")
    guides_data = download_json("guides")
    guide_languages = {}
    for guide in guides_data:
        channel_id = guide.get("channel")
        language = guide.get("lang")
        if channel_id and language:
            guide_languages.setdefault(channel_id, set()).add(language)
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
        if (not channel or channel.get("country") != "RU"
                or channel.get("is_nsfw") or channel.get("closed")):
            skipped_no_metadata += 1
            continue
        if not guide_allowed(channel_id, guide_languages):
            skipped_time_area += 1
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
        title = NAME_OVERRIDES.get(channel_id) or display_name(extinf, meta, channel)
        if not title:
            title = channel.get("name") or channel_id

        # Remove children's and religious channels entirely, not just their tags.
        if is_excluded_channel(channel, title):
            continue

        group = classify(channel_id, channel.get("categories"), feed)
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
