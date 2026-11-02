"""Whitelisted endpoints for the Akwaaba storefront.

- get_card_videos              : hover-video map for product cards
- place_order                  : the guest checkout endpoint
- get_order                    : light order status lookup, needs the order's key
- subscribe_newsletter         : homepage "Ntoma list" signup → Email Group
- init_paystack_payment        : creates a Paystack transaction, returns checkout URL
- verify_paystack_payment      : server-side verification of a Paystack reference
- paystack_webhook             : Paystack event receiver (charge.success, etc.)
- retry_unsettled_orders       : daily job, finishes paid orders missing records
"""

import hashlib
import hmac
import json
import re

import frappe
import requests
from frappe.rate_limiter import rate_limit
from frappe.utils import flt


DEFAULT_PRICE_LIST = "Akwaaba Retail"


# ---------------------------------------------------------------------------
# Product card hover videos
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def get_card_videos():
	"""Return published Website Items with a hover video configured.

	Called from public/js/akwaaba.js on every website page.
	"""
	if not frappe.db.exists("Custom Field", "Website Item-hover_video_url"):
		return []

	rows = frappe.db.sql(
		"""
		SELECT
			wi.item_code, wi.route,
			wi.hover_video_url, wi.hover_video_poster_url,
			COALESCE(NULLIF(wi.website_image, ''), i.image) AS website_image
		FROM `tabWebsite Item` wi
		LEFT JOIN `tabItem` i ON i.item_code = wi.item_code
		WHERE wi.published = 1
		  AND wi.hover_video_url IS NOT NULL
		  AND wi.hover_video_url != ''
		""",
		as_dict=True,
	)
	return rows


@frappe.whitelist(allow_guest=True)
def get_variant_options(template_item_code):
	"""Return the size variants for a template item.

	Fallback path — the shop grid already embeds this data server-side. Kept
	whitelisted so future surfaces (PDP, cart, admin previews) can call it
	without duplicating the SQL. Returns [] if the item isn't a variant
	template or has no Size attribute.
	"""
	if not template_item_code:
		return []
	rows = frappe.db.sql(
		"""
		SELECT
			wi.item_code AS code,
			iva.attribute_value AS size,
			ip.price_list_rate AS price,
			wi.on_backorder AS oos
		FROM `tabWebsite Item` wi
		JOIN `tabItem Variant Attribute` iva
			ON iva.parent = wi.item_code AND iva.attribute = 'Size'
		LEFT JOIN `tabItem Price` ip
			ON ip.item_code = wi.item_code
			AND ip.price_list = %(pl)s
			AND ip.selling = 1
		WHERE wi.variant_of = %(tpl)s AND wi.published = 1
		""",
		{"pl": DEFAULT_PRICE_LIST, "tpl": template_item_code},
		as_dict=True,
	)
	return rows


# ---------------------------------------------------------------------------
# Guest checkout — place_order
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=60 * 60)
def place_order(items, full_name, phone, email=None, address_line=None,
                city=None, region=None, notes=None):
	"""Create a Customer (if new) + Sales Order from the guest cart.

	`items` is a JSON string { item_code: qty }.
	Returns { name, url, total, currency, customer }; the frontend redirects
	to `url`, the order's private link.

	The order is created in Draft state (not submitted). Admin reviews it
	in ERPNext, then submits when payment is confirmed. Submission triggers
	the WhatsApp notification (see hooks.py doc_events).
	"""
	items = _parse_items(items)
	if not items:
		frappe.throw("Your cart is empty.", frappe.ValidationError)

	full_name = _clean(full_name)
	phone = _clean_phone(phone)
	email = _clean(email)
	if not full_name:
		frappe.throw("Please tell us your name.", frappe.ValidationError)
	if not phone:
		frappe.throw("Please add a WhatsApp phone number.", frappe.ValidationError)
	if not email:
		frappe.throw("Please add an email so we can send your order updates.",
		             frappe.ValidationError)
	# Reuse Frappe's validator; raises a clean message on a malformed address.
	from frappe.utils import validate_email_address
	validate_email_address(email, throw=True)
	if not address_line or not city:
		frappe.throw("Please add a delivery address.", frappe.ValidationError)

	# Public endpoint — the anonymous session lacks perms to read Account,
	# Customer, etc. Escalate to Administrator for the DB work only, then
	# restore the caller. Order is still tagged with the original session
	# email in the log via frappe.log_error if anything explodes.
	original_user = frappe.session.user
	frappe.set_user("Administrator")
	frappe.flags.ignore_permissions = True
	try:
		return _place_order_impl(items, full_name, phone, email,
		                        address_line, city, region, notes)
	finally:
		frappe.set_user(original_user)


