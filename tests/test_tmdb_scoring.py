from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from backend.repository import import_content, list_media, get_media
from backend.tmdb import (
    TmdbClient, TmdbError, collect_search_context, dated_candidates, search_media,
    score_candidate, should_auto_link, tmdb_year,
    matching_candidates,
)


class FakeTmdbClient(TmdbClient):
    def __init__(self, responder):
        super().__init__(api_key="test")
        self.responder = responder
        self.calls = []

    def request(self, path, params=None):
        values = dict(params or {})
        self.calls.append((path, values))
        return self.responder(path, values)


class TmdbScoringTests(unittest.TestCase):
    @staticmethod
    def resource_item(title, year, names, complete=True):
        return {"title": title, "year": year, "media_type": "unknown", "sources": [{
            "source_type": "115", "metadata_json": {"share_snapshot": {
                "status": "ok", "version": 2, "complete": complete,
                "files": [{"id": str(i), "name": n, "path": n} for i, n in enumerate(names)],
                "search_names": names,
            }},
        }]}

    def test_share_filename_disambiguates_the_apprentice_from_a_same_named_tv_show(self) -> None:
        movie = {"id": 1182047, "title": "飞黄腾达", "original_title": "The Apprentice", "media_type": "movie", "release_date": "2024-10-09"}
        tv = {"id": 246298, "name": "飞黄腾达", "original_name": "飛黃騰達", "media_type": "tv", "first_air_date": "2024-02-12"}
        def respond(path, params):
            if path == "/tv/246298":
                return {**tv, "name": "The Money Game"}
            return {"results": [movie, tv]}
        client = FakeTmdbClient(respond)
        item = self.resource_item("飞黄腾达", 2024, ["The.Apprentice.2024.2160p.mkv"])
        cs = client.search(item)
        self.assertTrue(should_auto_link(cs))
        self.assertEqual(cs[0]["tmdb_id"], 1182047)
        self.assertTrue(any(p == "/tv/246298" for p, _ in client.calls))
        self.assertTrue(cs[0]["match_evidence"]["resource_title_exact"])

    def test_single_video_does_not_overrule_a_tv_candidate_with_the_same_original_name(self) -> None:
        results = [
            {"id": 1, "title": "同名", "original_title": "Same Name", "media_type": "movie", "release_date": "2024-01-01"},
            {"id": 2, "name": "同名", "original_name": "Same Name", "media_type": "tv", "first_air_date": "2024-01-01"},
        ]
        cs = FakeTmdbClient(lambda p, q: {"results": results}).search(self.resource_item("同名", 2024, ["Same.Name.2024.mkv"]))
        self.assertEqual(len(cs), 2)
        self.assertFalse(should_auto_link(cs))

    def test_actual_episodes_exclude_same_year_movie_candidates(self) -> None:
        results = [
            {"id": 1, "title": "PLUTO", "media_type": "movie", "release_date": "2023-01-01"},
            {"id": 2, "name": "PLUTO", "media_type": "tv", "first_air_date": "2023-10-26"},
        ]
        item = self.resource_item("PLUTO", 2023, ["PLUTO.S01E01.2023.mkv", "PLUTO.S01E02.2023.mkv"])
        cs = FakeTmdbClient(lambda p, q: {"results": results}).search(item)
        self.assertEqual([c["tmdb_id"] for c in cs], [2])
        self.assertTrue(should_auto_link(cs))

    def test_disqualified_movie_does_not_require_alias_verification(self) -> None:
        results = [
            {"id": 1, "title": "测试片", "original_title": "Something Else", "media_type": "movie", "release_date": "2024-01-01"},
            {"id": 2, "name": "测试片", "original_name": "Test Show", "media_type": "tv", "first_air_date": "2024-01-01"},
        ]
        def respond(path, params):
            if path == "/movie/1":
                self.fail("a movie contradicted by actual episode files must not block a TV match")
            return {"results": results}
        item = self.resource_item("测试片", 2024, ["Test.Show.S01E01.2024.mkv"])
        self.assertTrue(should_auto_link(FakeTmdbClient(respond).search(item)))

    def test_incomplete_share_content_stays_for_review(self) -> None:
        result = {"id": 1, "title": "Example", "media_type": "movie", "release_date": "2024-01-01"}
        cs = FakeTmdbClient(lambda p, q: {"results": [result]}).search(self.resource_item("Example", 2024, ["Example.2024.mkv"], complete=False))
        self.assertFalse(should_auto_link(cs))
        self.assertTrue(cs[0]["match_evidence"]["resource_incomplete"])

    def test_conflicting_resources_do_not_force_a_type(self) -> None:
        item = self.resource_item("Example", 2024, ["Example.2024.mkv", "Example.S01E01.2024.mkv"])
        context = collect_search_context(item)
        self.assertTrue(context.resource_conflict)
        result = {"id": 1, "title": "Example", "media_type": "movie", "release_date": "2024-01-01"}
        self.assertFalse(should_auto_link(FakeTmdbClient(lambda p, q: {"results": [result]}).search(item)))

    def test_file_name_conflicting_with_the_share_label_is_not_auto_linked(self) -> None:
        result = {"id": 1, "name": "苍兰诀", "original_name": "苍兰诀", "media_type": "tv", "first_air_date": "2022-01-01"}
        def respond(path, params):
            if path == "/tv/1":
                return {**result, "name": "Love Between Fairy and Devil"}
            return {"results": [result]}
        cs = FakeTmdbClient(respond).search(self.resource_item("苍兰诀", 2022, ["Imperfect.Victim.S01E01.2022.mkv"]))
        self.assertFalse(should_auto_link(cs))
        self.assertTrue(cs[0]["match_evidence"]["resource_name_conflict"])

    @staticmethod
    def language_candidates():
        english = {"tmdb_id": 614040, "media_type": "movie", "title": "Obsessed",
                   "release_date": "2014-01-01", "score": 0.99,
                   "match_evidence": {"title_exact": True}}
        chinese = {"tmdb_id": 269955, "media_type": "movie", "title": "人间中毒",
                   "release_date": "2014-05-14", "score": 0.99,
                   "match_evidence": {"title_exact": True}}
        return english, chinese

    def test_same_year_exact_chinese_title_suppresses_english_candidate(self) -> None:
        english, chinese = self.language_candidates()
        for candidates in [[english, chinese], [chinese, english]]:
            self.assertEqual(matching_candidates(candidates), [chinese])
            self.assertTrue(should_auto_link(candidates))
        self.assertEqual(matching_candidates(matching_candidates([english, chinese])), [chinese])

    def test_english_title_is_kept_without_a_qualified_chinese_candidate(self) -> None:
        english, chinese = self.language_candidates()
        self.assertEqual(matching_candidates([english]), [english])
        for flag in ["type_conflict", "name_conflict", "verification_incomplete", "collection"]:
            rejected = {**chinese, "match_evidence": {"title_exact": True, flag: True}}
            self.assertIn(english, matching_candidates([english, rejected]))
        unrelated = {**chinese, "match_evidence": {"title_exact": False}}
        self.assertIn(english, matching_candidates([english, unrelated]))
        self.assertIn(english, matching_candidates([english, {**chinese, "score": 0.7}]))

    def test_language_preference_does_not_cross_year_or_media_type(self) -> None:
        english, chinese = self.language_candidates()
        self.assertIn(english, matching_candidates([english, {**chinese, "release_date": "2015-01-01"}]))
        self.assertIn(english, matching_candidates([english, {**chinese, "media_type": "tv"}]))

    def test_multiple_chinese_matches_still_require_review(self) -> None:
        english, chinese = self.language_candidates()
        other = {**chinese, "tmdb_id": 3}
        self.assertEqual(matching_candidates([english, chinese, other]), [chinese, other])
        self.assertFalse(should_auto_link([english, chinese, other]))

    def test_explicit_english_id_is_not_discarded_by_language_preference(self) -> None:
        english, chinese = self.language_candidates()
        english["match_evidence"]["explicit_tmdb_id"] = True
        self.assertIn(english, matching_candidates([english, chinese]))

    def test_japanese_or_korean_titles_are_not_treated_as_chinese(self) -> None:
        english, chinese = self.language_candidates()
        for title in ["人間の条件", "인간중독", "2014"]:
            self.assertIn(english, matching_candidates([english, {**chinese, "title": title}]))

    def test_english_punctuation_does_not_disable_language_preference(self) -> None:
        english, chinese = self.language_candidates()
        self.assertEqual(matching_candidates([{**english, "title": "Obsessed…"}, chinese]), [chinese])

    def test_obsessed_search_returns_only_the_preferred_exact_title(self) -> None:
        results = [
            {"id": 614040, "media_type": "movie", "title": "Obsessed", "original_title": "Obsessed", "release_date": "2014-01-01"},
            {"id": 269955, "media_type": "movie", "title": "人间中毒", "original_title": "인간중독", "release_date": "2014-05-14"},
        ]
        cs = FakeTmdbClient(lambda p, q: {"results": results}).search({
            "title": "人间中毒] Obsessed", "year": 2014, "media_type": "movie",
        })
        self.assertEqual([c["tmdb_id"] for c in cs], [269955])
        self.assertTrue(should_auto_link(cs))

    def test_selected_link_target_uses_the_language_filtered_candidates(self) -> None:
        client = FakeTmdbClient(lambda p, q: {"id": 269955, "title": "人间中毒", "release_date": "2014-05-14"})
        english, chinese = self.language_candidates()
        client.search = lambda item: [english, chinese]
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "media.db"
            import_content("人间中毒 (2014) https://115.com/s/test", "test.txt", db_path=db)
            mid = list_media(db_path=db)["items"][0]["id"]
            with patch("backend.tmdb.enrich_media_share_sources"):
                result = search_media(mid, auto_link=True, db_path=db, client=client)
            self.assertEqual(result["candidate"]["tmdb_id"], 269955)
            self.assertEqual(get_media(mid, db)["tmdb_id"], 269955)

    def test_sakura_only_keeps_the_matching_release_year(self) -> None:
        results = [
            {"id": 1262802, "media_type": "movie", "title": "Sakura", "release_date": "2023-06-10"},
            {"id": 593353, "media_type": "movie", "title": "樱", "original_title": "Sakura", "release_date": "2020-11-13"},
        ]
        client = FakeTmdbClient(lambda p, q: {"results": results})
        cs = client.search({"title": "Sakura", "year": 2020, "media_type": "movie"})
        self.assertEqual([c["tmdb_id"] for c in cs], [593353])
        self.assertTrue(should_auto_link(cs))

    def test_bilingual_name_conflict_is_not_resolved_by_year_alone(self) -> None:
        client = TmdbClient(api_key="test")
        candidate = client._candidate(
            {"title": "五十度黑] Fifty Shades Darker", "year": 2016, "media_type": "movie"},
            {"id": 351819, "title": "五十度黑", "original_title": "Fifty Shades of Black",
             "release_date": "2016-01-28"}, "movie", local_titles=["五十度黑", "Fifty Shades Darker"],
        )
        self.assertTrue(candidate["match_evidence"]["name_conflict"])
        self.assertEqual(candidate["score"], 0)
        self.assertFalse(should_auto_link([candidate]))

    def test_official_english_alias_can_resolve_bilingual_names(self) -> None:
        candidate = TmdbClient(api_key="test")._candidate(
            {"title": "测试片", "year": 2020, "media_type": "movie"},
            {"id": 1, "title": "测试片", "original_title": "Titre original",
             "_verified_titles": ["Test Movie"], "release_date": "2020-01-01"}, "movie",
            local_titles=["测试片", "Test Movie"],
        )
        self.assertFalse(candidate["match_evidence"]["name_conflict"])
        self.assertTrue(should_auto_link([candidate]))

    def test_rival_bilingual_alias_is_verified_before_discarding_it(self) -> None:
        a = {"id": 1, "media_type": "movie", "title": "测试片", "original_title": "Test Movie", "release_date": "2020-01-01"}
        b = {"id": 2, "media_type": "movie", "title": "测试片", "original_title": "Titre original", "release_date": "2020-02-01"}
        def respond(path, params):
            if path == "/movie/2":
                return {**b, "title": "Test Movie"}
            return {"results": [a, b]}
        client = FakeTmdbClient(respond)
        cs = client.search({"title": "测试片", "year": 2020, "media_type": "movie",
                            "sources": [{"filename": "Test.Movie.2020.mkv"}]})
        self.assertTrue(any(path == "/movie/2" for path, _ in client.calls))
        self.assertFalse(should_auto_link(cs))

    def test_all_year_mismatches_score_zero_including_three_year_gap(self) -> None:
        for year in [2019, 2021, 2022, 2023, 2024, 1946]:
            with self.subTest(year=year):
                score = score_candidate(local_title="Sakura", local_year=2020, local_type="movie",
                                        candidate_title="Sakura", candidate_original_title="Sakura",
                                        candidate_year=year, candidate_type="movie", popularity=100,
                                        explicit_match=True)
                self.assertEqual(score, 0)

    def test_local_year_takes_precedence_over_secondary_source_year(self) -> None:
        candidate = TmdbClient(api_key="test")._candidate(
            {"title": "Sakura", "year": 2020, "media_type": "movie"},
            {"id": 1, "title": "Sakura", "release_date": "2023-01-01"}, "movie",
            local_years=[2020, 2023],
        )
        self.assertEqual(candidate["score"], 0)
        self.assertTrue(candidate["match_evidence"]["year_conflict"])
        self.assertEqual(matching_candidates([candidate]), [])

    def test_wrong_year_candidates_do_not_block_a_dated_match(self) -> None:
        good = {"score": 0.99, "release_date": "2020-11-13", "match_evidence": {"year_conflict": False}}
        bad = {"score": 0.95, "release_date": "2023-06-10", "match_evidence": {"year_conflict": True}}
        self.assertEqual(matching_candidates([good, bad]), [good])
        self.assertTrue(should_auto_link([good, bad]))

    def test_without_local_year_different_dated_candidates_are_preserved(self) -> None:
        results = [{"id": 1, "title": "Sakura", "media_type": "movie", "release_date": "2020-11-13"},
                   {"id": 2, "title": "Sakura", "media_type": "movie", "release_date": "2023-06-10"}]
        client = FakeTmdbClient(lambda p, q: {"results": results})
        cs = client.search({"title": "Sakura", "year": None, "media_type": "movie"})
        self.assertEqual(len(cs), 2)
        self.assertFalse(should_auto_link(cs))

    def test_tmdb_year_requires_a_year_in_tmdb_metadata(self) -> None:
        for value in [None, "", " ", "unknown", "0000-00-00", "2020unknown", 2020]:
            with self.subTest(value=value):
                self.assertIsNone(tmdb_year({"release_date": value, "year": 2020}))
        self.assertEqual(tmdb_year({"release_date": "2020-05-01"}), 2020)
        self.assertEqual(tmdb_year({"first_air_date": "1899-01-01"}), 1899)
        self.assertEqual(tmdb_year({"release_date": "2020"}), 2020)

    def test_undated_search_results_are_excluded_for_movies_and_tv(self) -> None:
        for kind, field in [("movie", "release_date"), ("tv", "first_air_date")]:
            with self.subTest(kind=kind):
                results = [{"id": 1, "media_type": kind, "title": "Example", field: ""},
                           {"id": 2, "media_type": kind, "title": "Example", field: "2020-01-01"}]
                client = FakeTmdbClient(lambda p, q: {"results": results})
                cs = client.search({"title": "Example", "year": 2020, "media_type": kind})
                self.assertEqual([c["tmdb_id"] for c in cs], [2])
                self.assertTrue(should_auto_link(cs))

    def test_undated_candidates_do_not_block_the_margin(self) -> None:
        dated = {"tmdb_id": 2, "score": 0.95, "release_date": "2020-01-01"}
        undated = {"tmdb_id": 1, "score": 0.99, "release_date": None}
        self.assertEqual(dated_candidates([undated, dated]), [dated])
        self.assertTrue(should_auto_link([undated, dated]))
        self.assertTrue(should_auto_link([dated, {**undated, "score": 0.94}]))
        self.assertFalse(should_auto_link([undated]))

    def test_direct_id_lookup_cannot_link_an_undated_tmdb_entry(self) -> None:
        for kind in ["movie", "tv"]:
            with self.subTest(kind=kind):
                client = FakeTmdbClient(lambda p, q: {"id": 1, "title": "Example"})
                with self.assertRaises(TmdbError):
                    client.details(kind, 1)

    def test_filtering_and_selected_link_target_stay_consistent(self) -> None:
        client = FakeTmdbClient(lambda p, q: {"id": 2, "title": "Example", "release_date": "2020-01-01"})
        client.search = lambda item: [
            {"tmdb_id": 3, "media_type": "movie", "title": "Example", "score": 0.99,
             "release_date": "2023-01-01", "match_evidence": {"year_conflict": True}},
            {"tmdb_id": 1, "media_type": "movie", "title": "Example", "score": 0.99},
            {"tmdb_id": 2, "media_type": "movie", "title": "Example", "score": 0.95,
             "release_date": "2020-01-01"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "media.db"
            import_content("Example (2020) https://115.com/s/test", "test.txt", db_path=db)
            mid = list_media(db_path=db)["items"][0]["id"]
            with patch("backend.tmdb.enrich_media_share_sources"):
                result = search_media(mid, auto_link=True, db_path=db, client=client)
            self.assertEqual(result["candidate"]["tmdb_id"], 2)
            self.assertEqual(get_media(mid, db)["tmdb_id"], 2)

    def test_only_undated_results_leave_no_stored_candidates(self) -> None:
        client = FakeTmdbClient(lambda p, q: {"results": [{"id": 1, "media_type": "movie", "title": "Example"}]})
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "media.db"
            import_content("Example (2020) https://115.com/s/test", "test.txt", db_path=db)
            mid = list_media(db_path=db)["items"][0]["id"]
            with patch("backend.tmdb.enrich_media_share_sources"), patch("backend.tmdb.ImdbClient") as imdb:
                imdb.return_value.exact_matches.return_value = []
                result = search_media(mid, auto_link=True, db_path=db, client=client)
            self.assertEqual(result["status"], "not_found")
            self.assertEqual(get_media(mid, db)["candidates"], [])

    def test_joined_word_fallback_finds_black_swan(self) -> None:
        result = {"id": 44214, "media_type": "movie", "title": "黑天鹅",
                  "original_title": "Black Swan", "release_date": "2010-12-03"}
        client = FakeTmdbClient(lambda path, params: {
            "results": [result] if params.get("query", "").casefold() == "black swan" else [],
        })
        candidates = client.search({"title": "Blackswan", "year": 2010, "media_type": "movie",
                                    "sources": [{"filename": "Blackswan.2010.BluRay.REMUX.AVC.mkv"}]})
        self.assertEqual(candidates[0]["tmdb_id"], 44214)
        self.assertTrue(should_auto_link(candidates))
        self.assertFalse(any(params.get("query", "").lower() == "remux" for _, params in client.calls))

    def test_split_search_does_not_accept_a_different_title(self) -> None:
        result = {"id": 4, "title": "A Different Film", "release_date": "2010-01-01"}
        client = FakeTmdbClient(lambda path, params: {
            "results": [result] if params.get("query", "").casefold() == "black swan" else [],
        })
        candidates = client.search({"title": "Blackswan", "year": 2010, "media_type": "movie"})
        self.assertFalse(should_auto_link(candidates))

    def test_split_search_does_not_auto_link_a_similarly_named_sequel(self) -> None:
        result = {"id": 8, "title": "Black Swan 2", "release_date": "2010-01-01"}
        client = FakeTmdbClient(lambda path, params: {
            "results": [result] if params.get("query", "").casefold() == "black swan" else [],
        })
        candidates = client.search({"title": "Blackswan", "year": 2010, "media_type": "movie"})
        self.assertTrue(candidates)
        self.assertTrue(candidates[0]["match_evidence"]["derived_only"])
        self.assertFalse(should_auto_link(candidates))

    def test_official_alias_still_requires_year_and_disambiguation(self) -> None:
        score = score_candidate(local_title="Her Private Life", local_year=2019, local_type="tv",
                                candidate_title="她的私生活", candidate_original_title=None,
                                candidate_year=2001, candidate_type="tv", candidate_titles=["Her Private Life"])
        self.assertLess(score, 0.90)
        self.assertFalse(should_auto_link([
            {"score": 0.99, "release_date": "2019-01-01"},
            {"score": 0.99, "release_date": "2019-01-01"},
        ]))

    def test_english_title_is_cached_per_type_and_id(self) -> None:
        client = FakeTmdbClient(lambda p, q: {"id": 7, "name": "Example"})
        self.assertEqual(client.translated_titles("tv", 7), ["Example"])
        self.assertEqual(client.translated_titles("tv", 7), ["Example"])
        self.assertEqual(len(client.calls), 1)
        client.translated_titles("movie", 7)
        self.assertEqual(len(client.calls), 2)

    def test_collection_is_not_linked_to_a_single_film_by_an_alias(self) -> None:
        client = FakeTmdbClient(lambda p, q: {"results": []})
        candidate = client._candidate(
            {"title": "生化危机（系列）", "year": None, "media_type": "unknown"},
            {"id": 1, "title": "Resident Evil", "release_date": "2026-09-16",
             "_verified_titles": ["生化危机（系列）"], "popularity": 100}, "movie",
        )
        self.assertTrue(candidate["match_evidence"]["primary_title_exact"])
        self.assertFalse(should_auto_link([candidate]))

    def test_year_mismatch_or_missing_year_needs_review(self) -> None:
        for date in ["2011-01-01", ""]:
            with self.subTest(date=date):
                candidate = TmdbClient(api_key="test")._candidate(
                    {"title": "Black Swan", "year": 2010, "media_type": "movie"},
                    {"id": 7, "title": "Black Swan", "release_date": date}, "movie",
                )
                self.assertFalse(should_auto_link([candidate]))

    def test_episode_filename_does_not_auto_link_to_same_named_movie(self) -> None:
        movie = {"id": 7, "media_type": "movie", "title": "The Heir", "release_date": "2026-01-01"}
        client = FakeTmdbClient(lambda p, q: {"results": [movie]})
        candidates = client.search({"title": "The Heir", "year": 2026, "media_type": "tv",
                                    "sources": [{"filename": "The.Heir.S01E01.2026.1080p.mkv"}]})
        self.assertTrue(candidates[0]["match_evidence"]["type_conflict"])
        self.assertFalse(should_auto_link(candidates))

    def test_translated_english_title_recovers_a_low_scoring_tv_candidate(self) -> None:
        tv = {"id": 87553, "media_type": "tv", "name": "她的私生活",
              "original_name": "그녀의 사생활", "first_air_date": "2019-04-10"}
        movie = {"id": 503366, "media_type": "movie", "title": "Her Private Life",
                 "release_date": "1929-08-25"}
        def respond(path, params):
            if path == "/tv/87553":
                return {**tv, "name": "Her Private Life"}
            return {"results": [tv, movie]}
        client = FakeTmdbClient(respond)
        candidates = client.search({"title": "Her Private Life", "year": None, "media_type": "tv"})
        self.assertEqual(candidates[0]["tmdb_id"], 87553)
        self.assertTrue(should_auto_link(candidates))
        self.assertIn("Her Private Life", candidates[0]["match_evidence"]["verified_titles"])

    def test_official_alternative_title_and_source_alias_can_match(self) -> None:
        tv = {"id": 112486, "media_type": "tv", "name": "赌命为王",
              "original_name": "카지노", "first_air_date": "2022-12-21"}
        def respond(path, params):
            if path == "/tv/112486":
                return {**tv, "alternative_titles": {"results": [{"title": "Big Bet"}]}}
            return {"results": [tv] if params.get("query") == "Big Bet" else []}
        client = FakeTmdbClient(respond)
        candidates = client.search({"title": "精英之王", "year": 2022, "media_type": "tv",
                                    "sources": [{"filename": "Big.Bet.S01.2160p.mkv"}]})
        self.assertEqual(candidates[0]["tmdb_id"], 112486)
        self.assertTrue(should_auto_link(candidates))
        self.assertTrue(any(p == "/search/tv" and q.get("query") == "Big Bet"
                            and q.get("first_air_date_year") == 2022 for p, q in client.calls))

    def test_translation_failure_preserves_candidates_for_review(self) -> None:
        tv = {"id": 1, "media_type": "tv", "name": "她的私生活", "first_air_date": "2019-04-10"}
        def respond(path, params):
            if path == "/tv/1":
                raise TmdbError("timeout")
            return {"results": [tv]}
        candidates = FakeTmdbClient(respond).search({"title": "Her Private Life", "media_type": "tv"})
        self.assertTrue(candidates)
        self.assertTrue(candidates[0]["match_evidence"]["verification_incomplete"])
        self.assertFalse(should_auto_link(candidates))

    def test_title_digits_do_not_become_release_years(self) -> None:
        context = collect_search_context({"title": "Cold War 1994", "year": 2026,
                                          "sources": [{"filename": "Cold.War.1994.2026.1080p.mkv"}]})
        self.assertEqual(context.years, [2026])
        self.assertEqual(context.terms[0].title, "Cold War 1994")
        self.assertEqual(collect_search_context({"title": "使命1915"}).years, [])

    def test_event_year_can_be_an_alias_without_replacing_the_full_title(self) -> None:
        context = collect_search_context({"title": "2025bilibili跨年晚会·最美的夜", "year": 2025})
        self.assertEqual(context.terms[0].title, "2025bilibili跨年晚会·最美的夜")
        self.assertIn("bilibili跨年晚会·最美的夜", context.titles)
        self.assertEqual(context.years, [2025])

    def test_spaced_and_joined_aliases_are_both_searchable(self) -> None:
        context = collect_search_context({"title": "Blackswan", "year": 2010,
                                          "sources": [{"filename": "Black.Swan.2010.mkv"}]})
        self.assertEqual([t.title for t in context.terms], ["Blackswan", "Black Swan"])

    def test_only_technical_input_does_not_trigger_network_requests(self) -> None:
        client = FakeTmdbClient(lambda p, q: self.fail("technical words must not be searched"))
        self.assertEqual(client.search({"title": "1080p", "sources": [{"filename": "REMUX.mkv"}]}), [])

    def test_year_filtered_first_result_alone_is_not_strong_evidence(self) -> None:
        score = score_candidate(local_title="Unknown Film", local_year=2020, local_type="movie",
                                candidate_title="Something Else", candidate_original_title=None,
                                candidate_year=2020, candidate_type="movie",
                                search_hits=[{"year_filtered": True, "year": 2020, "rank": 0}])
        self.assertLess(score, 0.90)

    def test_similar_chinese_title_is_not_promoted_by_search_rank(self) -> None:
        score = score_candidate(local_title="小女子", local_year=2022, local_type="tv",
                                candidate_title="福星小子", candidate_original_title="うる星やつら",
                                candidate_year=2022, candidate_type="tv", popularity=100,
                                search_hits=[{"year_filtered": True, "year": 2022, "rank": 0}])
        self.assertLess(score, 0.90)

    def test_exact_title_and_year_scores_high(self) -> None:
        score = score_candidate(
            local_title="繁花",
            local_year=2023,
            local_type="tv",
            candidate_title="繁花",
            candidate_original_title="Blossoms Shanghai",
            candidate_year=2023,
            candidate_type="tv",
            popularity=30,
        )
        self.assertGreaterEqual(score, 0.95)

    def test_wrong_media_type_and_year_are_penalized(self) -> None:
        good = score_candidate(
            local_title="同名作品",
            local_year=2024,
            local_type="movie",
            candidate_title="同名作品",
            candidate_original_title=None,
            candidate_year=2024,
            candidate_type="movie",
        )
        bad = score_candidate(
            local_title="同名作品",
            local_year=2024,
            local_type="movie",
            candidate_title="同名作品",
            candidate_original_title=None,
            candidate_year=2010,
            candidate_type="tv",
        )
        self.assertGreater(good - bad, 0.2)

    def test_wrong_tv_type_can_recover_movie_from_multi_search(self) -> None:
        movie = {
            "id": 1321139,
            "media_type": "movie",
            "title": "有病才会喜欢你",
            "original_title": "Lovesick",
            "release_date": "2025-04-02",
            "popularity": 20,
        }
        tv = {
            "id": 321122,
            "media_type": "tv",
            "name": "Lovesick",
            "original_name": "Lovesick",
            "first_air_date": "",
            "popularity": 20,
        }

        def respond(path, params):
            if path == "/search/multi":
                return {"results": [movie, tv]}
            return {"results": []}

        client = FakeTmdbClient(respond)
        candidates = client.search(
            {
                "title": "有病才会喜欢你",
                "year": 2025,
                "media_type": "tv",
                "sources": [
                    {"metadata_json": '{"extra":"Lovesick.2025.1080p.WEB-DL.mkv"}'}
                ],
            }
        )

        self.assertEqual((candidates[0]["media_type"], candidates[0]["tmdb_id"]), ("movie", 1321139))
        self.assertTrue(should_auto_link(candidates))
        self.assertTrue(any(path == "/search/multi" for path, _ in client.calls))

    def test_unfiltered_search_does_not_reintroduce_a_conflicting_year(self) -> None:
        result = {
            "id": 298767,
            "name": "三体·周年纪念版",
            "original_name": "三体·周年纪念版",
            "first_air_date": "2023-04-20",
            "popularity": 10,
        }

        def respond(path, params):
            if path == "/search/tv" and "first_air_date_year" not in params:
                return {"results": [result]}
            return {"results": []}

        client = FakeTmdbClient(respond)
        candidates = client.search(
            {"title": "三体・周年纪念版", "year": 2024, "media_type": "tv", "sources": []}
        )

        self.assertEqual(candidates, [])
        self.assertTrue(
            any(path == "/search/tv" and "first_air_date_year" not in params for path, params in client.calls)
        )

    def test_release_name_alias_scores_original_title(self) -> None:
        result = {
            "id": 1290159,
            "media_type": "movie",
            "title": "炸药屋",
            "original_title": "A House of Dynamite",
            "release_date": "2025-10-02",
            "popularity": 30,
        }

        def respond(path, params):
            if params.get("query") == "A House Of Dynamite":
                return {"results": [result]}
            return {"results": []}

        client = FakeTmdbClient(respond)
        candidates = client.search(
            {
                "title": "炸裂白宫",
                "year": 2025,
                "media_type": "unknown",
                "sources": [
                    {
                        "raw_label": "炸裂白宫 (2025)",
                        "metadata_json": (
                            '{"extra":"A.HOUSE.OF.DYNAMITE.2025.1080p.NF.WEB-DL.mkv '
                            '访问码：z8a9"}'
                        ),
                    }
                ],
            }
        )

        self.assertEqual(candidates[0]["tmdb_id"], 1290159)
        self.assertGreaterEqual(candidates[0]["score"], 0.90)

    def test_candidates_are_deduplicated_across_alias_queries(self) -> None:
        result = {
            "id": 99,
            "media_type": "movie",
            "title": "测试片",
            "original_title": "Test Movie",
            "release_date": "2024-01-01",
        }

        client = FakeTmdbClient(lambda path, params: {"results": [result]})
        candidates = client.search(
            {
                "title": "测试片",
                "year": None,
                "media_type": "unknown",
                "sources": [{"filename": "Test.Movie.2024.1080p.WEB-DL.mkv"}],
            }
        )

        self.assertEqual(len(candidates), 1)

    def test_same_title_movie_and_tv_stays_for_review(self) -> None:
        movie = {
            "id": 1,
            "title": "恋如雨止",
            "original_title": "恋は雨上がりのように",
            "release_date": "2018-05-25",
            "popularity": 20,
        }
        tv = {
            "id": 2,
            "name": "恋如雨止",
            "original_name": "恋は雨上がりのように",
            "first_air_date": "2018-01-12",
            "popularity": 25,
        }

        def respond(path, params):
            if path == "/search/movie":
                return {"results": [movie]}
            if path == "/search/tv":
                return {"results": [tv]}
            return {"results": []}

        client = FakeTmdbClient(respond)
        candidates = client.search(
            {"title": "恋如雨止", "year": 2018, "media_type": "unknown", "sources": []}
        )

        self.assertEqual(len(candidates), 2)
        self.assertFalse(should_auto_link(candidates))

    def test_unique_exact_title_without_year_can_auto_link(self) -> None:
        result = {
            "id": 197060,
            "media_type": "tv",
            "name": "山河月明",
            "original_name": "山河月明",
            "first_air_date": "2022-04-06",
            "popularity": 0,
        }
        client = FakeTmdbClient(lambda path, params: {"results": [result]})
        candidates = client.search(
            {"title": "山河月明", "year": None, "media_type": "unknown", "sources": []}
        )

        self.assertEqual(candidates[0]["score"], 0.88)
        self.assertTrue(should_auto_link(candidates))

    def test_adult_search_is_only_used_as_empty_result_fallback(self) -> None:
        result = {
            "id": 777,
            "title": "计程车女孩",
            "original_title": "Taxi Girls",
            "release_date": "1979-01-01",
        }

        def respond(path, params):
            if params.get("include_adult") == "true":
                return {"results": [result]}
            return {"results": []}

        client = FakeTmdbClient(respond)
        candidates = client.search(
            {"title": "计程车女孩", "year": 1979, "media_type": "movie", "sources": []}
        )

        self.assertEqual(candidates[0]["tmdb_id"], 777)
        self.assertTrue(any(params.get("include_adult") == "true" for _, params in client.calls))


if __name__ == "__main__":
    unittest.main()
