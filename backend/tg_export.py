"""解析 Telegram 桌面版导出的 HTML 频道记录。

Telegram Desktop 导出的结构是固定的：`messages.html` 存最旧一页，之后是
`messages2.html` … 每条普通消息都是 `<div class="message default …">`，正文在
该消息的 `.text` 容器里；图片占位、投票、机器人按钮和表情回应都在 `.text`
之外，因此按容器捕获正文时天然被排除。转发消息的正文在 `forwarded body`
的 `.text` 里，同样被捕获。

链接的可见文字经常不是 URL（例如 `🔗链接: 点击查看`），所以锚点统一输出
`href`；`href` 为空的锚点（话题标签）保留可见文字。

不少消息把「查看资源」做成机器人按钮，正文里只有占位文字，链接只存在于
`.bot_buttons_table` 的锚点里，因此按钮链接单独收进 `button_urls`，由导入方
决定哪些需要拼回正文。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator

MESSAGE_FILE_RE = re.compile(r"^messages(\d*)\.html$")
MESSAGE_ID_RE = re.compile(r"^message(-?\d+)$")
DATE_RE = re.compile(
    r"^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})(?:\s*UTC\s*([+-])(\d{2}):?(\d{2}))?$"
)
VOID_TAGS = {"br", "img", "meta", "link", "hr", "input", "area", "base", "col", "embed", "source", "track", "wbr"}


class ExportError(ValueError):
    """导出目录结构不符合 Telegram 桌面版导出格式。"""


@dataclass(frozen=True)
class ExportMessage:
    """一条导出的频道消息。"""

    message_id: int
    published_at: str | None
    text: str
    channel_name: str | None = None
    button_urls: tuple[str, ...] = ()


def parse_published_at(value: str | None) -> str | None:
    """把 `title` 属性里的 `24.07.2024 18:59:33 UTC+08:00` 转成带偏移的 ISO 时间。"""
    if not value:
        return None
    match = DATE_RE.match(str(value).strip())
    if not match:
        return None
    day, month, year, hour, minute, second, sign, offset_hour, offset_minute = match.groups()
    tzinfo = timezone.utc
    if sign and offset_hour and offset_minute:
        offset = timedelta(hours=int(offset_hour), minutes=int(offset_minute))
        tzinfo = timezone(-offset if sign == "-" else offset)
    try:
        moment = datetime(int(year), int(month), int(day), int(hour), int(int(minute)), int(second), tzinfo=tzinfo)
    except ValueError:
        return None
    return moment.isoformat(timespec="seconds")


def normalize_message_text(parts: list[str]) -> str:
    """合并捕获到的文本片段：去行首尾空白、合并多余空行、保留换行结构。"""
    lines = [line.strip() for line in "".join(parts).splitlines()]
    result: list[str] = []
    for line in lines:
        if line or (result and result[-1]):
            result.append(line)
    while result and not result[0]:
        result.pop(0)
    while result and not result[-1]:
        result.pop()
    return "\n".join(result)


def _classes(attrs: list[tuple[str, str | None]]) -> list[str]:
    for key, value in attrs:
        if key == "class" and value:
            return value.split()
    return []


def _attr(attrs: list[tuple[str, str | None]], name: str) -> str | None:
    for key, value in attrs:
        if key == name:
            return value
    return None


class _ExportParser(HTMLParser):
    """逐条产出普通消息，忽略 service 消息和所有非正文元素。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.channel_name: str | None = None
        self.messages: list[ExportMessage] = []
        self._stack: list[str] = []
        self._message: dict[str, object] | None = None
        self._message_depth = 0
        self._text_depth = 0
        self._text_parts: list[str] = []
        self._header_depth = 0
        self._header_parts: list[str] = []
        self._anchor_depth = 0
        self._anchor_href: str | None = None
        self._buttons_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = _classes(attrs)
        if tag not in VOID_TAGS:
            self._stack.append(tag)
        depth = len(self._stack)

        if tag == "div" and "message" in classes:
            self._finish_message()
            identifier = MESSAGE_ID_RE.match(str(_attr(attrs, "id") or ""))
            self._message = {
                "message_id": int(identifier.group(1)) if identifier else None,
                "published_at": None,
                "service": "service" in classes,
                "texts": [],
                "buttons": [],
            }
            self._message_depth = depth
            return

        if self._message is None:
            if tag == "div" and "text" in classes and "bold" in classes and not self._header_depth:
                self._header_depth = depth
                self._header_parts = []
            return

        if tag == "div" and "date" in classes and "details" in classes:
            title = _attr(attrs, "title")
            if self._message.get("published_at") is None and title:
                self._message["published_at"] = parse_published_at(title)
            return

        if tag == "table" and "bot_buttons_table" in classes:
            self._buttons_depth = depth
            return

        if tag == "a" and self._buttons_depth:
            # 机器人按钮里的链接（如「查看资源」）往往才是资源页入口，
            # 正文里只有「🔗链接: 点击查看」这类占位文字。
            href = str(_attr(attrs, "href") or "").strip()
            buttons: list[str] = self._message["buttons"]  # type: ignore[assignment]
            if href and href not in {"#", "#!"} and href not in buttons:
                buttons.append(href)
            return

        if tag == "div" and "text" in classes and not self._text_depth:
            self._text_depth = depth
            self._text_parts = []
            return

        if not self._text_depth:
            return

        if tag == "br":
            self._text_parts.append("\n")
        elif tag == "a":
            href = str(_attr(attrs, "href") or "").strip()
            # 话题标签的 href 是空的，这时保留可见文字（`#标签`）。
            self._anchor_href = href if href and href not in {"#", "#!"} else None
            self._anchor_depth = depth
            if self._anchor_href:
                self._text_parts.append(self._anchor_href)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._message is None:
            if self._header_depth:
                self._header_parts.append(data)
            return
        if not self._text_depth:
            return
        if self._anchor_depth and self._anchor_href:
            # 锚点已经按 href 输出，可见文字（如「点击查看」）不再重复。
            return
        self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        depth = len(self._stack)
        if tag == "a":
            self._anchor_depth = 0
            self._anchor_href = None
        if tag == "table" and self._buttons_depth and depth == self._buttons_depth:
            self._buttons_depth = 0
        if tag == "div" and self._text_depth and depth == self._text_depth:
            text = normalize_message_text(self._text_parts)
            self._text_depth = 0
            self._text_parts = []
            if text and self._message is not None:
                self._message["texts"].append(text)  # type: ignore[union-attr]
        if tag == "div" and self._header_depth and depth == self._header_depth:
            self._header_depth = 0
            name = " ".join("".join(self._header_parts).split())
            self._header_parts = []
            if name and not self.channel_name:
                self.channel_name = name
        if tag == "div" and self._message is not None and depth == self._message_depth:
            self._finish_message()
        if self._stack and tag in self._stack:
            # 只弹出到对应层级，容忍导出文件里偶发的未闭合标签。
            while self._stack:
                popped = self._stack.pop()
                if popped == tag:
                    break

    def _finish_message(self) -> None:
        message = self._message
        self._message = None
        self._message_depth = 0
        self._text_depth = 0
        self._anchor_depth = 0
        self._anchor_href = None
        self._buttons_depth = 0
        if not message or message.get("service"):
            return
        message_id = message.get("message_id")
        texts = [text for text in message["texts"] if text]  # type: ignore[index]
        buttons = tuple(str(url) for url in message["buttons"])  # type: ignore[index]
        # 纯图片消息导出时只有「Not included」占位，没有正文也没有按钮：
        # 仍然产出一条空消息，导入方可以把它统计成「无正文」而不是悄悄漏掉。
        if message_id is None:
            return
        self.messages.append(
            ExportMessage(
                message_id=int(message_id),
                published_at=message.get("published_at"),  # type: ignore[arg-type]
                text="\n".join(texts),
                channel_name=self.channel_name,
                button_urls=buttons,
            )
        )


