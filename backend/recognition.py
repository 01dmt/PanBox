"""recognize_chain.py — 文件/文件夹 → 媒体 TMDb ID 识别链（可移植单文件版）。

零第三方依赖（仅标准库），Python 3.10+，可直接复制进任何项目。
识别逻辑与 115xemby / ETKN 识别链路一致，分两层：

  1) 解析层（纯规则，不联网）：显式 tmdb= 提取 → 自定义季集正则 → 内置季集规则
     → 截断式标题清洗 → 年份/类型推断，并产出机制层所需的上下文
     （剧名目录 context_name / 查询变体 query_variants / 合集线索 collection）。
  2) 机制层（编排宿主能力）：显式 ID 直查 → 剧集批量身份共享 → 合集 parts 映射
     → 多查询变体搜索（精确命中即停、精确优先）→ AI 辅助 → MoviePilot 辅助
     → 置信分流（matched / review / unmatched）。

宿主只需实现 search() 与 fetch_by_id() 两个方法，其余均为可选增强。
"""
from __future__ import annotations

import difflib
import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence

# ================================================================ 配置默认值

DEFAULT_OPTIONS = {
    "explicit_lookup": True,        # 文件名/目录名里的 tmdb=12345 直接按 ID 匹配
    "series_identity": True,        # 同剧名目录首集确认后整季共享身份
    "collection_mapping": True,     # 合集目录里的影片按合集 parts 清单匹配
    "search_variants": True,        # 解析标题→文件名→目录名 多查询变体搜索
    "ai_recognition": False,        # AI 辅助文件名识别（需宿主实现 ai_parse）
    "moviepilot_auxiliary": False,  # MoviePilot 辅助识别（需宿主实现 moviepilot）
}

DEFAULT_THRESHOLDS = {
    "auto_threshold": 0.85,   # 低于此分进人工确认（review）
    "ambiguity_margin": 0.08,  # 前两名分差小于此值视为歧义（review）
    "candidate_floor": 0.45,   # 候选噪声地板，低于即丢弃
}

# 证据标记（candidate.metadata["recognition"]["evidence"]），用于排查“为什么是这个 ID”：
#   explicit_tmdb                  显式 tmdb= 直查命中
#   series_batch_identity          剧集批量身份共享（同剧名目录复用）
#   official_collection_part       合集 parts 清单匹配
#   legacy_rule_search             多查询变体回退命中
#   exact_title_match              归一化标题精确命中（排序优先）
#   ai_recognition                 AI 辅助解析后搜索命中
#   moviepilot_auxiliary_recognition  MoviePilot 辅助识别命中

# ================================================================ 解析层常量

