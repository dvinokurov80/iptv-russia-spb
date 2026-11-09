#!/usr/bin/env python3
"""Build a broad free-to-access Russian IPTV playlist for Moscow time."""

from urllib.request import Request, urlopen
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
import gzip
import json
import re
import xml.etree.ElementTree as ET

SOURCES = {
    "national": "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "ntv": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/ru_ntv.m3u",
    "community_ngrch": "https://raw.githubusercontent.com/ngrch/iptv/main/ru.m3u",
    "community_gdydg": "https://raw.githubusercontent.com/gdydg/iptv/main/ru.m3u",
    "spb": "https://iptv-org.github.io/iptv/subdivisions/ru-spe.m3u",
    "len": "https://iptv-org.github.io/iptv/subdivisions/ru-len.m3u",
}
API = "https://iptv-org.github.io/api"
EPG_URLS = (
    "http://epg.one/epg2.xml.gz",
    "http://epg.one/ru2.xml.gz",
    "http://download.teleguide.su/download/new3/xmltv.xml.gz",
    "http://programtv.ru/xmltv.xml.gz",
)
EPG_URL = EPG_URLS[0]
OUTPUT = Path("playlist.m3u")

# Only nationwide feeds and feeds explicitly assigned to Saint Petersburg
# or Leningrad Oblast are accepted. Every accepted feed must be Moscow-time.
ALLOWED_AREAS = {"c/RU", "s/RU-SPE", "s/RU-LEN"}
REQUIRED_TIMEZONE = "Europe/Moscow"
MIN_UNIQUE_CHANNELS = 200

# Exclude known non-local regional channels, children's channels missed by
# imperfect source tags, and radio stations from this TV-only playlist.
EXCLUDED_CHANNEL_IDS = {
    "Kuban24.ru", "Tavriya.ru", "NTS.ru", "ZaTV.ru", "Istoki.ru",
    "ShayanTV.ru", "VestiFM.ru", "GovoritMoskva.ru", "RadioRossii.ru",
    "Moymir.ru", "Tonus.ru",
    # Other-region and foreign-language channels are outside this playlist.
    "360-podmoskovye", "360-podmoskovie", "360-news", "krym24", "moskva-24", "RTBalkan.ru",
    # Explicit non-SPB regional channels found in the published playlist.
    "360.ru", "360News.ru", "Crimea24.ru", "106",
}

# Additional verified names used only for matching XMLTV IDs.
EPG_NAME_ALIASES = {
    "BRIDGE.ru": ["Bridge TV"],
    "BRIDGEHits.ru": ["Bridge TV Hits", "Bridge TV Dance"],
    "OTR.ru": ["ОТР", "ОТР HD"],
}

NAME_OVERRIDES = {
    "Ani.ru": "Ani",
    "Nauka.ru": "Наука 2.0",
    "InvestigationDiscovery.ru": "Investigation Discovery",
    "BabyTime.ru": "Бэби Тайм",
    "GulliGirl.ru": "Гулли Гёрл",
    "CartoonClassics.ru": "Картун Классикс",
    "15PlusMusic.ru": "15+ Music",
    "BRIDGE.ru": "BRIDGE TV",
    "BRIDGEDeluxe.ru": "BRIDGE DELUXE",
    "BRIDGEClassic.ru": "BRIDGE CLASSIC",
    "BRIDGERock.ru": "BRIDGE ROCK",
    "BRIDGEHits.ru": "BRIDGE HITS",
    "VIVARussia.ru": "VIVA Russia",
    "DanceHits80.ru": "DanceHits80",
    "EuropaPlusTV.ru": "Europa Plus TV",
    "ClassicMusic.ru": "Classic Music",
    "LiveMusicChannel.ru": "Лайв Мьюзик Ченнел HD",
    "RussianMusicBox.ru": "Russian Music Box",
    "SONGTVRussia.ru": "SONG TV Russia",
    "Domashniy.ru": "Домашний",
    "Zoopark.ru": "Зоопарк",
    "red.ru": ".red",
    "Nickelodeon.ru": "Nickelodeon",
    "STSkids.ru": "СТС Kids",
    "NationalGeographicWild.ru": "National Geographic Wild",
    "NationalGeographic.ru": "National Geographic",
    "MuzykaLive.ru": "Музыка Live",
    "StranaFM.ru": "Страна FM",
    "FONMusic.ru": "FON MUSIC",
    "VestiFM.ru": "Вести FM",
    "RTDocumentary.ru": "RTД",
    "OkkoPrajmSport.ru": "Okko Прайм Спорт",
    "OkkoFutbol.ru": "Okko Футбол",
    "Bollywood.ru": "Болливуд HD",
}

