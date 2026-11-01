from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import wordninja

from .recognition import parse as parse_recognition
from .resource_metadata import clean_person_name
from .collection_markers import COLLECTION_RE as SHARE_COLLECTION_RE, STRIP_COLLECTION_RE


# Share URLs are parsed together so the rest of the ingestion pipeline can
# treat cloud shares uniformly while preserving the provider on each source.
# 123865.com/123pan.com are 123 网盘; guangyapan.com is 光鸭云盘.
SHARE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:115(?:cdn)?|123865|123pan|guangyapan)\.com/s/[^\s\t<>]+",
    re.IGNORECASE,
)
ED2K_RE = re.compile(
    r"ed2k://\|file\|[^|]+\|\d+\|[A-Fa-f0-9]{32}\|/",
    re.IGNORECASE,
)
def canonical_share_url(value: str) -> str:
    """Drop an empty URL fragment so ``...?password=x#`` and ``...?password=x``
    identify the same 115 share.  A non-empty fragment selects a file/folder
    and must be preserved."""
    value = value.strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if parts.fragment:
        return value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


BRACKETED_YEAR_RE = re.compile(
    r"[\(\[（【]\s*((?:19|20)\d{2})\s*[\)\]）】]\s*$"
)
# 频道里的「(2020-07-25)」是这一集的更新日期，不是作品年份；只作为
# 尾部元数据剥掉，绝不写入 year。
TRAILING_DATE_RE = re.compile(
    r"[\(\[（【]\s*(?:19|20)\d{2}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}\s*[\)\]）】]\s*$"
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
SHARE_AUDIO_RE = re.compile(
    r"(?i)\b(DDP?|AAC|DTS(?:[-_. ]HD)?(?:[-_. ]MA)?|TrueHD|FLAC)"
    r"(?:\s*([0-9]+)(?:[.\s]+([0-9]+))?)?(?:[.\s]*(Atmos))?\b"
)
SHARE_EPISODE_RE = re.compile(r"(?i)\bS(\d{1,2})\s*E(\d{1,3})\b")
SHARE_EPISODE_RANGE_RE = re.compile(r"(?i)\bE(\d{1,3})\s*[-~]\s*E(\d{1,4})\b")
# 「1-25集」「第3-18集」等同于 E01-E25；「全25集」表示第 1 到第 25 集（长番可到 4 位）。
SHARE_EPISODE_SPAN_RE = re.compile(r"第?\s*(\d{1,4})\s*[-~至到]\s*(\d{1,4})\s*集")
SHARE_EPISODE_TOTAL_RE = re.compile(r"全\s*(\d{1,4})\s*集")
# 「第四季」这类中文数字季号，避免有季号却回落到第 1 季。
CHINESE_SEASON_RE = re.compile(r"第\s*([一二三四五六七八九十]{1,3})\s*季")
SHARE_SEASON_RANGE_RE = re.compile(r"(?i)\bS(\d{1,2})\s*[-~]\s*S?(\d{1,2})(?=$|\D)")
SHARE_SEASON_RE = re.compile(r"(?i)\bS(\d{1,2})\b")
# 「全4季」「共4季」表示第 1 季到第 N 季；「第2-5季」给出明确的起止季。
SHARE_SEASON_TOTAL_RE = re.compile(r"全\s*(\d{1,2})\s*季|共\s*(\d{1,2})\s*季")
SHARE_SEASON_SPAN_RE = re.compile(r"第?\s*(\d{1,2})\s*[-~至到]\s*(\d{1,2})\s*季")
# 「第1季」这类阿拉伯数字单季写法（中文数字见 CHINESE_SEASON_RE）。
SHARE_SEASON_SINGLE_RE = re.compile(r"第\s*(\d{1,2})\s*季")
# 「更新至第17集」表示该季目前收录 E01-E17；频道也常省略「集」字（「更新至26」），
# 或用「更至10」，所以集数单位可选、动词也允许只写「更」。
SHARE_EPISODE_PROGRESS_RE = re.compile(r"(?:更新|更)\s*[至到]?\s*第?\s*(\d{1,4})\s*(?:集|话|期)?")
# 「第4季更新至第21集」里的第 4 季是该区间的归属季。
SHARE_PROGRESS_OWNER_RE = re.compile(r"第\s*(\d{1,2}|[一二三四五六七八九十]{1,3})\s*季\s*更新")
# 单独的「第19集」是单集。
SHARE_EPISODE_SINGLE_RE = re.compile(r"第\s*(\d{1,4})\s*集")
# 备用方案：没有任何季集标记时，只认紧跟片名年份之后的纯数字区间（如「兰香如故 (2026) 1-41」）。
SHARE_BARE_EPISODE_SPAN_RE = re.compile(
    r"\s*[\[\(（【]?\s*(\d{1,4})\s*[-~至到]\s*(\d{1,4})\s*(?=[\]\)）】]|$|\s|[-–—|·])"
)
SHARE_HEADER_YEAR_RE = re.compile(r"[\(\[（【]\s*((?:19|20)\d{2})\s*[\)\]）】]")
SHARE_TECHNICAL_RE = re.compile(
    r"(?i)(?<!\w)(?:2160p|1080p|1080i|720p|576p|480p|4k|"
    r"web[._ -]?dl|webrip|bluray|bdrip|hdtv|remux|dvdrip|"
    r"ddp?|aac|dts|truehd|flac|h[._-]?26[45]|x26[45]|hevc|av1|avc|hdr|dv)(?!\w)"
)
SHARE_METADATA_PREFIXES = ("🌟", "📽", "🌍", "🗣", "👥", "📖", "💾", "🙋")
# 字段式消息把标签写进正文首行（「名称：飞驰人生2（2024）」）。标签名不是片名，
# 片名是冒号后面的值，年份通常就在值的括号里。
SHARE_LABEL_PREFIX_RE = re.compile(r"^\s*(?:资源)?(?:名称|片名|剧名|标题|资源名)\s*[：:]\s*")
# 画质/压制噪声词：只用于截断标题，不参与 quality 字段取值（后者仍由 QUALITY_RE 决定）。
SHARE_NOISE_RE = re.compile(
    r"(?i)(高码率|臻彩|杜比视界|杜比|蓝光原盘|蓝光|原盘|hdr10\+?|hdr|dovi|60帧|高帧率|1080|2160|720)"
)
# 「📺 007(系列)」「📺 黑夜传说 (系列)」「📺 速度与激情（系列）国英双语」：频道用同一个
# 系列名给每一部单独发消息，所以系列标记不是片名；「合集 / 合辑 / 套装 /
# 全系列 / 三部曲」等则同时是资源包标记。括号可能被导出截断，右括号因此可选。
# 具体表达统一放在 collection_markers.py，解析器、TMDB 防护和资源分类共用同一套边界。
TECHNICAL_STOP_RE = re.compile(
    r"(?i)(?:^|[._\-\s])(2160p|1080p|1080i|720p|576p|480p|4K|WEB[._-]?DL|WEBRip|BluRay|BDRip|HDTV|REMUX|DVDRip|NF|AMZN|DSNP|HMAX|VIU|AI修复(?:版)?|修复版)(?=$|[._\-\s])"
)
MEDIA_EXTENSION_RE = re.compile(
    r"(?i)\.(mkv|mp4|m4v|mov|avi|wmv|webm|ts|m2ts|iso|rmvb?)(?:\s.*)?$"
)
AUDIO_EXTENSIONS = (".mp3", ".flac", ".m4a", ".ape", ".wav", ".aac", ".ogg", ".opus", ".wma", ".dsf", ".dff", ".alac", ".aiff")
SHARE_METADATA_RE = re.compile(
    r"(?i)\s*(?:访问码|提取码|密码)\s*[：:].*$|\s*复制这段内容.*$"
)
# 发布文件名常见的站标前缀，例如「www.98T.la@聊斋之艳蛇」。
# 只认「域名 + @」，避免把片名里的点号/邮箱误当站标。
SITE_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:https?://)?(?:[\[【(（]\s*)?"
    r"(?:www\.)?(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:com|net|org|la|cc|tv|me|info|xyz|top|club|vip|cn|io|co|site|online|link)"
    r"\s*(?:[\]】)）]\s*)?\s*[@＠]\s*"
)
ACCESS_CODE_RE = re.compile(r"(?i)(?:访问码|提取码|访问密码|密码|password|pwd)\s*[：:=\s]+([A-Za-z0-9]{2,16})")
TMDB_ID_RE = re.compile(r"(?i)\{?\s*tmdb(?:id)?\s*[-:=：]\s*(\d+)\s*\}?")
# 「{tv tmdb-206484}」这类简写也带类型提示（剧集/电影为中文别名）。
TMDB_TYPED_ID_RE = re.compile(r"(?i)\{?\s*(movie|tv|剧集|电影)\s+tmdb(?:id)?\s*[-:=：]\s*(\d+)\s*\}?")
# 频道消息常带 TMDB 官方链接（⭐️ TMDB评分… https://www.themoviedb.org/tv/282326），
# 它和 tmdb-xxx 简写一样是确定的身份信息，链接里的 /movie|tv/ 还给出媒体类型。
TMDB_URL_RE = re.compile(r"(?i)themoviedb\.org/(movie|tv)/(\d+)")
# 画质/技术关键词（分辨率、动态范围、片源、编码、音轨、帧率等），用于缓存展示与检索。
# 边界只用 ASCII 字母数字：中文与关键词紧邻时（如「原盘REMUX」）也要能命中。
QUALITY_TAG_RULES: tuple[tuple[str, str], ...] = (
    ("8K", r"(?i)(?<![A-Za-z0-9])8K(?![A-Za-z0-9])"),
    ("2160p", r"(?i)(?<![A-Za-z0-9])2160[pi](?![A-Za-z0-9])"),
    ("4K", r"(?i)(?<![A-Za-z0-9])4K(?![A-Za-z0-9])"),
    ("1080p", r"(?i)(?<![A-Za-z0-9])1080[pi](?![A-Za-z0-9])"),
    ("720p", r"(?i)(?<![A-Za-z0-9])720p(?![A-Za-z0-9])"),
    ("576p", r"(?i)(?<![A-Za-z0-9])576[pi](?![A-Za-z0-9])"),
    ("480p", r"(?i)(?<![A-Za-z0-9])480[pi](?![A-Za-z0-9])"),
    ("UHD", r"(?i)(?<![A-Za-z0-9])(?:UHD|Ultra[ ._-]?HD)(?![A-Za-z0-9])"),
    ("超高清", r"臻享超高清|超高清"),
    ("高清", r"(?<!超)高清"),
    ("蓝光", r"蓝光|Blu[ ._-]?ray"),
    ("原盘", r"原盘|BDRemux|BDMV"),
    ("REMUX", r"(?i)(?<![A-Za-z0-9])REMUX(?![A-Za-z0-9])"),
    ("WEB-DL", r"(?i)WEB[._\- ]?DL"),
    ("WEBRip", r"(?i)WEB[._\- ]?Rip"),
    ("HDTV", r"(?i)(?<![A-Za-z0-9])HDTV(?![A-Za-z0-9])"),
    ("H.265", r"(?i)(?<![A-Za-z0-9])(?:H[._\- ]?265|HEVC|x265)(?![A-Za-z0-9])"),
    ("H.264", r"(?i)(?<![A-Za-z0-9])(?:H[._\- ]?264|AVC|x264)(?![A-Za-z0-9])"),
    ("AV1", r"(?i)(?<![A-Za-z0-9])AV1(?![A-Za-z0-9])"),
    ("Dolby Vision", r"(?i)Dolby[._\- ]?Vision|(?<![A-Za-z0-9])(?:DoVi|DV)(?![A-Za-z0-9])"),
    ("HDR10+", r"(?i)HDR10\+|HDR10P"),
    ("HDR10", r"(?i)(?<![A-Za-z0-9])HDR10(?!\+|P|[A-Za-z0-9])"),
    ("HDR", r"(?i)(?<![A-Za-z0-9])HDR(?![A-Za-z0-9+])"),
    ("SDR", r"(?i)(?<![A-Za-z0-9])SDR(?![A-Za-z0-9])"),
    ("DDP", r"(?i)(?<![A-Za-z0-9])DDP(?:[ ._-]?([0-9]\.[0-9]))?(?![A-Za-z0-9])"),
    ("DD", r"(?i)(?<![A-Za-z0-9])DD(?:[ ._-]?([0-9]\.[0-9]))?(?![A-Za-z0-9])"),
    ("DTS-HD MA", r"(?i)DTS[._\- ]?HD[._\- ]?MA"),
    ("DTS", r"(?i)(?<![A-Za-z0-9])DTS(?![A-Za-z0-9])"),
    ("TrueHD", r"(?i)(?<![A-Za-z0-9])TrueHD(?![A-Za-z0-9])"),
    ("Atmos", r"(?i)(?<![A-Za-z0-9])Atmos(?![A-Za-z0-9])"),
    ("AAC", r"(?i)(?<![A-Za-z0-9])AAC(?![A-Za-z0-9])"),
    ("FLAC", r"(?i)(?<![A-Za-z0-9])FLAC(?![A-Za-z0-9])"),
    ("60fps", r"(?i)(?<![A-Za-z0-9])(?:60fps|60[._\- ]?FPS)(?![A-Za-z0-9])"),
    ("IMAX", r"(?i)(?<![A-Za-z0-9])IMAX(?![A-Za-z0-9])"),
    ("3D", r"(?i)(?<![A-Za-z0-9])3D(?![A-Za-z0-9])"),
    ("修复版", r"AI\s*修复|修复版"),
)
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
    resource_kind: str = "media"
    filename: Optional[str] = None
    file_size: Optional[int] = None
    ed2k_hash: Optional[str] = None
    season: Optional[int] = None
    season_end: Optional[int] = None
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