_NOISE_TOKEN_PATTERNS = (
    r"(?i)\b(?:WEB[-_. ]?DL|WEB[-_. ]?RIP|BLU[-_. ]?RAY|BDRIP|BRRIP|REMUX|DVDRIP|HDTV|UHD)\b",
    r"(?i)\b(?:HDR10\+?|HDR|DV|DOVI|DOLBY[.\s_-]*VISION|HLG)\b",
    r"(?i)\b(?:HEVC|AVC|X265|X264|H265|H264|10BIT|8BIT|AAC[.\s_-]*\d?(?:\.\d)?|DDP[.\s_-]*\d?(?:\.\d)?|DD[.\s_-]*\d?(?:\.\d)?|TRUEHD|ATMOS|DTS(?:[-_. ]?HD)?|FLAC)\b",
    r"(?i)\b(?:2160P|1080P|720P|576P|480P|4K)\b",
    r"(?i)\b(?:NF|NETFLIX|AMZN|AMAZON|DSNP|DISNEY|HMAX|MAX|ATVP|APPLE|IQIYI|YOUKU|WEB)\b",
    r"(?i)\b(?:MULTI|DUAL[-_. ]?AUDIO|DUAL|PROPER|REPACK|READNFO|EXTENDED|UNCUT|COMPLETE|FINAL)\b",
    r"(?i)\b(?:CHS|CHT|ENG|JPN|KOR|GB|BIG5|简中|繁中|中字|双语|国粤|内封|外挂|特效字幕)\b",
    r"(?i)\b(?:AAC2\.0|AAC5\.1|DDP5\.1|DD5\.1|DTS5\.1|TRUEHD7\.1|ATMOS7\.1)\b",
    r"(?i)\b(?:CAM|TS|TC|R5)\b",
)
_DATE_EPISODE_PATTERNS = (
    re.compile(r"(?<!\d)(20\d{2})[.\-_ ](0[1-9]|1[0-2])[.\-_ ]([0-3]\d)(?!\d)"),
    re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-3]\d)(?!\d)"),
)
_SPECIAL_FLAG_PATTERNS = (
    re.compile(r"(?i)(?:^|[ .\-_ /\[(])(?:specials?|sp|ova|oad|oads|extra(?:s)?|ncop|nced)(?:$|[ .\-_/\)\]])"),
    re.compile(r"(?:特别篇|特別篇|番外(?:篇)?|外传|外傳|总集篇|總集篇|OVA|OAD)", re.IGNORECASE),
)
_GENERIC_CONTEXT_RE = re.compile(
    r"^(?:"
    r"(?:19|20)\d{2}|"
    r"电影|电视剧|剧集|动漫|动画|综艺|纪录片|纪录|合集|系列|全套|打包|大包|资源|"
    r"待整理|转存|新片|影视|网盘|未识别|"
    r"collection|collections|series|pack|package|movie|movies|tv|shows?|anime|misc|unknown|"
    r"115|p115|shared|share|共享池|re0|频道|channel|moviepilot|mp"
    r")(?:\s*[\(\[【_-]?\s*(?:19|20)\d{2}\s*[\)\]】]?)?$",
    re.IGNORECASE,
)
_TMDB_RE = re.compile(r"(?i)(?:tmdb|tmdbid)[=\-_ ]*(\d{2,10})")
_SEASON_DIR_RE = re.compile(r"(?i)(?:season[ ._-]*\d+|S\d+|第[一二三四五六七八九十百零\d]+季|specials?)")
_COLLECTION_RE = re.compile(r"(?i)(合集|collection|系列|全集|trilogy|quadrilogy|部曲|saga|duology|anthology|pack)")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


# ================================================================ 数据结构


@dataclass
class Recognition:
    """纯规则解析结果（不联网）。"""
    title: str = ""
    media_type: str = "movie"            # movie | tv
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    confidence: str = "low"              # low | medium | high（解析层置信）
    evidence: list = field(default_factory=list)
    filename: str = ""
    context_name: str = ""               # 剧名目录（季节目录自动上跳一级）
    tmdb_id: str = ""
    collection: Optional[dict] = None    # {"name": 合集目录名, "tmdb_id": ""}
    query_variants: list = field(default_factory=list)  # [(title, year), ...]


@dataclass
class Candidate:
    provider: str
    external_id: str
    title: str
    media_type: str = "movie"
    year: Optional[int] = None
    original_title: str = ""
    confidence: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class Resolution:
    status: str                          # matched | review | unmatched
    selected: Optional[Candidate]
    candidates: list
    recognition: Recognition
    errors: list = field(default_factory=list)

    @property
    def tmdb_id(self) -> str:
        return self.selected.external_id if self.selected else ""


# ================================================================ 解析层（忠实移植自 ETKN/115xemby）


def _normalize_media_type(value: object) -> Optional[str]:
    text = str(value or "").strip().lower()
    if text in {"movie", "movies", "film", "电影"}:
        return "movie"
    if text in {"tv", "series", "episode", "season", "show", "电视剧", "剧集", "番剧", "动漫"}:
        return "tv"
    return None


def is_generic_context_name(value: object) -> bool:
    normalized = str(value or "").strip().strip("/\\")
    if not normalized:
        return True
    compact = re.sub(r"[\s._-]+", "", normalized).lower()
    if compact in {"115", "p115", "115批量整理", "moviepilot", "mp"}:
        return True
    return bool(_GENERIC_CONTEXT_RE.match(normalized))


def _parse_chinese_number(value: object) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100}
    total = current = 0
    for character in text:
        if character in digits:
            current = digits[character]
        elif character in units:
            if current == 0:
                current = 1
            total += current * units[character]
            current = 0
    return (total + current) or None


