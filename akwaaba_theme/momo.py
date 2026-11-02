"""Direct Mobile Money (manual) payment flow for the Akwaaba storefront.

This is the *no-gateway* model: the customer sends MoMo straight to the shop's
own number, then tells us they've paid. Because no gateway confirms the money
for us, every claim lands in an **Awaiting Confirmation** state and an admin
(or an automated SMS feed — see `momo_sms_inbox`) confirms the receipt before
the order is treated as paid.

State machine (custom field `payment_status` on Sales Order):

    Unpaid  ──customer clicks "I've paid"──►  Awaiting Confirmation
                                                     │
                        admin confirms / SMS matched │
                                                     ▼
                                                   Paid   (SO submitted,
                                                          SI + PE created via
                                                          the shared accounting
                                                          cycle in api.py)

Nothing here touches Paystack; the two models can coexist (Paystack for card,
direct MoMo for those who'd rather send to the number). Confirmation reuses
`api._complete_accounting_cycle`, so a MoMo-confirmed order produces exactly
the same Sales Invoice + Payment Entry an automatic Paystack payment would.
"""

import hmac
import re

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import flt

PAYMENT_STATUS_FIELD = "payment_status"
STATUS_UNPAID = "Unpaid"
STATUS_AWAITING = "Awaiting Confirmation"
STATUS_PAID = "Paid"


# ---------------------------------------------------------------------------
# Customer side — claim a payment (guest allowed)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=60 * 60)
def submit_momo_claim(order_name, key=None, payer_phone=None, txn_id=None):
	"""Customer tells us they've sent the MoMo.

	Moves the order to *Awaiting Confirmation*, records what they claim
	(the number they paid from + the MoMo transaction id from their SMS),
	and pings the admin. Does NOT mark anything paid — money is confirmed
	only by `confirm_momo_payment` (admin) or a matched `momo_sms_inbox`.

	Idempotent: re-submitting just refreshes the claimed details. Needs the
	order's key, so a visitor can't flip orders they've only guessed the name of.
	"""
	from akwaaba_theme.api import order_access_ok

	order_name = (order_name or "").strip()
	if not order_name:
		frappe.throw("No order specified.")
	if not order_access_ok(order_name, key):
		frappe.throw("Order not found.")

	payer_phone = _clean(payer_phone)
	txn_id = _clean(txn_id)

	# Guest session can't read/write Sales Order — escalate for the DB work.
	original_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		row = frappe.db.get_value(
			"Sales Order", order_name,
			["name", "docstatus", "grand_total", "total", "currency"],
			as_dict=True,
		)
		if not row:
			frappe.throw("Order not found.")
		if row.docstatus == 1:
			# Already paid/submitted — nothing to claim.
			return {"status": "already_paid", "order": order_name}
		if row.docstatus == 2:
			frappe.throw("This order was cancelled, so it can't be paid.")

		frappe.db.set_value("Sales Order", order_name, {
			PAYMENT_STATUS_FIELD: STATUS_AWAITING,
			"momo_payer_phone": payer_phone or "",
			"momo_txn_id": txn_id or "",
		}, update_modified=True)
		frappe.db.commit()

		_notify_admin_pending(order_name, row, payer_phone, txn_id)
		return {"status": "awaiting_confirmation", "order": order_name}
	finally:
		frappe.set_user(original_user)


# ---------------------------------------------------------------------------
# Admin side — confirm / reject a claimed payment
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def confirm_momo_payment(order_name, txn_id=None, amount=None):
	"""Admin confirms the MoMo receipt landed → run the full accounting cycle.

	NOT guest-callable. Requires write permission on Sales Order (enforced by
	the ORM below). Reuses `api._complete_accounting_cycle`, so this produces
	the same SI + PE an automatic payment would. Sets `payment_status = Paid`
	only once that cycle finishes; until then the order stays Awaiting.

	`amount` lets the admin record what actually landed if it differs from the
	order total (partials still submit the SO but the mismatch is noted).
	"""
	order_name = (order_name or "").strip()
	if not order_name:
		frappe.throw("No order specified.")

	# Permission gate — throws if the caller can't write Sales Orders.
	if not frappe.has_permission("Sales Order", "write", doc=order_name):
		frappe.throw("You are not allowed to confirm payments.", frappe.PermissionError)

	row = frappe.db.get_value(
		"Sales Order", order_name,
		["name", "docstatus", "grand_total", "total", "currency", "momo_txn_id"],
		as_dict=True,
	)
	if not row:
		frappe.throw("Order not found.")
	if row.docstatus == 2:
		frappe.throw("This order was cancelled, so it can't be confirmed as paid.")

	txn_id = _clean(txn_id) or row.get("momo_txn_id") or f"MOMO-{order_name}"
	received = flt(amount) if amount not in (None, "") else flt(row.grand_total or row.total)

	from akwaaba_theme.api import _complete_accounting_cycle

	# Synthetic gateway payload the accounting cycle understands. `amount` is
	# in minor units (pesewas) exactly like Paystack sends it.
	synthetic = {
		"channel": "mobile_money",
		"amount": int(round(received * 100)),
		"currency": row.currency or "GHS",
	}
	result = _complete_accounting_cycle(order_name, txn_id, synthetic)

	if result.get("ok"):
		frappe.db.set_value("Sales Order", order_name, PAYMENT_STATUS_FIELD, STATUS_PAID,
		                    update_modified=False)
	_comment(order_name,
	         f"MoMo payment {'confirmed' if result.get('ok') else 'received, accounting not finished,'} "
	         f"by {frappe.session.user}. "
	         f"Ref {txn_id}, {row.currency or 'GHS'} {received:,.2f}. "
	         f"Accounting: {', '.join(result.get('notes', [])) or 'n/a'}.")
	frappe.db.commit()

	return dict(result, status="confirmed" if result.get("ok") else "accounting_incomplete", order=order_name)


