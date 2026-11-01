from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.schema import connect, init_db
from backend.tg_export import ExportMessage
from backend.tg_import import (
    actionable_button_urls,
    build_event_body,
    describe_result,
    existing_message_ids,
    import_export,
    message_text,
    resolve_channel_identity,
)
from tests.test_tg_export import (
    BUTTON_ONLY,
    JOINED_MESSAGE,
    MESSAGE_WITH_BUTTON,
    PHOTO_ONLY,
    write_export,
)


def seed_event(
    db_path, *, event_id: str, channel_id: str, channel_username: str, message_id: str, status: str = "imported"
) -> None:
    init_db(db_path)
    with connect(db_path) as db:
        db.execute(
            """INSERT INTO ingestion_events
               (id,event_id,service,channel_id,channel_name,channel_username,message_id,message_url,raw_text,status,received_at)
               VALUES(?,?, 'telegram', ?, '115网盘资源分享频道', ?, ?, ?, 'text', ?, '2026-10-01T00:00:00+00:00')
               ON CONFLICT(service, event_id) DO NOTHING""",
            (
                f"ing_{event_id}",
                event_id,
                channel_id,
                channel_username,
                message_id,
                f"https://t.me/{channel_username}/{message_id}",
                status,
            ),
        )


class TgImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "media.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_build_event_body_uses_channel_identity(self) -> None:
        message = ExportMessage(
            message_id=42,
            published_at="2023-12-09T03:40:30+08:00",
            text="标题\nhttps://115.com/s/abc123?password=f816",
        )
        body = build_event_body(
            message, channel_id="2121362585", channel_name="115网盘资源分享频道", channel_username="Lsp115"
        )
        self.assertEqual(body["event_id"], "telegram:2121362585:42:v1")
        self.assertEqual(body["source"]["service"], "telegram")
        self.assertEqual(body["source"]["channel_id"], "2121362585")
        self.assertEqual(body["source"]["message_id"], "42")
        self.assertEqual(body["source"]["message_url"], "https://t.me/Lsp115/42")
        self.assertEqual(body["source"]["published_at"], "2023-12-09T03:40:30+08:00")
        self.assertEqual(body["message"]["text"], message.text)

    def test_actionable_button_urls_drop_ads(self) -> None:
        message = ExportMessage(
            message_id=1,
            published_at=None,
            text="标题",
            button_urls=(
                "https://telegra.ph/king-2021",
                "https://hongxingyun6.vip/web/#/login?code=kMgDVAlx",
                "https://115.com/s/abc123?password=f816",
                "https://t.me/hao115",
            ),
        )
        self.assertEqual(
            actionable_button_urls(message),
            ["https://telegra.ph/king-2021", "https://115.com/s/abc123?password=f816"],
        )
        self.assertEqual(message_text(message), "标题\nhttps://telegra.ph/king-2021\nhttps://115.com/s/abc123?password=f816")

    def test_message_text_keeps_original_when_no_actionable_button(self) -> None:
        message = ExportMessage(message_id=1, published_at=None, text="标题", button_urls=("https://t.me/hao115",))
        self.assertEqual(message_text(message), "标题")

    def test_resolve_channel_identity_reuses_existing_channel_id(self) -> None:
        seed_event(
            self.db_path,
            event_id="telegram:2121362585:19689:v1",
            channel_id="2121362585",
            channel_username="Lsp115",
            message_id="19689",
        )
        resolved = resolve_channel_identity(channel_username="lsp115", db_path=self.db_path)
        self.assertEqual(resolved["channel_id"], "2121362585")
        # 用户名沿用库里已有的大小写，避免同一频道出现两种写法。
        self.assertEqual(resolved["channel_username"], "Lsp115")

        # 显式传入的 ID 优先于库里查到的。
        explicit = resolve_channel_identity(channel_username="lsp115", channel_id="999", db_path=self.db_path)
        self.assertEqual(explicit["channel_id"], "999")

        # 没有入库记录时退回用户名，事件 ID 依然稳定。
        fallback = resolve_channel_identity(channel_username="otherchannel", db_path=self.db_path)
        self.assertEqual(fallback["channel_id"], "otherchannel")

    def test_resolve_channel_identity_falls_back_to_channel_name(self) -> None:
        seed_event(
            self.db_path,
            event_id="telegram:2121362585:19689:v1",
            channel_id="2121362585",
            channel_username="Lsp115",
            message_id="19689",
        )
        resolved = resolve_channel_identity(channel_name="115网盘资源分享频道", db_path=self.db_path)
        self.assertEqual(resolved["channel_id"], "2121362585")
        self.assertEqual(resolved["channel_username"], "Lsp115")

    def test_existing_message_ids_matches_username_case_insensitively(self) -> None:
        seed_event(
            self.db_path,
            event_id="telegram:2121362585:19689:v1",
            channel_id="2121362585",
            channel_username="Lsp115",
            message_id="19689",
        )
        self.assertEqual(
            existing_message_ids(channel_username="lsp115", channel_id="2121362585", db_path=self.db_path),
            {"19689"},
        )

    def test_existing_message_ids_ignores_interrupted_queued_events(self) -> None:
        # 上次导入被 Ctrl-C 打断时会留下 queued 记录，不能因此永久跳过这条消息。
        seed_event(
            self.db_path,
            event_id="telegram:2121362585:3:v1",
            channel_id="2121362585",
            channel_username="Lsp115",
            message_id="3",
        )
        seed_event(
            self.db_path,
            event_id="telegram:2121362585:4:v1",
            channel_id="2121362585",
            channel_username="Lsp115",
            message_id="4",
            status="queued",
        )
        self.assertEqual(
            existing_message_ids(channel_username="Lsp115", channel_id="2121362585", db_path=self.db_path),
            {"3"},
        )

    def test_import_export_skips_existing_and_sends_actionable_messages(self) -> None:
        export = write_export(self.root / "export", {"messages.html": MESSAGE_WITH_BUTTON + PHOTO_ONLY})
        seed_event(
            self.db_path,
            event_id="telegram:2121362585:3:v1",
            channel_id="2121362585",
            channel_username="Lsp115",
            message_id="3",
        )
        bodies: list[dict] = []
        result = import_export(
            export,
            channel_username="lsp115",
            channel_id="2121362585",
            channel_name="115网盘资源分享频道",
            receiver=lambda body: (bodies.append(body), {"status": "imported"})[1],
            db_path=self.db_path,
            progress=lambda message: None,
        )
        self.assertEqual(result["parsed"], 2)
        self.assertEqual(result["empty"], 1)
        self.assertEqual(result["existing"], 1)
        self.assertEqual(result["submitted"], 0)
        self.assertEqual(bodies, [])
        self.assertIn("已入库跳过 1", describe_result(result))

    def test_import_export_reports_statuses_and_errors(self) -> None:
        export = write_export(
            self.root / "export",
            {"messages.html": MESSAGE_WITH_BUTTON + JOINED_MESSAGE + PHOTO_ONLY},
        )
        bodies: list[dict] = []

        def receiver(body: dict) -> dict:
            bodies.append(body)
            if body["source"]["message_id"] == "4":
                raise RuntimeError("校验服务不可用")
            return {"status": "imported"}

        result = import_export(
            export,
            channel_username="lsp115",
            channel_id="2121362585",
            receiver=receiver,
            db_path=self.db_path,
            progress=lambda message: None,
        )
        self.assertEqual(result["parsed"], 3)
        self.assertEqual(result["empty"], 1)
        self.assertEqual(result["submitted"], 2)
        self.assertEqual(result["statuses"], {"imported": 1, "error": 1})
        self.assertEqual(result["errors"], 1)
        self.assertEqual([body["source"]["message_id"] for body in bodies], ["3", "4"])
        # 按钮里的资源页链接被拼回正文，广告链接不会。
        self.assertIn("https://telegra.ph/king-2021", bodies[0]["message"]["text"])
        self.assertNotIn("hongxingyun6", bodies[0]["message"]["text"])

    def test_import_export_limit_counts_only_new_messages(self) -> None:
        export = write_export(
            self.root / "export",
            {"messages.html": MESSAGE_WITH_BUTTON + JOINED_MESSAGE},
        )
        seed_event(
            self.db_path,
            event_id="telegram:2121362585:3:v1",
            channel_id="2121362585",
            channel_username="Lsp115",
            message_id="3",
        )
        seen: list[str] = []
        result = import_export(
            export,
            channel_username="lsp115",
            channel_id="2121362585",
            limit=1,
            receiver=lambda body: (seen.append(body["source"]["message_id"]), {"status": "imported"})[1],
            db_path=self.db_path,
            progress=lambda message: None,
        )
        # 限制的是本次真正处理的消息数，已入库的消息不算在内。
        self.assertEqual(result["submitted"], 1)
        self.assertEqual(seen, ["4"])

    def test_dry_run_never_writes_or_receives(self) -> None:
        export = write_export(
            self.root / "export",
            {"messages.html": MESSAGE_WITH_BUTTON + JOINED_MESSAGE},
        )
        calls: list[dict] = []
        result = import_export(
            export,
            channel_username="lsp115",
            channel_id="2121362585",
            dry_run=True,
            receiver=lambda body: calls.append(body),
            db_path=self.db_path,
            progress=lambda message: None,
        )
        self.assertEqual(calls, [])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["submitted"], 2)
        self.assertEqual(result["direct_source_messages"], 2)
        self.assertEqual(result["resource_page_messages"], 1)
        # 只读预演不能初始化数据库，更不能写入事件。
        with connect(self.db_path) as db:
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ingestion_events'"
            ).fetchall()
        self.assertEqual(tables, [])

    def test_dry_run_counts_unsupported_messages_as_zero_source(self) -> None:
        export = write_export(
            self.root / "export",
            {"messages.html": '<div class="message default clearfix joined" id="message9">'
             '<div class="body"><div class="pull_right date details" title="10.12.2023 09:05:00 UTC+08:00">09:05</div>'
             "<div class=\"text\">只有广告 https://hongxingyun6.vip/web/#/login?code=x</div></div></div>"},
        )
        result = import_export(
            export,
            channel_username="lsp115",
            dry_run=True,
            db_path=self.db_path,
            progress=lambda message: None,
        )
        self.assertEqual(result["direct_source_messages"], 0)
        self.assertEqual(result["resource_page_messages"], 0)

    def test_missing_export_path_raises(self) -> None:
        with self.assertRaises(ValueError):
            import_export(self.root / "missing", channel_username="lsp115", dry_run=True, db_path=self.db_path)


