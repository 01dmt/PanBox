import json
import tempfile
import unittest
from pathlib import Path

from backend.importers import ParsedSource, iter_share_sources
from backend.repository import import_content
from backend.resource_metadata import infer_class, source_resource_kind
from backend.resources import get_resource, get_resource_filters, list_resources, update_resource
from backend.schema import connect, init_db


class ResourceQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "resources.db"
        init_db(self.db)
        sources = [
            ParsedSource(
                source_type="115", provider="115", source_key="115:movie", title="星际穿越", year=2014,
                media_type="movie", raw_label="星际穿越", raw_text="星际穿越 4K https://115.com/s/movie?password=abcd",
                url="https://115.com/s/movie?password=abcd", quality="2160P",
            ),
            ParsedSource(
                source_type="123", provider="123", source_key="123:collection", title="周星驰电影合集", year=None,
                media_type="unknown", raw_label="周星驰电影合集", raw_text="周星驰电影合集 https://123pan.com/s/collection?pwd=efgh",
                url="https://123pan.com/s/collection?pwd=efgh", resource_kind="series",
            ),
        ]
        import_content("fixture", "fixture.txt", db_path=self.db, parsed_sources=sources)
        with connect(self.db) as db:
            db.execute("UPDATE source_records SET availability_status='available', last_checked_at='2026-10-03T12:00:00+00:00' WHERE source_key='115:movie'")
            db.execute("UPDATE source_records SET tags_json='[\"周星驰\",\"电影\"]' WHERE source_key='123:collection'")

    def tearDown(self):
        self.temp.cleanup()

    def test_each_share_is_a_resource_row_and_searches_source_text(self):
        result = list_resources(q="4K", page_size=10, db_path=self.db)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["provider"]["id"], "115")
        self.assertEqual(result["items"][0]["availability_status"], "available")

    def test_collection_filters_and_masks_access_code(self):
        result = list_resources(resource_class="collection", tag="周星驰", db_path=self.db)
        self.assertEqual(result["total"], 1)
        item = get_resource(result["items"][0]["id"], self.db)
        self.assertEqual(item["resource_class"], "collection")
        self.assertEqual(item["url_masked"], "https://123pan.com/s/collection?pwd=****")
        self.assertEqual(item["access_code_masked"], "****")

    def test_detail_preserves_original_url_for_copy_and_update(self):
        item = get_resource(1, self.db)
        self.assertIn("password=abcd", item["url"])
        updated = update_resource(item["id"], {"resource_class": "collection", "tags": ["4K", "电影"], "notes": "保留原始来源"}, self.db)
        self.assertEqual(updated["resource_class"], "collection")
        self.assertEqual(updated["tags"], ["4K", "电影"])
        self.assertEqual(updated["notes"], "保留原始来源")

    def test_filter_options_include_providers_and_classes(self):
        options = get_resource_filters(self.db)
        self.assertIn("115", [item["id"] for item in options["providers"]])
        self.assertIn("123", [item["id"] for item in options["providers"]])
        self.assertEqual(options["resource_classes"]["collection"], "资源包")

    def test_resource_summary_keeps_global_quick_filter_counts(self):
        result = list_resources(resource_class="collection", db_path=self.db)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["summary"]["total"], 2)
        self.assertEqual(result["summary"]["unrecognized"], 1)
        self.assertEqual(result["summary"]["series"], 1)

    def test_resource_groups_are_fixed_top_level_categories(self):
        self.assertEqual(list_resources(resource_group="media", db_path=self.db)["total"], 0)
        self.assertEqual(list_resources(resource_group="person", db_path=self.db)["total"], 0)
        self.assertEqual(list_resources(resource_group="series", db_path=self.db)["total"], 1)
        self.assertEqual(list_resources(resource_group="unrecognized", db_path=self.db)["total"], 1)

    def test_person_group_aggregates_sources_by_person_and_keeps_source_details(self):
        sources = [
            ParsedSource(source_type="115", provider="115", source_key=f"115:person-{index}", title="本田瞳",
                         year=None, media_type="unknown", raw_label=f"作品{index}.mp4", raw_text="👤 本田瞳",
                         url=f"https://115.com/s/person-{index}?password=abcd", resource_kind="person")
            for index in range(2)
        ]
        import_content("person", "person.txt", db_path=self.db, parsed_sources=sources)
        source_rows = list_resources(resource_group="person", db_path=self.db)
        grouped = list_resources(resource_group="person", aggregate="person", db_path=self.db)
        self.assertEqual(source_rows["total"], 2)
        self.assertEqual(grouped["total"], 1)
        self.assertEqual(grouped["source_total"], 2)
        self.assertEqual(grouped["items"][0]["title"], "本田瞳")
        self.assertEqual(grouped["items"][0]["resource_count"], 2)
        self.assertEqual({item["id"] for item in grouped["items"][0]["resources"]}, {3, 4})
        self.assertTrue(all(item["person_name"] == "本田瞳" for item in grouped["items"][0]["resources"]))
        self.assertTrue(all(item["url_masked"].endswith("password=****") for item in grouped["items"][0]["resources"]))
        with self.assertRaises(ValueError):
            list_resources(resource_group="series", aggregate="person", db_path=self.db)

    def test_groups_partition_sources_across_all_recognition_states(self):
        for status in ("pending", "review", "not_found", "error", "matched"):
            with self.subTest(status=status):
                with connect(self.db) as db:
                    db.execute("UPDATE media_items SET tmdb_status=?, tmdb_id=?",
                               (status, 157336 if status == "matched" else None))
                results = {group: list_resources(resource_group=group, db_path=self.db)
                           for group in ("all", "media", "person", "series", "unrecognized")}
                groups = {group: {row["id"] for row in result["items"]}
                          for group, result in results.items()}
                self.assertEqual(groups["person"], set())
                self.assertEqual(groups["series"], {2})
                self.assertEqual(groups["media"], {1} if status == "matched" else set())
                self.assertEqual(groups["unrecognized"], set() if status == "matched" else {1})
                self.assertEqual(groups["all"], groups["media"] | groups["person"] | groups["series"] | groups["unrecognized"])
                self.assertEqual(sum(results[g]["total"] for g in groups if g != "all"), results["all"]["total"])
                for group in ("media", "person", "series", "unrecognized"):
                    self.assertEqual(results[group]["total"], results["all"]["summary"][group])

    def test_matched_without_tmdb_id_stays_in_unrecognized_group(self):
        with connect(self.db) as db:
            db.execute("UPDATE media_items SET tmdb_status='matched', tmdb_id=NULL")
        self.assertEqual(list_resources(resource_group="media", db_path=self.db)["total"], 0)
        self.assertEqual(list_resources(resource_group="unrecognized", db_path=self.db)["total"], 1)

    def test_person_and_bundle_override_matches_and_group_counts_ignore_filters(self):
        with connect(self.db) as db:
            db.execute("UPDATE source_records SET resource_class='person' WHERE id=1")
            db.execute("UPDATE media_items SET tmdb_status='matched', tmdb_id=157336")
        result = list_resources(resource_group="person", provider="115", db_path=self.db)
        self.assertEqual([r["id"] for r in result["items"]], [1])
        self.assertEqual(result["summary"]["person"], 1)
        self.assertEqual(result["summary"]["series"], 1)
        self.assertEqual(result["summary"]["media"], 0)
        self.assertEqual(result["summary"]["unrecognized"], 0)
        with self.assertRaises(ValueError):
            list_resources(resource_group="invalid-group", db_path=self.db)

    def test_explicit_person_or_series_kind_wins_over_title_keywords(self):
        self.assertEqual(infer_class("person", "音乐会合集", "unknown"), "person")
        self.assertEqual(infer_class("series", "欧美音乐会", "unknown"), "collection")

    def red_queen_source(self):
        return next(iter_share_sources(
            "📺 剧集：红女王 (2024) S02E05\n🗂 分类：剧集/欧美剧集\n"
            "🔗 链接：https://123pan.com/s/red?pwd=abcd"
        ))

    def test_repeated_initialization_does_not_reclassify_a_tv_episode(self):
        source = self.red_queen_source()
        self.assertEqual(source.resource_kind, "media")
        import_content("red-queen", "red-queen.txt", db_path=self.db, parsed_sources=[source])
        init_db(self.db)
        import_content("duplicate", "red-queen.txt", db_path=self.db, parsed_sources=[source])
        with connect(self.db) as db:
            row = db.execute("""SELECT m.resource_kind,s.resource_class FROM source_records s
                JOIN media_items m ON m.id=s.media_id WHERE s.source_key=?""", (source.source_key,)).fetchone()
        self.assertEqual(tuple(row), ("media", "media"))

    def test_legacy_icon_migration_repairs_categories_and_preserves_source_data(self):
        source = self.red_queen_source()
        import_content("red-queen", "red-queen.txt", db_path=self.db, parsed_sources=[source])
        with connect(self.db) as db:
            red = db.execute("SELECT id,media_id FROM source_records WHERE source_key=?", (source.source_key,)).fetchone()
            db.execute("UPDATE media_items SET resource_kind='series', tmdb_status='matched', tmdb_id=203667 WHERE id=?", (red['media_id'],))
            db.execute("UPDATE source_records SET resource_class='person',tags_json='[\"4K\"]',notes='自定义备注' WHERE id=?", (red['id'],))
            before = dict(db.execute("SELECT * FROM source_records WHERE id=?", (red['id'],)).fetchone())
            # Simulate the database before this compatibility migration.
            db.execute("DELETE FROM schema_migrations WHERE name='resource_heading_kinds_v2'")
        init_db(self.db)
        with connect(self.db) as db:
            media = db.execute("SELECT resource_kind,tmdb_status,tmdb_id FROM media_items WHERE id=?", (red['media_id'],)).fetchone()
            after = dict(db.execute("SELECT * FROM source_records WHERE id=?", (red['id'],)).fetchone())
        self.assertEqual(tuple(media), ("media", "matched", 203667))
        self.assertEqual(after, {**before, "resource_class": "media"})
        self.assertEqual([item['title'] for item in list_resources(resource_group="media", db_path=self.db)['items']], ['红女王'])
        self.assertEqual(list_resources(resource_group="series", db_path=self.db)["total"], 1)
        # Later user edits must survive both restart and subsequent imports.
        update_resource(red['id'], {"resource_class": "person"}, self.db)
        init_db(self.db)
        import_content("duplicate", "red-queen.txt", db_path=self.db, parsed_sources=[source])
        self.assertEqual(get_resource(red['id'], self.db)['resource_class'], 'person')

    def test_heading_only_person_and_series_are_preserved_during_migration(self):
        for kind, heading, expected in [('person', '👤 示例人物', 'person'), ('series', '🗂 示例系列', 'collection')]:
            source = ParsedSource(source_type='115', provider='115', source_key=f'115:{kind}', title=heading,
                                  year=None, media_type='unknown', raw_label=heading, raw_text=heading,
                                  url=f'https://115.com/s/{kind}', resource_kind=kind, metadata={'context': heading})
            import_content(kind, f'{kind}.txt', db_path=self.db, parsed_sources=[source])
        with connect(self.db) as db:
            db.execute("UPDATE media_items SET resource_kind='media' WHERE title LIKE '%示例%'")
            db.execute("DELETE FROM schema_migrations WHERE name='resource_heading_kinds_v2'")
        init_db(self.db)
        with connect(self.db) as db:
            rows = db.execute("""SELECT s.source_key,m.resource_kind,s.resource_class FROM source_records s
                JOIN media_items m ON m.id=s.media_id WHERE m.title LIKE '%示例%' ORDER BY s.id""").fetchall()
        self.assertEqual([tuple(r) for r in rows], [('115:person', 'person', 'person'), ('115:series', 'series', 'collection')])

    def test_source_kind_ignores_metadata_icons_and_missing_context(self):
        for metadata, expected in [
            ({'context': '📺 红女王\n🗂 分类：剧集\n👤 投稿人'}, 'media'),
            ({'context': '🎬 示例电影\n🗂 分类：电影'}, 'media'),
            ({'context': '🗂 分类：剧集/欧美剧集'}, None),
            ({'context': '👤 示例人物'}, 'person'),
            ({'context': '🗂 示例系列'}, 'series'),
            ({}, None),
            ({'resource_kind': []}, None),
        ]:
            with self.subTest(metadata=metadata):
                self.assertEqual(source_resource_kind(json.dumps(metadata)), expected)


if __name__ == "__main__":
    unittest.main()
