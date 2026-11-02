"""The ways real money could go wrong: findings P1 to P8 of the code review."""

import hashlib
import hmac
import json
import threading
import time
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from akwaaba_theme import api, momo
from akwaaba_theme.tests.utils import (
	ITEM_CODE,
	PRICE,
	UNPRICED_ITEM_CODE,
	any_invoices,
	ledger_imbalance,
	paystack_payload,
	paystack_response,
	setup_shop,
	submitted_invoices,
	submitted_payments,
)
from akwaaba_theme.www.order import detail as order_page


class PaymentTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.settings = setup_shop()
		settings_patch = patch.object(api, "_get_payment_settings", lambda: cls.settings)
		settings_patch.start()
		cls.addClassCleanup(settings_patch.stop)

	def setUp(self):
		super().setUp()
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")

	def place(self, items=None, phone=None):
		PaymentTestCase.counter = getattr(PaymentTestCase, "counter", 0) + 1
		phone = phone or "024" + frappe.generate_hash(length=7).encode().hex()[:7]
		order = api.place_order(
			items=json.dumps(items or {ITEM_CODE: 1}),
			full_name="Test Customer",
			phone=phone,
			email=f"{phone}@example.com",
			address_line="1 Test Lane",
			city="Accra",
		)
		frappe.db.commit()
		return order

	def verify_with(self, reference, data):
		with patch.object(api.requests, "get", return_value=paystack_response(data)):
			return api.verify_payment(reference)

	def assert_settled(self, order_name):
		frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Sales Order", order_name, "docstatus"), 1)
		invoices = submitted_invoices(order_name)
		self.assertEqual(len(invoices), 1, "exactly one submitted invoice")
		payments = submitted_payments(invoices[0])
		self.assertEqual(len(payments), 1, "exactly one submitted payment")
		self.assertEqual(ledger_imbalance(invoices + payments), 0)


class TestPaystackReturn(PaymentTestCase):
	def test_return_from_paystack_survives_the_end_of_the_page_load(self):
		"""P1: Frappe rolls back a GET's uncommitted writes when the request ends."""
		order = self.place()
		reference = f"AKW-{order['name']}--try1"

		result = self.verify_with(reference, paystack_payload(reference, order["name"]))
		frappe.db.rollback()

		self.assertEqual(result["status"], "success")
		self.assertTrue(result["ok"])
		self.assert_settled(order["name"])
		self.assertEqual(frappe.db.get_value("Sales Order", order["name"], "payment_reference"), reference)

	def test_verifying_again_changes_nothing(self):
		order = self.place()
		reference = f"AKW-{order['name']}--try1"
		self.verify_with(reference, paystack_payload(reference, order["name"]))

		with patch.object(api.requests, "get") as paystack:
			result = api.verify_payment(reference)

		self.assertEqual(result["status"], "already_completed")
		paystack.assert_not_called()
		self.assert_settled(order["name"])

	def test_amount_mismatch_refuses_the_order(self):
		order = self.place()
		reference = f"AKW-{order['name']}--try1"

		result = self.verify_with(reference, paystack_payload(reference, order["name"], amount=PRICE - 1))

		self.assertEqual(result["status"], "amount_mismatch")
		self.assertEqual(frappe.db.get_value("Sales Order", order["name"], "docstatus"), 0)

	def test_reference_naming_another_order_is_refused(self):
		order = self.place()
		other = self.place()
		reference = f"AKW-{order['name']}--try1"

		result = self.verify_with(reference, paystack_payload(reference, other["name"]))

		self.assertEqual(result["status"], "not_found")
		self.assertEqual(frappe.db.get_value("Sales Order", order["name"], "docstatus"), 0)


