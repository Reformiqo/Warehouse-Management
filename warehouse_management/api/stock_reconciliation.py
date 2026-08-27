import frappe
from frappe.utils import flt

from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["POST"])
def create_stock_reconciliation(warehouse=None, items=None):
	"""Create a draft Stock Reconciliation for one warehouse.

	Body: `{warehouse, items}` — `items` is `{item_code: qty}`. Left in
	draft; valuation_rate is filled by ERPNext's own validate().
	"""
	try:
		warehouse = frappe.utils.strip(frappe.utils.cstr(warehouse))
		item_qty_map = frappe.parse_json(items) if isinstance(items, str) else items

		validation_error = _validate_reconciliation(warehouse, item_qty_map)
		if validation_error:
			return validation_error

		reconciliation = frappe.new_doc("Stock Reconciliation")
		reconciliation.purpose = "Stock Reconciliation"
		reconciliation.company = frappe.db.get_single_value("Global Defaults", "default_company")
		reconciliation.set_warehouse = warehouse
		for item_code, qty in item_qty_map.items():
			reconciliation.append(
				"items", {"item_code": item_code, "warehouse": warehouse, "qty": flt(qty)}
			)

		reconciliation.flags.ignore_permissions = True
		reconciliation.insert(ignore_permissions=True)

		frappe.db.commit()

		return success(
			data={
				"stock_reconciliation_id": reconciliation.name,
				"message": "Stock reconciliation created in draft.",
			},
			http_status=201,
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Stock reconciliation creation failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _validate_reconciliation(warehouse, item_qty_map):
	"""Return an error, or None when the input is valid."""
	if not warehouse:
		return error("Please provide a warehouse.", 400)

	if not frappe.db.exists("Warehouse", warehouse):
		return error(f"Warehouse '{warehouse}' not found.", 404)

	if not item_qty_map:
		return error("Please provide items as {item_code: qty}.", 400)

	for item_code, qty in item_qty_map.items():
		if not frappe.db.exists("Item", item_code):
			return error(f"Item '{item_code}' not found.", 404)

		if flt(qty) < 0:
			return error(f"Qty for item '{item_code}' cannot be negative.", 400)

	return None
