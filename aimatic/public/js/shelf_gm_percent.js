// Live GM % between Sale Price (custom_shelf_price) and Price After Taxes.
// Server validate also sets custom_gm_percent; this is Desk data-entry only
// so KPOs see margin while typing without waiting for save.
// Formula matches purchase_receipt_custom_layout /
// aimatic.shelf_pricing.events.compute_shelf_gm_percent.
//
// Never write on submitted/cancelled receipts: set_value dirties the form,
// Create Purchase Invoice then blocks on "unsaved changes", and Update fails
// because custom_gm_percent is not allow_on_submit (old PRs still store 0).

function compute_shelf_gm_percent(shelf_price, cost_after_taxes) {
	const sale = flt(shelf_price);
	if (sale <= 0) {
		return 0;
	}
	return flt(((sale - flt(cost_after_taxes)) / sale) * 100, 2);
}

function set_row_gm_percent(cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row) {
		return;
	}
	const gm = compute_shelf_gm_percent(row.custom_shelf_price, row.custom_price_after_taxes);
	if (flt(row.custom_gm_percent) === gm) {
		return;
	}
	frappe.model.set_value(cdt, cdn, "custom_gm_percent", gm);
}

function refresh_all_gm_percent(frm) {
	if (frm.doc.docstatus !== 0) {
		return;
	}
	(frm.doc.items || []).forEach((row) => {
		set_row_gm_percent(row.doctype, row.name);
	});
}

frappe.ui.form.on("Purchase Receipt", {
	refresh(frm) {
		refresh_all_gm_percent(frm);
	},
});

frappe.ui.form.on("Purchase Receipt Item", {
	custom_shelf_price(frm, cdt, cdn) {
		if (frm.doc.docstatus !== 0) {
			return;
		}
		set_row_gm_percent(cdt, cdn);
	},
	custom_price_after_taxes(frm, cdt, cdn) {
		if (frm.doc.docstatus !== 0) {
			return;
		}
		set_row_gm_percent(cdt, cdn);
	},
});
