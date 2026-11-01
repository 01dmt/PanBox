import json
import tempfile
import unittest
from pathlib import Path

from backend.importers import ParsedSource, iter_share_sources
from backend.repository import import_content
from backend.resource_metadata import av_code, infer_class, source_resource_kind
from backend.repository import get_media, get_stats, list_media, pending_media_ids
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
        self.assertEqual(options["resource_classes"]["collection"], "合集")
        self.assertEqual(options["resource_classes"]["nsfw"], "成人/NSFW")

    def test_resource_summary_keeps_global_quick_filter_counts(self):
        result = list_resources(resource_class="collection", db_path=self.db)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["summary"]["total"], 2)
        self.assertEqual(result["summary"]["unrecognized"], 1)
        self.assertEqual(result["summary"]["series"], 1)

    def test_cached_summary_and_filters_refresh_after_source_change(self):
        before = list_resources(db_path=self.db)
        get_resource_filters(self.db)
        self.assertEqual(before["summary"]["total"], 2)
        import_content("cache", "cache.txt", db_path=self.db, parsed_sources=[
            ParsedSource(
                source_type="115", provider="quark", source_key="quark:cache", title="缓存测试", year=2026,
                media_type="movie", raw_label="缓存测试", raw_text="缓存测试 https://pan.quark.cn/s/cache",
                url="https://pan.quark.cn/s/cache", quality="1080P",
            ),
        ])
        with connect(self.db) as db:
            db.execute("UPDATE source_records SET tags_json='[\"新标签\"]' WHERE source_key='quark:cache'")
        after = list_resources(db_path=self.db)
        options = get_resource_filters(self.db)
        self.assertEqual(after["summary"]["total"], 3)
        self.assertIn("quark", [item["id"] for item in options["providers"]])
        self.assertIn("新标签", options["tags"])

    def test_search_index_tracks_metadata_and_raw_text_updates(self):
        resource_id = list_resources(db_path=self.db)["items"][0]["id"]
        with connect(self.db) as db:
            db.execute("UPDATE source_records SET metadata_json=?, raw_text=? WHERE id=?",
                       (json.dumps({"extra": "UniqueMetaToken"}), "UniqueRawToken original message", resource_id))
        self.assertEqual(list_resources(q="UniqueMetaToken", db_path=self.db)["total"], 1)
        self.assertEqual(list_resources(q="UniqueRawToken", db_path=self.db)["total"], 1)
        with connect(self.db) as db:
            db.execute("UPDATE source_records SET metadata_json=?, raw_text=? WHERE id=?",
                       (json.dumps({"extra": "ChangedMetaToken"}), "ChangedRawToken new message", resource_id))
        self.assertEqual(list_resources(q="UniqueMetaToken", db_path=self.db)["total"], 0)
        self.assertEqual(list_resources(q="UniqueRawToken", db_path=self.db)["total"], 0)
        self.assertEqual(list_resources(q="ChangedMetaToken", db_path=self.db)["total"], 1)
        self.assertEqual(list_resources(q="ChangedRawToken", db_path=self.db)["total"], 1)

    def test_resource_groups_are_fixed_top_level_categories(self):
        self.assertEqual(list_resources(resource_group="media", db_path=self.db)["total"], 0)
        self.assertEqual(list_resources(resource_group="person", db_path=self.db)["total"], 0)
        self.assertEqual(list_resources(resource_group="series", db_path=self.db)["total"], 1)
        self.assertEqual(list_resources(resource_group="nsfw", db_path=self.db)["total"], 0)
        self.assertEqual(list_resources(resource_group="other", db_path=self.db)["total"], 0)
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
        lazy = list_resources(resource_group="person", aggregate="person", include_resources=False, db_path=self.db)
        self.assertEqual(lazy["total"], 1)
        self.assertEqual(lazy["items"][0]["resource_count"], 2)
        self.assertEqual(lazy["items"][0]["resources"], [])
        detail = list_resources(resource_group="person", aggregate="person", group_id=lazy["items"][0]["id"], db_path=self.db)
        self.assertEqual(detail["total"], 1)
        self.assertEqual({item["id"] for item in detail["items"][0]["resources"]}, {3, 4})
        with self.assertRaises(ValueError):
            list_resources(resource_group="series", aggregate="person", db_path=self.db)

    def test_series_group_aggregates_collection_sources_and_keeps_all_links(self):
        grouped = list_resources(resource_group="series", aggregate="series", db_path=self.db)
        self.assertEqual(grouped["total"], 1)
        self.assertEqual(grouped["source_total"], 1)
        self.assertEqual(grouped["aggregated"], "series")
        group = grouped["items"][0]
        self.assertEqual(group["title"], "周星驰电影合集")
        self.assertEqual(group["resource_class"], "collection")
        self.assertEqual(group["resource_count"], 1)
        self.assertEqual([item["id"] for item in group["resources"]], [2])
        self.assertTrue(group["resources"][0]["url_masked"].endswith("pwd=****"))
        with self.assertRaises(ValueError):
            list_resources(resource_group="person", aggregate="series", db_path=self.db)

    def test_bundle_collection_is_promoted_to_series(self):
        source = ParsedSource(
            source_type="115", provider="115", source_key="115:bundle-collection",
            title="指环王 合集 全3部", year=None, media_type="unknown", raw_label="",
            raw_text="📺 指环王 合集 全3部 https://115.com/s/bundle?password=abcd",
            url="https://115.com/s/bundle?password=abcd", resource_kind="media",
            metadata={"collection": True, "series_title": "指环王"},
        )
        import_content("bundle", "bundle.txt", db_path=self.db, parsed_sources=[source])
        with connect(self.db) as db:
            row = db.execute("SELECT id FROM media_items WHERE title='指环王 合集 全3部'").fetchone()
        self.assertIsNotNone(row)
        media = get_media(int(row["id"]), self.db)
        self.assertEqual(media["resource_kind"], "series")
        self.assertIsNotNone(media["series_id"])
        self.assertEqual(media["tmdb_status"], "pending")

    def test_single_file_with_collection_title_stays_media(self):
        source = ParsedSource(
            source_type="115", provider="115", source_key="115:single-collection-title",
            title="教父 全3部", year=None, media_type="movie",
            raw_label="The.Godfather.1972.mkv", filename="The.Godfather.1972.mkv",
            raw_text="📺 教父 全3部",
            url="https://115.com/s/single?password=abcd", resource_kind="media",
            metadata={"collection": True, "series_title": "教父"},
        )
        import_content("single", "single.txt", db_path=self.db, parsed_sources=[source])
        with connect(self.db) as db:
            row = db.execute("SELECT id FROM media_items WHERE title='教父 全3部'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(get_media(int(row["id"]), self.db)["resource_kind"], "media")

    def test_series_media_is_curated_and_not_in_work_queue(self):
        stats = get_stats(self.db)["media"]
        self.assertEqual(stats["total"], 1)  # only the movie; the collection is not a work
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(pending_media_ids(10, self.db), [1])
        self.assertEqual(list_media(db_path=self.db)["total"], 1)
        self.assertEqual(get_media(2, self.db)["resource_kind"], "series")
        self.assertEqual(list_resources(resource_group="series", aggregate="series", db_path=self.db)["items"][0]["title"], "周星驰电影合集")

    def test_av_code_is_nsfw_and_kept_out_of_pending_queue(self):
        source = ParsedSource(
            source_type="115", provider="115", source_key="115:av-ssis-433",
            title="SSIS-433] 交わる体液、濃密セックス", year=2022, media_type="movie",
            raw_label="SSIS-433] 交わる体液、濃密セックス",
            raw_text="📺 SSIS-433] 交わる体液、濃密セックス https://115.com/s/av?password=abcd",
            url="https://115.com/s/av?password=abcd", filename="SSIS-433.mp4",
        )
        import_content("av", "av.txt", db_path=self.db, parsed_sources=[source])
        with connect(self.db) as db:
            row = db.execute("SELECT id, title FROM media_items WHERE title='SSIS-433'").fetchone()
            source_row = db.execute("SELECT resource_class FROM source_records WHERE media_id=?", (row["id"],)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(source_row["resource_class"], "nsfw")
        self.assertEqual(get_media(int(row["id"]), self.db)["title"], "SSIS-433")
        stats = get_stats(self.db)["media"]
        self.assertEqual(stats["pending"], 1)  # only the ordinary movie
        self.assertEqual(stats["not_found"], 0)
        self.assertEqual(pending_media_ids(10, self.db), [1])
        self.assertEqual(list_media(status="pending", db_path=self.db)["total"], 1)
        self.assertEqual(list_resources(resource_group="nsfw", db_path=self.db)["total"], 1)

    def test_groups_partition_sources_across_all_recognition_states(self):
        for status in ("pending", "review", "not_found", "error", "matched"):
            with self.subTest(status=status):
                with connect(self.db) as db:
                    db.execute("UPDATE media_items SET tmdb_status=?, tmdb_id=?",
                               (status, 157336 if status == "matched" else None))
                results = {group: list_resources(resource_group=group, db_path=self.db)
                           for group in ("all", "media", "person", "series", "nsfw", "other", "unrecognized")}
                groups = {group: {row["id"] for row in result["items"]}
                          for group, result in results.items()}
                self.assertEqual(groups["person"], set())
                self.assertEqual(groups["series"], {2})
                self.assertEqual(groups["nsfw"], set())
                self.assertEqual(groups["other"], set())
                self.assertEqual(groups["media"], {1} if status == "matched" else set())
                self.assertEqual(groups["unrecognized"], set() if status == "matched" else {1})
                self.assertEqual(groups["all"], groups["media"] | groups["person"] | groups["series"] | groups["nsfw"] | groups["other"] | groups["unrecognized"])
                self.assertEqual(sum(results[g]["total"] for g in groups if g != "all"), results["all"]["total"])
                for group in ("media", "person", "series", "nsfw", "other", "unrecognized"):
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

    def test_av_codes_become_nsfw_titles(self):
        self.assertEqual(av_code("SSIS-433] 交わる体液、濃密セックス"), "SSIS-433")
        self.assertEqual(av_code("VENZ-005 [自提征用]"), "VENZ-005")
        self.assertEqual(av_code("Spider-Man"), None)
        self.assertEqual(av_code("S01E03"), None)
        self.assertEqual(infer_class("media", "SSIS-433] 交わる体液", "unknown"), "nsfw")
        self.assertEqual(infer_class("media", "SSIS-433] 交わる体液", "movie"), "nsfw")

    def test_collection_classification_does_not_false_kill_real_titles(self):
        self.assertEqual(infer_class("media", "成龙电影合集", "movie"), "collection")
        self.assertEqual(infer_class("media", "金瓶梅……等等电影", "movie"), "collection")
        self.assertEqual(infer_class("media", "诛仙合集篇", "tv"), "media")
        self.assertEqual(infer_class("media", "性梦爱三部曲性", "movie"), "media")
        self.assertEqual(infer_class("media", "Threesome Anthology", "movie"), "media")

    def test_audio_files_and_concert_titles_are_music(self):
        # 无损音频包的文件名里没有「音乐」二字，只有扩展名能认出来。
        self.assertEqual(infer_class("media", "track01", "unknown", "01.黄昏.flac"), "music")
        self.assertEqual(infer_class("media", "碟1", "unknown", "CD1/02.寂寞沙洲冷.ape"), "music")
        # 演唱会标题里常带 Live，但真正的片名「Live and Let Die」不能被误判。
        self.assertEqual(infer_class("media", "[谢安琪呐喊演唱会]Kay.Tse.Yelling.Live", "movie"), "music")
        self.assertEqual(infer_class("media", "Live and Let Die", "movie", "Live.and.Let.Die.1973.mkv"), "media")
        self.assertEqual(infer_class("media", "The Next 20 张敬轩演唱会", "movie"), "music")

    def test_format_words_only_count_when_there_is_no_video_file(self):
        # 115 分享没有文件名，只能靠标题里的格式词认出是音乐包。
        self.assertEqual(infer_class("media", "齐秦 转flac分轨", "unknown", None), "music")
        self.assertEqual(infer_class("media", "电影OST合集", "unknown", None), "music")
        # 带 .mkv 的发布名里 FLAC 只是音轨，不能因此判成音乐。
        self.assertEqual(
            infer_class("media", "坏孩子的天空 BluRay 1080p x264 FLAC", "movie", "Kids.Return.1996.mkv"), "media"
        )

    def test_audio_only_attachments_are_counted_as_music(self):
        # 剧集附带的 theme.mp3 也算音乐（用户要求计入，不排除）。
        self.assertEqual(infer_class("media", "theme", "tv", "theme.mp3"), "music")

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

    def test_person_entries_are_cleaned_and_merged_during_migration(self):
        with connect(self.db) as db:
            db.execute("INSERT INTO media_items (title, normalized_title, year, media_type, resource_kind, created_at, updated_at) VALUES ('浅野心，','浅野心',1994,'unknown','person','x','x')")
            db.execute("INSERT INTO media_items (title, normalized_title, year, media_type, resource_kind, created_at, updated_at) VALUES ('浅野心','浅野心',NULL,'unknown','person','x','x')")
            db.execute("INSERT INTO media_items (title, normalized_title, year, media_type, resource_kind, created_at, updated_at) VALUES ('另一个人,','另一个人',2001,'unknown','person','x','x')")
            ids = [r["id"] for r in db.execute("SELECT id FROM media_items WHERE normalized_title IN ('浅野心','另一个人') ORDER BY id").fetchall()]
            for key, media_id in zip(("115:legacy1", "115:legacy2"), ids):
                db.execute("INSERT INTO source_records (media_id, source_type, provider, source_key, raw_label, raw_text, url, resource_class, parsed_year, created_at) VALUES (?, '115','115',?, 'x','x',?, 'person', 1994, 'x')",
                           (media_id, key, f'https://115.com/s/{key}'))
            db.execute("INSERT INTO ingestion_events (id, event_id, raw_text, media_ids_json, received_at) VALUES ('e1','ev1','t', ?, 'x')", (json.dumps(ids),))
            # 模拟本次迁移之前的存量数据状态。
            db.execute("DELETE FROM schema_migrations WHERE name='person_entries_aggregation_v1'")
        init_db(self.db)
        with connect(self.db) as db:
            rows = db.execute("SELECT id, title, year FROM media_items WHERE resource_kind='person' ORDER BY id").fetchall()
            srcs = db.execute("SELECT source_key, media_id, parsed_year FROM source_records WHERE source_key LIKE '115:legacy%' ORDER BY source_key").fetchall()
            event = db.execute("SELECT media_ids_json FROM ingestion_events WHERE id='e1'").fetchone()
        self.assertEqual({row["title"] for row in rows}, {"浅野心", "另一个人"})
        self.assertTrue(all(row["year"] is None for row in rows))
        self.assertTrue(all(src["parsed_year"] is None for src in srcs))
        # 两条浅野心来源合并到同一条目，事件里的失效 id 重映射到保留条目。
        merged_person = next(src["media_id"] for src in srcs if src["source_key"] == "115:legacy1")
        self.assertEqual(next(src["media_id"] for src in srcs if src["source_key"] == "115:legacy2"), merged_person)
        self.assertEqual(sorted(json.loads(event["media_ids_json"])), sorted({row["id"] for row in rows}))

    def test_person_message_history_is_reattributed_during_migration(self):
        person_source = ("👤 浅野心，🎂 生日：2002-06-11\n"
                         "🔗 https://115.com/s/hist1\nhttps://115.com/s/hist2")
        single_source = "👤 小野六花\n🔗 https://115.com/s/solo"
        with connect(self.db) as db:
            # 浅野心：旧解析把同一人物按年份拆成两条普通影视条目。
            split = [db.execute(
                "INSERT INTO media_items (title, normalized_title, year, media_type, resource_kind, tmdb_status, created_at, updated_at) VALUES ('浅野心','浅野心',2023,'movie','media','error','x','x')"
            ).lastrowid, db.execute(
                "INSERT INTO media_items (title, normalized_title, year, media_type, resource_kind, tmdb_status, created_at, updated_at) VALUES ('浅野心','浅野心',2024,'movie','media','error','x','x')"
            ).lastrowid]
            # 小野六花：全部来源恰好落在同一条普通条目上，迁移时应提升该条目。
            solo = db.execute(
                "INSERT INTO media_items (title, normalized_title, year, media_type, resource_kind, tmdb_status, created_at, updated_at) VALUES ('小野六花','小野六花',NULL,'movie','media','error','x','x')"
            ).lastrowid
            for media_id, key, year in ((split[0], "115:hist1", 2023), (split[1], "115:hist2", 2023), (solo, "115:solo", None)):
                db.execute("INSERT INTO source_records (media_id, source_type, provider, source_key, raw_label, raw_text, url, resource_class, parsed_year, created_at) VALUES (?, '115','115',?, 'x','x',?, 'other', ?, 'x')",
                           (media_id, key, f'https://115.com/s/{key.split(":")[-1]}', year))
            db.execute("INSERT INTO ingestion_events (id, event_id, service, channel_username, message_url, raw_text, status, media_ids_json, received_at) VALUES ('ev-a','19682','telegram','Lsp115','https://t.me/Lsp115/19682',?,'imported',?,'x')",
                       (person_source, json.dumps(split)))
            db.execute("INSERT INTO ingestion_events (id, event_id, service, channel_username, message_url, raw_text, status, media_ids_json, received_at) VALUES ('ev-b','19680','telegram','Lsp115','https://t.me/Lsp115/19680',?,'imported',?,'x')",
                       (single_source, json.dumps([solo])))
            for key, media_id in (("115:hist1", split[0]), ("115:hist2", split[1])):
                db.execute("INSERT INTO ingestion_links (ingestion_id, source_key, provider, source_type, url, status, media_id) VALUES ('ev-a',?,'115','115',?,'inserted',?)",
                           (key, f'https://115.com/s/{key.split(":")[-1]}', media_id))
            db.execute("INSERT INTO ingestion_links (ingestion_id, source_key, provider, source_type, url, status, media_id) VALUES ('ev-b','115:solo','115','115','https://115.com/s/solo','inserted',?)", (solo,))
            # 模拟本次迁移之前的存量数据状态。
            db.execute("DELETE FROM schema_migrations WHERE name='person_message_attribution_v1'")
        init_db(self.db)
        with connect(self.db) as db:
            persons = db.execute("SELECT id, title, year, media_type FROM media_items WHERE resource_kind='person' ORDER BY id").fetchall()
            srcs = db.execute("SELECT source_key, media_id, resource_class, parsed_year FROM source_records WHERE source_key IN ('115:hist1','115:hist2','115:solo') ORDER BY source_key").fetchall()
            events = db.execute("SELECT id, media_ids_json FROM ingestion_events WHERE id IN ('ev-a','ev-b') ORDER BY id").fetchall()
            leftovers = db.execute("SELECT COUNT(*) FROM media_items WHERE id IN (?,?,?)", (*split, solo)).fetchone()[0]
        by_title = {row["title"]: row for row in persons}
        self.assertEqual({title: (row["year"], row["media_type"]) for title, row in by_title.items()},
                         {"浅野心": (None, "unknown"), "小野六花": (None, "unknown")})
        self.assertEqual({src["source_key"]: (src["media_id"], src["resource_class"], src["parsed_year"]) for src in srcs},
                         {"115:hist1": (by_title["浅野心"]["id"], "person", None),
                          "115:hist2": (by_title["浅野心"]["id"], "person", None),
                          "115:solo": (by_title["小野六花"]["id"], "person", None)})
        # 单条普通条目被提升时保留原 id，不产生新条目。
        self.assertEqual(by_title["小野六花"]["id"], solo)
        self.assertEqual({row["id"]: json.loads(row["media_ids_json"]) for row in events},
                         {"ev-a": [by_title["浅野心"]["id"]], "ev-b": [by_title["小野六花"]["id"]]})
        self.assertEqual(leftovers, 1)  # 只有被提升的 小野六花 条目保留，两条分裂条目被清理

    def test_person_profile_promotes_duplicate_media_collection(self):
        person_context = "👤 加藤リナ\n🎂 生日：1990-06-04\n🏠 出生地：Tokyo, Japan"
        with connect(self.db) as db:
            media_id = db.execute(
                "INSERT INTO media_items (title, normalized_title, year, media_type, resource_kind, tmdb_status, poster_path, series_id, created_at, updated_at) VALUES ('加藤莉娜 合集','加藤莉娜合集',NULL,'movie','media','review','/wrong.jpg',NULL,'x','x')"
            ).lastrowid
            db.execute(
                "INSERT INTO source_records (media_id, source_type, provider, source_key, raw_label, raw_text, url, resource_class, metadata_json, created_at) VALUES (?, 'ed2k','ed2k','ed2k:profile','www.98T.la@ABS-209.mp4','x','ed2k://file','collection',?,'x')",
                (media_id, json.dumps({"context": person_context, "resource_kind": "person"}, ensure_ascii=False)),
            )
            db.execute(
                "INSERT INTO tmdb_candidates (media_id, tmdb_id, media_type, title, score, payload_json, created_at) VALUES (?, 999, 'movie', 'wrong', 0.9, '{}', 'x')",
                (media_id,),
            )
            db.execute(
                "INSERT INTO ingestion_events (id, event_id, service, channel_username, message_url, raw_text, status, media_ids_json, received_at) VALUES ('ev-profile','profile','telegram','Lsp115','https://t.me/Lsp115/profile',?,'duplicate',?,'x')",
                (person_context, json.dumps([media_id])),
            )
            db.execute(
                "INSERT INTO ingestion_links (ingestion_id, source_key, provider, source_type, url, status, media_id) VALUES ('ev-profile','ed2k:profile','ed2k','ed2k','ed2k://file','duplicate',?)",
                (media_id,),
            )
            db.execute("DELETE FROM schema_migrations WHERE name='person_message_attribution_v2'")
        init_db(self.db)
        with connect(self.db) as db:
            media = db.execute("SELECT id, title, normalized_title, year, media_type, resource_kind, tmdb_status, poster_path, resource_metadata_json FROM media_items WHERE id=?", (media_id,)).fetchone()
            source = db.execute("SELECT media_id, resource_class FROM source_records WHERE source_key='ed2k:profile'").fetchone()
            candidates = db.execute("SELECT COUNT(*) FROM tmdb_candidates WHERE media_id=?", (media_id,)).fetchone()[0]
            event = db.execute("SELECT media_ids_json FROM ingestion_events WHERE id='ev-profile'").fetchone()
        self.assertEqual(media["title"], "加藤リナ")
        self.assertEqual(media["normalized_title"], "加藤リナ")
        self.assertEqual((media["year"], media["media_type"], media["resource_kind"], media["tmdb_status"]), (None, "unknown", "person", "pending"))
        self.assertIsNone(media["poster_path"])
        self.assertIn("👤 加藤リナ", media["resource_metadata_json"])
        self.assertEqual((source["media_id"], source["resource_class"]), (media_id, "person"))
        self.assertEqual(candidates, 0)
        self.assertEqual(json.loads(event["media_ids_json"]), [media_id])

    def test_person_tmdb_state_is_reset_during_migration(self):
        with connect(self.db) as db:
            person_id = db.execute(
                "INSERT INTO media_items (title, normalized_title, year, media_type, resource_kind, tmdb_id, tmdb_media_type, tmdb_status, tmdb_title, poster_path, last_tmdb_error, match_confidence, created_at, updated_at) VALUES ('浅野心','浅野心',NULL,'unknown','person',455,'movie','error','浅野心','/p.jpg','boom',0.9,'x','x')"
            ).lastrowid
            db.execute("INSERT INTO tmdb_candidates (media_id, tmdb_id, media_type, title, score, payload_json, created_at) VALUES (?,455,'movie','浅野心',0.9,'{}','x')", (person_id,))
            db.execute("INSERT INTO imdb_lookups (media_id, evidence_json, updated_at) VALUES (?,'{}','x')", (person_id,))
            # 模拟本次迁移之前的存量数据状态。
            db.execute("DELETE FROM schema_migrations WHERE name='person_tmdb_state_reset_v1'")
        init_db(self.db)
        with connect(self.db) as db:
            row = db.execute(
                "SELECT tmdb_status, tmdb_id, poster_path, last_tmdb_error, match_confidence FROM media_items WHERE id=?",
                (person_id,),
            ).fetchone()
            candidates = db.execute("SELECT COUNT(*) FROM tmdb_candidates WHERE media_id=?", (person_id,)).fetchone()[0]
            lookups = db.execute("SELECT COUNT(*) FROM imdb_lookups WHERE media_id=?", (person_id,)).fetchone()[0]
        self.assertEqual(tuple(row), ("pending", None, None, None, None))
        self.assertEqual((candidates, lookups), (0, 0))

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
