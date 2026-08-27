"""Property Setters this app applies to standard doctypes."""

import frappe


def get_property_setters():
	return [
		{"doctype": "Employee", "fieldname": "gender", "property": "reqd", "value": "0"},
		{"doctype": "Employee", "fieldname": "date_of_birth", "property": "reqd", "value": "0"},
		{"doctype": "Employee", "fieldname": "date_of_joining", "property": "reqd", "value": "0"},
		# a trip may be pickup-only, so delivery stops can't be mandatory
		{"doctype": "Delivery Trip", "fieldname": "delivery_stops", "property": "reqd", "value": "0"},
	]


def create_property_setters():
	for args in get_property_setters():
		frappe.make_property_setter(args)
