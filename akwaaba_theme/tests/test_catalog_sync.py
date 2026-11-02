"""The catalog sync: what a push plans, and what the sync user can and can't do."""

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from akwaaba_theme.catalog_sync import api
from akwaaba_theme.catalog_sync.plan import describe, make_plan, next_state
from akwaaba_theme.tests.utils import ITEM_CODE, PRICE_LIST, setup_shop


def catalog():
	return {
		"item_groups": {},
		"items": {
			"A": {"item_name": "Piece A", "item_group": "G", "variant_of": "", "image": "/files/a.jpg", "attributes": [], "_modified": "t1"},
		},
		"prices": {"A": {"item_code": "A", "price_list_rate": 350.0, "_modified": "t1"}},
		"website_items": {
			"A": {"item_code": "A", "published": 1, "website_image": "/files/a.jpg", "short_description": "Soft", "_modified": "t1"},
		},
		"slideshows": {
			"A Gallery": {"slideshow_name": "A Gallery", "slides": [{"image": "/files/a.jpg", "heading": "", "description": "", "url": ""}], "_modified": "t1"},
		},
		"attachments": {},
		"attribute_values": {"Size": ["Mini"]},
		"files": {"/files/a.jpg": "hash-a"},
	}


def synced_state(live):
	return {c: {k: rec["_modified"] for k, rec in live[c].items()} for c in ("item_groups", "items", "prices", "website_items", "slideshows")}


class TestPlan(UnitTestCase):
	def setUp(self):
		self.local, self.live = catalog(), catalog()
		self.state = synced_state(self.live)

	def plan(self, **kwargs):
		return make_plan(self.local, self.live, self.state, **kwargs)

	def test_identical_catalogs_plan_nothing(self):
		self.assertTrue(self.plan().is_empty)

	def test_same_photo_under_another_name_is_not_a_change(self):
		self.live["items"]["A"]["image"] = "/files/a-on-live.jpg"
		self.live["website_items"]["A"]["website_image"] = "/files/a-on-live.jpg"
		self.live["slideshows"]["A Gallery"]["slides"][0]["image"] = "/files/a-on-live.jpg"
		self.live["files"] = {"/files/a-on-live.jpg": "hash-a"}
		self.assertTrue(self.plan().is_empty)

	def test_new_photo_is_a_change(self):
		self.local["files"]["/files/b.jpg"] = "hash-b"
		self.local["website_items"]["A"]["website_image"] = "/files/b.jpg"
		changes = self.plan().changes
		self.assertEqual([(c.doctype, list(c.values)) for c in changes], [("Website Item", ["website_image"])])

	def test_price_change_reports_old_and_new(self):
		self.local["prices"]["A"]["price_list_rate"] = 400.0
		plan = self.plan()
		self.assertEqual(plan.changes[0].before, {"price_list_rate": 350.0})
		self.assertIn("350.0 → 400.0", describe(plan)[0])

	def test_change_on_live_since_last_sync_is_left_alone(self):
		"""The conflict guard."""
		self.local["prices"]["A"]["price_list_rate"] = 400.0
		self.live["prices"]["A"]["_modified"] = "t2"
		plan = self.plan()
		self.assertEqual(plan.changes, [])
		self.assertEqual(plan.conflicts[0][:2], ("prices", "A"))

	def test_never_synced_record_is_not_overwritten(self):
		"""Before a baseline, nothing says the laptop's copy is the newer one."""
		self.local["prices"]["A"]["price_list_rate"] = 400.0
		self.state = {}
		plan = self.plan()
		self.assertEqual(plan.changes, [])
		self.assertEqual(len(plan.conflicts), 1)

	def test_new_product_creates_template_before_variant(self):
		self.local["items"]["V"] = {"item_name": "V", "variant_of": "T", "attributes": [], "_modified": "t1"}
		self.local["items"]["T"] = {"item_name": "T", "variant_of": "", "has_variants": 1, "attributes": [], "_modified": "t1"}
		created = [c.key for c in self.plan().changes if c.collection == "items"]
		self.assertEqual(created, ["T", "V"])

	def test_removed_from_laptop_unpublishes_and_never_deletes(self):
		del self.local["website_items"]["A"]
		changes = self.plan().changes
		self.assertEqual([(c.action, c.key) for c in changes], [("unpublish", "A")])

	def test_record_only_on_live_is_left_alone(self):
		self.live["website_items"]["L"] = {"item_code": "L", "published": 1, "_modified": "t9"}
		plan = self.plan()
		self.assertEqual(plan.changes, [])
		self.assertIn(("website_items", "L"), plan.live_only)

	def test_laptop_only_photo_from_before_the_baseline_is_held_back(self):
		self.local["files"]["/files/x.jpg"] = "hash-x"
		self.local["attachments"]["Item/A"] = ["/files/x.jpg"]
		self.state["held_attachments"] = {"Item/A": ["hash-x"]}

		plan = self.plan()
		self.assertTrue(plan.is_empty)
		self.assertEqual(plan.held, 1)

		self.assertEqual(self.plan(include_held=True).attachments, [("Item", "A", "/files/x.jpg")])

	def test_new_photo_after_the_baseline_is_sent(self):
		self.local["files"]["/files/y.jpg"] = "hash-y"
		self.local["attachments"]["Item/A"] = ["/files/y.jpg"]
		self.assertEqual(self.plan().attachments, [("Item", "A", "/files/y.jpg")])

	def test_record_only_on_live_is_not_unpublished_by_a_later_push(self):
		"""A product added directly on live must survive every future push."""
		self.live["website_items"]["L"] = {"item_code": "L", "published": 1, "_modified": "t9"}
		self.state = next_state(self.state, self.live, self.local, self.plan().conflicts)
		plan = self.plan()
		self.assertEqual(plan.changes, [])
		self.assertIn(("website_items", "L"), plan.live_only)

	def test_skipped_conflict_stays_refused_on_the_next_push(self):
		"""Otherwise the second push would overwrite the edit the first one protected."""
		self.local["prices"]["A"]["price_list_rate"] = 400.0
		self.live["prices"]["A"].update(price_list_rate=999.0, _modified="t2")
		self.state = next_state(self.state, self.live, self.local, self.plan().conflicts)
		plan = self.plan()
		self.assertEqual(plan.changes, [])
		self.assertEqual(plan.conflicts[0][:2], ("prices", "A"))

	def test_pushed_record_moves_on(self):
		self.live["prices"]["A"]["_modified"] = "t3"
		self.state = next_state(self.state, self.live, self.local, [])
		self.assertEqual(self.state["prices"]["A"], "t3")

	def test_new_option_is_added(self):
		self.local["attribute_values"]["Size"].append("Maxi")
		self.assertEqual(self.plan().attribute_values, [("Size", "Maxi")])


