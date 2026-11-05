#!/usr/bin/env python3
"""Build a Russia + Saint Petersburg IPTV playlist from iptv-org public feeds."""

from urllib.request import Request, urlopen
from datetime import datetime, timezone
from pathlib import Path
import re

NATIONAL_URL = "https://iptv-org.github.io/iptv/countries/ru.m3u"
SPB_URL = "https://iptv-org.github.io/iptv/subdivisions/ru-spe.m3u"
EPG_URL = "https://epg.it999.ru/ru.xml.gz"
OUTPUT = Path("playlist.m3u")

# Deliberately curated federal channel names. Matching is conservative:
# only these channels are taken from the all-Russia feed.
FEDERAL_NAMES = {
    "первый канал": "Первый канал",
    "россия 1": "Россия 1",
    "россия 24": "Россия 24",
    "россия культура": "Россия Культура",
    "культура": "Россия Культура",
    "нтв": "НТВ",
    "рен тв": "РЕН ТВ",
    "рен-тв": "РЕН ТВ",
    "стс": "СТС",
    "тнт": "ТНТ",
    "пятница": "Пятница!",
    "домашний": "Домашний",
    "тв центр": "ТВ Центр",
    "твц": "ТВ Центр",
    "звезда": "Звезда",
    "отр": "ОТР",
    "спас": "Спас",
    "карусель": "Карусель",
    "мир": "Мир",
    "мир 24": "Мир 24",
    "рбк": "РБК",
    "пятый канал": "Пятый канал",
    "петербург 5 канал": "Пятый канал",
    "матч": "Матч ТВ",
    "матч тв": "Матч ТВ",
    "мужское женское": "Первый канал",
    "муз тв": "МУЗ-ТВ",
    "муз-тв": "МУЗ-ТВ",
    "тв 3": "ТВ-3",
    "тв-3": "ТВ-3",
}

BAD_MARKERS = ("[geo-blocked]", "[not 24/7]", "[offline]", "[broken]")

def download(url: str) -> str:
    req = Request(url, headers={"User-Agent": "iptv-russia-spb-playlist/1.0"})
    with urlopen(req, timeout=45) as response:
        return response.read().decode("utf-8-sig", errors="replace")

def parse_m3u(text: str):
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

def attrs(extinf: str):
    return dict(re.findall(r'([\w-]+)="([^"]*)"', extinf))

def clean_name(extinf: str, metadata: dict) -> str:
    return metadata.get("tvg-name") or extinf.rsplit(",", 1)[-1].strip()

def normalized(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"\[[^]]*\]", "", value)
    value = re.sub(r"[^0-9a-zа-я]+", " ", value)
    return " ".join(value.split())

def main():
    national = list(parse_m3u(download(NATIONAL_URL)))
    spb = list(parse_m3u(download(SPB_URL)))
    output = ["#EXTM3U x-tvg-url=\"" + EPG_URL + "\""]
    seen = set()

    def add(extinf, url, title=None, group=None):
        metadata = attrs(extinf)
        original_name = clean_name(extinf, metadata)
        key = (metadata.get("tvg-id") or normalized(original_name)).lower()
        if key in seen or any(marker in (extinf + " " + url).lower() for marker in BAD_MARKERS):
            return
        seen.add(key)
        if title:
            extinf = re.sub(r',.*$', "," + title, extinf)
            extinf = re.sub(r'group-title="[^"]*"', 'group-title="' + group + '"', extinf)
        output.extend((extinf, url))

    # Federal channels only from the Russia-wide feed.
    for extinf, url in national:
        metadata = attrs(extinf)
        name = normalized(clean_name(extinf, metadata))
        title = None
        for candidate, russian_title in FEDERAL_NAMES.items():
            candidate_norm = normalized(candidate)
            if name == candidate_norm or name.startswith(candidate_norm + " "):
                title = russian_title
                break
        if title:
            add(extinf, url, title, "Федеральные")

    # Every channel in this subdivision feed belongs to Saint Petersburg.
    for extinf, url in spb:
        metadata = attrs(extinf)
        name = clean_name(extinf, metadata)
        add(extinf, url, name, "Санкт-Петербург")

    if len(output) <= 1:
        raise RuntimeError("No channels selected; refusing to publish an empty playlist.")

    OUTPUT.write_text("\n".join(output) + "\n", encoding="utf-8")
    print(f"Generated {OUTPUT}: {(len(output) - 1) // 2} entries at {datetime.now(timezone.utc).isoformat()}")

if __name__ == "__main__":
    main()
