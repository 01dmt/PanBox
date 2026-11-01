from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .importers import ParsedSource, canonical_share_url, content_digest, detect_source_kind, iter_sources, normalize_title
from .schema import connect, init_db
from .resource_metadata import av_code, classify_metadata_pack, clean_person_name, infer_class
from .collection_markers import COLLECTION_RE
from .telegram_avatar import cache_channel_avatar, cached_channel_avatar, normalize_channel_username


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def refresh_metadata_flags(
    connection: sqlite3.Connection,
    media_ids: Iterable[int] | None = None,
) -> int:
    """Recompute ``media_items.is_metadata`` for the given (or all) works.

    ``is_metadata`` only hides unmatched auxiliary packs from the pending queue;
    a record with a confirmed TMDB identity is never hidden. If a pack later
    gains a playable source, it returns to ``pending`` so matching can retry.
    """
    ids = sorted({int(media_id) for media_id in media_ids}) if media_ids is not None else None
    if ids == []:
        return 0
    if ids is None:
        rows = connection.execute(
            """
            SELECT m.id, m.title, m.tmdb_status, m.resource_kind, m.is_metadata, m.metadata_reason, s.filename
            FROM media_items m
            LEFT JOIN source_records s ON s.media_id = m.id
            ORDER BY m.id, s.id
            """
        ).fetchall()
    else:
        placeholders = ",".join("?" * len(ids))
        rows = connection.execute(
            f"""
            SELECT m.id, m.title, m.tmdb_status, m.resource_kind, m.is_metadata, m.metadata_reason, s.filename
            FROM media_items m
            LEFT JOIN source_records s ON s.media_id = m.id
            WHERE m.id IN ({placeholders})
            ORDER BY m.id, s.id
            """,
            ids,
        ).fetchall()

    titles: dict[int, str] = {}
    statuses: dict[int, str] = {}
    kinds: dict[int, str] = {}
    previous: dict[int, tuple[int, str | None]] = {}
    files: dict[int, list[str]] = {}
    for row in rows:
        media_id = int(row["id"])
        titles[media_id] = str(row["title"] or "")
        statuses[media_id] = str(row["tmdb_status"] or "")
        kinds[media_id] = str(row["resource_kind"] or "media")
        previous[media_id] = (int(row["is_metadata"] or 0), row["metadata_reason"])
        files.setdefault(media_id, [])
        if row["filename"] is not None:
            files[media_id].append(row["filename"])

    changed = 0
    now = utc_now()
    for media_id, title in titles.items():
        is_metadata, reason = classify_metadata_pack(title, files.get(media_id, []))
        if kinds[media_id] != "media" or statuses[media_id] == "matched":
            # Person/series entries and confirmed TMDB links are not metadata packs.
            is_metadata, reason = False, None
        was_metadata = bool(previous[media_id][0])
        if was_metadata == is_metadata and previous[media_id][1] == reason:
            continue
        changed += 1
        connection.execute(
            "UPDATE media_items SET is_metadata = ?, metadata_reason = ?, updated_at = ? WHERE id = ?",
            (1 if is_metadata else 0, reason, now, media_id),
        )
        if was_metadata and not is_metadata and statuses[media_id] != "matched":
            connection.execute(
                """UPDATE media_items
                   SET tmdb_status='pending', last_tmdb_error=NULL,
                       match_confidence=NULL, match_method=NULL
                   WHERE id=?""",
                (media_id,),
            )
            connection.execute("DELETE FROM tmdb_candidates WHERE media_id = ?", (media_id,))
            connection.execute("DELETE FROM imdb_lookups WHERE media_id = ?", (media_id,))
    return changed


def _compatible_media(
    connection: sqlite3.Connection,
    source: ParsedSource,
) -> sqlite3.Row | None:
    normalized = normalize_title(source.title)
    if not normalized:
        return None

    if source.resource_kind == "person":
        # 人物条目按名字聚合：忽略年份（生日/文件名年份会让同一人裂成多条），
        # 优先命中已有人物条目；找不到时允许把同名占位条目提升为人物。
        rows = connection.execute(
            "SELECT * FROM media_items WHERE normalized_title = ? ORDER BY resource_kind NOT IN ('person','series'), id",
            (normalized,),
        ).fetchall()
        return rows[0] if rows else None

    if source.year is not None:
        rows = connection.execute(
            """
            SELECT * FROM media_items
            WHERE normalized_title = ? AND (year = ? OR year IS NULL)
            ORDER BY year IS NULL, id
            """,
            (normalized, source.year),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM media_items WHERE normalized_title = ? ORDER BY id",
            (normalized,),
        ).fetchall()

    if not rows:
        return None

    exact_type = [row for row in rows if row["media_type"] == source.media_type]
    if exact_type:
        return exact_type[0]

    unknown = [row for row in rows if row["media_type"] == "unknown"]
    if source.media_type != "unknown" and unknown:
        return unknown[0]

    if source.media_type == "unknown" and len(rows) == 1:
        return rows[0]

    return None


def ensure_series(connection: sqlite3.Connection, title: str) -> int:
    """按系列名归档，返回系列 id。

    频道给系列里的每一部单独发消息（标题只写「📺 007(系列)」），片名只存在于发布文件名里，
    所以作品仍是单片、各自关联 TMDB，系列只是它们的集合视图。
    """
    normalized = normalize_title(title)
    now = utc_now()
    row = connection.execute(
        "SELECT id FROM media_series WHERE normalized_title = ?", (normalized,)
    ).fetchone()
    if row:
        return int(row["id"])
    cursor = connection.execute(
        "INSERT INTO media_series (title, normalized_title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (title, normalized, now, now),
    )
    return int(cursor.lastrowid)


def attach_series(connection: sqlite3.Connection, media_id: int, title: str) -> int | None:
    """把作品挂到系列上；没有系列标记时不动。"""
    if not title:
        return None
    series_id = ensure_series(connection, title)
    connection.execute(
        "UPDATE media_items SET series_id = ?, updated_at = ? WHERE id = ? AND (series_id IS NULL OR series_id != ?)",
        (series_id, utc_now(), media_id, series_id),
    )
    return series_id


def is_bundle_source(source: ParsedSource) -> bool:
    """整包分享：带系列标记、又只有分享链接没有文件名的消息。

    频道通常一集/一部一条 ED2K（文件名能认出是哪一部）；只有整系列打成一个 115 分享时
    才拿不到文件名。115 目录列举已按风控关闭，拆不出成员，所以这种记录不能按单片匹配，
    否则会把「整个系列」错绑到其中某一部上。
    """
    if not source.metadata.get("collection"):
        return False
    return not str(source.filename or "").strip()


def ensure_media(connection: sqlite3.Connection, source: ParsedSource) -> int:
    # AV 番号是稳定的作品标识：标题只用番号，媒体类型未知，归 NSFW。
    # 在匹配前先归一化，避免同一部 AV 因日文宣传标题不同而裂成多条。
    code = av_code(source.raw_label) or av_code(source.title) or av_code(source.filename)
    if code:
        metadata = dict(source.metadata or {})
        metadata.pop('collection', None)
        metadata.pop('series_title', None)
        source = replace(
            source,
            title=code,
            media_type='unknown',
            resource_kind='media',
            metadata=metadata,
        )
    existing = _compatible_media(connection, source)
    now = utc_now()
    is_bundle = 1 if is_bundle_source(source) else 0
    if existing:
        media_type = existing["media_type"]
        if media_type == "unknown" and source.media_type != "unknown":
            media_type = source.media_type
        year = existing["year"] if existing["year"] is not None else source.year
        resource_kind = existing["resource_kind"] if existing["resource_kind"] in {"media", "person", "series"} else "media"
        # Keep an explicit profile/series identity when a later ordinary
        # source shares the same title. An explicit profile/series heading can
        # still promote an unclassified placeholder.
        if resource_kind == "media" and source.resource_kind in {"person", "series"}:
            resource_kind = source.resource_kind
        if resource_kind == "person":
            # 人物条目不保留年份：生日年份不是作品年份，还会拆散同一人物的聚合。
            year = None
        connection.execute(
            "UPDATE media_items SET media_type = ?, resource_kind = ?, year = ?,"
            " is_bundle = MAX(is_bundle, ?), updated_at = ? WHERE id = ?",
            (media_type, resource_kind, year, is_bundle, now, existing["id"]),
        )
        _attach_collection(connection, int(existing["id"]), source)
        return int(existing["id"])

    is_person = source.resource_kind == "person"
    # 人物条目在入库侧也清洗标题、去掉年份（解析层已处理，这里兜底），
    # 保证聚合键稳定：「浅野心，」和「浅野心」必须落进同一条目。
    title = (clean_person_name(source.title) or source.title) if is_person else source.title
    cursor = connection.execute(
        """
        INSERT INTO media_items (
            title, normalized_title, year, media_type, resource_kind, resource_metadata_json,
            is_bundle, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            normalize_title(title),
            None if is_person else source.year,
            source.media_type,
            source.resource_kind,
            json.dumps(source.metadata, ensure_ascii=False),
            is_bundle,
            now,
            now,
        ),
    )
    media_id = int(cursor.lastrowid)
    _attach_collection(connection, media_id, source)
    return media_id


def _attach_collection(connection: sqlite3.Connection, media_id: int, source: ParsedSource) -> None:
    """解析层标记了「属于某系列」的作品挂到系列上（人物不参与）。"""
    if source.resource_kind == "person":
        return
    if not source.metadata.get("collection"):
        return
    series_title = str(source.metadata.get("series_title") or source.title)
    attach_series(connection, media_id, series_title)


def _classify_source(connection: sqlite3.Connection, media_id: int, source: ParsedSource) -> str:
    """Classify one source, letting its media's collection identity win.

    A source of a collection can be an individual file (指环王1.mkv) whose own
    filename has no 合集 marker.  The media title/series membership still makes
    it part of the 合集 group.  Music and person classification always win.
    """
    inferred = infer_class(source.resource_kind, source.raw_label, source.media_type, source.filename)
    if inferred in {"music", "person", "nsfw"}:
        return inferred
    media = connection.execute(
        "SELECT title, resource_kind, is_bundle, series_id FROM media_items WHERE id = ?",
        (media_id,),
    ).fetchone()
    if media and (
        media["resource_kind"] == "series"
        or bool(media["is_bundle"])
        or media["series_id"] is not None
        or COLLECTION_RE.search(str(media["title"] or ""))
    ):
        return "collection"
    return inferred


def promote_collection_container(connection: sqlite3.Connection, media_id: int) -> bool:
    """Promote an unmatched whole-collection container to a series record.

    A collection heading does not automatically mean the media row is a
    container: ``指环王 全3部`` can still have one source per film.  Only
    promote when *all* sources are collection-classified and either the row is
    a filename-less bundle or it actually contains multiple sources.  Single
    film rows that merely inherited a collection title stay ordinary works.
    """
    row = connection.execute(
        "SELECT id, title, resource_kind, is_bundle, tmdb_status FROM media_items WHERE id = ?",
        (media_id,),
    ).fetchone()
    if not row or row["resource_kind"] != "media" or row["tmdb_status"] == "matched":
        return False
    counts = connection.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(resource_class = 'collection') AS collections
        FROM source_records WHERE media_id = ?
        """,
        (media_id,),
    ).fetchone()
    total = int(counts["total"] or 0)
    collections = int(counts["collections"] or 0)
    if not total or collections != total:
        return False
    # 「合集」是容器语义；「(系列)」不是。系列里的每一部常被来源分类器
    # 标成 collection（因为媒体标题带系列名），把它们升成 series 会把
    # 007/聊斋艳谭这类单片从作品库误删。只有标题或来源原文明确写了
    # 合集/合辑的容器才提升；单个文件（如 指环王 全3部 里的某一部）
    # 即使继承了合集标题也保留为作品。
    source_text = connection.execute(
        "SELECT COALESCE(GROUP_CONCAT(COALESCE(raw_label, '') || ' ' || COALESCE(raw_text, '')), '') FROM source_records WHERE media_id = ?",
        (media_id,),
    ).fetchone()[0]
    title_is_container = bool(
        re.search(r"(?:合集|合辑|合集包|合辑包|等等(?:电影|影片|资源))", f"{row['title'] or ''} {source_text or ''}")
    )
    if not title_is_container:
        return False
    if not bool(row["is_bundle"]) and total < 2:
        return False
    title = str(row["title"] or "").strip()
    if not title:
        return False
    series_id = ensure_series(connection, title)
    now = utc_now()
    connection.execute(
        """
        UPDATE media_items
        SET resource_kind = 'series', series_id = ?, media_type = 'unknown', year = NULL,
            tmdb_status = 'pending', tmdb_id = NULL, tmdb_media_type = NULL,
            tmdb_title = NULL, original_title = NULL, overview = NULL, poster_path = NULL,
            backdrop_path = NULL, release_date = NULL, vote_average = NULL, genres_json = NULL,
            origin_country_json = NULL, match_confidence = NULL, match_method = NULL,
            last_tmdb_error = NULL, metadata_reason = NULL, updated_at = ?
        WHERE id = ?
        """,
        (series_id, now, media_id),
    )
    connection.execute("DELETE FROM tmdb_candidates WHERE media_id = ?", (media_id,))
    connection.execute("DELETE FROM imdb_lookups WHERE media_id = ?", (media_id,))
    return True


