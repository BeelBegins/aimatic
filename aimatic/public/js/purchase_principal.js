frappe.ui.form.on("Purchase Order", {
	setup(frm) {
		aimatic_setup_principal_query(frm);
	},
	supplier(frm) {
		aimatic_clear_principal_on_supplier_change(frm);
	},
});

frappe.ui.form.on("Purchase Receipt", {
	setup(frm) {
		aimatic_setup_principal_query(frm);
	},
	supplier(frm) {
		aimatic_clear_principal_on_supplier_change(frm);
	},
});

frappe.ui.form.on("Purchase Invoice", {
	setup(frm) {
		aimatic_setup_principal_query(frm);
	},
	supplier(frm) {
		aimatic_clear_principal_on_supplier_change(frm);
	},
});

function aimatic_setup_principal_query(frm) {
	frm.set_query("custom_principal", () => {
		const supplier = frm.doc.supplier;
		if (!supplier) {
			return { filters: { name: ["in", []] } };
		}
		return {
			query: "aimatic.purchase_principal.get_principal_query",
			filters: { supplier },
		};
	});
}

function aimatic_clear_principal_on_supplier_change(frm) {
	if (frm.doc.custom_principal) {
		frm.set_value("custom_principal", "");
	}
}
