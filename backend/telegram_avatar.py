from __future__ import annotations

import html
import re
from urllib.request import Request, urlopen


USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
OG_IMAGE_RE = re.compile(
    r'<meta\s+[^>]*property=["\'](?:og:image|twitter:image)["\'][^>]*content=["\']([^"\']+)',
    re.IGNORECASE,
)
PHOTO_RE = re.compile(r'<img\s+[^>]*class=["\'][^"\']*tgme_page_photo_image[^"\']*["\'][^>]*src=["\']([^"\']+)', re.IGNORECASE)
ALLOWED_IMAGE_HOST_RE = re.compile(r"^cdn\d+\.telesco\.pe$", re.IGNORECASE)


def normalize_channel_username(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    username = value.strip().lstrip("@").strip()
    return username if USERNAME_RE.fullmatch(username) else None


def fetch_public_channel_avatar(username: str | None) -> str | None:
    """Resolve a public Telegram channel username to its preview avatar URL.

    This intentionally uses Telegram's public preview page and only accepts
    Telegram's own CDN as the returned image host. Private channels require a
    Telegram API client and are left unresolved.
    """
    username = normalize_channel_username(username)
    if not username:
        return None
    request = Request(
        f"https://t.me/{username}",
        headers={"User-Agent": "Mozilla/5.0 (PanBox channel preview)", "Accept": "text/html"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            page = response.read(512 * 1024).decode("utf-8", errors="replace")
    except Exception:
        return None
    match = OG_IMAGE_RE.search(page) or PHOTO_RE.search(page)
    if not match:
        return None
    value = html.unescape(match.group(1)).strip()
    if not value.startswith("https://"):
        return None
    from urllib.parse import urlparse

    parsed = urlparse(value)
    if parsed.scheme != "https" or not ALLOWED_IMAGE_HOST_RE.fullmatch(parsed.hostname or ""):
        return None
    return value