def insert_source(
    connection: sqlite3.Connection,
    media_id: int,
    import_id: int,
    source: ParsedSource,
) -> bool:
    try:
        connection.execute(
            """
            INSERT INTO source_records (
                media_id, import_id, source_type, provider, source_key, raw_label, raw_text,
                url, filename, file_size, ed2k_hash, season, season_end, episode, parsed_year,
                quality, codec, hdr, audio, release_group, metadata_json, resource_class, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                media_id,
                import_id,
                source.source_type,
                source.provider,
                source.source_key,
                source.raw_label,
                source.raw_text,
                source.url,
                source.filename,
                source.file_size,
                source.ed2k_hash,
                source.season,
                source.season_end,
                source.episode,
                None if source.resource_kind == "person" else source.year,
                source.quality,
                source.codec,
                source.hdr,
                source.audio,
                source.release_group,
                json.dumps(source.metadata, ensure_ascii=False),
                _classify_source(connection, media_id, source),
                utc_now(),
            ),
        )
        return True
    except sqlite3.IntegrityError as exc:
        if "source_records.source_key" in str(exc):
            return False
        raise


def _refresh_duplicate_source(
    connection: sqlite3.Connection,
    source_id: int,
    source: ParsedSource,
) -> int | None:
    """Refresh parser-derived fields when a previously imported source improves."""
    row = connection.execute(
        """
        SELECT s.media_id, m.tmdb_id, m.tmdb_status, m.media_type, m.resource_kind
        FROM source_records s
        JOIN media_items m ON m.id = s.media_id
        WHERE s.id = ?
        """,
        (source_id,),
    ).fetchone()
    if not row:
        return None

    old_media_id = int(row["media_id"])
    media_id = old_media_id
    # A parser correction may move an old unknown placeholder into the proper
    # work. An explicit 👤 person heading is stronger than an old movie/tv
    # guess: the same files are often first posted under a media collection
    # heading and later re-posted under the person profile. Promote that
    # unmatched placeholder instead of leaving it as a bogus work.
    # Never move a source that already has an explicit or TMDB identity.
    if row["tmdb_id"] is None and row["tmdb_status"] != "matched" and (
        row["media_type"] == "unknown" or source.resource_kind == "person"
    ):
        media_id = ensure_media(connection, source)

    connection.execute(
        """
        UPDATE source_records
        SET media_id = ?, raw_label = ?, season = ?, season_end = ?, episode = ?, parsed_year = ?,
            quality = ?, codec = ?, hdr = ?, audio = ?, release_group = ?, metadata_json = ?,
            resource_class = CASE WHEN ? = 'person' THEN 'person' ELSE resource_class END
        WHERE id = ?
        """,
        (
            media_id,
            source.raw_label,
            source.season,
            source.season_end,
            source.episode,
            None if source.resource_kind == "person" else source.year,
            source.quality,
            source.codec,
            source.hdr,
            source.audio,
            source.release_group,
            json.dumps(source.metadata, ensure_ascii=False),
            source.resource_kind,
            source_id,
        ),
    )
    if media_id != old_media_id:
        # Keep every event that references this source pointing at its current
        # owner, not just the event that happened to be processed last.
        connection.execute(
            "UPDATE ingestion_links SET media_id = ? WHERE source_key = ?",
            (media_id, source.source_key),
        )

    # Remove an empty placeholder work created by the old parser, but only when
    # it has no confirmed identity or remaining sources.
    if old_media_id != media_id:
        connection.execute(
            """
            DELETE FROM media_items
            WHERE id = ? AND tmdb_id IS NULL AND tmdb_status != 'matched'
              AND NOT EXISTS (SELECT 1 FROM source_records WHERE media_id = media_items.id)
            """,
            (old_media_id,),
        )
        if not connection.execute("SELECT 1 FROM media_items WHERE id=?", (old_media_id,)).fetchone():
            connection.execute("DELETE FROM tmdb_candidates WHERE media_id=?", (old_media_id,))
            connection.execute("DELETE FROM imdb_lookups WHERE media_id=?", (old_media_id,))
    return media_id


def remove_cancelled_source(
    source_id: int,
    expected_url: str,
    expected_metadata: str | None,
    db_path: str | Path | None = None,
) -> dict[str, int]:
    result = {"removed": 0, "media_removed": 0}
    with connect(db_path) as connection:
        source = connection.execute(
            "SELECT media_id FROM source_records WHERE id = ? AND source_type = '115'",
            (source_id,),
        ).fetchone()
        if not source:
            return result
        media_id = int(source["media_id"])
        deleted = connection.execute(
            """DELETE FROM source_records WHERE id = ? AND media_id = ? AND source_type = '115'
            AND url = ? AND metadata_json IS ?""",
            (source_id, media_id, expected_url, expected_metadata),
        ).rowcount
        if not deleted:
            return result
        result["removed"] = 1
        item = connection.execute("SELECT tmdb_status, tmdb_id FROM media_items WHERE id = ?", (media_id,)).fetchone()
        if item and item["tmdb_status"] != "matched" and item["tmdb_id"] is None:
            remaining = connection.execute("SELECT 1 FROM source_records WHERE media_id = ? LIMIT 1", (media_id,)).fetchone()
            if not remaining:
                connection.execute("DELETE FROM media_items WHERE id = ?", (media_id,))
                result["media_removed"] = 1
            else:
                connection.execute("DELETE FROM tmdb_candidates WHERE media_id = ?", (media_id,))
                connection.execute("DELETE FROM imdb_lookups WHERE media_id = ?", (media_id,))
                connection.execute(
                    """UPDATE media_items SET tmdb_status='pending', last_tmdb_error=NULL,
                    match_confidence=NULL, match_method=NULL, updated_at=? WHERE id=?""",
                    (utc_now(), media_id),
                )
        if connection.execute("SELECT 1 FROM media_items WHERE id = ?", (media_id,)).fetchone():
            refresh_metadata_flags(connection, {media_id})
    return result


def import_content(
    content: str,
    source_name: str,
    source_path: str | None = None,
    kind: str = "auto",
    db_path: str | Path | None = None,
    parsed_sources: Iterable[ParsedSource] | None = None,
) -> dict[str, Any]:
    init_db(db_path)
    actual_kind = detect_source_kind(content) if kind == "auto" else kind
    digest = content_digest(content)
    now = utc_now()
    total = inserted = duplicates = errors = 0
    inserted_source_keys: list[str] = []
    duplicate_source_keys: list[str] = []
    error_source_keys: list[str] = []
    affected_media_ids: set[int] = set()

    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO imports (
                source_name, source_path, source_kind, content_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (source_name, source_path, actual_kind, digest, now),
        )
        import_id = int(cursor.lastrowid)

        sources = parsed_sources if parsed_sources is not None else iter_sources(content, actual_kind)
        for source in sources:
            total += 1
            try:
                # Parser upgrades must not create empty works for an already imported source.
                source_keys = {source.source_key}
                if source.source_key.startswith("115:"):
                    raw_url = source.source_key[4:]
                    source_keys.add("115:" + canonical_share_url(raw_url))
                    source_keys.add("115:" + canonical_share_url(raw_url) + "#")
                existing = connection.execute(
                    "SELECT id, media_id FROM source_records WHERE source_key IN (%s)"
                    % ",".join("?" * len(source_keys)),
                    tuple(source_keys),
                ).fetchone()
                if existing:
                    duplicates += 1
                    duplicate_source_keys.append(source.source_key)
                    affected_media_ids.add(int(existing["media_id"]))
                    moved_media_id = _refresh_duplicate_source(connection, int(existing["id"]), source)
                    if moved_media_id is not None:
                        affected_media_ids.add(int(moved_media_id))
                    continue
                media_id = ensure_media(connection, source)
                affected_media_ids.add(int(media_id))
                if insert_source(connection, media_id, import_id, source):
                    inserted += 1
                    inserted_source_keys.append(source.source_key)
                    promote_collection_container(connection, media_id)
                else:
                    duplicates += 1
                    duplicate_source_keys.append(source.source_key)
            except Exception:
                errors += 1
                error_source_keys.append(source.source_key)

        refresh_metadata_flags(connection, affected_media_ids)
        connection.execute(
            """
            UPDATE imports
            SET total_records = ?, inserted_records = ?, duplicate_records = ?, error_records = ?
            WHERE id = ?
            """,
            (total, inserted, duplicates, errors, import_id),
        )

    return {
        "import_id": import_id,
        "source_name": source_name,
        "source_kind": actual_kind,
        "sha256": digest,
        "total": total,
        "inserted": inserted,
        "duplicates": duplicates,
        "errors": errors,
        "inserted_source_keys": inserted_source_keys,
        "duplicate_source_keys": duplicate_source_keys,
        "error_source_keys": error_source_keys,
        "media_ids": sorted(affected_media_ids),
    }


def import_file(
    path: str | Path,
    kind: str = "auto",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    source_path = Path(path).expanduser().resolve()
    content = source_path.read_text(encoding="utf-8-sig")
    return import_content(
        content=content,
        source_name=source_path.name,
        source_path=str(source_path),
        kind=kind,
        db_path=db_path,
    )


def get_stats(db_path: str | Path | None = None) -> dict[str, Any]:
    with connect(db_path) as connection:
        media = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(tmdb_status = 'matched') AS matched,
                SUM(tmdb_status != 'matched' AND resource_kind NOT IN ('person','series') AND is_bundle = 0
                    AND NOT EXISTS (SELECT 1 FROM source_records sm WHERE sm.media_id = media_items.id AND sm.resource_class = 'music')
                    AND NOT EXISTS (SELECT 1 FROM source_records sx WHERE sx.media_id = media_items.id AND sx.resource_class = 'nsfw')) AS pending,
                SUM(tmdb_status = 'review' AND resource_kind NOT IN ('person','series')
                    AND NOT EXISTS (SELECT 1 FROM source_records sm WHERE sm.media_id = media_items.id AND sm.resource_class = 'music')
                    AND NOT EXISTS (SELECT 1 FROM source_records sx WHERE sx.media_id = media_items.id AND sx.resource_class = 'nsfw')) AS review,
                SUM(tmdb_status = 'not_found' AND resource_kind NOT IN ('person','series') AND is_bundle = 0
                    AND NOT EXISTS (SELECT 1 FROM source_records sm WHERE sm.media_id = media_items.id AND sm.resource_class = 'music')
                    AND NOT EXISTS (SELECT 1 FROM source_records sx WHERE sx.media_id = media_items.id AND sx.resource_class = 'nsfw')) AS not_found,
                SUM(tmdb_status = 'pending' AND resource_kind NOT IN ('person','series')
                    AND NOT EXISTS (SELECT 1 FROM source_records sm WHERE sm.media_id = media_items.id AND sm.resource_class = 'music')
                    AND NOT EXISTS (SELECT 1 FROM source_records sx WHERE sx.media_id = media_items.id AND sx.resource_class = 'nsfw')) AS unsearched,
                SUM(tmdb_status = 'error' AND resource_kind NOT IN ('person','series')
                    AND NOT EXISTS (SELECT 1 FROM source_records sm WHERE sm.media_id = media_items.id AND sm.resource_class = 'music')
                    AND NOT EXISTS (SELECT 1 FROM source_records sx WHERE sx.media_id = media_items.id AND sx.resource_class = 'nsfw')) AS error,
                SUM(media_type = 'movie') AS movies,
                SUM(media_type = 'tv') AS tv,
                SUM(media_type = 'unknown') AS unknown,
                SUM(EXISTS (SELECT 1 FROM source_records s WHERE s.media_id = media_items.id)) AS with_sources,
                SUM(NOT EXISTS (SELECT 1 FROM source_records s WHERE s.media_id = media_items.id)) AS without_sources,
                (SELECT COUNT(*) FROM media_items WHERE is_metadata = 1 AND resource_kind = 'media') AS metadata
            FROM media_items
            WHERE is_metadata = 0 AND resource_kind NOT IN ('person','series')
            """
        ).fetchone()
        sources = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(source_type = '115') AS share_115,
                SUM(source_type = '123') AS share_123,
                SUM(source_type = 'guangya') AS share_guangya,
                SUM(source_type = 'ed2k') AS ed2k,
                SUM(file_size) AS bytes
            FROM source_records
            """
        ).fetchone()
        provider_rows = connection.execute(
            """
            SELECT CASE WHEN source_type = 'ed2k' THEN 'ed2k' ELSE provider END AS provider_id,
                   COUNT(*) AS total
            FROM source_records
            GROUP BY provider_id
            ORDER BY provider_id
            """
        ).fetchall()
        duplicates = connection.execute(
            """
            SELECT COUNT(*) AS total FROM (
                SELECT media_id FROM source_records GROUP BY media_id HAVING COUNT(*) > 1
            )
            """
        ).fetchone()
        imports = connection.execute("SELECT COUNT(*) AS total FROM imports").fetchone()

    return {
        "media": {key: int(media[key] or 0) for key in media.keys()},
        "sources": {
            "total": int(sources["total"] or 0),
            "share_115": int(sources["share_115"] or 0),
            "share_123": int(sources["share_123"] or 0),
            "share_guangya": int(sources["share_guangya"] or 0),
            "ed2k": int(sources["ed2k"] or 0),
            "bytes": int(sources["bytes"] or 0),
            "providers": [{"id": row["provider_id"], "count": int(row["total"] or 0)} for row in provider_rows],
        },
        "multi_source": int(duplicates["total"] or 0),
        "imports": int(imports["total"] or 0),
    }


def _dashboard_event_counts(row: sqlite3.Row | None) -> dict[str, Any]:
    keys = ("received", "imported", "duplicate", "ignored", "failed", "pending_validation", "queued")
    values = {key: int(row[key] or 0) if row is not None else 0 for key in keys}
    values["pending"] = values["pending_validation"] + values["queued"]
    processed = values["imported"] + values["duplicate"] + values["ignored"] + values["failed"]
    values["processed"] = processed
    values["success_rate"] = round(values["imported"] / processed, 4) if processed else None
    return values


def _dashboard_avatar_url(username: str | None) -> str | None:
    """Only reuse avatars already on disk so the dashboard never waits on Telegram."""
    normalized = normalize_channel_username(username)
    if not normalized or not cached_channel_avatar(normalized):
        return None
    return f"/api/channel-avatar/{normalized}"


def get_dashboard_overview(db_path: str | Path | None = None, days: int = 30) -> dict[str, Any]:
    """Aggregate read-only dashboard data from local state.

    "Today" and the trend use the server's local calendar day so the numbers
    match what an operator sees in the ingestion records page. The previous
    day is compared at the same clock time, because comparing a partial day
    with a finished one would always look like a collapse.
    """
    days = min(max(int(days), 7), 90)
    local_now = datetime.now().astimezone()
    today = local_now.date()
    yesterday = today - timedelta(days=1)
    start_day = today - timedelta(days=days - 1)
    start_utc = datetime.combine(start_day, time.min).astimezone(timezone.utc).isoformat(timespec="seconds")
    same_time = local_now.strftime("%H:%M:%S")

    with connect(db_path) as connection:
        today_counts = _dashboard_event_counts(connection.execute(
            """
            SELECT COUNT(*) AS received,
                   COALESCE(SUM(status = 'imported'), 0) AS imported,
                   COALESCE(SUM(status = 'duplicate'), 0) AS duplicate,
                   COALESCE(SUM(status = 'ignored'), 0) AS ignored,
                   COALESCE(SUM(status = 'failed'), 0) AS failed,
                   COALESCE(SUM(status = 'pending_validation'), 0) AS pending_validation,
                   COALESCE(SUM(status = 'queued'), 0) AS queued
            FROM ingestion_events
            WHERE date(received_at, 'localtime') = ?
            """,
            (today.isoformat(),),
        ).fetchone())
        yesterday_counts = _dashboard_event_counts(connection.execute(
            """
            SELECT COUNT(*) AS received,
                   COALESCE(SUM(status = 'imported'), 0) AS imported,
                   COALESCE(SUM(status = 'duplicate'), 0) AS duplicate,
                   COALESCE(SUM(status = 'ignored'), 0) AS ignored,
                   COALESCE(SUM(status = 'failed'), 0) AS failed,
                   COALESCE(SUM(status = 'pending_validation'), 0) AS pending_validation,
                   COALESCE(SUM(status = 'queued'), 0) AS queued
            FROM ingestion_events
            WHERE date(received_at, 'localtime') = ?
              AND datetime(received_at, 'localtime') <= ?
            """,
            (yesterday.isoformat(), f"{yesterday.isoformat()} {same_time}"),
        ).fetchone())

        trend_rows = connection.execute(
            """
            SELECT date(received_at, 'localtime') AS day,
                   COUNT(*) AS received,
                   COALESCE(SUM(status = 'imported'), 0) AS imported,
                   COALESCE(SUM(status = 'duplicate'), 0) AS duplicate,
                   COALESCE(SUM(status = 'ignored'), 0) AS ignored,
                   COALESCE(SUM(status = 'failed'), 0) AS failed,
                   COALESCE(SUM(status = 'pending_validation'), 0) AS pending_validation,
                   COALESCE(SUM(status = 'queued'), 0) AS queued
            FROM ingestion_events
            WHERE received_at >= ?
            GROUP BY day
            """,
            (start_utc,),
        ).fetchall()

        media = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(tmdb_status = 'matched'), 0) AS matched,
                   COALESCE(SUM(tmdb_status != 'matched'), 0) AS unmatched,
                   COALESCE(SUM(tmdb_status = 'review'), 0) AS review,
                   COALESCE(SUM(media_type = 'movie'), 0) AS movies,
                   COALESCE(SUM(media_type = 'tv'), 0) AS tv
            FROM media_items
            WHERE is_metadata = 0 AND resource_kind NOT IN ('person','series')
            """
        ).fetchone()
        sources = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(file_size), 0) AS bytes,
                   COALESCE(SUM(date(created_at, 'localtime') = ?), 0) AS added_today
            FROM source_records
            """,
            (today.isoformat(),),
        ).fetchone()
        providers = connection.execute(
            """
            SELECT CASE WHEN source_type = 'ed2k' THEN 'ed2k' ELSE provider END AS provider_id,
                   COUNT(*) AS total
            FROM source_records
            GROUP BY provider_id
            ORDER BY total DESC, provider_id
            """
        ).fetchall()
        backlog = connection.execute(
            """
            SELECT COALESCE(SUM(status = 'failed'), 0) AS failed,
                   COALESCE(SUM(status = 'pending_validation'), 0) AS pending_validation,
                   COALESCE(SUM(status = 'queued'), 0) AS queued
            FROM ingestion_events
            """
        ).fetchone()
        link_health = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(status = 'inserted'), 0) AS inserted,
                   COALESCE(SUM(status = 'duplicate'), 0) AS duplicate,
                   COALESCE(SUM(status = 'invalid'), 0) AS invalid,
                   COALESCE(SUM(status = 'pending_validation'), 0) AS pending,
                   COALESCE(SUM(status = 'error'), 0) AS errors
            FROM ingestion_links
            """
        ).fetchone()
        channel_rows = connection.execute(
            """
            SELECT COALESCE(NULLIF(channel_username, ''), NULLIF(channel_id, ''), channel_name, 'unknown') AS channel_key,
                   MAX(channel_name) AS channel_name,
                   MAX(channel_username) AS channel_username,
                   COUNT(*) AS received,
                   COALESCE(SUM(status = 'imported'), 0) AS imported,
                   MAX(received_at) AS last_received_at
            FROM ingestion_events
            WHERE received_at >= ?
            GROUP BY channel_key
            ORDER BY received DESC
            LIMIT 6
            """,
            (start_utc,),
        ).fetchall()

        recent_rows = connection.execute(
            """
            SELECT id, service, channel_name, channel_username, status, error, received_at, media_ids_json
            FROM ingestion_events
            ORDER BY received_at DESC
            LIMIT 8
            """
        ).fetchall()
        recent_ids = [row["id"] for row in recent_rows]
        recent_media_ids: set[int] = set()
        parsed_media_ids: dict[str, list[int]] = {}
        for row in recent_rows:
            try:
                values = json.loads(row["media_ids_json"] or "[]")
            except (TypeError, json.JSONDecodeError):
                values = []
            ids = [int(value) for value in values if isinstance(value, int) or str(value).isdigit()]
            parsed_media_ids[row["id"]] = ids
            recent_media_ids.update(ids)

        media_details: dict[int, dict[str, Any]] = {}
        if recent_media_ids:
            placeholders = ",".join("?" * len(recent_media_ids))
            for row in connection.execute(
                f"""
                SELECT id, COALESCE(NULLIF(tmdb_title, ''), title) AS title,
                       COALESCE(year, CASE
                           WHEN substr(release_date, 1, 4) GLOB '[12][0-9][0-9][0-9]'
                           THEN CAST(substr(release_date, 1, 4) AS INTEGER) END) AS year
                FROM media_items WHERE id IN ({placeholders})
                """,
                tuple(recent_media_ids),
            ).fetchall():
                media_details[int(row["id"])] = {"id": int(row["id"]), "title": row["title"], "year": row["year"]}

        recent_links: dict[str, dict[str, int]] = {}
        if recent_ids:
            placeholders = ",".join("?" * len(recent_ids))
            for row in connection.execute(
                f"""
                SELECT ingestion_id, status, COUNT(*) AS total
                FROM ingestion_links
                WHERE ingestion_id IN ({placeholders})
                GROUP BY ingestion_id, status
                """,
                tuple(recent_ids),
            ).fetchall():
                bucket = recent_links.setdefault(row["ingestion_id"], {"total": 0})
                bucket[row["status"]] = int(row["total"] or 0)
                bucket["total"] += int(row["total"] or 0)

    trend_by_day = {row["day"]: _dashboard_event_counts(row) for row in trend_rows}
    trend = []
    for offset in range(days):
        day = (start_day + timedelta(days=offset)).isoformat()
        entry = trend_by_day.get(day) or _dashboard_event_counts(None)
        trend.append({"date": day, **entry})

    channels = [
        {
            "id": row["channel_key"],
            "name": row["channel_name"] or row["channel_username"] or row["channel_key"],
            "username": normalize_channel_username(row["channel_username"]),
            "avatar_url": _dashboard_avatar_url(row["channel_username"]),
            "received": int(row["received"] or 0),
            "imported": int(row["imported"] or 0),
            "last_received_at": row["last_received_at"],
        }
        for row in channel_rows
    ]

    recent = []
    for row in recent_rows:
        links = recent_links.get(row["id"], {"total": 0})
        recent.append({
            "id": row["id"],
            "service": row["service"],
            "channel_name": row["channel_name"] or row["channel_username"] or "未知频道",
            "channel_username": normalize_channel_username(row["channel_username"]),
            "avatar_url": _dashboard_avatar_url(row["channel_username"]),
            "status": row["status"],
            "error": row["error"],
            "received_at": row["received_at"],
            "media": [media_details[mid] for mid in parsed_media_ids.get(row["id"], []) if mid in media_details],
            "links": links,
        })

    provider_rows = [{"id": row["provider_id"], "count": int(row["total"] or 0)} for row in providers]
    return {
        "generated_at": local_now.isoformat(timespec="seconds"),
        "days": days,
        "today": {**today_counts, "date": today.isoformat()},
        "yesterday": {**yesterday_counts, "date": yesterday.isoformat(), "same_time": same_time},
        "trend": trend,
        "media": {
            "total": int(media["total"] or 0),
            "matched": int(media["matched"] or 0),
            "unmatched": int(media["unmatched"] or 0),
            "review": int(media["review"] or 0),
            "movies": int(media["movies"] or 0),
            "tv": int(media["tv"] or 0),
            "match_rate": round(int(media["matched"] or 0) / int(media["total"]), 4) if media["total"] else None,
        },
        "sources": {
            "total": int(sources["total"] or 0),
            "bytes": int(sources["bytes"] or 0),
            "added_today": int(sources["added_today"] or 0),
            "providers": provider_rows,
        },
        "backlog": {
            "failed_events": int(backlog["failed"] or 0),
            "pending_events": int(backlog["pending_validation"] or 0) + int(backlog["queued"] or 0),
            "invalid_links": int(link_health["invalid"] or 0),
            "error_links": int(link_health["errors"] or 0),
            "pending_links": int(link_health["pending"] or 0),
            "checked_links": int(link_health["inserted"] or 0) + int(link_health["duplicate"] or 0)
            + int(link_health["invalid"] or 0) + int(link_health["errors"] or 0),
            "total_links": int(link_health["total"] or 0),
        },
        "channels": channels,
        "recent": recent,
    }


