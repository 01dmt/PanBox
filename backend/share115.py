from __future__ import annotations

import json
import fcntl
import re
import time
from collections import Counter, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .importers import (
    normalize_title, parse_release_title, parse_release_aliases, is_search_title, parse_episode_span,
    SEASON_EPISODE_RE, SEASON_RE,
)
from .schema import ROOT_DIR, connect
from .repository import remove_cancelled_source


SNAPSHOT_API = "https://webapi.115.com/share/snap"
SNAPSHOT_VERSION = 2
SEASON_MARKER_RE = re.compile(r"(?i)(?:^|[ /._\-])season[ ._-]*\d{1,2}(?=$|[ /._\-])|第[一二三四五六七八九十\d]+[季集]")
VIDEO_FILE_RE = re.compile(r"(?i)\.(mkv|mp4|m4v|mov|avi|wmv|webm|ts|m2ts|rmvb?)$")
EXTRA_RE = re.compile(
    r"(?i)(?:^|[ /._()\[\]-])(?:sample|trailers?|teaser|extras|featurettes?|"
    r"预告(?:片)?|花絮|采访|彩蛋|特典|片头|片尾|陪看预告)(?=$|[ /._()\[\]-])"
)
TECHNICAL_TITLE_RE = re.compile(
    r"(?i)^(?:\d{3,4}p|s\d{1,2}(?:e\d{1,3})?|web[._-]?dl|webrip|bluray|bdrip|remux)$"
)
CANCELLED_ERRNO = 4100010
INVALID_LINK_ERRNO = 4100009
INVALID_SHARE_ERRNOS = {CANCELLED_ERRNO, INVALID_LINK_ERRNO}
UNAVAILABLE_ERRNOS = {4100008, 4100012}


class Share115Error(RuntimeError):
    def __init__(self, message: str, errno: int | None = None, *, risk_control: bool = False):
        super().__init__(message)
        self.errno = errno
        self.risk_control = risk_control


