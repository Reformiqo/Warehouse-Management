# your_app/api/stock_ledger.py

import frappe
from warehouse_management.utils.response import error, success


@frappe.whitelist(methods=["GET"])
def stock_ledger(warehouse=None, item_code=None, item_group=None, brand=None, from_date=None, to_date=None, reconciliation_status=None, limit=20, offset=0):
    try:
        limit = int(limit)
        offset = int(offset)

        filters = frappe._dict({
            "from_date": from_date,
            "to_date": to_date
        })
        if warehouse:
            filters["warehouse"] = warehouse
        if item_code:
            filters["item_code"] = [item_code]
        if item_group:
            filters["item_group"] = item_group
        if brand:
            filters["brand"] = brand

        if reconciliation_status:
            if reconciliation_status == "reconciled":
                if filters.get("warehouse"):
                    is_reconciled = frappe.db.get_value("Warehouse", filters.get("warehouse"), "is_reconciled")
                    if not is_reconciled:
                        return []
            else:
                if filters.get("warehouse"):
                    is_reconciled = frappe.db.get_value("Warehouse", filters.get("warehouse"), "is_reconciled")
                    if is_reconciled:
                        return []

        data = execute(filters)
        data = data[offset: offset + limit]

        return success(data=data)

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            "Stock Ledger API Error"
        )

        return error(message=str(e))


@frappe.whitelist(methods=["GET"])
def warehouse_list(search=None, limit=20, offset=0):
    return _master_list(
        doctype="Warehouse",
        search=search,
        limit=limit,
        offset=offset,
        search_fields=["name"],
    )


@frappe.whitelist(methods=["GET"])
def item_list(search=None, limit=20, offset=0):
    return _master_list(
        doctype="Item",
        search=search,
        limit=limit,
        offset=offset,
        search_fields=["item_code", "item_name"],
    )


@frappe.whitelist(methods=["GET"])
def item_group_list(search=None, limit=20, offset=0):
    return _master_list(
        doctype="Item Group",
        search=search,
        limit=limit,
        offset=offset,
        search_fields=["name"],
    )


@frappe.whitelist(methods=["GET"])
def brand_list(search=None, limit=20, offset=0):
    return _master_list(
        doctype="Brand",
        search=search,
        limit=limit,
        offset=offset,
        search_fields=["name"],
    )


def _master_list(
    doctype,
    search=None,
    limit=20,
    offset=0,
    search_fields=None,
    order_by="name",
):
    """Reusable helper for master list APIs."""

    try:
        limit = int(limit or 20)
        offset = int(offset or 0)

        filters = {}
        or_filters = {}

        if search and search_fields:
            search = frappe.utils.strip_html(frappe.utils.cstr(search)).strip()

            if search:
                if len(search_fields) == 1:
                    filters[search_fields[0]] = ["like", f"%{search}%"]
                else:
                    or_filters = [
                        [field, "like", f"%{search}%"]
                        for field in search_fields
                    ]

        data = frappe.get_all(
            doctype,
            pluck="name",
            filters=filters,
            or_filters=or_filters,
            order_by=order_by,
            limit_start=offset,
            limit_page_length=limit,
        )

        return success(data=data)

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            f"{doctype} List API Error",
        )

        return {
            "success": False,
            "error": str(e),
        }



def execute(filters):
    """Wrapper for stock ledger report execute function."""
    from erpnext.stock.report.stock_ledger.stock_ledger import get_items
    from erpnext.stock.report.stock_ledger.stock_ledger import get_stock_ledger_entries
    # from erpnext.stock.report.stock_ledger.stock_ledger import get_item_details

    items = get_items(filters)
    sl_entries = get_stock_ledger_entries(filters, items)

    sl_entries = apply_stock_movement_filters(sl_entries, filters)

    return aggregate_stock_ledger_entries(sl_entries)


def apply_stock_movement_filters(sl_entries, filters):
    INCOMING_ENTRY = "Purchase Receipt"
    OUTGOING_ENTRY = "Delivery Note"
    TRANSFER_ENTRY = "Stock Entry"

    stock_movements = filters.get("stock_movement")
    if not stock_movements:
        return sl_entries

    if stock_movements == "incoming":
        stock_movements = [INCOMING_ENTRY]
    elif stock_movements == "outgoing":
        stock_movements = [OUTGOING_ENTRY]
    elif stock_movements == "transfer":
        stock_movements = [TRANSFER_ENTRY]
    else:
        return sl_entries

    sl_entries = [
        sle
        for sle in sl_entries
        if sle.voucher_type in stock_movements
    ]


def aggregate_stock_ledger_entries(sl_entries):
    aggregated_data = {}

    for sle in sl_entries:
        key = (sle.item_code, sle.warehouse)

        if key not in aggregated_data:
            aggregated_data[key] = {
                "item_code": sle.item_code,
                "warehouse": sle.warehouse,
                "qty": 0,
            }

        aggregated_data[key]["qty"] += sle.qty_after_transaction

    return list(aggregated_data.values())