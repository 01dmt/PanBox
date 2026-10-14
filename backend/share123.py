"""Small, read-only client for validating public 123 网盘 share links."""
from __future__ import annotations

import json
import random
import re
import time
import zlib
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


SHARE_API = "https://yun.123pan.com/b/api/share/get"


class Share123Error(RuntimeError):
    def __init__(self, message: str, *, terminal: bool = False, risk_control: bool = False):
        super().__init__(message)
        self.terminal = terminal
        self.risk_control = risk_control


def parse_share_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host not in {"123865.com", "123pan.com"}:
        raise Share123Error("不是受支持的 123 分享链接。", terminal=True)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[-2].lower() not in {"s", "share", "123pan"} or not parts[-1]:
        raise Share123Error("123 分享链接缺少分享码。", terminal=True)
    query = parse_qs(parsed.query)
    password = (query.get("pwd") or query.get("password") or [""])[0]
    return parts[-1].removesuffix(".html"), password


def _signed_query(path: str, params: dict[str, str]) -> str:
    # 123's web client uses a CRC32 signature as a query key. Keeping this
    # local avoids a third-party dependency and mirrors the public client.
    alphabet = b"adefghlmyijnopkqrstubcvwsz"
    timestamp = str(int(time.time()))
    random_part = str(round(10_000_000 * random.random()))
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d%H%M")
    encoded = bytes(alphabet[int(char)] for char in stamp)
    time_sign = str(zlib.crc32(encoded) & 0xFFFFFFFF)
    data = "|".join((timestamp, random_part, path, "web", "3", time_sign))
    data_sign = str(zlib.crc32(data.encode()) & 0xFFFFFFFF)
    params[time_sign] = f"{timestamp}-{random_part}-{data_sign}"
    return urlencode(params)


class Share123Client:
    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    def probe_share(self, share_url: str) -> None:
        share_key, password = parse_share_url(share_url)
        params = {
            "limit": "1", "next": "0", "orderBy": "file_id", "orderDirection": "desc",
            "parentFileId": "0", "Page": "1", "shareKey": share_key, "SharePwd": password,
        }
        request = Request(
            f"{SHARE_API}?{_signed_query(urlparse(SHARE_API).path, params)}",
            headers={
                "Accept": "application/json",
                "Origin": "https://yun.123pan.com",
                "Referer": "https://yun.123pan.com/",
                "Platform": "web",
                "App-Version": "3",
                "User-Agent": "Mozilla/5.0 (PanBox share validator)",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {400, 401, 403, 404}:
                raise Share123Error("123 分享链接无效或访问密码错误。", terminal=True) from exc
            raise Share123Error(f"123 分享接口暂时不可用（HTTP {exc.code}）。", risk_control=exc.code in {429, 405}) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise Share123Error("无法连接 123 分享接口，稍后重试。") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise Share123Error("123 分享接口返回异常，稍后重试。") from exc

        if not isinstance(payload, dict):
            raise Share123Error("123 分享接口返回异常，稍后重试。")
        code = payload.get("code")
        if code in (0, "0", None) and isinstance(payload.get("data"), dict):
            return
        message = str(payload.get("message") or payload.get("msg") or "123 分享不可访问")
        risk = bool(re.search(r"频繁|验证码|风控|限流|too many|rate.?limit|captcha", message, re.I))
        # A structured non-zero share response is an application decision
        # (bad code, missing share, or wrong password), unless it explicitly
        # signals a temporary protection/rate-limit response.
        terminal = not risk
        raise Share123Error(message[:200], terminal=terminal, risk_control=risk)
