from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.tg_export import (
    ExportError,
    export_files,
    iter_export_messages,
    normalize_message_text,
    parse_published_at,
    read_channel_name,
)

HEADER = """
<div class="page_wrap">
 <div class="page_header">
  <div class="content">
   <div class="text bold">
115网盘资源分享频道 
   </div>
  </div>
 </div>
 <div class="page_body chat_page">
  <div class="history">
"""

FOOTER = """
  </div>
 </div>
</div>
"""

MESSAGE_WITH_BUTTON = """
<div class="message default clearfix" id="message3">
 <div class="pull_left userpic_wrap">
  <div class="userpic userpic6"><div class="initials">1</div></div>
 </div>
 <div class="body">
  <div class="pull_right date details" title="09.12.2023 03:40:30 UTC+08:00">03:40</div>
  <div class="from_name">115网盘资源分享频道 </div>
  <div class="media_wrap clearfix">
   <div class="media clearfix pull_left media_photo">
    <div class="body">
     <div class="title bold">Photo</div>
     <div class="description">Not included, change data exporting settings to download.</div>
     <div class="status details">500×750, 20.9 KB</div>
    </div>
   </div>
  </div>
  <div class="text">🎥 国王排名 (2021)<br>🔗链接: <a href="https://115.com/s/abc123?password=f816#">点击查看</a><br>🏷标签: <a href="" onclick="return ShowHashtag(&quot;动画&quot;)">#动画</a></div>
  <table class="bot_buttons_table">
   <tbody>
    <tr>
     <td class="bot_button_row">
      <div class="bot_button"><a ="" href="https://telegra.ph/king-2021"><div>查看资源</div></a></div>
      <div class="bot_button"><a ="" href="https://t.me/hao115"><div>频道</div></a></div>
     </td>
    </tr>
   </tbody>
  </table>
  <span class="reactions">
   <span class="reaction"><span class="emoji">👍</span><span class="count">2</span></span>
  </span>
 </div>
</div>
"""

JOINED_MESSAGE = """
<div class="message default clearfix joined" id="message4">
 <div class="body">
  <div class="pull_right date details" title="09.12.2023 03:41:00 UTC+08:00">03:41</div>
  <div class="text">第二条 <a href="https://115cdn.com/s/xyz789?password=aa11">链接</a></div>
 </div>
</div>
"""

FORWARDED_MESSAGE = """
<div class="message default clearfix" id="message5">
 <div class="pull_left userpic_wrap">
  <div class="userpic userpic6"><div class="initials">1</div></div>
 </div>
 <div class="body">
  <div class="pull_right date details" title="10.12.2023 08:00:00 UTC+08:00">08:00</div>
  <div class="from_name">115网盘资源分享频道 </div>
  <div class="pull_left forwarded userpic_wrap">
   <div class="userpic userpic6"><div class="initials">SZ</div></div>
  </div>
  <div class="forwarded body">
   <div class="from_name">Su ZL<span class="date details" title="10.12.2023 07:59:55 UTC+08:00"> 10.12.2023 07:59:55</span></div>
   <div class="text">转发的资源<br><a href="https://115.com/s/fwd1?password=zz99">https://115.com/s/fwd1?password=zz99</a></div>
  </div>
 </div>
</div>
"""

PHOTO_ONLY = """
<div class="message default clearfix joined" id="message6">
 <div class="body">
  <div class="pull_right date details" title="10.12.2023 09:00:00 UTC+08:00">09:00</div>
  <div class="media_wrap clearfix">
   <div class="media clearfix pull_left media_photo">
    <div class="body">
     <div class="title bold">Photo</div>
     <div class="description">Not included, change data exporting settings to download.</div>
    </div>
   </div>
  </div>
 </div>
</div>
"""

BUTTON_ONLY = """
<div class="message default clearfix joined" id="message7">
 <div class="body">
  <div class="pull_right date details" title="10.12.2023 09:05:00 UTC+08:00">09:05</div>
  <table class="bot_buttons_table">
   <tbody>
    <tr>
     <td class="bot_button_row">
      <div class="bot_button"><a ="" href="https://telegra.ph/only-button"><div>查看资源</div></a></div>
     </td>
    </tr>
   </tbody>
  </table>
 </div>
</div>
"""


