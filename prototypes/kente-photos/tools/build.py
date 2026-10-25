"""Turn photo crops into compact loom-strip data.

usage: python3 -I build.py <photos dir> <out dir>
Writes <out>/cloths.json and preview PNGs (photo crops beside the recreation).
"""
import colorsys, json, math, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cloths import CLOTHS

SRC, OUT = sys.argv[1], sys.argv[2]
N = 40                      # cells across the strip
SUB = 4                     # pixels per cell side when voting
LEN = 4.18                  # strip length in strip-widths
K = 12                      # k-means clusters per cloth, before merging
MERGE = 11                  # merge clusters closer than this (CIE76)
CHARS = '0123456789'
os.makedirs(OUT, exist_ok=True)


def rectify(im, q, h_px):
    (tl, tr, br, bl) = q
    w = (math.dist(tl, tr) + math.dist(bl, br)) / 2
    h = (math.dist(tl, bl) + math.dist(tr, br)) / 2
    ratio = w / h
    out = im.transform((round(h_px * ratio), h_px), Image.QUAD,
                       data=(*tl, *bl, *br, *tr), resample=Image.BICUBIC)
    return out.transpose(Image.ROTATE_270), ratio   # 90° clockwise: row runs down the strip


def lift(rgb, s_mul=1.15):
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    r, g, b = colorsys.hls_to_rgb(h, l, min(1, s * s_mul))
    return tuple(round(c * 255) for c in (r, g, b))


def despeckle(grid):
    rows = len(grid)
    # isolated cells take their neighbours' colour
    out = [r[:] for r in grid]
    for y in range(rows):
        for x in range(N):
            nb = [grid[yy][xx] for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1))
                  if 0 <= yy < rows and 0 <= xx < N]
            if grid[y][x] not in nb:
                out[y][x] = max(set(nb), key=nb.count)
    # then mottled cells (at most one like neighbour of eight) follow a clear majority
    for _ in range(2):
        g, out = out, [r[:] for r in out]
        for y in range(rows):
            for x in range(N):
                nb = [g[yy][xx] for yy in (y - 1, y, y + 1) for xx in (x - 1, x, x + 1)
                      if (yy, xx) != (y, x) and 0 <= yy < rows and 0 <= xx < N]
                m = max(set(nb), key=nb.count)
                if nb.count(g[y][x]) <= 1 and nb.count(m) * 2 > len(nb):
                    out[y][x] = m
    return out


def to_lab(rgb):
    c = rgb / 255.0
    c = np.where(c > .04045, ((c + .055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[.4124, .3576, .1805], [.2126, .7152, .0722], [.0193, .1192, .9505]]).T
    xyz /= np.array([.9505, 1.0, 1.089])
    f = np.where(xyz > .008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], 1)


def kmeans(x, k, seed=1, iters=30):
    rng = np.random.default_rng(seed)
    cent = [x[rng.integers(len(x))]]
    for _ in range(k - 1):                               # k-means++ seeding
        d = np.min(((x[:, None] - np.array(cent)[None]) ** 2).sum(-1), 1)
        cent.append(x[rng.choice(len(x), p=d / d.sum())])
    cent = np.array(cent)
    for _ in range(iters):
        lab = np.argmin(((x[:, None] - cent[None]) ** 2).sum(-1), 1)
        cent = np.array([x[lab == j].mean(0) if (lab == j).any() else cent[j] for j in range(k)])
    return cent


