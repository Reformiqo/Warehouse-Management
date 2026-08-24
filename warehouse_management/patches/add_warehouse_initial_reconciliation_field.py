"""Reset every existing leaf warehouse's initial_reconciliation to 0 —
group warehouses hold no stock so reconciliation doesn't apply to them.
New leaf warehouses default to 1 (empty, nothing to reconcile) via the
field itself; hooks.py sets it back to 1 once a Stock Reconciliation is
submitted for a legacy warehouse.
"""

import frappe

from warehouse_management.setup.custom_fields import create_fields


def execute():
	create_fields()
	frappe.db.sql("UPDATE `tabWarehouse` SET initial_reconciliation = 0 WHERE is_group = 0")
