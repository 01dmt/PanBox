"""Pure source presentation helpers, shared by schema and resource queries."""
import json
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit, urlencode

from .collection_markers import COLLECTION_RE

RESOURCE_CLASSES = {'media': '影视', 'collection': '合集', 'music': '音乐', 'person': '人物', 'nsfw': '成人/NSFW', 'other': '其他'}
AVAILABILITY = {'available': '可用', 'pending': '待检查', 'invalid': '失效', 'unknown': '未知'}
PERSON_NAME_STRIP = ' \t-–—|·()（）[]【】✨，,。、；;：:…！？!?'
AUDIO_EXTENSIONS = {
    'mp3', 'flac', 'wav', 'ape', 'aac', 'm4a', 'dsf', 'dff', 'wv', 'alac', 'ogg',
    'opus', 'aiff', 'aif', 'dts', 'tta', 'wma', 'mka', 'mid', 'midi',
}
# 只认足够专指的音乐词。FLAC/DSD/Hi-Res 这类格式词会撞上电影发布名
# （「…BluRay.1080p.x264.FLAC」其实是无损音轨的电影），Live 会撞上
# 「Live and Let Die」，都不能当音乐判断依据。
MUSIC_TITLE = re.compile(r'音乐|专辑|歌曲|演唱会|音乐会|交响|无损|原声大碟|单曲|(?<![A-Za-z])OST(?![A-Za-z])', re.IGNORECASE)
# 格式词只在「不是视频文件」时才作数：115 分享没有文件名，「齐秦 转flac分轨」这类
# 只能靠标题里的 flac 认出来；但电影发布名里的 .FLAC 是无损音轨，不能当音乐。
AV_CODE_RE = re.compile(
    r'^\s*(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s*)?(?:\[[^\]]*\]\s*)?([A-Z]{2,6}-\d{3,5})(?![A-Z0-9])',
    re.IGNORECASE,
)


def av_code(value):
    """Return the normalized JAV/AV code in a title/filename, if any."""
    match = AV_CODE_RE.search(str(value or ''))
    if not match:
        return None
    code = match.group(1).upper()
    return None if code.startswith('TMDBID-') else code


MUSIC_FORMAT = re.compile(
    r'(?<![a-z0-9])(?:flac|ape|wav|dsd|dsf|mp3|m4a|hi-?res|alac)(?![a-z0-9])', re.IGNORECASE
)


VIDEO_EXTENSIONS = {
    'mkv', 'mp4', 'ts', 'm2ts', 'avi', 'mov', 'rmvb', 'wmv', 'm4v', 'mpg', 'mpeg',
    'vob', 'iso', 'flv', 'webm', 'mk3d', 'rm', '3gp',
}


def looks_like_video(filename):
    name = str(filename or '').strip()
    return '.' in name and name.rsplit('.', 1)[-1].lower() in VIDEO_EXTENSIONS


def looks_like_audio(filename):
    """按扩展名判断是不是音频文件（ed2k 里 26% 的附属文件靠这个兜住）。"""
    name = str(filename or '').strip()
    if '.' not in name:
        return False
    extension = name.rsplit('.', 1)[-1].lower()
    return extension in AUDIO_EXTENSIONS


IMAGE_EXTENSIONS = {
    'jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'tif', 'tiff', 'avif', 'heic',
}
# 只有刮削图/NFO 才可能是「纯元数据包」；字幕、歌词是正常资源，不能因
# 为没有视频就当成元数据（很多片子正需要外挂中文字幕）。
AUXILIARY_EXTENSIONS = IMAGE_EXTENSIONS | {'nfo'}
# 只认媒体库生成的固定刮削名，不把 001.jpg / IMG_0001.jpg 这类连续图片当附属图。
ARTWORK_IMAGE_RE = re.compile(
    r'^(?:(?:season|s)\d{1,2}[-_.])?'
    r'(?:poster|fanart|folder|backdrop|banner|clearlogo|clearart|discart|disc|'
    r'landscape|thumb|logo|cover|show|movie|tvshow)(?:[-_.]?\d+)?$',
    re.IGNORECASE,
)
EPISODE_THUMB_RE = re.compile(r'^s\d{1,2}e\d{1,3}[-_.]thumb$', re.IGNORECASE)
IMAGE_CONTENT_TITLE = re.compile(
    r'写真|套图|漫画|画集|画册|壁纸|CG|扫描|摄影|图集|素材|图片|相册|原画|插画',
    re.IGNORECASE,
)


