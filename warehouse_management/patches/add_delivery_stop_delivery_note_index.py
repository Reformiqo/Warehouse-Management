"""Index Delivery Stop.delivery_note so pending_delivery_notes can anti-join it
per candidate note. The property setter is what keeps the index through later
migrations; add_index creates it now, as a patch run counts as in_migrate.
"""

import frappe

from warehouse_management.setup.property_setters import DELIVERY_STOP_NOTE_INDEX


def execute():
	frappe.make_property_setter(DELIVERY_STOP_NOTE_INDEX)
	frappe.db.add_index("Delivery Stop", ["delivery_note"])
