from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional
from urllib.parse import unquote

import wordninja

from .recognition import parse as parse_recognition


SHARE_URL_RE = re.compile(r"https://115(?:cdn)?\.com/s/[^\s\t]+", re.IGNORECASE)
ED2K_RE = re.compile(
    r"ed2k://\|file\|[^|]+\|\d+\|[A-Fa-f0-9]{32}\|/",
    re.IGNORECASE,
)
BRACKETED_YEAR_RE = re.compile(
    r"[\(\[（【]\s*((?:19|20)\d{2})\s*[\)\]）】]\s*$"
)
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d|[pP])")
SEASON_EPISODE_RE = re.compile(
    r"(?i)(?:^|[._\-\s\[\]()/【】])S(\d{1,2})[._\-\s]*E(\d{1,3})(?=$|[._\-\s\[\]()/【】])"
)
SEASON_RE = re.compile(r"(?i)(?:^|[._\-\s\[\]()/【】])S(\d{1,2})(?=$|[._\-\s\[\]()/【】])")
EPISODE_SPAN_RE = re.compile(
    r"(?i)(?:^|[._\-\s\[\]()【】])S(\d{1,2})[._\-\s]*E(\d{1,3})"
    r"(?:\s*[-~]\s*(?:S(\d{1,2})[._\s]*)?E?(\d{1,3}))?"
    r"(?=$|[._\-\s\[\]()【】])"
)
QUALITY_RE = re.compile(r"(?i)(2160p|1080p|1080i|720p|576p|480p|4K)")
CODEC_RE = re.compile(r"(?i)(H[._-]?26[45]|x26[45]|HEVC|AV1|AVC)")
AUDIO_RE = re.compile(
    r"(?i)(DDP?\d(?:\.\d)?(?:\.Atmos)?|AAC\d(?:\.\d)?|AAC|DTS(?:-HD)?(?:\.MA)?\d?(?:\.\d)?|TrueHD(?:\.Atmos)?|FLAC\d?(?:\.\d)?)"
)
TECHNICAL_STOP_RE = re.compile(
    r"(?i)(?:^|[._\-\s])(2160p|1080p|1080i|720p|576p|480p|4K|WEB[._-]?DL|WEBRip|BluRay|BDRip|HDTV|REMUX|DVDRip|NF|AMZN|DSNP|HMAX|VIU|AI修复(?:版)?|修复版)(?=$|[._\-\s])"
)
MEDIA_EXTENSION_RE = re.compile(
    r"(?i)\.(mkv|mp4|m4v|mov|avi|wmv|webm|ts|m2ts|iso|rmvb?)(?:\s.*)?$"
)
SHARE_METADATA_RE = re.compile(
    r"(?i)\s*(?:访问码|提取码|密码)\s*[：:].*$|\s*复制这段内容.*$"
)
TMDB_ID_RE = re.compile(r"(?i)\{?\s*tmdb(?:id)?\s*[-:=：]\s*(\d+)\s*\}?")
SECONDARY_ALIAS_START_RE = re.compile(
    r"(?i)(?:^|[._\-\s])(BluRay|BDRip|DVDRip|WEB[._-]?DL|WEBRip|HDTV|REMUX)(?=$|[._\-\s])"
)
SECONDARY_ALIAS_STOP_RE = re.compile(
    r"(?i)(?:^|[._\-\s])(?:\d{3,4}[pi]|[48]K|UHD|HQ|HD|V\d+|\d+FPS|\d+bits?|"
    r"BluRay|BDRip|REMUX|IMAX|REPACK|PROPER|WEB[._-]?DL|WEBRip|"
    r"USA|JPN|GER|GBR|FRA|ITA|ESP|KOR|CHN|HKG|TW|"
    r"AC3(?:\.\d+)?|AAC\d*(?:\.\d+)?|DTS(?:-HD)?(?:\.MA)?\d*(?:\.\d+)?|"
    r"DDP?\d*(?:\.\d+)?|TrueHD(?:\.Atmos)?|FLAC\d*(?:\.\d+)?|"
    r"H[._-]?26[45]|x26[45]|AVC|HEVC|AV1|VP9|LPCM|DV|HDR10\+|HDR10P?|HDR|SDR|EDR|"
    r"\d+Audios?|FFans@[^._\-\s]*)(?=$|[._\-\s])"
)
TECHNICAL_TITLE_RE = re.compile(
    r"(?i)^(?:\d{3,4}[pi]|[48]k|uhd|hq|hd|v\d+|\d+fps|\d+bits?|"
    r"bluray|bdrip|remux|imax|repack|proper|webdl|webrip|hdtv|dvdrip|"
    r"[hx]26[45]|hevc|avc|av1|hdr10p?|hdr|dv|sdr|dts|dtshd|aac|ac3|flac|"
    r"truehd|atmos|lpcm|vp9|edr|s\d{1,2}(?:e\d{1,3})?)+$"
)
BILINGUAL_RELEASE_RE = re.compile(r"(?<=[)）\]】])\s+[-–—]\s+(?=[A-Za-z])")
BRACKETED_ALIAS_RE = re.compile(r"^[\[【]([^\]】]+)[\]】][._\s]*(.+)$")


