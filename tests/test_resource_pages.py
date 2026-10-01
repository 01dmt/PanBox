from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from backend.resource_pages import expand_resource_pages, fetch_resource_page


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ResourcePageTests(unittest.TestCase):
    def test_extracts_title_and_sources_from_telegraph_html(self) -> None:
        html = """
        <html><head><title>Chen – Telegraph</title>
        <meta property='og:title' content='陈百强'></head>
        <body><p>资料</p><p>ed2k://|file|陈百强01.zip|20|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/</p>
        <p>ed2k://|file|陈百强02.zip|30|BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB|/</p></body></html>
        """.encode()
        with patch("backend.resource_pages.urlopen", return_value=_Response(html)):
            title, body = fetch_resource_page("https://telegra.ph/chen-09-24")

        self.assertEqual(title, "陈百强")
        self.assertEqual(body.count("ed2k://"), 2)

    def test_expands_only_supported_pages_and_keeps_original_text(self) -> None:
        html = "<html><head><meta property='og:title' content='陈百强'></head><body>ed2k://|file|x.zip|20|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/</body></html>".encode()
        message = "👤 陈百强\n📎 查看资源 https://telegra.ph/chen-09-24"
        with patch("backend.resource_pages.urlopen", return_value=_Response(html)):
            expanded = expand_resource_pages(message)

        self.assertTrue(expanded.startswith(message))
        self.assertIn("📺 陈百强", expanded)
        self.assertIn("ed2k://|file|x.zip", expanded)


if __name__ == "__main__":
    unittest.main()
