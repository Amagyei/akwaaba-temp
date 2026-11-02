"""Whitelisted endpoints behind the phone Studio page (/studio).

Flow:
	start_session(file)            photo of the garment on a mannequin/hanger
	generate(session, prompts)     one Gemini call per chosen prompt, in parallel;
	                               each output is sliced into its grid panels
	add_photos / remove_rows       optional supporting photos, drop bad runs
	finalize(session, data)        new item, or add to an existing one

Every photo lives as a private File on the session until finalize copies the
chosen ones onto the catalog (see studio/catalog.py), so nothing a phone loses
mid-way — or an AI call already paid for — is thrown away.
"""

import io
from concurrent.futures import ThreadPoolExecutor

import frappe
from frappe.utils import cint, flt, now_datetime, time_diff_in_seconds
from PIL import Image, ImageOps

from akwaaba_theme.studio import catalog, gemini, slicer

STUDIO_ROLES = {"System Manager", "Item Manager", "Website Manager"}
SESSION = "Akwaaba Studio Session"
SETTINGS = "Akwaaba Studio Settings"
PROMPT = "Akwaaba Studio Prompt"
STALE_AFTER_SECS = 300  # a "Generating" session older than this was abandoned
GALLERY_KINDS = ("AI", "AI Raw")
SUPPORTING_KINDS = ("Original", "Extra")


def require_studio_user():
	if frappe.session.user == "Guest" or not STUDIO_ROLES & set(frappe.get_roles()):
		frappe.throw("Studio is for catalog staff only.", frappe.PermissionError)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@frappe.whitelist()
def bootstrap():
	"""Everything the page needs on load (also inlined by www/studio)."""
	require_studio_user()
	s = _settings()

	prompts = frappe.get_all(
		PROMPT,
		filters={"enabled": 1},
		fields=["name", "title", "hint", "rows", "cols", "aspect_ratio", "model", "image_size"],
		order_by="sort_order asc, title asc",
	)
	for p in prompts:
		model, size = _model_for(p, s)
		p.update(
			model=model,
			image_size=size,
			panels=max(1, cint(p.rows)) * max(1, cint(p.cols)),
			cost=gemini.estimate_cost(model, size),
		)

	open_sessions = frappe.get_all(
		SESSION,
		filters={"owner": frappe.session.user, "status": ["in", ["Draft", "Generating", "Review"]]},
		fields=["name", "title", "status", "source_image", "modified"],
		order_by="modified desc",
		limit=6,
	)

	return {
		"configured": bool(cint(s.enabled) and s.get_password("gemini_api_key", raise_exception=False)),
		"prompts": prompts,
		"groups": frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", order_by="name asc"),
		"default_group": s.default_item_group,
		"publish_by_default": cint(s.publish_by_default),
		"max_photo_px": cint(s.max_photo_px) or 2048,
		"currency": frappe.db.get_value("Price List", s.price_list or catalog.DEFAULT_PRICE_LIST, "currency") or "GHS",
		"open_sessions": open_sessions,
	}


@frappe.whitelist()
def get_session(session):
	require_studio_user()
	return _payload(_get_session(session))


@frappe.whitelist()
def suggest_code(item_group, item_name=""):
	require_studio_user()
	return catalog.suggest_item_code(item_group, item_name)


@frappe.whitelist()
def search_items(txt=""):
	require_studio_user()
	txt = (txt or "").strip()
	or_filters = [["item_code", "like", f"%{txt}%"], ["item_name", "like", f"%{txt}%"]] if txt else None
	return frappe.get_all(
		"Item",
		filters={"disabled": 0, "variant_of": ["is", "not set"]},
		or_filters=or_filters,
		fields=["item_code", "item_name", "item_group", "image"],
		order_by="modified desc",
		limit=15,
	)


# ---------------------------------------------------------------------------
# Photos in
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def start_session():
	"""Multipart `file`: the garment photo. Creates the session."""
	require_studio_user()
	upload = _uploads()[0]
	data = _normalise(upload.stream.read())

	sess = frappe.get_doc({"doctype": SESSION, "title": "Untitled", "status": "Draft"})
	sess.insert(ignore_permissions=True)
	f = _save_file(sess, data, "source", field="source_image")
	sess.source_image = f.file_url
	sess.append("images", {"image": f.file_url, "kind": "Original", "include": 1})
	sess.save(ignore_permissions=True)
	return _payload(sess)


