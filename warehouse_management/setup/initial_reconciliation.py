"""Seed the one-off initial count: one Warehouse Daily Assignment per leaf
warehouse holding stock, flagged is_initial_reconciliation and assigned to
nobody. A fresh install never runs patches, so after_install calls
seed_initial_reconciliation and the patches call in here for existing sites.
"""

import frappe
from frappe.utils import flt


def seed_initial_reconciliation():
	"""Install entry point, and a no-op once the site has been seeded — a
	reinstall must not re-snapshot shelves or put counted warehouses back to
	pending. On a first install nothing has been reconciled yet, so every leaf
	warehouse is reset: the custom field's default of 1 would otherwise read as
	already verified.
	"""
	if frappe.db.exists("Warehouse Daily Assignment", {"is_initial_reconciliation": 1}):
		return

	frappe.db.sql("UPDATE `tabWarehouse` SET initial_reconciliation = 0 WHERE is_group = 0")
	create_assignments()


def create_assignments():
	"""One unassigned assignment per warehouse still missing one. These rows are
	what the warehouse_items API serves and set_variation writes back to, so the
	shelf is snapshotted once here rather than re-read against live stock.
	"""
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
