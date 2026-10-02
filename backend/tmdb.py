from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .importers import (
    extract_tmdb_ids, is_search_title, normalize_title, parse_release_aliases,
    parse_release_title, parse_title_year, split_joined_title,
    SEASON_EPISODE_RE, SEASON_RE,
)
from .recognition import Candidate as RecognitionCandidate, Recognizer as ReusableRecognizer, parse as parse_recognition
from .imdb import ImdbClient, ImdbError, IMDB_ID_RE, normalize_imdb_title
from .repository import (
    get_media,
    link_tmdb,
    pending_media_ids,
    save_candidates,
    save_imdb_lookup,
)
from .share115 import cleanup_cancelled_shares, enrich_media_share_sources, inspect_resource


API_BASE = "https://api.themoviedb.org/3"
AUTO_LINK_SCORE = 0.90
AUTO_LINK_MARGIN = 0.08
EXACT_TITLE_SCORE = 0.88
EXACT_TITLE_MARGIN = 0.12
MAX_SEARCH_TERMS = 4
MAX_TITLE_LOOKUPS = 12
AUTO_LINK_BLOCKERS = (
    "verification_incomplete", "collection", "name_conflict", "type_conflict", "year_conflict", "year_unverified",
    "resource_conflict", "resource_incomplete", "resource_name_conflict", "resource_name_unverified",
    "season_episode_conflict",
)
EDITION_SUFFIX_RE = re.compile(
    r"(?i)\s+(?:director'?s?\s+cut|directors\s+cut|theatrical(?:\s+(?:cut|version))?|"
    r"extended(?:\s+(?:edition|cut))?|unrated|remastered|international(?:\s+(?:version|cut))?|"
    r"movie|国际版|杜比(?:版)?)$"
)
CJK_LATIN_BOUNDARY_RE = re.compile(r"(?<=[\u3400-\u9fff])\s+(?=[A-Za-z])")
COLLECTION_RE = re.compile(r"合集|[（(]\s*系列\s*[)）]|\b(?:collection|box[ .-]?set)\b", re.IGNORECASE)


class TmdbError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def tmdb_year(candidate: dict[str, Any]) -> int | None:
    value = candidate.get("release_date") or candidate.get("first_air_date")
    if not isinstance(value, str):
        return None
    match = re.match(r"^([0-9]{4})(?:-|$)", value.strip())
    if not match:
        return None
    return int(match.group(1)) or None


def dated_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [candidate for candidate in candidates if tmdb_year(candidate) is not None]


def matching_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [c for c in dated_candidates(candidates) if not any(
        (c.get("match_evidence") or {}).get(key) for key in ("year_conflict", "resource_type_conflict")
    )]
    localized_groups = set()
    for candidate in candidates:
        title = candidate.get("title") or ""
        evidence = candidate.get("match_evidence") or {}
        if (
            re.search(r"[\u3400-\u9fff]", title)
            and not re.search(r"[\u3040-\u30ff\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]", title)
            and evidence.get("title_exact")
            and candidate["score"] >= EXACT_TITLE_SCORE
            and candidate.get("media_type") in {"movie", "tv"}
            and not any(evidence.get(key) for key in AUTO_LINK_BLOCKERS)
        ):
            localized_groups.add((candidate["media_type"], tmdb_year(candidate)))

    result = []
    for candidate in candidates:
        evidence = candidate.get("match_evidence") or {}
        letters = [ch for ch in (candidate.get("title") or "") if ch.isalpha()]
        english_only = bool(letters) and all(ch.isascii() for ch in letters)
        if (
            english_only and evidence.get("title_exact")
            and not evidence.get("explicit_tmdb_id")
            and (candidate.get("media_type"), tmdb_year(candidate)) in localized_groups
        ):
            continue
        result.append(candidate)
    return result


def matching_years(local_year: int | None, source_years: list[int] | None = None) -> set[int]:
    if local_year is not None:
        return {int(local_year)}
    return {int(year) for year in source_years or []}


def payload_titles(payload: dict[str, Any]) -> list[str]:
    values = [payload.get("title") or payload.get("name")]
    aliases = payload.get("alternative_titles") or {}
    values.extend(row.get("title") for row in aliases.get("titles", aliases.get("results", [])))
    for row in (payload.get("translations") or {}).get("translations", []):
        data = row.get("data") or {}
        values.append(data.get("title") or data.get("name"))
    return list(dict.fromkeys(v for v in values if isinstance(v, str) and is_search_title(v)))


def verified_imdb_identity(
    payload: dict[str, Any], media_type: str, years: set[int], titles: list[str],
) -> dict[str, Any]:
    proof = payload.get("_imdb_identity")
    if not isinstance(proof, dict):
        return {}
    if isinstance(proof.get("year"), bool) or not isinstance(proof.get("year"), int):
        return {}
    actual_year = tmdb_year(payload)
    external_ids = payload.get("external_ids") or {}
    backref = payload.get("imdb_id") or (external_ids.get("imdb_id") if isinstance(external_ids, dict) else None)
    title = proof.get("title")
    if (
        proof.get("verified") is not True or not actual_year
        or not isinstance(title, str) or normalize_imdb_title(title) not in {normalize_imdb_title(t) for t in titles}
        or not IMDB_ID_RE.fullmatch(str(proof.get("imdb_id", ""))) or backref != proof.get("imdb_id")
        or proof.get("tmdb_id") != payload.get("id") or proof.get("media_type") != media_type
        or years != {proof.get("year")}
    ):
        return {}
    if proof["year"] != actual_year and not (
        proof.get("year_override") is True and proof.get("tmdb_year") == actual_year
    ):
        return {}
    return proof


