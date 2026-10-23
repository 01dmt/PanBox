from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.repository import connect, get_media, import_content, list_media, normalize_title
from backend.retitle import describe_refresh, refresh_titles


def seed_legacy_title(db_path: Path, heading: str, *, stored_title: str, status: str = "not_found") -> int:
    """Import a message, then rewrite the title back to the pre-fix (buggy) value."""
    import_content(f"{heading}\n剧情简介：示例\nhttps://115.com/s/example", "test.txt", db_path=db_path)
    media_id = list_media(db_path=db_path)["items"][0]["id"]
    with connect(db_path) as connection:
        connection.execute(
            "UPDATE media_items SET title = ?, normalized_title = ?, tmdb_status = ? WHERE id = ?",
            (stored_title, normalize_title(stored_title), status, media_id),
        )
    return media_id


class RefreshTitlesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "media.db"

    def test_preview_does_not_write(self) -> None:
        media_id = seed_legacy_title(self.db_path, "名称：飞驰人生2（2024）", stored_title="名称：飞驰人生2")
        result = refresh_titles(db_path=self.db_path)
        self.assertEqual(result["changed"], 1)
        self.assertEqual(result["samples"][0]["after"], "飞驰人生2")
        self.assertEqual(get_media(media_id, self.db_path)["title"], "名称：飞驰人生2")
        self.assertIn("--apply", describe_refresh(result, applied=False))

    def test_apply_fixes_title_and_reopens_matching(self) -> None:
        media_id = seed_legacy_title(self.db_path, "名称：飞驰人生2（2024）", stored_title="名称：飞驰人生2")
        refresh_titles(apply=True, db_path=self.db_path)
        item = get_media(media_id, self.db_path)
        self.assertEqual(item["title"], "飞驰人生2")
        self.assertEqual(item["year"], 2024)
        self.assertEqual(item["tmdb_status"], "pending")

    def test_matched_rows_keep_their_title(self) -> None:
        media_id = seed_legacy_title(
            self.db_path, "名称：飞驰人生2（2024）", stored_title="名称：飞驰人生2", status="matched"
        )
        result = refresh_titles(apply=True, db_path=self.db_path)
        self.assertEqual(result["changed"], 0)
        self.assertEqual(get_media(media_id, self.db_path)["title"], "名称：飞驰人生2")

    def test_collection_rows_are_linked_to_a_series(self) -> None:
        # 频道给系列里每一部单独发消息，标题只写系列名；重修要把它们聚到同一条系列。
        import_content("📺 007(系列) (1973)\nhttps://115.com/s/a", "test.txt", db_path=self.db_path)
        import_content("📺 007(系列) (2006)\nhttps://115.com/s/b", "test.txt", db_path=self.db_path)
        with connect(self.db_path) as connection:
            ids = [row["id"] for row in connection.execute("SELECT id FROM media_items ORDER BY id")]
            # 模拟修复前的存量：既没有系列表，也没有归属。
            connection.execute("UPDATE media_items SET series_id = NULL, title = '007'")
            connection.execute("DELETE FROM media_series")
        first, second = ids
        result = refresh_titles(apply=True, db_path=self.db_path)
        self.assertEqual(result["linked"], 2)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT id, series_id FROM media_items WHERE id IN (?, ?) ORDER BY id", (first, second)
            ).fetchall()
            series = connection.execute("SELECT title FROM media_series").fetchall()
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["title"], "007")
        self.assertIsNotNone(rows[0]["series_id"])
        self.assertEqual(rows[0]["series_id"], rows[1]["series_id"])

    def test_plain_rows_are_not_linked_to_a_series(self) -> None:
        seed_legacy_title(self.db_path, "📺 兰香如故 (2026) 全集", stored_title="兰香如故")
        result = refresh_titles(apply=True, db_path=self.db_path)
        self.assertEqual(result["linked"], 0)
        with connect(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_series").fetchone()[0], 0)

    def test_clean_titles_are_not_counted_as_changes(self) -> None:
        seed_legacy_title(self.db_path, "📺 疾速追杀4", stored_title="疾速追杀4")
        result = refresh_titles(apply=True, db_path=self.db_path)
        self.assertEqual(result["changed"], 0)

    def test_limit_bounds_the_scan(self) -> None:
        seed_legacy_title(self.db_path, "名称：飞驰人生2（2024）", stored_title="名称：飞驰人生2")
        seed_legacy_title(self.db_path, "📺 星球大战（系列", stored_title="星球大战（系列")
        result = refresh_titles(limit=1, db_path=self.db_path)
        self.assertEqual(result["scanned"], 1)


if __name__ == "__main__":
    unittest.main()
