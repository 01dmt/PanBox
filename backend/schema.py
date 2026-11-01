from __future__ import annotations

import os
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT_DIR / "data" / "media.db"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY
);

-- 系列（如「霍比特人」「007」）：频道给系列里的每一部单独发一条消息，标题只写系列名，
-- 片名藏在发布文件名里。作品仍按单片入库并各自关联 TMDB，这里只负责把它们聚成一条系列。
CREATE TABLE IF NOT EXISTS media_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL UNIQUE,
    tmdb_collection_id INTEGER,
    poster_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    year INTEGER,
    media_type TEXT NOT NULL DEFAULT 'unknown'
        CHECK (media_type IN ('movie', 'tv', 'unknown')),
    resource_kind TEXT NOT NULL DEFAULT 'media'
        CHECK (resource_kind IN ('media', 'person', 'series')),
    resource_metadata_json TEXT NOT NULL DEFAULT '{}',
    tmdb_id INTEGER,
    tmdb_media_type TEXT CHECK (tmdb_media_type IN ('movie', 'tv') OR tmdb_media_type IS NULL),
    tmdb_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (tmdb_status IN ('pending', 'review', 'matched', 'not_found', 'error')),
    tmdb_title TEXT,
    original_title TEXT,
    overview TEXT,
    poster_path TEXT,
    backdrop_path TEXT,
    release_date TEXT,
    vote_average REAL,
    genres_json TEXT,
    origin_country_json TEXT,
    match_confidence REAL,
    match_method TEXT,
    last_tmdb_error TEXT,
    series_id INTEGER REFERENCES media_series(id),
    -- 整包分享：一条 115 分享里装了整个系列（目录内容没法列举），不能当成某一部片。
    is_bundle INTEGER NOT NULL DEFAULT 0,
    -- 纯附属文件包：只有海报/NFO/字幕/歌词，没有可播放的视频或音频。
    is_metadata INTEGER NOT NULL DEFAULT 0,
    metadata_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_title ON media_items(normalized_title, year);
CREATE INDEX IF NOT EXISTS idx_media_tmdb ON media_items(tmdb_media_type, tmdb_id);
CREATE INDEX IF NOT EXISTS idx_media_status ON media_items(tmdb_status);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    source_path TEXT,
    source_kind TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    total_records INTEGER NOT NULL DEFAULT 0,
    inserted_records INTEGER NOT NULL DEFAULT 0,
    duplicate_records INTEGER NOT NULL DEFAULT 0,
    error_records INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_import_sha ON imports(content_sha256);

CREATE TABLE IF NOT EXISTS ingestion_events (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    service TEXT NOT NULL DEFAULT 'unknown',
    channel_id TEXT,
    channel_name TEXT,
    channel_username TEXT,
    message_id TEXT,
    message_url TEXT,
    channel_avatar_url TEXT,
    published_at TEXT,
    raw_text TEXT NOT NULL,
    raw_json TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    media_ids_json TEXT NOT NULL DEFAULT '[]',
    received_at TEXT NOT NULL,
    processed_at TEXT,
    UNIQUE(service, event_id)
);
CREATE INDEX IF NOT EXISTS idx_ingestion_status ON ingestion_events(status, received_at);

CREATE TABLE IF NOT EXISTS ingestion_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ingestion_id TEXT NOT NULL REFERENCES ingestion_events(id) ON DELETE CASCADE,
    source_key TEXT,
    provider TEXT NOT NULL,
    source_type TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'extracted',
    media_id INTEGER REFERENCES media_items(id) ON DELETE SET NULL,
    error TEXT,
    validation_attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    validation_error TEXT,
    UNIQUE(ingestion_id, source_key)
);
CREATE INDEX IF NOT EXISTS idx_ingestion_links_source ON ingestion_links(source_key);

CREATE TABLE IF NOT EXISTS source_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
    source_type TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '115',
    source_key TEXT NOT NULL UNIQUE,
    raw_label TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    url TEXT NOT NULL,
    filename TEXT,
    file_size INTEGER,
    ed2k_hash TEXT,
    season INTEGER,
    season_end INTEGER,
    episode INTEGER,
    parsed_year INTEGER,
    quality TEXT,
    codec TEXT,
    hdr TEXT,
    audio TEXT,
    release_group TEXT,
    metadata_json TEXT,
    resource_class TEXT NOT NULL DEFAULT 'media',
    tags_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    availability_status TEXT NOT NULL DEFAULT 'unknown',
    last_checked_at TEXT,
    last_check_error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sources_media ON source_records(media_id);
CREATE INDEX IF NOT EXISTS idx_sources_type ON source_records(source_type);
CREATE INDEX IF NOT EXISTS idx_sources_episode ON source_records(media_id, season, episode);

CREATE TABLE IF NOT EXISTS tmdb_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    tmdb_id INTEGER NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
    title TEXT NOT NULL,
    original_title TEXT,
    release_date TEXT,
    poster_path TEXT,
    overview TEXT,
    score REAL NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(media_id, media_type, tmdb_id)
);

CREATE INDEX IF NOT EXISTS idx_candidates_media ON tmdb_candidates(media_id, score DESC);