def _extract_custom_season_episode(text: object, rules: Sequence[Mapping[str, Any]]):
    """自定义季集正则：按顺序取第一条命中（优先于内置规则）。"""
    value = str(text or "")
    for rule in rules or []:
        if not isinstance(rule, Mapping) or rule.get("enabled", True) is False:
            continue
        try:
            match = re.search(str(rule.get("pattern") or ""), value)
            if not match:
                continue
            episode = int(match.group(int(rule.get("episode_group") or 1)))
            season = int(rule.get("default_season", 1)) if rule.get("mode") == "episode_only" else int(match.group(int(rule.get("season_group") or 1)))
            if season >= 0 and episode >= 0:
                return season, episode, ["episode_regex"]
        except (IndexError, TypeError, ValueError, re.error):
            continue
    return None, None, []


def _extract_season_episode(text: object):
    """内置季集规则：SxxExx / 1x02 / Season N / 第X季 / EPxx / 第X集 / 日期集 / 特别篇。"""
    normalized = str(text or "").replace("\\", "/")
    lower = normalized.lower()
    evidence: list = []
    if match := re.search(r"(?i)(?:^|[ .\-_ /\[(])s(\d{1,4})[ .\-_]*[eEpP](\d{1,4})(?:$|[ .\-_/\(\)\[\]])", normalized):
        return int(match.group(1)), int(match.group(2)), ["sxe"]
    if match := re.search(r"(?i)(?:^|[ ._ /\[(])(\d{1,2})x(\d{1,4})(?:$|[ ._/\)\]])", normalized):
        return int(match.group(1)), int(match.group(2)), ["season_episode_x"]
    if re.fullmatch(r"(?i)S\d{1,4}", normalized.strip()):
        return int(normalized.strip()[1:]), None, ["season_word"]
    season = episode = None
    if match := re.search(r"(?i)(?:^|[ .\-_ /\[(])season[ .\-_]*(\d{1,4})(?:$|[ .\-_/\)\]])", normalized):
        season, evidence = int(match.group(1)), ["season_word"]
    if match := re.search(r"第\s*([一二三四五六七八九十百零\d]+)\s*季", normalized):
        if season is None and (number := _parse_chinese_number(match.group(1))) is not None:
            season, evidence = number, evidence + ["season_zh"]
    if match := re.search(r"(?i)(?:^|[ .\-_ /\[(])(?:ep|episode)[ .\-_]*(\d{1,4})(?:$|[ .\-_/\)\]])", normalized):
        episode, evidence = int(match.group(1)), evidence + ["episode_word"]
    if match := re.search(r"(?i)(?:^|[ .\-_ /\[(])e(\d{1,4})(?:$|[ .\-_/\)\]])", normalized):
        if episode is None:
            episode, evidence = int(match.group(1)), evidence + ["episode_e"]
    if match := re.search(r"第\s*([一二三四五六七八九十百零\d]+)\s*[集话話回]", normalized):
        if episode is None and (number := _parse_chinese_number(match.group(1))) is not None:
            episode, evidence = number, evidence + ["episode_zh"]
    if episode is None:
        for pattern in _DATE_EPISODE_PATTERNS:
            if match := pattern.search(normalized):
                episode, evidence = int("".join(match.groups())), evidence + ["episode_date"]
                break
    if any(pattern.search(normalized) for pattern in _SPECIAL_FLAG_PATTERNS):
        season = 0 if season is None else season
        evidence.append("special")
    if episode is None and (match := re.search(r"(?i)(?:^|[ .\-_ /\[(])(?:sp|ova|oad)(\d{1,4})(?:$|[ .\-_/\)\]])", normalized)):
        episode, evidence = int(match.group(1)), evidence + ["special", "episode_special_code"]
    if season is None and episode is not None and any(flag in lower for flag in ("ep", "episode", "第", "话", "話", "回")):
        season = 1
    return season, episode, evidence


