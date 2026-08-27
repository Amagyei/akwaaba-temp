app_name = "akwaaba_theme"
app_title = "Serwa Kollections Theme"
app_publisher = "Serwa Kollections"
app_description = "Ghana editorial luxury clothing storefront theme."
app_email = "amagyei@example.com"
app_license = "mit"

# Assets injected into every website page (includes webshop).
# fitting-studio.* powers the Fitting Studio widget: it auto-mounts on
# webshop item pages (which are upstream templates we don't edit) and on any
# [data-akw-fitting-studio] element. Loading it site-wide is how it reaches
# the item page without touching webshop.
web_include_css = [
	"/assets/akwaaba_theme/css/akwaaba.css",
	"/assets/akwaaba_theme/css/fitting-studio.css",
]
web_include_js = [
	"/assets/akwaaba_theme/js/akwaaba.js",
	"/assets/akwaaba_theme/js/fitting-studio.js",
]

# Homepage: serves www/index.html at "/"
home_page = "index"
website_route_rules = [
    {"from_route": "/", "to_route": "index"},
    # Route /order/<any-slug> to the order confirmation controller
    {"from_route": "/order/<order_name>", "to_route": "order/detail"},
    # Bespoke product detail page. <path:slug> allows nested item routes
    # (e.g. /product/kente-collection/adinkra-print).
    {"from_route": "/product/<path:slug>", "to_route": "product"},
]

# Retire webshop's stock listing pages (their Frappe-default styling clashes
# with the theme). The themed /shop replaces them. Item Group generator pages
# (/accessories, …) are redirected via seeded Website Settings route redirects
# (install.seed_category_redirects) since their routes are data-driven.
# Anchored with $ so we don't catch asset paths like /all-products/index.js.
website_redirects = [
    {"source": r"/all-products$", "target": "/shop"},
    {"source": r"/shop-by-category$", "target": "/shop"},
]

# Redirect the stock Website Item route → our themed /product page, so there
# is exactly one product page. Webshop overrides Item / Item Group / Payment
# Request but NOT Website Item, so there is no override conflict.
override_doctype_class = {
    "Website Item": "akwaaba_theme.overrides.website_item.AkwaabaWebsiteItem",
}

# Doc-event hooks. Admin order notifications are handled natively via the
# Frappe `Notification` doctype (seeded by install.py — no custom Python).
doc_events = {
    "Item": {
        # Mirror Item.image → Website Item.website_image so uploads on the
        # Item form show up in the storefront without a separate upload.
        "on_update": "akwaaba_theme.doc_events.sync_item_image_to_website_item",
    },
    "Website Item": {
        # Mirror hover_video_file → hover_video_url so admins don't have to
        # copy the /files/ URL themselves. Pasted URLs win.
        "before_save": "akwaaba_theme.doc_events.mirror_hover_video_file_to_url",
    },
}

# Brand and footer overrides applied to Website Settings-based nav
website_context = {
    "favicon": "/assets/akwaaba_theme/images/favicon.svg",
    "splash_image": "/assets/akwaaba_theme/images/kente-splash.svg",
}

# Seed catalog + configure webshop on first install of this app
after_install = "akwaaba_theme.install.after_install"

# Export custom fields so they migrate to prod on `bench migrate`
# even without re-running install.py.
fixtures = [
	{
		"dt": "Custom Field",
		"filters": [
			["dt", "in", ["Website Item", "Item Group", "Contact", "Address", "Sales Order"]],
			["fieldname", "in", [
				# Sales Order — direct-MoMo (manual) payment tracking
				"akwaaba_momo_section",
				"payment_status",
				"momo_payer_phone",
				"momo_txn_id",
				# Website Item — media
				"akwaaba_media_section",
				"hover_video_file",
				"hover_video_url",
				"hover_video_poster_url",
				"feature_on_hero",
				"hero_position",
				# Item Group — homepage/nav display metadata
				"akwaaba_display_section",
				"homepage_position",
				"homepage_eyebrow",
				"homepage_caption",
				"homepage_background_class",
				"show_in_nav",
				# Contact — compat shim for erpnext develop against frappe v16
				"is_billing_contact",
				# Address — compat shims for erpnext develop against frappe v16
				"is_your_company_address",
				"tax_category",
			]],
		],
	},
	{
		"dt": "Client Script",
		"filters": [["name", "in", [
			"Akwaaba Reverse Order Button",
			"Akwaaba Hover Video Preview",
			"Akwaaba Confirm MoMo Payment",
		]]],
	},
	{
		"dt": "Server Script",
		"filters": [["name", "=", "akwaaba_reverse_order"]],
	},
	{
		"dt": "Notification",
		"filters": [["name", "in", [
			"Akwaaba New Order Email",
			"Akwaaba Order Placed (Customer)",
			"Akwaaba Payment Received (Customer)",
			"Akwaaba Order Dispatched (Customer)",
			"Akwaaba Order Cancelled (Customer)",
		]]],
	},
]
