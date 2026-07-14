import json
import os
import re


def load_json(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def parse_skill_desc(desc_str):
    """解析技能描述，格式为：技能名称|技能描述|代码"""
    if not desc_str:
        return {'name': '', 'description': ''}
    parts = desc_str.split('|')
    if len(parts) >= 2:
        return {'name': parts[0], 'description': parts[1]}
    return {'name': desc_str, 'description': ''}


def clean_html(text):
    """清理HTML标签，保留纯文本"""
    if not text:
        return ''
    # 替换<br>为换行
    text = text.replace('<br>', '\n')
    # 移除HTML标签
    text = re.sub(r'<[^>]+>', '', text)
    # 清理特殊标记如$c0 c$
    text = text.replace('$c0', '').replace('c$', '')
    # 清理#pet#、#V1#等占位符
    text = text.replace('#pet#', '自身')
    text = re.sub(r'#V\d+#', 'X', text)
    # 清理 ARG 占位符
    text = text.replace('ARG', 'X')
    return text.strip()


def parse_element_type(element_str, race_map):
    """解析属性类型 - 使用 racetype_config 中的有效属性ID"""
    if not element_str:
        return '未知'
    parts = element_str.replace(',', '-').split('-')
    names = []
    for p in parts:
        p = p.strip()
        if p in race_map:
            names.append(race_map[p])
        elif p:
            names.append(f'属性{p}')
    return '/'.join(names) if names else element_str


def classify_job(job_type_id):
    """职业分类"""
    if job_type_id == 999:
        return '特殊职业'
    if 5100 <= job_type_id < 5200:
        return '灵初基础职业'
    if 5200 <= job_type_id < 5300:
        return '灵初进阶职业'
    if 5300 <= job_type_id < 5400:
        return '灵初高级职业'
    if 5400 <= job_type_id < 5500:
        return '灵初精英职业'
    if 5500 <= job_type_id < 5600:
        return '灵初传说职业'
    if 5600 <= job_type_id < 5700:
        return '灵初神专职业'
    return '其他职业'


def build_buff_map(entry_config):
    """从 entry_config 构建 buff 名称到描述的映射"""
    buff_map = {}
    for entry in entry_config:
        name = entry.get('n', '')
        desc = entry.get('d', '')
        if name and desc:
            buff_map[name] = desc
    return buff_map


def extract_buff_names(text):
    """从文本中提取 [buff名称] 形式的 buff 名（去掉 ·后缀）"""
    if not text:
        return []
    matches = re.findall(r'\[([^\[\]]+)\]', text)
    result = []
    seen = set()
    for m in matches:
        base = m.split('·')[0].strip()
        if base and base not in seen:
            seen.add(base)
            result.append(base)
    return result


def find_pet_icon(race_id, peticon_root):
    """查找精灵图标

    Returns:
        dict: {
            'hasIcon': bool,
            'iconType': str,  # static/large/fang/fight/rectangle/background/battleaction
            'iconPath': str,  # 相对路径
            'iconAbsPath': str,  # 绝对路径
        }
    """
    # 按优先级查找各种类型的图标
    icon_types = [
        ('static立绘', 'static', 'peticon{}.png'),
        ('large大图', 'large', 'peticon{}.png'),
        ('fang方形', 'fang', 'peticon{}.png'),
        ('fight战斗', 'fight', 'peticon{}.png'),
        ('rectangle长方', 'rectangle', 'peticon{}.png'),
        ('background背景', 'background', 'peticon{}.png'),
    ]

    for type_name, subdir, pattern in icon_types:
        rel_path = f'peticon/{subdir}/{pattern.format(race_id)}'
        abs_path = os.path.join(peticon_root, subdir, pattern.format(race_id))
        if os.path.exists(abs_path):
            return {
                'hasIcon': True,
                'iconType': type_name,
                'iconRelPath': rel_path,
                'iconAbsPath': abs_path,
            }

    # 检查 battleaction
    ba_path = os.path.join(os.path.dirname(peticon_root), 'battleaction', f'action{race_id}.png')
    if os.path.exists(ba_path):
        return {
            'hasIcon': True,
            'iconType': 'battleaction动作',
            'iconRelPath': f'battleaction/action{race_id}.png',
            'iconAbsPath': ba_path,
        }

    return {
        'hasIcon': False,
        'iconType': None,
        'iconRelPath': None,
        'iconAbsPath': None,
    }


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.join(script_dir, 'output', 'config')
    output_dir = os.path.join(script_dir, '灵初精灵知识库')
    peticon_root = os.path.join(script_dir, 'output', 'peticon')

    os.makedirs(output_dir, exist_ok=True)

    pet_dict_path = os.path.join(base_dir, 'pet', 'petdictionarydata.json')
    skill_desc_path = os.path.join(base_dir, 'battleconfig', 'skill_desc_config.json')
    racetype_path = os.path.join(base_dir, 'racetype_config.json')
    entry_config_path = os.path.join(base_dir, 'battleconfig', 'entry_config.json')

    pet_dict = load_json(pet_dict_path) if os.path.exists(pet_dict_path) else {}
    skill_desc = load_json(skill_desc_path) if os.path.exists(skill_desc_path) else {}
    racetype_config = load_json(racetype_path) if os.path.exists(racetype_path) else {}
    entry_config = load_json(entry_config_path) if os.path.exists(entry_config_path) else []

    # 构建属性映射
    race_map = {}
    for race in racetype_config.get('race_type', []):
        race_map[str(race['id'])] = race['nm']

    # 构建 buff 描述映射
    buff_map = build_buff_map(entry_config)

    lingchu_pets = []

    for race_id, pet_info_arr in pet_dict.items():
        name = pet_info_arr[1] if len(pet_info_arr) > 1 else ''
        # 过滤掉特殊精灵（ID >= 10000，如皮肤、挑战boss等变体）
        if '灵初' in name and int(race_id) < 10000:
            job_type_id = pet_info_arr[7] if len(pet_info_arr) > 7 else 0
            # 修复：使用索引 [8] 作为属性类型（始终为有效的 racetype ID）
            # 索引 [9] 在部分灵初精灵中存储的是其他数据（如职业ID），不可作为属性
            element_type_str = pet_info_arr[8] if len(pet_info_arr) > 8 else ''

            # 技能ID
            normal_skill_id = pet_info_arr[14] if len(pet_info_arr) > 14 else 0
            super_skill_id = pet_info_arr[15] if len(pet_info_arr) > 15 else 0
            tongling_skill_id = pet_info_arr[37] if len(pet_info_arr) > 37 else 0
            tianming_skill_id = pet_info_arr[46] if len(pet_info_arr) > 46 else 0

            # 获取技能描述
            normal_skill = parse_skill_desc(skill_desc.get(str(normal_skill_id), ''))
            super_skill = parse_skill_desc(skill_desc.get(str(super_skill_id), ''))
            tongling_skill = parse_skill_desc(skill_desc.get(str(tongling_skill_id), ''))
            tianming_skill = parse_skill_desc(skill_desc.get(str(tianming_skill_id), ''))

            # 合并技能描述
            skills = []
            if normal_skill['name'] or normal_skill['description']:
                skills.append({
                    'type': '普攻',
                    'name': normal_skill['name'],
                    'description': clean_html(normal_skill['description'])
                })
            if super_skill['name'] or super_skill['description']:
                skills.append({
                    'type': '超杀',
                    'name': super_skill['name'],
                    'description': clean_html(super_skill['description'])
                })
            if tongling_skill['name'] or tongling_skill['description']:
                skills.append({
                    'type': '通灵技',
                    'name': tongling_skill['name'],
                    'description': clean_html(tongling_skill['description'])
                })

            tianming_skill_data = None
            if tianming_skill['name'] or tianming_skill['description']:
                tianming_skill_data = {
                    'name': tianming_skill['name'],
                    'description': clean_html(tianming_skill['description'])
                }

            # 提取所有技能描述中提到的 buff 名称
            all_texts = [s['description'] for s in skills]
            if tianming_skill_data:
                all_texts.append(tianming_skill_data['description'])
            buff_names_in_pet = []
            seen_buffs = set()
            for txt in all_texts:
                for bn in extract_buff_names(txt):
                    if bn not in seen_buffs:
                        seen_buffs.add(bn)
                        buff_names_in_pet.append(bn)

            # 查找 buff 描述
            related_buffs = []
            for bn in buff_names_in_pet:
                if bn in buff_map:
                    related_buffs.append({
                        'name': bn,
                        'description': clean_html(buff_map[bn])
                    })

            pet_detail = {
                'raceId': int(race_id),
                'name': name,
                'jobTypeId': job_type_id,
                'jobCategory': classify_job(job_type_id),
                'elementTypeId': element_type_str,
                'elementTypeName': parse_element_type(element_type_str, race_map),
                'skills': skills,
                'tianmingSkill': tianming_skill_data,
                'relatedBuffs': related_buffs,
            }
            # 添加图标信息
            icon_info = find_pet_icon(int(race_id), peticon_root)
            pet_detail.update(icon_info)
            lingchu_pets.append(pet_detail)

    lingchu_pets.sort(key=lambda x: x['raceId'])

    # 创建输出目录
    os.makedirs(os.path.join(output_dir, 'data'), exist_ok=True)
    os.makedirs(os.path.join(output_dir, 'markdown'), exist_ok=True)

    # 保存完整JSON数据
    with open(os.path.join(output_dir, 'data', 'lingchu_pets.json'), 'w', encoding='utf-8') as f:
        json.dump(lingchu_pets, f, ensure_ascii=False, indent=2)

    # 生成Markdown知识库
    md_content = '# 奥奇传说 - 灵初精灵知识库\n\n'
    md_content += f'> 共收录 **{len(lingchu_pets)}** 只灵初精灵\n\n'
    md_content += '---\n\n'

    md_content += '## 目录\n\n'
    for i, pet in enumerate(lingchu_pets, 1):
        md_content += f'{i}. [{pet["name"]}](#{pet["raceId"]}) - {pet["elementTypeName"]} / {pet["jobCategory"]}\n'

    md_content += '\n---\n\n'

    for pet in lingchu_pets:
        md_content += f'## <a id="{pet["raceId"]}"></a>{pet["name"]}\n\n'

        # 显示图标
        if pet.get('hasIcon') and pet.get('iconAbsPath'):
            # 使用绝对路径的 file URL
            icon_url = 'file:///' + pet['iconAbsPath'].replace('\\', '/')
            md_content += f'![{pet["name"]}]({icon_url})\n\n'

        md_content += f'- **精灵ID**: {pet["raceId"]}\n'
        md_content += f'- **精灵名称**: {pet["name"]}\n'
        md_content += f'- **属性**: {pet["elementTypeName"]}\n'
        md_content += f'- **职业**: {pet["jobCategory"]} (ID: {pet["jobTypeId"]})\n'
        if pet.get('hasIcon'):
            md_content += f'- **图标类型**: {pet.get("iconType", "未知")}\n'
        md_content += '\n'

        md_content += '### 技能描述\n\n'
        for skill in pet['skills']:
            md_content += f'**{skill["type"]}：{skill["name"]}**\n\n'
            md_content += f'{skill["description"]}\n\n'

        if pet['tianmingSkill']:
            md_content += '### 天命技描述\n\n'
            md_content += f'**{pet["tianmingSkill"]["name"]}**\n\n'
            md_content += f'{pet["tianmingSkill"]["description"]}\n\n'

        if pet['relatedBuffs']:
            md_content += '### 相关Buff说明\n\n'
            for buff in pet['relatedBuffs']:
                md_content += f'**[{buff["name"]}]**\n\n'
                md_content += f'{buff["description"]}\n\n'

        md_content += '---\n\n'

    with open(os.path.join(output_dir, 'markdown', '灵初精灵大全.md'), 'w', encoding='utf-8') as f:
        f.write(md_content)

    # 生成README
    icon_count = sum(1 for p in lingchu_pets if p.get('hasIcon'))

    readme = '# 灵初精灵知识库\n\n'
    readme += '## 知识库说明\n\n'
    readme += '本知识库包含奥奇传说中所有灵初系列精灵的核心数据，用于构建专属智能体。\n\n'
    readme += '## 数据统计\n\n'
    readme += f'- **精灵总数**: {len(lingchu_pets)} 只\n'
    readme += f'- **有图标精灵**: {icon_count} 只 ({icon_count/len(lingchu_pets)*100:.1f}%)\n' if lingchu_pets else '- **有图标精灵**: 0 只 (0%)\n'
    readme += f'- **数据更新时间**: 2026-07-05\n'
    readme += f'- **数据来源**: 奥奇传说游戏资源包\n\n'
    readme += '## 文件结构\n\n'
    readme += '```\n'
    readme += '灵初精灵知识库/\n'
    readme += '├── README.md                # 本文件\n'
    readme += '├── data/\n'
    readme += '│   └── lingchu_pets.json    # 精灵数据（含技能和天命技）\n'
    readme += '└── markdown/\n'
    readme += '    └── 灵初精灵大全.md       # Markdown格式的精灵图鉴\n'
    readme += '```\n\n'
    readme += '## 数据字段说明\n\n'
    readme += '| 字段 | 说明 |\n'
    readme += '|------|------|\n'
    readme += '| raceId | 精灵ID |\n'
    readme += '| name | 精灵名称 |\n'
    readme += '| jobTypeId | 职业类型ID |\n'
    readme += '| jobCategory | 职业分类 |\n'
    readme += '| elementTypeId | 属性类型ID |\n'
    readme += '| elementTypeName | 属性类型名称 |\n'
    readme += '| skills | 技能列表（普攻、超杀、通灵技） |\n'
    readme += '| tianmingSkill | 天命技（名称和描述） |\n\n'

    with open(os.path.join(output_dir, 'README.md'), 'w', encoding='utf-8') as f:
        f.write(readme)

    print(f'知识库构建完成！')
    print(f'输出目录: {output_dir}')
    print(f'精灵总数: {len(lingchu_pets)}')
    print(f'\n生成的文件:')
    print(f'  - data/lingchu_pets.json (精灵数据)')
    print(f'  - markdown/灵初精灵大全.md (Markdown图鉴)')
    print(f'  - README.md (知识库说明)')


if __name__ == '__main__':
    main()
