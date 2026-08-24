import frappe
from erpnext.selling.doctype.sales_order.sales_order import create_pick_list as map_pick_list_from_so
from frappe.utils import flt

from warehouse_management.api.profile import OPEN_SO_STATUSES
from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["GET"])
def open_so():
	"""Return open Sales Orders (To Deliver and Bill / To Deliver) that
	still need picking. No input required.
	"""
	try:
		rows = frappe.db.sql(
			"""
			SELECT sales_order.name AS id, sales_order.customer,
			       COUNT(DISTINCT so_item.item_code) AS total_items
			FROM `tabSales Order` sales_order
			INNER JOIN `tabSales Order Item` so_item ON so_item.parent = sales_order.name
			WHERE sales_order.status IN %(statuses)s
			GROUP BY sales_order.name
			ORDER BY sales_order.transaction_date DESC
			""",
			{"statuses": tuple(OPEN_SO_STATUSES)},
			as_dict=True,
		)
		return success(data=rows)
	except Exception as e:
		frappe.log_error(title="Pick list lookup failed", message=frappe.get_traceback())
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
		pick_list_doc.submit()
		frappe.db.commit()

		return success(
			data={"pick_list_id": pick_list_doc.name, "message": "Pick list created."},
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
