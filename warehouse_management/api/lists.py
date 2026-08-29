import frappe
from frappe.utils import cint

from warehouse_management.utils import item_search_filters, strip_link_marker
from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20
RECENT_LIMIT = 5

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
	"""Return stock Items (is_stock_item = 1).

	Query params, all optional: `barcode` (matches part of any of the item's
	barcodes, wins over `search`), `search` (matches item name), `limit`
	(default 20) and `offset` (rows to skip, default 0).
	"""
	try:
		search = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(search)))
		barcode = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(barcode)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		filters = [["Item", "is_stock_item", "=", 1], *item_search_filters(search, barcode)]

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
