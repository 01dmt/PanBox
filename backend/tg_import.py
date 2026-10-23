"""把 Telegram 桌面版导出的频道记录导入 PanBox。

`tg_export` 负责解析导出的 HTML，这里负责把每条消息按 `telegram:<频道ID>:<消息ID>:v1`
构造事件，再复用 `ingestion.receive` 的完整入库链路：幂等去重、👤 人物上下文、
`telegra.ph` 资源页展开、115/123/光鸭 只读校验、频道头像和 TMDB 自动匹配。

导入使用有界线程池驱动**同步模式**的 `receive`，不会为每条消息各开一个线程；
115 的本地请求闸门会把分享校验压到 5 秒一次，所以大批量导入应放后台分批执行。
重复执行是安全的：已入库的消息按 (service, channel_username, message_id) 直接跳过，
`--limit` 统计的是「本次真正处理的消息数」，因此反复带 `--limit N` 就能一批批往前推。
"""

from __future__ import annotations

import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable

from .importers import ED2K_RE, SHARE_URL_RE, detect_source_kind, iter_sources
from .ingestion import IngestionError, receive
from .resource_pages import supported_page_urls
from .schema import connect, init_db, resolve_db_path
from .telegram_avatar import normalize_channel_username
from .tg_export import ExportMessage, iter_export_messages, read_channel_name

PROGRESS_EVERY = 50
DEFAULT_WORKERS = 4


def actionable_button_urls(message: ExportMessage) -> list[str]:
    """机器人按钮里入库能用得上的链接（资源页或 115/123/光鸭/ED2K 直链）。"""
    urls: list[str] = []
    for url in message.button_urls:
        if SHARE_URL_RE.search(url) or ED2K_RE.search(url) or supported_page_urls(url):
            urls.append(url)
    return urls


def message_text(message: ExportMessage) -> str:
    """导入时使用的正文：原文末尾补上按钮里的资源链接，一条一行。

    这些链接原本是消息里的按钮，Telegram 导出不会出现在正文里；不补就只能把
    消息记成 `ignored`，拿不到里面的资源。
    """
    urls = actionable_button_urls(message)
    if not urls:
        return message.text
    return "\n".join([message.text.rstrip(), *urls]) if message.text.strip() else "\n".join(urls)


def build_event_body(
    message: ExportMessage,
    *,
    channel_id: str,
    channel_name: str | None = None,
    channel_username: str | None = None,
) -> dict[str, Any]:
    """按消息接入协议构造一条入库事件体。"""
    return {
        "event_id": f"telegram:{channel_id}:{message.message_id}:v1",
        "source": {
            "service": "telegram",
            "channel_id": str(channel_id),
            "channel_name": channel_name,
            "channel_username": channel_username,
            "message_id": str(message.message_id),
            "message_url": f"https://t.me/{channel_username}/{message.message_id}" if channel_username else None,
            "published_at": message.published_at,
        },
        "message": {"text": message_text(message)},
    }


def resolve_channel_identity(
    *,
    channel_username: str | None = None,
    channel_name: str | None = None,
    channel_id: str | None = None,
    export_path: str | Path | None = None,
    db_path=None,
) -> dict[str, str | None]:
    """补齐频道身份：导出文件只有频道名，ID 和用户名从既有入库记录里复用。

    复用既有 `channel_id` 才能让 `telegram:<频道ID>:<消息ID>:v1` 与 webhook 送来的
    同一条消息共用一个事件，避免同一条消息出现两条入库记录。
    """
    username = normalize_channel_username(channel_username)
    export_header = read_channel_name(export_path) if export_path else None
    resolved_name = (channel_name or export_header or "").strip() or None
    resolved_id = str(channel_id).strip() if channel_id else None
    stored_username: str | None = None

    with connect(db_path) as db:
        row = None
        try:
            if not resolved_id and username:
                row = db.execute(
                    """SELECT channel_id, channel_username FROM ingestion_events
                       WHERE service='telegram' AND channel_id IS NOT NULL AND channel_id != ''
                         AND lower(replace(coalesce(channel_username,''),'@','')) = ?
                       ORDER BY rowid DESC LIMIT 1""",
                    (username.casefold(),),
                ).fetchone()
            if not resolved_id and row is None and resolved_name:
                row = db.execute(
                    """SELECT channel_id, channel_username FROM ingestion_events
                       WHERE service='telegram' AND channel_id IS NOT NULL AND channel_id != ''
                         AND replace(coalesce(channel_name,''),' ','') = ?
                       ORDER BY rowid DESC LIMIT 1""",
                    (resolved_name.replace(" ", ""),),
                ).fetchone()
        except sqlite3.OperationalError:
            # 全新数据库还没有表；只读预演不能因为这一步失败。
            row = None
        if not resolved_id and row is not None and row["channel_id"]:
            resolved_id = str(row["channel_id"])
        if row is not None and row["channel_username"]:
            # 沿用库里已有的大小写，避免同一个频道出现两种写法。
            stored_username = normalize_channel_username(str(row["channel_username"]))

    return {
        "channel_id": resolved_id or username,
        "channel_username": stored_username or username,
        "channel_name": resolved_name,
    }


