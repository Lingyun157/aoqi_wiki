from flask import Flask, jsonify, request, send_from_directory, g
import json
import os
import time
import requests
import uuid
import hmac
import hashlib
import base64
import subprocess
import threading
from collections import defaultdict
from functools import wraps
from urllib.parse import urlparse
from database import init_db, get_db, hash_password, verify_password, ROLE_HIERARCHY, ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_USER, DB_CONFIG

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

app = Flask(__name__, static_folder='../frontend', template_folder='../frontend')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'aoqi_forum_secret_key_2024')

def generate_token(user_id, username, role):
    payload = json.dumps({
        'user_id': user_id,
        'username': username,
        'role': role,
        'exp': int(time.time()) + 7 * 24 * 3600
    })
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip('=')
    signature = hmac.new(
        app.config['SECRET_KEY'].encode(),
        payload_b64.encode(),
        hashlib.sha256
    ).hexdigest()
    return f"{payload_b64}.{signature}"

def decode_token(token):
    try:
        payload_b64, signature = token.split('.', 1)
        expected_sig = hmac.new(
            app.config['SECRET_KEY'].encode(),
            payload_b64.encode(),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += '=' * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        if payload.get('exp', 0) < time.time():
            return None
        return payload
    except Exception:
        return None

def get_current_user():
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
        return decode_token(token)
    return None

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({'error': '请先登录'}), 401
        g.current_user = user
        return f(*args, **kwargs)
    return decorated

def role_required(min_role):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({'error': '请先登录'}), 401
            user_role = user.get('role', ROLE_USER)
            user_level = ROLE_HIERARCHY.get(user_role, 0)
            min_level = ROLE_HIERARCHY.get(min_role, 0)
            if user_level < min_level:
                return jsonify({'error': '权限不足'}), 403
            g.current_user = user
            return f(*args, **kwargs)
        return decorated
    return decorator

def can_manage_content(owner_user_id):
    user = get_current_user()
    if not user:
        return False
    user_role = user.get('role', ROLE_USER)
    user_level = ROLE_HIERARCHY.get(user_role, 0)
    if user_level >= ROLE_HIERARCHY.get(ROLE_ADMIN, 0):
        return True
    if owner_user_id and user.get('user_id') == owner_user_id:
        return True
    return False

# ==================== 接口限流 ====================

_rate_limits = defaultdict(list)
_rate_lock = threading.Lock()

def rate_limit(max_requests=60, per_seconds=60):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            key = request.remote_addr
            now = time.time()
            with _rate_lock:
                _rate_limits[key] = [t for t in _rate_limits[key] if t > now - per_seconds]
                if len(_rate_limits[key]) >= max_requests:
                    return jsonify({'error': '请求过于频繁，请稍后再试'}), 429
                _rate_limits[key].append(now)
            return f(*args, **kwargs)
        return decorated
    return decorator

# ==================== 操作日志 ====================

def log_action(action, target_type=None, target_id=None, detail=None):
    user = getattr(g, 'current_user', None)
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'INSERT INTO audit_logs (user_id, username, action, target_type, target_id, detail, ip_address) VALUES (%s, %s, %s, %s, %s, %s, %s)',
                (user.get('user_id') if user else None, user.get('username') if user else None, action, target_type, target_id, detail, request.remote_addr)
            )
    finally:
        conn.close()

# ==================== 通知 ====================

def create_notification(user_id, ntype, title, content=None, link=None):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'INSERT INTO notifications (user_id, type, title, content, link) VALUES (%s, %s, %s, %s, %s)',
                (user_id, ntype, title, content, link)
            )
    finally:
        conn.close()

# 数据路径 - 指向 aoqi 根目录
AOQI_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PETS_PATH = os.path.join(AOQI_BASE, '灵初精灵知识库', 'data', 'lingchu_pets.json')
FORMATIONS_PATH = os.path.join(AOQI_BASE, '敌方阵容关卡图鉴', 'data', 'enemy_formations.json')
RACE_CONFIG_PATH = os.path.join(AOQI_BASE, 'output', 'config', 'racetype_config.json')
PET_DICT_PATH = os.path.join(AOQI_BASE, 'output', 'config', 'pet', 'petdictionarydata.json')
BILIBILI_VIDEOS_PATH = os.path.join(AOQI_BASE, '灵初精灵知识库', 'data', 'bilibili_videos.json')

# 上传目录
UPLOAD_FOLDER = os.path.join(AOQI_BASE, 'aoqi-agent', 'frontend', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5MB

# 缓存数据
pets_data = None
pets_data_sorted = None
formations_data = None
race_config = None
pet_dict = None
bilibili_videos = None
element_detail_cache = None
pet_by_id_cache = None

# 文件最后修改时间，用于自动重载
_last_load_time = 0
_file_mtimes = {}

def get_file_mtime(path):
    try:
        return os.path.getmtime(path)
    except:
        return 0

def check_files_changed():
    global _file_mtimes
    files = [PETS_PATH, FORMATIONS_PATH, RACE_CONFIG_PATH, PET_DICT_PATH, BILIBILI_VIDEOS_PATH]
    for f in files:
        current_mtime = get_file_mtime(f)
        if _file_mtimes.get(f) != current_mtime:
            return True
    return False

def load_data(force=False):
    global pets_data, pets_data_sorted, formations_data, race_config, pet_dict, bilibili_videos, _last_load_time, _file_mtimes, element_detail_cache, pet_by_id_cache
    
    if not force and not check_files_changed():
        return
    
    _last_load_time = time.time()
    _file_mtimes = {
        PETS_PATH: get_file_mtime(PETS_PATH),
        FORMATIONS_PATH: get_file_mtime(FORMATIONS_PATH),
        RACE_CONFIG_PATH: get_file_mtime(RACE_CONFIG_PATH),
        PET_DICT_PATH: get_file_mtime(PET_DICT_PATH),
        BILIBILI_VIDEOS_PATH: get_file_mtime(BILIBILI_VIDEOS_PATH),
    }
    
    with open(PETS_PATH, 'r', encoding='utf-8') as f:
        pets_data = json.load(f)
    
    try:
        with open(FORMATIONS_PATH, 'r', encoding='utf-8') as f:
            formations_data = json.load(f)
    except:
        formations_data = []
    
    with open(RACE_CONFIG_PATH, 'r', encoding='utf-8') as f:
        race_config = json.load(f)
    
    try:
        with open(PET_DICT_PATH, 'r', encoding='utf-8') as f:
            pet_dict = json.load(f)
    except:
        pet_dict = {}
    
    try:
        with open(BILIBILI_VIDEOS_PATH, 'r', encoding='utf-8') as f:
            bilibili_videos = json.load(f)
    except:
        bilibili_videos = {}
    
    # 预排序 - 按 raceId 倒序
    pets_data_sorted = sorted(pets_data, key=lambda x: x['raceId'], reverse=True)
    
    # 预构建精灵ID映射
    pet_by_id_cache = {p['raceId']: p for p in pets_data}
    
    # 预构建属性详情缓存
    race_types = race_config.get('race_type', [])
    element_detail_cache = {}
    for r in race_types:
        name = r['nm']
        target_id = r['id']
        rcs = r.get('rcs', [])
        counters = []
        weaknesses = []
        all_multipliers = []
        for i, rt in enumerate(race_types):
            if i < len(rcs):
                mult = float(rcs[i])
                all_multipliers.append({'element': rt['nm'], 'multiplier': mult})
                if mult > 1.0:
                    counters.append({'element': rt['nm'], 'multiplier': mult})
                elif mult < 1.0:
                    weaknesses.append({'element': rt['nm'], 'multiplier': mult})
        counters.sort(key=lambda x: x['multiplier'], reverse=True)
        weaknesses.sort(key=lambda x: x['multiplier'])
        element_detail_cache[name] = {
            'name': name,
            'id': target_id,
            'counters': counters,
            'weaknesses': weaknesses,
            'multipliers': all_multipliers
        }

load_data()

@app.route('/api/pets', methods=['GET'])
def get_pets():
    load_data()
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('pageSize', 20))
    keyword = request.args.get('keyword', '')
    element = request.args.get('element', '')
    
    filtered = pets_data_sorted
    
    if keyword:
        keyword = keyword.lower()
        filtered = [p for p in filtered if keyword in p['name'].lower()]
    
    if element:
        element = element.lower()
        filtered = [p for p in filtered if element in (p.get('elementTypeName', '').lower())]
    
    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    
    return jsonify({
        'data': filtered[start:end],
        'total': total,
        'page': page,
        'pageSize': page_size
    })

