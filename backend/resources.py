"""Source-level resource queries and local management. No remote I/O during reads."""
from datetime import datetime, timedelta, timezone
import copy
import json
import re
import threading
from urllib.parse import urlsplit

from .importers import normalize_title
from .schema import connect, resolve_db_path
from .repository import utc_now, QUALITY_GROUPS
from .resource_metadata import av_code, RESOURCE_CLASSES, AVAILABILITY, json_object, masked_url, access_code, redact
from .resource_providers import provider_info, probe

TMDB_STATUSES = {'matched': '已匹配', 'review': '待确认', 'not_found': '未找到', 'unrecognized': '未识别', 'error': '匹配错误'}
RESOURCE_GROUPS = {'all', 'media', 'person', 'series', 'music', 'nsfw', 'other', 'unrecognized'}
_LOCK = threading.Lock()
_CHECKS = set()
BASE_FIELDS = """s.*, m.title AS media_title,m.normalized_title AS media_normalized_title,m.tmdb_id,m.tmdb_media_type,m.tmdb_title,m.poster_path,m.resource_kind,
    m.series_id,ms.title AS series_title,
    CASE WHEN m.tmdb_status='matched' AND m.tmdb_id IS NOT NULL THEN 'matched'
         WHEN m.tmdb_status IN ('review','not_found','error') THEN m.tmdb_status ELSE 'unrecognized' END AS recognition_status,
    COALESCE(s.parsed_year,m.year,CAST(NULLIF(substr(m.release_date,1,4),'') AS INTEGER)) AS year,
    COALESCE(s.last_checked_at,s.created_at) AS updated_at,
    CASE WHEN s.source_type='ed2k' THEN 'ed2k' ELSE s.provider END AS provider_id"""
BASE_FROM = """FROM source_records s JOIN media_items m ON m.id=s.media_id
    LEFT JOIN media_series ms ON ms.id=m.series_id"""
BASE = f"""SELECT {BASE_FIELDS},
    (SELECT e.channel_name FROM ingestion_links l JOIN ingestion_events e ON e.id=l.ingestion_id
     WHERE l.source_key=s.source_key ORDER BY e.received_at DESC LIMIT 1) AS channel
    {BASE_FROM}"""
# 统计与全量筛选不需要 channel；去掉逐行相关子查询后 COUNT 会明显变快。
BASE_NO_CHANNEL = f"""SELECT {BASE_FIELDS}
    {BASE_FROM}"""
RESOURCE_GROUP_SUFFIX = '''), resources AS (
    SELECT *, CASE
        WHEN resource_class='person' THEN 'person'
        WHEN resource_class='collection' THEN 'series'
        WHEN resource_class='music' THEN 'music'
        WHEN resource_class='nsfw' THEN 'nsfw'
        WHEN resource_class='other' THEN 'other'
        WHEN recognition_status='matched' THEN 'media'
        ELSE 'unrecognized'
    END AS resource_group FROM resource_sources
) '''
CTE = 'WITH resource_sources AS (' + BASE + RESOURCE_GROUP_SUFFIX
COUNT_CTE = 'WITH resource_sources AS (' + BASE_NO_CHANNEL + RESOURCE_GROUP_SUFFIX
# 分组列表只需要分组头和计数，不读取 s.*（尤其是不读取 1GB 级的 raw_text）。
GROUP_FIELDS = """s.id,s.media_id,s.source_key,s.raw_label,s.filename,s.file_size,s.tags_json,s.resource_class,
    s.availability_status,s.created_at,s.last_checked_at,s.source_type,s.provider,
    m.title AS media_title,m.normalized_title AS media_normalized_title,m.series_id,ms.title AS series_title,
    m.tmdb_id,m.tmdb_media_type,m.tmdb_title,m.poster_path,m.resource_kind,
    CASE WHEN m.tmdb_status='matched' AND m.tmdb_id IS NOT NULL THEN 'matched'
         WHEN m.tmdb_status IN ('review','not_found','error') THEN m.tmdb_status ELSE 'unrecognized' END AS recognition_status,
    COALESCE(s.parsed_year,m.year,CAST(NULLIF(substr(m.release_date,1,4),'') AS INTEGER)) AS year,
    COALESCE(s.last_checked_at,s.created_at) AS updated_at,
    CASE WHEN s.source_type='ed2k' THEN 'ed2k' ELSE s.provider END AS provider_id"""