def _place_order_impl(items, full_name, phone, email, address_line, city, region, notes):
	customer = _ensure_customer(full_name, phone, email)
	address = _ensure_customer_address(customer, full_name, phone,
	                                   address_line, city, region)

	so = frappe.new_doc("Sales Order")
	so.customer = customer
	so.transaction_date = frappe.utils.today()
	so.delivery_date = frappe.utils.add_days(frappe.utils.today(), 7)
	so.order_type = "Sales"
	so.currency = "GHS"
	so.selling_price_list = DEFAULT_PRICE_LIST
	so.order_key = frappe.generate_hash(length=24)
	if _has_field("Sales Order", "customer_address") and address:
		so.customer_address = address
	if _has_field("Sales Order", "shipping_address_name") and address:
		so.shipping_address_name = address
	if _has_field("Sales Order", "contact_mobile"):
		so.contact_mobile = phone
	if _has_field("Sales Order", "contact_email") and email:
		so.contact_email = email
	if notes:
		so.terms = notes

	for code, qty in items.items():
		wi = frappe.db.get_value(
			"Website Item",
			{"item_code": code, "published": 1},
			["item_code", "web_item_name", "item_name"],
			as_dict=True,
		)
		if not wi:
			frappe.throw(f"Item {code} is no longer available.")
		price = flt(_lookup_price(code))
		if price <= 0:
			frappe.throw(f"{wi.web_item_name or code} can't be ordered online right now. Please message us.")
		so.append("items", {
			"item_code": code,
			"item_name": wi.web_item_name or wi.item_name or code,
			"qty": qty,
			"rate": price,
			"delivery_date": so.delivery_date,
		})

	so.flags.ignore_mandatory = True
	try:
		so.insert(ignore_permissions=True)
	except Exception as e:
		frappe.log_error(title="Akwaaba place_order failed", message=frappe.get_traceback())
		frappe.throw(
			"We couldn't create your order. Please try again or contact us on WhatsApp.",
			frappe.ValidationError,
		)

	# Optionally submit immediately (uncomment if you want auto-submit).
	# so.submit()

	frappe.db.commit()

	return {
		"name": so.name,
		"url": order_path(so.name, so.order_key),
		"total": so.grand_total or so.total,
		"currency": so.currency,
		"customer": customer,
	}


# ---------------------------------------------------------------------------
# Order lookup (used by /order/<name>)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def get_order(name, key=None):
	"""Return a small, sanitized order summary for the confirmation page.

	Only returns totals + item list, and only with the order's key: order names
	are sequential, so anyone could otherwise walk SAL-ORD-…-00001, 00002, ….
	"""
	if not name:
		frappe.throw("No order specified.")
	if not order_access_ok(name, key):
		frappe.throw("Order not found.")

	so = frappe.db.get_value(
		"Sales Order", name,
		[
			"name", "status", "docstatus",
			"grand_total", "total", "currency", "transaction_date",
		],
		as_dict=True,
	)
	if not so:
		frappe.throw("Order not found.")

	items = frappe.get_all(
		"Sales Order Item",
		filters={"parent": name},
		fields=["item_code", "item_name", "qty", "rate", "amount"],
		order_by="idx asc",
	)
	so["items"] = items
	return so


# ---------------------------------------------------------------------------
# Newsletter — "Join the Ntoma list" on the homepage
# ---------------------------------------------------------------------------

