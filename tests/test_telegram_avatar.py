import unittest
from unittest.mock import patch

from backend.telegram_avatar import fetch_public_channel_avatar


class TelegramAvatarTests(unittest.TestCase):
    @patch("backend.telegram_avatar.urlopen")
    def test_accepts_reversed_meta_attribute_order(self, urlopen):
        class Response:
            def read(self, _):
                return b'<meta content="https://cdn5.telesco.pe/file/avatar.jpg" property="og:image">'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        urlopen.return_value = Response()
        self.assertEqual(fetch_public_channel_avatar("@QukanMovie"), "https://cdn5.telesco.pe/file/avatar.jpg")


if __name__ == "__main__":
    unittest.main()
