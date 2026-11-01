from __future__ import annotations

import tempfile
import json
import unittest
from pathlib import Path

from backend.importers import (
    detect_source_kind,
    extract_quality_tags,
    extract_tmdb_ids,
    infer_share_media_type,
    iter_ed2k_sources,
    is_guangya_movie_channel,
    iter_share_sources,
    iter_sources,
    parse_ed2k_link,
    parse_release_aliases,
    parse_release_title,
    parse_share_heading,
    parse_share_line,
    split_joined_title,
    strip_search_noise,
)
from backend.repository import (
    get_media, get_stats, import_content, link_tmdb, list_media, save_candidates, update_media_fields,
)


class ImporterTests(unittest.TestCase):
    def test_reimporting_share_with_empty_fragment_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content("测试片 (2024) https://115.com/s/fragment?password=abcd#", "one.txt", db_path=db_path)
            import_content("测试片 (2024) https://115.com/s/fragment?password=abcd", "two.txt", db_path=db_path)
            self.assertEqual(len(list_media(db_path=db_path)["items"]), 1)
            self.assertEqual(get_stats(db_path)["sources"]["total"], 1)

    def test_reimport_after_parser_change_does_not_create_empty_media(self) -> None:
        content = "ed2k://|file|[片名].Movie.2020.mkv|100|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/"
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content(content, "release.txt", db_path=db_path)
            mid = list_media(db_path=db_path)["items"][0]["id"]
            update_media_fields(mid, {"title": "旧解析名称 Movie", "year": 1999}, db_path)
            result = import_content(content, "release.txt", db_path=db_path)
            self.assertEqual(result["duplicates"], 1)
            self.assertEqual(get_stats(db_path)["media"]["total"], 1)
            self.assertEqual(get_media(mid, db_path)["year"], 1999)

    def test_import_returns_affected_media_ids_for_auto_match(self) -> None:
        content = "ed2k://|file|[片名].Movie.2020.mkv|100|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/"
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            result = import_content(content, "release.txt", db_path=db_path)
            media_id = list_media(db_path=db_path)["items"][0]["id"]
            self.assertEqual(result["media_ids"], [media_id])

    def test_candidate_evidence_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content("Film (2020) https://115.com/s/example", "test.txt", db_path=db_path)
            mid = list_media(db_path=db_path)["items"][0]["id"]
            evidence = {"verified_titles": ["Film"], "derived_only": False}
            save_candidates(mid, [{"tmdb_id": 4, "media_type": "movie", "title": "片名",
                                   "score": 0.9, "payload": {"id": 4}, "match_evidence": evidence}],
                            "review", db_path=db_path)
            saved = get_media(mid, db_path)["candidates"][0]
            self.assertEqual(json.loads(saved["payload_json"])["_match_evidence"], evidence)

    def test_pending_queue_exposes_candidate_summary_and_status_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content("Film (2020) https://115.com/s/example", "test.txt", db_path=db_path)
            mid = list_media(db_path=db_path)["items"][0]["id"]
            save_candidates(mid, [
                {"tmdb_id": 4, "media_type": "movie", "title": "低分候选", "score": 0.4},
                {"tmdb_id": 5, "media_type": "movie", "title": "高分候选", "score": 0.92,
                 "poster_path": "/high.jpg", "release_date": "2020-05-01"},
            ], "review", db_path=db_path)
            page = list_media(status="review", db_path=db_path)
            stats = get_stats(db_path)

        item = page["items"][0]
        self.assertEqual(item["candidate_count"], 2)
        self.assertEqual(item["top_candidate"]["tmdb_id"], 5)
        self.assertEqual(item["top_candidate"]["title"], "高分候选")
        self.assertAlmostEqual(item["top_candidate"]["score"], 0.92)
        self.assertEqual(stats["media"]["review"], 1)
        self.assertEqual(stats["media"]["not_found"], 0)
        self.assertEqual(stats["media"]["unsearched"], 0)

    def test_technical_tail_is_not_a_release_alias(self) -> None:
        for marker in ["REMUX.AVC.DTS-HD.MA.5.1", "1080p.x264.FLAC", "UHD.REMUX.HDR",
                       "HQ.HDR.60FPS.H.265", "60FPS.FLAC.HDR", "HDR10+.10bit.HEVC"]:
            with self.subTest(marker=marker):
                self.assertEqual(
                    parse_release_aliases(f"Blackswan.2010.BluRay.{marker}-HDHIVE.mkv"),
                    ["Blackswan"],
                )

    def test_bracketed_bilingual_release_preserves_both_titles(self) -> None:
        name = "[堕落东京].Tokyo.Decadence.1992.GBR.BluRay.1080p.x264.FLAC-CMCT.mkv"
        self.assertEqual(parse_release_aliases(name), ["堕落东京", "Tokyo Decadence"])
        self.assertEqual(parse_release_title(name), ("堕落东京", 1992))

    def test_share_bilingual_filename_preserves_concert_alias(self) -> None:
        name = (
            "Lady Gaga：方寸混沌之美（科切拉音乐节） (2025) - "
            "MAYHEM In The Desert Lady Gaga Live At Coachella 2025 1080p WEB-DL AAC2 0.mkv"
        )
        self.assertEqual(parse_release_aliases(name), [
            "Lady Gaga：方寸混沌之美（科切拉音乐节）", "MAYHEM In The Desert Lady Gaga Live At Coachella",
        ])

    def test_year_in_release_group_is_not_the_film_year(self) -> None:
        self.assertEqual(parse_release_title("Movie.1999.1080p.BluRay-Team2025.mkv"), ("Movie", 1999))

    def test_repeated_release_year_does_not_become_part_of_the_title(self) -> None:
        name = "X.2022.2022.2160p.WEB-DL.x265.10bit.SDR.DDP5.1.Atmos-NOGRP.mkv"
        self.assertEqual(parse_release_title(name), ("X", 2022))
        self.assertEqual(parse_release_aliases(name), ["X"])
        self.assertEqual(parse_release_title("1917.2019.1080p.mkv"), ("1917", 2019))

    def test_joined_words_are_split_without_guessing_numbered_abbreviations(self) -> None:
        self.assertEqual(split_joined_title("Blackswan").casefold(), "black swan")
        self.assertIsNone(split_joined_title("Eva30111"))
        self.assertIsNone(split_joined_title("REMUX"))
        self.assertIsNone(split_joined_title("1080p"))
        self.assertIsNone(split_joined_title("Black Swan"))

    def test_parses_both_115_domains_and_year_spacing(self) -> None:
        first = parse_share_line(
            "\ufeff好东西 (2024)\thttps://115cdn.com/s/example?password=abcd#\t"
        )
        second = parse_share_line(
            "那山那人那狗(1999)\thttps://115.com/s/example2?password=efgh#"
        )
        self.assertEqual((first.title, first.year, first.source_type), ("好东西", 2024, "115"))
        self.assertEqual((second.title, second.year), ("那山那人那狗", 1999))

    def test_parses_channel_heading_for_115_share_metadata(self) -> None:
        content = (
            "📺 余红旧事 (2026) S01E16 ✨4K WEB-DL DDP 5 1\n\n"
            "🌟 评分： 5.5\n"
            "🔗 链接： 点击跳转 https://115cdn.com/s/swstch33zrk?password=t58d\n"
        )
        source = next(iter_share_sources(content))
        self.assertEqual(
            (source.title, source.year, source.media_type, source.season, source.episode),
            ("余红旧事", 2026, "tv", 1, 16),
        )
        self.assertEqual((source.quality, source.audio), ("4K", "DDP5.1"))
        self.assertEqual(source.metadata["media_info"], "4K WEB-DL DDP 5 1")
        self.assertEqual(source.raw_label, "余红旧事")

    def test_parses_123_share_with_channel_heading(self) -> None:
        content = (
            "📺 剧集：兰香如故 (2026) S01E40\n"
            "📝 简介：一段简介\n"
            "🗂 分类：剧集/国产剧集\n"
            "🎞 版本：2160p TX.WEB-DL H.265 DDP 5.1\n"
            "🔗 链接：点击保存到123云盘 https://www.123865.com/s/Oqtgvd-XY9hh?pwd=YSRG\n"
        )
        source = next(iter_share_sources(content))
        self.assertEqual(
            (source.title, source.year, source.media_type, source.season, source.episode),
            ("兰香如故", 2026, "tv", 1, 40),
        )
        self.assertEqual((source.source_type, source.provider), ("123", "123"))
        self.assertEqual(source.source_key, "123:https://www.123865.com/s/Oqtgvd-XY9hh?pwd=YSRG")

    def test_parses_guangya_share_and_adjacent_password(self) -> None:
        source = next(iter_share_sources(
            "电影 (2024)\n访问码：abcd\n🔗 https://www.guangyapan.com/s/example"
        ))
        self.assertEqual((source.source_type, source.provider), ("guangya", "guangya"))
        self.assertEqual(source.url, "https://www.guangyapan.com/s/example?password=abcd")
        self.assertEqual(source.source_key, "guangya:https://www.guangyapan.com/s/example?password=abcd")

        source = next(iter_share_sources(
            "电影 (2024)\n🔗 https://www.guangyapan.com/s/next\n提取码：efgh"
        ))
        self.assertEqual(source.url, "https://www.guangyapan.com/s/next?password=efgh")

    def test_guangya_source_is_included_in_stats_and_filter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content("电影 (2024) https://guangyapan.com/s/example?pwd=abcd", "guangya.txt", db_path=db_path)
            self.assertEqual(get_stats(db_path)["sources"]["share_guangya"], 1)
            self.assertEqual(list_media(source_type="guangya", db_path=db_path)["total"], 1)

    def test_attaches_access_code_from_adjacent_forwarded_line(self) -> None:
        source = next(iter_share_sources(
            "电影 (2024)\n访问码：abcd\n🔗 https://115.com/s/example"
        ))
        self.assertEqual(source.url, "https://115.com/s/example?password=abcd")

        source = next(iter_share_sources(
            "电影 (2024)\n密码：YSRG\n🔗 https://123865.com/s/Oqtgvd-XY9hh"
        ))
        self.assertEqual(source.url, "https://123865.com/s/Oqtgvd-XY9hh?pwd=YSRG")

    def test_inferrs_tv_from_episode_range_without_marker(self) -> None:
        source = parse_share_line(
            "🔗 https://115cdn.com/s/example?password=x",
            context="死有对证（2026）\n📁 收录版本：E01-E15（双版本均带国语）",
        )
        self.assertEqual((source.title, source.year, source.media_type, source.season, source.episode), ("死有对证", 2026, "tv", 1, 1))
        self.assertEqual(source.metadata["episode_range"], [1, 15])

    def test_classifies_person_and_series_resource_headings(self) -> None:
        person = parse_share_line("🔗 https://115cdn.com/s/person?password=x", context="👤 陈百强\n🎂 生日：1958-09-07\n🏠 出生地：British Hong Kong\n📚 代表作：秋天的童话")
        series = parse_share_line("🔗 https://115cdn.com/s/series?password=x", context="🗂 角头（系列）\n📀 包含影片：角头、角头2")
        self.assertEqual((person.title, person.resource_kind, person.media_type), ("陈百强", "person", "unknown"))
        self.assertEqual(person.metadata["resource_metadata"]["birthday"], "1958-09-07")
        self.assertEqual((series.title, series.resource_kind), ("角头", "series"))
        self.assertEqual(series.metadata["resource_metadata"]["included_films"], "角头、角头2")

    def test_person_name_is_cleaned_and_birthday_year_dropped(self) -> None:
        person = parse_share_line(
            "🔗 https://115cdn.com/s/person2?password=x",
            context="👤 浅野心，\n🎂 生日：1994-01-01\n🏠 出生地：日本\n📚 代表作：作品一 / 作品二",
        )
        self.assertEqual((person.title, person.year, person.resource_kind), ("浅野心", None, "person"))
        self.assertEqual(person.metadata["resource_metadata"]["birthday"], "1994-01-01")

    def test_person_context_owns_all_share_lines_regardless_of_label(self) -> None:
        context = "👤 浅野心"
        source = parse_share_line(
            "全部资源 https://123865.com/s/bundle?pwd=abcd",
            context=context,
            person_context=context,
        )
        self.assertEqual((source.title, source.resource_kind), ("浅野心", "person"))
        # 没有人物上下文时，非通用标签仍是普通影视标题，行为不变。
        ordinary = parse_share_line("全部资源 https://123865.com/s/bundle2?pwd=abcd")
        self.assertEqual(ordinary.resource_kind, "media")

    def test_guangya_share_heading_uses_film_title_not_contributor_field(self) -> None:
        """光鸭云盘影视热更频道：`👤 分享：热心网友` 是投稿人字段，不是人物条目。"""
        content = (
            "🎬 斗破苍穹 (2017) 已更新\n\n"
            "🎭 类型：国产剧 · 动画 / 动作冒险\n"
            "📺 质量：WEB-DL H265 AAC\n"
            "📼 集数：第217集 / 全279集\n"
            "👤 分享：热心网友\n\n"
            "[第217集][无字片源].Fights.Break.Sphere.S01.2022.2160p.WEB-DL.H265.AAC-ColorWEB\n\n"
            "📖 简介：萧炎曾是家族里公认的斗气天才。\n"
            "🔎 TMDB：79481 https://www.themoviedb.org/tv/79481\n"
            "🏷 标签：#斗破苍穹 #国产剧\n\n"
            "按钮链接：\n"
            "🔗 光鸭云盘：查看资源 https://www.guangyapan.com/s/1963686782622208053_ad4bBsAqF4UM3yDM\n"
        )
        sources = list(iter_sources(content, channel="regengguangya"))
        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual((source.title, source.year), ("斗破苍穹", 2017))
        self.assertEqual((source.resource_kind, source.media_type), ("media", "tv"))
        self.assertEqual(source.provider, "guangya")
        self.assertEqual(source.metadata["explicit_tmdb"], [{"id": 79481, "type": "tv"}])
        # 上下文取消息自己的 🎬 标题块，而不是 `👤 分享：` 字段行。
        self.assertTrue(source.metadata["context"].startswith("🎬 斗破苍穹 (2017)"))
        self.assertEqual(source.metadata["channel_fields"]["label_分享"], "热心网友")

    def test_guangya_share_keeps_each_film_as_its_own_media(self) -> None:
        """同一频道的多条消息不能因为共享 `👤 分享：热心网友` 字段并进同一个人物条目。"""
        messages = [
            "🎬 小城良方 (2026) 已更新\n🖥 画质：2160p\n📼 集数：第23-24集 / 全29集\n👤 分享：热心网友\n\n"
            "🔎 TMDB：285625 https://www.themoviedb.org/tv/285625\n按钮链接：\n"
            "🔗 光鸭云盘：查看资源 https://www.guangyapan.com/s/1963688045803958272_ad4bBsAqF4UM3yDM\n",
            "🎬 流浪地球2 (2023) 已更新\n🖥 画质：2160p\n👤 分享：热心网友\n\n"
            "🔎 TMDB：842675 https://www.themoviedb.org/movie/842675\n按钮链接：\n"
            "🔗 光鸭云盘：查看资源 https://www.guangyapan.com/s/1960000000000000001_ad4bBsAqF4UM3yDM\n",
        ]
        parsed = [next(iter_sources(message, channel="光鸭云盘影视热更频道")) for message in messages]
        self.assertEqual([source.title for source in parsed], ["小城良方", "流浪地球2"])
        # `👤 分享：` 是投稿人字段：不能把资源归成人物，也不能沿用上一部片的信息。
        self.assertEqual([source.resource_kind for source in parsed], ["media", "media"])
        self.assertEqual([source.year for source in parsed], [2026, 2023])
        self.assertEqual((parsed[0].season, parsed[0].episode), (1, 23))
        self.assertEqual(parsed[0].metadata["episode_range"], [23, 24])
        self.assertIsNone(parsed[1].episode)

    def test_guangya_field_line_rule_is_scoped_to_that_channel(self) -> None:
        """其他频道即使出现同样的 `👤 分享：` 写法，也保持原有通用解析行为。"""
        content = (
            "🎬 斗破苍穹 (2017) 已更新\n"
            "👤 分享：热心网友\n"
            "🔗 链接： https://www.guangyapan.com/s/1963686782622208053_ad4bBsAqF4UM3yDM\n"
        )
        guangya = next(iter_sources(content, channel="@regengguangya"))
        self.assertEqual((guangya.title, guangya.resource_kind), ("斗破苍穹", "media"))

        # 未登记的频道（也没传频道）继续按通用规则处理，不被光鸭规则改写。
        for unknown in (None, "some_other_channel", "123云盘影视热更频道"):
            with self.subTest(channel=unknown):
                other = next(iter_sources(content, channel=unknown))
                self.assertEqual((other.title, other.resource_kind), ("分享：热心网友", "person"))

    def test_guangya_channel_is_recognised_by_name_and_message_url(self) -> None:
        """频道身份按用户名、频道名称或消息链接识别，和 lsp115 的判定方式一致。"""
        for channel, url in (
            ("regengguangya", None),
            ("@RegengGuangya", None),
            ("光鸭云盘影视热更频道", None),
            (None, "https://t.me/regengguangya/6269"),
        ):
            with self.subTest(channel=channel, url=url):
                self.assertTrue(is_guangya_movie_channel(channel, message_url=url))
        for channel, url in (
            ("regeng123", None),
            ("Lsp115", None),
            (None, "https://t.me/regeng123/100"),
            (None, None),
        ):
            with self.subTest(channel=channel, url=url):
                self.assertFalse(is_guangya_movie_channel(channel, message_url=url))

    def test_guangya_explicit_episode_wins_over_channel_total(self) -> None:
        """`📼 集数：第217集 / 全279集` 里「全 N 集」是总集数，不是本集区间。"""
        cases = {
            "第217集": (1, 217, None, 279),
            "第03集": (1, 3, None, 12),
            "第23-24集": (1, 23, [23, 24], 29),
            "第38-39集": (1, 38, [38, 39], 60),
        }
        for piece, (season, episode, episode_range, total) in cases.items():
            with self.subTest(piece=piece):
                details = parse_share_heading(f"🎬 小城良方 (2026) 已更新\n📼 集数：{piece} / 全{total}集")
                self.assertEqual((details["season"], details["episode"]), (season, episode))
                self.assertEqual(details["episode_range"], episode_range)
                self.assertEqual(details["episode_total"], total)

    def test_channel_total_alone_still_means_a_full_season(self) -> None:
        """没有明确集号时，「全 N 集」仍然表示整季区间（既有行为不变）。"""
        for heading, season, episode_range in (
            ("📺 深渊无间 (2026) 全16集", 1, [1, 16]),
            ("📺 末日地堡 (2023) 第三季 全10集", 3, [1, 10]),
        ):
            with self.subTest(heading=heading):
                details = parse_share_heading(heading)
                self.assertEqual(details["season"], season)
                self.assertEqual(details["episode_range"], episode_range)
                self.assertIsNone(details["episode_total"])

    def test_ed2k_person_context_cleans_name_and_drops_year(self) -> None:
        source = parse_ed2k_link(
            "ed2k://|file|浅野心2023合集.zip|40|" + "D" * 32 + "|/",
            context="👤 浅野心，\n🎂 生日：1994-01-01",
        )
        self.assertEqual((source.title, source.year, source.resource_kind), ("浅野心", None, "person"))

    def test_preserves_channel_fields_for_manual_scraping(self) -> None:
        source = next(iter_share_sources(
            "📺 毒枭：墨西哥 (2018) S1-S3全集\n"
            "🌟 评分： 7.9\n📽 类型： 剧情, 犯罪\n🌍 地区： United States of America\n"
            "👥 主演： 演员甲, 演员乙\n📖 简介： 一段简介\n"
            "🔗 链接： 点击跳转 https://115cdn.com/s/example?password=x\n"
        ))
        fields = source.metadata["channel_fields"]
        self.assertEqual(fields["rating"], "7.9")
        self.assertEqual(fields["genres"], "剧情, 犯罪")
        self.assertEqual(fields["cast"], "演员甲, 演员乙")
        self.assertNotIn("links", fields)
        self.assertNotIn("raw_lines", fields)
        self.assertEqual(source.url, "https://115cdn.com/s/example?password=x")

    def test_empty_fragment_does_not_change_share_source_key(self) -> None:
        source = parse_share_line("标题 (2024) https://115.com/s/example?password=abcd#")
        self.assertEqual(source.source_key, "115:https://115.com/s/example?password=abcd")
        self.assertEqual(source.url, "https://115.com/s/example?password=abcd")

    def test_115_share_domain_is_used_in_source_key(self) -> None:
        source = parse_share_line("标题 (2024) https://115.com/s/example?password=abcd")
        self.assertEqual(source.source_key, "115:https://115.com/s/example?password=abcd")

    def test_preserves_bracketed_title(self) -> None:
        source = parse_share_line(
            "【我推的孩子】 (2023)\thttps://115.com/s/example3?password=ijkl#"
        )
        self.assertEqual((source.title, source.year), ("【我推的孩子】", 2023))

    def test_parses_episode_and_release_metadata(self) -> None:
        link = (
            "ed2k://|file|House.of.the.Dragon.S03E01.2022.2160p.HMAX.WEB-DL."
            "DDP5.1.Atmos.DV.HDR.H.265-HiveWeb.mkv|6998230498|"
            "A277EA4E2EFF8A4944104C470558F650|/"
        )
        source = parse_ed2k_link(link)
        self.assertEqual(source.title, "House of the Dragon")
        self.assertEqual((source.year, source.season, source.episode), (2022, 3, 1))
        self.assertEqual(source.quality, "2160P")
        self.assertEqual(source.codec, "H.265")
        self.assertEqual(source.hdr, "DV + HDR")
        self.assertEqual(source.release_group, "HiveWeb")

    def test_parses_complete_multi_season_share(self) -> None:
        source = parse_share_line(
            "🎬 毒枭：墨西哥 (2018) S1-S3全集\thttps://115cdn.com/s/example?password=o599#"
        )
        self.assertEqual((source.title, source.year, source.season, source.episode), ("毒枭：墨西哥", 2018, 1, None))
        self.assertEqual(source.season_end, 3)
        self.assertEqual(source.metadata["season_range"], [1, 3])
        self.assertEqual(source.media_type, "tv")

    def test_infer_share_media_type_uses_structural_evidence(self) -> None:
        self.assertEqual(infer_share_media_type("X", "X 更新至04集"), "tv")
        self.assertEqual(infer_share_media_type("X", "X 更至EP06"), "tv")
        self.assertEqual(infer_share_media_type("X", "X 第一季"), "tv")
        self.assertEqual(infer_share_media_type("X", "X 第一集"), "tv")
        self.assertEqual(infer_share_media_type("X", "X 更新07 08"), "tv")
        self.assertEqual(infer_share_media_type("X", "X 剧场版"), "movie")
        self.assertEqual(infer_share_media_type("X", "X 电影"), "movie")
        # Both a movie marker and episode structure are conflicting evidence.
        self.assertEqual(infer_share_media_type("X", "X 电影 第1集"), "unknown")
        # 电影简介里提到「电视剧集」只是剧情介绍，不能把电影判成剧集。
        synopsis = "人类清除计划 The.Purge.2013\n剧情简介：本片后来开发成一部电视剧集，剧集版由电视部门拍摄。"
        self.assertEqual(infer_share_media_type("人类清除计划", synopsis), "unknown")

    def test_theatrical_version_is_a_movie(self) -> None:
        source = parse_share_line(
            "📺 鬼灭之刃剧场版：无限列车篇\thttps://115cdn.com/s/theatrical?password=x#"
        )
        self.assertEqual((source.media_type, source.season, source.episode), ("movie", None, None))

    def test_progress_without_ji_suffix_is_tv(self) -> None:
        source = parse_share_line(
            "📺 时光大盗 更新07 08\thttps://115cdn.com/s/progress?password=x#"
        )
        self.assertEqual(source.media_type, "tv")
        self.assertIsNotNone(source.episode)

    def test_channel_emoji_alone_does_not_infer_media_type(self) -> None:
        for marker in ("📺", "🎥", "🎬"):
            with self.subTest(marker=marker):
                source = parse_share_line(
                    f"{marker} 无类型标题 (2026)\thttps://115cdn.com/s/icon-only?password=x#"
                )
                self.assertEqual((source.title, source.year, source.media_type), ("无类型标题", 2026, "unknown"))

    def test_channel_emoji_does_not_override_series_evidence(self) -> None:
        # 有频道给所有投稿（包括剧集全集）统一加 🎬；季集/全集等结构线索必须优先。
        source = parse_share_line(
            "🔗 链接： 点击跳转 https://115cdn.com/s/zzrs?password=x#",
            context="🎬 蒸蒸日上 (2026) 全集",
        )
        self.assertEqual((source.title, source.year, source.media_type), ("蒸蒸日上", 2026, "tv"))
        one_line = parse_share_line("🎬 蒸蒸日上 (2026) 全集\thttps://115cdn.com/s/zzrs?password=x#")
        self.assertEqual((one_line.title, one_line.year, one_line.media_type), ("蒸蒸日上", 2026, "tv"))
        movie = parse_share_line("🎬 一般电影 (2026)\thttps://115cdn.com/s/d?password=x#")
        self.assertEqual(movie.media_type, "movie")

    def test_tv_share_without_episode_defaults_to_season_one(self) -> None:
        # 没有季集线索的剧集资源默认视为整季：记为 S01（episode 为空）。
        source = parse_share_line(
            "🔗 链接： 点击跳转 https://115cdn.com/s/jjc?password=x#",
            context="📺 剧集：嫁金钗 (2026)",
        )
        self.assertEqual((source.media_type, source.season, source.episode), ("tv", 1, None))
        with_episode = parse_share_line(
            "🔗 链接： 点击跳转 https://115cdn.com/s/jjc2?password=x#",
            context="📺 剧集：嫁金钗 (2026) S02E05",
        )
        self.assertEqual((with_episode.season, with_episode.episode), (2, 5))

    def test_persists_multi_season_end_in_source_record(self) -> None:
        content = "🎬 毒枭：墨西哥 (2018) S1-S3全集\thttps://115cdn.com/s/season-pack?password=o599#"
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content(content, "season-pack.txt", db_path=db_path)
            item = get_media(list_media(db_path=db_path)["items"][0]["id"], db_path)

        self.assertEqual((item["sources"][0]["season"], item["sources"][0]["season_end"]), (1, 3))

    def test_parses_chinese_complete_season_total(self) -> None:
        source = parse_share_line(
            "神探夏洛克 (2010) 神探夏洛克 全4季\thttps://115cdn.com/s/sherlock?password=x#"
        )
        self.assertEqual((source.title, source.year, source.media_type), ("神探夏洛克", 2010, "tv"))
        self.assertEqual((source.season, source.season_end, source.episode), (1, 4, None))
        self.assertEqual(source.metadata["season_range"], [1, 4])

    def test_parses_chinese_season_variants(self) -> None:
        shared = parse_share_line("某剧 (2020) 共3季\thttps://115cdn.com/s/a?password=x#")
        self.assertEqual((shared.season, shared.season_end), (1, 3))
        span = parse_share_line("某剧 (2020) 第2-5季\thttps://115cdn.com/s/b?password=x#")
        self.assertEqual((span.season, span.season_end), (2, 5))
        single = parse_share_line("某剧 (2020) 全1季\thttps://115cdn.com/s/c?password=x#")
        self.assertEqual((single.season, single.season_end), (1, None))

    def test_persists_chinese_season_total_in_source_record(self) -> None:
        content = "神探夏洛克 (2010) 神探夏洛克 全4季\thttps://115cdn.com/s/sherlock?password=x#"
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content(content, "sherlock.txt", db_path=db_path)
            item = get_media(list_media(db_path=db_path)["items"][0]["id"], db_path)

        self.assertEqual((item["sources"][0]["season"], item["sources"][0]["season_end"]), (1, 4))

    def test_parses_chinese_episode_span(self) -> None:
        source = parse_share_line("🎬 魅影神捕 (2026) 1-25集\thttps://115cdn.com/s/a?password=x#")
        self.assertEqual((source.title, source.year, source.media_type), ("魅影神捕", 2026, "tv"))
        self.assertEqual((source.season, source.episode), (1, 1))
        self.assertEqual(source.metadata["episode_range"], [1, 25])

    def test_parses_four_digit_episode_spans(self) -> None:
        source = parse_share_line("🎬 名侦探柯南：犯人犯泽先生 (2022) TV版1-1275集\thttps://115cdn.com/s/a?password=x#")
        self.assertEqual((source.title, source.season, source.episode), ("名侦探柯南：犯人犯泽先生", 1, 1))
        self.assertEqual(source.metadata["episode_range"], [1, 1275])

    def test_chinese_season_number_keeps_its_season(self) -> None:
        source = parse_share_line("🎬 诛仙合集篇 (2025) 诛仙第四季1~8集\thttps://115cdn.com/s/b?password=x#")
        self.assertEqual((source.title, source.season, source.episode), ("诛仙合集篇", 4, 1))
        self.assertEqual(source.metadata["episode_range"], [1, 8])

    def test_single_episode_and_movie_ignore_episode_spans(self) -> None:
        episode = parse_share_line("📺 征途 (2026) S01E14 4K\thttps://115cdn.com/s/c?password=x#")
        self.assertEqual((episode.season, episode.episode), (1, 14))
        self.assertIsNone(episode.metadata.get("episode_range"))
        movie = parse_share_line("🎬 一般电影 (2026)\thttps://115cdn.com/s/d?password=x#")
        self.assertEqual((movie.media_type, movie.season, movie.episode), ("movie", None, None))

    def test_parses_arabic_season_and_update_progress(self) -> None:
        growing = parse_share_line("📺 魅影神捕 (2026) 第1季 更新至第17集\thttps://115cdn.com/s/a?password=x#")
        self.assertEqual((growing.title, growing.season, growing.episode), ("魅影神捕", 1, 1))
        self.assertEqual(growing.metadata["episode_range"], [1, 17])
        single = parse_share_line("🎬 我不是大师 (2026) 第19集\thttps://115cdn.com/s/b?password=x#")
        self.assertEqual((single.season, single.episode), (1, 19))
        self.assertIsNone(single.metadata.get("episode_range"))

    def test_progress_of_a_later_season_is_metadata_not_this_season_episodes(self) -> None:
        source = parse_share_line("📺 唐朝诡事录 (2022) 共4季 第4季更新至第21集\thttps://115cdn.com/s/c?password=x#")
        self.assertEqual((source.season, source.season_end, source.episode), (1, 4, None))
        self.assertEqual(source.metadata["latest_episode"], {"season": 4, "episode": 21})
        self.assertIsNone(source.metadata.get("episode_range"))

    def test_extracts_tmdb_ids_from_official_links(self) -> None:
        self.assertEqual(extract_tmdb_ids("https://www.themoviedb.org/tv/282326"), [282326])
        self.assertEqual(
            extract_tmdb_ids("⭐️ TMDB评分：6.8/10 https://www.themoviedb.org/movie/1381078?language=zh-CN"),
            [1381078],
        )
        self.assertEqual(extract_tmdb_ids("{tmdbid=289078} 和 https://www.themoviedb.org/tv/282326"), [289078, 282326])

    def test_attaches_message_level_tmdb_id_and_quality_tags(self) -> None:
        message = (
            "📺 剧集：兰香如故 (2026) S01E40\n"
            "🎞 版本：2160p TX.WEB-DL H.265 DDP 5.1\n"
            "🔗 链接：点击保存到123云盘 https://www.123865.com/s/Oqtgvd-XY9hh?pwd=YSRG\n"
            "⭐️ TMDB评分：6.8/10 https://www.themoviedb.org/tv/282326"
        )
        source = next(iter(iter_sources(message, detect_source_kind(message))))
        self.assertEqual(source.metadata["explicit_tmdb_ids"], [282326])
        self.assertEqual(source.metadata["quality_tags"], ["2160p", "WEB-DL", "H.265", "DDP5.1"])
        self.assertEqual((source.quality, source.codec, source.audio), ("2160P", "H.265", "DDP5.1"))

    def test_extracts_quality_tags_from_headings(self) -> None:
        self.assertEqual(
            extract_quality_tags("🎬 神探夏洛克 (2010) 全4季 蓝光原盘REMUX DIY原盘中字"),
            ["蓝光", "原盘", "REMUX"],
        )
        self.assertEqual(extract_quality_tags("🎬 野狗骨头[全32集][臻享超高清]"), ["超高清"])
        self.assertIn("Dolby Vision", extract_quality_tags("2160p WEB-DL Dolby Vision H.265 DDP5.1 Atmos"))
        self.assertEqual(extract_quality_tags("🎬 普通电影 (2026)"), [])

    def test_chinese_carrier_words_fill_the_quality_tier(self) -> None:
        bluray = parse_share_line("🎬 神探夏洛克 (2010) 全4季 蓝光原盘REMUX\thttps://115cdn.com/s/a?password=x#")
        self.assertEqual(bluray.quality, "1080P")
        self.assertEqual(bluray.metadata["quality_tags"], ["蓝光", "原盘", "REMUX"])
        uhd = parse_share_line("🎬 某片 (2000) [美版UHD原盘].iso\thttps://115cdn.com/s/b?password=x#")
        self.assertEqual((uhd.quality, uhd.metadata["quality_tags"]), ("2160P", ["UHD", "原盘"]))
        # 「臻享超高清」只作为标签，不猜分级。
        vague = parse_share_line("🎬 野狗骨头 (2026) [全32集][臻享超高清]\thttps://115cdn.com/s/c?password=x#")
        self.assertIsNone(vague.quality)
        self.assertEqual(vague.metadata["quality_tags"], ["超高清"])

    def test_bare_number_span_is_only_a_fallback_after_the_title_year(self) -> None:
        fallback = parse_share_line("🎬 兰香如故 (2026) 1-41\thttps://115cdn.com/s/a?password=x#")
        self.assertEqual((fallback.season, fallback.episode), (1, 1))
        self.assertEqual(fallback.metadata["episode_range"], [1, 41])
        # 已有季标记或区间不在年份之后时，备用方案不生效。
        self.assertIsNone(parse_share_line("🎬 某剧 (2020) 第1季 1-41\thttps://115cdn.com/s/b?password=x#").metadata.get("episode_range"))
        misplaced = parse_share_line("🎬 某剧 (2020) 中字 1-41\thttps://115cdn.com/s/c?password=x#")
        self.assertIsNone(misplaced.metadata.get("episode_range"))
        self.assertIsNone(misplaced.episode)

    def test_extracts_multiple_ed2k_links_from_one_line(self) -> None:
        content = (
            "ed2k://|file|Show.S01E01.2026.1080p.WEB-DL.mkv|100|"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/ "
            "ed2k://|file|Show.S01E02.2026.1080p.WEB-DL.mkv|101|"
            "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB|/"
        )
        sources = list(iter_ed2k_sources(content))
        self.assertEqual(len(sources), 2)
        self.assertEqual([source.episode for source in sources], [1, 2])

    def test_release_group_bracket_is_not_part_of_title(self) -> None:
        label = (
            "名称: [LGNB顶级封装][梦比优斯奥特曼和奥特兄弟][4K60帧修复]"
            "[日语+台配国语+广州新时代国语+新创华国语+广配国语+粤语+英语+导评]"
            "[台配国语中文字幕+新时代国语中文字幕+新创华国语中文字幕+广配国语中文字幕+粤语中文字幕]"
            "[MKV][16.31GB][Ultraman.Mebius.and.Ultra.Brothers.2006.Bluray.REMUX.AI.2160p.60fps.AVC.DD.5.1-LGNB@oSpecialCN]"
        )
        source = parse_share_line(f"{label}\nhttps://115.com/s/mebius?password=x")
        self.assertEqual(source.title, "梦比优斯奥特曼和奥特兄弟")
        self.assertEqual(source.year, 2006)
        self.assertEqual(source.quality, "4K")

        truncated = parse_share_heading("名称: [LGNB顶级封装][疯狂的麦克斯2")
        self.assertEqual(truncated["title"], "疯狂的麦克斯2")
        self.assertEqual(parse_share_heading("名称: [LGNB封装][圣斗士星矢.真人版][BD")["title"], "圣斗士星矢.真人版")
        # 没有 release-group 前缀的方括号标题不能被误剥；「全球」等词也可能
        # 是正常片名的一部分，不能只因出现关键词就跳掉整个括号。
        self.assertEqual(parse_share_heading("[Spider-Man][2024]")["title"], "Spider-Man")
        self.assertEqual(parse_share_heading("[全球风暴][2017]")["title"], "全球风暴")
        self.assertEqual(parse_share_heading("[制作人][2024]")["title"], "制作人")

    def test_series_heading_keeps_the_filename_as_the_film_title(self) -> None:
        context = "📺 聊斋艳谭（系列）"
        first = parse_ed2k_link(
            "ed2k://|file|山中艳谭（完整版）.1995.LDRip.2AC3.HKFACT_prob3.mp4|"
            "6955916275|1B85B53629F4F596AFE87E1B14E7EFA8|/",
            context=context,
        )
        second = parse_ed2k_link(
            "ed2k://|file|www.98T.la@聊斋之艳蛇 (1994) 1080p AC3.mkv|"
            "10385530513|DB120387C374564F9E791E46F81FB2E6|/",
            context=context,
        )
        self.assertEqual((first.title, first.year), ("山中艳谭（完整版）", 1995))
        self.assertEqual((second.title, second.year), ("聊斋之艳蛇", 1994))
        self.assertEqual(first.metadata["series_title"], "聊斋艳谭")
        self.assertTrue(first.metadata["collection"])

    def test_ed2k_filename_strips_site_prefix_and_preserves_title_brackets(self) -> None:
        first = parse_ed2k_link(
            "ed2k://|file|山中艳谭（完整版）.1995.LDRip.2AC3.HKFACT_prob3.mp4|"
            "6955916275|1B85B53629F4F596AFE87E1B14E7EFA8|/"
        )
        second = parse_ed2k_link(
            "ed2k://|file|www.98T.la@聊斋之艳蛇 (1994) 1080p AC3.mkv|"
            "10385530513|DB120387C374564F9E791E46F81FB2E6|/"
        )
        self.assertEqual((first.title, first.year), ("山中艳谭（完整版）", 1995))
        self.assertEqual((second.title, second.year), ("聊斋之艳蛇", 1994))
        self.assertEqual(
            parse_release_title("电影（导演剪辑版）.2020.1080p.mkv"),
            ("电影（导演剪辑版）", 2020),
        )
        # 导出截断/脏数据留下的单边括号仍然清掉。
        self.assertEqual(parse_release_title("片名）.2020.mkv"), ("片名", 2020))

    def test_handles_leading_year_and_year_named_movie(self) -> None:
        leading = parse_ed2k_link(
            "ed2k://|file|1993 三分之一情人.AI修复.mp4|2041552713|"
            "D0EAE41AA4706DADC83CD8BBBA44523A|/"
        )
        year_named = parse_ed2k_link(
            "ed2k://|file|1917.2019.1080p.BluRay.x264.mkv|100|"
            "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC|/"
        )
        self.assertEqual((leading.title, leading.year), ("三分之一情人", 1993))
        self.assertEqual((year_named.title, year_named.year), ("1917", 2019))

    def test_parses_release_alias_and_ignores_resolution_typo(self) -> None:
        alias = parse_release_title(
            "A.HOUSE.OF.DYNAMITE.2025.1080p.NF.WEB-DL.DDP5.1.mkv 访问码：z8a9"
        )
        typo = parse_release_title(
            "From.Russia.With.Love.1964.2025p.AMZN.WEB-DL.DDP5.1.mkv"
        )
        self.assertEqual(alias, ("A House Of Dynamite", 2025))
        self.assertEqual(typo, ("From Russia With Love", 1964))

    def test_extracts_explicit_tmdb_id(self) -> None:
        self.assertEqual(
            extract_tmdb_ids("五等分的新娘＊ (2024) {tmdbid-1287324} 访问码：l265"),
            [1287324],
        )

    def test_extracts_title_after_release_source_marker(self) -> None:
        aliases = parse_release_aliases(
            "计程车女孩.1979.BDRip.Taxi.Girls.USA.AC3.FFans@至尊宝.mkv"
        )
        self.assertEqual(aliases, ["计程车女孩", "Taxi Girls"])

    def test_import_is_idempotent_and_groups_episodes(self) -> None:
        content = "\n".join(
            [
                "ed2k://|file|Show.Name.S01E01.2026.1080p.WEB-DL.mkv|100|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/",
                "ed2k://|file|Show.Name.S01E02.2026.1080p.WEB-DL.mkv|101|BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB|/",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            first = import_content(content, "episodes.txt", db_path=db_path)
            second = import_content(content, "episodes.txt", db_path=db_path)
            stats = get_stats(db_path)
            page = list_media(db_path=db_path)

        self.assertEqual(first["inserted"], 2)
        self.assertEqual(second["duplicates"], 2)
        self.assertEqual(stats["media"]["total"], 1)
        self.assertEqual(stats["sources"]["total"], 2)
        self.assertEqual(page["items"][0]["episode_count"], 2)

    def test_reimport_refreshes_old_unknown_share_placeholder(self) -> None:
        old = "🔗 链接： 点击跳转 https://115cdn.com/s/refresh?password=abcd"
        corrected = (
            "📺 余红旧事 (2026) S01E16 ✨4K WEB-DL DDP 5 1\n"
            "🔗 链接： 点击跳转 https://115cdn.com/s/refresh?password=abcd"
        )
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content(old, "old.txt", db_path=db_path)
            result = import_content(corrected, "corrected.txt", db_path=db_path)
            page = list_media(db_path=db_path)
            source = get_media(page["items"][0]["id"], db_path)["sources"][0]

        self.assertEqual(result["duplicates"], 1)
        self.assertEqual((page["items"][0]["title"], page["items"][0]["year"]), ("余红旧事", 2026))
        self.assertEqual((source["season"], source["episode"], source["quality"], source["audio"]), (1, 16, "4K", "DDP5.1"))

    def test_tmdb_link_corrects_inferred_media_type(self) -> None:
        content = (
            "电影名 (2025)\thttps://115cdn.com/s/example4?password=mnop# "
            "Movie.Name.S01E01.2025.1080p.mkv"
        )
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            import_content(content, "wrong-type.txt", db_path=db_path)
            media_id = list_media(db_path=db_path)["items"][0]["id"]
            link_tmdb(
                media_id,
                {"tmdb_id": 123, "media_type": "movie", "title": "电影名"},
                0.98,
                "auto",
                db_path,
            )
            item = get_media(media_id, db_path)

        self.assertEqual(item["media_type"], "movie")


class ShareHeadingCleaningTests(unittest.TestCase):
    """字段式消息把标签/集数/画质写进首行，标题必须只取真正的片名。"""

    def test_label_prefix_is_not_part_of_the_title(self) -> None:
        for heading, expected in (
            ("名称：飞驰人生2（2024）", "飞驰人生2"),
            ("名称: 大明王朝1566", "大明王朝1566"),
            ("资源名称：沙丘2", "沙丘2"),
            ("片名：疾速追杀4", "疾速追杀4"),
        ):
            with self.subTest(heading=heading):
                self.assertEqual(parse_share_heading(heading)["title"], expected)

    def test_label_prefix_keeps_the_year_from_the_value(self) -> None:
        self.assertEqual(parse_share_heading("名称：飞驰人生2（2024）")["year"], 2024)

    def test_series_marker_names_the_collection_not_the_film(self) -> None:
        # 频道给系列里每一部单独发消息，首行只写系列名；标记要保留成「属于哪个系列」。
        for heading, expected in (
            ("📺 007(系列)", "007"),
            ("📺 黑夜传说 (系列)", "黑夜传说"),
            ("📺 速度与激情（系列）国英双语", "速度与激情"),
            ("📺 霍比特人（系列", "霍比特人"),
        ):
            with self.subTest(heading=heading):
                details = parse_share_heading(heading)
                self.assertEqual(details["title"], expected)
                self.assertTrue(details["collection"])

    def test_complete_season_marker_is_not_a_collection(self) -> None:
        # 「全集」是整季齐了，不是系列合集：这类剧集照常按单片（整季）入库。
        details = parse_share_heading("📺 兰香如故 (2026) 全集")
        self.assertEqual(details["title"], "兰香如故")
        self.assertFalse(details["collection"])

    def test_collection_suffix_variants_are_recognized(self) -> None:
        # 频道不只写「（系列）」：合集/合辑/套装/套盒/全系列/部曲 都表示资源包。
        for heading, expected in (
            ("📺 成龙电影合集", "成龙电影"),
            ("📺 火影忍者全系列绝版大合集", "火影忍者"),
            ("📺 法证先锋全系列1-6", "法证先锋"),
            ("📺 猛鬼街全系列1080p蓝光", "猛鬼街"),
            ("📺 燕双鹰前三部曲", "燕双鹰"),
            ("📺 龙游天下三部曲", "龙游天下"),
            ("📺 指环王 全3部 加长版", "指环王"),
            ("📺 生化危机法版4k原盘remux全六部", "生化危机法版"),
            ("📺 疾速追杀5部", "疾速追杀"),
            ("📺 哆啦A梦剧场版 21部国语版本", "哆啦A梦剧场版"),
            ("📺 生化危机1~6部]", "生化危机"),
            ("📺 招魂系列", "招魂"),
            ("📺 周星驰合辑", "周星驰"),
            ("📺 漫威套装", "漫威"),
            ("📺 指环王套盒", "指环王"),
            ("📺 火影合集包", "火影"),
            ("📺 漫威系列电影", "漫威"),
        ):
            with self.subTest(heading=heading):
                details = parse_share_heading(heading)
                self.assertEqual(details["title"], expected)
                self.assertTrue(details["collection"])

    def test_collection_words_inside_real_titles_do_not_false_kill(self) -> None:
        for heading, expected in (
            ("🎬 诛仙合集篇 (2025)", "诛仙合集篇"),
            ("🎬 性梦爱三部曲性 (2024)", "性梦爱三部曲性"),
            ("🎬 性梦爱三部曲梦 (2024)", "性梦爱三部曲梦"),
            ("🎬 套装 (2022)", "套装"),
            ("🎬 蓝白红三部曲之红 (1994)", "蓝白红三部曲之红"),
            ("🎬 天龙八部 (1997)", "天龙八部"),
            ("🎬 封神第一部：朝歌风云 (2023)", "封神第一部：朝歌风云"),
            ("🎬 一部塞尔维亚电影 (2010)", "一部塞尔维亚电影"),
            ("🎬 九部的检察官 (2024)", "九部的检察官"),
        ):
            with self.subTest(heading=heading):
                details = parse_share_heading(heading)
                self.assertEqual(details["title"], expected)
                self.assertFalse(details["collection"])

    def test_update_progress_without_the_episode_unit_is_cut(self) -> None:
        for heading in ("📺 斗战天下 高码率  更新至26", "📺 剑来 更至10", "📺 逐玉 更新07"):
            with self.subTest(heading=heading):
                self.assertEqual(parse_share_heading(heading)["title"], heading.split()[1])

    def test_quality_noise_words_are_cut(self) -> None:
        for heading, expected in (
            ("📺 破碎的愤怒 HDR10", "破碎的愤怒"),
            ("📺 人偶之家 1080", "人偶之家"),
            ("📺 沧元图 杜比视界", "沧元图"),
        ):
            with self.subTest(heading=heading):
                self.assertEqual(parse_share_heading(heading)["title"], expected)

    def test_unclosed_bracket_keeps_the_text_before_it(self) -> None:
        for heading, expected in (
            ("📺 星球大战（系列", "星球大战"),
            ("📺 逐玉（1-15", "逐玉"),
            ("📺 非常了得(2011-2014", "非常了得"),
        ):
            with self.subTest(heading=heading):
                self.assertEqual(parse_share_heading(heading)["title"], expected)

    def test_collection_marker_is_stripped_for_any_kind(self) -> None:
        self.assertEqual(parse_share_heading("📺 生化危机（系列）")["title"], "生化危机")
        self.assertEqual(parse_share_heading("🗂 老虎出更（系列）")["title"], "老虎出更")

    def test_real_sequel_titles_keep_their_number(self) -> None:
        for heading in ("🎥 疾速追杀2", "🎥 美国队长4", "🎥 惊声尖叫7"):
            with self.subTest(heading=heading):
                self.assertEqual(parse_share_heading(heading)["title"], heading.split()[1])

    def test_date_heading_is_not_a_release_year(self) -> None:
        # 括号里的日期是这一集的更新日期，不是作品年份；只从标题剥掉。
        details = parse_share_heading("🎬 凡人修仙传172 (2020-07-25)")
        self.assertEqual(details["title"], "凡人修仙传172")
        self.assertIsNone(details["year"])

    def test_strip_search_noise_only_for_episodic_or_large_numbers(self) -> None:
        self.assertEqual(strip_search_noise("凡人修仙传172", episodic=True), "凡人修仙传")
        self.assertEqual(strip_search_noise("仙逆 EP101", episodic=True), "仙逆")
        self.assertEqual(strip_search_noise("破碎的愤怒 HDR10"), "破碎的愤怒")
        # 续集数字不能被当作集数，否则会关联到系列第一部。
        self.assertIsNone(strip_search_noise("疾速追杀2", episodic=False))
        self.assertIsNone(strip_search_noise("美国队长4", episodic=False))

    def test_strip_search_noise_returns_none_when_nothing_to_strip(self) -> None:
        self.assertIsNone(strip_search_noise("飞驰人生2", episodic=False))
        self.assertIsNone(strip_search_noise("", episodic=True))


if __name__ == "__main__":
    unittest.main()
