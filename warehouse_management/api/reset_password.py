"""Reset password — verify the email exists, then set a new password
directly. No OTP/token step; whoever submits a known email plus a new
password can replace that account's password.
"""

import frappe
from frappe.utils.password import update_password as set_new_password

from warehouse_management.utils import generate_api_keys
from warehouse_management.utils.response import error, success


@frappe.whitelist(allow_guest=True, methods=["POST"])
def verify_email(email=None):
	"""Check whether an enabled account exists for this email.

	Body: `{email}`.
	"""
	try:
		email = _sanitize_email(email)
		validation_error = _validate_email(email)
		if validation_error:
			return validation_error
		return success(data={"email": email, "message": "Email verified."})
	except Exception as e:
		frappe.log_error(title="Warehouse verify-email failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def reset_password(email=None, new_password=None):
	"""Set a new password for the given email.

	Body: `{email, new_password}`. Returns a fresh api_key/api_secret.
	"""
	try:
		email = _sanitize_email(email)
		new_password = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(new_password)))

		validation_error = _validate_email(email)
		if validation_error:
			return validation_error
		if not new_password:
			return error("Please enter a new password.", 400)

		set_new_password(email, new_password, logout_all_sessions=True)

		api_key, api_secret = generate_api_keys(email)
		frappe.db.commit()

		return success(data={"user": email, "api_key": api_key, "api_secret": api_secret})
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Warehouse password reset failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _sanitize_email(email):
	return frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(email))).lower()


def _validate_email(email):
	"""Return an error, or None when an enabled account exists for this email."""
	if not frappe.utils.validate_email_address(email):
		return error("Please enter valid email.", 400)
	if not frappe.db.get_value("User", email, "enabled"):
		return error("No active account found for this email.", 404)
	return None
