"""Load the cut cloths into a site's catalog as unpublished products.

usage, from the bench's sites folder:
	../env/bin/python -I <this file> <site> <photos dir> <cut.py out dir> [--publish PRICE]

Each cloth becomes a tracked Item in the Kente group (one of a kind, so its quantity
is set on live) with its strip on the Website Item. Names are placeholders, so they stay
unpublished until they're named and priced. Safe to run again: existing cloths are skipped.
--publish is for trying the page on a test site.
"""

import json
import os
import sys

import frappe

from akwaaba_theme.catalog_sync.api import save_file
from akwaaba_theme.studio import catalog

GROUP = "Kente"
SKIP = {"S"}  # the "Photo sample" weave isn't one of the shop's cloths


def read(path):
	with open(path, "rb") as f:
		return f.read()


def main(site, photos, cut, publish_price=None):
	frappe.init(site=site, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True

	if not frappe.db.exists("Item Group", GROUP):
		frappe.get_doc({
			"doctype": "Item Group",
			"item_group_name": GROUP,
			"parent_item_group": frappe.db.get_value("Item Group", {"is_group": 1, "parent_item_group": ""}) or "All Item Groups",
			"is_group": 0,
			"show_in_website": 1,
		}).insert(ignore_permissions=True)

	with open(os.path.join(cut, "cloths.json")) as f:
		cloths = json.load(f)
	made = 0
	for cloth in cloths:
		if cloth["key"] in SKIP:
			continue
		code = f"KNT-{cloth['key']}"
		if frappe.db.exists("Item", code):
			continue
		photo = save_file(f"kente-{code.lower()}.jpg", read(os.path.join(photos, cloth["photo"])))
		photo_file = frappe.db.get_value("File", {"file_url": photo})
		wi = catalog.create_item(
			item_code=code,
			item_name=f"Kente {cloth['name']}",
			item_group=GROUP,
			price=publish_price,
			publish=bool(publish_price),
			gallery=[photo_file],
			main=photo_file,
			is_stock_item=1,
		)
		wi.reload()
		wi.db_set({
			"loom_strip": save_file(f"strip-{code.lower()}.jpg", read(os.path.join(cut, f"strip-{cloth['key']}.jpg")), "Website Item", wi.name),
			"loom_thumb": save_file(f"thumb-{code.lower()}.jpg", read(os.path.join(cut, f"thumb-{cloth['key']}.jpg")), "Website Item", wi.name),
			"loom_warp_colors": cloth["warp"],
			"loom_bobbin_color": cloth["weft"],
		})
		made += 1
	frappe.db.commit()
	print(f"{made} cloths added to {site}.")
	frappe.destroy()


if __name__ == "__main__":
	price = float(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[4] == "--publish" else None
	main(sys.argv[1], sys.argv[2], sys.argv[3], price)