@frappe.whitelist(methods=["POST"])
def add_photos(session):
	"""Multipart `file` (repeatable): optional supporting photos."""
	require_studio_user()
	sess = _get_session(session, editable=True)
	n = sum(1 for r in sess.images if r.kind == "Extra")
	for upload in _uploads():
		n += 1
		f = _save_file(sess, _normalise(upload.stream.read()), f"extra-{n}")
		sess.append("images", {"image": f.file_url, "kind": "Extra", "include": 1})
	sess.save(ignore_permissions=True)
	return _payload(sess)


@frappe.whitelist(methods=["POST"])
def remove_rows(session, rows):
	"""Drop photos (child row names) from the session, files included."""
	require_studio_user()
	sess = _get_session(session, editable=True)
	rows = set(frappe.parse_json(rows) or [])
	gone = [r for r in sess.images if r.name in rows and r.kind != "Original"]
	for r in gone:
		sess.remove(r)
	sess.save(ignore_permissions=True)
	for r in gone:
		for f in frappe.get_all("File", filters=_file_filters(sess, r.image), pluck="name"):
			frappe.delete_doc("File", f, ignore_permissions=True)
	return _payload(sess)


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def generate(session, prompts):
	"""Run each prompt against the source photo; slice and store the results.

	Prompts run in parallel. A failed prompt doesn't sink the others — its
	error comes back in `errors` and the page offers a retry.
	"""
	require_studio_user()
	sess = _get_session(session, editable=True)
	names = frappe.parse_json(prompts) if isinstance(prompts, str) and prompts.startswith("[") else prompts
	names = [names] if isinstance(names, str) else list(names or [])
	if not names:
		frappe.throw("Pick at least one style.")
	if sess.status == "Generating" and not _is_stale(sess):
		frappe.throw("Still working on the last request — hang on a moment.")

	s = _settings()
	if not cint(s.enabled):
		frappe.throw("Studio is switched off in Akwaaba Studio Settings.")
	api_key = s.get_password("gemini_api_key", raise_exception=False)
	timeout = cint(s.request_timeout) or 110

	jobs = []
	for name in names:
		p = frappe.get_doc(PROMPT, name)
		model, size = _model_for(p, s)
		jobs.append({"prompt": p, "model": model, "size": size, "text": _full_prompt(p, s)})
	source = _file_bytes(sess, sess.source_image)

	# Make "Generating" visible to other requests (the phone may poll if the
	# connection drops while we wait on Gemini).
	sess.db_set({"status": "Generating", "generating_since": now_datetime(), "last_error": None})
	frappe.db.commit()

	def run(job):
		# Worker thread: no frappe calls in here.
		try:
			return gemini.edit_image(
				api_key=api_key,
				model=job["model"],
				prompt=job["text"],
				image_bytes=source,
				aspect_ratio=job["prompt"].aspect_ratio or "3:4",
				image_size=job["size"],
				timeout=timeout,
			), None
		except gemini.GeminiError as e:
			return None, str(e)
		except Exception as e:  # noqa: BLE001 — surfaced to the phone, logged below
			return None, f"Unexpected error: {e.__class__.__name__}: {e}"

	try:
		with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
			results = list(pool.map(run, jobs))

		sess.reload()
		errors, cost = [], flt(sess.est_cost_usd)
		run_no = max([cint(r.run) for r in sess.images] or [0])
		for job, (data, err) in zip(jobs, results):
			p = job["prompt"]
			if err:
				errors.append({"prompt": p.name, "title": p.title, "error": err})
				continue
			run_no += 1
			cost += gemini.estimate_cost(job["model"], job["size"])
			try:
				_store_run(sess, p, run_no, data)
			except Exception as e:
				frappe.log_error(title=f"Studio: couldn't slice {p.name}")
				errors.append({"prompt": p.name, "title": p.title, "error": f"Couldn't process the image: {e}"})

		has_ai = any(r.kind in GALLERY_KINDS for r in sess.images)
		sess.status = "Review" if has_ai else "Draft"
		sess.est_cost_usd = cost
		sess.generating_since = None
		sess.last_error = "\n".join(f"{e['title']}: {e['error']}" for e in errors) or None
		sess.save(ignore_permissions=True)
	except Exception:
		frappe.db.rollback()
		frappe.db.set_value(SESSION, sess.name, {"status": "Draft", "generating_since": None})
		frappe.db.commit()
		raise

	out = _payload(sess)
	out["errors"] = errors
	return out


