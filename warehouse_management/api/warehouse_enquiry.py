import frappe
from erpnext.stock.report.stock_balance.stock_balance import execute as run_stock_balance

from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["GET"])
def warehouse_enquiry(warehouse=None):
	"""Return unique item count, total quantity, and per-item balance for
	one warehouse, based on today's Stock Balance report.

	Query param: `warehouse` (required).
	"""
	try:
		warehouse = frappe.utils.strip(frappe.utils.cstr(warehouse))
		if not warehouse:
			return error("Please provide a warehouse.", 400)

		if not frappe.db.exists("Warehouse", warehouse):
			return error(f"Warehouse '{warehouse}' not found.", 404)

		items = _get_items_in_warehouse(warehouse)
		total_quantity = sum(item["balance_qty"] for item in items)

		return success(
			data={
				"warehouse": warehouse,
				"unique_items": len(items),
				"total_quantity": total_quantity,
				"last_inward": _last_submitted_date("Purchase Receipt Item", "Purchase Receipt", warehouse) or "",
				"last_outward": _last_submitted_date("Delivery Note Item", "Delivery Note", warehouse) or "",
				"items": items,
			}
		)
	except Exception as e:
		frappe.log_error(title="Warehouse enquiry failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _get_items_in_warehouse(warehouse):
	"""[{item_code, item_name, item_group, balance_qty}, ...] from
	today's Stock Balance report, filtered to one warehouse and combined
	uniquely by item_code (summed across any batch-level rows).
	"""
	today = frappe.utils.today()
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	filters = frappe._dict(
		{"company": company, "from_date": today, "to_date": today, "warehouse": warehouse}
	)
	_columns, data = run_stock_balance(filters)

	items_by_code = {}
	for row in data:
		item = items_by_code.setdefault(
			row["item_code"],
			{
				"item_code": row["item_code"],
				"item_name": row.get("item_name"),
				"item_group": row.get("item_group"),
				"balance_qty": 0,
			},
		)
		item["balance_qty"] += row.get("bal_qty") or 0

	return list(items_by_code.values())


def _last_submitted_date(child_doctype, parent_doctype, warehouse):
	"""posting_date of the most recently submitted parent_doctype whose
	child rows reference this warehouse, or None if there isn't one.
	"""
	rows = frappe.db.sql(
		f"""
		SELECT parent_doc.posting_date
		FROM `tab{child_doctype}` item_row
		INNER JOIN `tab{parent_doctype}` parent_doc ON parent_doc.name = item_row.parent
		WHERE item_row.warehouse = %(warehouse)s AND parent_doc.docstatus = 1
		ORDER BY parent_doc.posting_date DESC, parent_doc.posting_time DESC
		LIMIT 1
		""",
		{"warehouse": warehouse},
	)
	return str(rows[0][0]) if rows else None
