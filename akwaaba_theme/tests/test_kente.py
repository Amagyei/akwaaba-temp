"""The loom at the top of a Kente collection."""

import frappe
from frappe.tests import IntegrationTestCase

from akwaaba_theme.tests.utils import ensure_item, setup_shop
from akwaaba_theme.www.shop import index as shop_page


class TestLoomCloths(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		setup_shop()
		cls.group = "Kente Test"
		if not frappe.db.exists("Item Group", cls.group):
			frappe.get_doc({"doctype": "Item Group", "item_group_name": cls.group, "is_group": 0, "parent_item_group": "All Item Groups"}).insert(ignore_permissions=True)
		ensure_item("KNT-TEST-1", "Kente One", 900)
		ensure_item("KNT-TEST-2", "Kente Two", 900)
		for code in ("KNT-TEST-1", "KNT-TEST-2"):
			frappe.db.set_value("Item", code, "item_group", cls.group)
			frappe.db.set_value("Website Item", {"item_code": code}, "item_group", cls.group)
		frappe.db.set_value("Website Item", {"item_code": "KNT-TEST-1"}, {
			"loom_strip": "/files/strip-1.jpg", "loom_thumb": "/files/thumb-1.jpg",
			"loom_warp_colors": "AA0000 00AA00", "loom_bobbin_color": "#AA0000", "route": "kente-one",
			"short_description": "Wisdom",
		})
		frappe.db.set_value("Website Item", {"item_code": "KNT-TEST-2"}, "loom_strip", "")
		frappe.db.commit()

	def test_only_published_cloths_with_a_strip_are_on_the_loom(self):
		cloths = shop_page._loom_cloths(self.group)
		self.assertEqual([c["route"] for c in cloths], ["kente-one"])
		self.assertEqual(cloths[0]["meaning"], "Wisdom")
		self.assertEqual(cloths[0]["strip"], "/files/strip-1.jpg")
		self.assertEqual(cloths[0]["warp"], "AA0000 00AA00")

	def test_unpublished_cloth_leaves_the_loom(self):
		frappe.db.set_value("Website Item", {"item_code": "KNT-TEST-1"}, "published", 0)
		self.assertEqual(shop_page._loom_cloths(self.group), [])
		frappe.db.rollback()

	def test_all_collections_view_has_no_loom(self):
		self.assertEqual(shop_page._loom_cloths(None), [])
