import json
import os
import time
import requests
import random

AOQI_BASE = os.path.dirname(os.path.abspath(__file__))
PETS_PATH = os.path.join(AOQI_BASE, '灵初精灵知识库', 'data', 'lingchu_pets.json')
OUTPUT_PATH = os.path.join(AOQI_BASE, '灵初精灵知识库', 'data', 'bilibili_videos.json')
CACHE_PATH = os.path.join(AOQI_BASE, '灵初精灵知识库', 'data', 'bilibili_videos_cache.json')

MOBILE_SEARCH_URL = 'https://api.bilibili.com/x/web-interface/search/all/v2'

user_agents = [
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36',
]

def search_bilibili(keyword, page=1, pagesize=20):
    session = requests.Session()
    session.headers.update({
        'User-Agent': random.choice(user_agents),
        'Accept': 'application/json, text/plain, */*',
        'Referer': f'https://search.bilibili.com/all?keyword={requests.utils.quote(keyword)}',
        'Origin': 'https://search.bilibili.com',
    })
    
    params = {
        'keyword': keyword,
        'page': page,
        'page_size': pagesize,
        'context': '',
        'order': 'click',
        'duration': '0',
        'tids': '0',
        'single_column': '0',
    }
    
    for retry in range(3):
        try:
            resp = session.get(MOBILE_SEARCH_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get('code') == 0 and data.get('data'):
                result = data['data'].get('result', [])
                for item in result:
                    if item.get('result_type') == 'video':
                        videos = item.get('data', [])
                        return videos
            return []
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 412:
                print(f'    Retry {retry+1}/3: 412 error, switching UA')
                session.headers['User-Agent'] = random.choice(user_agents)
                time.sleep(2)
                continue
            print(f'    Search failed ({resp.status_code}): {e}')
            return []
        except Exception as e:
            print(f'    Search failed: {e}')
            return []
    return []

def clean_spirit_name(name):
    name = name.replace('[灵初]', '')
    name = name.replace('[星迹]', '')
    name = name.replace('[神运]', '')
    name = name.replace('[启元]', '')
    name = name.replace('[传说]', '')
    return name.strip()

def get_spirit_short_name(name):
    clean_name = clean_spirit_name(name)
    if '·' in clean_name:
        return clean_name.split('·')[-1]
    return clean_name

def format_duration(seconds):
    if isinstance(seconds, int):
        mins = seconds // 60
        secs = seconds % 60
        return f'{mins}:{secs:02d}'
    return str(seconds)

def is_valid_video(title, spirit_name):
    clean_name = clean_spirit_name(spirit_name)
    short_name = get_spirit_short_name(spirit_name)
    
    # 1. 必须明确包含奥奇传说相关内容
    aoqi_keywords = ['奥奇传说', '奥奇']
    has_aoqi = any(kw in title for kw in aoqi_keywords)
    if not has_aoqi:
        return False
    
    # 2. 必须包含精灵名称（全称或简称）
    has_spirit_name = clean_name in title or short_name in title
    if not has_spirit_name:
        return False
    
    # 3. 严格排除其他游戏和内容
    exclude_keywords = [
        '赛尔号', '洛克王国', '奥拉星', '卡布西游', '约瑟传说', '功夫派',
        '原神', '王者荣耀', '和平精英', '英雄联盟', 'LOL', 'DOTA',
        '脑叶公司', '废墟图书馆', '边狱公司',
        '铠甲勇士', '假面骑士', '奥特曼', '斗罗大陆', '斗破苍穹',
        '鬼泣', '崩坏', '星穹铁道',
        '我的世界', 'MC', '迷你世界',
        '赛尔号启航', '赛尔号手游',
        '坎公骑冠剑', '坎公', '坎骑',
        '明日方舟', '方舟', '阴阳师', 'FGO', '碧蓝航线',
        '三国杀', '炉石传说', '守望先锋', 'CS', '绝地求生',
        '高达', '模型', '手办', '可动', '测评', '开箱',
    ]
    for kw in exclude_keywords:
        if kw in title:
            return False
    
    # 4. 标题中不能出现与精灵名称冲突但明显不相关的其他热门 IP
    ip_indicators = ['开服', '公测', '周年庆', '联动', '礼包码']
    for kw in ip_indicators:
        if kw in title and ('攻略' not in title and '打法' not in title and '开荒' not in title):
            return False
    
    return True

def crawl_videos_for_spirit(spirit):
    name = spirit['name']
    race_id = spirit.get('raceId', '')
    clean_name = clean_spirit_name(name)
    short_name = get_spirit_short_name(name)
    
    # 关键词策略：优先使用 奥奇传说 灵初 + 精灵标识（raceId/名称）
    # raceId 通常不会有 B 站视频标题命中，使用较小 pagesize 减少请求开销
    keyword_specs = [
        (f'奥奇传说 灵初 {race_id}', 5),
        (f'奥奇传说 灵初 {short_name}', 20),
        (f'奥奇传说 灵初 {clean_name}', 20),
        (f'奥奇传说 {short_name} 攻略', 20),
        (f'奥奇传说 {short_name} 打法', 20),
    ]
    
    all_videos = []
    seen_bvid = set()
    
    for keyword, pagesize in keyword_specs:
        print(f'    Search: {keyword}')
        results = search_bilibili(keyword, page=1, pagesize=pagesize)
        
        for item in results:
            bvid = item.get('bvid') or item.get('aid')
            if not bvid or bvid in seen_bvid:
                continue
            
            title = item.get('title', '').replace('<em class="keyword">', '').replace('</em>', '')
            if not is_valid_video(title, name):
                continue
            
            seen_bvid.add(bvid)
            
            video = {
                'bvid': bvid,
                'title': title,
                'url': f'https://www.bilibili.com/video/{bvid}',
                'author': item.get('author', '') or item.get('up_name', ''),
                'play': item.get('play', 0) or item.get('view', 0),
                'danmaku': item.get('video_review', 0) or item.get('danmaku', 0),
                'favorites': item.get('favorites', 0),
                'pubdate': item.get('pubdate', 0),
                'duration': format_duration(item.get('duration', 0)),
                'pic': item.get('pic', '') or item.get('cover', ''),
                'keyword': keyword,
                'spiritRaceId': spirit.get('raceId'),
                'spiritName': clean_spirit_name(name),
            }
            all_videos.append(video)
        
        time.sleep(random.uniform(0.3, 0.8))
    
    all_videos.sort(key=lambda x: x['play'], reverse=True)
    return all_videos[:10]

def load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_cache(cache):
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def main():
    print('Loading spirit data...')
    with open(PETS_PATH, 'r', encoding='utf-8') as f:
        pets = json.load(f)
    
    cache = load_cache()
    results = {}
    
    total = len(pets)
    for i, spirit in enumerate(pets, 1):
        race_id = spirit['raceId']
        name = spirit['name']
        
        if race_id in cache:
            print(f'[{i}/{total}] Skip (cached): {name}')
            results[race_id] = cache[race_id]
            continue
        
        print(f'[{i}/{total}] Crawling: {name}')
        videos = crawl_videos_for_spirit(spirit)
        results[race_id] = videos
        
        cache[race_id] = videos
        save_cache(cache)
        
        if i % 5 == 0:
            print(f'    Progress: {i}/{total}, saving intermediate...')
            with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
        
        time.sleep(random.uniform(0.8, 1.5))
    
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f'\nDone! Total spirits: {total}')
    video_count = sum(len(v) for v in results.values())
    print(f'Total videos collected: {video_count}')

if __name__ == '__main__':
    main()
