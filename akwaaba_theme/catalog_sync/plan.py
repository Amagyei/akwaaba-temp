"""Work out what a push would change. Pure logic, no site access."""

from dataclasses import dataclass, field

from akwaaba_theme.catalog_sync.export import FIELDS, FILE_FIELDS

COLLECTIONS = (
	("item_groups", "Item Group"),
	("items", "Item"),
	("prices", "Item Price"),
	("slideshows", "Website Slideshow"),
	("website_items", "Website Item"),
)

# What each field is called in the report.
NUMERIC = {"price_list_rate"}


@dataclass
class Change:
	collection: str
	doctype: str
	key: str
	action: str  # create, update, unpublish
	values: dict = field(default_factory=dict)  # field → local value, URLs still local
	before: dict = field(default_factory=dict)  # field → live value


@dataclass
class Plan:
	changes: list = field(default_factory=list)
	conflicts: list = field(default_factory=list)  # (collection, key, what changed)
	live_only: list = field(default_factory=list)  # records on live the laptop has never seen
	attachments: list = field(default_factory=list)  # (doctype, name, local url)
	attribute_values: list = field(default_factory=list)  # (attribute, value)
	held: int = 0  # laptop-only photos from before the baseline, not being sent

	@property
	def is_empty(self):
		return not (self.changes or self.attachments or self.attribute_values)


def _same(field_name, ours, theirs, is_file, local_files, live_files):
	if is_file:
		ours_hash = local_files.get(ours)
		theirs_hash = live_files.get(theirs)
		if ours_hash and theirs_hash:
			return ours_hash == theirs_hash
	if field_name in NUMERIC or isinstance(ours, (int, float)) or isinstance(theirs, (int, float)):
		try:
			return abs(float(ours or 0) - float(theirs or 0)) < 0.005
		except (TypeError, ValueError):
			pass
	return (ours or "") == (theirs or "")


def _slides_same(ours, theirs, local_files, live_files):
	if len(ours) != len(theirs):
		return False
	for a, b in zip(ours, theirs):
		if not _same("image", a["image"], b["image"], True, local_files, live_files):
			return False
		if any((a[f] or "") != (b[f] or "") for f in ("heading", "description", "url")):
			return False
	return True


def diff_record(collection, doctype, ours, theirs, local_files, live_files):
	"""Fields that differ, as {field: local value}, plus what live has now."""
	values, before = {}, {}
	if collection == "slideshows":
		if not _slides_same(ours["slides"], theirs["slides"], local_files, live_files):
			values["slides"], before["slides"] = ours["slides"], theirs["slides"]
		return values, before
	file_fields = set(FILE_FIELDS.get(doctype, ()))
	for name in FIELDS[doctype]:
		if name not in ours:
			continue
		if not _same(name, ours[name], theirs.get(name), name in file_fields, local_files, live_files):
			values[name], before[name] = ours[name], theirs.get(name)
	if collection == "items" and [dict(a) for a in ours.get("attributes", [])] != [dict(a) for a in theirs.get("attributes", [])]:
		values["attributes"], before["attributes"] = ours["attributes"], theirs.get("attributes")
	return values, before


def laptop_only_attachments(local, live):
	"""{owner: [hash, …]} for photos attached on the laptop and not on live."""
	local_files, live_files = local.get("files", {}), live.get("files", {})
	out = {}
	for owner, urls in local.get("attachments", {}).items():
		on_live = {live_files.get(u) for u in live.get("attachments", {}).get(owner, [])}
		missing = {local_files.get(u) for u in urls} - on_live - {None}
		if missing:
			out[owner] = sorted(missing)
	return out


