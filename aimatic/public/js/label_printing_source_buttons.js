// Adds a "Create Barcode Labels" button to Delivery Note, Stock Entry, and
// Sales Invoice, mirroring the one Purchase Receipt already has
// (purchase_receipt_label_printing.js). Kept as a separate file/doctype_js
// entry per doctype so Purchase Receipt's existing, working script is left
// untouched.
["Delivery Note", "Stock Entry", "Sales Invoice"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (frm.doc.docstatus !== 1) {
				return;
			}
			if (doctype === "Stock Entry" && frm.doc.purpose !== "Material Transfer") {
				return;
			}
			if (!frappe.model.can_create("AIM Label Print Job")) {
				return;
			}

			frm.add_custom_button(__("Create Barcode Labels"), () => {
				frappe.call({
					method: "aimatic.label_printing.api.create_from_source_document",
					args: {
						source_type: doctype,
						source_name: frm.doc.name,
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
});
