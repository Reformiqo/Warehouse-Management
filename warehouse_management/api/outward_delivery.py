import frappe
from frappe.utils import cint, nowdate

from warehouse_management.utils import strip_link_marker
from warehouse_management.utils.response import error, success

DELIVERY_DOCTYPE = "Hns Outward Delivery"
MISC_MASTER_DOCTYPE = "Hns Misc Master Details"
CUSTOM_PO_DOCTYPE = "Hns Custom Po"
DEFAULT_LIMIT = 20

# each Hns Misc Master Details picker on the doctype is one master group - the
# same filter the desk link_filters apply
DISPATCHED_BY_GROUP = "Dispatched By"
RACK_GROUP = "Rack"
PURPOSE_GROUP = "Purpose"

# reqd = 1 on the doctype, less `date` which defaults to today here as it does
# on the form. Every other field is optional.
REQUIRED_FIELDS = ("customer_name", "city", "total_box", "dispatched_by")

# columns a caller may set - `customer` is read_only and fetched from
# customer_name, so Frappe fills it in rather than the caller posting it
WRITABLE_FIELDS = (
	"naming_series",
	"date",
	"delivery_challan",
	"delivery_note",
	"hns_custom_po",
	"customer_name",
	"city",
	"packed_by",
	"courier_name",
	"docket_no",
	"total_box",
	"dispatched_by",
	"location_from",
	"rack_no",
	"gst_no",
	"received_person_name",
	"received_person_mobile",
	"customer_mobile_no",
	"customer_email_address",
	"purpose",
	"remark",
	"file_upload",
)

# link pickers post a value as "id^^Doctype", so these are unwrapped before use
LINK_FIELDS = (
	"delivery_note",
	"hns_custom_po",
	"customer_name",
	"dispatched_by",
	"location_from",
	"rack_no",
	"purpose",
)

LIST_FIELDS = (
	"name as outward_delivery_id",
	"date",
	"customer_name",
	"customer",
	"city",
	"delivery_note",
	"docket_no",
	"courier_name",
	"total_box",
	"dispatched_by",
)
DETAIL_FIELDS = ("name as outward_delivery_id", "customer", *WRITABLE_FIELDS)


@frappe.whitelist(methods=["POST"])
def create_outward_delivery(
	customer_name=None,
	city=None,
	total_box=None,
	dispatched_by=None,
	date=None,
	naming_series=None,
	delivery_challan=None,
	delivery_note=None,
	hns_custom_po=None,
	packed_by=None,
	courier_name=None,
	docket_no=None,
	location_from=None,
	rack_no=None,
	gst_no=None,
	received_person_name=None,
	received_person_mobile=None,
	customer_mobile_no=None,
	customer_email_address=None,
	purpose=None,
	remark=None,
	file_upload=None,
):
	"""Create one Hns Outward Delivery.

	Body: `customer_name` (a Customer id), `city`, `total_box` and
	`dispatched_by` are required, `date` defaults to today and everything else
	is optional. `file_upload` is a url from /api/method/upload_file, which is
	also relinked to the delivery so it shows in its attachments.
	"""
	try:
		if not frappe.db.exists("DocType", DELIVERY_DOCTYPE):
			return error(f"{DELIVERY_DOCTYPE} is not available on this site", 404)

		values = _clean(
			{
				"customer_name": customer_name,
				"city": city,
				"total_box": total_box,
				"dispatched_by": dispatched_by,
				"date": date or nowdate(),
				"naming_series": naming_series,
				"delivery_challan": delivery_challan,
				"delivery_note": delivery_note,
				"hns_custom_po": hns_custom_po,
				"packed_by": packed_by,
				"courier_name": courier_name,
				"docket_no": docket_no,
				"location_from": location_from,
				"rack_no": rack_no,
				"gst_no": gst_no,
				"received_person_name": received_person_name,
				"received_person_mobile": received_person_mobile,
				"customer_mobile_no": customer_mobile_no,
				"customer_email_address": customer_email_address,
				"purpose": purpose,
				"remark": remark,
				"file_upload": file_upload,
			}
		)

		validation_error = _validate_delivery(values)
		if validation_error:
			return validation_error

		delivery = frappe.get_doc({"doctype": DELIVERY_DOCTYPE, **values})
		delivery.flags.ignore_permissions = True
		delivery.insert(ignore_permissions=True)
		_attach_file(delivery.name, values.get("file_upload"))
		frappe.db.commit()

		return success(
			data={"outward_delivery_id": delivery.name, "message": "Outward delivery created."},
			http_status=201,
		)
	except frappe.ValidationError as e:
		frappe.db.rollback()
		return error(str(e), 400)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Outward delivery creation failed", message=frappe.get_traceback())
		return error(str(e), 500)


# @frappe.whitelist(methods=["GET"])
# def outward_delivery_list(
# 	customer=None, delivery_note=None, from_date=None, to_date=None, search=None, limit=None, offset=None
# ):
# 	"""Return Hns Outward Delivery rows, newest first.

