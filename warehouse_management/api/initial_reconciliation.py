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
def create_stock_reconciliation(items=None, file_url=None):
	"""Post a warehouse's first count. The daily endpoint does the work —
	raising the Stock Reconciliation and closing the assignment it belongs to —
	and the warehouses counted are then marked as initially reconciled.

	Body: `{items, file_url}`, the same shape the daily endpoint takes: items
	is `[{warehouse, item_code, qty}]`.
	"""
	try:
		items = frappe.parse_json(items) if isinstance(items, str) else items

		response = create_daily_reconciliation(items=items, file_url=file_url)
		if not response.get("success"):
			return response

		_mark_reconciled(items)
		frappe.db.commit()

		return response
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Initial reconciliation failed", message=frappe.get_traceback())
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
