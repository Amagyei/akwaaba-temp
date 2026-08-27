"""Confirmation page for a placed order. Route: /order/<name>.

Also handles the Paystack return: when Paystack bounces the user back with
?reference=… we verify server-side and, on success, submit the linked SO.
By the time the template renders, the DB state reflects the payment (or the
webhook has already handled it, in which case verify is a no-op).
"""

import frappe

from akwaaba_theme.context import with_shared_context


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False
	with_shared_context(context)

	order_name = (frappe.form_dict.get("order_name") or "").strip()
	reference = (frappe.form_dict.get("reference") or "").strip()

	context.order_name = order_name
	context.currency = "GHS"
	context.order = None
	context.paystack_enabled = False
	context.paystack_brand = "MoMo / Card"
	context.payment_check = None
	context.direct_momo = None

	# If Paystack redirected the user back, run server-side verify first.
	# This idempotently submits the SO if the payment succeeded.
	if reference:
		try:
			from akwaaba_theme.api import verify_paystack_payment
			context.payment_check = verify_paystack_payment(reference)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Paystack verify from detail page failed")
			context.payment_check = {"status": "error"}

	if not order_name:
		context.title = "Order — Serwa Kollections"
		return context

	# Guest visitors can't read Sales Order via ORM; direct SQL bypasses perms
	rows = frappe.db.sql(
		"""
		SELECT name, customer, status, docstatus,
		       grand_total, total, currency, transaction_date,
		       contact_mobile, contact_email, terms,
		       payment_status
		FROM `tabSales Order`
		WHERE name = %s
		LIMIT 1
		""",
		(order_name,),
		as_dict=True,
	)
	if not rows:
		context.title = "Order not found — Serwa Kollections"
		return context

	order = rows[0]
	order["order_items"] = frappe.db.sql(
		"""
		SELECT item_code, item_name, qty, rate, amount
		FROM `tabSales Order Item`
		WHERE parent = %s
		ORDER BY idx ASC
		""",
		(order_name,),
		as_dict=True,
	)

	# Convenience flags for template
	order["is_paid"] = order["docstatus"] == 1
	order["is_draft"] = order["docstatus"] == 0

	# Paystack availability + brand — read via helper so we don't leak the secret
	try:
		from akwaaba_theme.api import _get_payment_settings
		settings = _get_payment_settings() or {}
		context.paystack_enabled = bool(
			settings.get("enabled") and settings.get("secret_key") and not order["is_paid"]
		)
		context.paystack_brand = settings.get("brand_label") or "MoMo / Card"

		# Direct MoMo (manual) — show pay-to instructions when enabled & unpaid.
		if settings.get("direct_momo_enabled") and not order["is_paid"]:
			context.direct_momo = {
				"network": settings.get("momo_network") or "",
				"number": settings.get("momo_number") or "",
				"account_name": settings.get("momo_account_name") or "",
				"instructions": settings.get("momo_instructions") or "",
			}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Paystack settings read failed")

	context.order = order
	context.title = f"Order {order['name']} — Serwa Kollections"
	return context
