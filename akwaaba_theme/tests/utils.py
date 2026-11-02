"""Shared fixtures for the Akwaaba tests. Paystack is always faked here."""

from unittest.mock import MagicMock

import frappe

ITEM_CODE = "AKW-TEST-PIECE"
UNPRICED_ITEM_CODE = "AKW-TEST-UNPRICED"
PRICE = 350
PRICE_LIST = "Akwaaba Retail"


def before_tests():
	"""A new site has no company or ERPNext fixtures until the setup wizard has run."""
	if frappe.db.a_row_exists("Company"):
		return
	from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

	year = frappe.utils.now_datetime().year
	setup_complete({
		"language": "English",
		"country": "Ghana",
		"timezone": "Africa/Accra",
		"currency": "GHS",
		"full_name": "Test User",
		"email": "test.user@example.com",
		"company_name": "Akwaaba Test",
		"company_abbr": "AKT",
		"chart_of_accounts": "Standard",
		"fy_start_date": f"{year}-01-01",
		"fy_end_date": f"{year}-12-31",
		"domains": ["Retail"],
	})
	frappe.db.commit()


def ensure_company():
	"""A GHS company set as the default, which is what place_order relies on."""
	name = frappe.db.get_default("company")
	if name and frappe.db.get_value("Company", name, "default_currency") == "GHS":
		return name
	name = "Akwaaba Test"
	if not frappe.db.exists("Company", name):
		frappe.get_doc({
			"doctype": "Company",
			"company_name": name,
			"abbr": "AKT",
			"default_currency": "GHS",
			"country": "Ghana",
		}).insert(ignore_permissions=True)
	frappe.db.set_default("company", name)
	return name


def ensure_item(item_code, item_name, price=None):
	if not frappe.db.exists("Item", item_code):
		group = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0]
		frappe.get_doc({
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_name,
			"item_group": group,
			"stock_uom": "Nos",
			"is_stock_item": 0,
		}).insert(ignore_permissions=True)
	if not frappe.db.exists("Website Item", {"item_code": item_code}):
		from webshop.webshop.doctype.website_item.website_item import make_website_item

		wi = make_website_item(frappe.get_doc("Item", item_code), save=False)
		wi.published = 1
		wi.insert(ignore_permissions=True)
	frappe.db.set_value("Website Item", {"item_code": item_code}, "published", 1)
	if price and not frappe.db.exists("Item Price", {"item_code": item_code, "price_list": PRICE_LIST}):
		frappe.get_doc({
			"doctype": "Item Price",
			"item_code": item_code,
			"price_list": PRICE_LIST,
			"price_list_rate": price,
			"selling": 1,
			"currency": "GHS",
		}).insert(ignore_permissions=True)


def setup_shop():
	"""Everything an order needs. Returns the fake Payment Settings."""
	company = ensure_company()
	if not frappe.db.exists("Price List", PRICE_LIST):
		frappe.get_doc({
			"doctype": "Price List",
			"price_list_name": PRICE_LIST,
			"currency": "GHS",
			"selling": 1,
		}).insert(ignore_permissions=True)
	if not frappe.db.exists("Mode of Payment", "Cash"):
		frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": "Cash", "type": "Cash"}).insert(ignore_permissions=True)
	ensure_item(ITEM_CODE, "Akwaaba Test Piece", PRICE)
	ensure_item(UNPRICED_ITEM_CODE, "Akwaaba Unpriced Piece")
	cash = frappe.get_all(
		"Account",
		filters={"company": company, "account_type": ["in", ["Cash", "Bank"]], "is_group": 0},
		pluck="name",
		limit=1,
	)[0]
	frappe.db.commit()
	return frappe._dict(
		enabled=True,
		environment="Test",
		secret_key="sk_fake",
		webhook_secret="whsec_fake",
		brand_label="MoMo / Card",
		company=company,
		auto_complete_accounting=True,
		momo_account=cash,
		card_account=cash,
		momo_mode_of_payment="Cash",
		card_mode_of_payment="Cash",
		direct_momo_enabled=True,
		sms_inbox_secret="sms_fake",
		sms_allowed_senders="MobileMoney, MTNMoMo",
	)


def paystack_payload(reference, order_name, amount=PRICE, status="success", channel="mobile_money"):
	return {
		"reference": reference,
		"status": status,
		"amount": int(amount * 100),
		"currency": "GHS",
		"channel": channel,
		"gateway_response": "Approved" if status == "success" else status,
		"metadata": {"order_name": order_name},
	}


def paystack_response(data):
	"""What requests.get returns for /transaction/verify."""
	response = MagicMock()
	response.ok = True
	response.json.return_value = {"status": True, "data": data}
	return response


def submitted_invoices(order_name):
	return frappe.db.sql_list(
		"""SELECT DISTINCT sii.parent FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE sii.sales_order = %s AND si.docstatus = 1""",
		order_name,
	)


def any_invoices(order_name):
	return frappe.db.sql_list(
		"SELECT DISTINCT parent FROM `tabSales Invoice Item` WHERE sales_order = %s", order_name
	)


def submitted_payments(invoice):
	return frappe.db.sql_list(
		"""SELECT DISTINCT per.parent FROM `tabPayment Entry Reference` per
		JOIN `tabPayment Entry` pe ON pe.name = per.parent
		WHERE per.reference_name = %s AND pe.docstatus = 1""",
		invoice,
	)


def ledger_imbalance(vouchers):
	"""Debits minus credits across these vouchers; 0 when the books balance."""
	row = frappe.db.sql(
		"""SELECT COALESCE(SUM(debit), 0) - COALESCE(SUM(credit), 0) FROM `tabGL Entry`
		WHERE voucher_no IN %s AND is_cancelled = 0""",
		(tuple(vouchers),),
	)
	return round(float(row[0][0]), 2)


def make_tracked_item(qty, price=PRICE):
	"""A new one-of-a-kind style piece with `qty` on the shelf."""
	from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

	company = ensure_company()
	warehouse = frappe.db.get_value("Warehouse", {"company": company, "warehouse_name": "Stores", "is_group": 0})
	frappe.db.set_single_value("Stock Settings", "default_warehouse", warehouse)
	code = "AKW-TRACKED-" + frappe.generate_hash(length=6).upper()
	group = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0]
	frappe.get_doc({
		"doctype": "Item",
		"item_code": code,
		"item_name": f"Tracked {code}",
		"item_group": group,
		"stock_uom": "Nos",
		"is_stock_item": 1,
		"valuation_rate": 100,
	}).insert(ignore_permissions=True)
	ensure_item(code, f"Tracked {code}", price)
	if qty:
		make_stock_entry(item_code=code, qty=qty, to_warehouse=warehouse, rate=100, company=company)
	frappe.db.commit()
	return code