def _filename_stem(name):
    value = str(name or '')
    return value.rsplit('.', 1)[0].strip().lower() if '.' in value else value.strip().lower()


def looks_like_artwork(filename):
    """Whether an image name is a known media-library artwork name."""
    stem = _filename_stem(filename)
    return bool(ARTWORK_IMAGE_RE.fullmatch(stem) or EPISODE_THUMB_RE.fullmatch(stem))


def classify_metadata_pack(title, filenames):
    """Return ``(is_metadata, reason)`` for one media item's source files.

    Conservative by design: it only flags packs that have **no playable
    content** and whose files are all artwork/NFO. A pack with a video or a
    real audio track is never metadata, a pack with missing filenames (115
    share links) is left alone, and sequential image names such as ``001.jpg``
    / ``IMG_1001.jpg`` are treated as image content rather than artwork.
    Subtitle/lyric files are deliberately *not* auxiliary: a standalone .srt or
    .lrc is a normal resource (external Chinese subtitles, lyrics), so it stays
    in the pending queue. ``theme.mp3`` is only auxiliary when both artwork and
    NFO are present.
    """
    names = [str(name or '').strip() for name in filenames]
    if not names or any(not name for name in names):
        return False, None
    extensions = [
        name.rsplit('.', 1)[-1].lower() if '.' in name else ''
        for name in names
    ]
    if any(extension in VIDEO_EXTENSIONS for extension in extensions):
        return False, None

    has_artwork = any(extension in IMAGE_EXTENSIONS for extension in extensions)
    has_nfo = any(extension == 'nfo' for extension in extensions)

    def is_theme_audio(name, extension):
        # A bare theme.mp3 (or one with only cover art) may be a real music
        # single; require TV metadata (NFO) before treating it as auxiliary.
        return (
            extension == 'mp3'
            and has_artwork
            and has_nfo
            and _filename_stem(name) == 'theme'
        )

    if any(
        extension in AUDIO_EXTENSIONS and not is_theme_audio(name, extension)
        for name, extension in zip(names, extensions)
    ):
        return False, None
    if not all(
        extension in AUXILIARY_EXTENSIONS or is_theme_audio(name, extension)
        for name, extension in zip(names, extensions)
    ):
        return False, None
    if IMAGE_CONTENT_TITLE.search(str(title or '')):
        return False, None

    images = [
        name for name, extension in zip(names, extensions)
        if extension in IMAGE_EXTENSIONS
    ]
    if images and not all(looks_like_artwork(name) for name in images):
        return False, None

    if images:
        reason = 'artwork'
    elif any(extension == 'nfo' for extension in extensions):
        reason = 'nfo'
    else:
        reason = 'auxiliary'
    return True, reason


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


def infer_class(kind, title, media_type='unknown', filename=None):
    if kind == 'person':
        return 'person'
    if kind == 'series':
        return 'collection'
    if av_code(title) or av_code(filename):
        return 'nsfw'
    if looks_like_audio(filename):
        return 'music'
    if MUSIC_TITLE.search(title or ''):
        return 'music'
    if not looks_like_video(filename) and MUSIC_FORMAT.search(title or ''):
        return 'music'
    if COLLECTION_RE.search(title or '') or re.search(r'资源包|电影集', title or '', re.I):
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
