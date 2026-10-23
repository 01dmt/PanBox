from __future__ import annotations

import concurrent.futures
import inspect
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from backend.importers import ParsedSource
from backend.ingestion import _auto_match_media, _lsp115_person_context, get_event, receive, reprocess_ignored
from backend.repository import _format_ingestion_episode_labels, get_media, import_content, list_ingestion_records, list_media, pending_media_ids
from backend.schema import connect
from backend.share115 import Share115Error
from backend.tmdb import search_media


class IngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "media.db"
        self._probe_patch = patch("backend.ingestion.Share115Client.probe_share")
        self._probe_patch.start()
        self._probe_123_patch = patch("backend.ingestion.Share123Client.probe_share")
        self._probe_123_patch.start()

    def tearDown(self) -> None:
        self._probe_123_patch.stop()
        self._probe_patch.stop()
        self.temp_dir.cleanup()

    def wait_for_status(self, ingestion_id: str) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            event = get_event(ingestion_id, self.db_path)
            if event and event["status"] != "queued":
                return event
            time.sleep(0.01)
        self.fail("ingestion event remained queued")

    def test_link_status_distinguishes_inserted_and_duplicate_sources(self) -> None:
        first = receive(
            {"event_id": "first", "source": {"service": "test"}, "message": {"text": "电影 (2024) https://115.com/s/example"}},
            self.db_path,
        )
        first_event = self.wait_for_status(first["id"])
        self.assertTrue(first["accepted"])
        self.assertEqual(first_event["status"], "imported")
        self.assertEqual([link["status"] for link in first_event["links"]], ["inserted"])

        second = receive(
            {"event_id": "second", "source": {"service": "test"}, "message": {"text": "电影 (2024) https://115.com/s/example"}},
            self.db_path,
        )
        self.assertTrue(second["accepted"])
        second_event = self.wait_for_status(second["id"])
        self.assertEqual(second_event["status"], "duplicate")
        self.assertEqual([link["status"] for link in second_event["links"]], ["duplicate"])

    def test_concurrent_retries_share_one_event(self) -> None:
        body = {
            "event_id": "same-event",
            "source": {"service": "test", "channel_id": "channel"},
            "message": {"text": "并发测试，无支持链接"},
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: receive(body, self.db_path), range(8)))

        ids = {result["id"] for result in results}
        self.assertEqual(len(ids), 1)
        self.assertEqual(sum(not result["duplicate"] for result in results), 1)
        event = self.wait_for_status(next(iter(ids)))
        self.assertEqual(event["status"], "ignored")
        with connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) AS count FROM ingestion_events").fetchone()["count"], 1)

    def test_imported_media_is_sent_to_auto_matcher(self) -> None:
        with patch("backend.ingestion.TmdbClient") as client_type, patch("backend.ingestion.search_media") as search:
            client_type.return_value.configured = True
            search.return_value = {"status": "matched"}
            result = receive(
                {"event_id": "auto-match", "source": {"service": "test"}, "message": {"text": "自动匹配 (2024) https://115.com/s/auto-match"}},
                self.db_path,
            )
            event = self.wait_for_status(result["id"])

        self.assertEqual(event["status"], "imported")
        search.assert_called_once()
        self.assertTrue(search.call_args.kwargs["auto_link"])
        self.assertFalse(search.call_args.kwargs["check_share"])
        # 调用参数必须与 search_media 签名兼容：参数漂移曾被 except 静默吞掉，
        # 导致新入库作品永远停在 pending 且不显示海报。
        inspect.signature(search_media).bind(0, **search.call_args.kwargs)

    def test_successful_retry_clears_stale_ingestion_error(self) -> None:
        with patch("backend.ingestion.expand_resource_pages", return_value="电影 (2024) https://115.com/s/retry"):
            first = receive(
                {"event_id": "retry-error", "source": {"service": "test"}, "message": {"text": "原始消息 https://telegra.ph/retry"}},
                self.db_path,
            )
            event = self.wait_for_status(first["id"])
        with connect(self.db_path) as db:
            db.execute("UPDATE ingestion_events SET status='ignored', error='旧错误' WHERE id=?", (first["id"],))

        with patch("backend.ingestion.expand_resource_pages", return_value="电影 (2024) https://115.com/s/retry"), patch("backend.ingestion.TmdbClient") as client_type:
            client_type.return_value.configured = False
            reprocess_ignored(db_path=self.db_path)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                event = get_event(first["id"], self.db_path)
                if event and event["status"] != "ignored":
                    break
                time.sleep(0.01)
            else:
                self.fail("ingestion retry remained ignored")

        # The source belongs to this very event's own import batch, so a re-run
        # reports the event as imported instead of mislabelling it duplicate.
        self.assertEqual(event["status"], "imported")
        self.assertEqual(event["links"][0]["status"], "inserted")
        self.assertIsNone(event["error"])

    def test_retry_repairs_imported_event_without_media_link(self) -> None:
        first = receive(
            {"event_id": "orphaned-media", "source": {"service": "test"}, "message": {"text": "孤立媒体 (2024) https://115.com/s/orphaned"}},
            self.db_path,
        )
        event = self.wait_for_status(first["id"])
        with connect(self.db_path) as db:
            media_id = db.execute("SELECT media_id FROM ingestion_links WHERE ingestion_id=?", (first["id"],)).fetchone()[0]
            db.execute("DELETE FROM media_items WHERE id=?", (media_id,))
            db.execute("UPDATE ingestion_events SET error='旧错误' WHERE id=?", (first["id"],))

        with patch("backend.ingestion.TmdbClient") as client_type, patch("backend.ingestion.Share115Client.probe_share"):
            client_type.return_value.configured = False
            result = reprocess_ignored(db_path=self.db_path)
            self.assertEqual(result["queued"], 1)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                event = get_event(first["id"], self.db_path)
                with connect(self.db_path) as db:
                    linked_media = db.execute("SELECT media_id FROM ingestion_links WHERE ingestion_id=?", (first["id"],)).fetchone()[0]
                if event and linked_media is not None:
                    break
                time.sleep(0.01)
            else:
                self.fail("orphaned ingestion event was not repaired")

        self.assertEqual(event["status"], "imported")
        self.assertIsNone(event["error"])

    def test_public_channel_username_is_cached_on_ingestion_record(self) -> None:
        with patch("backend.ingestion.cache_channel_avatar", return_value=None), patch("backend.ingestion.fetch_public_channel_avatar", return_value="https://cdn5.telesco.pe/file/avatar.jpg"):
            result = receive(
                {
                    "event_id": "channel-avatar",
                    "source": {
                        "service": "telegram",
                        "channel_id": "123",
                        "channel_name": "示例频道",
                        "channel_username": "ExampleChannel",
                        "message_url": "https://t.me/ExampleChannel/1",
                    },
                    "message": {"text": "头像测试 (2024) https://115.com/s/avatar"},
                },
                self.db_path,
            )
            deadline = time.monotonic() + 3
            event = None
            while time.monotonic() < deadline:
                event = get_event(result["id"], self.db_path)
                if event and event.get("channel_avatar_url"):
                    break
                time.sleep(0.01)

        self.assertEqual(event["channel_username"], "ExampleChannel")
        self.assertEqual(event["message_url"], "https://t.me/ExampleChannel/1")
        self.assertEqual(event["channel_avatar_url"], "https://cdn5.telesco.pe/file/avatar.jpg")

    def test_ingestion_records_include_media_year_and_episode_labels(self) -> None:
        text = (
            "📺 征途 (2026) S01E14 4K\n🔗 链接： https://115cdn.com/s/detail-one?password=x\n"
            "📺 征途 (2026) S02E01 4K\n🔗 链接： https://115cdn.com/s/detail-two?password=x"
        )
        with patch("backend.ingestion.TmdbClient") as client_type, patch("backend.ingestion.Share115Client.probe_share"):
            client_type.return_value.configured = False
            result = receive({"event_id": "record-labels", "source": {"service": "telegram"}, "message": {"text": text}}, self.db_path)
            event = self.wait_for_status(result["id"])

        self.assertEqual(event["status"], "imported")
        record = list_ingestion_records(db_path=self.db_path)["items"][0]
        self.assertEqual(record["media_titles"], ["征途（2026） · S01E14 · S02E01"])
        self.assertEqual(_format_ingestion_episode_labels([
            {"season": 1, "episode": None},
            {"season": 2, "episode": None},
            {"season": 3, "episode": None},
        ]), ["S1-S3"])
        self.assertEqual(_format_ingestion_episode_labels([
            {"season": 1, "episode": None, "season_range": [1, 3]},
        ]), ["S1-S3"])
        self.assertEqual(_format_ingestion_episode_labels([
            {"season": 1, "episode": 1, "episode_range": [1, 25]},
        ]), ["S01E01-E25"])
        self.assertEqual(_format_ingestion_episode_labels([
            {"season": 1, "episode": 1, "episode_range": [1, 25]},
            {"season": 2, "episode": 4},
        ]), ["S01E01-E25", "S02E04"])

    def test_ingestion_records_can_filter_by_status(self) -> None:
        ok = receive(
            {"event_id": "filter-ok", "source": {"service": "test"}, "message": {"text": "电影 (2024) https://115.com/s/filter-ok"}},
            self.db_path,
        )
        self.wait_for_status(ok["id"])
        with patch("backend.ingestion.Share115Client.probe_share", side_effect=Share115Error("密码错误", 4100008)):
            bad = receive(
                {"event_id": "filter-failed", "source": {"service": "test"}, "message": {"text": "电影 (2024) https://115.com/s/filter-bad"}},
                self.db_path,
            )
            bad_event = self.wait_for_status(bad["id"])
        self.assertEqual(bad_event["status"], "failed")

        failed = list_ingestion_records(db_path=self.db_path, status="failed")
        self.assertEqual([item["event_id"] for item in failed["items"]], ["filter-failed"])
        imported = list_ingestion_records(db_path=self.db_path, status="imported")
        self.assertEqual([item["event_id"] for item in imported["items"]], ["filter-ok"])
        self.assertTrue(imported["items"][0]["media_titles"])
        everything = list_ingestion_records(db_path=self.db_path)
        self.assertEqual({item["event_id"] for item in everything["items"]}, {"filter-ok", "filter-failed"})
        # The today counter reports the unfiltered volume in every view.
        self.assertEqual(failed["today_count"], everything["today_count"])
        self.assertEqual(list_ingestion_records(db_path=self.db_path, status="ignored")["items"], [])

    def test_ingestion_records_can_search(self) -> None:
        with patch("backend.ingestion.TmdbClient") as client_type:
            client_type.return_value.configured = False
            alpha = receive(
                {
                    "event_id": "search-alpha",
                    "source": {"service": "test", "channel_name": "阿尔法频道", "channel_username": "alpha_channel"},
                    "message": {"text": "征途 (2026) https://115.com/s/search-alpha"},
                },
                self.db_path,
            )
            self.wait_for_status(alpha["id"])
            beta = receive(
                {
                    "event_id": "search-beta",
                    "source": {"service": "test", "channel_name": "贝塔频道", "channel_username": "beta_channel"},
                    "message": {"text": "兰香如故 (2026) https://115.com/s/search-beta"},
                },
                self.db_path,
            )
            self.wait_for_status(beta["id"])
            gamma = receive(
                {
                    "event_id": "search-gamma",
                    "source": {"service": "test", "channel_name": "伽马频道", "channel_username": "gamma"},
                    "message": {"text": "其它影片 (2026) https://115.com/s/search-gamma"},
                },
                self.db_path,
            )
            self.wait_for_status(gamma["id"])

        def matched(**kwargs):
            return [item["event_id"] for item in list_ingestion_records(db_path=self.db_path, **kwargs)["items"]]

        self.assertEqual(matched(query="阿尔法"), ["search-alpha"])          # channel name
        self.assertEqual(matched(query="beta_channel"), ["search-beta"])     # channel username
        self.assertEqual(matched(query="兰香"), ["search-beta"])             # identified media title
        self.assertEqual(matched(query="search-alpha"), ["search-alpha"])    # extracted share url
        self.assertEqual(matched(query="search-beta"), ["search-beta"])      # raw message text
        self.assertEqual(matched(query="不存在的关键词"), [])
        # LIKE wildcards are escaped: "%" matches nothing here, "_" only the
        # records that literally contain an underscore.
        self.assertEqual(matched(query="%"), [])
        self.assertEqual(matched(query="_"), ["search-alpha", "search-beta"])
        # Search and status filter combine.
        self.assertEqual(matched(query="阿尔法", status="failed"), [])

    def test_ingests_123_share_as_a_cloud_source(self) -> None:
        text = (
            "📺 剧集：兰香如故 (2026) S01E40\n"
            "🎞 版本：2160p TX.WEB-DL H.265 DDP 5.1\n"
            "🔗 链接：点击保存到123云盘 https://www.123865.com/s/Oqtgvd-XY9hh?pwd=YSRG"
        )
        with patch("backend.ingestion.TmdbClient") as client_type:
            client_type.return_value.configured = False
            result = receive({"event_id": "123-share", "source": {"service": "telegram"}, "message": {"text": text}}, self.db_path)
            event = self.wait_for_status(result["id"])

        self.assertEqual(event["status"], "imported")
        self.assertEqual(event["links"][0]["provider"], "123")
        self.assertEqual(event["links"][0]["source_type"], "123")
        self.assertEqual(event["media_ids"], [1])

    def test_telegraph_resource_page_sources_are_ingested_under_page_title(self) -> None:
        page = (
            "👤 陈百强\n📺 陈百强\n"
            "ed2k://|file|陈百强03.zip|20|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/\n"
            "ed2k://|file|陈百强02.zip|30|BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB|/\n"
            "ed2k://|file|陈百强01.zip|40|CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC|/"
        )
        with patch("backend.ingestion.expand_resource_pages", return_value=page), patch("backend.ingestion.TmdbClient") as client_type:
            client_type.return_value.configured = False
            result = receive(
                {"event_id": "telegraph-resource", "source": {"service": "telegram"}, "message": {"text": "👤 陈百强\n📎 查看资源 https://telegra.ph/chen-09-24"}},
                self.db_path,
            )
            event = self.wait_for_status(result["id"])

        self.assertEqual(event["status"], "imported")
        self.assertEqual(len(event["links"]), 3)
        records = list_ingestion_records(db_path=self.db_path)["items"][0]
        self.assertEqual(records["media_titles"], ["陈百强"])

    def test_lsp115_person_heading_owns_expanded_resources(self) -> None:
        expanded_source = "ed2k://|file|本田瞳合集.zip|40|DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD|/"
        message = (
            "👤 本田瞳  🎂 生日：1992-08-04 🏠 出生地：Japan "
            "📚 代表作：代表作一 / 代表作二\n📎 查看资源 https://telegra.ph/hitomi-08-04"
        )

        def expand(text, *, inherited_heading=None):
            self.assertEqual(inherited_heading, "👤 本田瞳\n🎂 生日：1992-08-04\n🏠 出生地：Japan\n📚 代表作：代表作一 / 代表作二")
            return f"{text}\n{inherited_heading}\n{expanded_source}"

        with patch("backend.ingestion.expand_resource_pages", side_effect=expand), patch("backend.ingestion.TmdbClient") as client_type:
            client_type.return_value.configured = False
            result = receive(
                {
                    "event_id": "lsp115-person-resource",
                    "source": {
                        "service": "telegram",
                        "channel_name": "115网盘资源分享频道",
                        "channel_username": "Lsp115",
                        "message_url": "https://t.me/Lsp115/19662",
                    },
                    "message": {"text": message},
                },
                self.db_path,
            )
            event = self.wait_for_status(result["id"])

        self.assertEqual(event["status"], "imported")
        with connect(self.db_path) as db:
            media = db.execute("SELECT title, resource_kind, media_type, resource_metadata_json FROM media_items").fetchone()
            source = db.execute("SELECT resource_class, metadata_json FROM source_records").fetchone()
        self.assertEqual((media["title"], media["resource_kind"], media["media_type"]), ("本田瞳", "person", "unknown"))
        self.assertEqual(source["resource_class"], "person")
        self.assertIn("代表作一 / 代表作二", source["metadata_json"])
        self.assertIsNone(_lsp115_person_context(message, {"channel_username": "other-channel"}))

    def test_lsp115_person_messages_aggregate_under_clean_name(self) -> None:
        source = {"service": "telegram", "channel_name": "115网盘资源分享频道", "channel_username": "Lsp115"}
        messages = [
            ("lsp115-person-a", "👤 浅野心，🎂 生日：1994-01-01 🏠 出生地：日本\n"
                               "ed2k://|file|浅野心合集1.zip|40|" + "A" * 32 + "|/"),
            ("lsp115-person-b", "👤 浅野心\n"
                               "ed2k://|file|浅野心合集2.zip|40|" + "B" * 32 + "|/"),
        ]
        with patch("backend.ingestion.TmdbClient") as client_type:
            client_type.return_value.configured = False
            media_ids = []
            for event_id, text in messages:
                result = receive({"event_id": event_id, "source": source, "message": {"text": text}}, self.db_path)
                event = self.wait_for_status(result["id"])
                self.assertEqual(event["status"], "imported")
                media_ids.append(event["media_ids"])
        with connect(self.db_path) as db:
            rows = db.execute("SELECT title, year, resource_kind FROM media_items").fetchall()
            person_sources = db.execute("SELECT COUNT(*) FROM source_records WHERE resource_class='person'").fetchone()[0]
        # 同一人物的两条消息聚合成一个条目：名字清洗尾随逗号、不取生日年份。
        self.assertEqual([tuple(r) for r in rows], [("浅野心", None, "person")])
        self.assertEqual(person_sources, 2)
        self.assertEqual(media_ids[0], media_ids[1])


