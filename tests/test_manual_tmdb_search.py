from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.repository import get_media, import_content, list_media, save_candidates
from backend.server import MediaRequestHandler
from backend.tmdb import TmdbClient, TmdbError


class ManualTmdbSearchTests(unittest.TestCase):
    def setUp(self):
        self.client = TmdbClient(api_key="test")
        self.rows = [
            {"id": 10, "media_type": "movie", "title": "Sakura", "release_date": "2023-06-10"},
            {"id": 11, "media_type": "movie", "title": "Cherry Blossom", "release_date": "2020-11-13"},
            {"id": 10, "media_type": "tv", "name": "Sakura", "first_air_date": "2020-01-01"},
            {"id": 12, "media_type": "person", "name": "Sakura"},
            {"id": 13, "media_type": "movie", "title": "Sakura", "release_date": ""},
        ]
        self.client.request = Mock(return_value={"results": self.rows, "total_pages": 3, "total_results": 50})

    def test_manual_results_preserve_order_types_and_different_years_without_scoring(self):
        result = self.client.manual_search(" Sakura ")
        self.assertEqual([(r["tmdb_id"], r["media_type"]) for r in result["results"]], [(10, "movie"), (11, "movie"), (10, "tv")])
        self.assertEqual(result["excluded_undated"], 1)
        self.assertEqual((result["query"], result["page"], result["pages"]), ("Sakura", 1, 3))
        self.assertTrue(all("score" not in row and "payload" not in row for row in result["results"]))
        self.client.request.assert_called_once_with("/search/multi", {"query": "Sakura", "language": "zh-CN", "page": 1, "include_adult": "false"})

    def test_search_type_page_and_adult_options_are_forwarded(self):
        self.client.request.return_value = {"results": [{"id": 1, "name": "Show", "first_air_date": "2025-01-01"}], "total_pages": 8}
        result = self.client.manual_search("Show", media_type="tv", page=2, include_adult=True)
        self.assertEqual(result["results"][0]["media_type"], "tv")
        self.client.request.assert_called_once_with("/search/tv", {"query": "Show", "language": "zh-CN", "page": 2, "include_adult": "true"})

    def test_duplicate_ids_are_deduplicated_within_type_only(self):
        self.rows.extend(self.rows[:3])
        self.assertEqual(len(self.client.manual_search("Sakura")["results"]), 3)

    def test_people_missing_years_and_malformed_rows_are_not_linkable(self):
        self.client.request.return_value = {"results": [None, {"id": True, "title": "Bad"}, {"id": -1, "media_type": "movie"}, *self.rows[3:]], "total_pages": 2}
        result = self.client.manual_search("Sakura")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["excluded_undated"], 1)

    def test_invalid_input_never_sends_a_tmdb_request(self):
        for query, params in [(" ", {}), ("x" * 201, {}), ("Show", {"media_type": "person"}),
                              ("Show", {"page": 0}), ("Show", {"page": 501}), ("Show", {"page": True})]:
            with self.subTest(query=query, params=params), self.assertRaises(ValueError):
                self.client.manual_search(query, **params)
        self.client.request.assert_not_called()

    def test_empty_response_and_max_pages_follow_tmdb_bounds(self):
        self.client.request.return_value = {"results": [], "total_pages": 0}
        self.assertEqual(self.client.manual_search("empty")["pages"], 1)
        self.client.request.return_value = {"results": [], "total_pages": 1000}
        self.assertEqual(self.client.manual_search("big")["pages"], 500)

    def test_network_errors_are_not_an_empty_search_result(self):
        self.client.request.side_effect = TmdbError("TMDB unavailable", 429)
        with self.assertRaises(TmdbError):
            self.client.manual_search("Sakura")
        self.assertEqual(self.client.request.call_count, 1)

    def test_invalid_response_is_a_clear_search_error(self):
        for payload in [None, [], {}, {"results": {}}, {"results": [], "total_pages": "invalid"}]:
            self.client.request.return_value = payload
            with self.subTest(payload=payload), self.assertRaises(TmdbError):
                self.client.manual_search("Sakura")

    def test_movie_search_does_not_return_unexpected_tv_rows(self):
        self.assertEqual([r["tmdb_id"] for r in self.client.manual_search("Sakura", media_type="movie")["results"]], [10, 11])

    def test_public_api_is_read_only_and_does_not_access_shares(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "test.db"
            import_content("Sakura (2020) https://115.com/s/test", "test.txt", db_path=db)
            mid = list_media(db_path=db)["items"][0]["id"]
            save_candidates(mid, [{"tmdb_id": 3, "media_type": "movie", "title": "Kept", "score": 0.9}], "review", db_path=db)
            before = get_media(mid, db)
            handler = type("TestHandler", (MediaRequestHandler,), {"db_path": db, "log_message": lambda *args: None})
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}/api/tmdb/search"
                with patch("backend.server.TmdbClient", return_value=self.client), patch("backend.share115.Share115Client._fetch_page", side_effect=AssertionError("must not access 115")):
                    with urlopen(base + "?q=Sakura&type=tv&page=2&include_adult=true", timeout=5) as response:
                        payload = json.load(response)
                    self.assertEqual(payload["page"], 2)
                    self.assertTrue(payload["include_adult"])
                    self.assertEqual(get_media(mid, db), before)
                    self.assertNotIn("api_key", json.dumps(payload))
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base + "?q=Sakura&page=0", timeout=5)
                    self.assertEqual(error.exception.code, 400)
                    self.client.request.side_effect = TmdbError("unavailable")
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base + "?q=Sakura", timeout=5)
                    self.assertEqual(error.exception.code, 502)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_a_manual_result_can_be_linked_through_the_existing_api(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "test.db"
            import_content("Sakura (2020) https://115.com/s/test", "test.txt", db_path=db)
            mid = list_media(db_path=db)["items"][0]["id"]
            handler = type("TestHandler", (MediaRequestHandler,), {"db_path": db, "log_message": lambda *args: None})
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                details = {"tmdb_id": 10, "media_type": "tv", "title": "Sakura", "release_date": "2020-01-01"}
                with patch("backend.server.TmdbClient", return_value=self.client), patch.object(self.client, "details", return_value=details) as lookup:
                    with urlopen(base + "/api/tmdb/search?q=Sakura", timeout=5) as response:
                        candidate = json.load(response)["results"][-1]
                    request = Request(base + f"/api/media/{mid}/tmdb/link", data=json.dumps({
                        "tmdb_id": candidate["tmdb_id"], "media_type": candidate["media_type"], "method": "manual",
                    }).encode(), headers={"Content-Type": "application/json"})
                    with urlopen(request, timeout=5) as response:
                        linked = json.load(response)
                    lookup.assert_called_once_with("tv", 10)
                    self.assertEqual((linked["tmdb_status"], linked["tmdb_id"], linked["tmdb_media_type"], linked["match_method"]), ("matched", 10, "tv", "manual"))
                    self.assertEqual(linked["source_count"], 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