GROUP_CTE = 'WITH resource_sources AS (SELECT ' + GROUP_FIELDS + ' ' + BASE_FROM + RESOURCE_GROUP_SUFFIX
_MISS = object()
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_LIMIT = 16


def _cache_token(path):
    """Use the main DB and WAL mtimes as a cheap cross-process cache version."""
    try:
        main = path.stat()
        parts = [main.st_mtime_ns, main.st_size]
        wal = path.with_name(path.name + '-wal')
        if wal.exists():
            wal_stat = wal.stat()
            parts.extend([wal_stat.st_mtime_ns, wal_stat.st_size])
        else:
            parts.extend([0, 0])
        return tuple(parts)
    except OSError:
        return None


def _cache_get(kind, path, token):
    if token is None:
        return _MISS
    with _CACHE_LOCK:
        entry = _CACHE.get((kind, str(path)))
        if entry and entry[0] == token:
            return entry[1]
    return _MISS


def _cache_set(kind, path, token, value):
    if token is None:
        return
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[(kind, str(path))] = (token, value)


def _tags(value):
    try:
        tags = json.loads(value or '[]')
        return [redact(x) for x in tags if isinstance(x, str)] if isinstance(tags, list) else []
    except (ValueError, TypeError):
        return []


def _summary(row):
    """Return message-derived resource facts.

    Directory snapshots are no longer read: walking a 115 share needed up to a
    dozen requests each and mass access triggered 115 access restrictions, so
    PanBox relies on what the ingested message provided.
    """
    metadata = json_object(row['metadata_json'])
    tags = metadata.get('quality_tags')
    return {
        'total_size': row['file_size'],
        'media_info': redact(metadata.get('media_info')),
        'quality_tags': [str(tag) for tag in tags if isinstance(tag, str)] if isinstance(tags, list) else [],
    }


def _present(row):
    summary = _summary(row)
    title = row['raw_label'] or row['filename'] or row['media_title'] or '未命名资源'
    # AV 番号是作品名本身；列表不要再用日文宣传语/带日期的文件名当标题。
    if row['resource_class'] == 'nsfw' and av_code(row['media_title']):
        title = row['media_title']
    return {'id': row['id'], 'media_id': row['media_id'], 'title': redact(title),
            'filename': redact(row['filename']), 'resource_class': row['resource_class'],
            'person_name': redact(row['media_title']) if row['resource_class'] == 'person' else '', 'tags': _tags(row['tags_json']),
            'provider': provider_info(row['provider_id']), 'availability_status': row['availability_status'],
            'last_checked_at': row['last_checked_at'], 'last_check_error': redact(row['last_check_error']),
            'created_at': row['created_at'], 'updated_at': row['updated_at'], 'year': row['year'],
            'quality': row['quality'], 'season': row['season'], 'season_end': row['season_end'], 'episode': row['episode'],
            'tmdb_status': row['recognition_status'], 'tmdb_id': row['tmdb_id'] if row['recognition_status']=='matched' else None,
            'tmdb_title': redact(row['tmdb_title']), 'poster_path': row['poster_path'] if row['recognition_status']=='matched' else None,
            'channel': redact(row['channel']), 'url_masked': masked_url(row['url']),
            'access_code': access_code(row['url']),
            'access_code_masked': '****' if access_code(row['url']) else '', 'summary': summary}


def _group_title(row):
    title = row['raw_label'] or row['filename'] or row['media_title'] or '未命名资源'
    if row['resource_class'] == 'nsfw' and av_code(row['media_title']):
        title = row['media_title']
    return redact(title)


