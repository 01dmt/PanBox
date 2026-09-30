#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from backend.repository import import_file
from backend.schema import ROOT_DIR, init_db, resolve_db_path
from backend.server import serve
from backend.fastapi_app import create_app
from backend.tmdb import TmdbClient, bulk_match
from backend.share_audit import audit_share_links


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="影库：115 / ED2K 媒体资料库")
    root.add_argument("--db", help="SQLite 数据库路径")
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="初始化数据库")

    import_command = commands.add_parser("import", help="导入一个或多个文本清单")
    import_command.add_argument("paths", nargs="+", help="文本文件路径")
    import_command.add_argument("--kind", choices=["auto", "115", "ed2k", "mixed"], default="auto")

    match_command = commands.add_parser("match", help="批量搜索并关联 TMDB")
    match_command.add_argument("--limit", type=int, default=50)

    audit_command = commands.add_parser("check-shares", help="低频检查全部 115 分享，只删除确认已取消或失效的链接")
    audit_command.add_argument("--resume", action="store_true", help="从上次未完成的位置继续，不重复检查已处理链接")

    serve_command = commands.add_parser("serve", help="启动本地 Web 管理界面")
    serve_command.add_argument("--host", default=os.getenv("MEDIA_HOST", "127.0.0.1"))
    serve_command.add_argument("--port", type=int, default=int(os.getenv("MEDIA_PORT", "8088")))
    serve_command.add_argument("--static-dir", help="前端构建目录")
    serve_command.add_argument("--legacy", action="store_true", help="使用旧版标准库 HTTP 服务")
    return root


def main() -> None:
    load_env(ROOT_DIR / ".env")
    args = parser().parse_args()
    db_path = resolve_db_path(args.db)

    if args.command == "init":
        print(init_db(db_path))
        return

    if args.command == "import":
        init_db(db_path)
        for value in args.paths:
            result = import_file(value, kind=args.kind, db_path=db_path)
            print(
                f"{result['source_name']}: 总计 {result['total']}，新增 {result['inserted']}，"
                f"重复 {result['duplicates']}，错误 {result['errors']}"
            )
        return

    if args.command == "match":
        result = bulk_match(args.limit, db_path=db_path, client=TmdbClient())
        print(result)
        return

    if args.command == "check-shares":
        print(audit_share_links(db_path, resume=args.resume))
        return

    if args.command == "serve":
        if args.legacy:
            serve(args.host, args.port, db_path, args.static_dir)
            return
        import uvicorn
        uvicorn.run(create_app(db_path, args.static_dir), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
