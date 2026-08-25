import frappe
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
from frappe.utils import flt

from warehouse_management.api.profile import OPEN_PO_STATUSES
from warehouse_management.utils import get_recent_documents_by_owner
from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["GET"])
def recent_prs():
	"""Return the caller's last 5 submitted Purchase Receipts. No input
	required; scoped to the Authorization header user.
	"""
	try:
		receipts = get_recent_documents_by_owner("Purchase Receipt", "Purchase Receipt Item", "supplier")
		return success(data=receipts)
	except Exception as e:
		frappe.log_error(title="Purchase receipt lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def create_purchase_receipt(po_id=None, items=None):
	"""Create and submit a Purchase Receipt from a Purchase Order,
	receiving only the given items at the given quantities.

	Body: `{po_id, items}` — `items` is `{item_code: qty}`. Uses
	ERPNext's own make_purchase_receipt mapper so every row's
	purchase_order/purchase_order_item reference is set correctly —
	required for the PO's received_qty tracking to work.
	"""
	try:
		po_id = frappe.utils.strip(frappe.utils.cstr(po_id))
		if not po_id:
			return error("Please provide a po_id.", 400)

		if not frappe.db.exists("Purchase Order", po_id):
			return error(f"Purchase Order '{po_id}' not found.", 404)

		item_qty_map = frappe.parse_json(items) if isinstance(items, str) else items
		if not item_qty_map:
			return error("Please provide items as {item_code: qty}.", 400)

		po_status = frappe.db.get_value("Purchase Order", po_id, "status")
		if po_status not in OPEN_PO_STATUSES:
			return error(f"Purchase Order '{po_id}' has nothing pending to receive.", 400)

		receipt = make_purchase_receipt(po_id)
		receipt.items = _apply_received_items(receipt.items, item_qty_map)
		if not receipt.items:
			return error("None of the given items are pending on this Purchase Order.", 400)

		receipt.flags.ignore_permissions = True
		receipt.insert(ignore_permissions=True)
		receipt.submit()
		frappe.db.commit()

		return success(
			data={"purchase_receipt_id": receipt.name, "message": "Purchase receipt created."},
			http_status=201,
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Purchase receipt creation failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _apply_received_items(rows, item_qty_map):
	"""Keep only rows for items in item_qty_map, with qty overridden to
	the requested amount. stock_qty is kept in step with qty, the same
	way ERPNext's own PO->PR mapper sets it.
	"""
	kept_rows = []
	for row in rows:
		if row.item_code not in item_qty_map:
			continue

		row.qty = flt(item_qty_map[row.item_code])
		row.received_qty = row.qty
		kept_rows.append(row)

	return kept_rows
