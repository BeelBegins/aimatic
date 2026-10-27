frappe.ui.form.on("Journal Entry", {
	onload_post_render(frm) {
		aimatic_show_je_cost_center(frm);
	},
	refresh(frm) {
		aimatic_show_je_cost_center(frm);
	},
});

frappe.ui.form.on("Journal Entry Account", {
	branch(frm, cdt, cdn) {
		aimatic_sync_je_row_cost_center(frm, cdt, cdn);
	},
	account(frm, cdt, cdn) {
		aimatic_sync_je_row_cost_center(frm, cdt, cdn);
	},
});

function aimatic_show_je_cost_center(frm) {
	if (!frm.fields_dict.accounts) {
		return;
	}
	frm.set_df_property("accounts", "cannot_add_rows", frm.doc.docstatus !== 0);
	const grid = frm.fields_dict.accounts.grid;
	if (!grid) {
		return;
	}
	// Keep the existing accounting/dimension fields alongside per-line remarks.
	// Ten data columns leave room for Frappe's row controls without overflowing.
	const widths = {
		account: 2,
		party_type: 1,
		party: 1,
		cost_center: 1,
		branch: 1,
		debit_in_account_currency: 1,
		credit_in_account_currency: 1,
		user_remark: 2,
	};
	for (const [fieldname, columns] of Object.entries(widths)) {
		grid.update_docfield_property(fieldname, "columns", columns);
	}
	for (const fieldname of ["cost_center", "branch", "user_remark"]) {
		grid.update_docfield_property(fieldname, "in_list_view", 1);
	}
	grid.update_docfield_property("user_remark", "label", "Remarks");
	if (grid.header_row) {
		grid.reset_grid();
	}
}

function aimatic_sync_je_row_cost_center(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row || !row.branch || frm.doc.docstatus !== 0) {
		return;
	}
	frappe.db.get_value("Branch", row.branch, "cost_center", (r) => {
		if (r && r.cost_center) {
			frappe.model.set_value(cdt, cdn, "cost_center", r.cost_center);
		}
	});
}
