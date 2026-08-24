import frappe

from warehouse_management.utils.response import error, success

DRIVER_ROLE = "Driver"


@frappe.whitelist(methods=["GET"])
def delivery_trip():
	"""Return Delivery Trips for the caller: drafts if they have the
	Driver role, submitted ones otherwise. No input required.
	"""
	try:
		docstatus = 0 if DRIVER_ROLE in frappe.get_roles() else 1
		delivery_trips = frappe.get_all(
			"Delivery Trip",
			filters={"docstatus": docstatus},
			fields=["name", "driver", "driver_name", "vehicle", "departure_time", "status"],
			order_by="departure_time desc",
		)
		return success(data=delivery_trips)
	except Exception as e:
		frappe.log_error(title="Delivery trip lookup failed", message=frappe.get_traceback())
		return error(str(e), 500)