# 频道把集数/画质直接拼在片名后面（「凡人修仙传172」「破碎的愤怒 HDR10」），
# 这种串拿去搜 TMDB 必然 0 结果。下面几个正则只用于「精确检索已落空」时的兜底查询词。
SEARCH_NOISE_TAIL_RE = re.compile(
    r"(?i)(?:\s*(?:4k|8k|2160p|1080p|720p|2160|1080|720|高码率|臻彩|杜比视界|杜比|hdr10\+?|hdr|dovi|60帧|高帧率|蓝光原盘|蓝光|原盘))+$"
)
SEARCH_PROGRESS_TAIL_RE = re.compile(r"(?:更新|更)\s*[至到]?\s*第?\s*\d{1,4}\s*(?:集|话|期)?\s*$")
SEARCH_EPISODE_TAIL_RE = re.compile(r"(?i)\s*(?:ep|e)\s*\d{1,4}\s*$")
SEARCH_CN_EPISODE_TAIL_RE = re.compile(r"\s*第\s*\d{1,4}\s*[集话期]\s*$")
SEARCH_BARE_NUMBER_TAIL_RE = re.compile(r"\s*(\d{1,4})\s*$")


def strip_search_noise(value: str, *, episodic: bool = False) -> str | None:
    """Return the base title with trailing episode/quality noise removed.

    Only used as an *additional* fallback query after an exact search returned
    nothing. A bare trailing number is stripped only for episodic resources or
    when it is too large to be a sequel number (``凡人修仙传172`` vs ``疾速追杀2``),
    so genuine sequels keep their exact-title search.
    """
    text = (value or "").strip()
    original = text
    changed = True
    while changed:
        changed = False
        for pattern in (SEARCH_PROGRESS_TAIL_RE, SEARCH_EPISODE_TAIL_RE, SEARCH_CN_EPISODE_TAIL_RE, SEARCH_NOISE_TAIL_RE):
            stripped = pattern.sub("", text).strip(" \t-–—|·（）()[]【】")
            if stripped and stripped != text:
                text, changed = stripped, True
    bare = SEARCH_BARE_NUMBER_TAIL_RE.search(text)
    if bare:
        head = text[: bare.start()].strip(" \t-–—|·（）()[]【】")
        if len(head) >= 2 and (episodic or int(bare.group(1)) >= 10):
            text = head
    if text == original or len(text) < 2 or not is_search_title(text):
        return None
    return text


