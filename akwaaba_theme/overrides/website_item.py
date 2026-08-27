"""Override for webshop's Website Item.

Website Item is a WebsiteGenerator: on `published=1` it renders webshop's
stock item page (templates/generators/item/item.html) at its own route.
That page clashes with the Akwaaba theme, so we 301 it to our bespoke
`/product/<route>` page — leaving exactly one, fully-themed product page.

Everything else about Website Item is inherited unchanged. Registered via
`override_doctype_class` in hooks.py. Webshop overrides Item / Item Group /
Payment Request but NOT Website Item, so there is no override conflict.
"""

import frappe
from webshop.webshop.doctype.website_item.website_item import WebsiteItem


class AkwaabaWebsiteItem(WebsiteItem):
	def get_context(self, context):
		# Redirect the stock route → our themed product page. Prefer the
		# clean web route; fall back to the item code if a route is unset.
		slug = self.route or self.item_code or self.name
		frappe.local.flags.redirect_location = "/product/" + slug.strip("/")
		raise frappe.Redirect
