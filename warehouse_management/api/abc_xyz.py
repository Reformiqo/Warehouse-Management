import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe.query_builder.functions import Sum
from frappe.utils import add_months, cint, flt, get_first_day, getdate, today

from warehouse_management.utils import strip_link_marker
from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["GET"])
def get_abc_xyz(
	company=None,
	abc_a_threshold=0.80,
	abc_b_threshold=0.95,
	x_threshold=0.10,
	y_threshold=0.25,
	include_demand=0,
):
	"""Classify every item sold in the current fiscal year as ABC (share of
	sales value) by XYZ (month-to-month demand variability).

	Query params, all optional: `company` (defaults to the user's default
	Company), the four thresholds, and `include_demand=1` to also return each
	item's monthly demand series.
	"""
	try:
		company = strip_link_marker(company) or frappe.defaults.get_user_default("Company")
		if not company:
			return error("Please provide a company or set a default Company for the user.", 400)

		if not frappe.db.exists("Company", company):
			return error(f"Company '{company}' not found.", 404)

		thresholds = {
			"abc_a_threshold": flt(abc_a_threshold),
			"abc_b_threshold": flt(abc_b_threshold),
			"x_threshold": flt(x_threshold),
			"y_threshold": flt(y_threshold),
		}
		invalid = _validate_thresholds(thresholds)
		if invalid:
			return error(invalid, 400)

		fiscal_year = get_fiscal_year(today(), company=company, as_dict=True, raise_on_missing=False)
		if not fiscal_year:
			return error(f"No active Fiscal Year covers today for '{company}'.", 404)

		periods = _get_month_periods(fiscal_year.year_start_date, fiscal_year.year_end_date)
		items = _get_item_sales(company, fiscal_year, periods)

		_calculate_statistics(items)
		_calculate_abc(items, thresholds["abc_a_threshold"], thresholds["abc_b_threshold"])
		_calculate_xyz(items, thresholds["x_threshold"], thresholds["y_threshold"])

		return success(
			data={
				"company": company,
				"fiscal_year": fiscal_year.name,
				"from_date": fiscal_year.year_start_date,
				"to_date": fiscal_year.year_end_date,
				"periods": periods,
				"thresholds": thresholds,
				"summary": _build_summary(items),
				"items": _build_rows(items, cint(include_demand)),
			}
		)
	except Exception as e:
		frappe.log_error(title="ABC-XYZ classification failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _validate_thresholds(thresholds):
	"""Message for an unusable threshold set, None when they are fine."""
	if not 0 < thresholds["abc_a_threshold"] < thresholds["abc_b_threshold"] <= 1:
		return "Thresholds must satisfy 0 < abc_a_threshold < abc_b_threshold <= 1."

	if not 0 <= thresholds["x_threshold"] < thresholds["y_threshold"]:
		return "Thresholds must satisfy 0 <= x_threshold < y_threshold."

	return None


def _get_month_periods(from_date, to_date):
	"""["YYYY-MM", ...], one key per month the fiscal year spans."""
	periods = []
	cursor = get_first_day(getdate(from_date))
	last = get_first_day(getdate(to_date))

	while cursor <= last:
		periods.append(cursor.strftime("%Y-%m"))
		cursor = getdate(add_months(cursor, 1))

	return periods


def _get_item_sales(company, fiscal_year, periods):
	"""One row per item ordered in the fiscal year, holding its sales value
	and its demand quantity bucketed into `periods`. Warehouses are summed
	together, so an item stocked in several places stays a single row.
	"""
	so = frappe.qb.DocType("Sales Order")
	so_item = frappe.qb.DocType("Sales Order Item")

	rows = (
		frappe.qb.from_(so)
		.inner_join(so_item)
		.on(so_item.parent == so.name)
		.select(
			so_item.item_code,
			so_item.item_name,
			so.transaction_date,
			Sum(so_item.stock_qty).as_("qty"),
			Sum(so_item.base_amount).as_("amount"),
		)
		.where(
			(so.docstatus == 1)
			& (so.company == company)
			& (so.transaction_date >= fiscal_year.year_start_date)
			& (so.transaction_date <= fiscal_year.year_end_date)
		)
		.groupby(so_item.item_code, so_item.item_name, so.transaction_date)
	).run(as_dict=True)

	position_of = {period: index for index, period in enumerate(periods)}
	items = {}

	for row in rows:
		item = items.setdefault(
			row.item_code,
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"demand_values": [0.0] * len(periods),
				"consumption_value": 0.0,
			},
		)

		item["consumption_value"] += flt(row.amount)
		position = position_of.get(getdate(row.transaction_date).strftime("%Y-%m"))
		if position is not None:
			item["demand_values"][position] += flt(row.qty)

	return list(items.values())


