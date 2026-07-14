import json
import os
import re


def load_json(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def sanitize_filename(name):
    """清理文件名中的非法字符"""
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    return name.strip()


def clean_name(name):
    """去掉前缀标签，提取精灵名"""
    return name.replace('[灵初]', '').replace('[神运]', '').strip()


def build_obsidian_vault():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    vault_root = os.path.join(script_dir, '奥奇传说知识库')
    lingchu_json = os.path.join(script_dir, '灵初精灵知识库', 'data', 'lingchu_pets.json')
    formations_json = os.path.join(script_dir, '敌方阵容关卡图鉴', 'data', 'enemy_formations.json')

    pets = load_json(lingchu_json)
    challenges = load_json(formations_json)

    # 目录结构
    dirs = [
        '00-入口',
        '01-灵初精灵',
        '02-敌方阵容',
        '03-Buff词典',
        '04-属性与职业',
        '99-附件',
    ]
    for d in dirs:
        os.makedirs(os.path.join(vault_root, d), exist_ok=True)

    # ========== 1. 收集所有Buff，建立索引 ==========
    all_buffs = {}
    for pet in pets:
        for buff in pet.get('relatedBuffs', []):
            name = buff['name']
            if name not in all_buffs:
                all_buffs[name] = buff['description']

    # 生成 Buff 词典
    buff_md = '---\ntype: moc\ntitle: Buff词典\ntags: [buff, 词典]\n---\n\n# Buff 词典\n\n'
    buff_md += '> 所有灵初精灵技能中涉及的 Buff 效果说明汇总\n\n'
    buff_md += '---\n\n'
    sorted_buffs = sorted(all_buffs.items(), key=lambda x: x[0])
    for name, desc in sorted_buffs:
        safe_name = sanitize_filename(name)
        buff_md += f'### [[{safe_name}|{name}]]\n\n'
        buff_md += f'{desc}\n\n'

    with open(os.path.join(vault_root, '03-Buff词典', 'Buff词典.md'), 'w', encoding='utf-8') as f:
        f.write(buff_md)

    # 为每个 Buff 生成独立笔记（用于双向链接跳转）
    for name, desc in sorted_buffs:
        safe_name = sanitize_filename(name)
        md = f'---\ntype: buff\nname: {name}\ntags: [buff]\n---\n\n# {name}\n\n'
        md += f'{desc}\n\n'
        md += '---\n\n'
        md += '## 相关精灵\n\n'
        # 查找使用了该buff的精灵
        related_pets = []
        for pet in pets:
            for b in pet.get('relatedBuffs', []):
                if b['name'] == name:
                    pet_safe = sanitize_filename(clean_name(pet['name']))
                    related_pets.append(f'- [[{pet_safe}|{pet["name"]}]]')
                    break
        if related_pets:
            md += '\n'.join(related_pets) + '\n'
        else:
            md += '_暂无关联精灵_\n'

        with open(os.path.join(vault_root, '03-Buff词典', f'{safe_name}.md'), 'w', encoding='utf-8') as f:
            f.write(md)

    # ========== 2. 生成灵初精灵笔记 ==========
    element_groups = {}
    job_groups = {}
    pet_file_map = {}  # name -> filename for linking

    for pet in pets:
        name = pet['name']
        clean = clean_name(name)
        safe = sanitize_filename(clean)
        pet_file_map[name] = safe

        element = pet.get('elementTypeName', '')
        job = pet.get('jobCategory', '')

        if element not in element_groups:
            element_groups[element] = []
        element_groups[element].append(pet)

        if job not in job_groups:
            job_groups[job] = []
        job_groups[job].append(pet)

        # YAML frontmatter
        md = '---\n'
        md += f'type: 精灵\n'
        md += f'name: "{name}"\n'
        md += f'raceId: {pet["raceId"]}\n'
        md += f'属性: {element}\n'
        md += f'职业: {pet.get("jobCategory", "")}\n'
        md += f'职业ID: {pet.get("jobTypeId", "")}\n'
        if pet.get('hasIcon') and pet.get('iconAbsPath'):
            icon_rel = f'../assets/peticons/{pet["raceId"]}.png'
            md += f'icon: "{icon_rel}"\n'
        md += f'tags: [灵初精灵, {element}, {pet.get("jobCategory", "")}]\n'
        md += '---\n\n'

        md += f'# {name}\n\n'

        # 显示图标
        if pet.get('hasIcon') and pet.get('iconAbsPath'):
            icon_abs = pet['iconAbsPath'].replace('\\', '/')
            md += f'![[{icon_abs}|300]]\n\n'
            if pet.get('iconType'):
                md += f'> 图标类型: {pet["iconType"]}\n\n'

        md += f'**精灵ID**: {pet["raceId"]}  \n'
        md += f'**属性**: {element}  \n'
        md += f'**职业**: {pet.get("jobCategory", "")} (ID: {pet.get("jobTypeId", "")})\n\n'

        # 技能
        for skill in pet.get('skills', []):
            md += f'## {skill["type"]}：{skill["name"]}\n\n'
            desc = skill['description']
            # 替换 [xxx] 为 [[buff名|buff名]] 链接
            desc = replace_buff_links(desc, all_buffs)
            md += f'{desc}\n\n'

        # 天命技
        tianming = pet.get('tianmingSkill')
        if tianming:
            md += f'## 天命技：{tianming["name"]}\n\n'
            desc = tianming['description']
            desc = replace_buff_links(desc, all_buffs)
            md += f'{desc}\n\n'

        # 相关Buff
        related = pet.get('relatedBuffs', [])
        if related:
            md += '## 相关Buff\n\n'
            for buff in related:
                bname = buff['name']
                bsafe = sanitize_filename(bname)
                md += f'### [[{bsafe}|{bname}]]\n\n'
                md += f'> {buff["description"]}\n\n'

        # 出场关卡（反向链接）
        appearances = find_pet_appearances(pet['raceId'], challenges)
        if appearances:
            md += '## 出场关卡\n\n'
            for ch_name, teams in appearances.items():
                ch_safe = sanitize_filename(ch_name)
                md += f'- [[{ch_safe}|{ch_name}]]：{", ".join(str(t) for t in teams)}\n'
            md += '\n'

        filepath = os.path.join(vault_root, '01-灵初精灵', f'{safe}.md')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(md)

    # ========== 3. 生成敌方阵容笔记 ==========
    for ch in challenges:
        title = ch.get('petName') or ch.get('activityDesc') or ch.get('activityName') or ch.get('file', '未知活动')
        safe_title = sanitize_filename(title)

        md = '---\n'
        md += f'type: 挑战活动\n'
        md += f'name: "{title}"\n'
        md += f'date: {ch["date"]}\n'
        md += f'活动名: {ch.get("activityName", "")}\n'
        if ch.get('petRaceId'):
            md += f'目标精灵ID: {ch["petRaceId"]}\n'
        if ch.get('totalLevels'):
            md += f'关卡层数: {ch["totalLevels"]}\n'
        md += f'关卡数量: {len(ch.get("teamIds", []))}\n'
        md += f'tags: [敌方阵容, 挑战, {ch["date"]}]\n'
        md += '---\n\n'

        md += f'# {title}\n\n'
        md += f'**活动日期**: {ch["date"]}  \n'
        md += f'**活动描述**: {ch.get("activityDesc", "")}  \n'
        if ch.get('petRaceId'):
            # 链接到对应精灵笔记
            pet_name = ch.get('petName', '')
            pet_safe = pet_file_map.get(pet_name, '')
            if pet_safe:
                md += f'**目标精灵**: [[{pet_safe}|{pet_name}]] (ID: {ch["petRaceId"]})  \n'
            else:
                md += f'**目标精灵**: {pet_name} (ID: {ch["petRaceId"]})  \n'
        if ch.get('totalLevels'):
            md += f'**关卡层数**: {ch["totalLevels"]}  \n'
        md += f'**关卡ID总数**: {len(ch.get("teamIds", []))}\n\n'

        # 机制说明
        entries = ch.get('entries', [])
        if entries:
            md += '## 关卡机制\n\n'
            for e in entries:
                e = replace_buff_links(e, all_buffs)
                md += f'> {e}\n\n'

        # 规则
        rules = ch.get('rules', [])
        if rules:
            md += '## 挑战规则\n\n'
            for r in rules:
                r = replace_buff_links(r, all_buffs)
                md += f'- {r}\n'
            md += '\n'

        # 关卡结构
        levels = ch.get('levelStructure', [])
        if levels:
            md += '## 关卡结构\n\n'
            for lv in levels:
                if not lv['teamIds']:
                    continue
                md += f'### {lv["levelName"]}\n\n'
                for tid in lv['teamIds']:
                    md += f'- **阵容 {tid}**：'
                    formation = ch.get('formations', {}).get(str(tid))
                    if formation and formation['pets']:
                        pet_names = []
                        for p in formation['pets']:
                            pname = p.get('name', '')
                            # 如果是灵初精灵，加链接
                            if pname in pet_file_map:
                                pet_names.append(f'[[{pet_file_map[pname]}|{pname}]]')
                            else:
                                pet_names.append(pname)
                        md += '、'.join(pet_names)
                    md += '\n'
                md += '\n'

        # 阵容详情
        formations = ch.get('formations', {})
        if formations:
            md += '## 阵容详情\n\n'
            for tid_str, fm in formations.items():
                if not fm:
                    continue
                md += f'### 阵容 {tid_str}\n\n'
                md += '| 站位 | 精灵名称 | 精灵ID | 关系 |\n'
                md += '|------|---------|--------|------|\n'
                ps = fm.get('positionString', '').split('#')
                slot_pos = {}
                for idx, s in enumerate(ps):
                    try:
                        sid = int(s)
                        if sid > 0:
                            slot_pos[sid] = idx + 1
                    except ValueError:
                        pass

                for p in fm.get('pets', []):
                    pos = slot_pos.get(p.get('slotId', 0), '?')
                    pname = p.get('name', '')
                    pid = p.get('raceId', '')
                    rel_parts = []
                    if p.get('summonedPetSlotId'):
                        rel_parts.append(f'召唤')
                    if p.get('summonerSlotId'):
                        rel_parts.append(f'召唤者')
                    if p.get('contractPetSlotId'):
                        rel_parts.append(f'契约')
                    rel = '、'.join(rel_parts) if rel_parts else '-'

                    if pname in pet_file_map:
                        name_cell = f'[[{pet_file_map[pname]}|{pname}]]'
                    else:
                        name_cell = pname

                    md += f'| {pos} | {name_cell} | {pid} | {rel} |\n'
                md += '\n'

                # 英雄技
                if fm.get('heroSkill'):
                    md += f'- **英雄技ID**: {fm["heroSkill"]}\n'

                # 详细属性/buff
                has_detail = any(p.get('buff') or p.get('extraProperty') for p in fm.get('pets', []))
                if has_detail:
                    md += '\n??? note "详细属性"\n\n'
                    for p in fm.get('pets', []):
                        if not (p.get('buff') or p.get('extraProperty')):
                            continue
                        md += f'    - **{p.get("name", "")}** ({p.get("raceId", "")})\n'
                        if p.get('extraProperty'):
                            md += f'        - 强化: {p["extraProperty"]}\n'
                        if p.get('buff'):
                            md += f'        - Buff: `{p["buff"]}`\n'
                    md += '\n'

        filepath = os.path.join(vault_root, '02-敌方阵容', f'{safe_title}.md')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(md)

    # ========== 4. 生成 MOC 导航页 ==========

    # 4.1 灵初精灵 MOC（按属性）
    moc_element = '---\ntype: moc\ntitle: 灵初精灵图鉴（按属性）\ntags: [moc, 灵初精灵, 属性]\n---\n\n'
    moc_element += '# 灵初精灵图鉴（按属性）\n\n'
    moc_element += '> 按属性分类的灵初精灵索引\n\n'
    moc_element += '---\n\n'
    for elem in sorted(element_groups.keys()):
        pet_list = element_groups[elem]
        moc_element += f'## {elem}（{len(pet_list)}只）\n\n'
        # 有图标的先显示
        icon_pets = [p for p in pet_list if p.get('hasIcon')]
        no_icon_pets = [p for p in pet_list if not p.get('hasIcon')]

        if icon_pets:
            moc_element += '### 有立绘/图标\n\n'
            moc_element += '<div style="display: flex; flex-wrap: wrap; gap: 10px;">\n\n'
            for pet in sorted(icon_pets, key=lambda x: x['raceId']):
                safe = pet_file_map.get(pet['name'], '')
                icon_abs = pet.get('iconAbsPath', '').replace('\\', '/')
                moc_element += f'[[{safe}|![{pet["name"]}|80]({icon_abs})<br>{pet["name"]}]]  '
            moc_element += '\n\n</div>\n\n'

        if no_icon_pets:
            moc_element += '### 列表\n\n'
            for pet in sorted(no_icon_pets, key=lambda x: x['raceId']):
                safe = pet_file_map.get(pet['name'], '')
                moc_element += f'- [[{safe}|{pet["name"]}]] — {pet.get("jobCategory", "")}\n'
        moc_element += '\n'

    with open(os.path.join(vault_root, '04-属性与职业', '灵初精灵图鉴-属性.md'), 'w', encoding='utf-8') as f:
        f.write(moc_element)

    # 4.2 灵初精灵 MOC（按职业）
    moc_job = '---\ntype: moc\ntitle: 灵初精灵图鉴（按职业）\ntags: [moc, 灵初精灵, 职业]\n---\n\n'
    moc_job += '# 灵初精灵图鉴（按职业）\n\n'
    moc_job += '> 按职业分类的灵初精灵索引\n\n'
    moc_job += '---\n\n'
    for job in sorted(job_groups.keys()):
        pet_list = job_groups[job]
        moc_job += f'## {job}（{len(pet_list)}只）\n\n'
        for pet in sorted(pet_list, key=lambda x: x['raceId']):
            safe = pet_file_map.get(pet['name'], '')
            elem = pet.get('elementTypeName', '')
            moc_job += f'- [[{safe}|{pet["name"]}]] — {elem}\n'
        moc_job += '\n'

    with open(os.path.join(vault_root, '04-属性与职业', '灵初精灵图鉴-职业.md'), 'w', encoding='utf-8') as f:
        f.write(moc_job)

    # 4.3 敌方阵容 MOC
    moc_challenge = '---\ntype: moc\ntitle: 敌方阵容挑战图鉴\ntags: [moc, 敌方阵容, 挑战]\n---\n\n'
    moc_challenge += '# 敌方阵容挑战图鉴\n\n'
    moc_challenge += f'> 共收录 **{len(challenges)}** 个挑战活动的敌方阵容\n\n'
    moc_challenge += '---\n\n'

    # 按分类分组
    categories = {}
    for ch in challenges:
        cat = ch.get('category', '其他挑战')
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(ch)

    # 分类排序
    cat_order = ['灵初挑战', '神运挑战', '星迹挑战', '起源挑战', '天启挑战', '特殊挑战', '其他挑战']
    for cat in cat_order:
        if cat not in categories:
            continue
        cat_list = sorted(categories[cat], key=lambda x: x['date'], reverse=True)
        moc_challenge += f'## {cat}（{len(cat_list)}）\n\n'
        for ch in cat_list:
            title = ch.get('petName') or ch.get('activityDesc') or ch.get('activityName') or ch.get('file', '')
            safe = sanitize_filename(title)
            level_count = len(ch.get('levels', ch.get('teamIds', [])))
            moc_challenge += f'- [[{safe}|{title}]] — {ch["date"]}  '
            if level_count:
                moc_challenge += f'（{level_count}关）'
            moc_challenge += '\n'
        moc_challenge += '\n'

    with open(os.path.join(vault_root, '02-敌方阵容', '挑战活动索引.md'), 'w', encoding='utf-8') as f:
        f.write(moc_challenge)

    # ========== 4.5 生成游戏玩法与机制笔记 ==========
    game_dir = os.path.join(vault_root, '05-游戏机制')
    os.makedirs(game_dir, exist_ok=True)

    build_game_mechanics(vault_root, game_dir, pets, all_buffs)

    # ========== 5. 生成首页 ==========
    home = '---\ntype: home\ntitle: 奥奇传说知识库\ntags: [首页, moc]\n---\n\n'
    home += '# 🏰 奥奇传说知识库\n\n'
    home += '> 奥奇传说个人知识库 — 灵初精灵图鉴 & 敌方阵容关卡\n\n'
    home += '---\n\n'

    home += '## 📚 知识库导航\n\n'
    home += '| 分类 | 数量 | 入口 |\n'
    home += '|------|------|------|\n'
    home += f'| 灵初精灵 | {len(pets)} 只 | [[灵初精灵图鉴-属性]] · [[灵初精灵图鉴-职业]] |\n'
    home += f'| 敌方阵容 | {len(challenges)} 个 | [[挑战活动索引]] |\n'
    home += f'| Buff词典 | {len(all_buffs)} 个 | [[Buff词典]] |\n'
    home += '| 游戏机制 | 6篇 | [[游戏机制索引]] · [[属性克制关系]] · [[战斗流程]] |\n'
    home += '\n'

    home += '## 🔍 快速检索\n\n'
    home += '### 最新挑战活动\n\n'
    recent = sorted(challenges, key=lambda x: x['date'], reverse=True)[:5]
    for ch in recent:
        title = ch.get('petName') or ch.get('activityDesc') or ch.get('activityName') or ch.get('file', '未知活动')
        safe = sanitize_filename(title)
        home += f'- [[{safe}|{title}]] — {ch["date"]}\n'
    home += '\n'

    home += '### 属性一览\n\n'
    for elem in sorted(element_groups.keys()):
        home += f'- **{elem}**：{len(element_groups[elem])}只 — [[灵初精灵图鉴-属性#{elem}|查看列表]]\n'
    home += '\n'

    home += '---\n\n'
    home += '> 📅 数据更新：自动同步于游戏资源更新后\n'

    with open(os.path.join(vault_root, '00-入口', '首页.md'), 'w', encoding='utf-8') as f:
        f.write(home)

    # 也在 vault 根目录放一个首页
    with open(os.path.join(vault_root, '首页.md'), 'w', encoding='utf-8') as f:
        f.write(home)

    # ========== 6. README ==========
    readme = '# 奥奇传说知识库 (Obsidian Vault)\n\n'
    readme += '## 使用方法\n\n'
    readme += '用 Obsidian 打开本文件夹即可浏览知识库。\n\n'
    readme += '## 目录结构\n\n'
    readme += '```\n'
    readme += '奥奇传说知识库/\n'
    readme += '├── 首页.md              # Vault 入口页\n'
    readme += '├── 00-入口/             # 导航 MOC\n'
    readme += '│   └── 首页.md\n'
    readme += '├── 01-灵初精灵/         # 每只精灵独立笔记\n'
    readme += f'│   └── {len(pets)} 只精灵笔记\n'
    readme += '├── 02-敌方阵容/         # 每个挑战活动独立笔记\n'
    readme += f'│   ├── {len(challenges)} 个挑战活动笔记\n'
    readme += '│   └── 挑战活动索引.md\n'
    readme += '├── 03-Buff词典/         # Buff 效果说明 + 反向链接\n'
    readme += f'│   ├── Buff词典.md\n'
    readme += f'│   └── {len(all_buffs)} 个 Buff 笔记\n'
    readme += '├── 04-属性与职业/       # 分类索引 MOC\n'
    readme += '│   ├── 灵初精灵图鉴-属性.md\n'
    readme += '│   └── 灵初精灵图鉴-职业.md\n'
    readme += '├── 05-游戏机制/         # 战斗机制、属性克制、职业体系\n'
    readme += '│   ├── 游戏机制索引.md\n'
    readme += '│   ├── 属性克制关系.md\n'
    readme += '│   ├── 基础战斗属性.md\n'
    readme += '│   ├── 灵初核心机制.md\n'
    readme += '│   ├── 职业体系.md\n'
    readme += '│   └── 战斗流程.md\n'
    readme += '└── 99-附件/             # 图片等附件\n'
    readme += '```\n'

    with open(os.path.join(vault_root, 'README.md'), 'w', encoding='utf-8') as f:
        f.write(readme)

    print(f'Obsidian Vault 构建完成！')
    print(f'Vault 路径: {vault_root}')
    print(f'灵初精灵笔记: {len(pets)} 篇')
    print(f'敌方阵容笔记: {len(challenges)} 篇')
    print(f'Buff词典: {len(all_buffs)} 条')
    print(f'生成文件总数: {len(pets) + len(challenges) + len(all_buffs) + 6}')


def clean_html(text):
    """清理HTML标签和颜色标记"""
    text = re.sub(r'<br>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('$c0', '').replace('c$', '')
    text = text.replace('#V1#', '基础值')
    return text.strip()


def build_game_mechanics(vault_root, game_dir, pets, all_buffs):
    """生成游戏玩法与机制相关笔记"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_dir = os.path.join(script_dir, 'output', 'config')

    # 1. 属性克制关系
    race_config = load_json(os.path.join(config_dir, 'racetype_config.json'))
    races = race_config.get('race_type', [])

    build_element_guide(game_dir, races, pets, all_buffs)

    # 2. 基础战斗属性
    suling_config = load_json(os.path.join(config_dir, 'formation', 'lingchuskillsulingconfig.json'))
    build_base_stats_guide(game_dir, suling_config, all_buffs)

    # 3. 灵初核心机制
    build_lingchu_mechanics(game_dir, suling_config, all_buffs)

    # 4. 职业体系
    job_config = load_json(os.path.join(config_dir, 'jobtype_config.json'))
    build_job_guide(game_dir, job_config, pets)

    # 5. 战斗流程总览
    build_battle_flow(game_dir)

    # 6. 机制 MOC 索引
    build_mechanics_moc(game_dir)


def build_element_guide(game_dir, races, pets, all_buffs):
    """属性克制关系指南"""
    god_races = [r for r in races if 21 <= r['id'] <= 28]
    normal_races = [r for r in races if r['id'] <= 20]

    md = '---\ntype: guide\ntitle: 属性克制关系\ntags: [机制, 属性, 克制]\n---\n\n'
    md += '# 属性克制关系\n\n'
    md += '> 奥奇传说中属性分为普通属性（20种）和神属性（8种），神属性对普通属性有碾压优势。\n\n'
    md += '---\n\n'

    # 神属性之间的克制表
    md += '## 神属性克制表\n\n'
    md += '神属性共8种：神草、神水、神火、神暗、神光、神灵、神无极、神幻。\n\n'
    md += '| 攻击方\\\\被击方 | 神草 | 神水 | 神火 | 神暗 | 神光 | 神灵 | 神无极 | 神幻 |\n'
    md += '|-----------------|------|------|------|------|------|------|--------|------|\n'
    for a in god_races:
        row = f'| **{a["nm"]}** |'
        for d_ in god_races:
            m = a['rcs'][d_['id'] - 1]
            if m > 1.0:
                row += f' ✅{m:.1f} |'
            elif m < 1.0:
                row += f' ❌{m:.1f} |'
            else:
                row += f' {m:.1f} |'
        md += row + '\n'
    md += '\n'

    # 神属性克制规律
    md += '### 神属性克制规律\n\n'
    md += '- **神草** 克制 神火（1.5倍），被 神暗（0.5倍）克制\n'
    md += '- **神水** 克制 神暗（1.5倍），被 神火（1.5倍）克制\n'
    md += '- **神火** 克制 神水（1.5倍）、神幻（1.5倍），被 神草（1.5倍）克制\n'
    md += '- **神暗** 克制 神灵（1.5倍），被 神水（1.5倍）、神幻（1.5倍）克制\n'
    md += '- **神光** 克制 神灵（1.5倍），被 神幻（0.5倍）克制\n'
    md += '- **神灵** 克制 神暗（0.5倍反向？）、神光… （详见上表）\n'
    md += '- **神无极**：对所有属性均为1.3倍，不被任何属性克制（也不克制其他神属性）\n'
    md += '- **神幻** 克制 神光（1.5倍）、神暗（1.5倍）、神无极（1.5倍），被 神火（1.5倍）、神灵（0.5倍）克制\n\n'

    # 神属性 vs 普通属性
    md += '## 神属性 vs 普通属性\n\n'
    md += '所有神属性对**全部20种普通属性**均造成 **1.3倍** 伤害，且不被任何普通属性克制。\n\n'
    md += '> 💡 神属性精灵在对抗普通属性精灵时拥有天然优势，因此版本主流精灵多为神属性。\n\n'

    # 普通属性
    md += '## 普通属性克制\n\n'
    md += '普通属性共20种，克制关系较为复杂。以下为主要克制链：\n\n'
    md += '- **草** 克 火 · **水** 克 火/土 · **火** 克 水/龙 · **风** 克 暗 · **电** 克 机械/幻化\n'
    md += '- **土** 克 火 · **暗** 克 水/火/武 · **光** 克 水/龙 · **飞行** 克 电/美食\n'
    md += '- **机械** 克 超能 · **武** 克 草 · **美食** 克 恶魔 · **超能** 克 龙/美食/萌\n'
    md += '- **幻化** 克 龙/飞行 · **恶魔** 克 武/美食/战神 · **萌** 克 超能 · **战神** 克 电/机械\n\n'

    # 属性对应精灵数
    md += '## 灵初精灵属性分布\n\n'
    md += '| 属性 | 精灵数量 | 查看 |\n'
    md += '|------|---------|------|\n'
    elem_count = {}
    for pet in pets:
        e = pet.get('elementTypeName', '')
        elem_count[e] = elem_count.get(e, 0) + 1
    for elem in sorted(elem_count.keys()):
        md += f'| {elem} | {elem_count[elem]} | [[灵初精灵图鉴-属性#{elem}|查看列表]] |\n'
    md += '\n'

    with open(os.path.join(game_dir, '属性克制关系.md'), 'w', encoding='utf-8') as f:
        f.write(md)


def build_base_stats_guide(game_dir, suling_config, all_buffs):
    """基础战斗属性指南"""
    md = '---\ntype: guide\ntitle: 基础战斗属性\ntags: [机制, 战斗, 属性]\n---\n\n'
    md += '# 基础战斗属性\n\n'
    md += '> 影响战斗结果的核心数值属性及其作用机制。\n\n'
    md += '---\n\n'

    # 从suling_config提取
    data = suling_config.get('11', {})

    stats_map = {
        'bj': ('暴击率', 'bjE', 'bjT'),
        'fb': ('防暴率', 'fbE', 'fbT'),
        'mz': ('命中率', 'mzE', 'mzT'),
        'sb': ('闪避率', 'sbE', 'sbT'),
        'pj': ('破击率', 'pjE', 'pjT'),
        'gd': ('格挡率', 'gdE', 'gdT'),
    }

    for key, (name, desc_key, base_key) in stats_map.items():
        desc = data.get(desc_key, '')
        desc = clean_html(desc)
        base = data.get(base_key, '')
        if desc:
            md += f'## {name}\n\n'
            md += f'{desc}\n\n'
            if base:
                md += f'- **基础值**: {base}\n\n'

    # 气势
    md += '## 气势\n\n'
    md += '- 精灵通过**攻击**和**被攻击**积累气势\n'
    md += '- 气势达到**100点**（满气势）时，下次出手将释放**超杀技**\n'
    md += '- 超杀释放后气势重置，重新积累\n'
    md += '- 部分效果可直接提升/降低/锁定气势\n\n'

    with open(os.path.join(game_dir, '基础战斗属性.md'), 'w', encoding='utf-8') as f:
        f.write(md)


def build_lingchu_mechanics(game_dir, suling_config, all_buffs):
    """灵初核心机制指南"""
    data = suling_config.get('11', {})

    md = '---\ntype: guide\ntitle: 灵初核心机制\ntags: [机制, 灵初, 核心]\n---\n\n'
    md += '# 灵初核心机制\n\n'
    md += '> 灵初体系特有的战斗机制，是理解灵初精灵玩法的关键。\n\n'
    md += '---\n\n'

    # 强化之力
    qh_desc = clean_html(data.get('qhE', ''))
    md += '## [[强化之力|强化之力]]\n\n'
    md += f'{qh_desc}\n\n'

    # 抵抗之力
    dk_desc = clean_html(data.get('dkE', ''))
    md += '## [[抵抗之力|抵抗之力]]\n\n'
    md += f'{dk_desc}\n\n'

    # 灵初被动
    if '灵初被动' in all_buffs:
        md += '## [[灵初被动|灵初被动]]\n\n'
        md += f'{all_buffs["灵初被动"]}\n\n'

    # 灵盾
    if '灵盾' in all_buffs:
        md += '## [[灵盾|灵盾]]\n\n'
        md += f'{all_buffs["灵盾"]}\n\n'

    # 灵初正面 vs 灵初负面
    md += '## 灵初正面 / 灵初负面效果\n\n'
    md += '- **灵初正面效果**：需消耗[[强化之力|强化之力]]才能生效，否则失效\n'
    md += '- **灵初负面效果**：敌方施加时，可消耗[[抵抗之力|抵抗之力]]令其无效\n'
    md += '- 抵抗之力每个大回合开始时恢复至基础值\n\n'

    # 其他常见灵初机制
    lingchu_buffs = [k for k in all_buffs.keys() if any(kw in k for kw in ['灵力', '之力', '灵盾', '幻梦', '战略'])]
    if lingchu_buffs:
        md += '## 相关Buff索引\n\n'
        for b in sorted(lingchu_buffs):
            safe = sanitize_filename(b)
            md += f'- [[{safe}|{b}]]\n'
        md += '\n'

    with open(os.path.join(game_dir, '灵初核心机制.md'), 'w', encoding='utf-8') as f:
        f.write(md)


def build_job_guide(game_dir, job_config, pets):
    """职业体系指南"""
    jobs = job_config.get('job_type', [])

    md = '---\ntype: guide\ntitle: 职业体系\ntags: [机制, 职业]\n---\n\n'
    md += '# 职业体系\n\n'
    md += '> 精灵职业决定了其定位和战斗角色。灵初精灵拥有独立的职业体系。\n\n'
    md += '---\n\n'

    # 普通职业
    md += '## 普通职业（1-20）\n\n'
    basic_jobs = [j for j in jobs if j['id'] <= 20]
    md += '| ID | 职业名称 | 类型 | 说明 |\n'
    md += '|----|---------|------|------|\n'
    for j in basic_jobs:
        jtype = '物理' if j['type'] == 1 else '魔法' if j['type'] == 2 else '其他'
        desc = ''
        if j['id'] == 1: desc = '高物理输出，血少'
        elif j['id'] == 2: desc = '高魔法输出'
        elif j['id'] == 3: desc = '远程物理输出'
        elif j['id'] == 4: desc = '治疗辅助'
        elif j['id'] == 5: desc = '攻守兼备'
        elif j['id'] == 6: desc = '高防御高血量'
        elif j['id'] in [7, 8]: desc = '英雄技提供者'
        elif j['id'] in [9, 10]: desc = '可召唤召唤兽'
        elif j['id'] in [11, 12]: desc = '高血量高输出'
        elif j['id'] in [14, 15]: desc = '龙骑，可骑乘'
        md += f'| {j["id"]} | {j["nm"]} | {jtype} | {desc} |\n'
    md += '\n'

    # 神属性职业
    md += '## 神属性职业（21-43）\n\n'
    god_jobs = [j for j in jobs if 21 <= j['id'] <= 43]
    md += '| ID | 职业名称 | 类型 | 说明 |\n'
    md += '|----|---------|------|------|\n'
    for j in god_jobs:
        jtype = '物理' if j['type'] == 3 else '魔法' if j['type'] == 4 else '其他'
        jt = '神职业' if j['jobType'] == 1 else '超级英雄' if j['jobType'] == 2 else ''
        md += f'| {j["id"]} | {j["nm"]} | {jtype} | {jt} |\n'
    md += '\n'

    # 灵初职业
    md += '## 灵初职业\n\n'
    md += '灵初精灵拥有独立的职业ID体系（5100-5700区间），按等级分为：\n\n'
    job_cats = ['灵初基础职业', '灵初进阶职业', '灵初高级职业', '灵初精英职业', '灵初传说职业', '灵初神专职业']
    cat_count = {}
    for pet in pets:
        c = pet.get('jobCategory', '')
        cat_count[c] = cat_count.get(c, 0) + 1
    for cat in job_cats:
        cnt = cat_count.get(cat, 0)
        md += f'- **{cat}**：{cnt}只 — [[灵初精灵图鉴-职业#{cat}|查看]]\n'
    md += '\n'

    with open(os.path.join(game_dir, '职业体系.md'), 'w', encoding='utf-8') as f:
        f.write(md)


def build_battle_flow(game_dir):
    """战斗流程总览"""
    md = '---\ntype: guide\ntitle: 战斗流程\ntags: [机制, 战斗, 流程]\n---\n\n'
    md += '# 战斗流程\n\n'
    md += '> 奥奇传说回合制战斗的基本流程。\n\n'
    md += '---\n\n'

    md += '## 战斗开始前\n\n'
    md += '- 双方布阵（最多9个站位，3x3网格）\n'
    md += '- 选择英雄技（英雄精灵提供全队增益）\n'
    md += '- 契约兽、召唤兽配置确认\n\n'

    md += '## 回合流程\n\n'
    md += '### 1. 大回合开始\n'
    md += '- 恢复气势（灵初精灵恢复满气势，第1回合除外）\n'
    md += '- 抵抗之力恢复至基础值\n'
    md += '- 各类回合开始效果触发\n\n'
    md += '### 2. 双方完整回合交替进行\n'
    md += '- 按速度决定出手顺序\n'
    md += '- 每次出手前/后触发各类效果\n'
    md += '- 普攻/超杀/通灵技等释放\n'
    md += '- 死亡判定与死亡触发效果\n\n'
    md += '### 3. 双方完整回合结束\n'
    md += '- 回合结束效果触发（如Buff持续时间减少）\n'
    md += '- 特殊计数效果（如暴击率阈值奖励）\n\n'

    md += '## 核心战斗概念\n\n'
    md += '### 气势与超杀\n'
    md += '- 攻击和被攻击获得气势\n'
    md += '- 满气势后下次出手释放超杀技\n'
    md += '- 超杀通常拥有更强的效果和倍率\n\n'

    md += '### 通灵技\n'
    md += '- 通灵师特有机制\n'
    md += '- 收集通灵之魂，集满后触发通灵\n'
    md += '- 通灵后变身为通灵形态，获得额外能力并立刻出手\n\n'

    md += '### 英雄技\n'
    md += '- 英雄精灵提供全队增益\n'
    md += '- 战斗开始时触发，有激活条件\n\n'

    md += '### 召唤与契约\n'
    md += '- 召唤师可召唤召唤兽参战\n'
    md += '- 契约兽可与主精灵契约，获得额外效果\n\n'

    md += '## 伤害计算\n'
    md += '- 属性克制倍率\n'
    md += '- 攻击 vs 防御\n'
    md += '- 暴击（2倍伤害，可被防暴抵抗）\n'
    md += '- 格挡（减半伤害，可被破击抵抗）\n'
    md += '- 闪避（完全免伤，可被命中抵抗）\n'
    md += '- 各类增伤/减伤效果叠加\n\n'

    with open(os.path.join(game_dir, '战斗流程.md'), 'w', encoding='utf-8') as f:
        f.write(md)


def build_mechanics_moc(game_dir):
    """机制 MOC 索引页"""
    md = '---\ntype: moc\ntitle: 游戏机制索引\ntags: [moc, 机制]\n---\n\n'
    md += '# 🎮 游戏机制索引\n\n'
    md += '> 奥奇传说核心玩法与战斗机制知识库。\n\n'
    md += '---\n\n'

    md += '## 战斗核心\n\n'
    md += '- [[属性克制关系]] — 20种普通属性 + 8种神属性的克制规则\n'
    md += '- [[基础战斗属性]] — 暴击、防暴、闪避、命中、破击、格挡\n'
    md += '- [[战斗流程]] — 回合制战斗完整流程与核心概念\n'
    md += '- [[职业体系]] — 普通职业、神职业、灵初职业分类\n\n'

    md += '## 灵初专属\n\n'
    md += '- [[灵初核心机制]] — 强化之力、抵抗之力、灵初被动等核心概念\n'
    md += '- [[Buff词典]] — 所有Buff效果说明\n\n'

    md += '## 进阶内容\n\n'
    md += '- [[挑战活动索引]] — 各挑战活动敌方阵容与规则\n'
    md += '- [[灵初精灵图鉴-属性]] — 按属性分类的精灵图鉴\n'
    md += '- [[灵初精灵图鉴-职业]] — 按职业分类的精灵图鉴\n\n'

    with open(os.path.join(game_dir, '游戏机制索引.md'), 'w', encoding='utf-8') as f:
        f.write(md)


def replace_buff_links(text, all_buffs):
    """将文本中的 [Buff名] 替换为 [[Buff名|Buff名]] 双向链接"""
    def replace_match(m):
        name = m.group(1)
        # 去掉 ·后缀（如 疗愈衰减·90% → 疗愈衰减）
        base = name.split('·')[0]
        if base in all_buffs:
            safe = sanitize_filename(base)
            return f'[[{safe}|{name}]]'
        return m.group(0)

    return re.sub(r'\[([^\[\]]+)\]', replace_match, text)


def find_pet_appearances(race_id, challenges):
    """查找某精灵在哪些挑战的哪些阵容中出场"""
    result = {}
    for ch in challenges:
        title = ch.get('petName') or ch.get('activityDesc') or ch.get('activityName') or ch.get('file', '未知活动')
        teams = []
        for tid_str, fm in ch.get('formations', {}).items():
            if not fm:
                continue
            for p in fm.get('pets', []):
                if p.get('raceId') == race_id:
                    teams.append(int(tid_str))
                    break
        if teams:
            result[title] = teams
    return result


if __name__ == '__main__':
    build_obsidian_vault()
