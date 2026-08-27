import frappe
from frappe.utils import flt

from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["POST"])
def create_stock_reconciliation(items=None):
	"""Create one draft Stock Reconciliation per warehouse.

	Body: `{items}` — a list of `{warehouse, item_code, qty}`, since an item
	now carries the warehouse it was counted in. Left in draft;
	valuation_rate is filled by ERPNext's own validate().
	"""
	try:
		items = frappe.parse_json(items) if isinstance(items, str) else items

		validation_error = _validate_items(items)
		if validation_error:
			return validation_error

		warehouse_items = {}
		for row in items:
			warehouse_items.setdefault(row["warehouse"], {})[row["item_code"]] = flt(row.get("qty"))

		created = [
			_create_for_warehouse(warehouse, item_qty_map)
			for warehouse, item_qty_map in warehouse_items.items()
		]
		frappe.db.commit()

		return success(data={"stock_reconciliation_ids": created}, http_status=201)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Stock reconciliation creation failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _validate_items(items):
	"""Return an error, or None when the input is valid."""
	if not items or not isinstance(items, list):
		return error("Please provide items as [{warehouse, item_code, qty}].", 400)

	for row in items:
		warehouse, item_code = row.get("warehouse"), row.get("item_code")
		if not warehouse or not item_code:
			return error("Every item row needs a warehouse and an item_code.", 400)

		if not frappe.db.exists("Warehouse", warehouse):
			return error(f"Warehouse '{warehouse}' not found.", 404)

		if not frappe.db.exists("Item", item_code):
			return error(f"Item '{item_code}' not found.", 404)

		if flt(row.get("qty")) < 0:
			return error(f"Qty for item '{item_code}' cannot be negative.", 400)

	return None


def _create_for_warehouse(warehouse, item_qty_map):
	"""Insert one draft Stock Reconciliation and return its name."""
	reconciliation = frappe.new_doc("Stock Reconciliation")
	reconciliation.purpose = "Stock Reconciliation"
	reconciliation.company = frappe.db.get_single_value("Global Defaults", "default_company")
	reconciliation.set_warehouse = warehouse
	for item_code, qty in item_qty_map.items():
		reconciliation.append("items", {"item_code": item_code, "warehouse": warehouse, "qty": qty})

	reconciliation.flags.ignore_permissions = True
	reconciliation.insert(ignore_permissions=True)

	return reconciliation.name
