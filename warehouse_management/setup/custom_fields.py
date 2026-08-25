"""Custom fields added by this app to standard doctypes."""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# Frappe has no native submit timestamp, and Pick List has no posting_date
# either, so this is the one field orderable across all four doctypes.
SUBMITTED_AT_DOCTYPES = ["Purchase Receipt", "Delivery Note", "Pick List", "Stock Entry"]

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
	}
	for doctype in SUBMITTED_AT_DOCTYPES:
		fields[doctype] = [dict(SUBMITTED_AT_FIELD)]

	return fields


def create_fields():
	create_custom_fields(get_custom_fields())