def export_files(export_path: str | Path) -> list[Path]:
    """返回导出目录里的 messages*.html，按 Telegram 的分页顺序（最旧在前）排列。"""
    path = Path(export_path).expanduser()
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ExportError(f"导出路径不存在：{path}")
    files = []
    for child in path.iterdir():
        match = MESSAGE_FILE_RE.match(child.name)
        if match and child.is_file():
            files.append((int(match.group(1) or 1), child))
    if not files:
        raise ExportError(f"{path} 里没有找到 messages*.html 导出文件。")
    files.sort(key=lambda item: item[0])
    return [child for _, child in files]


def _parse_file(path: Path) -> _ExportParser:
    parser = _ExportParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    parser.close()
    return parser


def read_channel_name(export_path: str | Path) -> str | None:
    """读取导出页头里的频道名（只解析第一页，页头在每页重复）。"""
    files = export_files(export_path)
    return _parse_file(files[0]).channel_name


def iter_export_messages(export_path: str | Path) -> Iterator[ExportMessage]:
    """按时间顺序（最旧在前）产出导出的普通消息。

    service 消息（日期分隔、频道创建等）被跳过；导出里没有正文的纯图片消息以
    空 `text` 产出，由调用方决定如何统计。
    """
    for path in export_files(export_path):
        yield from _parse_file(path).messages