@frappe.whitelist()
def reject_momo_payment(order_name, reason=None):
	"""Admin couldn't find the receipt → send the order back to Unpaid.

	Leaves an audit comment. The customer's confirmation page will again show
	the pay-instructions so they can retry / send proof.
	"""
	order_name = (order_name or "").strip()
	if not order_name:
		frappe.throw("No order specified.")
	if not frappe.has_permission("Sales Order", "write", doc=order_name):
		frappe.throw("You are not allowed to reject payments.", frappe.PermissionError)

	reason = _clean(reason) or "no reason given"
	frappe.db.set_value("Sales Order", order_name, PAYMENT_STATUS_FIELD, STATUS_UNPAID,
	                    update_modified=True)
	_comment(order_name, f"MoMo payment claim rejected by {frappe.session.user}: {reason}")
	frappe.db.commit()
	return {"status": "rejected", "order": order_name}


# ---------------------------------------------------------------------------
# Automation hook — SMS forwarding inbox (optional, closes the manual gap)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True, methods=["POST"])
def momo_sms_inbox(secret=None, message=None, sender=None):
	"""Receive a forwarded MoMo SMS and auto-confirm a matching order.

	Point an Android SMS-forwarding app (e.g. "SMS to URL Forwarder",
	MacroDroid, Tasker) at:

	    POST /api/method/akwaaba_theme.momo.momo_sms_inbox
	    { "secret": "<shared secret>", "message": "<full sms text>",
	      "sender": "<sms sender, e.g. MTNMobileMoney>" }

	Confirms only when the SMS names the order and shows at least its full
	total. Anything else is logged for manual review and left Pending. Amounts
	alone never match: with a small price list, two orders share a total all
	the time.

	Security: gated by a shared `secret` matched against Payment Settings
	(`sms_inbox_secret`) and by `sms_allowed_senders`, because any phone can
	text the forwarding phone. Either one unset ⇒ endpoint refuses.
	"""
	from akwaaba_theme.api import _get_payment_settings

	settings = _get_payment_settings() or {}
	expected = (settings.get("sms_inbox_secret") or "").strip()
	if not expected:
		return {"status": "disabled", "reason": "no sms_inbox_secret configured"}
	if not hmac.compare_digest((secret or "").strip(), expected):
		frappe.local.response.http_status_code = 401
		return {"status": "unauthorized"}

	allowed = {s.strip().lower() for s in (settings.get("sms_allowed_senders") or "").split(",") if s.strip()}
	if not allowed:
		return {"status": "disabled", "reason": "no sms_allowed_senders configured"}
	if (sender or "").strip().lower() not in allowed:
		frappe.log_error(title="Akwaaba MoMo SMS sender refused", message=f"MoMo SMS from a sender not on the list: {sender!r}")
		return {"status": "ignored", "reason": "sender not allowed"}

	text = message or ""
	if not text.strip():
		return {"status": "ignored", "reason": "empty message"}

	original_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		order_name = _find_order_ref_in_text(text)
		amount = _parse_amount(text)

		if not order_name:
			frappe.log_error(
				title="Akwaaba MoMo SMS unmatched",
				message=f"Unmatched MoMo SMS from {sender!r}: {text[:400]}",
			)
			return {"status": "unmatched", "amount": amount}

		row = frappe.db.get_value("Sales Order", order_name, ["docstatus", "grand_total", "total"], as_dict=True)
		total = flt(row.grand_total or row.total)
		if row.docstatus != 0 or not amount or amount < total - 0.01:
			frappe.log_error(
				title="Akwaaba MoMo SMS needs review",
				message=f"MoMo SMS for {order_name} needs a person: docstatus {row.docstatus}, "
				f"SMS amount {amount}, order total {total}. From {sender!r}: {text[:400]}",
			)
			return {"status": "needs_review", "order": order_name, "amount": amount}

		result = confirm_momo_payment(order_name, txn_id=_parse_txn_id(text), amount=amount)
		return {"status": "auto_confirmed" if result.get("ok") else "accounting_incomplete",
		        "order": order_name, "accounting": result.get("notes")}
	finally:
		frappe.set_user(original_user)


