from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.repository import import_content, list_media
from backend.schema import connect
from backend.type_evidence import (
    apply_strong_type_evidence,
    audit_type_evidence,
    backfill_legacy_movie_types,
    classify_media,
)


class TypeEvidenceTests(unittest.TestCase):
    def _import(self, db_path: Path, heading: str, slug: str) -> int:
        import_content(
            f"{heading}\thttps://115cdn.com/s/{slug}?password=x#",
            f"{slug}.txt",
            db_path=db_path,
        )
        return list_media(db_path=db_path)["items"][0]["id"]

    def test_strong_tv_movie_weak_and_conflict_grades(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            tv_id = self._import(db_path, "📺 某剧 更新至04集", "tv")
            movie_id = self._import(db_path, "📺 某电影 剧场版", "movie")
            weak_id = self._import(db_path, "📺 无类型标题", "weak")
            conflict_id = self._import(db_path, "📺 混合 电影 第1集", "conflict")

            with connect(db_path) as connection:
                connection.execute("UPDATE source_records SET season=1 WHERE media_id=?", (weak_id,))
                def evidence(media_id: int):
                    row = connection.execute(
                        "SELECT id,title,year,media_type,tmdb_status,resource_kind,is_bundle,is_metadata "
                        "FROM media_items WHERE id=?",
                        (media_id,),
                    ).fetchone()
                    return classify_media(connection, row)

                self.assertEqual((evidence(tv_id)["type"], evidence(tv_id)["confidence"]), ("tv", "strong"))
                self.assertEqual((evidence(movie_id)["type"], evidence(movie_id)["confidence"]), ("movie", "strong"))
                self.assertEqual(evidence(weak_id)["confidence"], "weak")
                self.assertEqual(evidence(conflict_id)["confidence"], "conflict")

    def test_apply_only_strong_single_sided_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            tv_id = self._import(db_path, "📺 某剧 更新至04集", "tv-apply")
            movie_id = self._import(db_path, "📺 某电影 剧场版", "movie-apply")
            weak_id = self._import(db_path, "📺 无类型标题", "weak-apply")

            with connect(db_path) as connection:
                connection.execute("UPDATE media_items SET media_type='movie' WHERE id=?", (tv_id,))
                connection.execute("UPDATE media_items SET media_type='tv' WHERE id=?", (movie_id,))
                connection.execute("UPDATE source_records SET season=1 WHERE media_id=?", (movie_id,))

            result = apply_strong_type_evidence(db_path=db_path)
            self.assertEqual(result["changed"], 2)
            with connect(db_path) as connection:
                types = {
                    row["id"]: row["media_type"]
                    for row in connection.execute("SELECT id, media_type FROM media_items")
                }
            self.assertEqual(types[tv_id], "tv")
            self.assertEqual(types[movie_id], "movie")
            self.assertEqual(types[weak_id], "unknown")
            with connect(db_path) as connection:
                self.assertIsNone(
                    connection.execute(
                        "SELECT season FROM source_records WHERE media_id=?", (movie_id,)
                    ).fetchone()[0]
                )

    def test_backfill_legacy_tv_uses_movie_candidate_without_strong_tv_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            movie_id = self._import(db_path, "📺 某电影 (2024)", "legacy-movie")
            tv_id = self._import(db_path, "📺 某剧 更新至04集", "real-tv")
            with connect(db_path) as connection:
                connection.execute(
                    "UPDATE media_items SET media_type='tv', tmdb_status='review' WHERE id IN (?,?)",
                    (movie_id, tv_id),
                )
                connection.execute("UPDATE source_records SET season=1 WHERE media_id=?", (movie_id,))
                for media_id, title in ((movie_id, "某电影"), (tv_id, "某剧")):
                    connection.execute(
                        "INSERT INTO tmdb_candidates(media_id,tmdb_id,media_type,title,release_date,score,payload_json,created_at)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (
                            media_id, 9000 + media_id, "movie", title, "2024-01-01", 0.96,
                            json.dumps({"_match_evidence": {
                                "title_exact": True,
                                "primary_title_exact": True,
                                "type_conflict": True,
                            }}),
                            "x",
                        ),
                    )
            result = backfill_legacy_movie_types(dry_run=False, db_path=db_path)
            self.assertEqual(result["changed"], 1)
            with connect(db_path) as connection:
                movie_type = connection.execute(
                    "SELECT media_type FROM media_items WHERE id=?", (movie_id,)
                ).fetchone()[0]
                tv_type = connection.execute(
                    "SELECT media_type FROM media_items WHERE id=?", (tv_id,)
                ).fetchone()[0]
                season = connection.execute(
                    "SELECT season FROM source_records WHERE media_id=?", (movie_id,)
                ).fetchone()[0]
            self.assertEqual(movie_type, "movie")
            self.assertEqual(tv_type, "tv")
            self.assertIsNone(season)

    def test_apply_skips_matched_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            media_id = self._import(db_path, "📺 某剧 更新至04集", "matched")
            with connect(db_path) as connection:
                connection.execute(
                    "UPDATE media_items SET media_type='movie', tmdb_status='matched' WHERE id=?",
                    (media_id,),
                )
            result = apply_strong_type_evidence(db_path=db_path)
            self.assertEqual(result["changed"], 0)
            with connect(db_path) as connection:
                self.assertEqual(
                    connection.execute("SELECT media_type FROM media_items WHERE id=?", (media_id,)).fetchone()[0],
                    "movie",
                )

    def test_audit_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            tv_id = self._import(db_path, "📺 某剧 更新至04集", "tv-audit")
            with connect(db_path) as connection:
                connection.execute("UPDATE media_items SET media_type='movie' WHERE id=?", (tv_id,))

            report = audit_type_evidence(db_path=db_path)
            self.assertGreater(report["total"], 0)
            with connect(db_path) as connection:
                self.assertEqual(
                    connection.execute("SELECT media_type FROM media_items WHERE id=?", (tv_id,)).fetchone()[0],
                    "movie",
                )

    def test_date_like_episode_is_not_strong_tv_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            media_id = self._import(db_path, "📺 某节目", "date")
            with connect(db_path) as connection:
                connection.execute("UPDATE source_records SET episode=20240707 WHERE media_id=?", (media_id,))
                row = connection.execute(
                    "SELECT id,title,year,media_type,tmdb_status,resource_kind,is_bundle,is_metadata "
                    "FROM media_items WHERE id=?",
                    (media_id,),
                ).fetchone()
                evidence = classify_media(connection, row)
            self.assertEqual(evidence["confidence"], "weak")
            self.assertFalse(any(reason.startswith("episode:") for reason in evidence["reasons"]))


if __name__ == "__main__":
    unittest.main()
