"""cloths.json -> the CLOTHS array embedded in kente-loom.html.

usage: python3 -I assemble.py <out dir>
"""
import json, re, sys

OUT = sys.argv[1]
N = 40
# Metallic (lurex) yarns, marked by eye against the photos: palette indices per cloth.
LUREX = {
    'A': [1], 'C': [0], 'D': [7], 'E': [0, 1, 2], 'F': [1, 2, 5], 'G': [], 'H': [2, 5],
    'J': [2], 'K': [3, 7, 9, 10], 'L': [4, 8], 'M': [4], 'N': [0, 2], 'O': [0, 3], 'P': [0, 1, 3], 'Q': [],
}
# Colours split only by uneven light in the photo: from -> to.
MERGE = {'J': {8: 0, 9: 0}}
LETTERS = 'abcdefghijklmnop'

cloths = json.load(open(f'{OUT}/cloths.json'))
items = []
for c in cloths:
    m = MERGE.get(c['key'], {})
    blocks = [[m.get(int(ch, 16), int(ch, 16)) for ch in b] for b in c['blocks']]
    used = sorted({v for b in blocks for v in b})
    remap = {v: k for k, v in enumerate(used)}
    pal = [c['pal'][v][1:] for v in used]
    lx = ''.join(LETTERS[remap[v]] for v in LUREX[c['key']] if v in remap)
    rle = []
    for b in blocks:
        s, i = '', 0
        while i < len(b):
            j = i
            while j < len(b) and b[j] == b[i]:
                j += 1
            s += LETTERS[remap[b[i]]] + (str(j - i) if j - i > 1 else '')
            i = j
        rle.append(s)
    items.append(f"\t\t{{ name: '{c['name']}', photo: {c['photo']}, pal: '{' '.join(pal)}', lurex: '{lx}',\n"
                 + '\t\t\tblocks: [\n' + ''.join(f"\t\t\t\t'{s}',\n" for s in rle) + '\t\t\t] },\n')
js = ''.join(items)
open(f'{OUT}/cloths.js', 'w').write(js)
print(len(js), 'chars')