# 	Query params, all optional: `customer` (a Customer id), `delivery_note`,
# 	`from_date` and `to_date` (both on the delivery date), `search` (matches the
# 	delivery id, the docket no or the customer name), `limit` (default 20) and
# 	`offset` (rows to skip).
# 	"""
# 	try:
# 		if not frappe.db.exists("DocType", DELIVERY_DOCTYPE):
# 			return error(f"{DELIVERY_DOCTYPE} is not available on this site", 404)

# 		customer = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(customer)))
# 		delivery_note = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(delivery_note)))
# 		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
# 		from_date = frappe.utils.strip(frappe.utils.cstr(from_date))
# 		to_date = frappe.utils.strip(frappe.utils.cstr(to_date))
# 		limit = cint(limit) or DEFAULT_LIMIT
# 		offset = cint(offset)

# 		filters = []
# 		if customer:
# 			filters.append(["customer_name", "=", customer])
# 		if delivery_note:
# 			filters.append(["delivery_note", "=", delivery_note])
# 		if from_date:
# 			filters.append(["date", ">=", from_date])
# 		if to_date:
# 			filters.append(["date", "<=", to_date])

# 		# the three searchable columns must OR together - as filters they would
# 		# AND and match nothing
# 		or_filters = {}
# 		if search:
# 			or_filters = {
# 				"name": ["like", f"%{search}%"],
# 				"docket_no": ["like", f"%{search}%"],
# 				"customer": ["like", f"%{search}%"],
# 			}

# 		deliveries = frappe.get_all(
# 			DELIVERY_DOCTYPE,
# 			filters=filters,
# 			or_filters=or_filters,
# 			fields=list(LIST_FIELDS),
# 			order_by="date desc, creation desc",
# 			limit_start=offset,
# 			limit_page_length=limit,
# 		)
# 		return success(data=deliveries)
# 	except Exception as e:
# 		frappe.log_error(title="Outward delivery list failed", message=frappe.get_traceback())
# 		return error(str(e), 500)


# @frappe.whitelist(methods=["GET"])
# def outward_delivery_detail(outward_delivery_id=None):
# 	"""Return one Hns Outward Delivery in full. `outward_delivery_id` is
# 	required and is the id create_outward_delivery hands back.
# 	"""
# 	try:
# 		if not frappe.db.exists("DocType", DELIVERY_DOCTYPE):
# 			return error(f"{DELIVERY_DOCTYPE} is not available on this site", 404)

# 		outward_delivery_id = strip_link_marker(
# 			frappe.utils.strip_html(frappe.utils.cstr(outward_delivery_id))
# 		)
# 		if not outward_delivery_id:
# 			return error("Please provide an outward_delivery_id.", 400)

# 		deliveries = frappe.get_all(
# 			DELIVERY_DOCTYPE,
# 			filters={"name": outward_delivery_id},
# 			fields=list(DETAIL_FIELDS),
# 			limit_page_length=1,
# 		)
# 		if not deliveries:
# 			return error(f"Outward delivery '{outward_delivery_id}' not found.", 404)

# 		return success(data=deliveries[0])
# 	except Exception as e:
# 		frappe.log_error(title="Outward delivery detail failed", message=frappe.get_traceback())
# 		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def delivery_note_list(customer=None, search=None, limit=None, offset=None):
	"""Return submitted Delivery Notes, for the delivery_note picker. Query
	params, all optional: `customer` (a Customer id), `search` (matches the
	delivery note id), `limit` (default 20) and `offset` (rows to skip).
	"""
	try:
		customer = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(customer)))
		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		filters = {"docstatus": 1}
		if customer:
			filters["customer"] = customer
		if search:
			filters["name"] = ["like", f"%{search}%"]

		delivery_notes = frappe.get_all(
			"Delivery Note",
			filters=filters,
			fields=["name as delivery_note_id", "customer", "customer_name", "posting_date"],
			order_by="posting_date desc",
			limit_start=offset,
			limit_page_length=limit,
		)
		return success(data=delivery_notes)
	except Exception as e:
		frappe.log_error(title="Delivery note list failed", message=frappe.get_traceback())
		return error(str(e), 500)



