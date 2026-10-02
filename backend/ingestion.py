from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from .importers import detect_source_kind, iter_sources
from .repository import import_content
from .resource_pages import expand_resource_pages
from .schema import connect, init_db
from .tmdb import TmdbClient, TmdbError, search_media
from .telegram_avatar import cache_channel_avatar, fetch_public_channel_avatar, normalize_channel_username


class IngestionError(ValueError):
    pass


_INIT_LOCK = threading.Lock()


def _auto_match_media(media_ids: set[int], db_path=None) -> dict[int, str]:
    """Run the normal TMDB matcher for media created by an incoming message.

    Matching is deliberately best-effort: ingestion has already succeeded if
    the source was stored, and a temporary TMDB/network error must not turn a
    valid webhook into a failed delivery. ``search_media`` still enforces the
    confidence and ambiguity rules, so only high-confidence candidates are
    linked automatically; everything else remains in review/pending state.
    """
    if not media_ids:
        return {}
    client = TmdbClient()
    if not client.configured:
        return {}
    results: dict[int, str] = {}
    for media_id in sorted(media_ids):
        try:
            result = search_media(
                media_id,
                auto_link=True,
                refresh_share=True,
                db_path=db_path,
                client=client,
            )
            results[media_id] = str(result.get("status") or "unknown")
        except TmdbError:
            results[media_id] = "error"
        except Exception:
            # Keep delivery processing resilient to an unexpected matcher
            # failure. The media item remains available for manual retry.
            results[media_id] = "error"
    return results


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event_id() -> str:
    return f"ing_{uuid.uuid4().hex}"


def _text(body: dict[str, Any]) -> str:
    message = body.get("message")
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, dict):
        for key in ("text", "caption"):
            if isinstance(message.get(key), str) and message[key].strip():
                return message[key].strip()
    for key in ("text", "caption"):
        if isinstance(body.get(key), str) and body[key].strip():
            return body[key].strip()
    raise IngestionError("消息必须包含 message.text 或 message.caption。")


def _source_info(body: dict[str, Any]) -> dict[str, str | None]:
    source = body.get("source") if isinstance(body.get("source"), dict) else {}
    return {key: str(source[key]) if source.get(key) is not None else None for key in (
        "service", "channel_id", "channel_name", "channel_username", "message_id", "message_url", "published_at",
    )}


def _refresh_channel_avatar(ingestion_id: str, source: dict[str, str | None], db_path=None) -> None:
    username = normalize_channel_username(source.get("channel_username"))
    if not username:
        return
    with connect(db_path) as db:
        cached = db.execute(
            """SELECT channel_avatar_url FROM ingestion_events
               WHERE channel_username = ? AND channel_avatar_url IS NOT NULL AND channel_avatar_url != ''
               ORDER BY received_at DESC LIMIT 1""",
            (username,),
        ).fetchone()
    cached_path = cache_channel_avatar(username)
    avatar_url = f"/api/channel-avatar/{username}" if cached_path else (cached["channel_avatar_url"] if cached else fetch_public_channel_avatar(username))
    if avatar_url:
        with connect(db_path) as db:
            db.execute("UPDATE ingestion_events SET channel_avatar_url = ? WHERE channel_username = ?", (avatar_url, username))


def receive(body: dict[str, Any], db_path=None) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise IngestionError("请求必须是 JSON 对象。")
    event_id = str(body.get("event_id") or "").strip()
    if not event_id or len(event_id) > 200:
        raise IngestionError("event_id 必须是 1 至 200 个字符。")
    text = _text(body)
    if len(text) > 512 * 1024:
        raise IngestionError("原始消息不能超过 512 KB。")
    source = _source_info(body)
    service = source["service"] or "unknown"
    # The API initializes the schema at startup, but receive() is also used
    # directly by importers and tests. Serialize the idempotent setup because
    # SQLite cannot change journal mode while another initializer is writing.
    with _INIT_LOCK:
        init_db(db_path)
    ingestion_id = _event_id()
    with connect(db_path) as db:
        cursor = db.execute(
            """INSERT INTO ingestion_events
            (id,event_id,service,channel_id,channel_name,channel_username,message_id,message_url,published_at,raw_text,raw_json,status,received_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued',?)
            ON CONFLICT(service, event_id) DO NOTHING""",
            (ingestion_id, event_id, service, source["channel_id"], source["channel_name"], source["channel_username"], source["message_id"], source["message_url"], source["published_at"], text, json.dumps(body, ensure_ascii=False), _now()),
        )
        if cursor.rowcount == 0:
            existing = db.execute("SELECT id,status FROM ingestion_events WHERE service=? AND event_id=?", (service, event_id)).fetchone()
            if existing:
                return {"id": existing["id"], "event_id": event_id, "status": existing["status"], "duplicate": True}
            raise sqlite3.IntegrityError("ingestion event was ignored but could not be read")
    thread = threading.Thread(target=process, args=(ingestion_id, text, db_path), daemon=True)
    thread.start()
    return {"id": ingestion_id, "event_id": event_id, "status": "queued", "duplicate": False}