def compare_episode_coverage(
    candidate: dict[str, Any], details: dict[str, Any], coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    proof: dict[str, Any] = {"status": "unverified", "resource": coverage}
    if not isinstance(details, dict) or details.get("id") != candidate["tmdb_id"]:
        return {**proof, "reason": "identity_changed"}
    names = {normalize_title(t) for t in (candidate["title"], candidate.get("original_title")) if t}
    detail_names = {normalize_title(t) for t in (details.get("name"), details.get("original_name")) if isinstance(t, str) and t}
    if tmdb_year(details) != tmdb_year(candidate) or not names.intersection(detail_names):
        return {**proof, "reason": "identity_changed"}
    if details.get("status") != "Ended":
        return {**proof, "reason": "series_not_ended"}
    seasons = details.get("seasons")
    if not isinstance(seasons, list) or not seasons:
        return {**proof, "reason": "missing_seasons"}
    counts = {}
    for season in seasons:
        if not isinstance(season, dict):
            return {**proof, "reason": "invalid_seasons"}
        number, count = season.get("season_number"), season.get("episode_count")
        if type(number) is not int or number < 0 or number in counts:
            return {**proof, "reason": "invalid_seasons"}
        if number == 0:
            continue
        if type(count) is not int or count <= 0:
            return {**proof, "reason": "missing_episode_count"}
        counts[number] = count
    if any(row["season"] not in counts for row in coverage):
        return {**proof, "reason": "missing_resource_season"}
    total = details.get("number_of_episodes")
    season_total = details.get("number_of_seasons")
    consistent = (
        type(total) is int and total == sum(counts.values())
        and type(season_total) is int and season_total == len(counts)
    )
    proof.update(
        tmdb_seasons=[{"season": n, "episode_count": count} for n, count in sorted(counts.items())],
        tmdb_total_episodes=total, totals_consistent=consistent,
    )
    if any(row["last_episode"] > counts[row["season"]] for row in coverage):
        return {**proof, "status": "conflict", "reason": "resource_exceeds_season"}
    if not consistent:
        return {**proof, "reason": "inconsistent_totals"}
    exact = all(
        row["contiguous"] and row["first_episode"] == 1 and row["episode_count"] == counts[row["season"]]
        for row in coverage
    )
    return {**proof, "status": "exact" if exact else "compatible"}


@dataclass(frozen=True)
class SearchTerm:
    title: str
    year: int | None


@dataclass(frozen=True)
class SearchContext:
    terms: list[SearchTerm]
    titles: list[str]
    years: list[int]
    explicit_tmdb_ids: list[int]
    episodic: bool = False
    resource_groups: list[list[str]] = field(default_factory=list)
    resource_kind: str = "unknown"
    resource_conflict: bool = False
    resource_incomplete: bool = False
    resource_episodes: list[dict[str, Any]] = field(default_factory=list)
    resource_episode_incomplete: bool = False


def _title_variants(value: str) -> list[str]:
    base = re.sub(r"\s+", " ", value).strip(" \t-–—|·[]【】()（）")
    if not base:
        return []

    bracket_parts = [
        part.strip(" \t-–—|·[]【】()（）")
        for part in re.split(r"[\]】]\s*", base)
        if part.strip(" \t-–—|·[]【】()（）")
    ]
    candidates = bracket_parts if len(bracket_parts) > 1 else [base]
    expanded: list[str] = []
    for candidate in candidates:
        boundary_parts = [part.strip() for part in CJK_LATIN_BOUNDARY_RE.split(candidate, maxsplit=1)]
        if len(boundary_parts) > 1 and all(boundary_parts):
            expanded.extend(boundary_parts)
        else:
            expanded.append(candidate)
    expanded.append(base)

    result: list[str] = []
    seen: set[str] = set()
    for candidate in expanded:
        for option in (EDITION_SUFFIX_RE.sub("", candidate).strip(), candidate.strip()):
            normalized = normalize_title(option)
            if not is_search_title(option) or normalized in seen:
                continue
            seen.add(normalized)
            result.append(option)
    return result


def collect_search_context(item: dict[str, Any]) -> SearchContext:
    terms: list[SearchTerm] = []
    titles: list[str] = []
    years: list[int] = []
    explicit_ids: list[int] = []
    seen_terms: set[tuple[str, int | None]] = set()
    seen_titles: set[str] = set()
    seen_years: set[int] = set()
    seen_ids: set[int] = set()
    episodic = False
    resource_groups: list[list[str]] = []
    resource_kinds: set[str] = set()
    resource_incomplete = False
    resource_episodes: list[dict[str, Any]] = []
    resource_episode_incomplete = False

    def add_episode_coverage(resource: dict[str, Any]) -> None:
        nonlocal resource_episode_incomplete
        if resource["kind"] != "tv":
            return
        coverage = resource.get("episode_coverage") or []
        resource_episode_incomplete = resource_episode_incomplete or not coverage
        for row in coverage:
            if row not in resource_episodes:
                resource_episodes.append(row)

    def add_year(value: Any) -> None:
        try:
            year = int(value)
        except (TypeError, ValueError):
            return
        if 1870 <= year <= 2100 and year not in seen_years:
            seen_years.add(year)
            years.append(year)

    def add_value(value: Any, year_hint: Any = None, *, label: bool = False) -> None:
        nonlocal episodic
        if not isinstance(value, str) or not value.strip():
            return
        if not label and (SEASON_EPISODE_RE.search(value) or SEASON_RE.search(value)):
            episodic = True
        for tmdb_id in extract_tmdb_ids(value):
            if tmdb_id not in seen_ids:
                seen_ids.add(tmdb_id)
                explicit_ids.append(tmdb_id)

        # A library label is already a title: digits in "Cold War 1994" are not a release year.
        parsed_title, parsed_year = parse_title_year(value) if label else parse_release_title(value)
        add_year(parsed_year)
        add_year(year_hint)
        effective_year = parsed_year or (int(year_hint) if str(year_hint or "").isdigit() else None)
        release_titles = [parsed_title] if label else parse_release_aliases(value)
        if label and effective_year and parsed_title.startswith(str(effective_year)) and len(parsed_title) > 4:
            release_titles.extend(parse_release_aliases(parsed_title))
        for release_title in release_titles:
            for title in _title_variants(release_title):
                normalized = normalize_title(title)
                if normalized not in seen_titles:
                    seen_titles.add(normalized)
                    titles.append(title)
                query_key = (re.sub(r"\s+", " ", title).casefold(), effective_year)
                if query_key not in seen_terms:
                    seen_terms.add(query_key)
                    terms.append(SearchTerm(title=title, year=effective_year))

    # Telegram channel messages use a stable heading convention. Prefer the
    # heading's explicit TMDB id, then its ``Title (Year)`` identity before
    # considering noisy source filenames or placeholder channel fields.
    channel_heading = re.compile(r"(?m)^\s*[📺🎥🎬]\s*(.+?)\s*[（(]\s*((?:19|20)\d{2})\s*[）)]")
    for source in item.get("sources") or []:
        raw_text = source.get("raw_text")
        if not isinstance(raw_text, str):
            continue
        for tmdb_id in extract_tmdb_ids(raw_text):
            if tmdb_id not in seen_ids:
                seen_ids.add(tmdb_id)
                explicit_ids.append(tmdb_id)
        match = channel_heading.search(raw_text)
        if match:
            add_value(f"{match.group(1).strip()} ({match.group(2)})", label=True)
            episodic = episodic or raw_text.lstrip().startswith("📺")

    add_year(item.get("year"))
    add_value(item.get("title"), item.get("year"), label=True)
    ed2k_names = [s["filename"] for s in item.get("sources") or [] if s.get("source_type") == "ed2k" and s.get("filename")]
    ed2k_episodes = [bool(SEASON_EPISODE_RE.search(n) or SEASON_RE.search(n)) for n in ed2k_names]
    if ed2k_episodes and any(ed2k_episodes):
        resource_kinds.add("tv" if all(ed2k_episodes) else "mixed")
        add_episode_coverage(inspect_resource({
            "status": "ok", "complete": True,
            "files": [{"name": name, "path": name} for name in ed2k_names],
        }))
    for source in item.get("sources") or []:
        episodic = episodic or source.get("season") is not None or source.get("episode") is not None
        source_year = source.get("parsed_year") or item.get("year")
        add_value(source.get("filename"), source_year)
        add_value(source.get("raw_label"), source_year, label=source.get("source_type") == "115")
        raw_text = source.get("raw_text")
        if isinstance(raw_text, str):
            for tmdb_id in extract_tmdb_ids(raw_text):
                if tmdb_id not in seen_ids:
                    seen_ids.add(tmdb_id)
                    explicit_ids.append(tmdb_id)

        metadata = source.get("metadata_json")
        if isinstance(metadata, str) and metadata:
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if isinstance(metadata, dict):
            add_value(metadata.get("extra"), source_year)
            share_snapshot = metadata.get("share_snapshot")
            if isinstance(share_snapshot, dict) and share_snapshot.get("status") == "ok":
                add_value(share_snapshot.get("share_title"), source_year)
                for name in share_snapshot.get("search_names") or []:
                    add_value(name, source_year)
                resource = inspect_resource(share_snapshot)
                add_episode_coverage(resource)
                resource_incomplete = resource_incomplete or share_snapshot.get("complete") is not True
                if resource["kind"] != "unknown":
                    resource_kinds.add(resource["kind"])
                if resource["kind"] in {"tv", "single_video"}:
                    episodic = episodic or resource["kind"] == "tv"
                    if resource["titles"]:
                        resource_groups.append(resource["titles"])
                        for name in resource["titles"]:
                            add_value(name, source_year, label=True)

    return SearchContext(
        terms=terms,
        titles=titles,
        years=years,
        explicit_tmdb_ids=explicit_ids,
        episodic=episodic,
        resource_groups=resource_groups,
        resource_kind="tv" if resource_kinds == {"tv"} else "single_video" if resource_kinds == {"single_video"} else "unknown",
        resource_conflict="mixed" in resource_kinds or ("tv" in resource_kinds and "single_video" in resource_kinds),
        resource_incomplete=resource_incomplete,
        resource_episodes=resource_episodes,
        resource_episode_incomplete=resource_episode_incomplete,
    )


class TmdbClient:
    def __init__(self, token: str | None = None, api_key: str | None = None):
        self.token = token or os.getenv("TMDB_API_TOKEN")
        self.api_key = api_key or os.getenv("TMDB_API_KEY")
        self._title_cache: dict[tuple[str, int], list[str]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.token or self.api_key)

    @property
    def auth_mode(self) -> str | None:
        if self.token:
            return "bearer"
        if self.api_key:
            return "api_key"
        return None

    def request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise TmdbError("TMDB 未配置。请在 .env 中设置 TMDB_API_TOKEN 或 TMDB_API_KEY。")

        query = {key: value for key, value in (params or {}).items() if value not in (None, "")}
        if self.api_key and not self.token:
            query["api_key"] = self.api_key
        url = f"{API_BASE}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"

        headers = {
            "Accept": "application/json",
            "User-Agent": "Yingku/1.0 (local media manager)",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            retry_after = exc.headers.get("Retry-After")
            if exc.code == 429 and retry_after:
                time.sleep(min(float(retry_after), 10.0))
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                message = payload.get("status_message") or str(exc)
            except Exception:
                message = str(exc)
            raise TmdbError(f"TMDB 请求失败 ({exc.code}): {message}", exc.code) from exc
        except URLError as exc:
            raise TmdbError(f"无法连接 TMDB: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TmdbError("TMDB 请求超时。") from exc

    def manual_search(
        self, query: str, *, media_type: str = "multi", page: int = 1, include_adult: bool = False,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query or len(query) > 200:
            raise ValueError("请输入 1 至 200 个字符的 TMDB 搜索词。")
        if media_type not in {"multi", "movie", "tv"}:
            raise ValueError("搜索类型必须为 multi、movie 或 tv。")
        if type(page) is not int or not 1 <= page <= 500:
            raise ValueError("TMDB 搜索页码必须在 1 至 500 之间。")
        try:
            payload = self.request(f"/search/{media_type}", {
                "query": query, "language": "zh-CN", "page": page,
                "include_adult": "true" if include_adult else "false",
            })
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("invalid search response")
            pages = int(payload.get("total_pages") or 0)
        except (ValueError, TypeError) as exc:
            raise TmdbError("TMDB 返回的搜索数据格式异常，请稍后重试。") from exc

        results = []
        seen = set()
        undated = 0
        for row in payload["results"]:
            if not isinstance(row, dict):
                continue
            kind = row.get("media_type") or media_type
            if kind not in {"movie", "tv"} or type(row.get("id")) is not int or row["id"] <= 0:
                continue
            if media_type != "multi" and kind != media_type:
                continue
            key = (kind, row["id"])
            if key in seen:
                continue
            seen.add(key)
            if tmdb_year(row) is None:
                undated += 1
                continue
            title = row.get("title") or row.get("name") or "未命名"
            if not isinstance(title, str):
                continue
            # Manual discovery preserves TMDB order, without local matching rules or database writes.
            results.append({
                "tmdb_id": row["id"], "media_type": kind,
                "title": title,
                "original_title": row.get("original_title") or row.get("original_name"),
                "release_date": row.get("release_date") or row.get("first_air_date"),
                "poster_path": row.get("poster_path"), "overview": row.get("overview"),
            })
        return {
            "query": query, "media_type": media_type, "include_adult": include_adult,
            "page": page, "pages": max(1, min(500, pages)), "results": results,
            "excluded_undated": undated,
        }

    def translated_titles(self, media_type: str, tmdb_id: int) -> list[str]:
        key = (media_type, tmdb_id)
        if key not in self._title_cache:
            payload = self.request(
                f"/{media_type}/{tmdb_id}",
                {"language": "en-US", "append_to_response": "alternative_titles,translations"},
            )
            if payload.get("id") != tmdb_id:
                return []
            self._title_cache[key] = payload_titles(payload)
        return self._title_cache[key]

    def _reusable_recognition(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        """Run the portable recognition chain as a conservative TMDB fallback.

        The normal matcher still performs resource/cache evidence checks. This
        adapter makes the reusable parser and mechanism available for names the
        legacy context builder cannot resolve, without weakening those checks.
        """
        source = next((s for s in item.get("sources") or [] if s.get("filename")), None)
        filename = (source or {}).get("filename") or item.get("title") or ""
        main_dir = item.get("title") or ""
        # Treat the library title as the stable directory context only when it
        # is not a generic placeholder; the filename remains the primary input.
        parsed = parse_recognition(filename, main_dir_name=main_dir)

        client = self
        class Host:
            def search(self, title, media_type, year=None, season=None, episode=None):
                path = f"/search/{media_type}"
                params = {"query": title, "language": "zh-CN"}
                if year:
                    params["year" if media_type == "movie" else "first_air_date_year"] = year
                payload = client.request(path, params)
                rows = []
                for row in payload.get("results", []):
                    kind = row.get("media_type") or media_type
                    if kind != media_type or not row.get("id"):
                        continue
                    rows.append(RecognitionCandidate(
                        provider="tmdb", external_id=str(row["id"]),
                        title=row.get("title") or row.get("name") or "未命名",
                        media_type=kind, year=tmdb_year(row),
                        original_title=row.get("original_title") or row.get("original_name"),
                    ))
                return rows

            def fetch_by_id(self, tmdb_id, media_type):
                payload = client.request(f"/{media_type}/{int(tmdb_id)}", {"language": "zh-CN"})
                if payload.get("id") != int(tmdb_id):
                    return None
                return RecognitionCandidate(
                    provider="tmdb", external_id=str(payload["id"]),
                    title=payload.get("title") or payload.get("name") or "未命名",
                    media_type=media_type, year=tmdb_year(payload),
                    original_title=payload.get("original_title") or payload.get("original_name"),
                )

        result = ReusableRecognizer(
            Host(),
            options={"ai_recognition": False, "moviepilot_auxiliary": False},
            thresholds={"auto_threshold": AUTO_LINK_SCORE, "ambiguity_margin": AUTO_LINK_MARGIN},
        ).identify(filename, main_dir_name=main_dir)
        if not result.selected:
            return []
        selected = result.selected
        payload = self.request(f"/{selected.media_type}/{int(selected.external_id)}", {"language": "zh-CN"})
        if not isinstance(payload, dict) or payload.get("id") is None:
            return []
        candidate = self._candidate(
            item, payload, selected.media_type,
            local_titles=[parsed.title, *[title for title, _ in parsed.query_variants]],
            local_years=[parsed.year] if parsed.year else [],
            search_hits=[{"query": parsed.title, "rank": 0, "year": parsed.year, "derived": False}],
            explicit_match="explicit_tmdb" in parsed.evidence,
            episodic=parsed.media_type == "tv",
        )
        candidate["match_evidence"]["reusable_recognition"] = True
        candidate["match_evidence"]["recognition_evidence"] = list(
            (selected.metadata.get("recognition") or {}).get("evidence") or []
        )
        return [candidate]

    def search(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        context = collect_search_context(item)
        if not context.terms:
            return []
        local_type = item.get("media_type", "unknown")
        allowed_years = matching_years(item.get("year"), context.years)
        entries: dict[tuple[str, int], dict[str, Any]] = {}
        searched: set[tuple] = set()
        verification_incomplete = False

        def merge_result(
            result: dict[str, Any],
            result_type: str,
            hit: dict[str, Any] | None = None,
            explicit_match: bool = False,
        ) -> None:
            if result_type not in {"movie", "tv"} or not result.get("id"):
                return
            year = tmdb_year(result)
            if year is None or (allowed_years and year not in allowed_years):
                return
            key = (result_type, int(result["id"]))
            entry = entries.setdefault(
                key,
                {
                    "result": result,
                    "media_type": result_type,
                    "hits": [],
                    "explicit_match": False,
                },
            )
            if hit and hit not in entry["hits"]:
                entry["hits"].append(hit)
            entry["explicit_match"] = entry["explicit_match"] or explicit_match

        def run_search(
            search_type: str,
            term: SearchTerm,
            use_year: bool = False,
            include_adult: bool = False,
            derived: bool = False,
        ) -> None:
            path = f"/search/{search_type}"
            params: dict[str, Any] = {
                "query": term.title,
                "language": "zh-CN",
                "include_adult": "true" if include_adult else "false",
            }
            query_year = term.year if use_year else None
            if query_year and search_type == "movie":
                params["year"] = query_year
            elif query_year and search_type == "tv":
                params["first_air_date_year"] = query_year
            key = (path, term.title, query_year, include_adult)
            if key in searched:
                return
            searched.add(key)
            payload = self.request(path, params)
            for rank, result in enumerate(payload.get("results", [])):
                result_type = result.get("media_type") or search_type
                merge_result(
                    result,
                    result_type,
                    {
                        "query": term.title,
                        "rank": rank,
                        "year": query_year,
                        "year_filtered": bool(query_year and search_type in {"movie", "tv"}),
                        "include_adult": include_adult,
                        "derived": derived,
                    },
                )

        def ranked() -> list[dict[str, Any]]:
            candidates = [
                self._candidate(
                    item,
                    entry["result"],
                    entry["media_type"],
                    local_titles=context.titles,
                    local_years=context.years,
                    search_hits=entry["hits"],
                    explicit_match=entry["explicit_match"],
                    episodic=context.episodic,
                    resource_context=context,
                )
                for entry in entries.values()
            ]
            unresolved_names = any(
                (c["match_evidence"]["name_conflict"] and "_verified_titles" not in c["payload"])
                or c["match_evidence"]["resource_name_unverified"]
                for c in candidates
                if not c["match_evidence"]["resource_type_conflict"]
            )
            for candidate in candidates:
                candidate["match_evidence"]["verification_incomplete"] = verification_incomplete or unresolved_names
            candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
            return candidates

        for tmdb_id in context.explicit_tmdb_ids:
            type_order = [local_type, "movie", "tv"] if local_type in {"movie", "tv"} else ["movie", "tv"]
            for candidate_type in dict.fromkeys(type_order):
                try:
                    payload = self.request(f"/{candidate_type}/{tmdb_id}", {"language": "zh-CN"})
                except TmdbError as exc:
                    if exc.status_code == 404:
                        continue
                    raise
                merge_result(payload, candidate_type, explicit_match=True)

        primary = context.terms[0]
        if local_type in {"movie", "tv"}:
            run_search(local_type, primary, use_year=bool(primary.year))
            run_search("multi", primary)
            if primary.year and not should_auto_link(ranked()):
                run_search(local_type, primary)
        elif primary.year:
            run_search("movie", primary, use_year=True)
            run_search("tv", primary, use_year=True)
            if not should_auto_link(ranked()):
                run_search("multi", primary)
        else:
            run_search("multi", primary)

        for term in context.terms[1:MAX_SEARCH_TERMS]:
            if local_type in {"movie", "tv"}:
                run_search(local_type, term, use_year=bool(term.year))
            run_search("multi", term)

        if not should_auto_link(ranked()):
            for term in context.terms[:2]:
                split = split_joined_title(term.title)
                if split:
                    alternative = SearchTerm(split, term.year)
                    if local_type in {"movie", "tv"}:
                        run_search(local_type, alternative, use_year=bool(term.year), derived=True)
                    run_search("multi", alternative, derived=True)

        def enrich_titles() -> None:
            nonlocal verification_incomplete
            # Retrieve translations before ranking, including low-scoring foreign-language hits.
            prioritized = sorted(
                entries.values(),
                key=lambda entry: min((h["rank"] for h in entry["hits"]), default=0),
            )
            local_titles = {normalize_title(t) for t in context.titles}
            lookups = 0
            for entry in prioritized:
                result = entry["result"]
                if "_verified_titles" in result:
                    continue
                known = [result.get(k) for k in ("title", "name", "original_title", "original_name")]
                base = self._candidate(
                    item, result, entry["media_type"], local_titles=context.titles,
                    local_years=context.years, search_hits=entry["hits"],
                    episodic=context.episodic,
                    resource_context=context,
                )
                if base["match_evidence"]["resource_type_conflict"]:
                    continue
                needs_resource_name = base["match_evidence"]["resource_name_unverified"]
                if any(normalize_title(t) in local_titles for t in known if t) and not base["match_evidence"]["name_conflict"] and not needs_resource_name:
                    continue
                rank = min((h["rank"] for h in entry["hits"]), default=0)
                if rank > 2 and base["score"] < 0.80 and not base["match_evidence"]["name_conflict"] and not needs_resource_name:
                    continue
                if lookups >= MAX_TITLE_LOOKUPS:
                    verification_incomplete = True
                    break
                lookups += 1
                try:
                    titles = self.translated_titles(entry["media_type"], int(result["id"]))
                except TmdbError:
                    verification_incomplete = True
                    continue
                entry["result"] = {**result, "_verified_titles": titles}

        if not should_auto_link(ranked()):
            enrich_titles()

        current = matching_candidates(ranked())
        if not current or (not should_auto_link(current) and current[0]["score"] < AUTO_LINK_SCORE):
            for term in context.terms[:MAX_SEARCH_TERMS]:
                if local_type in {"movie", "tv"}:
                    run_search(local_type, term, use_year=bool(term.year), include_adult=True)
                else:
                    run_search("multi", term, include_adult=True)
            enrich_titles()

        candidates = matching_candidates(ranked())
        if not candidates:
            # Reuse the portable recognition chain for a second, fully scored
            # path. It is intentionally last: existing resource evidence and
            # language/year safeguards remain authoritative.
            candidates = matching_candidates(self._reusable_recognition(item))
        return self.disambiguate_tv_episodes(candidates, context)[:12]

    def disambiguate_tv_episodes(
        self, candidates: list[dict[str, Any]], context: SearchContext,
    ) -> list[dict[str, Any]]:
        coverage = context.resource_episodes
        if (
            should_auto_link(candidates) or not coverage or context.resource_episode_incomplete
            or context.resource_incomplete or context.resource_conflict or context.explicit_tmdb_ids
            or len(set(context.years)) != 1
            or any(not row["contiguous"] or row["first_episode"] != 1 or row["episode_count"] < 2 for row in coverage)
        ):
            return candidates
        rivals = [c for c in candidates if (
            c["media_type"] == "tv" and c["score"] >= AUTO_LINK_SCORE
            and c["match_evidence"].get("primary_title_exact")
            and not c["match_evidence"].get("local_year_missing")
            and not any(c["match_evidence"].get(key) for key in AUTO_LINK_BLOCKERS)
        )]
        if not 2 <= len(rivals) <= MAX_TITLE_LOOKUPS or len({tmdb_year(c) for c in rivals}) != 1:
            return candidates

        for candidate in rivals:
            try:
                details = self.request(f"/tv/{candidate['tmdb_id']}", {"language": "zh-CN"})
                proof = compare_episode_coverage(candidate, details, coverage)
            except TmdbError:
                proof = {"status": "unverified", "reason": "request_failed", "resource": coverage}
            candidate["match_evidence"]["season_episode_comparison"] = proof

        exact = [c for c in rivals if c["match_evidence"]["season_episode_comparison"]["status"] == "exact"]
        # Missing, longer or equally compatible seasons are not evidence against a rival.
        if len(exact) == 1 and all(
            c is exact[0] or c["match_evidence"]["season_episode_comparison"]["status"] == "conflict"
            for c in rivals
        ):
            exact[0]["match_evidence"]["season_episode_match"] = True
            for candidate in rivals:
                if candidate is not exact[0]:
                    candidate["match_evidence"]["season_episode_conflict"] = True
                    candidate["score"] = min(candidate["score"], 0.79)
            candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates

    def imdb_fallback(self, item: dict[str, Any], client: ImdbClient | None = None) -> dict[str, Any]:
        context = collect_search_context(item)
        years = matching_years(item.get("year"), context.years)
        evidence: dict[str, Any] = {
            "status": "no_exact_match", "queries": [t.title for t in context.terms[:MAX_SEARCH_TERMS]],
            "expected_year": next(iter(years)) if len(years) == 1 else None, "matches": [], "mapped": [],
        }
        result = {"candidates": [], "evidence": evidence}
        if len(years) != 1:
            evidence["status"] = "missing_year" if not years else "conflicting_years"
            return result
        if COLLECTION_RE.search(item["title"]):
            evidence["status"] = "collection"
            return result
        year = evidence["expected_year"]
        try:
            matches = (client or ImdbClient()).exact_matches(evidence["queries"], year, episodic=context.episodic)
            # Recheck the boundary even for an alternate IMDb provider.
            names = {normalize_imdb_title(q) for q in evidence["queries"]}
            matches = [m for m in matches if IMDB_ID_RE.fullmatch(str(m.get("imdb_id", "")))
                       and m.get("year") == year and normalize_imdb_title(m.get("title", "")) in names
                       and m.get("media_type") in {"movie", "tv"}
                       and (not context.episodic or m["media_type"] == "tv")]
            matches = list({m["imdb_id"]: m for m in matches}.values())
            evidence["matches"] = matches
            if not matches:
                return result
            if len(matches) != 1:
                evidence["status"] = "ambiguous_imdb"
                return result
            match = matches[0]
            found = self.request(f"/find/{match['imdb_id']}", {"external_source": "imdb_id", "language": "zh-CN"})
            mapped = {(kind, int(row["id"])) for kind in ("movie", "tv")
                      for row in found.get(f"{kind}_results", []) if row.get("id")}
            evidence["mapped"] = [{"media_type": kind, "tmdb_id": mid} for kind, mid in sorted(mapped)]
            if not mapped:
                evidence["status"] = "no_tmdb_mapping"
                return result
            if len(mapped) != 1:
                evidence["status"] = "ambiguous_tmdb"
                return result
            kind, mid = next(iter(mapped))
            if kind != match["media_type"]:
                evidence["status"] = "type_conflict"
                return result
            payload = self.request(f"/{kind}/{mid}", {
                "language": "zh-CN", "append_to_response": "external_ids,alternative_titles,translations",
            })
            backref = payload.get("imdb_id") or (payload.get("external_ids") or {}).get("imdb_id")
            if payload.get("id") != mid or backref != match["imdb_id"]:
                evidence["status"] = "external_id_mismatch"
                return result
            actual_year = tmdb_year(payload)
            evidence["mapped"][0].update(title=payload.get("title") or payload.get("name"), year=actual_year)
            if not actual_year:
                evidence["status"] = "tmdb_missing_year"
                return result
            year_override = actual_year != year
            proof = {**match, "tmdb_id": mid, "verified": True, "tmdb_year": actual_year, "year_override": year_override}
            evidence.update(year_override=year_override, year_basis="imdb", effective_year=year)
            candidate = self._candidate(
                item, {**payload, "_verified_titles": payload_titles(payload), "_imdb_identity": proof}, kind,
                local_titles=context.titles, local_years=context.years, episodic=context.episodic,
                resource_context=context,
            )
            result["candidates"] = matching_candidates([candidate])
            evidence["status"] = ("located_imdb_year" if year_override else "located") if should_auto_link(result["candidates"]) else "review"
            return result
        except (ImdbError, TmdbError) as exc:
            evidence.update(status="error", error=str(exc))
            return result
        except (TypeError, ValueError, KeyError, AttributeError):
            evidence.update(status="error", error="IMDb 或 TMDB 返回的数据格式异常。")
            return result

    def details(self, media_type: str, tmdb_id: int) -> dict[str, Any]:
        if media_type not in {"movie", "tv"}:
            raise TmdbError("TMDB 类型必须是 movie 或 tv。")
        payload = self.request(f"/{media_type}/{int(tmdb_id)}", {"language": "zh-CN"})
        if tmdb_year(payload) is None:
            raise TmdbError("该 TMDB 条目缺少发行或首播年份，不能关联。")
        return normalize_details(payload, media_type)

    def _candidate(
        self,
        item: dict[str, Any],
        result: dict[str, Any],
        media_type: str,
        *,
        local_titles: list[str] | None = None,
        local_years: list[int] | None = None,
        search_hits: list[dict[str, Any]] | None = None,
        explicit_match: bool = False,
        episodic: bool = False,
        resource_context: SearchContext | None = None,
    ) -> dict[str, Any]:
        title = result.get("title") or result.get("name") or "未命名"
        original_title = result.get("original_title") or result.get("original_name")
        release_date = result.get("release_date") or result.get("first_air_date") or ""
        result_year = tmdb_year(result)
        known_years = matching_years(item.get("year"), local_years)
        imdb_identity = verified_imdb_identity(
            result, media_type, known_years, [item["title"], *(local_titles or [])],
        )
        identity_titles = [imdb_identity["title"]] if imdb_identity else []
        effective_year = imdb_identity["year"] if imdb_identity else result_year
        verified_titles = [*(result.get("_verified_titles") or []), *identity_titles]
        normalized_primary = normalize_title(item["title"])
        normalized_candidates = {
            normalize_title(value)
            for value in (title, original_title, *verified_titles)
            if value
        }
        # A bilingual release must not match just the Chinese name of a different film.
        latin_aliases = {
            normalize_title(t) for t in local_titles or []
            if t.isascii() and re.search(r"[A-Za-z]", t)
        }
        named_in_latin = any(
            t and t.isascii() and re.search(r"[A-Za-z]", t)
            for t in [original_title, *verified_titles]
        )
        name_conflict = bool(
            re.search(r"[\u3400-\u9fff]", item["title"]) and latin_aliases and named_in_latin
            and not latin_aliases.intersection(normalized_candidates)
        )
        score = score_candidate(
            local_title=item["title"],
            local_year=item.get("year"),
            local_type=item.get("media_type", "unknown"),
            candidate_title=title,
            candidate_original_title=original_title,
            candidate_year=effective_year,
            candidate_type=media_type,
            popularity=float(result.get("popularity") or 0),
            local_titles=local_titles,
            local_years=local_years,
            search_hits=search_hits,
            explicit_match=explicit_match,
            candidate_titles=verified_titles,
        )
        if name_conflict:
            score = 0.0
        title_exact = any(normalize_title(t) in normalized_candidates for t in [item["title"], *(local_titles or [])])
        resource_groups = resource_context.resource_groups if resource_context else []
        resource_title_exact = bool(resource_groups) and all(
            any(normalize_title(t) in normalized_candidates for t in group) for group in resource_groups
        )
        resource_name_unverified = bool(
            resource_groups and not resource_title_exact and "_verified_titles" not in result
            and (title_exact or score >= 0.80)
        )
        resource_name_conflict = bool(resource_groups and not resource_title_exact and "_verified_titles" in result)
        if resource_name_conflict:
            score = 0.0
        return {
            "tmdb_id": int(result["id"]),
            "media_type": media_type,
            "title": title,
            "original_title": original_title,
            "release_date": release_date or None,
            "poster_path": result.get("poster_path"),
            "overview": result.get("overview"),
            "score": score,
            "payload": result,
            "match_evidence": {
                "explicit_tmdb_id": explicit_match,
                "primary_title_exact": normalized_primary in normalized_candidates,
                "title_exact": title_exact,
                "derived_only": bool(search_hits) and all(hit.get("derived") for hit in search_hits),
                "collection": bool(COLLECTION_RE.search(item["title"])),
                "name_conflict": name_conflict,
                "type_conflict": episodic and media_type != "tv",
                "year_conflict": bool(known_years and effective_year and effective_year not in known_years),
                "year_unverified": bool(known_years) and not result_year,
                "tmdb_year": result_year,
                "effective_year": effective_year,
                "imdb_year_override": bool(imdb_identity and effective_year != result_year),
                "local_year_missing": not item.get("year") and not (local_years or []),
                "verified_titles": result.get("_verified_titles") or [],
                "imdb_identity": imdb_identity if identity_titles else None,
                "resource_kind": resource_context.resource_kind if resource_context else "unknown",
                "resource_titles": resource_groups,
                "resource_title_exact": resource_title_exact,
                "resource_type_conflict": bool(resource_context and resource_context.resource_kind == "tv" and media_type != "tv"),
                "resource_name_unverified": resource_name_unverified,
                "resource_name_conflict": resource_name_conflict,
                "resource_conflict": bool(resource_context and resource_context.resource_conflict),
                "resource_incomplete": bool(resource_context and resource_context.resource_incomplete),
                "search_hits": search_hits or [],
            },
        }


def score_candidate(
    *,
    local_title: str,
    local_year: int | None,
    local_type: str,
    candidate_title: str,
    candidate_original_title: str | None,
    candidate_year: int | None,
    candidate_type: str,
    popularity: float = 0,
    local_titles: list[str] | None = None,
    local_years: list[int] | None = None,
    search_hits: list[dict[str, Any]] | None = None,
    explicit_match: bool = False,
    candidate_titles: list[str] | None = None,
) -> float:
    year_options = matching_years(local_year, local_years)
    if candidate_year is None or (year_options and candidate_year not in year_options):
        return 0.0
    primary_title = normalize_title(local_title)
    local_options = [primary_title]
    local_options.extend(normalize_title(title) for title in (local_titles or []))
    local_options = list(dict.fromkeys(option for option in local_options if option))
    title_options = [normalize_title(candidate_title)]
    if candidate_original_title:
        title_options.append(normalize_title(candidate_original_title))
    title_options.extend(normalize_title(t) for t in candidate_titles or [])
    title_options = list(dict.fromkeys(option for option in title_options if option))
    similarities = [
        SequenceMatcher(None, local, option).ratio()
        for local in local_options
        for option in title_options
    ]
    similarity = max(similarities, default=0.0)

    title_score = similarity * 0.78
    if similarity >= 0.82:
        title_score += 0.04
    if primary_title and primary_title in title_options:
        title_score = max(title_score, 0.88)
    elif any(local in title_options for local in local_options):
        title_score = max(title_score, 0.80)

    if explicit_match:
        if similarity >= 0.55:
            title_score = max(title_score, 0.86)
        elif similarity >= 0.25:
            title_score = max(title_score, 0.74)

    score = title_score

    if year_options:
        score += 0.14

    if local_type in {"movie", "tv"}:
        score += 0.04 if local_type == candidate_type else -0.08

    if explicit_match:
        score += 0.04

    score += min(math.log1p(max(popularity, 0)) / 100, 0.025)
    return round(max(0.0, min(score, 0.99)), 4)


def should_auto_link(candidates: list[dict[str, Any]]) -> bool:
    candidates = matching_candidates(candidates)
    if not candidates:
        return False
    top_candidate = candidates[0]
    evidence = top_candidate.get("match_evidence") or {}
    if any(evidence.get(key) for key in AUTO_LINK_BLOCKERS):
        return False
    if evidence.get("derived_only") and not evidence.get("title_exact"):
        return False
    top = top_candidate["score"]
    runner_up = candidates[1]["score"] if len(candidates) > 1 else 0.0
    gap = round(top - runner_up, 4)
    if top >= AUTO_LINK_SCORE and gap >= AUTO_LINK_MARGIN:
        return True
    evidence = top_candidate.get("match_evidence") or {}
    return bool(
        top >= EXACT_TITLE_SCORE
        and gap >= EXACT_TITLE_MARGIN
        and evidence.get("primary_title_exact")
        and evidence.get("local_year_missing")
    )


def normalize_details(payload: dict[str, Any], media_type: str) -> dict[str, Any]:
    title = payload.get("title") or payload.get("name") or "未命名"
    original_title = payload.get("original_title") or payload.get("original_name")
    release_date = payload.get("release_date") or payload.get("first_air_date")
    origin_country = payload.get("origin_country") or []
    if not origin_country:
        origin_country = [
            country.get("iso_3166_1")
            for country in payload.get("production_countries", [])
            if country.get("iso_3166_1")
        ]
    return {
        "tmdb_id": int(payload["id"]),
        "media_type": media_type,
        "title": title,
        "original_title": original_title,
        "overview": payload.get("overview"),
        "poster_path": payload.get("poster_path"),
        "backdrop_path": payload.get("backdrop_path"),
        "release_date": release_date,
        "vote_average": payload.get("vote_average"),
        "genres": payload.get("genres") or [],
        "origin_country": origin_country,
        "payload": payload,
    }


def search_media(
    media_id: int,
    *,
    auto_link: bool = False,
    refresh_share: bool = False,
    db_path: str | Path | None = None,
    client: TmdbClient | None = None,
    imdb_client: ImdbClient | None = None,
) -> dict[str, Any]:
    tmdb = client or TmdbClient()
    cleanup = cleanup_cancelled_shares(db_path, media_id=media_id)
    item = get_media(media_id, db_path)
    if not item:
        if cleanup["media_removed"]:
            return {"status": "removed", "linked": False, "media_id": media_id, "candidates": [], "removed_sources": cleanup["removed"]}
        raise TmdbError(f"媒体记录 {media_id} 不存在。")
    if item.get("tmdb_status") == "matched" and item.get("tmdb_id"):
        if refresh_share:
            refreshed = enrich_media_share_sources(media_id, db_path=db_path, force=True)
            cleanup["removed"] += refreshed["removed"]
        return {"status": "matched", "linked": True, "media_id": media_id, "candidates": item["candidates"], "removed_sources": cleanup["removed"]}
    refreshed = enrich_media_share_sources(media_id, db_path=db_path, force=refresh_share)
    removed_sources = cleanup["removed"] + int(refreshed.get("removed", 0))
    item = get_media(media_id, db_path)
    if not item:
        if refreshed.get("media_removed"):
            return {"status": "removed", "linked": False, "media_id": media_id, "candidates": [], "removed_sources": removed_sources}
        raise TmdbError(f"媒体记录 {media_id} 不存在。")

    try:
        candidates = matching_candidates(tmdb.search(item))
    except TmdbError as exc:
        save_candidates(media_id, [], "error", str(exc), db_path)
        raise

    imdb_evidence = None
    if not candidates:
        fallback = tmdb.imdb_fallback(item, imdb_client)
        candidates = fallback["candidates"]
        imdb_evidence = fallback["evidence"]
    if not candidates:
        save_candidates(media_id, [], "not_found", None, db_path)
        save_imdb_lookup(media_id, imdb_evidence, db_path)
        return {"status": "not_found", "linked": False, "candidates": [], "removed_sources": removed_sources}

    top = candidates[0]
    should_link = auto_link and should_auto_link(candidates)

    if should_link:
        details = (normalize_details(top["payload"], top["media_type"])
                   if top.get("match_evidence", {}).get("imdb_identity")
                   else tmdb.details(top["media_type"], top["tmdb_id"]))
        imdb_identity = top.get("match_evidence", {}).get("imdb_identity")
        linked_id = link_tmdb(
            media_id, details, top["score"], "auto", db_path,
            canonical_year=imdb_identity["year"] if imdb_identity else None,
        )
        save_imdb_lookup(linked_id, imdb_evidence, db_path)
        return {
            "status": "matched",
            "linked": True,
            "media_id": linked_id,
            "candidate": top,
            "removed_sources": removed_sources,
        }

    save_candidates(media_id, candidates, "review", None, db_path)
    save_imdb_lookup(media_id, imdb_evidence, db_path)
    return {"status": "review", "linked": False, "candidates": candidates, "removed_sources": removed_sources}


def bulk_match(
    limit: int,
    *,
    db_path: str | Path | None = None,
    client: TmdbClient | None = None,
) -> dict[str, Any]:
    tmdb = client or TmdbClient()
    if not tmdb.configured:
        raise TmdbError("TMDB 未配置。请先设置本机 API 凭据。")

    results = {"processed": 0, "matched": 0, "review": 0, "not_found": 0, "errors": 0, "removed": 0, "removed_sources": 0}
    for media_id in pending_media_ids(limit, db_path):
        try:
            outcome = search_media(media_id, auto_link=True, db_path=db_path, client=tmdb)
            results["processed"] += 1
            results["removed_sources"] += outcome.get("removed_sources", 0)
            status = outcome["status"]
            if status in results:
                results[status] += 1
        except Exception:
            results["processed"] += 1
            results["errors"] += 1
        time.sleep(0.04)
    return results
