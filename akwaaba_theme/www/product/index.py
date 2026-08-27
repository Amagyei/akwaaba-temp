"""/product/<slug> — Akwaaba's bespoke product detail page.

Replaces webshop's stock item generator (which now 301s here via the
`Website Item` override). Product data is derived exactly the way `/shop`
does it — direct SQL against Website Item + Item Price (Akwaaba Retail) +
Size variants — so pricing, the cookie cart, and variant chips stay
consistent across the whole storefront.

Route rule (hooks.py):
    {"from_route": "/product/<path:slug>", "to_route": "product"}
"""

import frappe

from akwaaba_theme.context import with_shared_context

DEFAULT_PRICE_LIST = "Akwaaba Retail"
_SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL", "XXXL"]


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False
	context.body_class = "product-page akw-pdp-page"

	slug = (frappe.form_dict.get("slug") or "").strip().strip("/")
	item = _resolve_item(slug)
	if not item:
		# Unknown / unpublished product → fall back to the themed listing.
		frappe.local.flags.redirect_location = "/shop"
		raise frappe.Redirect

	with_shared_context(context)

	# variant_options may adjust item["price"] to the "from" min price, so
	# compute it before reading the price out.
	context.variant_options = _variants(item)

	context.item = item
	context.gallery = _gallery(item)
	context.specs = _specs(item.get("name"))
	context.description = item.get("web_long_description") or item.get("description") or ""
	context.currency = "GHS"
	context.price = item.get("price")
	context.price_from = bool(item.get("price_from"))
	context.in_stock = not item.get("on_backorder")
	context.preselect_size = item.get("preselect_size")
	context.crumbs = _crumbs(item)
	context.enable_fitting_studio = True

	title = item.get("web_item_name") or item.get("item_name") or item.get("item_code")
	context.product_title = title
	context.title = f"{title} — Serwa Kollections"
	context.meta_description = (item.get("short_description") or title)
	return context


# ---------------------------------------------------------------------------
# Data derivation (mirrors www/shop/index.py so pricing/variants stay in sync)
# ---------------------------------------------------------------------------

def _resolve_item(slug):
	"""Look up a published Website Item by web route, then by item code.
	If a *variant* URL is hit, resolve to its template (and remember the
	size so the PDP can preselect it)."""
	if not slug:
		return None

	row = _fetch_one("wi.route = %(slug)s", {"slug": slug}) \
		or _fetch_one("wi.item_code = %(slug)s", {"slug": slug})
	if not row:
		return None

	if row.get("variant_of"):
		tpl = _fetch_one("wi.item_code = %(code)s", {"code": row["variant_of"]})
		if tpl:
			tpl["preselect_size"] = row.get("_size")
			return tpl
	return row


def _fetch_one(where, values):
	values = dict(values, pl=DEFAULT_PRICE_LIST)
	rows = frappe.db.sql(
		f"""
		SELECT
			wi.name, wi.item_code, wi.item_name, wi.web_item_name,
			wi.item_group, wi.route, wi.slideshow, wi.short_description,
			wi.web_long_description, wi.description, wi.has_variants,
			wi.variant_of, wi.on_backorder,
			COALESCE(NULLIF(wi.website_image, ''), i.image) AS website_image,
			ip.price_list_rate AS price,
			iva.attribute_value AS _size
		FROM `tabWebsite Item` wi
		LEFT JOIN `tabItem` i ON i.item_code = wi.item_code
		LEFT JOIN `tabItem Price` ip
			ON ip.item_code = wi.item_code
			AND ip.price_list = %(pl)s
			AND ip.selling = 1
		LEFT JOIN `tabItem Variant Attribute` iva
			ON iva.parent = wi.item_code AND iva.attribute = 'Size'
		WHERE wi.published = 1 AND {where}
		LIMIT 1
		""",
		values,
		as_dict=True,
	)
	return rows[0] if rows else None


def _variants(item):
	"""Size variants for a template item, ordered XS→XXXL. Sets the parent's
	'from' price to the min in-stock variant price (matches the card)."""
	if not item.get("has_variants"):
		return []

	variants = frappe.db.sql(
		"""
		SELECT
			wi.item_code, wi.on_backorder,
			iva.attribute_value AS size,
			ip.price_list_rate AS price
		FROM `tabWebsite Item` wi
		JOIN `tabItem Variant Attribute` iva
			ON iva.parent = wi.item_code AND iva.attribute = 'Size'
		LEFT JOIN `tabItem Price` ip
			ON ip.item_code = wi.item_code
			AND ip.price_list = %(pl)s
			AND ip.selling = 1
		WHERE wi.variant_of = %(tpl)s AND wi.published = 1
		""",
		{"pl": DEFAULT_PRICE_LIST, "tpl": item["item_code"]},
		as_dict=True,
	)

	def _size_key(v):
		try:
			return _SIZE_ORDER.index(v.size)
		except ValueError:
			return 999

	variants.sort(key=_size_key)
	opts = [
		{
			"code": v.item_code,
			"size": v.size,
			"price": float(v.price or 0),
			"oos": bool(v.on_backorder),
		}
		for v in variants
	]

	in_stock = [o["price"] for o in opts if not o["oos"] and o["price"]]
	if in_stock:
		item["price"] = min(in_stock)
		item["price_from"] = True
	return opts


def _gallery(item):
	"""Hero image + any Website Slideshow images, de-duplicated."""
	imgs = []
	hero = item.get("website_image")
	if hero:
		imgs.append(hero)
	if item.get("slideshow"):
		rows = frappe.db.sql(
			"""
			SELECT image FROM `tabWebsite Slideshow Item`
			WHERE parent = %s AND parenttype = 'Website Slideshow'
			  AND IFNULL(image, '') != ''
			ORDER BY idx
			""",
			item["slideshow"],
			as_dict=True,
		)
		for r in rows:
			if r.image and r.image not in imgs:
				imgs.append(r.image)
	return imgs


def _specs(wi_name):
	if not wi_name:
		return []
	return frappe.db.sql(
		"""
		SELECT label, description FROM `tabItem Website Specification`
		WHERE parent = %s AND parenttype = 'Website Item'
		ORDER BY idx
		""",
		wi_name,
		as_dict=True,
	)


def _crumbs(item):
	"""Breadcrumb that only ever points at themed routes."""
	crumbs = [
		{"label": "Home", "route": "/"},
		{"label": "Shop", "route": "/shop"},
	]
	group = item.get("item_group")
	if group:
		group_route = frappe.db.get_value("Item Group", group, "route") or group
		crumbs.append({"label": group, "route": "/shop?category=" + group_route})
	crumbs.append({
		"label": item.get("web_item_name") or item.get("item_name") or item.get("item_code"),
		"route": None,
	})
	return crumbs
