"""Per warehouse reconciliation on an Item.

A child table holding one row per warehouse counted: what the last count
found there, and the stock in and out since. A Stock Reconciliation is what
puts a warehouse on the table and clears its movement; a Purchase Receipt,
Delivery Note or Stock Entry re-totals the movement against that count,
and passes over a warehouse the item has never been counted in.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now

DOCTYPE = "Warehouse Item Reconciliation"
PARENTFIELD = "warehouse_reconciliation"

# where a document row names its warehouse; a Stock Entry names both ends
WAREHOUSE_FIELDS = ("warehouse", "s_warehouse", "t_warehouse")

# everything submitted for one item and warehouse since it was counted
MOVEMENT = """
	SELECT 'in' AS direction, SUM(item.stock_qty) AS qty
	FROM `tabPurchase Receipt Item` item
	INNER JOIN `tabPurchase Receipt` doc ON doc.name = item.parent
	WHERE doc.docstatus = 1 AND item.item_code = %(item_code)s
	  AND item.warehouse = %(warehouse)s
	  AND TIMESTAMP(doc.posting_date, doc.posting_time) > %(since)s

	UNION ALL

	SELECT 'out', SUM(item.stock_qty)
	FROM `tabDelivery Note Item` item
	INNER JOIN `tabDelivery Note` doc ON doc.name = item.parent
	WHERE doc.docstatus = 1 AND item.item_code = %(item_code)s
	  AND item.warehouse = %(warehouse)s
	  AND TIMESTAMP(doc.posting_date, doc.posting_time) > %(since)s

	UNION ALL

	SELECT 'in', SUM(item.transfer_qty)
	FROM `tabStock Entry Detail` item
	INNER JOIN `tabStock Entry` doc ON doc.name = item.parent
	WHERE doc.docstatus = 1 AND item.item_code = %(item_code)s
	  AND item.t_warehouse = %(warehouse)s
	  AND TIMESTAMP(doc.posting_date, doc.posting_time) > %(since)s

	UNION ALL

	SELECT 'out', SUM(item.transfer_qty)
	FROM `tabStock Entry Detail` item
	INNER JOIN `tabStock Entry` doc ON doc.name = item.parent
	WHERE doc.docstatus = 1 AND item.item_code = %(item_code)s
	  AND item.s_warehouse = %(warehouse)s
	  AND TIMESTAMP(doc.posting_date, doc.posting_time) > %(since)s
"""


class WarehouseItemReconciliation(Document):
	pass


def update_reconciliation(doc, method=None):
	"""hooks.py doc_events target for Stock Reconciliation on_submit. A batched
	item is counted a row per batch, so the rows are summed back to one per
	item and warehouse before the pair's row is written.
	"""
	counted = {}
	for row in doc.items:
		if not row.warehouse:
			continue

		qty = counted.setdefault((row.item_code, row.warehouse), {"before": 0.0, "after": 0.0})
		qty["before"] += flt(row.current_qty)
		qty["after"] += flt(row.qty)

	for (item_code, warehouse), qty in counted.items():
		row = _row(item_code, warehouse)
		# the count is the new baseline, so what moved against the last one goes
		frappe.db.set_value(
			DOCTYPE,
			row.name if row else _add_row(item_code, warehouse),
			{
				"reconciled_by": frappe.session.user,
				"reconciled_on": now(),
				"qty_before": qty["before"],
				"qty_after": qty["after"],
				"qty_in": 0,
				"qty_out": 0,
			},
		)


def update_movement(doc, method=None):
	"""hooks.py doc_events target for Purchase Receipt, Delivery Note and
	Material Request on_submit / on_cancel. The totals are re-summed rather
	than added to, so a cancelled document drops out on its own.
	"""
	pairs = {
		(row.item_code, row.get(field)) for row in doc.items for field in WAREHOUSE_FIELDS if row.get(field)
	}

	for item_code, warehouse in pairs:
		row = _row(item_code, warehouse)
		if not row or not row.reconciled_on:
			continue

		qty_in, qty_out = _moved(item_code, warehouse, row.reconciled_on)
		frappe.db.set_value(DOCTYPE, row.name, {"qty_in": qty_in, "qty_out": qty_out})


def get_item_reconciliation(item_code):
	"""The item's rows, one per warehouse — what item enquiry displays."""
	rows = frappe.get_all(
		DOCTYPE,
		filters={"parent": item_code, "parenttype": "Item"},
		fields=[
			"warehouse",
			"reconciled_by",
			"reconciled_on",
			"qty_before",
			"qty_after",
			"qty_in",
			"qty_out",
		],
		order_by="warehouse asc",
	)

	for row in rows:
		row["reconciled_on"] = str(row["reconciled_on"]) if row["reconciled_on"] else None

	return rows


def _row(item_code, warehouse):
	"""The item's row for this warehouse, or None while it has never been
	counted there.
	"""
	return frappe.db.get_value(
		DOCTYPE,
		{"parent": item_code, "parenttype": "Item", "warehouse": warehouse},
		["name", "reconciled_on"],
		as_dict=True,
	)


def _add_row(item_code, warehouse):
	"""Put the warehouse on the item's table and return the new row. Written
	straight to the child table, so a count naming a hundred items doesn't fire
	a hundred Item saves and everything hooked onto them.
	"""
	row = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"parent": item_code,
			"parenttype": "Item",
			"parentfield": PARENTFIELD,
			"idx": frappe.db.count(DOCTYPE, {"parent": item_code, "parenttype": "Item"}) + 1,
			"warehouse": warehouse,
		}
	).insert(ignore_permissions=True)

	return row.name


def _moved(item_code, warehouse, since):
	"""(qty_in, qty_out) for one item and warehouse since it was counted."""
	rows = frappe.db.sql(
		MOVEMENT,
		{
			"item_code": item_code,
			"warehouse": warehouse,
			"since": since,
		},
		as_dict=True,
	)

	totals = {"in": 0.0, "out": 0.0}
	for row in rows:
		totals[row.direction] += flt(row.qty)

	return totals["in"], totals["out"]
