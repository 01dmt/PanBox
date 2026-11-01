from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.repository import utc_now
from backend.schema import connect, init_db


class CollectionClassMigrationTests(unittest.TestCase):
    def test_existing_sources_move_to_collection_group_without_false_positives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "media.db"
            init_db(db_path)
            now = utc_now()
            with connect(db_path) as db:
                db.execute(
                    "INSERT INTO imports(id,source_name,source_kind,content_sha256,created_at) VALUES (1,'t','x','sha',?)",
                    (now,),
                )
                cases = [
                    ("成龙电影合集", "名称：成龙电影合集", None, "media", "collection"),
                    ("诛仙合集篇", "诛仙合集篇", None, "collection", "media"),
                    ("蓝白红三部曲之红", "蓝白红三部曲之红", None, "collection", "media"),
                    ("指环王 全3部", "指环王1：护戒使者", "指环王1.mkv", "media", "collection"),
                    ("孙燕姿音乐合集", "音乐 孙燕姿 无损合集", None, "media", "music"),
                ]
                for index, (title, raw_label, filename, before, expected) in enumerate(cases, start=1):
                    media_id = db.execute(
                        "INSERT INTO media_items(title,normalized_title,media_type,resource_kind,"
                        "resource_metadata_json,is_bundle,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        (title, title, "movie", "media", "{}", 0, now, now),
                    ).lastrowid
                    db.execute(
                        "INSERT INTO source_records(media_id,import_id,source_type,provider,source_key,"
                        "raw_label,raw_text,url,filename,metadata_json,resource_class,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (media_id, 1, "115", "115", f"key-{index}", raw_label, raw_label, "https://115.com/s/x",
                         filename, "{}", before, now),
                    )
                    with self.subTest(title=title):
                        self.assertEqual(
                            db.execute(
                                "SELECT resource_class FROM source_records WHERE source_key=?",
                                (f"key-{index}",),
                            ).fetchone()["resource_class"],
                            before,
                        )
                db.execute("DELETE FROM schema_migrations WHERE name='resource_collection_classes_v1'")
            init_db(db_path)
            with connect(db_path) as db:
                rows = {
                    row["raw_label"]: row["resource_class"]
                    for row in db.execute("SELECT raw_label,resource_class FROM source_records")
                }
            self.assertEqual(rows["名称：成龙电影合集"], "collection")
            self.assertEqual(rows["诛仙合集篇"], "media")
            self.assertEqual(rows["蓝白红三部曲之红"], "media")
            self.assertEqual(rows["指环王1：护戒使者"], "collection")
            self.assertEqual(rows["音乐 孙燕姿 无损合集"], "music")


if __name__ == "__main__":
    unittest.main()