class TestConcurrency(PaymentTestCase):
	def run_together(self, *calls):
		"""Run each call on its own thread and DB connection, released at the same moment."""
		site, sites_path = frappe.local.site, frappe.local.sites_path
		barrier = threading.Barrier(len(calls))
		errors = []

		def run(call):
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			frappe.flags.in_test = True
			try:
				barrier.wait(timeout=30)
				call()
			except Exception:
				errors.append(frappe.get_traceback())
			finally:
				frappe.destroy()

		threads = [threading.Thread(target=run, args=(call,)) for call in calls]
		for thread in threads:
			thread.start()
		for thread in threads:
			thread.join(timeout=180)
		self.assertEqual(errors, [])

	def test_verify_and_webhook_at_the_same_moment_post_once(self):
		"""P3: both paths run on a real payment, often within the same second."""
		order = self.place()
		reference = f"AKW-{order['name']}--try1"
		data = paystack_payload(reference, order["name"])

		with patch.object(api.requests, "get", return_value=paystack_response(data)):
			self.run_together(
				lambda: api.verify_payment(reference),
				lambda: api._handle_charge_success(reference, data, self.settings),
			)

		self.assert_settled(order["name"])

	def test_two_retries_of_a_paid_order_create_one_invoice(self):
		"""P3: past the order's submit, nothing but the row lock stops double posting."""
		order = self.place()
		reference = f"AKW-{order['name']}--try1"
		data = paystack_payload(reference, order["name"])
		with patch.object(api, "_make_and_submit_sales_invoice", side_effect=RuntimeError("down")):
			self.assertFalse(self.verify_with(reference, data)["ok"])
		real_make_si = api._make_and_submit_sales_invoice

		def slow_make_si(order_name):
			# Hold the window between "no invoice yet" and "invoice saved" open.
			time.sleep(1)
			return real_make_si(order_name)

		with (
			patch.object(api.requests, "get", return_value=paystack_response(data)),
			patch.object(api, "_make_and_submit_sales_invoice", side_effect=slow_make_si),
		):
			self.run_together(
				lambda: api.verify_payment(reference),
				lambda: api._handle_charge_success(reference, data, self.settings),
			)

		self.assert_settled(order["name"])


class TestRetry(PaymentTestCase):
	def test_failed_step_rolls_back_then_retry_completes(self):
		"""P2: the order stays paid, no partial records stay, and a retry finishes."""
		order = self.place()
		reference = f"AKW-{order['name']}--try1"
		data = paystack_payload(reference, order["name"])

		with patch.object(api, "_make_and_submit_payment_entry", side_effect=RuntimeError("bank down")):
			first = self.verify_with(reference, data)
		frappe.db.rollback()

		self.assertEqual(first["status"], "success")
		self.assertFalse(first["ok"])
		self.assertEqual(frappe.db.get_value("Sales Order", order["name"], "docstatus"), 1)
		self.assertEqual(any_invoices(order["name"]), [], "the invoice rolled back with the payment")

		second = self.verify_with(reference, data)

		self.assertTrue(second["ok"])
		self.assert_settled(order["name"])

	def test_draft_invoice_does_not_block_the_retry(self):
		"""P2: a draft used to count as the invoice existing."""
		order = self.place()
		frappe.set_user("Administrator")
		so = frappe.get_doc("Sales Order", order["name"])
		so.submit()
		try:
			from erpnext.selling.doctype.sales_order.mapper import make_sales_invoice
		except ImportError:
			from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
		make_sales_invoice(source_name=order["name"]).insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.set_user("Guest")
		reference = f"AKW-{order['name']}--try1"

		result = self.verify_with(reference, paystack_payload(reference, order["name"]))

		self.assertTrue(result["ok"])
		self.assert_settled(order["name"])

	def test_missing_account_is_reported_and_retried(self):
		order = self.place()
		reference = f"AKW-{order['name']}--try1"
		data = paystack_payload(reference, order["name"])
		without_account = frappe._dict(self.settings, momo_account="")

		with patch.object(api, "_get_payment_settings", lambda: without_account):
			first = self.verify_with(reference, data)

		self.assertFalse(first["ok"])
		self.assertEqual(len(submitted_invoices(order["name"])), 1)

		self.assertTrue(self.verify_with(reference, data)["ok"])
		self.assert_settled(order["name"])

	def test_daily_job_finishes_stuck_orders_and_reports_the_rest(self):
		frappe.set_user("Administrator")
		fixable = self.place()
		stuck = self.place()
		frappe.set_user("Administrator")
		fix_ref = f"AKW-{fixable['name']}--try1"
		stuck_ref = f"AKW-{stuck['name']}--try1"
		fix_data = paystack_payload(fix_ref, fixable["name"])
		stuck_data = paystack_payload(stuck_ref, stuck["name"])
		with patch.object(api, "_make_and_submit_payment_entry", side_effect=RuntimeError("bank down")):
			self.verify_with(fix_ref, fix_data)
			self.verify_with(stuck_ref, stuck_data)

		responses = {fix_ref: fix_data, stuck_ref: stuck_data}

		def fake_get(url, **kwargs):
			return paystack_response(responses[url.rsplit("/", 1)[1]])

		real_make_pe = api._make_and_submit_payment_entry

		def fail_only_for_stuck(si_name, **kwargs):
			if kwargs["reference"] == stuck_ref:
				raise RuntimeError("still down")
			return real_make_pe(si_name=si_name, **kwargs)

		with (
			patch.object(api.requests, "get", side_effect=fake_get),
			patch.object(api, "_make_and_submit_payment_entry", side_effect=fail_only_for_stuck),
		):
			api.retry_unsettled_orders()

		self.assert_settled(fixable["name"])
		report = frappe.get_all(
			"Error Log",
			filters={"method": "Akwaaba paid orders missing records"},
			fields=["error"],
			order_by="creation desc",
			limit=1,
		)
		self.assertIn(stuck["name"], report[0].error)
		self.assertNotIn(fixable["name"], report[0].error)
		self.assertTrue(frappe.db.exists("Notification Log", {"subject": ["like", "%paid order(s) missing records"]}))

		# Later tests' daily job runs shouldn't trip over this deliberately stuck order.
		self.verify_with(stuck_ref, stuck_data)


