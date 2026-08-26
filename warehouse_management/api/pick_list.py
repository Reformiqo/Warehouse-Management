import frappe
from erpnext.selling.doctype.sales_order.sales_order import create_pick_list as map_pick_list_from_so
from frappe.utils import cint, flt

from warehouse_management.api.profile import OPEN_SO_STATUSES
from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20


@frappe.whitelist(methods=["GET"])
def open_so(limit=None, offset=None):
	"""Return open Sales Orders (To Deliver and Bill / To Deliver) that
	still need picking.

	Query params, both optional: `limit` (default 20) and `offset`
	(rows to skip, default 0).
	"""
	try:
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		rows = frappe.db.sql(
			"""
			SELECT sales_order.name AS id, sales_order.customer,
			       COUNT(DISTINCT so_item.item_code) AS total_items
			FROM `tabSales Order` sales_order
			INNER JOIN `tabSales Order Item` so_item ON so_item.parent = sales_order.name
			WHERE sales_order.status IN %(statuses)s
			GROUP BY sales_order.name
			ORDER BY sales_order.transaction_date DESC
			LIMIT %(limit)s OFFSET %(offset)s
			""",
			{"statuses": tuple(OPEN_SO_STATUSES), "limit": limit, "offset": offset},
			as_dict=True,
		)
		return success(data=rows)
	except Exception as e:
		frappe.log_error(title="Pick list lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def so_pending_items(so_id=None):
	"""Return the Sales Order lines still awaiting delivery, with ordered,
	delivered and pending quantities.

	Query param: `so_id` (required).
	"""
	try:
		so_id = frappe.utils.strip(frappe.utils.cstr(so_id))
		if not so_id:
			return error("Please provide a so_id.", 400)

		if not frappe.db.exists("Sales Order", so_id):
			return error(f"Sales Order '{so_id}' not found.", 404)

		rows = frappe.get_all(
			"Sales Order Item",
			filters={"parent": so_id},
			fields=["item_code", "item_name", "warehouse", "qty", "delivered_qty"],
			order_by="idx",
		)

		return success(
			data=[
				{
					"item_code": row.item_code,
					"item_name": row.item_name,
					"warehouse": row.warehouse,
					"total_qty": flt(row.qty),
					"delivered_qty": flt(row.delivered_qty),
					"pending_qty": flt(row.qty) - flt(row.delivered_qty),
				}
				for row in rows
				if flt(row.delivered_qty) < flt(row.qty)
			]
		)
	except Exception as e:
		frappe.log_error(title="SO pending items lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def create_pick_list(so_id=None, items=None):
	try:
		so_id = frappe.utils.strip(frappe.utils.cstr(so_id))
		if not so_id:
			return error("Please provide a so_id.", 400)

		if not frappe.db.exists("Sales Order", so_id):
			return error(f"Sales Order '{so_id}' not found.", 404)

		item_qty_map = frappe.parse_json(items) if isinstance(items, str) else items
		if not item_qty_map:
			return error("Please provide items as {item_code: qty}.", 400)

		so_status = frappe.db.get_value("Sales Order", so_id, "status")
		if so_status not in OPEN_SO_STATUSES:
			return error(f"Sales Order '{so_id}' has nothing pending to deliver.", 400)

		pick_list_doc = map_pick_list_from_so(so_id)
		pick_list_doc.flags.ignore_permissions = True
		pick_list_doc.set_item_locations()
		pick_list_doc.locations = _apply_requested_items(pick_list_doc.locations, item_qty_map)
		if not pick_list_doc.locations:
			return error("None of the given items are pending on this Sales Order.", 400)

		pick_list_doc.insert(ignore_permissions=True)
		frappe.db.commit()

		return success(
			data={"pick_list_id": pick_list_doc.name, "message": "Pick list created in draft."},
			http_status=201,
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Pick list creation failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _apply_requested_items(locations, item_qty_map):
	"""Keep only the rows for items in item_qty_map, with qty/stock_qty
	overridden to the requested amount.
	"""
	kept_rows = []
	for row in locations:
		if row.item_code not in item_qty_map:
			continue
		row.qty = flt(item_qty_map[row.item_code])
		row.stock_qty = row.qty * (flt(row.conversion_factor) or 1)
		kept_rows.append(row)
	return kept_rows