def _clean_title(text: object) -> str:
    """截断式标题清洗：在最早的“标题终止信号”处截断，再清噪声词（ETKN 同款）。"""
    if not text:
        return ""
    value = os.path.basename(str(text).replace("\\", "/").strip())
    # Directory names can contain dots (e.g. "The.Show (2024)"); only strip
    # a known media extension rather than treating every final dot as one.
    value = re.sub(r"(?i)\.(?:mkv|mp4|m4v|mov|avi|wmv|webm|ts|m2ts|iso|rmvb?)$", "", value)
    value = re.sub(r"[？?！!：:；;，,、]+", " ", value)
    cutoff_patterns = (
        r"(?i)\b(?:s\d{1,4}[ .\-_]*e\d{1,4}|season[ .\-_]*\d{1,4}|ep(?:isode)?[ .\-_]*\d{1,4})\b",
        r"第\s*[一二三四五六七八九十百零\d]+\s*[季集话話回]",
        r"(?<!\d)(?:19|20)\d{2}(?!\d)",
        r"(?i)\b(?:part|pt|cd)[ .\-_]*\d{1,2}\b",
        r"(?i)\b(?:tmdb|tmdbid)[=\-_ ]*\d+\b",
        r"(?i)\b(?:specials?|ova|oad|sp|extra(?:s)?|collection|complete)\b",
        r"(?i)\b(?:h[ ._-]?26[45]|x26[45])\b",
        r"(?i)\b(?:flac|aac|ddp|dd|dts|truehd|atmos)[ ._-]*\d(?:\.\d)?\b",
        *_NOISE_TOKEN_PATTERNS,
    )
    cutoffs = [
        (match.start(), value[: match.start()].strip(" ._-"))
        for pattern in cutoff_patterns
        if (match := re.search(pattern, value)) and value[: match.start()].strip(" ._-")
    ]
    if cutoffs:
        value = min(cutoffs, key=lambda item: item[0])[1]
    for pattern in _NOISE_TOKEN_PATTERNS:
        value = re.sub(pattern, " ", value)
    for pattern in (
        r"(?i)\b(?:s\d{1,4}[ .\-_]*e\d{1,4}|season[ .\-_]*\d{1,4}|ep(?:isode)?[ .\-_]*\d{1,4}|第\s*[一二三四五六七八九十百零\d]+\s*[季集话話回])\b",
        r"(?i)\b(?:part|pt|cd)[ .\-_]*\d{1,2}\b",
        r"(?i)\b(?:tmdb|tmdbid)[=\-_ ]*\d+\b",
        r"(?<!\d)(?:19|20)\d{2}(?!\d)",
        r"(?i)\b(?:specials?|ova|oad|sp|extra(?:s)?|collection|complete)\b",
        r"(?i)\b(?:h[ ._-]?26[45]|x26[45])\b",
        r"(?i)\b(?:flac|aac|ddp|dd|dts|truehd|atmos)[ ._-]*\d(?:\.\d)?\b",
    ):
        value = re.sub(pattern, " ", value)
    value = re.sub(r"(?i)(?:[-_. ]+)?[A-Za-z0-9][A-Za-z0-9._-]{1,20}@[A-Za-z0-9][A-Za-z0-9._-]{1,20}$", " ", value)
    value = re.sub(r"(?<!\w)\d\.\d(?!\w)", " ", value)
    value = re.sub(r"[\[\]\(\){}]", " ", value)
    return re.sub(r"\s+", " ", re.sub(r"[._]+", " ", value)).strip(" -_./ ")


