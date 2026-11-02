"""The Studio form: models, arcs, engines, and sizes or colours with labeled photos."""

import base64
import http.server
import io
import json
import threading
from unittest.mock import patch

import frappe
from PIL import Image

from akwaaba_theme import stock
from akwaaba_theme.catalog_sync.export import export_catalog
from akwaaba_theme.install import ensure_studio_defaults
from akwaaba_theme.studio import api as studio, gemini, local_engine
from akwaaba_theme.tests.test_studio import StudioTestCase, jpeg_bytes, make_session, unique_name
from akwaaba_theme.www.product import index as product_page

ABENA, ARC_1 = "Abena", "Hesitation to Authority"


def story_prompt():
	return frappe.db.get_value(studio.PROMPT, {"uses_model_arc": 1})


def add_labeled_photo(sess, label, color=(10, 20, 30)):
	buf = io.BytesIO()
	Image.new("RGB", (300, 400), color).save(buf, "JPEG")
	f = studio._save_file(sess, buf.getvalue(), f"opt-{frappe.generate_hash(length=4)}")
	sess.reload()
	sess.append("images", {"image": f.file_url, "kind": "Extra", "include": 1, "option_label": label})
	sess.save(ignore_permissions=True)
	frappe.db.commit()
	return f.file_url


class FormTestCase(StudioTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_studio_defaults()


class TestPromptLibrary(FormTestCase):
	def test_library_is_seeded(self):
		self.assertEqual(frappe.db.count(studio.MODEL), 4)
		self.assertEqual(frappe.db.count(studio.ARC), 46)
		self.assertEqual(frappe.db.count(studio.ARC, {"enabled": 1}), 18)
		self.assertEqual(frappe.db.count(studio.ARC, {"enabled": 1, "skirts_only": 1}), 5)

	def test_seeding_again_keeps_desk_edits(self):
		frappe.db.set_value(studio.ARC, ARC_1, "enabled", 0)
		ensure_studio_defaults()
		self.assertEqual(frappe.db.get_value(studio.ARC, ARC_1, "enabled"), 0)
		frappe.db.set_value(studio.ARC, ARC_1, "enabled", 1)
		frappe.db.commit()

	def test_every_model_is_an_adult(self):
		for profile in frappe.get_all(studio.MODEL, pluck="profile"):
			self.assertIn("adult", profile.lower())

	def test_built_prompt_has_the_model_and_all_four_shots_and_no_sample_outfit(self):
		s = frappe.get_single(studio.SETTINGS)
		arc = frappe.get_doc(studio.ARC, ARC_1)
		text = studio._full_prompt(frappe.get_doc(studio.PROMPT, story_prompt()), s, frappe.get_doc(studio.MODEL, ABENA), arc)
		self.assertIn("named Abena", text)
		for i in range(1, 5):
			self.assertIn(arc.get(f"shot_{i}"), text)
		self.assertIn("garment in the attached photo", text)
		for sample in ("batik", "scalloped", "mini skirt", "{story}", "{model}"):
			self.assertNotIn(sample, text)
		self.assertIn("2 rows × 2 columns", text)


class TestGenerate(FormTestCase):
	def test_story_needs_a_model_and_an_arc(self):
		sess = make_session()
		with patch.object(gemini, "edit_image") as call, self.assertRaises(frappe.ValidationError):
			studio.generate(sess.name, [story_prompt()])
		call.assert_not_called()

	def test_story_run_is_labeled_and_shot_4_starts_as_main(self):
		sess = make_session()
		with patch.object(gemini, "edit_image", return_value=jpeg_bytes(800, 1000)) as call:
			out = studio.generate(sess.name, [story_prompt()], model=ABENA, arc=ARC_1, engine=studio.ENGINE_GEMINI)
		run = out["runs"][0]
		self.assertEqual(run["label"], f"{ABENA} · {ARC_1}")
		self.assertEqual([p["panel"] for p in run["panels"] if p["main"]], [4])
		self.assertIn("named Abena", call.call_args.kwargs["prompt"])
		self.assertGreater(out["est_cost"], 0)

	def test_other_styles_do_not_need_a_model(self):
		sess = make_session()
		with patch.object(gemini, "edit_image", return_value=jpeg_bytes(800, 1000)):
			out = studio.generate(sess.name, [self.prompt], engine=studio.ENGINE_GEMINI)
		self.assertEqual(out["errors"], [])
		self.assertIsNone(out["runs"][0]["label"])


class FakeLocalTool(http.server.BaseHTTPRequestHandler):
	requests_seen = []

	def do_POST(self):
		body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
		FakeLocalTool.requests_seen.append((self.path, body))
		payload = json.dumps({"images": [base64.b64encode(jpeg_bytes(400, 500)).decode()]}).encode()
		self.send_response(200)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(payload)))
		self.end_headers()
		self.wfile.write(payload)

	def log_message(self, *args):
		pass