@app.route('/api/pet/<int:race_id>', methods=['GET'])
def get_pet(race_id):
    load_data()
    pet = pet_by_id_cache.get(race_id)
    if pet:
        return jsonify(pet)
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/pet/search', methods=['GET'])
def search_pet():
    load_data()
    keyword = request.args.get('keyword', '')
    if not keyword:
        return jsonify([])
    
    results = []
    for p in pets_data:
        if keyword.lower() in p['name'].lower():
            results.append({
                'raceId': p['raceId'],
                'name': p['name'],
                'elementTypeName': p.get('elementTypeName', ''),
                'jobCategory': p.get('jobCategory', '')
            })
    
    return jsonify(results[:20])

@app.route('/api/elements', methods=['GET'])
def get_elements():
    load_data()
    race_types = race_config.get('race_type', [])
    elements = []
    for r in race_types:
        elements.append({
            'id': r['id'],
            'name': r['nm']
        })
    return jsonify(elements)

@app.route('/api/element/<string:element_name>', methods=['GET'])
def get_element_detail(element_name):
    load_data()
    detail = element_detail_cache.get(element_name)
    if not detail:
        return jsonify({'error': 'Element not found'}), 404
    return jsonify(detail)

@app.route('/api/elements/matrix', methods=['GET'])
def get_elements_matrix():
    load_data()
    return jsonify(list(element_detail_cache.values()))

@app.route('/api/recommend', methods=['POST'])
def recommend_team():
    load_data()
    data = request.get_json()
    enemy_pets = data.get('enemies', [])
    team_size = data.get('teamSize', 6)
    
    if not enemy_pets:
        return jsonify({'error': 'No enemies provided'}), 400
    
    race_map = {r['nm']: r for r in race_config.get('race_type', [])}
    
    enemy_elements = []
    for ep in enemy_pets:
        pet = next((p for p in pets_data if p['raceId'] == ep), None)
        if pet:
            elem = pet.get('elementTypeName', '')
            if elem:
                enemy_elements.append(elem)
    
    if not enemy_elements:
        return jsonify({'error': 'Cannot analyze enemy elements'}), 400
    
    pet_scores = []
    for pet in pets_data:
        pet_elem = pet.get('elementTypeName', '')
        if not pet_elem:
            continue
        
        total_mult = 0
        counter_count = 0
        max_mult = 0
        
        for e_elem in enemy_elements:
            mult = 1.0
            if pet_elem in race_map and e_elem in race_map:
                attacker = race_map[pet_elem]
                defender = race_map[e_elem]
                def_id = defender['id']
                rcs = attacker.get('rcs', [])
                for i, r in enumerate(race_config.get('race_type', [])):
                    if r['id'] == def_id and i < len(rcs):
                        mult = float(rcs[i])
                        break
            total_mult += mult
            if mult > 1.0:
                counter_count += 1
            if mult > max_mult:
                max_mult = mult
        
        avg_mult = total_mult / len(enemy_elements)
        weak_count = sum(1 for e in enemy_elements if 
                         get_multiplier(pet_elem, e, race_map, race_config) < 1.0)
        
        score = avg_mult * 100 + counter_count * 10 + (max_mult - 1) * 50 - weak_count * 15
        
        job = pet.get('jobCategory', '')
        job_bonus = {
            '灵初超级英雄': 10,
            '灵初神通灵师': 9,
            '灵初神攻': 8,
            '灵初神召唤师': 7,
            '灵初神速': 6,
            '灵初神英雄': 5,
            '灵初神平衡': 4,
            '灵初神巨人': 3,
            '灵初神盾': 2,
            '灵初基础职业': 1,
        }
        score += job_bonus.get(job, 2)
        
        pet_scores.append({
            'pet': pet,
            'score': score,
            'avg_multiplier': avg_mult,
            'counter_count': counter_count,
            'max_multiplier': max_mult
        })
    
    pet_scores.sort(key=lambda x: x['score'], reverse=True)
    
    selected = []
    selected_elements = set()
    selected_jobs = set()
    
    for ps in pet_scores:
        if len(selected) >= team_size:
            break
        elem = ps['pet'].get('elementTypeName', '')
        job = ps['pet'].get('jobCategory', '')
        if elem not in selected_elements:
            selected.append(ps)
            selected_elements.add(elem)
            selected_jobs.add(job)
    
    for ps in pet_scores:
        if len(selected) >= team_size:
            break
        if ps in selected:
            continue
        job = ps['pet'].get('jobCategory', '')
        if job not in selected_jobs:
            selected.append(ps)
            selected_jobs.add(job)
    
    for ps in pet_scores:
        if len(selected) >= team_size:
            break
        if ps not in selected:
            selected.append(ps)
    
    team_score = sum(ps['score'] for ps in selected)
    
    return jsonify({
        'recommended_pets': [ps['pet'] for ps in selected],
        'pet_scores': pet_scores[:20],
        'team_score': team_score,
        'enemy_elements': enemy_elements,
        'reasoning': generate_reasoning(enemy_elements, selected)
    })

def get_multiplier(attacker, defender, race_map, config):
    if attacker not in race_map or defender not in race_map:
        return 1.0
    a = race_map[attacker]
    d = race_map[defender]
    rcs = a.get('rcs', [])
    for i, r in enumerate(config.get('race_type', [])):
        if r['id'] == d['id'] and i < len(rcs):
            return float(rcs[i])
    return 1.0

def generate_reasoning(enemy_elements, selected):
    lines = []
    lines.append(f'敌方属性: {", ".join(enemy_elements)}')
    lines.append('')
    lines.append(f'推荐阵容（共{len(selected)}只）:')
    for i, ps in enumerate(selected, 1):
        pet = ps['pet']
        elem = pet.get('elementTypeName', '')
        job = pet.get('jobCategory', '')
        tag = f'克制{ps["counter_count"]}种' if ps['counter_count'] > 0 else ''
        lines.append(f'  {i}. {pet["name"]} ({elem}/{job}) - 评分:{ps["score"]:.1f} {tag}')
    return '\n'.join(lines)

