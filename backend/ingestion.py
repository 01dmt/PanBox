from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from .importers import detect_source_kind, iter_sources
from .repository import import_content, save_candidates
from .resource_metadata import clean_person_name
from .resource_pages import expand_resource_pages
from .schema import connect, init_db
from .share115 import Share115Client, Share115Error
from .share123 import Share123Client, Share123Error
from .share_guangya import ShareGuangyaClient, ShareGuangyaError
from .tmdb import TmdbClient, search_media
from .telegram_avatar import cache_channel_avatar, fetch_public_channel_avatar, normalize_channel_username


class IngestionError(ValueError):
    pass


logger = logging.getLogger(__name__)


_INIT_LOCK = threading.Lock()
_WORKER_LOCK = threading.Lock()
_WORKERS: set[str] = set()
_PROCESSING_LOCK = threading.Lock()
_PROCESSING_EVENTS: set[str] = set()
_PERSON_RESOURCE_CHANNEL_USERNAME = "lsp115"
_PERSON_RESOURCE_CHANNEL_NAME = "115网盘资源分享频道"
_PERSON_LABEL_MARKERS = "🎂🏠📚🔖🤖🔍✈️📎🍿🍄"
# Retryable validation failures back off to one hour; after ~7 days of real
# attempts the link stops auto-retrying and stays inspectable as `error`.
VALIDATION_MAX_ATTEMPTS = 168


def _is_person_resource_channel(source: dict[str, Any]) -> bool:
    """Return whether an event came from the channel with person entries.

    This rule is intentionally scoped to Lsp115.  Other channels may use the
    same emoji for an unrelated heading and must keep the normal parser.
    """
    username = str(source.get("channel_username") or "").strip().lstrip("@").casefold()
    if username == _PERSON_RESOURCE_CHANNEL_USERNAME:
        return True
    channel_name = re.sub(r"\s+", "", str(source.get("channel_name") or ""))
    if channel_name == _PERSON_RESOURCE_CHANNEL_NAME:
        return True
    try:
        path_parts = [part for part in urlsplit(str(source.get("message_url") or "")).path.split("/") if part]
    except ValueError:
        path_parts = []
    return bool(path_parts and path_parts[0].lstrip("@").casefold() == _PERSON_RESOURCE_CHANNEL_USERNAME)


def _lsp115_person_context(text: str, source: dict[str, Any]) -> str | None:
    """Extract a compact person heading for Lsp115's inline profile format."""
    if not _is_person_resource_channel(source):
        return None
    match = re.search(r"(?m)^\s*👤\s*(.+?)\s*$", str(text or ""))
    if not match:
        return None

    profile = match.group(1).strip()
    name = re.split(rf"\s*(?=[{_PERSON_LABEL_MARKERS}])", profile, maxsplit=1)[0].strip()
    # 「👤 浅野心，🎂 生日：…」里姓名后的逗号要洗掉，否则同一个人会被拆成多个条目。
    name = clean_person_name(name)
    if not name:
        return None

    # Keep the useful profile fields in the context so the existing importer
    # can persist them as resource metadata, while leaving airport/bot links
    # out of the searchable title.
    context_lines = [f"👤 {name}"]
    # The channel uses both a compact one-line profile and a multi-line
    # profile. Search the complete message so the latter keeps its metadata.
    field_source = str(text or "")
    for label, icon in (("生日", "🎂"), ("出生地", "🏠"), ("代表作", "📚")):
        field = re.search(
            rf"(?:{re.escape(icon)}\s*)?{label}\s*[：:]\s*(.*?)(?=\s*(?:[{_PERSON_LABEL_MARKERS}])|$)",
            field_source,
            re.DOTALL,
        )
        if field and field.group(1).strip():
            context_lines.append(f"{icon} {label}：{field.group(1).strip()}")
    return "\n".join(context_lines)


