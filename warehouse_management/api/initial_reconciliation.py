"""Initial reconciliation — a warehouse's one-off first count.

The assignments are seeded by patches/create_initial_reconciliation_assignments
and flagged is_initial_reconciliation, one per warehouse and assigned to
nobody. A user picks a warehouse, lists it with warehouse_items, counts each
row through set_variation, adds anything the system never knew about with
add_item, then posts it with create_stock_reconciliation — which hands the
work to the daily endpoint and marks the warehouses after.
"""

import frappe
from frappe.utils import cint, cstr, flt, strip

from warehouse_management.api.daily_assignment import _variation_label
from warehouse_management.api.stock_reconciliation import (
	_create_for_warehouse,
	_validate_items,
)
from warehouse_management.api.stock_reconciliation import (
	create_stock_reconciliation as create_daily_reconciliation,
)
from warehouse_management.utils import strip_link_marker
from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20


@frappe.whitelist(methods=["GET"])
def warehouse_items(warehouse=None, search=None, limit=None, offset=None):
	"""Return the rows to count on a warehouse's initial reconciliation, each
	with the task id set_variation writes to and the count standing on it.
	`initial_reconciliation` says whether the warehouse has already been
	reconciled.

	Query params: `warehouse` (required); `search` (matches item code or
	name), `limit` (default 20) and `offset` (rows to skip, default 0)
	optional. unique_items and total_system_qty describe the whole
	warehouse, not the searched page.
	"""
	try:
		warehouse = strip_link_marker(warehouse)
		if not warehouse:
			return error("Please provide a warehouse.", 400)

		record = frappe.db.get_value(
			"Warehouse",
			{"name": warehouse, "disabled": 0},
			["name", "initial_reconciliation"],
			as_dict=True,
		)
		if not record:
			return error(f"Warehouse '{warehouse}' not found or is disabled.", 404)

		assignment = _initial_assignment(warehouse)

		search = strip(frappe.utils.strip_html(cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		items = _assignment_tasks(assignment)
		unique_items = len(items)
		total_system_qty = sum(item["system_qty"] for item in items)

		if search:
			needle = search.lower()
			items = [
				item
				for item in items
				if needle in item["item_code"].lower() or needle in (item["item_name"] or "").lower()
			]

		# resolved for the page only, so a warehouse of 1000 items costs one
		# extra query rather than one per item
		page = items[offset : offset + limit]
		for item in page:
			item["warehouses"] = warehouse

		return success(
			data={
				"warehouse": warehouse,
				"initial_reconciliation": cint(record.initial_reconciliation),
				"assignment_id": assignment,
				"unique_items": unique_items,
				"total_system_qty": total_system_qty,
				"items": page,
			}
		)
	except Exception as e:
		frappe.log_error(title="Warehouse items lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def set_variation(assignment_id=None, task_id=None, user_counted=None):
	"""Record what was counted on one row of a warehouse's initial
	reconciliation, and derive the variation against the system qty.

	Body: `{assignment_id, task_id, user_counted}`, both ids as they came back
	from warehouse_items. There is no owner check — an initial assignment
	belongs to a warehouse, not to an employee.
	"""
	try:
		assignment_id = strip(cstr(assignment_id))
		task_id = strip(cstr(task_id))
		if not assignment_id or not task_id:
			return error("Please provide assignment_id and task_id.", 400)

		if user_counted in (None, ""):
			return error("Please provide a user_counted.", 400)

		task = frappe.db.get_value(
			"Warehouse Daily Assignment Task",
			{"name": task_id, "parent": assignment_id},
			["name", "qty"],
			as_dict=True,
		)
		if not task:
			return error(f"Task '{task_id}' is not on assignment '{assignment_id}'.", 404)

		user_counted = flt(user_counted)
		variation = _variation_label(user_counted, task.qty)

		# counting the row is what completes it, so the rollup moves with the count
		frappe.db.set_value(
			"Warehouse Daily Assignment Task",
			task_id,
			{"user_counted": user_counted, "variation": variation, "is_completed": 1},
		)
		frappe.db.commit()

		return success(
			data={
				"assignment_id": assignment_id,
				"task_id": task_id,
				"system_qty": flt(task.qty),
				"user_counted": user_counted,
				"variation": variation,
				"is_completed": 1,
			}
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Initial set variation failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def add_item(assignment_id=None, item_code=None, user_counted=None):
	"""Add stock the system does not hold to an initial reconciliation,
	already counted.

	Body: `{assignment_id, item_code, user_counted}`. System qty is 0 — the
	stock is on the shelf but was never on the books — so the whole counted
	figure is the variation.
	"""
	try:
		assignment_id = strip(cstr(assignment_id))
		item_code = strip(cstr(item_code))
		user_counted = flt(user_counted)

		existing = frappe.db.get_value(
			"Warehouse Daily Assignment Task",
			{"parent": assignment_id, "item_code": item_code},
			"name",
		)
		if existing:
			return error(f"Item '{item_code}' is already on this warehouse.", 400, task_id=existing)

		task = _append_task(assignment_id, item_code, user_counted)
		frappe.db.commit()

		return success(
			data={
				"assignment_id": assignment_id,
				"task_id": task.name,
				"item_code": item_code,
				"item_name": task.item_name,
				"system_qty": 0.0,
				"user_counted": user_counted,
				"variation": task.variation,
				"is_completed": 1,
			},
			http_status=201,
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Initial reconciliation add item failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def create_initial_stock_reconciliation(assignment_id=None, warehouse=None, items=None):
	"""Post a warehouse's first count, link it to the initial assignment and
	mark the warehouse reconciled.

	Body: `{assignment_id, warehouse, items}`, items being `[{item_code, qty,
	images}]`. `images` is optional and holds up to three urls already uploaded
	through /api/method/upload_file, saved on that item's reconciliation row.
	"""
	try:
		items = frappe.parse_json(items) if isinstance(items, str) else items
		warehouse = strip_link_marker(warehouse)

		for item in items if isinstance(items, list) else []:
			item["warehouse"] = warehouse

		validation_error = _validate_items(items)
		if validation_error:
			return validation_error

		employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
		if not employee:
			return error("No Employee is linked to your user account.", 404)

		assignment_id = strip_link_marker(assignment_id) or _initial_assignment(warehouse)
		if not assignment_id:
			return error(f"No initial reconciliation found for warehouse '{warehouse}'.", 404)

		if not frappe.db.exists("Warehouse Daily Assignment", assignment_id):
			return error(f"No initial reconciliation found for warehouse '{warehouse}'.", 404)

		pending = frappe.get_all(
			"Warehouse Daily Assignment Task",
			filters={"parent": assignment_id, "is_completed": 0},
			pluck="item_code",
		)
		if pending:
			return error(f"Please complete the reconciliation for: {', '.join(pending)}.", 400)

		varied = _items_with_variation(
			warehouse, {item["item_code"]: flt(item.get("qty")) for item in items}
		)
		if not varied:
			frappe.db.set_value("Warehouse Daily Assignment", assignment_id, {"no_variation": 1})
			frappe.db.set_value("Warehouse", warehouse, "initial_reconciliation", 1)
			frappe.db.commit()

			return success(data={"no_variation": warehouse})

		name = _create_for_warehouse(warehouse, varied)
		_set_row_images(name, {item["item_code"]: item.get("images") for item in items})

		frappe.db.set_value(
			"Warehouse Daily Assignment", assignment_id, {"stock_reconciliation": name}
		)
		frappe.db.set_value("Warehouse", warehouse, "initial_reconciliation", 1)
		frappe.db.commit()

		return success(data={"stock_reconciliation_id": name}, http_status=201)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Initial stock reconciliation failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _initial_assignment(warehouse):
	"""Name of the warehouse's seeded initial reconciliation, or None."""
	return frappe.db.get_value(
		"Warehouse Daily Assignment",
		{"warehouse": warehouse, "is_initial_reconciliation": 1},
		"name",
	)


def _assignment_tasks(assignment):
	"""The seeded rows, each carrying the task id to write back to and the
	count standing on it. system_qty is what the patch stamped from Bin, so
	the figure counted against does not shift under the user mid-count.
	"""
	rows = frappe.db.sql(
		"""
		SELECT task.name AS task_id, task.item_code, task.item_name,
		       task.qty AS system_qty, task.user_counted, task.variation,
		       task.is_completed
		FROM `tabWarehouse Daily Assignment Task` task
		WHERE task.parent = %(assignment)s
		ORDER BY task.idx
		""",
		{"assignment": assignment},
		as_dict=True,
	)

	for row in rows:
		row.system_qty = flt(row.system_qty)
		row.user_counted = flt(row.user_counted)

	return rows


def _append_task(assignment, item_code, user_counted):
	"""Add one counted row to the assignment. Saved through the document so
	idx, total_tasks and the item_name fetched from Item all stay in step.
	"""
	doc = frappe.get_doc("Warehouse Daily Assignment", assignment)
	task = doc.append(
		"tasks",
		{
			"item_code": item_code,
			"qty": 0,
			"user_counted": user_counted,
			"variation": _variation_label(user_counted, 0),
			"is_completed": 1,
		},
	)
	doc.total_tasks = len(doc.tasks)
	doc.save(ignore_permissions=True)

	return task


def _mark_reconciled(items):
	"""Flag every warehouse just counted. Already-flagged ones are filtered
	out rather than rewritten, same as hooks.py does on submit.
	"""
	warehouses = list({row["warehouse"] for row in items if row.get("warehouse")})
	if not warehouses:
		return

	frappe.db.set_value(
		"Warehouse",
		{"name": ["in", warehouses], "initial_reconciliation": 0},
		"initial_reconciliation",
		1,
	)


def _items_with_variation(warehouse, item_qty_map):
	"""Drop items counted at exactly what the system holds — ERPNext strips
	unchanged rows and refuses a reconciliation left with none.
	"""
	bins = frappe.get_all(
		"Bin",
		filters={"warehouse": warehouse, "item_code": ["in", list(item_qty_map)]},
		fields=["item_code", "actual_qty"],
	)
	system_qty = {row.item_code: flt(row.actual_qty, 6) for row in bins}

	return {
		item_code: qty
		for item_code, qty in item_qty_map.items()
		if flt(qty, 6) != system_qty.get(item_code, 0.0)
	}


def _set_row_images(reconciliation, item_images):
	"""Stamp each item's images onto its row of the reconciliation. Items
	counted at system qty are not on the document, so their images drop with
	the row ERPNext strips anyway.
	"""
	rows = frappe.get_all(
		"Stock Reconciliation Item",
		filters={"parent": reconciliation},
		fields=["name", "item_code"],
	)
	for row in rows:
		urls = (item_images.get(row.item_code) or [])[:3]
		if urls:
			frappe.db.set_value(
				"Stock Reconciliation Item",
				row.name,
				{f"reconciliation_image_{index}": url for index, url in enumerate(urls, start=1)},
			)