# 频道把集数直接拼在片名后（「牧神记79」「吞噬星空219」），没有「集」字。
# 只把这套写法当作**已有剧集上下文**下的标题清理，不据此单独判断媒体类型；
# 19 以下留给续集片名（流浪地球2/阿凡达2/电锯惊魂10），4 位留给年份片名。
EPISODE_SUFFIX_RE = re.compile(r"^(?P<base>.+?)(?P<episode>\d{1,4})$")
EPISODE_SUFFIX_MIN = 19
EPISODE_SUFFIX_MAX = 999


def split_episode_suffix(value: str) -> tuple[str, int] | None:
    """Split ``牧神记79`` into ``("牧神记", 79)``; conservative by design.

    This is not type evidence on its own.  Callers must already know the work
    is episodic (or have a base-title group with TV members), otherwise a
    sequel such as ``疾速追杀4`` or a title such as ``机动搜查队404`` would be
    damaged.
    """
    text = (value or "").strip(" \t-–—|·")
    if not text or any(token in text for token in ("+", "更新至", "更至", "全", "共")):
        return None
    if re.search(r"(?i)(?:S\d{1,2}\s*E\d{1,3}|EP\s*\d+|\bE\d{1,3}\b|第\s*\d+\s*[集话季])", text):
        return None
    if re.search(r"\d{1,4}\s*[-~至到]\s*\d{1,4}", text):
        return None
    match = EPISODE_SUFFIX_RE.match(text)
    if not match:
        return None
    episode = int(match.group("episode"))
    base = match.group("base").strip(" \t-–—|·")
    if not EPISODE_SUFFIX_MIN <= episode <= EPISODE_SUFFIX_MAX:
        return None
    if len(base) < 2 or not re.search(r"[\u3400-\u9fff]", base):
        return None
    if re.search(r"(?:电影|剧场版)", base):
        return None
    return base, episode


def parse_title_year(value: str) -> tuple[str, Optional[int]]:
    # 先去掉频道前缀 Emoji（如 🎬），否则括号年份不在字符串末尾时无法识别。
    text = re.sub(r"^\s*[📺🎥🎬👤🗂🍿🎞️]+\s*", "", value.lstrip("\ufeff")).strip(" \t-–—|·")
    text = TRAILING_DATE_RE.sub("", text).strip(" \t-–—|·")
    match = BRACKETED_YEAR_RE.search(text)
    if not match:
        # 年份后可能还有"全集"等后缀，退而匹配任意位置的括号年份。
        match = SHARE_HEADER_YEAR_RE.search(text)
    year = int(match.group(1)) if match else None
    if match:
        text = text[: match.start()].rstrip(" \t-–—|·")
    return text or "未命名", year


def _chinese_number(value: str) -> int | None:
    """Convert ``四``/``十``/``十一``/``二十五`` to an integer, else ``None``."""
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if not value or any(char not in "一二三四五六七八九十" for char in value):
        return None
    if value == "十":
        return 10
    if "十" in value:
        tens, _, ones = value.partition("十")
        return (digits.get(tens, 1) if tens else 1) * 10 + (digits.get(ones, 0) if ones else 0)
    return digits.get(value) if len(value) == 1 else None


# 媒体类型只认内容结构，不认频道图标。这里的正则覆盖频道常见的
# 「更新至 04 集」「更至 EP16」「第一季」「第 12 集」「全 24 集」等写法；
# 没有这些证据时保持 unknown，交给 TMDB 搜索和人工审核。
_SHARE_TV_TEXT_RE = re.compile(
    r"(?i)(?:"
    r"S\d{1,2}(?:\s*E\d{1,3})?|"
    r"EP\s*\d{1,4}|"
    r"(?:更新|更)\s*[至到]?\s*第?\s*\d{1,4}\s*(?:集|话|期)?|"
    r"第\s*\d{1,4}\s*[集话期]|"
    r"第\s*[一二三四五六七八九十百]+\s*[集话期]|"
    r"全\s*\d*\s*集|全集|全\s*\d+\s*季|共\s*\d+\s*季|"
    r"第\s*[0-9一二三四五六七八九十百]+\s*季|"
    r"\d{1,3}\s*[-~至到]\s*\d{1,3}\s*集|"
    r"剧集|电视剧|连续剧|综艺|动漫|番剧"
    r")"
)
_SHARE_MOVIE_TEXT_RE = re.compile(r"电影|剧场版")


