import frappe
from erpnext.stock.report.batch_wise_balance_history.batch_wise_balance_history import (
	get_item_warehouse_batch_map,
)
from erpnext.stock.report.stock_balance.stock_balance import execute as run_stock_balance
from frappe.query_builder.functions import Count
from frappe.utils import cint, flt

from warehouse_management.api.profile import OPEN_PO_STATUSES, OPEN_SO_STATUSES
from warehouse_management.utils import (
	get_open_order_counts,
	get_pending_sales_orders,
	item_search_filters,
)
from warehouse_management.utils.response import error, success
from warehouse_management.warehouse_management.doctype.warehouse_item_reconciliation.warehouse_item_reconciliation import (
	get_item_reconciliation,
)

DEFAULT_LIMIT = 20
# standard_sale_price is read off this Price List
SELLING_PRICE_LIST = "Standard Selling"


@frappe.whitelist(methods=["GET"])
def item_enquiry(search=None, barcode=None, limit=None, offset=None):
	"""Return items with today's stock spread plus open PO/SO linkage.

	Query params, all optional: `barcode` (matches part of any of the item's
	barcodes, wins over `search`), `search` (matches the item code or the item
	name), `limit` (default 20) and `offset` (rows to skip, default 0).
	total_item is the count matching the filter, so the client can page through
	it. Rows come back by item name, ascending.
	"""
	try:
		search = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(search)))
		barcode = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(barcode)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		search_filters, or_filters = item_search_filters(search, barcode)
		# disabled is always in play, so the count matches the page below it
		filters = [["Item", "disabled", "=", 0], *search_filters]
		total_item = frappe.db.count("Item", filters)

		stock_by_item = _get_stock_by_item()
		open_so = get_open_order_counts("Sales Order Item", "Sales Order", OPEN_SO_STATUSES)
		open_po = get_open_order_counts("Purchase Order Item", "Purchase Order", OPEN_PO_STATUSES)

		page = frappe.get_all(
			"Item",
			filters=filters,
			or_filters=or_filters,
			fields=["item_code", "item_name", "item_group"],
			order_by="item_name asc",
			limit_start=offset,
			limit_page_length=limit,
			distinct=True,
		)

		items = []
		for item in page:
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

		return success(data={"total_item": total_item, "items": items})
	except Exception as e:
		frappe.log_error(title="Warehouse item enquiry failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def item_detail(item_code=None):
	"""Return today's per-warehouse batch breakdown, pending Sales Orders and
	the item's three rates.

	Query param: `item_code` (required). last_purchase_rate and
	avg_purchase_price are stored on the Item; standard_sale_price is the mean
	of the item's Standard Selling prices. `reconciliation` is served straight
	off Warehouse Item Reconciliation — per warehouse, the last count and the
	stock in and out since it.
	"""
	try:
		item_code = frappe.utils.strip(frappe.utils.cstr(item_code))
		if not item_code:
			return error("Please provide an item_code.", 400)

		if not frappe.db.exists("Item", {"name": item_code, "disabled": 0}):
			return error(f"Item '{item_code}' not found or is disabled.", 404)

		rates = _get_item_rates(item_code)
		return success(
			data={
				"warehouse_wise_stock": _get_warehouse_details(item_code),
				"reconciliation": get_item_reconciliation(item_code),
				"pending_sales_orders": get_pending_sales_orders(item_code, OPEN_SO_STATUSES),
				"recent_movement": _get_recent_movement(item_code),
				"last_purchase_rate": rates["last_purchase_rate"],
				"avg_purchase_price": rates["avg_purchase_price"],
				"standard_sale_price": rates["standard_sale_price"],
			}
		)
	except Exception as e:
		frappe.log_error(title="Warehouse item detail failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _count_items(filters, or_filters):
	"""Items matching the enquiry filters. db.count() takes no or_filters, and a
	barcode search joins Item Barcode, so the distinct name count is built
	through the query builder instead.
	"""
	item = frappe.qb.DocType("Item")
	query = frappe.qb.get_query(
		table="Item", filters=filters, or_filters=or_filters, fields=Count(item.name).distinct()
	)
	return query.run()[0][0]


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
	"""[{warehouse, batch, qty}, ...] for one item, zero balances left out.

	Only a batched item can be broken down per batch — the batch report reads
	ledger entries that carry a batch and returns nothing for anything else, so
	the rest is read straight off Bin with batch left None.
	"""
	if frappe.db.get_value("Item", item_code, "has_batch_no"):
		return _get_batch_details(item_code)

	bins = frappe.get_all(
		"Bin",
		filters={"item_code": item_code, "actual_qty": ["!=", 0]},
		fields=["warehouse", "actual_qty as qty"],
		order_by="warehouse",
	)
	return [{"warehouse": row.warehouse, "batch": None, "qty": row.qty} for row in bins]


def _get_batch_details(item_code):
	"""[{warehouse, batch, qty}, ...] for a batched item, today only.

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


def _get_item_rates(item_code):
	"""The three rates on the detail screen. The purchase ones are stored on the
	Item; the selling one averages every Standard Selling price on the item,
	since some items carry a row per uom or validity window.
	"""
	item = frappe.db.get_value(
		"Item", item_code, ["last_purchase_rate", "custom_hns_avg_purchase_rate"], as_dict=True
	)
	prices = [
		flt(rate)
		for rate in frappe.get_all(
			"Item Price",
			filters={"item_code": item_code, "price_list": SELLING_PRICE_LIST},
			pluck="price_list_rate",
		)
	]
	return {
		"last_purchase_rate": flt(item.last_purchase_rate),
		"avg_purchase_price": flt(item.custom_hns_avg_purchase_rate),
		"standard_sale_price": flt(sum(prices) / len(prices)) if prices else 0.0,
	}
