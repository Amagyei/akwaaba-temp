"""The laptop side of the sync: pull a baseline, plan a push, apply it."""

import base64
import json
import os
import subprocess
import time

import frappe
import requests

from akwaaba_theme.catalog_sync import api, export as export_module
from akwaaba_theme.catalog_sync.plan import COLLECTIONS, describe, diff_record, laptop_only_attachments, make_plan, next_state

CREDENTIALS = os.path.expanduser("~/.config/akwaaba/catalog_sync.json")
SSH_HOST = "akwaaba@23.95.130.188"
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "StrictHostKeyChecking=yes", "-o", "IdentitiesOnly=yes", "-i", os.path.expanduser("~/.ssh/id_ed25519")]
LIVE_SITE = "akwaaba.prod"
LIVE_URL = "https://serwakcollections.com"


def _sync_dir():
	path = frappe.get_site_path("private", "catalog_sync")
	os.makedirs(path, exist_ok=True)
	return path


def load_state():
	try:
		with open(os.path.join(_sync_dir(), "state.json")) as f:
			return json.load(f)
	except FileNotFoundError:
		return {}


def write_state(state):
	with open(os.path.join(_sync_dir(), "state.json"), "w") as f:
		json.dump(state, f, indent=1, sort_keys=True)


def audit(event, **details):
	with open(os.path.join(_sync_dir(), "audit.log"), "a") as f:
		f.write(json.dumps({"at": frappe.utils.now(), "user": os.environ.get("USER"), "event": event, **details}) + "\n")


class Live:
	"""The receiving site, over HTTPS with the sync user's API key."""

	def __init__(self, url=None, api_key=None, api_secret=None):
		if not api_key:
			with open(CREDENTIALS) as f:
				saved = json.load(f)
			url, api_key, api_secret = saved.get("url") or LIVE_URL, saved["api_key"], saved["api_secret"]
		self.url = url.rstrip("/")
		self.session = requests.Session()
		self.session.headers["Authorization"] = f"token {api_key}:{api_secret}"

	def call(self, method, **params):
		r = self.session.post(f"{self.url}/api/method/akwaaba_theme.catalog_sync.api.{method}", data=params, timeout=180)
		if not r.ok:
			try:
				messages = json.loads(r.json().get("_server_messages") or "[]")
				detail = "; ".join(json.loads(m).get("message", "") for m in messages)
			except ValueError:
				detail = r.text[:300]
			raise RuntimeError(f"{method} answered {r.status_code}: {detail}")
		return r.json().get("message")

	def export(self):
		return self.call("export")

	def upsert(self, doctype, key, values):
		return self.call("upsert", doctype=doctype, key=key, values=json.dumps(values))

	def unpublish(self, item_code):
		return self.call("unpublish", item_code=item_code)

	def add_attribute_value(self, attribute, value):
		return self.call("add_attribute_value", attribute=attribute, value=value)

	def upload(self, file_name, data, attached_to_doctype=None, attached_to_name=None):
		return self.call(
			"upload",
			file_name=file_name,
			content=base64.b64encode(data).decode(),
			attached_to_doctype=attached_to_doctype or "",
			attached_to_name=attached_to_name or "",
		)


def export_over_ssh():
	"""Read live's catalog before this app is deployed there. Changes nothing."""
	with open(export_module.__file__) as f:
		source = f.read()
	script = f"""
import json, sys
import frappe
{source}
frappe.init(site={LIVE_SITE!r}, sites_path=".")
frappe.connect()
print("@@JSON@@" + json.dumps(export_catalog(), default=str))
frappe.destroy()
"""
	done = subprocess.run(
		["ssh", *SSH_OPTIONS, SSH_HOST, "cd ~/frappe-bench/sites && ../env/bin/python -I -"],
		input=script, capture_output=True, text=True, timeout=300,
	)
	if "@@JSON@@" not in done.stdout:
		raise RuntimeError(f"Couldn't read live's catalog: {done.stderr[-500:]}")
	return json.loads(done.stdout.split("@@JSON@@", 1)[1])


def read_file(url):
	path = frappe.get_site_path("public", "files", os.path.basename(url.split("?")[0]))
	with open(path, "rb") as f:
		return f.read()


# ---------------------------------------------------------------------------
# pull: live → laptop
# ---------------------------------------------------------------------------