class TestLocalEngine(FormTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeLocalTool)
		threading.Thread(target=cls.server.serve_forever, daemon=True).start()
		cls.addClassCleanup(cls.server.shutdown)
		cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

	def setUp(self):
		super().setUp()
		frappe.db.set_single_value(studio.SETTINGS, "local_engine_url", self.url)
		frappe.db.commit()
		self.addCleanup(lambda: (frappe.db.set_single_value(studio.SETTINGS, "local_engine_url", ""), frappe.db.commit()))

	def test_local_run_is_free_sliced_and_sends_the_photo(self):
		FakeLocalTool.requests_seen.clear()
		sess = make_session()
		with patch.object(gemini, "edit_image") as paid:
			out = studio.generate(sess.name, [story_prompt()], model=ABENA, arc=ARC_1, engine=studio.ENGINE_LOCAL)
		paid.assert_not_called()
		self.assertEqual(out["errors"], [])
		self.assertEqual(len(out["runs"][0]["panels"]), 4)
		self.assertEqual(out["est_cost"], 0)
		path, body = FakeLocalTool.requests_seen[0]
		self.assertEqual(path, "/sdapi/v1/img2img")
		self.assertTrue(body["init_images"][0])
		self.assertIn("named Abena", body["prompt"])

	def test_unreachable_tool_comes_back_as_a_style_error(self):
		frappe.db.set_single_value(studio.SETTINGS, "local_engine_url", "http://127.0.0.1:9")
		frappe.db.commit()
		sess = make_session()
		out = studio.generate(sess.name, [self.prompt], engine=studio.ENGINE_LOCAL)
		self.assertIn("Couldn't reach", out["errors"][0]["error"])

	def test_form_offers_local_only_when_a_url_is_set(self):
		keys = [e["key"] for e in studio.bootstrap()["engines"]]
		self.assertIn(studio.ENGINE_LOCAL, keys)
		frappe.db.set_single_value(studio.SETTINGS, "local_engine_url", "")
		self.assertNotIn(studio.ENGINE_LOCAL, [e["key"] for e in studio.bootstrap()["engines"]])

	def test_sizes_are_multiples_of_64(self):
		self.assertEqual(local_engine.dimensions("3:4", "1K"), (768, 1024))
		for side in local_engine.dimensions("3:2", "2K"):
			self.assertEqual(side % 64, 0)


