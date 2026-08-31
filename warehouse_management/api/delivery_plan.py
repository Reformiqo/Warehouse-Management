import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe.utils import cint, flt, nowdate

from warehouse_management.utils import strip_link_marker
from warehouse_management.utils.response import error, success

DEFAULT_LIMIT = 20
# a Closed Sales Order is done with regardless of what is still undelivered
CLOSED_SO_STATUS = "Closed"
PLAN_DOCTYPE = "Hns Delivery Plan"
# columns a caller may set on a plan item; anything else in the payload is ignored
PLAN_ITEM_FIELDS = (
	"item_code",
	"item_name",
	"qty",
	"delivery_date",
	"lot_no",
	"sales_order_item",
	"remark",
	"is_reschedule",
)


@frappe.whitelist(methods=["GET"])
def customer_list(search=None, limit=None, offset=None):
	"""Return enabled Customers. Query params, all optional: `search` (matches
	the customer name), `limit` (default 20) and `offset` (rows to skip).
	"""
	try:
		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		filters = {"disabled": 0}
		if search:
			filters["customer_name"] = ["like", f"%{search}%"]

		customers = frappe.get_all(
			"Customer",
			filters=filters,
			fields=["name as customer_id", "customer_name"],
			order_by="customer_name",
			limit_start=offset,
			limit_page_length=limit,
		)
		return success(data=customers)
	except Exception as e:
		frappe.log_error(title="Customer list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def address_list(customer=None, search=None, limit=None, offset=None):
	"""Return enabled Addresses. Query params, all optional: `customer` (only
	addresses linked to that customer), `search` (matches the address title),
	`limit` (default 20) and `offset` (rows to skip).
	"""
	try:
		customer = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(customer)))
		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		filters = [["Address", "disabled", "=", 0]]
		if customer:
			# a party is tied to its addresses through the Dynamic Link child table
			filters.append(["Dynamic Link", "link_doctype", "=", "Customer"])
			filters.append(["Dynamic Link", "link_name", "=", customer])

		if search:
			filters.append(["Address", "address_title", "like", f"%{search}%"])

		addresses = frappe.get_all(
			"Address",
			filters=filters,
			fields=["name as address_id", "address_title"],
			order_by="address_title",
			limit_start=offset,
			limit_page_length=limit,
			distinct=True,
		)
		return success(data=addresses)
	except Exception as e:
		frappe.log_error(title="Address list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def sales_order_list(customer=None, search=None, limit=None, offset=None):
	"""Return the ids of submitted Sales Orders that are not Closed and still
	have something to deliver (per_delivered < 100).

	Query params, all optional: `customer` (the customer_id from customer_list),
	`search` (matches the order id), `limit` (default 20) and `offset`.
	"""
	try:
		customer = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(customer)))
		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		filters = {
			"docstatus": 1,
			"status": ["!=", CLOSED_SO_STATUS],
			"per_delivered": ["<", 100],
		}
		if customer:
			filters["customer"] = customer
		if search:
			filters["name"] = ["like", f"%{search}%"]

		orders = frappe.get_all(
			"Sales Order",
			filters=filters,
			order_by="transaction_date desc",
			limit_start=offset,
			limit_page_length=limit,
			pluck="name",
		)
		return success(data=orders)
	except Exception as e:
		frappe.log_error(title="Sales order list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["GET"])
def sales_order_item_list(sales_order=None, search=None, limit=None, offset=None):
	"""Return the row id, item code and name of each row on one Sales Order, in
	the order's own row order. sales_order_item is what create_delivery_plan
	takes on its item rows.

	Query params: `sales_order` is required; `search` (matches the item name),
	`limit` (default 20) and `offset` (rows to skip) are optional.
	"""
	try:
		sales_order = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(sales_order)))
		search = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(search)))
		limit = cint(limit) or DEFAULT_LIMIT
		offset = cint(offset)

		if not sales_order:
			return error("Please provide a sales_order.", 400)

		if not frappe.db.exists("Sales Order", sales_order):
			return error(f"Sales Order '{sales_order}' not found.", 404)

		filters = {"parent": sales_order}
		if search:
			filters["item_name"] = ["like", f"%{search}%"]

		items = frappe.get_all(
			"Sales Order Item",
			filters=filters,
			fields=["name as sales_order_item", "item_code", "item_name"],
			order_by="idx",
			limit_start=offset,
			limit_page_length=limit,
			parent_doctype="Sales Order",
		)
		return success(data=items)
	except Exception as e:
		frappe.log_error(title="Sales order item list failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def create_delivery_plan(
	customer=None,
	items=None,
	sales_order=None,
	transaction_date=None,
	delivery_date=None,
	company=None,
	ship_to=None,
	shipping_address=None,
):
	"""Create one Hns Delivery Plan as a draft.

	Body: `{customer, items, ...}` — `items` is a list of
	`{item_code, qty, delivery_date, lot_no, sales_order_item, remark}`.
	`transaction_date` is when the plan is raised (defaults to today) and
	`delivery_date` is when it ships - an item row without its own delivery_date
	inherits the plan's. custom_hns_fiscal, the company abbr and total_qty are
	filled in here; customer_name comes from the doctype's fetch_from.
	"""
	try:
		customer = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(customer)))
		sales_order = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(sales_order)))
		ship_to = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(ship_to)))
		transaction_date = frappe.utils.strip(frappe.utils.cstr(transaction_date)) or nowdate()
		company = strip_link_marker(frappe.utils.strip_html(frappe.utils.cstr(company))) or _default_company()

		rows = frappe.parse_json(items) if isinstance(items, str) else items
		plan = frappe.get_doc(
			{
				"doctype": PLAN_DOCTYPE,
				"customer": customer,
				"company": company,
				"transaction_date": transaction_date,
				"delivery_date": delivery_date or None,
				"sales_order": sales_order or None,
				"ship_to": ship_to or None,
				"shipping_address": shipping_address or None,
				"custom_hns_fiscal": _hns_fiscal(transaction_date),
				"custom_hns_company_abbr": frappe.db.get_value("Company", company, "abbr"),
				"items": [_plan_item(row, delivery_date) for row in rows],
			}
		)
		plan.total_qty = sum(flt(row.qty) for row in plan.items)
		plan.flags.ignore_permissions = True
		plan.insert(ignore_permissions=True)
		frappe.db.commit()

		return success(
			data={"delivery_plan_id": plan.name, "message": "Delivery plan created."},
			http_status=201,
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Delivery plan creation failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _plan_item(row, plan_delivery_date=None):
	"""One child row, keeping only the columns a caller is allowed to set. A row
	without its own delivery_date falls back to the plan's, which is how the
	existing plans are stored.

	uom and sales_order_item are filled in rather than left empty - the plan
	doctype keys its rows on both and cannot concatenate a None.
	"""
	item = {field: row[field] for field in PLAN_ITEM_FIELDS if row.get(field) not in (None, "")}
	if not item.get("delivery_date") and plan_delivery_date:
		item["delivery_date"] = plan_delivery_date

	item["sales_order_item"] = frappe.utils.cstr(item.get("sales_order_item"))
	item["uom"] = _row_uom(item)
	return item


def _row_uom(item):
	"""uom is not posted any more, so it comes off the linked Sales Order row,
	falling back to the item's own stock uom.
	"""
	if item.get("sales_order_item"):
		uom = frappe.db.get_value("Sales Order Item", item["sales_order_item"], "uom")
		if uom:
			return uom

	return frappe.utils.cstr(frappe.db.get_value("Item", item.get("item_code"), "stock_uom"))


def _hns_fiscal(date):
	"""The plan's fiscal tag, e.g. 2627 for 2026-04-01..2027-03-31. It feeds the
	naming series, and is not the Fiscal Year name - that one reads 2026-2027.
	"""
	fiscal_year = get_fiscal_year(date, as_dict=True)
	return f"{fiscal_year.year_start_date:%y}{fiscal_year.year_end_date:%y}"


def _default_company():
	"""The caller's default company, falling back to the system default."""
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
