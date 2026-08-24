import frappe
from erpnext.stock.report.batch_wise_balance_history.batch_wise_balance_history import (
	get_item_warehouse_batch_map,
)
from erpnext.stock.report.stock_balance.stock_balance import execute as run_stock_balance

from warehouse_management.api.profile import OPEN_PO_STATUSES, OPEN_SO_STATUSES, get_cached_stats
from warehouse_management.utils import get_open_order_counts, get_pending_sales_orders
from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["GET"])
def item_enquiry():
	"""Return every item with today's stock spread plus open PO/SO
	linkage. No input required.
	"""
	try:
		stock_by_item = _get_stock_by_item()
		open_so = get_open_order_counts("Sales Order Item", "Sales Order", OPEN_SO_STATUSES)
		open_po = get_open_order_counts("Purchase Order Item", "Purchase Order", OPEN_PO_STATUSES)

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
				"warehouse_wise_stock": _get_warehouse_details(item_code),
				"pending_sales_orders": get_pending_sales_orders(item_code, OPEN_SO_STATUSES),
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
	"""[{warehouse, batch, qty}, ...] for one item, today only.

	get_item_warehouse_batch_map() is the Batch-Wise Balance History
	report's own internal helper — it already returns
	{item: {warehouse: {batch: {bal_qty, ...}}}} before the report
	flattens that into rows for its grid UI, so we read it directly
	instead of running the report and re-parsing its output rows.
	"""
	today = frappe.utils.today()
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	filters = frappe._dict(
		{"company": company, "from_date": today, "to_date": today, "item_code": item_code}
	)

	float_precision = frappe.utils.cint(frappe.db.get_default("float_precision")) or 3
	warehouse_batch_map = get_item_warehouse_batch_map(filters, float_precision)

	details = []
	for warehouse, batches in warehouse_batch_map.get(item_code, {}).items():
		for batch, qty_dict in batches.items():
			if not qty_dict.bal_qty:
				continue

			details.append({"warehouse": warehouse, "batch": batch, "qty": qty_dict.bal_qty})

	return details


def _get_recent_movement(item_code):
	"""The caller's 5 most recent Stock Ledger Entry rows for one item."""
	return frappe.get_all(
		"Stock Ledger Entry",
		filters={"item_code": item_code, "is_cancelled": 0, "owner": frappe.session.user},
		fields=["voucher_type", "voucher_no", "warehouse"],
		order_by="posting_datetime desc, creation desc",
		limit=5,
	)


