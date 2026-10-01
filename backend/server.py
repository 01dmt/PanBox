from __future__ import annotations

import json
import mimetypes
import os
import re
import traceback
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .repository import (
    delete_candidate,
    get_media,
    get_media_filters,
    get_stats,
    import_content,
    link_tmdb,
    list_ingestion_records,
    list_imports,
    list_media,
    unlink_tmdb,
    update_media_fields,
    get_media_by_tmdb_id,
)
from .schema import ROOT_DIR, init_db, resolve_db_path
from . import ingestion
from .ingestion_config import INGESTION_CONFIG_PATH, read_ingestion_key, save_ingestion_key
from .tmdb import TmdbClient, TmdbError, bulk_match, search_media
from .share115 import Share115Error, cleanup_cancelled_shares
from .share_audit import audit_status, pause_audit


DIST_DIR = ROOT_DIR / "frontend" / "dist"
MEDIA_ID_RE = re.compile(r"^/api/media/(\d+)$")
CANDIDATES_RE = re.compile(r"^/api/media/(\d+)/candidates$")
REJECT_CANDIDATE_RE = re.compile(r"^/api/media/(\d+)/candidates/(\d+)/reject$")
LINK_RE = re.compile(r"^/api/media/(\d+)/tmdb/link$")
UNLINK_RE = re.compile(r"^/api/media/(\d+)/tmdb/unlink$")
PUBLIC_RESOURCES_RE = re.compile(r"^/api/media/tmdb/(\d+)/resources$")
INGESTION_RECORD_RE = re.compile(r"^/api/ingestion/records/([A-Za-z0-9_-]+)$")


