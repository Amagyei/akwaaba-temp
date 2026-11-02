"""What the sync tool may do on the receiving site.

The Catalog Sync role has no rights on any DocType, so Frappe itself refuses
that user everywhere else: orders, customers, payments, scripts, settings. These
few endpoints are all it can call, and they write only the listed catalog fields.
Stock quantities aren't among them, and nothing here deletes.
"""

import base64
import hashlib
import io

import frappe
from frappe.utils import cint, flt
from PIL import Image

from akwaaba_theme.catalog_sync.export import FIELDS, PRICE_LIST, export_catalog

ROLE = "Catalog Sync"
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
VIDEO_EXTENSIONS = (".mp4", ".webm")
ATTACHABLE = ("Item", "Website Item", "Website Slideshow", "Item Group")
_UPSERTS = {}


def require_sync_user():
	if frappe.session.user == "Guest" or ROLE not in frappe.get_roles():
		frappe.throw("This is for the catalog sync user only.", frappe.PermissionError)


@frappe.whitelist(methods=["GET", "POST"])
def export():
	require_sync_user()
	return export_catalog()


@frappe.whitelist(methods=["POST"])
def upsert(doctype, key, values):
	require_sync_user()
	return apply_upsert(doctype, key, frappe.parse_json(values))


@frappe.whitelist(methods=["POST"])
def unpublish(item_code):
	require_sync_user()
	wi = frappe.db.get_value("Website Item", {"item_code": item_code})
	if wi:
		doc = frappe.get_doc("Website Item", wi)
		doc.published = 0
		doc.save(ignore_permissions=True)
	return "ok"


@frappe.whitelist(methods=["POST"])
def add_attribute_value(attribute, value):
	require_sync_user()
	return apply_attribute_value(attribute, value)


@frappe.whitelist(methods=["POST"])
def upload(file_name, content, attached_to_doctype=None, attached_to_name=None):
	"""Base64 `content`. Returns the file's URL on this site."""
	require_sync_user()
	return save_file(file_name, base64.b64decode(content), attached_to_doctype, attached_to_name)


def save_file(file_name, data, attached_to_doctype=None, attached_to_name=None):
	if len(data) > MAX_UPLOAD_BYTES:
		frappe.throw(f"{file_name} is too large to sync.")
	if attached_to_doctype and attached_to_doctype not in ATTACHABLE:
		frappe.throw(f"Files can't be attached to {attached_to_doctype}.")
	if attached_to_doctype == "Website Item":
		# The tool knows Website Items by item code, since names differ between sites.
		attached_to_name = frappe.db.get_value("Website Item", {"item_code": attached_to_name}) or attached_to_name
	if not file_name.lower().endswith(VIDEO_EXTENSIONS):
		try:
			Image.open(io.BytesIO(data)).verify()
		except Exception:
			frappe.throw(f"{file_name} isn't an image.")

	# Reuse a public copy already attached the same way, so a retry doesn't pile up.
	digest = hashlib.md5(data).hexdigest()
	existing = frappe.db.get_value(
		"File",
		{
			"content_hash": digest,
			"is_private": 0,
			"attached_to_doctype": attached_to_doctype or ["is", "not set"],
			"attached_to_name": attached_to_name or ["is", "not set"],
		},
		"file_url",
	)
	if existing:
		return existing
	return frappe.get_doc({
		"doctype": "File",
		"file_name": file_name,
		"attached_to_doctype": attached_to_doctype,
		"attached_to_name": attached_to_name,
		"is_private": 0,
		"content": data,
		"decode": False,
	}).insert(ignore_permissions=True).file_url


def apply_attribute_value(attribute, value):
	doc = frappe.get_doc("Item Attribute", attribute)
	if not any(v.attribute_value == value for v in doc.item_attribute_values):
		taken = {v.abbr for v in doc.item_attribute_values}
		abbr, n = value[:10].upper(), 2
		while abbr in taken:
			abbr, n = f"{value[:8].upper()}{n}", n + 1
		doc.append("item_attribute_values", {"attribute_value": value, "abbr": abbr})
		doc.save(ignore_permissions=True)
	return "ok"


