from __future__ import annotations

import os
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

CREATE TABLE IF NOT EXISTS source_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('115', 'ed2k')),
    source_key TEXT NOT NULL UNIQUE,
    raw_label TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    url TEXT NOT NULL,
    filename TEXT,
    file_size INTEGER,
    ed2k_hash TEXT,
    season INTEGER,
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
    return path
