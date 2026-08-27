import frappe
from frappe.utils import cint, flt

from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20


@frappe.whitelist(methods=["GET"])
def my_assignment(search=None, limit=None, offset=None):
	"""Return today's Warehouse Daily Assignments for the caller's Employee:
	a progress rollup and one task per item to reconcile. An employee can
	hold one assignment per warehouse, so each task carries its own
	assignment and warehouse rather than those sitting on the payload.

	Query params, all optional: `search` (matches item code or name),
	`limit` (default 20) and `offset` (rows to skip, default 0). The
	progress counts describe every assignment, not the searched page.
	"""
	try:
		search = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
		if not employee:
			return error("No Employee is linked to your user account.", 404)

		assignments = frappe.get_all(
			"Warehouse Daily Assignment",
			filters={"employee": employee, "assignment_date": frappe.utils.today()},
			fields=["name", "warehouse", "assignment_date", "total_tasks"],
			order_by="warehouse",
		)
		if not assignments:
			return success(data={})

		tasks = _get_tasks([assignment.name for assignment in assignments])
		warehouses = {assignment.name: assignment.warehouse for assignment in assignments}
		for task in tasks:
			task["warehouse"] = warehouses.get(task["assignment_id"])

		total_tasks, completed_tasks = _progress(assignments, tasks)

		if search:
			needle = search.lower()
			tasks = [
				task
				for task in tasks
				if needle in task["item_code"].lower() or needle in (task["item_name"] or "").lower()
			]

		return success(
			data={
				"total_tasks": total_tasks,
				"completed_tasks": completed_tasks,
				"tasks": tasks[offset : offset + limit],
			}
		)
	except Exception as e:
		frappe.log_error(title="My assignment lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def set_variation(assignment_id=None, task_id=None, user_counted=None):
	"""Record what the picker counted on one task row of the caller's own
	assignment, and derive the variation against the system qty.

	Body: `{assignment_id, task_id, user_counted}`. The assignment is
	matched against the caller's Employee, so one user cannot write to
	another's tasks.
	"""
	try:
		assignment_id = frappe.utils.strip(frappe.utils.cstr(assignment_id))
		task_id = frappe.utils.strip(frappe.utils.cstr(task_id))
		if not assignment_id or not task_id:
			return error("Please provide assignment_id and task_id.", 400)

		if user_counted in (None, ""):
			return error("Please provide a user_counted.", 400)

		employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
		if not employee:
			return error("No Employee is linked to your user account.", 404)

		owns_assignment = frappe.db.exists(
			"Warehouse Daily Assignment", {"name": assignment_id, "employee": employee}
		)
		if not owns_assignment:
			return error(f"Assignment '{assignment_id}' is not yours.", 403)

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
				"task_id": task_id,
				"user_counted": user_counted,
				"system_qty": flt(task.qty),
				"variation": variation,
				"is_completed": 1,
			}
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Set variation failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _get_tasks(assignment_names):
	"""One row per task reference — the voucher that put the item on the
	list — with the item's fields carried flat on each row rather than
	nested, so an item on two vouchers comes back as two rows.
	"""
	if not assignment_names:
		return []

	return frappe.db.sql(
		"""
		SELECT task.parent AS assignment_id, task.name AS task_id,
		       task.item_code, item.item_name,
		       task.qty AS system_qty, task.user_counted, task.variation,
		       task.is_completed,
		       task.reference_doctype, task.reference_name
		FROM `tabWarehouse Daily Assignment Task` task
		LEFT JOIN `tabItem` item ON item.name = task.item_code
		WHERE task.parent IN %(assignments)s
		ORDER BY task.parent, task.idx
		""",
		{"assignments": tuple(assignment_names)},
		as_dict=True,
	)


def _variation_label(user_counted, system_qty):
	"""Signed difference as a display string, e.g. 'VAR +5', 'VAR -1', 'VAR 0'.
	Whole numbers drop the decimal so a count of 7 against 2 reads 'VAR +5'.
	"""
	difference = flt(flt(user_counted) - flt(system_qty), 2)
	if difference == int(difference):
		difference = int(difference)

	return f"VAR {'+' if difference > 0 else ''}{difference}"


def _progress(assignments, tasks):
	"""Rollup over every assignment. Rows are per reference, so items count
	once per assignment — the same item in two warehouses is two tasks.
	"""
	total_tasks = 0
	for assignment in assignments:
		items = {task["item_code"] for task in tasks if task["assignment_id"] == assignment.name}
		total_tasks += assignment.total_tasks or len(items)

	completed_tasks = len(
		{(task["assignment_id"], task["item_code"]) for task in tasks if task["is_completed"]}
	)
	return total_tasks, completed_tasks


def _rollup_status(total_tasks, completed_tasks):
	"""The same three states profile() reports, so both stay in step."""
	if not completed_tasks:
		return "Not Started"

	return "In Progress" if completed_tasks < total_tasks else "Completed"
