"""Seed the one-off initial count: one Warehouse Daily Assignment per leaf
warehouse holding stock, flagged is_initial_reconciliation and assigned to
nobody. These rows are what the warehouse_items API serves and set_variation
writes back to, so the shelf is snapshotted once here rather than re-read
against live stock on every call.
"""

import frappe
from frappe.utils import flt


def execute():
	for warehouse, tasks in _stock_by_warehouse().items():
		frappe.get_doc(
			{
				"doctype": "Warehouse Daily Assignment",
				"warehouse": warehouse,
				"assignment_date": frappe.utils.today(),
				"is_initial_reconciliation": 1,
				"total_tasks": len(tasks),
				"tasks": tasks,
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()


def _stock_by_warehouse():
	"""{warehouse: [{item_code, qty}, ...]} for every leaf warehouse holding
	stock. Read from Bin so non batch-tracked items are covered too; zero
	balances are left out as there is nothing to count, and warehouses already
	seeded are skipped so a re-run adds nothing twice.
	"""
	seeded = frappe.get_all(
		"Warehouse Daily Assignment",
		filters={"is_initial_reconciliation": 1},
		pluck="warehouse",
		distinct=True,
	)

	rows = frappe.db.sql(
		"""
		SELECT bin.warehouse, bin.item_code, bin.actual_qty
		FROM `tabBin` bin
		INNER JOIN `tabWarehouse` warehouse ON warehouse.name = bin.warehouse
		WHERE bin.actual_qty != 0 AND warehouse.disabled = 0 AND warehouse.is_group = 0
		ORDER BY bin.warehouse, bin.item_code
		""",
		as_dict=True,
	)

	tasks_by_warehouse = {}
	for row in rows:
		if row.warehouse in seeded:
			continue

		tasks_by_warehouse.setdefault(row.warehouse, []).append(
			{"item_code": row.item_code, "qty": flt(row.actual_qty), "is_completed": 0}
		)
	return tasks_by_warehouse