NEWSLETTER_GROUP = "Ntoma List"


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=5, seconds=60 * 60)
def subscribe_newsletter(email):
	"""Add an email to the Ntoma list, an Email Group admins manage in Desk.

	An address that unsubscribed earlier is switched back on; one already on
	the list is a quiet success, so the form never reveals who is subscribed.
	"""
	from frappe.utils import validate_email_address

	email = validate_email_address(_clean(email).lower(), throw=True)

	if not frappe.db.exists("Email Group", NEWSLETTER_GROUP):
		frappe.get_doc({"doctype": "Email Group", "title": NEWSLETTER_GROUP}).insert(ignore_permissions=True)

	member = frappe.db.get_value(
		"Email Group Member",
		{"email_group": NEWSLETTER_GROUP, "email": email},
		["name", "unsubscribed"],
		as_dict=True,
	)
	if member:
		if member.unsubscribed:
			frappe.db.set_value("Email Group Member", member.name, "unsubscribed", 0)
		return {"ok": True}

	try:
		frappe.get_doc({
			"doctype": "Email Group Member",
			"email_group": NEWSLETTER_GROUP,
			"email": email,
		}).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		pass  # a double-tap raced us to the same row
	return {"ok": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def order_path(name, key):
	"""An order's private link. Order names are guessable; the key isn't."""
	return f"/order/{name}/{key}" if key else f"/order/{name}"


def order_access_ok(name, key):
	"""May this caller see the order: its private key, or Desk rights on it."""
	if not name or not frappe.db.exists("Sales Order", name):
		return False
	stored = frappe.db.get_value("Sales Order", name, "order_key")
	if stored and key and hmac.compare_digest(str(stored), str(key)):
		return True
	return frappe.session.user != "Guest" and bool(frappe.has_permission("Sales Order", "read", doc=name))


def _parse_items(raw):
	if isinstance(raw, dict):
		data = raw
	else:
		try:
			data = json.loads(raw)
		except Exception:
			return {}
	out = {}
	for k, v in (data or {}).items():
		try:
			qty = int(v)
		except (TypeError, ValueError):
			continue
		if qty > 0 and isinstance(k, str) and k.strip():
			out[k.strip()] = qty
	return out


def _clean(s):
	if not s:
		return ""
	return re.sub(r"\s+", " ", str(s)).strip()[:140]


def _clean_phone(s):
	if not s:
		return ""
	return re.sub(r"[^0-9+]", "", str(s))[:20]


def _has_field(doctype, fieldname):
	try:
		meta = frappe.get_meta(doctype)
		return meta.has_field(fieldname)
	except Exception:
		return False


def _lookup_price(item_code):
	price = frappe.db.get_value(
		"Item Price",
		{"item_code": item_code, "price_list": DEFAULT_PRICE_LIST, "selling": 1},
		"price_list_rate",
	)
	return price or 0


def _ensure_customer(full_name, phone, email):
	"""Find-or-create a Customer keyed on the phone number.

	Behavior:
	- If a Customer already exists with this phone (via Contact), reuse it
	- Otherwise create Customer + Contact under the "Akwaaba Guests" group
	"""
	_ensure_customer_group("Akwaaba Guests")

	if phone:
		existing = frappe.db.sql(
			"""
			SELECT dl.link_name
			FROM `tabContact` c
			JOIN `tabContact Phone` cp ON cp.parent = c.name
			JOIN `tabDynamic Link` dl
				ON dl.parent = c.name
				AND dl.link_doctype = 'Customer'
			WHERE cp.phone = %(p)s OR c.mobile_no = %(p)s
			LIMIT 1
			""",
			{"p": phone},
			as_dict=True,
		)
		if existing:
			return existing[0]["link_name"]

	if email:
		existing = frappe.db.sql(
			"""
			SELECT dl.link_name
			FROM `tabContact` c
			JOIN `tabContact Email` ce ON ce.parent = c.name
			JOIN `tabDynamic Link` dl
				ON dl.parent = c.name
				AND dl.link_doctype = 'Customer'
			WHERE ce.email_id = %(e)s
			LIMIT 1
			""",
			{"e": email},
			as_dict=True,
		)
		if existing:
			return existing[0]["link_name"]

	# Create fresh customer
	c = frappe.new_doc("Customer")
	c.customer_name = full_name
	c.customer_type = "Individual"
	c.customer_group = "Akwaaba Guests"
	c.territory = "All Territories"
	c.flags.ignore_mandatory = True
	c.insert(ignore_permissions=True)

	# Attach Contact
	try:
		ct = frappe.new_doc("Contact")
		ct.first_name = full_name.split(" ", 1)[0]
		if " " in full_name:
			ct.last_name = full_name.split(" ", 1)[1]
		if email:
			ct.append("email_ids", {"email_id": email, "is_primary": 1})
		if phone:
			ct.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
		ct.mobile_no = phone
		ct.append("links", {"link_doctype": "Customer", "link_name": c.name})
		ct.flags.ignore_mandatory = True
		ct.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Akwaaba: contact create failed", message=frappe.get_traceback())

	return c.name


def _ensure_customer_group(name):
	if not frappe.db.exists("Customer Group", name):
		frappe.get_doc({
			"doctype": "Customer Group",
			"customer_group_name": name,
			"parent_customer_group": "All Customer Groups",
			"is_group": 0,
		}).insert(ignore_permissions=True)


# ===========================================================================
# Paystack — server-side init, verify, webhook
# ===========================================================================

PAYSTACK_BASE = "https://api.paystack.co"


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=60 * 60)
def init_paystack_payment(order_name, key=None):
	"""Ask Paystack to initialize a transaction for the Sales Order.

	Returns { authorization_url, reference }. Frontend redirects to
	authorization_url; Paystack hosts the MoMo/card modal, then bounces the
	user back to the order's link with ?reference=<ref>, which triggers verify.
	"""
	if not order_name or not order_access_ok(order_name, key):
		frappe.throw("Order not found.", frappe.ValidationError)

	settings = _get_payment_settings()
	if not settings or not settings.get("enabled") or not settings.get("secret_key"):
		frappe.throw("Payments aren't configured yet. Please contact us.", frappe.ValidationError)

	# Load order (Guest → direct SQL, no perms)
	rows = frappe.db.sql(
		"""
		SELECT name, customer, docstatus, grand_total, total, currency,
		       contact_mobile, contact_email, order_key
		FROM `tabSales Order`
		WHERE name = %s
		LIMIT 1
		""",
		(order_name,), as_dict=True,
	)
	if not rows:
		frappe.throw("Order not found.", frappe.ValidationError)
	order = rows[0]

	if order.docstatus == 1:
		# Already submitted → treat as paid
		return {
			"already_paid": True,
			"authorization_url": None,
			"reference": None,
		}
	if order.docstatus == 2:
		frappe.throw("This order was cancelled, so it can't be paid.", frappe.ValidationError)

	amount = flt(order.grand_total or order.total or 0)
	if amount <= 0:
		frappe.throw("Order total is zero.", frappe.ValidationError)

	# Paystack amounts are integers in the subunit (kobo/pesewas)
	amount_subunit = int(round(amount * 100))
	currency = (order.currency or "GHS").upper()
	if currency not in ("GHS", "NGN", "ZAR", "USD", "KES"):
		currency = "GHS"

	email = order.contact_email or "guest@akwaaba.gh"

	# A new reference per attempt: Paystack refuses a reference it has seen, so
	# a fixed one would block paying again after an abandoned or failed try.
	reference = f"AKW-{order.name}--{frappe.generate_hash(length=8)}"

	callback_url = frappe.utils.get_url(order_path(order.name, order.order_key))

	channels = ["card", "mobile_money"] if currency == "GHS" else ["card"]

	payload = {
		"email": email,
		"amount": amount_subunit,
		"currency": currency,
		"reference": reference,
		"callback_url": callback_url,
		"channels": channels,
		"metadata": {
			"order_name": order.name,
			"customer": order.customer,
			"phone": order.contact_mobile or "",
			"source": "akwaaba-storefront",
		},
	}

	try:
		r = requests.post(
			f"{PAYSTACK_BASE}/transaction/initialize",
			json=payload,
			headers={
				"Authorization": f"Bearer {settings['secret_key']}",
				"Content-Type": "application/json",
			},
			timeout=15,
		)
	except requests.RequestException as e:
		frappe.log_error(title="Paystack init request failed", message=str(e))
		frappe.throw("Payment provider is unreachable. Please try again.", frappe.ValidationError)

	if not r.ok:
		frappe.log_error(title=f"Paystack init HTTP {r.status_code} for {order.name}", message=r.text[:2000])
		frappe.throw("Payment provider rejected the request. Try again in a moment.", frappe.ValidationError)

	body = r.json() or {}
	if not body.get("status") or not body.get("data", {}).get("authorization_url"):
		frappe.log_error(title=f"Paystack init bad body for {order.name}", message=json.dumps(body)[:2000])
		frappe.throw("Payment provider returned an unexpected response.", frappe.ValidationError)

	return {
		"authorization_url": body["data"]["authorization_url"],
		"reference": body["data"].get("reference") or reference,
	}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=30, seconds=60 * 60)
