import frappe
from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["GET"])
def warehouse_list():
	"""Return every leaf Warehouse (is_group = 0). No input required."""
	try:
		warehouses = frappe.get_all(
			"Warehouse",
			filters={"is_group": 0},
			fields=["name as warehouse_id", "warehouse_name", "is_rejected_warehouse"],
			order_by="warehouse_name",
		)
		return success(data=warehouses)
	except Exception as e:
		frappe.log_error(title="Warehouse list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def item_list():
	"""Return every stock Item (is_stock_item = 1). No input required."""
	try:
		items = frappe.get_all(
			"Item",
			filters={"is_stock_item": 1},
			fields=["item_code", "item_name"],
			order_by="item_name",
		)
		return success(data=items)
	except Exception as e:
		frappe.log_error(title="Item list failed", message=frappe.get_traceback())
		return error(str(e), 500)
