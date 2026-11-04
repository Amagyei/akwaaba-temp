"""Whitelisted endpoints behind the phone Studio page (/studio).

Flow:
	start_session(file)            photo of the garment on a mannequin/hanger
	set_options(session, …)        optional sizes or colours; photos carry the label
	generate(session, prompts, …)  one image call per chosen prompt, in parallel,
	                               with Gemini or a local tool; each output is sliced
	                               into its grid panels. The local tool is queued
	                               instead: see "Jobs on the image Mac" below.
	add_photos / remove_rows       optional supporting photos, drop bad runs
	finalize(session, data)        new item, or add to an existing one

Every photo lives as a private File on the session until finalize copies the
chosen ones onto the catalog (see studio/catalog.py), so nothing a phone loses
mid-way — or an AI call already paid for — is thrown away.
"""

import io
import json
import random
from concurrent.futures import ThreadPoolExecutor

import frappe
from frappe.utils import cint, flt, now_datetime, time_diff_in_seconds
from PIL import Image, ImageOps

from akwaaba_theme.studio import catalog, gemini, local_engine, slicer

STUDIO_ROLES = {"System Manager", "Item Manager", "Website Manager"}
SESSION = "Akwaaba Studio Session"
SETTINGS = "Akwaaba Studio Settings"
PROMPT = "Akwaaba Studio Prompt"
MODEL = "Akwaaba Studio Model"
ARC = "Akwaaba Studio Arc"
OPTION_KINDS = ("Size", "Colour")
ENGINE_GEMINI = "gemini"
ENGINE_LOCAL = "local"
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
		fields=["name", "title", "hint", "uses_model_arc", "rows", "cols", "aspect_ratio", "model", "image_size"],
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

	has_gemini = bool(s.get_password("gemini_api_key", raise_exception=False))
	engines = []
	if has_gemini:
		engines.append({"key": ENGINE_GEMINI, "label": "Gemini (paid per image)"})
	if (s.local_engine_url or "").strip():
		engines.append({"key": ENGINE_LOCAL, "label": "This computer (free)"})

	return {
		"configured": bool(cint(s.enabled) and engines),
		"engines": engines,
		"models": frappe.get_all(MODEL, filters={"enabled": 1}, fields=["name", "label"], order_by="model_name asc"),
		"arcs": frappe.get_all(
			ARC,
			filters={"enabled": 1},
			fields=["name", "arc_number", "group_name", "summary", "skirts_only"],
			order_by="arc_number asc",
		),
		"option_kinds": OPTION_KINDS,
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
	sess = _get_session(session)
	if sess.status == "Generating" and sess.host_jobs:
		# Opening the page is a good moment to look: finished drawings turn up as drafts at once.
		try:
			collect_session(session)
			sess = _get_session(session)
		except Exception:
			frappe.log_error(title=f"Studio: couldn't collect images for {session}")
	return _payload(sess)


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
def set_options(session, kind=None, options=None):
	"""Sizes or colours this piece comes in: `options` is [{label, price}].

	The first photo belongs to the first option until moved. An empty `kind` means
	one version, and any labels on photos are cleared.
	"""
	require_studio_user()
	sess = _get_session(session, editable=True)
	options = frappe.parse_json(options) or [] if kind else []
	if kind and kind not in OPTION_KINDS:
		frappe.throw("A piece comes in sizes or colours.")

	clean, seen = [], set()
	for option in options:
		label = " ".join(str(option.get("label") or "").split())
		if not label:
			continue
		if label.lower() in seen:
			frappe.throw(f"{label} is listed twice.")
		seen.add(label.lower())
		clean.append({"label": label, "price": flt(option.get("price"))})

	labels = [o["label"] for o in clean]
	for r in sess.images:
		if r.option_label and r.option_label not in labels:
			r.option_label = None
		if r.kind == "Original" and not r.option_label and labels:
			r.option_label = labels[0]

	sess.option_kind = kind if clean else None
	sess.options_json = frappe.as_json(clean, indent=None) if clean else None
	sess.save(ignore_permissions=True)
	return _payload(sess)


@frappe.whitelist(methods=["POST"])
def add_photos(session, option_label=None):
	"""Multipart `file` (repeatable): optional supporting photos, or a size or
	colour's own photos when `option_label` is given."""
	require_studio_user()
	sess = _get_session(session, editable=True)
	if option_label and option_label not in [o["label"] for o in _options(sess)]:
		frappe.throw(f"{option_label} isn't one of this piece's options.")
	n = sum(1 for r in sess.images if r.kind == "Extra")
	for upload in _uploads():
		n += 1
		f = _save_file(sess, _normalise(upload.stream.read()), f"extra-{n}")
		sess.append("images", {"image": f.file_url, "kind": "Extra", "include": 1, "option_label": option_label or None})
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
def generate(session, prompts, model=None, arc=None, engine=None, option_label=None):
	"""Run each prompt against the source photo; slice and store the results.

	Prompts run in parallel. A failed prompt doesn't sink the others — its
	error comes back in `errors` and the page offers a retry. With `option_label`,
	the source is that size or colour's photo and the results carry its label.
	"""
	require_studio_user()
	sess = _get_session(session, editable=True)
	names = frappe.parse_json(prompts) if isinstance(prompts, str) and prompts.startswith("[") else prompts
	names = [names] if isinstance(names, str) else list(names or [])
	if not names:
		frappe.throw("Pick at least one style.")
	# Lock the row: a double-tap must not pass this check twice and pay twice.
	current = frappe.db.get_value(SESSION, sess.name, ["status", "generating_since", "host_jobs"], as_dict=True, for_update=True)
	# Drawings queued on the image Mac don't block more: they're waiting in line, not running here.
	if current.status == "Generating" and not _is_stale(current) and not current.host_jobs:
		frappe.throw("Still working on the last request — hang on a moment.")

	s = _settings()
	if not cint(s.enabled):
		frappe.throw("Studio is switched off in Akwaaba Studio Settings.")
	api_key = s.get_password("gemini_api_key", raise_exception=False)
	timeout = cint(s.request_timeout) or 110
	local_url = (s.local_engine_url or "").strip()
	engine = engine or (ENGINE_GEMINI if api_key or not local_url else ENGINE_LOCAL)
	if engine == ENGINE_LOCAL and not local_url:
		frappe.throw("No local image tool is set up in Akwaaba Studio Settings.")

	person = frappe.get_doc(MODEL, model) if model else None
	story = frappe.get_doc(ARC, arc) if arc else None

	jobs = []
	for name in names:
		p = frappe.get_doc(PROMPT, name)
		if cint(p.uses_model_arc) and not (person and story):
			frappe.throw(f"Pick a model and an arc for {p.title}.")
		ai_model, size = _model_for(p, s)
		jobs.append({
			"prompt": p, "model": ai_model, "size": size,
			"text": _full_prompt(p, s, person, story),
			"label": f"{person.name} · {story.name}" if cint(p.uses_model_arc) else None,
		})
	source = _file_bytes(sess, _source_for(sess, option_label))
	if engine == ENGINE_LOCAL:
		return _queue_on_host(sess, jobs, source, local_url, option_label)

	# Make "Generating" visible to other requests (the phone may poll if the
	# connection drops while we wait on Gemini).
	sess.db_set({"status": "Generating", "generating_since": now_datetime(), "last_error": None})
	frappe.db.commit()

	def run(job):
		# Worker thread: no frappe calls in here.
		try:
			if engine == ENGINE_LOCAL:
				return local_engine.edit_image(
					url=local_url,
					prompt=job["text"],
					image_bytes=source,
					aspect_ratio=job["prompt"].aspect_ratio or "3:4",
					image_size=job["size"],
				), None
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
			if engine != ENGINE_LOCAL:
				cost += gemini.estimate_cost(job["model"], job["size"])
			try:
				_store_run(sess, p, run_no, data, job["label"], option_label)
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


# ---------------------------------------------------------------------------
# Jobs on the image Mac
#
# A local drawing can take minutes, and the image Mac may be asleep, busy or out of
# reach. So generate() only hands the photo over and returns; the Mac keeps the job
# on disk and draws whenever it can. Finished drawings are fetched by collect_session
# (every minute, and whenever the session is opened) and land as ordinary rows that
# the review page shows with "include" ticked or not: nothing is final until finalize.
# ---------------------------------------------------------------------------

def _host_jobs(sess):
	try:
		return list(json.loads(sess.host_jobs)) if sess.host_jobs else []
	except ValueError:
		return []


def _queue_on_host(sess, jobs, source, url, option_label):
	waiting = _host_jobs(sess)
	queued, errors = [], []
	for job in jobs:
		p = job["prompt"]
		# A double tap shouldn't draw the same style twice.
		if any(w["prompt"] == p.name and (w.get("option_label") or None) == (option_label or None) for w in waiting + queued):
			continue
		try:
			job_id = local_engine.submit_job(
				url=url,
				prompt=job["text"],
				image_bytes=source,
				aspect_ratio=p.aspect_ratio or "3:4",
				image_size=job["size"],
				seed=random.randrange(2**31),  # a redo should look different, not the same again
			)
		except gemini.GeminiError as e:
			errors.append({"prompt": p.name, "title": p.title, "error": str(e)})
			continue
		queued.append({
			"job_id": job_id, "prompt": p.name, "label": job["label"],
			"option_label": option_label or None, "sent": str(now_datetime()),
		})

	if queued:
		sess.host_jobs = json.dumps(waiting + queued)
		sess.status = "Generating"
		sess.generating_since = now_datetime()
	if errors:
		sess.last_error = "\n".join(f"{e['title']}: {e['error']}" for e in errors)
	sess.save(ignore_permissions=True)
	frappe.db.commit()

	out = _payload(sess)
	out["errors"] = errors
	return out


def collect_session(name):
	"""Fetch what the image Mac has finished for this session. Returns True if anything changed.

	An unreachable Mac is not an error: whatever is still out there stays on the list
	and is asked for again next time.
	"""
	url = (_settings().local_engine_url or "").strip()
	if not url or not frappe.db.exists(SESSION, name):
		return False
	frappe.db.get_value(SESSION, name, "name", for_update=True)  # one collector at a time
	sess = frappe.get_doc(SESSION, name)
	waiting = _host_jobs(sess)
	if not waiting:
		return False

	remaining, errors, collected = [], [], []
	run_no = max([cint(r.run) for r in sess.images] or [0])
	offline = False
	for j in waiting:
		if offline:
			remaining.append(j)
			continue
		title = frappe.db.get_value(PROMPT, j["prompt"], "title") or j["prompt"]
		try:
			state = local_engine.job_status(url=url, job_id=j["job_id"])
		except local_engine.Unreachable:
			offline = True
			remaining.append(j)
			continue
		except gemini.GeminiError:
			remaining.append(j)  # an odd answer: ask again later
			continue

		status = state.get("status")
		if status in ("queued", "running"):
			remaining.append(j)
		elif status == "done":
			try:
				data = local_engine.job_result(url=url, job_id=j["job_id"])
				run_no += 1
				_store_run(sess, frappe.get_doc(PROMPT, j["prompt"]), run_no, data, j.get("label"), j.get("option_label"))
				collected.append(j)
			except local_engine.Unreachable:
				offline = True
				remaining.append(j)
			except Exception as e:  # noqa: BLE001 — shown on the page, logged below
				frappe.log_error(title=f"Studio: couldn't store {j['prompt']} for {name}")
				errors.append(f"{title}: Couldn't process the image: {e}")
				collected.append(j)
		else:  # failed, or the Mac no longer knows the job
			errors.append(f"{title}: {state.get('error') or 'The image Mac lost this job.'}")
			collected.append(j)

	if len(remaining) == len(waiting):
		return False

	sess.host_jobs = json.dumps(remaining) if remaining else None
	if errors:
		sess.last_error = "\n".join(errors)
	if not remaining:
		sess.generating_since = None
		sess.status = "Review" if any(r.kind in GALLERY_KINDS for r in sess.images) else "Draft"
	sess.save(ignore_permissions=True)
	frappe.db.commit()
	for j in collected:  # only now that the drafts are saved here is the Mac's copy not needed
		local_engine.acknowledge_job(url=url, job_id=j["job_id"])
	return True


def collect_host_jobs():
	"""Scheduler: bring home whatever the image Mac has finished, for every waiting session."""
	for name in frappe.get_all(SESSION, filters={"status": "Generating", "host_jobs": ["is", "set"]}, pluck="name"):
		try:
			collect_session(name)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Studio: couldn't collect images for {name}")


def _store_run(sess, prompt, run_no, data, run_label=None, option_label=None):
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
			"run_label": run_label, "option_label": option_label or None,
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
			"run_label": run_label, "option_label": option_label or None,
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
	if not gallery and not supporting:
		frappe.throw("Choose at least one photo.")
	if not gallery:
		# Only supporting photos were kept. The first one leads, so a product
		# can't publish with no main image.
		gallery, supporting = supporting[:1], supporting[1:]
	main = next((r for r in gallery if cint(r.is_main)), gallery[0])

	files = {r.name: _file_name(sess, r.image) for r in gallery + supporting}
	kw = dict(
		gallery=[files[r.name] for r in gallery],
		supporting=[files[r.name] for r in supporting],
		main=files[main.name],
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
		common = dict(
			item_code=item_code,
			item_name=item_name,
			item_group=data.item_group,
			price=flt(data.get("price")),
			description=(data.get("description") or "").strip(),
			publish=bool(cint(data.get("publish"))),
			is_stock_item=1 if data.get("stock_mode") == "limited" else 0,
		)
		options = _options(sess)
		if options:
			wi = catalog.create_item_with_options(
				kind=sess.option_kind,
				options=_option_photos(sess, options, gallery, supporting, main, files),
				**common,
				**_shared_photos(gallery, supporting, main, files),
			)
		else:
			wi = catalog.create_item(**common, **kw)
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


def cleanup_completed_sessions(days=30):
	"""Delete saved sessions and their private photos once they're this old.

	Weekly job. Published photos are separate public copies, so they stay.
	By hand: bench --site <site> execute akwaaba_theme.studio.api.cleanup_completed_sessions
	"""
	cutoff = frappe.utils.add_days(frappe.utils.now_datetime(), -cint(days))
	names = frappe.get_all(SESSION, filters={"status": "Completed", "modified": ["<", cutoff]}, pluck="name")
	for name in names:
		frappe.delete_doc(SESSION, name, ignore_permissions=True)
	frappe.db.commit()
	return len(names)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings():
	return frappe.get_single(SETTINGS)


def _model_for(prompt, s):
	model = (prompt.get("model") or s.default_model or "gemini-nano-banana-2.1").strip()
	size = prompt.get("image_size") or s.default_image_size or "2K"
	return model, gemini.resolve_size(model, size)


def _full_prompt(prompt, s, person=None, story=None):
	text = (prompt.prompt or "").strip()
	if cint(prompt.uses_model_arc):
		text = text.replace("{story}", _full_story(prompt, person, story))
		text = text.replace("{model}", person.profile)
	parts = [text]
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


def _full_story(prompt, person, story):
	"""The owner's master template, with the uploaded photo as the wardrobe."""
	with open(frappe.get_app_path("akwaaba_theme", "studio", "prompt_library.json")) as f:
		template = frappe.parse_json(f.read())["template"]
	shots = "\n".join(
		f"- {place} frame (Shot {i}): {story.get(f'shot_{i}')}"
		for i, place in enumerate(("Top-left", "Top-right", "Bottom-left", "Bottom-right"), start=1)
	)
	return "\n\n".join((
		template["format"],
		f"[MODEL]\n{person.profile}",
		f"[WARDROBE]\n{template['wardrobe']}",
		f"[LIGHTING & SET]\n{template['lighting_set']}",
		f"[DIRECTORIAL NARRATIVE ARC: {story.name}]\n{shots}",
		f"[STYLE & RENDERING]\n{template['style']}",
	))


def _options(sess):
	return frappe.parse_json(sess.options_json) or [] if sess.options_json else []


def _source_for(sess, option_label):
	if not option_label:
		return sess.source_image
	for r in sess.images:
		if r.kind in SUPPORTING_KINDS and r.option_label == option_label:
			return r.image
	frappe.throw(f"Add a photo for {option_label} first.")


def _shared_photos(gallery, supporting, main, files):
	"""Photos with no size or colour on them go on the template. With none, the
	template shows the first photos kept, so its page isn't empty."""
	shared = [r for r in gallery if not r.option_label] or gallery[:1]
	keep_main = main if main in shared else shared[0]
	return dict(
		gallery=[files[r.name] for r in shared],
		supporting=[files[r.name] for r in supporting if not r.option_label],
		main=files[keep_main.name],
	)


def _option_photos(sess, options, gallery, supporting, main, files):
	out = []
	for option in options:
		mine = [r for r in gallery if r.option_label == option["label"]]
		extras = [r for r in supporting if r.option_label == option["label"]]
		if not mine and extras:
			# Only its own plain photos were kept, so the first one leads.
			mine, extras = extras[:1], extras[1:]
		lead = main if main in mine else (mine[0] if mine else None)
		out.append(dict(
			option,
			gallery=[files[r.name] for r in mine],
			supporting=[files[r.name] for r in extras],
			main=files[lead.name] if lead else None,
		))
	return out


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
	return catalog.file_bytes(frappe.get_doc("File", _file_name(sess, url)))


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
			"label": r.run_label,
			"option": r.option_label,
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
		"busy": sess.status == "Generating" and (bool(sess.host_jobs) or not _is_stale(sess)),
		"queued": len(_host_jobs(sess)),
		"item": sess.item,
		"est_cost": flt(sess.est_cost_usd, 4),
		"source": sess.source_image,
		"runs": [runs[k] for k in sorted(runs)],
		"supporting": [_row(r) for r in sess.images if r.kind in SUPPORTING_KINDS],
		"option_kind": sess.option_kind,
		"options": _options(sess),
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
		"option": r.option_label,
	}
