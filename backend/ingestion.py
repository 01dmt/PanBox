from __future__ import annotations

import json
import secrets
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from .importers import detect_source_kind, iter_sources
from .repository import import_content
from .schema import connect, init_db


class IngestionError(ValueError):
    pass


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
    return {key: str(source[key]) if source.get(key) is not None else None for key in ("service", "channel_id", "channel_name", "message_id", "published_at")}


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
    init_db(db_path)
    with connect(db_path) as db:
        existing = db.execute("SELECT id,status FROM ingestion_events WHERE service=? AND event_id=?", (service, event_id)).fetchone()
        if existing:
            return {"id": existing["id"], "event_id": event_id, "status": existing["status"], "duplicate": True}
        ingestion_id = _event_id()
        db.execute(
            """INSERT INTO ingestion_events
            (id,event_id,service,channel_id,channel_name,message_id,published_at,raw_text,raw_json,status,received_at)
            VALUES(?,?,?,?,?,?,?,?,?,'queued',?)""",
            (ingestion_id, event_id, service, source["channel_id"], source["channel_name"], source["message_id"], source["published_at"], text, json.dumps(body, ensure_ascii=False), _now()),
        )
    thread = threading.Thread(target=process, args=(ingestion_id, text, db_path), daemon=True)
    thread.start()
    return {"id": ingestion_id, "event_id": event_id, "status": "queued", "duplicate": False}


def process(ingestion_id: str, text: str, db_path=None) -> None:
    try:
        kind = detect_source_kind(text)
        sources = list(iter_sources(text, kind))
        if not sources:
            with connect(db_path) as db:
                db.execute("UPDATE ingestion_events SET status='ignored', error=?, processed_at=? WHERE id=?", ("未提取到支持的 115 或 ED2K 资源。", _now(), ingestion_id))
            return
        result = import_content(text, f"webhook:{ingestion_id}.txt", kind=kind, db_path=db_path)
        media_ids: set[int] = set()
        with connect(db_path) as db:
            for source in sources:
                row = db.execute("SELECT id,media_id FROM source_records WHERE source_key=?", (source.source_key,)).fetchone()
                status = "inserted" if row else "duplicate"
                media_id = int(row["media_id"]) if row else None
                if media_id:
                    media_ids.add(media_id)
                db.execute(
                    "INSERT OR IGNORE INTO ingestion_links(ingestion_id,source_key,provider,source_type,url,status,media_id) VALUES(?,?,?,?,?,?,?)",
                    (ingestion_id, source.source_key, source.provider, source.source_type, source.url, status, media_id),
                )
            status = "imported" if result["inserted"] else "duplicate"
            db.execute("UPDATE ingestion_events SET status=?, media_ids_json=?, processed_at=? WHERE id=?", (status, json.dumps(sorted(media_ids)), _now(), ingestion_id))
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
