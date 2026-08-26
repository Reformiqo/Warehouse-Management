import frappe
from frappe.utils import cint, flt

from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20


@frappe.whitelist(methods=["GET"])
def my_assignment(search=None, limit=None, offset=None):
	"""Return today's Warehouse Daily Assignment for the caller's Employee:
	the warehouse, progress rollup, and one task per item to reconcile.

	Query params, all optional: `search` (matches item code or name),
	`limit` (default 20) and `offset` (rows to skip, default 0). The
	progress counts describe the whole assignment, not the searched page.
	"""
	try:
		search = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
		if not employee:
			return error("No Employee is linked to your user account.", 404)

		assignment = frappe.db.get_value(
			"Warehouse Daily Assignment",
			{"employee": employee, "assignment_date": frappe.utils.today()},
			["name", "warehouse", "assignment_date", "total_tasks"],
			as_dict=True,
		)
		if not assignment:
			return success(data={})

		tasks = _get_tasks(assignment.name)
		# rows are per reference, so progress counts distinct items to stay
		# in step with total_tasks and with profile()
		total_tasks = assignment.total_tasks or len({task["item_code"] for task in tasks})
		completed_tasks = len({task["item_code"] for task in tasks if task["is_completed"]})

		if search:
			needle = search.lower()
			tasks = [
				task
				for task in tasks
				if needle in task["item_code"].lower() or needle in (task["item_name"] or "").lower()
			]

		return success(
			data={
				"assignment_id": assignment.name,
				"warehouse": assignment.warehouse,
				"total_tasks": total_tasks,
				"completed_tasks": completed_tasks,
				"tasks": tasks[offset : offset + limit],
			}
		)
	except Exception as e:
		frappe.log_error(title="My assignment lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def set_variation(assignment_id=None, task_id=None, variation=None):
	"""Set the variation on one task row of the caller's own assignment.

	Body: `{assignment_id, task_id, variation}`. The assignment is
	matched against the caller's Employee, so one user cannot write to
	another's tasks.
	"""
	try:
		assignment_id = frappe.utils.strip(frappe.utils.cstr(assignment_id))
		task_id = frappe.utils.strip(frappe.utils.cstr(task_id))
		if not assignment_id or not task_id:
			return error("Please provide assignment_id and task_id.", 400)

		if variation in (None, ""):
			return error("Please provide a variation.", 400)

		employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
		if not employee:
			return error("No Employee is linked to your user account.", 404)

		owns_assignment = frappe.db.exists(
			"Warehouse Daily Assignment", {"name": assignment_id, "employee": employee}
		)
		if not owns_assignment:
			return error(f"Assignment '{assignment_id}' is not yours.", 403)

		if not frappe.db.exists(
			"Warehouse Daily Assignment Task", {"name": task_id, "parent": assignment_id}
		):
			return error(f"Task '{task_id}' is not on assignment '{assignment_id}'.", 404)

		frappe.db.set_value("Warehouse Daily Assignment Task", task_id, "variation", flt(variation))
		frappe.db.commit()

		return success(data={"task_id": task_id, "variation": flt(variation)})
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Set variation failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _get_tasks(assignment_name):
	"""One row per task reference — the voucher that put the item on the
	list — with the item's fields carried flat on each row rather than
	nested, so an item on two vouchers comes back as two rows.
	"""
	return frappe.db.sql(
		"""
		SELECT task.name AS task_id, task.item_code, item.item_name,
		       task.qty, task.variation, task.is_completed,
		       task.reference_doctype, task.reference_name
		FROM `tabWarehouse Daily Assignment Task` task
		LEFT JOIN `tabItem` item ON item.name = task.item_code
		WHERE task.parent = %(assignment)s
		ORDER BY task.idx
		""",
		{"assignment": assignment_name},
		as_dict=True,
	)


def _rollup_status(total_tasks, completed_tasks):
	"""The same three states profile() reports, so both stay in step."""
	if not completed_tasks:
		return "Not Started"

	return "In Progress" if completed_tasks < total_tasks else "Completed"