def _auto_match_media(media_ids: set[int], db_path=None) -> dict[int, str]:
    """Run the normal TMDB matcher for media created by an incoming message.

    Matching is deliberately best-effort: ingestion has already succeeded if
    the source was stored, and a temporary TMDB/network error must not turn a
    valid webhook into a failed delivery. ``search_media`` still enforces the
    confidence and ambiguity rules, so only high-confidence candidates are
    linked automatically; everything else stays in review state, and matcher
    failures are recorded as errors instead of silently remaining pending.
    """
    if not media_ids:
        return {}
    client = TmdbClient()
    if not client.configured:
        logger.warning("TMDB 未配置，入库作品未自动匹配 media_ids=%s", sorted(media_ids))
        return {}
    results: dict[int, str] = {}
    with connect(db_path) as db:
        kinds = {
            int(row["id"]): (str(row["resource_kind"]), int(row["is_bundle"] or 0))
            for row in db.execute(
                "SELECT id, resource_kind, is_bundle FROM media_items WHERE id IN (%s)" % ",".join("?" * len(media_ids)),
                sorted(media_ids),
            )
        }
    for media_id in sorted(media_ids):
        # 人物条目不是 TMDB 影视作品：不参与匹配，也不记录“匹配错误”。
        if (kinds.get(media_id) or ("media", 0))[0] == "person":
            results[media_id] = "skipped_person"
            continue
        # 整个系列打成一个分享时拆不出成员，按单片匹配只会错绑其中一部。
        if (kinds.get(media_id) or ("media", 0))[1]:
            results[media_id] = "skipped_bundle"
            continue
        try:
            result = search_media(
                media_id,
                auto_link=True,
                # Recognition must not probe or remove 115 sources. Link
                # validity belongs to the separate low-frequency audit.
                check_share=False,
                db_path=db_path,
                client=client,
            )
            results[media_id] = str(result.get("status") or "unknown")
        except Exception as exc:
            # Keep delivery processing resilient to a matcher failure, but
            # never fail silently: record and log the reason so the error is
            # visible and the media can be retried instead of lingering as a
            # pending item without a poster.
            logger.exception("TMDB 自动匹配失败 media_id=%s", media_id)
            try:
                save_candidates(media_id, [], "error", str(exc), db_path)
            except Exception:
                logger.exception("记录匹配错误失败 media_id=%s", media_id)
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


def receive(body: dict[str, Any], db_path=None, *, background: bool = True, auto_match: bool = True) -> dict[str, Any]:
    """接收一条消息；``background=False`` 时在调用线程里同步处理完再返回。

    Webhook 用默认的后台线程模式立刻返回 202；批量导入（`import-telegram`）用
    同步模式，这样调用方能用自己的线程池限制并发，而不是每条消息开一个线程。

    ``auto_match=False`` 跳过 TMDB 自动匹配，作品保持待匹配，导入结束后可以批量
    跑 `app.py match`，避免大批量导入时每条消息都打一次 TMDB。
    """
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
                return {"accepted": True, "id": existing["id"], "event_id": event_id, "status": existing["status"], "duplicate": True}
            raise sqlite3.IntegrityError("ingestion event was ignored but could not be read")
    if background:
        thread = threading.Thread(target=process, args=(ingestion_id, text, db_path), kwargs={"auto_match": auto_match}, daemon=True)
        thread.start()
        return {"accepted": True, "id": ingestion_id, "event_id": event_id, "status": "queued", "duplicate": False}
    process(ingestion_id, text, db_path, auto_match=auto_match)
    with connect(db_path) as db:
        row = db.execute("SELECT status FROM ingestion_events WHERE id=?", (ingestion_id,)).fetchone()
    return {
        "accepted": True,
        "id": ingestion_id,
        "event_id": event_id,
        "status": str(row["status"]) if row else "queued",
        "duplicate": False,
    }


def _validation_error(exc: Exception) -> tuple[str, bool]:
    """Return a user-safe reason and whether the failure is terminal."""
    if isinstance(exc, Share115Error):
        terminal = (not exc.risk_control) and (
            bool(exc.terminal) or exc.errno in {4100008, 4100009, 4100010, 4100012} or "HTTP 404" in str(exc)
        )
        return str(exc)[:300], terminal
    if isinstance(exc, Share123Error):
        return str(exc)[:300], exc.terminal
    if isinstance(exc, ShareGuangyaError):
        return str(exc)[:300], exc.terminal
    return str(exc)[:300], False


def _validate_source(source) -> tuple[str, str | None, bool]:
    """Return state, reason and whether the attempt counts toward the retry cap."""
    if source.source_type == "ed2k":
        return "valid", None, False
    try:
        if source.provider == "115":
            Share115Client().probe_share(source.url)
        elif source.provider == "123":
            Share123Client().probe_share(source.url)
        elif source.provider == "guangya":
            ShareGuangyaClient().probe_share(source.url)
        return "valid", None, True
    except Exception as exc:
        reason, terminal = _validation_error(exc)
        # A local gate refusal never reached the network and proves nothing
        # about the link, so it must not consume the retry budget.
        return ("invalid" if terminal else "pending"), reason, not getattr(exc, "gate_blocked", False)