@frappe.whitelist(methods=["GET"])
def custom_po_list(search=None, limit=None, offset=None):
	"""Return Hns Custom Po records, for the hns_custom_po picker. Query params,
	all optional: `search` (matches the record id or the po no), `limit`
	(default 20) and `offset` (rows to skip).
	"""
	try:
		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		if not frappe.db.exists("DocType", CUSTOM_PO_DOCTYPE):
			return error(f"{CUSTOM_PO_DOCTYPE} is not available on this site", 404)

		filters = {}
		if search:
			filters = {"name": ["like", f"%{search}%"]}

		custom_pos = frappe.get_all(
			CUSTOM_PO_DOCTYPE,
			filters=filters,
			pluck="name",
			order_by="transaction_date desc",
			limit_start=offset,
			limit_page_length=limit,
		)
		return success(data=custom_pos)
	except Exception as e:
		frappe.log_error(title="Custom po list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def location_list(search=None, limit=None, offset=None):
	"""Return Locations, for the location_from picker. Query params, all
	optional: `search` (matches the location name), `limit` (default 20) and
	`offset` (rows to skip).
	"""
	try:
		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		filters = {}
		if search:
			filters["location_name"] = ["like", f"%{search}%"]

		locations = frappe.get_all(
			"Location",
			filters=filters,
			fields=["name as location_id", "location_name"],
			order_by="location_name",
			limit_start=offset,
			limit_page_length=limit,
		)
		return success(data=locations)
	except Exception as e:
		frappe.log_error(title="Location list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def dispatched_by_list(search=None, limit=None, offset=None):
	"""Return the Hns Misc Master Details rows of the Dispatched By master, for
	the dispatched_by picker. Query params, all optional: `search` (matches the
	value), `limit` (default 20) and `offset` (rows to skip).
	"""
	return _misc_master_options(DISPATCHED_BY_GROUP, search, limit, offset)


@frappe.whitelist(methods=["GET"])
def rack_list(search=None, limit=None, offset=None):
	"""Return the Hns Misc Master Details rows of the Rack master, for the
	rack_no picker. Query params, all optional: `search` (matches the value),
	`limit` (default 20) and `offset` (rows to skip).
	"""
	return _misc_master_options(RACK_GROUP, search, limit, offset)


@frappe.whitelist(methods=["GET"])
def purpose_list(search=None, limit=None, offset=None):
	"""Return the Hns Misc Master Details rows of the Purpose master, for the
	purpose picker. Query params, all optional: `search` (matches the value),
	`limit` (default 20) and `offset` (rows to skip).
	"""
	return _misc_master_options(PURPOSE_GROUP, search, limit, offset)


@frappe.whitelist(methods=["GET"])
def delivery_challan_list():
	"""Return the delivery_challan options - Erp and Zebra. No input required."""
	return _select_options("delivery_challan")


@frappe.whitelist(methods=["GET"])
def naming_series_list():
	"""Return the series the delivery can be numbered with, for the
	naming_series picker. No input required.
	"""
	return _select_options("naming_series")


def _clean(values):
	"""Strip each posted value, unwrap "id^^Doctype" on the link fields and drop
	the blanks, so an optional field is left unset rather than written empty.
	"""
	cleaned = {}
	for field, value in values.items():
		value = frappe.utils.strip_html(frappe.utils.cstr(value))
		value = strip_link_marker(value) if field in LINK_FIELDS else frappe.utils.strip(value)
		if value:
			cleaned[field] = value

	return cleaned


def _validate_delivery(values):
	"""400 error for a missing required field or a box count below 1, None when
	the payload is good.
	"""
	missing = [field for field in REQUIRED_FIELDS if not values.get(field)]
	if missing:
		return error(f"Please provide {', '.join(missing)}.", 400)

	if cint(values["total_box"]) < 1:
		return error("total_box must be a positive number.", 400)

	return None


def _attach_file(delivery, file_url):
	"""Point an already-uploaded File at this delivery, so the url set on
	file_upload also shows in the attachments sidebar.
	"""
	if not file_url:
		return

	frappe.db.set_value(
		"File",
		{"file_url": file_url},
		{"attached_to_doctype": DELIVERY_DOCTYPE, "attached_to_name": delivery},
	)


def _misc_master_options(group, search=None, limit=None, offset=None):
	"""The Hns Misc Master Details rows of one master, keyed the same way as
	lists.misc_master_list so every misc master picker reads alike.
	"""
	try:
		if not frappe.db.exists("DocType", MISC_MASTER_DOCTYPE):
			return error(f"{MISC_MASTER_DOCTYPE} is not available on this site", 404)

		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		filters = {"misc_master_name": group}
		if search:
			filters["value"] = ["like", f"%{search}%"]

		masters = frappe.get_all(
			MISC_MASTER_DOCTYPE,
			filters=filters,
			fields=["record_id as name", "value"],
			order_by="value",
			limit_start=offset,
			limit_page_length=limit,
		)
		return success(data=masters)
	except Exception as e:
		frappe.log_error(title=f"{group} list failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _select_options(fieldname):
	"""The Select options of one field, read off the meta so a Property Setter
	that changes them on a site is picked up.
	"""
	try:
		if not frappe.db.exists("DocType", DELIVERY_DOCTYPE):
			return error(f"{DELIVERY_DOCTYPE} is not available on this site", 404)

		field = frappe.get_meta(DELIVERY_DOCTYPE).get_field(fieldname)
		if not field:
			return error(f"{DELIVERY_DOCTYPE} has no {fieldname} field", 404)

		values = frappe.utils.cstr(field.options).split("\n")
		return success(data=[value.strip() for value in values if value.strip()])
	except Exception as e:
		frappe.log_error(title=f"Outward delivery {fieldname} list failed", message=frappe.get_traceback())
		return error(str(e), 500)