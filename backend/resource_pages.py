"""Resolve supported resource-page links embedded in forwarded messages."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


RESOURCE_PAGE_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
RESOURCE_PAGE_HOSTS = {"telegra.ph", "files.catbox.moe"}
MAX_PAGE_BYTES = 512 * 1024
FETCH_TIMEOUT = 8


def _clean_url(value: str) -> str:
    return value.rstrip(".,;:!?，。；！？）)]}>")


def _is_supported_page(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = parsed.hostname.lower().removeprefix("www.") if parsed.hostname else ""
    return parsed.scheme.lower() == "https" and host in RESOURCE_PAGE_HOSTS and bool(parsed.path)


class _TelegraphParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.og_title: str | None = None
        self.text_parts: list[str] = []
        self.hrefs: list[str] = []
        self._in_title = False
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "title":
            self._in_title = True
        if tag == "a":
            attributes = dict(attrs)
            href = attributes.get("href")
            if href and (_is_supported_source(href) or _is_supported_page(href)):
                self.hrefs.append(href)
        if tag == "meta":
            attributes = dict(attrs)
            if attributes.get("property", "").lower() == "og:title" and attributes.get("content"):
                self.og_title = attributes["content"].strip()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if not self._ignored_depth and tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = re.sub(r"\s+", " ", data).strip()
        if not value:
            return
        if self._in_title:
            self.title_parts.append(value)
        self.text_parts.append(value)


def _is_supported_source(url: str) -> bool:
    lowered = url.lower()
    return lowered.startswith("ed2k://") or bool(re.match(r"https?://(?:www\.)?115(?:cdn)?\.com/s/", lowered))


def _page_title(parser: _TelegraphParser) -> str | None:
    value = (parser.og_title or " ".join(parser.title_parts)).strip()
    value = re.sub(r"\s*[–—-]\s*Telegraph\s*$", "", value, flags=re.IGNORECASE).strip()
    return value or None


def fetch_resource_page(url: str) -> tuple[str | None, str]:
    """Fetch one supported page and return its title plus source-bearing text."""
    request = Request(url, headers={"User-Agent": "PanBox/1.0"})
    with urlopen(request, timeout=FETCH_TIMEOUT) as response:
        payload = response.read(MAX_PAGE_BYTES + 1)
    if len(payload) > MAX_PAGE_BYTES:
        raise ValueError("资源页面超过大小限制")
    parser = _TelegraphParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    parts = list(parser.text_parts)
    parts.extend(parser.hrefs)
    return _page_title(parser), "\n".join(parts)


def expand_resource_pages(text: str) -> str:
    """Append source-bearing content from supported resource pages.

    The original message remains at the front so it is still retained verbatim
    in the ingestion record. Only explicitly supported HTTPS hosts are fetched.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for match in RESOURCE_PAGE_RE.finditer(text):
        url = _clean_url(match.group(0))
        if _is_supported_page(url) and url not in seen:
            seen.add(url)
            urls.append(url)
    if not urls:
        return text

    sections = [text]
    queue = list(urls)
    while queue:
        url = queue.pop(0)
        try:
            title, page_text = fetch_resource_page(url)
        except Exception:
            continue
        nested = []
        for match in RESOURCE_PAGE_RE.finditer(page_text):
            nested_url = _clean_url(match.group(0))
            if _is_supported_page(nested_url) and nested_url not in seen:
                seen.add(nested_url)
                nested.append(nested_url)
        source_lines = [line for line in page_text.splitlines() if _is_supported_source(line)]
        queue.extend(nested)
        if not source_lines and not nested:
            continue
        page_host = urlsplit(url).hostname.lower().removeprefix("www.")
        if not title:
            before = text[: text.find(url)]
            heading_match = re.findall(r"(?m)^\s*([📺🎥🎬👤🗂].+)$", before)
            title = heading_match[-1].strip() if heading_match else None
        elif page_host == "telegra.ph":
            title = f"📺 {title}"
        heading = f"{title}\n" if title else ""
        source_text = "\n".join(source_lines)
        sections.append(f"{heading}{source_text}")
    return "\n".join(sections)
