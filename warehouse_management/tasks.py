import random

import frappe
from frappe.utils import flt


def assign_daily_warehouses():
	"""Nightly job (see hooks.py scheduler_events, cron 0 0 * * *): wipe
	yesterday's Warehouse Daily Assignment rows and hand every warehouse whose
	stock moved the previous day to an employee. Employees are shuffled and
	dealt round-robin, so fewer employees than warehouses just means each one
	carries more of them rather than warehouses going unassigned.
	"""
	frappe.db.delete("Warehouse Daily Assignment")

	tasks_by_warehouse = _get_previous_day_tasks_by_warehouse()
	warehouses = list(tasks_by_warehouse.keys())
	if not warehouses:
		frappe.db.commit()
		return

	employees = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name")
	if not employees:
		return

	random.shuffle(employees)

	for index, warehouse in enumerate(warehouses):
		tasks = tasks_by_warehouse[warehouse]
		frappe.get_doc(
			{
				"doctype": "Warehouse Daily Assignment",
				"warehouse": warehouse,
				"employee": employees[index % len(employees)],
				"total_tasks": len({task["item_code"] for task in tasks}),
				"assignment_date": frappe.utils.today(),
				"tasks": tasks,
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()


def _get_previous_day_tasks_by_warehouse():
	"""{warehouse: [{reference_doctype, reference_name, item_code, qty}, ...]}
	from the non-cancelled Stock Ledger Entries submitted the previous day,
	deduped per (warehouse, voucher_type, voucher_no, item_code). Anchored on
	creation, not posting_date, so a backdated voucher can't land in a bucket
	whose run already passed and end up assigned to nobody.
	"""
	yesterday = frappe.utils.add_days(frappe.utils.today(), -1)
	rows = frappe.get_all(
		"Stock Ledger Entry",
		filters={"creation": ["between", [yesterday, yesterday]], "is_cancelled": 0},
		fields=["warehouse", "voucher_type", "voucher_no", "item_code"],
	)

	seen = set()
	tasks_by_warehouse = {}
	for row in rows:
		key = (row.warehouse, row.voucher_type, row.voucher_no, row.item_code)
		if key in seen:
			continue

		seen.add(key)
		tasks_by_warehouse.setdefault(row.warehouse, []).append(
			{
				"reference_doctype": row.voucher_type,
				"reference_name": row.voucher_no,
				"item_code": row.item_code,
				"qty": 0,
			}
		)

	_set_system_qty(tasks_by_warehouse)
	return tasks_by_warehouse


def _set_system_qty(tasks_by_warehouse):
	"""Stamp each task with live Bin stock — the shelf the picker counts, not
	the day's movement. The ledger only says which items to count; an item
	with no Bin row in that warehouse stays at 0.
	"""
	warehouses = list(tasks_by_warehouse.keys())
	items = {task["item_code"] for tasks in tasks_by_warehouse.values() for task in tasks}
	if not items:
		return

	bins = frappe.get_all(
		"Bin",
		filters={"warehouse": ["in", warehouses], "item_code": ["in", list(items)]},
		fields=["warehouse", "item_code", "actual_qty"],
	)
	qty_map = {(row.warehouse, row.item_code): flt(row.actual_qty) for row in bins}

	for warehouse, tasks in tasks_by_warehouse.items():
		for task in tasks:
			task["qty"] = qty_map.get((warehouse, task["item_code"]), 0.0)