# ---------------------------------------------------------------------------
# Admin notification
# ---------------------------------------------------------------------------

def _notify_admin_pending(order_name, order_row, payer_phone, txn_id):
	"""Alert admins that a MoMo payment needs confirming.

	Layered so at least one channel always lands:
	  1. Notification Log  — desk bell for every System Manager (reliable)
	  2. SO comment        — permanent audit trail on the order
	  3. WhatsApp (best-effort) — to `admin_notify_phone` if configured and
	     the frappe_whatsapp app is installed. Free-form WhatsApp only
	     delivers inside a 24h session window, so this is a bonus, not the
	     primary alert.
	"""
	from akwaaba_theme.api import notify_managers

	total = flt(order_row.grand_total or order_row.total)
	ccy = order_row.currency or "GHS"
	subject = f"MoMo payment to confirm · {order_name}"
	body = (
		f"Order <b>{order_name}</b> — customer says they've sent "
		f"<b>{ccy} {total:,.2f}</b> by MoMo.<br>"
		f"Paid from: {payer_phone or '—'}<br>"
		f"MoMo txn id: {txn_id or '—'}<br>"
		f"Open the order and click <b>Confirm MoMo Payment</b> once you see "
		f"the money in your MoMo."
	)

	# 1. Desk notification to every System Manager
	notify_managers(subject, body, order_name)

	# 2. Comment on the order
	_comment(order_name,
	         f"Customer submitted a MoMo payment claim. Paid from "
	         f"{payer_phone or '—'}, txn {txn_id or '—'}. Awaiting confirmation.")

	# 3. WhatsApp (best-effort)
	try:
		from akwaaba_theme.api import _get_payment_settings
		notify_phone = ((_get_payment_settings() or {}).get("admin_notify_phone") or "").strip()
		if notify_phone and frappe.db.exists("DocType", "WhatsApp Message"):
			frappe.get_doc({
				"doctype": "WhatsApp Message",
				"to": notify_phone,
				"message_type": "Manual",
				"content_type": "text",
				"message": (
					f"MoMo to confirm: {order_name} — {ccy} {total:,.2f}. "
					f"From {payer_phone or '?'}, txn {txn_id or '?'}."
				),
			}).insert(ignore_permissions=True)
	except Exception:
		# WhatsApp is a bonus channel; never let it break the claim flow.
		frappe.log_error(title="Akwaaba MoMo: WhatsApp notify failed", message=frappe.get_traceback())


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _comment(order_name, text):
	try:
		frappe.get_doc({
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "Sales Order",
			"reference_name": order_name,
			"content": text,
		}).insert(ignore_permissions=True)
	except Exception:
		pass


def _clean(s):
	if s is None:
		return ""
	return " ".join(str(s).split()).strip()


def _parse_amount(text):
	"""Pull the GHS amount out of a MoMo SMS. Returns float or None.

	Handles the common Ghana MoMo phrasings, e.g.
	  'Payment received for GHS 250.00 from ...'
	  'You have received GHS1,250.00 from ...'
	"""
	m = re.search(r"(?:GH[S₵]|GHC|₵)\s*([\d,]+(?:\.\d{1,2})?)", text, re.IGNORECASE)
	if not m:
		return None
	try:
		return float(m.group(1).replace(",", ""))
	except ValueError:
		return None


def _parse_txn_id(text):
	"""Best-effort extraction of a MoMo transaction id (long digit/alnum run)."""
	m = re.search(r"(?:transaction id|txn id|reference|ref)[:\s]*([A-Za-z0-9]{6,})",
	              text, re.IGNORECASE)
	if m:
		return m.group(1)
	m = re.search(r"\b(\d{8,})\b", text)
	return m.group(1) if m else None


def _find_order_ref_in_text(text):
	"""Return an existing Sales Order name mentioned in the SMS, or None."""
	for token in re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+", text):
		candidate = token[4:] if token.upper().startswith("AKW-") else token
		name = frappe.db.exists("Sales Order", candidate)
		if name:
			return name
	return None
