from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .repository import remove_cancelled_source
from .schema import connect, resolve_db_path
from .share115 import INVALID_SHARE_ERRNOS, UNAVAILABLE_ERRNOS, Share115Client, Share115Error, parse_share_url, utc_now


def audit_path(db_path: str | Path | None = None) -> Path:
    db = resolve_db_path(db_path)
    return db.with_name(f"{db.stem}-share-audit.json")


def write_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def audit_status(db_path: str | Path | None = None) -> dict[str, Any]:
    path = audit_path(db_path)
    if not path.exists():
        return {"status": "idle"}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        public = {key: value for key, value in state.items() if key not in {"queue", "db_path", "backup"}}
        public["pause_requested"] = path.with_suffix(".pause").exists()
        if public.get("status") in {"running", "cooldown"}:
            try:
                os.kill(int(public.get("pid", 0)), 0)
            except (ProcessLookupError, ValueError, TypeError):
                public["status"] = "interrupted"
        return public
    except (OSError, ValueError, TypeError):
        return {"status": "error", "reason": "Audit state is unreadable; no scan was started."}


def pause_audit(db_path: str | Path | None = None) -> None:
    audit_path(db_path).with_suffix(".pause").touch()


def audit_share_links(
    db_path: str | Path | None = None, *, resume: bool = False,
    client: Share115Client | None = None, batch_size: int = 100, cooldown: float = 120,
) -> dict[str, Any]:
    db_path = resolve_db_path(db_path)
    path = audit_path(db_path)
    pause_path = path.with_suffix(".pause")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("A share audit is already running.")

        if resume:
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("db_path") != str(db_path):
                raise ValueError("Audit database does not match.")
            if not state.get("queue"):
                return audit_status(db_path)
        else:
            if path.exists():
                previous = json.loads(path.read_text(encoding="utf-8"))
                if previous.get("queue"):
                    raise ValueError("An unfinished audit exists; resume it instead of starting over.")
            groups: dict[tuple[str, str], list[int]] = {}
            with connect(db_path) as db:
                # Cover confirmed and pending media, while checking each shared URL identity once.
                rows = db.execute("SELECT id, url FROM source_records WHERE source_type='115' ORDER BY id").fetchall()
                for row in rows:
                    try:
                        key = parse_share_url(row["url"])
                    except Share115Error:
                        key = ("invalid", str(row["id"]))
                    groups.setdefault(key, []).append(row["id"])
                backup = db_path.with_name(f"{db_path.stem}-before-share-audit-{time.time_ns()}.db")
                with sqlite3.connect(backup) as target:
                    db.backup(target)
            state = {
                "db_path": str(db_path), "backup": str(backup), "started_at": utc_now(),
                "total": len(groups), "source_total": len(rows), "processed": 0,
                "available": 0, "protected": 0, "removed": 0, "media_removed": 0,
                "skipped": 0, "queue": list(groups.values()), "requests": 0,
                "interval_seconds": 5, "batch_size": batch_size, "cooldown_seconds": cooldown,
            }
        pause_path.unlink(missing_ok=True)
        state.update(status="running", reason=None, last_error_errno=None, pid=os.getpid(), updated_at=utc_now())
        write_state(path, state)
        share_client = client or Share115Client()
        try:
            while state["queue"]:
                if pause_path.exists():
                    state.update(status="paused", reason="Paused by user.")
                    break
                ids = state["queue"][0]
                with connect(db_path) as db:
                    rows = db.execute(
                        f"SELECT id, url, metadata_json FROM source_records WHERE source_type='115' AND id IN ({','.join('?' for _ in ids)}) ORDER BY id",
                        ids,
                    ).fetchall()
                if not rows:
                    state["skipped"] += 1
                else:
                    try:
                        identity = parse_share_url(rows[0]["url"])
                        # A changed URL is checked independently, never deleted on another URL's response.
                        related = [r for r in rows if parse_share_url(r["url"]) == identity]
                    except Share115Error:
                        related = []
                    if not related:
                        state["skipped"] += 1
                    else:
                        changed = [r["id"] for r in rows if r not in related]
                        if changed:
                            state["queue"].append(changed)
                            state["total"] += 1
                        state["requests"] += 1
                        try:
                            share_client.probe_share(rows[0]["url"])
                            state["available"] += 1
                        except Share115Error as exc:
                            if exc.risk_control or exc.errno not in {*INVALID_SHARE_ERRNOS, *UNAVAILABLE_ERRNOS}:
                                state.update(
                                    status="paused_risk" if exc.risk_control else "paused_error",
                                    reason="Access restriction or unexpected response; no automatic retry." if exc.risk_control else "Request failed; link retained and audit stopped.",
                                    last_error_errno=exc.errno,
                                )
                                break
                            if exc.errno in INVALID_SHARE_ERRNOS:
                                for row in related:
                                    outcome = remove_cancelled_source(row["id"], row["url"], row["metadata_json"], db_path)
                                    state["removed"] += outcome["removed"]
                                    state["media_removed"] += outcome["media_removed"]
                            else:
                                state["protected"] += 1
                state["queue"].pop(0)
                state["processed"] += 1
                state["updated_at"] = utc_now()
                write_state(path, state)
                if state["queue"] and batch_size > 0 and state["processed"] % batch_size == 0:
                    state.update(status="cooldown", next_batch_at=time.time() + cooldown)
                    write_state(path, state)
                    while time.time() < state["next_batch_at"] and not pause_path.exists():
                        time.sleep(min(1, max(0, state["next_batch_at"] - time.time())))
                    state.update(status="running", next_batch_at=None)
            else:
                state.update(status="complete", completed_at=utc_now())
        except KeyboardInterrupt:
            state.update(status="paused", reason="Interrupted; remaining links can be resumed.")
        except Exception:
            state.update(status="error", reason="Audit stopped on an internal error; remaining links retained.")
            raise
        finally:
            state["updated_at"] = utc_now()
            write_state(path, state)
        return audit_status(db_path)
