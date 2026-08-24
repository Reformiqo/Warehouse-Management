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


def get_pending_sales_orders(item_code, statuses):
	"""[{customer, qty, so_name, so_date}, ...] for Sales Orders in the
	given statuses that reference this item.
	"""
	rows = frappe.db.sql(
		"""
		SELECT order_doc.customer, item_row.qty,
		       order_doc.name AS so_name, order_doc.transaction_date AS so_date
		FROM `tabSales Order Item` item_row
		INNER JOIN `tabSales Order` order_doc ON order_doc.name = item_row.parent
		WHERE order_doc.status IN %(statuses)s AND item_row.item_code = %(item_code)s
		""",
		{"statuses": tuple(statuses), "item_code": item_code},
		as_dict=True,
	)
	return [
		{
			"customer": row.customer,
			"qty": row.qty,
			"so_name": row.so_name,
			"so_date": str(row.so_date) if row.so_date else None,
		}
		for row in rows
	]


def get_open_order_counts(child_doctype, parent_doctype, statuses):
	"""{item_code: distinct parent-document count} for the given statuses."""
	rows = frappe.db.sql(
		f"""
		SELECT item_row.item_code, COUNT(DISTINCT item_row.parent) AS cnt
		FROM `tab{child_doctype}` item_row
		INNER JOIN `tab{parent_doctype}` order_doc ON order_doc.name = item_row.parent
		WHERE order_doc.status IN %(statuses)s
		GROUP BY item_row.item_code
		""",
		{"statuses": tuple(statuses)},
		as_dict=True,
	)
	return {row.item_code: row.cnt for row in rows}


def get_recent_documents_by_owner(doctype, child_doctype, party_field, limit=5):
	"""The caller's last `limit` submitted documents of `doctype`, each
	with total_unique_items (distinct item_code count in child_doctype)
	and `party_field` (e.g. "customer" or "supplier").
	"""
	documents = frappe.get_all(
		doctype,
		filters={"docstatus": 1, "owner": frappe.session.user},
		fields=["name", party_field],
		order_by="creation desc",
		limit=limit,
	)
	if not documents:
		return []

	names = [doc.name for doc in documents]
	documents_by_name = {doc.name: doc for doc in documents}

	unique_items_by_doc = {}
	for row in frappe.get_all(
		child_doctype, filters={"parent": ["in", names]}, fields=["parent", "item_code"]
	):
		unique_items_by_doc.setdefault(row.parent, set()).add(row.item_code)

	return [
		{
			"id": name,
			party_field: documents_by_name[name].get(party_field),
			"line_items": len(unique_items_by_doc.get(name, set())),
		}
		for name in names
		if name in documents_by_name
	]
