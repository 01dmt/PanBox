"""Read-only audit of historical movie/TV types.

The old parser used the channel heading icons (``📺`` / ``🎥`` / ``🎬``) as
type evidence.  That is not trustworthy: many channels put the same icon on
every post.  This module only reports what would be affected; it never writes
to the database.  Structural evidence (episodes, seasons, ``全集``, ``剧集``)
and explicit movie text protect rows from being treated as icon-only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .importers import parse_share_heading
from .repository import utc_now
from .schema import connect
from .tmdb import matching_candidates, normalize_details, score_candidate, should_auto_link
from .repository import link_tmdb

SAMPLE_LIMIT = 12

_ICON_TV = re.compile(r"^\s*📺")
_ICON_MOVIE = re.compile(r"^\s*(?:🎥|🎬)")
_TV_TEXT = re.compile(
    r"(?i)(?:S\d{1,2}\s*E\d{1,3}|S\d{1,2}(?![A-Za-z])|第\s*\d+\s*[季集]|"
    r"全\s*\d+\s*[集季]|共\s*\d+\s*季|更新|剧集|电视剧|连续剧|综艺|动漫|番剧|"
    r"全集|EP\s*\d+|E\s*\d+|更至|完结|\d+\s*集)"
)
_MOVIE_TEXT = re.compile(r"电影")
_AUDIO_EXT = re.compile(r"(?i)\.(?:flac|mp3|m4a|ape|wav|dsf|dff|aac|ogg|lrc)$")
_STRICT_BLOCKERS = {
    "collection", "name_conflict", "year_conflict", "year_unverified",
    "resource_conflict", "resource_incomplete", "resource_name_conflict",
    "resource_name_unverified", "verification_incomplete",
}


def _contexts(metadata_json: Any) -> list[str]:
    try:
        metadata = json.loads(metadata_json or "{}")
    except (TypeError, ValueError):
        return []
    context = metadata.get("context") if isinstance(metadata, dict) else None
    return [str(context)] if isinstance(context, str) and context.strip() else []


def _icon_type(contexts: list[str]) -> str | None:
    found: set[str] = set()
    for context in contexts:
        first = context.splitlines()[0].strip() if context.strip() else ""
        if _ICON_TV.match(first):
            found.add("tv")
        elif _ICON_MOVIE.match(first):
            found.add("movie")
    return next(iter(found)) if len(found) == 1 else None


def _candidate_year(release_date: Any) -> int | None:
    match = re.match(r"(\d{4})", str(release_date or ""))
    return int(match.group(1)) if match else None


def _strict_movie_candidate(media: Any, candidate: Any | None) -> bool:
    if not candidate or candidate["media_type"] != "movie" or float(candidate["score"] or 0) < 0.90:
        return False
    if media["year"] is None:
        return False
    try:
        evidence = json.loads(candidate["payload_json"] or "{}").get("_match_evidence") or {}
    except (TypeError, ValueError):
        evidence = {}
    if not evidence.get("primary_title_exact"):
        return False
    if any(evidence.get(key) for key in _STRICT_BLOCKERS):
        return False
    candidate_year = _candidate_year(candidate["release_date"])
    return candidate_year == int(media["year"])


def audit_media_types(*, limit: int | None = None, db_path: str | Path | None = None) -> dict[str, Any]:
    """Return an icon/type audit; this function never writes to the database."""
    result: dict[str, Any] = {
        "scanned": 0,
        "suspects": 0,
        "icon_only": {"tv": 0, "movie": 0},
        "candidate_movie": 0,
        "candidate_movie_ids": [],
        "candidate_tv": 0,
        "candidate_weak": 0,
        "no_candidate": 0,
        "protected_tv": 0,
        "protected_movie": 0,
        "no_icon": 0,
        "type_not_equal_icon": 0,
        "mixed_icons": 0,
        "music": 0,
        "skipped": 0,
        "samples": {"suspects": [], "candidate_movie": [], "candidate_tv": [], "protected_tv": [], "protected_movie": []},
    }
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT m.id, m.title, m.year, m.media_type, m.tmdb_status, m.is_bundle,
                   m.is_metadata, m.resource_kind,
                   (SELECT GROUP_CONCAT(COALESCE(s.metadata_json, ''), ' ||| ')
                      FROM source_records s WHERE s.media_id = m.id) AS metadata_json,
                   (SELECT GROUP_CONCAT(COALESCE(s.raw_label, ''), ' ||| ')
                      FROM source_records s WHERE s.media_id = m.id) AS raw_labels,
                   (SELECT GROUP_CONCAT(COALESCE(s.filename, ''), ' ||| ')
                      FROM source_records s WHERE s.media_id = m.id) AS filenames,
                   (SELECT MAX(CASE WHEN s.episode IS NOT NULL OR s.season_end IS NOT NULL
                                      OR (s.season IS NOT NULL AND s.season > 1) THEN 1 ELSE 0 END)
                      FROM source_records s WHERE s.media_id = m.id) AS strong_structure,
                   (SELECT MAX(CASE WHEN s.resource_class = 'music' THEN 1 ELSE 0 END)
                      FROM source_records s WHERE s.media_id = m.id) AS has_music
            FROM media_items m
            WHERE m.tmdb_status != 'matched' AND m.is_bundle = 0 AND m.is_metadata = 0
              AND m.resource_kind = 'media'
            ORDER BY m.id
            """
        ).fetchall()
        candidates = connection.execute(
            "SELECT media_id, media_type, score, release_date, payload_json FROM tmdb_candidates"
            " ORDER BY media_id, score DESC, id"
        ).fetchall()
    top_candidates: dict[int, Any] = {}
    for candidate in candidates:
        top_candidates.setdefault(int(candidate["media_id"]), candidate)

    for row in rows:
        if limit is not None and result["scanned"] >= limit:
            break
        result["scanned"] += 1
        current = str(row["media_type"] or "")
        if current not in {"tv", "movie"}:
            result["skipped"] += 1
            continue
        raw = " | ".join(x for x in (row["raw_labels"], row["filenames"]) if x)
        contexts = []
        for value in (row["metadata_json"] or "").split(" ||| "):
            contexts.extend(_contexts(value))
        context_text = " | ".join(contexts)
        text = f"{context_text} | {raw}"
        icon_types = {icon for icon in (_icon_type([context]) for context in contexts) if icon}
        if len(icon_types) > 1:
            result["mixed_icons"] += 1
            continue
        icons = next(iter(icon_types), None)
        if not icons:
            result["no_icon"] += 1
            continue
        if current != icons:
            result["type_not_equal_icon"] += 1
            continue
        if row["has_music"] or _AUDIO_EXT.search(raw):
            result["music"] += 1
            continue
        details: dict[str, Any] = {}
        first = contexts[0].splitlines()[0].strip() if contexts else ""
        if first:
            details = parse_share_heading(first)
        if (
            row["strong_structure"]
            or _TV_TEXT.search(text)
            or details.get("media_type") == "tv"
        ):
            result["protected_tv"] += 1
            if len(result["samples"]["protected_tv"]) < SAMPLE_LIMIT:
                result["samples"]["protected_tv"].append({"id": row["id"], "title": row["title"], "reason": "结构/剧集证据"})
            continue
        if _MOVIE_TEXT.search(text) or details.get("media_type") == "movie":
            result["protected_movie"] += 1
            if len(result["samples"]["protected_movie"]) < SAMPLE_LIMIT:
                result["samples"]["protected_movie"].append({"id": row["id"], "title": row["title"], "reason": "明确电影文本"})
            continue

        result["suspects"] += 1
        result["icon_only"][icons] += 1
        candidate = top_candidates.get(int(row["id"]))
        if not candidate:
            result["no_candidate"] += 1
        elif candidate["media_type"] == "tv":
            result["candidate_tv"] += 1
            if len(result["samples"]["candidate_tv"]) < SAMPLE_LIMIT:
                result["samples"]["candidate_tv"].append({"id": row["id"], "title": row["title"], "year": row["year"], "type": current})
        elif _strict_movie_candidate(row, candidate):
            result["candidate_movie"] += 1
            result["candidate_movie_ids"].append(int(row["id"]))
            if len(result["samples"]["candidate_movie"]) < SAMPLE_LIMIT:
                result["samples"]["candidate_movie"].append({
                    "id": row["id"], "title": row["title"], "year": row["year"],
                    "candidate": candidate["media_type"], "score": round(float(candidate["score"] or 0), 3),
                })
        else:
            result["candidate_weak"] += 1
        if len(result["samples"]["suspects"]) < SAMPLE_LIMIT:
            result["samples"]["suspects"].append({"id": row["id"], "title": row["title"], "year": row["year"], "type": current})
    return result