MEDIA_YEAR_SQL = """COALESCE(m.year, CASE
    WHEN substr(m.release_date, 1, 4) GLOB '[12][0-9][0-9][0-9]'
    THEN CAST(substr(m.release_date, 1, 4) AS INTEGER) END)"""
GENRES_SQL = "CASE WHEN json_valid(m.genres_json) THEN CASE WHEN json_type(m.genres_json)='array' THEN m.genres_json ELSE '[]' END ELSE '[]' END"
COUNTRIES_SQL = "CASE WHEN json_valid(m.origin_country_json) THEN CASE WHEN json_type(m.origin_country_json)='array' THEN m.origin_country_json ELSE '[]' END ELSE '[]' END"
GENRE_VALUE_SQL = "CASE WHEN genre.type='object' THEN genre.value ELSE '{}' END"
QUALITY_GROUPS = {
    "2160P": ("2160P", "4K", "UHD"), "1080P": ("1080P",),
    "1080I": ("1080I",), "720P": ("720P",), "480P": ("480P",),
}
CODEC_GROUPS = {"h265": ("H.265", "H265", "X265", "HEVC"),
                "h264": ("H.264", "H264", "X264", "AVC"), "av1": ("AV1",)}


def get_media_filters(db_path: str | Path | None = None) -> dict[str, Any]:
    """Inventory-wide options from cached metadata only, not remote searches."""
    with connect(db_path) as connection:
        years = connection.execute(f"SELECT DISTINCT {MEDIA_YEAR_SQL} AS year FROM media_items m WHERE {MEDIA_YEAR_SQL} IS NOT NULL ORDER BY year DESC").fetchall()
        genres = connection.execute(f"""
            SELECT json_extract({GENRE_VALUE_SQL}, '$.id') AS genre_id,
                   MAX(json_extract({GENRE_VALUE_SQL}, '$.name')) AS name
            FROM media_items m, json_each({GENRES_SQL}) genre
            WHERE json_type({GENRE_VALUE_SQL}, '$.id')='integer'
              AND json_type({GENRE_VALUE_SQL}, '$.name')='text'
            GROUP BY genre_id ORDER BY name, genre_id
        """).fetchall()
        countries = connection.execute(f"""
            SELECT DISTINCT UPPER(country.value) AS code
            FROM media_items m, json_each({COUNTRIES_SQL}) country
            WHERE country.type='text' AND country.value GLOB '[A-Za-z][A-Za-z]'
            ORDER BY code
        """).fetchall()
    return {"years": [row["year"] for row in years], "genres": [{"id": row["genre_id"], "name": row["name"]} for row in genres],
            "countries": [row["code"] for row in countries]}


