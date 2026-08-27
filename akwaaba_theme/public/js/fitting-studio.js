/* =========================================================================
 * AKWAABA — Fitting Studio
 * -------------------------------------------------------------------------
 * A lightweight, dependency-free "tailor's studio" widget for the item page:
 *
 *   1. A parametric female dress-form (tailor's mannequin) drawn in pure SVG.
 *      Its silhouette recomputes live from women's tailoring measurements
 *      (bust / waist / hip / shoulder / back-length / hip-height).
 *
 *   2. An interactive Kente pattern library — a horizontal scroll of woven
 *      swatches. Selecting one "drapes" the form: the pattern fills the
 *      silhouette so a buyer can preview cloth + fit together.
 *
 * No WebGL, no external libraries, no model files. Everything is generated
 * from data so it stays fast and easy to theme.
 *
 * FRAPPE INTEGRATION — the door is deliberately left open:
 *   - `loadPatterns()` is the single seam for the pattern library. Today it
 *     returns the bundled PATTERN_LIBRARY; swap the body for a fetch to a
 *     future `Kente Pattern` doctype (commented stub included) and nothing
 *     else changes.
 *   - `AkwaabaFittingStudio.mount(el, opts)` mounts anywhere. On webshop item
 *     pages it auto-mounts into the product content (see bottom of file), so
 *     no upstream webshop template needs to be edited.
 *
 * This file is also require()-able from Node (for offline SVG rendering /
 * tests) — see the module.exports guard at the very bottom.
 * ========================================================================= */