def auto_link_existing_candidates(
    *, limit: int | None = None, db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Link candidates that already satisfy the normal auto-link policy.

    This is deliberately separate from ``apply_movie_candidates``: changing a
    type is not the same action as writing a TMDB identity.  It reuses the
    stored candidates (no new search) and still applies every blocker and
    ambiguity rule from :func:`should_auto_link`.
    """
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT m.id, m.title, m.year, m.media_type, m.tmdb_status,
                   t.tmdb_id, t.media_type AS candidate_type, t.title AS candidate_title,
                   t.release_date, t.score, t.payload_json
            FROM media_items m
            JOIN tmdb_candidates t ON t.media_id = m.id
            WHERE m.tmdb_status != 'matched' AND m.is_bundle = 0 AND m.is_metadata = 0
              AND m.resource_kind = 'media'
            ORDER BY m.id, t.score DESC, t.id
            """
        ).fetchall()
    by_media: dict[int, list[Any]] = {}
    for row in rows:
        by_media.setdefault(int(row["id"]), []).append(row)
    linked = 0
    skipped = 0
    errors = 0
    for media_id, candidates in by_media.items():
        if limit is not None and linked + skipped + errors >= limit:
            break
        built = []
        for row in candidates:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            built.append({
                "tmdb_id": row["tmdb_id"],
                "media_type": row["candidate_type"],
                "title": row["candidate_title"],
                "release_date": row["release_date"],
                "score": row["score"],
                "match_evidence": payload.get("_match_evidence") or {},
                "payload": payload,
            })
        usable = matching_candidates(built)
        if not usable or not should_auto_link(usable):
            skipped += 1
            continue
        top = usable[0]
        if top["media_type"] != candidates[0]["candidate_type"]:
            skipped += 1
            continue
        payload = top.get("payload") or {}
        try:
            details = normalize_details(payload, top["media_type"])
            link_tmdb(media_id, details, top["score"], "auto", db_path)
        except (KeyError, TypeError, ValueError):
            errors += 1
            continue
        linked += 1
    return {"media": len(by_media), "linked": linked, "skipped": skipped, "errors": errors}


