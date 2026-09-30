from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from backend.repository import get_media_filters, get_stats, list_media
from backend.schema import connect, init_db
from backend.server import MediaRequestHandler


class MediaFilterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = init_db(Path(self.temp.name) / "media.db")
        with connect(self.db) as db:
            for mid, year, kind, status, release, genres, countries in [
                (1, 2024, "movie", "matched", None, [{"id": 18, "name": "Drama"}], ["CN", "HK"]),
                (2, 2020, "tv", "review", None, [{"id": 10765, "name": "Sci-Fi & Fantasy"}], ["US"]),
                (3, None, "movie", "pending", "2019-07-01", [], []),
                (4, 2000, "movie", "matched", None, [], []),
                (5, None, "unknown", "error", None, [{"id": 18, "name": "Drama"}], ["CN"]),
                (6, 2024, "tv", "not_found", None, [], []),
            ]:
                db.execute("""INSERT INTO media_items(id,title,normalized_title,year,media_type,tmdb_status,
                    release_date,genres_json,origin_country_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,'now','now')""",
                           (mid, f"Work {mid}", f"work{mid}", year, kind, status, release, json.dumps(genres), json.dumps(countries)))
            for sid, mid, kind, quality, codec, hdr in [
                (1, 1, "115", "1080P", "H.264", "SDR"),
                (2, 1, "ed2k", "2160P", "HEVC", "DV + HDR10"),
                (3, 2, "115", "4K", "X265", "DV + HDR"),
                (4, 3, "115", None, None, None),
                (5, 5, "ed2k", "1080P", "AVC", "HDR10+ + HDR10"),
                (6, 6, "115", "2160P", "h265", "HDR"),
            ]:
                db.execute("""INSERT INTO source_records(id,media_id,source_type,source_key,raw_label,raw_text,
                    url,filename,quality,codec,hdr,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,'now')""",
                           (sid, mid, kind, f"{kind}:{sid}", f"Version {sid}", "fixture", f"https://115.com/s/test{sid}", f"Version{sid}.mkv", quality, codec, hdr))

    def result(self, **filters):
        return list_media(db_path=self.db, **filters)

    def ids(self, **filters):
        return {item["id"] for item in self.result(**filters)["items"]}

    def test_availability_does_not_delete_metadata(self):
        self.assertEqual(self.ids(), {1, 2, 3, 4, 5, 6})
        self.assertEqual(self.ids(availability="available"), {1, 2, 3, 5, 6})
        self.assertEqual(self.ids(availability="missing"), {4})
        self.assertEqual(self.ids(availability="missing", source_type="115"), set())
        self.assertEqual(get_stats(self.db)["media"]["total"], 6)
        self.assertEqual(get_stats(self.db)["media"]["with_sources"], 5)
        self.assertEqual(get_stats(self.db)["media"]["without_sources"], 1)

    def test_source_and_technical_filters_require_the_same_source(self):
        self.assertEqual(self.ids(source_type="115", quality="2160P"), {2, 6})
        self.assertEqual(self.ids(source_type="ed2k", quality="2160P"), {1})
        self.assertEqual(self.ids(source_type="115", codec="h265", hdr="dv"), {2})
        self.assertEqual(self.ids(quality="1080P", hdr="dv"), set())
        self.assertEqual(self.ids(quality="2160P", codec="h264"), set())

    def test_codec_aliases_and_hdr_combinations(self):
        self.assertEqual(self.ids(codec="h265"), {1, 2, 6})
        self.assertEqual(self.ids(codec="h264"), {1, 5})
        self.assertEqual(self.ids(hdr="hdr"), {1, 2, 5, 6})
        self.assertEqual(self.ids(hdr="dv"), {1, 2})
        self.assertEqual(self.ids(hdr="sdr"), {1})

    def test_unknown_technical_data_requires_an_actual_source(self):
        for key in ("quality", "codec", "hdr"):
            self.assertEqual(self.ids(**{key: "unknown"}), {3})
            self.assertEqual(self.ids(availability="missing", **{key: "unknown"}), set())
        with connect(self.db) as db:
            db.execute("UPDATE source_records SET quality='  ',codec='' WHERE id=4")
        self.assertEqual(self.ids(quality="unknown", codec="unknown"), {3})

    def test_year_single_decade_unknown_and_tmdb_date_fallback(self):
        self.assertEqual(self.ids(year="2024"), {1, 6})
        self.assertEqual(self.ids(year="2020s"), {1, 2, 6})
        self.assertEqual(self.ids(year="2010s"), {3})
        self.assertEqual(self.ids(year="2019"), {3})
        self.assertEqual(self.ids(year="unknown"), {5})
        self.assertEqual(self.result(year="2019")["items"][0]["media_year"], 2019)
        with connect(self.db) as db:
            db.execute("UPDATE media_items SET year=2018 WHERE id=3")
        self.assertEqual(self.ids(year="2019"), set())
        self.assertEqual(self.ids(year="2018"), {3})

    def test_genre_country_type_status_and_search_combine(self):
        self.assertEqual(self.ids(genre="18", country="cn"), {1, 5})
        self.assertEqual(self.ids(genre="18", country="CN", year="2024", source_type="ed2k", quality="2160P", media_type="movie", status="matched", query="Work"), {1})
        self.assertEqual(self.ids(country="HK"), {1})
        self.assertEqual(self.ids(genre="10765", media_type="movie"), set())
        self.assertEqual(self.ids(status="pending"), {2, 3, 5, 6})
        self.assertEqual(self.ids(status="unsearched"), {3})

    def test_filter_options_are_cached_local_and_include_fallback_years(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("No remote metadata requests")):
            options = get_media_filters(self.db)
            self.assertEqual(self.ids(genre="18", country="CN"), {1, 5})
        self.assertEqual(options["years"], [2024, 2020, 2019, 2000])
        self.assertEqual(options["countries"], ["CN", "HK", "US"])
        self.assertEqual({entry["id"] for entry in options["genres"]}, {18, 10765})

    def test_malformed_json_and_wrong_shapes_do_not_break_filters(self):
        for value in ("bad-json", "null", '"not an array"', '{}', '["text", 123, null]', '[{"id": "not-numeric"}]'):
            with connect(self.db) as db:
                db.execute("UPDATE media_items SET genres_json=?,origin_country_json=? WHERE id=3", (value, value))
            self.assertEqual(self.ids(genre="18", country="CN"), {1, 5})
            get_media_filters(self.db)

    def test_matching_source_ids_count_and_quality_do_not_include_other_versions(self):
        item = self.result(source_type="115", year="2024", quality="1080P")["items"][0]
        self.assertEqual(item["id"], 1)
        self.assertEqual(item["source_count"], 2)
        self.assertEqual(item["matched_source_count"], 1)
        self.assertEqual(item["matched_source_ids"], [1])
        self.assertEqual(item["matched_qualities"], "1080P")
        unknown = self.result(quality="unknown")["items"][0]
        self.assertIsNone(unknown["matched_qualities"])

    def test_multiple_sources_and_multiple_genres_do_not_duplicate_works(self):
        with connect(self.db) as db:
            db.execute("UPDATE media_items SET genres_json=? WHERE id=1", (json.dumps([{"id": 18}, {"id": 18}]),))
        result = self.result(genre="18", multi_source_only=True)
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["matched_source_count"], 2)

    def test_pagination_total_and_stable_tiebreakers(self):
        with connect(self.db) as db:
            for mid in range(10, 35):
                db.execute("INSERT INTO media_items(id,title,normalized_title,year,created_at,updated_at) VALUES(?, 'Same', 'same', 2024, 'now', 'now')", (mid,))
                db.execute("INSERT INTO source_records(media_id,source_type,source_key,raw_label,raw_text,url,quality,created_at) VALUES(?,'115',?,'same','same','https://115.com/s/same','2160P','now')", (mid, str(mid)))
        first = self.result(year="2024", quality="2160P", page_size=10, page=1, sort="title_asc")
        second = self.result(year="2024", quality="2160P", page_size=10, page=2, sort="title_asc")
        self.assertEqual(first["total"], 27)
        self.assertEqual(first["pages"], 3)
        self.assertEqual([row["id"] for row in first["items"]], list(range(34, 24, -1)))
        self.assertFalse({row["id"] for row in first["items"]} & {row["id"] for row in second["items"]})

    def test_year_sorts_put_unknown_year_last(self):
        for sort, expected in [("year_asc", [2000, 2019, 2020, 2024, 2024, None]),
                               ("year_desc", [2024, 2024, 2020, 2019, 2000, None])]:
            self.assertEqual([item["media_year"] for item in self.result(sort=sort)["items"]], expected)

    def test_invalid_parameters_are_rejected(self):
        for options in ({"availability": "x"}, {"quality": "x"}, {"codec": "x"}, {"hdr": "x"},
                        {"year": "2021s"}, {"year": "9999"}, {"genre": "x' OR 1=1"}, {"country": "CN'"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.result(**options)

    def test_empty_library_filter_options_and_counts(self):
        db = init_db(Path(self.temp.name) / "empty.db")
        self.assertEqual(get_media_filters(db), {"years": [], "genres": [], "countries": []})
        result = list_media(availability="available", genre="18", db_path=db)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["pages"], 1)
        self.assertEqual(get_stats(db)["media"]["with_sources"], 0)

    def test_http_filter_options_and_combined_filters(self):
        handler = type("TestHandler", (MediaRequestHandler,), {"db_path": self.db, "log_message": lambda *args: None})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/api/media/filters") as response:
                self.assertIn(2019, json.load(response)["years"])
            with urlopen(base + "/api/media?availability=available&source=115&quality=2160P&hdr=dv&year=2020s&country=US&genre=10765") as response:
                result = json.load(response)
            self.assertEqual([item["id"] for item in result["items"]], [2])
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/api/media?year=invalid")
            self.assertEqual(error.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
