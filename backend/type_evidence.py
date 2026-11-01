from __future__ import annotations

"""Evidence grading for locally inferred movie / TV types.

The channel prefix (📺 / 🎥 / 🎬) is presentation only.  Local type decisions
must come from structure: episode fields, episode ranges, season ranges, and
explicit text such as ``更新至04集``, ``S01E02``, ``剧场版`` or ``电影``.

This module deliberately keeps weak evidence weak.  In particular, a bare
``season=1`` is common both for a real whole-season pack and for a synthetic
value written by an old parser, so it can never promote a row to ``tv`` by
itself.  The report is read-only; ``apply_strong_type_evidence`` only writes
single-sided, strong evidence and never touches matched rows.
"""

import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .schema import connect


ICON_RE = re.compile(r"^\s*[📺🎥🎬]")
# Keep these patterns narrower than the parser's loose fallbacks.  A match here
# is allowed to change an existing local type.
TV_TEXT_RE = re.compile(
    r"(?i)(?:"
    r"S\d{1,2}\s*E\d{1,3}|"
    r"EP\s*\d{1,4}|"
    r"(?:更新|更)\s*[至到]?\s*第?\s*\d{1,4}\s*(?:集|话|期)?|"
    r"第\s*\d{1,4}\s*[集话期]|"
    r"第\s*[一二三四五六七八九十百]+\s*[集话期]|"
    r"全\s*\d*\s*集|全集|全\s*\d+\s*季|共\s*\d+\s*季|"
    r"第\s*[0-9一二三四五六七八九十百]+\s*季|"
    r"剧集|电视剧|连续剧|综艺|动漫|番剧"
    r")"
)
MOVIE_TEXT_RE = re.compile(r"剧场版|电影")
AUDIO_EXT_RE = re.compile(r"(?i)\.(?:flac|mp3|m4a|ape|wav|dsf|dff|aac|ogg|lrc)$")
AUDIO_DRAMA_RE = re.compile(r"有声剧|广播剧")
# These titles are collections/profiles rather than a single work; changing
# their type from a file-level hint is not safe.
EXCLUDE_CORRECTION_RE = re.compile(r"合集|系列|有声剧|广播剧|豆瓣|AV|成人|写真|ASMR|直播|舞女|付费合集")
NUMERIC_TITLE_RE = re.compile(r"^\s*\d{1,4}\s*$")


