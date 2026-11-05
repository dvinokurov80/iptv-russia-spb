#!/usr/bin/env python3
"""Build a categorized Russia + Saint Petersburg IPTV playlist."""

from urllib.request import Request, urlopen
from datetime import datetime, timezone
from pathlib import Path
import re

NATIONAL_URL = "https://iptv-org.github.io/iptv/countries/ru.m3u"
SPB_URL = "https://iptv-org.github.io/iptv/subdivisions/ru-spe.m3u"
EPG_URL = "http://epg.one/ru.xml.gz"
# Public HLS source listed as playable by an IPTV stream directory.
# The provider may apply geographic restrictions outside Russia.
FIFTH_CHANNEL_FALLBACK = "https://zabava-htlive.cdn.ngenix.net/hls/CH_5TV/variant.m3u8"
OUTPUT = Path("playlist.m3u")

FEDERAL_IDS = {
    "channelone.ru": "Первый канал",
    "russia1.ru": "Россия 1",
    "russia24.ru": "Россия 24",
    "russiak.ru": "Россия Культура",
    "ntv.ru": "НТВ",
    "rentv.ru": "РЕН ТВ",
    "sts.ru": "СТС",
    "tnt.ru": "ТНТ",
    "tv3.ru": "ТВ-3",
    "friday.ru": "Пятница!",
    "muztv.ru": "МУЗ-ТВ",
    "domashniy.ru": "Домашний",
    "tvcentr.ru": "ТВ Центр",
    "zvezda.ru": "Звезда",
    "otr.ru": "ОТР",
    "spas.ru": "Спас",
    "karusel.ru": "Карусель",
    "match.ru": "Матч ТВ",
    "matchtv.ru": "Матч ТВ",
    "mir.ru": "Мир",
    "mir24.ru": "Мир 24",
    "rbc.ru": "РБК",
    "5kanal.ru": "Пятый канал",
    "channel5.ru": "Пятый канал",
    "2x2.ru": "2x2",
}
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
    "муз тв": "МУЗ-ТВ",
    "муз-тв": "МУЗ-ТВ",
    "тв 3": "ТВ-3",
}

FEDERAL_GROUPS = {
    "Первый канал": "Развлекательные",
    "Россия 1": "Развлекательные",
    "Россия 24": "Новости",
    "Россия Культура": "Познавательные",
    "НТВ": "Развлекательные",
    "РЕН ТВ": "Познавательные",
    "СТС": "Развлекательные",
    "ТНТ": "Развлекательные",
    "Пятница!": "Развлекательные",
    "Домашний": "Развлекательные",
    "ТВ Центр": "Новости",
    "Звезда": "Познавательные",
    "ОТР": "Познавательные",
    "Спас": "Познавательные",
    "Карусель": "Детские",
    "Мир": "Развлекательные",
    "Мир 24": "Новости",
    "РБК": "Новости",
    "Пятый канал": "Новости",
    "Матч ТВ": "Спорт",
    "МУЗ-ТВ": "Музыка",
    "ТВ-3": "Фильмы",
    "2x2": "Развлекательные",
}
FAVOURITES = {"Первый канал", "Россия 1", "НТВ", "СТС", "ТНТ", "Пятница!", "Россия 24", "Матч ТВ"}
SPB_NAMES = {"sanktpeterburg.ru": "Телеканал Санкт-Петербург"}
BAD_MARKERS = ("[geo-blocked]", "[not 24/7]", "[offline]", "[broken]")

def download(url):
    request = Request(url, headers={"User-Agent": "iptv-russia-spb-playlist/1.0"})
    with urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8-sig", errors="replace")

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

def clean_name(extinf, metadata):
    return metadata.get("tvg-name") or extinf.rsplit(",", 1)[-1].strip()

def normalized(value):
    value = value.lower().replace("ё", "е")
    value = re.sub(r"\[[^]]*\]", "", value)
    value = re.sub(r"[^0-9a-zа-я]+", " ", value)
    return " ".join(value.split())