def _merge_group_row(row, item, group):
    """把一行来源合并进分组头；item 只在需要完整来源字段时构建。"""
    if item is not None:
        group['resources'].append(item)
    group['resource_count'] += 1
    provider_id = item['provider']['id'] if item else row['provider_id']
    provider_label = item['provider']['label'] if item else provider_info(row['provider_id'])['label']
    provider_entry = group['provider_counts'].setdefault(
        provider_id, {'id': provider_id, 'label': provider_label, 'count': 0})
    provider_entry['count'] += 1
    availability = item['availability_status'] if item else row['availability_status']
    availability = availability if availability in group['availability_counts'] else 'unknown'
    group['availability_counts'][availability] += 1
    group['tags'] = sorted(set(group['tags']) | set(item['tags'] if item else _tags(row['tags_json'])))
    if row['updated_at'] and (not group['updated_at'] or row['updated_at'] > group['updated_at']):
        group['updated_at'] = row['updated_at']
    if row['created_at'] and (not group['created_at'] or row['created_at'] > group['created_at']):
        group['created_at'] = row['created_at']
    total_size = item['summary'].get('total_size') if item else row['file_size']
    if total_size:
        group['summary']['total_size'] += total_size


def _person_groups(rows, include_resources=True):
    groups = {}
    for row in rows:
        item = _present(row) if include_resources else None
        title = item['title'] if item else _group_title(row)
        key = normalize_title(str(row['media_title'] or title)) or f"media-{row['media_id']}"
        group = groups.get(key)
        if group is None:
            group = {
                'id': f'person-{key}', 'media_id': row['media_id'],
                'title': redact(row['media_title'] or title or '未命名人物'),
                'resource_class': 'person', 'tags': [], 'resources': [],
                'resource_count': 0, 'provider_counts': {},
                'availability_counts': {'available': 0, 'pending': 0, 'invalid': 0, 'unknown': 0},
                'updated_at': row['updated_at'], 'created_at': row['created_at'],
                'summary': {'total_size': 0},
            }
            groups[key] = group
        _merge_group_row(row, item, group)
    for group in groups.values():
        group['providers'] = list(group['provider_counts'].values())
        del group['provider_counts']
        if include_resources:
            group['resources'].sort(key=lambda item: (item.get('updated_at') or '', item['id']), reverse=True)
    return list(groups.values())


def _series_groups(rows, include_resources=True):
    """Aggregate collection sources into one 合集 row.

    RCTD 这类消息会把同一个系列拆成多条 media_items（年份/上下文不同），
    但来源都属于同一个系列或同一个合集标题。资源库需要像人物一样先显示
    一条合集，点开后再列出全部来源链接。
    """
    groups = {}
    for row in rows:
        item = _present(row) if include_resources else None
        title = item['title'] if item else _group_title(row)
        series_id = row['series_id']
        if series_id is not None:
            key = f"series-{series_id}"
        else:
            fallback = row['media_title'] or title
            key = f"collection-{normalize_title(str(fallback)) or row['media_id']}"
        group = groups.get(key)
        if group is None:
            group = {
                'id': key, 'media_id': row['media_id'], 'title': '',
                'resource_class': 'collection', 'tags': [], 'resources': [],
                'resource_count': 0, 'provider_counts': {},
                'availability_counts': {'available': 0, 'pending': 0, 'invalid': 0, 'unknown': 0},
                'updated_at': row['updated_at'], 'created_at': row['created_at'],
                'summary': {'total_size': 0}, '_titles': set(), '_series_title': row['series_title'],
            }
            groups[key] = group
        _merge_group_row(row, item, group)
        group['_titles'].add(str(row['media_title'] or title or '').strip())
        if row['series_title']:
            group['_series_title'] = row['series_title']
    for group in groups.values():
        titles = [title for title in group.pop('_titles') if title]
        series_title = group.pop('_series_title', None)
        # 同一系列所有成员共用同一个合集标题时（RCTD），直接用这个标题；
        # 成员标题不同（指环王1/2/3）时退回系列名。
        group['title'] = redact(titles[0] if len(titles) == 1 else (series_title or (titles[0] if titles else '未命名合集')))
        group['providers'] = list(group['provider_counts'].values())
        del group['provider_counts']
        if include_resources:
            group['resources'].sort(key=lambda item: (item.get('updated_at') or '', item['id']), reverse=True)
    return list(groups.values())

