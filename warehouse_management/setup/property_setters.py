"""Property Setters this app applies to standard doctypes."""

import frappe

# pending_delivery_notes looks a note up by stop, not a stop by trip, so the
# index this drives is added by patches/add_delivery_stop_delivery_note_index.py
DELIVERY_STOP_NOTE_INDEX = {
	"doctype": "Delivery Stop",
	"fieldname": "delivery_note",
	"property": "search_index",
	"value": "1",
}


# so print/preview reaches for the 30x20 label without the caller naming a format
ITEM_BARCODE_LABEL_DEFAULT = {
	"doctype": "Item",
	"doctype_or_field": "DocType",
	"property": "default_print_format",
	"property_type": "Data",
	"value": "Item Barcode Label 30x20",
}


def get_property_setters():
	return [
		{"doctype": "Employee", "fieldname": "gender", "property": "reqd", "value": "0"},
		{"doctype": "Employee", "fieldname": "date_of_birth", "property": "reqd", "value": "0"},
		{"doctype": "Employee", "fieldname": "date_of_joining", "property": "reqd", "value": "0"},
		# a trip may be pickup-only, so delivery stops can't be mandatory
		{"doctype": "Delivery Trip", "fieldname": "delivery_stops", "property": "reqd", "value": "0"},

		{
			"doctype": "Purchase Receipt",
			"fieldname": "custom_order_type",
			"property": "reqd",
			"value": "0",
		},
		DELIVERY_STOP_NOTE_INDEX,
		ITEM_BARCODE_LABEL_DEFAULT,
	]


def create_property_setters():
	for args in get_property_setters():
		# custom_order_type and friends come from other apps, so on a fresh site
		# they may not exist yet and Property Setter validation would abort setup
		if args.get("doctype_or_field", "DocField") == "DocField" and not frappe.get_meta(
			args["doctype"]
		).has_field(args["fieldname"]):
			continue

		# Employee carries orphan link fields from an uninstalled app, and a full
		# doctype revalidation on every insert would throw on them; we only set
		# reqd/search_index here, so nothing needs that check
		frappe.make_property_setter(args, validate_fields_for_doctype=False)
