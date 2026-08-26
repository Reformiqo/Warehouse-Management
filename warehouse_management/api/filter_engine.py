import frappe
from frappe.utils import cint, cstr, flt, strip, today

from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20
MOVEMENT_CONDITIONS = {"incoming": "sle.actual_qty > 0", "outgoing": "sle.actual_qty < 0"}


@frappe.whitelist(methods=["GET"])
def filter_stock_movement(
	warehouse=None,
	item_code=None,
	item_group=None,
	brand=None,
	from_date=None,
	to_date=None,
	movement_type=None,
	limit=None,
	offset=None,
):
	try:
		filters = {
			"warehouse": strip(cstr(warehouse)),
			"item_code": strip(cstr(item_code)),
			"item_group": strip(cstr(item_group)),
			"brand": strip(cstr(brand)),
			"from_date": strip(cstr(from_date)) or today(),
			"to_date": strip(cstr(to_date)) or today(),
			"movement_type": strip(cstr(movement_type)).lower(),
		}

		items = _matching_items(filters, cint(limit) or DEFAULT_LIMIT, cint(offset))
		total_unique_items = _count_items(filters)

		return success(
			data={
				"total_count": total_unique_items,
				"summary": {
					"total_unique_items": total_unique_items,
					"total_quantity": _total_quantity(filters),
				},
				"filters": filters,
				"items": _build_items(items, filters),
			}
		)
	except Exception as e:
		frappe.log_error(title="Stock movement filter failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _matching_items(filters, limit, offset):
	"""One page of items matching the item-side filters, oldest grouping first."""
	joins, conditions, values = _item_conditions(filters)

	return frappe.db.sql(
		f"""
		SELECT DISTINCT item.name AS item_code, item.item_name, item.item_group, item.brand
		FROM `tabItem` item {joins}
		WHERE {conditions}
		ORDER BY item.item_group, item.name
		LIMIT %(limit)s OFFSET %(offset)s
		""",
		{**values, "limit": limit, "offset": offset},
		as_dict=True,
	)


def _build_items(items, filters):
	"""Attach moved qty and reserved qty to the fetched page of items."""
	item_codes = [row.item_code for row in items]
	moved = _movement_by_item(item_codes, filters)
	reserved = _get_reserved_qty(item_codes, filters["warehouse"])

	return [
		{
			"item_code": row.item_code,
			"item_name": row.item_name or "",
			"item_group": row.item_group or "",
			"brand": row.brand or "",
			"qty": flt(moved.get(row.item_code)),
			"reserved_qty": flt(reserved.get(row.item_code)),
		}
		for row in items
	]


def _movement_by_item(item_codes, filters):
	"""{item_code: net moved qty} for the given items inside the date range."""
	if not item_codes:
		return {}

	conditions, values = _sle_conditions(filters)
	rows = frappe.db.sql(
		f"""
		SELECT sle.item_code, SUM(sle.actual_qty) AS qty
		FROM `tabStock Ledger Entry` sle
		WHERE {conditions} AND sle.item_code IN %(item_codes)s
		GROUP BY sle.item_code
		""",
		{**values, "item_codes": tuple(item_codes)},
		as_dict=True,
	)
	return {row.item_code: row.qty for row in rows}


def _get_reserved_qty(item_codes, warehouse):
	"""{item_code: reserved_qty} from Bin, confined to one warehouse when the
	filter names one, else summed across every warehouse holding the item."""
	if not item_codes:
		return {}

	conditions = ["item_code IN %(item_codes)s"]
	values = {"item_codes": tuple(item_codes)}

	if warehouse:
		conditions.append("warehouse = %(warehouse)s")
		values["warehouse"] = warehouse

	rows = frappe.db.sql(
		f"""
		SELECT item_code, SUM(reserved_qty) AS reserved_qty
		FROM `tabBin`
		WHERE {" AND ".join(conditions)}
		GROUP BY item_code
		""",
		values,
		as_dict=True,
	)
	return {row.item_code: row.reserved_qty for row in rows}


def _count_items(filters):
	"""Distinct items matching the item-side filters, ignoring paging."""
	joins, conditions, values = _item_conditions(filters)

	return cint(
		frappe.db.sql(
			f"SELECT COUNT(DISTINCT item.name) FROM `tabItem` item {joins} WHERE {conditions}",
			values,
		)[0][0]
	)


def _total_quantity(filters):
	"""Net moved qty across every matching item, so it holds steady while paging."""
	joins, item_conditions, item_values = _item_conditions(filters)
	sle_conditions, sle_values = _sle_conditions(filters)

	return flt(
		frappe.db.sql(
			f"""
			SELECT SUM(sle.actual_qty)
			FROM `tabStock Ledger Entry` sle
			INNER JOIN `tabItem` item ON item.name = sle.item_code {joins}
			WHERE {sle_conditions} AND {item_conditions}
			""",
			{**item_values, **sle_values},
		)[0][0]
	)


def _item_conditions(filters):
	"""JOIN and WHERE for the item list. A warehouse filter narrows to items
	binned there, so choosing a warehouse does not list the whole item master."""
	joins = ""
	conditions = ["item.disabled = 0"]
	values = {}

	if filters["warehouse"]:
		joins = "INNER JOIN `tabBin` bin ON bin.item_code = item.name AND bin.warehouse = %(warehouse)s"
		values["warehouse"] = filters["warehouse"]

	for fieldname, clause in (
		("item_code", "item.name = %(item_code)s"),
		("item_group", "item.item_group = %(item_group)s"),
		("brand", "item.brand = %(brand)s"),
	):
		if filters[fieldname]:
			conditions.append(clause)
			values[fieldname] = filters[fieldname]

	return joins, " AND ".join(conditions), values


def _sle_conditions(filters):
	"""WHERE clauses limiting the ledger to the requested range and direction."""
	conditions = ["sle.is_cancelled = 0", "sle.posting_date BETWEEN %(from_date)s AND %(to_date)s"]
	values = {"from_date": filters["from_date"], "to_date": filters["to_date"]}

	if filters["warehouse"]:
		conditions.append("sle.warehouse = %(warehouse)s")
		values["warehouse"] = filters["warehouse"]

	movement_condition = MOVEMENT_CONDITIONS.get(filters["movement_type"])
	if movement_condition:
		conditions.append(movement_condition)

	return " AND ".join(conditions), values
