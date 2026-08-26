import random

import frappe
from frappe.utils import flt


def assign_daily_warehouses():
	"""Nightly job (see hooks.py scheduler_events, cron 0 0 * * *): wipe
	yesterday's Warehouse Daily Assignment rows and randomly assign one
	active Employee to each warehouse whose stock movement was entered the
	previous day. zip() over a single shuffled employee list guarantees both
	directions of uniqueness — one employee per warehouse, one warehouse
	per employee.
	"""
	frappe.db.delete("Warehouse Daily Assignment")

	tasks_by_warehouse = _get_previous_day_tasks_by_warehouse()
	warehouses = list(tasks_by_warehouse.keys())
	if not warehouses:
		frappe.db.commit()
		return

	employees = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name")
	random.shuffle(employees)

	for warehouse, employee in zip(warehouses, employees):
		tasks = tasks_by_warehouse[warehouse]
		frappe.get_doc(
			{
				"doctype": "Warehouse Daily Assignment",
				"warehouse": warehouse,
				"employee": employee,
				"total_tasks": len({task["item_code"] for task in tasks}),
				"assignment_date": frappe.utils.today(),
				"tasks": tasks,
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()


def _get_previous_day_tasks_by_warehouse():
	"""{warehouse: [{reference_doctype, reference_name, item_code}, ...]}
	from the non-cancelled Stock Ledger Entries submitted the previous day,
	deduped per (warehouse, voucher_type, voucher_no, item_code). Anchored on
	creation, not posting_date, so a backdated voucher can't land in a bucket
	whose run already passed and end up assigned to nobody.
	"""
	yesterday = frappe.utils.add_days(frappe.utils.today(), -1)
	rows = frappe.get_all(
		"Stock Ledger Entry",
		filters={"creation": ["between", [yesterday, yesterday]], "is_cancelled": 0},
		fields=["warehouse", "voucher_type", "voucher_no", "item_code", "actual_qty"],
	)

	tasks_by_key = {}
	tasks_by_warehouse = {}
	for row in rows:
		key = (row.warehouse, row.voucher_type, row.voucher_no, row.item_code)
		task = tasks_by_key.get(key)
		if not task:
			task = {
				"reference_doctype": row.voucher_type,
				"reference_name": row.voucher_no,
				"item_code": row.item_code,
				"qty": 0,
			}
			tasks_by_key[key] = task
			tasks_by_warehouse.setdefault(row.warehouse, []).append(task)

		task["qty"] += flt(row.actual_qty)

	return tasks_by_warehouse