def parse(filename: str, main_dir_name: str = "", episode_regex: Sequence[Mapping[str, Any]] = (), container_name: str = "") -> Recognition:
    """纯规则解析入口。

    filename       视频文件名（含扩展名）
    main_dir_name  剧名目录/影片目录名（可空；季目录请先上跳一级）——标题优先来源
    episode_regex  自定义季集正则 [{"pattern", "mode", "season_group", "episode_group", "default_season", "enabled"}]
    container_name 合集容器目录名（影片目录的上一级；可空，空则不做合集判定）
    """
    result = Recognition(filename=str(filename or ""), context_name=str(main_dir_name or ""))
    sources = [text for text in (str(filename or ""), str(main_dir_name or "")) if text]
    # 1) 显式 TMDb ID
    for text in sources:
        if match := _TMDB_RE.search(text):
            result.tmdb_id, result.confidence = match.group(1), "high"
            result.evidence.append("explicit_tmdb")
            break
    # 2) 季集号：自定义正则优先，其次内置规则
    season = episode = None
    custom_evidence: list = []
    for text in sources:
        season, episode, custom_evidence = _extract_custom_season_episode(text, episode_regex)
        if episode is not None:
            break
    if episode is None:
        for text in sources:
            parsed_season, parsed_episode, parsed_evidence = _extract_season_episode(text)
            season = parsed_season if season is None else season
            episode = parsed_episode if episode is None else episode
            result.evidence.extend(item for item in parsed_evidence if item not in result.evidence)
            if season is not None and episode is not None and "special" not in result.evidence:
                break
    else:
        result.evidence.extend(custom_evidence)
    result.season, result.episode = season, episode
    # 3) 标题与年份：目录名优先（剧名目录比文件名稳定），自定义规则命中片段先剔除
    candidates = [str(filename)] if filename else []
    if main_dir_name and str(main_dir_name).strip() and str(main_dir_name) != str(filename) and not is_generic_context_name(main_dir_name):
        candidates.insert(0, str(main_dir_name))
    for candidate in candidates:
        title_candidate = candidate
        if any(item == "episode_regex" for item in result.evidence):
            try:
                title_candidate = re.sub(str((episode_regex or [{}])[0].get("pattern") or ""), "", title_candidate)
            except re.error:
                pass
        if title := _clean_title(title_candidate):
            result.title = title
            break
    for candidate in candidates:
        if matches := list(_YEAR_RE.finditer(candidate)):
            result.year = int(matches[-1].group(1))
            break
    # 4) 类型与置信
    if season is not None or (main_dir_name and _SEASON_DIR_RE.fullmatch(str(main_dir_name).strip())):
        result.media_type = "tv"
    elif result.tmdb_id or (result.title and result.year):
        result.media_type = "movie"
    if result.tmdb_id:
        result.confidence = "high"
    elif result.title and (result.year or result.media_type or result.episode is not None):
        result.confidence = "medium" if result.confidence == "low" else result.confidence
    # 5) 机制层上下文：查询变体（ETKN 语义：解析标题→文件名→目录名）
    variants: list = []

    def add_variant(title: str, year: Optional[int]) -> None:
        if title and (title, year) not in variants:
            variants.append((title, year))

    add_variant(result.title, result.year)
    if filename:
        solo = Recognition()
        solo.title, solo.year = _clean_title(filename), next((int(m.group(1)) for m in reversed(list(_YEAR_RE.finditer(str(filename))))), None)
        add_variant(solo.title, solo.year)
    if main_dir_name and not is_generic_context_name(main_dir_name):
        add_variant(_clean_title(main_dir_name), next((int(m.group(1)) for m in reversed(list(_YEAR_RE.finditer(str(main_dir_name))))), None))
    result.query_variants = variants
    # 6) 合集线索：容器目录（影片目录的上一级）带合集标记（仅电影）
    container = str(container_name or "").strip()
    if result.media_type == "movie" and container and (_COLLECTION_RE.search(container) or _TMDB_RE.search(container)):
        cid = _TMDB_RE.search(container)
        result.collection = {"name": container, "tmdb_id": cid.group(1) if cid else ""}
    return result


# ================================================================ 打分与挑选


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _title_similarity(left: str, right: str) -> float:
    left, right = _norm(left), _norm(right)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def score_candidate(query_title: str, query_year: Optional[int], title: str, year: Optional[int]) -> float:
    """候选打分：0.72×标题相似度 + 年份加减，配合 DEFAULT_THRESHOLDS 使用。"""
    value = 0.72 * _title_similarity(query_title, title)
    if query_year and year:
        value += 0.20 if query_year == year else (-0.08 if abs(query_year - year) > 1 else 0.04)
    elif query_year is None:
        value += 0.05
    return max(0.0, min(0.99, value))


def select_collection_part(parts: Sequence[Mapping[str, Any]], title: str, year: Optional[int]) -> Optional[Mapping[str, Any]]:
    """合集 part 挑选：归一化标题精确/包含匹配，年份择优（ETKN 同款）。"""
    normalized = _norm(title)
    if not normalized:
        return None
    matches = []
    for part in parts or []:
        titles = {_norm(part.get("title")), _norm(part.get("original_title"))}
        titles.discard("")
        if normalized not in titles and not any(normalized in item or item in normalized for item in titles):
            continue
        release = str(part.get("release_date") or "")
        part_year = int(release[:4]) if release[:4].isdigit() else None
        matches.append((0 if year is None or year == part_year else 1, part))
    return min(matches, key=lambda value: value[0])[1] if matches else None


# ================================================================ 宿主协议与编排


