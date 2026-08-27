import frappe
from frappe.query_builder.functions import Count
from frappe.utils import cint, cstr, strip

from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20


@frappe.whitelist(methods=["GET"])
def delivery_trip(limit=None, offset=None):
	"""Return every Delivery Trip with its driver, vehicle no and its delivery
	and pickup counts — the delivery stops and pickup rows on the trip.

	Query params, both optional: `limit` (default 20) and `offset` (rows to
	skip, default 0).
	"""
	try:
		delivery_trips = frappe.get_all(
			"Delivery Trip",
			fields=[
				"name AS delivery_trip_id",
				"driver_name",
				"vehicle AS vehicle_no",
				"departure_time"
			],
			order_by="departure_time desc",
			limit_page_length=cint(limit) or DEFAULT_LIMIT,
			limit_start=cint(offset),
		)

		trip_names = [trip.delivery_trip_id for trip in delivery_trips]
		delivery_counts = _child_counts(trip_names, "Delivery Stop")
		pickup_counts = _child_counts(trip_names, "Delivery Trip Pickup Detail")
		for trip in delivery_trips:
			trip["delivery"] = delivery_counts.get(trip.delivery_trip_id, 0)
			trip["pickup"] = pickup_counts.get(trip.delivery_trip_id, 0)

		return success(data=delivery_trips)
	except Exception as e:
		frappe.log_error(title="Delivery trip lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def delivery_trip_details(delivery_trip_id=None):
	"""Return one Delivery Trip with its stops, each carrying the linked
	Delivery Note, that note's Sales Invoices, customer PO and the party's
	name, address and contact.

	Query param: `delivery_trip_id` (required).
	"""
	try:
		delivery_trip_id = strip(cstr(delivery_trip_id))
		if not delivery_trip_id:
			return error("Please provide a delivery_trip_id.", 400)

		if not frappe.db.exists("Delivery Trip", delivery_trip_id):
			return error(f"Delivery Trip '{delivery_trip_id}' not found.", 404)

		trip = frappe.db.get_value(
			"Delivery Trip",
			delivery_trip_id,
			["name AS delivery_trip_id", "driver_name", "vehicle AS vehicle_no", "departure_time"],
			as_dict=True,
		)
		trip["stops"] = _trip_stops(delivery_trip_id)

		return success(data=trip)
	except Exception as e:
		frappe.log_error(title="Delivery trip details failed", message=frappe.get_traceback())
		return error(str(e), 500)


def validate_has_stops_or_pickups(doc, method=None):
	"""A trip must carry work. delivery_stops is no longer mandatory so that
	pickup-only trips can be saved, which leaves an empty trip valid otherwise.
	"""
	if not doc.get("delivery_stops") and not doc.get("pickup_details"):
		frappe.throw(frappe._("Add at least one Delivery Stop or Pickup Detail."))


def _trip_stops(delivery_trip_id):
	"""Stops of a trip, each enriched from its Delivery Note where linked."""
	stops = frappe.get_all(
		"Delivery Stop",
		filters={"parent": delivery_trip_id, "parenttype": "Delivery Trip"},
		fields=["delivery_note", "customer", "address", "contact"],
		order_by="idx",
	)

	notes = _delivery_notes([stop.delivery_note for stop in stops if stop.delivery_note])
	invoices = _sales_invoices(list(notes))
	addresses = _addresses(
		[note.customer_address for note in notes.values()] + [stop.address for stop in stops]
	)
	contacts = _contacts(
		[note.contact_person for note in notes.values()] + [stop.contact for stop in stops]
	)

	rows = []
	for stop in stops:
		note = notes.get(stop.delivery_note) or frappe._dict()
		rows.append(
			{
				"delivery_note_id": stop.delivery_note or None,
				"sales_invoices": invoices.get(stop.delivery_note, []),
				"customer_po_no": note.get("po_no") or None,
				"customer_po_date": note.get("po_date"),
				"party_name": note.get("customer_name") or stop.customer or None,
				"address": addresses.get(note.get("customer_address") or stop.address),
				"contact": contacts.get(note.get("contact_person") or stop.contact),
			}
		)
	return rows


def _delivery_notes(note_names):
	"""Delivery Notes keyed by name, with their party, PO and contact fields."""
	if not note_names:
		return {}

	notes = frappe.get_all(
		"Delivery Note",
		filters={"name": ["in", note_names]},
		fields=[
			"name",
			"customer_name",
			"po_no",
			"po_date",
			"customer_address",
			"contact_person",
		],
	)
	return {note.name: note for note in notes}


def _sales_invoices(note_names):
	"""Sales Invoice names per Delivery Note. A note can be billed across
	several invoices, so each entry is a list."""
	if not note_names:
		return {}

	rows = frappe.get_all(
		"Sales Invoice Item",
		filters={"delivery_note": ["in", note_names], "docstatus": ["<", 2]},
		fields=["delivery_note", "parent"],
		distinct=True,
	)

	invoices = {}
	for row in rows:
		invoices.setdefault(row.delivery_note, [])
		if row.parent not in invoices[row.delivery_note]:
			invoices[row.delivery_note].append(row.parent)
	return invoices


def _addresses(address_names):
	"""Addresses keyed by name, as structured fields rather than display HTML."""
	address_names = [name for name in address_names if name]
	if not address_names:
		return {}

	addresses = frappe.get_all(
		"Address",
		filters={"name": ["in", address_names]},
		fields=[
			"name",
			"address_line1",
			"address_line2",
			"city",
			"state",
			"pincode",
			"country",
			"phone",
			"email_id",
		],
	)
	return {address.pop("name"): address for address in addresses}


def _contacts(contact_names):
	"""Contacts keyed by name. Read live rather than from the Delivery Note's
	contact_* fields, which snapshot the company name as often as the person.
	"""
	contact_names = [name for name in contact_names if name]
	if not contact_names:
		return {}

	contacts = frappe.get_all(
		"Contact",
		filters={"name": ["in", contact_names]},
		fields=["name", "full_name", "mobile_no", "email_id"],
	)
	return {
		contact.name: {
			"name": contact.full_name,
			"mobile": contact.mobile_no or None,
			"email": contact.email_id or None,
		}
		for contact in contacts
	}


def _child_counts(trip_names, child_doctype):
	"""Rows of one child table per trip, keyed by trip name."""
	if not trip_names:
		return {}

	child = frappe.qb.DocType(child_doctype)
	rows = (
		frappe.qb.from_(child)
		.select(child.parent, Count(child.name).as_("total"))
		.where(child.parent.isin(trip_names) & (child.parenttype == "Delivery Trip"))
		.groupby(child.parent)
	).run(as_dict=True)

	return {row.parent: row.total for row in rows}
