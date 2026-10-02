from __future__ import annotations

import os
import json
import re
import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT_DIR / "data" / "media.db"


SCHEMA = """
PRAGMA foreign_keys = ON;

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
        media_columns = {row["name"] for row in connection.execute("PRAGMA table_info(media_items)")}
        if "resource_kind" not in media_columns:
            connection.execute("ALTER TABLE media_items ADD COLUMN resource_kind TEXT NOT NULL DEFAULT 'media'")
        if "resource_metadata_json" not in media_columns:
            connection.execute("ALTER TABLE media_items ADD COLUMN resource_metadata_json TEXT NOT NULL DEFAULT '{}'")
        connection.execute("""
            UPDATE media_items
            SET resource_kind = CASE
                WHEN EXISTS (SELECT 1 FROM source_records s WHERE s.media_id = media_items.id AND json_extract(s.metadata_json, '$.context') LIKE '%👤%') THEN 'person'
                WHEN EXISTS (SELECT 1 FROM source_records s WHERE s.media_id = media_items.id AND json_extract(s.metadata_json, '$.context') LIKE '%🗂%') THEN 'series'
                ELSE resource_kind END
            WHERE resource_kind = 'media'
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sources_provider ON source_records(provider)")
        ingestion_columns = {row["name"] for row in connection.execute("PRAGMA table_info(ingestion_events)")}
        for name, definition in (
            ("channel_username", "TEXT"),
            ("message_url", "TEXT"),
            ("channel_avatar_url", "TEXT"),
        ):
            if name not in ingestion_columns:
                connection.execute(f"ALTER TABLE ingestion_events ADD COLUMN {name} {definition}")
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
