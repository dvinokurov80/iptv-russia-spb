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

def genre_for(title, source_group="", is_spb=False):
    text = normalized(title + " " + source_group)
    if any(x in text for x in ("муз", "music")): return "Музыка"
    if any(x in text for x in ("кино", "фильм", "movie", "cinema", "сериал")): return "Фильмы"
    if any(x in text for x in ("спорт", "sport", "матч", "футбол", "хоккей")): return "Спорт"
    if any(x in text for x in ("новост", "news", "информац")): return "Новости"
    if any(x in text for x in ("познав", "науч", "документ", "образоват", "science", "education")): return "Познавательные"
    if any(x in text for x in ("детск", "мульт", "kids", "children")): return "Детские"
    if any(x in text for x in ("развлек", "шоу", "юмор", "entertain")): return "Развлекательные"
    return "Санкт-Петербург" if is_spb else "Общероссийские"

FAVOURITES = {"Первый канал", "Россия 1", "НТВ", "СТС", "ТНТ", "Пятница!", "Россия 24", "Матч ТВ"}

def main():
    national = list(parse_m3u(download(NATIONAL_URL)))
    spb = list(parse_m3u(download(SPB_URL)))
    output = ["#EXTM3U x-tvg-url=\"" + EPG_URL + "\""]
    seen = set()\n    selected = []

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
            selected.append((extinf, url, title, "Новости" if title in ("Россия 24", "РБК", "ТВ Центр", "Мир 24", "Пятый канал") else "Музыка" if title == "МУЗ-ТВ" else "Спорт" if title == "Матч ТВ" else "Детские" if title == "Карусель" else "Познавательные" if title in ("Россия Культура", "Звезда", "ОТР", "Спас", "РЕН ТВ") else "Развлекательные"))

    # Every channel in this subdivision feed belongs to Saint Petersburg.
    for extinf, url in spb:
        metadata = attrs(extinf)
        name = clean_name(extinf, metadata)
        selected.append((extinf, url, name, genre_for(name, metadata.get("group-title", ""), True)))

    # Write each channel into its genre group.
    for extinf, url, title, group in selected:
        extinf = re.sub(r'\\s+group-title="[^"]*"', "", extinf)
        extinf = re.sub(r',.*
        raise RuntimeError("No channels selected; refusing to publish an empty playlist.")

    OUTPUT.write_text("\n".join(output) + "\n", encoding="utf-8")
    print(f"Generated {OUTPUT}: {(len(output) - 1) // 2} entries at {datetime.now(timezone.utc).isoformat()}")

if __name__ == "__main__":
    main()
, "," + title, extinf)
        extinf = re.sub(r'^(#EXTINF:[^,]*)', r'\\1 group-title="' + group + '"', extinf)
        output.extend((extinf, url))

    # Duplicate a small set of popular federal channels into Favourites.
    for extinf, url, title, group in selected:
        if title in FAVOURITES:
            fav = re.sub(r'\\s+group-title="[^"]*"', "", extinf)
            fav = re.sub(r',.*
        raise RuntimeError("No channels selected; refusing to publish an empty playlist.")

    OUTPUT.write_text("\n".join(output) + "\n", encoding="utf-8")
    print(f"Generated {OUTPUT}: {(len(output) - 1) // 2} entries at {datetime.now(timezone.utc).isoformat()}")

if __name__ == "__main__":
    main()
, "," + title, fav)
            fav = re.sub(r'^(#EXTINF:[^,]*)', r'\\1 group-title="Избранное"', fav)
            output.extend((fav, url))

    if len(output) <= 1:
        raise RuntimeError("No channels selected; refusing to publish an empty playlist.")

    OUTPUT.write_text("\n".join(output) + "\n", encoding="utf-8")
    print(f"Generated {OUTPUT}: {(len(output) - 1) // 2} entries at {datetime.now(timezone.utc).isoformat()}")

if __name__ == "__main__":
    main()
