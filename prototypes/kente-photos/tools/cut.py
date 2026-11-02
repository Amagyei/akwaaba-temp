"""Cut each cloth's blocks out of its photo and stack them into one loom strip.

usage: python3 -I cut.py <photos dir> <out dir>
Writes <out>/strips.js (the CLOTHS entries for kente-loom.html) and <out>/strip-<key>.jpg.
"""
import base64, io, math, os, sys
from PIL import Image, ImageEnhance, ImageOps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cloths import CLOTHS

SRC, OUT = sys.argv[1], sys.argv[2]
W = 240                    # strip width in px
H = round(W * 4.18)        # strip length: the visible part of the loom
SEP = 3                    # thin white line between blocks
WHITE = (242, 238, 228)
THREADS = 40               # warp threads drawn above the fell
os.makedirs(OUT, exist_ok=True)


def cut(im, q, upright):
    tl, tr, br, bl = q
    across = (math.dist(tl, tr) + math.dist(bl, br)) / 2
    along = (math.dist(tl, bl) + math.dist(tr, br)) / 2
    if upright:
        return im.transform((W, round(W * along / across)), Image.QUAD, data=(*tl, *bl, *br, *tr), resample=Image.BICUBIC)
    # a horizontal row: straighten it, then turn it 90° clockwise to run down the strip
    row = im.transform((round(W * across / along), W), Image.QUAD, data=(*tl, *bl, *br, *tr), resample=Image.BICUBIC)
    return row.transpose(Image.ROTATE_270)


hexc = lambda c: '%02X%02X%02X' % tuple(c)
entries = []
for key, name, photo, quads, *opt in CLOTHS:
    opt = opt[0] if opt else {}
    src = photo if isinstance(photo, str) else f'cloth-photo-{photo:02d}.jpg'
    im = Image.open(f'{SRC}/{src}').convert('RGB')
    strip, y = Image.new('RGB', (W, H), WHITE), 0
    for q in quads:
        if y >= H:
            break
        block = ImageOps.autocontrast(cut(im, q, opt.get('upright')), cutoff=.5)
        strip.paste(block, (0, y))
        y += block.height + SEP
    assert y >= H, f'{key}: blocks fill only {y}px of {H}'
    strip = ImageEnhance.Color(strip).enhance(1.08)
    strip.save(f'{OUT}/strip-{key}.jpg', quality=80)
    buf = io.BytesIO()
    strip.save(buf, 'JPEG', quality=66, optimize=True, progressive=True)
    # warp threads above the fell carry the colours of the strip's first rows
    top = strip.crop((0, 0, W, 8)).resize((THREADS, 1), Image.BOX)
    warp = ' '.join(hexc(top.getpixel((x, 0))) for x in range(THREADS))
    # bobbin: the cloth's most used colour
    pal = strip.quantize(6)
    weft = pal.getpalette()[3 * max(range(6), key=lambda i: pal.histogram()[i]):][:3]
    entries.append(f"\t\t{{ name: '{name}', photo: '{src}', weft: '#{hexc(weft)}',\n"
                   f"\t\t\twarp: '{warp}',\n"
                   f"\t\t\tsrc: 'data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}' }},\n")
    print(key, name, len(buf.getvalue()) // 1024, 'KB')
open(f'{OUT}/strips.js', 'w').write(''.join(entries))