CREATE TABLE IF NOT EXISTS imdb_lookups (
    media_id INTEGER PRIMARY KEY REFERENCES media_items(id) ON DELETE CASCADE,
    evidence_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

"""


def resolve_db_path(value: str | os.PathLike[str] | None = None) -> Path:
    configured = value or os.getenv("MEDIA_DB_PATH")
    path = Path(configured).expanduser() if configured else DEFAULT_DB_PATH
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


def connect(db_path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _migrate_resource_headings(connection: sqlite3.Connection) -> None:
    """Repair icon-based legacy classification once, preserving later edits."""
    from .resource_metadata import infer_class, json_object, source_resource_kind

    migration = "resource_heading_kinds_v2"
    if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (migration,)).fetchone():
        return
    sources_by_media = {}
    for row in connection.execute("""
        SELECT s.id, s.media_id, s.resource_class, s.raw_label, s.metadata_json,
               m.media_type, m.resource_kind AS media_kind
        FROM source_records s JOIN media_items m ON m.id=s.media_id
    """).fetchall():
        kind = source_resource_kind(row["metadata_json"])
        context = str(json_object(row["metadata_json"]).get("context") or "")
        # The old LIKE '%🗂%' / LIKE '%👤%' test matched metadata and prose
        # anywhere in the message, even after an ordinary movie/TV heading.
        misleading_icon = kind == "media" and any(icon in context for icon in ("👤", "🗂"))
        sources_by_media.setdefault(row["media_id"], []).append((row, kind, misleading_icon))
        if misleading_icon and row["resource_class"] in {"person", "collection"}:
            connection.execute("UPDATE source_records SET resource_class=? WHERE id=?",
                               (infer_class("media", row["raw_label"], row["media_type"]), row["id"]))

    for media_id, sources in sources_by_media.items():
        current = sources[0][0]["media_kind"]
        kinds = {kind for _, kind, _ in sources if kind}
        # Preserve real profiles/collections, including legacy heading-only
        # metadata. A missing context is not proof of a misclassification.
        expected = next((kind for kind in ("person", "series") if kind in kinds), None)
        if expected is None and any(misleading for _, _, misleading in sources):
            expected = "media"
        if expected and current != expected:
            connection.execute("UPDATE media_items SET resource_kind=? WHERE id=?", (expected, media_id))
    connection.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))


def _merge_duplicate_person_entries(connection: sqlite3.Connection) -> None:
    """把清洗标题后同名的重复人物条目合并成一条，来源挂到保留条目上。"""
    groups = connection.execute(
        """SELECT normalized_title FROM media_items
           WHERE resource_kind='person' AND normalized_title != ''
           GROUP BY normalized_title HAVING COUNT(*) > 1"""
    ).fetchall()
    for group in groups:
        rows = connection.execute(
            "SELECT * FROM media_items WHERE resource_kind='person' AND normalized_title=? ORDER BY id",
            (group["normalized_title"],),
        ).fetchall()
        keep = dict(rows[0])
        keep_meta = {}
        try:
            keep_meta = json.loads(keep.get("resource_metadata_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            keep_meta = {}
        for dup in rows[1:]:
            connection.execute("UPDATE source_records SET media_id=? WHERE media_id=?", (keep["id"], dup["id"]))
            connection.execute("UPDATE ingestion_links SET media_id=? WHERE media_id=?", (keep["id"], dup["id"]))
            connection.execute("UPDATE OR IGNORE tmdb_candidates SET media_id=? WHERE media_id=?", (keep["id"], dup["id"]))
            connection.execute("UPDATE OR IGNORE imdb_lookups SET media_id=? WHERE media_id=?", (keep["id"], dup["id"]))
            if not keep_meta:
                try:
                    dup_meta = json.loads(dup["resource_metadata_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    dup_meta = {}
                if dup_meta:
                    connection.execute(
                        "UPDATE media_items SET resource_metadata_json=? WHERE id=?",
                        (json.dumps(dup_meta, ensure_ascii=False), keep["id"]),
                    )
                    keep_meta = dup_meta
            for event in connection.execute("SELECT id, media_ids_json FROM ingestion_events").fetchall():
                try:
                    ids = json.loads(event["media_ids_json"] or "[]")
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(ids, list) or dup["id"] not in ids:
                    continue
                merged = sorted({keep["id"] if value == dup["id"] else value for value in ids})
                connection.execute(
                    "UPDATE ingestion_events SET media_ids_json=? WHERE id=?",
                    (json.dumps(merged), event["id"]),
                )
            connection.execute("DELETE FROM media_items WHERE id=?", (dup["id"],))


def _migrate_music_classes_v2(connection: sqlite3.Connection) -> None:
    """Fix the over-broad v1 music rule (FLAC/DSD matched movie release names).

    v1 also left album packs half-classified: the .flac tracks became music
    while their .lrc/.jpg companions stayed 影视. Here we re-derive every source
    currently marked music, then let an audio-only pack lift all its files.
    """
    from .resource_metadata import infer_class, looks_like_video, source_resource_kind

    migration = "resource_music_classes_v2"
    if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (migration,)).fetchone():
        return
    rows = connection.execute("""
        SELECT s.id, s.media_id, s.resource_class, s.raw_label, s.filename, s.metadata_json,
               m.media_type, m.resource_kind AS media_kind
        FROM source_records s JOIN media_items m ON m.id=s.media_id
    """).fetchall()
    packs: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        if row["resource_class"] not in {"media", "other", "music"}:
            continue
        kind = source_resource_kind(row["metadata_json"]) or row["media_kind"]
        inferred = infer_class(kind, row["raw_label"], row["media_type"], row["filename"])
        if row["resource_class"] == "music" and inferred != "music":
            # 回滚 v1 的误判（电影发布名里的 FLAC / 剧集 mkv）。
            connection.execute("UPDATE source_records SET resource_class=? WHERE id=?", (inferred, row["id"]))
            row = dict(row, resource_class=inferred)
        packs.setdefault(int(row["media_id"]), []).append(row)

    for media_rows in packs.values():
        # 纯音频作品（没有任何视频文件）整体算音乐，避免歌词/封面留在影视里。
        if any(looks_like_video(r["filename"]) for r in media_rows):
            continue
        if not any(r["resource_class"] == "music" for r in media_rows):
            continue
        for r in media_rows:
            if r["resource_class"] in {"media", "other"}:
                connection.execute("UPDATE source_records SET resource_class='music' WHERE id=?", (r["id"],))
    connection.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))


def _migrate_music_classes_v3(connection: sqlite3.Connection) -> None:
    """Catch share-only music packs whose only hint is a format word in the title.

    115 分享没有文件名，《齐秦 转flac分轨》这类只能靠标题里的 flac 认出来；
    带视频文件名的来源不参与，避免把电影的无损音轨发布名当成音乐。
    """
    from .resource_metadata import infer_class, source_resource_kind

    migration = "resource_music_classes_v3"
    if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (migration,)).fetchone():
        return
    for row in connection.execute("""
        SELECT s.id, s.resource_class, s.raw_label, s.filename, s.metadata_json,
               m.media_type, m.resource_kind AS media_kind
        FROM source_records s JOIN media_items m ON m.id=s.media_id
        WHERE s.resource_class IN ('media', 'other')
    """).fetchall():
        kind = source_resource_kind(row["metadata_json"]) or row["media_kind"]
        if infer_class(kind, row["raw_label"], row["media_type"], row["filename"]) == "music":
            connection.execute("UPDATE source_records SET resource_class='music' WHERE id=?", (row["id"],))
    connection.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))


def _migrate_music_classes(connection: sqlite3.Connection) -> None:
    """Re-classify legacy sources that are music but were stored as media/other.

    Concerts and lossless albums came in before the music rule existed (or with
    a filename-only label), so they sat in 影视/未识别 and the music category
    looked almost empty. Only upgrades are applied: person/collection stay put,
    and anything already music is untouched.
    """
    from .resource_metadata import infer_class, source_resource_kind

    migration = "resource_music_classes_v1"
    if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (migration,)).fetchone():
        return
    rows = connection.execute("""
        SELECT s.id, s.resource_class, s.raw_label, s.filename, s.metadata_json,
               m.media_type, m.resource_kind AS media_kind
        FROM source_records s JOIN media_items m ON m.id=s.media_id
    """).fetchall()
    for row in rows:
        if row["resource_class"] not in {"media", "other"}:
            continue
        kind = source_resource_kind(row["metadata_json"]) or row["media_kind"]
        inferred = infer_class(kind, row["raw_label"], row["media_type"], row["filename"])
        if inferred == "music":
            connection.execute("UPDATE source_records SET resource_class='music' WHERE id=?", (row["id"],))
    connection.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))


def _migrate_person_entries(connection: sqlite3.Connection) -> None:
    """人物条目按名字聚合：清洗标题、去掉生日年份、合并重复人物条目。

    历史数据里人物名可能带尾随标点（「浅野心，」），生日年份被当成作品年份
    存进了 year；不清洗会让同一个人裂成多个条目，无法在人物分类里聚合。
    """
    from .importers import normalize_title
    from .resource_metadata import clean_person_name

    migration = "person_entries_aggregation_v1"
    if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (migration,)).fetchone():
        return
    for row in connection.execute("SELECT id, title FROM media_items WHERE resource_kind='person'").fetchall():
        cleaned = clean_person_name(row["title"])
        normalized = normalize_title(cleaned) if cleaned else ""
        if normalized:
            connection.execute(
                "UPDATE media_items SET title=?, normalized_title=?, year=NULL WHERE id=?",
                (cleaned, normalized, row["id"]),
            )
        else:
            connection.execute("UPDATE media_items SET year=NULL WHERE id=?", (row["id"],))
    connection.execute("UPDATE source_records SET parsed_year=NULL WHERE resource_class='person'")
    _merge_duplicate_person_entries(connection)
    connection.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))


def _migrate_person_message_attribution(connection: sqlite3.Connection) -> None:
    """历史人物消息重新归属：人物简介消息解析出的资源全部挂到人物条目。

    旧解析器不看消息级的「👤 人物名」标题，会把同一人物的作品按年份拆成多条
    普通影视条目（例如「浅野心」裂成 2023/2024/2025/2026 多条）。这里按消息
    重新归属：消息内全部来源移动到清洗后姓名对应的人物条目，事件与链接同步
    改指，清空后的占位影视条目删除。规则只作用于 lsp115 人物频道，与其他频道
    的 👤 标题保持隔离。
    """
    from .importers import normalize_title
    from .resource_metadata import clean_person_name

    migration = "person_message_attribution_v1"
    if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (migration,)).fetchone():
        return

    # 懒加载，避免 schema 与 ingestion 的模块级循环导入。
    from .ingestion import _is_person_resource_channel, _lsp115_person_context

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # 人物条目本身不承载作品年份，媒体类型统一为 unknown。
    connection.execute("UPDATE media_items SET media_type='unknown', year=NULL WHERE resource_kind='person'")
    events = connection.execute(
        """SELECT id, service, channel_username, channel_name, message_url, raw_text
           FROM ingestion_events
           WHERE raw_text LIKE '%👤%'
             AND (lower(replace(replace(coalesce(channel_username,''),'@',''),' ',''))='lsp115'
                  OR replace(replace(coalesce(channel_name,''),' ',''),'　','')='115网盘资源分享频道'
                  OR coalesce(message_url,'') LIKE '%/lsp115/%')"""
    ).fetchall()
    for event in events:
        source_info = dict(event)
        if not _is_person_resource_channel(source_info):
            continue
        context = _lsp115_person_context(event["raw_text"], source_info)
        if not context:
            continue
        name = clean_person_name(context.splitlines()[0])
        normalized = normalize_title(name)
        if not normalized:
            continue
        links = connection.execute(
            "SELECT id, source_key, media_id FROM ingestion_links WHERE ingestion_id=? AND source_key IS NOT NULL",
            (event["id"],),
        ).fetchall()
        link_keys = [link["source_key"] for link in links if link["source_key"]]
        if not link_keys:
            continue
        rows = connection.execute(
            "SELECT id, source_key, media_id FROM source_records WHERE source_key IN (%s)"
            % ",".join("?" * len(link_keys)),
            link_keys,
        ).fetchall()
        if not rows:
            continue

        person = connection.execute(
            "SELECT id FROM media_items WHERE resource_kind='person' AND normalized_title=? ORDER BY id LIMIT 1",
            (normalized,),
        ).fetchone()
        person_id = int(person["id"]) if person else None
        if person_id is None:
            # 该消息的来源恰好只落在一条普通条目上时，提升该条目并保留 id。
            media_ids = {row["media_id"] for row in rows}
            if len(media_ids) == 1:
                candidate_id = next(iter(media_ids))
                total = connection.execute(
                    "SELECT COUNT(*) FROM source_records WHERE media_id=?", (candidate_id,)
                ).fetchone()[0]
                if total == len(rows):
                    connection.execute(
                        """UPDATE media_items SET title=?, normalized_title=?, year=NULL,
                           media_type='unknown', resource_kind='person' WHERE id=?""",
                        (name, normalized, candidate_id),
                    )
                    person_id = int(candidate_id)
        if person_id is None:
            cursor = connection.execute(
                """INSERT INTO media_items
                   (title, normalized_title, year, media_type, resource_kind, resource_metadata_json, created_at, updated_at)
                   VALUES (?, ?, NULL, 'unknown', 'person', '{}', ?, ?)""",
                (name, normalized, now, now),
            )
            person_id = int(cursor.lastrowid)

        by_key = {row["source_key"]: row for row in rows}
        previous_media_ids: set[int] = set()
        for link in links:
            row = by_key.get(link["source_key"])
            if row is not None:
                previous_media_ids.add(int(row["media_id"]))
                connection.execute(
                    "UPDATE source_records SET media_id=?, resource_class='person', parsed_year=NULL WHERE id=?",
                    (person_id, row["id"]),
                )
            if link["media_id"] is not None:
                previous_media_ids.add(int(link["media_id"]))
            connection.execute("UPDATE ingestion_links SET media_id=? WHERE id=?", (person_id, link["id"]))
        connection.execute(
            "UPDATE ingestion_events SET media_ids_json=? WHERE id=?",
            (json.dumps([person_id]), event["id"]),
        )
        # 清空后没有来源、也没有 TMDB 身份的占位条目才删除；人物条目保留。
        for media_id in previous_media_ids - {person_id}:
            connection.execute(
                """DELETE FROM media_items
                   WHERE id=? AND resource_kind != 'person' AND tmdb_id IS NULL
                     AND tmdb_status != 'matched'
                     AND NOT EXISTS (SELECT 1 FROM source_records WHERE media_id=media_items.id)""",
                (media_id,),
            )
    connection.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))


def _promote_person_media(
    connection: sqlite3.Connection,
    media_id: int,
    name: str,
    normalized: str,
    metadata_json: str,
    now: str,
) -> None:
    """Turn a wrongly classified, unmatched media row into a person entry."""
    connection.execute(
        """UPDATE media_items
           SET title=?, normalized_title=?, year=NULL, media_type='unknown', resource_kind='person',
               resource_metadata_json=?, tmdb_status='pending', tmdb_id=NULL, tmdb_media_type=NULL,
               tmdb_title=NULL, original_title=NULL, overview=NULL, poster_path=NULL, backdrop_path=NULL,
               release_date=NULL, vote_average=NULL, genres_json=NULL, origin_country_json=NULL,
               match_confidence=NULL, match_method=NULL, last_tmdb_error=NULL, seasons_json=NULL,
               series_id=NULL, is_bundle=0, is_metadata=0, metadata_reason=NULL, updated_at=?
           WHERE id=?""",
        (name, normalized, metadata_json, now, media_id),
    )
    connection.execute("DELETE FROM tmdb_candidates WHERE media_id=?", (media_id,))
    connection.execute("DELETE FROM imdb_lookups WHERE media_id=?", (media_id,))


def _migrate_person_message_attribution_v2(connection: sqlite3.Connection) -> None:
    """Repair person messages whose files were first imported as a media work.

    v1 only promoted an unmatched placeholder when the message's files were its
    *only* sources.  A profile can share its ED2K files with an earlier media
    collection post; those files then sit under a movie/tv row with a stale
    title and ``resource_class='collection'``.  This pass promotes that row in
    place when all of its sources belong to the profile, otherwise it creates a
    separate person entry and moves only the profile's sources.
    """
    from .importers import normalize_title
    from .resource_metadata import clean_person_name

    migration = "person_message_attribution_v2"
    if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (migration,)).fetchone():
        return

    from .ingestion import _is_person_resource_channel, _lsp115_person_context

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    events = connection.execute(
        """SELECT id, service, channel_username, channel_name, message_url, raw_text
           FROM ingestion_events
           WHERE raw_text LIKE '%👤%'
             AND (lower(replace(replace(coalesce(channel_username,''),'@',''),' ',''))='lsp115'
                  OR replace(replace(coalesce(channel_name,''),' ',''),'　','')='115网盘资源分享频道'
                  OR coalesce(message_url,'') LIKE '%/lsp115/%')"""
    ).fetchall()
    for event in events:
        source_info = dict(event)
        if not _is_person_resource_channel(source_info):
            continue
        context = _lsp115_person_context(event["raw_text"], source_info)
        if not context:
            continue
        name = clean_person_name(context.splitlines()[0])
        normalized = normalize_title(name)
        if not normalized:
            continue
        links = connection.execute(
            "SELECT id, source_key, media_id FROM ingestion_links WHERE ingestion_id=? AND source_key IS NOT NULL",
            (event["id"],),
        ).fetchall()
        link_keys = [link["source_key"] for link in links if link["source_key"]]
        if not link_keys:
            continue
        placeholders = ",".join("?" * len(link_keys))
        rows = connection.execute(
            "SELECT id, source_key, media_id, metadata_json FROM source_records WHERE source_key IN (%s)"
            % placeholders,
            link_keys,
        ).fetchall()
        if not rows:
            continue

        person = connection.execute(
            "SELECT id FROM media_items WHERE resource_kind='person' AND normalized_title=? ORDER BY id LIMIT 1",
            (normalized,),
        ).fetchone()
        person_id = int(person["id"]) if person else None
        old_media_ids = {int(row["media_id"]) for row in rows}
        if person_id is not None:
            old_media_ids.add(person_id)
        # Never move files away from a confirmed TMDB identity. If a profile
        # shares files with a matched work, leave that work alone.
        other_media_ids = old_media_ids - ({person_id} if person_id is not None else set())
        if other_media_ids:
            identities = connection.execute(
                "SELECT id, tmdb_id, tmdb_status FROM media_items WHERE id IN (%s)"
                % ",".join("?" * len(other_media_ids)),
                list(other_media_ids),
            ).fetchall()
            if any(row["tmdb_id"] is not None or row["tmdb_status"] == "matched" for row in identities):
                continue
        if person_id is None:
            if len(old_media_ids) == 1:
                candidate_id = next(iter(old_media_ids))
                total = connection.execute(
                    "SELECT COUNT(*) FROM source_records WHERE media_id=?", (candidate_id,)
                ).fetchone()[0]
                if total == len(rows):
                    person_id = candidate_id
                    _promote_person_media(connection, person_id, name, normalized, "{}", now)
        if person_id is None:
            cursor = connection.execute(
                """INSERT INTO media_items
                   (title, normalized_title, year, media_type, resource_kind, resource_metadata_json, created_at, updated_at)
                   VALUES (?, ?, NULL, 'unknown', 'person', '{}', ?, ?)""",
                (name, normalized, now, now),
            )
            person_id = int(cursor.lastrowid)
        else:
            _promote_person_media(connection, person_id, name, normalized, "{}", now)

        metadata_json = "{}"
        for row in rows:
            try:
                candidate = json.loads(row["metadata_json"] or "{}")
            except (TypeError, ValueError):
                candidate = {}
            if candidate.get("resource_kind") == "person":
                metadata_json = json.dumps(candidate, ensure_ascii=False)
                break
        if metadata_json == "{}":
            metadata_json = json.dumps(
                {"context": context, "resource_kind": "person", "channel_fields": {}},
                ensure_ascii=False,
            )
        connection.execute(
            "UPDATE media_items SET resource_metadata_json=? WHERE id=?",
            (metadata_json, person_id),
        )

        for link in links:
            connection.execute(
                "UPDATE ingestion_links SET media_id=? WHERE id=?",
                (person_id, link["id"]),
            )
        connection.execute(
            "UPDATE source_records SET media_id=?, resource_class='person', parsed_year=NULL"
            " WHERE source_key IN (%s)" % placeholders,
            [person_id, *link_keys],
        )
        connection.execute(
            "UPDATE ingestion_events SET media_ids_json=? WHERE id=?",
            (json.dumps([person_id]), event["id"]),
        )
        for media_id in old_media_ids - {person_id}:
            connection.execute(
                """DELETE FROM media_items
                   WHERE id=? AND resource_kind != 'person' AND tmdb_id IS NULL
                     AND tmdb_status != 'matched'
                     AND NOT EXISTS (SELECT 1 FROM source_records WHERE media_id=media_items.id)""",
                (media_id,),
            )
            if not connection.execute("SELECT 1 FROM media_items WHERE id=?", (media_id,)).fetchone():
                connection.execute("DELETE FROM tmdb_candidates WHERE media_id=?", (media_id,))
                connection.execute("DELETE FROM imdb_lookups WHERE media_id=?", (media_id,))
    # A profile message can share its keys with another profile event; the last
    # attribution wins, so an earlier placeholder can end up with no sources.
    # Do not leave empty person rows behind.
    connection.execute(
        """DELETE FROM tmdb_candidates WHERE media_id IN (
               SELECT id FROM media_items m WHERE m.resource_kind='person'
                 AND m.tmdb_id IS NULL AND m.tmdb_status != 'matched'
                 AND NOT EXISTS (SELECT 1 FROM source_records s WHERE s.media_id=m.id)
           )"""
    )
    connection.execute(
        """DELETE FROM imdb_lookups WHERE media_id IN (
               SELECT id FROM media_items m WHERE m.resource_kind='person'
                 AND m.tmdb_id IS NULL AND m.tmdb_status != 'matched'
                 AND NOT EXISTS (SELECT 1 FROM source_records s WHERE s.media_id=m.id)
           )"""
    )
    connection.execute(
        """DELETE FROM media_items WHERE resource_kind='person'
             AND tmdb_id IS NULL AND tmdb_status != 'matched'
             AND NOT EXISTS (SELECT 1 FROM source_records s WHERE s.media_id=media_items.id)"""
    )
    connection.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))


def _migrate_person_tmdb_state(connection: sqlite3.Connection) -> None:
    """人物条目不是 TMDB 影视作品：清掉历史匹配错误与候选状态。

    旧流程会把人物名当作品送去 TMDB 匹配，失败后写入 tmdb_status='error'，
    资源库因此给人物资源显示「匹配错误」。人物条目不再参与匹配，这里把历史
    状态复位为 pending，并清理人物条目下的候选与 IMDb 附属数据。
    """
    migration = "person_tmdb_state_reset_v1"
    if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (migration,)).fetchone():
        return
    connection.execute(
        """UPDATE media_items SET tmdb_status='pending', tmdb_id=NULL, tmdb_media_type=NULL,
           tmdb_title=NULL, original_title=NULL, overview=NULL, poster_path=NULL, backdrop_path=NULL,
           release_date=NULL, vote_average=NULL, genres_json=NULL, origin_country_json=NULL,
           match_confidence=NULL, match_method=NULL, last_tmdb_error=NULL, seasons_json=NULL
           WHERE resource_kind='person'"""
    )
    connection.execute(
        "DELETE FROM tmdb_candidates WHERE media_id IN (SELECT id FROM media_items WHERE resource_kind='person')"
    )
    connection.execute(
        "DELETE FROM imdb_lookups WHERE media_id IN (SELECT id FROM media_items WHERE resource_kind='person')"
    )
    connection.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))


def _migrate_metadata_packs(connection: sqlite3.Connection) -> None:
    """Flag unmatched packs that contain only artwork/NFO/subtitle/lyrics.

    One-time backfill for databases created before ``media_items.is_metadata``
    existed. Matched records are never touched: a real TMDB identity wins over
    a filename heuristic. New imports are classified in ``repository.py``.
    """
    migration = "metadata_packs_v1"
    if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (migration,)).fetchone():
        return
    from .resource_metadata import classify_metadata_pack

    rows = connection.execute(
        """
        SELECT m.id, m.title, s.filename
        FROM media_items m
        LEFT JOIN source_records s ON s.media_id = m.id
        WHERE m.tmdb_status != 'matched' AND m.resource_kind = 'media'
        ORDER BY m.id, s.id
        """
    ).fetchall()
    titles: dict[int, str] = {}
    files: dict[int, list[str]] = {}
    for row in rows:
        media_id = int(row["id"])
        titles[media_id] = str(row["title"] or "")
        files.setdefault(media_id, [])
        if row["filename"] is not None:
            files[media_id].append(row["filename"])
    for media_id, title in titles.items():
        is_metadata, reason = classify_metadata_pack(title, files.get(media_id, []))
        connection.execute(
            "UPDATE media_items SET is_metadata = ?, metadata_reason = ? WHERE id = ?",
            (1 if is_metadata else 0, reason, media_id),
        )
    connection.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))


def _migrate_metadata_packs_v2(connection: sqlite3.Connection) -> None:
    """Undo v1's subtitle/lyrics false positives.

    v1 treated a standalone .srt/.lrc as metadata, but external Chinese
    subtitles and lyrics are normal resources. Re-run the (now artwork/NFO-only)
    classifier over rows already flagged by v1 and clear the flag. Status,
    candidates and updated_at are intentionally left untouched: these records
    were already reviewed/not-found before the flag existed.
    """
    migration = "metadata_packs_v2"
    if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (migration,)).fetchone():
        return
    from .resource_metadata import classify_metadata_pack

    rows = connection.execute(
        """
        SELECT m.id, m.title, s.filename
        FROM media_items m
        LEFT JOIN source_records s ON s.media_id = m.id
        WHERE m.is_metadata = 1 AND m.resource_kind = 'media' AND m.tmdb_status != 'matched'
        ORDER BY m.id, s.id
        """
    ).fetchall()
    titles: dict[int, str] = {}
    files: dict[int, list[str]] = {}
    for row in rows:
        media_id = int(row["id"])
        titles[media_id] = str(row["title"] or "")
        files.setdefault(media_id, [])
        if row["filename"] is not None:
            files[media_id].append(row["filename"])
    for media_id, title in titles.items():
        is_metadata, reason = classify_metadata_pack(title, files.get(media_id, []))
        if not is_metadata:
            connection.execute(
                "UPDATE media_items SET is_metadata = 0, metadata_reason = NULL WHERE id = ?",
                (media_id,),
            )
    connection.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))


def _migrate_collection_classes(connection: sqlite3.Connection) -> None:
    """Put existing collection sources into the resource-library 合集 group.

    The collection marker parser gained 合辑/套装/全系列/部曲/N部 等写法 after
    the first databases were built.  ``insert_source`` only classifies new rows;
    this one-time backfill re-derives ``resource_class`` for existing sources.
    A source of a media whose title is a collection also becomes collection,
    so an aggregate made of individual filenames (指环王 全3部) is not left in
    影视.  Music and person classification wins: 音乐合集 stays 音乐, not 合集.
    """
    migration = "resource_collection_classes_v1"
    if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (migration,)).fetchone():
        return
    from .resource_metadata import COLLECTION_RE, infer_class, source_resource_kind

    rows = connection.execute(
        """
        SELECT s.id, s.resource_class, s.raw_label, s.filename, s.metadata_json,
               m.media_type, m.resource_kind AS media_kind, m.title AS media_title,
               m.is_bundle
        FROM source_records s JOIN media_items m ON m.id = s.media_id
        """
    ).fetchall()
    for row in rows:
        current = str(row["resource_class"] or "media")
        if current == "person":
            continue
        kind = source_resource_kind(row["metadata_json"]) or row["media_kind"]
        inferred = infer_class(kind, row["raw_label"], row["media_type"], row["filename"])
        # 音乐/人物身份优先；音乐合集即使文件名是 .zip 也不能被归成合集。
        media_inferred = infer_class(kind, row["media_title"], row["media_type"], None)
        if inferred == "music" or media_inferred == "music":
            target = "music"
        elif kind == "series" or COLLECTION_RE.search(row["media_title"] or "") or row["is_bundle"]:
            target = "collection"
        else:
            target = inferred
        # 这是一次「合集归类」迁移：只处理合集的加/减，不顺手把所有
        # other→media、music→media 的老分类都改掉，避免一次迁移改动过大。
        if target == current or (target not in {"collection", "music"} and current != "collection"):
            continue
        connection.execute("UPDATE source_records SET resource_class = ? WHERE id = ?", (target, row["id"]))
    connection.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))




def _ensure_search_index(connection):
    """Local search uses deduplicated raw messages plus a small trigram index.

    A source row repeats the whole channel message; building FTS on
    source_records.raw_text would index ~1.4 GB of duplicate text and make the
    first upgrade take a minute. Keep one copy per distinct message and map
    sources/events to it instead.
    """
    source_columns = {row["name"] for row in connection.execute("PRAGMA table_info(source_records)")}
    if "search_text" not in source_columns:
        connection.execute("ALTER TABLE source_records ADD COLUMN search_text TEXT NOT NULL DEFAULT ''")
        connection.execute("""UPDATE source_records SET search_text = COALESCE((
            SELECT group_concat(j.value, ' ') FROM json_tree(
                CASE WHEN json_valid(metadata_json) THEN metadata_json ELSE '{}' END
            ) j WHERE j.type='text'), '')""")
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_texts'"
    ).fetchone()
    if not exists:
        connection.executescript("""
            CREATE TABLE search_texts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL UNIQUE
            );
            CREATE TABLE source_text_map (
                source_id INTEGER PRIMARY KEY,
                text_id INTEGER NOT NULL
            );
            CREATE TABLE event_text_map (
                event_id TEXT PRIMARY KEY,
                text_id INTEGER NOT NULL
            );
            CREATE INDEX idx_source_text_map_text ON source_text_map(text_id);
            CREATE INDEX idx_event_text_map_text ON event_text_map(text_id);
            CREATE VIRTUAL TABLE search_text_fts USING fts5(
                text, content='search_texts', content_rowid='id', tokenize='trigram'
            );
            INSERT OR IGNORE INTO search_texts(text)
                SELECT raw_text FROM source_records WHERE raw_text IS NOT NULL AND raw_text <> '';
            INSERT OR IGNORE INTO search_texts(text)
                SELECT raw_text FROM ingestion_events WHERE raw_text IS NOT NULL AND raw_text <> '';
            INSERT OR REPLACE INTO source_text_map(source_id, text_id)
                SELECT s.id, t.id FROM source_records s JOIN search_texts t ON t.text = s.raw_text
                WHERE s.raw_text IS NOT NULL AND s.raw_text <> '';
            INSERT OR REPLACE INTO event_text_map(event_id, text_id)
                SELECT e.id, t.id FROM ingestion_events e JOIN search_texts t ON t.text = e.raw_text
                WHERE e.raw_text IS NOT NULL AND e.raw_text <> '';
            INSERT INTO search_text_fts(search_text_fts) VALUES('rebuild');
        """)
    else:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS source_text_map (source_id INTEGER PRIMARY KEY, text_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS event_text_map (event_id TEXT PRIMARY KEY, text_id INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_source_text_map_text ON source_text_map(text_id);
            CREATE INDEX IF NOT EXISTS idx_event_text_map_text ON event_text_map(text_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS search_text_fts USING fts5(
                text, content='search_texts', content_rowid='id', tokenize='trigram'
            );
        """)
    connection.executescript("""
        CREATE TRIGGER IF NOT EXISTS search_texts_ai AFTER INSERT ON search_texts BEGIN
            INSERT INTO search_text_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS search_texts_ad AFTER DELETE ON search_texts BEGIN
            INSERT INTO search_text_fts(search_text_fts, rowid, text) VALUES ('delete', old.id, old.text);
        END;
        CREATE TRIGGER IF NOT EXISTS search_texts_au AFTER UPDATE OF text ON search_texts BEGIN
            INSERT INTO search_text_fts(search_text_fts, rowid, text) VALUES ('delete', old.id, old.text);
            INSERT INTO search_text_fts(rowid, text) VALUES (new.id, new.text);
        END;

        CREATE TRIGGER IF NOT EXISTS source_text_map_ai AFTER INSERT ON source_records BEGIN
            INSERT OR IGNORE INTO search_texts(text) SELECT new.raw_text WHERE new.raw_text <> '';
            DELETE FROM source_text_map WHERE source_id = new.id;
            INSERT INTO source_text_map(source_id, text_id)
                SELECT new.id, id FROM search_texts WHERE text = new.raw_text AND new.raw_text <> '';
        END;
        CREATE TRIGGER IF NOT EXISTS source_text_map_au AFTER UPDATE OF raw_text ON source_records BEGIN
            INSERT OR IGNORE INTO search_texts(text) SELECT new.raw_text WHERE new.raw_text <> '';
            DELETE FROM source_text_map WHERE source_id = new.id;
            INSERT INTO source_text_map(source_id, text_id)
                SELECT new.id, id FROM search_texts WHERE text = new.raw_text AND new.raw_text <> '';
        END;
        CREATE TRIGGER IF NOT EXISTS source_text_map_ad AFTER DELETE ON source_records BEGIN
            DELETE FROM source_text_map WHERE source_id = old.id;
        END;

        CREATE TRIGGER IF NOT EXISTS event_text_map_ai AFTER INSERT ON ingestion_events BEGIN
            INSERT OR IGNORE INTO search_texts(text) SELECT new.raw_text WHERE new.raw_text <> '';
            DELETE FROM event_text_map WHERE event_id = new.id;
            INSERT INTO event_text_map(event_id, text_id)
                SELECT new.id, id FROM search_texts WHERE text = new.raw_text AND new.raw_text <> '';
        END;
        CREATE TRIGGER IF NOT EXISTS event_text_map_au AFTER UPDATE OF raw_text ON ingestion_events BEGIN
            INSERT OR IGNORE INTO search_texts(text) SELECT new.raw_text WHERE new.raw_text <> '';
            DELETE FROM event_text_map WHERE event_id = new.id;
            INSERT INTO event_text_map(event_id, text_id)
                SELECT new.id, id FROM search_texts WHERE text = new.raw_text AND new.raw_text <> '';
        END;
        CREATE TRIGGER IF NOT EXISTS event_text_map_ad AFTER DELETE ON ingestion_events BEGIN
            DELETE FROM event_text_map WHERE event_id = old.id;
        END;

        CREATE TRIGGER IF NOT EXISTS source_search_text_ai AFTER INSERT ON source_records BEGIN
            UPDATE source_records SET search_text = COALESCE((
                SELECT group_concat(j.value, ' ') FROM json_tree(
                    CASE WHEN json_valid(new.metadata_json) THEN new.metadata_json ELSE '{}' END
                ) j WHERE j.type='text'), '') WHERE id = new.id;
        END;
        CREATE TRIGGER IF NOT EXISTS source_search_text_au AFTER UPDATE OF metadata_json ON source_records BEGIN
            UPDATE source_records SET search_text = COALESCE((
                SELECT group_concat(j.value, ' ') FROM json_tree(
                    CASE WHEN json_valid(new.metadata_json) THEN new.metadata_json ELSE '{}' END
                ) j WHERE j.type='text'), '') WHERE id = new.id;
        END;
    """)