def infer_share_media_type(title: str, extra: str) -> str:
    # 类型证据不能读剧情简介：剧情简介经常介绍同名电视剧/电影改编
    # （人类清除计划 2013 电影简介里就提到「电视剧集」），否则一部电影
    # 会被整段简介误判成剧集。保留标题行、标签行和季集行，只丢掉简介字段。
    lines = [
        line for line in (extra or "").splitlines()
        if not re.match(r"^\s*(?:剧情简介|内容简介|剧情介绍|简介)\s*[：:]", line)
    ]
    joined = "\n".join(lines)
    haystack = f"{title} {joined}"
    tv = bool(_SHARE_TV_TEXT_RE.search(haystack))
    movie = bool(_SHARE_MOVIE_TEXT_RE.search(haystack))
    if tv and movie:
        # 同时出现集数/季数和「电影/剧场版」时不猜，保留 unknown 让人审。
        return "unknown"
    if tv:
        return "tv"
    if movie:
        return "movie"
    return "unknown"


def _is_generic_share_label(value: str) -> bool:
    compact = re.sub(r"[\s:：|→>\-]+", "", value.strip())
    compact = compact.replace("🔗", "")
    return (
        not compact
        or compact in {"链接", "点击跳转", "链接点击跳转"}
        or value.lstrip().startswith(("🔗", "📎"))
        or "点击保存到" in value
    )


# 频道专属规则：每个频道的排版写法不同，规则只对识别出的频道生效，避免
# 「为 A 频道写的写法」在 B 频道上误判。识别方式与 ingestion 的 lsp115 判定
# 一致：频道用户名、频道名称或消息链接里的用户名。
# 目前只有光鸭云盘影视热更频道需要专属规则：
#   👤 分享：热心网友      ← 投稿人字段，不是人物条目，也不是片名
#   📺 质量：WEB-DL H265   ← 画质字段，不是片名
#   🎬 片名 (年份) 已更新  ← 真正的标题行
# 光鸭频道每条消息只有分享链接、没有发布文件名，字段行一旦被当成标题，整条消息
# 的来源会并入一条「分享：热心网友」的人物记录，不同影片还会互相顶替集号。
_GUANGYA_MOVIE_CHANNEL_USERNAME = "regengguangya"
_GUANGYA_MOVIE_CHANNEL_NAME = "光鸭云盘影视热更频道"

_SHARE_ATTR_LINE_RE = re.compile(
    r"^(?:👤|🗂|🏷|📺|🎭|🖥|📼|📦|⭐|🌟|📖|🔎)\s*"
    r"(?:分享|投稿人|分类|收录版本|播出进度|标签|质量|画质|集数|大小|类型|简介|TMDB(?:评分)?)\s*[：:]"
)


def _channel_identity(value: str | None) -> str:
    """Normalize a channel username/name for comparison."""
    return re.sub(r"[\s@]+", "", str(value or "")).casefold()


def is_guangya_movie_channel(channel: str | None = None, *, message_url: str | None = None) -> bool:
    """Return whether an event belongs to 光鸭云盘影视热更频道.

    Accepts the channel username or name directly, and falls back to the
    username in a ``t.me/<username>`` message link so entries recovered from
    stored messages resolve the same way as a live webhook.
    """
    identity = _channel_identity(channel)
    if identity in {
        _channel_identity(_GUANGYA_MOVIE_CHANNEL_USERNAME),
        _channel_identity(_GUANGYA_MOVIE_CHANNEL_NAME),
    }:
        return True
    try:
        path_parts = [part for part in urlsplit(str(message_url or "")).path.split("/") if part]
    except ValueError:
        return False
    if not path_parts:
        return False
    return _channel_identity(path_parts[0].lstrip("@")) == _channel_identity(_GUANGYA_MOVIE_CHANNEL_USERNAME)


def _is_channel_attr_line(value: str) -> bool:
    line = next((line.strip() for line in str(value or "").splitlines() if line.strip()), "")
    return bool(_SHARE_ATTR_LINE_RE.match(line))


def _extract_channel_fields(value: str) -> dict[str, object]:
    """Keep channel-provided metadata available for later manual scraping.

    Channel templates vary, so this intentionally captures both known labels
    and unknown labelled lines instead of discarding fields we do not yet
    understand. Values are kept as text; a later scraper can normalize them.
    """
    fields: dict[str, object] = {}
    labels = {
        "评分": "rating", "类型": "genres", "地区": "countries", "语言": "languages",
        "主演": "cast", "导演": "directors", "简介": "overview", "投稿": "submitter",
        "投稿人": "submitter", "搜索": "search", "机场": "airports", "包含影片": "included_films",
        "代表作": "representative_works", "生日": "birthday", "出生地": "birthplace",
    }
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^\s*[^\w\u4e00-\u9fff]*([^：:]{1,12})\s*[：:]\s*(.*?)\s*$", line)
        if not match:
            continue
        label, text = match.group(1).strip(), match.group(2).strip()
        if not text:
            continue
        key = labels.get(label, f"label_{normalize_title(label)}")
        if key in fields:
            previous = fields[key]
            fields[key] = f"{previous}\n{text}" if isinstance(previous, str) else [*previous, text]
        else:
            fields[key] = text
    return fields


RELEASE_GROUP_PREFIX_RE = re.compile(
    r"(?i)(?:LGNB|H3LL|FRDS|WiKi|CMCT|TLF|HDChina|QHstudIo|BeiTai|ADWeb|OurTV|"
    r"封装|封裝|压制|壓制|首发|首發|独家|獨家|重制|重製)"
)
LEADING_BRACKET_GROUP_RE = re.compile(r"^\s*[\[【]([^\]】]*)[\]】]\s*")
BRACKET_TITLE_GROUP_RE = re.compile(r"^\s*[\[【]([^\]】]*)(?:[\]】]|$)")


def _strip_release_group_prefix(value: str) -> str:
    """Drop a leading release-group bracket such as ``[LGNB顶级封装]``.

    Channels put the group/quality tag in the first bracket and the actual
    film title in the second: ``[LGNB顶级封装][梦比优斯奥特曼和奥特兄弟][4K...]``.
    The group is not part of the title, but the title bracket may be truncated
    by an export (``[LGNB顶级封装][疯狂的麦克斯2``); in that case take the rest
    of the line rather than returning the release group.
    """
    text = value.lstrip()
    match = LEADING_BRACKET_GROUP_RE.match(text)
    if not match:
        return value
    group = match.group(1).strip()
    if not group or not RELEASE_GROUP_PREFIX_RE.search(group):
        return value
    remainder = text[match.end():].lstrip()
    if not remainder:
        return value
    title_match = BRACKET_TITLE_GROUP_RE.match(remainder)
    if title_match:
        title = title_match.group(1).strip()
        if title:
            return title
    return remainder.lstrip("[【 ").strip()


def _cut_unclosed_bracket(value: str) -> str:
    """Drop an opening bracket that never closes; exports truncate such titles."""
    pairs = (("（", "）"), ("(", ")"), ("【", "】"), ("[", "]"), ("《", "》"))
    for opening, closing in pairs:
        start = value.find(opening)
        if start != -1 and closing not in value[start:]:
            return value[:start].rstrip(" \t-–—|·")
    return value


def parse_share_heading(value: str) -> dict[str, object]:
    """Public entry point: channel heading → title/year/season metadata."""
    return _share_heading_details(value)


