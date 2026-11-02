"""/studio — phone-first item upload Studio for catalog staff.

Snap a garment on a mannequin or hanger, pick saved prompts, let Gemini turn
it into model/product shots (grids are sliced into separate photos), choose
the main photo, and save it as a new item or onto an existing one. Backend:
akwaaba_theme.studio.api. Bootstrap data is inlined so the page is usable on a
slow connection after a single request.
"""

import frappe

from akwaaba_theme.studio.api import STUDIO_ROLES, bootstrap


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/studio"
		raise frappe.Redirect
	if not STUDIO_ROLES & set(frappe.get_roles()):
		raise frappe.PermissionError("Studio is for catalog staff only.")

	context.no_cache = 1
	context.show_sidebar = False
	context.title = "Studio — Serwa Kollections"
	context.body_class = "akw-studio-page"
	# Inlined in a <script> tag: escape "<" so no value can close the tag.
	context.boot_json = frappe.as_json(bootstrap(), indent=None).replace("<", "\\u003c")
	return context
