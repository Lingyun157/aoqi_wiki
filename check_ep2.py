import json

with open(r'd:\aoqi\aoqi\敌方阵容关卡图鉴\data\enemy_formations.json', encoding='utf-8') as f:
    data = json.load(f)

# Find the challenge with formation 33855 (first one)
for c in data:
    formations = c.get('formations', {})
    if '33855' in formations:
        fm = formations['33855']
        print(f"Challenge: {c.get('petName', c.get('file', '?'))}")
        print(f"Formation extraProperty (formation level): {repr(fm.get('extraProperty', ''))}")
        print(f"\nPets extraProperty values:")
        for pet in fm.get('pets', []):
            ep = pet.get('extraProperty', '')
            if ep:
                print(f"  pet id={pet.get('raceId')} name={pet.get('name')}: {ep}")
        break