def _set_event_status(ingestion_id: str, status: str, media_ids: set[int], error: str | None, db_path=None) -> None:
    with connect(db_path) as db:
        db.execute(
            "UPDATE ingestion_events SET status=?, media_ids_json=?, error=?, processed_at=? WHERE id=?",
            (status, json.dumps(sorted(media_ids)), error, _now(), ingestion_id),
        )


def _validate_and_import(ingestion_id: str, text: str, sources, db_path=None, *, auto_match: bool = True) -> None:
    """Run one event's validation unless another thread already owns it.

    The retry worker can see an event's freshly created ``pending_validation``
    links while the first probe is still running. Without this guard the second
    run imports the same sources again and downgrades them to ``duplicate``.
    """
    with _PROCESSING_LOCK:
        busy = ingestion_id in _PROCESSING_EVENTS
        if not busy:
            _PROCESSING_EVENTS.add(ingestion_id)
    if busy:
        return
    try:
        _run_validation_and_import(ingestion_id, text, sources, db_path, auto_match=auto_match)
    finally:
        with _PROCESSING_LOCK:
            _PROCESSING_EVENTS.discard(ingestion_id)


def _run_validation_and_import(ingestion_id: str, text: str, sources, db_path=None, *, auto_match: bool = True) -> None:
    """Validate extracted cloud shares, then import only the confirmed ones."""
    with connect(db_path) as db:
        for source in sources:
            db.execute(
                """
                INSERT INTO ingestion_links(ingestion_id,source_key,provider,source_type,url,status,media_id)
                VALUES(?,?,?,?,?,'pending_validation',NULL)
                ON CONFLICT(ingestion_id, source_key) DO UPDATE SET
                    provider=excluded.provider, source_type=excluded.source_type, url=excluded.url
                """,
                (ingestion_id, source.source_key, source.provider, source.source_type, source.url),
            )

    valid_sources = []
    pending = []
    invalid = []
    for source in sources:
        state, reason, count_attempt = _validate_source(source)
        with connect(db_path) as db:
            if state == "valid":
                valid_sources.append(source)
                db.execute(
                    "UPDATE ingestion_links SET status='validated', validation_error=NULL, next_retry_at=NULL WHERE ingestion_id=? AND source_key=?",
                    (ingestion_id, source.source_key),
                )
            elif state == "invalid":
                invalid.append((source, reason or "链接无效或访问密码错误。"))
                db.execute(
                    "UPDATE ingestion_links SET status='invalid', validation_error=?, error=? WHERE ingestion_id=? AND source_key=?",
                    (reason, reason, ingestion_id, source.source_key),
                )
            else:
                row = db.execute(
                    "SELECT validation_attempts FROM ingestion_links WHERE ingestion_id=? AND source_key=?",
                    (ingestion_id, source.source_key),
                ).fetchone()
                attempts = int(row["validation_attempts"] or 0) if row else 0
                if count_attempt:
                    attempts += 1
                if count_attempt and attempts >= VALIDATION_MAX_ATTEMPTS:
                    # Unrecognised transient failures must not retry forever;
                    # keep the record so a manual re-check can still revive it.
                    note = f"{reason}（连续 {attempts} 次校验未通过，已停止自动重试，请人工检查）"
                    db.execute(
                        "UPDATE ingestion_links SET status='error', validation_attempts=?, validation_error=?, error=? WHERE ingestion_id=? AND source_key=?",
                        (attempts, note, note, ingestion_id, source.source_key),
                    )
                    continue
                pending.append((source, reason or "暂时无法确认链接状态。"))
                if not count_attempt:
                    # Access is locally restricted; back off slowly without
                    # consuming the retry budget.
                    delay = 3600
                else:
                    delay = min(3600, 30 * (2 ** min(max(attempts - 1, 0), 6)))
                retry_at = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + delay, timezone.utc).isoformat(timespec="seconds")
                db.execute(
                    "UPDATE ingestion_links SET status='pending_validation', validation_attempts=?, next_retry_at=?, validation_error=?, error=? WHERE ingestion_id=? AND source_key=?",
                    (attempts, retry_at, reason, reason, ingestion_id, source.source_key),
                )

    media_ids: set[int] = set()
    if valid_sources:
        result = import_content(
            text,
            f"webhook:{ingestion_id}.txt",
            kind="mixed",
            db_path=db_path,
            parsed_sources=valid_sources,
        )
        inserted_keys = set(result.get("inserted_source_keys") or ())
        duplicate_keys = set(result.get("duplicate_source_keys") or ())
        error_keys = set(result.get("error_source_keys") or ())
        with connect(db_path) as db:
            for source in valid_sources:
                row = db.execute(
                    "SELECT id, media_id, import_id FROM source_records WHERE source_key=?",
                    (source.source_key,),
                ).fetchone()
                media_id = int(row["media_id"]) if row else None
                if row and source.source_type != "ed2k":
                    db.execute("UPDATE source_records SET availability_status='available', last_checked_at=?, last_check_error=NULL WHERE id=?", (_now(), row["id"]))
                if media_id:
                    media_ids.add(media_id)
                status = "inserted" if source.source_key in inserted_keys else "duplicate" if source.source_key in duplicate_keys else "error"
                if status == "duplicate" and row and db.execute(
                    "SELECT 1 FROM imports WHERE id=? AND source_name=?",
                    (row["import_id"], f"webhook:{ingestion_id}.txt"),
                ).fetchone():
                    # This event stores the source itself, so a re-run of the
                    # same event must not downgrade its own successful import.
                    status = "inserted"
                db.execute(
                    "UPDATE ingestion_links SET status=?, media_id=?, error=NULL, validation_error=NULL WHERE ingestion_id=? AND source_key=?",
                    (status, media_id, ingestion_id, source.source_key),
                )

    # A retry may only contain the links that were pending; retain media IDs
    # from sources that were already validated by an earlier attempt.
    with connect(db_path) as db:
        for row in db.execute("SELECT media_id FROM ingestion_links WHERE ingestion_id=? AND media_id IS NOT NULL", (ingestion_id,)):
            media_ids.add(int(row["media_id"]))

    if pending:
        _set_event_status(ingestion_id, "pending_validation", media_ids, pending[0][1], db_path)
    elif (valid_sources or media_ids) and not invalid:
        with connect(db_path) as db:
            counts = db.execute("SELECT SUM(status='inserted') AS inserted, SUM(status='error') AS errors FROM ingestion_links WHERE ingestion_id=?", (ingestion_id,)).fetchone()
        _set_event_status(ingestion_id, "failed" if counts["errors"] else ("imported" if counts["inserted"] else "duplicate"), media_ids, None, db_path)
    elif valid_sources:
        _set_event_status(ingestion_id, "imported", media_ids, invalid[0][1] if invalid else None, db_path)
    else:
        _set_event_status(ingestion_id, "failed", media_ids, invalid[0][1] if invalid else "链接暂未通过校验。", db_path)

    if media_ids and auto_match:
        _auto_match_media(media_ids, db_path)


