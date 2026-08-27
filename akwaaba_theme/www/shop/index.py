"""/shop — Akwaaba products listing, server-rendered, mobile-first.

Query params:
	category  — Item Group name or slug (route)
	sort      — newest | price_low | price_high | name
	start     — pagination offset
"""

import frappe
from frappe.utils import cint

from akwaaba_theme.context import with_shared_context


PAGE_SIZE = 24
DEFAULT_PRICE_LIST = "Akwaaba Retail"


SORT_MAP = {
	"newest": "wi.creation desc",
	"price_low": "ip.price_list_rate asc",
	"price_high": "ip.price_list_rate desc",
	"name": "wi.web_item_name asc",
}


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False

	form = frappe.form_dict
	category_param = (form.get("category") or "").strip()
	sort = form.get("sort") or "newest"
	if sort not in SORT_MAP:
		sort = "newest"
	start = max(cint(form.get("start")) or 0, 0)

	# Resolve category param (accepts either Item Group name or its route slug)
	item_group_name = _resolve_group(category_param) if category_param else None

	items, total = _query_items(item_group_name, sort, start, PAGE_SIZE)

	# Shared context: nav collections + all collections + homepage collections
	with_shared_context(context)

	context.items = items
	context.total = total
	context.page_size = PAGE_SIZE
	context.start = start
	context.has_next = (start + PAGE_SIZE) < total
	context.next_start = start + PAGE_SIZE
	context.sort = sort
	context.category = category_param
	context.category_name = item_group_name or "All Collections"

	context.title = context.category_name + " — Serwa Kollections"
	context.meta_description = (
		f"Shop the {context.category_name} at Serwa Kollections — Ghana-made "
		"Kente, Adinkra, Fugu, Ankara, and Kaba & Slit."
	)
	return context


def _resolve_group(param: str):
	"""Accept either 'Kente Collection' or 'kente-collection'."""
	if not param:
		return None
	if frappe.db.exists("Item Group", param):
		return param
	slug_match = frappe.db.get_value(
		"Item Group", {"route": param.strip("/")}, "name"
	)
	return slug_match


def _query_items(item_group, sort, start, page_size):
	order_by = SORT_MAP.get(sort, SORT_MAP["newest"])
	# Grid shows templates + single-SKU items only. Variants themselves are
	# fetched via _attach_variant_options and rendered as size chips on the
	# parent card, so they must not appear as separate cards.
	filters = "WHERE wi.published = 1 AND (wi.variant_of IS NULL OR wi.variant_of = '')"
	values = {"price_list": DEFAULT_PRICE_LIST, "start": start, "page_size": page_size}
	if item_group:
		filters += " AND wi.item_group = %(item_group)s"
		values["item_group"] = item_group

	rows = frappe.db.sql(
		f"""
		SELECT
			wi.name,
			wi.item_code,
			wi.item_name,
			wi.web_item_name,
			wi.item_group,
			wi.route,
			COALESCE(NULLIF(wi.website_image, ''), i.image) AS website_image,
			wi.short_description,
			wi.has_variants,
			wi.on_backorder,
			wi.hover_video_url,
			wi.hover_video_poster_url,
			ip.price_list_rate AS price,
			ip.currency
		FROM `tabWebsite Item` wi
		LEFT JOIN `tabItem` i ON i.item_code = wi.item_code
		LEFT JOIN `tabItem Price` ip
			ON ip.item_code = wi.item_code
			AND ip.price_list = %(price_list)s
			AND ip.selling = 1
		{filters}
		ORDER BY {order_by}
		LIMIT %(page_size)s OFFSET %(start)s
		""",
		values,
		as_dict=True,
	)

	_attach_variant_options(rows)

	count_filters = "WHERE wi.published = 1 AND (wi.variant_of IS NULL OR wi.variant_of = '')"
	count_values = {}
	if item_group:
		count_filters += " AND wi.item_group = %(item_group)s"
		count_values["item_group"] = item_group

	total = frappe.db.sql(
		f"SELECT COUNT(*) FROM `tabWebsite Item` wi {count_filters}",
		count_values,
	)[0][0]

	return rows, total


def _attach_variant_options(rows):
	"""For each has_variants row, fetch its size variants in one bulk query
	and attach them as `variant_options: [{code, size, price, oos}, ...]`.

	If the template has a min-price across its variants, replace the row's
	price with that min so the card shows "from ₵X" pricing accurately.
	"""
	templates = [r.item_code for r in rows if r.get("has_variants")]
	if not templates:
		for r in rows:
			r["variant_options"] = []
		return

	variants = frappe.db.sql(
		"""
		SELECT
			wi.item_code,
			wi.variant_of,
			wi.on_backorder,
			iva.attribute_value AS size,
			ip.price_list_rate AS price
		FROM `tabWebsite Item` wi
		JOIN `tabItem Variant Attribute` iva
			ON iva.parent = wi.item_code AND iva.attribute = 'Size'
		LEFT JOIN `tabItem Price` ip
			ON ip.item_code = wi.item_code
			AND ip.price_list = %(pl)s
			AND ip.selling = 1
		WHERE wi.variant_of IN %(templates)s
		  AND wi.published = 1
		""",
		{"pl": DEFAULT_PRICE_LIST, "templates": templates},
		as_dict=True,
	)

	# Preserve the seeded SIZES order (XS/S/M/L/XL) rather than lexical
	_SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL", "XXXL"]

	def _size_key(v):
		try:
			return _SIZE_ORDER.index(v.size)
		except ValueError:
			return 999

	by_template = {}
	for v in variants:
		by_template.setdefault(v.variant_of, []).append(v)

	for r in rows:
		if not r.get("has_variants"):
			r["variant_options"] = []
			continue
		opts = sorted(by_template.get(r.item_code, []), key=_size_key)
		r["variant_options"] = [
			{
				"code": v.item_code,
				"size": v.size,
				"price": float(v.price or 0),
				"oos": bool(v.on_backorder),
			}
			for v in opts
		]
		# "from" price = min price across in-stock variants
		in_stock_prices = [o["price"] for o in r["variant_options"] if not o["oos"] and o["price"]]
		if in_stock_prices:
			r["price"] = min(in_stock_prices)
			r["price_from"] = True