@app.route('/api/challenges', methods=['GET'])
def get_challenges():
    load_data()
    keyword = request.args.get('keyword', '')
    
    filtered = formations_data
    if keyword:
        keyword = keyword.lower()
        filtered = [c for c in filtered if keyword in (c.get('petName', '').lower() or c.get('file', '').lower())]
    
    result = []
    for c in filtered[:50]:
        result.append({
            'id': c.get('file', '') or c.get('petName', ''),
            'name': c.get('petName', '') or c.get('file', ''),
            'date': c.get('date', ''),
            'levelCount': len(c.get('formations', {}))
        })
    
    return jsonify(result)

@app.route('/api/challenge/<string:name>', methods=['GET'])
def get_challenge(name):
    load_data()
    challenge = next((c for c in formations_data if 
                      (c.get('petName') == name or c.get('file') == name)), None)
    
    if not challenge:
        return jsonify({'error': 'Not found'}), 404
    
    return jsonify(challenge)

@app.route('/api/compare', methods=['POST'])
def compare_pets():
    load_data()
    data = request.get_json()
    pet_ids = data.get('petIds', [])
    
    if len(pet_ids) < 2:
        return jsonify({'error': 'Need at least 2 pets'}), 400
    
    pets = []
    for pid in pet_ids:
        pet = next((p for p in pets_data if p['raceId'] == pid), None)
        if pet:
            pets.append(pet)
    
    race_map = {r['nm']: r for r in race_config.get('race_type', [])}
    
    matrix = []
    for pa in pets:
        row = []
        for pb in pets:
            if pa['raceId'] == pb['raceId']:
                row.append({'multiplier': 1.0, 'relation': 'same'})
            else:
                ea = pa.get('elementTypeName', '')
                eb = pb.get('elementTypeName', '')
                mult = get_multiplier(ea, eb, race_map, race_config)
                relation = 'counter' if mult > 1 else ('weak' if mult < 1 else 'neutral')
                row.append({'multiplier': mult, 'relation': relation})
        matrix.append(row)
    
    return jsonify({
        'pets': pets,
        'matrix': matrix
    })

def clean_spirit_name(name):
    return name.replace('[灵初]', '').replace('[星迹]', '').replace('[神运]', '').replace('[启元]', '').replace('[传说]', '')

@app.route('/api/videos', methods=['GET'])
def get_videos():
    load_data()
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('pageSize', 20))
    keyword = request.args.get('keyword', '')
    race_id = request.args.get('raceId', '')
    
    result = []
    
    def enrich_video(v):
        v_copy = dict(v)
        # 优先使用视频中自带的精灵信息，避免标签错误
        if 'spiritRaceId' in v_copy and v_copy['spiritRaceId']:
            pet = next((p for p in pets_data if str(p['raceId']) == str(v_copy['spiritRaceId'])), None)
        else:
            pet = None
        if 'spiritName' not in v_copy or not v_copy['spiritName']:
            v_copy['spiritName'] = clean_spirit_name(pet['name']) if pet else ''
        if 'spiritRaceId' not in v_copy or not v_copy['spiritRaceId']:
            v_copy['spiritRaceId'] = pet['raceId'] if pet else 0
        return v_copy
    
    if race_id:
        videos = bilibili_videos.get(str(race_id), [])
        for v in videos:
            result.append(enrich_video(v))
    elif keyword:
        keyword_lower = keyword.lower()
        # 支持按精灵名称、视频标题、作者搜索
        for pet in pets_data:
            pet_videos = bilibili_videos.get(str(pet['raceId']), [])
            for v in pet_videos:
                v_copy = enrich_video(v)
                title = v_copy.get('title', '').lower()
                author = v_copy.get('author', '').lower()
                spirit_name = v_copy.get('spiritName', '').lower()
                pet_name = pet['name'].lower()
                if (keyword_lower in pet_name or
                    keyword_lower in spirit_name or
                    keyword_lower in title or
                    keyword_lower in author):
                    result.append(v_copy)
    else:
        for pet in pets_data:
            videos = bilibili_videos.get(str(pet['raceId']), [])
            for v in videos:
                result.append(enrich_video(v))
    
    result.sort(key=lambda x: x.get('play', 0), reverse=True)
    
    total = len(result)
    start = (page - 1) * page_size
    end = start + page_size
    
    return jsonify({
        'data': result[start:end],
        'total': total,
        'page': page,
        'pageSize': page_size
    })

@app.route('/api/videos/pet/<int:race_id>', methods=['GET'])
def get_pet_videos(race_id):
    load_data()
    videos = bilibili_videos.get(str(race_id), [])
    return jsonify({
        'raceId': race_id,
        'videos': videos
    })

def save_bilibili_videos():
    global bilibili_videos
    os.makedirs(os.path.dirname(BILIBILI_VIDEOS_PATH), exist_ok=True)
    with open(BILIBILI_VIDEOS_PATH, 'w', encoding='utf-8') as f:
        json.dump(bilibili_videos, f, ensure_ascii=False, indent=2)

@app.route('/api/videos/pet/<int:race_id>', methods=['POST'])
@login_required
def add_pet_video(race_id):
    load_data()
    user = g.current_user
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    title = data.get('title', '').strip()
    url = data.get('url', '').strip()
    author = data.get('author', '').strip()
    note = data.get('note', '').strip()
    pic = data.get('pic', '').strip()
    levels = data.get('levels', [])
    tags = data.get('tags', [])
    
    if not title or not url:
        return jsonify({'error': 'Title and URL are required'}), 400
    
    import re
    bvid_match = re.search(r'(BV[\w]+)', url)
    video_id = bvid_match.group(1) if bvid_match else f'custom_{int(time.time())}'
    
    pet = next((p for p in pets_data if p['raceId'] == race_id), None)
    pet_name = clean_spirit_name(pet['name']) if pet else ''
    
    video = {
        'bvid': video_id,
        'title': title,
        'url': url,
        'author': author,
        'note': note,
        'play': 0,
        'danmaku': 0,
        'favorites': 0,
        'pubdate': int(time.time()),
        'duration': '',
        'pic': pic,
        'keyword': 'manual',
        'spiritRaceId': race_id,
        'spiritName': pet_name,
        'manual': True,
        'createdAt': int(time.time()),
        'levels': levels,
        'tags': tags,
        'userId': user['user_id'],
        'uploader': user['username'],
    }
    
    race_key = str(race_id)
    if race_key not in bilibili_videos:
        bilibili_videos[race_key] = []
    
    existing = next((v for v in bilibili_videos[race_key] if v.get('bvid') == video_id or v.get('url') == url), None)
    if existing:
        return jsonify({'error': 'Video already exists for this pet'}), 409
    
    bilibili_videos[race_key].append(video)
    save_bilibili_videos()
    
    return jsonify({
        'success': True,
        'video': video
    })