def verify_paystack_payment(reference):
	"""Public wrapper around verify_payment; see there."""
	return verify_payment(reference)


def verify_payment(reference):
	"""Verify a Paystack transaction. Robust across all outcome states.

	Returns { status, order, reason?, expected_amount?, actual_amount? }
	Status values:
	  - success            → paid; `ok` says whether the accounting finished too
	  - already_completed  → paid and every record exists; idempotent no-op
	  - cancelled          → order was cancelled; any money needs refunding
	  - failed             → Paystack reports failed
	  - abandoned          → user closed modal
	  - pending            → async MoMo, wait for webhook
	  - amount_mismatch    → response amount does not equal SO grand_total
	  - currency_mismatch  → response currency does not equal SO currency
	  - not_found          → reference does not map to a known SO
	  - not_configured     → Paystack not set up on this site
	  - error              → network / provider bug
	"""
	if not reference:
		return {"status": "error", "reason": "no_reference"}

	settings = _get_payment_settings()
	if not settings or not settings.get("secret_key"):
		return {"status": "not_configured"}

	order_name = _reference_to_order(reference)
	so = None
	if order_name and frappe.db.exists("Sales Order", order_name):
		so = frappe.db.get_value(
			"Sales Order", order_name,
			["name", "docstatus", "grand_total", "total", "currency", "payment_reference"],
			as_dict=True,
		)

	# Idempotent short-circuit, but only once every record exists: a paid order
	# missing its invoice or payment falls through so this call can finish it.
	settled = bool(so and so.docstatus == 1 and _accounting_settled(order_name, settings))
	if settled and so.payment_reference in (None, "", reference):
		return {"status": "already_completed", "order": order_name}

	try:
		r = requests.get(
			f"{PAYSTACK_BASE}/transaction/verify/{reference}",
			headers={"Authorization": f"Bearer {settings['secret_key']}"},
			timeout=15,
		)
	except requests.RequestException as e:
		frappe.log_error(title=f"Paystack verify network error for {reference}", message=str(e))
		return {"status": "error", "reason": "network"}

	if not r.ok:
		frappe.log_error(title=f"Paystack verify HTTP {r.status_code} for {reference}", message=r.text[:1500])
		return {"status": "error", "reason": f"http_{r.status_code}"}

	body = r.json() or {}
	data = body.get("data") or {}
	psk_status = (data.get("status") or "").lower()

	if not so:
		return {"status": "not_found", "reason": "no_matching_so"}

	if _metadata_order(data) not in (None, order_name):
		frappe.log_error(title="Paystack order mismatch",
		                 message=f"Reference {reference} names {order_name}; Paystack metadata names {_metadata_order(data)}")
		return {"status": "not_found", "reason": "order_mismatch"}

	_log_paystack_attempt(order_name, reference, psk_status, data)

	if psk_status != "success":
		# Failed / abandoned / pending / unknown → return raw so UI can react
		return {
			"status": psk_status or "pending",
			"order": order_name,
			"reason": data.get("gateway_response") or "not_success",
		}

	if so.docstatus == 2:
		_flag_for_refund(order_name, reference, data, "was cancelled before the money arrived")
		return {"status": "cancelled", "order": order_name}

	# ---- SUCCESS PATH: hard-check amount + currency before we do accounting ----
	expected = float(so.grand_total or so.total or 0)
	expected_subunit = int(round(expected * 100))
	actual_subunit = int(data.get("amount") or 0)
	actual_currency = (data.get("currency") or "").upper()
	expected_currency = (so.currency or "GHS").upper()

	if actual_currency and expected_currency and actual_currency != expected_currency:
		frappe.log_error(
			title="Paystack currency mismatch",
			message=f"Order {order_name}: currency mismatch. expected={expected_currency} actual={actual_currency}. "
			f"Reference={reference}. Refusing to complete accounting.",
		)
		return {
			"status": "currency_mismatch",
			"order": order_name,
			"expected_currency": expected_currency,
			"actual_currency": actual_currency,
		}

	if actual_subunit != expected_subunit:
		frappe.log_error(
			title="Paystack amount mismatch",
			message=f"Order {order_name}: amount mismatch. expected={expected_subunit} actual={actual_subunit}. "
			f"Reference={reference}. Refusing to complete accounting.",
		)
		return {
			"status": "amount_mismatch",
			"order": order_name,
			"expected_amount": expected / 1,
			"actual_amount": actual_subunit / 100,
		}

	if settled:
		# Everything's recorded under another reference, so this is a second payment.
		_flag_for_refund(order_name, reference, data, "was already paid under another reference")
		return {"status": "already_completed", "order": order_name}

	# All checks pass — run the accounting cycle
	result = _complete_accounting_cycle(order_name, reference, data)
	return dict(result, status="cancelled" if result.get("cancelled") else "success", order=order_name)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def paystack_webhook():
	"""Paystack event webhook. Verifies HMAC-SHA512 signature, then handles
	charge.success by submitting the linked Sales Order.

	Answers 500 while the order's records are incomplete: Paystack resends on
	any failure, and each resend retries the missing steps.
	"""
	settings = _get_payment_settings()
	if not settings or not settings.get("secret_key"):
		return {"ok": False, "reason": "not_configured"}

	raw = frappe.request.get_data() or b""
	signature = frappe.get_request_header("X-Paystack-Signature") or ""

	webhook_secret = settings.get("webhook_secret") or settings["secret_key"]

	expected = hmac.new(
		key=webhook_secret.encode("utf-8"),
		msg=raw,
		digestmod=hashlib.sha512,
	).hexdigest()

	if not hmac.compare_digest(expected, signature):
		frappe.local.response.http_status_code = 401
		return {"ok": False, "reason": "bad_signature"}

	try:
		event = json.loads(raw.decode("utf-8"))
	except Exception:
		return {"ok": False, "reason": "bad_json"}

	event_type = event.get("event") or ""
	data = event.get("data") or {}
	reference = data.get("reference") or ""

	if event_type == "charge.success" and reference:
		result = _handle_charge_success(reference, data, settings)
		if not result.get("ok", True):
			frappe.local.response.http_status_code = 500
			return {"ok": False, "reason": "accounting_incomplete"}

	return {"ok": True}