def infer_group(name, source_group=""):
    text = normalized(name + " " + source_group)
    rules = [
        ("Музыка", ("муз", "music")),
        ("Фильмы", ("кино", "фильм", "movie", "cinema", "сериал")),
        ("Спорт", ("спорт", "sport", "матч", "футбол", "хоккей")),
        ("Новости", ("новост", "news", "информац")),
        ("Познавательные", ("познав", "науч", "документ", "образоват", "science", "education")),
        ("Детские", ("детск", "мульт", "kids", "children")),
        ("Развлекательные", ("развлек", "шоу", "юмор", "entertain")),
    ]
    for group, words in rules:
        if any(word in text for word in words):
            return group
    return "Санкт-Петербург"

def set_group(extinf, group, title):
    head, comma, display = extinf.partition(",")
    head = re.sub(r'\s+group-title="[^"]*"', "", head)
    head += ' group-title="' + group + '"'
    return head + comma + title

def main():
    national = list(parse_m3u(download(NATIONAL_URL)))
    spb = list(parse_m3u(download(SPB_URL)))
    print("Loaded {} national and {} Saint Petersburg source entries".format(len(national), len(spb)))
    output = ['#EXTM3U x-tvg-url="' + EPG_URL + '" url-tvg="' + EPG_URL + '"']
    selected = []

    for extinf, url in national:
        if any(marker in (extinf + " " + url).lower() for marker in BAD_MARKERS):
            continue
        metadata = attrs(extinf)
        source_name = clean_name(extinf, metadata)
        normalized_name = normalized(source_name)
        channel_id = metadata.get("tvg-id", "").lower().split("@", 1)[0]
        title = FEDERAL_IDS.get(channel_id)
        if not title:
            # Try exact/leading name matches as a fallback for feeds without EPG IDs.
            for candidate, russian_title in sorted(FEDERAL_NAMES.items(), key=lambda item: len(item[0]), reverse=True):
                candidate_name = normalized(candidate)
                if normalized_name == candidate_name or normalized_name.startswith(candidate_name + " "):
                    title = russian_title
                    break
        if title:
            if title == "Пятый канал":
                url = FIFTH_CHANNEL_FALLBACK
            selected.append((extinf, url, title, FEDERAL_GROUPS.get(title, "Общероссийские"), False))

    for extinf, url in spb:
        if any(marker in (extinf + " " + url).lower() for marker in BAD_MARKERS):
            continue
        metadata = attrs(extinf)
        channel_id = metadata.get("tvg-id", "").lower().split("@", 1)[0]
        title = SPB_NAMES.get(channel_id, clean_name(extinf, metadata))
        group = infer_group(title, metadata.get("group-title", ""))
        selected.append((extinf, url, title, group, True))

    written = set()
    for extinf, url, title, group, is_spb in selected:
        metadata = attrs(extinf)
        channel_key = (metadata.get("tvg-id", "").lower().split("@", 1)[0] or normalized(title))
        key = (channel_key, group)
        if key not in written:
            written.add(key)
            extinf = re.sub(r'(tvg-id="[^"@]+)@[^"]*"', r'\1"', extinf)
            output.extend((set_group(extinf, group, title), url))

    for extinf, url, title, group, is_spb in selected:
        if not is_spb and title in FAVOURITES:
            metadata = attrs(extinf)
            channel_key = (metadata.get("tvg-id", "").lower().split("@", 1)[0] or normalized(title))
            key = (channel_key, "Избранное")
            if key not in written:
                written.add(key)
                extinf = re.sub(r'(tvg-id="[^"@]+)@[^"]*"', r'\1"', extinf)
                output.extend((set_group(extinf, "Избранное", title), url))

    if len(output) <= 1:
        raise RuntimeError("No channels selected; refusing to publish an empty playlist.")

    OUTPUT.write_text("\n".join(output) + "\n", encoding="utf-8")
    print("Generated {}: {} entries at {}".format(
        OUTPUT, (len(output) - 1) // 2, datetime.now(timezone.utc).isoformat()
    ))

if __name__ == "__main__":
    main()