def _valid_episode_total(total: int) -> bool:
    """A channel's ``全 N 集`` total must be a plausible episode count."""
    return 1 < total <= 2000


def _share_heading_details(value: str) -> dict[str, object]:
    """Parse a channel-style heading such as ``📺 Title (2026) S01E02 ✨4K``."""
    heading = value.lstrip("\ufeff \t")
    marker = re.match(r"^\s*([📺🎥🎬👤🗂])", heading)
    resource_kind = "person" if marker and marker.group(1) == "👤" else "series" if marker and marker.group(1) == "🗂" else "media"
    heading = re.sub(r"^\s*[📺🎥🎬👤🗂🍿🎞️]+\s*", "", heading)
    labelled = SHARE_LABEL_PREFIX_RE.match(heading)
    if labelled:
        # 「名称：飞驰人生2（2024）」这类字段式消息：片名是冒号后的值，
        # 标签名本身不是标题，后面还有剧情简介等行，取到首行结束即可。
        heading = heading[labelled.end():].strip() or heading
    heading = TRAILING_DATE_RE.sub("", heading).strip(" \t-–—|·") or heading
    year_match = SHARE_HEADER_YEAR_RE.search(heading) or YEAR_RE.search(heading)
    episode_match = SHARE_EPISODE_RE.search(heading)
    episode_range_match = SHARE_EPISODE_RANGE_RE.search(heading)
    season_range_match = SHARE_SEASON_RANGE_RE.search(heading)
    season_total_match = SHARE_SEASON_TOTAL_RE.search(heading)
    season_span_match = SHARE_SEASON_SPAN_RE.search(heading)
    season_single_match = SHARE_SEASON_SINGLE_RE.search(heading)
    chinese_season_match = CHINESE_SEASON_RE.search(heading)
    episode_span_match = SHARE_EPISODE_SPAN_RE.search(heading)
    episode_total_match = SHARE_EPISODE_TOTAL_RE.search(heading)
    episode_progress_match = SHARE_EPISODE_PROGRESS_RE.search(heading)
    episode_single_match = None if episode_progress_match else SHARE_EPISODE_SINGLE_RE.search(heading)
    season_match = episode_match or season_range_match or SHARE_SEASON_RE.search(heading)
    quality_match = QUALITY_RE.search(heading)
    technical_match = SHARE_TECHNICAL_RE.search(heading)
    noise_match = SHARE_NOISE_RE.search(heading)
    # 系列标记要在剥掉之前记下来：剥掉之后它既搜不到、也认不出归属。
    collection_match = SHARE_COLLECTION_RE.search(heading)
    is_collection = collection_match is not None

    cut_points = [
        match.start() for match in
        (year_match, episode_match, quality_match, technical_match, noise_match, season_total_match, season_span_match,
         season_single_match, chinese_season_match, episode_span_match, episode_total_match,
         episode_progress_match, episode_single_match)
        if match
    ]
    if collection_match:
        # 「速度与激情（系列）国英双语」：系列标记之后的都是版本备注，不是片名。
        cut_points.append(collection_match.start())
    title = heading[: min(cut_points)] if cut_points else heading
    title = _strip_release_group_prefix(title)
    title = title.strip(" \t-–—|·()（）[]【】✨") or "未命名"
    title = title.splitlines()[0].strip(" \t-–—|·()（）[]【】✨") or "未命名"
    # Some channels prefix the title with a human-readable category, for
    # example ``📺 剧集：兰香如故 (2026)``. Keep the category as metadata but
    # use the actual title for media grouping and TMDB matching.
    title = re.sub(r"^(?:剧集|电影|动漫|综艺)\s*[：:]\s*", "", title).strip() or "未命名"
    # 「（系列）」「（合集）」是资源集合标注，不是片名：任何类型都要剥掉，
    # 否则 TMDB 永远搜不到「生化危机（系列）」这种标题。
    title = STRIP_COLLECTION_RE.sub("", title).strip() or title
    # 只有整个标题都被书名号包住时（「《白日提灯》」）才去掉书名号；
    # 「《原神》音乐会」这类书名号属于片名本身，必须保留。
    title = re.sub(r"^《(.+)》$", r"\1", title).strip() or title
    # 导出/转发可能把括号截断（「星球大战（系列」「逐玉（1-15」）：只保留括号前的片名。
    title = _cut_unclosed_bracket(title).strip() or title
    year = int(year_match.group(1)) if year_match else None
    if resource_kind == "person":
        # 人物条目按名字聚合：清洗姓名尾部标点（如「浅野心，」）；
        # 「生日：1994-…」里的年份不是作品年份，不能入库。
        title = clean_person_name(title) or title
        year = None
    season = int(season_match.group(1)) if season_match else None
    episode = int(episode_match.group(2)) if episode_match else (int(episode_range_match.group(1)) if episode_range_match else None)
    if episode_range_match and not season_match:
        season = 1
    season_end = int(season_range_match.group(2)) if season_range_match else None
    if season_match is None:
        if season_span_match:
            season = int(season_span_match.group(1))
            season_end = int(season_span_match.group(2))
        elif season_total_match:
            total = int(season_total_match.group(1) or season_total_match.group(2))
            season = 1
            season_end = total if total > 1 else None
        elif season_single_match:
            season = int(season_single_match.group(1))
        elif chinese_season_match:
            season = _chinese_number(chinese_season_match.group(1))

    # 「1-25集」「第3-18集」「全25集」「更新至第17集」都与 E01-E25 等价：
    # 记录起始集并保留完整区间。
    episode_range = None
    latest_episode = None
    episode_total = None
    # 「第23-24集 / 全29集」「第03集 / 全12集」（光鸭云盘影视热更频道）里，
    # 前半段是要入库的实际集号，`全 N 集` 只是这部剧的总集数。明确集号存在时
    # 总集数不参与集号，更不能把起始集重置成 1。
    # 只认「第N集 / N-N集 / ENN」这类集号；综艺的「第一期」「先导片」不算，
    # 否则会把没有集数的消息凭空补出 S01。
    explicit_episode = bool(
        episode_match or episode_range_match or episode_span_match
        or (episode_single_match and "集" in episode_single_match.group(0))
    )
    if episode_range_match:
        episode_range = [int(episode_range_match.group(1)), int(episode_range_match.group(2))]
    elif episode_progress_match and episode_span_match is None and episode_total_match is None:
        last = int(episode_progress_match.group(1))
        owner = SHARE_PROGRESS_OWNER_RE.search(heading)
        owner_season = None
        if owner:
            raw_owner = owner.group(1)
            owner_season = int(raw_owner) if raw_owner.isdigit() else _chinese_number(raw_owner)
        target = season if season is not None else (owner_season or 1)
        if owner_season is not None and owner_season != target:
            # 区间属于另一季（如「共4季 第4季更新至第21集」）：只留信息，不写成本季集号。
            latest_episode = {"season": owner_season, "episode": last}
        elif last > 1:
            episode_range = [1, last]
            if season is None:
                season = target
    elif episode_span_match:
        first, last = int(episode_span_match.group(1)), int(episode_span_match.group(2))
        if last > first:
            episode_range = [first, last]
    elif episode_single_match:
        # 明确单集（「第03集」「第217集」）优先于「全 N 集」：后者在有集号时只是总集数。
        episode = int(episode_single_match.group(1))
        if season is None:
            season = 1
    elif episode_total_match:
        total = int(episode_total_match.group(1))
        if total > 1 and not explicit_episode:
            # 只有「全 N 集」独占集号信息时才表示整季。
            episode_range = [1, total]
    if episode_total_match and explicit_episode and _valid_episode_total(int(episode_total_match.group(1))):
        # 保留总集数供展示/核对，不参与集号计算。
        episode_total = int(episode_total_match.group(1))
    if episode_range and episode_match is None and episode_range_match is None:
        episode = episode_range[0]
        if season is None:
            season = 1

    # 备用方案：整行都没有季集标记时，才把紧跟年份的纯数字区间当作集数区间。
    if episode is None and episode_range is None and latest_episode is None and year_match:
        bare = SHARE_BARE_EPISODE_SPAN_RE.match(heading, year_match.end())
        if bare and 1 <= int(bare.group(1)) < int(bare.group(2)) <= 2000:
            episode_range = [int(bare.group(1)), int(bare.group(2))]
            episode = episode_range[0]
            if season is None:
                season = 1

    audio_match = SHARE_AUDIO_RE.search(heading)
    audio = None
    if audio_match:
        audio = audio_match.group(1).upper()
        if audio == "TRUEHD":
            audio = "TrueHD"
        if audio_match.group(2):
            audio += audio_match.group(2)
            if audio_match.group(3):
                audio += f".{audio_match.group(3)}"
        if audio_match.group(4):
            audio += ".Atmos"

    codec_match = CODEC_RE.search(heading)
    hdr_tags = []
    for pattern, label in (
        (r"(?i)(?<!\w)DV(?!\w)", "DV"),
        (r"(?i)HDR10P|HDR10\+", "HDR10+"),
        (r"(?i)(?<!\w)HDR10(?!\w)", "HDR10"),
        (r"(?i)(?<!\w)HDR(?!\w)", "HDR"),
        (r"(?i)(?<!\w)SDR(?!\w)", "SDR"),
    ):
        if re.search(pattern, heading) and label not in hdr_tags:
            hdr_tags.append(label)

    # 媒体类型只由内容结构决定：季集标记、全集/剧集/电影等文本证据。
    # 📺/🎥/🎬 只是频道排版图标，频道经常给所有投稿统一加同一个图标，
    # 不能据此判断电影或剧集；没有结构证据时保留 unknown，交给 TMDB 搜索。
    inferred_type = "tv" if season is not None or episode is not None else infer_share_media_type(title, heading)
    if inferred_type == "tv":
        media_type = "tv"
    elif inferred_type == "movie":
        media_type = "movie"
    else:
        media_type = "unknown"
    if resource_kind != "media":
        media_type = "unknown"
    if media_type == "tv" and season is None:
        # 没有季集线索的剧集资源默认视为整季：记为 S01（episode 为空即整季）。
        season = 1
    media_info = heading[technical_match.start() :].splitlines()[0].strip(" \t✨") if technical_match else None
    quality_tags = extract_quality_tags(heading, media_info)
    quality = quality_match.group(1).upper() if quality_match else None
    if quality is None:
        # 没写分辨率时按载体推断：UHD/4K → 2160P；蓝光原盘/REMUX → 1080P（蓝光原生分辨率）。
        if "UHD" in quality_tags or "4K" in quality_tags:
            quality = "2160P"
        elif {"蓝光", "原盘", "REMUX"} & set(quality_tags):
            quality = "1080P"
    return {
        "title": title,
        "year": year,
        "season": season,
        "season_end": season_end if season_end and season_end > season else None,
        "episode": episode,
        "media_type": media_type,
        "resource_kind": resource_kind,
        "collection": is_collection,
        "resource_metadata": {
            key: match.group(1).strip()
            for key, pattern in {
                "birthday": r"生日\s*[：:]\s*([^\n]+)",
                "birthplace": r"出生地\s*[：:]\s*([^\n]+)",
                "representative_works": r"代表作\s*[：:]\s*([^\n]+)",
                "included_films": r"包含影片\s*[：:]\s*([^\n]+)",
            }.items()
            if (match := re.search(pattern, heading, re.IGNORECASE))
        },
        "channel_fields": _extract_channel_fields(value),
        "quality": quality,
        "codec": codec_match.group(1).upper().replace("_", ".") if codec_match else None,
        "hdr": " + ".join(hdr_tags) if hdr_tags else None,
        "audio": audio,
        "media_info": media_info,
        "episode_range": episode_range,
        "episode_total": episode_total,
        "latest_episode": latest_episode,
        "quality_tags": quality_tags,
    }


