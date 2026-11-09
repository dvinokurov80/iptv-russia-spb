#!/usr/bin/env python3
"""Build a broad free-to-access Russian IPTV playlist for Moscow time."""

from urllib.request import Request, urlopen
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
import gzip
import json
import re
import time
from http.client import IncompleteRead, RemoteDisconnected
import xml.etree.ElementTree as ET

SOURCES = {
    "national": "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "ntv": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/ru_ntv.m3u",
    "iptv_org_streams": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/ru.m3u",
    "community_ngrch": "https://raw.githubusercontent.com/ngrch/iptv/main/ru.m3u",
    "community_gdydg": "https://raw.githubusercontent.com/gdydg/iptv/main/ru.m3u",
    "spb": "https://iptv-org.github.io/iptv/subdivisions/ru-spe.m3u",
    "len": "https://iptv-org.github.io/iptv/subdivisions/ru-len.m3u",
}
API = "https://iptv-org.github.io/api"
EPG_URLS = (
    "http://epg.one/epg2.xml.gz",
    "http://epg.one/ru2.xml.gz",
    "https://iptvx.one/EPG_LITE",
    "https://iptvx.one/epg/epg.xml.gz",
    "http://download.teleguide.su/download/new3/xmltv.xml.gz",
    "http://programtv.ru/xmltv.xml.gz",
)
EPG_URL = "https://raw.githubusercontent.com/dvinokurov80/iptv-russia-spb/main/epg.xml.gz"
EPG_OUTPUT = Path("epg.xml.gz")
OUTPUT = Path("playlist.m3u")

# Only nationwide feeds and feeds explicitly assigned to Saint Petersburg
# or Leningrad Oblast are accepted. Every accepted feed must be Moscow-time.
ALLOWED_AREAS = {"c/RU", "s/RU-SPE", "s/RU-LEN"}
REQUIRED_TIMEZONE = "Europe/Moscow"
MIN_UNIQUE_CHANNELS = 100

