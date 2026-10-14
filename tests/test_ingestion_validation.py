from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.ingestion import get_event, receive, retry_failed_ingestions, retry_ingestion_event, retry_pending_validations
from backend.share115 import Share115Error
from backend.share_guangya import ShareGuangyaError


class IngestionValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "media.db"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def wait(self, event_id: str) -> dict:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            event = get_event(event_id, self.db_path)
            if event and event["status"] != "queued":
                return event
            time.sleep(0.01)
        self.fail("event remained queued")

    def send(self, event_id: str, url: str) -> dict:
        return receive({
            "event_id": event_id,
            "source": {"service": "telegram"},
            "message": {"text": f"电影 (2024) {url}"},
        }, self.db_path)

    def test_invalid_password_is_retained_but_not_imported(self) -> None:
        with patch("backend.ingestion.Share115Client.probe_share", side_effect=Share115Error("密码错误", 4100008)):
            event = self.wait(self.send("invalid", "https://115.com/s/a?password=bad")["id"])
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["links"][0]["status"], "invalid")
        self.assertEqual(event["links"][0]["media_id"], None)

    def test_transient_failure_is_queued_and_can_be_retried(self) -> None:
        with patch("backend.ingestion.Share115Client.probe_share", side_effect=Share115Error("timeout")):
            event = self.wait(self.send("transient", "https://115.com/s/a?password=abcd")["id"])
        self.assertEqual(event["status"], "pending_validation")
        with patch("backend.ingestion.Share115Client.probe_share") as probe:
            # Make this record immediately due to exercise the durable retry path.
            from backend.schema import connect
            with connect(self.db_path) as db:
                db.execute("UPDATE ingestion_links SET next_retry_at=NULL WHERE ingestion_id=?", (event["id"],))
            self.assertEqual(retry_pending_validations(db_path=self.db_path)["queued"], 1)
            probe.return_value = None
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                event = get_event(event["id"], self.db_path)
                if event["status"] == "imported":
                    break
                time.sleep(0.01)
        self.assertEqual(event["status"], "imported")

    def test_guangya_invalid_password_is_not_imported(self) -> None:
        with patch(
            "backend.ingestion.ShareGuangyaClient.probe_share",
            side_effect=ShareGuangyaError("访问密码错误", terminal=True),
        ):
            event = self.wait(self.send("guangya-invalid", "https://guangyapan.com/s/example?pwd=bad")["id"])
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["links"][0]["provider"], "guangya")
        self.assertEqual(event["links"][0]["status"], "invalid")
        self.assertIsNone(event["links"][0]["media_id"])

    def test_guangya_transient_failure_is_pending(self) -> None:
        with patch(
            "backend.ingestion.ShareGuangyaClient.probe_share",
            side_effect=ShareGuangyaError("光鸭接口限流", risk_control=True),
        ):
            event = self.wait(self.send("guangya-pending", "https://guangyapan.com/s/example")["id"])
        self.assertEqual(event["status"], "pending_validation")
        self.assertEqual(event["links"][0]["status"], "pending_validation")

    def test_115_password_text_is_invalid_without_business_code(self) -> None:
        # 115 may answer with an unknown errno; a clear password message is
        # still terminal and must never enter the retry queue.
        with patch(
            "backend.ingestion.Share115Client.probe_share",
            side_effect=Share115Error("密码验证失败", 990002, terminal=True),
        ):
            event = self.wait(self.send("pw-text", "https://115.com/s/a?password=bad")["id"])
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["links"][0]["status"], "invalid")
        self.assertEqual(retry_pending_validations(db_path=self.db_path)["queued"], 0)

    def test_retry_gives_up_after_max_attempts(self) -> None:
        from backend.ingestion import VALIDATION_MAX_ATTEMPTS
        from backend.schema import connect
        with patch("backend.ingestion.Share115Client.probe_share", side_effect=Share115Error("timeout")):
            event = self.wait(self.send("capped", "https://115.com/s/a?password=abcd")["id"])
            self.assertEqual(event["links"][0]["status"], "pending_validation")
            with connect(self.db_path) as db:
                db.execute(
                    "UPDATE ingestion_links SET validation_attempts=?, next_retry_at=NULL WHERE ingestion_id=?",
                    (VALIDATION_MAX_ATTEMPTS - 1, event["id"]),
                )
            self.assertEqual(retry_pending_validations(db_path=self.db_path)["queued"], 1)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                event = get_event(event["id"], self.db_path)
                if event["links"][0]["status"] == "error":
                    break
                time.sleep(0.01)
        self.assertEqual(event["links"][0]["status"], "error")
        self.assertEqual(event["status"], "failed")
        self.assertIn("停止自动重试", event["links"][0]["validation_error"])
        self.assertEqual(retry_pending_validations(db_path=self.db_path)["queued"], 0)

    def test_inflight_event_is_not_processed_twice(self) -> None:
        from backend.ingestion import _PROCESSING_EVENTS
        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def slow_probe(url):
            calls.append(url)
            started.set()
            release.wait(3)

        with patch("backend.ingestion.Share115Client.probe_share", side_effect=slow_probe):
            event = self.send("inflight", "https://115.com/s/inflight?password=abcd")
            self.assertTrue(started.wait(2), "probe never started")
            # The validation worker would normally grab the freshly created
            # pending link while the first probe is still running.
            retry_pending_validations(db_path=self.db_path)
            time.sleep(0.2)
            self.assertEqual(len(calls), 1, "the same event was processed twice")
            self.assertIn(event["id"], _PROCESSING_EVENTS)
            release.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if get_event(event["id"], self.db_path)["status"] == "imported":
                    break
                time.sleep(0.01)

        current = get_event(event["id"], self.db_path)
        self.assertEqual(current["status"], "imported")
        self.assertEqual(current["links"][0]["status"], "inserted")
        self.assertEqual(len(calls), 1)
        self.assertNotIn(event["id"], _PROCESSING_EVENTS)

    def test_rerunning_an_imported_event_keeps_inserted_status(self) -> None:
        from backend.importers import detect_source_kind, iter_sources
        from backend.ingestion import _run_validation_and_import
        with patch("backend.ingestion.Share115Client.probe_share"):
            event = self.wait(self.send("rerun", "https://115.com/s/rerun?password=abcd")["id"])
        self.assertEqual(event["status"], "imported")
        self.assertEqual(event["links"][0]["status"], "inserted")

        # A second pass over the same event (manual reprocess, slow machine)
        # finds the source already stored and must not downgrade its own import.
        raw = get_event(event["id"], self.db_path)["raw_text"]
        sources = list(iter_sources(raw, detect_source_kind(raw)))
        with patch("backend.ingestion.Share115Client.probe_share"):
            _run_validation_and_import(event["id"], raw, sources, self.db_path)
        after = get_event(event["id"], self.db_path)
        self.assertEqual(after["links"][0]["status"], "inserted")
        self.assertEqual(after["status"], "imported")

    def test_processing_guard_releases_after_a_failed_run(self) -> None:
        from backend.schema import connect
        with patch("backend.ingestion.Share115Client.probe_share", side_effect=Share115Error("timeout")):
            event = self.wait(self.send("guard-release", "https://115.com/s/guard?password=abcd")["id"])
        self.assertEqual(event["status"], "pending_validation")
        with patch("backend.ingestion.Share115Client.probe_share"):
            with connect(self.db_path) as db:
                db.execute("UPDATE ingestion_links SET next_retry_at=NULL WHERE ingestion_id=?", (event["id"],))
            self.assertEqual(retry_pending_validations(db_path=self.db_path)["queued"], 1)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if get_event(event["id"], self.db_path)["status"] == "imported":
                    break
                time.sleep(0.01)
        self.assertEqual(get_event(event["id"], self.db_path)["status"], "imported")

    def test_gate_blocked_failures_do_not_consume_retry_budget(self) -> None:
        gate_error = Share115Error(
            "115 请求已因访问限制暂停，请先人工确认恢复，不会自动重试。",
            risk_control=True, gate_blocked=True,
        )
        with patch("backend.ingestion.Share115Client.probe_share", side_effect=gate_error):
            event = self.wait(self.send("gate-blocked", "https://115.com/s/a?password=abcd")["id"])
        self.assertEqual(event["status"], "pending_validation")
        self.assertEqual(event["links"][0]["status"], "pending_validation")
        self.assertEqual(event["links"][0]["validation_attempts"], 0)
        self.assertIsNotNone(event["links"][0]["next_retry_at"])

    def test_retry_ingestion_event_revalidates_failed_links(self) -> None:
        with patch("backend.ingestion.Share115Client.probe_share", side_effect=Share115Error("密码错误", 4100008)):
            event = self.wait(self.send("retry-me", "https://115.com/s/retry?password=bad")["id"])
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["links"][0]["status"], "invalid")

        with patch("backend.ingestion.Share115Client.probe_share") as probe:
            result = retry_ingestion_event(event["id"], self.db_path)
            self.assertEqual(result["queued"], 1)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                event = get_event(event["id"], self.db_path)
                if event["status"] == "imported":
                    break
                time.sleep(0.01)
        self.assertEqual(event["status"], "imported")
        self.assertEqual(event["links"][0]["status"], "inserted")
        self.assertEqual(event["links"][0]["validation_attempts"], 0)
        self.assertEqual(probe.call_count, 1)

    def test_retry_never_reprobes_already_imported_links(self) -> None:
        text = "电影 (2024) https://115.com/s/bad-retry?password=bad\n电影 (2025) https://115.com/s/kept"
        with patch(
            "backend.ingestion.Share115Client.probe_share",
            side_effect=[Share115Error("密码错误", 4100008), None],
        ):
            event = self.wait(self.send("mixed-retry", text)["id"])
            self.assertEqual(sorted(link["status"] for link in event["links"]), ["inserted", "invalid"])

            with patch("backend.ingestion.Share115Client.probe_share") as probe:
                self.assertEqual(retry_ingestion_event(event["id"], self.db_path)["queued"], 1)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    event = get_event(event["id"], self.db_path)
                    if all(link["status"] == "inserted" for link in event["links"]):
                        break
                    time.sleep(0.01)
        self.assertTrue(all(link["status"] == "inserted" for link in event["links"]))
        self.assertEqual(probe.call_count, 1)

    def test_retry_failed_ingestions_only_touches_failed_events(self) -> None:
        with patch("backend.ingestion.Share115Client.probe_share", side_effect=Share115Error("密码错误", 4100008)):
            failed = self.wait(self.send("bulk-fail", "https://115.com/s/bulk-bad?password=bad")["id"])["id"]
        with patch("backend.ingestion.Share115Client.probe_share"):
            good = self.wait(self.send("bulk-good", "https://115.com/s/bulk-good")["id"])["id"]
        self.assertEqual((get_event(failed, self.db_path)["status"], get_event(good, self.db_path)["status"]), ("failed", "imported"))

        with patch("backend.ingestion.Share115Client.probe_share") as probe:
            result = retry_failed_ingestions(db_path=self.db_path)
            self.assertEqual((result["queued"], result["links"]), (1, 1))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if get_event(failed, self.db_path)["status"] == "imported":
                    break
                time.sleep(0.01)
        self.assertEqual(get_event(failed, self.db_path)["status"], "imported")
        self.assertEqual(probe.call_count, 1)

        with self.assertRaises(ValueError):
            retry_ingestion_event("missing-event", self.db_path)


if __name__ == "__main__":
    unittest.main()