FAVOURITES = {
    "Первый канал", "Россия 1", "НТВ", "СТС", "ТНТ",
    "Пятница!", "Россия 24",
}

# Categories are deliberately broad and user-facing. General federal and
# nationwide broadcasters are grouped together; thematic channels get their
# own category before the general fallback.
CATEGORY_NAMES = {
    # The playlist uses fewer, clearer groups than the upstream database.
    "news": "Общероссийские",
    "general": "Общероссийские",
    "music": "Музыка",
    "movies": "Фильмы и сериалы",
    "series": "Фильмы и сериалы",
    "documentary": "Познавательные",
    "education": "Познавательные",
    "science": "Познавательные",
    "culture": "Познавательные",
    "travel": "Познавательные",
    "outdoor": "Познавательные",
    "lifestyle": "Развлекательные",
    "auto": "Познавательные",
    "cooking": "Познавательные",
    "entertainment": "Развлекательные",
    "comedy": "Развлекательные",
    "family": "Развлекательные",
}

CATEGORY_OVERRIDES = {
    # Editorial overrides for channels whose upstream tags are too generic.
    "T24.ru": "Познавательные",
    "Nauka.ru": "Познавательные",
    "NationalGeographic.ru": "Познавательные",
    "NationalGeographicWild.ru": "Познавательные",
    "InvestigationDiscovery.ru": "Познавательные",
    "300105": "Познавательные",  # Наука 2.0
    "NTVHit.ru": "Фильмы и сериалы",
    "NTVSeries.ru": "Фильмы и сериалы",
    "NTVStyle.ru": "Развлекательные",
}

CHILD_NAME_RE = re.compile(
    r"(детск|мульт|baby|tiji|nickelodeon|cartoon|kids|junior|"
    r"капитан фантастика|супергеро|рыжий|карусел)", re.I
)
RELIGIOUS_NAME_RE = re.compile(
    r"(^|[^а-я])(спас|союз|вера|надежда|православ|христиан|"
    r"миллет|ислам|мусульман)([^а-я]|$)", re.I
)
SPORTS_NAME_RE = re.compile(
    r"(спорт|sport|матч!?|футбол|хоккей|баскетбол|теннис|"
    r"бокс|единоборств|автоспорт|гонк|олимп|арсенал)", re.I
)
FILM_SERIES_NAME_RE = re.compile(
    r"(кино|кинозал|киносери|фильм|сериал|серия|cinema|movie|film|series|serial)", re.I
)
EDUCATIONAL_NAME_RE = re.compile(
    r"(наук|science|discovery|географ|geographic|travel|путешеств|"
    r"adventure|рыбал|рыболов|fishing|охот|hunting|природ|nature|"
    r"животн|animal|океан|ocean|истори|history|планет|зоопарк|"
    r"зоо\s*тв|боб[её]р|усадьб|загородн|приключен|большая\s+азия)", re.I
)
NONLOCAL_REGION_NAME_RE = re.compile(
    r"(?:^|\s)(?:360(?:°)?(?:\s+новости)?|крым\s*24|москва\s*24)(?:$|\s)",
    re.I
)

def is_excluded_channel(channel, title):
    tags = set(channel.get("categories") or [])
    if channel.get("id") in EXCLUDED_CHANNEL_IDS:
        return True
    if NONLOCAL_REGION_NAME_RE.search(title or ""):
        return True
    if "radio" in tags:
        return True
    if "kids" in tags or CHILD_NAME_RE.search(title or ""):
        return True
    if "religious" in tags or RELIGIOUS_NAME_RE.search(title or ""):
        return True
    # The user asked to remove sports channels entirely, not merely hide
    # their former dedicated category.
    if "sports" in tags or SPORTS_NAME_RE.search(title or ""):
        return True
    return False

def download_text(url):
    request = Request(url, headers={"User-Agent": "iptv-russia-spb-playlist/2.0"})
    with urlopen(request, timeout=60) as response:
        data = response.read()
    # Some XMLTV servers gzip-compress the body even when the URL has no .gz suffix.
    if data.startswith(bytes((31, 139))):
        data = gzip.decompress(data)
    return data.decode("utf-8-sig", errors="replace")

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

