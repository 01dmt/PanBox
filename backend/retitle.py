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

from .importers import parse_share_heading, split_episode_suffix
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
        "samples": [], "series_samples": [], "bundle_samples": [], "changed_ids": [],
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
        if details.get("collection") and row["has_filename"]:
            # 系列标题只是归属，单片标题来自发布文件名（视频/音频）；
            # 重放首行不能把「山中艳谭（完整版）」改回「聊斋艳谭」。
            continue
        if row["tmdb_status"] == "matched":
            # 已匹配的作品靠 TMDB 身份说话，标题只影响展示，不为此丢掉关联。
            continue
        if title == row["title"]:
            continue
        result["changed"] += 1
        result["changed_ids"].append(int(row["id"]))
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


def normalize_episode_suffix_titles(
    *,
    apply: bool = False,
    limit: int | None = None,
    media_ids: set[int] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Strip bare episode numbers from TV titles, with corpus context.

    ``牧神记79`` is an episode of ``牧神记``, while ``流浪地球2`` and
    ``银翼杀手2049`` are titles.  A title is only changed when the same base
    title has at least one real TV row; the suffix must also be 19..999, so
    sequel numbers and years are left alone.  Matched rows are never touched.
    """
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, title, media_type, tmdb_status, year
            FROM media_items
            WHERE is_bundle = 0 AND is_metadata = 0 AND resource_kind = 'media'
            ORDER BY id
            """
        ).fetchall()
        matched_ranges = connection.execute(
            """
            SELECT m.title, m.year, MIN(s.episode) AS min_episode, MAX(s.episode) AS max_episode
            FROM media_items m
            JOIN source_records s ON s.media_id = m.id
            WHERE m.media_type = 'tv' AND m.tmdb_status = 'matched'
              AND m.year IS NOT NULL AND s.episode IS NOT NULL
            GROUP BY m.id
            """
        ).fetchall()
    target_ids = {int(value) for value in media_ids} if media_ids is not None else None
    base_tv_titles = {
        str(row["title"] or "")
        for row in rows
        if row["media_type"] == "tv" and row["tmdb_status"] == "matched"
    }
    base_matched_years: dict[str, set[int]] = {}
    for row in rows:
        if row["media_type"] == "tv" and row["tmdb_status"] == "matched" and row["year"]:
            base_matched_years.setdefault(str(row["title"] or ""), set()).add(int(row["year"]))
    base_episode_ranges: dict[str, list[tuple[int, int, int]]] = {}
    for row in matched_ranges:
        base_episode_ranges.setdefault(str(row["title"] or ""), []).append(
            (int(row["min_episode"]), int(row["max_episode"]), int(row["year"]))
        )
    groups: dict[str, list[tuple[Any, int]]] = {}
    scanned = 0
    for row in rows:
        if limit is not None and scanned >= limit:
            break
        scanned += 1
        parsed = split_episode_suffix(str(row["title"] or ""))
        if parsed:
            base, episode = parsed
            groups.setdefault(base, []).append((row, episode))

    pending: list[tuple[int, str, int, int | None]] = []
    for base, entries in groups.items():
        # A single ``机动搜查队404`` must not become an episode just because
        # the suffix is numeric; require the base to have a TV sibling (or an
        # already matched base title, e.g. a new 凡人修仙传173 episode).
        if base not in base_tv_titles and not any(row["media_type"] == "tv" for row, _ in entries):
            continue
        for row, episode in entries:
            media_id = int(row["id"])
            if target_ids is not None and media_id not in target_ids:
                continue
            if row["tmdb_status"] == "matched" or row["media_type"] not in {"tv", "unknown"}:
                continue
            years = base_matched_years.get(base) or set()
            base_year = next(iter(years)) if len(years) == 1 else None
            if base_year is None:
                # Two same-name series (e.g. 斗破苍穹 2017 animation and 2018
                # live action) have different episode coverage.  Only adopt a
                # year when exactly one matched sibling covers this episode.
                covering = {
                    year
                    for first, last, year in base_episode_ranges.get(base, [])
                    if first <= episode <= last
                }
                if len(covering) == 1:
                    base_year = next(iter(covering))
            pending.append((media_id, base, episode, base_year))

    result: dict[str, Any] = {
        "scanned": scanned,
        "changed": len(pending),
        "samples": [
            {"id": media_id, "before": next(row["title"] for row, _ in groups[base] if int(row["id"]) == media_id), "after": base, "episode": episode}
            for media_id, base, episode, _ in pending[:12]
        ],
        "changed_ids": [media_id for media_id, _, _, _ in pending],
    }
    if not apply or not pending:
        return result
    now = utc_now()
    with connect(db_path) as connection:
        for media_id, base, episode, base_year in pending:
            connection.execute(
                """
                UPDATE media_items
                   SET title = ?, normalized_title = ?, media_type = 'tv',
                       year = COALESCE(year, ?),
                       tmdb_status = 'pending', last_tmdb_error = NULL,
                       match_confidence = NULL, match_method = NULL, updated_at = ?
                 WHERE id = ?
                """,
                (base, normalize_title(base), base_year, now, media_id),
            )
            connection.execute(
                """
                UPDATE source_records
                   SET season = COALESCE(season, 1), episode = COALESCE(episode, ?)
                 WHERE media_id = ?
                """,
                (episode, media_id),
            )
            connection.execute("DELETE FROM tmdb_candidates WHERE media_id = ?", (media_id,))
            connection.execute("DELETE FROM imdb_lookups WHERE media_id = ?", (media_id,))
    return result