def _store_run(sess, prompt, run_no, data):
	img = Image.open(io.BytesIO(data))
	img.load()
	panels, seams_ok = slicer.slice_grid(img, prompt.rows, prompt.cols)
	has_main = any(cint(r.is_main) for r in sess.images)
	main_panel = min(max(1, cint(prompt.default_main_panel) or 1), len(panels))

	if len(panels) > 1:
		raw = _save_file(sess, _jpeg(img), f"r{run_no}-full")
		sess.append("images", {
			"image": raw.file_url, "kind": "AI Raw", "prompt": prompt.name,
			"run": run_no, "panel": 0, "include": 0,
			"note": None if seams_ok else (
				"Couldn't see where the panels meet, so they were cut at the exact "
				"middle. Check the crops, or use the whole image instead."
			),
		})

	for i, panel in enumerate(panels, start=1):
		f = _save_file(sess, _jpeg(panel), f"r{run_no}-p{i}")
		sess.append("images", {
			"image": f.file_url, "kind": "AI", "prompt": prompt.name,
			"run": run_no, "panel": i, "include": 1,
			"is_main": 1 if (not has_main and i == main_panel) else 0,
		})


# ---------------------------------------------------------------------------
# Out to the catalog
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def finalize(session, data):
	"""Commit the chosen photos to a new or existing item.

	data: {selection: {row: 0|1}, main: row, mode: "new"|"existing",
	       item_code, item_name, item_group, price, description, publish,
	       existing_item, set_main}
	"""
	require_studio_user()
	sess = _get_session(session, editable=True)
	data = frappe._dict(frappe.parse_json(data) or {})

	selection = data.get("selection") or {}
	for r in sess.images:
		if r.name in selection:
			r.include = cint(selection[r.name])
		r.is_main = 1 if r.name == data.get("main") else 0
		if r.is_main:
			r.include = 1

	ordered = sorted(sess.images, key=lambda r: (cint(r.run), cint(r.panel)))
	gallery = [r for r in ordered if r.kind in GALLERY_KINDS and cint(r.include)]
	supporting = [r for r in sess.images if r.kind in SUPPORTING_KINDS and cint(r.include)]
	main = next((r for r in gallery if cint(r.is_main)), gallery[0] if gallery else None)
	if not gallery and not supporting:
		frappe.throw("Choose at least one photo.")

	files = {r.name: _file_name(sess, r.image) for r in gallery + supporting}
	kw = dict(
		gallery=[files[r.name] for r in gallery],
		supporting=[files[r.name] for r in supporting],
		main=files[main.name] if main else None,
	)

	if data.get("mode") == "existing":
		item_code = data.get("existing_item")
		if not item_code or not frappe.db.exists("Item", item_code):
			frappe.throw("Pick the item to add these photos to.")
		wi = catalog.add_to_item(item_code=item_code, set_main=bool(cint(data.get("set_main"))), **kw)
		title = frappe.db.get_value("Item", item_code, "item_name")
	else:
		item_name = (data.get("item_name") or "").strip()
		if not item_name:
			frappe.throw("Give the item a name.")
		if not data.get("item_group"):
			frappe.throw("Pick a category.")
		item_code = (data.get("item_code") or "").strip() or catalog.suggest_item_code(data.item_group, item_name)
		wi = catalog.create_item(
			item_code=item_code,
			item_name=item_name,
			item_group=data.item_group,
			price=flt(data.get("price")),
			description=(data.get("description") or "").strip(),
			publish=bool(cint(data.get("publish"))),
			**kw,
		)
		title = item_name

	sess.item = item_code
	sess.title = title
	sess.status = "Completed"
	sess.save(ignore_permissions=True)

	from frappe.website.utils import clear_cache

	clear_cache()
	return {
		"item_code": item_code,
		"item_name": title,
		"published": cint(wi.published),
		"product_url": f"/product/{wi.route}" if wi.route else None,
		"desk_url": f"/app/item/{item_code}",
		"website_item_url": f"/app/website-item/{wi.name}",
	}


@frappe.whitelist(methods=["POST"])
def discard(session):
	require_studio_user()
	sess = _get_session(session, editable=True)
	frappe.delete_doc(SESSION, sess.name, ignore_permissions=True)  # takes its files with it
	return "ok"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings():
	return frappe.get_single(SETTINGS)


