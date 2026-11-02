"""Give existing orders a private link, and point customer emails at it."""

import frappe

OLD_LINK = '/order/{{ doc.name }}"'
NEW_LINK = '/order/{{ doc.name }}/{{ doc.order_key }}"'
NOTIFICATIONS = ("Akwaaba Order Placed (Customer)", "Akwaaba Payment Received (Customer)")


def execute():
	from akwaaba_theme.install import _create_custom_fields, _ensure_momo_confirm_client_script

	_create_custom_fields()
	_ensure_momo_confirm_client_script()

	for name in frappe.get_all("Sales Order", filters={"order_key": ["is", "not set"]}, pluck="name"):
		frappe.db.set_value("Sales Order", name, "order_key", frappe.generate_hash(length=24), update_modified=False)

	for name in NOTIFICATIONS:
		message = frappe.db.get_value("Notification", name, "message")
		if message and OLD_LINK in message:
			frappe.db.set_value("Notification", name, "message", message.replace(OLD_LINK, NEW_LINK), update_modified=False)
