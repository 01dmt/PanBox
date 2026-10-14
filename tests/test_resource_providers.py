from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from backend.resource_providers import PROVIDERS, probe
from backend.share115 import CANCELLED_ERRNO, Share115Error


class ResourceProviderProbeTests(unittest.TestCase):
    def run_probe(self, provider: str, error: Exception | None):
        client = Mock()
        client.probe_share = Mock(side_effect=error)
        label, color, _ = PROVIDERS[provider]
        with patch.dict(PROVIDERS, {provider: (label, color, lambda: client)}):
            return probe(provider, "https://example.com/s/a?password=abcd")

    def test_cancelled_share_is_invalid(self) -> None:
        state, message = self.run_probe("115", Share115Error("分享已取消", CANCELLED_ERRNO))
        self.assertEqual(state, "invalid")

    def test_password_failure_keeps_the_source_pending(self) -> None:
        for message in ("密码错误", "密码验证失败"):
            state, hint = self.run_probe("115", Share115Error(message, 990002, terminal=True))
            self.assertEqual(state, "pending", message)
            self.assertIn("访问码", hint)

    def test_dead_share_text_is_invalid_for_115(self) -> None:
        state, _ = self.run_probe("115", Share115Error("该分享已失效", terminal=True))
        self.assertEqual(state, "invalid")

    def test_access_restriction_stays_pending(self) -> None:
        state, _ = self.run_probe("115", Share115Error("blocked", risk_control=True))
        self.assertEqual(state, "pending")


if __name__ == "__main__":
    unittest.main()
