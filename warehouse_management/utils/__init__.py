import frappe


def generate_api_keys(user_name):
	"""Create or rotate a User's API key/secret. `ignore_permissions=True`
	covers the save, so no Administrator switch is needed.
	"""
	user_doc = frappe.get_doc("User", user_name)
	if not user_doc.api_key:
		user_doc.api_key = frappe.generate_hash(length=15)
	api_secret = frappe.generate_hash(length=15)
	user_doc.api_secret = api_secret
	user_doc.flags.ignore_permissions = True
	user_doc.save(ignore_permissions=True)
	return user_doc.api_key, api_secret