def process(ingestion_id: str, text: str, db_path=None, *, auto_match: bool = True) -> None:
    try:
        with connect(db_path) as db:
            event_source_row = db.execute(
                "SELECT service, channel_id, channel_name, channel_username, message_id, message_url, published_at FROM ingestion_events WHERE id = ?",
                (ingestion_id,),
            ).fetchone()
        event_source = dict(event_source_row) if event_source_row else {}
        person_context = _lsp115_person_context(text, event_source)
        expanded_text = expand_resource_pages(text, inherited_heading=person_context)
        kind = detect_source_kind(expanded_text)
        sources = list(iter_sources(expanded_text, kind, person_context=person_context))
        if not sources:
            with connect(db_path) as db:
                db.execute("UPDATE ingestion_events SET status='ignored', error=?, processed_at=? WHERE id=?", ("未提取到支持的 115、123、光鸭或 ED2K 资源。", _now(), ingestion_id))
            return
        _validate_and_import(ingestion_id, expanded_text, sources, db_path, auto_match=auto_match)
        # Do this after the import transaction is committed. A webhook should
        # acknowledge and retain its resources even if TMDB is unavailable;
        # successful matches are linked automatically by search_media.
        _refresh_channel_avatar(ingestion_id, event_source, db_path)
    except Exception as exc:
        with connect(db_path) as db:
            db.execute("UPDATE ingestion_events SET status='failed', error=?, processed_at=? WHERE id=?", (str(exc)[:500], _now(), ingestion_id))


