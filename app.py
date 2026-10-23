#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from backend.repository import import_file
from backend.retitle import describe_refresh, refresh_titles
from backend.series import describe_series_refresh, refresh_series_collections
from backend.schema import ROOT_DIR, init_db, resolve_db_path
from backend.server import serve
from backend.fastapi_app import create_app
from backend.tg_import import describe_result, import_export
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

    telegram_command = commands.add_parser(
        "import-telegram", help="导入 Telegram 桌面版导出的频道记录（HTML 导出目录）"
    )
    telegram_command.add_argument("path", help="导出目录或其中的 messages*.html 文件")
    telegram_command.add_argument("--channel", dest="channel_username", help="频道用户名，如 lsp115")
    telegram_command.add_argument("--channel-id", help="频道数字 ID；默认从既有入库记录复用")
    telegram_command.add_argument("--channel-name", help="覆盖导出页头里的频道名")
    telegram_command.add_argument("--limit", type=int, help="本次最多处理 N 条消息（已入库的消息不计入）")
    telegram_command.add_argument("--workers", type=int, default=4, help="并发处理线程数，默认 4")
    telegram_command.add_argument("--delay", type=float, default=0.0, help="每提交一条消息后的间隔秒数")
    telegram_command.add_argument("--dry-run", action="store_true", help="只解析统计，不写数据库、不联网")
    telegram_command.add_argument(
        "--no-tmdb",
        action="store_true",
        help="导入时跳过 TMDB 自动匹配，作品留在待匹配状态，事后用 app.py match 批量补",
    )

    match_command = commands.add_parser("match", help="批量搜索并关联 TMDB")
    match_command.add_argument("--limit", type=int, default=50)
    match_command.add_argument("--workers", type=int, default=4, help="并发检索线程数，默认 4")
    match_command.add_argument("--series", action="store_true", help="只补匹配属于某个系列的成员")

    retitle_command = commands.add_parser(
        "retitle", help="用入库时保存的频道首行重算标题与系列归属（解析规则修复后重修存量）"
    )
    retitle_command.add_argument("--apply", action="store_true", help="真正写库；默认只预览")
    retitle_command.add_argument("--limit", type=int, help="最多扫描 N 条")

    series_command = commands.add_parser(
        "series", help="用 TMDB 合集补齐系列成员（匹配合并后重挂走散的作品）"
    )
    series_command.add_argument("--apply", action="store_true", help="真正写库；默认只预览")
    series_command.add_argument("--limit", type=int, help="最多处理 N 个系列")

    audit_command = commands.add_parser("check-shares", help="低频检查全部 115 分享，只删除确认已取消或失效的链接")
    audit_command.add_argument("--resume", action="store_true", help="从上次未完成的位置继续，不重复检查已处理链接")
    audit_command.add_argument("--imported", action="store_true", help="只检查批量导入（app.py import）的来源，跳过入库时已校验的 webhook 来源")

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

    if args.command == "import-telegram":
        result = import_export(
            args.path,
            channel_username=args.channel_username,
            channel_name=args.channel_name,
            channel_id=args.channel_id,
            limit=args.limit,
            workers=args.workers,
            delay=args.delay,
            dry_run=args.dry_run,
            auto_match=not args.no_tmdb,
            db_path=db_path,
        )
        print(describe_result(result))
        return

    if args.command == "match":
        def report(progress: dict) -> None:
            print(
                f"[{progress['processed']}] matched={progress['matched']} review={progress['review']} "
                f"not_found={progress['not_found']} errors={progress['errors']}",
                flush=True,
            )

        result = bulk_match(
            args.limit,
            db_path=db_path,
            client=TmdbClient(),
            workers=args.workers,
            progress=report,
            series_only=args.series,
        )
        print(result)
        return

    if args.command == "series":
        load_env(ROOT_DIR / ".env")
        result = refresh_series_collections(apply=args.apply, limit=args.limit, db_path=db_path)
        print(describe_series_refresh(result, applied=args.apply))
        return

    if args.command == "retitle":
        result = refresh_titles(apply=args.apply, limit=args.limit, db_path=db_path)
        print(describe_refresh(result, applied=args.apply))
        return

    if args.command == "check-shares":
        print(audit_share_links(db_path, resume=args.resume, imported_only=args.imported))
        return

    if args.command == "serve":
        if args.legacy:
            serve(args.host, args.port, db_path, args.static_dir)
            return
        import uvicorn
        uvicorn.run(create_app(db_path, args.static_dir), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