def rescore_movie_type_conflicts(
    *, limit: int | None = None, db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Recompute scores whose ``type_conflict`` came from the old tv type.

    The batch type fix changes ``media_type`` but candidate scores are stored at
    match time.  A record matched as tv got a -0.08 type penalty; after it is
    corrected to movie that score is stale.  Only rows with no remaining
    season/episode evidence are rescored, so real TV evidence is never erased.
    """
    rows: list[Any]
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT t.id AS candidate_id, t.media_id, t.media_type AS candidate_type,
                   t.title AS candidate_title, t.release_date AS candidate_release_date,
                   t.score, t.payload_json, m.title, m.year, m.media_type,
                   EXISTS(
                       SELECT 1 FROM source_records s
                        WHERE s.media_id = m.id
                          AND (s.season IS NOT NULL OR s.season_end IS NOT NULL OR s.episode IS NOT NULL)
                   ) AS has_season
            FROM tmdb_candidates t
            JOIN media_items m ON m.id = t.media_id
            WHERE m.media_type = 'movie'
              AND json_extract(t.payload_json, '$._match_evidence.type_conflict') = 1
            ORDER BY t.id
            """
        ).fetchall()
    updated = 0
    protected = 0
    skipped = 0
    for row in rows:
        if limit is not None and updated + protected + skipped >= limit:
            break
        if row["has_season"]:
            protected += 1
            continue
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            skipped += 1
            continue
        evidence = payload.get("_match_evidence") if isinstance(payload, dict) else None
        if not isinstance(evidence, dict) or not evidence.get("type_conflict"):
            skipped += 1
            continue
        candidate_title = payload.get("title") or payload.get("name") or row["candidate_title"] or ""
        candidate_original = payload.get("original_title") or payload.get("original_name")
        effective_year = evidence.get("effective_year") or evidence.get("tmdb_year")
        if effective_year is None and row["candidate_release_date"]:
            effective_year = int(str(row["candidate_release_date"])[:4]) if str(row["candidate_release_date"])[:4].isdigit() else None
        score = score_candidate(
            local_title=row["title"],
            local_year=row["year"],
            local_type="movie",
            candidate_title=candidate_title,
            candidate_original_title=candidate_original,
            candidate_year=effective_year,
            candidate_type=row["candidate_type"],
            popularity=float(payload.get("popularity") or 0),
            local_years=[row["year"]] if row["year"] else [],
            search_hits=evidence.get("search_hits") or [],
            explicit_match=bool(evidence.get("explicit_tmdb_id")),
            candidate_titles=evidence.get("verified_titles") or [],
        )
        if evidence.get("name_conflict") or evidence.get("resource_name_conflict"):
            score = 0.0
        evidence["type_conflict"] = False
        payload["_match_evidence"] = evidence
        with connect(db_path) as connection:
            connection.execute(
                "UPDATE tmdb_candidates SET score=?, payload_json=? WHERE id=?",
                (score, json.dumps(payload, ensure_ascii=False), row["candidate_id"]),
            )
        updated += 1
    return {"candidate_rows": len(rows), "updated": updated, "protected": protected, "skipped": skipped}


