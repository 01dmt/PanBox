"""Source-level resource queries and local management. No remote I/O during reads."""
from datetime import datetime, timedelta, timezone
import json
import threading
from urllib.parse import urlsplit

from .importers import normalize_title
from .schema import connect, resolve_db_path
from .repository import utc_now, QUALITY_GROUPS
from .resource_metadata import RESOURCE_CLASSES, AVAILABILITY, json_object, masked_url, access_code, redact
from .resource_providers import provider_info, probe

TMDB_STATUSES = {'matched': '已匹配', 'review': '待确认', 'not_found': '未找到', 'unrecognized': '未识别', 'error': '匹配错误'}
RESOURCE_GROUPS = {'all', 'media', 'person', 'series', 'unrecognized'}
_LOCK = threading.Lock()
_CHECKS = set()
BASE = """SELECT s.*, m.title AS media_title,m.tmdb_id,m.tmdb_media_type,m.tmdb_title,m.poster_path,m.resource_kind,
    CASE WHEN m.tmdb_status='matched' AND m.tmdb_id IS NOT NULL THEN 'matched'
         WHEN m.tmdb_status IN ('review','not_found','error') THEN m.tmdb_status ELSE 'unrecognized' END AS recognition_status,
    COALESCE(s.parsed_year,m.year,CAST(NULLIF(substr(m.release_date,1,4),'') AS INTEGER)) AS year,
    COALESCE(s.last_checked_at,s.created_at) AS updated_at,
    CASE WHEN s.source_type='ed2k' THEN 'ed2k' ELSE s.provider END AS provider_id,
    (SELECT e.channel_name FROM ingestion_links l JOIN ingestion_events e ON e.id=l.ingestion_id
     WHERE l.source_key=s.source_key ORDER BY e.received_at DESC LIMIT 1) AS channel
    FROM source_records s JOIN media_items m ON m.id=s.media_id"""
CTE = 'WITH resource_sources AS (' + BASE + '''), resources AS (
    SELECT *, CASE
        WHEN resource_class='person' THEN 'person'
        WHEN resource_class='collection' THEN 'series'
        WHEN recognition_status='matched' THEN 'media'
        ELSE 'unrecognized'
    END AS resource_group FROM resource_sources
) '''


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
    return {'total_size': row['file_size'], 'media_info': redact(metadata.get('media_info'))}


def _present(row):
    summary = _summary(row)
    title = row['raw_label'] or row['filename'] or row['media_title'] or '未命名资源'
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
            'access_code_masked': '****' if access_code(row['url']) else '', 'summary': summary}


def _person_groups(rows):
    groups = {}
    for row in rows:
        item = _present(row)
        key = normalize_title(str(row['media_title'] or item['title'])) or f"media-{row['media_id']}"
        group = groups.get(key)
        if group is None:
            group = {
                'id': f'person-{key}', 'media_id': row['media_id'],
                'title': redact(row['media_title'] or item['title'] or '未命名人物'),
                'resource_class': 'person', 'tags': [], 'resources': [],
                'resource_count': 0, 'provider_counts': {},
                'availability_counts': {'available': 0, 'pending': 0, 'invalid': 0, 'unknown': 0},
                'updated_at': row['updated_at'], 'created_at': row['created_at'],
                'summary': {'total_size': 0},
            }
            groups[key] = group
        group['resources'].append(item)
        group['resource_count'] += 1
        provider_id = item['provider']['id']
        provider_entry = group['provider_counts'].setdefault(provider_id, {'id': provider_id, 'label': item['provider']['label'], 'count': 0})
        provider_entry['count'] += 1
        availability = item['availability_status'] if item['availability_status'] in group['availability_counts'] else 'unknown'
        group['availability_counts'][availability] += 1
        group['tags'] = sorted(set(group['tags']) | set(item['tags']))
        if item['updated_at'] and (not group['updated_at'] or item['updated_at'] > group['updated_at']):
            group['updated_at'] = item['updated_at']
        if item['created_at'] and (not group['created_at'] or item['created_at'] > group['created_at']):
            group['created_at'] = item['created_at']
        summary = item['summary']
        if summary.get('total_size'):
            group['summary']['total_size'] += summary['total_size']
    for group in groups.values():
        group['providers'] = list(group['provider_counts'].values())
        del group['provider_counts']
        group['resources'].sort(key=lambda item: (item.get('updated_at') or '', item['id']), reverse=True)
    return list(groups.values())


