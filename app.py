#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from backend.ingestion import auto_match_media
from backend.repository import import_file
from backend.retitle import (
    describe_episode_suffix_refresh,
    describe_episode_year_repair,
    describe_refresh,
    normalize_episode_suffix_titles,
    refresh_titles,
    repair_episode_base_years,
)
from backend.series import describe_series_refresh, refresh_series_collections
from backend.schema import ROOT_DIR, init_db, resolve_db_path
from backend.server import serve
from backend.fastapi_app import create_app
from backend.tg_import import describe_result, import_export
from backend.tmdb import TmdbClient, bulk_match
from backend.type_audit import (
    apply_movie_candidates,
    audit_media_types,
    auto_link_existing_candidates,
    describe_type_audit,
    rescore_movie_type_conflicts,
)
from backend.type_evidence import (
    apply_strong_type_evidence,
    audit_type_evidence,
    backfill_legacy_movie_types,
    describe_type_evidence,
)
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
    import_command.add_argument(
        "--no-tmdb",
        action="store_true",
        help="导入时跳过 TMDB 自动匹配，作品留在待匹配状态，事后用 app.py match 批量补",
    )

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
    match_command.add_argument(
        "--no-auto-link",
        action="store_true",
        help="只刷新候选，不自动关联；用于规则全量重跑后的安全审核",
    )

    retitle_command = commands.add_parser(
        "retitle", help="用入库时保存的频道首行重算标题与系列归属（解析规则修复后重修存量）"
    )
    retitle_command.add_argument("--apply", action="store_true", help="真正写库；默认只预览")
    retitle_command.add_argument("--limit", type=int, help="最多扫描 N 条")

    episode_command = commands.add_parser(
        "normalize-episode-suffix",
        help="把「牧神记79」这类剧名尾部数字识别为集数（只处理有 TV 同组标题的记录）",
    )
    episode_command.add_argument("--apply", action="store_true", help="真正写库；默认只预览")
    episode_command.add_argument("--limit", type=int, help="最多扫描 N 条")

    repair_command = commands.add_parser(
        "repair-episode-years",
        help="按同名已匹配剧集的集数覆盖，为缺年份的集数补检索年份",
    )
    repair_command.add_argument("--apply", action="store_true", help="真正写库；默认只预览")
    repair_command.add_argument("--limit", type=int, help="最多扫描 N 条")

    series_command = commands.add_parser(
        "series", help="用 TMDB 合集补齐系列成员（匹配合并后重挂走散的作品）"
    )
    series_command.add_argument("--apply", action="store_true", help="真正写库；默认只预览")
    series_command.add_argument("--limit", type=int, help="最多处理 N 个系列")

    type_audit_command = commands.add_parser(
        "audit-types", help="只读检查存量媒体类型，按图标/结构证据分组，防止误改"
    )
    type_audit_command.add_argument("--limit", type=int, help="最多扫描 N 条")
    type_audit_command.add_argument(
        "--apply-movies",
        action="store_true",
        help="只修正 audit 中高置信电影候选的 media_type，不改 TMDB ID",
    )
    type_audit_command.add_argument(
        "--rescore",
        action="store_true",
        help="重算已修正电影候选的分数和 type_conflict，不动季集和 TMDB ID",
    )
    type_audit_command.add_argument(
        "--auto-link",
        action="store_true",
        help="对已存候选补做自动关联，仍遵守 should_auto_link 的全部限制",
    )

    evidence_command = commands.add_parser(
        "audit-type-evidence", help="按结构证据分级本地电影/剧集类型；默认只读，--apply 只修强证据冲突"
    )
    evidence_command.add_argument("--limit", type=int, help="最多扫描 N 条")
    evidence_command.add_argument("--apply", action="store_true", help="只应用单边强证据，弱证据和冲突不动")

    backfill_command = commands.add_parser(
        "backfill-movie-types",
        help="回填历史 tv 误判：仅修正有高置信电影候选、且当前解析器没有强剧集证据的条目",
    )
    backfill_command.add_argument("--apply", action="store_true", help="真正写库；默认只预览")
    backfill_command.add_argument("--limit", type=int, help="最多修正 N 条")

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
            match_note = ""
            if not args.no_tmdb:
                matched = auto_match_media(result.get("media_ids") or (), db_path)
                counts: dict[str, int] = {}
                for status in matched.values():
                    counts[status] = counts.get(status, 0) + 1
                if counts:
                    match_note = "，自动匹配 " + " ".join(f"{key}={value}" for key, value in sorted(counts.items()))
            print(
                f"{result['source_name']}: 总计 {result['total']}，新增 {result['inserted']}，"
                f"重复 {result['duplicates']}，错误 {result['errors']}{match_note}"
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
            auto_link=not args.no_auto_link,
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
        if args.apply and result.get("changed_ids"):
            matched = auto_match_media(result["changed_ids"], db_path)
            counts: dict[str, int] = {}
            for status in matched.values():
                counts[status] = counts.get(status, 0) + 1
            if counts:
                print("自动匹配 " + " ".join(f"{key}={value}" for key, value in sorted(counts.items())))
        return

    if args.command == "normalize-episode-suffix":
        result = normalize_episode_suffix_titles(apply=args.apply, limit=args.limit, db_path=db_path)
        print(describe_episode_suffix_refresh(result, applied=args.apply))
        return

    if args.command == "repair-episode-years":
        result = repair_episode_base_years(apply=args.apply, limit=args.limit, db_path=db_path)
        print(describe_episode_year_repair(result, applied=args.apply))
        return

    if args.command == "audit-types":
        result = audit_media_types(limit=args.limit, db_path=db_path)
        print(describe_type_audit(result, read_only=not (args.apply_movies or args.rescore)))
        if args.apply_movies:
            applied = apply_movie_candidates(audit=result, db_path=db_path)
            print(
                f"已修正高置信电影候选 {applied['updated']} 条"
                f"（候选 {applied['candidate_movie']}，跳过 {applied['missing']}）；未写 TMDB ID。"
            )
        if args.rescore:
            rescored = rescore_movie_type_conflicts(db_path=db_path)
            print(
                f"已重算候选 {rescored['updated']} 条"
                f"（扫描 {rescored['candidate_rows']}，受保护 {rescored['protected']}，跳过 {rescored['skipped']}）。"
            )
        if args.auto_link:
            linked = auto_link_existing_candidates(db_path=db_path)
            print(
                f"已自动关联 {linked['linked']} 条"
                f"（扫描 {linked['media']}，保留审核 {linked['skipped']}，异常 {linked['errors']}）。"
            )
        return

    if args.command == "backfill-movie-types":
        result = backfill_legacy_movie_types(
            dry_run=not args.apply, limit=args.limit, db_path=db_path,
        )
        print(
            f"扫描 {result['scanned']} 条 tv→movie 候选；"
            f"{'已修正' if args.apply else '可修正'} {result['changed']} 条，跳过 {result['skipped']} 条。"
        )
        for change in result["changes"][:20]:
            print(f"  #{change['id']} {change['title']}: {change['from']} -> {change['to']} ({change['score']:.3f})")
        if len(result["changes"]) > 20:
            print(f"  …另有 {len(result['changes']) - 20} 条")
        if args.apply and result["changed"]:
            rescored = rescore_movie_type_conflicts(db_path=db_path)
            print(
                f"已重算候选 {rescored['updated']} 条"
                f"（扫描 {rescored['candidate_rows']}，受保护 {rescored['protected']}，跳过 {rescored['skipped']}）。"
            )
        return

    if args.command == "audit-type-evidence":
        result = audit_type_evidence(limit=args.limit, db_path=db_path)
        print(describe_type_evidence(result, applied=False))
        if args.apply:
            applied = apply_strong_type_evidence(limit=args.limit, db_path=db_path)
            print(describe_type_evidence(applied, applied=True))
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
