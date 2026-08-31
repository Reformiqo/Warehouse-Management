import frappe
from frappe.utils import cint

from warehouse_management.utils.response import error, success

ENTRY_TYPE_DOCTYPE = "Hns Stock Arrival"
ENTRY_TYPE_FIELD = "entry_type"
ARRIVAL_LOCATION_FIELD = "arrival_location"
SOURCE_DOCTYPE = "Hns Misc Master Details"
SOURCE_MISC_MASTER = "Delivery By"
DEFAULT_LIMIT = 20

# reqd = 1 on the doctype, plus party_type - supplier_name is a Dynamic Link and
# cannot resolve without it. Every other field is optional.
REQUIRED_FIELDS = (
	"entry_type",
	"transaction_date",
	"time",
	"party_type",
	"supplier_name",
	"city",
	"received_by",
	"source",
	"courier_name",
	"total_boxes",
	"brand",
	"original_invoice_received",
)


@frappe.whitelist(methods=["GET"])
def entry_type_list():
	try:
		if not frappe.db.exists("DocType", ENTRY_TYPE_DOCTYPE):
			return error(f"{ENTRY_TYPE_DOCTYPE} is not available on this site", 404)

		field = frappe.get_meta(ENTRY_TYPE_DOCTYPE).get_field(ENTRY_TYPE_FIELD)
		if not field:
			return error(f"{ENTRY_TYPE_DOCTYPE} has no {ENTRY_TYPE_FIELD} field", 404)

		values = frappe.utils.cstr(field.options).split("\n")
		data = [value.strip() for value in values if value.strip()]
		return success(data=data)
	except Exception as e:
		frappe.log_error(title="Stock arrival entry type list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def arrival_location_list():
	try:
		if not frappe.db.exists("DocType", ENTRY_TYPE_DOCTYPE):
			return error(f"{ENTRY_TYPE_DOCTYPE} is not available on this site", 404)

		field = frappe.get_meta(ENTRY_TYPE_DOCTYPE).get_field(ARRIVAL_LOCATION_FIELD)
		if not field:
			return error(f"{ENTRY_TYPE_DOCTYPE} has no {ARRIVAL_LOCATION_FIELD} field", 404)

		values = frappe.utils.cstr(field.options).split("\n")
		data = [value.strip() for value in values if value.strip()]
		return success(data=data)
	except Exception as e:
		frappe.log_error(title="Stock arrival location list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def create_stock_arrival(
	entry_type=None,
	transaction_date=None,
	time=None,
	party_type=None,
	supplier_name=None,
	city=None,
	received_by=None,
	source=None,
	courier_name=None,
	total_boxes=None,
	brand=None,
	original_invoice_received=None,
	purchase_order=None,
	invoice_no=None,
	invoice_date=None,
	lr_no=None,
	remark=None,
	arrival_location=None,
	file_urls=None,
):
	"""Create one Hns Stock Arrival. naming_series is left to the doctype, which
	defaults it to SA-.FY.- . Select values and missing links are checked by
	Frappe itself on insert and come back as a 400.

	`file_urls` optionally carries files already uploaded through
	/api/method/upload_file - one url or a list of them - which are attached to
	the arrival raised.
	"""
	try:
		values = {
			"entry_type": entry_type,
			"transaction_date": transaction_date,
			"time": time,
			"party_type": party_type,
			"supplier_name": supplier_name,
			"city": city,
			"received_by": received_by,
			"source": source,
			"courier_name": courier_name,
			"total_boxes": total_boxes,
			"brand": brand,
			"original_invoice_received": original_invoice_received,
			"purchase_order": purchase_order,
			"invoice_no": invoice_no,
			"invoice_date": invoice_date,
			"lr_no": lr_no,
			"remark": remark,
			"arrival_location": arrival_location,
		}
		# leave a blank optional field unset rather than writing an empty string
		values = {field: value for field, value in values.items() if value not in (None, "")}

		validation_error = _validate_arrival(values)
		if validation_error:
			return validation_error

		arrival = frappe.get_doc({"doctype": ENTRY_TYPE_DOCTYPE, **values})
		arrival.flags.ignore_permissions = True
		arrival.insert(ignore_permissions=True)
		_attach_to(arrival.name, file_urls)
		frappe.db.commit()

		return success(
			data={"stock_arrival_id": arrival.name, "message": "Stock arrival created."},
			http_status=201,
		)
	except frappe.ValidationError as e:
		frappe.db.rollback()
		return error(str(e), 400)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Stock arrival creation failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _attach_to(arrival, file_urls):
	"""Point already-uploaded Files at this arrival, by url. Each upload is
	relinked in place, so nothing is copied and no second File row appears.
	"""
	# a form-encoded body sends a list as a JSON string, a single url as itself
	urls = file_urls
	if isinstance(urls, str) and urls.strip().startswith("["):
		urls = frappe.parse_json(urls)
	if not isinstance(urls, list):
		urls = [urls]

	for url in urls:
		url = frappe.utils.strip(frappe.utils.cstr(url))
		if not url:
			continue

		frappe.db.set_value(
			"File",
			{"file_url": url},
			{"attached_to_doctype": ENTRY_TYPE_DOCTYPE, "attached_to_name": arrival},
		)


def _validate_arrival(values):
	"""400 error for a missing required field or a non positive box count,
	None when the payload is good.
	"""
	missing = [field for field in REQUIRED_FIELDS if not values.get(field)]
	if missing:
		return error(f"Please provide {', '.join(missing)}.", 400)

	if cint(values["total_boxes"]) < 1:
		return error("total_boxes must be a positive number.", 400)

	return None