result = []
for key, name, photo, quads, *opt in CLOTHS:
    opt = opt[0] if opt else {}
    im = Image.open(f'{SRC}/cloth-photo-{photo:02d}.jpg').convert('RGB')
    crops, raws, blocks, used = [], [], [], 0.0
    for q in quads:
        if used >= LEN - .05:
            break
        crop, ratio = rectify(im, q, N * SUB * 2)
        rows = max(1, round(N * min(ratio, LEN - used)))
        crop = crop.crop((0, 0, crop.width, round(crop.height * rows / round(N * ratio))))
        raws.append(crop)
        # smooth out lurex glints and thread texture before sampling
        crops.append(crop.filter(ImageFilter.MedianFilter(5)).resize((N * SUB, rows * SUB), Image.BOX))
        blocks.append(rows)
        used += rows / N
    px = np.concatenate([np.asarray(c, float).reshape(-1, 3) for c in crops])
    lab = to_lab(px)
    sample = lab[np.random.default_rng(0).choice(len(lab), min(len(lab), 12000), replace=False)]
    cent = kmeans(sample, K)
    while True:                                          # merge near-duplicate clusters
        d = np.sqrt(((cent[:, None] - cent[None]) ** 2).sum(-1)) + np.eye(len(cent)) * 1e9
        i, j = np.unravel_index(np.argmin(d), d.shape)
        if d[i, j] >= opt.get('merge', MERGE):
            break
        cent = np.delete(np.vstack([cent, (cent[i] + cent[j]) / 2]), [i, j], 0)
    labels = np.argmin(((lab[:, None] - cent[None]) ** 2).sum(-1), 1)
    # majority vote per cell
    grids, off = [], 0
    for rows in blocks:
        L = labels[off:off + rows * SUB * N * SUB].reshape(rows, SUB, N, SUB).transpose(0, 2, 1, 3).reshape(rows, N, -1)
        off += rows * SUB * N * SUB
        g = [[int(np.bincount(L[y, x], minlength=len(cent)).argmax()) for x in range(N)] for y in range(rows)]
        grids.append(despeckle(g))
    flat = [v for g in grids for r in g for v in r]
    order = sorted(set(flat), key=lambda v: -flat.count(v))
    remap = {v: k for k, v in enumerate(order)}
    colours, spark = [], []
    for v in order:
        sel = labels == v
        colours.append(lift(tuple(int(c) for c in np.median(px[sel], 0))))
        # lurex: share of glints (pixels far brighter than their cell) in this colour's cells
        hits = tot = 0
        for g, raw in zip(grids, raws):
            a = np.asarray(raw.convert('L'), float)
            ch, cw = a.shape[0] / len(g), a.shape[1] / N
            for y, r in enumerate(g):
                for x, c in enumerate(r):
                    if c != v:
                        continue
                    cell = a[round(y * ch):round((y + 1) * ch), round(x * cw):round((x + 1) * cw)]
                    hits += (cell > np.median(cell) + 50).mean()
                    tot += 1
        spark.append(round(hits / max(tot, 1), 3))
    data = {
        'key': key, 'name': name, 'photo': photo,
        'pal': ['#%02X%02X%02X' % c for c in colours],
        'spark': spark,
        'blocks': [''.join('0123456789abcdef'[remap[v]] for r in g for v in r) for g in grids],
    }
    result.append(data)

    S, sep = 8, 3
    H = sum(len(b) // N for b in data['blocks']) * S + sep * (len(blocks) - 1)
    prev = Image.new('RGB', (N * S * 2 + 20, H), 'white')
    d = ImageDraw.Draw(prev)
    y = 0
    for raw, blk in zip(raws, data['blocks']):
        rows = len(blk) // N
        prev.paste(raw.resize((N * S, rows * S)), (0, y))
        for k, ch in enumerate(blk):
            cx, cy = k % N, k // N
            col = data['pal'][int(ch, 16)]
            d.rectangle([N * S + 20 + cx * S, y + cy * S, N * S + 20 + (cx + 1) * S - 1, y + (cy + 1) * S - 1], fill=col)
        y += rows * S + sep
    prev.save(f'{OUT}/prev-{key}.png')
    print(key, name, 'rows', [len(b) // N for b in data['blocks']], 'n', len(order), 'spark', spark)

json.dump(result, open(f'{OUT}/cloths.json', 'w'))
