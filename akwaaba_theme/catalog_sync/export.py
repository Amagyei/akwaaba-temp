"""A plain snapshot of the catalog. Reads only, needs nothing but frappe, and gets
sent to the server over SSH as source for a baseline taken before this app is
deployed there, so keep its imports to frappe and the standard library.
"""

import hashlib
import os

import frappe

PRICE_LIST = "Akwaaba Retail"

FIELDS = {
	"Item Group": [
		"item_group_name", "parent_item_group", "is_group", "route", "description", "image",
		"show_in_website", "homepage_position", "homepage_eyebrow", "homepage_caption", "show_in_nav",
	],
	"Item": [
		"item_name", "item_group", "description", "stock_uom", "is_stock_item", "has_variants",
		"variant_of", "variant_based_on", "disabled", "image",
	],
	"Item Price": ["item_code", "price_list", "price_list_rate", "selling", "currency"],
	"Website Item": [
		"item_code", "web_item_name", "item_group", "route", "published", "short_description",
		"description", "web_long_description", "website_image", "slideshow", "on_backorder",
		"has_variants", "variant_of", "hover_video_file", "hover_video_url", "hover_video_poster_url",
		"feature_on_hero", "hero_position", "colorway_code",
	],
	"Website Slideshow": ["slideshow_name"],
}

# Fields holding a site file's URL. They compare by content, because the same
# photo can have a different file name on each site.
FILE_FIELDS = {
	"Item Group": ["image"],
	"Item": ["image"],
	"Website Item": ["website_image", "hover_video_file", "hover_video_url", "hover_video_poster_url"],
}

SLIDE_FIELDS = ["image", "heading", "description", "url"]


def _clean(row, fields):
	out = {}
	for field in fields:
		value = row.get(field)
		out[field] = "" if value is None else value
	return out


def _present(doctype, fields):
	meta = frappe.get_meta(doctype)
	return [f for f in fields if meta.has_field(f)]


def export_catalog():
	data = {
		"item_groups": {},
		"items": {},
		"prices": {},
		"website_items": {},
		"slideshows": {},
		"attachments": {},
		"attribute_values": {},
	}

	for row in frappe.get_all("Item Group", fields=["name", "modified", *_present("Item Group", FIELDS["Item Group"])]):
		data["item_groups"][row.name] = dict(_clean(row, FIELDS["Item Group"]), _modified=str(row.modified))

	for row in frappe.get_all("Item", fields=["name", "modified", *_present("Item", FIELDS["Item"])]):
		rec = _clean(row, FIELDS["Item"])
		rec["attributes"] = [
			_clean(a, ["attribute", "attribute_value"])
			for a in frappe.get_all(
				"Item Variant Attribute",
				filters={"parent": row.name, "parenttype": "Item"},
				fields=["attribute", "attribute_value"],
				order_by="idx asc",
			)
		]
		rec["_modified"] = str(row.modified)
		data["items"][row.name] = rec

	for row in frappe.get_all(
		"Item Price",
		filters={"price_list": PRICE_LIST, "selling": 1},
		fields=["name", "modified", *FIELDS["Item Price"]],
	):
		data["prices"][row.item_code] = dict(_clean(row, FIELDS["Item Price"]), _modified=str(row.modified))

	for row in frappe.get_all("Website Item", fields=["name", "modified", *_present("Website Item", FIELDS["Website Item"])]):
		data["website_items"][row.item_code] = dict(_clean(row, FIELDS["Website Item"]), _modified=str(row.modified))

	for row in frappe.get_all("Website Slideshow", fields=["name", "modified", "slideshow_name"]):
		slides = frappe.get_all(
			"Website Slideshow Item",
			filters={"parent": row.name},
			fields=SLIDE_FIELDS,
			order_by="idx asc",
		)
		data["slideshows"][row.name] = {
			"slideshow_name": row.slideshow_name,
			"slides": [_clean(s, SLIDE_FIELDS) for s in slides],
			"_modified": str(row.modified),
		}

	# Website Item names differ between sites, so owners go by item code.
	codes = {r.name: r.item_code for r in frappe.get_all("Website Item", fields=["name", "item_code"])}
	for row in frappe.get_all(
		"File",
		filters={"attached_to_doctype": ["in", ["Item", "Website Item"]], "is_private": 0, "is_folder": 0},
		fields=["attached_to_doctype", "attached_to_name", "file_url"],
	):
		if "-card" in (row.file_url or ""):
			continue
		name = codes.get(row.attached_to_name, row.attached_to_name)
		found = data["attachments"].setdefault(f"{row.attached_to_doctype}/{name}", [])
		if row.file_url not in found:
			found.append(row.file_url)

	for row in frappe.get_all("Item Attribute Value", fields=["parent", "attribute_value"]):
		data["attribute_values"].setdefault(row.parent, []).append(row.attribute_value)

	data["files"] = _file_hashes(data)
	return data


def _file_hashes(data):
	"""URL → md5 of the bytes on disk, for every site file the catalog mentions."""
	urls = set()
	for collection, doctype in (("item_groups", "Item Group"), ("items", "Item"), ("website_items", "Website Item")):
		for rec in data[collection].values():
			urls.update(rec[f] for f in FILE_FIELDS[doctype] if rec.get(f))
	for rec in data["slideshows"].values():
		urls.update(s["image"] for s in rec["slides"] if s["image"])
	for found in data["attachments"].values():
		urls.update(found)

	out = {}
	for url in urls:
		if not url.startswith("/files/"):
			continue
		path = frappe.get_site_path("public", "files", os.path.basename(url.split("?")[0]))
		if os.path.exists(path):
			with open(path, "rb") as f:
				out[url] = hashlib.md5(f.read()).hexdigest()
	return out
