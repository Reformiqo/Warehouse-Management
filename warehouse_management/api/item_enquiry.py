import frappe
from erpnext.stock.report.batch_wise_balance_history.batch_wise_balance_history import (
	execute as run_batch_wise_balance,
)
from erpnext.stock.report.stock_balance.stock_balance import execute as run_stock_balance

from warehouse_management.api.profile import OPEN_PO_STATUSES, OPEN_SO_STATUSES, get_cached_stats
from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["GET"])
def item_enquiry():
	"""Return every item with today's stock spread plus open PO/SO
	linkage. No input required.
	"""
	try:
		stock_by_item = _get_stock_by_item()
		open_so = _open_order_counts("Sales Order Item", "Sales Order", OPEN_SO_STATUSES)
		open_po = _open_order_counts("Purchase Order Item", "Purchase Order", OPEN_PO_STATUSES)

		items = []
		for item in frappe.get_all("Item", fields=["item_code", "item_name", "item_group"]):
			stock = stock_by_item.get(item.item_code, {"warehouse_count": 0, "balance_qty": 0})
			items.append(
				{
					"item_code": item.item_code,
					"item_name": item.item_name,
					"item_group": item.item_group,
					"loc": stock["warehouse_count"],
					"balance_qty": stock["balance_qty"],
					"open_so": open_so.get(item.item_code, 0),
					"open_po": open_po.get(item.item_code, 0),
				}
			)

		return success(data={"total_item": get_cached_stats()["total_items"], "items": items})
	except Exception as e:
		frappe.log_error(title="Warehouse item enquiry failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def item_detail(item_code=None):
	"""Return today's per-warehouse batch breakdown and pending Sales
	Orders for one item.

	Query param: `item_code` (required).
	"""
	try:
		item_code = frappe.utils.strip(frappe.utils.cstr(item_code))
		if not item_code:
			return error("Please provide an item_code.", 400)
		if not frappe.db.exists("Item", item_code):
			return error(f"Item '{item_code}' not found.", 404)

		return success(
			data={
				"item_code": item_code,
				"warehouse_details": _get_warehouse_details(item_code),
				"pending_sales_orders": _get_pending_sales_orders(item_code),
				"recent_movement": _get_recent_movement(item_code),
			}
		)
	except Exception as e:
		frappe.log_error(title="Warehouse item detail failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _get_stock_by_item():
	"""{item_code: {warehouse_count, balance_qty}} from today's Stock
	Balance report. Items with no stock activity are absent here —
	item_enquiry() fills those in as zero.
	"""
	today = frappe.utils.today()
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	filters = frappe._dict({"company": company, "from_date": today, "to_date": today})
	_columns, data = run_stock_balance(filters)

	stock_by_item = {}
	for row in data:
		stock = stock_by_item.setdefault(row["item_code"], {"warehouse_count": 0, "balance_qty": 0})
		stock["warehouse_count"] += 1
		stock["balance_qty"] += row.get("bal_qty") or 0
	return stock_by_item


def _get_warehouse_details(item_code):
	"""[{warehouse, batch, qty}, ...] from today's Batch-Wise Balance
	History report, filtered to one item.
	"""
	today = frappe.utils.today()
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	filters = frappe._dict(
		{"company": company, "from_date": today, "to_date": today, "item_code": item_code}
	)
	_columns, data = run_batch_wise_balance(filters)

	# Row order: item, item_name, description, warehouse, batch,
	# opening_qty, in_qty, out_qty, bal_qty, valuation_rate, bal_value, uom
	return [{"warehouse": row[3], "batch": row[4], "qty": row[8]} for row in data]


def _get_pending_sales_orders(item_code):
	"""[{customer, qty, so_name, so_date}, ...] for Sales Orders with an
	open status (see OPEN_SO_STATUSES) referencing this item.
	"""
	rows = frappe.db.sql(
		"""
		SELECT order_doc.customer, item_row.qty,
		       order_doc.name AS so_name, order_doc.transaction_date AS so_date
		FROM `tabSales Order Item` item_row
		INNER JOIN `tabSales Order` order_doc ON order_doc.name = item_row.parent
		WHERE order_doc.status IN %(statuses)s AND item_row.item_code = %(item_code)s
		""",
		{"statuses": tuple(OPEN_SO_STATUSES), "item_code": item_code},
		as_dict=True,
	)
	return [
		{
			"customer": row.customer,
			"qty": row.qty,
			"so_name": row.so_name,
			"so_date": str(row.so_date) if row.so_date else None,
		}
		for row in rows
	]


def _get_recent_movement(item_code):
	"""The 5 most recent Stock Ledger Entry rows for one item."""
	return frappe.get_all(
		"Stock Ledger Entry",
		filters={"item_code": item_code, "is_cancelled": 0},
		fields=["voucher_type", "voucher_no", "warehouse"],
		order_by="posting_datetime desc, creation desc",
		limit=5,
	)


def _open_order_counts(child_doctype, parent_doctype, statuses):
	"""{item_code: distinct parent-document count} for the given statuses."""
	rows = frappe.db.sql(
		f"""
		SELECT item_row.item_code, COUNT(DISTINCT item_row.parent) AS cnt
		FROM `tab{child_doctype}` item_row
		INNER JOIN `tab{parent_doctype}` order_doc ON order_doc.name = item_row.parent
		WHERE order_doc.status IN %(statuses)s
		GROUP BY item_row.item_code
		""",
		{"statuses": tuple(statuses)},
		as_dict=True,
	)
	return {row.item_code: row.cnt for row in rows}
