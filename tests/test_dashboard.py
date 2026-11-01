import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.importers import ParsedSource
from backend.repository import get_dashboard_overview, import_content
from backend.schema import connect, init_db


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


class DashboardOverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "dashboard.db"
        init_db(self.db)
        import_content("fixture", "fixture.txt", db_path=self.db, parsed_sources=[
            ParsedSource(
                source_type="115", provider="115", source_key="115:one", title="星际穿越", year=2014,
                media_type="movie", raw_label="星际穿越", raw_text="星际穿越 https://115.com/s/one",
                url="https://115.com/s/one", file_size=1024,
            ),
            ParsedSource(
                source_type="ed2k", provider="ed2k", source_key="ed2k:two", title="繁花", year=2023,
                media_type="tv", raw_label="繁花", raw_text="繁花 ed2k://|file|test|",
                url="ed2k://|file|test|", file_size=2048,
            ),
        ])
        with connect(self.db) as db:
            rows = db.execute("SELECT id, title FROM media_items ORDER BY id").fetchall()
            self.media_ids = {row["title"]: int(row["id"]) for row in rows}
            db.execute("UPDATE media_items SET tmdb_status='matched' WHERE id=?", (self.media_ids["星际穿越"],))
            db.execute("UPDATE media_items SET tmdb_status='review' WHERE id=?", (self.media_ids["繁花"],))
            now = datetime.now(timezone.utc)
            self.now = now
            events = [
                ("evt-imported", "channel-a", "频道 A", "imported", now - timedelta(minutes=1), [self.media_ids["星际穿越"]]),
                ("evt-duplicate", "channel-a", "频道 A", "duplicate", now - timedelta(minutes=2), []),
                ("evt-failed", "channel-b", "频道 B", "failed", now - timedelta(minutes=3), []),
                ("evt-yesterday", "channel-b", "频道 B", "imported", now - timedelta(days=1), []),
                ("evt-old", "channel-b", "频道 B", "imported", now - timedelta(days=10), []),
            ]
            for index, (event_id, channel_id, channel_name, status, received_at, media_ids) in enumerate(events):
                db.execute(
                    """
                    INSERT INTO ingestion_events
                        (id, event_id, service, channel_id, channel_name, channel_username,
                         raw_text, status, media_ids_json, received_at)
                    VALUES (?, ?, 'telegram', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id, event_id, channel_id, channel_name, channel_name.replace("频道 ", "channel"),
                        "示例正文", status, json.dumps(media_ids), _iso(received_at),
                    ),
                )
                if index == 0:
                    db.execute(
                        "INSERT INTO ingestion_links (ingestion_id, provider, source_type, url, status) VALUES (?, '115', '115', 'https://115.com/s/one', 'inserted')",
                        (event_id,),
                    )
                if index == 2:
                    db.execute(
                        "INSERT INTO ingestion_links (ingestion_id, provider, source_type, url, status) VALUES (?, '115', '115', 'https://115.com/s/gone', 'invalid')",
                        (event_id,),
                    )

    def tearDown(self):
        self.temp.cleanup()

    def test_today_counts_and_success_rate_exclude_pending_work(self):
        overview = get_dashboard_overview(self.db, days=7)
        today = overview["today"]
        self.assertEqual(today["received"], 3)
        self.assertEqual(today["imported"], 1)
        self.assertEqual(today["duplicate"], 1)
        self.assertEqual(today["failed"], 1)
        self.assertEqual(today["processed"], 3)
        self.assertAlmostEqual(today["success_rate"], 1 / 3, places=4)

    def test_yesterday_uses_same_clock_time_and_trend_is_dense(self):
        overview = get_dashboard_overview(self.db, days=7)
        self.assertEqual(overview["yesterday"]["received"], 1)
        self.assertEqual(len(overview["trend"]), 7)
        self.assertEqual(overview["trend"][-1]["date"], overview["today"]["date"])
        self.assertEqual(overview["trend"][-2]["received"], 1)
        self.assertEqual(overview["trend"][0]["received"], 0)

    def test_media_backlog_and_source_providers(self):
        overview = get_dashboard_overview(self.db, days=7)
        self.assertEqual(overview["media"]["total"], 2)
        self.assertEqual(overview["media"]["matched"], 1)
        self.assertEqual(overview["media"]["unmatched"], 1)
        self.assertEqual(overview["media"]["review"], 1)
        self.assertEqual(overview["sources"]["total"], 2)
        self.assertEqual(overview["sources"]["added_today"], 2)
        providers = {row["id"]: row["count"] for row in overview["sources"]["providers"]}
        self.assertEqual(providers, {"115": 1, "ed2k": 1})
        self.assertEqual(overview["backlog"]["failed_events"], 1)
        self.assertEqual(overview["backlog"]["invalid_links"], 1)
        self.assertEqual(overview["backlog"]["total_links"], 2)

    def test_channels_rank_by_volume_and_recent_items_keep_media(self):
        overview = get_dashboard_overview(self.db, days=7)
        counts = {channel["id"]: channel["received"] for channel in overview["channels"]}
        self.assertEqual(counts, {"channelA": 2, "channelB": 2})
        self.assertIsNone(overview["channels"][0]["avatar_url"])
        recent = overview["recent"]
        self.assertEqual(len(recent), 5)
        self.assertEqual(recent[0]["id"], "evt-imported")
        self.assertEqual(recent[0]["media"][0]["title"], "星际穿越")
        self.assertEqual(recent[0]["links"]["inserted"], 1)
        self.assertEqual(recent[2]["links"]["invalid"], 1)

    def test_days_are_bounded(self):
        self.assertEqual(get_dashboard_overview(self.db, days=1)["days"], 7)
        self.assertEqual(get_dashboard_overview(self.db, days=365)["days"], 90)


if __name__ == "__main__":
    unittest.main()