@app.route('/api/videos/pet/<int:race_id>/<string:video_id>', methods=['PUT'])
@login_required
def update_pet_video(race_id, video_id):
    load_data()
    user = g.current_user
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    race_key = str(race_id)
    if race_key not in bilibili_videos:
        return jsonify({'error': 'Pet not found'}), 404
    
    video = next((v for v in bilibili_videos[race_key] if v.get('bvid') == video_id or v.get('url') == video_id), None)
    if not video:
        return jsonify({'error': 'Video not found'}), 404
    
    if not can_manage_content(video.get('userId')):
        return jsonify({'error': '无权限修改此视频'}), 403
    
    if 'title' in data:
        video['title'] = data['title'].strip()
    if 'author' in data:
        video['author'] = data['author'].strip()
    if 'note' in data:
        video['note'] = data['note'].strip()
    if 'pic' in data:
        video['pic'] = data['pic'].strip()
    if 'url' in data:
        video['url'] = data['url'].strip()
    if 'levels' in data:
        video['levels'] = data['levels']
    if 'tags' in data:
        video['tags'] = data['tags']

    video['updatedAt'] = int(time.time())
    save_bilibili_videos()
    
    return jsonify({
        'success': True,
        'video': video
    })

@app.route('/api/videos/pet/<int:race_id>/<string:video_id>', methods=['DELETE'])
@login_required
def delete_pet_video(race_id, video_id):
    load_data()
    user = g.current_user
    race_key = str(race_id)
    if race_key not in bilibili_videos:
        return jsonify({'error': 'Pet not found'}), 404
    
    video = next((v for v in bilibili_videos[race_key] if v.get('bvid') == video_id or v.get('url') == video_id), None)
    if not video:
        return jsonify({'error': 'Video not found'}), 404
    
    if not can_manage_content(video.get('userId')):
        return jsonify({'error': '无权限删除此视频'}), 403
    
    before = len(bilibili_videos[race_key])
    bilibili_videos[race_key] = [v for v in bilibili_videos[race_key] if v.get('bvid') != video_id and v.get('url') != video_id]
    after = len(bilibili_videos[race_key])
    
    if before == after:
        return jsonify({'error': 'Video not found'}), 404
    
    save_bilibili_videos()
    log_action('delete_video', 'video', None, f'race_id={race_id}, video_id={video_id}')
    return jsonify({'success': True})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    load_data()
    video_count = sum(len(v) for v in bilibili_videos.values())
    return jsonify({
        'totalPets': len(pets_data),
        'iconPets': sum(1 for p in pets_data if p.get('hasIcon')),
        'totalChallenges': len(formations_data),
        'totalElements': len(race_config.get('race_type', [])),
        'totalVideos': video_count
    })

@app.route('/api/bilibili-image')
def bilibili_image_proxy():
    url = request.args.get('url', '')
    if not url:
        return '', 400
    if url.startswith('//'):
        url = 'https:' + url
    try:
        parsed = urlparse(url)
        if 'hdslb.com' not in parsed.netloc and 'bilibili.com' not in parsed.netloc:
            return '', 400
        headers = {
            'Referer': 'https://www.bilibili.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        resp = requests.get(url, headers=headers, timeout=10)
        return resp.content, 200, {'Content-Type': resp.headers.get('Content-Type', 'image/jpeg')}
    except Exception as e:
        return '', 500

@app.route('/api/bilibili/video-info', methods=['GET'])
def bilibili_video_info():
    url = request.args.get('url', '').strip()
    bvid = request.args.get('bvid', '').strip()
    
    if not bvid and url:
        import re
        bvid_match = re.search(r'(BV[\w]+)', url)
        if bvid_match:
            bvid = bvid_match.group(1)
    
    if not bvid:
        return jsonify({'error': '无法从链接中提取 BV 号'}), 400
    
    try:
        api_url = f'https://api.bilibili.com/x/web-interface/view?bvid={bvid}'
        headers = {
            'Referer': 'https://www.bilibili.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        resp = requests.get(api_url, headers=headers, timeout=10)
        data = resp.json()
        
        if data.get('code') != 0:
            return jsonify({'error': data.get('message', '获取视频信息失败')}), 400
        
        video_data = data.get('data', {})
        pic = video_data.get('pic', '')
        if pic and pic.startswith('//'):
            pic = 'https:' + pic
        
        return jsonify({
            'bvid': bvid,
            'title': video_data.get('title', ''),
            'author': video_data.get('owner', {}).get('name', ''),
            'pic': pic,
            'desc': video_data.get('desc', ''),
            'duration': video_data.get('duration', 0),
            'play': video_data.get('stat', {}).get('view', 0),
            'danmaku': video_data.get('stat', {}).get('danmaku', 0),
        })
    except Exception as e:
        return jsonify({'error': f'获取视频信息失败: {str(e)}'}), 500

@app.route('/')
def index():
    return send_from_directory('../frontend', 'index.html')

@app.route('/css/<path:path>')
def send_css(path):
    return send_from_directory('../frontend/css', path)

@app.route('/js/<path:path>')
def send_js(path):
    return send_from_directory('../frontend/js', path)

@app.route('/assets/<path:path>')
def send_assets(path):
    return send_from_directory('../frontend/assets', path)

@app.route('/uploads/<path:path>')
def send_uploads(path):
    return send_from_directory('../frontend/uploads', path)

@app.route('/image.jpg')
def send_bg_image():
    return send_from_directory('../frontend', 'image.jpg')

@app.route('/images/peticon/<int:race_id>')
def send_peticon(race_id):
    peticon_root = os.path.join(AOQI_BASE, 'output', 'peticon')
    for subdir in ['fang', 'large', 'static', 'fight', 'rectangle']:
        path = os.path.join(peticon_root, subdir, f'peticon{race_id}.png')
        if os.path.exists(path):
            return send_from_directory(os.path.join(peticon_root, subdir), f'peticon{race_id}.png')
    
    web_path = fetch_peticon_from_web(race_id)
    if web_path:
        return send_from_directory(os.path.dirname(web_path), os.path.basename(web_path))
    
    ba_path = os.path.join(AOQI_BASE, 'output', 'battleaction', f'action{race_id}.png')
    if os.path.exists(ba_path):
        return send_from_directory(os.path.join(AOQI_BASE, 'output', 'battleaction'), f'action{race_id}.png')
    
    return '', 404

@app.route('/hybridaction/<path:path>')
def hybrid_action(path):
    callback = request.args.get('callback', '')
    if callback:
        return f'{callback}({json.dumps({})})', 200, {'Content-Type': 'application/javascript'}
    return jsonify({}), 200

# ==================== 用户认证 API ====================

@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400
    if len(username) < 3 or len(username) > 32:
        return jsonify({'error': '用户名长度需在3-32个字符之间'}), 400
    if len(password) < 6:
        return jsonify({'error': '密码长度不能少于6位'}), 400

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id FROM users WHERE username = %s', (username,))
            if cursor.fetchone():
                return jsonify({'error': '用户名已存在'}), 409

            pwd_hash = hash_password(password)
            cursor.execute(
                'INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)',
                (username, pwd_hash, ROLE_USER)
            )
            user_id = cursor.lastrowid

        token = generate_token(user_id, username, ROLE_USER)
        return jsonify({
            'success': True,
            'token': token,
            'user': {
                'id': user_id,
                'username': username,
                'role': ROLE_USER
            }
        })
    finally:
        conn.close()

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
            user = cursor.fetchone()
            if not user or not verify_password(password, user['password_hash']):
                return jsonify({'error': '用户名或密码错误'}), 401

            token = generate_token(user['id'], user['username'], user['role'])
            return jsonify({
                'success': True,
                'token': token,
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'role': user['role'],
                    'avatar': user.get('avatar')
                }
            })
    finally:
        conn.close()

@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    user = get_current_user()
    if not user:
        return jsonify({'error': '未登录'}), 401

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id, username, role, avatar, created_at FROM users WHERE id = %s', (user['user_id'],))
            db_user = cursor.fetchone()
            if not db_user:
                return jsonify({'error': '用户不存在'}), 404
            return jsonify(db_user)
    finally:
        conn.close()

@app.route('/api/auth/users', methods=['GET'])
@role_required(ROLE_SUPER_ADMIN)
def auth_user_list():
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('pageSize', 20))
    keyword = request.args.get('keyword', '')

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            where = []
            params = []
            if keyword:
                where.append('username LIKE %s')
                params.append(f'%{keyword}%')
            where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''

            cursor.execute(f'SELECT COUNT(*) as total FROM users{where_sql}', params)
            total = cursor.fetchone()['total']

            offset = (page - 1) * page_size
            cursor.execute(
                f'SELECT id, username, role, avatar, created_at FROM users{where_sql} ORDER BY id DESC LIMIT %s OFFSET %s',
                params + [page_size, offset]
            )
            users = cursor.fetchall()

            return jsonify({
                'data': users,
                'total': total,
                'page': page,
                'pageSize': page_size
            })
    finally:
        conn.close()

