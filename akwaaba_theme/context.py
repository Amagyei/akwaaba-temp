"""Shared context helpers used by the Akwaaba www pages.

Loads collections (Item Groups) and their display metadata from the backend
so templates never hardcode category names, URLs, or presentation labels.
"""

import frappe


def get_shop_collections(homepage_only=False, nav_only=False):
	"""Return published Item Groups with their Akwaaba display metadata.

	Args:
		homepage_only: only groups with homepage_position > 0
		nav_only: only groups with show_in_nav = 1

	Sort:
		homepage grid → homepage_position asc
		everywhere else → alphabetical
	"""
	filters = {"show_in_website": 1, "is_group": 0}
	fields = [
		"name",
		"item_group_name",
		"route",
		"description",
	]

	# Item Group may or may not have our custom fields (they get added on
	# install). Guard so this still returns rows on a fresh site.
	optional = {
		"homepage_position": "Website Item-hover_video_url",  # dummy, replaced below
	}
	if frappe.db.exists("Custom Field", "Item Group-homepage_position"):
		fields += [
			"homepage_position",
			"homepage_eyebrow",
			"homepage_caption",
			"show_in_nav",
		]

	# Prefer intentional ordering (homepage_position asc) when available,
	# fall back to alphabetical.
	if frappe.db.exists("Custom Field", "Item Group-homepage_position"):
		order_by = "homepage_position asc, item_group_name asc"
	else:
		order_by = "item_group_name asc"

	if homepage_only and frappe.db.exists("Custom Field", "Item Group-homepage_position"):
		filters["homepage_position"] = [">", 0]

	if nav_only and frappe.db.exists("Custom Field", "Item Group-show_in_nav"):
		filters["show_in_nav"] = 1

	rows = frappe.get_all(
		"Item Group",
		filters=filters,
		fields=fields,
		order_by=order_by,
	)
	# Normalize + provide safe defaults so templates never blow up
	for r in rows:
		r.label = r.get("item_group_name") or r.get("name")
		r.url = "/shop?category=" + (r.get("route") or r.get("name") or "")
		r.eyebrow = r.get("homepage_eyebrow") or ""
		r.caption = r.get("homepage_caption") or ""
		r.images = _collection_images(r.get("name"))
	return rows


def _collection_images(item_group, limit=5):
	"""A handful of real product photos for this Item Group's homepage card,
	newest first. Powers a background slideshow instead of a static swatch."""
	if not item_group:
		return []
	rows = frappe.db.sql(
		"""
		SELECT COALESCE(NULLIF(wi.website_image, ''), i.image) AS image
		FROM `tabWebsite Item` wi
		LEFT JOIN `tabItem` i ON i.item_code = wi.item_code
		WHERE wi.published = 1 AND wi.item_group = %(item_group)s
		  AND COALESCE(NULLIF(wi.website_image, ''), i.image, '') != ''
		ORDER BY wi.creation DESC
		LIMIT %(limit)s
		""",
		{"item_group": item_group, "limit": limit},
		as_dict=True,
	)
	return [r.image for r in rows]


def get_hero_slides(limit=6):
	"""Published Website Items flagged 'Feature on Hero', for the home hero's
	cycling backdrop.

	Each slide carries the item's website image, optional hover video (+poster),
	title, and product URL so the hero can reuse the exact media the shop cards
	already show. Ordered by hero_position asc (lower first), newest breaking
	ties. Items without any image are skipped so the hero never renders blank.
	"""
	# The flag/order fields are added on install; guard so a fresh site (or one
	# that hasn't migrated yet) still renders the plain gradient hero.
	if not frappe.db.exists("Custom Field", "Website Item-feature_on_hero"):
		return []

	rows = frappe.db.sql(
		"""
		SELECT
			wi.web_item_name, wi.item_name, wi.item_code, wi.route, wi.item_group,
			COALESCE(NULLIF(wi.website_image, ''), i.image) AS website_image,
			wi.hover_video_url, wi.hover_video_poster_url
		FROM `tabWebsite Item` wi
		LEFT JOIN `tabItem` i ON i.item_code = wi.item_code
		WHERE wi.published = 1 AND wi.feature_on_hero = 1
		  AND COALESCE(NULLIF(wi.website_image, ''), i.image, '') != ''
		ORDER BY wi.hero_position ASC, wi.creation DESC
		LIMIT %(limit)s
		""",
		{"limit": limit},
		as_dict=True,
	)

	slides = []
	for r in rows:
		title = r.get("web_item_name") or r.get("item_name") or r.get("item_code")
		slides.append({
			"title": title,
			"item_group": r.get("item_group") or "",
			"url": "/product/" + (r.get("route") or r.get("item_code")),
			"image": r.get("website_image"),
			"video": r.get("hover_video_url") or "",
			"poster": r.get("hover_video_poster_url") or r.get("website_image"),
		})
	return slides


def with_shared_context(context):
	"""Populate context vars that every Akwaaba page needs (nav + footer)."""
	context.akw_all_collections = get_shop_collections()
	context.akw_nav_collections = get_shop_collections(nav_only=True)
	context.akw_homepage_collections = get_shop_collections(homepage_only=True)
	return context
