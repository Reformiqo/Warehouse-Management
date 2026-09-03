import frappe
from erpnext.stock.doctype.delivery_note.delivery_note import make_delivery_trip
from frappe.query_builder import Order
from frappe.query_builder.functions import Coalesce, Count
from frappe.utils import cint, cstr, flt, strip

from warehouse_management.utils import strip_link_marker
from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20
ROW_DOCTYPES = {"stop": "Delivery Stop", "pickup": "Delivery Trip Pickup Detail"}
PACKING_SLIP_DOCTYPE = "Hns Packing Slip"


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
	"""Return one Delivery Trip with its stops and its pickups. A stop carries
	its Delivery Note, that note's Sales Invoices and customer PO; a pickup
	carries the Purchase Order being collected. Both carry party name,
	address and contact. is_submitted tells the client the trip is closed,
	so it can drop the mark-as-visited action.

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
			[
				"name AS delivery_trip_id",
				"driver_name",
				"vehicle AS vehicle_no",
				"departure_time",
				"docstatus",
			],
			as_dict=True,
		)
		# cancelled counts as submitted here: either way the trip is closed
		trip["is_submitted"] = trip.pop("docstatus") != 0
		trip["stops"] = _trip_stops(delivery_trip_id)
		trip["pickups"] = _trip_pickups(delivery_trip_id)

		return success(data=trip)
	except Exception as e:
		frappe.log_error(title="Delivery trip details failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def create_delivery_trip(driver_id=None, vehicle_id=None, departure_time=None, delivery_notes=None):
	"""Create one Delivery Trip as a draft through erpnext's own
	make_delivery_trip, a stop per Delivery Note — the mapping the desk runs
	under "Get stops from > Delivery Note", which checks the rest itself.

	Body: `{driver_id, vehicle_id, departure_time, delivery_notes}` — the ids
	come from driver_list and vehicle_list, delivery_notes is a list of the
	delivery_note_id values pending_delivery_notes returns, and departure_time
	reads "YYYY-MM-DD HH:mm:ss". The stops keep the order the notes are sent in.
	"""
	try:
		note_names = (
			frappe.parse_json(delivery_notes) if isinstance(delivery_notes, str) else delivery_notes
		)

		trip = frappe.new_doc("Delivery Trip")
		for note_name in note_names or []:
			make_delivery_trip(note_name, trip)

		trip.driver = driver_id
		trip.vehicle = vehicle_id
		trip.departure_time = departure_time
		trip.flags.ignore_permissions = True
		trip.insert(ignore_permissions=True)
		frappe.db.commit()

		return success(
			data={"delivery_trip_id": trip.name, "message": "Delivery trip created."},
			http_status=201,
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Delivery trip creation failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def mark_visited(
	delivery_trip_id=None, row_type=None, row_id=None, visited=1, remark=None, attachment=None
):
	"""Tick or clear visited on one stop or pickup of a Delivery Trip, with what
	the driver saw at the stop and the proof of delivery.

	Body: `{delivery_trip_id, row_type, row_id, visited, remark, attachment}` —
	row_type is "stop" or "pickup", row_id the `row_id` from the details
	response, and visited 1 to mark or 0 to clear (default 1). `remark` and
	`attachment` are for a stop only; `attachment` is the file_url that
	/api/method/upload_file gives back for the photo, uploaded before this call.
	"""
	try:
		delivery_trip_id = strip(cstr(delivery_trip_id))
		row_type = strip(cstr(row_type)).lower()
		row_id = strip(cstr(row_id))
		remark = strip(frappe.utils.strip_html(cstr(remark)))
		attachment = strip(cstr(attachment))

		validation_error = _validate_trip(delivery_trip_id)
		if validation_error:
			return validation_error

		if row_type not in ROW_DOCTYPES:
			return error("Please provide a row_type of 'stop' or 'pickup'.", 400)

		if not row_id:
			return error("Please provide a row_id.", 400)

		child_doctype = ROW_DOCTYPES[row_type]
		visited = cint(visited)
		visit = {"visited": visited}
		if remark:
			visit["remark"] = remark
		if attachment:
			visit["attachment"] = attachment
			_attach_to_trip(attachment, delivery_trip_id)

		frappe.db.set_value(child_doctype, row_id, visit)
		frappe.db.commit()

		return success(
			data={
				"delivery_trip_id": delivery_trip_id,
				"row_type": row_type,
				"row_id": row_id,
				"visited": bool(visited),
				"remark": remark or None,
				"attachment": attachment or None,
			}
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Mark visited failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def submit_delivery_trip(delivery_trip_id=None):
	"""Submit a Delivery Trip once every stop and pickup is visited.

	Body: `{delivery_trip_id}`.
	"""
	try:
		delivery_trip_id = strip(cstr(delivery_trip_id))

		validation_error = _validate_trip(delivery_trip_id)
		if validation_error:
			return validation_error

		pending = _unvisited_count(delivery_trip_id)
		if pending:
			return error(f"{pending} stop or pickup is still not marked visited.", 400)

		trip = frappe.get_doc("Delivery Trip", delivery_trip_id)
		trip.flags.ignore_permissions = True
		trip.submit()
		frappe.db.commit()

		return success(
			data={
				"delivery_trip_id": trip.name,
				"status": trip.status,
				"message": "Delivery trip submitted.",
			}
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Delivery trip submit failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def save_delivery_trip(delivery_trip_id=None, driver_id=None, vehicle_id=None):
	"""Set a new driver, a new vehicle or both on a draft Delivery Trip. The
	driver name and address on the trip refresh from the driver on save.

	Body: `{delivery_trip_id, driver_id, vehicle_id}` — the ids come from
	driver_list and vehicle_list; at least one of the two is required.
	"""
	try:
		delivery_trip_id = strip(cstr(delivery_trip_id))
		driver_id = strip(cstr(driver_id))
		vehicle_id = strip(cstr(vehicle_id))

		validation_error = _validate_trip(delivery_trip_id) or _validate_driver_and_vehicle(
			driver_id, vehicle_id
		)
		if validation_error:
			return validation_error

		trip = frappe.get_doc("Delivery Trip", delivery_trip_id)
		if trip.docstatus != 0:
			return error("A submitted delivery trip can no longer be changed.", 400)

		if driver_id:
			trip.driver = driver_id
		if vehicle_id:
			trip.vehicle = vehicle_id

		trip.flags.ignore_permissions = True
		trip.save(ignore_permissions=True)
		frappe.db.commit()

		return success(
			data={
				"delivery_trip_id": trip.name,
				"driver_id": trip.driver,
				"driver_name": trip.driver_name,
				"vehicle_no": trip.vehicle,
				"message": "Delivery trip saved.",
			}
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Delivery trip save failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def pending_delivery_notes(search=None, limit=None, offset=None):
	"""Return submitted Delivery Notes that are not on a Delivery Trip yet, so
	a trip can be planned against them. Each note carries its customer, the
	shipping and the billing address, the contact, the customer PO and the
	Sales Invoices raised against it.

	Query params, all optional: `search` (matches the note id), `limit`
	(default 20) and `offset` (rows to skip).
	"""
	try:
		search = strip_link_marker(frappe.utils.strip_html(cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		notes = _untripped_notes(search, limit, offset)
		if not notes:
			return success(data=[])

		invoices = _sales_invoice_rows([note.name for note in notes])
		addresses = _addresses(
			[note.shipping_address_name for note in notes]
			+ [note.customer_address for note in notes]
		)
		contacts = _contacts([note.contact_person for note in notes])

		return success(
			data=[
				{
					"delivery_note_id": note.name,
					"customer_name": note.customer_name or None,
					"delivery_date": note.posting_date,
					"shipping_address": addresses.get(note.shipping_address_name),
					"customer_address": addresses.get(note.customer_address),
					"contact": contacts.get(note.contact_person),
					"customer_po_no": note.po_no or None,
					"customer_po_date": note.po_date,
					"sales_invoices": invoices.get(note.name, []),
				}
				for note in notes
			]
		)
	except Exception as e:
		frappe.log_error(title="Pending delivery notes failed", message=frappe.get_traceback())
		return error(str(e), 500)


def validate_has_stops_or_pickups(doc, method=None):
	"""A trip must carry work. delivery_stops is no longer mandatory so that
	pickup-only trips can be saved, which leaves an empty trip valid otherwise.
	"""
	if not doc.get("delivery_stops") and not doc.get("pickup_details"):
		frappe.throw(frappe._("Add at least one Delivery Stop or Pickup Detail."))


def add_delivery_trip_to_purchase_order_dashboard(data):
	"""hooks.py override_doctype_dashboards target for Purchase Order: list the
	Delivery Trips collecting it. Frappe resolves the link through Delivery Trip
	Pickup Detail.purchase_order, the same way Delivery Note reaches its trips.
	"""
	data.setdefault("transactions", []).append(
		{"label": frappe._("Logistics"), "items": ["Delivery Trip"]}
	)
	return data


def _attach_to_trip(file_url, delivery_trip_id):
	"""Point a file that was uploaded on its own at the trip, so it shows there
	and goes when the trip goes. One already attached elsewhere is left alone.
	"""
	frappe.db.set_value(
		"File",
		{"file_url": file_url},
		{"attached_to_doctype": "Delivery Trip", "attached_to_name": delivery_trip_id},
	)


def _validate_trip(delivery_trip_id):
	"""Return an error, or None when the trip id is given and exists."""
	if not delivery_trip_id:
		return error("Please provide a delivery_trip_id.", 400)

	if not frappe.db.exists("Delivery Trip", delivery_trip_id):
		return error(f"Delivery Trip '{delivery_trip_id}' not found.", 404)


def _validate_driver_and_vehicle(driver_id, vehicle_id):
	"""Return an error, or None when at least one of the two is given and each
	one given exists."""
	if not driver_id and not vehicle_id:
		return error("Please provide a driver_id or a vehicle_id.", 400)

	if driver_id and not frappe.db.exists("Driver", driver_id):
		return error(f"Driver '{driver_id}' not found.", 404)

	if vehicle_id and not frappe.db.exists("Vehicle", vehicle_id):
		return error(f"Vehicle '{vehicle_id}' not found.", 404)


def _unvisited_count(delivery_trip_id):
	"""Stops plus pickups on the trip still waiting to be marked visited."""
	return sum(
		frappe.db.count(
			child_doctype,
			{"parent": delivery_trip_id, "parenttype": "Delivery Trip", "visited": 0},
		)
		for child_doctype in ROW_DOCTYPES.values()
	)


def _trip_stops(delivery_trip_id):
	"""Stops of a trip, each enriched from its Delivery Note where linked."""
	stops = frappe.get_all(
		"Delivery Stop",
		filters={"parent": delivery_trip_id, "parenttype": "Delivery Trip"},
		fields=["name", "delivery_note", "customer", "address", "contact", "visited"],
		order_by="idx",
	)

	notes = _delivery_notes([stop.delivery_note for stop in stops if stop.delivery_note])
	invoices = _sales_invoices(list(notes))
	cargo = _cargo_by_note(list(notes))
	addresses = _addresses([stop.address for stop in stops])
	contacts = _contacts([stop.contact for stop in stops])

	rows = []
	for stop in stops:
		note = notes.get(stop.delivery_note) or frappe._dict()
		rows.append(
			{
				"row_id": stop.name,
				"delivery_note_id": stop.delivery_note or None,
				"sales_invoices": invoices.get(stop.delivery_note, []),
				"customer_po_no": note.get("po_no") or None,
				"customer_po_date": note.get("po_date"),
				"party_name": note.get("customer_name") or stop.customer or None,
				"cargo": cargo.get(stop.delivery_note),
				"visited": bool(stop.visited),
				"address": addresses.get(stop.address),
				"contact": contacts.get(stop.contact),
			}
		)
	return rows


def _trip_pickups(delivery_trip_id):
	"""Pickup rows of a trip, each the Purchase Order being collected. The
	supplier, address and contact live on the trip itself — a trip collects
	from one supplier — so every row repeats them, matching a stop's shape.
	"""
	rows = frappe.get_all(
		"Delivery Trip Pickup Detail",
		filters={"parent": delivery_trip_id, "parenttype": "Delivery Trip"},
		fields=["name", "purchase_order", "supplier", "total", "visited"],
		order_by="idx",
	)
	if not rows:
		return []

	supplier, address_name, contact_name = frappe.db.get_value(
		"Delivery Trip",
		delivery_trip_id,
		["pickup_supplier", "pickup_supplier_address", "pickup_supplier_contact"],
	)
	address = _addresses([address_name]).get(address_name)
	contact = _contacts([contact_name]).get(contact_name)
	orders = _purchase_orders([row.purchase_order for row in rows if row.purchase_order])

	pickups = []
	for row in rows:
		order = orders.get(row.purchase_order) or frappe._dict()
		pickups.append(
			{
				"row_id": row.name,
				"purchase_order_id": row.purchase_order or None,
				"purchase_order_date": order.get("transaction_date"),
				"party_name": order.get("supplier_name") or row.supplier or supplier or None,
				"total": row.total,
				"visited": bool(row.visited),
				"address": address,
				"contact": contact,
			}
		)
	return pickups


def _delivery_notes(note_names):
	"""Delivery Notes keyed by name. Address and contact are not read here —
	the stop carries its own, which is what the trip was planned against.
	"""
	if not note_names:
		return {}

	notes = frappe.get_all(
		"Delivery Note",
		filters={"name": ["in", note_names]},
		fields=["name", "customer_name", "po_no", "po_date"],
	)
	return {note.name: note for note in notes}


def _untripped_notes(search, limit, offset):
	"""Submitted Delivery Notes carrying no trip, newest first. A trip stamps
	delivery_trip on its notes as it saves, but the trips this database was
	restored with mostly never got that far - so the stop rows are the check
	that holds and the stamp is a second one.

	The stops are matched on delivery_note, indexed by
	patches/add_delivery_stop_delivery_note_index.py, so the planner looks each
	candidate note up instead of reading every stop ever made. Reading the trip's
	own docstatus would cost that index - a stop already carries its trip's.
	"""
	note = frappe.qb.DocType("Delivery Note")
	stop = frappe.qb.DocType("Delivery Stop")

	# a cancelled trip's stops drop out, freeing its notes to be planned again.
	# a stop need not name a note, and one NULL would make NOT IN match nothing
	tripped = (
		frappe.qb.from_(stop)
		.select(stop.delivery_note)
		.where(
			(stop.parenttype == "Delivery Trip")
			& (stop.docstatus < 2)
			& stop.delivery_note.isnotnull()
		)
	)

	query = (
		frappe.qb.from_(note)
		.select(
			note.name,
			note.customer_name,
			note.posting_date,
			note.shipping_address_name,
			note.customer_address,
			note.contact_person,
			note.po_no,
			note.po_date,
		)
		.where(
			(note.docstatus == 1)
			& (Coalesce(note.delivery_trip, "") == "")
			& note.name.notin(tripped)
		)
	)

	if search:
		query = query.where(note.name.like(f"%{search}%"))

	# posting_date is indexed, so the newest rows are walked in order and the
	# scan stops at the page - no sort over the whole table
	return (
		query.orderby(note.posting_date, order=Order.desc)
		.orderby(note.name, order=Order.desc)
		.limit(limit)
		.offset(offset)
	).run(as_dict=True)


def _sales_invoice_rows(note_names):
	"""[{sales_invoice_id, sales_invoice_date}, ...] per Delivery Note, for the
	notes that are billed - a note can be billed across several invoices."""
	invoices = _sales_invoices(note_names)
	invoice_names = {name for names in invoices.values() for name in names}
	if not invoice_names:
		return {}

	dates = dict(
		frappe.get_all(
			"Sales Invoice",
			filters={"name": ["in", list(invoice_names)]},
			fields=["name", "posting_date"],
			as_list=True,
		)
	)
	return {
		note: [
			{"sales_invoice_id": name, "sales_invoice_date": dates.get(name)} for name in names
		]
		for note, names in invoices.items()
	}


def _sales_invoices(note_names):
	"""Sales Invoice names per Delivery Note, read off the note's own items.
	A note can be billed across several invoices, so each entry is a list."""
	if not note_names:
		return {}

	rows = frappe.get_all(
		"Delivery Note Item",
		filters={"parent": ["in", note_names], "against_sales_invoice": ["is", "set"]},
		fields=["parent AS delivery_note", "against_sales_invoice AS sales_invoice"],
		distinct=True,
	)
	invoices = _group_invoices(rows)

	# against_sales_invoice is only filled when the note was raised from the
	# invoice; billed the other way round, the link sits on the invoice's items.
	billed_after = [name for name in note_names if name not in invoices]
	if billed_after:
		rows = frappe.get_all(
			"Sales Invoice Item",
			filters={"delivery_note": ["in", billed_after], "docstatus": ["<", 2]},
			fields=["delivery_note", "parent AS sales_invoice"],
			distinct=True,
		)
		invoices.update(_group_invoices(rows))

	return invoices


def _group_invoices(rows):
	"""{delivery_note: [sales_invoice, ...]}, duplicates dropped."""
	invoices = {}
	for row in rows:
		invoices.setdefault(row.delivery_note, [])
		if row.sales_invoice not in invoices[row.delivery_note]:
			invoices[row.delivery_note].append(row.sales_invoice)
	return invoices


def _cargo_by_note(note_names):
	"""What each Delivery Note's Pick List was packed into: item count, boxes
	and weight. A box repeats its weight on every item row it holds, so the
	weight is taken once per box rather than summed across rows.
	"""
	if not note_names:
		return {}

	links = frappe.get_all(
		"Delivery Note Item",
		filters={"parent": ["in", note_names], "against_pick_list": ["is", "set"]},
		fields=["parent AS delivery_note", "against_pick_list AS pick_list"],
		distinct=True,
	)
	if not links:
		return {}

	pick_lists_by_note, notes_by_pick_list = {}, {}
	for link in links:
		pick_lists_by_note.setdefault(link.delivery_note, []).append(link.pick_list)
		notes_by_pick_list.setdefault(link.pick_list, []).append(link.delivery_note)

	rows = frappe.get_all(
		PACKING_SLIP_DOCTYPE,
		filters={"parent": ["in", list(notes_by_pick_list)], "parenttype": "Pick List"},
		fields=["parent AS pick_list", "item", "qty", "box_number", "box_weight"],
	)

	totals = {note: {"items": set(), "boxes": {}} for note in pick_lists_by_note}
	for row in rows:
		for note in notes_by_pick_list[row.pick_list]:
			total = totals[note]
			total["items"].add(row.item)
			box = (row.pick_list, row.box_number)
			total["boxes"][box] = max(flt(row.box_weight), total["boxes"].get(box, 0.0))

	return {
		note: {
			"total_items": len(total["items"]),
			"total_boxes": len(total["boxes"]),
			"total_weight": sum(total["boxes"].values()),
		}
		for note, total in totals.items()
	}


def _purchase_orders(po_names):
	"""Purchase Orders keyed by name, with the supplier name and order date
	the pickup row itself does not store."""
	if not po_names:
		return {}

	orders = frappe.get_all(
		"Purchase Order",
		filters={"name": ["in", po_names]},
		fields=["name", "supplier_name", "transaction_date"],
	)
	return {order.name: order for order in orders}


def _addresses(address_names):
	"""Addresses keyed by name, each flattened to one comma-joined line with
	the blank parts dropped."""
	address_names = [name for name in address_names if name]
	if not address_names:
		return {}

	addresses = frappe.get_all(
		"Address",
		filters={"name": ["in", address_names]},
		fields=["name", "address_line1", "address_line2", "city", "state", "pincode"],
	)
	return {
		address.name: ", ".join(
			part
			for part in (
				address.address_line1,
				address.address_line2,
				address.city,
				address.state,
				address.pincode,
			)
			if part
		)
		or None
		for address in addresses
	}


def _contacts(contact_names):
	"""Phone number per contact, mobile first and landline as the fallback.
	Read live rather than from the snapshotted contact_* fields on the parent.
	"""
	contact_names = [name for name in contact_names if name]
	if not contact_names:
		return {}

	contacts = frappe.get_all(
		"Contact",
		filters={"name": ["in", contact_names]},
		fields=["name", "mobile_no", "phone"],
	)
	phones = {
		contact.name: contact.mobile_no or contact.phone
		for contact in contacts
		if contact.mobile_no or contact.phone
	}

	# Frappe only lifts a number onto the Contact once its row is flagged
	# primary, so read the child table for the ones still left empty.
	unflagged = [contact.name for contact in contacts if contact.name not in phones]
	if unflagged:
		rows = frappe.get_all(
			"Contact Phone",
			filters={"parent": ["in", unflagged], "parenttype": "Contact"},
			fields=["parent", "phone"],
			order_by="is_primary_mobile_no desc, is_primary_phone desc, idx",
		)
		for row in rows:
			phones.setdefault(row.parent, row.phone)

	return phones


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