@app.route('/api/auth/user/<int:user_id>/role', methods=['PUT'])
@role_required(ROLE_SUPER_ADMIN)
def auth_update_role(user_id):
    data = request.get_json() or {}
    new_role = data.get('role', '').strip()

    if new_role not in [ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_USER]:
        return jsonify({'error': '无效的角色'}), 400

    current_user = g.current_user
    if current_user['user_id'] == user_id:
        return jsonify({'error': '不能修改自己的角色'}), 400

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id FROM users WHERE id = %s', (user_id,))
            if not cursor.fetchone():
                return jsonify({'error': '用户不存在'}), 404

            cursor.execute('UPDATE users SET role = %s WHERE id = %s', (new_role, user_id))
            log_action('update_role', 'user', user_id, f'角色修改为 {new_role}')
            return jsonify({'success': True, 'message': '角色更新成功'})
    finally:
        conn.close()

@app.route('/api/auth/avatar', methods=['POST'])
@login_required
def auth_upload_avatar():
    if 'file' not in request.files:
        return jsonify({'error': '没有上传文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '未选择文件'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': '不支持的图片格式'}), 400

    user = g.current_user
    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"avatar_{user['user_id']}_{uuid.uuid4().hex[:8]}.{ext}"
    avatar_dir = os.path.join(UPLOAD_FOLDER, 'avatars')
    os.makedirs(avatar_dir, exist_ok=True)
    filepath = os.path.join(avatar_dir, filename)
    file.save(filepath)

    avatar_url = f'/uploads/avatars/{filename}'
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('UPDATE users SET avatar = %s WHERE id = %s', (avatar_url, user['user_id']))
    finally:
        conn.close()

    return jsonify({'success': True, 'avatar': avatar_url})

@app.route('/api/auth/password', methods=['PUT'])
@login_required
def auth_change_password():
    data = request.get_json() or {}
    old_password = (data.get('old_password') or '').strip()
    new_password = (data.get('new_password') or '').strip()

    if not old_password or not new_password:
        return jsonify({'error': '旧密码和新密码不能为空'}), 400
    if len(new_password) < 6:
        return jsonify({'error': '新密码长度不能少于6位'}), 400

    user = g.current_user
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT password_hash FROM users WHERE id = %s', (user['user_id'],))
            db_user = cursor.fetchone()
            if not db_user or not verify_password(old_password, db_user['password_hash']):
                return jsonify({'error': '旧密码错误'}), 400

            new_hash = hash_password(new_password)
            cursor.execute('UPDATE users SET password_hash = %s WHERE id = %s', (new_hash, user['user_id']))
            log_action('change_password', 'user', user['user_id'])
            return jsonify({'success': True, 'message': '密码修改成功'})
    finally:
        conn.close()

@app.route('/api/auth/user/<int:user_id>/profile', methods=['GET'])
def auth_user_profile(user_id):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id, username, role, avatar, created_at FROM users WHERE id = %s', (user_id,))
            user = cursor.fetchone()
            if not user:
                return jsonify({'error': '用户不存在'}), 404

            cursor.execute('SELECT COUNT(*) as cnt FROM posts WHERE user_id = %s', (user_id,))
            post_count = cursor.fetchone()['cnt']
            cursor.execute('SELECT COUNT(*) as cnt FROM comments WHERE user_id = %s', (user_id,))
            comment_count = cursor.fetchone()['cnt']

            page = int(request.args.get('page', 1))
            page_size = int(request.args.get('pageSize', 10))
            tab = request.args.get('tab', 'posts')
            offset = (page - 1) * page_size

            if tab == 'comments':
                cursor.execute(
                    'SELECT c.id, c.post_id, c.content, c.created_at, p.title as post_title FROM comments c LEFT JOIN posts p ON c.post_id = p.id WHERE c.user_id = %s ORDER BY c.created_at DESC LIMIT %s OFFSET %s',
                    (user_id, page_size, offset)
                )
                items = cursor.fetchall()
                total = comment_count
            else:
                cursor.execute(
                    'SELECT id, title, category, likes, views, comment_count, is_pinned, is_featured, created_at FROM posts WHERE user_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s',
                    (user_id, page_size, offset)
                )
                items = cursor.fetchall()
                total = post_count

            user['post_count'] = post_count
            user['comment_count'] = comment_count
            return jsonify({
                'user': user,
                'items': items,
                'total': total,
                'page': page,
                'pageSize': page_size
            })
    finally:
        conn.close()

# ==================== 论坛 API ====================

POST_CATEGORIES = ['综合讨论', '攻略分享', '精灵培养', '阵容搭配', '问题求助', '闲聊灌水']

@app.route('/api/forum/categories', methods=['GET'])
def forum_categories():
    return jsonify(POST_CATEGORIES)

@app.route('/api/forum/posts', methods=['GET'])
def forum_posts():
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('pageSize', 20))
    category = request.args.get('category', '')
    keyword = request.args.get('keyword', '')
    sort_by = request.args.get('sort', 'latest')

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            where = []
            params = []
            if category:
                where.append('category = %s')
                params.append(category)
            if keyword:
                where.append('(title LIKE %s OR content LIKE %s)')
                params.extend([f'%{keyword}%', f'%{keyword}%'])
            where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''

            order_sql = 'ORDER BY is_pinned DESC, created_at DESC'
            if sort_by == 'hot':
                order_sql = 'ORDER BY is_pinned DESC, (likes * 2 + comment_count * 3 + views) DESC, created_at DESC'
            elif sort_by == 'likes':
                order_sql = 'ORDER BY is_pinned DESC, likes DESC, created_at DESC'

            count_sql = f'SELECT COUNT(*) as total FROM posts{where_sql}'
            cursor.execute(count_sql, params)
            total = cursor.fetchone()['total']

            offset = (page - 1) * page_size
            data_sql = f'''
                SELECT id, user_id, title, LEFT(content, 120) as excerpt, author,
                       category, likes, views, comment_count, images, is_pinned, is_featured, created_at, updated_at
                FROM posts{where_sql}
                {order_sql}
                LIMIT %s OFFSET %s
            '''
            cursor.execute(data_sql, params + [page_size, offset])
            posts = cursor.fetchall()

            return jsonify({
                'data': posts,
                'total': total,
                'page': page,
                'pageSize': page_size
            })
    finally:
        conn.close()

