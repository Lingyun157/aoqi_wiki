import os
import re
import json
import sqlite3
import time
from collections import defaultdict

KNOWLEDGE_BASE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    '..', '奥奇传说知识库'
)

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    '..', 'knowledge_index.db'
)

INDEX_BUILT = False
INDEX_BUILD_TIME = 0
_INDEXED_FILES = {}


def _parse_frontmatter(content):
    if not content.startswith('---'):
        return {}, content
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content
    yaml_block = parts[1].strip()
    body = parts[2].strip()
    meta = {}
    current_key = None
    for line in yaml_block.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('- ') and current_key:
            if current_key not in meta:
                meta[current_key] = []
            val = line[2:].strip().strip('"').strip("'")
            meta[current_key].append(val)
        elif ':' in line:
            key, val = line.split(':', 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val.startswith('[') and val.endswith(']'):
                val = [v.strip().strip('"').strip("'") for v in val[1:-1].split(',') if v.strip()]
            if key == 'tags' and isinstance(val, str):
                val = [v.strip().strip('"').strip("'") for v in val.split(',') if v.strip()]
            meta[key] = val
            current_key = key
        else:
            current_key = None
    return meta, body


def _slugify(filepath, kb_root):
    rel = os.path.relpath(filepath, kb_root)
    slug = rel.replace(os.sep, '/').replace('.md', '')
    return slug


def _get_all_md_files(kb_path):
    files = []
    for root, dirs, fnames in os.walk(kb_path):
        if root.startswith('.'):
            continue
        for fname in fnames:
            if fname.endswith('.md'):
                files.append(os.path.join(root, fname))
    return files


def build_index(force=False):
    global INDEX_BUILT, INDEX_BUILD_TIME, _INDEXED_FILES

    now = time.time()
    if not force and INDEX_BUILT and (now - INDEX_BUILD_TIME) < 3600:
        return

    kb_path = os.path.abspath(KNOWLEDGE_BASE_PATH)
    if not os.path.isdir(kb_path):
        INDEX_BUILT = True
        INDEX_BUILD_TIME = now
        return

    files = _get_all_md_files(kb_path)

    changed = False
    for fp in files:
        mtime = os.path.getmtime(fp)
        if _INDEXED_FILES.get(fp) != mtime:
            changed = True
            break
    if not force and not changed and INDEX_BUILT:
        INDEX_BUILD_TIME = now
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS pages (
            slug TEXT PRIMARY KEY,
            title TEXT,
            page_type TEXT,
            race_id INTEGER,
            element TEXT,
            profession TEXT,
            tags TEXT,
            level INTEGER,
            path TEXT,
            content TEXT,
            body_text TEXT
        )
    ''')
    conn.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
            title, body_text, tags, content=''
        )
    ''')
    conn.commit()

    existing = {row[0] for row in conn.execute('SELECT slug FROM pages').fetchall()}
    current_slugs = set()

    for fp in files:
        slug = _slugify(fp, kb_path)
        current_slugs.add(slug)
        mtime = os.path.getmtime(fp)

        if not force and _INDEXED_FILES.get(fp) == mtime and slug in existing:
            continue

        try:
            with open(fp, 'r', encoding='utf-8') as f:
                raw = f.read()
            meta, body = _parse_frontmatter(raw)

            title = meta.get('title', '') or meta.get('name', '') or ''
            page_type = meta.get('type', '')
            race_id = meta.get('raceId') or meta.get('race_id')
            element = meta.get('属性', '')
            profession = meta.get('职业', '')
            tags = meta.get('tags', [])
            if isinstance(tags, list):
                tags_str = ', '.join(tags)
            else:
                tags_str = str(tags)

            body_text = title + ' ' + body
            body_text = re.sub(r'\[\[([^|\]]+)\|?([^\]]*)\]\]', r'\1', body_text)
            body_text = re.sub(r'[#*`>]', ' ', body_text)
            body_text = re.sub(r'\s+', ' ', body_text).strip()

            content_json = json.dumps({
                'meta': meta,
                'body': body
            }, ensure_ascii=False)

            level = meta.get('level') or meta.get('关卡')
            try:
                level_num = int(level) if level else None
            except:
                level_num = None

            existing_data = conn.execute(
                'SELECT rowid FROM pages WHERE slug = ?', (slug,)
            ).fetchone()

            if existing_data:
                conn.execute('''
                    UPDATE pages SET
                        title=?, page_type=?, race_id=?, element=?, profession=?,
                        tags=?, level=?, path=?, content=?, body_text=?
                    WHERE slug=?
                ''', (title, page_type, race_id, element, profession,
                      tags_str, level_num, fp, content_json, body_text, slug))
                conn.execute('DELETE FROM pages_fts WHERE rowid=?', (existing_data[0],))

            else:
                conn.execute('''
                    INSERT INTO pages (slug, title, page_type, race_id, element, profession,
                        tags, level, path, content, body_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (slug, title, page_type, race_id, element, profession,
                      tags_str, level_num, fp, content_json, body_text))
                rowid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]

            conn.execute('''
                INSERT INTO pages_fts(rowid, title, body_text, tags)
                VALUES (?, ?, ?, ?)
            ''', (slug if isinstance(slug, int) else None, title, body_text, tags_str))

            _INDEXED_FILES[fp] = mtime
        except Exception as e:
            print(f'Error indexing {fp}: {e}')

    stale = existing - current_slugs
    for slug in stale:
        conn.execute('DELETE FROM pages WHERE slug = ?', (slug,))

    conn.commit()
    conn.close()

    INDEX_BUILT = True
    INDEX_BUILD_TIME = time.time()
    print(f'Knowledge index built: {len(current_slugs)} pages')


def search_pages(query, page_type=None, element=None, limit=10, offset=0):
    build_index()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    sql = 'SELECT p.* FROM pages p WHERE 1=1'
    params = []

    if query:
        sql += ' AND (p.title LIKE ? OR p.body_text LIKE ?)'
        q = f'%{query}%'
        params.extend([q, q])

    if page_type:
        sql += ' AND p.page_type = ?'
        params.append(page_type)

    if element:
        sql += ' AND p.element = ?'
        params.append(element)

    sql += ' ORDER BY p.title LIMIT ? OFFSET ?'
    params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()

    results = []
    for row in rows:
        result = {
            'slug': row['slug'],
            'title': row['title'],
            'type': row['page_type'],
            'raceId': row['race_id'],
            'element': row['element'],
            'profession': row['profession'],
            'tags': row['tags'].split(', ') if row['tags'] else [],
            'path': row['path'],
        }
        content = json.loads(row['content'])
        result['meta'] = content.get('meta', {})

        snippet = row['body_text'][:200]
        if query and query.lower() in snippet.lower():
            idx = snippet.lower().find(query.lower())
            start = max(0, idx - 30)
            snippet = ('...' if start > 0 else '') + snippet[start:start + 150]
            if start + 150 < len(row['body_text']):
                snippet += '...'
        result['snippet'] = snippet
        results.append(result)

    conn.close()
    return results


def get_page(slug):
    build_index()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    row = conn.execute('SELECT * FROM pages WHERE slug = ?', (slug,)).fetchone()
    conn.close()

    if not row:
        return None

    content = json.loads(row['content'])
    return {
        'slug': row['slug'],
        'title': row['title'],
        'type': row['page_type'],
        'raceId': row['race_id'],
        'element': row['element'],
        'profession': row['profession'],
        'tags': row['tags'].split(', ') if row['tags'] else [],
        'path': row['path'],
        'meta': content.get('meta', {}),
        'body': content.get('body', '')
    }


def get_page_list(page_type=None, element=None, search=None, limit=50, offset=0):
    build_index()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    sql = 'SELECT slug, title, page_type, race_id, element, profession, tags FROM pages WHERE 1=1'
    params = []

    if page_type:
        sql += ' AND page_type = ?'
        params.append(page_type)

    if element:
        sql += ' AND element = ?'
        params.append(element)

    if search:
        sql += ' AND (title LIKE ? OR tags LIKE ?)'
        q = f'%{search}%'
        params.extend([q, q])

    sql += ' ORDER BY title LIMIT ? OFFSET ?'
    params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    return [{
        'slug': row['slug'],
        'title': row['title'],
        'type': row['page_type'],
        'raceId': row['race_id'],
        'element': row['element'],
        'profession': row['profession'],
        'tags': row['tags'].split(', ') if row['tags'] else [],
    } for row in rows]


def get_stats():
    build_index()
    conn = sqlite3.connect(DB_PATH)
    stats = {
        'total_pages': conn.execute('SELECT COUNT(*) FROM pages').fetchone()[0],
        'by_type': {},
        'by_element': {},
    }
    for row in conn.execute('SELECT page_type, COUNT(*) FROM pages GROUP BY page_type').fetchall():
        if row[0]:
            stats['by_type'][row[0]] = row[1]
    for row in conn.execute('SELECT element, COUNT(*) FROM pages WHERE element != "" GROUP BY element').fetchall():
        if row[0]:
            stats['by_element'][row[0]] = row[1]
    conn.close()
    return stats
