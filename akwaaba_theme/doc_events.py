"""Doc events that keep Item and Website Item in sync.

Sync rule: if the admin uploads an image on the Item form, mirror that
image URL to the linked Website Item's `website_image` field — unless
Website Item already has its own image, in which case we respect it as
an explicit override.
"""

import frappe


def sync_item_image_to_website_item(doc, method=None):
	"""Copy Item.image → Website Item.website_image when needed."""
	if not doc or not doc.get("image"):
		return
	wi_name = frappe.db.get_value("Website Item", {"item_code": doc.item_code}, "name")
	if not wi_name:
		return
	current = frappe.db.get_value("Website Item", wi_name, "website_image")
	if current:
		return  # Website Item has its own image — don't overwrite
	frappe.db.set_value("Website Item", wi_name, "website_image", doc.image)


def mirror_hover_video_file_to_url(doc, method=None):
	"""Copy Website Item.hover_video_file → hover_video_url when it makes sense.

	Rules:
	  - If hover_video_url is empty, mirror the file's URL over.
	  - If hover_video_url currently points at an old /files/ path (i.e. the
	    admin previously uploaded a file, and is now replacing it), mirror
	    the new file's URL over.
	  - If hover_video_url is a paste-in external URL (Cloudinary, Mux, S3,
	    etc.), leave it alone — the paste wins.
	"""
	if not doc:
		return
	uploaded = doc.get("hover_video_file") or ""
	current_url = doc.get("hover_video_url") or ""

	if not uploaded:
		return

	# If nothing pasted, or the current url is itself a local /files/ path
	# (meaning we previously mirrored it), replace it with the new upload.
	if not current_url or current_url.startswith("/files/") or current_url.startswith("/private/files/"):
		if current_url != uploaded:
			doc.hover_video_url = uploaded


def backfill_website_item_images():
	"""One-time: copy Item.image to Website Item.website_image where the
	Website Item still has no image. Safe to run any time.

	Usage: bench --site <site> execute
	    akwaaba_theme.doc_events.backfill_website_item_images
	"""
	rows = frappe.db.sql(
		"""
		SELECT wi.name AS wi, i.image AS image
		FROM `tabWebsite Item` wi
		JOIN `tabItem` i ON i.item_code = wi.item_code
		WHERE i.image IS NOT NULL AND i.image != ''
		  AND (wi.website_image IS NULL OR wi.website_image = '')
		""",
		as_dict=True,
	)
	for r in rows:
		frappe.db.set_value("Website Item", r.wi, "website_image", r.image)
	frappe.db.commit()
	print(f"Backfilled {len(rows)} Website Item images.")
	return len(rows)
