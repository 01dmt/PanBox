from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from backend.imdb import ImdbClient, ImdbError
from backend.repository import get_media, import_content, link_tmdb, list_media, update_media_fields
from backend.tmdb import TmdbClient, TmdbError, search_media, should_auto_link, matching_candidates, score_candidate


class FakeImdb(ImdbClient):
    def __init__(self, rows=None):
        super().__init__()
        self.rows = rows or []
        self.calls = []

    def suggestions(self, title):
        self.calls.append(title)
        return self.rows


class FakeTmdb(TmdbClient):
    def __init__(self, *, normal=None, detail=None, found=None):
        super().__init__(api_key="test")
        self.normal = normal or []
        self.calls = []
        self.detail = detail or {"id": 55, "title": "本地译名", "original_title": "원제",
                                 "release_date": "2020-05-01", "imdb_id": "tt1234567"}
        self.found = found if found is not None else {"movie_results": [{"id": 55}]}

    def search(self, item):
        self.calls.append("normal")
        if isinstance(self.normal, Exception):
            raise self.normal
        return self.normal

    def request(self, path, params=None):
        self.calls.append((path, params))
        return self.found if path.startswith("/find/") else self.detail


class ImdbTests(unittest.TestCase):
    def row(self, **overrides):
        return {"id": "tt1234567", "l": "Film Name", "y": 2020, "qid": "movie", **overrides}

    def test_exact_title_and_year_only(self):
        imdb = FakeImdb([self.row(), self.row(id="tt1234568", y=2021),
                         self.row(id="tt1234569", l="Film Name 2"), self.row(id="tt1234570", y=None)])
        matches = imdb.exact_matches(["film   name"], 2020)
        self.assertEqual([m["imdb_id"] for m in matches], ["tt1234567"])

    def test_punctuation_subtitles_and_sequel_markers_are_not_discarded(self):
        imdb = FakeImdb([self.row()])
        for title in ["Film Name*", "Film Name 2", "Film Name: Part One", "Film-Name"]:
            self.assertEqual(imdb.exact_matches([title], 2020), [])

    def test_a_chinese_query_returning_english_is_not_an_exact_match(self):
        imdb = FakeImdb([self.row()])
        self.assertEqual(imdb.exact_matches(["中文译名"], 2020), [])

    def test_people_episodes_games_and_invalid_ids_are_not_work_matches(self):
        for values in [{"id": "nm1234567"}, {"id": "tt1"}, {"qid": "tvEpisode"},
                       {"qid": "videoGame"}, {"qid": "podcastSeries"}, {"qid": []}]:
            self.assertEqual(FakeImdb([self.row(**values)]).exact_matches(["Film Name"], 2020), [])

    def test_type_evidence_and_missing_year_are_respected(self):
        imdb = FakeImdb([self.row()])
        self.assertEqual(imdb.exact_matches(["Film Name"], 2020, episodic=True), [])
        self.assertEqual(imdb.exact_matches(["Film Name"], None), [])
        self.assertEqual(len(imdb.calls), 1)

    def test_duplicate_ids_are_deduplicated_and_queries_are_bounded(self):
        imdb = FakeImdb([self.row(), self.row()])
        matches = imdb.exact_matches(["Film Name", "Film.Name", "A", "B", "C"], 2020)
        self.assertEqual(len(matches), 1)
        self.assertEqual(len(imdb.calls), 4)

    def test_suggestion_response_is_parsed_and_cached(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"d": [self.row()]}).encode()
        with patch("backend.imdb.urlopen", return_value=response) as request:
            imdb = ImdbClient()
            self.assertEqual(len(imdb.suggestions("Film/Name")), 1)
            imdb.suggestions("Film/Name")
            self.assertEqual(request.call_count, 1)
            self.assertIn("Film%2FName.json", request.call_args.args[0].full_url)

    def test_invalid_response_is_an_explicit_error(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"<html>unavailable</html>"
        with patch("backend.imdb.urlopen", return_value=response):
            with self.assertRaises(ImdbError):
                ImdbClient().suggestions("Film")

    def test_verified_imdb_id_bridge_can_resolve_different_tmdb_titles(self):
        tmdb = FakeTmdb()
        result = tmdb.imdb_fallback({"title": "Film Name", "year": 2020, "media_type": "movie"}, FakeImdb([self.row()]))
        self.assertEqual(result["evidence"]["status"], "located")
        self.assertTrue(should_auto_link(result["candidates"]))
        self.assertEqual(result["candidates"][0]["tmdb_id"], 55)
        self.assertEqual(result["candidates"][0]["match_evidence"]["imdb_identity"]["imdb_id"], "tt1234567")
        self.assertEqual(tmdb.calls[0], ("/find/tt1234567", {"external_source": "imdb_id", "language": "zh-CN"}))

    def test_missing_year_or_bad_backref_cannot_override_tmdb_rules(self):
        for patch_values, status in [({"release_date": ""}, "tmdb_missing_year"),
                                     ({"imdb_id": "tt9999999"}, "external_id_mismatch"),
                                     ({"id": 99}, "external_id_mismatch")]:
            tmdb = FakeTmdb()
            tmdb.detail.update(patch_values)
            result = tmdb.imdb_fallback({"title": "Film Name", "year": 2020}, FakeImdb([self.row()]))
            self.assertEqual(result["evidence"]["status"], status)
            self.assertEqual(result["candidates"], [])

    def test_verified_imdb_year_can_override_a_different_tmdb_year(self):
        tmdb = FakeTmdb()
        tmdb.detail["release_date"] = "2021-01-01"
        item = {"title": "Film Name", "year": 2020, "media_type": "movie"}
        result = tmdb.imdb_fallback(item, FakeImdb([self.row()]))
        self.assertEqual(result["evidence"]["status"], "located_imdb_year")
        self.assertTrue(should_auto_link(result["candidates"]))
        candidate = result["candidates"][0]
        self.assertEqual(candidate["release_date"], "2021-01-01")
        self.assertEqual(candidate["match_evidence"]["effective_year"], 2020)
        self.assertEqual(candidate["match_evidence"]["tmdb_year"], 2021)
        self.assertTrue(candidate["match_evidence"]["imdb_year_override"])
        self.assertFalse(candidate["match_evidence"]["year_conflict"])
        self.assertEqual(item["year"], 2020)

    def test_stale_or_unverified_imdb_proof_cannot_override_a_year(self):
        tmdb = FakeTmdb()
        tmdb.detail["release_date"] = "2021-01-01"
        item = {"title": "Film Name", "year": 2020, "media_type": "movie"}
        payload = tmdb.imdb_fallback(item, FakeImdb([self.row()]))["candidates"][0]["payload"]
        for changes in [{"verified": False}, {"year_override": False}, {"tmdb_year": 2019},
                        {"year": 2018}, {"title": "Different Film"}, {"tmdb_id": 6},
                        {"imdb_id": "tt7654321"}, {"media_type": "tv"}, {"year": [2020]}]:
            with self.subTest(changes=changes):
                altered = {**payload, "_imdb_identity": {**payload["_imdb_identity"], **changes}}
                candidate = tmdb._candidate(item, altered, "movie")
                self.assertFalse(should_auto_link([candidate]))
                self.assertEqual(matching_candidates([candidate]), [])
        candidate = tmdb._candidate({**item, "year": 2019}, payload, "movie")
        self.assertEqual(matching_candidates([candidate]), [])

    def test_plain_tmdb_matches_still_reject_different_years(self):
        self.assertEqual(score_candidate(local_title="Film Name", local_year=2020, local_type="movie",
                                         candidate_title="Film Name", candidate_original_title="Film Name",
                                         candidate_year=2021, candidate_type="movie"), 0)

    def test_imdb_year_exception_does_not_bypass_other_resource_guards(self):
        tmdb = FakeTmdb()
        tmdb.detail["release_date"] = "2021-01-01"
        item = {"title": "Film Name", "year": 2020, "sources": [{"source_type": "115", "metadata_json": {
            "share_snapshot": {"version": 2, "status": "ok", "complete": False, "files": []},
        }}]}
        result = tmdb.imdb_fallback(item, FakeImdb([self.row()]))
        self.assertEqual(result["evidence"]["status"], "review")
        self.assertFalse(should_auto_link(result["candidates"]))

    def test_no_mapping_multiple_mappings_or_multiple_imdb_ids_do_not_guess(self):
        for found, status in [({}, "no_tmdb_mapping"),
                              ({"movie_results": [{"id": 55}, {"id": 56}]}, "ambiguous_tmdb"),
                              ({"tv_results": [{"id": 55}]}, "type_conflict")]:
            r = FakeTmdb(found=found).imdb_fallback({"title": "Film Name", "year": 2020}, FakeImdb([self.row()]))
            self.assertEqual(r["evidence"]["status"], status)
            self.assertEqual(r["candidates"], [])
        tmdb = FakeTmdb()
        r = tmdb.imdb_fallback({"title": "Film Name", "year": 2020}, FakeImdb([self.row(), self.row(id="tt7654321")]))
        self.assertEqual(r["evidence"]["status"], "ambiguous_imdb")
        self.assertEqual(tmdb.calls, [])

    def test_unknown_or_conflicting_local_year_does_not_query_imdb(self):
        for item in [{"title": "Film Name"}, {"title": "Film Name", "sources": [
                {"filename": "Film.Name.2020.mkv"}, {"filename": "Film.Name.2021.mkv"}]}]:
            imdb = FakeImdb([self.row()])
            self.assertFalse(FakeTmdb().imdb_fallback(item, imdb)["candidates"])
            self.assertEqual(imdb.calls, [])

    def test_tv_backreference_is_checked_using_external_ids(self):
        tmdb = FakeTmdb(found={"tv_results": [{"id": 55}]}, detail={"id": 55, "name": "译名",
                         "first_air_date": "2020-01-01", "external_ids": {"imdb_id": "tt1234567"}})
        r = tmdb.imdb_fallback({"title": "Film Name", "year": 2020, "media_type": "tv"},
                              FakeImdb([self.row(qid="tvSeries")]))
        self.assertEqual(r["evidence"]["status"], "located")
        self.assertEqual(r["candidates"][0]["media_type"], "tv")

    def with_media(self, fn):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"test.db"
            import_content("Film Name (2020) https://115.com/s/test", "test", db_path=db)
            mid = list_media(db_path=db)["items"][0]["id"]
            with patch("backend.tmdb.enrich_media_share_sources"):
                fn(db, mid)

    def test_normal_candidates_even_low_scores_do_not_trigger_imdb(self):
        def run(db, mid):
            imdb = FakeImdb([self.row()])
            normal = [{"tmdb_id": 9, "title": "Something", "media_type": "movie", "release_date": "2020-01-01", "score": 0.1}]
            result = search_media(mid, auto_link=True, db_path=db, client=FakeTmdb(normal=normal), imdb_client=imdb)
            self.assertEqual(result["status"], "review")
            self.assertEqual(imdb.calls, [])
            self.assertIsNone(get_media(mid, db)["imdb_lookup"])
        self.with_media(run)

    def test_tmdb_network_errors_do_not_trigger_imdb(self):
        def run(db, mid):
            imdb = FakeImdb([self.row()])
            with self.assertRaises(TmdbError):
                search_media(mid, db_path=db, client=FakeTmdb(normal=TmdbError("network")), imdb_client=imdb)
            self.assertEqual(imdb.calls, [])
        self.with_media(run)

    def test_positive_fallback_is_linked_and_evidence_is_persisted(self):
        def run(db, mid):
            result = search_media(mid, auto_link=True, db_path=db, client=FakeTmdb(), imdb_client=FakeImdb([self.row()]))
            item = get_media(result["media_id"], db)
            self.assertEqual(item["tmdb_id"], 55)
            self.assertEqual(item["imdb_lookup"]["matches"][0]["imdb_id"], "tt1234567")
            self.assertEqual(item["title"], "Film Name")
            self.assertEqual(item["year"], 2020)
            update_media_fields(mid, {"year": 2021}, db)
            self.assertIsNone(get_media(mid, db)["imdb_lookup"])
        self.with_media(run)

    def test_confirmed_items_are_not_rescraped_or_overwritten(self):
        def run(db, mid):
            link_tmdb(mid, {"tmdb_id": 8, "media_type": "movie", "title": "Manual"}, 1, "manual", db)
            tmdb, imdb = FakeTmdb(), FakeImdb([self.row()])
            search_media(mid, auto_link=True, db_path=db, client=tmdb, imdb_client=imdb)
            self.assertEqual(tmdb.calls, [])
            self.assertEqual(imdb.calls, [])
            self.assertEqual(get_media(mid, db)["tmdb_id"], 8)
        self.with_media(run)

    def test_evidence_survives_a_tmdb_merge(self):
        def run(db, mid):
            link_tmdb(mid, {"tmdb_id": 55, "media_type": "movie", "title": "Existing"}, 1, "manual", db)
            import_content("Temporary Name (2020) https://115.com/s/new", "new", db_path=db)
            new_id = list_media(query="Temporary Name", db_path=db)["items"][0]["id"]
            update_media_fields(new_id, {"title": "Film Name"}, db)
            result = search_media(new_id, auto_link=True, db_path=db, client=FakeTmdb(), imdb_client=FakeImdb([self.row()]))
            self.assertEqual(result["media_id"], mid)
            self.assertIsNotNone(get_media(mid, db)["imdb_lookup"])
            self.assertIsNone(get_media(new_id, db))
        self.with_media(run)

    def test_imdb_canonical_year_survives_a_merge_without_changing_tmdb_date(self):
        def run(db, mid):
            update_media_fields(mid, {"year": 2021}, db)
            link_tmdb(mid, {"tmdb_id": 55, "media_type": "movie", "title": "Existing",
                            "release_date": "2021-01-01"}, 1, "manual", db)
            import_content("Temporary Name (2020) https://115.com/s/new", "new", db_path=db)
            new_id = list_media(query="Temporary Name", db_path=db)["items"][0]["id"]
            update_media_fields(new_id, {"title": "Film Name"}, db)
            tmdb = FakeTmdb()
            tmdb.detail["release_date"] = "2021-01-01"
            result = search_media(new_id, auto_link=True, db_path=db, client=tmdb, imdb_client=FakeImdb([self.row()]))
            kept = get_media(mid, db)
            self.assertEqual(result["media_id"], mid)
            self.assertEqual(kept["year"], 2020)
            self.assertEqual(kept["release_date"], "2021-01-01")
            self.assertEqual(kept["imdb_lookup"]["expected_year"], 2020)
            self.assertTrue(kept["imdb_lookup"]["year_override"])
            self.assertEqual(kept["source_count"], 2)
        self.with_media(run)

    def test_imdb_title_and_id_remain_searchable_after_a_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"test.db"
            import_content("既有片名 (2021) https://115.com/s/first", "first", db_path=db)
            old_id = list_media(db_path=db)["items"][0]["id"]
            link_tmdb(old_id, {"tmdb_id": 55, "media_type": "movie", "title": "本地译名",
                              "release_date": "2021-01-01"}, 1, "manual", db)
            import_content("另一片名 (2020) https://115.com/s/second", "second", db_path=db)
            new_id = list_media(query="另一片名", db_path=db)["items"][0]["id"]
            update_media_fields(new_id, {"title": "Film Name"}, db)
            tmdb = FakeTmdb()
            tmdb.detail["release_date"] = "2021-01-01"
            with patch("backend.tmdb.enrich_media_share_sources"):
                search_media(new_id, auto_link=True, db_path=db, client=tmdb, imdb_client=FakeImdb([self.row()]))
            for query in ["Film Name", "tt1234567"]:
                page = list_media(query=query, db_path=db)
                self.assertEqual(page["total"], 1)
                self.assertEqual(page["items"][0]["id"], old_id)


if __name__ == "__main__":
    unittest.main()