def _find_share_heading(lines: list[str], index: int, *, channel_specific: bool = False) -> str | None:
    """Find the title line preceding a generic link label in one forwarded message.

    ``channel_specific`` enables that channel's own layout rules; without it the
    generic parser keeps the behaviour every other channel relies on.
    """
    for position in range(index - 1, -1, -1):
        previous = lines[position]
        candidate = previous.strip()
        if not candidate or candidate.startswith(SHARE_METADATA_PREFIXES):
            continue
        # A channel's category line also starts with ``🗂`` (for example
        # ``🗂 分类：剧集/国产剧集``). It is metadata, not the work heading.
        heading_marker = re.match(r"^([📺🎥👤🗂])\s*", candidate)
        is_category = bool(re.match(r"^(?:🗂\s*)?(?:分类|收录版本|播出进度)\s*[：:]", candidate))
        if heading_marker and not is_category and not (channel_specific and _is_channel_attr_line(candidate)):
            return "\n".join(line.strip() for line in lines[position:index] if line.strip())
    for position, previous in enumerate(lines[:index]):
        candidate = previous.strip()
        if candidate and not candidate.startswith(SHARE_METADATA_PREFIXES) and not SHARE_URL_RE.search(candidate):
            return "\n".join(line.strip() for line in lines[position:index] if line.strip())
    return None