# Exclude known non-local regional channels, children's channels missed by
# imperfect source tags, and radio stations from this TV-only playlist.
EXCLUDED_CHANNEL_IDS = {
    "Kuban24.ru", "Tavriya.ru", "NTS.ru", "ZaTV.ru", "Istoki.ru",
    "ShayanTV.ru", "VestiFM.ru", "GovoritMoskva.ru", "RadioRossii.ru",
    "Moymir.ru", "Tonus.ru", "StranaFM.ru",
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

# NGRCH explicitly separates the base nationwide feeds from regional
# time-shifted variants (+2/+4/+7). Match its Russian names to iptv-org
# channel records; the first unshifted entry is the preferred Moscow feed.
NGRCH_NAME_ALIASES = {
    "первый": ["первый канал", "channel one", "1tv"],
    "россия 1": ["россия1", "russia 1", "rossiya 1"],
    "матч!": ["матч", "match tv"],
    "пятый": ["пятый канал", "5 канал", "пятый"],
    "россия к": ["культура", "россия культура"],
    "россия 24": ["россия24", "russia 24"],
    "тв центр": ["твц", "tv center", "tvc"],
    "рен тв": ["рентв", "ren tv"],
    "тв3": ["тв 3", "tv3"],
    "пятница": ["пятница!"],
}

NAME_OVERRIDES = {
    "ChannelOne.ru": "Первый канал",
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
    if re.search(r"(?:радио|radio|\bFM\b)$", title or "", re.I):
        return True
    if "music" in tags:
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
    request = Request(url, headers={"User-Agent": "iptv-russia-spb-playlist/2.1"})
    last_error = None
    # Large XMLTV feeds sometimes terminate the connection mid-download.
    # Retry transient transport and server errors before marking a source unavailable.
    for attempt in range(4):
        try:
            with urlopen(request, timeout=90) as response:
                data = response.read()
            # Some XMLTV servers gzip-compress the body even when the URL has no .gz suffix.
            if data.startswith(bytes((31, 139))):
                data = gzip.decompress(data)
            return data.decode("utf-8-sig", errors="replace")
        except HTTPError as error:
            last_error = error
            if error.code not in (408, 425, 429, 500, 502, 503, 504):
                raise
        except (URLError, TimeoutError, ConnectionError, OSError,
                IncompleteRead, RemoteDisconnected, EOFError) as error:
            last_error = error
        if attempt < 3:
            delay = 2 ** (attempt + 1)
            print(f"Download retry {attempt + 1}/3 in {delay}s: {url}: {last_error}")
            time.sleep(delay)
    raise RuntimeError(f"Download failed after 4 attempts: {url}: {last_error}")

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

def parse_xmltv_datetime(value):
    """Parse an XMLTV timestamp with an explicit numeric UTC offset."""
    value = (value or "").strip()
    if not re.fullmatch(r"\d{14}\s+[+-]\d{4}", value):
        raise ValueError(f"Unsupported XMLTV timestamp: {value!r}")
    return datetime.strptime(value, "%Y%m%d%H%M%S %z")

def format_moscow_xmltv(value):
    """Convert an aware datetime to fixed Moscow time (UTC+03:00)."""
    return value.astimezone(timezone(timedelta(hours=3))).strftime("%Y%m%d%H%M%S %z")

def build_merged_epg(chosen):
    """Merge XMLTV feeds, normalize timestamps and prefer source order on conflicts."""
    errors = []
    channels_by_id = {}
    alias_to_id = {}
    ambiguous_aliases = set()
    programme_candidates_by_id = {}
    programmes_by_id = {}
    now_utc = datetime.now(timezone.utc)
    window_start = now_utc - timedelta(hours=6)
    window_end = now_utc + timedelta(days=8)
    loaded_sources = 0

    for source_index, source_url in enumerate(EPG_URLS):
        try:
            source_root = ET.fromstring(download_text(source_url))
            source_channels = {
                node.get("id"): node for node in source_root.findall("channel") if node.get("id")
            }
            source_programmes = source_root.findall("programme")
            if not source_channels or not source_programmes:
                raise ValueError("XMLTV feed has no channels or programmes")

            source_id_map = {}
            for old_id, node in source_channels.items():
                names = [
                    child.text.strip() for child in node.findall("display-name")
                    if child.text and child.text.strip()
                ]
                normalized = {
                    normalize_epg_name(name) for name in names
                    if len(normalize_epg_name(name)) >= 4
                }
                matches = {
                    alias_to_id[name] for name in normalized
                    if name in alias_to_id and name not in ambiguous_aliases
                }
                if len(matches) == 1:
                    canonical_id = next(iter(matches))
                else:
                    # XMLTV channel IDs are not globally unique across providers.
                    # Never merge two sources merely because their opaque IDs collide.
                    canonical_id = old_id
                    if canonical_id in channels_by_id:
                        canonical_id = f"xmltv{source_index}_{old_id}"

                source_id_map[old_id] = canonical_id
                if canonical_id not in channels_by_id:
                    channel_copy = ET.fromstring(ET.tostring(node, encoding="utf-8"))
                    channel_copy.set("id", canonical_id)
                    channels_by_id[canonical_id] = channel_copy
                else:
                    existing = channels_by_id[canonical_id]
                    existing_names = {
                        (child.text or "").strip().casefold()
                        for child in existing.findall("display-name")
                    }
                    for child in node.findall("display-name"):
                        name = (child.text or "").strip()
                        if name and name.casefold() not in existing_names:
                            existing.append(ET.fromstring(ET.tostring(child, encoding="utf-8")))
                            existing_names.add(name.casefold())

                for name in normalized:
                    if name in ambiguous_aliases:
                        continue
                    if name not in alias_to_id:
                        alias_to_id[name] = canonical_id
                    elif alias_to_id[name] != canonical_id:
                        alias_to_id.pop(name, None)
                        ambiguous_aliases.add(name)

            accepted = 0
            for programme in source_programmes:
                canonical_id = source_id_map.get(programme.get("channel"))
                if not canonical_id:
                    continue
                try:
                    start_dt = parse_xmltv_datetime(programme.get("start"))
                    stop_dt = parse_xmltv_datetime(programme.get("stop"))
                except Exception:
                    continue
                start_utc = start_dt.astimezone(timezone.utc)
                stop_utc = stop_dt.astimezone(timezone.utc)
                if stop_utc <= window_start or start_utc >= window_end or stop_utc <= start_utc:
                    continue

                copied = ET.fromstring(ET.tostring(programme, encoding="utf-8"))
                copied.set("channel", canonical_id)
                copied.set("start", format_moscow_xmltv(start_dt))
                copied.set("stop", format_moscow_xmltv(stop_dt))
                programme_candidates_by_id.setdefault(canonical_id, []).append(
                    (source_index, start_utc, stop_utc, copied)
                )
                accepted += 1

            loaded_sources += 1
            print(
                f"EPG source loaded: {source_url}; channels={len(source_channels)}, "
                f"programmes accepted={accepted}"
            )
        except Exception as error:
            errors.append(f"{source_url}: {error}")
            print(f"EPG source skipped: {source_url}: {error}")

    # Resolve overlaps efficiently after loading all sources. Sources are
    # processed in configured priority order; later guides fill only gaps.
    for channel_id, candidates in programme_candidates_by_id.items():
        accepted_intervals = []
        accepted_programmes = []
        source_indexes = sorted({entry[0] for entry in candidates})
        for source_index in source_indexes:
            current = sorted(
                (entry for entry in candidates if entry[0] == source_index),
                key=lambda entry: (entry[1], entry[2])
            )
            new_intervals = []
            last_source_stop = None
            existing_index = 0
            for _source, start_utc, stop_utc, copied in current:
                if last_source_stop is not None and start_utc < last_source_stop:
                    last_source_stop = max(last_source_stop, stop_utc)
                    continue
                last_source_stop = stop_utc
                while (
                    existing_index < len(accepted_intervals)
                    and accepted_intervals[existing_index][1] <= start_utc
                ):
                    existing_index += 1
                overlaps_existing = (
                    existing_index < len(accepted_intervals)
                    and accepted_intervals[existing_index][0] < stop_utc
                )
                if overlaps_existing:
                    continue
                new_intervals.append((start_utc, stop_utc))
                accepted_programmes.append(copied)

            # Both lists are sorted and non-overlapping; merge in linear time.
            merged = []
            left = right = 0
            while left < len(accepted_intervals) and right < len(new_intervals):
                if accepted_intervals[left][0] <= new_intervals[right][0]:
                    merged.append(accepted_intervals[left])
                    left += 1
                else:
                    merged.append(new_intervals[right])
                    right += 1
            merged.extend(accepted_intervals[left:])
            merged.extend(new_intervals[right:])
            accepted_intervals = merged
        programmes_by_id[channel_id] = accepted_programmes

    programme_ids = set(programmes_by_id)
    if not programme_ids:
        raise RuntimeError("No valid programmes could be merged: " + " | ".join(errors))

    names_by_id = {}
    display_names_by_id = {}
    for channel_id, node in channels_by_id.items():
        if channel_id not in programme_ids:
            continue
        names = [
            (child.text.strip(), child.attrib.get("lang", ""))
            for child in node.findall("display-name")
            if child.text and child.text.strip()
        ]
        if names:
            display_names_by_id[channel_id] = next(
                (name for name, lang in names if lang == "ru"), names[0][0]
            )
        for name, _lang in names:
            normalized = normalize_epg_name(name)
            if normalized:
                names_by_id.setdefault(normalized, set()).add(channel_id)

    exact = by_name = unmatched = 0
    unmatched_details = []
    used_ids = set()
    alias_owners = {}
    for channel_id, item in chosen.items():
        aliases = item.get("epg_names") or [item.get("title", "")]
        for alias in {normalize_epg_name(value) for value in aliases} - {""}:
            alias_owners.setdefault(alias, set()).add(channel_id)

    for channel_id, item in chosen.items():
        # Playlist source IDs and XMLTV IDs belong to different namespaces;
        # only a verified display-name match may assign a guide ID.
        aliases = item.get("epg_names") or [item.get("title", "")]
        normalized_aliases = {
            normalize_epg_name(alias) for alias in aliases
            if normalize_epg_name(alias) and alias_owners.get(normalize_epg_name(alias)) == {channel_id}
        }
        candidates = set()
        for name in normalized_aliases:
            candidates.update(names_by_id.get(name, set()))

        if len(candidates) == 1 and not (candidates & used_ids):
            epg_id = next(iter(candidates))
            matched_names = [
                child.text.strip()
                for child in channels_by_id[epg_id].findall("display-name")
                if child.text and normalize_epg_name(child.text) in normalized_aliases
            ]
            preferred = next(
                (name for name in matched_names if has_cyrillic(name)),
                matched_names[0] if matched_names else display_names_by_id.get(epg_id, item.get("title", ""))
            )
            item["epg_id"] = epg_id
            item["epg_name"] = preferred
            used_ids.add(epg_id)
            by_name += 1
        else:
            item.pop("epg_id", None)
            item["epg_name"] = item.get("title", "")
            unmatched += 1
            unmatched_details.append({
                "id": channel_id,
                "title": item.get("title", ""),
                "aliases": aliases,
                "candidate_epg_ids": sorted(candidates),
            })
            continue
    # Publish a compact guide for the playlist's matched channels only.
    matched_ids = {item["epg_id"] for item in chosen.values() if item.get("epg_id")}
    final_root = ET.Element(
        "tv", {"source-info-name": "Merged Russian XMLTV; Moscow time UTC+03:00"}
    )
    for channel_id in sorted(matched_ids):
        if channel_id in channels_by_id:
            final_root.append(channels_by_id[channel_id])
    final_programmes = 0
    for channel_id in sorted(matched_ids):
        for programme in sorted(programmes_by_id.get(channel_id, []), key=lambda node: node.get("start", "")):
            final_root.append(programme)
            final_programmes += 1

    xml_bytes = ET.tostring(final_root, encoding="utf-8", xml_declaration=True)
    EPG_OUTPUT.write_bytes(gzip.compress(xml_bytes, compresslevel=9, mtime=0))
    print(
        f"EPG merged: {loaded_sources}/{len(EPG_URLS)} sources; "
        f"{final_programmes} programmes for {len(matched_ids)} channels; "
        "all timestamps normalized to +0300"
    )
    print(
        f"EPG matching: {exact} exact IDs, {by_name} unique alias matches, "
        f"{unmatched} without a confirmed programme ID "
        f"(merged guide contains {len(programme_ids)} channel IDs)"
    )
    if unmatched_details:
        print("EPG unmatched channel details:")
        for item in unmatched_details:
            print(f"  {item['id']} | {item['title']} | {'; '.join(item['aliases'])}")

    global EPG_URL
    EPG_URL = "https://raw.githubusercontent.com/dvinokurov80/iptv-russia-spb/main/epg.xml.gz"

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
    head = f'#EXTINF:-1 tvg-id="{epg_id}"' if epg_id else "#EXTINF:-1"
    if logo:
        head += f' tvg-logo="{logo}"'
    safe_tvg_name = (epg_name or title).replace('"', "").strip()
    head += f' tvg-name="{safe_tvg_name}"'
    head += f' group-title="{group}"'
    return head + "," + title

def fetch_probe(url, byte_limit=262144, timeout=12):
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; IPTVPlaylistHealthCheck/2.0)",
        "Accept": "*/*",
        "Range": "bytes=0-" + str(byte_limit - 1),
    })
    with urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        if status < 200 or status >= 300:
            raise ValueError("HTTP " + str(status))
        return response.read(byte_limit), response.headers.get("Content-Type", "").lower()