def parse_episode_span(filename: str) -> tuple[int, int, int] | None:
    """Read an explicit single-season episode span, not an inferred season total."""
    name = filename.rsplit("/", 1)[-1]
    matches = list(EPISODE_SPAN_RE.finditer(name))
    if len(matches) != 1:
        return None
    match = matches[0]
    season, first = int(match[1]), int(match[2])
    end_season = int(match[3]) if match[3] else season
    last = int(match[4]) if match[4] else first
    # Unsupported/chained ranges must not silently become single-episode evidence.
    if re.match(r"(?i)[._\s]*[-~][._\s]*(?:S\d|E\d|\d)", name[match.end():]):
        return None
    if season < 1 or end_season != season or not 1 <= first <= last <= 999:
        return None
    return season, first, last


@dataclass(frozen=True)
class ParsedSource:
    source_type: str
    source_key: str
    title: str
    year: Optional[int]
    media_type: str
    raw_label: str
    raw_text: str
    url: str
    provider: str = "115"
    filename: Optional[str] = None
    file_size: Optional[int] = None
    ed2k_hash: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    quality: Optional[str] = None
    codec: Optional[str] = None
    hdr: Optional[str] = None
    audio: Optional[str] = None
    release_group: Optional[str] = None
    metadata: dict = field(default_factory=dict)


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(ch for ch in normalized if ch.isalnum())


def is_search_title(value: str) -> bool:
    normalized = normalize_title(value)
    return bool(normalized and value != "未命名" and not TECHNICAL_TITLE_RE.fullmatch(normalized))


def split_joined_title(value: str) -> str | None:
    if not re.fullmatch(r"[A-Za-z]{6,40}", value) or not is_search_title(value):
        return None
    words = wordninja.split(value)
    if 2 <= len(words) <= 5 and all(len(word) >= 2 for word in words):
        return " ".join(words)
    return None


def parse_title_year(value: str) -> tuple[str, Optional[int]]:
    text = value.lstrip("\ufeff").strip(" \t-–—|·")
    match = BRACKETED_YEAR_RE.search(text)
    year = int(match.group(1)) if match else None
    if match:
        text = text[: match.start()].rstrip(" \t-–—|·")
    return text or "未命名", year


def infer_share_media_type(title: str, extra: str) -> str:
    haystack = f"{title} {extra}"
    if re.search(r"(?i)S\d{1,2}(?:E\d{1,3})?", haystack):
        return "tv"
    if re.search(r"全\s*\d+\s*集|第\s*\d+\s*季|剧集|电视剧|综艺|连续剧", haystack):
        return "tv"
    if "电影" in extra:
        return "movie"
    return "unknown"


