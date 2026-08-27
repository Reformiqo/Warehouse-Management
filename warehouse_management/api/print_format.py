import frappe

from warehouse_management.utils.response import error, success

PICK_LIST_DOCTYPE = "Pick List"
PRINT_DOCTYPE = "Sales Order"


@frappe.whitelist(methods=["GET"])
def pick_list_html(docname=None):
	"""Render a Pick List through its default print format as HTML, for the
	Android app to show in a WebView.

	Query param: `docname` (required).
	"""
	try:
		docname = frappe.utils.strip(frappe.utils.cstr(docname))
		if not docname:
			return error("Please provide a docname.", 400)

		if not frappe.db.exists(PICK_LIST_DOCTYPE, docname):
			return error(f"Pick List '{docname}' not found.", 404)

		if not frappe.has_permission(PICK_LIST_DOCTYPE, "read", doc=docname):
			return error(f"Not permitted to print Pick List '{docname}'.", 403)

		html = frappe.get_print(PICK_LIST_DOCTYPE, docname)

		return success(data={"html": html})
	except Exception as e:
		frappe.log_error(title="Pick List print HTML failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def sales_order_pdf(sales_order_id=None, print_format=None):
	"""Stream a Sales Order as a PDF built from a print format.

	Query params: `sales_order_id` (required), `print_format` (optional,
	falls back to the site default for Sales Order). frappe.get_print
	renders the format's Jinja template with the doc in context, so
	letterhead and print styling come through as they do in Desk.
	"""
	try:
		sales_order_id = frappe.utils.strip(frappe.utils.cstr(sales_order_id))
		if not sales_order_id:
			return error("Please provide a sales_order_id.", 400)

		if not frappe.db.exists(PRINT_DOCTYPE, sales_order_id):
			return error(f"Sales Order '{sales_order_id}' not found.", 404)

		print_format = frappe.utils.strip(frappe.utils.cstr(print_format)) or None
		if print_format and not frappe.db.exists("Print Format", print_format):
			return error(f"Print Format '{print_format}' not found.", 404)

		pdf = frappe.get_print(PRINT_DOCTYPE, sales_order_id, print_format=print_format, as_pdf=True)

		frappe.local.response.filename = f"{sales_order_id}.pdf"
		frappe.local.response.filecontent = pdf
		frappe.local.response.type = "pdf"
	except Exception as e:
		frappe.log_error(title="Sales Order PDF failed", message=frappe.get_traceback())
		return error(str(e), 500)