def list_resources(*, q='', resource_group='all', resource_class='all', provider='all', availability='all', tmdb_status='all', year='all', tag='',
                   quality='all', channel='all', updated='all', sort='updated_desc', page=1, page_size=30, aggregate=None,
                   include_resources=True, group_id=None, db_path=None):
    where, args = [], []
    if resource_group not in RESOURCE_GROUPS:
        raise ValueError('资源大类筛选值无效。')
    if resource_group != 'all':
        where.append('r.resource_group=?')
        args.append(resource_group)
    for field, selected, choices in [('resource_class', resource_class, RESOURCE_CLASSES), ('availability_status', availability, AVAILABILITY), ('recognition_status', tmdb_status, TMDB_STATUSES)]:
        if selected != 'all':
            if selected not in choices:
                raise ValueError('资源筛选值无效。')
            where.append(f'r.{field}=?'); args.append(selected)
    if provider != 'all':
        where.append('r.provider_id=?'); args.append(provider)
    if year != 'all':
        if year == 'unknown': where.append('r.year IS NULL')
        else:
            if not str(year).isdigit() or not 1870 <= int(year) <= 2100: raise ValueError('年份筛选无效。')
            where.append('r.year=?'); args.append(int(year))
    if quality != 'all':
        if quality == 'unknown': where.append("NULLIF(TRIM(r.quality),'') IS NULL")
        elif quality in QUALITY_GROUPS:
            values = QUALITY_GROUPS[quality]
            where.append(f"UPPER(r.quality) IN ({','.join('?' for _ in values)})"); args.extend(values)
        else: raise ValueError('清晰度筛选无效。')
    if channel != 'all':
        where.append('EXISTS (SELECT 1 FROM ingestion_links l JOIN ingestion_events e ON e.id=l.ingestion_id WHERE l.source_key=r.source_key AND e.channel_name=?)'); args.append(channel)
    if tag:
        where.append("EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(r.tags_json) THEN r.tags_json ELSE '[]' END) WHERE value=?)"); args.append(tag)
    if updated != 'all':
        if updated not in {'7', '30', '90'}: raise ValueError('更新时间筛选无效。')
        where.append('r.updated_at>=?'); args.append((datetime.now(timezone.utc)-timedelta(days=int(updated))).isoformat(timespec='seconds'))
    terms = q.strip().split()
    if len(q) > 300: raise ValueError('搜索内容不能超过 300 字符。')
    # 纯字母/数字/CJK 且至少 3 字时走去重原文的 trigram 索引；短词和标点保持
    # 原来的 LIKE 语义（trigram 无法匹配 2 字中文）。
    fts_terms = bool(terms) and all(re.fullmatch(r'[0-9A-Za-z_\u4e00-\u9fff]{3,}', term) for term in terms)
    for term in terms:
        needle = '%' + term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
        if fts_terms:
            match = '"' + term + '"'
            where.append("""(r.raw_label LIKE ? ESCAPE '\\' OR r.filename LIKE ? ESCAPE '\\'
                OR r.media_title LIKE ? ESCAPE '\\' OR r.tmdb_title LIKE ? ESCAPE '\\'
                OR r.search_text LIKE ? ESCAPE '\\'
                OR r.id IN (SELECT m.source_id FROM source_text_map m
                    WHERE m.text_id IN (SELECT rowid FROM search_text_fts WHERE search_text_fts MATCH ?))
                OR EXISTS (SELECT 1 FROM ingestion_links l
                    JOIN event_text_map em ON em.event_id=l.ingestion_id
                    WHERE l.source_key=r.source_key
                      AND em.text_id IN (SELECT rowid FROM search_text_fts WHERE search_text_fts MATCH ?)))""")
            args.extend([needle]*5 + [match, match])
            continue
        where.append("""(r.raw_label LIKE ? ESCAPE '\\' OR r.filename LIKE ? ESCAPE '\\' OR r.media_title LIKE ? ESCAPE '\\'
            OR r.tmdb_title LIKE ? ESCAPE '\\'
            OR r.search_text LIKE ? ESCAPE '\\'
            OR r.id IN (SELECT m.source_id FROM search_texts t CROSS JOIN source_text_map m
                WHERE m.text_id=t.id AND t.text LIKE ? ESCAPE '\\')
            OR EXISTS (SELECT 1 FROM ingestion_links l CROSS JOIN event_text_map em CROSS JOIN search_texts t
                WHERE l.source_key=r.source_key AND em.event_id=l.ingestion_id
                  AND t.id=em.text_id AND t.text LIKE ? ESCAPE '\\'))""")
        args.extend([needle]*7)
    predicate = ' AND '.join(where) or '1=1'
    orders = {'updated_desc':'r.updated_at DESC,r.id DESC', 'title_asc':'r.raw_label COLLATE NOCASE,r.id DESC',
              'checked_desc':'r.last_checked_at DESC,r.id DESC', 'created_desc':'r.created_at DESC,r.id DESC'}
    if sort not in orders: raise ValueError('排序方式无效。')
    page_size = max(10,min(int(page_size),100)); page = max(1,int(page))
    if aggregate not in {None, '', 'person', 'series'} or (aggregate == 'person' and resource_group != 'person') or (aggregate == 'series' and resource_group != 'series'):
        raise ValueError('资源聚合方式无效。')
    if group_id is not None and (not isinstance(group_id, str) or not group_id or len(group_id) > 500):
        raise ValueError('资源分组标识无效。')
    if group_id:
        # 分组详情直接按分组键过滤，避免为了一个 5 条资源的人物取回 1 万条来源。
        group_where = None
        if group_id.startswith('person-'):
            key = group_id[7:]
            if key.startswith('media-') and key[6:].isdigit():
                group_where = ('r.media_id=?', [int(key[6:])])
            else:
                group_where = ('r.media_normalized_title=?', [key])
        elif group_id.startswith('series-') and group_id[7:].isdigit():
            group_where = ('r.series_id=?', [int(group_id[7:])])
        elif group_id.startswith('collection-'):
            group_where = ('r.series_id IS NULL AND r.media_normalized_title=?', [group_id[11:]])
        if group_where:
            predicate = '(' + predicate + ') AND ' + group_where[0]
            args.extend(group_where[1])
    path = resolve_db_path(db_path)
    token = _cache_token(path)
    cached_summary = _cache_get('summary', path, token)
    # 分组头只需要来源字段，不需要 channel；分组详情才构建完整来源。
    row_cte = GROUP_CTE if aggregate and not include_resources else CTE
    with connect(path) as db:
        db.execute('BEGIN')
        if terms:
            # 搜索通常是高选择性的：用窗口计数把 COUNT 和当前页合并成一次扫描。
            # 普通列表不这样做，COUNT(*) OVER() 会强制为全部 4 万多行计算 channel。
            if aggregate:
                rows = db.execute(row_cte + 'SELECT *, COUNT(*) OVER() AS __total FROM resources r WHERE '+predicate
                                  + ' ORDER BY '+orders[sort]+' LIMIT -1', args).fetchall()
                source_total = int(rows[0]['__total']) if rows else 0
            else:
                rows = db.execute(row_cte + 'SELECT *, COUNT(*) OVER() AS __total FROM resources r WHERE '+predicate
                                  + ' ORDER BY '+orders[sort]+' LIMIT ? OFFSET ?',
                                  [*args, page_size, (page-1)*page_size]).fetchall()
                if rows:
                    source_total = int(rows[0]['__total'])
                elif page > 1:
                    source_total = db.execute(COUNT_CTE + 'SELECT COUNT(*) FROM resources r WHERE '+predicate, args).fetchone()[0]
                else:
                    source_total = 0
        else:
            source_total = db.execute(COUNT_CTE + 'SELECT COUNT(*) FROM resources r WHERE '+predicate,args).fetchone()[0]
            fetch_limit = source_total if aggregate else page_size
            rows=db.execute(row_cte+'SELECT * FROM resources r WHERE '+predicate+' ORDER BY '+orders[sort]+' LIMIT ? OFFSET ?', [*args,fetch_limit,(0 if aggregate else (page-1)*page_size)]).fetchall()
        if cached_summary is _MISS:
            summary=dict(db.execute(COUNT_CTE + """
                SELECT COUNT(*) AS total,
                       COALESCE(SUM(availability_status='available'),0) AS available,
                       COALESCE(SUM(availability_status='pending'),0) AS pending,
                       COALESCE(SUM(availability_status='invalid'),0) AS invalid,
                       COALESCE(SUM(resource_group='media'),0) AS media,
                       COALESCE(SUM(resource_group='person'),0) AS person,
                       COALESCE(SUM(resource_group='series'),0) AS series,
                       COALESCE(SUM(resource_group='music'),0) AS music,
                       COALESCE(SUM(resource_group='nsfw'),0) AS nsfw,
                       COALESCE(SUM(resource_group='other'),0) AS other,
                       COALESCE(SUM(resource_group='unrecognized'),0) AS unrecognized
                FROM resources
            """).fetchone())
        else:
            summary = dict(cached_summary)
    if cached_summary is _MISS:
        _cache_set('summary', path, token, summary)
    if aggregate:
        grouped = _series_groups(rows, include_resources) if aggregate == 'series' else _person_groups(rows, include_resources)
        if group_id:
            grouped = [item for item in grouped if item['id'] == group_id]
        if sort == 'title_asc':
            grouped.sort(key=lambda item: item['title'].casefold())
        elif sort in {'created_desc', 'updated_desc', 'checked_desc'}:
            grouped.sort(key=lambda item: (item['updated_at'] or '', item['id']), reverse=True)
        total = len(grouped)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        return {'items': grouped[start:start + page_size], 'total': total, 'source_total': source_total,
                'page': page, 'pages': pages, 'page_size': page_size, 'summary': summary, 'aggregated': aggregate}
    total = source_total
    pages=max(1,(total+page_size-1)//page_size);page=min(page,pages)
    return {'items':[_present(r) for r in rows], 'total':total,'page':page,'pages':pages,'page_size':page_size,'summary':summary}


def get_resource(resource_id, db_path=None):
    with connect(db_path) as db:
        row = db.execute(CTE+'SELECT * FROM resources WHERE id=?',(resource_id,)).fetchone()
        if row is None: return None
        result=_present(row)
        messages=db.execute("""SELECT DISTINCT e.id,e.channel_name,e.message_url,e.raw_text,e.received_at,l.status AS link_status
            FROM ingestion_links l
            JOIN ingestion_events e ON e.id=l.ingestion_id WHERE l.source_key=? ORDER BY e.received_at DESC""", (row['source_key'],)).fetchall()
        related=db.execute(CTE+'SELECT * FROM resources WHERE media_id=? AND id!=? ORDER BY id DESC LIMIT 30',(row['media_id'],resource_id)).fetchall() if row['recognition_status']=='matched' else []
    # 一条来源只归属「真正把它发出来的那条消息」；同一个链接被别的投稿重复发过时
    # 单独放在 repeat_messages 里，避免把 115 分享和 ed2k 文件的出处混成一锅。
    deliveries=[m for m in messages if m['link_status']=='inserted']
    repeats=[m for m in messages if m['link_status']!='inserted']
    if not deliveries and messages:
        deliveries=[messages[-1]]
        repeats=[m for m in messages if m['id']!=messages[-1]['id']]
    present=lambda rows:[{k:redact(m[k]) for k in m.keys() if k!='link_status'} for m in rows]
    result.update(url=row['url'], notes=redact(row['notes']), original_messages=present(deliveries),
                  repeat_messages=present(repeats),
                  raw_text=redact(row['raw_text']), related_sources=[_present(r) for r in related])
    return result


def get_resource_filters(db_path=None):
    path = resolve_db_path(db_path)
    token = _cache_token(path)
    cached = _cache_get('filters', path, token)
    if cached is not _MISS:
        return copy.deepcopy(cached)
    with connect(path) as db:
        providers=[r[0] for r in db.execute("SELECT DISTINCT CASE WHEN source_type='ed2k' THEN 'ed2k' ELSE provider END FROM source_records")]
        years=[r[0] for r in db.execute(COUNT_CTE+'SELECT DISTINCT year FROM resources WHERE year IS NOT NULL ORDER BY year DESC')]
        tags=[r[0] for r in db.execute("SELECT DISTINCT j.value FROM source_records s,json_each(CASE WHEN json_valid(s.tags_json) THEN s.tags_json ELSE '[]' END) j WHERE j.type='text' ORDER BY j.value")]
        channels=[r[0] for r in db.execute("SELECT DISTINCT e.channel_name FROM ingestion_events e JOIN ingestion_links l ON l.ingestion_id=e.id JOIN source_records s ON s.source_key=l.source_key WHERE e.channel_name IS NOT NULL AND e.channel_name!='' ORDER BY e.channel_name")]
    result = {'providers':[provider_info(p) for p in sorted(providers)], 'resource_classes':RESOURCE_CLASSES,'availability':AVAILABILITY,
              'tmdb_statuses':TMDB_STATUSES,'years':years,'tags':tags,'channels':channels}
    _cache_set('filters', path, token, result)
    return copy.deepcopy(result)


def update_resource(resource_id, values, db_path=None):
    allowed={'resource_class','tags','notes'}
    if not values or set(values)-allowed: raise ValueError('仅可编辑资源分类、标签和备注。')
    updates, args=[],[]
    if 'resource_class' in values:
        if values['resource_class'] not in RESOURCE_CLASSES: raise ValueError('资源分类无效。')
        updates.append('resource_class=?'); args.append(values['resource_class'])
    if 'tags' in values:
        tags=values['tags']
        if not isinstance(tags,list) or len(tags)>20 or any(not isinstance(t,str) or not 1<=len(t.strip())<=40 for t in tags): raise ValueError('最多 20 个标签，每个标签 1–40 字符。')
        updates.append('tags_json=?');args.append(json.dumps(list(dict.fromkeys(t.strip() for t in tags)),ensure_ascii=False))
    if 'notes' in values:
        if not isinstance(values['notes'],str) or len(values['notes'])>2000: raise ValueError('备注不能超过 2000 字符。')
        updates.append('notes=?');args.append(values['notes'])
    with connect(db_path) as db:
        db.execute('UPDATE source_records SET '+','.join(updates)+' WHERE id=?', [*args,resource_id])
    return get_resource(resource_id,db_path)


def resource_access(resource_id, db_path=None):
    with connect(db_path) as db:
        row=db.execute('SELECT url FROM source_records WHERE id=?',(resource_id,)).fetchone()
    if not row: return None
    scheme=urlsplit(row['url']).scheme.lower()
    return {'url':row['url'], 'access_code':access_code(row['url']), 'can_open':scheme in {'http','https'}}


def check_resource(resource_id, db_path=None):
    with connect(db_path) as db:
        row=db.execute('SELECT * FROM source_records WHERE id=?',(resource_id,)).fetchone()
    if not row: return None
    provider='ed2k' if row['source_type']=='ed2k' else row['provider']
    if not provider_info(provider)['can_check']: raise ValueError('该来源暂不支持在线检查。')
    key=(str(resolve_db_path(db_path)),provider)
    with _LOCK:
        if key in _CHECKS: raise ValueError('该网盘已有检查正在进行，请等待完成。')
        _CHECKS.add(key)
    try:
        # Persist attempt time before I/O; repeated clicks and network failures cannot create a retry storm.
        now=utc_now()
        if row['last_checked_at'] and row['last_checked_at'] > (datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat(timespec='seconds'):
            raise ValueError('刚刚检查过，请至少等待 60 秒后重试。')
        with connect(db_path) as db:
            db.execute("UPDATE source_records SET last_checked_at=?, availability_status='pending', last_check_error=NULL WHERE id=?",(now,resource_id))
        try:
            state,error=probe(provider,row['url'])
        except Exception:
            state,error='pending','检查未完成，稍后可再次手动检查。'
        with connect(db_path) as db:
            db.execute('UPDATE source_records SET availability_status=?,last_checked_at=?,last_check_error=? WHERE id=? AND url=?', (state,utc_now(),error,resource_id,row['url']))
    finally:
        with _LOCK: _CHECKS.discard(key)
    return get_resource(resource_id,db_path)
