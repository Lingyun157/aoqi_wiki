import json
import os
import re
import sys
import argparse


def load_json(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def parse_js_string_array(text, field_name):
    pattern = rf'\.{field_name}\s*=\s*\[((?:"[^"]*"(?:\s*,\s*"[^"]*")*)\s*)\]'
    m = re.search(pattern, text)
    if not m:
        return None
    return re.findall(r'"([^"]*)"', m.group(1))


def parse_js_value(text, field_name, value_type='int'):
    if value_type == 'int':
        m = re.search(rf'\.{field_name}\s*=\s*(\d+)', text)
        return int(m.group(1)) if m else None
    elif value_type == 'str':
        m = re.search(rf'\.{field_name}\s*=\s*"([^"]*)"', text)
        return m.group(1) if m else None
    return None


def parse_challenge_levels(text):
    """从JS中提取关卡teamId及结构信息"""
    # 模式1: teamId 在 desc 之后
    pattern = r'\{[^}]*teamId\s*:\s*(\d+)[^}]*dataKey\s*:\s*"([^"]+)"[^}]*dataId\s*:\s*(\d+)[^}]*\}'
    levels = []
    for m in re.finditer(pattern, text):
        team_id = int(m.group(1))
        if team_id < 1000:
            continue
        levels.append({
            'teamId': team_id,
            'dataKey': m.group(2),
            'dataId': int(m.group(3)),
        })

    if not levels:
        # 模式2: dataKey 在 teamId 前面
        pattern2 = r'\{[^}]*dataKey\s*:\s*"([^"]+)"[^}]*dataId\s*:\s*(\d+)[^}]*teamId\s*:\s*(\d+)[^}]*\}'
        for m in re.finditer(pattern2, text):
            team_id = int(m.group(3))
            if team_id < 1000:
                continue
            levels.append({
                'teamId': team_id,
                'dataKey': m.group(1),
                'dataId': int(m.group(2)),
            })

    # 去重
    seen = set()
    unique = []
    for lv in levels:
        if lv['teamId'] not in seen:
            seen.add(lv['teamId'])
            unique.append(lv)
    return unique


def parse_level_desc_structure(text):
    """提取 desc 与 teamId 的层级分组关系"""
    events = []
    for m in re.finditer(r'desc\s*:\s*"([^"]+)"', text):
        events.append((m.start(), 'desc', m.group(1)))
    for m in re.finditer(r'teamId\s*:\s*(\d+)', text):
        tid = int(m.group(1))
        if tid >= 1000:
            events.append((m.start(), 'teamId', tid))
    events.sort()

    current_desc = '未知关卡'
    grouped = {}
    order = []
    for pos, typ, val in events:
        if typ == 'desc':
            current_desc = val
            if current_desc not in grouped:
                grouped[current_desc] = []
                order.append(current_desc)
        elif typ == 'teamId':
            if current_desc not in grouped:
                grouped[current_desc] = []
                order.append(current_desc)
            grouped[current_desc].append(val)
    return [(d, list(dict.fromkeys(grouped[d]))) for d in order if d != '整个挑战']


def parse_formation_pets(formation_data):
    if not formation_data:
        return None
    pets = formation_data.get('pets', [])
    ps = formation_data.get('ps', '')
    pet_list = []
    for p in pets:
        pet_id = p.get('id')
        pet_info = {
            'slotId': pet_id,
            'raceId': p.get('r') or p.get('fr'),
            'name': p.get('n', ''),
            'strengthenId': p.get('strengthenId'),
            'isMarked': str(pet_id).startswith('1000') if pet_id is not None else False,
        }
        if 'sepi' in p:
            pet_info['summonedPetSlotId'] = p['sepi']
        if 'srpi' in p:
            pet_info['summonerSlotId'] = p['srpi']
        if 'cepi' in p:
            pet_info['cepi'] = p['cepi']
            pet_info['contractPetSlotId'] = p['cepi']
        if 'buff' in p:
            pet_info['buff'] = p['buff']
        if 'extraProperty' in p:
            pet_info['extraProperty'] = p['extraProperty']
        pet_list.append(pet_info)
    return {
        'positionString': ps,
        'pets': pet_list,
        'heroSkill': formation_data.get('heroSkill'),
    }


def categorize_challenge(fname):
    """根据文件名分类挑战类型"""
    f = fname.lower()
    if 'lingchu' in f:
        return '灵初挑战'
    if 'shenyun' in f:
        return '神运挑战'
    if 'xingji' in f:
        return '星迹挑战'
    if 'qiyuan' in f:
        return '起源挑战'
    if 'tianqi' in f:
        return '天启挑战'
    if 'challenge' in f:
        return '特殊挑战'
    return '其他挑战'


def scan_challenges(activities_root, date_filter=None):
    """扫描所有JS文件，提取挑战活动

    Args:
        activities_root: 活动JS根目录
        date_filter: 只扫描指定日期（用于增量更新），None表示全量
    """
    challenges = []
    total_files = 0

    date_dirs = sorted(os.listdir(activities_root))
    if date_filter:
        date_dirs = [d for d in date_dirs if d == date_filter]

    for date_dir in date_dirs:
        date_path = os.path.join(activities_root, date_dir)
        if not os.path.isdir(date_path):
            continue
        for fname in os.listdir(date_path):
            if not fname.endswith('.js'):
                continue
            total_files += 1
            fpath = os.path.join(date_path, fname)
            try:
                content = open(fpath, 'r', encoding='utf-8').read()
            except:
                continue

            # 快速判断：有没有 teamId
            if 'teamId' not in content:
                continue

            levels = parse_challenge_levels(content)
            if len(levels) < 3:
                continue

            pet_race_id = parse_js_value(content, 'PetRaceId', 'int')
            total_lv = parse_js_value(content, 'TotalLv', 'int')
            rules = parse_js_string_array(content, 'RULES')
            entries = parse_js_string_array(content, 'ENTRIES')
            level_structure = parse_level_desc_structure(content)

            # 如果没有解析到 levelStructure，自动生成默认的：每个 teamId 一个关卡
            if not level_structure:
                team_ids = []
                for lv in levels:
                    tid = lv.get('teamId')
                    if tid and tid not in team_ids:
                        team_ids.append(tid)
                if team_ids:
                    level_structure = [(f'关卡{i+1}', [tid]) for i, tid in enumerate(team_ids)]

            category = categorize_challenge(fname)

            # 活动中文名推断：从 activityName 或文件名
            act_desc = parse_js_value(content, 'ActivityName', 'str') or ''

            challenges.append({
                'date': date_dir,
                'file': fname.replace('.js', ''),
                'category': category,
                'petRaceId': pet_race_id,
                'totalLevels': total_lv,
                'rules': rules or [],
                'entries': entries or [],
                'levels': levels,
                'levelStructure': [
                    {'levelName': ln, 'teamIds': tids}
                    for ln, tids in level_structure
                ],
                'activityDesc': act_desc,
            })

    return challenges, total_files


def build_knowledge_base(args):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.join(script_dir, 'output', 'config')
    output_dir = os.path.join(script_dir, '敌方阵容关卡图鉴')
    activities_root = os.path.join(script_dir, 'output', 'js', 'activities')

    os.makedirs(output_dir, exist_ok=True)

    pet_dict_path = os.path.join(base_dir, 'pet', 'petdictionarydata.json')
    formation_path = os.path.join(base_dir, 'ConfigureFormationDefines.json')

    pet_dict = load_json(pet_dict_path) if os.path.exists(pet_dict_path) else {}
    formations = load_json(formation_path) if os.path.exists(formation_path) else {}

    # 确定是全量还是增量
    json_path = os.path.join(output_dir, 'data', 'enemy_formations.json')
    existing_data = []
    date_filter = None

    if args.incremental and os.path.exists(json_path):
        # 增量模式：读取现有数据，找到最新日期
        with open(json_path, 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
        if existing_data:
            max_date = max(c['date'] for c in existing_data)
            # 扫描比 max_date 更新的日期
            all_dates = sorted([d for d in os.listdir(activities_root)
                                if os.path.isdir(os.path.join(activities_root, d))])
            new_dates = [d for d in all_dates if d > max_date]
            if not new_dates:
                print(f'增量更新：没有新的日期（最新: {max_date}），跳过')
                return
            date_filter = new_dates[-1]  # 只扫最新的一天
            print(f'增量更新：最新现有日期 {max_date}，扫描新日期 {date_filter}')

    # 扫描挑战
    challenges, total_files = scan_challenges(activities_root, date_filter)
    print(f'扫描JS文件: {total_files} 个')
    print(f'解析到挑战活动: {len(challenges)} 个')

    # 补充精灵名称
    for ch in challenges:
        if ch['petRaceId'] and str(ch['petRaceId']) in pet_dict:
            ch['petName'] = pet_dict[str(ch['petRaceId'])][1]
        else:
            ch['petName'] = ''

    # 关联阵容数据
    for ch in challenges:
        ch['formations'] = {}
        for lv in ch['levels']:
            tid = lv['teamId']
            formation_data = formations.get(str(tid))
            if formation_data:
                ch['formations'][str(tid)] = parse_formation_pets(formation_data)

    # 合并数据
    if args.incremental and existing_data:
        # 去重：以 date + file 为 key
        existing_keys = {(c['date'], c['file']) for c in existing_data}
        new_count = 0
        for ch in challenges:
            key = (ch['date'], ch['file'])
            if key not in existing_keys:
                existing_data.append(ch)
                new_count += 1
            else:
                # 替换已有
                for i, c in enumerate(existing_data):
                    if (c['date'], c['file']) == key:
                        existing_data[i] = ch
                        break
        all_challenges = existing_data
        print(f'增量新增: {new_count} 个活动')
    else:
        all_challenges = challenges
        print(f'全量重建: {len(all_challenges)} 个活动')

    # 按日期倒序
    all_challenges.sort(key=lambda x: (x['date'], x['file']), reverse=True)

    # 输出 JSON
    os.makedirs(os.path.join(output_dir, 'data'), exist_ok=True)
    os.makedirs(os.path.join(output_dir, 'markdown'), exist_ok=True)

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(all_challenges, f, ensure_ascii=False, indent=2)

    # 按分类统计
    cat_stats = {}
    for ch in all_challenges:
        c = ch['category']
        cat_stats[c] = cat_stats.get(c, 0) + 1

    # 生成 Markdown
    md = '# 奥奇传说 - 敌方阵容关卡图鉴\n\n'
    md += f'> 共收录 **{len(all_challenges)}** 个挑战活动，覆盖灵初/神运/星迹/起源等各阶段\n\n'
    md += '---\n\n'

    # 分类统计
    md += '## 挑战分类统计\n\n'
    md += '| 分类 | 数量 |\n|------|------|\n'
    for cat in ['灵初挑战', '神运挑战', '星迹挑战', '起源挑战', '天启挑战', '特殊挑战', '其他挑战']:
        if cat in cat_stats:
            md += f'| {cat} | {cat_stats[cat]} |\n'
    md += '\n---\n\n'

    # 目录 - 按分类分组
    md += '## 目录\n\n'
    for cat in ['灵初挑战', '神运挑战', '星迹挑战', '起源挑战', '天启挑战', '特殊挑战', '其他挑战']:
        cat_list = [c for c in all_challenges if c['category'] == cat]
        if not cat_list:
            continue
        md += f'### {cat}\n\n'
        for i, ch in enumerate(cat_list, 1):
            title = ch.get('petName') or ch.get('activityDesc') or ch['file']
            anchor = f'{ch["date"]}_{ch["file"]}'
            md += f'{i}. [{title}](#{anchor}) - {ch["date"]}\n'
        md += '\n'

    md += '---\n\n'

    # 详情 - 按分类分组
    for cat in ['灵初挑战', '神运挑战', '星迹挑战', '起源挑战', '天启挑战', '特殊挑战', '其他挑战']:
        cat_list = [c for c in all_challenges if c['category'] == cat]
        if not cat_list:
            continue
        md += f'# {cat}\n\n'

        for ch in cat_list:
            title = ch.get('petName') or ch.get('activityDesc') or ch['file']
            anchor = f'{ch["date"]}_{ch["file"]}'
            md += f'## <a id="{anchor}"></a>{title}\n\n'
            md += f'- **活动日期**: {ch["date"]}\n'
            md += f'- **活动文件**: {ch["file"]}\n'
            if ch.get('petName'):
                md += f'- **目标精灵**: {ch["petName"]}（ID: {ch["petRaceId"]}）\n'
            elif ch.get('petRaceId'):
                md += f'- **目标精灵ID**: {ch["petRaceId"]}\n'
            if ch['totalLevels']:
                md += f'- **关卡层数**: {ch["totalLevels"]}\n'
            md += f'- **敌方阵容数**: {len(ch["levels"])}\n\n'

            if ch.get('entries'):
                md += '### 关卡机制说明\n\n'
                for entry in ch['entries']:
                    md += f'> {entry}\n\n'

            if ch.get('rules'):
                md += '### 挑战规则\n\n'
                for rule in ch['rules']:
                    md += f'- {rule}\n'
                md += '\n'

            if ch.get('levelStructure'):
                md += '### 关卡结构\n\n'
                for lv in ch['levelStructure']:
                    tids = ', '.join(str(t) for t in lv['teamIds'])
                    md += f'- **{lv["levelName"]}**: {tids}\n'
                md += '\n'

            if ch.get('formations'):
                md += '### 敌方阵容详情\n\n'
                for lv in ch['levels']:
                    tid = str(lv['teamId'])
                    formation = ch['formations'].get(tid)
                    if not formation:
                        continue
                    dk = lv.get('dataKey', '')
                    md += f'#### 阵容 {dk}（ID: {tid}）\n\n'
                    pets = formation['pets']
                    ps_parts = formation['positionString'].split('#') if formation['positionString'] else []
                    slot_positions = {}
                    for idx, slot_str in enumerate(ps_parts):
                        try:
                            slot_id = int(slot_str)
                            if slot_id > 0:
                                slot_positions[slot_id] = idx + 1
                        except ValueError:
                            pass

                    md += '| 站位 | 精灵名称 | 精灵ID | 备注 |\n'
                    md += '|------|---------|--------|------|\n'
                    for p in pets:
                        pos = slot_positions.get(p['slotId'], '?')
                        note_parts = []
                        if p.get('summonedPetSlotId'):
                            note_parts.append(f'召唤:槽{p["summonedPetSlotId"]}')
                        if p.get('summonerSlotId'):
                            note_parts.append(f'召唤者:槽{p["summonerSlotId"]}')
                        if p.get('contractPetSlotId'):
                            note_parts.append(f'契约:槽{p["contractPetSlotId"]}')
                        note = '、'.join(note_parts) if note_parts else '-'
                        md += f'| {pos} | {p["name"]} | {p["raceId"]} | {note} |\n'
                    md += '\n'

                    has_detail = any(p.get('buff') or p.get('extraProperty') for p in pets)
                    if has_detail:
                        md += '<details><summary>详细属性与Buff</summary>\n\n'
                        for p in pets:
                            if not (p.get('buff') or p.get('extraProperty')):
                                continue
                            md += f'- **{p["name"]}** ({p["raceId"]})\n'
                            if p.get('extraProperty'):
                                md += f'  - 强化属性: {p["extraProperty"]}\n'
                            if p.get('buff'):
                                md += f'  - Buff: {p["buff"]}\n'
                        md += '\n</details>\n\n'

            md += '---\n\n'

    md_path = os.path.join(output_dir, 'markdown', '敌方阵容关卡图鉴.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md)

    # README
    readme = '# 敌方阵容关卡图鉴\n\n'
    readme += '## 知识库说明\n\n'
    readme += '本图鉴记录奥奇传说所有挑战活动的敌方阵容配置，包括关卡规则、机制说明和具体敌方精灵站位。\n\n'
    readme += '覆盖灵初、神运、星迹、起源、天启等各阶段挑战。\n\n'
    readme += '## 数据统计\n\n'
    readme += f'- **挑战活动总数**: {len(all_challenges)} 个\n'
    readme += f'- **更新模式**: {"增量" if args.incremental else "全量"}\n'
    for cat, n in sorted(cat_stats.items(), key=lambda x: -x[1]):
        readme += f'- **{cat}**: {n} 个\n'
    readme += '\n## 使用说明\n\n'
    readme += '```bash\n'
    readme += '# 全量重建\n'
    readme += 'python build_enemy_formation_guide.py\n\n'
    readme += '# 增量更新（只扫描最新日期）\n'
    readme += 'python build_enemy_formation_guide.py --incremental\n'
    readme += '```\n\n'
    readme += '## 文件结构\n\n'
    readme += '```\n'
    readme += '敌方阵容关卡图鉴/\n'
    readme += '├── README.md\n'
    readme += '├── data/\n'
    readme += '│   └── enemy_formations.json\n'
    readme += '└── markdown/\n'
    readme += '    └── 敌方阵容关卡图鉴.md\n'
    readme += '```\n'

    with open(os.path.join(output_dir, 'README.md'), 'w', encoding='utf-8') as f:
        f.write(readme)

    print(f'\n图鉴构建完成！')
    print(f'输出目录: {output_dir}')
    print(f'挑战活动总数: {len(all_challenges)}')
    print(f'分类: {cat_stats}')


def main():
    parser = argparse.ArgumentParser(description='奥奇传说敌方阵容关卡图鉴构建工具')
    parser.add_argument('--incremental', '-i', action='store_true',
                        help='增量更新模式：只扫描最新日期的JS文件')
    args = parser.parse_args()
    build_knowledge_base(args)


if __name__ == '__main__':
    main()
