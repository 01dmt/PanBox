from __future__ import annotations

import unittest
import json
import sqlite3
import tempfile
from pathlib import Path

from backend.share115 import Share115Client, derive_search_names, infer_media_type, parse_share_url, inspect_resource, enrich_media_share_sources
from backend.repository import import_content, list_media, get_media
from backend.tmdb import collect_search_context


class FakeShare115Client(Share115Client):
    def _fetch_page(self, **kwargs):
        cid = kwargs["cid"]
        if cid == "0":
            return {
                "count": 1,
                "shareinfo": {"share_title": "精英之王"},
                "list": [{"n": "精英之王", "cid": "folder-root", "fc": 0}],
            }
        if cid == "folder-root":
            return {
                "count": 2,
                "list": [
                    {"n": "Big.Bet.S01.2160p.WEB-DL", "cid": "season-1", "fc": 0},
                    {"n": "Big.Bet.S02.2160p.WEB-DL", "cid": "season-2", "fc": 0},
                ],
            }
        return {"count": 0, "list": []}


class Share115Tests(unittest.TestCase):
    def test_single_video_is_evidence_not_a_movie_classification(self) -> None:
        client = Share115Client()
        client._fetch_page = lambda **kwargs: {"count": 1, "list": [{"fid": "1", "fc": 1, "n": "The.Apprentice.2024.mkv"}]}
        snapshot = client.fetch_snapshot("https://115.com/s/test")
        self.assertTrue(snapshot["complete"])
        resource = inspect_resource(snapshot)
        self.assertEqual(resource["kind"], "single_video")
        self.assertEqual(resource["titles"], ["The Apprentice"])
        self.assertIsNone(snapshot["inferred_media_type"])

    def test_a_folder_or_partial_scan_is_not_a_single_movie(self) -> None:
        snapshot = FakeShare115Client().fetch_snapshot("https://115.com/s/test", max_depth=0)
        self.assertFalse(snapshot["complete"])
        self.assertEqual(inspect_resource(snapshot)["kind"], "unknown")

    def test_pagination_and_request_limit_are_reported(self) -> None:
        client = Share115Client()
        calls = []
        def page(**kwargs):
            offset = kwargs["offset"]; calls.append(offset)
            return {"count": 2, "list": [{"fid": str(offset+1), "fc": "1", "n": f"Film.{offset}.mkv"}]}
        client._fetch_page = page
        snapshot = client.fetch_snapshot("https://115.com/s/test", page_size=1)
        self.assertTrue(snapshot["complete"])
        self.assertEqual(calls, [0, 1])
        snapshot = client.fetch_snapshot("https://115.com/s/test", page_size=1, max_requests=1)
        self.assertFalse(snapshot["complete"])
        self.assertIn("request_limit", snapshot["incomplete_reasons"])

    def test_same_named_files_are_not_collapsed_into_one_video(self) -> None:
        client = Share115Client()
        client._fetch_page = lambda **kwargs: {"count": 2, "list": [
            {"fid": "1", "fc": 1, "n": "Film.2024.mkv"}, {"fid": "2", "fc": 1, "n": "Film.2024.mkv"},
        ]}
        snapshot = client.fetch_snapshot("https://115.com/s/test")
        self.assertEqual(len(snapshot["files"]), 2)
        self.assertNotEqual(inspect_resource(snapshot)["kind"], "single_video")

    def test_repeated_pages_do_not_claim_a_complete_scan(self) -> None:
        client = Share115Client()
        client._fetch_page = lambda **kwargs: {"count": 2, "list": [{"fid": "1", "fc": 1, "n": "Film.mkv"}]}
        self.assertFalse(client.fetch_snapshot("https://115.com/s/test", page_size=1)["complete"])

    def test_trailers_and_subtitles_do_not_count_as_main_videos(self) -> None:
        files = [{"name": n, "path": n} for n in ["Film.2024.mkv", "Film.trailer.mp4", "Extras/clip.mkv", "Film.srt"]]
        resource = inspect_resource({"status": "ok", "complete": True, "files": files})
        self.assertEqual(resource["video_names"], ["Film.2024.mkv"])
        self.assertEqual(resource["kind"], "single_video")

    def test_archives_are_not_single_video_evidence(self) -> None:
        for name in ["Film.zip", "Film.iso"]:
            resource = inspect_resource({"status": "ok", "complete": True, "files": [{"name": name, "path": name}]})
            self.assertEqual(resource["kind"], "unknown")

    def test_streaming_labels_after_technical_markers_are_not_resource_titles(self) -> None:
        for name in ["S01E08 - 2160p.WEB-DL Netflix Dual Audio HDR10.mkv", "Moon.Man.2022.2160p.WEB-DL.IMAX.H265.mkv"]:
            resource = inspect_resource({"status": "ok", "complete": True, "files": [{"name": name, "path": name}]})
            self.assertNotIn("mixed", resource["kind"])
            self.assertNotIn("Imax", resource["titles"])
            self.assertNotIn("Netflix Dual Audio", resource["titles"])

    def test_bracketed_episodes_and_season_folders_are_tv_evidence(self) -> None:
        for name in ["[Group]_Show_[S01E12][1080p].mkv", "Show/Season 01/01.mkv", "Show.S01E01-E79.mp4"]:
            resource = inspect_resource({"status": "ok", "complete": True, "files": [{"name": name.split('/')[-1], "path": name}]})
            self.assertEqual(resource["kind"], "tv")

    def test_network_scans_do_not_hold_a_write_lock_or_overwrite_other_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "test.db"
            import_content("Film (2024) https://115.com/s/a\nFilm (2024) https://115.com/s/b", "test", db_path=db)
            mid = list_media(db_path=db)["items"][0]["id"]
            client = Share115Client()
            def fetch(url):
                with sqlite3.connect(db, timeout=0.1) as connection:
                    connection.execute("UPDATE source_records SET metadata_json=json_set(metadata_json,'$.custom','retained')")
                return {"version": 2, "status": "ok", "complete": True, "files": [], "names": []}
            client.fetch_snapshot = fetch
            result = enrich_media_share_sources(mid, db_path=db, client=client)
            self.assertEqual(result["enriched"], 2)
            for source in get_media(mid, db)["sources"]:
                self.assertEqual(json.loads(source["metadata_json"])["custom"], "retained")
            self.assertEqual(enrich_media_share_sources(mid, db_path=db, client=client)["processed"], 0)

    def test_parses_share_code_and_receive_code(self) -> None:
        self.assertEqual(
            parse_share_url("https://115cdn.com/s/example123?password=abcd#"),
            ("example123", "abcd"),
        )
        self.assertEqual(
            parse_share_url("https://www.115.com/s/example123?password=abcd"),
            ("example123", "abcd"),
        )

    def test_snapshot_extracts_dominant_nested_release_name(self) -> None:
        snapshot = FakeShare115Client().fetch_snapshot(
            "https://115.com/s/example123?password=abcd"
        )
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["inferred_media_type"], "tv")
        self.assertIn("Big.Bet.S01.2160p.WEB-DL", snapshot["search_names"])

    def test_collection_does_not_promote_unrelated_child_titles(self) -> None:
        names = ["电影合集", "First.Movie.2020.mkv", "Second.Movie.2021.mkv"]
        self.assertEqual(derive_search_names(["电影合集"], names), ["电影合集"])

    def test_cached_snapshot_names_become_tmdb_search_terms(self) -> None:
        context = collect_search_context(
            {
                "title": "精英之王",
                "year": 2022,
                "media_type": "unknown",
                "sources": [
                    {
                        "raw_label": "精英之王 (2022)",
                        "parsed_year": 2022,
                        "metadata_json": (
                            '{"share_snapshot":{"status":"ok","share_title":"精英之王",'
                            '"search_names":["Big.Bet.S01.2160p.WEB-DL"]}}'
                        ),
                    }
                ],
            }
        )
        self.assertIn("Big Bet", context.titles)
        self.assertIn(2022, context.years)

    def test_season_marker_infers_tv(self) -> None:
        self.assertEqual(infer_media_type(["KPOPPED.S01E01.2160p.mkv"]), "tv")
        self.assertIsNone(infer_media_type(["Blitz.2024.2160p.mkv"]))


if __name__ == "__main__":
    unittest.main()
