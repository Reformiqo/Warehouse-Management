import frappe
from frappe import _
from frappe.utils import flt, get_link_to_form

from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["POST"])
def create_stock_reconciliation(items=None):
	"""Create one draft Stock Reconciliation per warehouse and link it back to
	the caller's daily assignment for that warehouse.

	Body: `{items}` — a list of `{warehouse, item_code, qty}`, since an item
	carries the warehouse it was counted in. Every task on the assignment must
	be counted first, and a warehouse whose count matches system stock is only
	flagged no_variation rather than sent to a reconciliation ERPNext would
	reject as empty.
	"""
	try:
		items = frappe.parse_json(items) if isinstance(items, str) else items

		validation_error = _validate_items(items)
		if validation_error:
			return validation_error

		employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
		if not employee:
			return error("No Employee is linked to your user account.", 404)

		warehouse_items = {}
		for row in items:
			warehouse_items.setdefault(row["warehouse"], {})[row["item_code"]] = flt(row.get("qty"))

		assignments = _get_assignments(employee, list(warehouse_items))
		completion_error = _validate_tasks_completed(assignments)
		if completion_error:
			return completion_error

		created, no_variance = [], []
		for warehouse, item_qty_map in warehouse_items.items():
			varied = _items_with_variation(warehouse, item_qty_map)
			if varied:
				name = _create_for_warehouse(warehouse, varied)
				created.append(name)
				values = {"stock_reconciliation": name}
			else:
				# counted at exactly system stock, so ERPNext has nothing to post and
				# the flag is what marks the warehouse done in place of a document
				no_variance.append(warehouse)
				values = {"no_variation": 1}

			if assignments.get(warehouse):
				frappe.db.set_value("Warehouse Daily Assignment", assignments[warehouse], values)

		frappe.db.commit()

		return success(
			data={"stock_reconciliation_ids": created, "no_variance": no_variance},
			http_status=201 if created else 200,
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Stock reconciliation creation failed", message=frappe.get_traceback())
		return error(str(e), 500)


def restrict_pending_reconciliation_warehouse(doc, method=None):
	"""hooks.py doc_events target for Stock Ledger Entry before_insert: refuse
	stock moving through a warehouse whose Stock Reconciliation is still in
	draft. Every stock document writes ledger entries, so this one hook covers
	Sales Invoice, Purchase Invoice, Delivery Note, Stock Entry and the rest.

	Reconciliations themselves are exempt — the blocking document has to be
	able to submit and clear the restriction.
	"""
	if doc.voucher_type == "Stock Reconciliation" or not doc.warehouse:
		return

	pending = _pending_reconciliation(doc.warehouse)
	if pending:
		frappe.throw(
			_("{0} is restricted because the reconciliation is not done yet. Submit {1} first.").format(
				frappe.bold(doc.warehouse),
				get_link_to_form("Stock Reconciliation", pending),
			),
			title=_("Warehouse Restricted"),
		)


def _pending_reconciliation(warehouse):
	"""Name of the draft Stock Reconciliation holding this warehouse, or None."""
	rows = frappe.db.sql(
		"""
		SELECT reconciliation.name
		FROM `tabWarehouse Daily Assignment` assignment
		INNER JOIN `tabStock Reconciliation` reconciliation
		        ON reconciliation.name = assignment.stock_reconciliation
		WHERE assignment.warehouse = %(warehouse)s AND reconciliation.docstatus = 0
		LIMIT 1
		""",
		{"warehouse": warehouse},
	)
	return rows[0][0] if rows else None


def _validate_items(items):
	"""Return an error, or None when the input is valid."""
	if not items or not isinstance(items, list):
		return error("Please provide items as [{warehouse, item_code, qty}].", 400)

	for row in items:
		warehouse, item_code = row.get("warehouse"), row.get("item_code")
		if not warehouse or not item_code:
			return error("Every item row needs a warehouse and an item_code.", 400)

		if not frappe.db.exists("Warehouse", warehouse):
			return error(f"Warehouse '{warehouse}' not found.", 404)

		if not frappe.db.exists("Item", item_code):
			return error(f"Item '{item_code}' not found.", 404)

		if flt(row.get("qty")) < 0:
			return error(f"Qty for item '{item_code}' cannot be negative.", 400)

	return None


def _get_assignments(employee, warehouses):
	"""{warehouse: assignment name} for today's assignments held by this
	employee, so the caller can only reconcile warehouses that are their own.
	"""
	rows = frappe.get_all(
		"Warehouse Daily Assignment",
		filters={
			"employee": employee,
			"assignment_date": frappe.utils.today(),
			"warehouse": ["in", warehouses],
		},
		fields=["name", "warehouse"],
	)
	return {row.warehouse: row.name for row in rows}


def _validate_tasks_completed(assignments):
	"""Return an error while any task on the caller's assignments is still
	uncounted. Nothing is created until this passes.
	"""
	if not assignments:
		return None

	pending = frappe.get_all(
		"Warehouse Daily Assignment Task",
		filters={"parent": ["in", list(assignments.values())], "is_completed": 0},
		fields=["parent", "item_code"],
	)
	if pending:
		warehouse_by_assignment = {name: warehouse for warehouse, name in assignments.items()}
		details = ", ".join(
			sorted({f"{row.item_code} ({warehouse_by_assignment[row.parent]})" for row in pending})
		)
		return error(f"Please complete the daily reconciliation for: {details}.", 400)

	return None


def _items_with_variation(warehouse, item_qty_map):
	"""Drop items counted at exactly what the system holds — ERPNext strips
	unchanged rows and refuses a reconciliation left with none.
	"""
	bins = frappe.get_all(
		"Bin",
		filters={"warehouse": warehouse, "item_code": ["in", list(item_qty_map)]},
		fields=["item_code", "actual_qty"],
	)
	system_qty = {row.item_code: flt(row.actual_qty, 6) for row in bins}

	return {
		item_code: qty
		for item_code, qty in item_qty_map.items()
		if flt(qty, 6) != system_qty.get(item_code, 0.0)
	}


def _create_for_warehouse(warehouse, item_qty_map):
	"""Insert one draft Stock Reconciliation and return its name."""
	reconciliation = frappe.new_doc("Stock Reconciliation")
	reconciliation.purpose = "Stock Reconciliation"
	reconciliation.company = frappe.db.get_single_value("Global Defaults", "default_company")
	reconciliation.set_warehouse = warehouse
	for item_code, qty in item_qty_map.items():
		reconciliation.append("items", {"item_code": item_code, "warehouse": warehouse, "qty": qty})

	reconciliation.flags.ignore_permissions = True
	reconciliation.insert(ignore_permissions=True)

	return reconciliation.name