@app.route('/api/forum/post/<int:post_id>', methods=['GET'])
def forum_post_detail(post_id):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('UPDATE posts SET views = views + 1 WHERE id = %s', (post_id,))
            cursor.execute('SELECT * FROM posts WHERE id = %s', (post_id,))
            post = cursor.fetchone()
            if not post:
                return jsonify({'error': 'Post not found'}), 404
            return jsonify(post)
    finally:
        conn.close()

@app.route('/api/forum/post', methods=['POST'])
@login_required
def forum_create_post():
    data = request.get_json() or {}
    user = g.current_user
    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()
    category = (data.get('category') or '综合讨论').strip()
    images = data.get('images') or []

    if not title or not content:
        return jsonify({'error': '标题和内容不能为空'}), 400
    if len(title) > 255:
        return jsonify({'error': '标题不能超过255字'}), 400
    if category not in POST_CATEGORIES:
        category = '综合讨论'

    images_json = json.dumps(images) if images and len(images) > 0 else None

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'INSERT INTO posts (user_id, title, content, author, category, images) VALUES (%s, %s, %s, %s, %s, %s)',
                (user['user_id'], title, content, user['username'], category, images_json)
            )
            post_id = cursor.lastrowid
            return jsonify({'id': post_id, 'message': '发布成功'})
    finally:
        conn.close()

@app.route('/api/forum/post/<int:post_id>', methods=['PUT'])
@login_required
def forum_update_post(post_id):
    data = request.get_json() or {}
    user = g.current_user
    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()
    category = (data.get('category') or '').strip()
    images = data.get('images')

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM posts WHERE id = %s', (post_id,))
            post = cursor.fetchone()
            if not post:
                return jsonify({'error': '帖子不存在'}), 404

            if not can_manage_content(post['user_id']):
                return jsonify({'error': '无权限修改此帖子'}), 403

            updates = []
            params = []
            if title:
                if len(title) > 255:
                    return jsonify({'error': '标题不能超过255字'}), 400
                updates.append('title = %s')
                params.append(title)
            if content:
                updates.append('content = %s')
                params.append(content)
            if category and category in POST_CATEGORIES:
                updates.append('category = %s')
                params.append(category)
            if images is not None:
                updates.append('images = %s')
                params.append(json.dumps(images) if images and len(images) > 0 else None)

            if not updates:
                return jsonify({'error': '没有更新内容'}), 400

            params.append(post_id)
            cursor.execute(f'UPDATE posts SET {", ".join(updates)} WHERE id = %s', params)
            return jsonify({'success': True, 'message': '更新成功'})
    finally:
        conn.close()

@app.route('/api/forum/post/<int:post_id>', methods=['DELETE'])
@login_required
def forum_delete_post(post_id):
    user = g.current_user
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT user_id FROM posts WHERE id = %s', (post_id,))
            post = cursor.fetchone()
            if not post:
                return jsonify({'error': '帖子不存在'}), 404

            if not can_manage_content(post['user_id']):
                return jsonify({'error': '无权限删除此帖子'}), 403

            cursor.execute('DELETE FROM posts WHERE id = %s', (post_id,))
            log_action('delete_post', 'post', post_id)
            return jsonify({'success': True, 'message': '删除成功'})
    finally:
        conn.close()

@app.route('/api/forum/post/<int:post_id>/like', methods=['POST'])
def forum_like_post(post_id):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('UPDATE posts SET likes = likes + 1 WHERE id = %s', (post_id,))
            if cursor.rowcount == 0:
                return jsonify({'error': 'Post not found'}), 404
            cursor.execute('SELECT likes FROM posts WHERE id = %s', (post_id,))
            return jsonify({'likes': cursor.fetchone()['likes']})
    finally:
        conn.close()

@app.route('/api/forum/post/<int:post_id>/comments', methods=['GET'])
def forum_comments(post_id):
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('pageSize', 50))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) as total FROM comments WHERE post_id = %s', (post_id,))
            total = cursor.fetchone()['total']

            offset = (page - 1) * page_size
            cursor.execute(
                'SELECT id, user_id, author, content, likes, images, parent_id, created_at FROM comments WHERE post_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s',
                (post_id, page_size, offset)
            )
            comments = cursor.fetchall()

            return jsonify({
                'data': comments,
                'total': total,
                'page': page,
                'pageSize': page_size
            })
    finally:
        conn.close()

@app.route('/api/forum/post/<int:post_id>/comment', methods=['POST'])
@login_required
def forum_add_comment(post_id):
    data = request.get_json() or {}
    user = g.current_user
    content = (data.get('content') or '').strip()
    images = data.get('images') or []
    parent_id = data.get('parent_id')

    if not content:
        return jsonify({'error': '评论内容不能为空'}), 400

    images_json = json.dumps(images) if images and len(images) > 0 else None

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id, user_id, title FROM posts WHERE id = %s', (post_id,))
            post = cursor.fetchone()
            if not post:
                return jsonify({'error': '帖子不存在'}), 404

            cursor.execute(
                'INSERT INTO comments (post_id, user_id, content, author, images, parent_id) VALUES (%s, %s, %s, %s, %s, %s)',
                (post_id, user['user_id'], content, user['username'], images_json, parent_id)
            )
            comment_id = cursor.lastrowid
            cursor.execute('UPDATE posts SET comment_count = comment_count + 1 WHERE id = %s', (post_id,))

            # 通知帖子作者
            if post['user_id'] and post['user_id'] != user['user_id']:
                create_notification(
                    post['user_id'], 'comment',
                    '你的帖子收到了新评论',
                    f'{user["username"]} 评论了你的帖子「{post["title"]}」',
                    f'/forum/post/{post_id}'
                )

            # 如果是回复评论，通知被回复的评论作者
            if parent_id:
                cursor.execute('SELECT user_id FROM comments WHERE id = %s', (parent_id,))
                parent_comment = cursor.fetchone()
                if parent_comment and parent_comment['user_id'] and parent_comment['user_id'] != user['user_id']:
                    create_notification(
                        parent_comment['user_id'], 'reply',
                        '你的评论收到了回复',
                        f'{user["username"]} 回复了你的评论',
                        f'/forum/post/{post_id}'
                    )

            return jsonify({'id': comment_id, 'message': '评论成功'})
    finally:
        conn.close()

@app.route('/api/forum/comment/<int:comment_id>', methods=['DELETE'])
@login_required
def forum_delete_comment(comment_id):
    user = g.current_user
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM comments WHERE id = %s', (comment_id,))
            comment = cursor.fetchone()
            if not comment:
                return jsonify({'error': '评论不存在'}), 404

            if not can_manage_content(comment['user_id']):
                return jsonify({'error': '无权限删除此评论'}), 403

            cursor.execute('DELETE FROM comments WHERE id = %s', (comment_id,))
            cursor.execute('UPDATE posts SET comment_count = comment_count - 1 WHERE id = %s', (comment['post_id'],))
            log_action('delete_comment', 'comment', comment_id)
            return jsonify({'success': True, 'message': '删除成功'})
    finally:
        conn.close()