SORTS = {
    "updated_desc": "m.updated_at DESC, m.id DESC",
    "vote_desc": "m.vote_average IS NULL, m.vote_average DESC, m.updated_at DESC, m.id DESC",
    "title_asc": "m.normalized_title ASC, m.year DESC, m.id DESC",
    "year_desc": "media_year IS NULL, media_year DESC, m.normalized_title ASC, m.id DESC",
    "year_asc": "media_year IS NULL, media_year ASC, m.normalized_title ASC, m.id DESC",
    "sources_desc": "matched_source_count DESC, source_count DESC, m.normalized_title ASC, m.id DESC",
    "confidence_asc": "m.match_confidence IS NULL, m.match_confidence ASC, m.normalized_title ASC, m.id DESC",
}


def list_media(
    *,
    query: str = "",
    status: str = "all",
    media_type: str = "all",
    category: str = "all",
    resource_kind: str = "all",
    source_type: str = "all",
    availability: str = "all",
    year: str = "all",
    genre: str = "all",
    country: str = "all",
    quality: str = "all",
    codec: str = "all",
    hdr: str = "all",
    multi_source_only: bool = False,
    sort: str = "updated_desc",
    page: int = 1,
    page_size: int = 30,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 10), 100)
    where = ["m.is_metadata = 0", "m.resource_kind != 'series'"]
    params: list[Any] = []
    if availability not in {"all", "available", "missing"}:
        raise ValueError("资源可用性筛选无效。")
    if quality not in {"all", "unknown", *QUALITY_GROUPS}:
        raise ValueError("清晰度筛选无效。")
    if codec not in {"all", "unknown", *CODEC_GROUPS}:
        raise ValueError("视频编码筛选无效。")
    if hdr not in {"all", "unknown", "dv", "hdr", "sdr"}:
        raise ValueError("动态范围筛选无效。")

    query = query.strip()
    if query:
        def escape_like(value: str) -> str:
            return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        needle = f"%{escape_like(query)}%"
        normalized_title = normalize_title(query)
        normalized = f"%{normalized_title}%" if normalized_title else ""
        filename_needle = "%" + "%".join(escape_like(part) for part in query.split()) + "%"
        source_json = "CASE WHEN json_valid(sx.metadata_json) THEN sx.metadata_json ELSE '{}' END"
        where.append(
            f"""(
                m.title LIKE ? ESCAPE '\\' OR m.tmdb_title LIKE ? ESCAPE '\\' OR m.original_title LIKE ? ESCAPE '\\'
                OR m.normalized_title LIKE ? OR CAST(m.tmdb_id AS TEXT) = ? OR CAST(m.year AS TEXT) = ?
                OR EXISTS (
                    SELECT 1 FROM source_records sx
                    WHERE sx.media_id = m.id AND (
                        sx.filename LIKE ? ESCAPE '\\' OR sx.raw_label LIKE ? ESCAPE '\\'
                        OR json_extract({source_json}, '$.extra') LIKE ? ESCAPE '\\'
                        OR EXISTS (
                            SELECT 1 FROM json_tree({source_json}, '$.share_snapshot') cached
                            WHERE json_extract({source_json}, '$.share_snapshot.status') = 'ok'
                            AND cached.type = 'text' AND cached.atom LIKE ? ESCAPE '\\'
                            AND (
                                (cached.key = 'share_title' AND cached.path = '$.share_snapshot')
                                OR cached.path IN ('$.share_snapshot.names', '$.share_snapshot.search_names', '$.share_snapshot.root_names',
                                                   '$.share_snapshot."search_names"', '$.share_snapshot."root_names"')
                                OR (cached.key IN ('name', 'path') AND cached.path LIKE '$.share_snapshot.files[%]')
                            )
                        )
                    )
                )
                OR EXISTS (
                    SELECT 1 FROM imdb_lookups il, json_each(il.evidence_json, '$.matches') im
                    WHERE il.media_id = m.id AND (
                        json_extract(im.value, '$.title') LIKE ? ESCAPE '\\' OR json_extract(im.value, '$.imdb_id') = ?
                    )
                )
            )"""
        )
        params.extend([needle, needle, needle, normalized, query, query,
                       filename_needle, needle, filename_needle, filename_needle, needle, query])

    if status == "matched":
        where.append("m.tmdb_status = 'matched'")
    elif status == "pending":
        # Persons are resources, not works waiting for a TMDB match. NSFW/AV,
        # collection containers and non-work "other" rows have no ordinary
        # single-TMDB identity and must not occupy this queue.
        where.append("m.tmdb_status != 'matched' AND m.resource_kind NOT IN ('person','series') AND m.is_bundle = 0"
                     " AND NOT EXISTS (SELECT 1 FROM source_records sm WHERE sm.media_id = m.id AND sm.resource_class = 'music')"
                     " AND NOT EXISTS (SELECT 1 FROM source_records sx WHERE sx.media_id = m.id AND sx.resource_class = 'nsfw')")
    elif status == "unsearched":
        where.append("m.tmdb_status = 'pending' AND m.resource_kind NOT IN ('person','series')"
                     " AND NOT EXISTS (SELECT 1 FROM source_records sm WHERE sm.media_id = m.id AND sm.resource_class = 'music')"
                     " AND NOT EXISTS (SELECT 1 FROM source_records sx WHERE sx.media_id = m.id AND sx.resource_class = 'nsfw')")
    elif status in {"review", "not_found", "error"}:
        where.append("m.tmdb_status = ? AND m.resource_kind NOT IN ('person','series') AND m.is_bundle = 0"
                     " AND NOT EXISTS (SELECT 1 FROM source_records sm WHERE sm.media_id = m.id AND sm.resource_class = 'music')"
                     " AND NOT EXISTS (SELECT 1 FROM source_records sx WHERE sx.media_id = m.id AND sx.resource_class = 'nsfw')")
        params.append(status)

    if media_type in {"movie", "tv", "unknown"}:
        where.append("m.media_type = ?")
        params.append(media_type)
    if category == "anime":
        where.append(f"EXISTS (SELECT 1 FROM json_each({GENRES_SQL}) genre WHERE json_extract({GENRE_VALUE_SQL}, '$.id') = 16)")
    elif category in {"documentary", "doc"}:
        where.append(f"EXISTS (SELECT 1 FROM json_each({GENRES_SQL}) genre WHERE json_extract({GENRE_VALUE_SQL}, '$.id') = 99)")
    elif category == "variety":
        where.append(f"EXISTS (SELECT 1 FROM json_each({GENRES_SQL}) genre WHERE json_extract({GENRE_VALUE_SQL}, '$.id') IN (10764, 10767))")
    elif category in {"drama", "series"}:
        where.append(f"NOT EXISTS (SELECT 1 FROM json_each({GENRES_SQL}) genre WHERE json_extract({GENRE_VALUE_SQL}, '$.id') IN (16, 99, 10764, 10767))")
    if resource_kind in {"media", "person", "series"}:
        where.append("m.resource_kind = ?")
        params.append(resource_kind)

    if year == "unknown":
        where.append(f"{MEDIA_YEAR_SQL} IS NULL")
    elif year == "earlier":
        where.append(f"{MEDIA_YEAR_SQL} < 2007")
    elif year != "all":
        decade = isinstance(year, str) and year.endswith("0s")
        number = year[:-1] if decade else str(year)
        if not re.fullmatch(r"[12][0-9]{3}", number) or not 1870 <= int(number) <= 2100:
            raise ValueError("年份筛选无效。")
        where.append(f"{MEDIA_YEAR_SQL} BETWEEN ? AND ?")
        params.extend([int(number), int(number) + (9 if decade else 0)])
    if genre != "all":
        if re.fullmatch(r"[1-9][0-9]{0,7}", str(genre)):
            where.append(f"EXISTS (SELECT 1 FROM json_each({GENRES_SQL}) genre WHERE json_extract({GENRE_VALUE_SQL}, '$.id') = ?)")
            params.append(int(genre))
        elif re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9 &]{1,20}", str(genre)):
            where.append(f"EXISTS (SELECT 1 FROM json_each({GENRES_SQL}) genre WHERE json_extract({GENRE_VALUE_SQL}, '$.name') = ?)")
            params.append(str(genre))
        else:
            raise ValueError("题材筛选无效。")
    if country != "all":
        c_upper = country.strip().lower()
        western_countries = (
            "US", "GB", "FR", "DE", "IT", "ES", "CA", "AU", "NZ", "IE", "NL", "SE", "NO", "DK",
            "FI", "BE", "AT", "CH", "PL", "CZ", "HU", "GR", "PT", "RU", "RO", "UA", "IS", "MX",
            "BR", "AR", "CL", "CO"
        )
        if c_upper in {"europeandamerica", "western", "欧美", "eu_us"}:
            placeholders = ",".join("?" for _ in western_countries)
            where.append(f"EXISTS (SELECT 1 FROM json_each({COUNTRIES_SQL}) country WHERE country.type='text' AND UPPER(country.value) IN ({placeholders}))")
            params.extend(western_countries)
        elif not isinstance(country, str) or not re.fullmatch(r"[A-Za-z]{2}", country):
            raise ValueError("国家或地区筛选无效。")
        else:
            where.append(f"EXISTS (SELECT 1 FROM json_each({COUNTRIES_SQL}) country WHERE country.type='text' AND UPPER(country.value)=?)")
            params.append(country.upper())

    # All technical criteria must hold on the same source, not different versions of a work.
    source_where, source_params = [], []
    if source_type in {"115", "123", "guangya", "ed2k"}:
        source_where.append("sf.source_type = ?")
        source_params.append(source_type)
    for field, selected, groups in (("quality", quality, QUALITY_GROUPS), ("codec", codec, CODEC_GROUPS)):
        if selected == "unknown":
            source_where.append(f"NULLIF(TRIM(sf.{field}), '') IS NULL")
        elif selected in groups:
            values = groups[selected]
            source_where.append(f"UPPER(TRIM(sf.{field})) IN ({','.join('?' for _ in values)})")
            source_params.extend(values)
    if hdr == "unknown":
        source_where.append("NULLIF(TRIM(sf.hdr), '') IS NULL")
    elif hdr == "hdr":
        source_where.append("UPPER(sf.hdr) LIKE '%HDR%'")
    elif hdr in {"dv", "sdr"}:
        source_where.append("('+' || UPPER(REPLACE(sf.hdr, ' ', '')) || '+') LIKE ?")
        source_params.append(f"%+{hdr.upper()}+%")
    source_predicate = " AND ".join(source_where) or "1=1"
    source_cte = f"WITH filtered_sources AS (SELECT * FROM source_records sf WHERE {source_predicate})"
    if source_where:
        where.append("EXISTS (SELECT 1 FROM filtered_sources sf WHERE sf.media_id = m.id)")
    if availability in {"available", "missing"}:
        exists = "EXISTS" if availability == "available" else "NOT EXISTS"
        where.append(f"{exists} (SELECT 1 FROM source_records sf WHERE sf.media_id = m.id)")

    where_sql = " AND ".join(where)
    having_sql = "HAVING COUNT(s.id) > 1" if multi_source_only else ""
    order_sql = SORTS.get(sort, SORTS["updated_desc"])

    query_sql = f"""{source_cte}
        SELECT
            m.*,
            {MEDIA_YEAR_SQL} AS media_year,
            (SELECT COUNT(*) FROM filtered_sources sf WHERE sf.media_id=m.id) AS matched_source_count,
            (SELECT GROUP_CONCAT(sf.id) FROM filtered_sources sf WHERE sf.media_id=m.id) AS matched_source_ids,
            (SELECT GROUP_CONCAT(DISTINCT sf.quality) FROM filtered_sources sf WHERE sf.media_id=m.id) AS matched_qualities,
            COUNT(s.id) AS source_count,
            SUM(s.source_type = '115') AS source_115_count,
            SUM(s.source_type = '123') AS source_123_count,
            SUM(s.source_type = 'guangya') AS source_guangya_count,
            SUM(s.source_type = 'ed2k') AS source_ed2k_count,
            GROUP_CONCAT(DISTINCT s.quality) AS qualities,
            MAX(COALESCE(s.season_end, s.season)) AS max_season,
            COUNT(DISTINCT CASE WHEN s.episode IS NOT NULL THEN printf('%d:%d', s.season, s.episode) END) AS episode_count
        FROM media_items m
        LEFT JOIN source_records s ON s.media_id = m.id
        WHERE {where_sql}
        GROUP BY m.id
        {having_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
    """

    count_sql = f"""{source_cte}
        SELECT COUNT(*) AS total FROM (
            SELECT m.id
            FROM media_items m
            LEFT JOIN source_records s ON s.media_id = m.id
            WHERE {where_sql}
            GROUP BY m.id
            {having_sql}
        ) counted
    """

    with connect(db_path) as connection:
        connection.execute("BEGIN")
        total = int(connection.execute(count_sql, [*source_params, *params]).fetchone()["total"])
        rows = connection.execute(
            query_sql,
            [*source_params, *params, page_size, (page - 1) * page_size],
        ).fetchall()
        # 待匹配队列需要在不打开详情的情况下看到候选概况，因此按当前页批量取一次候选。
        candidate_counts: dict[int, int] = {}
        top_candidates: dict[int, dict[str, Any]] = {}
        media_ids = [int(row["id"]) for row in rows]
        if media_ids:
            placeholders = ",".join("?" for _ in media_ids)
            candidate_rows = connection.execute(
                f"""
                SELECT media_id, id, tmdb_id, media_type, title, release_date, poster_path, score
                FROM tmdb_candidates
                WHERE media_id IN ({placeholders})
                ORDER BY media_id, score DESC, id
                """,
                media_ids,
            ).fetchall()
            for candidate in candidate_rows:
                media_id = int(candidate["media_id"])
                candidate_counts[media_id] = candidate_counts.get(media_id, 0) + 1
                if media_id not in top_candidates:
                    top_candidates[media_id] = dict(candidate)

    items = []
    for row in rows:
        media_id = int(row["id"])
        items.append({
            **dict(row),
            "matched_source_ids": [int(value) for value in (row["matched_source_ids"] or "").split(",") if value],
            "candidate_count": candidate_counts.get(media_id, 0),
            "top_candidate": top_candidates.get(media_id),
        })

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


