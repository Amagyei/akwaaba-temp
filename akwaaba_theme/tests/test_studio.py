"""Studio findings S2, S4, S5, S6 and S7 of the code review. Gemini is always faked."""

import io
import os
import threading
import time
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from PIL import Image

from akwaaba_theme.overrides.website_item import CARD_WIDTH, make_card_image
from akwaaba_theme.studio import api as studio, catalog, gemini
from akwaaba_theme.tests.utils import setup_shop
from akwaaba_theme.www.product import index as product_page


def jpeg_bytes(width=2000, height=2600, color=(150, 90, 40)):
	buf = io.BytesIO()
	Image.effect_noise((width, height), 60).convert("RGB").save(buf, "JPEG", quality=95)
	return buf.getvalue()


def make_session():
	sess = frappe.get_doc({"doctype": studio.SESSION, "title": "Test", "status": "Draft"}).insert(ignore_permissions=True)
	f = studio._save_file(sess, jpeg_bytes(800, 1000), "source", field="source_image")
	sess.source_image = f.file_url
	sess.append("images", {"image": f.file_url, "kind": "Original", "include": 1})
	sess.save(ignore_permissions=True)
	frappe.db.commit()
	return sess


def unique_name():
	return "Test Piece " + frappe.generate_hash(length=6)


class StudioTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		setup_shop()
		frappe.db.set_single_value(studio.SETTINGS, "enabled", 1)
		frappe.db.commit()
		cls.group = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0]
		cls.prompt = frappe.get_all(studio.PROMPT, filters={"rows": 2, "cols": 2, "uses_model_arc": 0}, pluck="name", limit=1)[0]


class TestFinalize(StudioTestCase):
	def test_supporting_photo_becomes_main_when_nothing_generated_was_kept(self):
		"""S2: a product could publish with no main image."""
		sess = make_session()
		name = unique_name()

		out = studio.finalize(sess.name, frappe.as_json({
			"mode": "new", "item_name": name, "item_group": self.group, "price": 300, "publish": 1,
		}))

		image = frappe.db.get_value("Website Item", {"item_code": out["item_code"]}, "website_image")
		self.assertTrue(image)
		self.assertEqual(frappe.db.get_value("Item", out["item_code"], "image"), image)

	def test_public_copy_is_web_sized(self):
		"""S4: public copies went out at the session's full size and quality."""
		small = catalog._web_copy(jpeg_bytes())
		img = Image.open(io.BytesIO(small))
		self.assertLessEqual(max(img.size), catalog.WEB_MAX_PX)
		self.assertLess(len(small), len(jpeg_bytes()))


class TestCardImage(StudioTestCase):
	def make_item(self):
		sess = make_session()
		out = studio.finalize(sess.name, frappe.as_json({
			"mode": "new", "item_name": unique_name(), "item_group": self.group, "price": 300, "publish": 1,
		}))
		return frappe.get_doc("Website Item", {"item_code": out["item_code"]})

	def test_saving_a_product_makes_a_600px_card_image(self):
		"""S4."""
		wi = self.make_item()
		self.assertIn("-card", wi.thumbnail)
		with Image.open(frappe.get_site_path("public", "files", os.path.basename(wi.thumbnail))) as img:
			self.assertLessEqual(img.width, CARD_WIDTH)

	def test_shop_uses_the_card_image_and_the_product_page_the_full_one(self):
		"""S4."""
		from akwaaba_theme.www.shop import index as shop

		wi = self.make_item()
		rows = frappe.db.sql(
			f"""SELECT {shop.CARD_IMAGE_SQL} AS image FROM `tabWebsite Item` wi
			LEFT JOIN `tabItem` i ON i.item_code = wi.item_code WHERE wi.name = %(n)s""",
			{"n": wi.name},
			as_dict=True,
		)
		self.assertEqual(rows[0].image, wi.thumbnail)
		self.assertNotEqual(wi.website_image, wi.thumbnail)

	def test_unreadable_photo_never_blocks_a_product(self):
		wi = frappe._dict(name="x", website_image="/files/does-not-exist.jpg")
		self.assertIsNone(make_card_image(wi))
		self.assertIsNone(make_card_image(frappe._dict(name="x", website_image="https://elsewhere.test/a.jpg")))


class TestDescription(IntegrationTestCase):
	def test_identical_text_is_not_printed_twice(self):
		"""S5."""
		text = "Handwoven in Bonwire."
		self.assertEqual(product_page._description(frappe._dict(short_description=text, description=f"<p>{text}</p>")), "")
		self.assertEqual(product_page._description(frappe._dict(item_name="Piece", description="Piece")), "")
		self.assertEqual(
			product_page._description(frappe._dict(short_description=text, description="Longer story.")),
			"Longer story.",
		)


class TestGenerate(StudioTestCase):
	def test_double_tap_pays_once(self):
		"""S6: the Generating check wasn't atomic."""
		sess = make_session()
		site, sites_path = frappe.local.site, frappe.local.sites_path
		calls, outcomes = [], []
		barrier = threading.Barrier(2)
		image = jpeg_bytes(400, 400)

		def fake_edit(**kwargs):
			calls.append(1)
			time.sleep(1)
			return image

		def tap():
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			frappe.flags.in_test = True
			frappe.set_user("Administrator")
			try:
				barrier.wait(timeout=30)
				studio.generate(sess.name, [self.prompt])
				outcomes.append("ran")
			except frappe.ValidationError:
				outcomes.append("refused")
			finally:
				frappe.destroy()

		with patch.object(gemini, "edit_image", side_effect=fake_edit):
			threads = [threading.Thread(target=tap) for _ in range(2)]
			for thread in threads:
				thread.start()
			for thread in threads:
				thread.join(timeout=60)

		self.assertEqual(sorted(outcomes), ["ran", "refused"])
		self.assertEqual(len(calls), 1)

	def test_grid_is_sliced_into_four_gallery_photos(self):
		sess = make_session()
		with patch.object(gemini, "edit_image", return_value=jpeg_bytes(800, 1000)):
			out = studio.generate(sess.name, [self.prompt])
		self.assertEqual(out["errors"], [])
		self.assertEqual(len(out["runs"][0]["panels"]), 4)


class TestCleanup(StudioTestCase):
	def test_old_completed_sessions_go_and_published_photos_stay(self):
		"""S7."""
		sess = make_session()
		out = studio.finalize(sess.name, frappe.as_json({
			"mode": "new", "item_name": unique_name(), "item_group": self.group, "price": 300, "publish": 1,
		}))
		private_url = sess.source_image
		public_url = frappe.db.get_value("Website Item", {"item_code": out["item_code"]}, "website_image")
		frappe.db.set_value(
			studio.SESSION, sess.name, "modified", frappe.utils.add_days(frappe.utils.now_datetime(), -40), update_modified=False
		)
		fresh = make_session()
		frappe.db.commit()

		studio.cleanup_completed_sessions(days=30)

		self.assertFalse(frappe.db.exists(studio.SESSION, sess.name))
		self.assertFalse(frappe.db.exists("File", {"file_url": private_url, "attached_to_name": sess.name}))
		self.assertTrue(frappe.db.exists(studio.SESSION, fresh.name))
		self.assertTrue(os.path.exists(frappe.get_site_path("public", "files", os.path.basename(public_url))))
