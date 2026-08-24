frappe.ui.form.on("Purchase Invoice", {
	is_return(frm) {
		aimatic_ensure_return_updates_stock(frm);
	},
	onload(frm) {
		aimatic_ensure_return_updates_stock(frm);
	},
	refresh(frm) {
		aimatic_ensure_return_updates_stock(frm);
	},
});

function aimatic_ensure_return_updates_stock(frm) {
	if (frm.doc.docstatus !== 0 || !cint(frm.doc.is_return)) {
		return;
	}
	if (cint(frm.doc.update_stock)) {
		return;
	}
	frm.set_value("update_stock", 1);
}
