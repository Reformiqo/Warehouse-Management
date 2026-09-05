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
		DELIVERY_STOP_NOTE_INDEX,
		ITEM_BARCODE_LABEL_DEFAULT,
	]


def create_property_setters():
	for args in get_property_setters():
		frappe.make_property_setter(args)
