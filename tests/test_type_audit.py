from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.repository import utc_now
from backend.schema import connect, init_db
from backend.type_audit import (
    apply_movie_candidates,
    audit_media_types,
    auto_link_existing_candidates,
    describe_type_audit,
    rescore_movie_type_conflicts,
)


class TypeAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "media.db"
        init_db(self.db_path)
        self.now = utc_now()
        with connect(self.db_path) as db:
            db.execute(
                "INSERT INTO imports(id,source_name,source_kind,content_sha256,created_at) VALUES (1,'t','x','sha',?)",
                (self.now,),
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_media(self, title: str, media_type: str, context: str, *, status: str = "review", candidate: dict | None = None) -> int:
        with connect(self.db_path) as db:
            media_id = db.execute(
                "INSERT INTO media_items(title,normalized_title,year,media_type,resource_kind,"
                "resource_metadata_json,is_bundle,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (title, title, 2024, media_type, "media", "{}", 0, self.now, self.now),
            ).lastrowid
            db.execute(
                "INSERT INTO source_records(media_id,import_id,source_type,provider,source_key,raw_label,"
                "raw_text,url,metadata_json,resource_class,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (media_id, 1, "115", "115", f"key-{media_id}", context, context, "https://115.com/s/x",
                 json.dumps({"context": context}, ensure_ascii=False), "media", self.now),
            )
            if candidate:
                db.execute(
                    "INSERT INTO tmdb_candidates(media_id,tmdb_id,media_type,title,release_date,score,payload_json,created_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (media_id, candidate["id"], candidate["type"], candidate["title"], candidate["release_date"],
                     candidate["score"], json.dumps(candidate["payload"], ensure_ascii=False), self.now),
                )
            db.execute("UPDATE media_items SET tmdb_status=? WHERE id=?", (status, media_id))
        return int(media_id)

    def test_audit_separates_icon_only_from_structural_evidence(self) -> None:
        self.add_media("无候选标题", "tv", "📺 无候选标题 (2024)")
        self.add_media(
            "候选标题", "tv", "📺 候选标题 (2024)",
            candidate={
                "id": 10,
                "type": "movie",
                "title": "候选标题",
                "release_date": "2024-01-01",
                "score": 0.95,
                "payload": {"_match_evidence": {"primary_title_exact": True, "type_conflict": True}},
            },
        )
        self.add_media("连续剧", "tv", "📺 连续剧 EP01")
        self.add_media("一部电影", "tv", "📺 一部电影 (2024)")
        self.add_media("普通剧", "tv", "普通剧 (2024)")
        self.add_media("已匹配", "tv", "📺 已匹配 (2024)", status="matched")

        result = audit_media_types(db_path=self.db_path)
        self.assertEqual(result["scanned"], 5)
        self.assertEqual(result["suspects"], 2)
        self.assertEqual(result["icon_only"], {"tv": 2, "movie": 0})
        self.assertEqual(result["candidate_movie"], 1)
        self.assertEqual(result["no_candidate"], 1)
        self.assertEqual(result["protected_tv"], 1)
        self.assertEqual(result["protected_movie"], 1)
        self.assertEqual(result["no_icon"], 1)
        self.assertEqual(result["candidate_tv"], 0)
        self.assertIn("只读检查", describe_type_audit(result))

    def test_audit_is_read_only(self) -> None:
        media_id = self.add_media("无候选标题", "tv", "📺 无候选标题 (2024)")
        audit_media_types(db_path=self.db_path)
        with connect(self.db_path) as db:
            row = db.execute("SELECT media_type FROM media_items WHERE id=?", (media_id,)).fetchone()
        self.assertEqual(row["media_type"], "tv")

    def test_apply_only_movie_candidates_and_clear_synthetic_season(self) -> None:
        movie_id = self.add_media(
            "候选标题", "tv", "📺 候选标题 (2024)",
            candidate={
                "id": 10,
                "type": "movie",
                "title": "候选标题",
                "release_date": "2024-01-01",
                "score": 0.95,
                "payload": {"_match_evidence": {"primary_title_exact": True, "type_conflict": True}},
            },
        )
        tv_id = self.add_media("连续剧", "tv", "📺 连续剧 EP01")
        with connect(self.db_path) as db:
            db.execute("UPDATE source_records SET season=1 WHERE media_id=?", (movie_id,))
        result = audit_media_types(db_path=self.db_path)
        applied = apply_movie_candidates(audit=result, db_path=self.db_path)
        self.assertEqual(applied, {"candidate_movie": 1, "updated": 1, "missing": 0})
        with connect(self.db_path) as db:
            movie = db.execute("SELECT media_type, tmdb_id FROM media_items WHERE id=?", (movie_id,)).fetchone()
            tv = db.execute("SELECT media_type FROM media_items WHERE id=?", (tv_id,)).fetchone()
            source = db.execute("SELECT season, season_end, episode FROM source_records WHERE media_id=?", (movie_id,)).fetchone()
        self.assertEqual(movie["media_type"], "movie")
        self.assertIsNone(movie["tmdb_id"])
        self.assertEqual(tv["media_type"], "tv")
        self.assertEqual((source["season"], source["season_end"], source["episode"]), (None, None, None))

    def test_auto_link_uses_existing_candidates_and_respects_blockers(self) -> None:
        movie_id = self.add_media(
            "候选标题", "movie", "📺 候选标题 (2024)",
            candidate={
                "id": 10,
                "type": "movie",
                "title": "候选标题",
                "release_date": "2024-01-01",
                "score": 0.99,
                "payload": {
                    "id": 10, "title": "候选标题", "release_date": "2024-01-01", "popularity": 10,
                    "_match_evidence": {"type_conflict": False, "primary_title_exact": True, "title_exact": True},
                },
            },
        )
        blocked_id = self.add_media(
            "合集标题", "movie", "📺 合集标题 (2024)",
            candidate={
                "id": 11,
                "type": "movie",
                "title": "合集标题",
                "release_date": "2024-01-01",
                "score": 0.99,
                "payload": {
                    "id": 11, "title": "合集标题", "release_date": "2024-01-01",
                    "_match_evidence": {"type_conflict": False, "collection": True, "title_exact": True},
                },
            },
        )
        unknown_id = self.add_media(
            "异形", "unknown", "📺 异形 (2024)",
            candidate={
                "id": 12,
                "type": "movie",
                "title": "异形",
                "release_date": "2024-01-01",
                "score": 0.99,
                "payload": {
                    "id": 12, "title": "异形", "release_date": "2024-01-01",
                    "_match_evidence": {"type_conflict": False, "primary_title_exact": True, "title_exact": True},
                },
            },
        )
        result = auto_link_existing_candidates(db_path=self.db_path)
        self.assertEqual(result["linked"], 2)
        self.assertGreaterEqual(result["skipped"], 1)
        with connect(self.db_path) as db:
            movie = db.execute("SELECT tmdb_id, tmdb_status, media_type FROM media_items WHERE id=?", (movie_id,)).fetchone()
            unknown = db.execute("SELECT tmdb_id, tmdb_status, media_type FROM media_items WHERE id=?", (unknown_id,)).fetchone()
            blocked = db.execute("SELECT tmdb_id, tmdb_status FROM media_items WHERE id=?", (blocked_id,)).fetchone()
        self.assertEqual((movie["tmdb_id"], movie["tmdb_status"], movie["media_type"]), (10, "matched", "movie"))
        self.assertEqual((unknown["tmdb_id"], unknown["tmdb_status"], unknown["media_type"]), (12, "matched", "movie"))
        self.assertEqual((blocked["tmdb_id"], blocked["tmdb_status"]), (None, "review"))

    def test_rescore_clears_stale_type_conflict_without_touching_protected_rows(self) -> None:
        movie_id = self.add_media(
            "候选标题", "movie", "📺 候选标题 (2024)",
            candidate={
                "id": 10,
                "type": "movie",
                "title": "候选标题",
                "release_date": "2024-01-01",
                "score": 0.965,
                "payload": {"popularity": 10, "_match_evidence": {"type_conflict": True, "effective_year": 2024}},
            },
        )
        protected_id = self.add_media(
            "连续剧", "movie", "📺 连续剧 EP01",
            candidate={
                "id": 11,
                "type": "movie",
                "title": "连续剧",
                "release_date": "2024-01-01",
                "score": 0.965,
                "payload": {"_match_evidence": {"type_conflict": True, "effective_year": 2024}},
            },
        )
        with connect(self.db_path) as db:
            db.execute("UPDATE source_records SET season=1 WHERE media_id=?", (protected_id,))
        result = rescore_movie_type_conflicts(db_path=self.db_path)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["protected"], 1)
        with connect(self.db_path) as db:
            row = db.execute("SELECT score, payload_json FROM tmdb_candidates WHERE media_id=?", (movie_id,)).fetchone()
            protected = db.execute("SELECT score FROM tmdb_candidates WHERE media_id=?", (protected_id,)).fetchone()
        self.assertGreater(row["score"], 0.965)
        self.assertFalse(json.loads(row["payload_json"])["_match_evidence"]["type_conflict"])
        self.assertEqual(protected["score"], 0.965)


if __name__ == "__main__":
    unittest.main()
