# Kente cloth photos

Source photos for `../kente-loom.html`, a static loom prototype that rotates between kente cloths.

- `photo-sample-1..4.jpg`: the first cloth. It is already recreated by hand as the "Photo sample" weave (`photoCloth()` in the page).
- `cloth-photo-01..20.jpg`: the owner's photos, in the order they sent them. No shop names or proverbs were given. Use descriptive placeholder names, and leave the meaning line blank.

Each cloth is many ~4-inch weft-faced strips sewn side by side. In these photos the cloths hang over a stand, so each **horizontal row is one strip**. Thin white lines split a row into blocks, and stitched seams separate the rows. To put a block on the loom, rotate it 90° clockwise so the strip runs top to bottom.

## Cloths on the loom

All 15 cloths below are recreated in the page, after the hand-drawn "Photo sample". The names are placeholders. Photos 05, 06 (right) and 07 show the same blue stole, so the earlier "I" group is merged into H.

| Cloth | Placeholder name | Photo used | Also in |
|---|---|---|---|
| A | Lattice and Steps | 15 | 01 left, 02 left, 04 left |
| C | Silver Steps | 19 | 02 right |
| D | Black and Gold | 01 right | none |
| E | Emerald Triangles | 20 | 03 left |
| F | Bronze Checks | 18 | 03 right |
| G | Golden Trees | 17 | 04 right |
| H | Royal Blue Stole | 07 | 05, 06 right |
| J | Green Cross | 06 left | none |
| K | Lurex Diamonds | 08 | none |
| L | Rose Garden | 09 | none |
| M | Turquoise Field | 10 | none |
| N | Midnight Arrows | 11 | none |
| O | Coral Lurex | 12 | none |
| P | Classic Gold | 13 | none |
| Q | Sunburst | 14 | 16 |

The owner also has 6 videos that could not be copied here, because macOS blocks access to their Downloads folder.

## How the strips were made (`tools/`)

1. `cloths.py` lists 3–6 block crops per cloth, as four corner points in the photo. The last block is cut off where the strip ends (4.18 strip-widths).
2. `build.py` straightens each crop, turns it 90° clockwise, and reads it as a grid 40 cells across. The colours come from per-pixel k-means clustering in Lab space, with near-duplicate colours merged and saturation lifted slightly. Each cell takes its majority colour, and isolated or mottled cells are cleaned up. It also writes preview PNGs that show each photo crop beside its recreation.
3. `assemble.py` marks lurex colours (picked by eye) and merges colours that were only split by uneven light. It then run-length encodes the grids into the `CLOTHS` array in the page.

```
python3 -I tools/build.py . /tmp/kente-out
python3 -I tools/assemble.py /tmp/kente-out   # then paste /tmp/kente-out/cloths.js into CLOTHS
```

To use real product data, swap the placeholder names, fill in the meaning line, and point the "Shop" link at each product page.
