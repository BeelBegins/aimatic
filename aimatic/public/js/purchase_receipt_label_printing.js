frappe.ui.form.on("Purchase Receipt", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) {
			return;
		}
		if (!frappe.model.can_create("AIM Label Print Job")) {
			return;
		}

		frm.add_custom_button(__("Create Barcode Labels"), () => {
			frappe.call({
				method: "aimatic.label_printing.api.create_from_purchase_receipt",
				args: {
					purchase_receipt: frm.doc.name,
				},
				freeze: true,
				freeze_message: __("Creating label print job..."),
				callback: (r) => {
					if (r.message) {
						frappe.set_route("Form", "AIM Label Print Job", r.message);
					}
				},
			});
		});
	},
});
