import random

import frappe


def assign_daily_warehouses():
	"""Nightly job (see hooks.py scheduler_events, cron 0 0 * * *): wipe
	yesterday's Warehouse Daily Assignment rows and randomly assign one
	active Employee to each warehouse that had stock movement the previous
	day. zip() over a single shuffled employee list guarantees both
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
	from the previous day's non-cancelled Stock Ledger Entries, deduped
	per (warehouse, voucher_type, voucher_no, item_code). The job runs at
	midnight, so the movement to work through is the day that just ended.
	"""
	rows = frappe.get_all(
		"Stock Ledger Entry",
		filters={"posting_date": frappe.utils.add_days(frappe.utils.today(), -1), "is_cancelled": 0},
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
			}
		)
	return tasks_by_warehouse
