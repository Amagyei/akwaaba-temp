"""Turn a reviewed Studio session into catalog records.

Where photos land (matches what www/product renders):
	AI panels           → Website Slideshow on the Website Item (PDP swipe gallery)
	main AI panel       → Item.image / Website Item.website_image (cards + PDP hero)
	original + extras   → public attachments on the Item ("More Views" strip)

Files stay private on the session while under review; only the chosen ones
are copied out as public files when they're committed to an item.
"""

import re
import unicodedata

import frappe
from frappe.utils import flt

DEFAULT_PRICE_LIST = "Akwaaba Retail"

# Twi/Ewe letters NFKD can't fold to ASCII (ɛ, ɔ, ŋ …) — keep them readable in URLs.
_FOLD = str.maketrans({"ɛ": "e", "Ɛ": "E", "ɔ": "o", "Ɔ": "O", "ŋ": "n", "Ŋ": "N", "ƒ": "f", "ʋ": "v", "ɖ": "d"})


def slugify(text):
	text = unicodedata.normalize("NFKD", (text or "").translate(_FOLD))
	text = text.encode("ascii", "ignore").decode("ascii").lower()
	return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def suggest_item_code(item_group, item_name=""):
	"""Follow the group's existing code pattern.

	EMBB-05…EMBB-08 → EMBB-09; KKL-SKT-TRG (lettered) → KKL-SKT-<3 letters of
	the colourway>; empty group → initials of the group name + -01.
	"""
	codes = frappe.get_all("Item", filters={"item_group": item_group}, pluck="item_code") if item_group else []
	prefixes = {}
	for code in codes:
		if "-" in code:
			prefix, _, suffix = code.rpartition("-")
			prefixes.setdefault(prefix + "-", []).append(suffix)

	if prefixes:
		prefix, suffixes = max(prefixes.items(), key=lambda kv: len(kv[1]))
		numeric = [s for s in suffixes if s.isdigit()]
		if numeric and len(numeric) == len(suffixes):
			width = max(len(s) for s in numeric)
			nxt = max(int(s) for s in numeric) + 1
			return _unique(lambda n: f"{prefix}{str(nxt + n).zfill(width)}")
		# Lettered suffixes: take the colourway part of the name ("… — Torgbui").
		tail = re.split(r"\s+[—–-]\s+", item_name or "")[-1]
		letters = re.sub(r"[^A-Z]", "", slugify(tail).upper()) or "NEW"
		return _unique(lambda n: f"{prefix}{letters[:3]}" + (str(n + 1) if n else ""))

	initials = "".join(w[0] for w in slugify(item_group or "Item").split("-") if w)[:4].upper() or "ITM"
	return _unique(lambda n: f"{initials}-{str(n + 1).zfill(2)}")


def _unique(make):
	for n in range(500):
		code = make(n)
		if not frappe.db.exists("Item", code):
			return code
	return make(0) + "-" + frappe.generate_hash(length=4).upper()


def unique_route(base):
	base = base or "item"
	route, n = base, 2
	while frappe.db.exists("Website Item", {"route": route}):
		route, n = f"{base}-{n}", n + 1
	return route


# ---------------------------------------------------------------------------
# Create / extend
# ---------------------------------------------------------------------------

def create_item(*, item_code, item_name, item_group, price=None, description="",
		publish=False, gallery=(), supporting=(), main=None):
	"""New Item + Website Item (+ slideshow, price). Returns the Website Item doc.

	gallery / supporting are File doc names; main is a File name from gallery.
	"""
	item_code = (item_code or "").strip()
	if not item_code:
		frappe.throw("Item code is required.")
	if frappe.db.exists("Item", item_code):
		frappe.throw(f"Item {item_code} already exists — pick another code or add photos to it instead.")
	if publish and not flt(price):
		frappe.throw("Set a price before publishing.")

	item = frappe.get_doc({
		"doctype": "Item",
		"item_code": item_code,
		"item_name": item_name,
		"item_group": item_group,
		"stock_uom": "Nos",
		"is_stock_item": 0,
		"include_item_in_manufacturing": 0,
		"description": description or item_name,
	}).insert(ignore_permissions=True)

	if flt(price):
		_set_price(item_code, price)

	from webshop.webshop.doctype.website_item.website_item import make_website_item

	wi = make_website_item(item, save=False)
	wi.route = unique_route(slugify(item_name))
	wi.published = 1 if publish else 0
	wi.short_description = description or ""
	wi.insert(ignore_permissions=True)

	_add_photos(item, wi, gallery=gallery, supporting=supporting, main=main, set_main=True)
	return wi


