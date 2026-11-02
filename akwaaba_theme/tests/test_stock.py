"""Mixed stock: made-to-order pieces never run out, tracked pieces do."""

import threading
from unittest.mock import patch

import frappe

from akwaaba_theme import api, stock
from akwaaba_theme.tests.test_payments import PaymentTestCase
from akwaaba_theme.tests.utils import (
	ITEM_CODE,
	make_tracked_item,
	paystack_payload,
	paystack_response,
	submitted_invoices,
	submitted_payments,
)
from akwaaba_theme.www.product import index as product_page
from akwaaba_theme.www.shop import index as shop_page


class StockTestCase(PaymentTestCase):
	def tracked(self, qty):
		frappe.set_user("Administrator")
		code = make_tracked_item(qty)
		frappe.set_user("Guest")
		return code

	def pay(self, order):
		reference = f"AKW-{order['name']}--try1"
		return self.verify_with(reference, paystack_payload(reference, order["name"]))

	def flagged(self, order):
		return frappe.db.exists("Error Log", {"method": f"Paystack refund needed {order['name']}"})


class TestAvailability(StockTestCase):
	def test_made_to_order_has_no_limit(self):
		self.assertEqual(stock.available([ITEM_CODE]), {})
		self.assertEqual(stock.short_lines({ITEM_CODE: 1000}), [])
		self.place({ITEM_CODE: 50})

	def test_never_stocked_counts_as_none_left(self):
		code = self.tracked(0)
		self.assertEqual(stock.available([code]), {code: 0.0})

	def test_paid_order_reserves_the_piece(self):
		code = self.tracked(1)
		order = self.place({code: 1})
		self.assertEqual(stock.available([code])[code], 1.0, "a draft holds nothing")
		self.assertTrue(self.pay(order)["ok"])
		self.assertEqual(stock.available([code])[code], 0.0)

	def test_cannot_order_more_than_is_left(self):
		code = self.tracked(2)
		with self.assertRaises(frappe.ValidationError) as caught:
			self.place({code: 3})
		self.assertIn("Only 2 left", str(caught.exception))

	def test_sold_out_piece_refuses_a_new_order(self):
		code = self.tracked(1)
		self.pay(self.place({code: 1}))
		with self.assertRaises(frappe.ValidationError) as caught:
			self.place({code: 1})
		self.assertIn("sold out", str(caught.exception))

	def test_waiting_order_cannot_start_paying_after_the_last_piece_went(self):
		code = self.tracked(1)
		first, second = self.place({code: 1}), self.place({code: 1})
		self.pay(first)
		with patch.object(api.requests, "post") as post, self.assertRaises(frappe.ValidationError):
			api.init_paystack_payment(second["name"], key=second["url"].rsplit("/", 1)[1])
		post.assert_not_called()


class TestLostRace(StockTestCase):
	def test_paying_after_the_last_piece_went_is_recorded_and_flagged(self):
		"""The money is real, so the books record it, and a person decides what's next."""
		code = self.tracked(1)
		first, second = self.place({code: 1}), self.place({code: 1})

		self.assertFalse(self.pay(first).get("oversold"))
		result = self.pay(second)

		self.assertEqual(result["oversold"], [code])
		self.assertTrue(result["ok"])
		self.assertTrue(self.flagged(second))
		self.assertFalse(self.flagged(first))
		self.assert_settled(second["name"])

	def test_two_orders_paying_at_once_flag_exactly_one(self):
		code = self.tracked(1)
		orders = [self.place({code: 1}), self.place({code: 1})]
		site, sites_path = frappe.local.site, frappe.local.sites_path
		barrier = threading.Barrier(2)
		errors = []

		def pay(order):
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			frappe.flags.in_test = True
			try:
				barrier.wait(timeout=30)
				reference = f"AKW-{order['name']}--try1"
				api._complete_accounting_cycle(order["name"], reference, paystack_payload(reference, order["name"]))
			except Exception:
				errors.append(frappe.get_traceback())
			finally:
				frappe.destroy()

		threads = [threading.Thread(target=pay, args=(o,)) for o in orders]
		for thread in threads:
			thread.start()
		for thread in threads:
			thread.join(timeout=180)

		self.assertEqual(errors, [])
		frappe.db.rollback()
		self.assertEqual(sum(1 for o in orders if self.flagged(o)), 1)
		for order in orders:
			self.assert_settled(order["name"])


class TestDelivery(StockTestCase):
	def test_stock_leaves_on_the_delivery_note(self):
		code = self.tracked(2)
		order = self.place({code: 1})
		self.pay(order)
		frappe.set_user("Administrator")
		warehouse = stock.stock_warehouse()
		self.assertEqual(frappe.db.get_value("Bin", {"item_code": code, "warehouse": warehouse}, "actual_qty"), 2)

		try:
			from erpnext.selling.doctype.sales_order.mapper import make_delivery_note
		except ImportError:
			from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
		dn = make_delivery_note(order["name"])
		dn.insert(ignore_permissions=True)
		dn.submit()
		frappe.db.commit()

		self.assertEqual(frappe.db.get_value("Bin", {"item_code": code, "warehouse": warehouse}, "actual_qty"), 1)
		self.assertEqual(stock.available([code])[code], 1.0)


class TestDisplay(StockTestCase):
	def test_shop_and_product_page_show_sold_out(self):
		code = self.tracked(1)
		item = frappe._dict(item_code=code)
		self.assertFalse(product_page._sold_out(item, []))
		self.pay(self.place({code: 1}))
		self.assertTrue(product_page._sold_out(item, []))
		self.assertEqual(shop_page.sold_out_codes([code, ITEM_CODE]), {code})

	def test_template_is_sold_out_only_when_every_option_is(self):
		item = frappe._dict(item_code="T", has_variants=1)
		self.assertTrue(product_page._sold_out(item, [{"oos": True}, {"oos": True}]))
		self.assertFalse(product_page._sold_out(item, [{"oos": True}, {"oos": False}]))
