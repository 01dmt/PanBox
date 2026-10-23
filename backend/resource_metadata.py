"""Pure source presentation helpers, shared by schema and resource queries."""
import json
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit, urlencode

RESOURCE_CLASSES = {'media': '影视', 'collection': '资源包', 'music': '音乐', 'person': '人物', 'other': '其他'}
AVAILABILITY = {'available': '可用', 'pending': '待检查', 'invalid': '失效', 'unknown': '未知'}
PERSON_NAME_STRIP = ' \t-–—|·()（）[]【】✨，,。、；;：:…！？!?'


def clean_person_name(value):
    """稳定人物名：取标题行、去图标与尾部标点，用于入库聚合与人物分组。

    频道消息常见「👤 浅野心，🎂 生日：…」这类紧凑写法，姓名后紧跟的
    中文逗号等标点必须去掉，否则「浅野心，」和「浅野心」会被拆成两个人物。
    """
    text = re.sub(r'^\s*[📺🎥🎬👤🗂🍿🎞️]+\s*', '', str(value or '').lstrip('\ufeff'))
    first = text.splitlines()[0].strip() if text.strip() else ''
    return first.strip(PERSON_NAME_STRIP)


def json_object(value):
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def source_resource_kind(metadata):
    """Return the explicit resource kind recorded for one source.

    Only the declared parser field or the marker on the first context line is
    authoritative.  Later metadata lines may contain icons such as ``🗂`` in
    labels (for example ``🗂 分类：剧集/欧美剧集``), which must not turn a normal
    movie or TV episode into a collection.
    """
    data = metadata if isinstance(metadata, dict) else json_object(metadata)
    declared = data.get("resource_kind")
    if isinstance(declared, str) and declared in {"media", "person", "series"}:
        return declared
    context = data.get("context")
    first_line = str(context or "").splitlines()[0].strip("\ufeff \t") if context else ""
    marker = re.match(r"^([📺🎥🎬👤🗂])", first_line)
    if marker and marker.group(1) == "👤":
        return "person"
    if marker and marker.group(1) == "🗂" and not re.match(r"^🗂\ufe0f?\s*分类\s*[:：]", first_line):
        return "series"
    if marker and marker.group(1) in {"📺", "🎥", "🎬"}:
        return "media"
    return None


def infer_class(kind, title, media_type='unknown'):
    if kind == 'person':
        return 'person'
    if kind == 'series':
        return 'collection'
    if re.search(r'音乐|专辑|歌曲|演唱会|\b(?:music|album|flac|mp3)\b', title or '', re.I):
        return 'music'
    if re.search(r'合集|合辑|资源包|电影集|collection', title or '', re.I):
        return 'collection'
    return 'media' if media_type in {'movie', 'tv'} else 'other'


def access_code(url):
    try:
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        return next((query[k][0] for k in ('password', 'pwd', 'code', 'receive_code', 'access_code') if query.get(k)), '')
    except ValueError:
        return ''


def masked_url(url):
    try:
        p = urlsplit(url)
        query = parse_qs(p.query, keep_blank_values=True)
        for key in query:
            if key.lower() in {'password', 'pwd', 'code', 'receive_code', 'access_code', 'token', 'access_token'}:
                query[key] = ['****']
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query, doseq=True, safe='*'), p.fragment))
    except ValueError:
        return ''


def redact(value):
    text = str(value or '')
    text = re.sub(r'(?i)((?:password|pwd|receive_code|access_code|access_token|token|code)=)[^\s&#]+', r'\1****', text)
    return re.sub(r'(?i)((?:访问密码|访问码|提取码|密码)\s*[:：=]?\s*)[a-z0-9]{2,32}', r'\1****', text)


def snapshot_state(metadata):
    snapshot = json_object(metadata).get('share_snapshot') or {}
    if not isinstance(snapshot, dict):
        return 'unknown', None
    if snapshot.get('status') == 'ok':
        return 'available', snapshot.get('fetched_at')
    if str(snapshot.get('errno')) in {'4100009', '4100010'}:
        return 'invalid', snapshot.get('fetched_at')
    if snapshot.get('status') in {'error', 'unavailable'}:
        return 'pending', snapshot.get('fetched_at')
    return 'unknown', None
