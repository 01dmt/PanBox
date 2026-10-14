"""Read-only validation client for public 光鸭云盘 share links."""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


API_BASE = "https://api.guangyapan.com"
SUMMARY_API = f"{API_BASE}/userres/v1/get_share_summary"
ACCESS_TOKEN_API = f"{API_BASE}/userres/v1/get_share_access_token"


class ShareGuangyaError(RuntimeError):
    def __init__(self, message: str, *, terminal: bool = False, risk_control: bool = False):
        super().__init__(message)
        self.terminal = terminal
        self.risk_control = risk_control


def parse_share_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host not in {"guangyapan.com"}:
        raise ShareGuangyaError("不是受支持的光鸭云盘分享链接。", terminal=True)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[-2].lower() != "s" or not parts[-1]:
        raise ShareGuangyaError("光鸭分享链接缺少分享 ID。", terminal=True)
    query = parse_qs(parsed.query)
    password = (query.get("pwd") or query.get("password") or query.get("code") or [""])[0]
    return parts[-1], password


class ShareGuangyaClient:
    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    def _post(self, endpoint: str, body: dict[str, str]) -> dict:
        request = Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://www.guangyapan.com",
                "Referer": "https://www.guangyapan.com/",
                "User-Agent": "Mozilla/5.0 (PanBox share validator)",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {400, 404}:
                raise ShareGuangyaError("光鸭分享链接无效或访问密码错误。", terminal=True) from exc
            raise ShareGuangyaError(
                f"光鸭分享接口暂时不可用（HTTP {exc.code}）。",
                risk_control=exc.code in {401, 403, 405, 429},
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ShareGuangyaError("无法连接光鸭分享接口，稍后重试。") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ShareGuangyaError("光鸭分享接口返回异常，稍后重试。") from exc
        if not isinstance(payload, dict):
            raise ShareGuangyaError("光鸭分享接口返回异常，稍后重试。")
        return payload

    @staticmethod
    def _business_error(payload: dict, *, phase: str) -> ShareGuangyaError | None:
        code = payload.get("code")
        if code in (None, 0, "0"):
            return None
        try:
            numeric_code = int(code)
        except (TypeError, ValueError):
            numeric_code = None
        message = str(payload.get("msg") or payload.get("message") or "光鸭分享不可访问")[:200]
        risk = bool(re.search(r"频繁|验证码|风控|限流|too many|rate.?limit|captcha", message, re.I))
        # The web client uses 200/201 for invalid shares, 202 for expired
        # shares, and 209 when access-token verification is protected. Keep
        # 209 retryable even when the response message is not localized.
        if phase == "access_token" and numeric_code == 209:
            risk = True
        terminal_codes = {200, 201, 202}
        terminal = not risk and (numeric_code in terminal_codes or bool(re.search(r"密码|提取码|不存在|失效|过期|无效", message, re.I)))
        if not terminal and not risk and phase == "summary":
            # Unknown summary business codes are usually temporary API errors;
            # keep them in the same retry queue as network failures.
            return ShareGuangyaError(message, risk_control=False)
        return ShareGuangyaError(message, terminal=terminal, risk_control=risk)

    def probe_share(self, share_url: str) -> None:
        share_id, password = parse_share_url(share_url)
        summary = self._post(SUMMARY_API, {"shareId": share_id})
        error = self._business_error(summary, phase="summary")
        if error:
            raise error
        data = summary.get("data")
        if not isinstance(data, dict):
            raise ShareGuangyaError("光鸭分享接口缺少分享信息，稍后重试。")
        if data.get("shareStatus") in {0, False, "0"}:
            raise ShareGuangyaError("光鸭分享已失效。", terminal=True)
        if data.get("needCode") and not password:
            raise ShareGuangyaError("光鸭分享需要访问密码。", terminal=True)

        token = self._post(ACCESS_TOKEN_API, {"shareId": share_id, **({"code": password} if password else {})})
        error = self._business_error(token, phase="access_token")
        if error:
            raise error
        token_data = token.get("data")
        if not isinstance(token_data, dict) or not token_data.get("accessToken"):
            raise ShareGuangyaError("光鸭分享接口缺少访问令牌，稍后重试。")