def _calculate_statistics(items):
	"""Mean, population standard deviation and coefficient of variation of the
	monthly demand series. Months without an order count as a real zero.
	"""
	for item in items:
		demand_values = item["demand_values"]
		months = len(demand_values) or 1

		mean_demand = sum(demand_values) / months
		variance = sum((value - mean_demand) ** 2 for value in demand_values) / months

		item["mean_demand"] = mean_demand
		item["std_dev"] = variance**0.5
		item["cv"] = item["std_dev"] / mean_demand if mean_demand else None


def _calculate_abc(items, abc_a_threshold, abc_b_threshold):
	"""A/B/C on descending share of sales value. The band is read off the
	cumulative share the item opens at, so the item that carries the total
	past a threshold still belongs to the band it started in.
	"""
	total_consumption_value = sum(item["consumption_value"] for item in items)
	cumulative_value = 0.0

	for item in sorted(items, key=lambda item: item["consumption_value"], reverse=True):
		opening_share = cumulative_value / total_consumption_value if total_consumption_value else 0.0
		cumulative_value += item["consumption_value"]

		if total_consumption_value:
			item["consumption_percentage"] = item["consumption_value"] / total_consumption_value
			item["cumulative_percentage"] = cumulative_value / total_consumption_value
		else:
			item["consumption_percentage"] = 0.0
			item["cumulative_percentage"] = 0.0

		if opening_share < abc_a_threshold:
			item["abc"] = "A"
		elif opening_share < abc_b_threshold:
			item["abc"] = "B"
		else:
			item["abc"] = "C"


def _calculate_xyz(items, x_threshold, y_threshold):
	"""X/Y/Z on the coefficient of variation. An item with no demand quantity
	has no meaningful CV and counts as the most erratic.
	"""
	for item in items:
		cv = item["cv"]

		if cv is None:
			item["xyz"] = "Z"
		elif cv < x_threshold:
			item["xyz"] = "X"
		elif cv <= y_threshold:
			item["xyz"] = "Y"
		else:
			item["xyz"] = "Z"


def _build_summary(items):
	"""Item counts per ABC band, per XYZ band and per AX-CZ combination."""
	summary = {
		"total_items": len(items),
		"total_consumption_value": flt(sum(item["consumption_value"] for item in items), 2),
		"abc": dict.fromkeys("ABC", 0),
		"xyz": dict.fromkeys("XYZ", 0),
		"combinations": {abc + xyz: 0 for abc in "ABC" for xyz in "XYZ"},
	}

	for item in items:
		summary["abc"][item["abc"]] += 1
		summary["xyz"][item["xyz"]] += 1
		summary["combinations"][item["abc"] + item["xyz"]] += 1

	return summary


def _build_rows(items, include_demand):
	"""Rows by sales value, highest first. demand_values is one number per
	month per item, so it ships only when the caller asks for it.
	"""
	rows = []

	for item in sorted(items, key=lambda item: item["consumption_value"], reverse=True):
		row = {
			"item_code": item["item_code"],
			"item_name": item["item_name"],
			"consumption_value": flt(item["consumption_value"], 2),
			"consumption_percentage": flt(item["consumption_percentage"] * 100, 2),
			"cumulative_percentage": flt(item["cumulative_percentage"] * 100, 2),
			"mean_demand": flt(item["mean_demand"], 2),
			"std_dev": flt(item["std_dev"], 2),
			"cv": flt(item["cv"], 3) if item["cv"] is not None else None,
			"abc": item["abc"],
			"xyz": item["xyz"],
			"class": item["abc"] + item["xyz"],
		}

		if include_demand:
			row["demand_values"] = item["demand_values"]

		rows.append(row)

	return rows
