from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import patch

from backend.share_guangya import ShareGuangyaClient, ShareGuangyaError, parse_share_url


def response(payload: dict) -> BytesIO:
    result = BytesIO(json.dumps(payload, ensure_ascii=False).encode())
    result.__enter__ = lambda: result
    result.__exit__ = lambda *_: False
    return result


class ShareGuangyaTests(unittest.TestCase):
    def test_parse_share_id_and_password_aliases(self) -> None:
        url = "https://www.guangyapan.com/s/1953094801817313288_aeXMzSa0Twth-O73?pwd=abcd"
        self.assertEqual(
            parse_share_url(url),
            ("1953094801817313288_aeXMzSa0Twth-O73", "abcd"),
        )
        self.assertEqual(
            parse_share_url("https://guangyapan.com/s/example?password=efgh"),
            ("example", "efgh"),
        )

    def test_probe_uses_summary_then_access_token(self) -> None:
        with patch(
            "backend.share_guangya.urlopen",
            side_effect=[
                response({"msg": "success", "data": {"shareStatus": 1}}),
                response({"msg": "success", "data": {"accessToken": "token"}}),
            ],
        ) as open_url:
            ShareGuangyaClient().probe_share("https://guangyapan.com/s/example?password=abcd")
        self.assertEqual(open_url.call_count, 2)
        summary_request = open_url.call_args_list[0].args[0]
        access_request = open_url.call_args_list[1].args[0]
        self.assertIn('"shareId": "example"', summary_request.data.decode())
        self.assertIn('"code": "abcd"', access_request.data.decode())

    def test_probe_rejects_wrong_password(self) -> None:
        with patch(
            "backend.share_guangya.urlopen",
            side_effect=[
                response({"msg": "success", "data": {"shareStatus": 1}}),
                response({"code": 201, "msg": "访问密码错误"}),
            ],
        ):
            with self.assertRaisesRegex(ShareGuangyaError, "密码") as raised:
                ShareGuangyaClient().probe_share("https://guangyapan.com/s/example?pwd=bad")
        self.assertTrue(raised.exception.terminal)

    def test_probe_retries_temporary_risk_control_response(self) -> None:
        with patch(
            "backend.share_guangya.urlopen",
            return_value=response({"code": 429, "msg": "请求过于频繁，请稍后重试"}),
        ):
            with self.assertRaises(ShareGuangyaError) as raised:
                ShareGuangyaClient().probe_share("https://guangyapan.com/s/example")
        self.assertFalse(raised.exception.terminal)
        self.assertTrue(raised.exception.risk_control)

    def test_access_token_verification_code_is_retryable(self) -> None:
        with patch(
            "backend.share_guangya.urlopen",
            side_effect=[
                response({"msg": "success", "data": {"needCode": 1, "shareStatus": 1}}),
                response({"code": 209, "msg": "需要验证码"}),
            ],
        ):
            with self.assertRaises(ShareGuangyaError) as raised:
                ShareGuangyaClient().probe_share("https://guangyapan.com/s/example?pwd=bad")
        self.assertFalse(raised.exception.terminal)
        self.assertTrue(raised.exception.risk_control)


if __name__ == "__main__":
    unittest.main()