class TestOptions(FormTestCase):
	def session_with(self, kind, options):
		sess = make_session()
		studio.set_options(sess.name, kind, frappe.as_json(options))
		sess.reload()
		return sess

	def finalize(self, sess, **data):
		return studio.finalize(sess.name, frappe.as_json(dict(
			{"mode": "new", "item_name": unique_name(), "item_group": self.group, "price": 300, "publish": 1}, **data
		)))

	def test_first_photo_goes_to_the_first_option_and_labels_are_validated(self):
		sess = self.session_with("Size", [{"label": "Mini", "price": 350}, {"label": "Maxi"}])
		self.assertEqual(next(r.option_label for r in sess.images if r.kind == "Original"), "Mini")
		with self.assertRaises(frappe.ValidationError):
			studio.set_options(sess.name, "Size", frappe.as_json([{"label": "Mini"}, {"label": "mini"}]))
		with self.assertRaises(frappe.ValidationError):
			studio.set_options(sess.name, "Weight", frappe.as_json([{"label": "Heavy"}]))

	def test_removing_an_option_clears_its_photos_labels(self):
		sess = self.session_with("Size", [{"label": "Mini"}, {"label": "Maxi"}])
		add_labeled_photo(sess, "Maxi")
		out = studio.set_options(sess.name, "Size", frappe.as_json([{"label": "Mini"}]))
		self.assertEqual({r["option"] for r in out["supporting"]}, {"Mini", None})

	def test_generating_starts_from_that_options_photo_and_labels_the_results(self):
		sess = self.session_with("Colour", [{"label": "Indigo"}, {"label": "Wine"}])
		add_labeled_photo(sess, "Wine", (120, 10, 40))
		wine_url = next(r.image for r in frappe.get_doc(studio.SESSION, sess.name).images if r.option_label == "Wine")
		wine_bytes = studio._file_bytes(sess, wine_url)
		with patch.object(gemini, "edit_image", return_value=jpeg_bytes(800, 1000)) as call:
			out = studio.generate(sess.name, [self.prompt], engine=studio.ENGINE_GEMINI, option_label="Wine")
		self.assertEqual(call.call_args.kwargs["image_bytes"], wine_bytes)
		self.assertIsInstance(wine_bytes, bytes, "a plain JPEG must not come back as text")
		self.assertEqual(out["runs"][0]["option"], "Wine")
		self.assertTrue(all(p["option"] == "Wine" for p in out["runs"][0]["panels"]))

	def test_option_with_no_photo_says_so(self):
		sess = self.session_with("Size", [{"label": "Mini"}, {"label": "Maxi"}])
		with patch.object(gemini, "edit_image") as call, self.assertRaises(frappe.ValidationError):
			studio.generate(sess.name, [self.prompt], engine=studio.ENGINE_GEMINI, option_label="Maxi")
		call.assert_not_called()

	def test_saving_sizes_makes_variants_with_their_own_price_and_photos(self):
		sess = self.session_with("Size", [{"label": "Mini", "price": 350}, {"label": "Maxi", "price": 500}])
		maxi_photo = add_labeled_photo(sess, "Maxi", (200, 30, 30))

		out = self.finalize(sess)

		template = out["item_code"]
		self.assertEqual(frappe.db.get_value("Item", template, "has_variants"), 1)
		variants = frappe.get_all("Item", filters={"variant_of": template}, pluck="name")
		self.assertEqual(len(variants), 2)

		wi = frappe.db.get_value("Website Item", {"item_code": template}, ["name", "item_code", "has_variants"], as_dict=True)
		options = {o["size"]: o for o in product_page._variants(frappe._dict(wi))}
		self.assertEqual({k: v["price"] for k, v in options.items()}, {"Mini": 350.0, "Maxi": 500.0})
		self.assertTrue(options["Maxi"]["images"] and options["Mini"]["images"])
		self.assertNotEqual(options["Maxi"]["images"][0], options["Mini"]["images"][0])
		self.assertTrue(maxi_photo)

	def test_colours_work_and_say_colour_on_the_page(self):
		sess = self.session_with("Colour", [{"label": "Indigo"}, {"label": "Wine"}])
		add_labeled_photo(sess, "Wine", (120, 10, 40))
		out = self.finalize(sess)
		item = frappe._dict(item_code=out["item_code"], has_variants=1)
		options = product_page._variants(item)
		self.assertEqual({o["size"] for o in options}, {"Indigo", "Wine"})
		self.assertEqual(item.option_word, "colour")

	def test_option_without_a_price_uses_the_main_price(self):
		sess = self.session_with("Size", [{"label": "Mini", "price": 350}, {"label": "Maxi"}])
		out = self.finalize(sess, price=420)
		item = frappe._dict(item_code=out["item_code"], has_variants=1)
		self.assertEqual({o["size"]: o["price"] for o in product_page._variants(item)}, {"Mini": 350.0, "Maxi": 420.0})

	def test_publishing_needs_a_price_for_every_option(self):
		sess = self.session_with("Size", [{"label": "Mini", "price": 350}, {"label": "Maxi"}])
		with self.assertRaises(frappe.ValidationError):
			self.finalize(sess, price=0)

	def test_limited_stock_is_tracked_per_option(self):
		sess = self.session_with("Size", [{"label": "Mini", "price": 350}, {"label": "Maxi", "price": 500}])
		out = self.finalize(sess, stock_mode="limited")
		variants = frappe.get_all("Item", filters={"variant_of": out["item_code"]}, pluck="name")
		self.assertTrue(all(frappe.db.get_value("Item", v, "is_stock_item") for v in variants))
		self.assertEqual(set(stock.available(variants).values()), {0.0})
		item = frappe._dict(item_code=out["item_code"], has_variants=1)
		options = product_page._variants(item)
		self.assertTrue(all(o["oos"] for o in options))
		self.assertTrue(product_page._sold_out(item, options))

	def test_made_to_order_is_the_default(self):
		out = self.finalize(make_session())
		self.assertEqual(frappe.db.get_value("Item", out["item_code"], "is_stock_item"), 0)

	def test_variants_travel_in_the_catalog_sync(self):
		sess = self.session_with("Size", [{"label": "Mini", "price": 350}])
		out = self.finalize(sess)
		data = export_catalog()
		variant = frappe.get_all("Item", filters={"variant_of": out["item_code"]}, pluck="name")[0]
		self.assertEqual(data["items"][variant]["variant_of"], out["item_code"])
		self.assertEqual(data["items"][variant]["attributes"], [{"attribute": "Size", "attribute_value": "Mini"}])
		self.assertEqual(data["prices"][variant]["price_list_rate"], 350.0)
		self.assertIn(variant, data["website_items"])