class TgImportPipelineTests(unittest.TestCase):
    """导入编排接到真实入库链路上的行为（分享校验和头像抓取都打桩）。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "media.db"
        self._patches = [
            patch("backend.ingestion.Share115Client.probe_share"),
            patch("backend.ingestion.Share123Client.probe_share"),
            patch("backend.ingestion.ShareGuangyaClient.probe_share"),
            patch("backend.ingestion._refresh_channel_avatar"),
            patch(
                "backend.resource_pages.fetch_resource_page",
                return_value=("国王排名", "https://115.com/s/frompage?password=abcd"),
            ),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_import_export_runs_the_ingestion_pipeline(self) -> None:
        export = write_export(
            self.root / "export",
            {"messages.html": MESSAGE_WITH_BUTTON + BUTTON_ONLY},
        )
        result = import_export(
            export,
            channel_username="lsp115",
            channel_id="2121362585",
            channel_name="115网盘资源分享频道",
            workers=1,  # 单线程保证 message 3 先入库，duplicate 判定才是确定的
            db_path=self.db_path,
            progress=lambda message: None,
        )
        self.assertEqual(result["submitted"], 2)
        # 第二条消息只有资源页，而页里的分享已经由第一条消息入库，因此记为 duplicate。
        self.assertEqual(result["statuses"], {"imported": 1, "duplicate": 1})
        self.assertEqual(result["errors"], 0)

        with connect(self.db_path) as db:
            events = db.execute(
                "SELECT message_id, channel_username, channel_name, message_url, status FROM ingestion_events ORDER BY message_id"
            ).fetchall()
            self.assertEqual([row["message_id"] for row in events], ["3", "7"])
            self.assertEqual(events[0]["channel_username"], "lsp115")
            self.assertEqual(events[0]["channel_name"], "115网盘资源分享频道")
            self.assertEqual(events[0]["message_url"], "https://t.me/lsp115/3")
            self.assertEqual(events[0]["status"], "imported")

            sources = db.execute("SELECT url FROM source_records ORDER BY url").fetchall()
            # 正文里的 115 直链，加上机器人按钮里的 telegra.ph 资源页展开后的分享。
            self.assertEqual(len(sources), 2)

    def test_import_export_is_idempotent(self) -> None:
        export = write_export(self.root / "export", {"messages.html": MESSAGE_WITH_BUTTON})
        first = import_export(
            export,
            channel_username="lsp115",
            channel_id="2121362585",
            db_path=self.db_path,
            progress=lambda message: None,
        )
        second = import_export(
            export,
            channel_username="lsp115",
            channel_id="2121362585",
            db_path=self.db_path,
            progress=lambda message: None,
        )
        self.assertEqual(first["statuses"], {"imported": 1})
        self.assertEqual(second["submitted"], 0)
        self.assertEqual(second["existing"], 1)
        with connect(self.db_path) as db:
            total = db.execute("SELECT COUNT(*) AS total FROM ingestion_events").fetchone()["total"]
        self.assertEqual(total, 1)

    def test_receive_background_false_processes_inline(self) -> None:
        from backend import ingestion

        seen: list[str] = []

        def fake_process(ingestion_id, text, db_path=None, **kwargs):
            seen.append((ingestion_id, kwargs.get("auto_match")))

        with patch.object(ingestion, "process", side_effect=fake_process):
            result = ingestion.receive(
                {
                    "event_id": "telegram:2121362585:3:v1",
                    "source": {"service": "telegram", "channel_username": "lsp115", "message_id": "3"},
                    "message": {"text": "标题\nhttps://115.com/s/abc123?password=f816"},
                },
                self.db_path,
                background=False,
                auto_match=False,
            )
        self.assertEqual(seen, [(result["id"], False)])
        self.assertEqual(result["status"], "queued")

    def test_interrupted_messages_are_reimported(self) -> None:
        # 上次导入被 Ctrl-C 打断留下的 queued 记录，重跑时必须补完，而不是被跳过。
        export = write_export(self.root / "export", {"messages.html": MESSAGE_WITH_BUTTON})
        seed_event(
            self.db_path,
            event_id="telegram:2121362585:3:v1",
            channel_id="2121362585",
            channel_username="Lsp115",
            message_id="3",
            status="queued",
        )
        result = import_export(
            export,
            channel_username="lsp115",
            channel_id="2121362585",
            db_path=self.db_path,
            progress=lambda message: None,
        )
        self.assertEqual(result["requeued"], 1)
        self.assertEqual(result["existing"], 0)
        self.assertEqual(result["statuses"], {"imported": 1})
        with connect(self.db_path) as db:
            rows = db.execute("SELECT status FROM ingestion_events WHERE message_id='3'").fetchall()
        self.assertEqual([row["status"] for row in rows], ["imported"])

    def test_import_export_can_defer_tmdb_matching(self) -> None:
        export = write_export(self.root / "export", {"messages.html": MESSAGE_WITH_BUTTON})
        with patch("backend.tmdb.TmdbClient.search") as search:
            result = import_export(
                export,
                channel_username="lsp115",
                channel_id="2121362585",
                auto_match=False,
                db_path=self.db_path,
                progress=lambda message: None,
            )
        self.assertEqual(result["statuses"], {"imported": 1})
        # 导入阶段完全不碰 TMDB，作品留在待匹配状态，事后可以批量跑 app.py match。
        search.assert_not_called()
        with connect(self.db_path) as db:
            rows = db.execute("SELECT tmdb_status FROM media_items ORDER BY id").fetchall()
        # 直链与资源页的两个来源标题/年份相同，不再因为 📺/🎥 图标被拆成两部作品，
        # 会聚合成一条待匹配记录。
        self.assertEqual([row["tmdb_status"] for row in rows], ["pending"])


if __name__ == "__main__":
    unittest.main()
