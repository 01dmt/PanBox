from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.repository import (
    connect, get_series, import_content, list_media, list_series, merge_media_items, pending_media_ids,
)
from backend.series import refresh_series_collections
from backend.importers import ParsedSource
from backend.schema import init_db


class SeriesGroupingTests(unittest.TestCase):
    """频道给系列里每一部单独发消息、标题只写系列名：作品按单片入库，系列负责聚合。"""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "media.db"
        init_db(self.db_path)

    def _import(self, heading: str, url: str) -> None:
        import_content(f"{heading}\nhttps://115.com/s/{url}", "test.txt", db_path=self.db_path)

    def test_films_of_one_series_are_grouped(self) -> None:
        self._import("📺 007(系列) (1973)", "a")
        self._import("📺 007(系列) (2006)", "b")

        series = list_series(db_path=self.db_path)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["title"], "007")
        self.assertEqual(series[0]["media_count"], 2)

        detail = get_series(series[0]["id"], self.db_path)
        self.assertEqual([item["year"] for item in detail["items"]], [1973, 2006])
        # 作品本身仍是一条独立的影视条目，各自有来源和 TMDB 状态。
        self.assertEqual(len(list_media(db_path=self.db_path)["items"]), 2)

    def test_series_survives_a_second_import_of_the_same_name(self) -> None:
        self._import("📺 黑夜传说 (系列) (2003)", "a")
        self._import("📺 黑夜传说（系列）(2006)", "b")
        with connect(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_series").fetchone()[0], 1)

    def test_plain_titles_do_not_create_a_series(self) -> None:
        self._import("📺 兰香如故 (2026) 全集", "a")
        self.assertEqual(list_series(db_path=self.db_path), [])

    def test_a_whole_series_in_one_share_is_not_matched_as_a_single_film(self) -> None:
        # 一条 115 分享装了整个系列、没有文件名 → 拆不出成员，只能标成整包，不能当单片。
        self._import("📺 角头（系列）", "folder")
        with connect(self.db_path) as connection:
            row = connection.execute("SELECT id, is_bundle FROM media_items").fetchone()
        self.assertEqual(row["is_bundle"], 1)
        self.assertNotIn(int(row["id"]), pending_media_ids(10, self.db_path))

    def test_a_single_film_with_a_filename_is_still_matchable(self) -> None:
        # 同一频道也会一集/一部一条 ED2K：文件名能认出是哪一部，照常参与匹配。
        import_content(
            "📺 007(系列)",
            "test.txt",
            db_path=self.db_path,
            parsed_sources=[ParsedSource(
                source_type="ed2k", provider="ed2k",
                source_key="ed2k:Live.and.Let.Die.1973",
                title="007", year=None, media_type="movie", resource_kind="media",
                raw_label="Live.and.Let.Die.1973.1080p.BluRay.REMUX.mkv",
                raw_text="📺 007(系列)",
                url="ed2k://|file|Live.and.Let.Die.1973.1080p.BluRay.REMUX.mkv|2900000000|abc|/",
                filename="Live.and.Let.Die.1973.1080p.BluRay.REMUX.mkv",
                metadata={"collection": True, "context": "📺 007(系列)"},
            )],
        )
        with connect(self.db_path) as connection:
            row = connection.execute("SELECT id, is_bundle FROM media_items").fetchone()
        self.assertEqual(row["is_bundle"], 0)
        self.assertIn(int(row["id"]), pending_media_ids(10, self.db_path))

    def test_merging_duplicate_works_keeps_the_series_membership(self) -> None:
        # 匹配合并重复作品时如果把 series_id 丢了，系列视图会凭空少掉成员。
        self._import("📺 007(系列) (1962)", "a")
        self._import("📺 007(系列) (1964)", "b")
        with connect(self.db_path) as connection:
            ids = [row["id"] for row in connection.execute("SELECT id FROM media_items ORDER BY id")]
            series_id = connection.execute("SELECT series_id FROM media_items WHERE id=?", (ids[0],)).fetchone()[0]
            connection.execute("UPDATE media_items SET series_id = ? WHERE id = ?", (series_id, ids[1]))
            connection.execute("UPDATE media_items SET series_id = NULL WHERE id = ?", (ids[0],))
            merge_media_items(ids[0], ids[1], connection)
            kept = connection.execute("SELECT series_id FROM media_items WHERE id=?", (ids[0],)).fetchone()[0]
        self.assertEqual(kept, series_id)

    def test_tmdb_collection_fills_missing_members(self) -> None:
        class FakeClient:
            configured = True

            def request(self, path, params=None):
                if path.startswith("/movie/"):
                    return {"belongs_to_collection": {"id": 645, "name": "007", "poster_path": "/c.jpg"}}
                return {"parts": [{"id": 646}, {"id": 647}]}

        self._import("📺 007(系列) (1962)", "a")
        import_content(
            "📺 007：金手指",
            "test.txt",
            db_path=self.db_path,
            parsed_sources=[ParsedSource(
                source_type="115", provider="115", source_key="115:goldfinger",
                title="007：金手指", year=1964, media_type="movie", resource_kind="media",
                raw_label="007：金手指", raw_text="📺 007：金手指", url="https://115.com/s/gold",
            )],
        )
        with connect(self.db_path) as connection:
            member = connection.execute("SELECT id FROM media_items WHERE series_id IS NOT NULL").fetchone()[0]
            orphan = connection.execute("SELECT id FROM media_items WHERE series_id IS NULL").fetchone()[0]
            connection.execute("UPDATE media_items SET tmdb_id=646, tmdb_media_type='movie' WHERE id=?", (member,))
            connection.execute("UPDATE media_items SET tmdb_id=647, tmdb_media_type='movie' WHERE id=?", (orphan,))

        preview = refresh_series_collections(db_path=self.db_path, client=FakeClient())
        self.assertEqual(preview["attached"], 1)
        with connect(self.db_path) as connection:
            self.assertIsNone(connection.execute("SELECT series_id FROM media_items WHERE id=?", (orphan,)).fetchone()[0])

        refresh_series_collections(apply=True, db_path=self.db_path, client=FakeClient())
        with connect(self.db_path) as connection:
            self.assertIsNotNone(connection.execute("SELECT series_id FROM media_items WHERE id=?", (orphan,)).fetchone()[0])
            self.assertEqual(
                connection.execute("SELECT tmdb_collection_id FROM media_series").fetchone()[0], 645
            )

    def test_unknown_series_returns_none(self) -> None:
        self.assertIsNone(get_series(424242, self.db_path))


if __name__ == "__main__":
    unittest.main()
