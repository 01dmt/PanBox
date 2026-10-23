"""Replay stored channel headings over existing rows: titles and series membership.

Every imported message keeps its first line in
``resource_metadata_json["context"]``. A parsing fix (for example stripping the
``名称：`` label that field-style messages put in front of the real title) can
therefore be replayed over existing rows without re-downloading anything.

Two things are re-derived:

* the title — only for rows that are not matched yet: a good TMDB link is worth
  more than a cosmetic title, and changed rows are reset to ``pending`` so the
  next ``app.py match`` re-searches them with the corrected title;
* the series membership — for every row whose heading says ``📺 007(系列)``.
  Channels post one message per film and only write the series name as the
  title, so the series link is what turns 24 identical ``007`` rows into one
  series entry with 24 films inside it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from .importers import parse_share_heading
from .repository import attach_series, connect, normalize_title, utc_now

SAMPLE_LIMIT = 12


def _stored_headings(db_path: str | Path | None) -> Iterator[tuple[Any, str]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, title, year, media_type, tmdb_status, series_id, is_bundle, resource_metadata_json,
                   EXISTS(
                       SELECT 1 FROM source_records t
                        WHERE t.media_id = media_items.id AND COALESCE(t.filename, '') != ''
                   ) AS has_filename
            FROM media_items
            WHERE resource_metadata_json IS NOT NULL
              AND resource_metadata_json LIKE '%"context"%'
            ORDER BY id
            """
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["resource_metadata_json"] or "{}")
        except ValueError:
            continue
        context = payload.get("context")
        if not isinstance(context, str):
            continue
        heading = context.splitlines()[0].strip() if context.strip() else ""
        if heading:
            yield row, heading


def refresh_titles(
    *,
    apply: bool = False,
    limit: int | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Re-derive titles and series links from the stored heading; ``apply=False`` previews."""
    result: dict[str, Any] = {
        "scanned": 0, "changed": 0, "linked": 0, "bundles": 0, "skipped": 0,
        "samples": [], "series_samples": [], "bundle_samples": [],
    }
    pending_titles: list[tuple[int, str]] = []
    pending_series: list[tuple[int, str]] = []
    pending_bundles: list[int] = []
    for row, heading in _stored_headings(db_path):
        if limit is not None and result["scanned"] >= limit:
            break
        result["scanned"] += 1
        details = parse_share_heading(heading)
        title = str(details.get("title") or "").strip()
        if details.get("resource_kind") == "person":
            # 人物条目按姓名聚合，标题里本来就没有片名可清洗；重算只会丢年份。
            result["skipped"] += 1
            continue
        if not title or title == "未命名":
            result["skipped"] += 1
            continue
        if details.get("collection") and not row["has_filename"] and not row["is_bundle"]:
            # 整包分享：系列标记 + 只有分享链接、没有文件名 → 拆不出成员，不能当单片。
            result["bundles"] += 1
            if len(result["bundle_samples"]) < SAMPLE_LIMIT:
                result["bundle_samples"].append({"id": row["id"], "title": row["title"]})
            pending_bundles.append(int(row["id"]))
        if details.get("collection") and row["series_id"] is None:
            result["linked"] += 1
            if len(result["series_samples"]) < SAMPLE_LIMIT:
                result["series_samples"].append({"id": row["id"], "title": row["title"], "series": title})
            pending_series.append((row["id"], title))
        if row["tmdb_status"] == "matched":
            # 已匹配的作品靠 TMDB 身份说话，标题只影响展示，不为此丢掉关联。
            continue
        if title == row["title"]:
            continue
        result["changed"] += 1
        if len(result["samples"]) < SAMPLE_LIMIT:
            result["samples"].append({"id": row["id"], "before": row["title"], "after": title})
        pending_titles.append((row["id"], title))

    if not apply or not (pending_titles or pending_series or pending_bundles):
        return result

    now = utc_now()
    with connect(db_path) as connection:
        for media_id, title in pending_titles:
            # 只改标题：类型和年份是导入时用整段原文判定的，用首行重算会把
            # 「霍比特人」这类电影翻成 tv，反倒让匹配更差。
            connection.execute(
                """
                UPDATE media_items
                   SET title = ?, normalized_title = ?,
                       tmdb_status = 'pending', last_tmdb_error = NULL,
                       match_confidence = NULL, match_method = NULL, updated_at = ?
                 WHERE id = ?
                """,
                (title, normalize_title(title), now, media_id),
            )
            connection.execute("DELETE FROM tmdb_candidates WHERE media_id = ?", (media_id,))
            connection.execute("DELETE FROM imdb_lookups WHERE media_id = ?", (media_id,))
        for media_id, series_title in pending_series:
            attach_series(connection, media_id, series_title)
        for media_id in pending_bundles:
            connection.execute("UPDATE media_items SET is_bundle = 1, updated_at = ? WHERE id = ?", (now, media_id))
    return result


def describe_refresh(result: dict[str, Any], *, applied: bool) -> str:
    lines = [
        f"扫描 {result['scanned']} 条，标题需更新 {result['changed']} 条，"
        f"需挂到系列 {result['linked']} 条，整包分享 {result['bundles']} 条，跳过 {result['skipped']} 条"
    ]
    for sample in result["samples"]:
        lines.append(f"  #{sample['id']}: {sample['before']!r} → {sample['after']!r}")
    if result["changed"] > len(result["samples"]):
        lines.append(f"  …另有 {result['changed'] - len(result['samples'])} 条同类改动")
    for sample in result["series_samples"]:
        lines.append(f"  #{sample['id']}: {sample['title']!r} 归入系列 {sample['series']!r}")
    for sample in result["bundle_samples"]:
        lines.append(f"  #{sample['id']}: {sample['title']!r} 标为整包分享（不参与单片匹配）")
    if result["linked"] > len(result["series_samples"]):
        lines.append(f"  …另有 {result['linked'] - len(result['series_samples'])} 条同类归属")
    if not applied and (result["changed"] or result["linked"] or result["bundles"]):
        lines.append("这是预览，未写库。加 --apply 才会写标题、重置待匹配并挂系列。")
    return "\n".join(lines)
