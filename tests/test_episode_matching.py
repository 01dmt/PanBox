from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.importers import parse_episode_span
from backend.repository import get_media, import_content, list_media
from backend.schema import connect
from backend.tmdb import TmdbClient, TmdbError, collect_search_context, search_media, should_auto_link


def resource_item(names=None, complete=True):
    names = names or ["Show.S01E01-E79.mp4"]
    return {"title": "Show", "year": 2025, "media_type": "tv", "sources": [{
        "source_type": "115", "metadata_json": {"share_snapshot": {
            "version": 2, "status": "ok", "complete": complete,
            "search_names": names,
            "files": [{"name": n, "path": n} for n in names],
        }},
    }]}


def tv_details(mid, counts, **overrides):
    return {
        "id": mid, "media_type": "tv", "name": "Show", "original_name": "Show",
        "first_air_date": "2025-03-14", "status": "Ended",
        "number_of_seasons": len(counts), "number_of_episodes": sum(counts.values()),
        "seasons": [{"season_number": n, "episode_count": count} for n, count in counts.items()],
        **overrides,
    }


class EpisodeClient(TmdbClient):
    def __init__(self, details=None):
        super().__init__(api_key="test")
        self.responses = details or [tv_details(286363, {1: 79}), tv_details(307496, {1: 2, 2: 50}, number_of_episodes=26)]
        self.calls = []
        self.errors = {}

    def request(self, path, params=None):
        self.calls.append(path)
        if path in self.errors:
            raise self.errors[path]
        if path.startswith("/search/"):
            return {"results": [{k: row[k] for k in (
                "id", "media_type", "name", "original_name", "first_air_date",
            )} for row in reversed(self.responses)]}
        for row in self.responses:
            if path == f"/tv/{row['id']}":
                return copy.deepcopy(row)
        raise TmdbError("Not found", 404)


class EpisodeParsingTests(unittest.TestCase):
    def test_single_episode_and_same_season_ranges(self):
        for name in ["Show.S01E01-E79.mp4", "Show.S01E01-S01E79.mp4", "Show.S01E01-79.mp4", "[Show][s01e01-e079][1080p].mkv"]:
            with self.subTest(name=name):
                self.assertEqual(parse_episode_span(name), (1, 1, 79))
        self.assertEqual(parse_episode_span("Show.S02E07.1080p.mkv"), (2, 7, 7))

    def test_ambiguous_or_invalid_spans_are_not_guessed(self):
        for name in [
            "Show.S01E01-S02E79.mp4", "Show.S01E79-E01.mp4", "Show.S00E01-E79.mp4",
            "Show.S01E00-E79.mp4", "Show.S01E01-E1000.mp4", "Show.S01E01-E02-E03.mp4",
            "Show.S01E01E02.mp4", "Show.S01E01.S01E02.mp4", "Show.S01.79episodes.mp4",
            "Show.2025.79.1080p.mp4", "Show.S01/01.mp4",
        ]:
            with self.subTest(name=name):
                self.assertIsNone(parse_episode_span(name))

    def test_coverage_deduplicates_quality_and_ignores_extras(self):
        item = resource_item(["Show.S01E01-E03.720p.mp4", "Show.S01E01-E03.1080p.mp4", "Show.S01E04.mkv", "Show.S01E99.trailer.mp4", "Show.S01E05.srt"])
        coverage = collect_search_context(item).resource_episodes
        self.assertEqual(coverage, [{"season": 1, "first_episode": 1, "last_episode": 4, "episode_count": 4, "contiguous": True}])

    def test_gaps_and_unknown_file_numbers_are_not_full_coverage(self):
        context = collect_search_context(resource_item(["Show.S01E01.mp4", "Show.S01E79.mp4"]))
        self.assertFalse(context.resource_episodes[0]["contiguous"])
        context = collect_search_context(resource_item(["Show.S01E01-E79.mp4", "Show.S01/80.mp4"]))
        self.assertEqual(context.resource_episodes, [])
        self.assertTrue(context.resource_episode_incomplete)

    def test_share_labels_and_incomplete_scans_are_not_episode_evidence(self):
        item = resource_item(["Show.mp4"])
        item["sources"][0]["metadata_json"]["share_snapshot"]["share_title"] = "Show.S01E01-E79"
        self.assertEqual(collect_search_context(item).resource_episodes, [])
        self.assertEqual(collect_search_context(resource_item(complete=False)).resource_episodes, [])

    def test_ed2k_episodes_use_the_same_parser(self):
        item = {"title": "Show", "year": 2025, "sources": [
            {"source_type": "ed2k", "filename": f"Show.S01E{n:02}.mp4"} for n in range(1, 4)
        ]}
        self.assertEqual(collect_search_context(item).resource_episodes[0]["episode_count"], 3)

    def test_separate_shares_are_not_combined_to_invent_full_coverage(self):
        item = resource_item(["Show.S01E01-E40.mp4"])
        item["sources"].extend(resource_item(["Show.S01E41-E79.mp4"])["sources"])
        coverage = collect_search_context(item).resource_episodes
        self.assertEqual([row["first_episode"] for row in coverage], [1, 41])
        self.assertFalse(should_auto_link(EpisodeClient().search(item)))