def apply_movie_candidates(
    *,
    audit: dict[str, Any] | None = None,
    limit: int | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Apply only the strict candidate_movie subset from the read-only audit.

    This deliberately does not write TMDB ids, reset review status, or touch
    the 75 TV-candidate / 248 weak / 181 no-candidate rows.  It only changes
    ``media_type`` to movie and clears the synthetic season fields that the old
    icon parser added to a movie share.
    """
    if audit is None:
        audit = audit_media_types(limit=limit, db_path=db_path)
    ids = [int(value) for value in audit.get("candidate_movie_ids") or []]
    updated = 0
    missing = 0
    with connect(db_path) as connection:
        for media_id in ids:
            row = connection.execute(
                "SELECT id FROM media_items WHERE id=? AND media_type='tv' AND tmdb_status!='matched'"
                " AND is_bundle=0 AND is_metadata=0 AND resource_kind='media'",
                (media_id,),
            ).fetchone()
            if not row:
                missing += 1
                continue
            connection.execute(
                "UPDATE media_items SET media_type='movie', updated_at=? WHERE id=?",
                (utc_now(), media_id),
            )
            connection.execute(
                "UPDATE source_records SET season=NULL, season_end=NULL, episode=NULL WHERE media_id=?",
                (media_id,),
            )
            updated += 1
    return {"candidate_movie": len(ids), "updated": updated, "missing": missing}


def describe_type_audit(result: dict[str, Any], *, read_only: bool = True) -> str:
    lines = [
        f"扫描 {result['scanned']} 条待匹配作品；图标单独决定类型 {result['suspects']} 条",
        f"  图标-only: tv={result['icon_only']['tv']} movie={result['icon_only']['movie']}",
        f"  其中高置信电影候选 {result['candidate_movie']} 条，剧集候选 {result['candidate_tv']} 条，"
        f"弱候选 {result['candidate_weak']} 条，无候选 {result['no_candidate']} 条",
        f"  受保护: 结构/剧集证据 {result['protected_tv']} 条，明确电影文本 {result['protected_movie']} 条",
        f"  跳过: 无图标 {result['no_icon']}，图标与当前类型不一致 {result['type_not_equal_icon']}，"
        f"混合图标 {result['mixed_icons']}，音乐 {result['music']}，其他 {result['skipped']}",
        "这是只读检查，未写数据库。" if read_only else "本次检查带写库参数，下方汇总实际写入结果。",
    ]
    for key, label in (
        ("candidate_movie", "高置信电影候选"),
        ("candidate_tv", "剧集候选"),
        ("suspects", "图标-only"),
        ("protected_tv", "受保护剧集"),
        ("protected_movie", "受保护电影"),
    ):
        for sample in result["samples"][key]:
            lines.append(f"  {label} #{sample['id']}: {sample['title']!r}" + (f" ({sample['year']})" if sample.get("year") else ""))
        if result.get(key) > len(result["samples"][key]):
            lines.append(f"  …另有 {result[key] - len(result['samples'][key])} 条{label}")
    return "\n".join(lines)
