# Kente cloth photos

Source photos for `../kente-loom.html`, a static loom prototype that rotates between kente cloths.

- `photo-sample-1..4.jpg`: the first cloth. It is already recreated by hand as the "Photo sample" weave (`photoCloth()` in the page).
- `cloth-photo-01..20.jpg`: the owner's photos, in the order they sent them. No shop names or proverbs were given. Use descriptive placeholder names, and leave the meaning line blank.

Each cloth is many ~4-inch weft-faced strips sewn side by side. In these photos the cloths hang over a stand, so each **horizontal row is one strip**. Thin white lines split a row into blocks, and stitched seams separate the rows. To put a block on the loom, rotate it 90° clockwise so the strip runs top to bottom.

## Which photos show which cloth (first pass, needs checking)

| Cloth | Best photo | Also in | Look |
|---|---|---|---|
| A | 15 | 01 left, 02 left, 04 left | multicolour: green zigzag, blue/magenta lattice, chevrons, magenta/green "tree" motifs |
| C | 19 | 02 right | purple/red top, grey/blue/orange steps, zigzags, cream/red cross, orange/black checks |
| D | 01 right | none | black/olive blocks, yellow/black/blue chevrons, stripes (only seen once) |
| E | 20 | 03 left | dark green lurex with pink/grey/red triangles |
| F | 18 | 03 right | brown lurex with blue/orange triangles, diamonds, cream/brown checks |
| G | 17 | 04 right | yellow zigzag, yellow "tree" motif, orange stripes with leaves |
| H | 07 | 05 left (?) | blue stole: gold/white zigzag, orange oval, cream/black steps, orange stripes |
| I | 05 right | none | blue with arrows/stars (may be the same as 06 right) |
| J | 06 left | none | green top with a cross, cream with crosses, blue blocks |
| K | 08 | none | blue/red/gold lurex diamonds and lattice |
| L | 09 | none | red/pink: pink/gold zigzag, gold lurex diamond |
| M | 10 | none | turquoise/green with triangles and X motifs |
| N | 11 | none | navy/peach/sky with gold-silver steps |
| O | 12 | none | orange/pink lurex with diamonds and chevrons |
| P | 13 | none | gold/green/maroon (classic): stripes, stepped diamond, zigzag |
| Q | 14 | 16 (?) | yellow/orange: green zigzag, red/yellow diamond, pink/green diamond |

The owner also has 6 videos that could not be copied here, because macOS blocks access to their Downloads folder.

## Plan

1. For each cloth, crop 3–4 distinctive blocks from its best photo and rotate each 90° clockwise. Stack them to fill the loom strip (about 4.2 strip-widths long), with a thin white line between blocks.
2. Turn each block into a woven-looking grid of about 24 cells across: average each cell's colour, map it to a palette of about 8 colours per cloth with saturation lifted slightly, and clean up isolated single-cell speckles. Mark metallic (lurex) colours so they get silver flecks.
3. Embed each cloth as compact data in the page: a palette plus rows of palette indices, drawn as run-length rects. The preview loads the page as a data: URL, so external files won't load.
4. Remove the generated stand-ins (Sika Futoro, Oyokoman, Adwini Asa, Emaa Da, Abusua Ye Dom) and their block code.
5. With about 15 cloths, change the picker from a list to a grid of small strip swatches. Show the active name above it with the auto-rotate progress bar.
6. Compare each recreation against its photo.
