from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.repository import (
    get_media,
    get_stats,
    import_content,
    list_media,
    pending_media_ids,
    refresh_metadata_flags,
    save_candidates,
)
from backend.resource_metadata import classify_metadata_pack
from backend.schema import connect, init_db


def ed2k(filename: str, marker: str, size: int = 100) -> str:
    return f"ed2k://|file|{filename}|{size}|{marker * 32}|/"


class MetadataClassifierTests(unittest.TestCase):
    def test_artwork_and_nfo_pack_is_metadata(self) -> None:
        self.assertEqual(
            classify_metadata_pack("毒液", ["fanart.jpg", "poster.jpg", "毒液（系列）.nfo"]),
            (True, "artwork"),
        )

    def test_known_artwork_names_and_episode_thumbs_are_metadata(self) -> None:
        self.assertEqual(classify_metadata_pack("folder jpg", ["folder.jpg"]), (True, "artwork"))
        self.assertEqual(
            classify_metadata_pack("Show", ["season01-poster.jpg", "S01E01-thumb.jpg"]),
            (True, "artwork"),
        )

    def test_subtitle_or_lyrics_only_pack_is_not_metadata(self) -> None:
        self.assertEqual(
            classify_metadata_pack("The Lodger", ["The.Lodger.Cht.srt"]),
            (False, None),
        )
        self.assertEqual(
            classify_metadata_pack("01 九龙公园游泳池 lrc", ["01. 九龙公园游泳池.lrc"]),
            (False, None),
        )
        # 海报/字幕同时存在但没有视频时，也保留字幕资源。
        self.assertEqual(
            classify_metadata_pack("Movie", ["poster.jpg", "Movie.Cht.srt"]),
            (False, None),
        )

    def test_video_or_real_audio_keeps_pack(self) -> None:
        self.assertEqual(classify_metadata_pack("电影", ["poster.jpg", "movie.mkv"]), (False, None))
        self.assertEqual(classify_metadata_pack("专辑", ["cover.jpg", "01.flac"]), (False, None))
        self.assertEqual(classify_metadata_pack("音乐", ["folder.jpg", "01.mp3"]), (False, None))

    def test_sequential_images_are_content_not_artwork(self) -> None:
        self.assertEqual(classify_metadata_pack("写真 120P", ["001.jpg", "002.jpg"]), (False, None))
        self.assertEqual(classify_metadata_pack("电影", ["poster.jpg", "001.jpg"]), (False, None))

    def test_content_title_wins_over_artwork_name(self) -> None:
        self.assertEqual(classify_metadata_pack("漫画 全彩", ["folder.jpg"]), (False, None))
        self.assertEqual(classify_metadata_pack("写真 封面", ["cover.jpg"]), (False, None))

    def test_missing_filename_share_is_unknown(self) -> None:
        self.assertEqual(classify_metadata_pack("某剧 (2026)", [None]), (False, None))
        self.assertEqual(classify_metadata_pack("某剧 (2026)", [""]), (False, None))

    def test_theme_mp3_is_auxiliary_only_with_artwork(self) -> None:
        self.assertEqual(classify_metadata_pack("某剧", ["theme.mp3"]), (False, None))
        self.assertEqual(
            classify_metadata_pack("某剧", ["theme.mp3", "poster.jpg"]),
            (False, None),
        )
        self.assertEqual(
            classify_metadata_pack("某剧", ["theme.mp3", "poster.jpg", "tvshow.nfo"]),
            (True, "artwork"),
        )