class ShareRequestGate:
    """Serialize requests across the web server and audit worker; fail closed on risk signals."""
    def __init__(self, path: Path | None = None, interval: float = 5.0):
        self.path = path or ROOT_DIR / "data" / "share115-request-gate.json"
        self.interval = max(5.0, interval)

    @contextmanager
    def request(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            file.seek(0)
            raw = file.read()
            try:
                state = json.loads(raw) if raw else {}
                if not isinstance(state, dict):
                    raise ValueError("invalid gate state")
                next_at = float(state.get("next_at", 0))
            except (ValueError, TypeError):
                raise Share115Error("115 请求保护状态异常，已停止访问，请检查本地状态文件。", risk_control=True)
            if state.get("paused"):
                raise Share115Error("115 请求已因访问限制暂停，请先人工确认恢复，不会自动重试。", risk_control=True)
            delay = next_at - time.time()
            if delay > 0:
                time.sleep(delay)
            try:
                yield
            except Share115Error as exc:
                if exc.risk_control:
                    state.update(paused=True, reason="access_restriction", paused_at=utc_now(), last_error_errno=exc.errno)
                raise
            finally:
                state.update(next_at=time.time() + self.interval, updated_at=utc_now())
                file.seek(0)
                file.truncate()
                json.dump(state, file)
                file.flush()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_cancelled_snapshot(snapshot: Any) -> bool:
    """Recognize terminal cancellation or invalid-link responses, never generic access failures."""
    return bool(
        isinstance(snapshot, dict)
        and isinstance(snapshot.get("status"), str)
        and snapshot.get("status") in {"unavailable", "error", "cancelled", "expired"}
        and str(snapshot.get("errno")) in {str(code) for code in INVALID_SHARE_ERRNOS}
    )


def cleanup_cancelled_shares(
    db_path: str | Path | None = None, *, media_id: int | None = None,
) -> dict[str, int]:
    query = "SELECT id, url, metadata_json FROM source_records WHERE source_type = '115'"
    params = []
    if media_id is not None:
        query += " AND media_id = ?"
        params.append(media_id)
    with connect(db_path) as connection:
        rows = connection.execute(query, params).fetchall()
    result = {"removed": 0, "media_removed": 0}
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(metadata, dict) or not is_cancelled_snapshot(metadata.get("share_snapshot")):
            continue
        outcome = remove_cancelled_source(row["id"], row["url"], row["metadata_json"], db_path)
        for key in result:
            result[key] += outcome[key]
    return result


def parse_share_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    if hostname not in {"115.com", "115cdn.com"}:
        raise Share115Error("不是受支持的 115 分享链接。")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[-2] != "s" or not parts[-1]:
        raise Share115Error("115 分享链接缺少分享码。")
    receive_code = parse_qs(parsed.query).get("password", [""])[0]
    return parts[-1], receive_code


class Share115Client:
    def __init__(self, timeout: float = 20.0, *, gate: ShareRequestGate | None = None):
        self.timeout = timeout
        self.gate = gate or ShareRequestGate()

    def _fetch_page(
        self,
        *,
        share_url: str,
        share_code: str,
        receive_code: str,
        cid: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        params = urlencode(
            {
                "share_code": share_code,
                "receive_code": receive_code,
                "offset": offset,
                "limit": limit,
                "asc": "0",
                "cid": cid,
                "format": "json",
            }
        )
        request = Request(
            f"{SNAPSHOT_API}?{params}",
            headers={
                "Accept": "application/json",
                "Referer": share_url,
                "User-Agent": "Yingku/1.0 (local media manager)",
            },
        )
        with self.gate.request():
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                raise Share115Error(
                    f"115 分享接口返回 HTTP {exc.code}。", risk_control=exc.code in {401, 403, 405, 429},
                ) from exc
            except (URLError, TimeoutError) as exc:
                raise Share115Error("无法连接 115 分享接口。") from exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise Share115Error("115 分享接口返回非 JSON 数据，已暂停访问以避免连续请求。", risk_control=True) from exc

            if not isinstance(payload, dict):
                raise Share115Error("115 分享接口数据结构异常，已暂停访问。", risk_control=True)
            if payload.get("state") not in (True, 1):
                errno = payload.get("errno")
                try:
                    errno = int(errno) if errno is not None else None
                except (TypeError, ValueError):
                    errno = None
                message = str(payload.get("error") or payload.get("message") or "分享内容不可访问")
                risk = errno not in {*INVALID_SHARE_ERRNOS, *UNAVAILABLE_ERRNOS} or bool(
                    re.search(r"风控|频繁|验证码|验证|访问限制|访问被阻断|captcha|too many|rate.?limit", message, re.I)
                )
                raise Share115Error(message, errno, risk_control=risk)
            data = payload.get("data")
            if not isinstance(data, dict):
                raise Share115Error("115 分享接口缺少目录数据，已暂停访问。", risk_control=True)
            return data

    def probe_share(self, share_url: str) -> None:
        share_code, receive_code = parse_share_url(share_url)
        self._fetch_page(share_url=share_url, share_code=share_code, receive_code=receive_code, cid="0", offset=0, limit=1)

    def fetch_snapshot(
        self,
        share_url: str,
        *,
        max_depth: int = 3,
        max_names: int = 240,
        page_size: int = 100,
        max_requests: int = 12,
    ) -> dict[str, Any]:
        share_code, receive_code = parse_share_url(share_url)
        queue = deque([("0", 0, ())])
        visited: set[str] = set()
        names: list[str] = []
        root_names: list[str] = []
        files: list[dict[str, Any]] = []
        seen_entries: set[tuple[str, str]] = set()
        incomplete: set[str] = set()
        requests = 0
        entry_count = 0
        share_title: str | None = None

        while queue and entry_count < max_names and requests < max_requests:
            cid, depth, parents = queue.popleft()
            if cid in visited:
                continue
            visited.add(cid)
            offset = 0

            while entry_count < max_names and requests < max_requests:
                requests += 1
                data = self._fetch_page(
                    share_url=share_url,
                    share_code=share_code,
                    receive_code=receive_code,
                    cid=cid,
                    offset=offset,
                    limit=page_size,
                )
                if share_title is None:
                    value = (data.get("shareinfo") or {}).get("share_title")
                    share_title = str(value).strip() if value else None

                items = data.get("list") or []
                if not isinstance(items, list):
                    incomplete.add("invalid_page")
                    items = []
                page_entries = 0
                for item in items:
                    if not isinstance(item, dict):
                        incomplete.add("invalid_entry")
                        continue
                    name = str(item.get("n") or item.get("file_name") or "").strip()
                    is_directory = item.get("fc") in {0, "0"} or item.get("is_dir") in {1, True, "1"}
                    child_cid = str(item.get("cid") or "")
                    fid = str(item.get("fid") or "")
                    identity = ("directory" if is_directory else "file", child_cid if is_directory else fid)
                    if not name or not identity[1]:
                        incomplete.add("invalid_entry")
                        continue
                    if identity in seen_entries:
                        incomplete.add("duplicate_entries")
                        continue
                    seen_entries.add(identity)
                    entry_count += 1
                    page_entries += 1
                    if name and name not in names:
                        names.append(name)
                        if depth == 0:
                            root_names.append(name)
                    if is_directory:
                        if child_cid == cid:
                            incomplete.add("directory_cycle")
                        elif depth < max_depth:
                            queue.append((child_cid, depth + 1, (*parents, name)))
                        else:
                            incomplete.add("depth_limit")
                    else:
                        files.append({"id": fid, "name": name, "path": "/".join((*parents, name))})
                    if entry_count >= max_names:
                        incomplete.add("entry_limit")
                        break

                count = data.get("count")
                try:
                    count = int(count)
                except (TypeError, ValueError):
                    incomplete.add("missing_count")
                    count = len(items)
                if offset + len(items) >= count:
                    break
                if not page_entries:
                    incomplete.add("incomplete_page")
                    break
                offset += len(items)
                if requests >= max_requests:
                    incomplete.add("request_limit")

        if queue:
            incomplete.add("unvisited_directories")

        return {
            "version": SNAPSHOT_VERSION,
            "status": "ok",
            "fetched_at": utc_now(),
            "complete": not incomplete,
            "incomplete_reasons": sorted(incomplete),
            "files": files,
            "root_names": root_names,
            "request_count": requests,
            "share_title": share_title,
            "names": names,
            "search_names": derive_search_names(root_names, names),
            "inferred_media_type": infer_media_type(names),
        }


def derive_search_names(root_names: list[str], names: list[str]) -> list[str]:
    parsed: list[tuple[str, str]] = []
    for name in names:
        title, _ = parse_release_title(name)
        normalized = normalize_title(title)
        if len(normalized) < 2 or TECHNICAL_TITLE_RE.fullmatch(normalized):
            continue
        parsed.append((normalized, name))

    counts = Counter(normalized for normalized, _ in parsed)
    total = len(parsed)
    selected: list[str] = []

    if len(root_names) == 1:
        selected.append(root_names[0])

    for normalized, count in counts.most_common():
        if count < 2 or count / max(total, 1) < 0.5:
            continue
        representative = next(name for key, name in parsed if key == normalized)
        if representative not in selected:
            selected.append(representative)

    return selected[:8]


def infer_media_type(names: list[str]) -> str | None:
    return "tv" if any(
        SEASON_EPISODE_RE.search(name) or SEASON_RE.search(name) or SEASON_MARKER_RE.search(name)
        for name in names
    ) else None


def inspect_resource(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("status") != "ok" or snapshot.get("complete") is not True:
        return {"kind": "unknown", "titles": [], "video_names": []}
    videos = [f for f in snapshot.get("files", []) if VIDEO_FILE_RE.search(f["name"])
              and not EXTRA_RE.search(f["path"])]
    if not videos:
        return {"kind": "unknown", "titles": [], "video_names": []}
    episodic = [infer_media_type([f["path"]]) == "tv" for f in videos]
    kind = "tv" if all(episodic) else "mixed" if any(episodic) else "single_video" if len(videos) == 1 else "multiple_videos"
    titles = set()
    for file in videos:
        name = file["name"]
        # A fansub/release-group prefix is not an independent English work title.
        if re.match(r"^\[[^\]\u3400-\u9fff]+\]", name):
            continue
        for title in parse_release_aliases(name, include_secondary=False):
            if title.isascii() and re.search(r"[A-Za-z]", title) and is_search_title(title):
                titles.add(title)
    if len({normalize_title(t) for t in titles}) > 1:
        kind = "mixed"
    coverage = []
    if kind == "tv":
        spans = [parse_episode_span(f["name"]) for f in videos]
        if all(spans):
            episodes: dict[int, set[int]] = {}
            for season, first, last in spans:
                episodes.setdefault(season, set()).update(range(first, last + 1))
            coverage = [{
                "season": season, "first_episode": min(numbers), "last_episode": max(numbers),
                "episode_count": len(numbers),
                "contiguous": len(numbers) == max(numbers) - min(numbers) + 1,
            } for season, numbers in sorted(episodes.items())]
    return {
        "kind": kind, "titles": sorted(titles), "video_names": [f["name"] for f in videos],
        "episode_coverage": coverage,
    }


def enrich_media_share_sources(
    media_id: int,
    *,
    db_path: str | Path | None = None,
    client: Share115Client | None = None,
    force: bool = False,
) -> dict[str, int]:
    share_client = client or Share115Client()
    results = {"processed": 0, "enriched": 0, "unavailable": 0, "errors": 0, "removed": 0, "media_removed": 0}
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT id, url, metadata_json FROM source_records WHERE media_id = ? AND source_type = '115'",
            (media_id,),
        ).fetchall()

    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        cached = metadata.get("share_snapshot")
        cached_status = cached.get("status") if isinstance(cached, dict) else None
        if is_cancelled_snapshot(cached):
            outcome = remove_cancelled_source(row["id"], row["url"], row["metadata_json"], db_path)
            results["removed"] += outcome["removed"]
            results["media_removed"] += outcome["media_removed"]
            continue
        if not force and (cached_status == "unavailable" or (
            cached_status == "ok" and cached.get("version") == SNAPSHOT_VERSION
        )):
            continue

        results["processed"] += 1
        try:
            snapshot = share_client.fetch_snapshot(row["url"])
            results["enriched"] += 1
        except Share115Error as exc:
            if exc.risk_control:
                raise
            if exc.errno in INVALID_SHARE_ERRNOS:
                outcome = remove_cancelled_source(row["id"], row["url"], row["metadata_json"], db_path)
                results["removed"] += outcome["removed"]
                results["media_removed"] += outcome["media_removed"]
                continue
            status = "unavailable" if exc.errno in UNAVAILABLE_ERRNOS else "error"
            snapshot = {
                "status": status,
                "fetched_at": utc_now(),
                "error": str(exc),
                "errno": exc.errno,
                "names": [],
                "search_names": [],
                "inferred_media_type": None,
            }
            results["unavailable" if status == "unavailable" else "errors"] += 1

        # No database write lock is held while walking a remote share.
        with connect(db_path) as connection:
            current = connection.execute("SELECT metadata_json FROM source_records WHERE id = ?", (row["id"],)).fetchone()
            if not current:
                continue
            try:
                metadata = json.loads(current["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["share_snapshot"] = snapshot
            connection.execute(
                "UPDATE source_records SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False), row["id"]),
            )

    return results
