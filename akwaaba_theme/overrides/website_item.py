"""Override for webshop's Website Item.

Website Item is a WebsiteGenerator: on `published=1` it renders webshop's
stock item page (templates/generators/item/item.html) at its own route.
That page clashes with the Akwaaba theme, so we 301 it to our bespoke
`/product/<route>` page — leaving exactly one, fully-themed product page.

It also swaps webshop's 300 px thumbnail for a 600 px card image, which is what
product cards show, so a phone isn't sent full-size photos for a grid.

Everything else about Website Item is inherited unchanged. Registered via
`override_doctype_class` in hooks.py. Webshop overrides Item / Item Group /
Payment Request but NOT Website Item, so there is no override conflict.
"""

import io
import os

import frappe
from PIL import Image, ImageOps
from webshop.webshop.doctype.website_item.website_item import WebsiteItem

CARD_WIDTH = 600
CARD_QUALITY = 80


class AkwaabaWebsiteItem(WebsiteItem):
	def get_context(self, context):
		# Redirect the stock route → our themed product page. Prefer the
		# clean web route; fall back to the item code if a route is unset.
		slug = self.route or self.item_code or self.name
		frappe.local.flags.redirect_location = "/product/" + slug.strip("/")
		raise frappe.Redirect

	def make_thumbnail(self):
		if frappe.flags.in_import or frappe.flags.in_migrate:
			return
		if not self.is_new() and self.website_image != frappe.db.get_value(self.doctype, self.name, "website_image"):
			self.thumbnail = None
		if self.website_image and not self.thumbnail:
			self.thumbnail = make_card_image(self)


def make_card_image(wi):
	"""A light copy of the product's main photo; returns its URL.

	Returns None when the photo can't be read, and cards then fall back to the
	full-size image, because a missing thumbnail must never block saving a product.
	"""
	path = _local_path(wi.website_image)
	if not path:
		return None
	try:
		with Image.open(path) as img:
			img = ImageOps.exif_transpose(img).convert("RGB")
	except Exception:
		frappe.log_error(title=f"Akwaaba: couldn't read {wi.website_image}")
		return None

	if img.width > CARD_WIDTH:
		img = img.resize((CARD_WIDTH, round(img.height * CARD_WIDTH / img.width)), Image.Resampling.LANCZOS)
	buf = io.BytesIO()
	img.save(buf, "JPEG", quality=CARD_QUALITY, optimize=True, progressive=True)

	stem = os.path.splitext(os.path.basename(path))[0]
	return frappe.get_doc({
		"doctype": "File",
		"file_name": f"{stem}-card.jpg",
		"attached_to_doctype": "Website Item",
		"attached_to_name": wi.name,
		"is_private": 0,
		"content": buf.getvalue(),
		"decode": False,
	}).insert(ignore_permissions=True).file_url


def _local_path(url):
	"""Where a site photo URL lives on disk; None for anything else."""
	url = (url or "").split("?")[0]
	if not url.startswith("/files/"):
		return None
	path = frappe.get_site_path("public", "files", os.path.basename(url))
	return path if os.path.exists(path) else None