def normalize_epg_name(value):
    value = re.sub(
        r"\s*(?:\b(?:HD|FHD|UHD|SD|4K)\b|\d{3,4}\s*[pi])\s*$",
        "", value or "", flags=re.I
    )
    return re.sub(r"[^0-9a-zа-яё]+", "", value.casefold())

def apply_epg_ids(chosen):
    """Match playlist channels to IDs with actual programmes in the Russian XMLTV guide."""
    try:
        global EPG_URL
        errors = []
        for source_url in EPG_URLS:
            try:
                candidate_root = ET.fromstring(download_text(source_url))
                candidate_ids = {
                    node.get("channel") for node in candidate_root.findall("programme")
                    if node.get("channel")
                }
                if not candidate_ids:
                    raise ValueError("XMLTV source contains no programme channel IDs")
                programme_starts = [
                    node.get("start", "").strip()
                    for node in candidate_root.findall("programme")
                    if node.get("start")
                ]
                offsets = {
                    match.group(1)
                    for value in programme_starts
                    for match in [re.search(r"([+-]\d{4})$", value)]
                    if match
                }
                if not programme_starts or offsets != {"+0300"}:
                    raise ValueError(
                        f"XMLTV timestamps are not consistently Moscow time (+0300): {sorted(offsets)}"
                    )
                if any(not re.search(r"[+-]\d{4}$", value) for value in programme_starts):
                    raise ValueError("XMLTV contains programme times without an explicit UTC offset")
                root = candidate_root
                programme_ids = candidate_ids
                EPG_URL = source_url
                break
            except Exception as source_error:
                errors.append(f"{source_url}: {source_error}")
        else:
            raise RuntimeError("All XMLTV sources failed: " + " | ".join(errors))
        print(f"EPG source selected: {EPG_URL}")
        names_by_id = {}
        display_names_by_id = {}
        for node in root.findall("channel"):
            epg_id = node.get("id")
            if not epg_id or epg_id not in programme_ids:
                continue
            display_names = [
                (child.text.strip(), child.attrib.get("lang", ""))
                for child in node.findall("display-name") if child.text and child.text.strip()
            ]
            if display_names:
                display_names_by_id[epg_id] = next(
                    (name for name, lang in display_names if lang == "ru"),
                    display_names[0][0]
                )
            for display_name, _lang in display_names:
                name = normalize_epg_name(display_name)
                if name:
                    names_by_id.setdefault(name, set()).add(epg_id)

        exact = 0
        by_name = 0
        unmatched = 0
        unmatched_details = []
        used_ids = set()
        alias_owners = {}
        for channel_id, item in chosen.items():
            aliases = item.get("epg_names") or [item.get("title", "")]
            normalized = {normalize_epg_name(alias) for alias in aliases}
            for name in normalized - {""}:
                alias_owners.setdefault(name, set()).add(channel_id)

        for channel_id, item in chosen.items():
            if channel_id in programme_ids:
                item["epg_id"] = channel_id
                item["epg_name"] = display_names_by_id.get(channel_id, item.get("title", ""))
                used_ids.add(channel_id)
                exact += 1

        for channel_id, item in chosen.items():
            if "epg_id" in item:
                continue
            aliases = item.get("epg_names") or [item.get("title", "")]
            candidates = set()
            for alias in aliases:
                name = normalize_epg_name(alias)
                if name and alias_owners.get(name) == {channel_id}:
                    candidates.update(names_by_id.get(name, set()))
            candidates -= used_ids
            if len(candidates) == 1:
                item["epg_id"] = next(iter(candidates))
                item["epg_name"] = display_names_by_id.get(item["epg_id"], item.get("title", ""))
                used_ids.add(item["epg_id"])
                by_name += 1
            else:
                item["epg_id"] = channel_id
                item["epg_name"] = item.get("title", "")
                unmatched += 1
                unmatched_details.append({
                    "id": channel_id,
                    "title": item.get("title", ""),
                    "aliases": aliases,
                })

        print(
            f"EPG matching: {exact} exact IDs, {by_name} unique alias matches, "
            f"{unmatched} without a confirmed programme ID "
            f"(guide contains {len(programme_ids)} channel IDs)"
        )
        if unmatched_details:
            print("EPG unmatched channel details:")
            for item in unmatched_details:
                print(
                    f"  {item['id']} | {item['title']} | "
                    f"{'; '.join(item['aliases'])}"
                )
    except Exception as error:
        raise RuntimeError(f"Unable to verify XMLTV coverage: {error}") from error