def existing_message_ids(*, channel_username: str | None, channel_id: str | None, db_path=None) -> set[str]:
    """返回该频道已经入库的消息 ID，用于跳过重复消息。

    `queued` 是还没处理完的状态（上一次导入被 Ctrl-C 打断时会留下这种行），
    这里跳过它们，重新导入时才会把这条消息重新送进入库链路补完。
    """
    clauses = [
        "service='telegram'",
        "message_id IS NOT NULL",
        "message_id != ''",
        "COALESCE(status,'') != 'queued'",
    ]
    params: list[str] = []
    if channel_username:
        clauses.append("lower(replace(coalesce(channel_username,''),'@','')) = ?")
        params.append(channel_username.casefold())
    elif channel_id:
        clauses.append("channel_id = ?")
        params.append(str(channel_id))
    else:
        return set()
    with connect(db_path) as db:
        try:
            rows = db.execute(
                f"SELECT DISTINCT message_id FROM ingestion_events WHERE {' AND '.join(clauses)}",
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            return set()
    return {str(row["message_id"]) for row in rows}


def clear_interrupted_events(*, channel_username: str | None, channel_id: str | None, db_path=None) -> int:
    """清掉上次导入中断留下的 `queued` 半截记录，返回删除条数。

    `receive` 对已存在的事件只会回放当时的状态、不会重新处理，所以必须先删掉这些
    半截记录，对应的消息才能重新进入入库链路补完；删除会级联清掉它们的来源明细。
    """
    clauses = ["service='telegram'", "status='queued'"]
    params: list[str] = []
    if channel_username:
        clauses.append("lower(replace(coalesce(channel_username,''),'@','')) = ?")
        params.append(channel_username.casefold())
    elif channel_id:
        clauses.append("channel_id = ?")
        params.append(str(channel_id))
    else:
        return 0
    with connect(db_path) as db:
        try:
            cursor = db.execute(f"DELETE FROM ingestion_events WHERE {' AND '.join(clauses)}", params)
        except sqlite3.OperationalError:
            return 0
        deleted = int(cursor.rowcount or 0)
    return max(deleted, 0)


def _default_receiver(db_path, *, auto_match: bool = True) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _receive_sync(body: dict[str, Any]) -> dict[str, Any]:
        return receive(body, db_path, background=False, auto_match=auto_match)

    return _receive_sync


def preview_sources(text: str) -> tuple[bool, int]:
    """返回 (是否有直链来源, 需要抓取的资源页数量)，不产生任何网络请求。"""
    try:
        direct = bool(list(iter_sources(text, detect_source_kind(text))))
    except Exception:
        direct = False
    return direct, len(supported_page_urls(text))


def _record_status(statuses: dict[str, int], result: Any) -> None:
    status = str(result.get("status") or "queued") if isinstance(result, dict) else "queued"
    statuses[status] = statuses.get(status, 0) + 1


def _progress_line(*, processed: int, total: int, statuses: dict[str, int], errors: int, elapsed: float) -> str:
    counts = " ".join(f"{key}={value}" for key, value in sorted(statuses.items()))
    eta = ""
    if processed:
        remaining = max(total - processed, 0)
        eta = f" eta≈{remaining * elapsed / processed:.0f}s"
    return f"[{processed}/{total}] {counts} errors={errors} elapsed={elapsed:.1f}s{eta}"


def import_export(
    export_path: str | Path,
    *,
    channel_username: str | None = None,
    channel_name: str | None = None,
    channel_id: str | None = None,
    limit: int | None = None,
    workers: int = DEFAULT_WORKERS,
    delay: float = 0.0,
    dry_run: bool = False,
    auto_match: bool = True,
    receiver: Callable[[dict[str, Any]], Any] | None = None,
    db_path=None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """导入一个 Telegram 导出目录里的全部频道消息。

    ``receiver`` 用于测试注入；生产实现是同步模式的 `ingestion.receive`。
    ``progress`` 每处理一批消息回调一次进度文案。
    ``auto_match=False`` 导入时不做 TMDB 自动匹配，作品留在待匹配状态，
    大批量导入可以事后用 `app.py match` 统一补。
    """
    started = time.monotonic()
    db_path = resolve_db_path(db_path)
    export_dir = Path(export_path).expanduser()
    if not export_dir.exists():
        raise ValueError(f"导出路径不存在：{export_dir}")

    identity = resolve_channel_identity(
        channel_username=channel_username,
        channel_name=channel_name,
        channel_id=channel_id,
        export_path=export_dir,
        db_path=db_path,
    )
    resolved_id = identity["channel_id"]
    if not resolved_id:
        raise ValueError("无法确定频道：请用 --channel 传入频道用户名。")
    resolved_username = identity["channel_username"]
    resolved_name = identity["channel_name"]

    # 上次导入被打断会留下 queued 半截记录：先清掉，这些消息才能重新进入链路补完。
    requeued = 0 if dry_run else clear_interrupted_events(
        channel_username=resolved_username, channel_id=resolved_id, db_path=db_path
    )
    existing = existing_message_ids(channel_username=resolved_username, channel_id=resolved_id, db_path=db_path)
    statuses: dict[str, int] = {}
    error_samples: list[dict[str, Any]] = []
    parsed = empty = skipped_existing = submitted = errors = 0
    direct_source_messages = resource_page_messages = 0

    def take(message: ExportMessage) -> ExportMessage | None:
        """返回需要处理的消息；空消息和已入库消息在这里被跳过。"""
        nonlocal parsed, empty, skipped_existing
        parsed += 1
        if not message_text(message).strip():
            empty += 1
            return None
        if str(message.message_id) in existing:
            skipped_existing += 1
            return None
        return message

    if dry_run:
        for message in iter_export_messages(export_dir):
            candidate = take(message)
            if candidate is None or (limit is not None and submitted >= limit):
                continue
            direct, pages = preview_sources(message_text(candidate))
            direct_source_messages += int(direct)
            resource_page_messages += int(bool(pages))
            submitted += 1
        return {
            "export": str(export_dir),
            "dry_run": True,
            "channel_id": resolved_id,
            "channel_username": resolved_username,
            "channel_name": resolved_name,
            "parsed": parsed,
            "empty": empty,
            "existing": skipped_existing,
            "requeued": requeued,
            "submitted": submitted,
            "direct_source_messages": direct_source_messages,
            "resource_page_messages": resource_page_messages,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }

    init_db(db_path)
    deliver = receiver or _default_receiver(db_path, auto_match=auto_match)
    report = progress if progress is not None else (lambda message: print(message, flush=True))
    bounded_workers = max(1, int(workers))
    window = bounded_workers * 2
    next_report = PROGRESS_EVERY

    def collect(done: set[Future]) -> None:
        nonlocal errors, next_report
        for future in done:
            message = pending.pop(future)
            try:
                _record_status(statuses, future.result())
            except Exception as exc:  # 单条消息失败不能中断整批导入
                errors += 1
                statuses["error"] = statuses.get("error", 0) + 1
                if len(error_samples) < 20:
                    error_samples.append({"message_id": message.message_id, "error": str(exc)[:200]})
        processed = sum(statuses.values())
        if processed >= next_report:
            next_report = processed + PROGRESS_EVERY
            report(
                _progress_line(
                    processed=processed,
                    total=max(submitted, processed),
                    statuses=statuses,
                    errors=errors,
                    elapsed=time.monotonic() - started,
                )
            )

    pending: dict[Future, ExportMessage] = {}
    with ThreadPoolExecutor(max_workers=bounded_workers) as pool:
        for message in iter_export_messages(export_dir):
            candidate = take(message)
            if candidate is None or (limit is not None and submitted >= limit):
                continue
            body = build_event_body(
                candidate,
                channel_id=str(resolved_id),
                channel_name=resolved_name,
                channel_username=resolved_username,
            )
            pending[pool.submit(deliver, body)] = candidate
            submitted += 1
            if delay:
                time.sleep(delay)
            if len(pending) >= window:
                done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
                collect(done)
        while pending:
            done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            collect(done)

    elapsed = time.monotonic() - started
    report(
        _progress_line(
            processed=sum(statuses.values()),
            total=max(submitted, sum(statuses.values())),
            statuses=statuses,
            errors=errors,
            elapsed=elapsed,
        )
    )
    return {
        "export": str(export_dir),
        "dry_run": False,
        "channel_id": resolved_id,
        "channel_username": resolved_username,
        "channel_name": resolved_name,
        "parsed": parsed,
        "empty": empty,
        "existing": skipped_existing,
        "requeued": requeued,
        "submitted": submitted,
        "statuses": statuses,
        "errors": errors,
        "error_samples": error_samples,
        "elapsed_seconds": round(elapsed, 1),
    }


def describe_result(result: dict[str, Any]) -> str:
    """把导入结果整理成一段可读文案。"""
    username = result.get("channel_username") or "-"
    lines = [
        f"导出目录：{result.get('export')}",
        f"频道：{result.get('channel_name') or '-'}（@{username}，ID {result.get('channel_id')}）",
        f"解析消息 {result.get('parsed', 0)} 条：无正文 {result.get('empty', 0)}，"
        f"已入库跳过 {result.get('existing', 0)}，本次处理 {result.get('submitted', 0)}",
    ]
    requeued = int(result.get("requeued") or 0)
    if requeued:
        lines.append(f"补处理上次导入中断的记录 {requeued} 条")
    if result.get("dry_run"):
        lines.append(
            f"预估网络量：含直链来源的消息 {result.get('direct_source_messages', 0)} 条，"
            f"需抓取资源页的消息 {result.get('resource_page_messages', 0)} 条"
        )
    else:
        statuses = " ".join(f"{key}={value}" for key, value in sorted((result.get("statuses") or {}).items()))
        lines.append(f"处理结果：{statuses or '无'}；失败 {result.get('errors', 0)}")
        for sample in result.get("error_samples") or []:
            lines.append(f"  - message {sample.get('message_id')}：{sample.get('error')}")
    lines.append(f"耗时 {result.get('elapsed_seconds')} 秒")
    return "\n".join(lines)
