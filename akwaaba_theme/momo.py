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

import json

import frappe
from frappe.utils import flt

PAYMENT_STATUS_FIELD = "payment_status"
STATUS_UNPAID = "Unpaid"
STATUS_AWAITING = "Awaiting Confirmation"
STATUS_PAID = "Paid"


# ---------------------------------------------------------------------------
# Customer side — claim a payment (guest allowed)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def submit_momo_claim(order_name, payer_phone=None, txn_id=None):
	"""Customer tells us they've sent the MoMo.

	Moves the order to *Awaiting Confirmation*, records what they claim
	(the number they paid from + the MoMo transaction id from their SMS),
	and pings the admin. Does NOT mark anything paid — money is confirmed
	only by `confirm_momo_payment` (admin) or a matched `momo_sms_inbox`.

	Idempotent: re-submitting just refreshes the claimed details.
	"""
	order_name = (order_name or "").strip()
	if not order_name:
		frappe.throw("No order specified.")

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

@frappe.whitelist()
def confirm_momo_payment(order_name, txn_id=None, amount=None):
	"""Admin confirms the MoMo receipt landed → run the full accounting cycle.

	NOT guest-callable. Requires write permission on Sales Order (enforced by
	the ORM below). Reuses `api._complete_accounting_cycle`, so this produces
	the same SI + PE an automatic payment would. Sets `payment_status = Paid`.

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

	frappe.db.set_value("Sales Order", order_name, PAYMENT_STATUS_FIELD, STATUS_PAID,
	                    update_modified=False)
	_comment(order_name,
	         f"MoMo payment confirmed by {frappe.session.user}. "
	         f"Ref {txn_id}, {row.currency or 'GHS'} {received:,.2f}. "
	         f"Accounting: {', '.join(result.get('notes', [])) or 'n/a'}.")
	frappe.db.commit()

	return dict(result, status="confirmed", order=order_name)


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

@frappe.whitelist(allow_guest=True)
def momo_sms_inbox(secret=None, message=None, sender=None):
	"""Receive a forwarded MoMo SMS and auto-confirm a matching order.

	Point an Android SMS-forwarding app (e.g. "SMS to URL Forwarder",
	MacroDroid, Tasker) at:

	    POST /api/method/akwaaba_theme.momo.momo_sms_inbox
	    { "secret": "<shared secret>", "message": "<full sms text>",
	      "sender": "<sms sender, e.g. MTNMobileMoney>" }

	Matching strategy (deterministic first, then best-effort):
	  1. If the SMS text contains an order name (AKW/SAL-… or the bare SO
	     name), confirm that order.
	  2. Else, if exactly one order is Awaiting Confirmation with a grand
	     total equal to the amount parsed from the SMS, confirm it.
	  3. Else, log for manual review and leave everything Pending.

	Security: gated by a shared `secret` matched against Payment Settings
	(`sms_inbox_secret`). No secret configured ⇒ endpoint refuses.
	"""
	from akwaaba_theme.api import _get_payment_settings

	settings = _get_payment_settings() or {}
	expected = (settings.get("sms_inbox_secret") or "").strip()
	if not expected:
		return {"status": "disabled", "reason": "no sms_inbox_secret configured"}
	if (secret or "").strip() != expected:
		frappe.local.response.http_status_code = 401
		return {"status": "unauthorized"}

	text = message or ""
	if not text.strip():
		return {"status": "ignored", "reason": "empty message"}

	original_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		# --- Strategy 1: explicit order reference in the SMS ---
		order_name = _find_order_ref_in_text(text)

		# --- Strategy 2: unique amount match among pending orders ---
		amount = _parse_amount(text)
		if not order_name and amount:
			order_name = _unique_pending_order_for_amount(amount)

		if not order_name:
			frappe.log_error(
				f"Unmatched MoMo SMS from {sender!r}: {text[:400]}",
				"Akwaaba MoMo SMS unmatched",
			)
			return {"status": "unmatched", "amount": amount}

		result = confirm_momo_payment(order_name, txn_id=_parse_txn_id(text), amount=amount)
		return {"status": "auto_confirmed", "order": order_name, "accounting": result.get("notes")}
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
	try:
		managers = frappe.get_all(
			"Has Role",
			filters={"role": "System Manager", "parenttype": "User"},
			pluck="parent",
		)
		for user in set(managers):
			if user in ("Administrator", "Guest"):
				continue
			frappe.get_doc({
				"doctype": "Notification Log",
				"subject": subject,
				"email_content": body,
				"for_user": user,
				"type": "Alert",
				"document_type": "Sales Order",
				"document_name": order_name,
			}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Akwaaba MoMo: desk notify failed")

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
		frappe.log_error(frappe.get_traceback(), "Akwaaba MoMo: WhatsApp notify failed")


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
	import re
	m = re.search(r"(?:GH[S₵]|GHC|₵)\s*([\d,]+(?:\.\d{1,2})?)", text, re.IGNORECASE)
	if not m:
		return None
	try:
		return float(m.group(1).replace(",", ""))
	except ValueError:
		return None


def _parse_txn_id(text):
	"""Best-effort extraction of a MoMo transaction id (long digit/alnum run)."""
	import re
	m = re.search(r"(?:transaction id|txn id|reference|ref)[:\s]*([A-Za-z0-9]{6,})",
	              text, re.IGNORECASE)
	if m:
		return m.group(1)
	m = re.search(r"\b(\d{8,})\b", text)
	return m.group(1) if m else None


def _find_order_ref_in_text(text):
	"""Return an existing Sales Order name mentioned in the SMS, or None."""
	import re
	for token in re.findall(r"[A-Za-z]{2,}-?\d{2,}[-\d]*", text):
		candidate = token.replace("AKW-", "")
		if frappe.db.exists("Sales Order", candidate):
			return candidate
	return None


def _unique_pending_order_for_amount(amount):
	"""If exactly one Awaiting-Confirmation order matches this amount, return it."""
	rows = frappe.get_all(
		"Sales Order",
		filters={PAYMENT_STATUS_FIELD: STATUS_AWAITING, "docstatus": 0},
		fields=["name", "grand_total", "total"],
	)
	matches = [
		r.name for r in rows
		if abs(flt(r.grand_total or r.total) - flt(amount)) < 0.01
	]
	return matches[0] if len(matches) == 1 else None
