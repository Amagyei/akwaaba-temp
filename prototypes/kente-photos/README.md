# Kente cloth photos

Source photos for `../kente-loom.html`, a static loom prototype that rotates between kente cloths.

- `photo-sample-1..4.jpg`: the first cloth. It is already recreated by hand as the "Photo sample" weave (`photoCloth()` in the page).
- `cloth-photo-01..20.jpg`: the owner's photos, in the order they sent them. No shop names or proverbs were given. Use descriptive placeholder names, and leave the meaning line blank.

Each cloth is many ~4-inch weft-faced strips sewn side by side. In these photos the cloths hang over a stand, so each **horizontal row is one strip**. Thin white lines split a row into blocks, and stitched seams separate the rows. To put a block on the loom, rotate it 90° clockwise so the strip runs top to bottom.

## Cloths on the loom

Each strip on the loom is cut from the real photos. No pattern is redrawn. "Photo sample" comes from `photo-sample-4.jpg`, and the 15 cloths below come from the owner's photos. The names are placeholders. Photos 05, 06 (right side) and 07 show the same blue stole, so the earlier "I" group is merged into H.

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

1. `cloths.py` lists 3–7 block cuts per cloth, as four corner points in the photo. In the owner's photos each block hangs as a horizontal row, so it is turned 90° clockwise to run down the strip. The sample photo's strips already run top to bottom.
2. `cut.py` straightens each cut, stretches its contrast slightly, and stacks the cuts at their real proportions with a thin white line between them. The last block is cut where the loom strip ends (4.18 strip-widths). It then writes each strip as an embedded JPEG, together with the warp-thread colours and the bobbin colour.

```
python3 -I tools/cut.py . /tmp/kente-out   # then paste /tmp/kente-out/strips.js into CLOTHS
```

The page loads as a single file, so the photos are embedded (about 1 MB in total). To use real product data, swap the placeholder names, fill in the meaning line, and point the "Shop" link at each product page.