def get_media_by_tmdb_id(tmdb_id: int, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """Return the locally cached work and all its sources for a TMDB ID."""
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT id FROM media_items WHERE tmdb_id = ? ORDER BY id LIMIT 1",
            (tmdb_id,),
        ).fetchone()
    return get_media(int(row["id"]), db_path) if row else None


def list_series(q: str = "", db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """系列列表：条数、已匹配数、年份跨度，以及一张代表海报（优先已匹配成员）。"""
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT
                s.id,
                s.title,
                COUNT(m.id) AS media_count,
                SUM(CASE WHEN m.tmdb_status = 'matched' THEN 1 ELSE 0 END) AS matched_count,
                MIN(m.year) AS year_from,
                MAX(m.year) AS year_to,
                (
                    SELECT p.poster_path FROM media_items p
                     WHERE p.series_id = s.id AND p.is_metadata = 0 AND p.poster_path IS NOT NULL
                     ORDER BY (p.tmdb_status = 'matched') DESC, p.id
                     LIMIT 1
                ) AS poster_path,
                s.updated_at
            FROM media_series s
            JOIN media_items m ON m.series_id = s.id AND m.is_metadata = 0
            WHERE (? = '' OR s.title LIKE '%' || ? || '%')
            GROUP BY s.id
            ORDER BY media_count DESC, s.title
            """,
            (q, q),
        ).fetchall()
    return [dict(row) for row in rows]


def get_series(series_id: int, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        series = connection.execute("SELECT * FROM media_series WHERE id = ?", (series_id,)).fetchone()
        if not series:
            return None
        members = connection.execute(
            """
            SELECT
                m.id, m.title, m.year, m.media_type, m.resource_kind, m.tmdb_status, m.tmdb_id,
                m.tmdb_title, m.poster_path, m.vote_average, m.release_date, m.series_id, m.is_bundle,
                COUNT(s.id) AS source_count,
                (
                    SELECT COALESCE(NULLIF(t.filename, ''), t.raw_label) FROM source_records t
                     WHERE t.media_id = m.id ORDER BY t.id LIMIT 1
                ) AS sample_label
            FROM media_items m
            LEFT JOIN source_records s ON s.media_id = m.id
            WHERE m.series_id = ? AND m.is_metadata = 0
            GROUP BY m.id
            ORDER BY COALESCE(m.year, 9999), m.id
            """,
            (series_id,),
        ).fetchall()
    return {**dict(series), "items": [dict(row) for row in members]}


def get_media(media_id: int, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        connection.execute("BEGIN")
        item = connection.execute(
            """
            SELECT
                m.*,
                COUNT(s.id) AS source_count,
                SUM(s.source_type = '115') AS source_115_count,
                SUM(s.source_type = '123') AS source_123_count,
                SUM(s.source_type = 'guangya') AS source_guangya_count,
                SUM(s.source_type = 'ed2k') AS source_ed2k_count,
                SUM(s.file_size) AS total_bytes,
                MAX(COALESCE(s.season_end, s.season)) AS max_season,
                COUNT(DISTINCT CASE WHEN s.episode IS NOT NULL THEN printf('%d:%d', s.season, s.episode) END) AS episode_count
            FROM media_items m
            LEFT JOIN source_records s ON s.media_id = m.id
            WHERE m.id = ?
            GROUP BY m.id
            """,
            (media_id,),
        ).fetchone()
        if not item:
            return None

        sources = connection.execute(
            """
            SELECT
                s.*,
                i.source_name AS import_source_name,
                i.source_kind AS import_source_kind,
                i.source_path AS import_source_path,
                i.created_at AS import_created_at,
                (SELECT e.channel_name FROM ingestion_links l JOIN ingestion_events e ON e.id = l.ingestion_id WHERE l.source_key = s.source_key ORDER BY e.received_at DESC LIMIT 1) AS channel_name,
                (SELECT e.channel_avatar_url FROM ingestion_links l JOIN ingestion_events e ON e.id = l.ingestion_id WHERE l.source_key = s.source_key ORDER BY e.received_at DESC LIMIT 1) AS channel_avatar_url,
                (SELECT COALESCE(e.published_at, e.received_at) FROM ingestion_links l JOIN ingestion_events e ON e.id = l.ingestion_id WHERE l.source_key = s.source_key ORDER BY e.received_at DESC LIMIT 1) AS published_at
            FROM source_records s
            LEFT JOIN imports i ON i.id = s.import_id
            WHERE s.media_id = ?
            ORDER BY s.source_type, s.season, s.episode, s.id
            """,
            (media_id,),
        ).fetchall()
        candidates = connection.execute(
            """
            SELECT * FROM tmdb_candidates
            WHERE media_id = ?
            ORDER BY score DESC, id
            LIMIT 12
            """,
            (media_id,),
        ).fetchall()
        imdb_lookup = connection.execute(
            "SELECT evidence_json FROM imdb_lookups WHERE media_id = ?", (media_id,)
        ).fetchone()
        messages = connection.execute(
            """
            SELECT DISTINCT e.id, e.event_id, e.service, e.channel_name, e.channel_username,
                   e.message_id, e.message_url, e.channel_avatar_url, e.published_at,
                   e.raw_text, e.raw_json, e.status, e.received_at
            FROM ingestion_events e
            JOIN ingestion_links l ON l.ingestion_id = e.id
            JOIN source_records s ON s.source_key = l.source_key
            WHERE s.media_id = ?
            ORDER BY e.received_at DESC
            """,
            (media_id,),
        ).fetchall()

    result = dict(item)
    result["sources"] = [
        {**dict(row), "is_season_pack": row["season"] is not None and row["episode"] is None}
        for row in sources
    ]
    result["candidates"] = [dict(row) for row in candidates]
    result["imdb_lookup"] = json.loads(imdb_lookup["evidence_json"]) if imdb_lookup else None
    result["original_messages"] = [dict(row) for row in messages]
    return result


def save_imdb_lookup(media_id: int, evidence: dict[str, Any] | None, db_path: str | Path | None = None) -> None:
    with connect(db_path) as connection:
        if evidence is None:
            connection.execute("DELETE FROM imdb_lookups WHERE media_id = ?", (media_id,))
        else:
            connection.execute(
                """INSERT INTO imdb_lookups (media_id, evidence_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(media_id) DO UPDATE SET evidence_json=excluded.evidence_json, updated_at=excluded.updated_at""",
                (media_id, json.dumps(evidence, ensure_ascii=False), utc_now()),
            )


def list_imports(limit: int = 50, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM imports ORDER BY id DESC LIMIT ?",
            (min(max(limit, 1), 250),),
        ).fetchall()
    return [dict(row) for row in rows]


def _format_ingestion_number_ranges(values: Iterable[int], prefix: str = "") -> list[str]:
    numbers = sorted({int(value) for value in values})
    if not numbers:
        return []
    ranges: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(f"{prefix}{start}-{prefix}{previous}" if start != previous else f"{prefix}{start}")
        start = previous = number
    ranges.append(f"{prefix}{start}-{prefix}{previous}" if start != previous else f"{prefix}{start}")
    return ranges


def _format_ingestion_episode_labels(rows: list[dict[str, Any]]) -> list[str]:
    episodes: set[tuple[int, int]] = set()
    episode_ranges: set[tuple[int, int, int]] = set()
    for row in rows:
        if row.get("season") is None or row.get("episode") is None:
            continue
        season, episode = int(row["season"]), int(row["episode"])
        span = row.get("episode_range")
        if isinstance(span, (list, tuple)) and len(span) == 2 and int(span[1]) > int(span[0]):
            episode_ranges.add((season, int(span[0]), int(span[1])))
        else:
            episodes.add((season, episode))
    episode_labels = [f"S{season:02d}E{first:02d}-E{last:02d}" for season, first, last in sorted(episode_ranges)]
    episode_labels += [f"S{season:02d}E{episode:02d}" for season, episode in sorted(episodes)]
    season_ranges = set()
    covered_seasons = set()
    for row in rows:
        season_range = row.get("season_range")
        if isinstance(season_range, (list, tuple)) and len(season_range) == 2:
            start, end = int(season_range[0]), int(season_range[1])
            if end > start:
                season_ranges.add((start, end))
                covered_seasons.update(range(start, end + 1))
    season_only = {int(row["season"]) for row in rows if row.get("season") is not None and row.get("episode") is None} - covered_seasons
    episode_seasons = {season for season, _ in episodes} | {season for season, _, _ in episode_ranges}
    season_labels = [f"S{start}-S{end}" for start, end in sorted(season_ranges)]
    season_labels.extend(_format_ingestion_number_ranges(season_only - episode_seasons, prefix="S"))
    return episode_labels + season_labels


def list_ingestion_records(
    limit: int = 100, db_path: str | Path | None = None,
    status: str | None = None, query: str | None = None,
) -> dict[str, Any]:
    """Return webhook intake records with channel, provider and media details.

    ``status`` optionally restricts the list to one event status such as
    ``failed`` or ``pending_validation``; ``query`` matches channel, original
    message, extracted link and identified media text. The today counter stays
    global in both cases.
    """
    bounded_limit = min(max(limit, 1), 250)
    status = str(status).strip() if status else ""
    query = str(query).strip() if query else ""

    conditions: list[str] = []
    params: list[Any] = []
    if status:
        conditions.append("e.status = ?")
        params.append(status)
    if query:
        def escape_like(value: str) -> str:
            return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        needle = f"%{escape_like(query)}%"
        conditions.append(
            """(
                e.channel_name LIKE ? ESCAPE '\\' OR e.channel_username LIKE ? ESCAPE '\\'
                OR e.channel_id LIKE ? ESCAPE '\\' OR e.message_url LIKE ? ESCAPE '\\'
                OR e.raw_text LIKE ? ESCAPE '\\' OR e.event_id LIKE ? ESCAPE '\\'
                OR e.error LIKE ? ESCAPE '\\'
                OR EXISTS (
                    SELECT 1 FROM ingestion_links l
                    LEFT JOIN source_records s ON s.source_key = l.source_key
                    LEFT JOIN media_items m ON m.id = COALESCE(l.media_id, s.media_id)
                    WHERE l.ingestion_id = e.id AND (
                        m.title LIKE ? ESCAPE '\\' OR m.tmdb_title LIKE ? ESCAPE '\\'
                        OR s.raw_label LIKE ? ESCAPE '\\' OR s.filename LIKE ? ESCAPE '\\'
                        OR s.url LIKE ? ESCAPE '\\'
                    )
                )
            )"""
        )
        params.extend([needle] * 12)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    event_params = (*params, bounded_limit)
    with connect(db_path) as connection:
        events = connection.execute(
            f"SELECT * FROM ingestion_events e{where} ORDER BY e.received_at DESC LIMIT ?",
            event_params,
        ).fetchall()
        today_count = connection.execute(
            "SELECT COUNT(*) AS total FROM ingestion_events WHERE date(received_at, 'localtime') = date('now', 'localtime')",
        ).fetchone()["total"]
        links = connection.execute(
            f"""
            SELECT l.ingestion_id, l.status AS link_status, l.provider,
                   COALESCE(l.media_id, s.media_id) AS media_id, COALESCE(m.tmdb_title, m.title) AS media_title,
                   COALESCE(m.year, s.parsed_year, CASE
                       WHEN substr(m.release_date, 1, 4) GLOB '[12][0-9][0-9][0-9]'
                       THEN CAST(substr(m.release_date, 1, 4) AS INTEGER) END) AS media_year,
                   s.season, s.season_end, s.episode, s.metadata_json
            FROM ingestion_links l
            LEFT JOIN source_records s ON s.source_key = l.source_key
            LEFT JOIN media_items m ON m.id = COALESCE(l.media_id, s.media_id)
            WHERE l.ingestion_id IN (SELECT e.id FROM ingestion_events e{where} ORDER BY e.received_at DESC LIMIT ?)
            ORDER BY l.id
            """,
            event_params,
        ).fetchall()

    media_by_event: dict[str, dict[int, dict[str, Any]]] = {}
    for link in links:
        if link["media_id"] is None:
            continue
        event_media = media_by_event.setdefault(link["ingestion_id"], {})
        media = event_media.setdefault(int(link["media_id"]), {
            "id": int(link["media_id"]),
            "title": link["media_title"],
            "year": link["media_year"],
            "_sources": [],
        })
        try:
            source_metadata = json.loads(link["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            source_metadata = {}
        if link["season_end"] is not None:
            source_metadata["season_range"] = [link["season"], link["season_end"]]
        elif not source_metadata.get("season_range"):
            context = str(source_metadata.get("context") or "")
            range_match = re.search(r"(?i)\bS(\d{1,2})\s*[-~]\s*S?(\d{1,2})(?=$|\D)", context)
            if range_match and int(range_match.group(2)) > int(range_match.group(1)):
                source_metadata["season_range"] = [int(range_match.group(1)), int(range_match.group(2))]
        media["_sources"].append({
            "season": link["season"],
            "episode": link["episode"],
            "season_range": source_metadata.get("season_range"),
            "episode_range": source_metadata.get("episode_range"),
        })

    items = []
    for event in events:
        item = dict(event)
        username = normalize_channel_username(item.get("channel_username"))
        if username and cache_channel_avatar(username):
            item["channel_avatar_url"] = f"/api/channel-avatar/{username}"
        item["media_ids"] = json.loads(item.pop("media_ids_json") or "[]")
        item["source_channel"] = item.get("channel_name") or item.get("channel_id") or "未知频道"
        item["source_username"] = item.get("channel_username")
        item["source_service"] = item.get("service") or "未知渠道"
        media_details = []
        for media in media_by_event.get(item["id"], {}).values():
            episode_labels = _format_ingestion_episode_labels(media.pop("_sources"))
            title = media.get("title") or "未识别媒体"
            display_title = f"{title}（{media['year']}）" if media.get("year") else title
            media["episode_labels"] = episode_labels
            media["display"] = " · ".join([display_title, *episode_labels]) if episode_labels else display_title
            media_details.append(media)
        item["media"] = media_details
        item["media_details"] = media_details
        item["media_titles"] = [row["display"] for row in media_details]
        items.append(item)
    return {"items": items, "today_count": int(today_count or 0), "total": len(items)}


def update_media_fields(
    media_id: int,
    fields: dict[str, Any],
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    allowed = {"title", "year", "media_type"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return get_media(media_id, db_path)
    if "title" in updates:
        updates["normalized_title"] = normalize_title(str(updates["title"]))
    updates["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with connect(db_path) as connection:
        connection.execute(
            f"UPDATE media_items SET {assignments} WHERE id = ?",
            [*updates.values(), media_id],
        )
        connection.execute("DELETE FROM imdb_lookups WHERE media_id = ?", (media_id,))
        refresh_metadata_flags(connection, {media_id})
    return get_media(media_id, db_path)


def delete_candidate(
    media_id: int,
    candidate_id: int,
    db_path: str | Path | None = None,
) -> None:
    with connect(db_path) as connection:
        connection.execute(
            "DELETE FROM tmdb_candidates WHERE id = ? AND media_id = ?",
            (candidate_id, media_id),
        )


def pending_media_ids(
    limit: int,
    db_path: str | Path | None = None,
    *,
    series_only: bool = False,
) -> list[int]:
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id FROM media_items
            WHERE tmdb_status != 'matched' AND resource_kind NOT IN ('person','series') AND is_bundle = 0
              AND is_metadata = 0
              AND NOT EXISTS (
                  SELECT 1 FROM source_records sm
                  WHERE sm.media_id = media_items.id AND sm.resource_class = 'music'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM source_records sx
                  WHERE sx.media_id = media_items.id AND sx.resource_class = 'nsfw'
              )
              AND (? = 0 OR series_id IS NOT NULL)
            -- 顺序：待匹配 → 上次网络失败（重试有可能成功）→ 待确认 → 明确没找到。
            -- review 重跑通常还是 review，所以不能让它把 error 的补试挤掉。
            ORDER BY CASE tmdb_status WHEN 'pending' THEN 0 WHEN 'error' THEN 1 WHEN 'review' THEN 2 ELSE 3 END, id
            LIMIT ?
            """,
            (1 if series_only else 0, min(max(limit, 1), 5000)),
        ).fetchall()
    return [int(row["id"]) for row in rows]


def save_candidates(
    media_id: int,
    candidates: Iterable[dict[str, Any]],
    status: str,
    error: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    now = utc_now()
    with connect(db_path) as connection:
        connection.execute("DELETE FROM tmdb_candidates WHERE media_id = ?", (media_id,))
        for candidate in candidates:
            connection.execute(
                """
                INSERT INTO tmdb_candidates (
                    media_id, tmdb_id, media_type, title, original_title, release_date,
                    poster_path, overview, score, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    media_id,
                    candidate["tmdb_id"],
                    candidate["media_type"],
                    candidate["title"],
                    candidate.get("original_title"),
                    candidate.get("release_date"),
                    candidate.get("poster_path"),
                    candidate.get("overview"),
                    candidate["score"],
                    json.dumps({
                        **candidate.get("payload", {}),
                        "_match_evidence": candidate.get("match_evidence", {}),
                    }, ensure_ascii=False),
                    now,
                ),
            )
        connection.execute(
            """
            UPDATE media_items
            SET tmdb_status = ?, last_tmdb_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error, now, media_id),
        )
        if status == "review":
            # 候选已生成但尚未人工确认：用最优候选的海报做临时展示，
            # 正式关联后 link_tmdb 会用官方海报覆盖它。
            connection.execute(
                """
                UPDATE media_items
                SET poster_path = COALESCE(poster_path, (
                    SELECT poster_path FROM tmdb_candidates
                    WHERE media_id = ? AND poster_path IS NOT NULL
                    ORDER BY score DESC, id LIMIT 1
                ))
                WHERE id = ?
                """,
                (media_id, media_id),
            )


def merge_media_items(
    keep_id: int,
    remove_id: int,
    connection: sqlite3.Connection,
) -> None:
    # 合并前先接住系列归属：否则「速度与激情(系列)」里的 11 部会因为合并掉重复条目
    # 只剩下一两部，系列视图凭空少掉作品。
    removed = connection.execute(
        "SELECT series_id FROM media_items WHERE id = ?", (remove_id,)
    ).fetchone()
    if removed and removed["series_id"] is not None:
        connection.execute(
            "UPDATE media_items SET series_id = COALESCE(series_id, ?), updated_at = ? WHERE id = ?",
            (int(removed["series_id"]), utc_now(), keep_id),
        )
    connection.execute(
        "UPDATE OR IGNORE source_records SET media_id = ? WHERE media_id = ?",
        (keep_id, remove_id),
    )
    connection.execute(
        "UPDATE ingestion_links SET media_id = ? WHERE media_id = ?",
        (keep_id, remove_id),
    )
    # 入库事件里保存的是当时的 media_ids_json；合并后必须把旧 id 改成保留 id，
    # 否则入库历史会显示已删除的作品。只改包含 remove_id 的事件，避免全表扫描。
    for event in connection.execute(
        "SELECT id, media_ids_json FROM ingestion_events WHERE media_ids_json LIKE ?",
        (f"%{remove_id}%",),
    ).fetchall():
        try:
            values = json.loads(event["media_ids_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(values, list) or remove_id not in values:
            continue
        rewritten = list(dict.fromkeys(
            keep_id if value == remove_id else value
            for value in values
            if isinstance(value, int)
        ))
        connection.execute(
            "UPDATE ingestion_events SET media_ids_json = ? WHERE id = ?",
            (json.dumps(rewritten, ensure_ascii=False), event["id"]),
        )
    connection.execute("DELETE FROM source_records WHERE media_id = ?", (remove_id,))
    connection.execute("DELETE FROM tmdb_candidates WHERE media_id = ?", (remove_id,))
    connection.execute("DELETE FROM media_items WHERE id = ?", (remove_id,))
    refresh_metadata_flags(connection, {keep_id})


def extract_tmdb_seasons(details: dict[str, Any]) -> list[dict[str, int]] | None:
    """提取 TMDB 季集基准 [{season: 1, episode_count: 12}, ...]；没有则返回 None。"""
    seasons = details.get("seasons")
    if not isinstance(seasons, list):
        payload = details.get("payload")
        seasons = payload.get("seasons") if isinstance(payload, dict) else None
    if not isinstance(seasons, list):
        return None
    rows = []
    for season in seasons:
        if not isinstance(season, dict):
            continue
        number, count = season.get("season_number"), season.get("episode_count")
        if isinstance(number, int) and number >= 1 and isinstance(count, int) and count > 0:
            rows.append({"season": number, "episode_count": count})
    rows.sort(key=lambda row: row["season"])
    return rows or None


def link_tmdb(
    media_id: int,
    details: dict[str, Any],
    confidence: float,
    method: str,
    db_path: str | Path | None = None,
    *,
    canonical_year: int | None = None,
) -> int:
    if canonical_year is not None and (isinstance(canonical_year, bool) or not isinstance(canonical_year, int) or not 1870 <= canonical_year <= 2100):
        raise ValueError("无效的核验年份。")
    media_type = details["media_type"]
    tmdb_id = int(details["tmdb_id"])
    now = utc_now()
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE media_items
            SET
                tmdb_id = ?, tmdb_media_type = ?, tmdb_status = 'matched',
                tmdb_title = ?, original_title = ?, overview = ?, poster_path = ?,
                backdrop_path = ?, release_date = ?, vote_average = ?, genres_json = ?,
                origin_country_json = ?, match_confidence = ?, match_method = ?,
                last_tmdb_error = NULL,
                media_type = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                tmdb_id,
                media_type,
                details.get("title"),
                details.get("original_title"),
                details.get("overview"),
                details.get("poster_path"),
                details.get("backdrop_path"),
                details.get("release_date"),
                details.get("vote_average"),
                json.dumps(details.get("genres", []), ensure_ascii=False),
                json.dumps(details.get("origin_country", []), ensure_ascii=False),
                confidence,
                method,
                media_type,
                now,
                media_id,
            ),
        )

        duplicate = connection.execute(
            """
            SELECT id FROM media_items
            WHERE tmdb_id = ? AND tmdb_media_type = ? AND id != ?
            ORDER BY id
            LIMIT 1
            """,
            (tmdb_id, media_type, media_id),
        ).fetchone()
        if duplicate:
            other_id = int(duplicate["id"])
            keep_id, remove_id = (other_id, media_id) if other_id < media_id else (media_id, other_id)
            if keep_id != media_id:
                connection.execute(
                    """
                    UPDATE media_items SET
                        tmdb_id = ?, tmdb_media_type = ?, tmdb_status = 'matched',
                        tmdb_title = ?, original_title = ?, overview = ?, poster_path = ?,
                        backdrop_path = ?, release_date = ?, vote_average = ?, genres_json = ?,
                        origin_country_json = ?, match_confidence = ?, match_method = ?,
                        last_tmdb_error = NULL, media_type = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        tmdb_id,
                        media_type,
                        details.get("title"),
                        details.get("original_title"),
                        details.get("overview"),
                        details.get("poster_path"),
                        details.get("backdrop_path"),
                        details.get("release_date"),
                        details.get("vote_average"),
                        json.dumps(details.get("genres", []), ensure_ascii=False),
                        json.dumps(details.get("origin_country", []), ensure_ascii=False),
                        confidence,
                        method,
                        media_type,
                        now,
                        keep_id,
                    ),
                )
            merge_media_items(keep_id, remove_id, connection)
            media_id = keep_id

        if media_type == "tv":
            # 剧集资源默认整季：没有季集线索的来源补记 S01，保证覆盖范围可读。
            connection.execute(
                "UPDATE source_records SET season = 1 WHERE media_id = ? AND season IS NULL AND episode IS NULL",
                (media_id,),
            )
            seasons = extract_tmdb_seasons(details)
            if seasons:
                # 缓存 TMDB 每季集数基准，详情页的覆盖格子图以此为分母。
                connection.execute(
                    "UPDATE media_items SET seasons_json = ? WHERE id = ?",
                    (json.dumps(seasons, ensure_ascii=False), media_id),
                )

        if canonical_year is not None:
            connection.execute("UPDATE media_items SET year = ? WHERE id = ?", (canonical_year, media_id))
        connection.execute("DELETE FROM tmdb_candidates WHERE media_id = ?", (media_id,))
        refresh_metadata_flags(connection, {media_id})

    return media_id


def unlink_tmdb(media_id: int, db_path: str | Path | None = None) -> None:
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE media_items SET
                tmdb_id = NULL, tmdb_media_type = NULL, tmdb_status = 'pending',
                tmdb_title = NULL, original_title = NULL, overview = NULL,
                poster_path = NULL, backdrop_path = NULL, release_date = NULL,
                vote_average = NULL, genres_json = NULL, origin_country_json = NULL,
                match_confidence = NULL, match_method = NULL, last_tmdb_error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (utc_now(), media_id),
        )
        refresh_metadata_flags(connection, {media_id})
