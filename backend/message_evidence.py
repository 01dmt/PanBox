"""Resolve review candidates from the original channel message.

The current Lsp115 messages carry an explicit ``名称``/``发行时间`` block (or a
``🎬/📺`` heading with a release date).  That is stronger evidence than a
title-only score, but it is deliberately used as an *additional* gate: a
message can only confirm a candidate that is already in the stored candidate
list, with an exact title and an exact year.  It never invents a TMDB id and it
never relaxes the normal conflict checks.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from .importers import normalize_title

_GENERIC_TITLES = {
    "美剧", "电影", "电视剧", "资源", "合集", "无海报", "未命名",
    "jack分享", "1080p", "1080ps01全", "标签", "链接",
}
_QUALIFIERS = ("总集版", "合集", "剧场版", "特别篇", "番外", "完结篇", "最终章")
_SEASON_RE = re.compile(r"第([一二三四五六七八九十\d]+)季|S(\d{1,2})|Season\s*(\d+)", re.I)
_CN_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


@dataclass(frozen=True)
class MessageEvidence:
    title: str
    year: int | None
    season: int | None
    standardized: bool
    raw_title: str
    text: str
    media_type_hint: str | None = None


def _first_field(text: str, names: str) -> str:
    match = re.search(rf"(?:{names})\s*[:：]\s*([^\n]+)", text)
    return match.group(1).strip() if match else ""


def _strip_title_noise(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^(?:🎬|📺|🎥|🗂)\s*", "", value)
    # A year immediately after the title is the work year, not part of the title.
    match = re.match(r"^(.*?)(?<!\d)((?:19|20)\d{2})(?!\d)(.*)$", value)
    if match and match.group(1).strip():
        value = match.group(1).strip()
    # Remove release-date/update-date fragments and aliases in parentheses.
    value = re.sub(r"[（(](?:19|20)\d{2}(?:[-/.]\d{1,2}[-/.]\d{1,2})?[）)]", "", value)
    value = re.sub(r"[（(](?:[A-Za-z][^）)]*|B站版|国配[^）)]*|粤语[^）)]*)[）)]", "", value)
    value = re.sub(r"(?i)(?:4k|1080p|720p|hdr|dv|sdr|remux|bluray|web-?dl).*$", "", value)
    value = re.sub(
        r"(?:4K|1080P|720P|臻彩|杜比视界|国配|粤语|双声道|高码|原盘|特效字幕|"
        r"已刮削.*|更新至.*|更至.*|全\d+集|S\d+.*|第[一二三四五六七八九十\d]+季).*$",
        "",
        value,
        flags=re.I,
    )
    value = value.strip(" -_:：·")
    value = value.rstrip("（(").strip()
    value = re.sub(r"\s*(?:全|完)$", "", value).strip()
    return value


def _season_number(text: str) -> int | None:
    match = _SEASON_RE.search(text)
    if not match:
        return None
    raw = match.group(1) or match.group(2) or match.group(3)
    if raw and raw.isdigit():
        return int(raw)
    return _CN_NUMBERS.get(raw or "")


def parse_message_evidence(text: str) -> MessageEvidence | None:
    """Parse a standardized channel message.

    Returns ``None`` for generic/aggregate messages and for messages without a
    usable title.  A parsed evidence object is only a proposal; callers must
    still match it against an existing candidate and require a year.
    """

    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None

    field_title = _first_field(text, "名称|片名|标题")
    raw_title = field_title or lines[0]
    raw_title = re.sub(r"^(?:🎬|📺|🎥|🗂)\s*", "", raw_title).strip()
    if not raw_title:
        return None

    title_key = normalize_title(raw_title)
    if title_key in _GENERIC_TITLES or title_key.startswith("1080p") or title_key.startswith("jack"):
        return None

    year: int | None = None
    explicit_date = re.search(r"(?:发行时间|上映时间|首播|年份)\s*[:：]\s*((?:19|20)\d{2})", text)
    if explicit_date:
        year = int(explicit_date.group(1))

    # The first line may carry the year directly after the title:
    # 「笑傲江湖1996 吕颂贤 ...」 or 「仙武传 2023 4K...」.
    title = _strip_title_noise(raw_title)
    if not title:
        return None
    if year is None:
        year_match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", raw_title)
        if year_match:
            year = int(year_match.group(1))

    # Standardized means an explicit field block or an icon heading with a
    # year.  A bare title is not enough for this new evidence path.
    standardized = bool(
        (field_title and (explicit_date or year))
        or (re.match(r"^(?:🎬|📺|🎥|🗂)", text.strip()) and year)
    )
    if not standardized or year is None:
        return None

    # A season marker means the candidate's first-air year is not necessarily
    # the season year.  Season 1 is safe to treat as the work; later seasons
    # are left to manual review.
    season = _season_number(raw_title)
    type_hint = None
    if re.search(r"#电影|类型\s*[:：]\s*电影", text):
        type_hint = "movie"
    elif re.search(r"#电视剧|#剧集|类型\s*[:：]\s*电视剧", text):
        type_hint = "tv"
    return MessageEvidence(
        title=title,
        year=year,
        season=season,
        standardized=True,
        raw_title=raw_title,
        text=text,
        media_type_hint=type_hint,
    )


def _candidate_title(candidate: dict[str, Any]) -> str:
    return str(candidate.get("title") or candidate.get("name") or "")


def _candidate_year(candidate: dict[str, Any]) -> int | None:
    raw = candidate.get("release_date") or candidate.get("first_air_date") or ""
    match = re.match(r"((?:19|20)\d{2})", str(raw))
    return int(match.group(1)) if match else None


def _same_work_title(evidence_title: str, media_title: str) -> bool:
    left = normalize_title(evidence_title)
    right = normalize_title(media_title)
    if not left or not right:
        return False
    if left == right:
        return True
    # Allow the channel to add a short alias, e.g. 「毒液：致命守护者 (Venom)」
    # or 「Pluribus 同乐者」.  Do not allow an unrelated longer title.
    return left in right or right in left


def _consistent_evidence(connection: sqlite3.Connection, media_id: int) -> MessageEvidence | None:
    rows = connection.execute(
        """
        SELECT s.id AS source_id, e.raw_text
        FROM source_records s
        LEFT JOIN ingestion_links l ON l.source_key = s.source_key
        LEFT JOIN ingestion_events e ON e.id = l.ingestion_id
        WHERE s.media_id = ?
        ORDER BY s.id, e.published_at DESC
        """,
        (media_id,),
    ).fetchall()
    if not rows:
        return None

    by_source: dict[int, list[MessageEvidence]] = {}
    for row in rows:
        if not row["raw_text"]:
            continue
        evidence = parse_message_evidence(str(row["raw_text"]))
        if evidence:
            by_source.setdefault(int(row["source_id"]), []).append(evidence)
    if not by_source:
        return None

    # Every source must have at least one parseable message, and all parsed
    # messages must describe the same title/year/season.  This is what stops a
    # mixed legacy row (e.g. two different Doraemon films in one row) from
    # being silently bound to one of them.
    signatures: set[tuple[str, int | None, int | None]] = set()
    for source_id, _ in [(int(row["source_id"]), None) for row in rows]:
        if source_id not in by_source:
            return None
    for items in by_source.values():
        for item in items:
            signatures.add((normalize_title(item.title), item.year, item.season))
    if len(signatures) != 1:
        return None
    return next(iter(by_source.values()))[0]


def message_evidence_for_media(connection: sqlite3.Connection, media_id: int) -> MessageEvidence | None:
    """Return consistent message evidence for a work, if every source agrees."""

    return _consistent_evidence(connection, media_id)


def select_message_candidate(
    evidence: MessageEvidence,
    candidates: Iterable[dict[str, Any]],
    media_type: str,
) -> dict[str, Any] | None:
    """Pick one candidate confirmed by the message, or return ``None``.

    This intentionally requires an exact title and exact year.  It does not use
    the score alone, and it does not use a candidate whose title contains an
    extra qualifier such as 「总集版」 or 「之长安」.
    """

    if not evidence.standardized or evidence.year is None:
        return None
    if evidence.season is not None and evidence.season > 1:
        return None
    if evidence.title.endswith(_QUALIFIERS):
        return None

    year_matched = [
        candidate for candidate in candidates
        if _candidate_year(candidate) == evidence.year
    ]
    if not year_matched:
        return None

    def _exact(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [c for c in items if normalize_title(_candidate_title(c)) == normalize_title(evidence.title)]

    # Prefer the type stated by the message, then the local type, then an
    # exact title/year match across a legacy wrong local type.  The last step
    # is safe because both title and year must be exact and the candidate must
    # be unique; it repairs historical movie/tv flips such as 南京照相馆.
    if evidence.media_type_hint:
        typed = [c for c in year_matched if str(c.get("media_type") or "") == evidence.media_type_hint]
        exact = _exact(typed)
        if len(exact) == 1:
            return exact[0]
    typed = [c for c in year_matched if str(c.get("media_type") or "") == media_type]
    exact = _exact(typed)
    if len(exact) == 1:
        return exact[0]

    exact = _exact(year_matched)
    if len(exact) == 1:
        return exact[0]

    # A Latin alias can be embedded in a Chinese heading: Pluribus 同乐者.
    alias = []
    for candidate in year_matched:
        candidate_title = _candidate_title(candidate)
        if not re.fullmatch(r"[A-Za-z0-9 :\-]+", candidate_title):
            continue
        key = normalize_title(candidate_title)
        if key and key in normalize_title(evidence.title):
            alias.append(candidate)
    if len(alias) == 1:
        return alias[0]
    return None


def resolve_message_candidate(
    connection: sqlite3.Connection,
    media_id: int,
    candidates: Iterable[dict[str, Any]],
    media_type: str,
    media_title: str,
) -> tuple[dict[str, Any] | None, MessageEvidence | None]:
    """Return a confirmed candidate plus the evidence used to confirm it."""

    evidence = message_evidence_for_media(connection, media_id)
    if not evidence:
        return None, None
    if not _same_work_title(evidence.title, media_title):
        return None, evidence
    return select_message_candidate(evidence, candidates, media_type), evidence