class TestSyncUser(IntegrationTestCase):
	EMAIL = "catalog.sync.test@example.com"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		setup_shop()
		from akwaaba_theme.install import _ensure_catalog_sync_role

		_ensure_catalog_sync_role()
		frappe.delete_doc_if_exists("User", cls.EMAIL, force=1)
		if not frappe.db.exists("User", cls.EMAIL):
			frappe.get_doc({
				"doctype": "User",
				"email": cls.EMAIL,
				"first_name": "Sync",
				"send_welcome_email": 0,
				"roles": [{"role": api.ROLE}],
			}).insert(ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, "Administrator")

	def test_refused_everywhere_outside_the_sync_endpoints(self):
		frappe.set_user(self.EMAIL)
		for doctype in ("Sales Order", "Sales Invoice", "Payment Entry", "Customer", "Website Script",
		                "Server Script", "Akwaaba Payment Settings", "Stock Reconciliation"):
			self.assertFalse(frappe.has_permission(doctype, "read"), f"{doctype} read")
			self.assertFalse(frappe.has_permission(doctype, "write"), f"{doctype} write")
		self.assertFalse(frappe.has_permission("User", "read", doc="Administrator"))
		self.assertFalse(frappe.has_permission("User", "write", doc="Administrator"))

	def test_is_not_a_desk_user(self):
		self.assertEqual(frappe.db.get_value("User", self.EMAIL, "user_type"), "Website User")

	def test_has_no_direct_rights_on_catalog_records_either(self):
		frappe.set_user(self.EMAIL)
		for doctype in ("Item", "Item Price", "Website Item"):
			self.assertFalse(frappe.has_permission(doctype, "delete"), f"{doctype} delete")
			self.assertFalse(frappe.has_permission(doctype, "write"), f"{doctype} write")

	def test_not_studio_staff(self):
		from akwaaba_theme.studio.api import require_studio_user

		frappe.set_user(self.EMAIL)
		with self.assertRaises(frappe.PermissionError):
			require_studio_user()

	def test_others_cannot_use_the_endpoints(self):
		frappe.set_user("Guest")
		for call in (api.export, lambda: api.upsert("Item", ITEM_CODE, "{}"), lambda: api.unpublish(ITEM_CODE)):
			with self.assertRaises(frappe.PermissionError):
				call()

	def test_export_works_and_carries_no_orders_or_quantities(self):
		frappe.set_user(self.EMAIL)
		data = api.export()
		self.assertIn(ITEM_CODE, data["items"])
		self.assertEqual(
			set(data),
			{"item_groups", "items", "prices", "website_items", "slideshows", "attachments", "attribute_values", "files"},
		)
		self.assertNotIn("opening_stock", data["items"][ITEM_CODE])

	def test_only_catalog_fields_can_be_written(self):
		"""Stock quantities must never travel."""
		frappe.set_user(self.EMAIL)
		for bad in ("opening_stock", "valuation_rate", "owner", "docstatus"):
			with self.assertRaises(frappe.PermissionError):
				api.upsert("Item", ITEM_CODE, frappe.as_json({bad: 5}))

	def test_only_catalog_doctypes_can_be_written(self):
		frappe.set_user(self.EMAIL)
		for doctype in ("Sales Order", "Customer", "User", "Website Script"):
			with self.assertRaises(frappe.PermissionError):
				api.upsert(doctype, "x", "{}")

	def test_files_only_attach_to_catalog_records(self):
		frappe.set_user(self.EMAIL)
		with self.assertRaises(frappe.ValidationError):
			api.save_file("x.jpg", b"not checked yet", "Sales Order", "SAL-ORD-1")

	def test_non_image_upload_is_refused(self):
		frappe.set_user(self.EMAIL)
		with self.assertRaises(frappe.ValidationError):
			api.save_file("shell.jpg", b"#!/bin/sh\necho hi\n")

	def test_price_update_goes_through(self):
		frappe.set_user(self.EMAIL)
		before = frappe.db.get_value("Item Price", {"item_code": ITEM_CODE, "price_list": PRICE_LIST}, "price_list_rate")
		api.upsert("Item Price", ITEM_CODE, frappe.as_json({"price_list_rate": before + 1}))
		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value("Item Price", {"item_code": ITEM_CODE, "price_list": PRICE_LIST}, "price_list_rate"),
			before + 1,
		)
		frappe.db.rollback()