def write_export(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (root / name).write_text(HEADER + body + FOOTER, encoding="utf-8")
    return root


class ExportParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_parse_published_at_keeps_offset(self) -> None:
        self.assertEqual(parse_published_at("24.07.2024 18:59:33 UTC+08:00"), "2024-07-24T18:59:33+08:00")
        self.assertEqual(parse_published_at("1.1.2025 00:00:00 UTC-05:30"), "2025-01-01T00:00:00-05:30")
        self.assertIsNone(parse_published_at("not a date"))
        self.assertIsNone(parse_published_at(None))

    def test_normalize_message_text_strips_indentation_and_blank_runs(self) -> None:
        # <br><br> 会留下一个空行，HTML 缩进不能变成正文里的空白。
        self.assertEqual(
            normalize_message_text(["\n  标题  \n", "\n", "\n正文\n       "]),
            "标题\n\n正文",
        )

    def test_iter_export_messages_extracts_actionable_text(self) -> None:
        write_export(
            self.root,
            {
                "messages.html": MESSAGE_WITH_BUTTON
                + """
<div class="message service" id="message-2"><div class="body details">9 December 2023</div></div>
<div class="message service" id="message2"><div class="body details">Channel &laquo;115网盘资源分享频道&raquo; created</div></div>
"""
                + JOINED_MESSAGE,
            },
        )
        messages = list(iter_export_messages(self.root))
        self.assertEqual([message.message_id for message in messages], [3, 4])

        first = messages[0]
        self.assertEqual(first.channel_name, "115网盘资源分享频道")
        self.assertEqual(first.published_at, "2023-12-09T03:40:30+08:00")
        # 链接按 href 输出，占位文字「点击查看」被丢掉，话题标签保留。
        self.assertIn("🔗链接: https://115.com/s/abc123?password=f816#", first.text)
        self.assertIn("#动画", first.text)
        self.assertNotIn("点击查看", first.text)
        # 图片占位、机器人按钮和表情回应都不进正文。
        self.assertNotIn("Not included", first.text)
        self.assertNotIn("查看资源", first.text)
        self.assertNotIn("👍", first.text)
        self.assertEqual(
            first.button_urls,
            ("https://telegra.ph/king-2021", "https://t.me/hao115"),
        )
        self.assertEqual(messages[1].published_at, "2023-12-09T03:41:00+08:00")

    def test_forwarded_and_button_only_messages_are_kept(self) -> None:
        write_export(self.root, {"messages.html": FORWARDED_MESSAGE + PHOTO_ONLY + BUTTON_ONLY})
        messages = list(iter_export_messages(self.root))
        # 纯图片消息没有正文，仍然产出，方便调用方统计「无正文」。
        self.assertEqual([message.message_id for message in messages], [5, 6, 7])
        self.assertEqual(messages[0].text, "转发的资源\nhttps://115.com/s/fwd1?password=zz99")
        self.assertEqual(messages[1].text, "")
        self.assertEqual(messages[1].button_urls, ())
        self.assertEqual(messages[2].text, "")
        self.assertEqual(messages[2].button_urls, ("https://telegra.ph/only-button",))

    def test_channel_name_comes_from_page_header(self) -> None:
        write_export(self.root, {"messages.html": JOINED_MESSAGE})
        self.assertEqual(read_channel_name(self.root), "115网盘资源分享频道")

    def test_export_files_are_ordered_by_page_number(self) -> None:
        write_export(self.root, {"messages10.html": JOINED_MESSAGE, "messages2.html": JOINED_MESSAGE, "messages.html": JOINED_MESSAGE})
        names = [path.name for path in export_files(self.root)]
        self.assertEqual(names, ["messages.html", "messages2.html", "messages10.html"])

    def test_iter_export_messages_walks_every_page(self) -> None:
        write_export(self.root, {"messages.html": MESSAGE_WITH_BUTTON, "messages2.html": JOINED_MESSAGE})
        self.assertEqual([message.message_id for message in iter_export_messages(self.root)], [3, 4])

    def test_single_file_path_is_accepted(self) -> None:
        write_export(self.root, {"messages.html": JOINED_MESSAGE})
        messages = list(iter_export_messages(self.root / "messages.html"))
        self.assertEqual([message.message_id for message in messages], [4])

    def test_missing_export_raises(self) -> None:
        with self.assertRaises(ExportError):
            export_files(self.root / "missing")
        self.root.mkdir(exist_ok=True)
        (self.root / "readme.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(ExportError):
            export_files(self.root)


if __name__ == "__main__":
    unittest.main()