class MediaRequestHandler(BaseHTTPRequestHandler):
    db_path: Path = resolve_db_path()
    static_dir: Path = DIST_DIR
    server_version = "Yingku/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _headers(self, status: int, content_type: str, content_length: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        if content_length is not None:
            self.send_header("Content-Length", str(content_length))
        self.end_headers()

    def _json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _error(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > 20 * 1024 * 1024:
            raise ValueError("请求体不能超过 20 MB。")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求必须是有效的 UTF-8 JSON。") from exc

    def do_OPTIONS(self) -> None:
        self._headers(HTTPStatus.NO_CONTENT, "text/plain", 0)

    def do_HEAD(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self._headers(HTTPStatus.OK, "application/json; charset=utf-8", 0)
                return
            self._serve_static(parsed.path, head_only=True)
        except Exception as exc:
            self._handle_exception(exc)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            resources_match = PUBLIC_RESOURCES_RE.match(path)
            if resources_match:
                if not self._public_api_key_valid():
                    return
                item = get_media_by_tmdb_id(int(resources_match.group(1)), self.db_path)
                if not item:
                    self._error("未找到对应 TMDB ID 的媒体或本地资源。", HTTPStatus.NOT_FOUND)
                    return
                self._json({
                    "media": {key: value for key, value in item.items() if key != "sources"},
                    "resources": item["sources"],
                })
                return

            if match := re.fullmatch(r"/api/v1/ingestion/messages/([A-Za-z0-9_-]+)", path):
                if not self._ingestion_key_valid():
                    return
                event = ingestion.get_event(match.group(1), self.db_path)
                if not event:
                    self._error("接收记录不存在。", HTTPStatus.NOT_FOUND)
                    return
                self._json(event)
                return
            if path == "/api/health":
                self._json({"ok": True})
                return
            if path == "/api/v1/settings/ingestion":
                if not self._settings_local():
                    return
                _, source = read_ingestion_key(INGESTION_CONFIG_PATH)
                self._json({"configured": source != "none", "source": source})
                return
            if path == "/api/config":
                tmdb = TmdbClient()
                self._json(
                    {
                        "tmdb_configured": tmdb.configured,
                        "tmdb_auth_mode": tmdb.auth_mode,
                        "db_path": str(self.db_path),
                    }
                )
                return
            if path == "/api/stats":
                self._json(get_stats(self.db_path))
                return
            if path == "/api/share-audit":
                self._json(audit_status(self.db_path))
                return
            if path == "/api/tmdb/search":
                self._json(TmdbClient().manual_search(
                    _first(query, "q", ""), media_type=_first(query, "type", "multi"),
                    page=int(_first(query, "page", "1")),
                    include_adult=_first(query, "include_adult", "false") == "true",
                ))
                return
            if path == "/api/imports":
                self._json({"items": list_imports(int(_first(query, "limit", "50")), self.db_path)})
                return
            if path == "/api/ingestion/records":
                self._json(list_ingestion_records(int(_first(query, "limit", "100")), self.db_path))
                return
            if match := INGESTION_RECORD_RE.match(path):
                event = ingestion.get_event(match.group(1), self.db_path)
                if not event:
                    self._error("入库记录不存在。", HTTPStatus.NOT_FOUND)
                    return
                self._json(event)
                return
            if path == "/api/media/filters":
                self._json(get_media_filters(self.db_path))
                return
            if path == "/api/media":
                self._json(
                    list_media(
                        query=_first(query, "q", ""),
                        status=_first(query, "status", "all"),
                        media_type=_first(query, "type", "all"),
                        source_type=_first(query, "source", "all"),
                        availability=_first(query, "availability", "all"),
                        year=_first(query, "year", "all"),
                        genre=_first(query, "genre", "all"),
                        country=_first(query, "country", "all"),
                        quality=_first(query, "quality", "all"),
                        codec=_first(query, "codec", "all"),
                        hdr=_first(query, "hdr", "all"),
                        multi_source_only=_first(query, "multi_source", "false") == "true",
                        sort=_first(query, "sort", "updated_desc"),
                        page=int(_first(query, "page", "1")),
                        page_size=int(_first(query, "page_size", "30")),
                        db_path=self.db_path,
                    )
                )
                return

            media_match = MEDIA_ID_RE.match(path)
            if media_match:
                item = get_media(int(media_match.group(1)), self.db_path)
                if not item:
                    self._error("媒体记录不存在。", HTTPStatus.NOT_FOUND)
                    return
                self._json(item)
                return

            self._serve_static(path)
        except Exception as exc:
            self._handle_exception(exc)

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/api/v1/ingestion/messages":
                if not self._ingestion_key_valid():
                    return
                self._json(ingestion.receive(self._body(), self.db_path), HTTPStatus.ACCEPTED)
                return
            if path == "/api/v1/settings/ingestion":
                if not self._settings_local():
                    return
                body = self._body()
                try:
                    save_ingestion_key(body.get("api_key"), INGESTION_CONFIG_PATH)
                except (TypeError, ValueError) as exc:
                    self._error(str(exc), HTTPStatus.BAD_REQUEST)
                    return
                self._json({"ok": True, "configured": True, "source": "file"})
                return
            body = self._body()

            if path == "/api/import":
                content = body.get("content")
                if not isinstance(content, str) or not content.strip():
                    self._error("没有可导入的文本内容。")
                    return
                result = import_content(
                    content=content,
                    source_name=str(body.get("name") or "browser-import.txt"),
                    source_path=None,
                    kind=str(body.get("kind") or "auto"),
                    db_path=self.db_path,
                )
                self._json(result, HTTPStatus.CREATED)
                return

            if path == "/api/tmdb/sync":
                limit = min(max(int(body.get("limit") or 20), 1), 100)
                self._json(bulk_match(limit, db_path=self.db_path))
                return
            if path == "/api/share-audit/pause":
                pause_audit(self.db_path)
                self._json(audit_status(self.db_path))
                return

            candidate_match = CANDIDATES_RE.match(path)
            if candidate_match:
                media_id = int(candidate_match.group(1))
                result = search_media(
                    media_id,
                    auto_link=bool(body.get("auto_link", False)),
                    refresh_share=bool(body.get("refresh_share", False)),
                    db_path=self.db_path,
                )
                result["item"] = get_media(result.get("media_id", media_id), self.db_path)
                self._json(result)
                return

            reject_match = REJECT_CANDIDATE_RE.match(path)
            if reject_match:
                media_id, candidate_id = map(int, reject_match.groups())
                delete_candidate(media_id, candidate_id, self.db_path)
                self._json(get_media(media_id, self.db_path))
                return

            link_match = LINK_RE.match(path)
            if link_match:
                media_id = int(link_match.group(1))
                tmdb_id = int(body.get("tmdb_id"))
                media_type = str(body.get("media_type"))
                confidence = float(body.get("confidence") or 1.0)
                details = TmdbClient().details(media_type, tmdb_id)
                linked_id = link_tmdb(
                    media_id,
                    details,
                    confidence,
                    str(body.get("method") or "manual"),
                    self.db_path,
                )
                self._json(get_media(linked_id, self.db_path))
                return

            unlink_match = UNLINK_RE.match(path)
            if unlink_match:
                media_id = int(unlink_match.group(1))
                unlink_tmdb(media_id, self.db_path)
                self._json(get_media(media_id, self.db_path))
                return

            self._error("API 路径不存在。", HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._handle_exception(exc)

    def do_PATCH(self) -> None:
        try:
            path = urlparse(self.path).path
            match = MEDIA_ID_RE.match(path)
            if not match:
                self._error("API 路径不存在。", HTTPStatus.NOT_FOUND)
                return
            item = update_media_fields(int(match.group(1)), self._body(), self.db_path)
            if not item:
                self._error("媒体记录不存在。", HTTPStatus.NOT_FOUND)
                return
            self._json(item)
        except Exception as exc:
            self._handle_exception(exc)

    def _handle_exception(self, exc: Exception) -> None:
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        if isinstance(exc, Share115Error):
            self._error(str(exc), HTTPStatus.SERVICE_UNAVAILABLE if exc.risk_control else HTTPStatus.BAD_GATEWAY)
            return
        if isinstance(exc, TmdbError):
            self._error(str(exc), HTTPStatus.BAD_GATEWAY)
            return
        if isinstance(exc, (ValueError, TypeError)):
            self._error(str(exc), HTTPStatus.BAD_REQUEST)
            return
        traceback.print_exc()
        self._error("服务器处理请求时发生错误。", HTTPStatus.INTERNAL_SERVER_ERROR)

    def _ingestion_key_valid(self) -> bool:
        expected, _ = read_ingestion_key(INGESTION_CONFIG_PATH)
        provided = self.headers.get("Authorization", "")
        if not expected:
            self._error("接收接口尚未配置 INGESTION_API_KEY。", HTTPStatus.SERVICE_UNAVAILABLE)
            return False
        if not hmac.compare_digest(provided, f"Bearer {expected}"):
            self._error("需要有效的接入凭据。", HTTPStatus.UNAUTHORIZED)
            return False
        return True

    def _settings_local(self) -> bool:
        """Keep credential management available only from the local UI."""
        try:
            host = urlparse("http://" + self.headers.get("Host", ""))
            valid = self.client_address[0] in {"127.0.0.1", "::1"} and host.hostname in {"127.0.0.1", "localhost", "::1"}
        except ValueError:
            valid = False
        if not valid:
            self._error("此设置接口仅允许本机访问。", HTTPStatus.FORBIDDEN)
        return valid

    def _public_api_key_valid(self) -> bool:
        expected = os.getenv("MEDIA_API_KEY", "")
        provided = self.headers.get("X-API-Key", "")
        if not expected:
            self._error("公共资源接口尚未配置 MEDIA_API_KEY。", HTTPStatus.SERVICE_UNAVAILABLE)
            return False
        if not provided or not hmac.compare_digest(provided, expected):
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("WWW-Authenticate", "ApiKey")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "需要有效的 API Key。"}, ensure_ascii=False).encode("utf-8"))
            return False
        return True

    def _local_request(self, service: str) -> bool:
        try:
            host = urlparse("http://" + self.headers.get("Host", ""))
            origin = urlparse(self.headers.get("Origin", ""))
            valid = (self.client_address[0] in {"127.0.0.1", "::1"}
                     and host.hostname in {"127.0.0.1", "localhost", "::1"}
                     and not host.username and not host.password
                     and self.headers.get("X-Yingku-Local") == "1"
                     and self.headers.get("Sec-Fetch-Site", "same-origin") in {"same-origin", "none"}
                     and (not self.headers.get("Origin") or (origin.scheme == "http" and origin.netloc == host.netloc))
                     and int(self.headers.get("Content-Length", "0")) in range(0, 65537))
            if self.command == "POST":
                valid = valid and self.headers.get("Content-Type", "").split(";")[0] == "application/json"
        except ValueError:
            valid = False
        if not valid:
            self._error(f"{service} 仅接受本机管理页面的同源 JSON 请求。", HTTPStatus.FORBIDDEN)
        return bool(valid)

    def _serve_static(self, request_path: str, head_only: bool = False) -> None:
        if not self.static_dir.exists():
            self._error(
                "前端尚未构建。请先在 frontend 目录运行 pnpm build。",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return

        clean = unquote(request_path).lstrip("/")
        candidate = (self.static_dir / clean).resolve()
        static_root = self.static_dir.resolve()
        if candidate != static_root and static_root not in candidate.parents:
            self._error("非法静态文件路径。", HTTPStatus.BAD_REQUEST)
            return
        if not candidate.is_file():
            candidate = static_root / "index.html"
        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type = f"{content_type}; charset=utf-8"
        self._headers(HTTPStatus.OK, content_type, len(data))
        if not head_only:
            self.wfile.write(data)


def _first(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    return values[0] if values else default


def serve(
    host: str = "127.0.0.1",
    port: int = 8088,
    db_path: str | Path | None = None,
    static_dir: str | Path | None = None,
) -> None:
    resolved_db = init_db(db_path)
    cleanup = cleanup_cancelled_shares(resolved_db)
    if cleanup["removed"]:
        print(f"已清理 {cleanup['removed']} 条已取消分享、{cleanup['media_removed']} 条无来源待处理记录。")
    handler = type(
        "ConfiguredMediaRequestHandler",
        (MediaRequestHandler,),
        {
            "db_path": resolved_db,
            "static_dir": Path(static_dir).resolve() if static_dir else DIST_DIR.resolve(),
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"影库正在运行: http://{host}:{port}")
    print(f"数据库: {resolved_db}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
