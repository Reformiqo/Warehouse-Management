import frappe


def success(data=None, http_status=200, **kwargs):
	frappe.local.response["http_status_code"] = http_status
	payload = {"success": True, "data": data if data is not None else {}}
	payload.update(kwargs)
	return payload


def error(message, http_status=400, **kwargs):
	frappe.local.response["http_status_code"] = http_status
	payload = {"success": False, "error": {"message": message}}
	payload.update(kwargs)
	return payload
