from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import ingestion
from .ingestion_config import INGESTION_CONFIG_PATH, read_ingestion_key, save_ingestion_key
from .repository import (
    delete_candidate, get_media, get_media_by_tmdb_id, get_media_filters, get_stats,
    import_content, link_tmdb, list_imports, list_ingestion_records, list_media, unlink_tmdb,
    update_media_fields,
)
from .schema import ROOT_DIR, init_db, resolve_db_path
from .share115 import Share115Error, cleanup_cancelled_shares
from .share_audit import audit_status, pause_audit
from .tmdb import TmdbClient, TmdbError, bulk_match, search_media

DIST_DIR = ROOT_DIR / "frontend" / "dist"


def create_app(db_path=None, static_dir=None) -> FastAPI:
    database = resolve_db_path(db_path)
    static_root = Path(static_dir).resolve() if static_dir else DIST_DIR.resolve()
    init_db(database)
    app = FastAPI(title="PanBox API", version="1.0", docs_url="/api/docs", redoc_url=None)
    app.state.db_path = database
    app.state.static_dir = static_root
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["*"])

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(TmdbError)
    async def tmdb_error(_: Request, exc: TmdbError):
        return JSONResponse({"error": str(exc)}, status_code=502)

    def db(): return app.state.db_path

    def require_media_key(key: Optional[str]):
        expected = os.getenv("MEDIA_API_KEY", "")
        if not expected:
            raise HTTPException(503, "公共资源接口尚未配置 MEDIA_API_KEY。")
        if not key or key != expected:
            raise HTTPException(401, "需要有效的 API Key。")

    def _local_only(request: Request):
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise HTTPException(403, "此设置接口仅允许本机访问。")

    def require_ingestion_key(authorization: Optional[str]):
        expected, _ = read_ingestion_key(INGESTION_CONFIG_PATH)
        if not expected:
            raise HTTPException(503, "接收接口尚未配置 INGESTION_API_KEY。")
        if authorization != f"Bearer {expected}":
            raise HTTPException(401, "需要有效的接入凭据。")

    @app.get("/api/health")
    async def health(): return {"ok": True}

    @app.get("/api/v1/settings/ingestion")
    async def ingestion_config(request: Request):
        _local_only(request)
        _, source = read_ingestion_key(INGESTION_CONFIG_PATH)
        return {"configured": source != "none", "source": source}

    @app.post("/api/v1/settings/ingestion")
    async def save_ingestion_config(request: Request, body: dict[str, Any]):
        _local_only(request)
        key = body.get("api_key")
        if not isinstance(key, str):
            raise HTTPException(400, "API Key 至少需要 16 个字符。")
        try:
            save_ingestion_key(key, INGESTION_CONFIG_PATH)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "configured": True, "source": "file"}

    @app.get("/api/config")
    async def config():
        tmdb = TmdbClient()
        return {"tmdb_configured": tmdb.configured, "tmdb_auth_mode": tmdb.auth_mode, "db_path": str(db())}

    @app.get("/api/stats")
    async def stats(): return get_stats(db())

    @app.get("/api/share-audit")
    async def share_status(): return audit_status(db())

    @app.get("/api/imports")
    async def imports(limit: int = Query(50, ge=1, le=250)): return {"items": list_imports(limit, db())}

    @app.get("/api/ingestion/records")
    async def ingestion_records(limit: int = Query(100, ge=1, le=250)): return list_ingestion_records(limit, db())

    @app.get("/api/ingestion/records/{ingestion_id}")
    async def ingestion_record_detail(ingestion_id: str):
        event = ingestion.get_event(ingestion_id, db())
        if not event: raise HTTPException(404, "入库记录不存在。")
        return event

    @app.post("/api/ingestion/reprocess")
    async def reprocess_ingestion(body: dict[str, Any]):
        return ingestion.reprocess_ignored(int(body.get("limit") or 100), db())

    @app.get("/api/media/filters")
    async def media_filters(): return get_media_filters(db())

    @app.get("/api/media")
    async def media(q: str = "", status: str = "all", type: str = "all", resource_kind: str = "all", source: str = "all", availability: str = "all", year: str = "all", genre: str = "all", country: str = "all", quality: str = "all", codec: str = "all", hdr: str = "all", multi_source: bool = False, sort: str = "updated_desc", page: int = 1, page_size: int = 30):
        return list_media(query=q, status=status, media_type=type, resource_kind=resource_kind, source_type=source, availability=availability, year=year, genre=genre, country=country, quality=quality, codec=codec, hdr=hdr, multi_source_only=multi_source, sort=sort, page=page, page_size=page_size, db_path=db())

    @app.get("/api/media/{media_id}")
    async def media_detail(media_id: int):
        item = get_media(media_id, db())
        if not item: raise HTTPException(404, "媒体记录不存在。")
        return item

    @app.get("/api/media/tmdb/{tmdb_id}/resources")
    async def public_resources(tmdb_id: int, x_api_key: Optional[str] = Header(None)):
        require_media_key(x_api_key)
        item = get_media_by_tmdb_id(tmdb_id, db())
        if not item: raise HTTPException(404, "未找到对应 TMDB ID 的媒体或本地资源。")
        return {"media": {key: value for key, value in item.items() if key != "sources"}, "resources": item["sources"]}

    @app.post("/api/import", status_code=201)
    async def import_api(body: dict[str, Any]):
        content = body.get("content")
        if not isinstance(content, str) or not content.strip(): raise ValueError("没有可导入的文本内容。")
        return import_content(content, str(body.get("name") or "browser-import.txt"), kind=str(body.get("kind") or "auto"), db_path=db())

    @app.post("/api/tmdb/sync")
    async def tmdb_sync(body: dict[str, Any]): return bulk_match(min(max(int(body.get("limit") or 20), 1), 100), db_path=db())

    @app.post("/api/share-audit/pause")
    async def pause(): pause_audit(db()); return audit_status(db())

    @app.get("/api/tmdb/search")
    async def tmdb_search(q: str, type: str = "multi", page: int = 1, include_adult: bool = False): return TmdbClient().manual_search(q, media_type=type, page=page, include_adult=include_adult)

    @app.post("/api/media/{media_id}/candidates")
    async def candidates(media_id: int, body: dict[str, Any]):
        result = search_media(media_id, auto_link=bool(body.get("auto_link", False)), refresh_share=bool(body.get("refresh_share", False)), db_path=db())
        result["item"] = get_media(result.get("media_id", media_id), db())
        return result

    @app.post("/api/media/{media_id}/candidates/{candidate_id}/reject")
    async def reject(media_id: int, candidate_id: int):
        delete_candidate(media_id, candidate_id, db()); return get_media(media_id, db())

    @app.post("/api/media/{media_id}/tmdb/link")
    async def link(media_id: int, body: dict[str, Any]):
        details = TmdbClient().details(str(body.get("media_type")), int(body.get("tmdb_id")))
        return get_media(link_tmdb(media_id, details, float(body.get("confidence") or 1), str(body.get("method") or "manual"), db()), db())

    @app.post("/api/media/{media_id}/tmdb/unlink")
    async def unlink(media_id: int): unlink_tmdb(media_id, db()); return get_media(media_id, db())

    @app.patch("/api/media/{media_id}")
    async def patch_media(media_id: int, body: dict[str, Any]):
        item = update_media_fields(media_id, body, db())
        if not item: raise HTTPException(404, "媒体记录不存在。")
        return item

    @app.post("/api/v1/ingestion/messages", status_code=202)
    async def receive_ingestion(body: dict[str, Any], authorization: Optional[str] = Header(None)):
        require_ingestion_key(authorization); return ingestion.receive(body, db())

    @app.get("/api/v1/ingestion/messages/{ingestion_id}")
    async def ingestion_detail(ingestion_id: str, authorization: Optional[str] = Header(None)):
        require_ingestion_key(authorization)
        event = ingestion.get_event(ingestion_id, db())
        if not event: raise HTTPException(404, "接收记录不存在。")
        return event

    @app.get("/{path:path}")
    async def static(path: str = ""):
        clean = unquote(path).lstrip("/")
        candidate = (static_root / clean).resolve()
        if candidate != static_root and static_root not in candidate.parents: raise HTTPException(400, "非法静态文件路径。")
        if candidate.is_file(): return FileResponse(candidate, headers={"Cache-Control": "no-cache"})
        index = static_root / "index.html"
        if index.is_file(): return FileResponse(index, headers={"Cache-Control": "no-cache"})
        raise HTTPException(503, "前端尚未构建。")

    return app


app = create_app()
