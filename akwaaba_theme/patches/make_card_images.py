"""Give every existing product a card image, so the shop grid stops loading full-size photos."""

import frappe

from akwaaba_theme.overrides.website_item import make_card_image


def execute():
	rows = frappe.get_all(
		"Website Item",
		filters={"website_image": ["is", "set"]},
		fields=["name", "website_image", "thumbnail"],
	)
	for wi in rows:
		if "-card" in (wi.thumbnail or ""):
			continue
		url = make_card_image(wi)
		if url:
			frappe.db.set_value("Website Item", wi.name, "thumbnail", url, update_modified=False)
