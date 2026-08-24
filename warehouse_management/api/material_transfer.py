import frappe
from frappe.utils import flt

from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["GET"])
def recent_transfers():
	"""Return the caller's last 5 submitted Material Transfer stock
	entries. No input required; scoped to the Authorization header user.
	"""
	try:
		material_transfers = frappe.db.sql(
			"""
			SELECT
				parent_doc.name AS material_transfer_id,
				COUNT(item_row.name) AS total_items,
				SUM(item_row.qty) AS total_items_transferred
			FROM `tabStock Entry` parent_doc
			INNER JOIN `tabStock Entry Detail` item_row ON item_row.parent = parent_doc.name
			WHERE 
				parent_doc.purpose = 'Material Transfer'
			  	AND parent_doc.docstatus = 1
			  	AND parent_doc.owner = %(owner)s
			GROUP BY parent_doc.name
			ORDER BY parent_doc.posting_date DESC, parent_doc.posting_time DESC
			LIMIT 5
			""",
			{"owner": frappe.session.user},
			as_dict=True,
		)

		return success(data=material_transfers)
	except Exception as e:
		frappe.log_error(title="Material transfer list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def create_material_transfer(source_warehouse=None, target_warehouse=None, item_code=None, qty=None):
	"""Create and submit a Material Transfer Stock Entry for one item.

	Body: `{source_warehouse, target_warehouse, item_code, qty}`.
	"""
	try:
		validation_error = _validate_transfer(source_warehouse, target_warehouse, item_code, qty)
		if validation_error:
			return validation_error

		entry = frappe.new_doc("Stock Entry")
		entry.purpose = "Material Transfer"
		entry.stock_entry_type = "Material Transfer"
		entry.append(
			"items",
			{
				"item_code": item_code,
				"s_warehouse": source_warehouse,
				"t_warehouse": target_warehouse,
				"qty": flt(qty),
			},
		)
		entry.flags.ignore_permissions = True
		entry.insert(ignore_permissions=True)
		entry.submit()
		frappe.db.commit()

		return success(
			data={"material_transfer_id": entry.name, "message": "Material transfer created."},
			http_status=201,
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Material transfer creation failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _validate_transfer(source_warehouse, target_warehouse, item_code, qty):
	"""Return an error, or None when the input is valid."""
	if not source_warehouse or not frappe.db.exists("Warehouse", source_warehouse):
		return error("Please provide a valid source warehouse.", 400)

	if not target_warehouse or not frappe.db.exists("Warehouse", target_warehouse):
		return error("Please provide a valid target warehouse.", 400)

	if source_warehouse == target_warehouse:
		return error("Source and target warehouse cannot be the same.", 400)

	if not item_code or not frappe.db.exists("Item", item_code):
		return error("Please provide a valid item_code.", 400)

	if flt(qty) <= 0:
		return error("Qty must be greater than zero.", 400)

	valuation_rate = frappe.db.get_value(
		"Bin", {"item_code": item_code, "warehouse": source_warehouse}, "valuation_rate"
	)
	if not valuation_rate or flt(valuation_rate) <= 0:
		return error(
			f"Item '{item_code}' has no valuation rate at warehouse '{source_warehouse}'; cannot transfer.",
			400,
		)

	return None