@app.route('/api/forum/comment/<int:comment_id>/like', methods=['POST'])
def forum_like_comment(comment_id):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('UPDATE comments SET likes = likes + 1 WHERE id = %s', (comment_id,))
            if cursor.rowcount == 0:
                return jsonify({'error': 'Comment not found'}), 404
            cursor.execute('SELECT likes FROM comments WHERE id = %s', (comment_id,))
            return jsonify({'likes': cursor.fetchone()['likes']})
    finally:
        conn.close()

@app.route('/api/forum/stats', methods=['GET'])
def forum_stats():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) as totalPosts FROM posts')
            total_posts = cursor.fetchone()['totalPosts']
            cursor.execute('SELECT COUNT(*) as totalComments FROM comments')
            total_comments = cursor.fetchone()['totalComments']
            cursor.execute('''
                SELECT COUNT(*) as todayPosts FROM posts
                WHERE DATE(created_at) = CURDATE()
            ''')
            today_posts = cursor.fetchone()['todayPosts']
            return jsonify({
                'totalPosts': total_posts,
                'totalComments': total_comments,
                'todayPosts': today_posts
            })
    finally:
        conn.close()

# ==================== 论坛增强 API ====================

@app.route('/api/forum/post/<int:post_id>/pin', methods=['PUT'])
@role_required(ROLE_ADMIN)
def forum_pin_post(post_id):
    data = request.get_json() or {}
    is_pinned = 1 if data.get('is_pinned') else 0

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id, user_id, title FROM posts WHERE id = %s', (post_id,))
            post = cursor.fetchone()
            if not post:
                return jsonify({'error': '帖子不存在'}), 404

            cursor.execute('UPDATE posts SET is_pinned = %s WHERE id = %s', (is_pinned, post_id))
            log_action('pin_post' if is_pinned else 'unpin_post', 'post', post_id)

            if is_pinned and post['user_id']:
                create_notification(
                    post['user_id'], 'pin',
                    '你的帖子已被置顶',
                    f'你的帖子「{post["title"]}」已被管理员置顶',
                    f'/forum/post/{post_id}'
                )

            return jsonify({'success': True, 'is_pinned': is_pinned})
    finally:
        conn.close()

@app.route('/api/forum/post/<int:post_id>/feature', methods=['PUT'])
@role_required(ROLE_ADMIN)
def forum_feature_post(post_id):
    data = request.get_json() or {}
    is_featured = 1 if data.get('is_featured') else 0

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id, user_id, title FROM posts WHERE id = %s', (post_id,))
            post = cursor.fetchone()
            if not post:
                return jsonify({'error': '帖子不存在'}), 404

            cursor.execute('UPDATE posts SET is_featured = %s WHERE id = %s', (is_featured, post_id))
            log_action('feature_post' if is_featured else 'unfeature_post', 'post', post_id)

            if is_featured and post['user_id']:
                create_notification(
                    post['user_id'], 'feature',
                    '你的帖子已被加精',
                    f'你的帖子「{post["title"]}」已被管理员加为精华',
                    f'/forum/post/{post_id}'
                )

            return jsonify({'success': True, 'is_featured': is_featured})
    finally:
        conn.close()

@app.route('/api/forum/post/<int:post_id>/favorite', methods=['POST'])
@login_required
def forum_favorite_post(post_id):
    user = g.current_user
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id FROM posts WHERE id = %s', (post_id,))
            if not cursor.fetchone():
                return jsonify({'error': '帖子不存在'}), 404

            try:
                cursor.execute(
                    'INSERT INTO post_favorites (user_id, post_id) VALUES (%s, %s)',
                    (user['user_id'], post_id)
                )
                return jsonify({'success': True, 'message': '收藏成功'})
            except Exception:
                return jsonify({'error': '已经收藏过该帖子'}), 409
    finally:
        conn.close()

@app.route('/api/forum/post/<int:post_id>/favorite', methods=['DELETE'])
@login_required
def forum_unfavorite_post(post_id):
    user = g.current_user
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'DELETE FROM post_favorites WHERE user_id = %s AND post_id = %s',
                (user['user_id'], post_id)
            )
            if cursor.rowcount == 0:
                return jsonify({'error': '未收藏该帖子'}), 404
            return jsonify({'success': True, 'message': '取消收藏成功'})
    finally:
        conn.close()

@app.route('/api/forum/favorites', methods=['GET'])
@login_required
def forum_favorites():
    user = g.current_user
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('pageSize', 20))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'SELECT COUNT(*) as total FROM post_favorites WHERE user_id = %s',
                (user['user_id'],)
            )
            total = cursor.fetchone()['total']

            offset = (page - 1) * page_size
            cursor.execute(
                '''SELECT p.id, p.title, p.author, p.category, p.likes, p.views, p.comment_count,
                          p.is_pinned, p.is_featured, p.created_at, pf.created_at as favorited_at
                   FROM post_favorites pf
                   JOIN posts p ON pf.post_id = p.id
                   WHERE pf.user_id = %s
                   ORDER BY pf.created_at DESC
                   LIMIT %s OFFSET %s''',
                (user['user_id'], page_size, offset)
            )
            posts = cursor.fetchall()

            return jsonify({
                'data': posts,
                'total': total,
                'page': page,
                'pageSize': page_size
            })
    finally:
        conn.close()

