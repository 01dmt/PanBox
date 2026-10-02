from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .importers import ParsedSource, content_digest, detect_source_kind, iter_sources, normalize_title
from .schema import connect, init_db


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _compatible_media(
    connection: sqlite3.Connection,
    source: ParsedSource,
) -> sqlite3.Row | None:
    normalized = normalize_title(source.title)
    if not normalized:
        return None

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


def ensure_media(connection: sqlite3.Connection, source: ParsedSource) -> int:
    existing = _compatible_media(connection, source)
    now = utc_now()
    if existing:
        media_type = existing["media_type"]
        if media_type == "unknown" and source.media_type != "unknown":
            media_type = source.media_type
        year = existing["year"] if existing["year"] is not None else source.year
        connection.execute(
            "UPDATE media_items SET media_type = ?, resource_kind = ?, year = ?, updated_at = ? WHERE id = ?",
            (media_type, source.resource_kind, year, now, existing["id"]),
        )
        return int(existing["id"])

    cursor = connection.execute(
        """
        INSERT INTO media_items (
            title, normalized_title, year, media_type, resource_kind, resource_metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source.title,
            normalize_title(source.title),
            source.year,
            source.media_type,
            source.resource_kind,
            json.dumps(source.metadata, ensure_ascii=False),
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


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
                quality, codec, hdr, audio, release_group, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                source.year,
                source.quality,
                source.codec,
                source.hdr,
                source.audio,
                source.release_group,
                json.dumps(source.metadata, ensure_ascii=False),
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
) -> None:
    """Refresh parser-derived fields when a previously imported source improves."""
    row = connection.execute(
        """
        SELECT s.media_id, m.tmdb_id, m.tmdb_status, m.media_type
        FROM source_records s
        JOIN media_items m ON m.id = s.media_id
        WHERE s.id = ?
        """,
        (source_id,),
    ).fetchone()
    if not row:
        return

    old_media_id = int(row["media_id"])
    media_id = old_media_id
    # A parser correction may move an old unknown placeholder into the proper
    # work. Never move a source that already has an explicit or TMDB identity.
    if row["tmdb_id"] is None and row["tmdb_status"] != "matched" and row["media_type"] == "unknown":
        media_id = ensure_media(connection, source)

    connection.execute(
        """
        UPDATE source_records
        SET media_id = ?, raw_label = ?, season = ?, season_end = ?, episode = ?, parsed_year = ?,
            quality = ?, codec = ?, hdr = ?, audio = ?, release_group = ?, metadata_json = ?
        WHERE id = ?
        """,
        (
            media_id,
            source.raw_label,
            source.season,
            source.season_end,
            source.episode,
            source.year,
            source.quality,
            source.codec,
            source.hdr,
            source.audio,
            source.release_group,
            json.dumps(source.metadata, ensure_ascii=False),
            source_id,
        ),
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
    return result


def import_content(
    content: str,
    source_name: str,
    source_path: str | None = None,
    kind: str = "auto",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    init_db(db_path)
    actual_kind = detect_source_kind(content) if kind == "auto" else kind
    digest = content_digest(content)
    now = utc_now()
    total = inserted = duplicates = errors = 0
    inserted_source_keys: list[str] = []
    duplicate_source_keys: list[str] = []
    error_source_keys: list[str] = []

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

        for source in iter_sources(content, actual_kind):
            total += 1
            try:
                # Parser upgrades must not create empty works for an already imported source.
                existing = connection.execute(
                    "SELECT id FROM source_records WHERE source_key = ?", (source.source_key,)
                ).fetchone()
                if existing:
                    duplicates += 1
                    duplicate_source_keys.append(source.source_key)
                    _refresh_duplicate_source(connection, int(existing["id"]), source)
                    continue
                media_id = ensure_media(connection, source)
                if insert_source(connection, media_id, import_id, source):
                    inserted += 1
                    inserted_source_keys.append(source.source_key)
                else:
                    duplicates += 1
                    duplicate_source_keys.append(source.source_key)
            except Exception:
                errors += 1
                error_source_keys.append(source.source_key)

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
                SUM(tmdb_status != 'matched') AS pending,
                SUM(tmdb_status = 'review') AS review,
                SUM(media_type = 'movie') AS movies,
                SUM(media_type = 'tv') AS tv,
                SUM(media_type = 'unknown') AS unknown,
                SUM(EXISTS (SELECT 1 FROM source_records s WHERE s.media_id = media_items.id)) AS with_sources,
                SUM(NOT EXISTS (SELECT 1 FROM source_records s WHERE s.media_id = media_items.id)) AS without_sources
            FROM media_items
            """
        ).fetchone()
        sources = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(source_type = '115') AS share_115,
                SUM(source_type = 'ed2k') AS ed2k,
                SUM(file_size) AS bytes
            FROM source_records
            """
        ).fetchone()
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
            "ed2k": int(sources["ed2k"] or 0),
            "bytes": int(sources["bytes"] or 0),
        },
        "multi_source": int(duplicates["total"] or 0),
        "imports": int(imports["total"] or 0),
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
    where = ["1 = 1"]
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
        where.append("m.tmdb_status != 'matched'")
    elif status == "unsearched":
        where.append("m.tmdb_status = 'pending'")
    elif status in {"review", "not_found", "error"}:
        where.append("m.tmdb_status = ?")
        params.append(status)

    if media_type in {"movie", "tv", "unknown"}:
        where.append("m.media_type = ?")
        params.append(media_type)
    if resource_kind in {"media", "person", "series"}:
        where.append("m.resource_kind = ?")
        params.append(resource_kind)

    if year == "unknown":
        where.append(f"{MEDIA_YEAR_SQL} IS NULL")
    elif year != "all":
        decade = isinstance(year, str) and year.endswith("0s")
        number = year[:-1] if decade else str(year)
        if not re.fullmatch(r"[12][0-9]{3}", number) or not 1870 <= int(number) <= 2100:
            raise ValueError("年份筛选无效。")
        where.append(f"{MEDIA_YEAR_SQL} BETWEEN ? AND ?")
        params.extend([int(number), int(number) + (9 if decade else 0)])
    if genre != "all":
        if not re.fullmatch(r"[1-9][0-9]{0,7}", str(genre)):
            raise ValueError("题材筛选无效。")
        where.append(f"EXISTS (SELECT 1 FROM json_each({GENRES_SQL}) genre WHERE json_extract({GENRE_VALUE_SQL}, '$.id') = ?)")
        params.append(int(genre))
    if country != "all":
        if not isinstance(country, str) or not re.fullmatch(r"[A-Za-z]{2}", country):
            raise ValueError("国家或地区筛选无效。")
        where.append(f"EXISTS (SELECT 1 FROM json_each({COUNTRIES_SQL}) country WHERE country.type='text' AND UPPER(country.value)=?)")
        params.append(country.upper())

    # All technical criteria must hold on the same source, not different versions of a work.
    source_where, source_params = [], []
    if source_type in {"115", "ed2k"}:
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

    return {
        "items": [{**dict(row), "matched_source_ids": [int(value) for value in (row["matched_source_ids"] or "").split(",") if value]} for row in rows],
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


def get_media(media_id: int, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        connection.execute("BEGIN")
        item = connection.execute(
            """
            SELECT
                m.*,
                COUNT(s.id) AS source_count,
                SUM(s.source_type = '115') AS source_115_count,
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
            SELECT * FROM source_records
            WHERE media_id = ?
            ORDER BY source_type, season, episode, id
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

    result = dict(item)
    result["sources"] = [
        {**dict(row), "is_season_pack": row["season"] is not None and row["episode"] is None}
        for row in sources
    ]
    result["candidates"] = [dict(row) for row in candidates]
    result["imdb_lookup"] = json.loads(imdb_lookup["evidence_json"]) if imdb_lookup else None
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
    episodes = sorted({(int(row["season"]), int(row["episode"])) for row in rows if row.get("season") is not None and row.get("episode") is not None})
    episode_labels = [f"S{season:02d}E{episode:02d}" for season, episode in episodes]
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
    episode_seasons = {season for season, _ in episodes}
    season_labels = [f"S{start}-S{end}" for start, end in sorted(season_ranges)]
    season_labels.extend(_format_ingestion_number_ranges(season_only - episode_seasons, prefix="S"))
    return episode_labels + season_labels


