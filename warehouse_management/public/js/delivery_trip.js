// Narrows the Pick Up Details lookups to the supplier chosen on the trip.
frappe.ui.form.on("Delivery Trip", {
	refresh(frm) {
		frm.set_query("purchase_order", "pickup_details", (doc) => {
			return { filters: { supplier: doc.pickup_supplier } }
		});

		frm.set_query("pickup_supplier_address", () => ({
			query: "frappe.contacts.doctype.address.address.address_query",
			filters: { link_doctype: "Supplier", link_name: frm.doc.pickup_supplier },
		}));

		frm.set_query("pickup_supplier_contact", () => ({
			query: "frappe.contacts.doctype.contact.contact.contact_query",
			filters: { link_doctype: "Supplier", link_name: frm.doc.pickup_supplier },
		}));
	},

	pickup_supplier(frm) {
		// the old address and contact belong to the previous supplier
		frm.set_value("pickup_supplier_address", null);
		frm.set_value("pickup_supplier_contact", null);
	},
});
