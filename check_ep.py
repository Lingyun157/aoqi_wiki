import json

with open(r'd:\aoqi\aoqi\敌方阵容关卡图鉴\data\enemy_formations.json', encoding='utf-8') as f:
    data = json.load(f)

for c in data[:10]:
    name = c.get('petName', c.get('file', '?'))
    formations = c.get('formations', {})
    ls = c.get('levelStructure', [])
    print(f'=== {name} ===')
    print(f'  levelStructure: {len(ls)} levels')
    print(f'  formations keys: {list(formations.keys())[:5]}')
    for k, v in list(formations.items())[:3]:
        ep = v.get('extraProperty', '')
        print(f'  Key={k}, extraProperty={repr(ep)}')
        pets = v.get('pets', [])
        print(f'    pets count: {len(pets)}')
        if pets:
            print(f'    first pet keys: {list(pets[0].keys())}')
    print()
