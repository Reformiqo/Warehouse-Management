"""Add Sales Order.pick_list and fill it in for the orders already picked, so
the field reads true for the existing data rather than only for new pick lists.
"""

import frappe

from warehouse_management.setup.custom_fields import create_fields


def execute():
	create_fields()
	# create_fields skips the schema when nothing changed, which a rerun after a
	# half-applied patch would do - leaving the column missing
	frappe.db.updatedb("Sales Order")

	rows = frappe.get_all(
		"Pick List Item",
		filters={"docstatus": ["<", 2], "sales_order": ["is", "set"]},
		fields=["sales_order", "parent"],
		order_by="creation",
	)
	# a later row overwrites, so each order keeps its newest live pick list
	for sales_order, pick_list in {row.sales_order: row.parent for row in rows}.items():
		frappe.db.set_value(
			"Sales Order", sales_order, "pick_list", pick_list, update_modified=False
		)
