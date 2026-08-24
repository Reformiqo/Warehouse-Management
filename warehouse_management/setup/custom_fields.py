"""Custom fields added by this app to standard doctypes."""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def get_custom_fields():
	return {
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
	}


def create_fields():
	create_custom_fields(get_custom_fields())