# ==================== 图片上传 API ====================

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/upload/image', methods=['POST'])
def upload_image():
    if 'file' not in request.files:
        return jsonify({'error': '没有上传文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '未选择文件'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': '不支持的图片格式，支持: png, jpg, gif, webp'}), 400

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > MAX_UPLOAD_SIZE:
        return jsonify({'error': '图片大小不能超过 5MB'}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    # 图片压缩：超过1MB时压缩，最大宽度1920px，质量85
    if HAS_PIL and os.path.getsize(filepath) > 1024 * 1024:
        try:
            img = Image.open(filepath)
            if img.width > 1920:
                ratio = 1920 / img.width
                new_size = (1920, int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)
            if ext in ('jpg', 'jpeg'):
                img.save(filepath, 'JPEG', quality=85)
            elif ext == 'webp':
                img.save(filepath, 'WEBP', quality=85)
            elif ext == 'png':
                img.save(filepath, 'PNG', optimize=True)
        except Exception:
            pass

    return jsonify({
        'url': f'/uploads/{filename}',
        'filename': filename
    })

def fetch_peticon_from_web(race_id):
    cache_dir = os.path.join(AOQI_BASE, 'aoqi-agent', 'frontend', 'assets', 'peticons')
    os.makedirs(cache_dir, exist_ok=True)
    
    cache_path = os.path.join(cache_dir, f'{race_id}.webp')
    
    if os.path.exists(cache_path):
        return cache_path
    
    urls = [
        f'https://aoqi.100bt.com/h5/peticon/fang/peticon{race_id}~20211126.webp',
        f'https://aoqi.100bt.com/h5/peticon/fight/peticon{race_id}~20211126.webp',
        f'https://aoqi.100bt.com/h5/peticon/rectangle/peticon{race_id}~20211126.webp',
        f'https://aoqi.100bt.com/h5/peticon/static/peticon{race_id}~20211126.webp',
    ]
    
    for url in urls:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                with open(cache_path, 'wb') as f:
                    f.write(resp.content)
                return cache_path
        except:
            continue
    
    return None

# ==================== 视频增强 API ====================

@app.route('/api/videos/<int:race_id>/<string:video_id>/like', methods=['POST'])
@login_required
def like_video(race_id, video_id):
    load_data()
    race_key = str(race_id)
    if race_key not in bilibili_videos:
        return jsonify({'error': '视频不存在'}), 404

    video = next((v for v in bilibili_videos[race_key] if v.get('bvid') == video_id or v.get('url') == video_id), None)
    if not video:
        return jsonify({'error': '视频不存在'}), 404

    video['likes'] = video.get('likes', 0) + 1
    save_bilibili_videos()
    return jsonify({'success': True, 'likes': video['likes']})

@app.route('/api/videos/<int:race_id>/<string:video_id>/comments', methods=['GET'])
def get_video_comments(race_id, video_id):
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('pageSize', 20))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'SELECT COUNT(*) as total FROM video_comments WHERE race_id = %s AND video_id = %s',
                (race_id, video_id)
            )
            total = cursor.fetchone()['total']

            offset = (page - 1) * page_size
            cursor.execute(
                'SELECT id, user_id, content, author, likes, created_at FROM video_comments WHERE race_id = %s AND video_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s',
                (race_id, video_id, page_size, offset)
            )
            comments = cursor.fetchall()

            return jsonify({
                'data': comments,
                'total': total,
                'page': page,
                'pageSize': page_size
            })
    finally:
        conn.close()

@app.route('/api/videos/<int:race_id>/<string:video_id>/comments', methods=['POST'])
@login_required
def add_video_comment(race_id, video_id):
    data = request.get_json() or {}
    user = g.current_user
    content = (data.get('content') or '').strip()

    if not content:
        return jsonify({'error': '评论内容不能为空'}), 400

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'INSERT INTO video_comments (race_id, video_id, user_id, content, author) VALUES (%s, %s, %s, %s, %s)',
                (race_id, video_id, user['user_id'], content, user['username'])
            )
            comment_id = cursor.lastrowid
            return jsonify({'id': comment_id, 'message': '评论成功'})
    finally:
        conn.close()

@app.route('/api/videos/comment/<int:comment_id>', methods=['DELETE'])
@login_required
def delete_video_comment(comment_id):
    user = g.current_user
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM video_comments WHERE id = %s', (comment_id,))
            comment = cursor.fetchone()
            if not comment:
                return jsonify({'error': '评论不存在'}), 404

            if not can_manage_content(comment['user_id']):
                return jsonify({'error': '无权限删除此评论'}), 403

            cursor.execute('DELETE FROM video_comments WHERE id = %s', (comment_id,))
            log_action('delete_video_comment', 'video_comment', comment_id)
            return jsonify({'success': True, 'message': '删除成功'})
    finally:
        conn.close()

# ==================== 通知系统 API ====================

@app.route('/api/notifications', methods=['GET'])
@login_required
def get_notifications():
    user = g.current_user
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('pageSize', 20))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'SELECT COUNT(*) as total FROM notifications WHERE user_id = %s',
                (user['user_id'],)
            )
            total = cursor.fetchone()['total']

            offset = (page - 1) * page_size
            cursor.execute(
                'SELECT id, type, title, content, link, is_read, created_at FROM notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s',
                (user['user_id'], page_size, offset)
            )
            notifications = cursor.fetchall()

            return jsonify({
                'data': notifications,
                'total': total,
                'page': page,
                'pageSize': page_size
            })
    finally:
        conn.close()

@app.route('/api/notifications/<int:notif_id>/read', methods=['PUT'])
@login_required
def read_notification(notif_id):
    user = g.current_user
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'UPDATE notifications SET is_read = 1 WHERE id = %s AND user_id = %s',
                (notif_id, user['user_id'])
            )
            if cursor.rowcount == 0:
                return jsonify({'error': '通知不存在'}), 404
            return jsonify({'success': True})
    finally:
        conn.close()

@app.route('/api/notifications/read-all', methods=['PUT'])
@login_required
def read_all_notifications():
    user = g.current_user
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'UPDATE notifications SET is_read = 1 WHERE user_id = %s AND is_read = 0',
                (user['user_id'],)
            )
            return jsonify({'success': True, 'count': cursor.rowcount})
    finally:
        conn.close()

@app.route('/api/notifications/unread-count', methods=['GET'])
@login_required
def unread_count():
    user = g.current_user
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'SELECT COUNT(*) as count FROM notifications WHERE user_id = %s AND is_read = 0',
                (user['user_id'],)
            )
            return jsonify({'count': cursor.fetchone()['count']})
    finally:
        conn.close()

# ==================== 操作日志 API ====================

@app.route('/api/audit-logs', methods=['GET'])
@role_required(ROLE_SUPER_ADMIN)
def get_audit_logs():
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('pageSize', 20))
    action = request.args.get('action', '')
    keyword = request.args.get('keyword', '')

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            where = []
            params = []
            if action:
                where.append('action = %s')
                params.append(action)
            if keyword:
                where.append('(username LIKE %s OR detail LIKE %s)')
                params.extend([f'%{keyword}%', f'%{keyword}%'])
            where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''

            cursor.execute(f'SELECT COUNT(*) as total FROM audit_logs{where_sql}', params)
            total = cursor.fetchone()['total']

            offset = (page - 1) * page_size
            cursor.execute(
                f'SELECT id, user_id, username, action, target_type, target_id, detail, ip_address, created_at FROM audit_logs{where_sql} ORDER BY created_at DESC LIMIT %s OFFSET %s',
                params + [page_size, offset]
            )
            logs = cursor.fetchall()

            return jsonify({
                'data': logs,
                'total': total,
                'page': page,
                'pageSize': page_size
            })
    finally:
        conn.close()

# ==================== 数据库备份 API ====================

@app.route('/api/backup', methods=['POST'])
@role_required(ROLE_SUPER_ADMIN)
def backup_database():
    backup_dir = os.path.join(AOQI_BASE, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    filename = f"aoqi_forum_{time.strftime('%Y%m%d_%H%M%S')}.sql"
    filepath = os.path.join(backup_dir, filename)

    cmd = f"mysqldump -h {DB_CONFIG['host']} -P {DB_CONFIG['port']} -u {DB_CONFIG['user']} -p{DB_CONFIG['password']} {DB_CONFIG['database']} > {filepath}"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        log_action('backup_database', detail=filename)
        return jsonify({'success': True, 'filename': filename})
    return jsonify({'error': '备份失败', 'detail': result.stderr}), 500

if __name__ == '__main__':
    try:
        init_db()
        print('Database initialized successfully')
    except Exception as e:
        print(f'Warning: Database init failed: {e}')
        print('Forum features will be disabled until MySQL is configured.')
    app.run(host='0.0.0.0', port=5000, debug=True)