def add_to_item(*, item_code, gallery=(), supporting=(), main=None, set_main=False):
	"""Append photos to an existing item's gallery / More Views."""
	item = frappe.get_doc("Item", item_code)
	wi_name = frappe.db.get_value("Website Item", {"item_code": item_code})
	if wi_name:
		wi = frappe.get_doc("Website Item", wi_name)
	else:
		from webshop.webshop.doctype.website_item.website_item import make_website_item

		wi = make_website_item(item, save=False)
		wi.route = unique_route(slugify(item.item_name))
		wi.published = 0
		wi.insert(ignore_permissions=True)
		set_main = set_main or not item.image

	_add_photos(item, wi, gallery=gallery, supporting=supporting, main=main, set_main=set_main)
	return wi


def _add_photos(item, wi, *, gallery, supporting, main, set_main):
	gallery = list(gallery)
	if main and main in gallery:
		gallery.remove(main)
		gallery.insert(0, main)

	base = slugify(item.item_name) or slugify(item.item_code)
	urls = []
	if gallery:
		show = _slideshow_for(wi)
		start = len(show.slideshow_items)
		urls = [
			_publish_file(name, "Website Slideshow", show.name, f"{base}-{start + i}.jpg")
			for i, name in enumerate(gallery, start=1)
		]
		new_rows = [{"image": u} for u in urls]
		old_rows = [
			{"image": r.image, "heading": r.heading, "description": r.description, "url": r.url}
			for r in show.slideshow_items
			if r.image not in urls
		]
		# A new main photo leads the swipe gallery; otherwise new photos go last.
		show.set("slideshow_items", new_rows + old_rows if set_main else old_rows + new_rows)
		show.save(ignore_permissions=True)
		if wi.slideshow != show.name:
			wi.db_set("slideshow", show.name)

	for i, name in enumerate(supporting, start=1):
		_publish_file(name, "Item", item.name, f"{base}-view-{i}.jpg")

	if set_main and urls:
		# Full saves so webshop validates the image and rebuilds the card
		# thumbnail, and the Item → Website Item sync hook runs.
		item.reload()
		item.image = urls[0]
		item.flags.ignore_permissions = True
		item.save()
		wi.reload()
		wi.website_image = urls[0]
		wi.flags.ignore_permissions = True
		wi.save()


def _slideshow_for(wi):
	if wi.slideshow and frappe.db.exists("Website Slideshow", wi.slideshow):
		return frappe.get_doc("Website Slideshow", wi.slideshow)
	name = f"{wi.item_code} Gallery"
	if frappe.db.exists("Website Slideshow", name):
		return frappe.get_doc("Website Slideshow", name)
	return frappe.get_doc({
		"doctype": "Website Slideshow",
		"slideshow_name": name,
	}).insert(ignore_permissions=True)


def _publish_file(file_name, doctype, docname, public_name):
	"""Public copy of a private session File, attached to `doctype/docname`.

	A copy rather than flipping is_private: Frappe de-duplicates identical
	uploads onto one file on disk, and moving it would break the other
	session that shares it. The session keeps its private originals.
	"""
	src = frappe.get_doc("File", file_name)
	return frappe.get_doc({
		"doctype": "File",
		"file_name": public_name,
		"attached_to_doctype": doctype,
		"attached_to_name": docname,
		"is_private": 0,
		"content": src.get_content(),
		"decode": False,
	}).insert(ignore_permissions=True).file_url


def _set_price(item_code, price):
	price_list = (
		frappe.db.get_single_value("Akwaaba Studio Settings", "price_list") or DEFAULT_PRICE_LIST
	)
	name = frappe.db.get_value("Item Price", {"item_code": item_code, "price_list": price_list})
	if name:
		frappe.db.set_value("Item Price", name, "price_list_rate", flt(price))
		return
	frappe.get_doc({
		"doctype": "Item Price",
		"item_code": item_code,
		"price_list": price_list,
		"price_list_rate": flt(price),
		"selling": 1,
	}).insert(ignore_permissions=True)
