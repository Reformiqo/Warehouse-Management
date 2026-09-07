import frappe

from warehouse_management.api.daily_assignment import _get_tasks, _progress, _rollup_status
from warehouse_management.utils.response import error, success

# the fields that travel with a task row when it moves to another assignment —
# the count comes along so a row already reconciled stays reconciled, and
# item_name is left out because it is fetched from item_code
TASK_FIELDS = (
	"reference_doctype",
	"reference_name",
	"item_code",
	"qty",
	"user_counted",
	"variation",
	"is_completed",
)


@frappe.whitelist(methods=["GET"])
def employee_assignment(employee=None):
	"""Return today's assignments of one Employee — the drill-down behind a
	team_status row: the same rollup as my_assignment plus one row per task,
	each carrying its own warehouse, assignment and status.

	Query param: `employee` (the Employee id).
	"""
	try:
		employee = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(employee)))
		if not employee:
			return error("Please provide an employee.", 400)

		employee_name = frappe.db.get_value("Employee", employee, "employee_name")
		if not employee_name:
			return error(f"Employee '{employee}' not found.", 404)

		assignments = frappe.get_all(
			"Warehouse Daily Assignment",
			filters={
				"employee": employee,
				"assignment_date": frappe.utils.today(),
				"is_initial_reconciliation": 0,
			},
			fields=["name", "warehouse", "assignment_date", "total_tasks"],
			order_by="warehouse",
		)

		tasks = _get_tasks([assignment.name for assignment in assignments])
		warehouses = {assignment.name: assignment.warehouse for assignment in assignments}
		for task in tasks:
			task["warehouse"] = warehouses.get(task["assignment_id"])
			task["status"] = "Completed" if task["is_completed"] else "Pending"

		total_tasks, completed_tasks = _progress(assignments, tasks)

		return success(
			data={
				"employee": employee,
				"employee_name": employee_name,
				"total_tasks": total_tasks,
				"completed_tasks": completed_tasks,
				"status": _rollup_status(total_tasks, completed_tasks),
				"tasks": tasks,
			}
		)
	except Exception as e:
		frappe.log_error(title="Employee assignment lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def assign_tasks(employee=None, tasks=None):
	"""Move tasks over to another Employee, each onto their assignment for the
	same warehouse, created if they hold none today.

	Body: `{employee, tasks}`, tasks being `[{assignment_id, task_id}, ...]` so
	rows from different assignments can move together. A counted row moves with
	its count intact, and an assignment left with no tasks is deleted rather
	than kept empty. Nothing is written unless every row passes.
	"""
	try:
		employee = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(employee)))
		task_map = _as_task_map(tasks)
		if not employee or not task_map:
			return error("Please provide employee and tasks [{assignment_id, task_id}].", 400)

		sources = {}
		for assignment_id, task_ids in task_map.items():
			source, source_error = _source_assignment(assignment_id, task_ids, employee)
			if source_error:
				return source_error

			sources[assignment_id] = source

		moved = [
			_move_tasks(sources[assignment_id], task_ids, employee)
			for assignment_id, task_ids in task_map.items()
		]
		frappe.db.commit()

		return success(data={"employee": employee, "moved": moved})
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Assign tasks failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _source_assignment(assignment_id, task_ids, employee):
	"""(assignment doc, None) when every task can move off it, else (None, the
	error to return) — checked up front so a bad row moves nothing at all.
	"""
	source_exists = frappe.db.exists(
		"Warehouse Daily Assignment", {"name": assignment_id, "is_initial_reconciliation": 0}
	)
	if not source_exists:
		return None, error(f"Assignment '{assignment_id}' not found.", 404)

	source = frappe.get_doc("Warehouse Daily Assignment", assignment_id)
	if source.employee == employee:
		return None, error(f"Assignment '{assignment_id}' is already with this employee.", 400)

	rows = {row.name for row in source.tasks}
	missing = [task_id for task_id in task_ids if task_id not in rows]
	if missing:
		return None, error(f"Tasks not on assignment '{assignment_id}': {', '.join(missing)}.", 404)

	return source, None


def _move_tasks(source, task_ids, employee):
	"""Move the rows onto the employee's assignment for the same warehouse and
	drop the source once nothing is left on it.
	"""
	target = _assignment_doc(employee, source.warehouse)
	for row in source.tasks:
		if row.name in task_ids:
			target.append("tasks", {field: row.get(field) for field in TASK_FIELDS})

	_save_assignment(target)

	source.tasks = [row for row in source.tasks if row.name not in task_ids]
	if source.tasks:
		_save_assignment(source)
	else:
		source.delete(ignore_permissions=True)

	return {
		"source_assignment": source.name,
		"warehouse": source.warehouse,
		"assignment_id": target.name,
		"moved_tasks": len(task_ids),
	}


def _assignment_doc(employee, warehouse):
	"""That employee's assignment for the warehouse today — they hold one per
	warehouse — as a new unsaved doc when they hold none yet.
	"""
	name = frappe.db.get_value(
		"Warehouse Daily Assignment",
		{
			"employee": employee,
			"warehouse": warehouse,
			"assignment_date": frappe.utils.today(),
			"is_initial_reconciliation": 0,
		},
	)
	if name:
		return frappe.get_doc("Warehouse Daily Assignment", name)

	return frappe.get_doc(
		{
			"doctype": "Warehouse Daily Assignment",
			"warehouse": warehouse,
			"employee": employee,
			"assignment_date": frappe.utils.today(),
		}
	)


def _save_assignment(assignment):
	"""Save with total_tasks re-cut — it is a stored count of the distinct items
	on the assignment, so moving rows has to refresh it.
	"""
	assignment.total_tasks = len({row.item_code for row in assignment.tasks})
	assignment.save(ignore_permissions=True)


def _as_task_map(value):
	"""{assignment_id: [task_id, ...]} from the posted rows, which arrive as a
	list of {assignment_id, task_id} dicts, one such dict, or its JSON string.
	"""
	if isinstance(value, str):
		value = frappe.parse_json(value)
	if isinstance(value, dict):
		value = [value]

	task_map = {}
	for row in value or []:
		if not isinstance(row, dict):
			continue

		assignment_id = frappe.utils.strip(frappe.utils.cstr(row.get("assignment_id")))
		task_id = frappe.utils.strip(frappe.utils.cstr(row.get("task_id")))
		if not assignment_id or not task_id:
			continue

		task_ids = task_map.setdefault(assignment_id, [])
		if task_id not in task_ids:
			task_ids.append(task_id)

	return task_map