def has_cyrillic(value):
    return bool(re.search(r"[А-Яа-яЁё]", value or ""))

def display_name(extinf, metadata, channel):
    # Keep the broadcaster's original spelling. Prefer an established Cyrillic
    # alias when one exists; otherwise preserve the official Latin-script name.
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
    local_name = next((item for item in candidates if has_cyrillic(item)), None)
    if local_name:
        return local_name
    # No local-script name: use the broadcaster's canonical database name
    # rather than inventing a Russian transliteration.
    canonical = channel.get("name") or (candidates[0] if candidates else channel.get("id", ""))
    return remove_quality(canonical.strip())

def clean_extinf(extinf, channel_id, group, title, epg_id=None, epg_name=None):
    # Rebuild the attribute list instead of carrying arbitrary source attributes.
    # Some upstream entries contain malformed/unclosed http-user-agent quotes,
    # which can make IPTV players reject the entire M3U document.
    metadata = attrs(extinf)
    logo = metadata.get("tvg-logo", "")
    head = f'#EXTINF:-1 tvg-id="{epg_id or channel_id}"'
    if logo:
        head += f' tvg-logo="{logo}"'
    safe_tvg_name = (epg_name or title).replace('"', "").strip()
    head += f' tvg-name="{safe_tvg_name}"'
    head += f' group-title="{group}"'
    return head + "," + title

def probe_stream(url):
    """Return True for a responding stream, False for a clearly dead URL, None if inconclusive."""
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; IPTVPlaylistHealthCheck/1.0)",
        "Range": "bytes=0-2047",
    })
    try:
        with urlopen(request, timeout=7) as response:
            status = getattr(response, "status", 200)
            sample = response.read(2048)
            if status in (404, 410, 451):
                return False
            if ".m3u8" in url.lower() and sample:
                prefix = sample.lstrip()[:256].lower()
                if prefix.startswith((b"<!doctype html", b"<html", b"<head")):
                    return False
                if not sample.lstrip().startswith(b"#EXTM3U"):
                    return None
            return True
    except HTTPError as error:
        if error.code in (404, 410, 451):
            return False
        return None
    except (TimeoutError, URLError, OSError):
        return None


def choose_healthy_streams(candidates_by_channel):
    """Check each channel's best stream first; probe fallbacks only when needed."""
    ranked_by_channel = {}
    for channel_id, items in candidates_by_channel.items():
        unique = {}
        for item in items:
            previous = unique.get(item["url"])
            if previous is None or item["score"] > previous["score"]:
                unique[item["url"]] = item
        ranked_by_channel[channel_id] = sorted(
            unique.values(), key=lambda item: item["score"], reverse=True
        )

    chosen = {}
    uncertain = {}
    active = ranked_by_channel
    confirmed = unknown = removed = 0

    while active:
        batch = {channel_id: items[0] for channel_id, items in active.items()}
        checks = {}
        with ThreadPoolExecutor(max_workers=32) as pool:
            futures = {
                pool.submit(probe_stream, item["url"]): channel_id
                for channel_id, item in batch.items()
            }
            for future in as_completed(futures):
                channel_id = futures[future]
                try:
                    checks[channel_id] = future.result()
                except Exception:
                    checks[channel_id] = None

        next_active = {}
        for channel_id, items in active.items():
            item = items[0]
            result = checks.get(channel_id)
            if result is True:
                chosen[channel_id] = item
                confirmed += 1
                continue
            if result is None and channel_id not in uncertain:
                uncertain[channel_id] = item
            if len(items) > 1:
                next_active[channel_id] = items[1:]
            elif channel_id in uncertain:
                chosen[channel_id] = uncertain[channel_id]
                unknown += 1
            else:
                removed += 1
        active = next_active

    print(
        f"Stream check: {confirmed} confirmed responding, "
        f"{unknown} inconclusive retained, {removed} channels removed"
    )
    return chosen

