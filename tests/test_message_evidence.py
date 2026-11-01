from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.message_evidence import (
    message_evidence_for_media,
    parse_message_evidence,
    resolve_message_candidate,
    select_message_candidate,
)
from backend.repository import utc_now
from backend.tmdb import link_message_evidence
from backend.schema import connect, init_db


class MessageEvidenceTests(unittest.TestCase):
    def test_explicit_name_and_release_date_are_parsed(self) -> None:
        evidence = parse_message_evidence(
            "名称: 绝世战魂 4K臻彩 更新至105\n\n"
            "发行时间: 2024-04-27\n\n"
            "剧情简介: 四大宗门之首的玄灵宗……"
        )
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence.title, "绝世战魂")
        self.assertEqual(evidence.year, 2024)
        self.assertTrue(evidence.standardized)

    def test_icon_heading_and_date_are_parsed(self) -> None:
        evidence = parse_message_evidence(
            "🎬 南京照相馆 (2025-07-19)\n\n"
            "🏷 剧情 / 战争 / 历史\n"
        )
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence.title, "南京照相馆")
        self.assertEqual(evidence.year, 2025)

    def test_generic_title_without_year_is_not_evidence(self) -> None:
        self.assertIsNone(parse_message_evidence("名称: 美剧[最后生还者 第二季]更新至6集"))
        self.assertIsNone(parse_message_evidence("🎬 时光大盗"))

    def test_exact_title_and_year_selects_only_the_matching_candidate(self) -> None:
        evidence = parse_message_evidence("名称: 绝世战魂 4K臻彩 更新至105\n发行时间: 2024-04-27")
        assert evidence is not None
        candidates = [
            {"tmdb_id": 252788, "title": "绝世战魂", "media_type": "tv", "release_date": "2024-04-27"},
            {"tmdb_id": 250817, "title": "绝世战魂", "media_type": "tv", "release_date": "2021-05-12"},
        ]
        chosen = select_message_candidate(evidence, candidates, "tv")
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen["tmdb_id"], 252788)

    def test_total_edition_does_not_win_over_exact_title(self) -> None:
        evidence = parse_message_evidence("名称: 宗门里除了我都是卧底 (2024)")
        assert evidence is not None
        candidates = [
            {"tmdb_id": 274149, "title": "宗门里除了我都是卧底", "media_type": "tv", "release_date": "2024-10-14"},
            {"tmdb_id": 313419, "title": "宗门里除了我都是卧底（总集版）", "media_type": "tv", "release_date": "2024-10-14"},
        ]
        chosen = select_message_candidate(evidence, candidates, "tv")
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen["tmdb_id"], 274149)

    def test_same_year_but_wrong_title_is_rejected(self) -> None:
        # 「诛仙」的消息年份也是 2024，但候选是另一部剧，不能因为年份相同而绑定。
        evidence = parse_message_evidence("名称：诛仙（2024） 4K 臻彩 更至EP43")
        assert evidence is not None
        candidates = [
            {"tmdb_id": 249261, "title": "我在养老院里学诛仙", "media_type": "tv", "release_date": "2024-02-09"},
        ]
        self.assertIsNone(select_message_candidate(evidence, candidates, "tv"))

    def test_later_season_is_left_for_manual_review(self) -> None:
        evidence = parse_message_evidence(
            "名称: 神秘博士 第二季 4K杜比视界更新至03\n发行时间: 2024-05-11"
        )
        assert evidence is not None
        candidates = [
            {"tmdb_id": 239770, "title": "神秘博士", "media_type": "tv", "release_date": "2024-05-11"},
        ]
        self.assertIsNone(select_message_candidate(evidence, candidates, "tv"))

    def test_message_type_hint_can_repair_a_local_type_flip(self) -> None:
        evidence = parse_message_evidence(
            "🎬 南京照相馆 (2025-07-19)\n\n🏷 剧情 / 战争 / 历史\n🔖 标签: #南京照相馆 #电影"
        )
        assert evidence is not None
        self.assertEqual(evidence.media_type_hint, "movie")
        candidates = [
            {"tmdb_id": 1500536, "title": "南京照相馆", "media_type": "movie", "release_date": "2025-07-19"},
        ]
        chosen = select_message_candidate(evidence, candidates, "tv")
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen["media_type"], "movie")

    def test_mixed_events_are_not_used_for_a_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            init_db(db_path)
            with connect(db_path) as db:
                now = utc_now()
                media_id = db.execute(
                    "INSERT INTO media_items (title, normalized_title, media_type, created_at, updated_at) "
                    "VALUES ('混合行', '混合行', 'tv', ?, ?)",
                    (now, now),
                ).lastrowid
                db.execute(
                    "INSERT INTO ingestion_events (id, event_id, service, raw_text, received_at) "
                    "VALUES ('ev-a', 'ev-a', 'telegram', ?, ?)",
                    ("名称: 师兄啊师兄 更新至75", now),
                )
                db.execute(
                    "INSERT INTO ingestion_events (id, event_id, service, raw_text, received_at) "
                    "VALUES ('ev-b', 'ev-b', 'telegram', ?, ?)",
                    ("名称：仙逆 4K 臻彩 更至 EP50", now),
                )
                for index, event_id in enumerate(('ev-a', 'ev-b'), start=1):
                    source_id = db.execute(
                        "INSERT INTO source_records (media_id, source_type, source_key, raw_label, raw_text, url, created_at) "
                        "VALUES (?, 'ed2k', ?, 'x', 'x', 'ed2k://x', ?)",
                        (media_id, f"mixed-{index}", now),
                    ).lastrowid
                    db.execute(
                        "INSERT INTO ingestion_links (ingestion_id, source_key, provider, source_type, url, media_id) "
                        "VALUES (?, ?, '115', 'ed2k', 'ed2k://x', ?)",
                        (event_id, f"mixed-{index}", media_id),
                    )
            with connect(db_path) as db:
                self.assertIsNone(message_evidence_for_media(db, int(media_id)))


