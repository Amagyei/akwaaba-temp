app_name = "akwaaba_theme"
app_title = "Serwa Kollections Theme"
app_publisher = "Serwa Kollections"
app_description = "Ghana editorial luxury clothing storefront theme."
app_email = "amagyei@example.com"
app_license = "mit"

# Assets injected into every website page (includes webshop).
web_include_css = [
	"/assets/akwaaba_theme/css/akwaaba.css",
]
web_include_js = [
	"/assets/akwaaba_theme/js/akwaaba.js",
]

# Homepage: serves www/index.html at "/"
home_page = "index"
website_route_rules = [
    {"from_route": "/", "to_route": "index"},
    # Route /order/<any-slug> to the order confirmation controller. The key is
    # the secret part of a customer's link; without it the page shows no details.
    {"from_route": "/order/<order_name>/<order_key>", "to_route": "order/detail"},
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
# Keeps custom fields current on live sites, and seeds Studio prompts and house
# style on a fresh deploy.
after_migrate = [
	"akwaaba_theme.install.ensure_schema",
	"akwaaba_theme.install.ensure_studio_defaults",
]

before_tests = "akwaaba_theme.tests.utils.before_tests"

scheduler_events = {
	"cron": {
		# Bring home drawings the image Mac has finished, whenever it was reachable.
		"* * * * *": ["akwaaba_theme.studio.api.collect_host_jobs"],
	},
	"daily": ["akwaaba_theme.api.retry_unsettled_orders"],
	"weekly": ["akwaaba_theme.studio.api.cleanup_completed_sessions"],
}
