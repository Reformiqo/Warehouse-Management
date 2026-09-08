"""Custom fields added by this app to standard doctypes."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# Frappe has no native submit timestamp, and Pick List has no posting_date
# either, so this is the one field orderable across every doctype below.
# Delivery Trip has no posting_time to sit after, hence the per doctype anchor.
SUBMITTED_AT_DOCTYPES = {
	"Purchase Receipt": "posting_time",
	"Delivery Note": "posting_time",
	"Pick List": "posting_time",
	"Stock Entry": "posting_time",
	"Delivery Trip": "departure_time",
}

SUBMITTED_AT_FIELD = {
	"fieldname": "submitted_at",
	"label": "Submitted At",
	"fieldtype": "Datetime",
	"read_only": 1,
	"no_copy": 1,
	"print_hide": 1,
	"search_index": 1,
}


def get_custom_fields():
	fields = {
		"Warehouse": [
			{
				"fieldname": "initial_reconciliation",
				"label": "Initial Reconciliation",
				"fieldtype": "Int",
				"default": "1",
				"insert_after": "disabled",
				"depends_on": "eval:!doc.is_group",
			},
		],
		"Item": [
			{
				"fieldname": "reconciliation_section",
				"label": "Last Reconciliation",
				"fieldtype": "Section Break",
				"insert_after": "image",
				"collapsible": 1,
			},
			{
				"fieldname": "warehouse_reconciliation",
				"label": "Warehouse Reconciliation",
				"fieldtype": "Table",
				"options": "Warehouse Item Reconciliation",
				"insert_after": "reconciliation_section",
				"read_only": 1,
				"no_copy": 1,
			},
		],
		"Stock Reconciliation Item": [
			{
				"fieldname": "reconciliation_image_1",
				"label": "Reconciliation Image 1",
				"fieldtype": "Attach",
				"insert_after": "current_amount",
				"no_copy": 1,
			},
			{
				"fieldname": "reconciliation_image_2",
				"label": "Reconciliation Image 2",
				"fieldtype": "Attach",
				"insert_after": "reconciliation_image_1",
				"no_copy": 1,
			},
			{
				"fieldname": "reconciliation_image_3",
				"label": "Reconciliation Image 3",
				"fieldtype": "Attach",
				"insert_after": "reconciliation_image_2",
				"no_copy": 1,
			},
		],
		# a driver notes what happened at the stop and attaches proof of delivery,
		# and the stop is checked off twice on the way — out of the warehouse,
		# then into the customer's hands
		"Delivery Stop": [
			{
				"fieldname": "released_from_warehouse",
				"label": "Released from Warehouse",
				"fieldtype": "Check",
				"default": "0",
				"insert_after": "visited",
				"allow_on_submit": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "delivered_to_customer",
				"label": "Delivered to Customer",
				"fieldtype": "Check",
				"default": "0",
				"insert_after": "released_from_warehouse",
				"allow_on_submit": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "remark",
				"label": "Remark",
				"fieldtype": "Data",
				"insert_after": "details",
			},
			{
				"fieldname": "attachment",
				"label": "Attachment",
				"fieldtype": "Attach",
				"insert_after": "remark",
			},
		],
		# the pick list an order is being picked on, kept by api/pick_list.py
		"Sales Order": [
			{
				"fieldname": "pick_list",
				"label": "Pick List",
				"fieldtype": "Link",
				"options": "Pick List",
				"insert_after": "per_picked",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1,
			},
		],
		"User": [
			{
				"fieldname": "mpin",
				"label": "MPIN",
				"fieldtype": "Password",
				"insert_after": "api_secret",
				"no_copy": 1,
				"print_hide": 1,
			},
		],
		# a trip collects from one supplier, so the supplier sits on the trip
		# and the table only lists that supplier's purchase orders
		"Delivery Trip": [
			{
				"fieldname": "pickup_details_section",
				"label": "Pick Up Details",
				"fieldtype": "Section Break",
				"insert_after": "optimize_route",
				"collapsible": 0,
			},
			{
				"fieldname": "pickup_supplier",
				"label": "Supplier",
				"fieldtype": "Link",
				"options": "Supplier",
				"insert_after": "pickup_details_section",
			},
			{
				"fieldname": "pickup_supplier_address",
				"label": "Supplier Address",
				"fieldtype": "Link",
				"options": "Address",
				"insert_after": "pickup_supplier",
				"depends_on": "pickup_supplier",
			},
			{
				"fieldname": "pickup_supplier_contact",
				"label": "Supplier Contact",
				"fieldtype": "Link",
				"options": "Contact",
				"insert_after": "pickup_supplier_address",
				"depends_on": "pickup_supplier",
			},
			{
				"fieldname": "pickup_details",
				"label": "Pickup Details",
				"fieldtype": "Table",
				"options": "Delivery Trip Pickup Detail",
				"insert_after": "pickup_supplier_contact",
			},
		],
	}
	# appended, so a doctype that already has fields above keeps them
	for doctype, insert_after in SUBMITTED_AT_DOCTYPES.items():
		fields.setdefault(doctype, []).append({**SUBMITTED_AT_FIELD, "insert_after": insert_after})

	return fields


def create_fields():
	create_custom_fields(get_custom_fields())
