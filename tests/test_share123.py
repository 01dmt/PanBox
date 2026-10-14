from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import patch

from backend.share123 import Share123Client, Share123Error, parse_share_url


class Share123Tests(unittest.TestCase):
    def test_parse_share_code_and_password(self) -> None:
        self.assertEqual(
            parse_share_url("https://www.123865.com/s/Oqtgvd-XY9hh?pwd=YSRG"),
            ("Oqtgvd-XY9hh", "YSRG"),
        )

    def test_probe_accepts_success_response(self) -> None:
        response = BytesIO(json.dumps({"code": 0, "data": {"InfoList": []}}).encode())
        response.__enter__ = lambda: response
        response.__exit__ = lambda *_: False
        with patch("backend.share123.urlopen", return_value=response) as open_url:
            Share123Client().probe_share("https://123pan.com/s/example?pwd=abcd")
        request = open_url.call_args.args[0]
        self.assertIn("shareKey=example", request.full_url)
        self.assertIn("SharePwd=abcd", request.full_url)

    def test_probe_rejects_wrong_password(self) -> None:
        response = BytesIO(json.dumps({"code": 401, "message": "分享密码错误"}).encode())
        response.__enter__ = lambda: response
        response.__exit__ = lambda *_: False
        with patch("backend.share123.urlopen", return_value=response):
            with self.assertRaisesRegex(Share123Error, "密码") as raised:
                Share123Client().probe_share("https://123pan.com/s/example?pwd=bad")
        self.assertTrue(raised.exception.terminal)


if __name__ == "__main__":
    unittest.main()
