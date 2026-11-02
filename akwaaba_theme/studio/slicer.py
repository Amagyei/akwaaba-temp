"""Slice an AI-generated grid (2×2, 1×2, …) into its panels.

Models draw *roughly* equal panels, so cutting at exact fractions can leave a
sliver of the neighbouring panel or a gutter on an edge. Each cut snaps to the
nearest real seam within ±6% of where it is expected, in this order:

1. a thin band of flat rows — a gutter/border the model drew;
2. a row where the change from the row above spikes far above the rest of
   the window — two panels butting straight up against each other. A hem
   or horizon inside one panel is diluted across the full width, and a
   continuous photo has no spike at all (measured: real seams 6–10× the
   window median, single photos ~2×);
3. otherwise the exact fraction, flagged so the phone can ask for a check —
   this is also how a model that ignored the grid instruction shows up.

Then any thin flat border left on a panel's edges is trimmed. Pure Pillow.
"""

import statistics

from PIL import Image, ImageChops, ImageStat

WINDOW = 0.06  # search ± this share of the side around each expected cut
FLAT_STD = 7.0  # grey-level std below which a line counts as flat
MAX_GUTTER = 0.03  # flat bands thicker than this are backdrop, not gutters
SEAM_SPIKE = 4.0  # seam row's change vs the window median
SEAM_MIN = 12.0  # …and at least this mean grey-level change


def slice_grid(img, rows=1, cols=1):
	"""Return (panels in reading order, seams_found).

	seams_found is False when at least one cut fell back to the exact
	fraction, i.e. we couldn't see where the panels meet.
	"""
	img = img.convert("RGB")
	rows, cols = max(1, int(rows or 1)), max(1, int(cols or 1))
	if rows * cols == 1:
		return [_trim(img)], True

	gray = img.convert("L")
	ys, ys_ok = _cuts(gray, rows)
	xs, xs_ok = _cuts(gray.transpose(Image.Transpose.TRANSPOSE), cols)

	panels = [
		_trim(img.crop((xs[c], ys[r], xs[c + 1], ys[r + 1])))
		for r in range(rows)
		for c in range(cols)
	]
	return panels, ys_ok and xs_ok


def _cuts(gray, n):
	"""Horizontal cut positions [0, …, height] splitting `gray` into n bands."""
	h = gray.height
	cuts, all_found = [0], True
	for i in range(1, n):
		expected = round(h * i / n)
		reach = max(2, int(h * WINDOW))
		lo = max(cuts[-1] + 2, expected - reach, 1)
		hi = min(h - 2, expected + reach)
		y, found = _snap(gray, expected, lo, hi)
		cuts.append(y)
		all_found = all_found and found
	cuts.append(h)
	return cuts, all_found


def _snap(gray, expected, lo, hi):
	w, h = gray.size
	rows = {y: gray.crop((0, y, w, y + 1)) for y in range(lo - 1, hi + 1)}

	# 1. Flat bands (gutters, or plain backdrop shared by both panels).
	flat = [_std(rows[y]) < FLAT_STD for y in range(lo, hi + 1)]
	runs, start = [], None
	for i, is_flat in enumerate(flat + [False]):
		if is_flat and start is None:
			start = i
		elif not is_flat and start is not None:
			runs.append((lo + start, lo + i - 1))
			start = None
	for a, b in runs:
		if a <= expected <= b and (b - a + 1) > h * MAX_GUTTER:
			# A wide flat region straddles the cut: it's backdrop, so cutting
			# right where expected is clean.
			return expected, True
	gutters = [(a, b) for a, b in runs if 2 <= (b - a + 1) <= max(2, h * MAX_GUTTER)]
	if gutters:
		a, b = min(gutters, key=lambda r: abs((r[0] + r[1]) / 2 - expected))
		return (a + b) // 2, True

	# 2. A hard seam: the row-to-row change spikes far above its neighbours.
	jumps = {y: ImageStat.Stat(ImageChops.difference(rows[y], rows[y - 1])).mean[0] for y in range(lo, hi + 1)}
	if jumps:
		best_y = max(jumps, key=jumps.get)
		typical = statistics.median(jumps.values())
		if jumps[best_y] >= SEAM_MIN and jumps[best_y] >= SEAM_SPIKE * max(typical, 1.0):
			return best_y, True

	# 3. Seamless grid (or the model ignored the grid): exact fraction.
	return expected, False


def _trim(panel):
	"""Drop thin flat borders (gutter remnants) from a panel's edges."""
	gray = panel.convert("L")
	w, h = gray.size
	lim_y, lim_x = max(1, int(h * MAX_GUTTER)), max(1, int(w * MAX_GUTTER))

	top = _flat_band(lambda i: gray.crop((0, i, w, i + 1)), lim_y)
	bottom = _flat_band(lambda i: gray.crop((0, h - 1 - i, w, h - i)), lim_y)
	left = _flat_band(lambda i: gray.crop((i, 0, i + 1, h)), lim_x)
	right = _flat_band(lambda i: gray.crop((w - 1 - i, 0, w - i, h)), lim_x)

	if not (top or bottom or left or right):
		return panel
	return panel.crop((left, top, w - right, h - bottom))


def _flat_band(line_at, limit):
	"""Thickness of the flat band at an edge; 0 if none or if it runs past
	`limit` (then it's backdrop we should keep, not a gutter)."""
	n = 0
	while n <= limit and _std(line_at(n)) < FLAT_STD:
		n += 1
	return n if n <= limit else 0


def _std(line):
	return ImageStat.Stat(line).stddev[0]
