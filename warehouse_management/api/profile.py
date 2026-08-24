"""Profile — the logged-in user's details plus warehouse-wide stats.

No input: the user is taken from the Authorization header, same as logout.
total_items/total_warehouse only change on Item/Warehouse creation, so
they're cached and cleared via hooks.py. open_po/open_so and
initial_reconciliation change on document submits with no single reliable
hook to chase (see status_updater.py / mark_warehouse_reconciled), so
they're queried live on each call instead.
"""

import frappe

from warehouse_management.utils.response import error, success

STATS_CACHE_KEY = "warehouse_management:profile_stats"
STATS_CACHE_SECONDS = 300

OPEN_PO_STATUSES = ["To Receive and Bill", "To Receive"]
OPEN_SO_STATUSES = ["To Deliver and Bill", "To Deliver"]


@frappe.whitelist(methods=["GET"])
def profile():
	"""Return the logged-in user's profile plus warehouse stats.
	No input required.
	"""
	try:
		user = frappe.session.user
		full_name = frappe.db.get_value("User", user, "full_name")

		return success(
			data={
				"full_name": full_name,
				"email": user,
				**get_cached_stats(),
				"open_po": frappe.db.count("Purchase Order", {"status": ["in", OPEN_PO_STATUSES]}),
				"open_so": frappe.db.count("Sales Order", {"status": ["in", OPEN_SO_STATUSES]}),
				"initial_reconciliation": _all_leaf_warehouses_reconciled(),
			}
		)
	except Exception as e:
		frappe.log_error(title="Warehouse profile failed", message=frappe.get_traceback())
		return error(str(e), 500)


def get_cached_stats():
	"""Item/warehouse counts, cached for 5 minutes and cleared by
	hooks.py the moment an Item or Warehouse is created.
	"""
	cached = frappe.cache.get_value(STATS_CACHE_KEY)
	if cached:
		return cached

	stats = {
		"total_items": frappe.db.count("Item"),
		"total_warehouse": frappe.db.count("Warehouse"),
	}
	frappe.cache.set_value(STATS_CACHE_KEY, stats, expires_in_sec=STATS_CACHE_SECONDS)
	return stats


def invalidate_stats_cache(doc=None, method=None):
	"""hooks.py doc_events target for Item/Warehouse after_insert."""
	frappe.cache.delete_value(STATS_CACHE_KEY)


def mark_warehouse_reconciled(doc, method=None):
	"""hooks.py doc_events target for Stock Reconciliation on_submit.
	Flags every warehouse in this reconciliation's items as done.
	"""
	warehouses = {row.warehouse for row in doc.items if row.warehouse}
	for warehouse in warehouses:
		frappe.db.set_value("Warehouse", warehouse, "initial_reconciliation", 1)


def _all_leaf_warehouses_reconciled():
	"""True unless at least one leaf warehouse still has
	initial_reconciliation = 0. Queried live, not cached, since it
	changes via mark_warehouse_reconciled on Stock Reconciliation submit.
	"""
	return not frappe.db.exists("Warehouse", {"is_group": 0, "initial_reconciliation": 0})