class EpisodeMatchingTests(unittest.TestCase):
    def test_jia_li_jia_wai_prefers_79_episode_season_not_search_order(self):
        client = EpisodeClient()
        candidates = client.search(resource_item())
        self.assertEqual(candidates[0]["tmdb_id"], 286363)
        self.assertTrue(should_auto_link(candidates))
        self.assertTrue(candidates[0]["match_evidence"]["season_episode_match"])
        rival = candidates[1]
        self.assertEqual(rival["score"], 0.79)
        self.assertTrue(rival["match_evidence"]["season_episode_conflict"])
        self.assertFalse(should_auto_link([rival]))
        self.assertFalse(rival["match_evidence"]["season_episode_comparison"]["totals_consistent"])
        self.assertEqual(client.calls.count("/tv/286363"), 1)
        self.assertEqual(client.calls.count("/tv/307496"), 1)

    def test_identical_or_longer_seasons_remain_ambiguous(self):
        for counts in [{1: 79}, {1: 80}, {1: 79, 2: 50}]:
            client = EpisodeClient([tv_details(1, {1: 79}), tv_details(2, counts)])
            candidates = client.search(resource_item())
            self.assertFalse(should_auto_link(candidates))
            self.assertTrue(all(c["score"] > 0.9 for c in candidates))

    def test_inconsistent_totals_cannot_make_a_winner(self):
        client = EpisodeClient([tv_details(1, {1: 79}, number_of_episodes=2), tv_details(2, {1: 2})])
        self.assertFalse(should_auto_link(client.search(resource_item())))

    def test_missing_or_malformed_details_do_not_disqualify_a_rival(self):
        for fields in [
            {"seasons": None}, {"seasons": []}, {"seasons": [None]},
            {"seasons": [{"season_number": 1, "episode_count": None}]},
            {"seasons": [{"season_number": 1, "episode_count": True}]},
            {"seasons": [{"season_number": 1, "episode_count": 2}] * 2},
            {"seasons": [{"season_number": 2, "episode_count": 79}]},
            {"status": "Returning Series"},
        ]:
            with self.subTest(fields=fields):
                client = EpisodeClient([tv_details(1, {1: 79}), tv_details(2, {1: 2}, **fields)])
                self.assertFalse(should_auto_link(client.search(resource_item())))

    def test_network_errors_and_changed_identity_preserve_review(self):
        client = EpisodeClient()
        client.errors["/tv/307496"] = TmdbError("timeout")
        self.assertFalse(should_auto_link(client.search(resource_item())))
        for fields in [{"id": 99}, {"first_air_date": "2024-03-14"}, {"name": "Other", "original_name": "Other"}]:
            client = EpisodeClient()
            original_request = client.request
            client.request = lambda p, q=None: {**original_request(p, q), **fields} if p == "/tv/307496" else original_request(p, q)
            self.assertFalse(should_auto_link(client.search(resource_item())))

    def test_partial_resources_and_no_episode_numbers_do_not_trigger_lookups(self):
        for item in [
            resource_item(complete=False), resource_item(["Show.S01.mp4"]),
            resource_item(["Show.S01E10-E79.mp4"]), resource_item(["Show.S01E01.mp4", "Show.S01E79.mp4"]),
            resource_item(["Show.S01E01.mp4"]), resource_item(["Show.S01E01-S02E79.mp4"]),
        ]:
            client = EpisodeClient()
            self.assertFalse(should_auto_link(client.search(item)))
            self.assertFalse(any(p.startswith("/tv/") for p in client.calls))

    def test_a_partial_season_does_not_match_a_larger_season_total(self):
        client = EpisodeClient()
        self.assertFalse(should_auto_link(client.search(resource_item(["Show.S01E01-E20.mp4"]))))

    def test_single_candidate_keeps_existing_behavior_and_has_no_extra_requests(self):
        client = EpisodeClient([tv_details(1, {1: 79})])
        self.assertTrue(should_auto_link(client.search(resource_item())))
        self.assertFalse(any(p.startswith("/tv/") for p in client.calls))

    def test_all_observed_seasons_must_match_and_unobserved_seasons_are_allowed(self):
        client = EpisodeClient([tv_details(1, {1: 79, 2: 50, 3: 12}), tv_details(2, {1: 79, 2: 2})])
        candidates = client.search(resource_item(["Show.S01E01-E79.mp4", "Show.S02E01-E50.mp4"]))
        self.assertTrue(should_auto_link(candidates))
        self.assertEqual(candidates[0]["tmdb_id"], 1)

    def test_two_exact_candidates_and_a_conflict_are_not_resolved(self):
        client = EpisodeClient([tv_details(1, {1: 79}), tv_details(2, {1: 79}), tv_details(3, {1: 2})])
        self.assertFalse(should_auto_link(client.search(resource_item())))

    def test_unknown_third_candidate_prevents_partial_verification_from_resolving_a_tie(self):
        client = EpisodeClient([tv_details(1, {1: 79}), tv_details(2, {1: 2}), tv_details(3, {1: 2}, seasons=None)])
        self.assertFalse(should_auto_link(client.search(resource_item())))

    def test_large_candidate_groups_do_not_verify_only_a_subset(self):
        client = EpisodeClient([tv_details(mid, {1: 79 if mid == 1 else 2}) for mid in range(1, 14)])
        self.assertFalse(should_auto_link(client.search(resource_item())))
        self.assertFalse(any(p.startswith("/tv/") for p in client.calls))

    def test_explicit_id_is_not_overridden(self):
        item = resource_item()
        item["sources"][0]["raw_text"] = "{tmdb-307496}"
        candidates = EpisodeClient().search(item)
        self.assertFalse(any(c["match_evidence"].get("season_episode_match") for c in candidates))

    def test_database_persists_evidence_and_links_correct_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "media.db"
            import_content("Show (2025) https://115.com/s/test", "test.txt", db_path=db)
            mid = list_media(db_path=db)["items"][0]["id"]
            with connect(db) as connection:
                connection.execute("UPDATE source_records SET metadata_json = ?", (
                    json.dumps(resource_item()["sources"][0]["metadata_json"]),
                ))
            with patch("backend.tmdb.enrich_media_share_sources", return_value={"removed": 0}):
                result = search_media(mid, auto_link=False, db_path=db, client=EpisodeClient())
                self.assertEqual(result["status"], "review")
                with connect(db) as connection:
                    payload = json.loads(connection.execute(
                        "SELECT payload_json FROM tmdb_candidates WHERE tmdb_id=286363",
                    ).fetchone()[0])
                self.assertEqual(payload["_match_evidence"]["season_episode_comparison"]["status"], "exact")
                result = search_media(mid, auto_link=True, db_path=db, client=EpisodeClient())
                self.assertTrue(result["linked"])
                self.assertEqual(get_media(mid, db)["tmdb_id"], 286363)
                self.assertEqual(len(get_media(mid, db)["sources"]), 1)


if __name__ == "__main__":
    unittest.main()
