// Adds a "Create Barcode Labels" button to Delivery Note, Stock Entry,
// Sales Invoice, and Purchase Order, mirroring Purchase Receipt
// (purchase_receipt_label_printing.js). Kept as a separate file/doctype_js
// entry per doctype so Purchase Receipt's existing script is left untouched.
["Delivery Note", "Stock Entry", "Sales Invoice", "Purchase Order"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			// PO allows draft + submitted (pre-receipt labeling); others stay
			// submitted-only. Cancelled docs never get a button.
			if (doctype === "Purchase Order") {
				if (frm.doc.docstatus === 2 || frm.is_new()) {
					return;
				}
			} else if (frm.doc.docstatus !== 1) {
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
