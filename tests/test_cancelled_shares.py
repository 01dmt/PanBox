from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock
from urllib.request import Request, urlopen

from backend.repository import (
    get_media, get_stats, import_content, link_tmdb, list_media, remove_cancelled_source,
    save_candidates, save_imdb_lookup,
)
from backend.schema import connect
from backend.server import MediaRequestHandler
from backend.share115 import (
    CANCELLED_ERRNO, INVALID_LINK_ERRNO, Share115Client, Share115Error, cleanup_cancelled_shares,
    enrich_media_share_sources, is_cancelled_snapshot,
)
from backend.tmdb import TmdbClient, bulk_match, search_media


class CancelledShareTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "media.db"
        import_content("Film (2020) https://115.com/s/a", "shares", db_path=self.db)
        self.mid = list_media(db_path=self.db)["items"][0]["id"]

    def mark_cancelled(self, *, source_id=None):
        metadata = {"share_snapshot": {"status": "unavailable", "errno": CANCELLED_ERRNO, "error": "分享已取消"}}
        with connect(self.db) as conn:
            if source_id is None:
                source_id = conn.execute("SELECT id FROM source_records WHERE source_type='115' ORDER BY id LIMIT 1").fetchone()[0]
            conn.execute("UPDATE source_records SET metadata_json=? WHERE id=?", (json.dumps(metadata), source_id))
        return source_id

    def client_error(self, code):
        client = Share115Client()
        client.fetch_snapshot = Mock(side_effect=Share115Error("unavailable", code))
        return client

    def test_only_confirmed_cancellation_is_deleted(self):
        for code in [None, 4100008, 4100012, 429]:
            self.assertFalse(is_cancelled_snapshot({"status": "unavailable", "errno": code, "error": "分享已取消"}))
        self.assertFalse(is_cancelled_snapshot({"status": ["unavailable"], "errno": CANCELLED_ERRNO}))
        self.assertFalse(is_cancelled_snapshot({"status": "ok", "errno": CANCELLED_ERRNO}))
        self.assertTrue(is_cancelled_snapshot({"status": "unavailable", "errno": str(CANCELLED_ERRNO)}))

    def test_cached_cancellation_removes_source_empty_work_and_derived_data(self):
        self.mark_cancelled()
        save_candidates(self.mid, [{"tmdb_id": 7, "media_type": "movie", "title": "Film", "score": 0.9}], "review", db_path=self.db)
        save_imdb_lookup(self.mid, {"status": "no_exact_match"}, self.db)
        self.assertEqual(cleanup_cancelled_shares(self.db), {"removed": 1, "media_removed": 1})
        self.assertIsNone(get_media(self.mid, self.db))
        with connect(self.db) as conn:
            for table in ["source_records", "tmdb_candidates", "imdb_lookups"]:
                self.assertEqual(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(cleanup_cancelled_shares(self.db), {"removed": 0, "media_removed": 0})

    def test_valid_alternate_sources_and_ed2k_are_preserved(self):
        import_content("Film (2020) https://115.com/s/b\ned2k://|file|Film.2020.mkv|100|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/", "other", db_path=self.db)
        self.mark_cancelled()
        save_candidates(self.mid, [{"tmdb_id": 7, "media_type": "movie", "title": "Film", "score": 0.9}], "review", db_path=self.db)
        save_imdb_lookup(self.mid, {"status": "review"}, self.db)
        cleanup_cancelled_shares(self.db)
        item = get_media(self.mid, self.db)
        self.assertEqual(item["source_count"], 2)
        self.assertEqual({s["source_type"] for s in item["sources"]}, {"115", "ed2k"})
        self.assertEqual(item["tmdb_status"], "pending")
        self.assertEqual(item["candidates"], [])
        self.assertIsNone(item["imdb_lookup"])

    def test_confirmed_tmdb_metadata_is_preserved_even_without_sources(self):
        link_tmdb(self.mid, {"tmdb_id": 7, "media_type": "movie", "title": "Confirmed"}, 1, "manual", self.db)
        save_imdb_lookup(self.mid, {"status": "located"}, self.db)
        self.mark_cancelled()
        self.assertEqual(cleanup_cancelled_shares(self.db), {"removed": 1, "media_removed": 0})
        item = get_media(self.mid, self.db)
        self.assertEqual((item["tmdb_status"], item["tmdb_id"], item["match_method"]), ("matched", 7, "manual"))
        self.assertEqual(item["source_count"], 0)
        self.assertEqual(item["imdb_lookup"]["status"], "located")

    def test_live_cancellation_is_not_cached(self):
        result = enrich_media_share_sources(self.mid, db_path=self.db, client=self.client_error(CANCELLED_ERRNO))
        self.assertEqual((result["removed"], result["media_removed"], result["errors"]), (1, 1, 0))
        self.assertEqual(get_stats(self.db)["sources"]["total"], 0)
        self.assertIsNone(get_media(self.mid, self.db))

    def test_explicit_invalid_link_removes_source_without_caching_failure(self):
        result = enrich_media_share_sources(self.mid, db_path=self.db, client=self.client_error(INVALID_LINK_ERRNO))
        self.assertEqual((result["removed"], result["media_removed"], result["errors"]), (1, 1, 0))
        self.assertIsNone(get_media(self.mid, self.db))

    def test_legacy_invalid_link_cache_is_cleaned_but_generic_errors_are_not(self):
        self.assertFalse(is_cancelled_snapshot({"status": "error", "errno": 911, "error": "Link invalid"}))
        self.assertFalse(is_cancelled_snapshot({"status": "ok", "errno": INVALID_LINK_ERRNO}))
        with connect(self.db) as conn:
            conn.execute("UPDATE source_records SET metadata_json=?", (json.dumps({
                "share_snapshot": {"status": "error", "errno": str(INVALID_LINK_ERRNO)},
            }),))
        self.assertEqual(cleanup_cancelled_shares(self.db), {"removed": 1, "media_removed": 1})

    def test_wrong_password_and_network_failure_do_not_delete_sources(self):
        for code in [None, 4100008, 4100012]:
            enrich_media_share_sources(self.mid, db_path=self.db, client=self.client_error(code), force=True)
            self.assertEqual(get_media(self.mid, self.db)["source_count"], 1)
            self.assertEqual(cleanup_cancelled_shares(self.db)["removed"], 0)

    def test_cancelled_cache_is_removed_without_refetch_even_when_forced(self):
        self.mark_cancelled()
        client = Share115Client()
        client.fetch_snapshot = Mock(side_effect=AssertionError("must not refetch a cancelled link"))
        result = enrich_media_share_sources(self.mid, db_path=self.db, client=client, force=True)
        self.assertEqual(result["removed"], 1)
        client.fetch_snapshot.assert_not_called()

    def test_a_source_changed_during_fetch_is_not_deleted_or_overwritten(self):
        client = Share115Client()
        def fetch(url):
            with connect(self.db) as conn:
                conn.execute("UPDATE source_records SET metadata_json=?", (json.dumps({"custom": "newer", "share_snapshot": {"status": "ok", "version": 2}}),))
            raise Share115Error("cancelled", CANCELLED_ERRNO)
        client.fetch_snapshot = fetch
        result = enrich_media_share_sources(self.mid, db_path=self.db, client=client)
        self.assertEqual(result["removed"], 0)
        item = get_media(self.mid, self.db)
        self.assertEqual(json.loads(item["sources"][0]["metadata_json"])["custom"], "newer")

    def test_changed_urls_and_ed2k_cannot_be_removed_by_stale_checks(self):
        source = get_media(self.mid, self.db)["sources"][0]
        with connect(self.db) as conn:
            conn.execute("UPDATE source_records SET url='https://115.com/s/repaired' WHERE id=?", (source["id"],))
        result = remove_cancelled_source(source["id"], source["url"], source["metadata_json"], self.db)
        self.assertEqual(result["removed"], 0)
        import_content("ed2k://|file|Film.2020.mkv|100|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/", "file", db_path=self.db)
        source = next(s for s in get_media(self.mid, self.db)["sources"] if s["source_type"] == "ed2k")
        self.mark_cancelled(source_id=source["id"])
        self.assertEqual(cleanup_cancelled_shares(self.db)["removed"], 0)

    def test_removed_work_is_not_sent_to_tmdb_or_imdb(self):
        self.mark_cancelled()
        client = TmdbClient(api_key="test")
        client.search = Mock(side_effect=AssertionError("removed work must not be searched"))
        client.imdb_fallback = Mock(side_effect=AssertionError("removed work must not be searched"))
        result = search_media(self.mid, auto_link=True, db_path=self.db, client=client)
        self.assertEqual(result["status"], "removed")
        self.assertEqual(result["removed_sources"], 1)
        client.search.assert_not_called()
        client.imdb_fallback.assert_not_called()

    def test_bulk_matching_counts_removals_not_errors(self):
        self.mark_cancelled()
        result = bulk_match(20, db_path=self.db, client=TmdbClient(api_key="test"))
        self.assertEqual((result["processed"], result["removed"], result["removed_sources"], result["errors"]), (1, 1, 1, 0))

    def test_api_returns_removed_state_with_null_item(self):
        self.mark_cancelled()
        handler = type("TestHandler", (MediaRequestHandler,), {"db_path": self.db, "log_message": lambda *args: None})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(f"http://127.0.0.1:{server.server_port}/api/media/{self.mid}/candidates", data=b"{}", headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=5) as response:
                result = json.load(response)
            self.assertEqual(result["status"], "removed")
            self.assertIsNone(result["item"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