def _heading_values(connection, media_id: int) -> list[str]:
    values: list[str] = []
    for source in connection.execute(
        "SELECT raw_label, filename, metadata_json FROM source_records WHERE media_id = ?",
        (media_id,),
    ):
        try:
            metadata = json.loads(source["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        context = metadata.get("context")
        if isinstance(context, str) and context.strip():
            values.append(context.splitlines()[0].strip())
        for key in ("raw_label", "filename"):
            value = source[key]
            if value:
                values.append(str(value).splitlines()[0].strip())
    return list(dict.fromkeys(value for value in values if value))


def _add_reason(reasons: list[str], value: str) -> None:
    if value not in reasons:
        reasons.append(value)


def classify_media(connection, row: Any) -> dict[str, Any]:
    """Return evidence for one media row without changing the database.

    ``type`` is the evidence-derived type (``tv`` / ``movie`` / ``unknown``),
    while ``current_type`` is kept separately so callers can report conflicts.
    """
    current_type = str(row["media_type"] or "unknown")
    status = str(row["tmdb_status"] or "")
    base = {
        "id": int(row["id"]),
        "title": str(row["title"] or ""),
        "year": row["year"],
        "current_type": current_type,
        "type": "unknown",
        "confidence": "none",
        "reasons": [],
        "source": "none",
    }
    if status == "matched":
        base.update(type=current_type, confidence="tmdb", reasons=["tmdb_match"], source="tmdb")
        return base
    if str(row["resource_kind"] or "media") != "media":
        base.update(confidence="excluded", reasons=["non_media_resource"], source="resource_kind")
        return base
    if row["is_bundle"]:
        base.update(confidence="excluded", reasons=["bundle"], source="resource_kind")
        return base
    if row["is_metadata"]:
        base.update(confidence="excluded", reasons=["metadata_pack"], source="resource_kind")
        return base

    reasons: list[str] = []
    tv: list[str] = []
    movie: list[str] = []
    weak: list[str] = []
    icon = False

    # Explicit fields are stronger than text.  episode_range is the parser's
    # interval (e.g. 1-4); a single ``episode`` is still an explicit episode.
    for source in connection.execute(
        "SELECT season, season_end, episode, metadata_json FROM source_records WHERE media_id = ?",
        (base["id"],),
    ):
        season = source["season"]
        season_end = source["season_end"]
        episode = source["episode"]
        if episode is not None:
            # Old numeric fallbacks sometimes wrote a date (20240707) into
            # episode.  That is not an episode number and must stay weak.
            try:
                episode_number = int(episode)
            except (TypeError, ValueError):
                episode_number = 0
            if 0 < episode_number <= 9999:
                _add_reason(tv, f"episode:{episode}")
            else:
                _add_reason(weak, f"episode_date:{episode}")
        if season_end is not None:
            _add_reason(tv, f"season_end:{season_end}")
        elif season is not None and int(season) > 1:
            _add_reason(tv, f"season:{season}")
        elif season == 1:
            _add_reason(weak, "season:1")
        try:
            metadata = json.loads(source["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        episode_range = metadata.get("episode_range")
        if isinstance(episode_range, (list, tuple)) and len(episode_range) == 2:
            _add_reason(tv, f"episode_range:{episode_range[0]}-{episode_range[1]}")
        season_range = metadata.get("season_range")
        if isinstance(season_range, (list, tuple)) and len(season_range) == 2:
            _add_reason(tv, f"season_range:{season_range[0]}-{season_range[1]}")

    headings = _heading_values(connection, base["id"])
    for heading in headings:
        if ICON_RE.search(heading):
            icon = True
            _add_reason(weak, "icon_only")
        tv_match = TV_TEXT_RE.search(heading)
        if tv_match:
            _add_reason(tv, f"tv_text:{tv_match.group(0).strip()}")
        movie_match = MOVIE_TEXT_RE.search(heading)
        if movie_match:
            _add_reason(movie, f"movie_text:{movie_match.group(0).strip()}")

    # Audio dramas / radio plays may mention 动漫 or a season in their title,
    # but are not TV episodes.  An audio filename is likewise not enough to
    # decide the work's type; leave that to the existing music classification.
    if AUDIO_DRAMA_RE.search(base["title"]):
        base.update(confidence="weak", reasons=weak + ["audio_drama"], source="none")
        return base
    if AUDIO_EXT_RE.search(" ".join(headings)):
        base.update(confidence="weak", reasons=weak + ["audio_file"], source="none")
        return base

    if tv and movie:
        base.update(type="unknown", confidence="conflict", reasons=tv + movie, source="conflict")
        return base
    if tv:
        base.update(type="tv", confidence="strong", reasons=tv, source="fields" if any(r.startswith(("episode", "season")) for r in tv) else "heading")
        return base
    if movie:
        base.update(type="movie", confidence="strong", reasons=movie, source="heading")
        return base
    if weak:
        base.update(type="unknown", confidence="weak", reasons=weak, source="icon" if icon else "season")
        return base
    return base


def _candidate_rows(connection, limit: int | None = None) -> list[Any]:
    sql = """
        SELECT id, title, year, media_type, tmdb_status, resource_kind,
               is_bundle, is_metadata
        FROM media_items
        ORDER BY id
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    return list(connection.execute(sql, params))


def audit_type_evidence(db_path: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """Classify every (or the first N) media row and return a summary report."""
    with connect(db_path) as connection:
        rows = _candidate_rows(connection, limit)
        records = [classify_media(connection, row) for row in rows]

    groups: Counter[tuple[str, str, str]] = Counter()
    examples: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = f"{record['current_type']}:{record['confidence']}:{record['type']}"
        groups[key] += 1
        examples.setdefault(key, [])
        if len(examples[key]) < 5:
            examples[key].append(record)

    summary = [
        {"group": key, "count": count, "examples": examples[key]}
        for key, count in groups.most_common()
    ]
    return {
        "total": len(records),
        "groups": summary,
        "records": records,
    }


def apply_strong_type_evidence(
    db_path: str | None = None,
    limit: int | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply only single-sided strong evidence to unmatched media rows.

    Weak evidence (icon only, bare season=1, no evidence) and conflicting
    evidence are deliberately left untouched.  Existing matched rows are
    always skipped because their type comes from TMDB.
    """
    changes: list[dict[str, Any]] = []
    with connect(db_path) as connection:
        rows = _candidate_rows(connection, limit)
        for row in rows:
            evidence = classify_media(connection, row)
            if evidence["confidence"] != "strong" or evidence["type"] not in {"tv", "movie"}:
                continue
            if evidence["type"] == evidence["current_type"]:
                continue
            title = evidence["title"]
            if EXCLUDE_CORRECTION_RE.search(title):
                continue
            if evidence["type"] == "tv" and NUMERIC_TITLE_RE.match(title):
                # A bare numeric title is usually a parser artifact; the
                # episode text can still be real, but this is not safe enough
                # to change the type automatically.
                continue
            if evidence["type"] == "movie" and ("＋" in title or "+" in title):
                # e.g. 「排球少年＋剧场版」 is a mixed TV + movie collection.
                continue
            media_id = evidence["id"]
            now = datetime.now(timezone.utc).isoformat()
            if not dry_run:
                connection.execute(
                    "UPDATE media_items SET media_type = ?, updated_at = ? WHERE id = ?",
                    (evidence["type"], now, media_id),
                )
                if evidence["type"] == "movie":
                    # Only this function's strong evidence path reaches here;
                    # the old synthetic season fields are not real episode evidence.
                    connection.execute(
                        "UPDATE source_records SET season = NULL, season_end = NULL, episode = NULL WHERE media_id = ?",
                        (media_id,),
                    )
            changes.append(
                {
                    "id": media_id,
                    "title": evidence["title"],
                    "from": evidence["current_type"],
                    "to": evidence["type"],
                    "reasons": evidence["reasons"],
                }
            )
    return {"changed": len(changes), "changes": changes, "dry_run": dry_run}


LEGACY_MOVIE_SCORE = 0.90
_LEGACY_BLOCKERS = (
    "collection", "name_conflict", "year_conflict", "year_unverified",
    "resource_conflict", "resource_incomplete", "resource_name_conflict",
    "resource_name_unverified", "verification_incomplete",
)


def backfill_legacy_movie_types(
    *, dry_run: bool = True, limit: int | None = None, db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Fix old ``tv`` rows that only got that type from a synthetic season.

    The old parser often wrote ``season=1`` after seeing a channel icon and then
    classified the source as TV.  A high-confidence, same-title movie candidate
    is a safe correction only when the current parser finds no strong TV
    evidence (no real episode/season text) and no other blocker applies.  This
    is a historical backfill; normal imports already use the corrected parser.
    """
    changes: list[dict[str, Any]] = []
    skipped = 0
    scanned = 0
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT m.*, t.media_type AS candidate_type, t.title AS candidate_title,
                   t.release_date AS candidate_release_date, t.score, t.payload_json
            FROM media_items m
            JOIN tmdb_candidates t ON t.id = (
                SELECT id FROM tmdb_candidates
                WHERE media_id = m.id ORDER BY score DESC, id LIMIT 1
            )
            WHERE m.media_type = 'tv' AND m.tmdb_status != 'matched'
              AND m.resource_kind = 'media' AND m.is_bundle = 0 AND m.is_metadata = 0
              AND t.media_type = 'movie'
            ORDER BY m.id
            """
        ).fetchall()
        for row in rows:
            scanned += 1
            if limit is not None and len(changes) >= limit:
                break
            if float(row["score"] or 0) < LEGACY_MOVIE_SCORE:
                skipped += 1
                continue
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError):
                skipped += 1
                continue
            evidence = payload.get("_match_evidence") if isinstance(payload, dict) else None
            if not isinstance(evidence, dict):
                skipped += 1
                continue
            if not (evidence.get("title_exact") or evidence.get("primary_title_exact")):
                skipped += 1
                continue
            if any(evidence.get(key) for key in _LEGACY_BLOCKERS):
                skipped += 1
                continue
            local = classify_media(connection, row)
            if local["type"] == "tv" and local["confidence"] == "strong":
                skipped += 1
                continue
            media_id = int(row["id"])
            changes.append(
                {
                    "id": media_id,
                    "title": str(row["title"] or ""),
                    "from": "tv",
                    "to": "movie",
                    "score": float(row["score"] or 0),
                    "reasons": local["reasons"],
                }
            )
            if dry_run:
                continue
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "UPDATE media_items SET media_type = 'movie', updated_at = ? WHERE id = ? AND media_type = 'tv'",
                (now, media_id),
            )
            # Only synthetic season fields are removed; a real episode/season
            # would have made classify_media return strong TV and be skipped.
            connection.execute(
                "UPDATE source_records SET season = NULL, season_end = NULL, episode = NULL WHERE media_id = ?",
                (media_id,),
            )
    return {
        "scanned": scanned,
        "changed": len(changes),
        "skipped": skipped,
        "changes": changes,
        "dry_run": dry_run,
    }


def describe_type_evidence(result: dict[str, Any], *, applied: bool = False) -> str:
    lines = [f"共扫描 {result['total']} 条" if "total" in result else f"已修正 {result.get('changed', 0)} 条"]
    if "groups" in result:
        for group in result["groups"]:
            lines.append(f"{group['count']:5d}  {group['group']}")
            for example in group["examples"][:2]:
                lines.append(f"       #{example['id']} {example['title']}  {example['reasons'][:3]}")
    if applied and "changes" in result:
        for change in result["changes"]:
            lines.append(f"#{change['id']} {change['title']}: {change['from']} -> {change['to']}  {change['reasons']}")
    return "\n".join(lines)