def parse_share_line(
    line: str,
    *,
    context: str | None = None,
    access_context: str | None = None,
    person_context: str | None = None,
    channel_specific: bool = False,
) -> Optional[ParsedSource]:
    raw = line.rstrip("\r\n")
    match = SHARE_URL_RE.search(raw)
    if not match:
        return None

    url = match.group(0).rstrip("，。；;）)")
    # Forwarded channel messages sometimes put the access code on a separate
    # line. Attach it to the normalized URL before validation and de-duplication
    # while leaving the original message untouched in ingestion_events.
    parsed_url = urlsplit(url)
    query_text = parsed_url.query.lower()
    if not any(key in query_text for key in ("password=", "pwd=")):
        code_match = ACCESS_CODE_RE.search("\n".join(value for value in (line, context or "", access_context or "") if value))
        if code_match:
            host = (parsed_url.hostname or "").lower().removeprefix("www.")
            key = "pwd" if host in {"123865.com", "123pan.com"} else "password"
            base_url = url.split("#", 1)[0]
            fragment = f"#{url.split('#', 1)[1]}" if "#" in url else ""
            url = f"{base_url}{'&' if '?' in base_url else '?'}{key}={quote(code_match.group(1), safe='')}{fragment}"
    prefix = raw[: match.start()].strip("\ufeff \t")
    suffix = raw[match.end() :].strip(" \t")
    label = prefix.split("\t", 1)[0].strip() if prefix else suffix
    # 人物消息（如 lsp115）解析出的资源都归属该人物：person_context 存在时
    # 标题一律取人物标题块，不看链接行自身的标签。
    heading = context if context and (person_context or _is_generic_share_label(label)) else None
    # 光鸭云盘影视热更频道的「👤 分享：热心网友」只是投稿人字段，不是人物条目，
    # 也不是片名；照搬会丢掉消息自己的 🎬 标题，整条消息的来源被并成一条
    # 「分享：热心网友」的人物记录。这条规则只对该频道生效。
    if channel_specific and heading and _is_channel_attr_line(heading):
        heading = None
    details = _share_heading_details(heading or label)
    if heading is None and not SHARE_LABEL_PREFIX_RE.match(label):
        # Preserve the established one-line format while extracting episode and
        # technical metadata when it is present beside the URL.  A field label
        # such as ``名称: [LGNB...][片名]...`` was already parsed as a heading;
        # re-parsing the whole line would put the release tag back into title.
        title, year = parse_title_year(label)
        if details.get("season") is None and details.get("episode") is None:
            details.update({
                "title": title,
                "year": year,
                "media_type": infer_share_media_type(title, f"{label} {suffix}"),
            })
    details["media_type"] = "tv" if details.get("season") is not None or details.get("episode") is not None else details["media_type"]

    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if host in {"123865.com", "123pan.com"}:
        provider = "123"
    elif host == "guangyapan.com":
        provider = "guangya"
    else:
        provider = "115"
    source_url = canonical_share_url(url)
    return ParsedSource(
        source_type=provider,
        provider=provider,
        source_key=f"{provider}:{source_url}",
        title=str(details["title"]),
        year=details["year"],
        media_type=str(details["media_type"]),
        resource_kind=str(details.get("resource_kind") or "media"),
        # Keep the searchable label clean; retain the complete channel heading
        # in metadata so media details remain available without polluting title
        # matching with the episode and quality suffix.
        raw_label=str(details["title"]),
        raw_text=raw,
        url=source_url,
        season=details["season"],
        season_end=details.get("season_end"),
        episode=details["episode"],
        quality=details["quality"],
        codec=details["codec"],
        hdr=details["hdr"],
        audio=details["audio"],
        metadata={
            key: value
            for key, value in {
                "context": heading,
                "season_range": [details["season"], details["season_end"]] if details.get("season_end") else None,
                "episode_range": details.get("episode_range"),
                "episode_total": details.get("episode_total"),
                "latest_episode": details.get("latest_episode"),
                "quality_tags": details.get("quality_tags"),
                "media_info": details.get("media_info"),
                "resource_kind": details.get("resource_kind"),
                "collection": details.get("collection"),
                "resource_metadata": details.get("resource_metadata"),
                "channel_fields": details.get("channel_fields"),
                "extra": suffix,
            }.items()
            if value
        },
    )


def iter_share_sources(
    content: str,
    *,
    person_context: str | None = None,
    channel_specific: bool = False,
) -> Iterator[ParsedSource]:
    lines = content.splitlines()
    # 「⭐️ TMDB评分：6.8/10 https://www.themoviedb.org/tv/282326」这类官方链接
    # 通常不在标题行上，按消息级提取后附给每条来源；链接里的 /movie|tv/ 同时给出媒体类型。
    content_targets = extract_tmdb_targets(content)
    for index, line in enumerate(lines):
        match = SHARE_URL_RE.search(line)
        if not match:
            continue
        prefix = line[: match.start()].strip("\ufeff \t")
        label = prefix.split("\t", 1)[0].strip() if prefix else ""
        context = person_context or (
            _find_share_heading(lines, index, channel_specific=channel_specific)
            if _is_generic_share_label(label) or channel_specific
            else None
        )
        access_context = lines[index + 1] if index + 1 < len(lines) and ACCESS_CODE_RE.search(lines[index + 1]) else None
        parsed = parse_share_line(
            line,
            context=context,
            access_context=access_context,
            person_context=person_context,
            channel_specific=channel_specific,
        )
        if parsed:
            if content_targets:
                parsed.metadata["explicit_tmdb_ids"] = list(dict.fromkeys(tmdb_id for tmdb_id, _ in content_targets))
                parsed.metadata["explicit_tmdb"] = [
                    {"id": tmdb_id, "type": media_type} for tmdb_id, media_type in content_targets
                ]
            yield parsed


def trim_unbalanced_brackets(value: str) -> str:
    """Strip only unmatched edge brackets; keep balanced ones like （完整版）."""
    text = value
    pairs = (("(", ")"), ("（", "）"), ("[", "]"), ("【", "】"))
    changed = True
    while text and changed:
        changed = False
        for left, right in pairs:
            if text.startswith(left) and right not in text:
                text = text[len(left):].lstrip(" -_")
                changed = True
            if text.startswith(right) and left not in text:
                text = text[len(right):].lstrip(" -_")
                changed = True
            if text.endswith(left) and right not in text:
                text = text[:-len(left)].rstrip(" -_")
                changed = True
            if text.endswith(right) and left not in text:
                text = text[:-len(right)].rstrip(" -_")
                changed = True
    return text


def _clean_filename_title(value: str) -> str:
    text = SITE_PREFIX_RE.sub("", unquote(value)).strip()
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"[\[\]【】]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_")
    text = re.sub(r"\s*([：:])\s*", r"\1", text)
    text = trim_unbalanced_brackets(text)
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
    # 发布名偶尔重复年份（X.2022.2022.2160p）：第一段是片名 X，第二段才是
    # 发行年；只把末尾重复的同一个年份剥掉，避免把「1917.2019」里的片名 1917 也删了。
    if year and not leading_year:
        year_scope = stem[: technical_match.start()] if technical_match else stem
        repeated = [match for match in YEAR_RE.finditer(year_scope) if match.group(1) == str(year)]
        if len(repeated) >= 2 and not year_scope[repeated[-1].end() :].strip("._- "):
            title_part = year_scope[: repeated[0].start()].rstrip("._- ")
    return _clean_filename_title(title_part), year


def _release_segments(value: str) -> list[str]:
    text = SITE_PREFIX_RE.sub("", unquote(value).strip()).strip()
    text = SHARE_METADATA_RE.sub("", text)
    text = TMDB_ID_RE.sub("", text).strip()
    text = TMDB_URL_RE.sub("", text).strip()
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
    """Collect explicit TMDB ids from ``tmdb-123`` shorthands and official links."""
    return [tmdb_id for tmdb_id, _ in extract_tmdb_targets(value)]