def init_db(db_path: str | os.PathLike[str] | None = None) -> Path:
    path = resolve_db_path(db_path)
    with connect(path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(source_records)")}
        if "provider" not in columns:
            connection.execute("ALTER TABLE source_records ADD COLUMN provider TEXT NOT NULL DEFAULT '115'")
        if "season_end" not in columns:
            connection.execute("ALTER TABLE source_records ADD COLUMN season_end INTEGER")
        for name, definition in (
            ("resource_class", "TEXT NOT NULL DEFAULT 'media'"),
            ("tags_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("notes", "TEXT NOT NULL DEFAULT ''"),
            ("availability_status", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("last_checked_at", "TEXT"),
            ("last_check_error", "TEXT"),
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE source_records ADD COLUMN {name} {definition}")
        media_columns = {row["name"] for row in connection.execute("PRAGMA table_info(media_items)")}
        if "resource_kind" not in media_columns:
            connection.execute("ALTER TABLE media_items ADD COLUMN resource_kind TEXT NOT NULL DEFAULT 'media'")
        if "resource_metadata_json" not in media_columns:
            connection.execute("ALTER TABLE media_items ADD COLUMN resource_metadata_json TEXT NOT NULL DEFAULT '{}'")
        if "seasons_json" not in media_columns:
            # TMDB 剧集季集基准：[{"season": 1, "episode_count": 12}, ...]，供覆盖格子图使用。
            connection.execute("ALTER TABLE media_items ADD COLUMN seasons_json TEXT")
        if "series_id" not in media_columns:
            connection.execute("ALTER TABLE media_items ADD COLUMN series_id INTEGER REFERENCES media_series(id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_media_series ON media_items(series_id)")
        if "is_bundle" not in media_columns:
            connection.execute("ALTER TABLE media_items ADD COLUMN is_bundle INTEGER NOT NULL DEFAULT 0")
        if "is_metadata" not in media_columns:
            connection.execute("ALTER TABLE media_items ADD COLUMN is_metadata INTEGER NOT NULL DEFAULT 0")
        if "metadata_reason" not in media_columns:
            connection.execute("ALTER TABLE media_items ADD COLUMN metadata_reason TEXT")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_media_metadata ON media_items(is_metadata) WHERE is_metadata = 1")
        _migrate_resource_headings(connection)
        # Backfill only when introducing the source management fields. Never overwrite user edits.
        if "resource_class" not in columns:
            from .resource_metadata import infer_class, snapshot_state, source_resource_kind
            for row in connection.execute("SELECT s.id,s.raw_label,s.metadata_json,m.resource_kind,m.media_type FROM source_records s JOIN media_items m ON m.id=s.media_id").fetchall():
                state, checked_at = snapshot_state(row["metadata_json"])
                connection.execute("UPDATE source_records SET resource_class=?, availability_status=?, last_checked_at=? WHERE id=?",
                                   (infer_class(source_resource_kind(row["metadata_json"]) or row["resource_kind"], row["raw_label"], row["media_type"]), state, checked_at, row["id"]))
            connection.execute("""UPDATE source_records SET availability_status='available',
                last_checked_at=(SELECT MAX(e.processed_at) FROM ingestion_links l JOIN ingestion_events e ON e.id=l.ingestion_id
                                 WHERE l.source_key=source_records.source_key AND l.status IN ('inserted','duplicate'))
                WHERE source_type != 'ed2k' AND availability_status='unknown'
                AND EXISTS (SELECT 1 FROM ingestion_links l WHERE l.source_key=source_records.source_key AND l.status IN ('inserted','duplicate'))""")
        _migrate_person_entries(connection)
        _migrate_music_classes(connection)
        _migrate_music_classes_v2(connection)
        _migrate_music_classes_v3(connection)
        _migrate_person_message_attribution(connection)
        _migrate_person_message_attribution_v2(connection)
        _migrate_person_tmdb_state(connection)
        _migrate_metadata_packs(connection)
        _migrate_metadata_packs_v2(connection)
        _migrate_collection_classes(connection)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sources_provider ON source_records(provider)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sources_resource_class ON source_records(resource_class)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sources_availability ON source_records(availability_status)")
        ingestion_columns = {row["name"] for row in connection.execute("PRAGMA table_info(ingestion_events)")}
        for name, definition in (
            ("channel_username", "TEXT"),
            ("message_url", "TEXT"),
            ("channel_avatar_url", "TEXT"),
        ):
            if name not in ingestion_columns:
                connection.execute(f"ALTER TABLE ingestion_events ADD COLUMN {name} {definition}")
        link_columns = {row["name"] for row in connection.execute("PRAGMA table_info(ingestion_links)")}
        for name, definition in (
            ("validation_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("next_retry_at", "TEXT"),
            ("validation_error", "TEXT"),
        ):
            if name not in link_columns:
                connection.execute(f"ALTER TABLE ingestion_links ADD COLUMN {name} {definition}")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_ingestion_links_validation ON ingestion_links(status, next_retry_at)")
        table_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='source_records'").fetchone()[0] or ""
        if "source_type IN ('115', 'ed2k')" in table_sql:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.executescript("""
                CREATE TABLE source_records_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
                    import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
                    source_type TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '115',
                    source_key TEXT NOT NULL UNIQUE,
                    raw_label TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    url TEXT NOT NULL,
                    filename TEXT,
                    file_size INTEGER,
                    ed2k_hash TEXT,
                    season INTEGER,
                    season_end INTEGER,
                    episode INTEGER,
                    parsed_year INTEGER,
                    quality TEXT,
                    codec TEXT,
                    hdr TEXT,
                    audio TEXT,
                    release_group TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                );
                INSERT INTO source_records_v2 SELECT id, media_id, import_id, source_type, provider,
                    source_key, raw_label, raw_text, url, filename, file_size, ed2k_hash, season, season_end,
                    episode, parsed_year, quality, codec, hdr, audio, release_group, metadata_json, created_at
                    FROM source_records;
                DROP TABLE source_records;
                ALTER TABLE source_records_v2 RENAME TO source_records;
                CREATE INDEX IF NOT EXISTS idx_sources_media ON source_records(media_id);
                CREATE INDEX IF NOT EXISTS idx_sources_type ON source_records(source_type);
                CREATE INDEX IF NOT EXISTS idx_sources_provider ON source_records(provider);
                CREATE INDEX IF NOT EXISTS idx_sources_episode ON source_records(media_id, season, episode);
            """)
            connection.execute("PRAGMA foreign_keys = ON")
        # 资源库默认按「最近检查/入库时间」倒序；表达式索引避免每次列表都全表排序。
        source_columns = {row["name"] for row in connection.execute("PRAGMA table_info(source_records)")}
        if "last_checked_at" in source_columns:
            connection.execute("CREATE INDEX IF NOT EXISTS idx_sources_updated ON source_records(COALESCE(last_checked_at, created_at) DESC, id DESC)")
        _ensure_search_index(connection)
        # Older rows may only have the original S1-S3 heading in metadata.
        # Backfill the first-class range column so the database and UI agree.
        for row in connection.execute("SELECT id, metadata_json FROM source_records WHERE season_end IS NULL"):
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            season_range = metadata.get("season_range")
            if not (isinstance(season_range, list) and len(season_range) == 2):
                match = re.search(r"(?i)\bS(\d{1,2})\s*[-~]\s*S?(\d{1,2})(?=$|\D)", str(metadata.get("context") or ""))
                season_range = [int(match.group(1)), int(match.group(2))] if match else None
            if isinstance(season_range, list) and len(season_range) == 2 and int(season_range[1]) > int(season_range[0]):
                connection.execute("UPDATE source_records SET season_end = ? WHERE id = ?", (int(season_range[1]), row["id"]))
    return path