class MetadataImportTests(unittest.TestCase):
    def test_artwork_pack_is_hidden_from_pending_and_stats(self) -> None:
        content = "\n".join([
            "📺 毒液（系列） (2020)",
            ed2k("fanart.jpg", "A"),
            ed2k("poster.jpg", "B"),
            ed2k("毒液（系列）.nfo", "C"),
            "",
            "📺 Movie Name (2020)",
            ed2k("Movie.Name.2020.1080p.mkv", "D", 200),
            ed2k("poster.jpg", "E", 201),
        ])
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content(content, "metadata.txt", db_path=db_path)
            stats = get_stats(db_path)["media"]
            self.assertEqual((stats["total"], stats["pending"], stats["metadata"]), (1, 1, 1))
            items = list_media(db_path=db_path)["items"]
            self.assertEqual(len(items), 1)
            self.assertNotEqual(items[0]["title"], "毒液（系列）")
            with connect(db_path) as connection:
                hidden = connection.execute(
                    "SELECT id, is_metadata, metadata_reason FROM media_items WHERE is_metadata = 1"
                ).fetchone()
            self.assertIsNotNone(hidden)
            self.assertEqual(hidden["metadata_reason"], "artwork")
            # Detail remains available for audit; only queue/list views hide it.
            self.assertIsNotNone(get_media(int(hidden["id"]), db_path))
            self.assertEqual(pending_media_ids(10, db_path), [int(items[0]["id"])])

    def test_video_audio_image_content_and_unknown_share_are_kept(self) -> None:
        content = "\n".join([
            "📺 Video Pack (2020)",
            ed2k("Video.Pack.2020.1080p.mkv", "A"),
            ed2k("poster.jpg", "B"),
            "",
            "📺 Music Album (2020)",
            ed2k("01.flac", "C"),
            ed2k("cover.jpg", "D"),
            "",
            "📺 写真 120P (2020)",
            ed2k("001.jpg", "E"),
            ed2k("002.jpg", "F"),
            "",
            "📺 某剧 (2026) https://115.com/s/unknown-share",
        ])
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content(content, "kept.txt", db_path=db_path)
            stats = get_stats(db_path)["media"]
            self.assertEqual(stats["metadata"], 0)
            self.assertEqual(stats["total"], 4)
            self.assertEqual(len(list_media(db_path=db_path)["items"]), 4)

    def test_migration_rebuilds_flags_for_old_database(self) -> None:
        content = "\n".join([
            "📺 毒液（系列） (2020)",
            ed2k("fanart.jpg", "A"),
            ed2k("poster.jpg", "B"),
            ed2k("毒液（系列）.nfo", "C"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content(content, "metadata.txt", db_path=db_path)
            with connect(db_path) as connection:
                connection.execute("UPDATE media_items SET is_metadata = 0, metadata_reason = NULL")
                connection.execute("DELETE FROM schema_migrations WHERE name = 'metadata_packs_v1'")
            init_db(db_path)
            with connect(db_path) as connection:
                row = connection.execute("SELECT is_metadata, metadata_reason FROM media_items").fetchone()
            self.assertEqual((row["is_metadata"], row["metadata_reason"]), (1, "artwork"))

    def test_subtitle_and_lyrics_only_packs_stay_in_queue(self) -> None:
        content = "\n".join([
            "📺 The Lodger (2026)",
            ed2k("The.Lodger.Cht.srt", "A"),
            "",
            "📺 01 九龙公园游泳池 lrc (2026)",
            ed2k("01. 九龙公园游泳池.lrc", "B"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content(content, "subtitles.txt", db_path=db_path)
            stats = get_stats(db_path)["media"]
            self.assertEqual((stats["metadata"], stats["pending"]), (0, 2))
            self.assertEqual(len(list_media(db_path=db_path)["items"]), 2)

    def test_v2_migration_restores_subtitles_without_resetting_status(self) -> None:
        content = "\n".join([
            "📺 The Lodger (2026)",
            ed2k("The.Lodger.Cht.srt", "A"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content(content, "subtitles.txt", db_path=db_path)
            with connect(db_path) as connection:
                connection.execute(
                    "UPDATE media_items SET is_metadata = 1, metadata_reason = 'subtitle', tmdb_status = 'review'"
                )
                connection.execute("DELETE FROM schema_migrations WHERE name = 'metadata_packs_v2'")
            init_db(db_path)
            with connect(db_path) as connection:
                row = connection.execute(
                    "SELECT is_metadata, metadata_reason, tmdb_status FROM media_items"
                ).fetchone()
            self.assertEqual(
                (row["is_metadata"], row["metadata_reason"], row["tmdb_status"]),
                (0, None, "review"),
            )

    def test_gaining_video_returns_pack_to_pending_and_clears_stale_candidates(self) -> None:
        content = "\n".join([
            "📺 毒液（系列） (2020)",
            ed2k("fanart.jpg", "A"),
            ed2k("poster.jpg", "B"),
            ed2k("毒液（系列）.nfo", "C"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content(content, "metadata.txt", db_path=db_path)
            with connect(db_path) as connection:
                media_id = int(connection.execute("SELECT id FROM media_items").fetchone()["id"])
            save_candidates(
                media_id,
                [{"tmdb_id": 4, "media_type": "movie", "title": "候选", "score": 0.8}],
                "review",
                db_path=db_path,
            )
            with connect(db_path) as connection:
                connection.execute(
                    "UPDATE source_records SET filename = 'movie.mkv' WHERE media_id = ?",
                    (media_id,),
                )
                refresh_metadata_flags(connection, {media_id})
                row = connection.execute(
                    "SELECT is_metadata, tmdb_status FROM media_items WHERE id = ?", (media_id,)
                ).fetchone()
                candidates = connection.execute(
                    "SELECT COUNT(*) AS total FROM tmdb_candidates WHERE media_id = ?", (media_id,)
                ).fetchone()["total"]
            self.assertEqual((row["is_metadata"], row["tmdb_status"], candidates), (0, "pending", 0))

    def test_matched_record_is_never_hidden_by_filename_heuristic(self) -> None:
        content = "\n".join([
            "📺 毒液（系列） (2020)",
            ed2k("fanart.jpg", "A"),
            ed2k("poster.jpg", "B"),
            ed2k("毒液（系列）.nfo", "C"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content(content, "metadata.txt", db_path=db_path)
            with connect(db_path) as connection:
                connection.execute("UPDATE media_items SET tmdb_status = 'matched'")
                connection.execute("UPDATE media_items SET is_metadata = 0, metadata_reason = NULL")
                connection.execute("DELETE FROM schema_migrations WHERE name = 'metadata_packs_v1'")
            init_db(db_path)
            with connect(db_path) as connection:
                row = connection.execute("SELECT is_metadata FROM media_items").fetchone()
            self.assertEqual(row["is_metadata"], 0)
            self.assertEqual(get_stats(db_path)["media"]["metadata"], 0)


if __name__ == "__main__":
    unittest.main()
