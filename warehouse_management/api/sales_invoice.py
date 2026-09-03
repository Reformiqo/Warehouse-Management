import frappe
from frappe.utils import cint, cstr

from warehouse_management.utils import strip_link_marker
from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20
# what is still owed on: not due yet, past due, or paid in part
OUTSTANDING_STATUSES = ("Unpaid", "Overdue", "Partly Paid")


@frappe.whitelist(methods=["GET"])
def outstanding_invoices(customer=None, limit=None, offset=None):
	"""Return the Sales Invoices a customer still owes on — the ones left
	Unpaid, Overdue or Partly Paid — newest first.

	Query params: `customer` is required; `limit` (default 20) and `offset`
	(rows to skip) are optional.
	"""
	try:
		customer = strip_link_marker(frappe.utils.strip_html(cstr(customer)))
		if not customer:
			return error("Please provide a customer.", 400)

		invoices = frappe.get_all(
			"Sales Invoice",
			filters={
				"customer": customer,
				"docstatus": 1,
				"status": ["in", OUTSTANDING_STATUSES],
			},
			fields=[
				"name AS sales_invoice_id",
				"posting_date AS sales_invoice_date",
				"status",
				"outstanding_amount",
			],
			order_by="posting_date desc, name desc",
			limit_page_length=cint(limit) or DEFAULT_LIMIT,
			limit_start=cint(offset),
		)
		return success(data=invoices)
	except Exception as e:
		frappe.log_error(title="Outstanding invoices failed", message=frappe.get_traceback())
		return error(str(e), 500)
