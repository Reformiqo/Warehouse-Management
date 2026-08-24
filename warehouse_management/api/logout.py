"""Logout — invalidate the caller's API secret. api_key is left alone;
it's just the identifier, not the credential.

api_secret is a Password field: its real value lives encrypted in __Auth,
not the plain User row, so it must be cleared via doc.save() (which routes
through Frappe's _save_passwords/remove_encrypted_password) rather than
frappe.db.set_value, which would only touch the unused plain column.

Not using frappe.local.login_manager.logout() here: Frappe's own session
code (frappe/sessions.py) never touches api_key/api_secret — sessions and
API tokens are fully independent in core — and login_manager.logout() also
injects home_page/full_name into the response, fields meant for a browser
login, not this token-based API.
"""

import frappe

from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["POST"])
def logout():
	"""Invalidate the calling user's API secret. No body required; the
	user is taken from the Authorization header.
	"""
	try:
		user_doc = frappe.get_doc("User", frappe.session.user)
		user_doc.api_secret = ""
		user_doc.flags.ignore_permissions = True
		user_doc.save(ignore_permissions=True)

		frappe.db.commit()
		return success(data={"message": "User logged out successfully."})
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Warehouse logout failed", message=frappe.get_traceback())
		return error(str(e), 500)