def list_resources(*, q='', resource_group='all', resource_class='all', provider='all', availability='all', tmdb_status='all', year='all', tag='',
                   quality='all', channel='all', updated='all', sort='updated_desc', page=1, page_size=30, aggregate=None, db_path=None):
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
    for term in terms:
        needle = '%' + term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
        where.append("""(r.raw_label LIKE ? ESCAPE '\\' OR r.filename LIKE ? ESCAPE '\\' OR r.media_title LIKE ? ESCAPE '\\'
            OR r.tmdb_title LIKE ? ESCAPE '\\' OR r.raw_text LIKE ? ESCAPE '\\'
            OR EXISTS (SELECT 1 FROM json_tree(CASE WHEN json_valid(r.metadata_json) THEN r.metadata_json ELSE '{}' END) j
                WHERE j.type='text' AND j.atom LIKE ? ESCAPE '\\')
            OR EXISTS (SELECT 1 FROM ingestion_links l JOIN ingestion_events e ON e.id=l.ingestion_id
                WHERE l.source_key=r.source_key AND e.raw_text LIKE ? ESCAPE '\\'))""")
        args.extend([needle]*7)
    predicate = ' AND '.join(where) or '1=1'
    orders = {'updated_desc':'r.updated_at DESC,r.id DESC', 'title_asc':'r.raw_label COLLATE NOCASE,r.id DESC',
              'checked_desc':'r.last_checked_at DESC,r.id DESC', 'created_desc':'r.created_at DESC,r.id DESC'}
    if sort not in orders: raise ValueError('排序方式无效。')
    page_size = max(10,min(int(page_size),100)); page = max(1,int(page))
    if aggregate not in {None, '', 'person'} or (aggregate == 'person' and resource_group != 'person'):
        raise ValueError('资源聚合方式无效。')
    with connect(db_path) as db:
        db.execute('BEGIN')
        source_total = db.execute(CTE + 'SELECT COUNT(*) FROM resources r WHERE '+predicate,args).fetchone()[0]
        fetch_limit = source_total if aggregate == 'person' else page_size
        rows=db.execute(CTE+'SELECT * FROM resources r WHERE '+predicate+' ORDER BY '+orders[sort]+' LIMIT ? OFFSET ?', [*args,fetch_limit,(0 if aggregate == 'person' else (page-1)*page_size)]).fetchall()
        summary=dict(db.execute(CTE + """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(availability_status='available'),0) AS available,
                   COALESCE(SUM(availability_status='pending'),0) AS pending,
                   COALESCE(SUM(availability_status='invalid'),0) AS invalid,
                   COALESCE(SUM(resource_group='media'),0) AS media,
                   COALESCE(SUM(resource_group='person'),0) AS person,
                   COALESCE(SUM(resource_group='series'),0) AS series,
                   COALESCE(SUM(resource_group='unrecognized'),0) AS unrecognized
            FROM resources
        """).fetchone())
    if aggregate == 'person':
        grouped = _person_groups(rows)
        if sort == 'title_asc':
            grouped.sort(key=lambda item: item['title'].casefold())
        elif sort in {'created_desc', 'updated_desc', 'checked_desc'}:
            grouped.sort(key=lambda item: (item['updated_at'] or '', item['id']), reverse=True)
        total = len(grouped)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        return {'items': grouped[start:start + page_size], 'total': total, 'source_total': source_total,
                'page': page, 'pages': pages, 'page_size': page_size, 'summary': summary, 'aggregated': 'person'}
    total = source_total
    pages=max(1,(total+page_size-1)//page_size);page=min(page,pages)
    return {'items':[_present(r) for r in rows], 'total':total,'page':page,'pages':pages,'page_size':page_size,'summary':summary}


def get_resource(resource_id, db_path=None):
    with connect(db_path) as db:
        row = db.execute(CTE+'SELECT * FROM resources WHERE id=?',(resource_id,)).fetchone()
        if row is None: return None
        result=_present(row)
        messages=db.execute("""SELECT DISTINCT e.id,e.channel_name,e.message_url,e.raw_text,e.received_at FROM ingestion_links l
            JOIN ingestion_events e ON e.id=l.ingestion_id WHERE l.source_key=? ORDER BY e.received_at DESC""", (row['source_key'],)).fetchall()
        related=db.execute(CTE+'SELECT * FROM resources WHERE media_id=? AND id!=? ORDER BY id DESC LIMIT 30',(row['media_id'],resource_id)).fetchall() if row['recognition_status']=='matched' else []
    result.update(url=row['url'], notes=redact(row['notes']), original_messages=[{k:redact(m[k]) for k in m.keys()} for m in messages],
                  raw_text=redact(row['raw_text']), related_sources=[_present(r) for r in related])
    return result


def get_resource_filters(db_path=None):
    with connect(db_path) as db:
        providers=[r[0] for r in db.execute("SELECT DISTINCT CASE WHEN source_type='ed2k' THEN 'ed2k' ELSE provider END FROM source_records")]
        years=[r[0] for r in db.execute(CTE+'SELECT DISTINCT year FROM resources WHERE year IS NOT NULL ORDER BY year DESC')]
        tags=[r[0] for r in db.execute("SELECT DISTINCT j.value FROM source_records s,json_each(CASE WHEN json_valid(s.tags_json) THEN s.tags_json ELSE '[]' END) j WHERE j.type='text' ORDER BY j.value")]
        channels=[r[0] for r in db.execute("SELECT DISTINCT e.channel_name FROM ingestion_events e JOIN ingestion_links l ON l.ingestion_id=e.id JOIN source_records s ON s.source_key=l.source_key WHERE e.channel_name IS NOT NULL AND e.channel_name!='' ORDER BY e.channel_name")]
    return {'providers':[provider_info(p) for p in sorted(providers)], 'resource_classes':RESOURCE_CLASSES,'availability':AVAILABILITY,
            'tmdb_statuses':TMDB_STATUSES,'years':years,'tags':tags,'channels':channels}


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