def apply_upsert(doctype, key, values):
	"""Create or update one catalog record. Returns its `modified`."""
	if doctype not in FIELDS:
		frappe.throw(f"{doctype} isn't a catalog record.", frappe.PermissionError)
	allowed = set(FIELDS[doctype]) | {"attributes", "slides"}
	unknown = set(values) - allowed
	if unknown:
		frappe.throw(f"Not catalog fields on {doctype}: {', '.join(sorted(unknown))}", frappe.PermissionError)
	doc = _UPSERTS[doctype](key, values)
	return {"name": doc.name, "modified": str(doc.modified)}


def _upsert_item_group(key, values):
	if frappe.db.exists("Item Group", key):
		doc = frappe.get_doc("Item Group", key)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return doc
	return frappe.get_doc(dict(values, doctype="Item Group")).insert(ignore_permissions=True)


def _upsert_item(key, values):
	values = dict(values)
	attributes = values.pop("attributes", None)
	if frappe.db.exists("Item", key):
		doc = frappe.get_doc("Item", key)
		doc.update(values)
		if attributes is not None:
			doc.set("attributes", attributes)
		doc.save(ignore_permissions=True)
		return doc
	doc = frappe.get_doc(dict(values, doctype="Item", item_code=key, attributes=attributes or []))
	return doc.insert(ignore_permissions=True)


def _upsert_price(key, values):
	name = frappe.db.get_value("Item Price", {"item_code": key, "price_list": PRICE_LIST, "selling": 1})
	if name:
		doc = frappe.get_doc("Item Price", name)
		doc.price_list_rate = flt(values.get("price_list_rate", doc.price_list_rate))
		doc.save(ignore_permissions=True)
		return doc
	return frappe.get_doc({
		"doctype": "Item Price",
		"item_code": key,
		"price_list": PRICE_LIST,
		"selling": 1,
		"price_list_rate": flt(values.get("price_list_rate")),
		"currency": values.get("currency") or "GHS",
	}).insert(ignore_permissions=True)


def _upsert_website_item(key, values):
	name = frappe.db.get_value("Website Item", {"item_code": key})
	if name:
		doc = frappe.get_doc("Website Item", name)
	else:
		from webshop.webshop.doctype.website_item.website_item import make_website_item

		doc = make_website_item(frappe.get_doc("Item", key), save=False)
	doc.update({k: v for k, v in values.items() if k != "item_code"})
	doc.save(ignore_permissions=True)
	return doc


def _upsert_slideshow(key, values):
	if frappe.db.exists("Website Slideshow", key):
		doc = frappe.get_doc("Website Slideshow", key)
	else:
		doc = frappe.get_doc({"doctype": "Website Slideshow", "slideshow_name": values.get("slideshow_name") or key})
	if "slides" in values:
		doc.set("slideshow_items", values["slides"])
	doc.save(ignore_permissions=True)
	return doc


_UPSERTS.update({
	"Item Group": _upsert_item_group,
	"Item": _upsert_item,
	"Item Price": _upsert_price,
	"Website Item": _upsert_website_item,
	"Website Slideshow": _upsert_slideshow,
})


def create_sync_user(email="catalog.sync@serwakcollections.com"):
	"""Make the sync user and its API key. Prints the key once; store it on the laptop.

	bench --site <site> execute akwaaba_theme.catalog_sync.api.create_sync_user
	"""
	if not frappe.db.exists("User", email):
		frappe.get_doc({
			"doctype": "User",
			"email": email,
			"first_name": "Catalog Sync",
			"send_welcome_email": 0,
			"roles": [{"role": ROLE}],
		}).insert(ignore_permissions=True)
	from frappe.core.doctype.user.user import generate_keys

	keys = generate_keys(email)
	frappe.db.commit()
	print(frappe.as_json({"api_key": keys["api_key"], "api_secret": keys["api_secret"]}))