def _model_for(prompt, s):
	model = (prompt.get("model") or s.default_model or "gemini-nano-banana-2.1").strip()
	size = prompt.get("image_size") or s.default_image_size or "2K"
	return model, gemini.resolve_size(model, size)


def _full_prompt(prompt, s):
	parts = [(prompt.prompt or "").strip()]
	rows, cols = max(1, cint(prompt.rows)), max(1, cint(prompt.cols))
	if rows * cols > 1:
		# Spelled out on every grid prompt because the slicer depends on it.
		parts.append(
			f"Layout: a single image made of exactly {rows} row{'s' if rows > 1 else ''} × "
			f"{cols} column{'s' if cols > 1 else ''} = {rows * cols} equal panels, edge to edge, "
			"with no gaps, borders, frames, captions or dividing lines between them. "
			"Keep each panel's subject fully inside its own panel."
		)
	if (s.house_style or "").strip():
		parts.append(s.house_style.strip())
	return "\n\n".join(parts)


def _get_session(name, editable=False):
	if not name or not frappe.db.exists(SESSION, name):
		frappe.throw("That Studio session no longer exists.", frappe.DoesNotExistError)
	sess = frappe.get_doc(SESSION, name)
	if editable and sess.status == "Completed":
		frappe.throw(f"This session was already saved to {sess.item}. Start a new one.")
	return sess


def _is_stale(sess):
	return not sess.generating_since or (
		time_diff_in_seconds(now_datetime(), sess.generating_since) > STALE_AFTER_SECS
	)


def _uploads():
	files = frappe.request.files.getlist("file") if frappe.request and frappe.request.files else []
	files = [f for f in files if f and f.filename]
	if not files:
		frappe.throw("No photo received.")
	return files


def _normalise(raw):
	"""Phone photo → upright, size-capped RGB JPEG bytes."""
	max_px = cint(frappe.db.get_single_value(SETTINGS, "max_photo_px")) or 2048
	try:
		img = Image.open(io.BytesIO(raw))
		img = ImageOps.exif_transpose(img)
	except Exception:
		frappe.throw("Couldn't read that photo. Try taking it again, or pick a JPEG/PNG.")
	img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
	return _jpeg(img)


def _jpeg(img, quality=90):
	buf = io.BytesIO()
	img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
	return buf.getvalue()


def _save_file(sess, content, suffix, field=None):
	return frappe.get_doc({
		"doctype": "File",
		"file_name": f"{sess.name.lower()}-{suffix}.jpg",
		"attached_to_doctype": SESSION,
		"attached_to_name": sess.name,
		"attached_to_field": field,
		"is_private": 1,
		"content": content,
		"decode": False,
	}).insert(ignore_permissions=True)


def _file_filters(sess, url):
	return {"file_url": url, "attached_to_doctype": SESSION, "attached_to_name": sess.name}


def _file_name(sess, url):
	name = frappe.db.get_value("File", _file_filters(sess, url)) or frappe.db.get_value("File", {"file_url": url})
	if not name:
		frappe.throw(f"Photo {url} is missing — remove it and try again.")
	return name


def _file_bytes(sess, url):
	return frappe.get_doc("File", _file_name(sess, url)).get_content()


def _payload(sess):
	titles = dict(frappe.get_all(PROMPT, fields=["name", "title"], as_list=True))
	runs = {}
	for r in sess.images:
		if r.kind not in GALLERY_KINDS:
			continue
		run = runs.setdefault(cint(r.run), {
			"run": cint(r.run),
			"prompt": r.prompt,
			"title": titles.get(r.prompt, r.prompt),
			"raw": None,
			"note": None,
			"panels": [],
		})
		if r.kind == "AI Raw":
			run["raw"], run["note"] = _row(r), r.note
		else:
			run["panels"].append(_row(r))
	for run in runs.values():
		run["panels"].sort(key=lambda p: p["panel"])

	return {
		"name": sess.name,
		"title": sess.title,
		"status": sess.status,
		"busy": sess.status == "Generating" and not _is_stale(sess),
		"item": sess.item,
		"est_cost": flt(sess.est_cost_usd, 4),
		"source": sess.source_image,
		"runs": [runs[k] for k in sorted(runs)],
		"supporting": [_row(r) for r in sess.images if r.kind in SUPPORTING_KINDS],
		"last_error": sess.last_error,
	}


def _row(r):
	return {
		"id": r.name,
		"url": r.image,
		"kind": r.kind,
		"panel": cint(r.panel),
		"include": cint(r.include),
		"main": cint(r.is_main),
	}