def extract_tmdb_targets(value: str) -> list[tuple[int, str | None]]:
    """Return ``(id, type)`` pairs; the type comes from official link paths."""
    text = unquote(value)
    found: list[tuple[int, int, str | None]] = []
    for match in TMDB_TYPED_ID_RE.finditer(text):
        kind = match.group(1).lower()
        found.append((match.start(), int(match.group(2)), "tv" if kind in {"tv", "剧集"} else "movie"))
    for match in TMDB_URL_RE.finditer(text):
        found.append((match.start(), int(match.group(2)), match.group(1).lower()))
    for match in TMDB_ID_RE.finditer(text):
        found.append((match.start(), int(match.group(1)), None))
    targets: dict[int, str | None] = {}
    for _, tmdb_id, media_type in sorted(found):
        if media_type or tmdb_id not in targets:
            targets[tmdb_id] = media_type
    return list(targets.items())


def extract_quality_tags(*values: str) -> list[str]:
    """Return normalized technical tags (resolution, HDR, source, codec, audio)."""
    text = " ".join(value for value in values if value)
    if not text:
        return []
    tags: list[str] = []
    for label, pattern in QUALITY_TAG_RULES:
        match = re.search(pattern, text)
        if not match:
            continue
        channels = match.group(1) if match.groups() else None
        tag = f"{label}{channels}" if channels and label in {"DDP", "DD"} else label
        if tag not in tags:
            tags.append(tag)
    # 「DTS-HD MA」已经说明音轨规格，不再重复记泛化的 DTS。
    if "DTS-HD MA" in tags and "DTS" in tags:
        tags.remove("DTS")
    return tags


def _is_primary_media_filename(filename: str | None) -> bool:
    """Video/real-audio files carry a per-film title; sidecars follow the heading."""
    name = (filename or "").strip()
    if not name:
        return False
    if MEDIA_EXTENSION_RE.search(name):
        return True
    lowered = name.lower()
    return lowered.endswith(AUDIO_EXTENSIONS) and not lowered.endswith("theme.mp3")


def _ed2k_metadata(
    context: str | None,
    resource_kind: str,
    collection: bool,
    series_title: str,
) -> dict:
    if not context:
        return {}
    metadata: dict = {
        "context": context,
        "resource_kind": resource_kind,
        "channel_fields": _extract_channel_fields(context),
    }
    if collection:
        metadata["collection"] = True
        if series_title:
            metadata["series_title"] = series_title
    return metadata


def parse_ed2k_link(
    link: str,
    raw_text: Optional[str] = None,
    context: str | None = None,
) -> Optional[ParsedSource]:
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

    context_value = re.sub(r"^\s*[📺🎥🎬👤🗂🍿🎞️]+\s*", "", context or "")
    # Profile and collection headings may carry labelled metadata on following
    # lines. Only the first line is the stable grouping title.
    context_title_line = context_value.splitlines()[0].strip() if context_value else ""
    context_title, context_year = parse_title_year(context_title_line) if context else ("", None)
    context_marker = re.match(r"^\s*([📺🎥🎬👤🗂])", context or "")
    resource_kind = "person" if context_marker and context_marker.group(1) == "👤" else "series" if context_marker and context_marker.group(1) == "🗂" else "media"
    if resource_kind != "media":
        media_type = "unknown"
    context_details = _share_heading_details(context_title_line) if context_title_line else {}
    context_is_collection = bool(context_details.get("collection"))
    context_series_title = str(context_details.get("title") or "")
    if context_title and context_title != "未命名" and not context_title.lower().startswith("链接"):
        if context_is_collection and _is_primary_media_filename(filename):
            # 「📺 聊斋艳谭（系列）」只是系列归属，单片片名在发布文件名里
            # （山中艳谭（完整版）.1995... / 聊斋之艳蛇 (1994)...）。不能用
            # 系列名覆盖文件名标题，否则所有成员都会变成同一个标题。
            # 海报/NFO/字幕等附属文件仍沿用系列标题，避免拆成多条记录。
            if year is None:
                year = context_year
        else:
            title = context_series_title if context_is_collection and context_series_title else context_title
            year = year or context_year
    if resource_kind == "person":
        # 人物资源归属人物本身：标题取清洗后的人物名，
        # 文件名/生日里的年份不是人物年份，一律留空。
        title = clean_person_name(title) or title
        year = None

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
        season_end=None,
        episode=episode,
        quality=quality_match.group(1).upper() if quality_match else None,
        codec=codec_match.group(1).upper().replace("_", ".") if codec_match else None,
        hdr=" + ".join(hdr_tags) if hdr_tags else None,
        audio=audio_match.group(1) if audio_match else None,
        release_group=release_group_match.group(1) if release_group_match else None,
        resource_kind=resource_kind,
        metadata=_ed2k_metadata(
            context, resource_kind, context_is_collection, context_series_title
        ),
    )


def _find_ed2k_heading(lines: list[str], index: int) -> str | None:
    for previous in reversed(lines[:index]):
        candidate = previous.strip()
        if not candidate:
            continue
        if candidate.lower().startswith("ed2k://"):
            continue
        if candidate.startswith(("📺", "🎥", "👤", "🗂")):
            return candidate
        break
    return None


def iter_ed2k_sources(
    content: str,
    *,
    person_context: str | None = None,
    channel_specific: bool = False,
) -> Iterator[ParsedSource]:
    lines = content.splitlines()
    for index, line in enumerate(lines):
        context = person_context or _find_ed2k_heading(lines, index)
        for match in ED2K_RE.finditer(line):
            parsed = parse_ed2k_link(match.group(0), raw_text=line, context=context)
            if parsed:
                yield parsed


def detect_source_kind(content: str) -> str:
    has_share = bool(SHARE_URL_RE.search(content))
    has_ed2k = bool(ED2K_RE.search(content))
    if has_share and has_ed2k:
        return "mixed"
    if has_share:
        return "share"
    if has_ed2k:
        return "ed2k"
    return "unknown"


def iter_sources(
    content: str,
    kind: str = "auto",
    *,
    person_context: str | None = None,
    channel: str | None = None,
    message_url: str | None = None,
) -> Iterable[ParsedSource]:
    """Extract sources from one message.

    ``channel`` / ``message_url`` identify the source channel so a channel's
    own layout rules stay scoped to it. Without a recognised channel the
    generic parser runs unchanged.
    """
    channel_specific = is_guangya_movie_channel(channel, message_url=message_url)
    selected = detect_source_kind(content) if kind == "auto" else kind
    if selected in {"115", "123", "guangya", "share", "mixed"}:
        yield from iter_share_sources(
            content, person_context=person_context, channel_specific=channel_specific
        )
    if selected in {"ed2k", "mixed"}:
        yield from iter_ed2k_sources(
            content, person_context=person_context, channel_specific=channel_specific
        )


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8-sig")).hexdigest()
