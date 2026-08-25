"""Signup — creates a Frappe User and linked Employee with the roles in
SIGNUP_ROLES.

Inputs are sanitized before use. `ignore_permissions=True` covers both
inserts. Employee.create_user_permission is turned off so setting
user_id directly doesn't trigger ERPNext's add_user_permission(), which
does its own insert() checked against the session user and isn't
covered by ignore_permissions on the Employee insert itself.
"""

import frappe

from warehouse_management.utils import generate_api_keys
from warehouse_management.utils.response import error, success

SIGNUP_ROLES = ["Stock Manager", "Purchase User"]


@frappe.whitelist(allow_guest=True, methods=["POST"])
def signup(full_name=None, email=None, password=None):
	"""Create a Stock Manager account from a name, email and password.

	Body: `{full_name, email, password}`. Returns the created User.
	"""
	try:
		full_name, email, password = _sanitize(full_name, email, password)

		validation_error = _validate(full_name, email, password)
		if validation_error:
			return validation_error

		user = _create_user(full_name, email, password)
		_create_employee(full_name, email, user)
		api_key, api_secret = generate_api_keys(user)
		frappe.db.commit()

		return success(
			data={
				"user": user,
				"api_key": api_key,
				"api_secret": api_secret,
				"message": "User created Successfully",
			},
			http_status=201,
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Warehouse signup failed", message=frappe.get_traceback())
		return error(str(e), 500)


def _sanitize(full_name, email, password):
	"""Sanitize using Frappe's own helpers: strip_html strips markup and
	cstr/strip normalize type and whitespace.
	"""
	full_name = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(full_name)))
	email = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(email))).lower()
	password = frappe.utils.strip(frappe.utils.strip_html(frappe.utils.cstr(password)))
	return full_name, email, password


def _validate(full_name, email, password):
	"""Return an error, or None when the input is valid."""
	if not full_name:
		return error("Please enter your full name.", 400)
	if not frappe.utils.validate_email_address(email):
		return error("Please enter valid email.", 400)
	if not password:
		return error("Please enter a password.", 400)
	if frappe.db.exists("User", email):
		return error(
			"An account already exists for this email. Please log in or use Forgot Password.", 409
		)
	return None


def _create_user(full_name, email, password):
	first_name, middle_name, last_name = _split_name(full_name)
	user = frappe.new_doc("User")
	user.email = email
	user.first_name = first_name
	user.middle_name = middle_name
	user.last_name = last_name
	user.user_type = "System User"
	user.enabled = 1
	user.send_welcome_email = 0
	user.new_password = password
	user.append_roles(*SIGNUP_ROLES)
	user.flags.ignore_permissions = True
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)
	return user.name


def _create_employee(full_name, email, user):
	"""gender/date_of_birth/date_of_joining are non-mandatory via property
	setter (see setup/property_setters.py) since signup collects none of
	them. create_user_permission is turned off so setting user_id doesn't
	trigger ERPNext's add_user_permission(), which does its own insert()
	checked against the session user and fails for this endpoint's Guest
	session.
	"""
	first_name, middle_name, last_name = _split_name(full_name)
	emp = frappe.new_doc("Employee")
	emp.first_name = first_name
	emp.middle_name = middle_name
	emp.last_name = last_name
	emp.employee_name = full_name
	emp.company = frappe.db.get_single_value("Global Defaults", "default_company")
	emp.date_of_joining = frappe.utils.today()
	emp.status = "Active"
	emp.personal_email = email
	emp.user_id = user
	emp.create_user_permission = 0
	emp.flags.ignore_permissions = True
	emp.insert(ignore_permissions=True)
	return emp.name


def _split_name(full_name):
	"""First word is first_name, last word is last_name, anything in
	between is middle_name.
	"""
	parts = full_name.split()
	if len(parts) <= 2:
		return parts[0], "", (parts[1] if len(parts) > 1 else "")
	return parts[0], " ".join(parts[1:-1]), parts[-1]