def list_ingestion_records(limit: int = 100, db_path: str | Path | None = None) -> dict[str, Any]:
    """Return webhook intake records with channel, provider and media details."""
    bounded_limit = min(max(limit, 1), 250)
    with connect(db_path) as connection:
        events = connection.execute(
            "SELECT * FROM ingestion_events ORDER BY received_at DESC LIMIT ?",
            (bounded_limit,),
        ).fetchall()
        today_count = connection.execute(
            "SELECT COUNT(*) AS total FROM ingestion_events WHERE date(received_at, 'localtime') = date('now', 'localtime')",
        ).fetchone()["total"]
        links = connection.execute(
            """
            SELECT l.ingestion_id, l.status AS link_status, l.provider,
                   m.id AS media_id, COALESCE(m.tmdb_title, m.title) AS media_title,
                   COALESCE(m.year, s.parsed_year, CASE
                       WHEN substr(m.release_date, 1, 4) GLOB '[12][0-9][0-9][0-9]'
                       THEN CAST(substr(m.release_date, 1, 4) AS INTEGER) END) AS media_year,
                   s.season, s.season_end, s.episode, s.metadata_json
            FROM ingestion_links l
            LEFT JOIN media_items m ON m.id = l.media_id
            LEFT JOIN source_records s ON s.source_key = l.source_key
            WHERE l.ingestion_id IN (SELECT id FROM ingestion_events ORDER BY received_at DESC LIMIT ?)
            ORDER BY l.id
            """,
            (bounded_limit,),
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
        })

    items = []
    for event in events:
        item = dict(event)
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


def pending_media_ids(limit: int, db_path: str | Path | None = None) -> list[int]:
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id FROM media_items
            WHERE tmdb_status != 'matched'
            ORDER BY CASE tmdb_status WHEN 'pending' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, id
            LIMIT ?
            """,
            (min(max(limit, 1), 5000),),
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


def merge_media_items(
    keep_id: int,
    remove_id: int,
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        "UPDATE OR IGNORE source_records SET media_id = ? WHERE media_id = ?",
        (keep_id, remove_id),
    )
    connection.execute("DELETE FROM source_records WHERE media_id = ?", (remove_id,))
    connection.execute("DELETE FROM tmdb_candidates WHERE media_id = ?", (remove_id,))
    connection.execute("DELETE FROM media_items WHERE id = ?", (remove_id,))


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

        if canonical_year is not None:
            connection.execute("UPDATE media_items SET year = ? WHERE id = ?", (canonical_year, media_id))
        connection.execute("DELETE FROM tmdb_candidates WHERE media_id = ?", (media_id,))

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