class AutoMatchTests(unittest.TestCase):
    """入库自动匹配必须真实调用 search_media；参数漂移曾被静默吞掉，作品永远停在 pending 且无海报。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "media.db"
        import_content("邪不压正 (2018) https://115.com/s/abc?password=abcd", "msg.txt", db_path=self.db_path)
        self.media_id = list_media(db_path=self.db_path)["items"][0]["id"]

    def test_auto_match_links_media_and_stores_poster(self) -> None:
        class FakeTmdb:
            configured = True

            def search(self, item):
                return [{
                    "tmdb_id": 232905, "media_type": "movie", "title": "邪不压正", "score": 0.95,
                    "release_date": "2018-07-13",
                    "payload": {"id": 232905, "title": "邪不压正", "release_date": "2018-07-13",
                                "poster_path": "/yhBc.jpg", "media_type": "movie"},
                    "match_evidence": {"title_exact": True, "primary_title_exact": True, "year_exact": True},
                }]

            def details(self, media_type, tmdb_id):
                return {"id": tmdb_id, "tmdb_id": tmdb_id, "media_type": media_type, "title": "邪不压正",
                        "release_date": "2018-07-13", "poster_path": "/yhBc.jpg", "overview": "北洋年间"}

            def imdb_fallback(self, item, client=None):
                return {"candidates": [], "evidence": None}

        with patch("backend.ingestion.TmdbClient", FakeTmdb):
            result = _auto_match_media({self.media_id}, self.db_path)
        self.assertEqual(result, {self.media_id: "matched"})
        with connect(self.db_path) as db:
            row = db.execute(
                "SELECT tmdb_status, tmdb_id, poster_path FROM media_items WHERE id=?", (self.media_id,)
            ).fetchone()
        self.assertEqual((row["tmdb_status"], row["tmdb_id"], row["poster_path"]), ("matched", 232905, "/yhBc.jpg"))

    def test_auto_match_failure_is_recorded_instead_of_silent(self) -> None:
        with patch("backend.ingestion.TmdbClient") as client_cls, \
                patch("backend.ingestion.search_media", side_effect=RuntimeError("boom")):
            client_cls.return_value.configured = True
            result = _auto_match_media({self.media_id}, self.db_path)
        self.assertEqual(result, {self.media_id: "error"})
        with connect(self.db_path) as db:
            row = db.execute(
                "SELECT tmdb_status, last_tmdb_error FROM media_items WHERE id=?", (self.media_id,)
            ).fetchone()
        self.assertEqual(row["tmdb_status"], "error")
        self.assertIn("boom", row["last_tmdb_error"] or "")

    def person_media_id(self) -> int:
        import_content("person", "person.txt", db_path=self.db_path, parsed_sources=[ParsedSource(
            source_type="115", provider="115", source_key="115:person", title="浅野心", year=None,
            media_type="unknown", raw_label="浅野心", raw_text="👤 浅野心",
            url="https://115.com/s/person", resource_kind="person", metadata={"context": "👤 浅野心"})])
        with connect(self.db_path) as db:
            return int(db.execute("SELECT id FROM media_items WHERE resource_kind='person'").fetchone()[0])

    def test_person_media_is_skipped_by_auto_match(self) -> None:
        person_id = self.person_media_id()
        with patch("backend.ingestion.TmdbClient") as client_cls, \
                patch("backend.ingestion.search_media") as matcher:
            client_cls.return_value.configured = True
            result = _auto_match_media({person_id}, self.db_path)
        matcher.assert_not_called()
        self.assertEqual(result, {person_id: "skipped_person"})
        with connect(self.db_path) as db:
            row = db.execute("SELECT tmdb_status FROM media_items WHERE id=?", (person_id,)).fetchone()
        self.assertEqual(row["tmdb_status"], "pending")

    def test_pending_media_ids_excludes_person_entries(self) -> None:
        person_id = self.person_media_id()
        ids = pending_media_ids(10, self.db_path)
        self.assertIn(self.media_id, ids)
        self.assertNotIn(person_id, ids)

    def test_pending_media_ids_retries_errors_before_review(self) -> None:
        # 网络失败（error）重试有机会成功；review 重跑通常还是 review，
        # 所以 error 必须排在 review 前面，否则小批次补试永远轮不到它。
        with connect(self.db_path) as db:
            db.execute("UPDATE media_items SET tmdb_status='review' WHERE id=?", (self.media_id,))
            db.execute(
                "INSERT INTO media_items (title, normalized_title, media_type, tmdb_status, created_at, updated_at)"
                " VALUES ('乙', '乙', 'movie', 'error', '2026-01-01', '2026-01-01')"
            )
            error_id = int(db.execute("SELECT id FROM media_items WHERE title='乙'").fetchone()[0])
        ids = pending_media_ids(10, self.db_path)
        self.assertLess(ids.index(error_id), ids.index(self.media_id))


if __name__ == "__main__":
    unittest.main()
