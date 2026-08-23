// Bidirectional GM % ↔ Sale Price (custom_shelf_price) vs Price After Taxes.
// - Edit Sale Price / cost → recompute GM % (actual margin).
// - Edit GM % → set Sale Price = round(cost / (1 - gm/100)) so KPOs can
//   type 20 / 15 / 10 and get a whole-rupee shelf price.
// Server before_save also sets custom_gm_percent from final prices.
//
// Never write on submitted/cancelled receipts: set_value dirties the form,
// Create Purchase Invoice then blocks on "unsaved changes", and Update fails
// because custom_gm_percent is not allow_on_submit (old PRs still store 0).

let _gm_shelf_sync_lock = false;

function compute_shelf_gm_percent(shelf_price, cost_after_taxes) {
	const sale = flt(shelf_price);
	if (sale <= 0) {
		return 0;
	}
	return flt(((sale - flt(cost_after_taxes)) / sale) * 100, 2);
}

function compute_shelf_price_from_gm(cost_after_taxes, gm_percent) {
	const gm = flt(gm_percent);
	const cost = flt(cost_after_taxes);
	if (gm >= 100) {
		return null;
	}
	const denom = 1 - gm / 100;
	if (Math.abs(denom) < 1e-12) {
		return null;
	}
	// Whole-rupee round: cost 100, GM 20 → 125; cost 83.33, GM 20 → 104.
	return Math.round(cost / denom);
}

function set_row_gm_percent(cdt, cdn) {
	if (_gm_shelf_sync_lock) {
		return;
	}
	const row = locals[cdt][cdn];
	if (!row) {
		return;
	}
	const gm = compute_shelf_gm_percent(row.custom_shelf_price, row.custom_price_after_taxes);
	if (flt(row.custom_gm_percent) === gm) {
		return;
	}
	_gm_shelf_sync_lock = true;
	frappe.model.set_value(cdt, cdn, "custom_gm_percent", gm).always(() => {
		_gm_shelf_sync_lock = false;
	});
}

function set_row_shelf_from_gm(cdt, cdn) {
	if (_gm_shelf_sync_lock) {
		return;
	}
	const row = locals[cdt][cdn];
	if (!row) {
		return;
	}
	const shelf = compute_shelf_price_from_gm(row.custom_price_after_taxes, row.custom_gm_percent);
	if (shelf === null) {
		return;
	}
	if (flt(row.custom_shelf_price) === shelf) {
		return;
	}
	_gm_shelf_sync_lock = true;
	frappe.model.set_value(cdt, cdn, "custom_shelf_price", shelf).always(() => {
		_gm_shelf_sync_lock = false;
	});
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
	custom_gm_percent(frm, cdt, cdn) {
		if (frm.doc.docstatus !== 0) {
			return;
		}
		set_row_shelf_from_gm(cdt, cdn);
	},
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