class MessageEvidenceResolutionTests(unittest.TestCase):
    def test_link_message_evidence_backfills_a_stored_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            init_db(db_path)
            with connect(db_path) as db:
                now = utc_now()
                media_id = db.execute(
                    "INSERT INTO media_items (title, normalized_title, media_type, tmdb_status, created_at, updated_at) "
                    "VALUES ('绝世战魂', '绝世战魂', 'tv', 'review', ?, ?)",
                    (now, now),
                ).lastrowid
                db.execute(
                    "INSERT INTO ingestion_events (id, event_id, service, raw_text, received_at) "
                    "VALUES ('ev-2', 'ev-2', 'telegram', ?, ?)",
                    ("名称: 绝世战魂 4K臻彩 更新至105\n发行时间: 2024-04-27", now),
                )
                db.execute(
                    "INSERT INTO source_records (media_id, source_type, source_key, raw_label, raw_text, url, created_at) "
                    "VALUES (?, 'ed2k', 'exact-2', 'x', 'x', 'ed2k://x', ?)",
                    (media_id, now),
                )
                db.execute(
                    "INSERT INTO ingestion_links (ingestion_id, source_key, provider, source_type, url, media_id) "
                    "VALUES ('ev-2', 'exact-2', '115', 'ed2k', 'ed2k://x', ?)",
                    (media_id,),
                )
                db.execute(
                    "INSERT INTO tmdb_candidates (media_id, tmdb_id, media_type, title, original_title, "
                    "release_date, poster_path, overview, score, payload_json, created_at) "
                    "VALUES (?, 252788, 'tv', '绝世战魂', '绝世战魂', '2024-04-27', NULL, NULL, 0.96, ?, ?)",
                    (media_id, json.dumps({"id": 252788, "name": "绝世战魂", "first_air_date": "2024-04-27"}), now),
                )
            result = link_message_evidence(int(media_id), db_path)
            self.assertTrue(result["linked"])
            with connect(db_path) as db:
                row = db.execute("SELECT tmdb_id, tmdb_status, match_method FROM media_items WHERE id = ?", (media_id,)).fetchone()
                self.assertEqual(row["tmdb_id"], 252788)
                self.assertEqual(row["tmdb_status"], "matched")
                self.assertEqual(row["match_method"], "message_evidence")

    def test_resolution_uses_message_evidence_for_exact_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "media.db"
            init_db(db_path)
            with connect(db_path) as db:
                now = utc_now()
                media_id = db.execute(
                    "INSERT INTO media_items (title, normalized_title, media_type, created_at, updated_at) "
                    "VALUES ('绝世战魂', '绝世战魂', 'tv', ?, ?)",
                    (now, now),
                ).lastrowid
                db.execute(
                    "INSERT INTO ingestion_events (id, event_id, service, raw_text, received_at) "
                    "VALUES ('ev-1', 'ev-1', 'telegram', ?, ?)",
                    ("名称: 绝世战魂 4K臻彩 更新至105\n发行时间: 2024-04-27", now),
                )
                event_id = 'ev-1'
                db.execute(
                    "INSERT INTO source_records (media_id, source_type, source_key, raw_label, raw_text, url, created_at) "
                    "VALUES (?, 'ed2k', 'exact-1', 'x', 'x', 'ed2k://x', ?)",
                    (media_id, now),
                )
                db.execute(
                    "INSERT INTO ingestion_links (ingestion_id, source_key, provider, source_type, url, media_id) "
                    "VALUES (?, 'exact-1', '115', 'ed2k', 'ed2k://x', ?)",
                    (event_id, media_id),
                )
                candidates = [
                    {"tmdb_id": 252788, "title": "绝世战魂", "media_type": "tv", "release_date": "2024-04-27"},
                    {"tmdb_id": 250817, "title": "绝世战魂", "media_type": "tv", "release_date": "2021-05-12"},
                ]
                chosen, evidence = resolve_message_candidate(db, int(media_id), candidates, "tv", "绝世战魂")
                self.assertIsNotNone(evidence)
                self.assertIsNotNone(chosen)
                assert chosen is not None
                self.assertEqual(chosen["tmdb_id"], 252788)
