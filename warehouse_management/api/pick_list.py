import frappe
from erpnext.selling.doctype.sales_order.sales_order import create_pick_list as map_pick_list_from_so
from frappe.utils import cint, flt

from warehouse_management.api.profile import OPEN_SO_STATUSES
from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20

# Custom Table field on Pick List, holding Hns Packing Slip rows
PACKING_SLIP_FIELD = "custom_packing_slip"


@frappe.whitelist(methods=["GET"])
def open_so(limit=None, offset=None):
	"""Return open Sales Orders (To Deliver and Bill / To Deliver) that
	still need picking, each flagged with whether every pending item is
	already sitting in its own warehouse.

	Query params, both optional: `limit` (default 20) and `offset`
	(rows to skip, default 0). Availability is resolved for the returned
	page in one extra query, so the cost holds steady however many open
	orders, items or warehouses there are.
	"""
	try:
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		rows = frappe.db.sql(
			"""
			SELECT sales_order.name AS id, sales_order.customer,
			       COUNT(DISTINCT so_item.item_code) AS total_items
			FROM `tabSales Order` sales_order
			INNER JOIN `tabSales Order Item` so_item ON so_item.parent = sales_order.name
			WHERE sales_order.status IN %(statuses)s
			GROUP BY sales_order.name
			ORDER BY sales_order.transaction_date DESC
			LIMIT %(limit)s OFFSET %(offset)s
			""",
			{"statuses": tuple(OPEN_SO_STATUSES), "limit": limit, "offset": offset},
			as_dict=True,
		)
		if not rows:
			return success(data=[])

		availability = _availability_by_so([row.id for row in rows])
		for row in rows:
			stats = availability.get(row.id, {})
			row["pending_items"] = stats.get("pending_items", 0)
			row["short_items"] = stats.get("short_items", 0)
			row["is_fully_available"] = bool(row["pending_items"]) and not row["short_items"]

		return success(data=rows)
	except Exception as e:
		frappe.log_error(title="Pick list lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def so_pending_items(so_id=None):
	"""Return the Sales Order lines still awaiting delivery, with ordered,
	delivered and pending quantities.

	Query param: `so_id` (required).
	"""
	try:
		so_id = frappe.utils.strip(frappe.utils.cstr(so_id))
		if not so_id:
			return error("Please provide a so_id.", 400)

		if not frappe.db.exists("Sales Order", so_id):
			return error(f"Sales Order '{so_id}' not found.", 404)

		rows = frappe.get_all(
			"Sales Order Item",
			filters={"parent": so_id},
			fields=["item_code", "item_name", "warehouse", "qty", "delivered_qty"],
			order_by="idx",
		)

		return success(
			data=[
				{
					"item_code": row.item_code,
					"item_name": row.item_name,
					"warehouse": row.warehouse,
					"total_qty": flt(row.qty),
					"delivered_qty": flt(row.delivered_qty),
					"pending_qty": flt(row.qty) - flt(row.delivered_qty),
				}
				for row in rows
				if flt(row.delivered_qty) < flt(row.qty)
			]
		)
	except Exception as e:
		frappe.log_error(title="SO pending items lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def create_pick_list(so_id=None, items=None):
	try:
		so_id = frappe.utils.strip(frappe.utils.cstr(so_id))
		if not so_id:
			return error("Please provide a so_id.", 400)

		if not frappe.db.exists("Sales Order", so_id):
			return error(f"Sales Order '{so_id}' not found.", 404)

		item_qty_map = frappe.parse_json(items) if isinstance(items, str) else items
		if not item_qty_map:
			return error("Please provide items as {item_code: qty}.", 400)

		so_status = frappe.db.get_value("Sales Order", so_id, "status")
		if so_status not in OPEN_SO_STATUSES:
			return error(f"Sales Order '{so_id}' has nothing pending to deliver.", 400)

		pick_list_doc = map_pick_list_from_so(so_id)
		pick_list_doc.flags.ignore_permissions = True
		pick_list_doc.set_item_locations()
		pick_list_doc.locations = _apply_requested_items(pick_list_doc.locations, item_qty_map)
		if not pick_list_doc.locations:
			return error("None of the given items are pending on this Sales Order.", 400)

		pick_list_doc.insert(ignore_permissions=True)
		frappe.db.commit()

		return success(
			data={"pick_list_id": pick_list_doc.name, "message": "Pick list created in draft."},
			http_status=201,
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Pick list creation failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def packing_list(limit=None, offset=None):
	"""Return the packing list — Pick Lists still in draft, with their
	customer and how many distinct items each one covers.

	Query params, both optional: `limit` (default 20) and `offset`
	(rows to skip, default 0). Newest touched first.
	"""
	try:
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		rows = frappe.db.sql(
			"""
			SELECT pick_list.name AS id, pick_list.customer, pick_list.customer_name,
			       COUNT(DISTINCT pick_list_item.item_code) AS total_items
			FROM `tabPick List` pick_list
			LEFT JOIN `tabPick List Item` pick_list_item
			       ON pick_list_item.parent = pick_list.name
			WHERE pick_list.docstatus = 0
			GROUP BY pick_list.name
			ORDER BY pick_list.modified DESC
			LIMIT %(limit)s OFFSET %(offset)s
			""",
			{"limit": limit, "offset": offset},
			as_dict=True,
		)
		return success(data=rows)
	except Exception as e:
		frappe.log_error(title="Packing list lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def pick_list_details(pick_list_id=None):
	"""Return one Pick List: its customer, every item row with the qty to
	pick, and the Delivery Notes raised against it.

	Query param: `pick_list_id` (required). Each row carries a `row_id` —
	pack_pick_list takes those back, since one item can sit on several rows.
	"""
	try:
		pick_list_id = frappe.utils.strip(frappe.utils.cstr(pick_list_id))
		if not pick_list_id:
			return error("Please provide a pick_list_id.", 400)

		pick_list = frappe.db.get_value(
			"Pick List",
			pick_list_id,
			["name", "customer", "customer_name", "status", "docstatus"],
			as_dict=True,
		)
		if not pick_list:
			return error(f"Pick List '{pick_list_id}' not found.", 404)

		rows = frappe.get_all(
			"Pick List Item",
			filters={"parent": pick_list_id},
			fields=[
				"name as row_id",
				"item_code",
				"item_name",
				"warehouse",
				"qty",
				"stock_qty",
				"picked_qty",
			],
			order_by="idx",
		)

		return success(
			data={
				"pick_list_id": pick_list.name,
				"customer": pick_list.customer,
				"customer_name": pick_list.customer_name,
				"status": pick_list.status,
				"docstatus": pick_list.docstatus,
				"total_items": len({row.item_code for row in rows}),
				"delivery_notes": _delivery_notes_for(pick_list_id),
				"items": rows,
			}
		)
	except Exception as e:
		frappe.log_error(title="Pick list details lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def pack_pick_list(pick_list_id=None, items=None):
	"""Fill the Packing Slip table on a draft Pick List and submit it.

	Body: `{pick_list_id, items}` — items is `[{item_code, qty, box_number,
	box_weight}]`, one row per item per box, qty in stock UOM. The packed
	total per item also lands on picked_qty, which ERPNext would otherwise
	fill with the whole pick qty on submit.
	"""
	try:
		pick_list_id = frappe.utils.strip(frappe.utils.cstr(pick_list_id))
		if not pick_list_id:
			return error("Please provide a pick_list_id.", 400)

		if not frappe.db.exists("Pick List", pick_list_id):
			return error(f"Pick List '{pick_list_id}' not found.", 404)

		items = frappe.parse_json(items) if isinstance(items, str) else items

		pick_list_doc = frappe.get_doc("Pick List", pick_list_id)
		if pick_list_doc.docstatus != 0:
			return error(f"Pick List '{pick_list_id}' is not in draft.", 400)

		rows_by_item = {}
		for row in pick_list_doc.locations:
			rows_by_item.setdefault(row.item_code, []).append(row)

		validation_error = _validate_packed_items(items, rows_by_item)
		if validation_error:
			return validation_error

		_set_packing_slip(pick_list_doc, items, rows_by_item)

		pick_list_doc.flags.ignore_permissions = True
		pick_list_doc.submit()
		frappe.db.commit()

		return success(
			data={
				"pick_list_id": pick_list_doc.name,
				"status": pick_list_doc.status,
				"message": "Pick list submitted.",
			}
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Pick list packing failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _delivery_notes_for(pick_list_id):
	"""Delivery Notes raised against this Pick List. The link lives on
	Delivery Note Item.against_pick_list, not on the Pick List itself, so a
	draft one always comes back empty.
	"""
	return frappe.get_all(
		"Delivery Note Item",
		filters={"against_pick_list": pick_list_id, "docstatus": ["<", 2]},
		pluck="parent",
		distinct=True,
	)


def _validate_packed_items(items, rows_by_item):
	"""Return an error, or None when every packed item is on this Pick List."""
	if not items or not isinstance(items, list):
		return error("Please provide items as [{item_code, qty, box_number}].", 400)

	for row in items:
		item_code = row.get("item_code")
		if not item_code:
			return error("Every item row needs an item_code.", 400)

		if item_code not in rows_by_item:
			return error(f"Item '{item_code}' is not on this Pick List.", 404)

		if flt(row.get("qty")) < 0:
			return error(f"Qty for item '{item_code}' cannot be negative.", 400)

	return None


def _set_packing_slip(pick_list_doc, items, rows_by_item):
	"""Replace the Packing Slip table with the packed rows — one per item per
	box — and push each item's packed total onto its location rows, so the
	pick list submits against what was actually packed.
	"""
	pick_list_doc.set(PACKING_SLIP_FIELD, [])
	packed_by_item = {}
	for row in items:
		item_code = row["item_code"]
		packed_by_item[item_code] = packed_by_item.get(item_code, 0) + flt(row.get("qty"))
		pick_list_doc.append(
			PACKING_SLIP_FIELD,
			{
				"item": item_code,
				"pick_qty": sum(flt(location.stock_qty) for location in rows_by_item[item_code]),
				"qty": flt(row.get("qty")),
				"box_number": flt(row.get("box_number")),
				"box_weight": flt(row.get("box_weight")),
			},
		)

	for item_code, packed_qty in packed_by_item.items():
		_apply_packed_qty(rows_by_item[item_code], packed_qty)


def _apply_packed_qty(rows, packed_qty):
	"""Spread a packed qty over that item's rows in order, filling each to its
	stock qty before the next — one item can sit on several warehouse rows.
	Anything left over lands on the last row for ERPNext to reject as over-picked.
	"""
	remaining = flt(packed_qty)
	for row in rows:
		row.picked_qty = min(remaining, flt(row.stock_qty))
		remaining -= row.picked_qty

	if remaining:
		rows[-1].picked_qty += remaining


def _availability_by_so(so_ids):
	"""{so_id: {pending_items, short_items}} for the given orders. Pending qty
	is summed per (item, warehouse) first, so two lines of the same item can't
	each claim the whole Bin, and it is scaled to stock UOM to match Bin.
	"""
	rows = frappe.db.sql(
		"""
		SELECT pending.so_id,
		       SUM(CASE WHEN ROUND(pending.pending_qty, 6) > ROUND(pending.available_qty, 6)
		                THEN 1 ELSE 0 END) AS short_items
		FROM (
			SELECT so_item.parent AS so_id, so_item.item_code, so_item.warehouse,
			       SUM((so_item.qty - so_item.delivered_qty) * so_item.conversion_factor)
			           AS pending_qty,
			       IFNULL(MAX(bin.actual_qty), 0) AS available_qty
			FROM `tabSales Order Item` so_item
			LEFT JOIN `tabBin` bin ON bin.item_code = so_item.item_code
			                      AND bin.warehouse = so_item.warehouse
			WHERE so_item.parent IN %(so_ids)s
			GROUP BY so_item.parent, so_item.item_code, so_item.warehouse
		) pending
		GROUP BY pending.so_id
		""",
		{"so_ids": tuple(so_ids)},
		as_dict=True,
	)
	return {
		row.so_id: {"pending_items": cint(row.pending_items), "short_items": cint(row.short_items)}
		for row in rows
	}


def _apply_requested_items(locations, item_qty_map):
	"""Keep only the rows for items in item_qty_map, with qty/stock_qty
	overridden to the requested amount.
	"""
	kept_rows = []
	for row in locations:
		if row.item_code not in item_qty_map:
			continue
		row.qty = flt(item_qty_map[row.item_code])
		row.stock_qty = row.qty * (flt(row.conversion_factor) or 1)
		kept_rows.append(row)
	return kept_rows
