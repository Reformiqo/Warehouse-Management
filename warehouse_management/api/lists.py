import frappe
from frappe.utils import cint

from warehouse_management.utils import item_search_filters, strip_link_marker
from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20
RECENT_LIMIT = 5

# Party Type value -> the doctype field holding that party's display name
PARTY_NAME_FIELD = {"Customer": "customer_name", "Supplier": "supplier_name"}
MISC_MASTER_DOCTYPE = "Hns Misc Master"
EXCLUDED_USERS = ("Administrator", "Guest")

# (doctype, label, extra filters) — Material Transfer is a Stock Entry purpose
RECENT_SOURCES = [
	("Purchase Receipt", "Purchase Receipt", {}),
	("Delivery Note", "Delivery Note", {}),
	("Pick List", "Pick List", {}),
	("Stock Entry", "Material Transfer", {"purpose": "Material Transfer"}),
]


@frappe.whitelist(methods=["GET"])
def warehouse_list(search=None, limit=None, offset=None):
	"""Return enabled leaf Warehouses (is_group = 0, not disabled).

	Query params, all optional: `search` (matches the warehouse id),
	`limit` (default 20) and `offset` (rows to skip, default 0).
	"""
	try:
		search = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(search)))
		search = strip_link_marker(search)
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		filters = {"is_group": 0, "disabled": 0}
		if search:
			filters["name"] = ["like", f"%{search}%"]

		warehouses = frappe.get_all(
			"Warehouse",
			filters=filters,
			fields=["name as warehouse_id", "warehouse_name", "is_rejected_warehouse"],
			order_by="warehouse_name",
			limit_start=offset,
			limit_page_length=limit,
		)
		return success(data=warehouses)
	except Exception as e:
		frappe.log_error(title="Warehouse list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def item_list(search=None, barcode=None, limit=None, offset=None):
	"""Return enabled stock Items (is_stock_item = 1, not disabled).

	Query params, all optional: `barcode` (matches part of any of the item's
	barcodes, wins over `search`), `search` (matches item name), `limit`
	(default 20) and `offset` (rows to skip, default 0).
	"""
	try:
		search = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(search)))
		barcode = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(barcode)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		filters = [
			["Item", "is_stock_item", "=", 1],
			["Item", "disabled", "=", 0],
			*item_search_filters(search, barcode),
		]

		items = frappe.get_all(
			"Item",
			filters=filters,
			fields=["item_code", "item_name"],
			order_by="item_name",
			limit_start=offset,
			limit_page_length=limit,
			distinct=True,
		)
		return success(data=items)
	except Exception as e:
		frappe.log_error(title="Item list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def recent_entries():
	"""The caller's 5 most recent submitted documents across Purchase
	Receipt, Delivery Note, Pick List and Material Transfer. No input
	required; scoped to the Authorization header user.
	"""
	try:
		entries = []
		for doctype, label, extra_filters in RECENT_SOURCES:
			entries.extend(_recent_for(doctype, label, extra_filters))

		entries.sort(key=lambda entry: entry["submitted_at"], reverse=True)
		recent = entries[:RECENT_LIMIT]
		for entry in recent:
			entry["submitted_at"] = str(entry["submitted_at"])

		return success(data=recent)
	except Exception as e:
		frappe.log_error(title="Recent entries lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def party_list(party_type=None, search=None, limit=None, offset=None):
	"""Return enabled Customers or Suppliers, for the Hns Stock Arrival party
	picker. `party_type` is required and must be Customer or Supplier; `search`
	matches the party name, `limit` (default 20) and `offset` page the result.
	"""
	try:
		party_type = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(party_type))).title()
		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		name_field = PARTY_NAME_FIELD.get(party_type)
		if not name_field:
			return error("party_type is required and must be either Customer or Supplier.", 400)

		filters = {"disabled": 0}
		if search:
			filters[name_field] = ["like", f"%{search}%"]

		parties = frappe.get_all(
			party_type,
			filters=filters,
			fields=["name as party_id", f"{name_field} as party_name"],
			order_by=name_field,
			limit_start=offset,
			limit_page_length=limit,
		)
		return success(data=parties)
	except Exception as e:
		frappe.log_error(title="Party list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def misc_master_list(search=None, limit=None, offset=None):
	"""Return Hns Misc Master codes - the groups that Hns Misc Master Details
	rows hang off. Query params, all optional: `search` (matches the code),
	`limit` (default 20) and `offset` (rows to skip, default 0).
	"""
	try:
		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		if not frappe.db.exists("DocType", MISC_MASTER_DOCTYPE):
			return error(f"{MISC_MASTER_DOCTYPE} is not available on this site", 404)

		filters = {}
		if search:
			filters["misc_master_code"] = ["like", f"%{search}%"]

		masters = frappe.get_all(
			MISC_MASTER_DOCTYPE,
			filters=filters,
			fields=["misc_master_code", "misc_master_name"],
			order_by="misc_master_code",
			limit_start=offset,
			limit_page_length=limit,
		)
		return success(data=masters)
	except Exception as e:
		frappe.log_error(title="Misc master list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def brand_list(search=None, limit=None, offset=None):
	"""Return Brands, for the Hns Stock Arrival brand picker. Query params, all
	optional: `search` (matches the brand name), `limit` (default 20) and
	`offset` (rows to skip, default 0).
	"""
	try:
		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		filters = {}
		if search:
			filters["brand"] = ["like", f"%{search}%"]

		brands = frappe.get_all(
			"Brand",
			filters=filters,
			fields=["name as brand_id", "brand as brand_name"],
			order_by="brand",
			limit_start=offset,
			limit_page_length=limit,
		)
		return success(data=brands)
	except Exception as e:
		frappe.log_error(title="Brand list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def user_list(search=None, limit=None, offset=None):
	"""Return enabled system Users, for the Hns Stock Arrival received-by picker.
	Administrator and Guest are left out, as they are in the desk link picker.

	Query params, all optional: `search` (matches the email or the full name),
	`limit` (default 20) and `offset` (rows to skip, default 0).
	"""
	try:
		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		filters = {
			"enabled": 1,
			"user_type": "System User",
			"name": ["not in", EXCLUDED_USERS],
		}
		or_filters = {}
		if search:
			or_filters = {"name": ["like", f"%{search}%"], "full_name": ["like", f"%{search}%"]}

		users = frappe.get_all(
			"User",
			filters=filters,
			or_filters=or_filters,
			fields=["name as email", "full_name"],
			order_by="full_name",
			limit_start=offset,
			limit_page_length=limit,
		)
		return success(data=users)
	except Exception as e:
		frappe.log_error(title="User list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def courier_list(search=None, limit=None, offset=None):
	"""Return enabled Suppliers flagged Is Transporter, for the Hns Stock Arrival
	courier picker. Query params, all optional: `search` (matches the supplier
	name), `limit` (default 20) and `offset` (rows to skip, default 0).
	"""
	try:
		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		filters = {"is_transporter": 1, "disabled": 0}
		if search:
			filters["supplier_name"] = ["like", f"%{search}%"]

		couriers = frappe.get_all(
			"Supplier",
			filters=filters,
			fields=["name as courier_id", "supplier_name as courier_name"],
			order_by="supplier_name",
			limit_start=offset,
			limit_page_length=limit,
		)
		return success(data=couriers)
	except Exception as e:
		frappe.log_error(title="Courier list failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _recent_for(doctype, label, extra_filters):
	"""The caller's last RECENT_LIMIT submitted docs of one doctype,
	newest first, by the submitted_at custom field (see
	setup/custom_fields.py — it carries a search_index for this sort).
	"""
	rows = frappe.get_all(
		doctype,
		filters={"docstatus": 1, "owner": frappe.session.user, **extra_filters},
		fields=["name", "submitted_at"],
		order_by="submitted_at desc",
		limit=RECENT_LIMIT,
	)
	return [{"doctype": label, "name": row.name, "submitted_at": row.submitted_at} for row in rows]