(function () {
	"use strict";

	var SVG_NS = "http://www.w3.org/2000/svg";

	/* =====================================================================
	 * KENTE PATTERN LIBRARY (data-driven)
	 * Each entry: id, name, meaning, base (warp/background), weft (band
	 * colours, top→bottom), accent (motif colour), motif style.
	 * These are stylised weaves — evocative, not archival reproductions.
	 * ===================================================================== */
	var PATTERN_LIBRARY = [
		{
			id: "adweneasa",
			name: "Adwɛneasa",
			meaning: "“My skill is exhausted” — the master weaver’s showcase.",
			base: "#12100e",
			weft: ["#e0a417", "#0f7a3d", "#c0261f", "#e0a417"],
			accent: "#f4d24b",
			motif: "diamond",
		},
		{
			id: "oyokoman",
			name: "Oyokoman",
			meaning: "Royal cloth of the Oyoko — unity of the nation.",
			base: "#1a0a08",
			weft: ["#c0261f", "#e0a417", "#0f7a3d", "#c0261f"],
			accent: "#f4d24b",
			motif: "zigzag",
		},
		{
			id: "emaada",
			name: "Emaa Da",
			meaning: "“It has not happened before” — novelty and distinction.",
			base: "#0c1b2a",
			weft: ["#1c6fb0", "#e6e2d3", "#e0a417", "#1c6fb0"],
			accent: "#eef0f2",
			motif: "blocks",
		},
		{
			id: "sikafutoro",
			name: "Sika Futuro",
			meaning: "“Gold dust” — wealth, royalty and worth.",
			base: "#1c1205",
			weft: ["#e0a417", "#d97706", "#f4d24b", "#8a5a12"],
			accent: "#fff1c2",
			motif: "diamond",
		},
		{
			id: "fathia",
			name: "Fathia Fata Nkrumah",
			meaning: "Woven for a union — harmony across worlds.",
			base: "#07211d",
			weft: ["#0f9488", "#e0a417", "#0b3f39", "#0f9488"],
			accent: "#f4d24b",
			motif: "cross",
		},
		{
			id: "nkyinkyim",
			name: "Nkyinkyim",
			meaning: "“Twisting” — resourcefulness and adaptability.",
			base: "#0e0e0e",
			weft: ["#e0a417", "#c0261f", "#f4d24b", "#c0261f"],
			accent: "#ffffff",
			motif: "zigzag",
		},
		{
			id: "babadua",
			name: "Babadua",
			meaning: "Ridged like the reed — strength through the lean season.",
			base: "#10130a",
			weft: ["#0f7a3d", "#e0a417", "#c0261f", "#0f7a3d"],
			accent: "#f4d24b",
			motif: "blocks",
		},
		{
			id: "tokuakyem",
			name: "Toku Akyem",
			meaning: "The queen mother’s shield — courage remembered.",
			base: "#0a0f18",
			weft: ["#cfd6dd", "#1c6fb0", "#0a0f18", "#cfd6dd"],
			accent: "#e0a417",
			motif: "cross",
		},
	];

	/* Seam for future Frappe wiring. Async so the swap is invisible. */
	function loadPatterns() {
		// FUTURE (Frappe): fetch a `Kente Pattern` doctype instead of static data
		//
		//   return fetch("/api/method/akwaaba_theme.api.get_kente_patterns", {
		//       headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
		//   })
		//       .then(function (r) { return r.json(); })
		//       .then(function (d) { return d.message || PATTERN_LIBRARY.slice(); })
		//       .catch(function () { return PATTERN_LIBRARY.slice(); });
		//
		return Promise.resolve(PATTERN_LIBRARY.slice());
	}

	/* =====================================================================
	 * MEASUREMENTS — women's tailoring set. All visibly reshape the form.
	 * Values in inches. `drives` is documentation for what each controls.
	 * ===================================================================== */
	var MEASUREMENTS = [
		{ key: "bust",       label: "Bust",          min: 30, max: 50, step: 0.5, def: 36,   drives: "bust width" },
		{ key: "waist",      label: "Waist",         min: 22, max: 46, step: 0.5, def: 28,   drives: "waist width" },
		{ key: "hip",        label: "Hips",          min: 32, max: 54, step: 0.5, def: 38,   drives: "hip width" },
		{ key: "shoulder",   label: "Shoulder",      min: 13, max: 21, step: 0.5, def: 15.5, drives: "shoulder width" },
		{ key: "backLength", label: "Nape to waist", min: 13, max: 20, step: 0.5, def: 16,   drives: "torso length" },
		{ key: "hipHeight",  label: "Waist to hip",  min: 7,  max: 13, step: 0.5, def: 9,    drives: "form length" },
	];

	function defaultMeasurements() {
		var m = {};
		MEASUREMENTS.forEach(function (d) { m[d.key] = d.def; });
		return m;
	}

	/* =====================================================================
	 * GEOMETRY — parametric dress form
	 * viewBox 320 × 560, centre line CX = 160. Circumferences map to a
	 * front-view half-width; lengths map to vertical anchors.
	 * ===================================================================== */
	var VIEW = { w: 320, h: 560, cx: 160 };

	var GEO = {
		K_CIRC: 1.28,     // circumference (in) → half-width (px)
		K_SHOULDER: 3.0,  // shoulder width (in) → half-width (px)
		K_BACK: 7.0,      // back length (in) → shoulder→waist (px)
		K_HIPH: 8.0,      // waist→hip height (in) → waist→hem (px)
		yShoulder: 104,
		bustDrop: 64,     // shoulder → bust
		neckBaseHalf: 16,
		neckKnobHalf: 12,
		yNeckBase: 90,
		yNeckTop: 52,
		neckCap: 9,
		shoulderCurve: 9,
		hemRound: 15,
	};

	function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

	// Derive all silhouette anchor points from a measurements object.
	function deriveForm(m) {
		var g = GEO;
		var cx = VIEW.cx;

		var shoulderHalf = clamp(m.shoulder * g.K_SHOULDER, 34, 132);
		var bustHalf = clamp(m.bust * g.K_CIRC, 28, 138);
		var waistHalf = clamp(m.waist * g.K_CIRC, 22, 128);
		var hipHalf = clamp(m.hip * g.K_CIRC, 30, 140);
		var hemHalf = hipHalf * 0.9;

		var yShoulder = g.yShoulder;
		var yBust = yShoulder + g.bustDrop;
		var yWaist = yShoulder + clamp(m.backLength * g.K_BACK, 84, 150);
		var yHem = yWaist + clamp(m.hipHeight * g.K_HIPH, 52, 104);
		var yHip = yWaist + (yHem - yWaist) * 0.58;

		return {
			cx: cx,
			shoulderHalf: shoulderHalf, bustHalf: bustHalf, waistHalf: waistHalf,
			hipHalf: hipHalf, hemHalf: hemHalf,
			yShoulder: yShoulder, yBust: yBust, yWaist: yWaist, yHip: yHip, yHem: yHem,
		};
	}

	// Smooth vertical spline: C-commands through [[x,y],...] with vertical
	// tangents. Assumes the current path point already equals points[0].
	function smoothDown(points, tension) {
		var t = tension == null ? 0.5 : tension;
		var out = "";
		for (var i = 1; i < points.length; i++) {
			var p0 = points[i - 1], p1 = points[i];
			var seg = p1[1] - p0[1];
			var c1y = p0[1] + seg * t, c2y = p1[1] - seg * t;
			out += " C " + p0[0].toFixed(1) + " " + c1y.toFixed(1) +
				" " + p1[0].toFixed(1) + " " + c2y.toFixed(1) +
				" " + p1[0].toFixed(1) + " " + p1[1].toFixed(1);
		}
		return out;
	}

	// Closed silhouette path for the dress form.
	function formPath(f) {
		var g = GEO, cx = f.cx;
		var R = function (w) { return (cx + w).toFixed(1); };
		var L = function (w) { return (cx - w).toFixed(1); };

		var d = "M " + L(g.neckBaseHalf) + " " + f.yShoulder;
		// up-left side of neck, over the top, down-right side of neck
		d += " Q " + L(g.neckBaseHalf) + " " + g.yNeckBase +
			" " + (cx - g.neckKnobHalf).toFixed(1) + " " + g.yNeckTop;
		d += " Q " + cx + " " + (g.yNeckTop - g.neckCap) +
			" " + (cx + g.neckKnobHalf).toFixed(1) + " " + g.yNeckTop;
		d += " Q " + R(g.neckBaseHalf) + " " + g.yNeckBase +
			" " + R(g.neckBaseHalf) + " " + f.yShoulder;
		// right shoulder out to the tip
		d += " Q " + (cx + g.neckBaseHalf + g.shoulderCurve).toFixed(1) + " " + (f.yShoulder - 3) +
			" " + R(f.shoulderHalf) + " " + f.yShoulder;
		// down the right body: shoulder → bust → waist → hip → hem
		d += smoothDown([
			[cx + f.shoulderHalf, f.yShoulder],
			[cx + f.bustHalf, f.yBust],
			[cx + f.waistHalf, f.yWaist],
			[cx + f.hipHalf, f.yHip],
			[cx + f.hemHalf, f.yHem],
		]);
		// rounded hem across to the left
		d += " Q " + cx + " " + (f.yHem + g.hemRound) + " " + L(f.hemHalf) + " " + f.yHem;
		// up the left body: hem → hip → waist → bust → shoulder
		d += smoothDown([
			[cx - f.hemHalf, f.yHem],
			[cx - f.hipHalf, f.yHip],
			[cx - f.waistHalf, f.yWaist],
			[cx - f.bustHalf, f.yBust],
			[cx - f.shoulderHalf, f.yShoulder],
		]);
		// left shoulder in to the neck base
		d += " Q " + (cx - g.neckBaseHalf - g.shoulderCurve).toFixed(1) + " " + (f.yShoulder - 3) +
			" " + L(g.neckBaseHalf) + " " + f.yShoulder + " Z";
		return d;
	}

	/* =====================================================================
	 * KENTE TILE — one warp-strip of stylised weave, reused as the form
	 * fill <pattern> and as standalone swatches.
	 * ===================================================================== */
	var TILE = { w: 46, h: 64 };

	function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

	function kenteTileInner(p) {
		var W = TILE.w, H = TILE.h;
		var bands = p.weft.length;
		var bh = H / bands;
		var s = "";
		// warp base
		s += '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="' + p.base + '"/>';
		for (var i = 0; i < bands; i++) {
			var y = i * bh;
			var col = p.weft[i];
			s += '<rect x="0" y="' + y.toFixed(2) + '" width="' + W + '" height="' + bh.toFixed(2) + '" fill="' + col + '"/>';
			// motif overlaid on alternating bands
			if (i % 2 === 1) {
				s += kenteMotif(p.motif, W, y, bh, p.accent, p.base);
			}
			// weft separator
			s += '<rect x="0" y="' + y.toFixed(2) + '" width="' + W + '" height="1.1" fill="rgba(0,0,0,0.45)"/>';
		}
		// vertical warp threads (gives the woven grain)
		for (var x = W / 4; x < W; x += W / 2) {
			s += '<rect x="' + (x - 0.6).toFixed(2) + '" y="0" width="1.2" height="' + H + '" fill="rgba(255,255,255,0.10)"/>';
		}
		return s;
	}

	function kenteMotif(kind, W, y, bh, accent, base) {
		var cy = y + bh / 2;
		var s = "";
		if (kind === "diamond") {
			for (var x = W / 4; x <= W; x += W / 2) {
				var r = bh * 0.34;
				s += '<path d="M ' + x + ' ' + (cy - r) + ' L ' + (x + r) + ' ' + cy +
					' L ' + x + ' ' + (cy + r) + ' L ' + (x - r) + ' ' + cy + ' Z" fill="' + accent + '"/>';
			}
		} else if (kind === "zigzag") {
			var amp = bh * 0.3, step = W / 4, d = "M 0 " + (cy + amp);
			for (var xi = 0, up = true; xi <= W; xi += step, up = !up) {
				d += " L " + xi + " " + (up ? (cy - amp) : (cy + amp));
			}
			s += '<path d="' + d + '" fill="none" stroke="' + accent + '" stroke-width="2.4"/>';
		} else if (kind === "cross") {
			for (var xc = W / 4; xc <= W; xc += W / 2) {
				var a = bh * 0.28;
				s += '<rect x="' + (xc - a) + '" y="' + (cy - 1.6) + '" width="' + (2 * a) + '" height="3.2" fill="' + accent + '"/>';
				s += '<rect x="' + (xc - 1.6) + '" y="' + (cy - a) + '" width="3.2" height="' + (2 * a) + '" fill="' + accent + '"/>';
			}
		} else { // blocks
			var bw = W / 4;
			for (var bx = 0, k = 0; bx < W; bx += bw, k++) {
				if (k % 2 === 0) {
					s += '<rect x="' + bx + '" y="' + (cy - bh * 0.28) + '" width="' + (bw * 0.9) +
						'" height="' + (bh * 0.56) + '" fill="' + accent + '"/>';
				}
			}
		}
		return s;
	}

	// Standalone swatch SVG (used for the scroll strip).
	function kenteSwatchSVG(p, w, h) {
		w = w || 84; h = h || 108;
		var sx = w / TILE.w, sy = h / TILE.h;
		return '<svg viewBox="0 0 ' + w + ' ' + h + '" width="' + w + '" height="' + h +
			'" preserveAspectRatio="none" aria-hidden="true">' +
			'<g transform="scale(' + sx.toFixed(3) + ' ' + sy.toFixed(3) + ')">' +
			kenteTileInner(p) + '</g></svg>';
	}

	/* =====================================================================
	 * STAGE SVG — the full mannequin scene, driven by state.
	 * ===================================================================== */
	function stageSVG(state) {
		var m = state.measure;
		var pattern = state.pattern;
		var f = deriveForm(m);
		var d = formPath(f);
		var cx = f.cx;

		var patId = "akwfs-kente";
		var defs =
			'<linearGradient id="akwfs-sheen" x1="0" y1="0" x2="1" y2="0">' +
			'<stop offset="0" stop-color="#000" stop-opacity="0.30"/>' +
			'<stop offset="0.16" stop-color="#fff" stop-opacity="0.16"/>' +
			'<stop offset="0.5" stop-color="#fff" stop-opacity="0.02"/>' +
			'<stop offset="0.84" stop-color="#000" stop-opacity="0.12"/>' +
			'<stop offset="1" stop-color="#000" stop-opacity="0.34"/>' +
			'</linearGradient>' +
			'<radialGradient id="akwfs-floor" cx="0.5" cy="0.5" r="0.5">' +
			'<stop offset="0" stop-color="#000" stop-opacity="0.28"/>' +
			'<stop offset="1" stop-color="#000" stop-opacity="0"/>' +
			'</radialGradient>' +
			(pattern
				? '<pattern id="' + patId + '" patternUnits="userSpaceOnUse" width="' + TILE.w +
				  '" height="' + TILE.h + '" patternTransform="rotate(0)">' + kenteTileInner(pattern) + '</pattern>'
				: "");

		var bodyFill = pattern ? 'url(#' + patId + ')' : "#d9d2c5";

		// stand
		var yStandTop = f.yHem + GEO.hemRound - 4;
		var stand =
			'<ellipse cx="' + cx + '" cy="524" rx="78" ry="13" fill="url(#akwfs-floor)"/>' +
			'<ellipse cx="' + cx + '" cy="518" rx="60" ry="11" fill="#3a3a3e"/>' +
			'<ellipse cx="' + cx + '" cy="515" rx="60" ry="11" fill="#26262a"/>' +
			'<rect x="' + (cx - 4) + '" y="' + yStandTop + '" width="8" height="' + (512 - yStandTop) + '" fill="#3a3a3e"/>' +
			'<rect x="' + (cx - 4) + '" y="' + yStandTop + '" width="3" height="' + (512 - yStandTop) + '" fill="#55555b"/>';

		// neck cap finial
		var finial = '<ellipse cx="' + cx + '" cy="' + (GEO.yNeckTop - 4) + '" rx="9" ry="6" fill="#2b2b2f"/>';

		// guide lines (bust/waist/hip) — the tailoring read-out
		var guides = "";
		if (state.showGuides) {
			guides = guideLine(cx, f.bustHalf, f.yBust, "Bust", m.bust) +
				guideLine(cx, f.waistHalf, f.yWaist, "Waist", m.waist) +
				guideLine(cx, f.hipHalf, f.yHip, "Hip", m.hip);
		}

		var svg =
			'<svg class="akwfs-svg" viewBox="0 0 ' + VIEW.w + ' ' + VIEW.h + '" ' +
			'preserveAspectRatio="xMidYMid meet" role="img" ' +
			'aria-label="Adjustable dress form previewing ' + (pattern ? esc(pattern.name) : "the selected") + ' kente">' +
			'<defs>' + defs + '</defs>' +
			stand +
			'<g class="akwfs-form">' +
			finial +
			// body fill (kente / plain)
			'<path d="' + d + '" fill="' + bodyFill + '" stroke="#1c1a17" stroke-width="1.4"/>' +
			// volume sheen
			'<path d="' + d + '" fill="url(#akwfs-sheen)"/>' +
			// centre seam
			'<line x1="' + cx + '" y1="' + (f.yShoulder + 6) + '" x2="' + cx + '" y2="' + (f.yHem - 4) +
			'" stroke="rgba(0,0,0,0.22)" stroke-width="1" stroke-dasharray="2 3"/>' +
			'</g>' +
			guides +
			'</svg>';
		return svg;
	}

	function guideLine(cx, half, y, label, val) {
		var x2 = cx + half + 26;
		return '<g class="akwfs-guide">' +
			'<line x1="' + (cx - half) + '" y1="' + y + '" x2="' + x2 + '" y2="' + y +
			'" stroke="#b98a2e" stroke-width="1" stroke-dasharray="3 3"/>' +
			'<circle cx="' + (cx - half) + '" cy="' + y + '" r="2.2" fill="#b98a2e"/>' +
			'<circle cx="' + (cx + half) + '" cy="' + y + '" r="2.2" fill="#b98a2e"/>' +
			'<text x="' + (x2 + 3) + '" y="' + (y + 3.5) + '" class="akwfs-guide-txt">' +
			esc(label) + ' ' + val + '&#8243;</text>' +
			'</g>';
	}

	/* =====================================================================
	 * MOUNT — build the DOM, wire interaction. Browser only.
	 * ===================================================================== */
	function mount(container, opts) {
		if (typeof document === "undefined") return null;
		if (!container) return null;
		opts = opts || {};

		var state = {
			measure: defaultMeasurements(),
			pattern: null,
			patterns: [],
			showGuides: true,
		};

		container.classList.add("akwfs");
		container.innerHTML = shellHTML();

		var stageEl = container.querySelector(".akwfs-stage");
		var slidersEl = container.querySelector(".akwfs-sliders");
		var stripEl = container.querySelector(".akwfs-strip");
		var captionEl = container.querySelector(".akwfs-caption");
		var guideToggle = container.querySelector(".akwfs-guidetoggle");

		// --- build measurement sliders ---
		slidersEl.innerHTML = MEASUREMENTS.map(function (d) {
			var v = state.measure[d.key];
			return '<div class="akwfs-field" data-key="' + d.key + '">' +
				'<label>' + d.label + '<output>' + fmt(v) + '&#8243;</output></label>' +
				'<input type="range" min="' + d.min + '" max="' + d.max + '" step="' + d.step +
				'" value="' + v + '" aria-label="' + d.label + '">' +
				'</div>';
		}).join("");

		slidersEl.addEventListener("input", function (e) {
			var input = e.target;
			if (input.type !== "range") return;
			var key = input.closest(".akwfs-field").getAttribute("data-key");
			state.measure[key] = parseFloat(input.value);
			input.closest(".akwfs-field").querySelector("output").innerHTML = fmt(input.value) + "&#8243;";
			render();
		});

		guideToggle.addEventListener("click", function () {
			state.showGuides = !state.showGuides;
			guideToggle.setAttribute("aria-pressed", String(state.showGuides));
			guideToggle.classList.toggle("-on", state.showGuides);
			render();
		});

		container.querySelector(".akwfs-reset").addEventListener("click", function () {
			state.measure = defaultMeasurements();
			MEASUREMENTS.forEach(function (d) {
				var field = slidersEl.querySelector('.akwfs-field[data-key="' + d.key + '"]');
				field.querySelector("input").value = d.def;
				field.querySelector("output").innerHTML = fmt(d.def) + "&#8243;";
			});
			render();
		});

		container.querySelector(".akwfs-scroll-prev").addEventListener("click", function () { nudge(-1); });
		container.querySelector(".akwfs-scroll-next").addEventListener("click", function () { nudge(1); });
		function nudge(dir) { stripEl.scrollBy({ left: dir * stripEl.clientWidth * 0.8, behavior: "smooth" }); }

		container.querySelector(".akwfs-save").addEventListener("click", function () {
			try { localStorage.setItem("akw_measurements", JSON.stringify(state.measure)); } catch (e) {}
			flash(container.querySelector(".akwfs-save"), "Saved ✓");
		});

		// --- load pattern library, build the scroll strip ---
		(opts.loadPatterns || loadPatterns)().then(function (list) {
			state.patterns = list;
			stripEl.innerHTML = list.map(function (p, i) {
				return '<button class="akwfs-swatch" data-idx="' + i + '" title="' + esc(p.name) +
					'" aria-label="' + esc(p.name) + '">' +
					kenteSwatchSVG(p) +
					'<span class="akwfs-swatch-name">' + esc(p.name) + '</span>' +
					'</button>';
			}).join("");

			stripEl.addEventListener("click", function (e) {
				var btn = e.target.closest(".akwfs-swatch");
				if (!btn) return;
				selectPattern(parseInt(btn.getAttribute("data-idx"), 10));
			});

			// restore saved measurements if present
			try {
				var saved = JSON.parse(localStorage.getItem("akw_measurements") || "null");
				if (saved && typeof saved === "object") {
					MEASUREMENTS.forEach(function (d) {
						if (typeof saved[d.key] === "number") {
							state.measure[d.key] = clamp(saved[d.key], d.min, d.max);
							var field = slidersEl.querySelector('.akwfs-field[data-key="' + d.key + '"]');
							field.querySelector("input").value = state.measure[d.key];
							field.querySelector("output").innerHTML = fmt(state.measure[d.key]) + "&#8243;";
						}
					});
				}
			} catch (e) {}

			selectPattern(0);
		});

		function selectPattern(idx) {
			state.pattern = state.patterns[idx] || null;
			var btns = stripEl.querySelectorAll(".akwfs-swatch");
			btns.forEach(function (b, i) { b.classList.toggle("-active", i === idx); });
			if (state.pattern) {
				captionEl.innerHTML =
					'<span class="akwfs-caption-name">' + esc(state.pattern.name) + "</span>" +
					'<span class="akwfs-caption-meaning">' + esc(state.pattern.meaning) + "</span>";
			}
			render();
		}

		function render() { stageEl.innerHTML = stageSVG(state); }

		render(); // initial (plain form until patterns load)
		return { state: state, render: render };
	}

	function shellHTML() {
		return '' +
			'<div class="akwfs-grid">' +
			'  <div class="akwfs-stagewrap">' +
			'    <div class="akwfs-stage"></div>' +
			'    <div class="akwfs-stagebar">' +
			'      <button type="button" class="akwfs-guidetoggle -on" aria-pressed="true">Measurements</button>' +
			'      <button type="button" class="akwfs-reset">Reset</button>' +
			'    </div>' +
			'  </div>' +
			'  <div class="akwfs-panel">' +
			'    <div class="akwfs-h">Your measurements</div>' +
			'    <p class="akwfs-sub">Set your figure and the form reshapes to your fit.</p>' +
			'    <div class="akwfs-sliders"></div>' +
			'    <button type="button" class="akwfs-save">Save my measurements</button>' +
			'    <div class="akwfs-h akwfs-h-2">Kente library</div>' +
			'    <p class="akwfs-sub">Scroll the weave and drape it on the form.</p>' +
			'    <div class="akwfs-scroller">' +
			'      <button type="button" class="akwfs-scroll-prev" aria-label="Previous patterns">&#8249;</button>' +
			'      <div class="akwfs-strip"></div>' +
			'      <button type="button" class="akwfs-scroll-next" aria-label="More patterns">&#8250;</button>' +
			'    </div>' +
			'    <div class="akwfs-caption"></div>' +
			'  </div>' +
			'</div>';
	}

	function fmt(v) { v = parseFloat(v); return (v % 1 === 0) ? String(v) : v.toFixed(1); }

	function flash(btn, text) {
		var old = btn.textContent;
		btn.textContent = text; btn.disabled = true;
		setTimeout(function () { btn.textContent = old; btn.disabled = false; }, 1400);
	}

	/* =====================================================================
	 * AUTO-MOUNT
	 *   - any [data-akw-fitting-studio] element
	 *   - webshop item pages: inject after the product content (no upstream
	 *     template edit needed). Gated so it only fires on the item generator.
	 * ===================================================================== */
	function autoInit() {
		document.querySelectorAll("[data-akw-fitting-studio]").forEach(function (el) {
			if (!el.getAttribute("data-akwfs-mounted")) {
				el.setAttribute("data-akwfs-mounted", "1");
				mount(el);
			}
		});

		// item page: .item-main is the webshop item generator wrapper
		var itemMain = document.querySelector(".product-page-content");
		var onItemRoute = /\/[^/]+$/.test(location.pathname) && document.querySelector(".item-main");
		if (itemMain && onItemRoute && !document.querySelector(".akwfs")) {
			var host = document.createElement("section");
			host.className = "akwfs-itemblock";
			host.innerHTML =
				'<div class="akwfs-itemblock-head">' +
				'<span class="akwfs-eyebrow">The Fitting Studio</span>' +
				'<h2>See it on your figure</h2>' +
				'<p>Adjust the form to your measurements and drape the kente before you buy.</p>' +
				'</div><div class="akwfs-itemblock-mount"></div>';
			itemMain.parentNode.insertBefore(host, itemMain.nextSibling);
			mount(host.querySelector(".akwfs-itemblock-mount"));
		}
	}

	if (typeof document !== "undefined") {
		if (document.readyState === "loading") {
			document.addEventListener("DOMContentLoaded", autoInit);
		} else {
			autoInit();
		}
		window.AkwaabaFittingStudio = {
			mount: mount,
			loadPatterns: loadPatterns,
			PATTERN_LIBRARY: PATTERN_LIBRARY,
			MEASUREMENTS: MEASUREMENTS,
		};
	}

	/* Node export for offline rendering / tests (no DOM required). */
	if (typeof module !== "undefined" && module.exports) {
		module.exports = {
			PATTERN_LIBRARY: PATTERN_LIBRARY,
			MEASUREMENTS: MEASUREMENTS,
			defaultMeasurements: defaultMeasurements,
			deriveForm: deriveForm,
			formPath: formPath,
			stageSVG: stageSVG,
			kenteSwatchSVG: kenteSwatchSVG,
			kenteTileInner: kenteTileInner,
			VIEW: VIEW,
		};
	}
})();
