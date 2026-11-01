"""Shared recognition of Chinese/English collection markers.

The same channel can express a collection as ``合集``, ``合辑``, ``套装``,
``全系列``, ``三部曲`` or ``(系列)``.  A plain substring match is dangerous:
``诛仙合集篇`` and ``性梦爱三部曲性`` are real titles, not collections.  Keep
the marker patterns here so the parser, TMDB guard and resource classifier
agree on the same boundaries.
"""
from __future__ import annotations

import re

# Trailing markers are only recognized at a title boundary: end of string,
# whitespace, a bracket, a digit range, or a known release/quality word.
_BOUNDARY = (
    r"(?=$|[\s)】）\]]|[（(【\[]|[A-Za-z]|\d|版|[：:、,，.。;；/|+·•\-–—]|"
    r"4K|1080|2160|720|蓝光|原盘|高清|高码|已刮削|国语|粤语|中字|双语|HDR|HEVC|收藏|国|港|台|剧|含|外|完|电影)"
)
_BRACKET = (
    r"[（(【\[]\s*(?:系列|合集|合辑|合輯|套装|套裝|套盒|合集包|全系列|系列电影|系列電影|系列合集|"
    r"collection|box[ .-]?set)\s*[）)】\]]?"
)
# ``全系列``, ``合集包`` etc. are unambiguous even without a bracket.  ``套装``
# is also a real film title (The Outfit), so it only counts after a title
# prefix (``漫威套装``), never as the whole title.
_SIMPLE = r"全系列|系列电影|系列電影|系列合集|合集电影|合集包|等等(?:电影|影片|资源)"
_PREFIXED = r"(?<=[\u4e00-\u9fff])\s*(?:套装|套裝|套盒|合辑|合輯)"
_HEDGED = (
    rf"合集{_BOUNDARY}"
    rf"|全[一二三四五六七八九十百两\d]+部{_BOUNDARY}"
    rf"|(?:(?:前|后)?[一二三四五六七八九十百两\d]+)?部曲{_BOUNDARY}"
    rf"|系列{_BOUNDARY}"
)
_NUM = r"[一二三四五六七八九十百两\d]+"
_RANGE = rf"{_NUM}\s*[-~—至]\s*{_NUM}部{_BOUNDARY}"
# ``N部`` is a collection when it is a count (``5部``/``21部国语``), but not
# when it is part of a real title: 天龙八部 / 第一部 / 一部塞尔维亚电影 / 九部的检察官.
_PART = (
    r"(?<!\d)(?<!第)(?<!天龙)(?<![一二三四五六七八九十百两])"
    rf"(?:[二三四五六七八九十百两]|\d{{1,3}})部{_BOUNDARY}"
)
# ``2015三部``/``2005-2007-2015三部``: a year immediately followed by a count
# is still a collection, while a plain ``2015部`` is not.
_YEAR_PART = rf"(?<=\d{{4}})[二三四五六七八九十两]部{_BOUNDARY}"
# English ``collection`` is deliberately bracket-only: ``The Collection`` and
# ``Threesome Anthology`` are real titles, so a bare English suffix is unsafe.
_ENGLISH = r"\b(?:box[ .-]?set)\b"
COLLECTION_RE = re.compile(
    rf"(?i)(?:{_BRACKET}|{_SIMPLE}|{_PREFIXED}|{_HEDGED}|{_RANGE}|{_YEAR_PART}|{_PART}|{_ENGLISH})"
)

# ``全集`` means a complete season, not a film collection; it is only stripped
# from a display title, never used as a collection signal.
STRIP_COLLECTION_RE = re.compile(
    r"\s*[（(【\[]\s*(?:系列|合集|合辑|合輯|套装|套裝|套盒|合集包|全系列|系列电影|系列電影|系列合集|全集)"
    r"\s*[）)】\]]?\s*$"
)


def has_collection_marker(value: object) -> bool:
    return bool(isinstance(value, str) and COLLECTION_RE.search(value))


def strip_collection_marker(value: str) -> str:
    return STRIP_COLLECTION_RE.sub("", value).strip()