def looks_like_media_segment(data):
    if not data or len(data) < 188:
        return False
    if data[0] == 0x47 and (len(data) < 377 or data[188] == 0x47):
        return True
    return any(box in data[:512] for box in (b"ftyp", b"styp", b"moof", b"mdat"))


def probe_hls(url):
    """Check a live HLS manifest and multiple recent media segments."""
    manifest, _content_type = fetch_probe(url, timeout=10)
    if not manifest.lstrip().startswith(b"#EXTM3U"):
        return False
    text = manifest.decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    variants = []
    for index, line in enumerate(lines[:-1]):
        if line.startswith("#EXT-X-STREAM-INF:"):
            uri = lines[index + 1]
            if not uri.startswith("#"):
                bandwidth = re.search(r"(?:AVERAGE-)?BANDWIDTH=(\d+)", line)
                variants.append((int(bandwidth.group(1)) if bandwidth else 0, urljoin(url, uri)))
    if variants:
        # Prefer the lowest advertised bitrate: it is less likely to stall
        # on constrained connections and is the safest default for a playlist.
        variant_url = sorted(variants)[0][1]
        manifest, _ = fetch_probe(variant_url, timeout=10)
        if not manifest.lstrip().startswith(b"#EXTM3U"):
            return False
        text = manifest.decode("utf-8", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
    else:
        variant_url = url

    segments = [
        line for line in lines
        if not line.startswith("#") and not line.startswith("data:")
    ]
    if not segments:
        return None

    recent = segments[-3:]
    valid = 0
    for segment_path in recent:
        try:
            segment, _ = fetch_probe(urljoin(variant_url, segment_path),
                                    byte_limit=65536, timeout=8)
            if looks_like_media_segment(segment):
                valid += 1
        except (HTTPError, TimeoutError, URLError, OSError, ValueError):
            continue

    # A single successful segment is too weak a signal for a live stream.
    # Accept one only when the manifest itself exposes just one segment.
    required = min(2, len(recent))
    return True if valid >= required else None


def probe_stream(url):
    """Check a media manifest and at least one segment where possible."""
    try:
        lower = url.lower().split("?", 1)[0]
        if lower.endswith(".mpd"):
            return None
        if lower.endswith(".m3u8"):
            return probe_hls(url)
        data, content_type = fetch_probe(url, byte_limit=65536)
        if data.lstrip().startswith(b"#EXTM3U"):
            return probe_hls(url)
        if looks_like_media_segment(data):
            return True
        if any(token in content_type for token in ("video/", "application/octet-stream")):
            return None
        return False
    except HTTPError as error:
        if error.code in (404, 410, 451):
            return False
        return None
    except (TimeoutError, URLError, OSError, ValueError):
        return None


def choose_healthy_streams(candidates_by_channel):
    """Prefer HLS and retain only streams with a segment-level confirmation."""
    ranked_by_channel = {}
    for channel_id, items in candidates_by_channel.items():
        unique = {}
        for item in items:
            previous = unique.get(item["url"])
            if previous is None or item["score"] > previous["score"]:
                unique[item["url"]] = item
        # If NGRCH supplied an explicitly unshifted base feed, do not
        # silently replace it with an unclassified mirror just because the
        # mirror advertises a higher resolution or responds faster.
        preferred = [item for item in unique.values() if item.get("preferred_moscow")]
        if preferred:
            unique = {item["url"]: item for item in preferred}
        def rank(item):
            url = item["url"].lower().split("?", 1)[0]
            protocol = 30000 if url.endswith(".m3u8") else (-30000 if url.endswith(".mpd") else 0)
            source_priority = (
                100000 if item.get("preferred_moscow")
                else 50000 if item.get("backup_moscow")
                else 0
            )
            return source_priority + protocol + item["score"]
        ranked_by_channel[channel_id] = sorted(unique.values(), key=rank, reverse=True)

    chosen = {}
    inconclusive_fallbacks = {}
    active = ranked_by_channel
    confirmed = removed = inconclusive = 0
    while active:
        batch = {channel_id: items[0] for channel_id, items in active.items()}
        checks = {}
        with ThreadPoolExecutor(max_workers=12) as pool:
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
            result = checks.get(channel_id)
            if result is True:
                chosen[channel_id] = items[0]
                confirmed += 1
            else:
                if result is None and channel_id not in inconclusive_fallbacks:
                    inconclusive_fallbacks[channel_id] = items[0]
                if len(items) > 1:
                    next_active[channel_id] = items[1:]
                else:
                    if result is None:
                        inconclusive += 1
                    removed += 1
        active = next_active

    # A timeout or unsupported container is not proof that a feed is dead.
    # Keep the best-ranked inconclusive candidate only when no alternative
    # for that channel passed or definitively failed all candidates.
    fallback_count = 0
    for channel_id, item in inconclusive_fallbacks.items():
        if channel_id not in chosen:
            chosen[channel_id] = item
            fallback_count += 1

    print(
        f"Stream checks: {confirmed} multi-segment confirmed, "
        f"{fallback_count} retained as inconclusive fallback, "
        f"{removed} removed after all candidates failed"
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
    ngrch_entries = []
    for label, url in SOURCES.items():
        parsed = list(parse_m3u(download_text(url)))
        print(f"Loaded {len(parsed)} entries from {label}")
        source_entries.extend(parsed)
        if label == "community_ngrch":
            ngrch_entries = parsed

    # Index canonical channel names and known Russian aliases. NGRCH uses
    # short ids such as "sts"/"tnt", so matching by its display name is safer
    # than treating those ids as iptv-org ids.
    def channel_name_key(value):
        return normalize_epg_name(re.sub(r"\s*\(\d+\)\s*$", "", value or ""))

    channel_name_index = {}
    for known_id, known_channel in channels.items():
        known_names = [
            known_channel.get("name", ""),
            known_channel.get("native_name", ""),
            * (known_channel.get("alt_names") or []),
            NAME_OVERRIDES.get(known_id, ""),
        ]
        for known_name in known_names:
            key = channel_name_key(known_name)
            if key:
                channel_name_index.setdefault(key, set()).add(known_id)
    for base_name, aliases in NGRCH_NAME_ALIASES.items():
        key = channel_name_key(base_name)
        matched = set(channel_name_index.get(key, set()))
        for alias in aliases:
            matched.update(channel_name_index.get(channel_name_key(alias), set()))
        if len(matched) == 1:
            channel_name_index[key] = matched

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
            "backup_moscow": bool(feed.get("is_main")),
            "area": set(feed.get("broadcast_area") or []),
        }
        candidates_by_channel.setdefault(channel_id, []).append(candidate)

    # Add the first unshifted NGRCH feed for each uniquely matched channel.
    # Explicit regional groups and labels (+1/+2/+3/+4/+5/+7) are never eligible.
    ngrch_preferred = {}
    for extinf, url in ngrch_entries:
        metadata = attrs(extinf)
        group_title = metadata.get("group-title", "").strip().casefold()
        raw_title = extinf.rsplit(",", 1)[-1].strip()
        base_title = re.sub(r"\s*\(\d+\)\s*$", "", raw_title).strip()
        if group_title != "основные":
            continue
        if re.search(r"\s[+-]\s?\d+\s*$", raw_title):
            continue
        if not url.startswith(("http://", "https://")):
            continue
        key = channel_name_key(base_title)
        matched_ids = channel_name_index.get(key, set())
        if len(matched_ids) != 1:
            continue
        channel_id = next(iter(matched_ids))
        channel = channels.get(channel_id)
        eligible = eligible_by_channel.get(channel_id, [])
        main_feeds = [feed for feed in eligible if feed.get("is_main")]
        if not channel or not main_feeds:
            continue
        title = NAME_OVERRIDES.get(channel_id) or display_name(extinf, metadata, channel)
        if not title or is_excluded_channel(channel, title):
            continue
        feed = main_feeds[0]
        epg_names = [
            title, metadata.get("tvg-name", ""), raw_title,
            channel.get("name", ""), channel.get("native_name", ""),
        ] + (channel.get("alt_names") or []) + EPG_NAME_ALIASES.get(channel_id, [])
        item = {
            "channel_id": channel_id,
            "extinf": extinf,
            "url": url,
            "title": title,
            "epg_names": list(dict.fromkeys(name for name in epg_names if name)),
            "group": classify(channel_id, channel.get("categories"), feed, title),
            "score": quality_score(feed.get("format")),
            "main": True,
            "area": set(feed.get("broadcast_area") or []),
            "preferred_moscow": True,
            "source": "NGRCH",
        }
        bucket = ngrch_preferred.setdefault(channel_id, [])
        if all(existing["url"] != url for existing in bucket):
            bucket.append(item)

    for channel_id, items in ngrch_preferred.items():
        # Keep independently hosted iptv-org feeds as possible backups. The
        # primary chooser still prefers the explicitly unshifted NGRCH base.
        merged = list(items)
        seen_urls = {item["url"] for item in merged}
        for candidate in candidates_by_channel.get(channel_id, []):
            if candidate.get("backup_moscow") and candidate["url"] not in seen_urls:
                merged.append(candidate)
                seen_urls.add(candidate["url"])
        candidates_by_channel[channel_id] = merged

    print(
        f"NGRCH unshifted Moscow-base feeds matched: "
        f"{sum(len(items) for items in ngrch_preferred.values())} URLs "
        f"for {len(ngrch_preferred)} channels; "
        "regional time-shift variants excluded"
    )

    chosen = choose_healthy_streams(candidates_by_channel)

    # Select a second independently hosted feed explicitly declared as a
    # Moscow-time main feed by its source metadata, or as an NGRCH base feed.
    backups = {}
    for channel_id, primary in chosen.items():
        primary_host = (urlsplit(primary["url"]).hostname or "").lower()
        alternatives = [
            item for item in candidates_by_channel.get(channel_id, [])
            if (item.get("preferred_moscow") or item.get("backup_moscow"))
            and item["url"] != primary["url"]
            and (urlsplit(item["url"]).hostname or "").lower() != primary_host
        ]
        for candidate in alternatives:
            if probe_stream(candidate["url"]) is True:
                backups[channel_id] = candidate
                break
    print(
        f"Independent Moscow-base backups confirmed: {len(backups)} "
        f"of {len(chosen)} channels"
    )

    # No manually injected URLs: every published stream must pass checks.

    build_merged_epg(chosen)
    for channel_id, backup in backups.items():
        primary = chosen.get(channel_id, {})
        if primary.get("epg_id"):
            backup["epg_id"] = primary["epg_id"]
            backup["epg_name"] = primary.get("epg_name", backup.get("title", ""))

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

    # Add a second source directly after each primary channel entry so the
    # player presents an immediately adjacent manual failover option.
    for channel_id, item in sorted(chosen.items(), key=lambda pair: (pair[1]["group"], pair[1]["title"].casefold())):
        backup = backups.get(channel_id)
        if not backup:
            continue
        extinf = clean_extinf(
            backup["extinf"], channel_id, backup["group"], backup["title"],
            backup.get("epg_id"), backup.get("epg_name")
        )
        output.extend((extinf, backup["url"]))

    # Favourites remain a separate convenience group.
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
