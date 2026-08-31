import frappe
from frappe.utils import cint

from warehouse_management.api.profile import OPEN_PO_STATUSES
from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20


@frappe.whitelist(methods=["GET"])
def get_purchase_orders(
	from_date=None, to_date=None, item_code=None, supplier_name=None, limit=None, offset=None
):
	"""Return open Purchase Orders (To Receive and Bill / To Receive),
	optionally narrowed by posting date range, item, and/or supplier.

	Query params, all optional: `from_date`, `to_date`, `item_code`,
	`supplier_name`, `limit` (default 20), `offset` (rows to skip,
	default 0). total_count ignores limit/offset, so it drives paging.
	"""
	try:
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		po_names = _matching_po_names(from_date, to_date, item_code, supplier_name)
		total_count = len(po_names)
		page = po_names[offset : offset + limit]

		purchase_orders = _build_purchase_orders(page)

		return success(data={"total_count": total_count, "purchase_orders": purchase_orders})
	except Exception as e:
		frappe.log_error(title="Purchase order filter failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def purchase_order_detail(po_id=None):
	"""Return one Purchase Order's supplier and item-level receiving
	detail: received/pending qty and current stock at each item's
	target warehouse.

	Query param: `po_id` (required).
	"""
	try:
		po_id = frappe.utils.strip(frappe.utils.cstr(po_id))
		if not po_id:
			return error("Please provide a po_id.", 400)

		if not frappe.db.exists("Purchase Order", po_id):
			return error(f"Purchase Order '{po_id}' not found.", 404)

		supplier_name = frappe.db.get_value("Purchase Order", po_id, "supplier_name")

		return success(
			data={"po_id": po_id, "supplier_name": supplier_name, "items": _get_po_item_detail(po_id)}
		)
	except Exception as e:
		frappe.log_error(title="Purchase order detail failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def item_stock(item_code=None):
	"""Return every warehouse currently holding this item, with its
	balance qty. Read from Bin so it covers non batch-tracked items.

	Query param: `item_code` (required).
	"""
	try:
		item_code = frappe.utils.strip(frappe.utils.cstr(item_code))
		if not item_code:
			return error("Please provide an item_code.", 400)

		if not frappe.db.exists("Item", {"name": item_code, "disabled": 0}):
			return error(f"Item '{item_code}' not found or is disabled.", 404)

		rows = frappe.get_all(
			"Bin",
			filters={"item_code": item_code, "actual_qty": ["!=", 0]},
			fields=["warehouse", "actual_qty as balance_qty"],
			order_by="warehouse",
		)
		return success(data=rows, total_balance_qty=sum(row.balance_qty for row in rows))
	except Exception as e:
		frappe.log_error(title="Item stock lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _get_po_item_detail(po_id):
	"""[{item_code, item_name, received_qty, pending_qty, in_store}, ...]
	for one Purchase Order's items. in_store comes straight off the row
	itself — actual_qty is ERPNext's own "Available Qty at Target
	Warehouse" field, already maintained per row.
	"""
	item_rows = frappe.get_all(
		"Purchase Order Item",
		filters={"parent": po_id},
		fields=["item_code", "item_name", "qty", "received_qty", "actual_qty"],
	)
	return [
		{
			"item_code": row.item_code,
			"item_name": row.item_name,
			"received_qty": row.received_qty,
			"pending_qty": row.qty - row.received_qty,
			"in_store": row.actual_qty,
		}
		for row in item_rows
	]


def _matching_po_names(from_date, to_date, item_code, supplier_name):
	"""Distinct Purchase Order names matching the open-PO statuses plus
	optional date range, item, and supplier filters, newest first.
	"""
	conditions = ["purchase_order.status IN %(statuses)s"]
	values = {"statuses": tuple(OPEN_PO_STATUSES)}

	if from_date:
		conditions.append("purchase_order.transaction_date >= %(from_date)s")
		values["from_date"] = from_date

	if to_date:
		conditions.append("purchase_order.transaction_date <= %(to_date)s")
		values["to_date"] = to_date

	if item_code:
		conditions.append("po_item.item_code LIKE %(item_code)s")
		values["item_code"] = f"%{item_code}%"

	if supplier_name:
		conditions.append("purchase_order.supplier_name LIKE %(supplier_name)s")
		values["supplier_name"] = f"%{supplier_name}%"

	rows = frappe.db.sql(
		f"""
		SELECT DISTINCT purchase_order.name, purchase_order.transaction_date
		FROM `tabPurchase Order` purchase_order
		INNER JOIN `tabPurchase Order Item` po_item ON po_item.parent = purchase_order.name
		WHERE {" AND ".join(conditions)}
		ORDER BY purchase_order.transaction_date DESC
		""",
		values,
		as_dict=True,
	)
	return [row.name for row in rows]


def _build_purchase_orders(po_names):
	"""[{po_id, supplier_name, posting_date, item_codes}, ...] for the
	given Purchase Order names — one joined query, grouped in Python.
	"""
	if not po_names:
		return []

	rows = frappe.db.sql(
		"""
		SELECT DISTINCT purchase_order.name AS po_id, purchase_order.supplier_name,
		       purchase_order.transaction_date AS posting_date, po_item.item_code
		FROM `tabPurchase Order` purchase_order
		INNER JOIN `tabPurchase Order Item` po_item ON po_item.parent = purchase_order.name
		WHERE purchase_order.name IN %(names)s
		ORDER BY purchase_order.transaction_date DESC
		""",
		{"names": tuple(po_names)},
		as_dict=True,
	)

	orders_by_id = {}
	order_sequence = []
	for row in rows:
		order = orders_by_id.get(row.po_id)
		if not order:
			order = {
				"po_id": row.po_id,
				"supplier_name": row.supplier_name,
				"posting_date": str(row.posting_date) if row.posting_date else None,
				"item_codes": [],
			}
			orders_by_id[row.po_id] = order
			order_sequence.append(row.po_id)
		order["item_codes"].append(row.item_code)

	return [orders_by_id[po_id] for po_id in order_sequence]
