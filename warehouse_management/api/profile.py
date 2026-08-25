"""Profile — the logged-in user's details plus warehouse-wide stats.

No input: the user is taken from the Authorization header, same as logout.
total_items/total_warehouse only change on Item/Warehouse creation, so
they're cached and cleared via hooks.py. open_po/open_so and
initial_reconciliation change on document submits with no single reliable
hook to chase (see status_updater.py / mark_warehouse_reconciled), so
they're queried live on each call instead.
"""

import frappe
from frappe.utils import flt

from warehouse_management.utils.response import error, success

STATS_CACHE_KEY = "warehouse_management:profile_stats"

TEAM_STATUS_ROLE = "System Manager"

OPEN_PO_STATUSES = ["To Receive and Bill", "To Receive"]
OPEN_SO_STATUSES = ["To Deliver and Bill", "To Deliver"]


@frappe.whitelist(methods=["GET"])
def profile():
	"""Return the logged-in user's profile plus warehouse stats.
	No input required.
	"""
	try:
		user = frappe.session.user
		full_name = frappe.db.get_value("User", user, "full_name")

		return success(
			data={
				"full_name": full_name,
				"email": user,
				**get_cached_stats(),
				"open_po": frappe.db.count("Purchase Order", {"status": ["in", OPEN_PO_STATUSES]}),
				"open_so": frappe.db.count("Sales Order", {"status": ["in", OPEN_SO_STATUSES]}),
				"initial_reconciliation": _all_leaf_warehouses_reconciled(),
				"daily_reconciliation": _daily_reconciliation_status(user),
			}
		)
	except Exception as e:
		frappe.log_error(title="Warehouse profile failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def team_status():
	"""Return today's warehouse assignments: who is on which warehouse,
	how many tasks they have, and whether they are done. No input
	required. Not being allowed to see the team is a normal state, not
	an error, so it comes back as permitted=False rather than a 403.
	"""
	try:
		if TEAM_STATUS_ROLE not in frappe.get_roles():
			return success(data=[], permitted=False)

		rows = frappe.db.sql(
			"""
			SELECT
				assignment.employee AS emp_id,
				employee.employee_name AS emp_name,
				employee.designation,
				assignment.total_tasks,
				COUNT(DISTINCT CASE WHEN task.is_completed = 1 THEN task.item_code END)
					AS completed_tasks
			FROM `tabWarehouse Daily Assignment` assignment
			INNER JOIN `tabEmployee` employee ON employee.name = assignment.employee
			LEFT JOIN `tabWarehouse Daily Assignment Task` task ON task.parent = assignment.name
			WHERE assignment.assignment_date = %(today)s
			GROUP BY assignment.name
			ORDER BY employee.employee_name
			""",
			{"today": frappe.utils.today()},
			as_dict=True,
		)
		return success(data=rows, permitted=True)
	except Exception as e:
		frappe.log_error(title="Team status lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)


def get_cached_stats():
	"""Item/warehouse counts, cached for 5 minutes and cleared by
	hooks.py the moment an Item or Warehouse is created.
	"""
	cached = frappe.cache.get_value(STATS_CACHE_KEY)
	if cached:
		return cached

	stats = {
		"total_items": frappe.db.count("Item"),
		"total_warehouse": frappe.db.count("Warehouse"),
	}
	frappe.cache.set_value(STATS_CACHE_KEY, stats)
	return stats


def invalidate_stats_cache(doc=None, method=None):
	"""hooks.py doc_events target for Item/Warehouse after_insert."""
	frappe.cache.delete_value(STATS_CACHE_KEY)


def mark_warehouse_reconciled(doc, method=None):
	"""hooks.py doc_events target for Stock Reconciliation on_submit.
	initial_reconciliation is set once per warehouse, so the filter
	skips already-flagged ones instead of rewriting them every submit.
	"""
	warehouses = list({row.warehouse for row in doc.items if row.warehouse})
	if not warehouses:
		return

	frappe.db.set_value(
		"Warehouse",
		{"name": ["in", warehouses], "initial_reconciliation": 0},
		"initial_reconciliation",
		1,
	)


def _daily_reconciliation_status(user):
	"""Today's Warehouse Daily Assignment progress for this user's
	employee: task counts, percentage done, and a rollup status. None
	when nothing is assigned, so the client can tell that apart from an
	assignment that just has no progress yet.
	"""
	employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if not employee:
		return None

	rows = frappe.db.sql(
		"""
		SELECT
			assignment.total_tasks,
			COUNT(DISTINCT CASE WHEN task.is_completed = 1 THEN task.item_code END)
				AS completed_tasks
		FROM `tabWarehouse Daily Assignment` assignment
		LEFT JOIN `tabWarehouse Daily Assignment Task` task ON task.parent = assignment.name
		WHERE assignment.employee = %(employee)s
		  AND assignment.assignment_date = %(today)s
		GROUP BY assignment.name
		""",
		{"employee": employee, "today": frappe.utils.today()},
		as_dict=True,
	)
	if not rows:
		return None

	total_tasks = rows[0].total_tasks or 0
	completed_tasks = rows[0].completed_tasks or 0

	if not completed_tasks:
		status = "Not Started"
	elif completed_tasks < total_tasks:
		status = "In Progress"
	else:
		status = "Completed"

	return {
		"status": status,
		"percentage": flt(completed_tasks / total_tasks * 100, 2) if total_tasks else 0.0,
		"total_tasks": total_tasks,
		"completed_tasks": completed_tasks,
	}


def _all_leaf_warehouses_reconciled():
	"""True unless at least one leaf warehouse still has
	initial_reconciliation = 0. Queried live, not cached, since it
	changes via mark_warehouse_reconciled on Stock Reconciliation submit.
	"""
	return not frappe.db.exists("Warehouse", {"is_group": 0, "initial_reconciliation": 0})
