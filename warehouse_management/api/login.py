"""Login — verifies email/password and returns the User's API key/secret.

Uses Frappe's own check_password() to verify credentials without opening a
Desk session; the returned key/secret are what later API calls authenticate
with (as `Authorization: token <api_key>:<api_secret>`).
"""

from hmac import compare_digest

import frappe
from frappe.utils.password import check_password, get_decrypted_password

from warehouse_management.utils import generate_api_keys
from warehouse_management.utils.response import error, success


@frappe.whitelist(allow_guest=True, methods=["POST"])
def login(email=None, password=None):
	"""Verify email and password, return the API key/secret pair.

	Body: `{email, password}`. Returns `{user, api_key, api_secret,
	is_mpin_available}` — the flag tells the client whether to prompt for
	an MPIN or to send the user through setting one up.
	"""
	try:
		email, password = _sanitize(email, password)

		validation_error = _validate(email, password)
		if validation_error:
			return validation_error

		try:
			user = check_password(email, password)
		except frappe.AuthenticationError:
			return error("Incorrect email or password.", 401)

		api_key, api_secret = generate_api_keys(user)

		return success(
			data={
				"user": user,
				"api_key": api_key,
				"api_secret": api_secret,
				"has_mpin": bool(frappe.db.get_value("User", user, "mpin")),
			}
		)
	except Exception as e:
		frappe.log_error(title="Warehouse login failed", message=frappe.get_traceback())
		return error(str(e), 500)


@frappe.whitelist(methods=["POST"])
def verify_mpin(mpin=None):
	"""Verify the calling user's MPIN. Body: `{mpin}`.

	compare_digest is used instead of == so the comparison time doesn't
	leak how much of the MPIN was correct.
	"""
	try:
		mpin = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(mpin)))
		if not mpin:
			return error("Please provide an mpin.", 400)

		stored_mpin = _get_stored_mpin(frappe.session.user)
		if not stored_mpin:
			return error("MPIN is not set for this user.", 400, is_mpin_available=False)

		if not compare_digest(mpin, stored_mpin):
			return error("Incorrect MPIN.", 401)

		return success(data={"message": "MPIN verified."})
	except Exception as e:
		frappe.log_error(title="MPIN verification failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _get_stored_mpin(user):
	"""mpin is a Password field, so the real value lives encrypted in
	__Auth — reading it off the User row returns only the mask.
	"""
	return get_decrypted_password("User", user, "mpin", raise_exception=False)


def _sanitize(email, password):
	"""Sanitize using Frappe's own helpers: strip_html strips markup and
	cstr/strip normalize type and whitespace.
	"""
	email = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(email))).lower()
	password = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(password)))
	return email, password


def _validate(email, password):
	"""Return an error, or None when the input is valid."""
	if not frappe.utils.validate_email_address(email):
		return error("Please enter valid email.", 400)
	if not password:
		return error("Please enter a password.", 400)
	if not frappe.db.get_value("User", email, "enabled"):
		return error("This account is disabled or does not exist.", 403)
	return None