class TestWebhook(PaymentTestCase):
	def call_webhook(self, data, signature=None):
		raw = json.dumps({"event": "charge.success", "data": data}).encode()
		signature = signature or hmac.new(self.settings.webhook_secret.encode(), raw, hashlib.sha512).hexdigest()
		frappe.local.request = MagicMock()
		frappe.local.request.get_data.return_value = raw
		frappe.local.request.headers = {"X-Paystack-Signature": signature}
		frappe.local.response = frappe._dict()
		self.addCleanup(setattr, frappe.local, "request", None)
		return api.paystack_webhook(), frappe.local.response.get("http_status_code")

	def test_bad_signature_is_refused(self):
		order = self.place()
		data = paystack_payload(f"AKW-{order['name']}--try1", order["name"])

		body, status = self.call_webhook(data, signature="not-it")

		self.assertEqual(status, 401)
		self.assertFalse(body["ok"])
		self.assertEqual(frappe.db.get_value("Sales Order", order["name"], "docstatus"), 0)

	def test_webhook_completes_the_order(self):
		order = self.place()

		body, status = self.call_webhook(paystack_payload(f"AKW-{order['name']}--try1", order["name"]))

		self.assertTrue(body["ok"])
		self.assertIsNone(status)
		self.assert_settled(order["name"])

	def test_failure_answers_500_so_paystack_resends(self):
		"""P2: the webhook used to answer 200 always, so Paystack stopped resending."""
		order = self.place()
		data = paystack_payload(f"AKW-{order['name']}--try1", order["name"])

		with patch.object(api, "_make_and_submit_sales_invoice", side_effect=RuntimeError("down")):
			body, status = self.call_webhook(data)
		self.assertEqual(status, 500)
		self.assertFalse(body["ok"])

		body, status = self.call_webhook(data)
		self.assertTrue(body["ok"])
		self.assertIsNone(status)
		self.assert_settled(order["name"])


