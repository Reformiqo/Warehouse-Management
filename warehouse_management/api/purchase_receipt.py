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

		# This is their custom field, and mapped is setting this value as per po, which is wrong
		receipt.create_purchase_receipt = ""

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


@frappe.whitelist(methods=["GET"])
def purchase_receipt_detail(pr_id=None):
	"""Return one Purchase Receipt: who it came from, the Purchase Order behind
	it and every line received.

	Query param: `pr_id` (required). Each item carries item_code, item_name,
	qty and the warehouse the stock went into.
	"""
	try:
		pr_id = frappe.utils.strip(frappe.utils.cstr(pr_id))
		if not pr_id:
			return error("Please provide a pr_id.", 400)

		receipt = frappe.db.get_value("Purchase Receipt", pr_id, ["name", "supplier_name"], as_dict=True)
		if not receipt:
			return error(f"Purchase Receipt '{pr_id}' not found.", 404)

		rows = frappe.get_all(
			"Purchase Receipt Item",
			filters={"parent": pr_id},
			fields=["item_code", "item_name", "qty", "warehouse"],
			order_by="idx",
		)

		return success(
			data={
				"purchase_receipt_id": receipt.name,
				"supplier_name": receipt.supplier_name,
				"items": [
					{
						"item_code": row.item_code,
						"item_name": row.item_name,
						"qty": flt(row.qty),
						"warehouse": row.warehouse,
					}
					for row in rows
				],
			}
		)
	except Exception as e:
		frappe.log_error(title="Purchase receipt detail failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def cancel_purchase_receipt(pr_id=None):
	"""Cancel a submitted Purchase Receipt.

	Body: `{pr_id}`. Cancelling reverses the stock the receipt brought in, so a
	draft or an already-cancelled one is refused rather than touched.
	"""
	try:
		pr_id = frappe.utils.strip(frappe.utils.cstr(pr_id))
		if not pr_id:
			return error("Please provide a pr_id.", 400)

		validation_error = _validate_cancellable(pr_id)
		if validation_error:
			return validation_error

		receipt = frappe.get_doc("Purchase Receipt", pr_id)
		receipt.flags.ignore_permissions = True
		receipt.cancel()
		frappe.db.commit()

		return success(data={"purchase_receipt_id": pr_id, "message": "Purchase receipt cancelled."})
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Purchase receipt cancellation failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _validate_cancellable(pr_id):
	"""Return an error, or None when the receipt can be cancelled."""
	docstatus = frappe.db.get_value("Purchase Receipt", pr_id, "docstatus")
	if docstatus is None:
		return error(f"Purchase Receipt '{pr_id}' not found.", 404)

	if docstatus == 0:
		return error(f"Purchase Receipt '{pr_id}' is a draft, so there is nothing to cancel.", 400)

	if docstatus == 2:
		return error(f"Purchase Receipt '{pr_id}' is already cancelled.", 400)

	return None


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