def _handle_charge_success(reference, data, settings):
	order_name = _metadata_order(data) or _reference_to_order(reference)
	if not order_name or not frappe.db.exists("Sales Order", order_name):
		return {"ok": True}

	# Amount/currency guard here too — webhook can't be less strict than verify
	so = frappe.db.get_value(
		"Sales Order", order_name,
		["docstatus", "grand_total", "total", "currency", "payment_reference"],
		as_dict=True,
	)
	if so.docstatus == 2:
		_flag_for_refund(order_name, reference, data, "was cancelled before the money arrived")
		return {"ok": True}

	expected_subunit = int(round(float(so.grand_total or so.total or 0) * 100))
	actual_subunit = int(data.get("amount") or 0)
	expected_ccy = (so.currency or "GHS").upper()
	actual_ccy = (data.get("currency") or "").upper()
	if actual_subunit != expected_subunit or (actual_ccy and actual_ccy != expected_ccy):
		frappe.log_error(
			title=f"Paystack webhook mismatch {order_name}",
			message=f"Webhook rejected: amount/currency mismatch. "
			f"expected={expected_subunit} {expected_ccy}, "
			f"actual={actual_subunit} {actual_ccy}",
		)
		return {"ok": True}

	if so.docstatus == 1 and _accounting_settled(order_name, settings):
		if so.payment_reference not in (None, "", reference):
			_flag_for_refund(order_name, reference, data, "was already paid under another reference")
		return {"ok": True}

	_log_paystack_attempt(order_name, reference, "success", data)
	return _complete_accounting_cycle(order_name, reference, data)