class TestAttempts(PaymentTestCase):
	def init_payment(self, order):
		response = MagicMock()
		response.ok = True
		response.json.return_value = {"status": True, "data": {"authorization_url": "https://paystack.test/pay"}}
		with patch.object(api.requests, "post", return_value=response) as post:
			api.init_paystack_payment(order["name"], key=order["url"].rsplit("/", 1)[1])
		return post.call_args.kwargs["json"]

	def test_each_attempt_gets_its_own_reference_and_the_last_one_pays(self):
		"""P4: Paystack refuses a reference it has seen, which blocked paying again."""
		order = self.place()

		first = self.init_payment(order)
		second = self.init_payment(order)

		self.assertNotEqual(first["reference"], second["reference"])
		self.assertEqual(api._reference_to_order(first["reference"]), order["name"])
		self.assertEqual(api._reference_to_order(second["reference"]), order["name"])
		self.assertTrue(first["callback_url"].endswith(order["url"]))

		abandoned = self.verify_with(first["reference"], paystack_payload(first["reference"], order["name"], status="abandoned"))
		self.assertEqual(abandoned["status"], "abandoned")

		paid = self.verify_with(second["reference"], paystack_payload(second["reference"], order["name"]))
		self.assertEqual(paid["status"], "success")
		self.assert_settled(order["name"])

	def test_old_reference_format_still_resolves(self):
		self.assertEqual(api._reference_to_order("AKW-SAL-ORD-2026-00001"), "SAL-ORD-2026-00001")

	def test_paying_twice_flags_a_refund_and_posts_once(self):
		"""The other side of P4: two open tabs can now both take money."""
		order = self.place()
		first = f"AKW-{order['name']}--try1"
		second = f"AKW-{order['name']}--try2"
		self.verify_with(first, paystack_payload(first, order["name"]))

		result = self.verify_with(second, paystack_payload(second, order["name"]))

		self.assertEqual(result["status"], "already_completed")
		self.assert_settled(order["name"])
		self.assertTrue(frappe.db.exists("Error Log", {"method": f"Paystack refund needed {order['name']}"}))


class TestCancelled(PaymentTestCase):
	def cancelled_order(self):
		order = self.place()
		frappe.set_user("Administrator")
		so = frappe.get_doc("Sales Order", order["name"])
		so.submit()
		so.cancel()
		frappe.db.commit()
		frappe.set_user("Guest")
		return order, order["url"].rsplit("/", 1)[1]

	def test_cancelled_order_cannot_start_a_payment(self):
		"""P5."""
		order, key = self.cancelled_order()
		with patch.object(api.requests, "post") as post, self.assertRaises(frappe.ValidationError):
			api.init_paystack_payment(order["name"], key=key)
		post.assert_not_called()

	def test_money_for_a_cancelled_order_is_flagged_not_posted(self):
		"""P5."""
		order, _key = self.cancelled_order()
		reference = f"AKW-{order['name']}--try1"

		result = self.verify_with(reference, paystack_payload(reference, order["name"]))

		self.assertEqual(result["status"], "cancelled")
		self.assertEqual(any_invoices(order["name"]), [])
		self.assertTrue(frappe.db.exists("Error Log", {"method": f"Paystack refund needed {order['name']}"}))

	def test_cancelled_order_cannot_take_a_momo_claim(self):
		order, key = self.cancelled_order()
		with self.assertRaises(frappe.ValidationError):
			momo.submit_momo_claim(order["name"], key=key)

	def test_order_page_offers_no_payment(self):
		order, key = self.cancelled_order()
		context = open_order_page(order["name"], key)
		self.assertTrue(context.order.is_cancelled)
		self.assertFalse(context.paystack_enabled)
		self.assertIsNone(context.direct_momo)


