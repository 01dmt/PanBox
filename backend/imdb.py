from __future__ import annotations

import json
import re
import unicodedata
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .importers import is_search_title


SUGGESTION_BASE = "https://v3.sg.media-imdb.com/suggestion/x/"
IMDB_ID_RE = re.compile(r"^tt[0-9]{7,12}$")
TITLE_TYPES = {
    "movie": "movie", "short": "movie", "video": "movie",
    "tvMovie": "movie", "tvSpecial": "movie", "tvShort": "movie",
    "tvSeries": "tv", "tvMiniSeries": "tv",
}


class ImdbError(RuntimeError):
    pass


def normalize_imdb_title(title: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", title).casefold()).strip()


class ImdbClient:
    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def suggestions(self, title: str) -> list[dict[str, Any]]:
        if title in self._cache:
            return self._cache[title]
        request = Request(
            f"{SUGGESTION_BASE}{quote(title, safe='')}.json",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read(512 * 1024 + 1)
            if len(body) > 512 * 1024:
                raise ImdbError("IMDb 响应超过大小限制。")
            payload = json.loads(body.decode("utf-8"))
        except HTTPError as exc:
            raise ImdbError(f"IMDb 检索接口返回 HTTP {exc.code}。") from exc
        except (URLError, TimeoutError) as exc:
            raise ImdbError("无法连接 IMDb 检索接口。") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImdbError("IMDb 返回了无效数据。") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("d", []), list):
            raise ImdbError("IMDb 返回了未知格式。")
        self._cache[title] = payload.get("d", [])
        return self._cache[title]

    def exact_matches(self, titles: list[str], year: int, *, episodic: bool = False) -> list[dict[str, Any]]:
        if isinstance(year, bool) or not isinstance(year, int) or not 1870 <= year <= 2100:
            return []
        matches: dict[str, dict[str, Any]] = {}
        seen_queries: set[str] = set()
        for title in titles:
            key = re.sub(r"\s+", " ", title).strip().casefold()
            if not is_search_title(title) or key in seen_queries:
                continue
            if len(seen_queries) >= 4:
                break
            seen_queries.add(key)
            for row in self.suggestions(title):
                if not isinstance(row, dict):
                    continue
                imdb_id, name, found_year = row.get("id"), row.get("l"), row.get("y")
                kind = TITLE_TYPES.get(row.get("qid")) if isinstance(row.get("qid"), str) else None
                if (
                    not isinstance(imdb_id, str) or not IMDB_ID_RE.fullmatch(imdb_id)
                    or not isinstance(name, str) or normalize_imdb_title(name) != normalize_imdb_title(title)
                    or isinstance(found_year, bool) or str(found_year) != str(year)
                    or kind is None or (episodic and kind != "tv")
                ):
                    continue
                match = matches.setdefault(imdb_id, {
                    "imdb_id": imdb_id, "title": name, "year": year, "media_type": kind,
                    "matched_queries": [], "url": f"https://www.imdb.com/title/{imdb_id}/",
                })
                if title not in match["matched_queries"]:
                    match["matched_queries"].append(title)
        return list(matches.values())
