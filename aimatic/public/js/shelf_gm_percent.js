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
// Row names where the user typed GM % and Sale Price should follow cost + GM,
// not the other way around (refresh / tax recalc must not wipe typed GM %).
const _gm_drives_shelf = new Set();

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
	if (cost <= 0 || gm <= 0) {
		return null;
	}
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

function gm_drives_shelf_row(row) {
	return Boolean(row && _gm_drives_shelf.has(row.name) && flt(row.custom_gm_percent) > 0);
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

function sync_row_after_cost_change(cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row) {
		return;
	}
	if (gm_drives_shelf_row(row)) {
		set_row_shelf_from_gm(cdt, cdn);
		return;
	}
	set_row_gm_percent(cdt, cdn);
}

function refresh_all_gm_shelf(frm) {
	if (frm.doc.docstatus !== 0) {
		return;
	}
	(frm.doc.items || []).forEach((row) => {
		if (gm_drives_shelf_row(row)) {
			// Cost may have arrived after GM % was typed — apply margin now.
			if (flt(row.custom_price_after_taxes) > 0) {
				set_row_shelf_from_gm(row.doctype, row.name);
			}
			return;
		}
		set_row_gm_percent(row.doctype, row.name);
	});
}

frappe.ui.form.on("Purchase Receipt", {
	refresh(frm) {
		refresh_all_gm_shelf(frm);
	},
});

frappe.ui.form.on("Purchase Receipt Item", {
	custom_gm_percent(frm, cdt, cdn) {
		if (frm.doc.docstatus !== 0) {
			return;
		}
		const row = locals[cdt][cdn];
		if (!row) {
			return;
		}
		if (flt(row.custom_gm_percent) > 0) {
			_gm_drives_shelf.add(cdn);
		} else {
			_gm_drives_shelf.delete(cdn);
		}
		set_row_shelf_from_gm(cdt, cdn);
	},
	custom_shelf_price(frm, cdt, cdn) {
		if (frm.doc.docstatus !== 0) {
			return;
		}
		// Programmatic shelf updates from GM % must not clear GM-driven mode.
		if (_gm_shelf_sync_lock) {
			return;
		}
		_gm_drives_shelf.delete(cdn);
		set_row_gm_percent(cdt, cdn);
	},
	custom_price_after_taxes(frm, cdt, cdn) {
		if (frm.doc.docstatus !== 0) {
			return;
		}
		sync_row_after_cost_change(cdt, cdn);
	},
});