def repair_episode_base_years(
    *,
    apply: bool = False,
    limit: int | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Fill a missing episode year from an already matched sibling's coverage.

    This is deliberately narrower than using the message date: the year is
    copied from a matched TV entry with the exact same title only when exactly
    one sibling's known episode range covers the target episode.  Two same-name
    series (e.g. 斗破苍穹 2017 animation vs 2018 live action) are left alone if
    both cover the number.
    """
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT m.id, m.title, m.year, m.media_type, m.tmdb_status,
                   MIN(s.episode) AS min_episode, MAX(s.episode) AS max_episode
            FROM media_items m
            JOIN source_records s ON s.media_id = m.id
            WHERE m.tmdb_status != 'matched' AND m.year IS NULL
              AND m.media_type = 'tv' AND m.is_bundle = 0 AND m.is_metadata = 0
              AND m.resource_kind = 'media' AND s.episode IS NOT NULL
            GROUP BY m.id
            ORDER BY m.id
            """
        ).fetchall()
        matched = connection.execute(
            """
            SELECT m.id, m.title, m.year, MIN(s.episode) AS min_episode, MAX(s.episode) AS max_episode
            FROM media_items m
            JOIN source_records s ON s.media_id = m.id
            WHERE m.media_type = 'tv' AND m.tmdb_status = 'matched'
              AND m.year IS NOT NULL AND s.episode IS NOT NULL
            GROUP BY m.id
            ORDER BY m.id
            """
        ).fetchall()
    by_title: dict[str, list[tuple[int, int, int]]] = {}
    for row in matched:
        by_title.setdefault(str(row["title"] or ""), []).append(
            (int(row["min_episode"]), int(row["max_episode"]), int(row["year"]))
        )
    pending: list[tuple[int, str, int]] = []
    scanned = 0
    for row in rows:
        if limit is not None and scanned >= limit:
            break
        scanned += 1
        first = int(row["min_episode"])
        last = int(row["max_episode"])
        years = {
            year
            for min_episode, max_episode, year in by_title.get(str(row["title"] or ""), [])
            if min_episode <= first <= max_episode or min_episode <= last <= max_episode
        }
        if len(years) == 1:
            pending.append((int(row["id"]), str(row["title"] or ""), next(iter(years))))
    result: dict[str, Any] = {
        "scanned": scanned,
        "changed": len(pending),
        "samples": [{"id": media_id, "title": title, "year": year} for media_id, title, year in pending[:12]],
        "changed_ids": [media_id for media_id, _, _ in pending],
    }
    if not apply or not pending:
        return result
    now = utc_now()
    with connect(db_path) as connection:
        for media_id, _, year in pending:
            connection.execute(
                """
                UPDATE media_items
                   SET year = ?, tmdb_status = 'pending', last_tmdb_error = NULL,
                       match_confidence = NULL, match_method = NULL, updated_at = ?
                 WHERE id = ?
                """,
                (year, now, media_id),
            )
            connection.execute("DELETE FROM tmdb_candidates WHERE media_id = ?", (media_id,))
            connection.execute("DELETE FROM imdb_lookups WHERE media_id = ?", (media_id,))
    return result


def describe_episode_year_repair(result: dict[str, Any], *, applied: bool) -> str:
    lines = [f"扫描 {result['scanned']} 条，可按同名剧集集数覆盖补年份 {result['changed']} 条"]
    for sample in result["samples"]:
        lines.append(f"  #{sample['id']}: {sample['title']!r} → {sample['year']}")
    if result["changed"] > len(result["samples"]):
        lines.append(f"  …另有 {result['changed'] - len(result['samples'])} 条同类改动")
    if not applied and result["changed"]:
        lines.append("这是预览，未写库。加 --apply 才会补年份并重置待匹配。")
    return "\n".join(lines)


def describe_episode_suffix_refresh(result: dict[str, Any], *, applied: bool) -> str:
    lines = [f"扫描 {result['scanned']} 条，需把标题尾部数字识别为集数 {result['changed']} 条"]
    for sample in result["samples"]:
        lines.append(f"  #{sample['id']}: {sample['before']!r} → {sample['after']!r}（第 {sample['episode']} 集）")
    if result["changed"] > len(result["samples"]):
        lines.append(f"  …另有 {result['changed'] - len(result['samples'])} 条同类改动")
    if not applied and result["changed"]:
        lines.append("这是预览，未写库。加 --apply 才会改标题、写集数并重置待匹配。")
    return "\n".join(lines)


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