def parse_share_line(line: str) -> Optional[ParsedSource]:
    raw = line.rstrip("\r\n")
    match = SHARE_URL_RE.search(raw)
    if not match:
        return None

    url = match.group(0).rstrip("，。；;）)")
    prefix = raw[: match.start()].strip("\ufeff \t")
    suffix = raw[match.end() :].strip(" \t")
    label = prefix.split("\t", 1)[0].strip() if prefix else suffix
    title, year = parse_title_year(label)
    media_type = infer_share_media_type(title, suffix)

    return ParsedSource(
        source_type="115",
        provider="115",
        source_key=f"115:{url}",
        title=title,
        year=year,
        media_type=media_type,
        raw_label=label or title,
        raw_text=raw,
        url=url,
        metadata={"extra": suffix} if suffix else {},
    )


def iter_share_sources(content: str) -> Iterator[ParsedSource]:
    for line in content.splitlines():
        parsed = parse_share_line(line)
        if parsed:
            yield parsed


def _clean_filename_title(value: str) -> str:
    text = unquote(value)
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"[\[\]【】]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_()（）")
    if text.isupper() and any(ch.isalpha() for ch in text):
        text = text.title()
    return text or "未命名"


def _find_year(stem: str) -> tuple[Optional[int], Optional[re.Match[str]]]:
    matches = list(YEAR_RE.finditer(stem))
    if not matches:
        return None, None
    # Release names often contain a year-shaped title followed by the actual
    # release year, for example "1917.2019.1080p".
    match = matches[-1]
    return int(match.group(1)), match


def _release_title_year(
    stem: str,
    episode_match: Optional[re.Match[str]] = None,
) -> tuple[str, Optional[int]]:
    technical_match = TECHNICAL_STOP_RE.search(stem)
    year, year_match = _find_year(stem[:technical_match.start()] if technical_match else stem)
    leading_year = bool(
        year_match
        and not stem[: year_match.start()].strip("._- ")
    )
    if leading_year and year_match:
        remainder = stem[year_match.end() :].lstrip("._- ")
        remainder_technical = TECHNICAL_STOP_RE.search(remainder)
        if remainder and not (remainder_technical and remainder_technical.start() == 0):
            title_part = remainder[: remainder_technical.start()] if remainder_technical else remainder
        else:
            title_part = year_match.group(1)
    else:
        cut_points: list[int] = []
        if episode_match:
            cut_points.append(episode_match.start())
        if year_match:
            cut_points.append(year_match.start())
        if technical_match:
            cut_points.append(technical_match.start())
        title_part = stem[: min(cut_points)] if cut_points else stem
    return _clean_filename_title(title_part), year


def _release_segments(value: str) -> list[str]:
    text = unquote(value).strip()
    text = SHARE_METADATA_RE.sub("", text)
    text = TMDB_ID_RE.sub("", text).strip()
    stem = MEDIA_EXTENSION_RE.sub("", text).strip()
    segments = BILINGUAL_RELEASE_RE.split(stem, maxsplit=1)
    if len(segments) == 1:
        bracketed = BRACKETED_ALIAS_RE.match(stem)
        if bracketed:
            segments = list(bracketed.groups())
    return segments


def parse_release_title(value: str) -> tuple[str, Optional[int]]:
    segments = _release_segments(value)
    stem = segments[0]
    title_stop = SEASON_EPISODE_RE.search(stem) or SEASON_RE.search(stem)
    title, year = _release_title_year(stem, title_stop)
    if year is None and len(segments) > 1:
        _, year = _release_title_year(segments[1])
    return title, year


def parse_release_aliases(value: str, *, include_secondary: bool = True) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()

    def add(title: str) -> None:
        normalized = normalize_title(title)
        if is_search_title(title) and normalized not in seen:
            seen.add(normalized)
            aliases.append(title)

    for stem in _release_segments(value):
        stop = SEASON_EPISODE_RE.search(stem) or SEASON_RE.search(stem)
        primary, _ = _release_title_year(stem, stop)
        add(primary)
        marker = SECONDARY_ALIAS_START_RE.search(stem)
        if marker and include_secondary:
            tail = stem[marker.end() :].lstrip("._- ")
            stop = SECONDARY_ALIAS_STOP_RE.search(tail)
            if stop:
                tail = tail[: stop.start()]
            add(_clean_filename_title(tail))
    return aliases


