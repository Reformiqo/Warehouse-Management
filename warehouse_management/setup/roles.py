"""Roles this app creates — one per app module, plus Operator and Admin.

They carry no permissions of their own; they mark what a user is allowed to
open in the mobile app, which reads them back from the profile API.
"""

import frappe

APP_ROLES = [
	"Item Enquiry",
	"Warehouse Enquiry",
	"Stock Arrival",
	"Purchase Receipt",
	"Material Transfer",
	"Pick List",
	"Packing List",
	"Delivery Trip",
	"Outward Delivery",
	"Mobile App Operator",
	"Mobile App Admin",
]


def create_roles():
	"""hooks.py after_install/after_migrate target. A role that already exists
	is left untouched, so one disabled or edited on site stays that way.
	"""
	existing = set(frappe.get_all("Role", filters={"name": ["in", APP_ROLES]}, pluck="name"))

	for role in APP_ROLES:
		if role in existing:
			continue

		frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)
