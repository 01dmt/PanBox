from __future__ import annotations

import tempfile
import json
import unittest
from pathlib import Path

from backend.importers import (
    detect_source_kind,
    extract_quality_tags,
    extract_tmdb_ids,
    iter_ed2k_sources,
    iter_share_sources,
    iter_sources,
    parse_ed2k_link,
    parse_release_aliases,
    parse_release_title,
    parse_share_line,
    split_joined_title,
)
from backend.repository import (
    get_media, get_stats, import_content, link_tmdb, list_media, save_candidates, update_media_fields,
)


class ImporterTests(unittest.TestCase):
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
            "Lady Gaga：方寸混沌之美（科切拉音乐节", "MAYHEM In The Desert Lady Gaga Live At Coachella",
        ])

    def test_year_in_release_group_is_not_the_film_year(self) -> None:
        self.assertEqual(parse_release_title("Movie.1999.1080p.BluRay-Team2025.mkv"), ("Movie", 1999))

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


if __name__ == "__main__":
    unittest.main()