def retry_pending_validations(limit: int = 100, db_path=None) -> dict[str, int]:
    """Resume due validation links; state is durable across process restarts."""
    bounded = min(max(int(limit), 1), 250)
    with connect(db_path) as db:
        rows = db.execute(
            """
            SELECT DISTINCT e.id, e.raw_text, e.channel_name, e.channel_username, e.message_url
            FROM ingestion_events e JOIN ingestion_links l ON l.ingestion_id=e.id
            WHERE l.status='pending_validation'
              AND (l.next_retry_at IS NULL OR l.next_retry_at <= ?)
            ORDER BY e.received_at LIMIT ?
            """,
            (_now(), bounded),
        ).fetchall()
    queued = 0
    for row in rows:
        event_source = dict(row)
        person_context = _lsp115_person_context(row["raw_text"], event_source)
        expanded = expand_resource_pages(row["raw_text"], inherited_heading=person_context)
        sources = list(iter_sources(expanded, detect_source_kind(expanded), person_context=person_context))
        with connect(db_path) as db:
            pending_keys = {
                item["source_key"] for item in db.execute(
                    "SELECT source_key FROM ingestion_links WHERE ingestion_id=? AND status='pending_validation'",
                    (row["id"],),
                ).fetchall()
            }
        sources = [source for source in sources if source.source_key in pending_keys]
        if sources:
            threading.Thread(target=_validate_and_import, args=(row["id"], expanded, sources, db_path), daemon=True).start()
            queued += 1
    return {"queued": queued}


def start_validation_worker(db_path=None, interval: float = 30.0) -> None:
    """Start one lightweight retry loop per database, if the server is running."""
    key = str(db_path or "default")
    with _WORKER_LOCK:
        if key in _WORKERS:
            return
        _WORKERS.add(key)

    def worker() -> None:
        while True:
            try:
                retry_pending_validations(100, db_path)
            except Exception:
                pass
            threading.Event().wait(max(5.0, interval))

    threading.Thread(target=worker, name="panbox-validation-worker", daemon=True).start()


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
    """Retry ignored or orphaned ingestion events with the current recognizer."""
    bounded = min(max(int(limit), 1), 250)
    with connect(db_path) as db:
        rows = db.execute(
            """
            SELECT id, raw_text
            FROM ingestion_events AS e
            WHERE e.status = 'ignored'
               OR EXISTS (
                   SELECT 1 FROM ingestion_links AS l
                   WHERE l.ingestion_id = e.id AND l.media_id IS NULL
               )
            ORDER BY received_at DESC
            LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    for row in rows:
        threading.Thread(target=process, args=(row["id"], row["raw_text"], db_path), daemon=True).start()
    return {"queued": len(rows)}


RETRYABLE_LINK_STATUSES = ("invalid", "error", "pending_validation")


def retry_ingestion_event(ingestion_id: str, db_path=None) -> dict[str, Any]:
    """Re-validate and import one event's unsuccessful links.

    Links already imported keep their state and are never probed again.
    ``invalid``/``error`` links get a fresh validation budget because an
    explicit retry is a human decision to try again.
    """
    with connect(db_path) as db:
        row = db.execute(
            "SELECT raw_text, channel_name, channel_username, message_url FROM ingestion_events WHERE id=?",
            (ingestion_id,),
        ).fetchone()
        if not row:
            raise ValueError("入库记录不存在。")
        placeholders = ",".join("?" * len(RETRYABLE_LINK_STATUSES))
        target_keys = {
            item["source_key"] for item in db.execute(
                f"SELECT source_key FROM ingestion_links WHERE ingestion_id=? AND status IN ({placeholders})",
                (ingestion_id, *RETRYABLE_LINK_STATUSES),
            ).fetchall()
        }
        if target_keys:
            db.execute(
                f"UPDATE ingestion_links SET status='pending_validation', validation_attempts=0,"
                f" next_retry_at=NULL, validation_error=NULL, error=NULL"
                f" WHERE ingestion_id=? AND status IN ({placeholders})",
                (ingestion_id, *RETRYABLE_LINK_STATUSES),
            )
    if not target_keys:
        return {"queued": 0}
    person_context = _lsp115_person_context(row["raw_text"], dict(row))
    expanded = expand_resource_pages(row["raw_text"], inherited_heading=person_context)
    sources = [
        source for source in iter_sources(expanded, detect_source_kind(expanded), person_context=person_context)
        if source.source_key in target_keys
    ]
    if sources:
        threading.Thread(target=_validate_and_import, args=(ingestion_id, expanded, sources, db_path), daemon=True).start()
    return {"queued": len(sources)}


def retry_failed_ingestions(limit: int = 100, db_path=None) -> dict[str, int]:
    """Bulk re-validate failed events, retrying only their unsuccessful links."""
    bounded = min(max(int(limit), 1), 250)
    with connect(db_path) as db:
        rows = db.execute(
            "SELECT id FROM ingestion_events WHERE status='failed' ORDER BY received_at DESC LIMIT ?",
            (bounded,),
        ).fetchall()
    events = 0
    links = 0
    for row in rows:
        result = retry_ingestion_event(row["id"], db_path)
        if result["queued"]:
            events += 1
            links += result["queued"]
    return {"queued": events, "links": links}