# ---------------------------------------------------------------------------
# Paystack helpers
# ---------------------------------------------------------------------------

def _get_payment_settings():
	"""Load Akwaaba Payment Settings with the secret decrypted."""
	if not frappe.db.exists("DocType", "Akwaaba Payment Settings"):
		return None
	try:
		doc = frappe.get_cached_doc("Akwaaba Payment Settings", "Akwaaba Payment Settings")
	except Exception:
		return None
	return {
		"enabled": bool(doc.enabled),
		"environment": doc.environment or "Test",
		"public_key": doc.public_key or "",
		"secret_key": (doc.get_password("secret_key", raise_exception=False) or "") if doc.secret_key else "",
		"webhook_secret": (doc.get_password("webhook_secret", raise_exception=False) or "") if doc.webhook_secret else "",
		"brand_label": doc.brand_label or "MoMo / Card",
		# Accounting mapping
		"company": doc.get("company") or "",
		"auto_complete_accounting": bool(doc.get("auto_complete_accounting")),
		"momo_account": doc.get("momo_account") or "",
		"card_account": doc.get("card_account") or "",
		"momo_mode_of_payment": doc.get("momo_mode_of_payment") or "",
		"card_mode_of_payment": doc.get("card_mode_of_payment") or "",
		# Direct MoMo (manual, no gateway) — see momo.py
		"direct_momo_enabled": bool(doc.get("direct_momo_enabled")),
		"momo_network": doc.get("momo_network") or "",
		"momo_number": doc.get("momo_number") or "",
		"momo_account_name": doc.get("momo_account_name") or "",
		"momo_instructions": doc.get("momo_instructions") or "",
		"admin_notify_phone": doc.get("admin_notify_phone") or "",
		"sms_inbox_secret": (doc.get_password("sms_inbox_secret", raise_exception=False) or "") if doc.get("sms_inbox_secret") else "",
		"sms_allowed_senders": doc.get("sms_allowed_senders") or "",
	}


def _reference_to_order(reference):
	"""Reverse the reference format used in init_paystack_payment."""
	if not reference:
		return None
	# Format: AKW-<sales-order-name>--<attempt>
	if reference.startswith("AKW-"):
		return reference[4:].split("--", 1)[0]
	# Fallback: last-attempt lookup by exact match
	if frappe.db.exists("Sales Order", reference):
		return reference
	return None


def _metadata_order(data):
	"""The order we told Paystack this transaction is for, if it came back."""
	meta = (data or {}).get("metadata")
	if isinstance(meta, str):
		try:
			meta = json.loads(meta)
		except ValueError:
			return None
	return (meta or {}).get("order_name") if isinstance(meta, dict) else None


def _accounting_settled(order_name, settings):
	"""Every record a paid order should have exists, as far as settings ask for."""
	if not settings.get("auto_complete_accounting"):
		return True
	si_name = _existing_invoice_for_order(order_name)
	return bool(si_name and _existing_pe_for_invoice(si_name))


def _lock_order(order_name):
	"""Take the order's row lock for this transaction; returns its docstatus."""
	return frappe.db.get_value("Sales Order", order_name, "docstatus", for_update=True)


def _flag_for_refund(order_name, reference, data, why):
	"""Money arrived that can't be applied to the order. A person has to act."""
	amount = flt((data or {}).get("amount") or 0) / 100
	ccy = (data or {}).get("currency") or "GHS"
	message = f"Order {order_name} {why}. Paystack reference {reference}, {ccy} {amount:,.2f}. Refund it in Paystack."
	frappe.log_error(title=f"Paystack refund needed {order_name}", message=message)
	notify_managers(f"Refund needed · {order_name}", message, order_name)
	frappe.db.commit()


