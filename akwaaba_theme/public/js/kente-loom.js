/* Kente collection: the loom above the shop grid. The cloth never moves; a new
   weave is revealed once, then holds still. */
(() => {
	const NS = 'http://www.w3.org/2000/svg';
	const XLINK = 'http://www.w3.org/1999/xlink';

	// Loom geometry, in SVG units. The cloth runs from the fell (where it
	// leaves the beater) to the bottom of the cloth beam.
	const STRIP = { x: 150, w: 100, fell: 246, end: 664 };
	const VIS = STRIP.end - STRIP.fell;
	const H1 = 76, H2 = 124;
	const THREADS = 40;
	const WIPE_MS = 900;

	// Each cloth's strip is cut from the owner's photos (prototypes/kente-photos/tools/cut.py):
	// its woven blocks, straightened and stacked at their real proportions to fill
	// the loom. warp: the colours of the threads above the fell.
	const TW = 120;
	const dataEl = document.getElementById('loom-data');
	if (!dataEl) return;
	const WEAVES = JSON.parse(dataEl.textContent).map(c => ({
		name: c.name, meaning: c.meaning || '', weft: c.weft, stripUrl: c.strip, thumb: c.thumb, route: c.route,
		fullWarp: (c.warp || '').split(' ').filter(Boolean).map(h => ['#' + h, TW / 40]),
	}));
	if (!WEAVES.length) return;

	function colorAt(stripes, x) {
		let acc = 0;
		for (const [c, w] of stripes) { acc += w; if (x < acc) return c; }
		return stripes[stripes.length - 1][0];
	}

	// ---------- build the loom's dynamic parts ----------
	const $ = id => document.getElementById(id);
	function el(tag, attrs) {
		const n = document.createElementNS(NS, tag);
		for (const k in attrs) n.setAttribute(k, attrs[k]);
		return n;
	}

	const pitch = STRIP.w / THREADS;
	const xFell = k => STRIP.x + pitch / 2 + k * pitch;
	const xTop = k => 200 + (xFell(k) - 200) * .8;                  // warp converges toward the drag stone
	const xAt = (k, y) => xTop(k) + (xFell(k) - xTop(k)) * (y + 10) / (STRIP.fell + 10);

	const warps = $('warps');
	WEAVES.forEach((w, i) => {
		const g = el('g', { class: 'warp' });
		g.style.opacity = i === 0 ? 1 : 0;
		for (let k = 0; k < THREADS && w.fullWarp.length; k++) {
			g.append(el('line', {
				x1: xTop(k), y1: -10, x2: xFell(k), y2: STRIP.fell,
				stroke: colorAt(w.fullWarp, (pitch / 2 + k * pitch) * TW / STRIP.w), 'stroke-width': 1.6,
			}));
		}
		warps.append(g);
		w.warpG = g;
	});

	function buildHeddle(g, base) {
		const strings = el('g', { stroke: '#F5EEDF', 'stroke-opacity': .75, 'stroke-width': .5 });
		for (let k = 0; k < THREADS; k++) {
			strings.append(el('line', { x1: xAt(k, base + 6), y1: base + 6, x2: xAt(k, base + 34), y2: base + 34 }));
		}
		g.append(strings,
			el('rect', { x: 116, y: base, width: 168, height: 6, rx: 3, fill: 'url(#woodH)' }),
			el('rect', { x: 116, y: base + 34, width: 168, height: 6, rx: 3, fill: 'url(#woodH)' }));
	}
	buildHeddle($('heddle1'), H1);
	buildHeddle($('heddle2'), H2);

	const reed = $('reed');
	for (let x = 111; x < 290; x += 2.6) {
		reed.append(el('line', { x1: x, y1: 187, x2: x, y2: 208, stroke: '#2E1C0E', 'stroke-opacity': .45, 'stroke-width': .6 }));
	}

	// One cloth layer per weave, each with its own reveal clip for the switch.
	const defs = $('loom-defs'), strip = $('strip');
	WEAVES.forEach((w, i) => {
		const rect = el('rect', { x: STRIP.x, y: STRIP.fell, width: STRIP.w, height: 0 });
		const clip = el('clipPath', { id: 'reveal' + i });
		clip.append(rect);
		defs.append(clip);
		const outer = el('g', { 'clip-path': `url(#reveal${i})` });
		outer.style.display = 'none';
		const img = el('image', { x: STRIP.x, y: STRIP.fell, width: STRIP.w, height: VIS, preserveAspectRatio: 'none' });
		outer.append(img);
		strip.append(outer);
		w.layer = { outer, rect, img };
	});

	// Strips load as they come up, so a phone isn't sent every cloth at once.
	function loadStrip(w) {
		if (w.layer.img.getAttribute('href')) return;
		w.layer.img.setAttribute('href', w.stripUrl);
		w.layer.img.setAttributeNS(XLINK, 'xlink:href', w.stripUrl);
	}

	// ---------- picker ----------
	const section = $('loom-section'), toggle = $('loom-toggle');
	const nowName = $('loom-now'), cta = $('loom-cta'), bobbin = $('bobbin');
	const pickName = $('weave-name'), pickMeaning = $('weave-meaning'), bar = $('weave-bar');

	const chips = WEAVES.map((w, i) => {
		const li = document.createElement('li');
		const b = document.createElement('button');
		b.type = 'button';
		b.className = 'weave';
		b.title = w.name;
		b.setAttribute('aria-label', w.name);
		b.style.backgroundImage = `url("${w.thumb || w.stripUrl}")`;
		b.addEventListener('click', () => select(i));
		li.append(b);
		$('weaves').append(li);
		return b;
	});
	bar.addEventListener('animationend', () => select((target + 1) % WEAVES.length));

	// ---------- state ----------
	const reduce = matchMedia('(prefers-reduced-motion: reduce)');
	let paused = reduce.matches, inView = true, target = 0, seq = 0;
	const shown = new Set();

	function syncState() {
		section.classList.toggle('is-user-paused', paused);
		section.classList.toggle('is-still', paused || !inView);
		toggle.setAttribute('aria-label', paused ? 'Play rotation' : 'Pause rotation');
	}

	function applyUI(i) {
		const w = WEAVES[i];
		chips.forEach((c, k) => c.setAttribute('aria-pressed', String(k === i)));
		bar.classList.remove('is-timing');
		WEAVES.forEach((x, k) => { x.warpG.style.opacity = k === i ? 1 : 0; });
		bobbin.setAttribute('fill', w.weft);
		nowName.textContent = pickName.textContent = w.name;
		pickMeaning.textContent = w.meaning;
		cta.firstChild.textContent = `Shop ${w.name} `;
		cta.setAttribute('href', '/product/' + w.route);
	}

	function startTimer() {
		if (WEAVES.length > 1) new Image().src = WEAVES[(target + 1) % WEAVES.length].stripUrl;
		bar.classList.remove('is-timing');
		void bar.offsetWidth; // restart the progress animation
		bar.classList.add('is-timing');
	}

	function settle(L) {
		for (const o of shown) {
			if (o === L) continue;
			o.outer.style.display = 'none';
			shown.delete(o);
		}
		startTimer();
	}

	const ease = t => t < .5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;

	// The cloth never moves. A new weave is revealed once, top-down from the
	// fell (a crossfade under reduced motion), then holds still.
	function select(i) {
		if (i === target) return;
		target = i;
		applyUI(i);
		loadStrip(WEAVES[i]);
		const L = WEAVES[i].layer, token = ++seq;
		L.outer.style.display = '';
		L.outer.style.transition = 'none';
		L.outer.style.opacity = 1;
		strip.append(L.outer); // on top of whatever is on the loom now
		shown.add(L);

		if (reduce.matches) {
			L.rect.setAttribute('height', VIS);
			L.outer.style.opacity = 0;
			L.outer.getBoundingClientRect();
			L.outer.style.transition = 'opacity .5s ease';
			L.outer.style.opacity = 1;
			setTimeout(() => { if (token === seq) settle(L); }, 550);
			return;
		}
		L.rect.setAttribute('height', 0);
		const t0 = performance.now();
		const step = now => {
			if (token !== seq) return;
			const t = Math.min(1, (now - t0) / WIPE_MS);
			L.rect.setAttribute('height', VIS * ease(t));
			if (t < 1) requestAnimationFrame(step);
			else settle(L);
		};
		requestAnimationFrame(step);
	}

	toggle.addEventListener('click', () => { paused = !paused; syncState(); });
	new IntersectionObserver(([e]) => { inView = e.isIntersecting; syncState(); }).observe($('loom-stage'));

	loadStrip(WEAVES[0]);
	const L0 = WEAVES[0].layer;
	L0.outer.style.display = '';
	L0.rect.setAttribute('height', VIS);
	shown.add(L0);
	applyUI(0);
	syncState();
	if (WEAVES.length > 1) startTimer();
	else { toggle.hidden = true; bar.hidden = true; }
})();