class Host(Protocol):
    """宿主适配协议：前两个方法必填，其余可选（未实现即自动跳过对应机制）。"""

    def search(self, title: str, media_type: str, year: Optional[int] = None,
               season: Optional[int] = None, episode: Optional[int] = None) -> Sequence[Candidate]:
        """文本搜索 TMDb（movie/tv 端点按 media_type 选择）。"""

    def fetch_by_id(self, tmdb_id: str, media_type: str) -> Optional[Candidate]:
        """按 TMDb ID 直取详情。"""

    # ---- 可选增强 ----
    def collection_parts(self, collection_tmdb_id: str = "", collection_name: str = "") -> Sequence[Mapping[str, Any]]:
        """合集 parts 清单：[{"id", "title", "original_title", "release_date"}]。"""
        return ()

    def moviepilot(self, queries: Sequence[str]) -> Optional[Mapping[str, Any]]:
        """MoviePilot 辅助识别：返回 {"tmdb_id","title","media_type","year"} 或 None。"""
        return None

    def ai_parse(self, filename: str) -> Optional[Mapping[str, Any]]:
        """AI 辅助解析文件名：返回 {"title","year","media_type"} 或 None。"""
        return None


class Recognizer:
    """识别链编排器。options / thresholds 均可运行时调整。"""

    def __init__(self, host: Host, options: Optional[Mapping[str, Any]] = None,
                 thresholds: Optional[Mapping[str, Any]] = None, series_cache_size: int = 256) -> None:
        self.host = host
        self.options = {**DEFAULT_OPTIONS, **dict(options or {})}
        t = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
        self.auto_threshold = float(t["auto_threshold"])
        self.ambiguity_margin = float(t["ambiguity_margin"])
        self.candidate_floor = float(t["candidate_floor"])
        self._series: "OrderedDict[tuple, Candidate]" = OrderedDict()
        self._series_cap = int(series_cache_size)

    # ---- 机制 ----
    def _fetch_by_id(self, media_type: str, tmdb_id: str, confidence: float, evidence: list) -> Optional[Candidate]:
        try:
            candidate = self.host.fetch_by_id(str(tmdb_id), media_type)
        except Exception:
            return None
        if candidate is None or str(candidate.external_id) != str(tmdb_id):
            return None
        candidate.confidence = confidence
        candidate.metadata = {**candidate.metadata, "recognition": {"evidence": list(evidence)}}
        return candidate

    def _series_key(self, rec: Recognition):
        if rec.media_type != "tv" or not rec.context_name:
            return None
        return ("series", _norm(rec.context_name), rec.year or 0)

    def _series_cached(self, rec: Recognition) -> Optional[Candidate]:
        key = self._series_key(rec)
        if key is None or key not in self._series:
            return None
        self._series.move_to_end(key)
        candidate = self._series[key]
        candidate.metadata = {**candidate.metadata, "recognition": {"evidence": ["series_batch_identity"]}}
        return candidate

    def _cache_series(self, rec: Recognition, candidate: Candidate) -> None:
        key = self._series_key(rec)
        if key is None:
            return
        self._series[key] = candidate
        self._series.move_to_end(key)
        while len(self._series) > self._series_cap:
            self._series.popitem(last=False)

    def _collection_candidate(self, rec: Recognition) -> Optional[Candidate]:
        if not rec.collection or not hasattr(self.host, "collection_parts"):
            return None
        parts = self.host.collection_parts(rec.collection.get("tmdb_id", ""), rec.collection.get("name", "")) or []
        part = select_collection_part(parts, rec.title, rec.year)
        if not part:
            return None
        return self._fetch_by_id(rec.media_type, str(part.get("id") or ""), 0.97, ["official_collection_part"])

    def _search_variants(self, rec: Recognition, variants: Sequence[tuple], extra_evidence: Sequence[str] = ()) -> list:
        """按变体顺序搜索；归一化标题精确命中即停（ETKN 语义），精确候选标记后排序优先。"""
        collected: dict = {}
        for index, (title, year) in enumerate(variants):
            exact_hit = False
            try:
                rows = self.host.search(title, rec.media_type, year, rec.season, rec.episode) or []
            except Exception:
                continue
            for row in rows:
                candidate = Candidate(row.provider, row.external_id, row.title, row.media_type,
                                      row.year, row.original_title,
                                      score_candidate(title, year, row.title, row.year), dict(row.metadata))
                if candidate.confidence < self.candidate_floor:
                    continue
                evidence = list(extra_evidence) + (["legacy_rule_search"] if index > 0 else [])
                if _norm(title) in {_norm(candidate.title), _norm(candidate.original_title)}:
                    exact_hit = True
                    evidence.append("exact_title_match")
                if evidence:
                    candidate.metadata = {**candidate.metadata, "recognition": {"evidence": evidence}}
                key = (candidate.provider, str(candidate.external_id))
                previous = collected.get(key)
                if previous is None or candidate.confidence > previous.confidence:
                    collected[key] = candidate
            if exact_hit:
                break
        return sorted(collected.values(), key=lambda item: item.confidence, reverse=True)

    def _moviepilot_candidate(self, rec: Recognition) -> Optional[Candidate]:
        queries: list = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in queries:
                queries.append(text)

        add(rec.title)
        if rec.title and rec.year:
            add(f"{rec.title} ({rec.year})")
        if rec.title and rec.media_type == "tv" and rec.season is not None:
            add(f"{rec.title} S{int(rec.season):02d}")
        add(rec.context_name)
        add(rec.filename)
        try:
            identity = self.host.moviepilot(queries) or {}
        except Exception:
            return None
        tmdb_id = str(identity.get("tmdb_id") or "").strip()
        if not tmdb_id:
            return None
        # 类型冲突时保留规则锁定的类型（ETKN 同语义）
        mp_type = _normalize_media_type(identity.get("media_type")) or rec.media_type
        if mp_type != rec.media_type:
            return None
        return self._fetch_by_id(rec.media_type, tmdb_id, 0.92, ["moviepilot_auxiliary_recognition"])

    # ---- 主入口 ----
    def identify(self, filename: str, main_dir_name: str = "",
                 episode_regex: Sequence[Mapping[str, Any]] = (), container_name: str = "") -> Resolution:
        rec = parse(filename, main_dir_name, episode_regex, container_name)
        errors: list = []
        direct: Optional[Candidate] = None
        if self.options.get("explicit_lookup") and rec.tmdb_id:
            direct = self._fetch_by_id(rec.media_type, rec.tmdb_id, 0.99, ["explicit_tmdb"])
        if direct is None and self.options.get("series_identity"):
            direct = self._series_cached(rec)
        if direct is None and self.options.get("collection_mapping"):
            direct = self._collection_candidate(rec)
        if direct is not None:
            candidates = [direct]
        else:
            variants = rec.query_variants if self.options.get("search_variants") else [(rec.title, rec.year)]
            candidates = self._search_variants(rec, variants)
            if not candidates and self.options.get("ai_recognition") and hasattr(self.host, "ai_parse"):
                try:
                    parsed = self.host.ai_parse(rec.filename or rec.title) or {}
                except Exception:
                    parsed = {}
                if str(parsed.get("title") or "").strip():
                    ai_variant = (str(parsed["title"]).strip(), parsed.get("year") or rec.year)
                    candidates = self._search_variants(rec, [ai_variant], ["ai_recognition"])
            if not candidates and self.options.get("moviepilot_auxiliary") and hasattr(self.host, "moviepilot"):
                direct = self._moviepilot_candidate(rec)
                candidates = [direct] if direct else []
        # 精确命中优先排序；歧义分差检查照旧（近似候选并存仍进人工确认）
        exact = [c for c in candidates if "exact_title_match" in ((c.metadata.get("recognition") or {}).get("evidence") or [])]
        if exact:
            exact.sort(key=lambda item: item.confidence, reverse=True)
            selected = exact[0]
            candidates = [selected] + [c for c in candidates if c is not selected]
        else:
            selected = candidates[0] if candidates else None
        if selected is None:
            status = "unmatched"
        elif selected.confidence < self.auto_threshold:
            status = "review"
        elif len(candidates) > 1 and selected.confidence - candidates[1].confidence < self.ambiguity_margin:
            status = "review"
        else:
            status = "matched"
        if status == "matched" and selected is not None:
            self._cache_series(rec, selected)
        return Resolution(status, selected, candidates, rec, errors)


__all__ = ["Recognition", "Candidate", "Resolution", "Host", "Recognizer",
           "parse", "score_candidate", "select_collection_part", "is_generic_context_name",
           "DEFAULT_OPTIONS", "DEFAULT_THRESHOLDS"]
