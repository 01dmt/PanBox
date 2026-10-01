from __future__ import annotations

import concurrent.futures
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from backend.ingestion import get_event, receive
from backend.repository import _format_ingestion_episode_labels, list_ingestion_records
from backend.schema import connect


class IngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "media.db"

    def tearDown(self) -> None:
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
        self.assertEqual(first_event["status"], "imported")
        self.assertEqual([link["status"] for link in first_event["links"]], ["inserted"])

        second = receive(
            {"event_id": "second", "source": {"service": "test"}, "message": {"text": "电影 (2024) https://115.com/s/example"}},
            self.db_path,
        )
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
        self.assertTrue(search.call_args.kwargs["refresh_share"])

    def test_public_channel_username_is_cached_on_ingestion_record(self) -> None:
        with patch("backend.ingestion.fetch_public_channel_avatar", return_value="https://cdn5.telesco.pe/file/avatar.jpg"):
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
        with patch("backend.ingestion.TmdbClient") as client_type:
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


if __name__ == "__main__":
    unittest.main()