def notify_managers(subject, body, order_name=None):
	"""Desk bell for every System Manager. Never lets a failure spread."""
	try:
		managers = frappe.get_all(
			"Has Role",
			filters={"role": "System Manager", "parenttype": "User"},
			pluck="parent",
		)
		for user in set(managers):
			if user == "Guest":
				continue
			frappe.get_doc({
				"doctype": "Notification Log",
				"subject": subject,
				"email_content": body,
				"for_user": user,
				"type": "Alert",
				"document_type": "Sales Order" if order_name else None,
				"document_name": order_name,
			}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Akwaaba: desk notify failed", message=frappe.get_traceback())


def retry_unsettled_orders():
	"""Daily: finish paid orders missing an invoice or payment; report any stuck."""
	settings = _get_payment_settings() or {}
	if not settings.get("auto_complete_accounting"):
		return

	stuck = []
	orders = frappe.get_all(
		"Sales Order",
		filters={
			"docstatus": 1,
			"payment_reference": ["is", "set"],
			"transaction_date": [">=", frappe.utils.add_days(frappe.utils.today(), -90)],
		},
		fields=["name", "payment_reference"],
	)
	for row in orders:
		if _accounting_settled(row.name, settings):
			continue
		if row.payment_reference.startswith("AKW-"):
			try:
				verify_payment(row.payment_reference)
			except Exception:
				frappe.log_error(title=f"Akwaaba retry failed for {row.name}", message=frappe.get_traceback())
				frappe.db.rollback()
			if _accounting_settled(row.name, settings):
				continue
		stuck.append(row.name)

	if stuck:
		message = f"Paid but missing an invoice or payment: {', '.join(stuck)}. See the Error Log for why."
		frappe.log_error(title="Akwaaba paid orders missing records", message=message)
		notify_managers(f"{len(stuck)} paid order(s) missing records", message)
	frappe.db.commit()


def _log_paystack_attempt(order_name, reference, status, data):
	"""Record every Paystack interaction to Error Log for audit trail.

	Not a real error — Error Log is just the most convenient built-in audit
	sink (queryable, timestamped, retained). One line per verify + webhook.
	"""
	try:
		summary = (
			f"order={order_name} ref={reference} status={status} "
			f"channel={(data or {}).get('channel', '?')} "
			f"amount={(data or {}).get('amount', '?')} "
			f"currency={(data or {}).get('currency', '?')}"
		)
		frappe.log_error(title=f"Paystack attempt: {status}", message=summary)
	except Exception:
		pass


def _complete_accounting_cycle(order_name, reference, paystack_data):
	"""On a successful payment: submit SO → create + submit Sales Invoice
	→ create + submit Payment Entry. Safe to repeat: it finishes whatever is
	still missing, and a concurrent call waits on the order's row lock.

	Commits its own work, because the Paystack return runs inside a page GET
	and Frappe rolls back whatever a GET leaves uncommitted. The submitted order
	is committed first so a paid order stands even if the accounting fails; the
	invoice and payment then succeed or roll back together.

	Falls back to just submitting the SO (with a note) if
	`auto_complete_accounting` is off. Every step logs its outcome so admin
	can trace what happened.

	Returns { so, si?, pe?, accounting_completed, ok, cancelled?, notes: [str,...] }.
	`ok` False means records are still missing and retrying is worthwhile.
	"""
	original_user = frappe.session.user
	frappe.set_user("Administrator")

	settings = _get_payment_settings() or {}
	channel = (paystack_data or {}).get("channel") or "unknown"
	amount = flt((paystack_data or {}).get("amount") or 0) / 100

	# Pick the mode of payment + receiving account based on channel
	if channel == "mobile_money":
		mop_name = settings.get("momo_mode_of_payment") or "MoMo"
		paid_to = settings.get("momo_account")
	else:  # card or anything else — treat as card
		mop_name = settings.get("card_mode_of_payment") or "Paystack Card"
		paid_to = settings.get("card_account")

	auto = bool(settings.get("auto_complete_accounting", True))
	result = {"so": order_name, "notes": [], "accounting_completed": False, "ok": False}

	try:
		# Commit first so the reads after the lock start a fresh snapshot and
		# see anything another request saved while this one waited.
		frappe.db.commit()
		if _lock_order(order_name) == 2:
			_flag_for_refund(order_name, reference, paystack_data, "was cancelled before the money arrived")
			result.update(cancelled=True, ok=True)
			result["notes"].append("SO cancelled; payment flagged for refund")
			return result

		so = frappe.get_doc("Sales Order", order_name)

		# --- STEP 1: submit SO (idempotent) ---
		if so.docstatus != 1:
			note = (
				f"\n\n---\nPaystack reference: {reference}\n"
				f"Channel: {channel}\n"
				f"Amount received: {so.currency} {amount:.2f}"
			)
			so.terms = (so.terms or "") + note
			so.payment_reference = reference
			so.flags.ignore_permissions = True
			so.submit()
			result["notes"].append("SO submitted")
		else:
			if not so.payment_reference:
				so.db_set("payment_reference", reference, update_modified=False)
			result["notes"].append("SO already submitted")
		frappe.db.commit()

		if not auto:
			result["ok"] = True
			result["notes"].append("auto_complete_accounting is off; stopping at SO")
			return result

		# --- STEP 2: Sales Invoice ---
		_lock_order(order_name)
		si_name = _existing_invoice_for_order(order_name)
		if not si_name:
			si_name = _make_and_submit_sales_invoice(order_name)
			result["notes"].append(f"SI {si_name} submitted")
		else:
			result["notes"].append(f"SI {si_name} already existed")
		result["si"] = si_name

		# --- STEP 3: Payment Entry ---
		missing = None
		if not paid_to:
			missing = "No receiving account configured (Akwaaba Payment Settings); PE skipped."
		elif not mop_name or not frappe.db.exists("Mode of Payment", mop_name):
			missing = f"Mode of Payment '{mop_name}' missing; PE skipped."
		if missing:
			frappe.log_error(title=f"Paystack accounting incomplete {order_name}", message=f"{order_name}: {missing}")
			result["notes"].append(missing)
			frappe.db.commit()
			return result

		pe_name = _existing_pe_for_invoice(si_name)
		if not pe_name:
			pe_name = _make_and_submit_payment_entry(
				si_name=si_name,
				mode_of_payment=mop_name,
				paid_to=paid_to,
				reference=reference,
				received_amount=amount,
			)
			result["notes"].append(f"PE {pe_name} submitted")
		else:
			result["notes"].append(f"PE {pe_name} already existed")
		result["pe"] = pe_name
		frappe.db.commit()

		result.update(accounting_completed=True, ok=True)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			title=f"Paystack accounting cycle failed for {order_name}",
			message=frappe.get_traceback(),
		)
		frappe.db.commit()
		result["notes"].append("accounting_cycle: exception (see Error Log)")
		result.pop("si", None)
		result.pop("pe", None)
	finally:
		frappe.set_user(original_user)

	return result


