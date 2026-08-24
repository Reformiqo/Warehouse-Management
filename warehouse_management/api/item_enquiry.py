"""Item Enquiry — every item, with today's stock spread plus open PO/SO
linkage.

Starts from the full Item catalog, not the Stock Balance report output,
so an item with no stock/PO/SO activity still appears with zeros rather
than being silently omitted. Stock comes from ERPNext's own Stock
Balance report for today only (from_date = to_date = today); open_so/
open_po are computed with one aggregate query each across all items, not
per item, to avoid N+1 queries.
"""

import frappe
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