def process(ingestion_id: str, text: str, db_path=None) -> None:
    try:
        with connect(db_path) as db:
            event_source_row = db.execute(
                "SELECT service, channel_id, channel_name, channel_username, message_id, message_url, published_at FROM ingestion_events WHERE id = ?",
                (ingestion_id,),
            ).fetchone()
        event_source = dict(event_source_row) if event_source_row else {}
        expanded_text = expand_resource_pages(text)
        kind = detect_source_kind(expanded_text)
        sources = list(iter_sources(expanded_text, kind))
        if not sources:
            with connect(db_path) as db:
                db.execute("UPDATE ingestion_events SET status='ignored', error=?, processed_at=? WHERE id=?", ("未提取到支持的 115 或 ED2K 资源。", _now(), ingestion_id))
            return
        result = import_content(expanded_text, f"webhook:{ingestion_id}.txt", kind=kind, db_path=db_path)
        inserted_keys = set(result.get("inserted_source_keys") or ())
        duplicate_keys = set(result.get("duplicate_source_keys") or ())
        error_keys = set(result.get("error_source_keys") or ())
        media_ids: set[int] = set()
        with connect(db_path) as db:
            for source in sources:
                row = db.execute("SELECT id,media_id FROM source_records WHERE source_key=?", (source.source_key,)).fetchone()
                if source.source_key in inserted_keys:
                    status = "inserted"
                elif source.source_key in error_keys:
                    status = "error"
                elif source.source_key in duplicate_keys:
                    status = "duplicate"
                else:
                    status = "error"
                media_id = int(row["media_id"]) if row else None
                if media_id:
                    media_ids.add(media_id)
                db.execute(
                    "INSERT OR IGNORE INTO ingestion_links(ingestion_id,source_key,provider,source_type,url,status,media_id) VALUES(?,?,?,?,?,?,?)",
                    (ingestion_id, source.source_key, source.provider, source.source_type, source.url, status, media_id),
                )
            status = "imported" if result["inserted"] else ("failed" if result["errors"] else "duplicate")
            db.execute("UPDATE ingestion_events SET status=?, media_ids_json=?, processed_at=? WHERE id=?", (status, json.dumps(sorted(media_ids)), _now(), ingestion_id))
        # Do this after the import transaction is committed. A webhook should
        # acknowledge and retain its resources even if TMDB is unavailable;
        # successful matches are linked automatically by search_media.
        _refresh_channel_avatar(ingestion_id, event_source, db_path)
        _auto_match_media(media_ids, db_path)
    except Exception as exc:
        with connect(db_path) as db:
            db.execute("UPDATE ingestion_events SET status='failed', error=?, processed_at=? WHERE id=?", (str(exc)[:500], _now(), ingestion_id))


def get_event(ingestion_id: str, db_path=None) -> dict[str, Any] | None:
    with connect(db_path) as db:
        event = db.execute("SELECT * FROM ingestion_events WHERE id=?", (ingestion_id,)).fetchone()
        if not event:
            return None
        links = db.execute("SELECT * FROM ingestion_links WHERE ingestion_id=? ORDER BY id", (ingestion_id,)).fetchall()
    result = dict(event)
    result["raw_json"] = json.loads(result["raw_json"]) if result.get("raw_json") else None
    result["media_ids"] = json.loads(result.pop("media_ids_json") or "[]")
    result["links"] = [dict(link) for link in links]
    return result


def reprocess_ignored(limit: int = 100, db_path=None) -> dict[str, int]:
    """Retry ignored ingestion events with the current source recognizer."""
    bounded = min(max(int(limit), 1), 250)
    with connect(db_path) as db:
        rows = db.execute(
            "SELECT id, raw_text FROM ingestion_events WHERE status = 'ignored' ORDER BY received_at DESC LIMIT ?",
            (bounded,),
        ).fetchall()
    for row in rows:
        threading.Thread(target=process, args=(row["id"], row["raw_text"], db_path), daemon=True).start()
    return {"queued": len(rows)}
