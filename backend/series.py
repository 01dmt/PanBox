"""Fill series membership from TMDB collections.

A channel that posts ``📺 速度与激情(系列)`` one film per message leaves the series
name in the title and the film identity in the release filename. When matching
merges two rows of the same film, or when a film was imported long before the
series existed, the series view would silently lose members. TMDB knows the
official composition of a film series (``belongs_to_collection``), so the
membership can be rebuilt from data we already trusted enough to link.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .repository import connect, utc_now
from .tmdb import TmdbClient, TmdbError

SAMPLE_LIMIT = 12


def _collection_for(tmdb: TmdbClient, row: Any) -> dict[str, Any] | None:
    """先按已匹配成员的 belongs_to_collection 找，再退回按系列名搜合集。

    成员全被匹配合并掉、或者系列里一部都没匹配上时，前者问不出东西；系列名本身
    通常就是 TMDB 合集名（「终结者」「人类清除计划」），按名字搜一次能救回来。
    """
    if row["tmdb_id"]:
        payload = tmdb.request(f"/movie/{int(row['tmdb_id'])}", {"language": "zh-CN"})
        collection = payload.get("belongs_to_collection") or {}
        if collection.get("id"):
            return collection
    needle = str(row["title"] or "").strip()
    if not needle:
        return None
    hits = tmdb.request("/search/collection", {"query": needle, "language": "zh-CN"}).get("results") or []
    for hit in hits:
        name = str(hit.get("name") or "")
        if name and needle in name and hit.get("id"):
            return {"id": hit["id"], "poster_path": hit.get("poster_path")}
    return None


def refresh_series_collections(
    *,
    apply: bool = False,
    limit: int | None = None,
    db_path: str | Path | None = None,
    client: TmdbClient | None = None,
) -> dict[str, Any]:
    """Look up each series' TMDB collection and attach local films that belong to it."""
    tmdb = client or TmdbClient()
    if not tmdb.configured:
        raise TmdbError("TMDB 未配置。请先在 .env 里设置 TMDB_API_TOKEN 或 TMDB_API_KEY。")

    result: dict[str, Any] = {
        "series": 0, "collections": 0, "attached": 0, "skipped": 0, "failed": 0,
        "samples": [],
    }
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT s.id, s.title, s.tmdb_collection_id,
                   (
                       SELECT m.tmdb_id FROM media_items m
                        WHERE m.series_id = s.id AND m.tmdb_id IS NOT NULL
                          AND m.tmdb_media_type = 'movie'
                        ORDER BY m.id LIMIT 1
                   ) AS tmdb_id
            FROM media_series s
            ORDER BY s.id
            """
        ).fetchall()
    if limit is not None:
        rows = rows[:limit]

    seen: set[int] = set()
    for row in rows:
        result["series"] += 1
        try:
            collection = _collection_for(tmdb, row)
        except TmdbError:
            result["failed"] += 1
            continue
        collection_id = (collection or {}).get("id")
        if not collection_id or int(collection_id) in seen:
            result["skipped"] += 1
            continue
        seen.add(int(collection_id))
        try:
            parts = tmdb.request(f"/collection/{int(collection_id)}", {"language": "zh-CN"}).get("parts") or []
        except TmdbError:
            result["failed"] += 1
            continue
        part_ids = {int(part["id"]) for part in parts if part.get("id")}
        if not part_ids:
            result["skipped"] += 1
            continue
        result["collections"] += 1

        with connect(db_path) as connection:
            if apply:
                connection.execute(
                    "UPDATE media_series SET tmdb_collection_id = ?, poster_path = COALESCE(poster_path, ?),"
                    " updated_at = ? WHERE id = ?",
                    (int(collection_id), collection.get("poster_path"), utc_now(), row["id"]),
                )
            orphans = connection.execute(
                "SELECT id, title, year FROM media_items WHERE series_id IS NULL AND tmdb_media_type = 'movie'"
                " AND tmdb_id IN (%s)"
                % ",".join("?" * len(part_ids)),
                tuple(sorted(part_ids)),
            ).fetchall()
            for orphan in orphans:
                result["attached"] += 1
                if len(result["samples"]) < SAMPLE_LIMIT:
                    result["samples"].append(
                        {"series": row["title"], "title": orphan["title"], "year": orphan["year"]}
                    )
                if apply:
                    connection.execute(
                        "UPDATE media_items SET series_id = ?, updated_at = ? WHERE id = ?",
                        (row["id"], utc_now(), orphan["id"]),
                    )
    return result


def describe_series_refresh(result: dict[str, Any], *, applied: bool) -> str:
    lines = [
        f"扫描 {result['series']} 个系列：识别到 TMDB 合集 {result['collections']} 个，"
        f"可补入成员 {result['attached']} 部（跳过 {result['skipped']}，失败 {result['failed']}）"
    ]
    for sample in result["samples"]:
        year = f" ({sample['year']})" if sample.get("year") else ""
        lines.append(f"  {sample['series']} ← {sample['title']!r}{year}")
    if result["attached"] > len(result["samples"]):
        lines.append(f"  …另有 {result['attached'] - len(result['samples'])} 部同类补齐")
    if not applied and result["attached"]:
        lines.append("这是预览，未写库。加 --apply 才会把作品挂到系列上。")
    return "\n".join(lines)