def make_plan(local, live, state, include_held=False):
	"""`state` holds each record's live `modified` as of the last sync. A record
	changed on live since then is left alone and reported, not overwritten."""
	plan = Plan()
	local_files, live_files = local.get("files", {}), live.get("files", {})

	for collection, doctype in COLLECTIONS:
		seen = state.get(collection, {})
		for key, ours in local[collection].items():
			theirs = live[collection].get(key)
			if theirs is None:
				plan.changes.append(Change(collection, doctype, key, "create", _without_modified(ours)))
				continue
			values, before = diff_record(collection, doctype, ours, theirs, local_files, live_files)
			if not values:
				continue
			if key in seen and seen[key] != theirs["_modified"]:
				plan.conflicts.append((collection, key, sorted(values)))
				continue
			if key not in seen:
				# Never synced, so nothing says the laptop's version is the newer one.
				plan.conflicts.append((collection, key, sorted(values)))
				continue
			plan.changes.append(Change(collection, doctype, key, "update", values, before))

		for key, theirs in live[collection].items():
			if key in local[collection]:
				continue
			if collection == "website_items" and key in seen:
				if theirs.get("published"):
					plan.changes.append(Change(collection, doctype, key, "unpublish", {"published": 0}, {"published": 1}))
			elif key not in seen:
				plan.live_only.append((collection, key))

	held = state.get("held_attachments", {})
	for owner, missing in laptop_only_attachments(local, live).items():
		doctype, name = owner.split("/", 1)
		url_for = {local_files.get(u): u for u in local["attachments"][owner]}
		for digest in missing:
			if digest in held.get(owner, []) and not include_held:
				plan.held += 1
			else:
				plan.attachments.append((doctype, name, url_for[digest]))

	for attribute, values in local.get("attribute_values", {}).items():
		known = set(live.get("attribute_values", {}).get(attribute, []))
		plan.attribute_values.extend((attribute, v) for v in values if v not in known)

	order = {c: i for i, (c, _d) in enumerate(COLLECTIONS)}
	# A template must exist before its variants, and a parent group before its children.
	plan.changes.sort(key=lambda c: (order[c.collection], bool(c.values.get("variant_of"))))
	return plan


def next_state(old, after, local, conflicts):
	"""What a push leaves behind. Only records that really are in step move on.

	A record only on live stays unknown, so it's never mistaken for one removed
	from the laptop and unpublished. A skipped conflict keeps its old mark, so the
	next push still refuses it instead of overwriting that edit.
	"""
	skipped = {(collection, key) for collection, key, _fields in conflicts}
	state = {"held_attachments": old.get("held_attachments", {})}
	for collection, _doctype in COLLECTIONS:
		kept = dict(old.get(collection, {}))
		for key, rec in after[collection].items():
			if key in local[collection] and (collection, key) not in skipped:
				kept[key] = rec["_modified"]
		state[collection] = kept
	return state


def _without_modified(rec):
	return {k: v for k, v in rec.items() if k != "_modified"}


def describe(plan):
	"""The dry-run report, as lines."""
	lines = []
	labels = {"create": "NEW", "update": "CHANGE", "unpublish": "UNPUBLISH"}
	for c in plan.changes:
		if c.action == "update":
			detail = "; ".join(_describe_field(f, c.before.get(f), v) for f, v in c.values.items())
		elif c.action == "create":
			detail = _describe_new(c)
		else:
			detail = "no longer on the laptop"
		lines.append(f"{labels[c.action]:9} {c.doctype} {c.key}: {detail}")
	for attribute, value in plan.attribute_values:
		lines.append(f"{'NEW':9} {attribute} option {value}")
	for doctype, name, url in plan.attachments:
		lines.append(f"{'NEW':9} photo on {doctype} {name}: {url}")
	for collection, key, fields in plan.conflicts:
		lines.append(f"{'SKIPPED':9} {collection} {key}: changed on live since the last sync ({', '.join(fields)}). Run pull, or edit live back.")
	for collection, key in plan.live_only:
		lines.append(f"{'LIVE ONLY':9} {collection} {key}: exists only on live, left alone")
	if plan.held:
		lines.append(f"{'HELD BACK':9} {plan.held} photo(s) only on the laptop since before the baseline. --include-held sends them.")
	return lines


def _describe_new(change):
	v = change.values
	if change.doctype == "Item Price":
		return f"GHS {v.get('price_list_rate')}"
	if change.doctype == "Website Slideshow":
		return f"{len(v.get('slides', []))} photos"
	return str(v.get("item_name") or v.get("web_item_name") or v.get("item_group_name") or "")


def _describe_field(name, old, new):
	if name == "slides":
		return f"gallery {len(old or [])} → {len(new or [])} photos"
	if name == "attributes":
		return "variant options changed"
	old_text, new_text = str(old or "(empty)"), str(new or "(empty)")
	if len(old_text) > 40 or len(new_text) > 40:
		return f"{name} changed"
	return f"{name} {old_text} → {new_text}"