class TestMomoSms(PaymentTestCase):
	def sms(self, message, sender="MobileMoney", secret=None):
		return momo.momo_sms_inbox(secret=secret or self.settings.sms_inbox_secret, message=message, sender=sender)

	def test_full_amount_naming_the_order_confirms_it(self):
		order = self.place()

		result = self.sms(f"Payment received for GHS {PRICE}.00 from 024XXX. Reference: {order['name']}.")

		self.assertEqual(result["status"], "auto_confirmed")
		self.assert_settled(order["name"])
		self.assertEqual(frappe.db.get_value("Sales Order", order["name"], "payment_status"), "Paid")

	def test_part_payment_does_not_confirm(self):
		"""P6."""
		order = self.place()

		result = self.sms(f"Payment received for GHS {PRICE / 2:.2f} from 024XXX. Reference: {order['name']}.")

		self.assertEqual(result["status"], "needs_review")
		self.assertEqual(frappe.db.get_value("Sales Order", order["name"], "docstatus"), 0)

	def test_matching_amount_alone_does_not_confirm(self):
		"""P6: with prices of 280 and 350, totals collide all the time."""
		order = self.place()
		frappe.set_user("Administrator")
		frappe.db.set_value("Sales Order", order["name"], "payment_status", momo.STATUS_AWAITING)
		frappe.db.commit()

		result = self.sms(f"Payment received for GHS {PRICE}.00 from 024XXX.")

		self.assertEqual(result["status"], "unmatched")
		self.assertEqual(frappe.db.get_value("Sales Order", order["name"], "docstatus"), 0)

	def test_sender_not_on_the_list_is_ignored(self):
		"""P6: any phone can text the forwarding phone."""
		order = self.place()

		result = self.sms(f"Payment received for GHS {PRICE}.00. Reference: {order['name']}.", sender="+233244000000")

		self.assertEqual(result["status"], "ignored")
		self.assertEqual(frappe.db.get_value("Sales Order", order["name"], "docstatus"), 0)

	def test_no_allowlist_turns_the_endpoint_off(self):
		order = self.place()
		with patch.object(api, "_get_payment_settings", lambda: frappe._dict(self.settings, sms_allowed_senders="")):
			result = self.sms(f"Payment received for GHS {PRICE}.00. Reference: {order['name']}.")
		self.assertEqual(result["status"], "disabled")

	def test_wrong_secret_is_refused(self):
		frappe.local.response = frappe._dict()
		self.assertEqual(self.sms("anything", secret="wrong")["status"], "unauthorized")

	def test_paid_is_set_only_after_accounting_finishes(self):
		"""P6: Paid used to be set even when accounting failed."""
		order = self.place()

		with patch.object(api, "_make_and_submit_payment_entry", side_effect=RuntimeError("down")):
			result = self.sms(f"Payment received for GHS {PRICE}.00. Reference: {order['name']}.")

		self.assertEqual(result["status"], "accounting_incomplete")
		self.assertNotEqual(frappe.db.get_value("Sales Order", order["name"], "payment_status"), "Paid")

	def test_claim_needs_the_order_key(self):
		"""P6: any visitor could flip any order to Awaiting."""
		order = self.place()
		with self.assertRaises(frappe.ValidationError):
			momo.submit_momo_claim(order["name"], key="guessed")
		self.assertNotEqual(frappe.db.get_value("Sales Order", order["name"], "payment_status"), momo.STATUS_AWAITING)

		result = momo.submit_momo_claim(order["name"], key=order["url"].rsplit("/", 1)[1], payer_phone="0240000000")

		self.assertEqual(result["status"], "awaiting_confirmation")


class TestPlaceOrder(PaymentTestCase):
	def test_unpriced_item_is_refused(self):
		"""P7: it used to become a free line."""
		with self.assertRaises(frappe.ValidationError):
			self.place({UNPRICED_ITEM_CODE: 1})

	def test_order_gets_a_private_link(self):
		"""P8."""
		order = self.place()
		key = frappe.db.get_value("Sales Order", order["name"], "order_key")
		self.assertGreaterEqual(len(key), 20)
		self.assertEqual(order["url"], f"/order/{order['name']}/{key}")


class TestOrderPrivacy(PaymentTestCase):
	def test_page_needs_the_key(self):
		"""P8: order numbers are sequential, so the name alone must show nothing."""
		order = self.place()
		key = order["url"].rsplit("/", 1)[1]

		for wrong in ("", "guessed"):
			context = open_order_page(order["name"], wrong)
			self.assertTrue(context.needs_link)
			self.assertIsNone(context.order)

		context = open_order_page(order["name"], key)
		self.assertFalse(context.needs_link)
		self.assertEqual(context.order.name, order["name"])
		self.assertEqual(context.order.order_items[0].item_code, ITEM_CODE)

	def test_page_looks_the_same_for_an_order_that_does_not_exist(self):
		context = open_order_page("SAL-ORD-1999-00001", "")
		self.assertTrue(context.needs_link)
		self.assertIsNone(context.order)

	def test_get_order_needs_the_key(self):
		order = self.place()
		with self.assertRaises(frappe.ValidationError):
			api.get_order(order["name"])
		summary = api.get_order(order["name"], key=order["url"].rsplit("/", 1)[1])
		self.assertEqual(summary["name"], order["name"])
		self.assertNotIn("customer", summary)
		self.assertNotIn("contact_email", summary)

	def test_starting_a_payment_needs_the_key(self):
		order = self.place()
		with patch.object(api.requests, "post") as post, self.assertRaises(frappe.ValidationError):
			api.init_paystack_payment(order["name"])
		post.assert_not_called()


def open_order_page(name, key, reference=""):
	frappe.local.form_dict = frappe._dict(order_name=name, order_key=key, reference=reference)
	context = frappe._dict()
	order_page.get_context(context)
	return context
