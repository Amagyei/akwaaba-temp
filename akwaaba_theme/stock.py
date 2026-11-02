"""Which pieces can still be bought.

Some pieces are made to order and never run out; others are tracked, often
one of a kind. A tracked piece is an Item with `is_stock_item` set, and what's left
is what's on the shelf minus what paid orders have reserved. Stock leaves on the
Delivery Note. `Website Item.on_backorder` stays a manual override on top of this.
"""

import frappe
from frappe.utils import flt


def stock_warehouse():
	return frappe.db.get_single_value("Stock Settings", "default_warehouse")


def available(item_codes, for_update=False):
	"""{item_code: quantity left} for the tracked ones among these. Made-to-order
	items aren't in the result, because they have no limit.

	`for_update` locks their stock rows until the transaction ends, so two orders
	paying at once can't both take the last piece.
	"""
	item_codes = list(item_codes)
	if not item_codes:
		return {}
	tracked = frappe.get_all("Item", filters={"name": ["in", item_codes], "is_stock_item": 1}, pluck="name")
	if not tracked:
		return {}
	lock = " FOR UPDATE" if for_update else ""
	rows = frappe.db.sql(
		f"""SELECT item_code, SUM(actual_qty - reserved_qty) FROM `tabBin`
		WHERE item_code IN %(codes)s AND warehouse = %(warehouse)s
		GROUP BY item_code{lock}""",
		{"codes": tuple(tracked), "warehouse": stock_warehouse()},
	)
	left = {code: flt(qty) for code, qty in rows}
	return {code: left.get(code, 0.0) for code in tracked}


def short_lines(wanted, for_update=False):
	"""The {item_code: quantity} lines that more than what's left is asked for."""
	left = available(wanted, for_update=for_update)
	return [code for code, qty in wanted.items() if code in left and left[code] < flt(qty)]


def sold_out_codes(item_codes):
	return {code for code, qty in available(item_codes).items() if qty <= 0}