def quality_score(value):
    match = re.search(r'(\d{3,4})\s*([pi])?', str(value or "").lower())
    if not match:
        return 0
    height = int(match.group(1))
    progressive = 1 if match.group(2) == "p" else 0
    return height * 10 + progressive

def classify(channel_id, categories, feed, title=""):
    # Local-only feeds are kept separate from nationwide channels.
    areas = set(feed.get("broadcast_area") or [])
    if "c/RU" not in areas and areas & {"s/RU-SPE", "s/RU-LEN"}:
        return "Санкт-Петербург и Ленобласть"

    # Explicit editorial corrections take priority over imperfect source tags.
    if channel_id in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[channel_id]

    tags = set(categories or [])
    # Names catch film/series channels whose source tags are missing or wrong.
    if tags & {"movies", "series"} or FILM_SERIES_NAME_RE.search(title or ""):
        return "Фильмы и сериалы"
    if (tags & {"documentary", "education", "science", "culture", "travel",
                "outdoor", "auto", "cooking"}
            or EDUCATIONAL_NAME_RE.search(title or "")):
        return "Познавательные"
    if tags & {"music"}:
        return "Музыка"
    if tags & {"entertainment", "comedy", "family", "lifestyle"}:
        return "Развлекательные"

    # Keep the category list compact: uncategorized regional channels,
    # including ЛенТВ24, go into the same general/news group as federal
    # broadcasters rather than creating a one-channel regional category.
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
    candidates_by_channel = {}
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

        group = classify(channel_id, channel.get("categories"), feed, title)
        score = quality_score(feed.get("format"))
        epg_names = [
            title, meta.get("tvg-name", ""), extinf.rsplit(",", 1)[-1].strip(),
            channel.get("name", ""), channel.get("native_name", ""),
        ] + (channel.get("alt_names") or []) + EPG_NAME_ALIASES.get(channel_id, [])
        candidate = {
            "channel_id": channel_id,
            "extinf": extinf,
            "url": url,
            "title": title,
            "epg_names": list(dict.fromkeys(name for name in epg_names if name)),
            "group": group,
            "score": score,
            "main": bool(feed.get("is_main")),
            "area": set(feed.get("broadcast_area") or []),
        }
        candidates_by_channel.setdefault(channel_id, []).append(candidate)

    chosen = choose_healthy_streams(candidates_by_channel)

    # NTV Style is confirmed as a Russian, Moscow-time channel with an
    # official NTV CDN stream, but some upstream playlist mirrors omit tvg-id.
    # Keep this single reviewed fallback so it is not lost during discovery.
    if "NTVStyle.ru" not in chosen:
        chosen["NTVStyle.ru"] = {
            "channel_id": "NTVStyle.ru",
            "extinf": '#EXTINF:-1 tvg-id="NTVStyle.ru@SD",НТВ Стиль',
            "url": "https://cdn.ntv.ru/th_style/playlist.m3u8",
            "title": "НТВ Стиль",
            "epg_names": ["НТВ Стиль", "NTV Style", "NTV Style HD"],
            "group": "Развлекательные",
            "score": 5760,
            "main": True,
            "area": {"c/RU"},
        }

    apply_epg_ids(chosen)

    if len(chosen) < MIN_UNIQUE_CHANNELS:
        raise RuntimeError(
            f"Only {len(chosen)} unique eligible channels found; refusing to replace "
            f"the published playlist. Check source metadata and Moscow-time filters. "
            f"Skipped for area/time-zone mismatch: {skipped_time_area}."
        )

    output = [f'#EXTM3U x-tvg-url="{EPG_URL}" url-tvg="{EPG_URL}"']
    for channel_id, item in sorted(chosen.items(), key=lambda pair: (pair[1]["group"], pair[1]["title"].casefold())):
        extinf = clean_extinf(
            item["extinf"], channel_id, item["group"], item["title"],
            item.get("epg_id"), item.get("epg_name")
        )
        output.extend((extinf, item["url"]))

    # Favourites are a convenience category; the main channel entries remain
    # unique and are not duplicated in different quality variants.
    for channel_id, item in sorted(chosen.items(), key=lambda pair: pair[1]["title"].casefold()):
        if item["title"] in FAVOURITES:
            output.extend((
                clean_extinf(
                    item["extinf"], channel_id, "Избранное", item["title"],
                    item.get("epg_id"), item.get("epg_name")
                ),
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
