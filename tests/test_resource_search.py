from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.repository import import_content, list_media
from backend.schema import connect


class ResourceSearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "test.db"
        import_content("Local Show (2025) https://115.com/s/test\nOther Film (1999) https://115.com/s/other", "test.txt", db_path=self.db)
        self.mid = list_media(query="Local Show", db_path=self.db)["items"][0]["id"]

    def metadata(self, value):
        with connect(self.db) as db:
            db.execute("UPDATE source_records SET metadata_json=? WHERE media_id=?", (
                value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), self.mid,
            ))

    def search(self, query, **options):
        return list_media(query=query, db_path=self.db, **options)

    def test_cached_files_and_paths_are_searchable_without_network(self):
        self.metadata({"share_snapshot": {"status": "ok", "files": [
            {"name": "Jia.Li.Jia.Wai.S01E01-E79.mp4", "path": "Archive Folder/Jia.Li.Jia.Wai.S01E01-E79.mp4"},
        ]}})
        with patch("urllib.request.urlopen", side_effect=AssertionError("Local search must be offline")):
            for query in ["Jia.Li.Jia.Wai", "Jia Li Jia Wai", "S01E01-E79", "Archive Folder"]:
                with self.subTest(query=query):
                    result = self.search(query)
                    self.assertEqual(result["total"], 1)
                    self.assertEqual(result["items"][0]["id"], self.mid)

    def test_old_snapshot_names_and_share_titles_remain_searchable(self):
        for field, value in [("share_title", "Unique Name"), ("names", ["Unique Name"]),
                             ("search_names", ["Unique Name"]), ("root_names", ["Unique Name"])]:
            self.metadata({"share_snapshot": {"status": "ok", field: value}})
            self.assertEqual(self.search("Unique Name")["total"], 1)

    def test_invalid_or_unavailable_metadata_is_not_a_search_result(self):
        for metadata in ["not json", "null", "[]", {"share_snapshot": {"status": "error", "names": ["Needle"]}},
                         {"share_snapshot": {"status": "ok", "error": "Needle", "errno": "Needle"}}]:
            self.metadata(metadata)
            self.assertEqual(self.search("Needle")["total"], 0)

    def test_metadata_keys_and_other_arbitrary_metadata_are_not_searched(self):
        self.metadata({"token": "secretvalue", "share_snapshot": {"status": "ok", "files": [{"name": "Unique.mp4", "id": "secretvalue"}]}})
        for query in ["share_snapshot", "secretvalue", "files"]:
            self.assertEqual(self.search(query)["total"], 0)

    def test_year_and_tmdb_id_search(self):
        with connect(self.db) as db:
            db.execute("UPDATE media_items SET tmdb_id=286363 WHERE id=?", (self.mid,))
        self.assertEqual(self.search("2025")["items"][0]["id"], self.mid)
        self.assertEqual(self.search("286363")["items"][0]["id"], self.mid)
        self.assertEqual(self.search("1999")["total"], 1)

    def test_literal_wildcards_and_punctuation_do_not_match_everything(self):
        self.metadata({"share_snapshot": {"status": "ok", "files": [{"name": "100%_quality.mp4", "path": "100%_quality.mp4"}]}})
        for query in ["%", "_", "100%_"]:
            self.assertEqual(self.search(query)["total"], 1)
        self.assertEqual(self.search("!!!")["total"], 0)
        self.assertEqual(self.search("' OR 1=1 --")["total"], 0)

    def test_whitespace_and_normalized_titles(self):
        self.assertEqual(self.search("  ")["total"], 2)
        self.assertEqual(self.search("localshow")["total"], 1)
        self.assertEqual(self.search("  Local Show  ")["total"], 1)

    def test_multiple_cached_matches_do_not_duplicate_media_and_filters_still_apply(self):
        self.metadata({"share_snapshot": {"status": "ok", "names": ["Needle", "Needle two"], "files": [{"name": "Needle.mp4"}]}})
        result = self.search("Needle", source_type="115", status="pending")
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(self.search("Needle", source_type="ed2k")["total"], 0)
        self.assertEqual(self.search("Needle", status="matched")["total"], 0)
        self.assertEqual(self.search("Needle", page=2, page_size=10)["items"], [])

    def test_ed2k_filename_and_imported_extra_are_searchable(self):
        self.metadata({"extra": "Original.Release.1080p.mp4"})
        self.assertEqual(self.search("Original Release")["total"], 1)
        with connect(self.db) as db:
            db.execute("UPDATE source_records SET source_type='ed2k', filename='Alternate.Release.2160p.mkv' WHERE media_id=?", (self.mid,))
        self.assertEqual(self.search("Alternate Release", source_type="ed2k")["total"], 1)


if __name__ == "__main__":
    unittest.main()