def _existing_invoice_for_order(order_name):
	row = frappe.db.sql(
		"""
		SELECT DISTINCT sii.parent
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE sii.sales_order = %s AND si.docstatus = 1
		LIMIT 1
		""",
		(order_name,),
	)
	return row[0][0] if row else None


def _existing_pe_for_invoice(si_name):
	row = frappe.db.sql(
		"""
		SELECT DISTINCT per.parent
		FROM `tabPayment Entry Reference` per
		JOIN `tabPayment Entry` pe ON pe.name = per.parent
		WHERE per.reference_name = %s
			AND per.reference_doctype = 'Sales Invoice'
			AND pe.docstatus = 1
		LIMIT 1
		""",
		(si_name,),
	)
	return row[0][0] if row else None


def _make_and_submit_sales_invoice(order_name):
	# In ERPNext develop `make_sales_invoice` lives in the mapper module.
	# Older versions kept it at sales_order.sales_order — support both.
	try:
		from erpnext.selling.doctype.sales_order.mapper import make_sales_invoice
	except ImportError:
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
	si = make_sales_invoice(source_name=order_name)
	si.set_missing_values()
	si.flags.ignore_permissions = True
	si.insert()
	si.submit()
	return si.name


def _make_and_submit_payment_entry(si_name, mode_of_payment, paid_to, reference, received_amount):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	pe = get_payment_entry("Sales Invoice", si_name)
	pe.mode_of_payment = mode_of_payment
	pe.paid_to = paid_to
	pe.reference_no = reference
	pe.reference_date = frappe.utils.today()
	if received_amount and abs(received_amount - float(pe.paid_amount or 0)) > 0.01:
		# Trust Paystack for the actual amount when it disagrees slightly
		pe.paid_amount = received_amount
		pe.received_amount = received_amount
	pe.flags.ignore_permissions = True
	pe.insert()
	pe.submit()
	return pe.name


# ---------------------------------------------------------------------------
# NOTE: The admin "Reverse Full Order" action now lives in the Server Script
# `akwaaba_reverse_order` (installed via install.py, editable in Desk under
# Server Script). Requires `server_script_enabled: 1` in site config.
# ---------------------------------------------------------------------------


# ===========================================================================
# Address helper (pre-existing)
# ===========================================================================

def _ensure_customer_address(customer, name, phone, address_line, city, region):
	"""Create a shipping-tagged Address linked to the customer."""
	title = f"{customer} — {city}"
	existing = frappe.db.get_value(
		"Address",
		{"address_title": title, "address_line1": address_line, "city": city},
		"name",
	)
	if existing:
		# Ensure the link is present
		if not frappe.db.exists(
			"Dynamic Link",
			{"parent": existing, "link_doctype": "Customer", "link_name": customer},
		):
			doc = frappe.get_doc("Address", existing)
			doc.append("links", {"link_doctype": "Customer", "link_name": customer})
			doc.save(ignore_permissions=True)
		return existing

	addr = frappe.new_doc("Address")
	addr.address_title = title
	addr.address_type = "Shipping"
	addr.address_line1 = address_line
	addr.city = city
	if region:
		addr.state = region
	addr.country = "Ghana"
	if phone:
		addr.phone = phone
	addr.append("links", {"link_doctype": "Customer", "link_name": customer})
	addr.flags.ignore_mandatory = True
	try:
		addr.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Akwaaba: address create failed", message=frappe.get_traceback())
		return None
	return addr.name
