/* Serwa Kollections — Studio (/studio)
 *
 * Phone-first item upload: photo of a garment → optional sizes or colours →
 * model, arc and saved AI prompts → sliced gallery → new or existing item.
 * Talks to akwaaba_theme.studio.api.
 *
 * Choices made while reviewing (which photos to keep, which is main) stay in
 * the page until "Create item"; everything else (photos, AI outputs) is saved
 * on the server session as it happens, so a dropped connection loses nothing.
 */
(function () {
	"use strict";

	var API = "/api/method/akwaaba_theme.studio.api.";
	var BUSY_LINES = [
		"Sending your photo…",
		"Dressing the model…",
		"Matching the print…",
		"Setting the lights…",
		"Framing the shots…",
		"Slicing the grid…",
	];

	var bootEl = document.getElementById("stu-boot");
	var S = {
		boot: bootEl ? JSON.parse(bootEl.textContent || "{}") : {},
		session: null,
		selected: new Set(),   // prompt names queued to generate
		inc: {},               // row id → 0/1 (keep in gallery)
		main: null,            // row id of the main photo
		mode: "new",
		form: {},
		codeTouched: false,
		existing: null,        // picked item for "existing" mode
		results: [],           // item search results
		errors: [],            // last generate() per-prompt failures
		done: null,
		busy: null,
		model: ((S_first(bootEl, "models")) || {}).name || "",
		arc: ((S_first(bootEl, "arcs")) || {}).name || "",
		engine: ((S_first(bootEl, "engines")) || {}).key || "",
		optKind: "",           // "", Size or Colour
		optRows: [],           // [{label, price}], including rows still being typed
		length: "",            // the garment's length, told to the image model
		sourceLabel: "",       // which option's photo the AI starts from
		extraLabel: "",        // which option the next added photos belong to
	};
	S.form.item_group = S.boot.default_group || (S.boot.groups || [])[0] || "";
	S.form.publish = !!S.boot.publish_by_default;
	S.form.stock_mode = "made_to_order";

	function S_first(el, key) {
		var boot = el ? JSON.parse(el.textContent || "{}") : {};
		return (boot[key] || [])[0];
	}

	var $ = function (sel, root) { return (root || document).querySelector(sel); };

	// ------------------------------------------------------------------ utils

	function esc(v) {
		return String(v == null ? "" : v)
			.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
	}

	function usd(n) {
		n = Number(n || 0);
		return "$" + (n < 0.1 ? n.toFixed(3) : n.toFixed(2));
	}

	function csrf() {
		return (window.frappe && window.frappe.csrf_token) || "";
	}

	function serverError(j) {
		if (j && j._server_messages) {
			try {
				return JSON.parse(j._server_messages).map(function (m) {
					try { return JSON.parse(m).message; } catch (e) { return m; }
				}).join("\n").replace(/<[^>]+>/g, "");
			} catch (e) { /* fall through */ }
		}
		if (j && j.exception) {
			var parts = String(j.exception).split(":");
			return (parts.length > 1 ? parts.slice(1).join(":") : parts[0]).trim();
		}
		return "";
	}

	function fail(message, status) {
		var err = new Error(message);
		err.status = status || 0;
		return err;
	}

	function api(method, args, opts) {
		opts = opts || {};
		var url = API + method;
		var init = {
			method: opts.get ? "GET" : "POST",
			credentials: "same-origin",
			headers: { "Accept": "application/json", "X-Frappe-CSRF-Token": csrf() },
		};
		if (opts.get) {
			url += "?" + new URLSearchParams(args || {}).toString();
		} else {
			init.headers["Content-Type"] = "application/json";
			init.body = JSON.stringify(args || {});
		}
		return fetch(url, init).then(function (r) {
			return r.json().catch(function () { return {}; }).then(function (j) {
				if (!r.ok) throw fail(serverError(j) || ("Server error " + r.status), r.status);
				return j.message;
			});
		}, function () {
			throw fail("Network error — check your connection.", 0);
		});
	}

	function upload(method, blobs, fields, onProgress) {
		return new Promise(function (resolve, reject) {
			var fd = new FormData();
			Object.keys(fields || {}).forEach(function (k) { fd.append(k, fields[k]); });
			blobs.forEach(function (b, i) { fd.append("file", b, "photo-" + (i + 1) + ".jpg"); });
			var xhr = new XMLHttpRequest();
			xhr.open("POST", API + method);
			xhr.setRequestHeader("Accept", "application/json");
			xhr.setRequestHeader("X-Frappe-CSRF-Token", csrf());
			xhr.upload.onprogress = function (e) {
				if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
			};
			xhr.onload = function () {
				var j = {};
				try { j = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
				if (xhr.status < 300) resolve(j.message);
				else reject(fail(serverError(j) || ("Upload failed (" + xhr.status + ")"), xhr.status));
			};
			xhr.onerror = function () { reject(fail("Network error — check your connection.", 0)); };
			xhr.send(fd);
		});
	}

	/** Downscale + re-encode on the phone: a 12MP photo becomes ~400KB. */
	function shrink(file, maxPx) {
		maxPx = maxPx || 2048;
		function draw(src, w, h) {
			var scale = Math.min(1, maxPx / Math.max(w, h));
			var c = document.createElement("canvas");
			c.width = Math.round(w * scale);
			c.height = Math.round(h * scale);
			c.getContext("2d").drawImage(src, 0, 0, c.width, c.height);
			return new Promise(function (res) { c.toBlob(function (b) { res(b || file); }, "image/jpeg", 0.9); });
		}
		function viaImg() {
			return new Promise(function (res) {
				var url = URL.createObjectURL(file);
				var img = new Image();
				img.onload = function () {
					draw(img, img.naturalWidth, img.naturalHeight).then(function (b) { URL.revokeObjectURL(url); res(b); });
				};
				img.onerror = function () { URL.revokeObjectURL(url); res(file); }; // let the server try
				img.src = url;
			});
		}
		if (!window.createImageBitmap) return viaImg();
		return createImageBitmap(file, { imageOrientation: "from-image" }).then(function (bmp) {
			return draw(bmp, bmp.width, bmp.height).then(function (b) { if (bmp.close) bmp.close(); return b; });
		}, viaImg);
	}

	function toast(msg, kind) {
		var el = $("#stu-toast");
		if (!el || !msg) return;
		el.textContent = msg;
		el.className = "stu-toast" + (kind ? " stu-toast--" + kind : "");
		el.hidden = false;
		clearTimeout(toast._t);
		toast._t = setTimeout(function () { el.hidden = true; }, kind === "error" ? 8000 : 4000);
	}

	function ago(ts) {
		var d = (Date.now() - new Date(String(ts).replace(" ", "T")).getTime()) / 1000;
		if (!isFinite(d)) return "";
		if (d < 90) return "just now";
		if (d < 3600) return Math.round(d / 60) + " min ago";
		if (d < 86400) return Math.round(d / 3600) + " h ago";
		return Math.round(d / 86400) + " d ago";
	}

	// ---------------------------------------------------------------- session

	function applySession(sess) {
		var isNew = !S.session || S.session.name !== sess.name;
		S.session = sess;
		if (isNew) {
			S.optKind = sess.option_kind || "";
			S.optRows = (sess.options || []).slice();
			S.length = sess.garment_length || "";
		}
		var labels = (sess.options || []).map(function (o) { return o.label; });
		if (labels.indexOf(S.sourceLabel) < 0) S.sourceLabel = labels[0] || "";
		var rows = allRows();
		var known = {};
		rows.forEach(function (r) {
			known[r.id] = 1;
			if (!(r.id in S.inc)) S.inc[r.id] = r.include;
			if (r.main && !S.main) S.main = r.id;
		});
		if (S.main && !known[S.main]) S.main = null;
		if (!S.main) {
			var first = galleryRows().filter(function (r) { return r.kind === "AI"; })[0];
			if (first) S.main = first.id;
		}
		watchQueue();
	}

	// While drawings wait on the image Mac, keep looking: each look also brings home what's
	// finished. Someone typing isn't interrupted; the next look picks up what they missed.
	function watchQueue() {
		clearTimeout(watchQueue._t);
		if (!S.session || !S.session.queued) return;
		watchQueue._t = setTimeout(function () {
			var before = S.session;
			if (!before || !before.queued) return;
			if (S.busy || document.visibilityState !== "visible" || isTyping()) return watchQueue();
			api("get_session", { session: before.name }, { get: true }).then(function (sess) {
				if (!S.session || S.session.name !== sess.name) return;
				var changed = sess.queued !== before.queued || sess.runs.length !== before.runs.length ||
					sess.last_error !== before.last_error;
				if (changed && !isTyping()) { applySession(sess); render(); } else watchQueue();
			}, function () { watchQueue(); });
		}, 15000);
	}

	function isTyping() {
		var el = document.activeElement;
		if (!el) return false;
		return el.tagName === "TEXTAREA" || el.tagName === "SELECT" ||
			(el.tagName === "INPUT" && el.type !== "checkbox" && el.type !== "file");
	}

	function galleryRows() {
		var out = [];
		((S.session && S.session.runs) || []).forEach(function (run) {
			out = out.concat(run.panels);
			if (run.raw) out.push(run.raw);
		});
		return out;
	}

	function allRows() {
		return galleryRows().concat((S.session && S.session.supporting) || []);
	}

	function keptGallery() {
		return galleryRows().filter(function (r) { return S.inc[r.id]; });
	}

	function pollSession(maxMs) {
		var started = Date.now();
		return new Promise(function (resolve) {
			(function tick() {
				api("get_session", { session: S.session.name }, { get: true }).then(function (sess) {
					if (!sess.busy || Date.now() - started > maxMs) return resolve(sess);
					setTimeout(tick, 4000);
				}, function () {
					if (Date.now() - started > maxMs) return resolve(null);
					setTimeout(tick, 5000);
				});
			})();
		});
	}

	// ------------------------------------------------------------------ views

	function head(n, title, extra) {
		return '<div class="stu-step__head"><span class="stu-num">' + n + '</span><h2 class="stu-h">' +
			esc(title) + "</h2>" + (extra || "") + "</div>";
	}

	function viewStart() {
		var html = "";
		if (!S.boot.configured) {
			html += '<div class="stu-note stu-note--warn">AI isn\'t set up yet. Add your Gemini API key in ' +
				'<a href="/app/akwaaba-studio-settings">Akwaaba Studio Settings</a>. You can still upload photos.</div>';
		}
		html += '<section class="stu-card stu-capture">' +
			'<h2 class="stu-h">New item</h2>' +
			'<p class="stu-muted">Photograph the piece on the mannequin or hanger: straight on, in good light, ' +
			"with the whole garment in frame.</p>" +
			'<button type="button" class="stu-capture__btn" data-act="camera">' +
			'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4h6l1.5 2H20a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h3.5z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><circle cx="12" cy="12.5" r="3.6" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>' +
			"<span>Take photo</span></button>" +
			'<button type="button" class="stu-btn stu-btn--ghost stu-btn--block" data-act="library">Choose from library</button>' +
			"</section>";

		var open = S.boot.open_sessions || [];
		if (open.length) {
			html += '<section class="stu-card"><h2 class="stu-h stu-h--sm">Pick up where you left off</h2><ul class="stu-resume">' +
				open.map(function (s) {
					return '<li><button type="button" data-act="resume" data-id="' + esc(s.name) + '">' +
						(s.source_image ? '<img src="' + esc(s.source_image) + '" alt="" loading="lazy">' : '<span class="stu-resume__ph"></span>') +
						"<span><strong>" + esc(s.title && s.title !== "Untitled" ? s.title : s.name) + "</strong>" +
						"<small>" + esc(s.status) + " · " + esc(ago(s.modified)) + "</small></span></button></li>";
				}).join("") + "</ul></section>";
		}
		return html;
	}

	function viewPhoto() {
		var s = S.session;
		return '<section class="stu-card stu-step">' +
			head(1, "Photo", '<button type="button" class="stu-link" data-act="new">New photo</button>') +
			'<div class="stu-source">' +
			'<img src="' + esc(s.source) + '" alt="Your photo" data-act="view" data-url="' + esc(s.source) + '">' +
			'<div class="stu-source__meta"><span>' + esc(s.name) + "</span>" +
			(s.est_cost ? "<span>AI so far ≈ " + usd(s.est_cost) + "</span>" : "") +
			'<button type="button" class="stu-link stu-link--danger" data-act="discard">Discard</button></div>' +
			"</div>" + viewOptions() + "</section>";
	}

	var SIZE_CHIPS = ["Mini", "Midi", "Maxi", "S", "M", "L", "XL"];

	function viewOptions() {
		var kinds = [["", "One version"], ["Size", "Sizes"], ["Colour", "Colours"]];
		var word = S.optKind === "Colour" ? "colour" : "size";
		var html = '<div class="stu-opts"><h3 class="stu-opts__h">Comes in</h3><div class="stu-seg" role="tablist">' +
			kinds.map(function (k) {
				return '<button type="button" role="tab" data-act="opt-kind" data-kind="' + k[0] + '" aria-selected="' + (S.optKind === k[0]) + '">' + k[1] + "</button>";
			}).join("") + "</div>";
		if (!S.optKind) return html + "</div>";

		html += '<p class="stu-muted">Each ' + word + " keeps its own photos and can have its own price. Leave a price empty to use the main one.</p>";
		html += S.optRows.map(function (row, i) {
			var photos = (S.session.supporting || []).filter(function (r) { return row.label && r.option === row.label; });
			return '<div class="stu-opt">' +
				'<div class="stu-opt__row">' +
				'<input data-opt="label" data-i="' + i + '" aria-label="Name of this ' + word + '" placeholder="' + (word === "size" ? "Mini" : "Indigo") + '" autocomplete="off" value="' + esc(row.label) + '">' +
				'<input data-opt="price" data-i="' + i + '" aria-label="Price" type="number" inputmode="decimal" min="0" step="0.01" placeholder="Price" value="' + esc(row.price || "") + '">' +
				'<button type="button" class="stu-opt__del" data-act="opt-remove" data-i="' + i + '" aria-label="Remove this ' + word + '">×</button>' +
				"</div>" +
				(row.label ? '<div class="stu-opt__photos">' + photos.map(function (r) {
					return '<img src="' + esc(r.url) + '" alt="" loading="lazy" data-act="view" data-url="' + esc(r.url) + '">';
				}).join("") + '<button type="button" class="stu-add stu-add--sm" data-act="opt-photo" data-label="' + esc(row.label) + '"><span>+</span>Photo</button></div>' : "") +
				"</div>";
		}).join("");
		html += '<button type="button" class="stu-link" data-act="opt-add">+ Add a ' + word + "</button>";
		if (S.optKind === "Size") {
			var used = S.optRows.map(function (r) { return r.label; });
			html += '<div class="stu-chips">' + SIZE_CHIPS.filter(function (c) { return used.indexOf(c) < 0; }).map(function (c) {
				return '<button type="button" class="stu-chip stu-chip--btn" data-act="opt-chip" data-label="' + c + '">' + c + "</button>";
			}).join("") + "</div>";
		}
		return html + "</div>";
	}

	function saveOptions() {
		var rows = S.optRows.filter(function (r) { return (r.label || "").trim(); });
		var blanks = S.optRows.filter(function (r) { return !(r.label || "").trim(); });
		var focus = document.activeElement;
		var refocus = focus && focus.hasAttribute && focus.hasAttribute("data-opt")
			? '[data-opt="' + focus.getAttribute("data-opt") + '"][data-i="' + focus.getAttribute("data-i") + '"]' : null;
		return api("set_options", { session: S.session.name, kind: S.optKind, options: rows }).then(function (sess) {
			applySession(sess);
			S.optRows = (sess.options || []).concat(blanks);
			render();
			var again = refocus && $(refocus);
			if (again) again.focus();
		}).catch(function (e) { toast(e.message, "error"); });
	}

	function storyFields() {
		var needsStory = (S.boot.prompts || []).some(function (p) { return S.selected.has(p.name) && p.uses_model_arc; });
		var html = "";
		if (needsStory) {
			var groups = {}, order = [];
			(S.boot.arcs || []).forEach(function (a) {
				if (!groups[a.group_name]) { groups[a.group_name] = []; order.push(a.group_name); }
				groups[a.group_name].push(a);
			});
			html += field("Model", '<select data-story="model">' + (S.boot.models || []).map(function (m) {
				return '<option value="' + esc(m.name) + '"' + (m.name === S.model ? " selected" : "") + ">" + esc(m.name) + (m.label ? " · " + esc(m.label) : "") + "</option>";
			}).join("") + "</select>") +
				field("Arc", '<select data-story="arc">' + order.map(function (g) {
					return '<optgroup label="' + esc(g) + '">' + groups[g].map(function (a) {
						return '<option value="' + esc(a.name) + '"' + (a.name === S.arc ? " selected" : "") + ">" +
							esc(a.arc_number + ". " + a.name) + (a.skirts_only ? " (skirts)" : "") + "</option>";
					}).join("") + "</optgroup>";
				}).join("") + "</select>");
		}
		if ((S.boot.engines || []).length > 1) {
			html += field("Made by", '<select data-story="engine">' + S.boot.engines.map(function (e) {
				return '<option value="' + esc(e.key) + '"' + (e.key === S.engine ? " selected" : "") + ">" + esc(e.label) + "</option>";
			}).join("") + "</select>");
		}
		html += field("Garment length", '<input data-story="length" list="stu-lengths" autocomplete="off" placeholder="e.g. floor-length maxi" value="' + esc(S.length) + '">' +
			'<datalist id="stu-lengths"><option value="mini"><option value="knee-length"><option value="midi"><option value="ankle-length"><option value="floor-length maxi"></datalist>');
		var options = S.session.options || [];
		if (options.length) {
			html += field("Start from the photo of", '<select data-story="sourceLabel">' + options.map(function (o) {
				return '<option value="' + esc(o.label) + '"' + (o.label === S.sourceLabel ? " selected" : "") + ">" + esc(o.label) + "</option>";
			}).join("") + "</select>");
		}
		return html;
	}

	function viewStyles() {
		var prompts = S.boot.prompts || [];
		var list = prompts.length ? prompts.map(function (p) {
			var on = S.selected.has(p.name);
			return '<li><label class="stu-prompt' + (on ? " is-on" : "") + '">' +
				'<input type="checkbox" data-act="prompt" value="' + esc(p.name) + '"' + (on ? " checked" : "") + ">" +
				'<span class="stu-check" aria-hidden="true"></span>' +
				'<span class="stu-prompt__body"><span class="stu-prompt__title">' + esc(p.title) + "</span>" +
				(p.hint ? '<span class="stu-prompt__hint">' + esc(p.hint) + "</span>" : "") +
				'<span class="stu-prompt__meta"><span class="stu-chip">' + (p.panels > 1 ? p.panels + " photos" : "1 photo") +
				'</span><span class="stu-chip">' + esc(p.image_size) + '</span><span class="stu-chip">≈ ' + usd(p.cost) + "</span></span>" +
				"</span></label></li>";
		}).join("") : '<li class="stu-muted">No prompts yet. Add some in <a href="/app/akwaaba-studio-prompt/new">Desk</a>.</li>';

		var hasRuns = S.session.runs.length > 0;
		return '<section class="stu-card stu-step">' +
			head(2, hasRuns ? "More styles" : "Choose styles") +
			'<p class="stu-muted">' + (hasRuns ? "Add another look, or redo one below." : "Pick one or more. Each runs once on your photo.") + "</p>" +
			'<ul class="stu-prompts">' + list + '</ul><div id="stu-story">' + storyFields() + "</div></section>";
	}

	function tile(r, caption) {
		var on = !!S.inc[r.id];
		var isMain = S.main === r.id;
		return '<figure class="stu-tile' + (on ? "" : " is-off") + (isMain ? " is-main" : "") + '">' +
			'<img src="' + esc(r.url) + '" alt="" loading="lazy" data-act="view" data-url="' + esc(r.url) + '">' +
			'<button type="button" class="stu-tile__btn stu-tile__main" data-act="main" data-id="' + esc(r.id) +
			'" aria-pressed="' + isMain + '" aria-label="Use as main photo">★</button>' +
			'<button type="button" class="stu-tile__btn stu-tile__inc" data-act="toggle" data-id="' + esc(r.id) +
			'" aria-pressed="' + on + '" aria-label="' + (on ? "Remove from gallery" : "Keep in gallery") + '">' + (on ? "✓" : "+") + "</button>" +
			(isMain ? '<span class="stu-tile__badge">Main</span>' : "") +
			(caption ? "<figcaption>" + esc(caption) + "</figcaption>" : "") +
			"</figure>";
	}

	function viewResults() {
		var runs = S.session.runs;
		var queued = S.session.queued || 0;
		var problem = S.errors.length ? "" : S.session.last_error;
		if (!runs.length && !S.errors.length && !queued && !problem) return "";
		var html = '<section class="stu-card stu-step" id="stu-results">' +
			head(3, "Pick photos") +
			'<p class="stu-muted">★ marks the main photo. ✓ keeps a photo in the gallery. Tap a photo to see it bigger.</p>';

		html += runs.map(function (run) {
			var multi = run.panels.length > 1;
			return '<div class="stu-run">' +
				'<div class="stu-run__head"><h3>' + esc(run.title) +
				(run.option ? ' <span class="stu-chip">' + esc(run.option) + "</span>" : "") +
				(run.label ? "<small>" + esc(run.label) + "</small>" : "") + "</h3>" +
				'<span class="stu-run__actions">' +
				'<button type="button" class="stu-link" data-act="retry" data-prompt="' + esc(run.prompt) + '">Redo</button>' +
				'<button type="button" class="stu-link stu-link--danger" data-act="drop-run" data-run="' + run.run + '">Remove</button>' +
				"</span></div>" +
				(run.note ? '<p class="stu-note stu-note--warn">' + esc(run.note) + "</p>" : "") +
				'<div class="stu-grid' + (multi ? "" : " stu-grid--one") + '">' +
				run.panels.map(function (p) { return tile(p); }).join("") + "</div>" +
				(run.raw ? '<div class="stu-raw">' + tile(run.raw, "Whole image, unsliced") + "</div>" : "") +
				"</div>";
		}).join("");

		html += S.errors.map(function (e) {
			return '<div class="stu-note stu-note--error"><strong>' + esc(e.title) + ":</strong> " + esc(e.error) +
				' <button type="button" class="stu-link" data-act="retry" data-prompt="' + esc(e.prompt) + '">Try again</button></div>';
		}).join("");
		if (problem) {
			html += '<div class="stu-note stu-note--error">' + esc(problem).replace(/\n/g, "<br>") +
				"<br>Choose the style again to try once more. It carries on from any photos already drawn.</div>";
		}
		if (queued) {
			html += '<p class="stu-note stu-note--warn">' + queued + (queued > 1 ? " drawings are" : " drawing is") +
				" waiting on the image Mac. Drafts appear here as they're ready, and you can close this page.</p>";
		}
		return html + "</section>";
	}

	function viewSupporting() {
		var rows = S.session.supporting || [];
		return '<section class="stu-card stu-step">' +
			head(4, "Supporting photos") +
			'<p class="stu-muted">Your original photo and any extras appear under “More Views” on the product page.</p>' +
			'<div class="stu-grid stu-grid--3">' +
			rows.map(function (r) {
				var on = !!S.inc[r.id];
				return '<figure class="stu-tile stu-tile--sm' + (on ? "" : " is-off") + '">' +
					'<img src="' + esc(r.url) + '" alt="" loading="lazy" data-act="view" data-url="' + esc(r.url) + '">' +
					'<button type="button" class="stu-tile__btn stu-tile__inc" data-act="toggle" data-id="' + esc(r.id) +
					'" aria-pressed="' + on + '" aria-label="' + (on ? "Leave out" : "Include") + '">' + (on ? "✓" : "+") + "</button>" +
					(r.kind === "Extra" ? '<button type="button" class="stu-tile__btn stu-tile__del" data-act="remove" data-id="' + esc(r.id) + '" aria-label="Delete photo">×</button>' : "") +
					"<figcaption>" + esc(r.option || (r.kind === "Original" ? "Original" : "Extra")) + "</figcaption></figure>";
			}).join("") +
			'<button type="button" class="stu-add" data-act="extra"><span>+</span>Add photos</button>' +
			"</div></section>";
	}

	function field(label, inner, opt) {
		return '<label class="stu-field"><span>' + esc(label) + (opt ? " <em>optional</em>" : "") + "</span>" + inner + "</label>";
	}

	function viewDetails() {
		var f = S.form;
		var html = '<section class="stu-card stu-step" id="stu-details">' + head(5, "Item details") +
			'<div class="stu-seg" role="tablist">' +
			'<button type="button" role="tab" data-act="mode" data-mode="new" aria-selected="' + (S.mode === "new") + '">New item</button>' +
			'<button type="button" role="tab" data-act="mode" data-mode="existing" aria-selected="' + (S.mode === "existing") + '">Existing item</button>' +
			"</div>";

		if (S.mode === "new") {
			html += field("Name", '<input name="item_name" data-bind autocomplete="off" enterkeyhint="next" placeholder="e.g. Eli Mix Bubu — Kola Wine" value="' + esc(f.item_name) + '">') +
				field("Category", '<select name="item_group" data-bind>' + (S.boot.groups || []).map(function (g) {
					return '<option value="' + esc(g) + '"' + (g === f.item_group ? " selected" : "") + ">" + esc(g) + "</option>";
				}).join("") + "</select>") +
				'<div class="stu-row">' +
				field("Price (" + esc(S.boot.currency || "GHS") + ")", '<input name="price" data-bind type="number" inputmode="decimal" min="0" step="0.01" placeholder="0.00" value="' + esc(f.price) + '">') +
				field("Item code", '<input name="item_code" data-bind autocapitalize="characters" autocomplete="off" spellcheck="false" value="' + esc(f.item_code) + '">') +
				"</div>" +
				field("Availability", '<select name="stock_mode" data-bind>' +
					'<option value="made_to_order"' + (f.stock_mode !== "limited" ? " selected" : "") + ">Made to order</option>" +
					'<option value="limited"' + (f.stock_mode === "limited" ? " selected" : "") + ">Limited pieces</option></select>") +
				(f.stock_mode === "limited" ? '<p class="stu-muted">Shows as sold out until its quantity is set on the live site.</p>' : "") +
				field("Description", '<textarea name="description" data-bind rows="3" placeholder="Fabric, fit, length…">' + esc(f.description) + "</textarea>", true) +
				'<label class="stu-switch"><input type="checkbox" name="publish" data-bind' + (f.publish ? " checked" : "") + '><span class="stu-switch__ui"></span>Publish on the shop now</label>';
		} else {
			if (S.existing) {
				var it = S.existing;
				html += '<div class="stu-picked">' +
					(it.image ? '<img src="' + esc(it.image) + '" alt="">' : '<span class="stu-resume__ph"></span>') +
					"<span><strong>" + esc(it.item_name) + "</strong><small>" + esc(it.item_code) + " · " + esc(it.item_group) + "</small></span>" +
					'<button type="button" class="stu-link" data-act="unpick">Change</button></div>';
			} else {
				html += field("Find the item", '<input type="search" id="stu-search" placeholder="Name or code" autocomplete="off" value="' + esc(f.q) + '">') +
					'<ul class="stu-results" id="stu-search-results">' + viewSearchResults() + "</ul>";
			}
			html += '<label class="stu-switch"><input type="checkbox" name="set_main" data-bind' + (f.set_main ? " checked" : "") +
				'><span class="stu-switch__ui"></span>Make the ★ photo its main photo</label>';
		}
		return html + "</section>";
	}

	function viewSearchResults() {
		return (S.results || []).map(function (it, i) {
			return '<li><button type="button" data-act="pick" data-i="' + i + '">' +
				(it.image ? '<img src="' + esc(it.image) + '" alt="" loading="lazy">' : '<span class="stu-resume__ph"></span>') +
				"<span><strong>" + esc(it.item_name) + "</strong><small>" + esc(it.item_code) + " · " + esc(it.item_group) + "</small></span></button></li>";
		}).join("");
	}

	function viewDone() {
		var d = S.done;
		return '<section class="stu-card stu-done">' +
			'<div class="stu-done__mark" aria-hidden="true">✓</div>' +
			'<h2 class="stu-h">' + esc(d.item_name) + "</h2>" +
			'<p class="stu-muted">' + esc(d.item_code) + " · " +
			(d.published ? "Live on the shop." : "Saved as a draft, not on the shop yet. Publish it from Desk once it's priced.") + "</p>" +
			'<div class="stu-done__links">' +
			(d.published && d.product_url ? '<a class="stu-btn" href="' + esc(d.product_url) + '">View on shop</a>' : "") +
			'<a class="stu-btn stu-btn--ghost" href="' + esc(d.desk_url) + '">Open in Desk</a></div>' +
			'<button type="button" class="stu-btn stu-btn--block" data-act="restart">Next item</button>' +
			"</section>";
	}

	function render() {
		var app = $("#stu-app");
		if (S.done) app.innerHTML = viewDone();
		else if (!S.session) app.innerHTML = viewStart();
		else app.innerHTML = viewPhoto() + viewStyles() + viewResults() + viewSupporting() + viewDetails();
		updateBar();
	}

	function updateBar() {
		var bar = $("#stu-bar");
		if (!S.session || S.done) { bar.hidden = true; document.body.classList.remove("stu-has-bar"); return; }
		var n = S.selected.size;
		var html;
		if (n) {
			var cost = (S.boot.prompts || []).filter(function (p) { return S.selected.has(p.name); })
				.reduce(function (sum, p) { return sum + Number(p.cost || 0); }, 0);
			html = '<button type="button" class="stu-btn stu-btn--block stu-btn--gold" data-act="generate"' + (S.boot.configured ? "" : " disabled") + ">" +
				"Generate " + n + " style" + (n > 1 ? "s" : "") + ' <span class="stu-btn__sub">' + (S.engine === "local" ? "free" : "≈ " + usd(cost)) + "</span></button>";
		} else if (S.session.runs.length || (S.session.supporting || []).length) {
			html = '<button type="button" class="stu-btn stu-btn--block" data-act="save">' +
				(S.mode === "existing" ? "Add photos to item" : "Create item") +
				' <span class="stu-btn__sub">' + keptGallery().length + " in gallery</span></button>";
		}
		bar.innerHTML = html || "";
		bar.hidden = !html;
		document.body.classList.toggle("stu-has-bar", !!html);
	}

	// ------------------------------------------------------------------- busy

	function setBusy(label, progress) {
		var el = $("#stu-busy");
		if (!label) {
			S.busy = null;
			if (el) el.remove();
			clearInterval(setBusy._t);
			return;
		}
		if (!el) {
			el = document.createElement("div");
			el.id = "stu-busy";
			el.className = "stu-busy";
			el.innerHTML = '<div class="stu-busy__card"><div class="stu-busy__spin" aria-hidden="true"></div>' +
				'<div class="stu-busy__label"></div><div class="stu-busy__line"></div>' +
				'<div class="stu-busy__bar"><span></span></div><div class="stu-busy__time"></div></div>';
			document.body.appendChild(el);
		}
		if (!S.busy || S.busy.label !== label) {
			S.busy = { label: label, since: Date.now() };
			clearInterval(setBusy._t);
			setBusy._t = setInterval(tickBusy, 1000);
		}
		$(".stu-busy__label", el).textContent = label;
		var bar = $(".stu-busy__bar", el);
		bar.hidden = progress == null;
		if (progress != null) $("span", bar).style.width = Math.round(progress * 100) + "%";
		tickBusy();
	}

	function tickBusy() {
		var el = $("#stu-busy");
		if (!el || !S.busy) return;
		var secs = Math.round((Date.now() - S.busy.since) / 1000);
		var generating = /Generat/.test(S.busy.label);
		$(".stu-busy__time", el).textContent = generating ? secs + "s · usually 15–60s" : "";
		$(".stu-busy__line", el).textContent = generating ? BUSY_LINES[Math.floor(secs / 5) % BUSY_LINES.length] : "";
	}

	// ---------------------------------------------------------------- actions

	function pickPhoto(input) {
		input.value = "";
		input.click();
	}

	function startWith(file) {
		setBusy("Preparing photo…");
		shrink(file, S.boot.max_photo_px).then(function (blob) {
			setBusy("Uploading photo…", 0);
			return upload("start_session", [blob], {}, function (p) { setBusy("Uploading photo…", p); });
		}).then(function (sess) {
			S.inc = {}; S.main = null; S.errors = []; S.selected.clear();
			applySession(sess);
			render();
			window.scrollTo({ top: 0 });
		}).catch(function (e) {
			toast(e.message, "error");
		}).then(function () { setBusy(null); });
	}

	function addExtras(files) {
		files = Array.prototype.slice.call(files || []);
		var label = S.extraLabel;
		S.extraLabel = "";
		if (!files.length) return;
		setBusy("Preparing photos…");
		Promise.all(files.map(function (f) { return shrink(f, S.boot.max_photo_px); })).then(function (blobs) {
			setBusy("Uploading photos…", 0);
			return upload("add_photos", blobs, { session: S.session.name, option_label: label }, function (p) { setBusy("Uploading photos…", p); });
		}).then(function (sess) {
			applySession(sess);
			render();
		}).catch(function (e) { toast(e.message, "error"); })
			.then(function () { setBusy(null); });
	}

	function generate(prompts) {
		if (!prompts.length) return;
		setBusy("Generating " + prompts.length + " style" + (prompts.length > 1 ? "s" : "") + "…");
		api("generate", {
			session: S.session.name,
			prompts: prompts,
			model: S.model,
			arc: S.arc,
			engine: S.engine,
			option_label: S.sourceLabel,
			garment_length: S.length,
		}).then(function (sess) {
			return sess;
		}, function (e) {
			// Gateway timeout or dropped connection: the server keeps going, so wait for it.
			if (!e.status || e.status >= 502) {
				setBusy("Still working — reconnecting…");
				return pollSession(240000).then(function (sess) {
					if (!sess) throw e;
					sess.errors = sess.last_error ? [{ title: "Some styles failed", error: sess.last_error, prompt: prompts[0] }] : [];
					return sess;
				});
			}
			throw e;
		}).then(function (sess) {
			S.errors = sess.errors || [];
			S.selected.clear();
			applySession(sess);
			render();
			var target = $("#stu-results");
			if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
			if (S.errors.length) toast(S.errors.length + " style" + (S.errors.length > 1 ? "s" : "") + " failed. See below.", "error");
			if (sess.queued) {
				// Sent to the image Mac: it may take a while, or wait until the Mac is reachable.
				toast(sess.queued + " sent to the image Mac. You can close this page; drafts appear under “Pick up where you left off”.");
			}
		}).catch(function (e) {
			toast(e.message, "error");
		}).then(function () { setBusy(null); });
	}

	function save() {
		var waiting = S.session.queued || 0;
		if (waiting && !window.confirm((waiting > 1 ? waiting + " drawings are" : "A drawing is") + " still waiting on the image Mac. Save without " + (waiting > 1 ? "them" : "it") + "?")) return;
		var kept = keptGallery();
		var selection = {};
		allRows().forEach(function (r) { selection[r.id] = S.inc[r.id] ? 1 : 0; });
		var main = S.main && S.inc[S.main] ? S.main : (kept[0] && kept[0].id) || null;
		var f = S.form;
		var data = { selection: selection, main: main, mode: S.mode };

		if (S.mode === "existing") {
			if (!S.existing) { toast("Pick the item first.", "error"); focusDetails(); return; }
			data.existing_item = S.existing.item_code;
			data.set_main = f.set_main ? 1 : 0;
		} else {
			if (!(f.item_name || "").trim()) { toast("Give the item a name.", "error"); focusDetails("item_name"); return; }
			var everyOptionPriced = (S.session.options || []).length && S.session.options.every(function (o) { return Number(o.price) > 0; });
			if (f.publish && !(Number(f.price) > 0) && !everyOptionPriced) { toast("Add a price to publish, or untick Publish.", "error"); focusDetails("price"); return; }
			["item_name", "item_group", "item_code", "price", "description", "stock_mode"].forEach(function (k) { data[k] = f[k] || ""; });
			data.publish = f.publish ? 1 : 0;
		}

		setBusy(S.mode === "existing" ? "Adding photos…" : "Creating item…");
		api("finalize", { session: S.session.name, data: data }).then(function (res) {
			S.done = res;
			S.boot.open_sessions = (S.boot.open_sessions || []).filter(function (s) { return s.name !== S.session.name; });
			render();
			window.scrollTo({ top: 0 });
		}).catch(function (e) { toast(e.message, "error"); })
			.then(function () { setBusy(null); });
	}

	function focusDetails(name) {
		var sec = $("#stu-details");
		if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
		var input = name && $('[name="' + name + '"]');
		if (input) setTimeout(function () { input.focus(); }, 350);
	}

	function resetToStart() {
		S.session = null; S.done = null; S.inc = {}; S.main = null; S.errors = [];
		S.selected.clear(); S.existing = null; S.results = [];
		S.form = { item_group: S.form.item_group, publish: !!S.boot.publish_by_default, stock_mode: "made_to_order" };
		S.optKind = ""; S.optRows = []; S.sourceLabel = ""; S.extraLabel = "";
		S.codeTouched = false;
		render();
		window.scrollTo({ top: 0 });
	}

	function refreshCode() {
		if (S.codeTouched || S.mode !== "new" || !S.form.item_group) return;
		api("suggest_code", { item_group: S.form.item_group, item_name: S.form.item_name || "" }, { get: true })
			.then(function (code) {
				if (S.codeTouched) return;
				S.form.item_code = code;
				var input = $('[name="item_code"]');
				if (input) input.value = code;
			}, function () { /* optional nicety */ });
	}

	var searchTimer;
	function search(q) {
		S.form.q = q;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(function () {
			api("search_items", { txt: q }, { get: true }).then(function (rows) {
				S.results = rows || [];
				var ul = $("#stu-search-results");
				if (ul) ul.innerHTML = viewSearchResults();
			}, function () { /* keep previous results */ });
		}, 250);
	}

	// ----------------------------------------------------------------- events

	document.addEventListener("click", function (ev) {
		var el = ev.target.closest("[data-act]");
		if (!el || el.tagName === "INPUT") return;
		var act = el.getAttribute("data-act");
		var id = el.getAttribute("data-id");

		switch (act) {
			case "camera": pickPhoto($("#stu-cam")); break;
			case "library": pickPhoto($("#stu-lib")); break;
			case "extra": pickPhoto($("#stu-extra")); break;
			case "view":
				var dlg = $("#stu-viewer");
				$("img", dlg).src = el.getAttribute("data-url");
				if (dlg.showModal) dlg.showModal(); else window.open(el.getAttribute("data-url"));
				break;
			case "resume":
				setBusy("Opening…");
				api("get_session", { session: id }, { get: true }).then(function (sess) {
					S.inc = {}; S.main = null; S.errors = [];
					applySession(sess);
					render();
					if (sess.busy) {
						setBusy("Still generating…");
						return pollSession(240000).then(function (s2) { if (s2) { applySession(s2); render(); } });
					}
				}).catch(function (e) { toast(e.message, "error"); })
					.then(function () { setBusy(null); });
				break;
			case "new":
				if (!S.session.runs.length || window.confirm("Start a new item? This one stays under “Pick up where you left off”.")) {
					var cur = S.session;
					if (cur && !(S.boot.open_sessions || []).some(function (s) { return s.name === cur.name; })) {
						(S.boot.open_sessions = S.boot.open_sessions || []).unshift({
							name: cur.name, title: cur.title, status: cur.status, source_image: cur.source, modified: new Date().toISOString(),
						});
					}
					resetToStart();
				}
				break;
			case "discard":
				if (!window.confirm("Delete this photo and everything generated from it?")) break;
				api("discard", { session: S.session.name }).then(function () {
					var gone = S.session.name;
					S.boot.open_sessions = (S.boot.open_sessions || []).filter(function (s) { return s.name !== gone; });
					resetToStart();
				}).catch(function (e) { toast(e.message, "error"); });
				break;
			case "opt-kind":
				S.optKind = el.getAttribute("data-kind");
				if (!S.optKind) { S.optRows = []; saveOptions(); break; }
				if (!S.optRows.length) S.optRows = [{ label: "", price: "" }];
				if (S.optRows.some(function (r) { return r.label; })) saveOptions(); else render();
				break;
			case "opt-add":
				S.optRows.push({ label: "", price: "" });
				render();
				break;
			case "opt-chip":
				var blank = S.optRows.filter(function (r) { return !r.label; })[0];
				if (blank) blank.label = el.getAttribute("data-label");
				else S.optRows.push({ label: el.getAttribute("data-label"), price: "" });
				saveOptions();
				break;
			case "opt-remove":
				S.optRows.splice(Number(el.getAttribute("data-i")), 1);
				saveOptions();
				break;
			case "opt-photo":
				S.extraLabel = el.getAttribute("data-label");
				pickPhoto($("#stu-extra"));
				break;
			case "generate": generate(Array.from(S.selected)); break;
			case "retry": generate([el.getAttribute("data-prompt")]); break;
			case "drop-run":
				var run = S.session.runs.filter(function (r) { return String(r.run) === el.getAttribute("data-run"); })[0];
				if (!run || !window.confirm("Remove these photos?")) break;
				var ids = run.panels.map(function (p) { return p.id; }).concat(run.raw ? [run.raw.id] : []);
				api("remove_rows", { session: S.session.name, rows: ids }).then(function (sess) {
					applySession(sess); render();
				}).catch(function (e) { toast(e.message, "error"); });
				break;
			case "remove":
				api("remove_rows", { session: S.session.name, rows: [id] }).then(function (sess) {
					applySession(sess); render();
				}).catch(function (e) { toast(e.message, "error"); });
				break;
			case "toggle":
				S.inc[id] = S.inc[id] ? 0 : 1;
				if (!S.inc[id] && S.main === id) S.main = null;
				render();
				break;
			case "main":
				S.main = id;
				S.inc[id] = 1;
				render();
				break;
			case "mode":
				S.mode = el.getAttribute("data-mode");
				render();
				if (S.mode === "existing" && !S.existing && !S.results.length) search("");
				if (S.mode === "new") refreshCode();
				break;
			case "pick":
				S.existing = S.results[Number(el.getAttribute("data-i"))];
				render();
				break;
			case "unpick":
				S.existing = null;
				render();
				break;
			case "save": save(); break;
			case "restart": resetToStart(); break;
		}
	});

	document.addEventListener("change", function (ev) {
		var t = ev.target;
		if (t.id === "stu-cam" || t.id === "stu-lib") {
			if (t.files && t.files[0]) startWith(t.files[0]);
			return;
		}
		if (t.id === "stu-extra") { addExtras(t.files); return; }
		if (t.matches('[data-act="prompt"]')) {
			if (t.checked) S.selected.add(t.value); else S.selected.delete(t.value);
			t.closest(".stu-prompt").classList.toggle("is-on", t.checked);
			var story = $("#stu-story");
			if (story) story.innerHTML = storyFields();
			updateBar();
			return;
		}
		if (t.hasAttribute("data-story")) {
			S[t.getAttribute("data-story")] = t.value;
			updateBar();
			return;
		}
		if (t.hasAttribute("data-opt")) {
			S.optRows[Number(t.getAttribute("data-i"))][t.getAttribute("data-opt")] = t.value;
			saveOptions();
			return;
		}
		if (t.hasAttribute("data-bind")) {
			S.form[t.name] = t.type === "checkbox" ? t.checked : t.value;
			if (t.name === "item_group" || t.name === "item_name") refreshCode();
			if (t.name === "stock_mode") render();
		}
	});

	document.addEventListener("input", function (ev) {
		var t = ev.target;
		if (t.id === "stu-search") { search(t.value); return; }
		if (t.hasAttribute("data-bind") && t.type !== "checkbox") {
			S.form[t.name] = t.value;
			if (t.name === "item_code") S.codeTouched = !!t.value.trim();
		}
	});

	// Back from the lock screen / another app mid-generation: catch up.
	document.addEventListener("visibilitychange", function () {
		if (document.visibilityState !== "visible" || !S.session || S.busy) return;
		if (S.session.status === "Generating" || S.session.busy) {
			api("get_session", { session: S.session.name }, { get: true }).then(function (sess) {
				applySession(sess); render();
			}, function () { /* next visibility change will retry */ });
		}
	});

	render();
	refreshCode();
})();
