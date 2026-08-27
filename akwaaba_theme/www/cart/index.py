"""/cart — Akwaaba's guest-friendly cart page.

Reads the `akw_cart` cookie ({item_code: qty} JSON), enriches with product
details from Website Item + Item Price, and renders a line-item view plus
a checkout form. All quantity mutations happen client-side against the same
cookie; only `place_order` writes to the backend.
"""

import json

import frappe

from akwaaba_theme.context import with_shared_context


CART_COOKIE = "akw_cart"
DEFAULT_PRICE_LIST = "Akwaaba Retail"


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False
	context.title = "Cart — Serwa Kollections"
	with_shared_context(context)

	cart = _read_cart_cookie()
	lines, subtotal = _build_lines(cart)

	context.lines = lines
	context.subtotal = subtotal
	context.item_count = sum(l["qty"] for l in lines)
	context.currency = "GHS"
	context.is_empty = len(lines) == 0
	return context


def _read_cart_cookie():
	try:
		raw = frappe.request.cookies.get(CART_COOKIE)
	except Exception:
		raw = None
	if not raw:
		return {}
	try:
		from urllib.parse import unquote
		data = json.loads(unquote(raw))
	except Exception:
		return {}
	# Coerce to {item_code: int qty} and drop invalid entries
	return {
		str(k): max(int(v), 0)
		for k, v in data.items()
		if isinstance(k, str) and str(v).lstrip("-").isdigit()
	}


def _build_lines(cart):
	"""Fetch product details for each cart item + compute subtotals."""
	if not cart:
		return [], 0.0

	codes = list(cart.keys())
	# variant_of + Size attribute pulled through so lines can render
	# "Kente Blazer · Size M" for variant SKUs. Templates use their own
	# website image; variants fall back to the template's image if theirs
	# is empty.
	rows = frappe.db.sql(
		"""
		SELECT
			wi.item_code, wi.web_item_name, wi.item_name, wi.item_group,
			wi.route, wi.variant_of,
			COALESCE(
				NULLIF(wi.website_image, ''),
				NULLIF(i.image, ''),
				NULLIF(tpl_wi.website_image, ''),
				NULLIF(tpl_i.image, '')
			) AS website_image,
			ip.price_list_rate AS unit_price,
			iva.attribute_value AS variant_size,
			COALESCE(tpl_wi.web_item_name, wi.web_item_name, wi.item_name) AS parent_title,
			COALESCE(tpl_wi.route, wi.route) AS parent_route
		FROM `tabWebsite Item` wi
		LEFT JOIN `tabItem` i ON i.item_code = wi.item_code
		LEFT JOIN `tabItem Price` ip
			ON ip.item_code = wi.item_code
			AND ip.price_list = %(pl)s
			AND ip.selling = 1
		LEFT JOIN `tabItem Variant Attribute` iva
			ON iva.parent = wi.item_code AND iva.attribute = 'Size'
		LEFT JOIN `tabWebsite Item` tpl_wi
			ON tpl_wi.item_code = wi.variant_of
		LEFT JOIN `tabItem` tpl_i
			ON tpl_i.item_code = wi.variant_of
		WHERE wi.item_code IN %(codes)s
			AND wi.published = 1
		""",
		{"pl": DEFAULT_PRICE_LIST, "codes": codes},
		as_dict=True,
	)

	by_code = {r.item_code: r for r in rows}
	lines = []
	subtotal = 0.0
	for code, qty in cart.items():
		row = by_code.get(code)
		if not row or qty <= 0:
			continue
		unit = float(row.unit_price or 0)
		line_total = unit * qty
		subtotal += line_total
		# For variants, prefer the parent's title + link so the cart reads
		# "Kente Panel Blazer" (not "…— M") and clicking goes to the template.
		title = row.parent_title if row.variant_of else (row.web_item_name or row.item_name or code)
		route = row.parent_route or row.route or ""
		lines.append({
			"item_code": code,
			"title": title,
			"variant_label": ("Size " + row.variant_size) if row.variant_size else "",
			"route": route,
			"image": row.website_image or "",
			"item_group": row.item_group or "",
			"unit_price": unit,
			"qty": qty,
			"subtotal": line_total,
		})
	return lines, subtotal
