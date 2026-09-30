from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from backend.repository import get_media, import_content, link_tmdb, list_media
from backend.schema import connect
from backend.share115 import CANCELLED_ERRNO, INVALID_LINK_ERRNO, Share115Client, Share115Error, ShareRequestGate, enrich_media_share_sources
from backend.share_audit import audit_path, audit_share_links, audit_status, pause_audit


class ShareRequestGateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "gate.json"
        self.gate = ShareRequestGate(self.path)

    def test_interval_is_shared_by_separate_client_instances(self):
        with patch("backend.share115.time.time", return_value=100), patch("backend.share115.time.sleep") as sleep:
            with self.gate.request():
                pass
            with ShareRequestGate(self.path).request():
                pass
            sleep.assert_called_once_with(5)

    def test_risk_signal_latches_across_clients_without_sleep_or_retry(self):
        with self.assertRaises(Share115Error), self.gate.request():
            raise Share115Error("blocked", risk_control=True)
        with patch("backend.share115.time.sleep") as sleep:
            with self.assertRaises(Share115Error), ShareRequestGate(self.path).request():
                self.fail("must not send another request")
            sleep.assert_not_called()
        self.assertTrue(json.loads(self.path.read_text())["paused"])

    def test_corrupted_state_fails_closed(self):
        self.path.write_text("broken", encoding="utf-8")
        with self.assertRaises(Share115Error), self.gate.request():
            self.fail("corrupt protection state must not permit network traffic")

    def test_probe_only_requests_one_root_entry(self):
        client = Share115Client(gate=self.gate)
        client._fetch_page = Mock(return_value={"list": []})
        client.probe_share("https://115.com/s/example?password=abcd")
        self.assertEqual(client._fetch_page.call_count, 1)
        self.assertEqual(client._fetch_page.call_args.kwargs["limit"], 1)
        self.assertEqual(client._fetch_page.call_args.kwargs["cid"], "0")

    def test_restrictions_and_html_are_not_cancellation(self):
        for code in (401, 403, 405, 429):
            self.path.unlink(missing_ok=True)
            with patch("backend.share115.urlopen", side_effect=HTTPError("https://115.com", code, "blocked", {}, None)):
                with self.assertRaises(Share115Error) as error:
                    Share115Client(gate=self.gate).probe_share("https://115.com/s/example")
                self.assertTrue(error.exception.risk_control)
                self.assertNotEqual(error.exception.errno, CANCELLED_ERRNO)
        self.path.unlink()
        with patch("backend.share115.urlopen", return_value=io.BytesIO(b"<!doctype html>captcha")):
            with self.assertRaises(Share115Error) as error:
                Share115Client(gate=self.gate).probe_share("https://115.com/s/example")
            self.assertTrue(error.exception.risk_control)

    def test_business_cancellation_and_password_errors_do_not_latch_gate(self):
        for code in (CANCELLED_ERRNO, INVALID_LINK_ERRNO, 4100008, 4100012):
            self.path.unlink(missing_ok=True)
            with patch("backend.share115.urlopen", return_value=io.BytesIO(json.dumps({"state": False, "errno": code, "error": "unavailable"}).encode())):
                with self.assertRaises(Share115Error) as error:
                    Share115Client(gate=self.gate).probe_share("https://115.com/s/example")
            self.assertEqual(error.exception.errno, code)
            self.assertFalse(error.exception.risk_control)
            self.assertFalse(json.loads(self.path.read_text()).get("paused", False))

    def test_unknown_api_errors_stop_requests_without_guessing_cancellation(self):
        with patch("backend.share115.urlopen", return_value=io.BytesIO(b'{"state":false,"errno":911,"error":"unknown"}')):
            with self.assertRaises(Share115Error) as error:
                Share115Client(gate=self.gate).probe_share("https://115.com/s/example")
            self.assertTrue(error.exception.risk_control)
        self.assertEqual(json.loads(self.path.read_text())["last_error_errno"], 911)


class ShareAuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "media.db"
        import_content("First (2025) https://115.com/s/a\nSecond (2025) https://115.com/s/b", "test.txt", db_path=self.db)
        self.first = list_media(query="First", db_path=self.db)["items"][0]["id"]

    def client(self, outcomes):
        return Mock(spec=Share115Client, probe_share=Mock(side_effect=outcomes))

    def run_audit(self, client, **kwargs):
        return audit_share_links(self.db, client=client, cooldown=0, **kwargs)

    def test_full_pass_checks_matched_and_pending_and_preserves_metadata(self):
        link_tmdb(self.first, {"tmdb_id": 7, "media_type": "movie", "title": "First"}, 1, "manual", self.db)
        result = self.run_audit(self.client([Share115Error("cancelled", CANCELLED_ERRNO), None]))
        self.assertEqual(result["status"], "complete")
        self.assertEqual((result["processed"], result["removed"], result["available"]), (2, 1, 1))
        item = get_media(self.first, self.db)
        self.assertEqual((item["tmdb_id"], item["tmdb_status"], item["source_count"]), (7, "matched", 0))
        self.assertEqual(len(list(self.db.parent.glob("media-before-share-audit-*.db"))), 1)
        self.assertNotIn("https://", audit_path(self.db).read_text())
        self.assertNotIn("queue", result)

    def test_risk_stops_immediately_without_deleting_or_overwriting_snapshot(self):
        metadata = json.dumps({"share_snapshot": {"version": 2, "status": "ok", "names": ["kept"]}})
        with connect(self.db) as db:
            db.execute("UPDATE source_records SET metadata_json=?", (metadata,))
        client = self.client([Share115Error("blocked", risk_control=True)])
        result = self.run_audit(client)
        self.assertEqual((result["status"], result["processed"], result["removed"]), ("paused_risk", 0, 0))
        self.assertEqual(client.probe_share.call_count, 1)
        with connect(self.db) as db:
            self.assertTrue(all(row[0] == metadata for row in db.execute("SELECT metadata_json FROM source_records")))

    def test_transient_error_is_retained_and_never_retried_automatically(self):
        client = self.client([Share115Error("timeout")])
        result = self.run_audit(client)
        self.assertEqual((result["status"], result["removed"]), ("paused_error", 0))
        self.assertEqual(client.probe_share.call_count, 1)

    def test_resume_continues_only_unfinished_links(self):
        self.run_audit(self.client([None, Share115Error("timeout")]))
        with self.assertRaises(ValueError):
            self.run_audit(self.client([]))
        client = self.client([None])
        result = self.run_audit(client, resume=True)
        self.assertEqual((result["status"], result["processed"]), ("complete", 2))
        client.probe_share.assert_called_once_with("https://115.com/s/b")

    def test_wrong_password_preserves_source_and_does_not_stop_other_checks(self):
        result = self.run_audit(self.client([Share115Error("password", 4100008), Share115Error("missing", 4100012)]))
        self.assertEqual((result["status"], result["protected"], result["removed"]), ("complete", 2, 0))

    def test_explicit_invalid_link_is_removed_and_scan_continues(self):
        link_tmdb(self.first, {"tmdb_id": 7, "media_type": "movie", "title": "First"}, 1, "manual", self.db)
        result = self.run_audit(self.client([Share115Error("Link invalid", INVALID_LINK_ERRNO), None]))
        self.assertEqual((result["status"], result["processed"], result["removed"], result["available"]), ("complete", 2, 1, 1))
        self.assertEqual((get_media(self.first, self.db)["tmdb_id"], get_media(self.first, self.db)["source_count"]), (7, 0))

    def test_unknown_error_code_is_recorded_without_authorizing_deletion(self):
        result = self.run_audit(self.client([Share115Error("Unknown error", 911, risk_control=True)]))
        self.assertEqual((result["status"], result["removed"], result["last_error_errno"]), ("paused_risk", 0, 911))

    def test_explicit_risk_signal_overrules_even_an_invalid_link_code(self):
        result = self.run_audit(self.client([Share115Error("captcha", INVALID_LINK_ERRNO, risk_control=True)]))
        self.assertEqual((result["status"], result["removed"]), ("paused_risk", 0))

    def test_cancelled_unconfirmed_orphan_is_removed_but_ed2k_survives(self):
        import_content("ed2k://|file|First.2025.mkv|100|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/", "ed2k", db_path=self.db)
        result = self.run_audit(self.client([Share115Error("cancelled", CANCELLED_ERRNO)] * 2))
        self.assertEqual((result["removed"], result["media_removed"]), (2, 1))
        self.assertEqual(get_media(self.first, self.db)["sources"][0]["source_type"], "ed2k")
        with connect(self.db) as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_pause_request_stops_before_the_next_link(self):
        def probe(url):
            pause_audit(self.db)
        client = self.client(probe)
        result = self.run_audit(client)
        self.assertEqual((result["status"], result["processed"]), ("paused", 1))
        self.assertEqual(client.probe_share.call_count, 1)

    def test_deletion_uses_existing_concurrency_guards(self):
        def probe(url):
            with connect(self.db) as db:
                db.execute("UPDATE source_records SET metadata_json=? WHERE url=?", (json.dumps({"updated": True}), url))
            raise Share115Error("cancelled", CANCELLED_ERRNO)
        result = self.run_audit(self.client(probe))
        self.assertEqual(result["removed"], 0)

    def test_enrichment_preserves_good_snapshot_and_stops_on_risk(self):
        with connect(self.db) as db:
            db.execute("UPDATE source_records SET metadata_json=?", (json.dumps({"share_snapshot": {"status": "ok", "version": 2}}),))
        client = Mock(spec=Share115Client, fetch_snapshot=Mock(side_effect=Share115Error("blocked", risk_control=True)))
        with self.assertRaises(Share115Error):
            enrich_media_share_sources(self.first, db_path=self.db, force=True, client=client)
        item = get_media(self.first, self.db)
        self.assertEqual(json.loads(item["sources"][0]["metadata_json"])["share_snapshot"]["status"], "ok")

    def test_empty_audit_finishes_and_idle_status_is_safe(self):
        self.assertEqual(audit_status(self.db)["status"], "idle")
        with connect(self.db) as db:
            db.execute("DELETE FROM source_records")
        client = self.client([])
        self.assertEqual(self.run_audit(client)["status"], "complete")
        client.probe_share.assert_not_called()


if __name__ == "__main__":
    unittest.main()
