import json

with open(r'D:\aoqi\敌方阵容关卡图鉴\data\enemy_formations.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 检查没有 levelStructure 的挑战
no_structure = [c for c in data if 'levelStructure' not in c or not c['levelStructure']]
print(f'没有 levelStructure 的挑战数: {len(no_structure)}')
if no_structure:
    print('示例:')
    for c in no_structure[:3]:
        print(f"  {c.get('petName', c.get('file'))}: levels={len(c.get('levels', []))}, formations={len(c.get('formations', {}))}")

# 检查 slotId > 9 的情况
print('\n--- 检查 slotId > 9 的情况 ---')
for c in data[:5]:
    for tid, fm in c.get('formations', {}).items():
        for p in fm.get('pets', []):
            if p['slotId'] > 9:
                print(f"{c.get('petName')} - teamId={tid}: {p['name']} slotId={p['slotId']} contract={p.get('contractPetSlotId')}")