def pull(live_data, base_url):
	"""Make the laptop's catalog match live, photos included, and remember it."""
	local = export_module.export_catalog()
	by_hash = {h: u for u, h in local["files"].items()}
	live_files = live_data["files"]

	def local_url(url):
		digest = live_files.get(url)
		if not digest:
			return url
		if digest not in by_hash:
			r = requests.get(base_url.rstrip("/") + url, timeout=120)
			r.raise_for_status()
			by_hash[digest] = api.save_file(os.path.basename(url), r.content)
		return by_hash[digest]

	counts = {"photos_before": len(local["files"])}
	for attribute, values in live_data["attribute_values"].items():
		if frappe.db.exists("Item Attribute", attribute):
			for value in values:
				if value not in local["attribute_values"].get(attribute, []):
					api.apply_attribute_value(attribute, value)

	# Parents and templates first, then children and variants.
	groups = sorted(live_data["item_groups"].items(), key=lambda kv: (_depth(kv[0], live_data["item_groups"]), kv[0]))
	items = sorted(live_data["items"].items(), key=lambda kv: bool(kv[1]["variant_of"]))
	for collection, doctype, rows in (
		("item_groups", "Item Group", groups),
		("items", "Item", items),
		("prices", "Item Price", live_data["prices"].items()),
		("slideshows", "Website Slideshow", live_data["slideshows"].items()),
		("website_items", "Website Item", live_data["website_items"].items()),
	):
		for key, rec in rows:
			values = _mapped(doctype, rec, local_url)
			mine = local[collection].get(key)
			if mine is not None:
				values, _before = diff_record(collection, doctype, values, mine, {}, {})
				if not values:
					continue
			api.apply_upsert(doctype, key, values)
			counts[collection] = counts.get(collection, 0) + 1
	frappe.db.commit()
	# Photos only the laptop has stay on the laptop until someone chooses to send them.
	held = laptop_only_attachments(export_module.export_catalog(), live_data)
	state = {c: {k: rec["_modified"] for k, rec in live_data[c].items()} for c, _d in COLLECTIONS}
	write_state(dict(state, held_attachments=held))
	counts["held_photos"] = sum(len(v) for v in held.values())
	audit("pull", counts=counts)
	return counts


def _depth(name, groups):
	depth = 0
	parent = groups.get(name, {}).get("parent_item_group")
	while parent and parent in groups and depth < 20:
		depth += 1
		parent = groups[parent].get("parent_item_group")
	return depth


def _mapped(doctype, rec, map_url):
	values = {k: v for k, v in rec.items() if k != "_modified"}
	for field in export_module.FILE_FIELDS.get(doctype, ()):
		if values.get(field):
			values[field] = map_url(values[field])
	if "slides" in values:
		values["slides"] = [dict(s, image=map_url(s["image"])) for s in values["slides"]]
	return values


# ---------------------------------------------------------------------------
# push: laptop → live
# ---------------------------------------------------------------------------

def plan_push(live_data, include_held=False):
	return make_plan(export_module.export_catalog(), live_data, load_state(), include_held)


def apply_plan(plan, live, local, live_data):
	"""Do the plan, record each step, then re-read live and prove it landed."""
	urls = {h: u for u, h in live_data["files"].items()}
	local_files = local["files"]

	def live_url(url):
		digest = local_files.get(url)
		if not digest:
			return url
		if digest not in urls:
			urls[digest] = live.upload(os.path.basename(url), read_file(url))
			audit("upload", url=url, live_url=urls[digest])
		return urls[digest]

	for attribute, value in plan.attribute_values:
		live.add_attribute_value(attribute, value)
		audit("attribute_value", attribute=attribute, value=value)

	for change in plan.changes:
		if change.action == "unpublish":
			live.unpublish(change.key)
		else:
			live.upsert(change.doctype, change.key, _mapped(change.doctype, change.values, live_url))
		audit(change.action, doctype=change.doctype, key=change.key, fields=sorted(change.values), before=change.before)

	for doctype, name, url in plan.attachments:
		live.upload(os.path.basename(url), read_file(url), doctype, name)
		audit("attachment", doctype=doctype, name=name, url=url)

	after = live.export()
	state = next_state(load_state(), after, local, plan.conflicts)
	write_state(state)
	return make_plan(local, after, state)


def main(argv=None):
	import argparse

	parser = argparse.ArgumentParser(prog="catalog_sync", description="Laptop to live catalog sync.")
	parser.add_argument("command", choices=["pull", "push"])
	parser.add_argument("--site", default="akwaaba.site", help="the laptop site")
	parser.add_argument("--apply", action="store_true", help="push: do it; without this, only report")
	parser.add_argument("--via-ssh", action="store_true", help="read live over SSH, for before this app is deployed there")
	parser.add_argument("--url", help="the receiving site, instead of the saved one")
	parser.add_argument("--include-held", action="store_true", help="push: also send photos held back at the baseline")
	args = parser.parse_args(argv)

	frappe.init(site=args.site, sites_path=".")
	frappe.connect()
	frappe.flags.mute_emails = True
	frappe.set_user("Administrator")
	try:
		if args.via_ssh:
			if args.apply:
				parser.error("--apply goes over HTTPS with the sync user, not SSH")
			live, live_data = None, export_over_ssh()
		else:
			live = Live(args.url) if not args.url else _live_for(args.url)
			live_data = live.export()

		if args.command == "pull":
			counts = pull(live_data, args.url or LIVE_URL)
			print("Pulled from live:", json.dumps(counts))
			return 0

		plan = plan_push(live_data, args.include_held)
		for line in describe(plan):
			print(line)
		if plan.is_empty and not plan.conflicts:
			print("No changes.")
		if not args.apply:
			if not plan.is_empty:
				print(f"\n{len(plan.changes)} record change(s). Nothing was done: add --apply to push.")
			return 0

		started = time.time()
		remaining = apply_plan(plan, live, export_module.export_catalog(), live_data)
		print(f"Applied in {time.time() - started:.0f}s.")
		if not remaining.is_empty:
			print("NOT everything landed. Still different on live:")
			for line in describe(remaining):
				print(" ", line)
			audit("verify_failed", remaining=describe(remaining))
			return 1
		audit("verified")
		print("Verified: live matches the laptop.")
		return 0
	finally:
		frappe.destroy()


def _live_for(url):
	with open(CREDENTIALS) as f:
		saved = json.load(f)
	return Live(url, saved["api_key"], saved["api_secret"])