def extract_tmdb_ids(value: str) -> list[int]:
    return [int(match.group(1)) for match in TMDB_ID_RE.finditer(unquote(value))]


def parse_ed2k_link(link: str, raw_text: Optional[str] = None) -> Optional[ParsedSource]:
    parts = link.split("|")
    if len(parts) < 6 or parts[1].lower() != "file":
        return None

    filename = unquote(parts[2])
    try:
        file_size = int(parts[3])
    except ValueError:
        return None
    ed2k_hash = parts[4].upper()
    stem = MEDIA_EXTENSION_RE.sub("", filename)

    episode_match = SEASON_EPISODE_RE.search(stem)
    season_match = episode_match or SEASON_RE.search(stem)
    title, year = parse_release_title(filename)
    # The reusable recognizer covers non-standard but common episode forms such
    # as 1x02, Season 2, 第三集, and date-based episode names. Keep the mature
    # importer parser as the first choice and use it only as a conservative fallback.
    fallback = parse_recognition(filename)

    season = int(season_match.group(1)) if season_match else fallback.season
    episode = int(episode_match.group(2)) if episode_match else fallback.episode
    media_type = "tv" if season is not None or fallback.media_type == "tv" else "movie"

    quality_match = QUALITY_RE.search(stem)
    codec_match = CODEC_RE.search(stem)
    audio_match = AUDIO_RE.search(stem)
    hdr_tags = []
    for pattern, label in (
        (r"(?i)(?:^|[._\-])DV(?:$|[._\-])", "DV"),
        (r"(?i)HDR10P|HDR10\+", "HDR10+"),
        (r"(?i)HDR10", "HDR10"),
        (r"(?i)(?:^|[._\-])HDR(?:$|[._\-])", "HDR"),
        (r"(?i)(?:^|[._\-])EDR(?:$|[._\-])", "EDR"),
        (r"(?i)(?:^|[._\-])SDR(?:$|[._\-])", "SDR"),
    ):
        if re.search(pattern, stem) and label not in hdr_tags:
            hdr_tags.append(label)

    release_group_match = re.search(r"-([A-Za-z0-9]+)$", stem)

    return ParsedSource(
        source_type="ed2k",
        provider="115",
        source_key=f"ed2k:{ed2k_hash}",
        title=title,
        year=year,
        media_type=media_type,
        raw_label=filename,
        raw_text=raw_text or link,
        url=link,
        filename=filename,
        file_size=file_size,
        ed2k_hash=ed2k_hash,
        season=season,
        episode=episode,
        quality=quality_match.group(1).upper() if quality_match else None,
        codec=codec_match.group(1).upper().replace("_", ".") if codec_match else None,
        hdr=" + ".join(hdr_tags) if hdr_tags else None,
        audio=audio_match.group(1) if audio_match else None,
        release_group=release_group_match.group(1) if release_group_match else None,
    )


def iter_ed2k_sources(content: str) -> Iterator[ParsedSource]:
    for line in content.splitlines():
        for match in ED2K_RE.finditer(line):
            parsed = parse_ed2k_link(match.group(0), raw_text=line)
            if parsed:
                yield parsed


def detect_source_kind(content: str) -> str:
    has_share = bool(SHARE_URL_RE.search(content))
    has_ed2k = bool(ED2K_RE.search(content))
    if has_share and has_ed2k:
        return "mixed"
    if has_share:
        return "115"
    if has_ed2k:
        return "ed2k"
    return "unknown"


def iter_sources(content: str, kind: str = "auto") -> Iterable[ParsedSource]:
    selected = detect_source_kind(content) if kind == "auto" else kind
    if selected in {"115", "mixed"}:
        yield from iter_share_sources(content)
    if selected in {"ed2k", "mixed"}:
        yield from iter_ed2k_sources(content)


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8-sig")).hexdigest()
