// Bidirectional GM % ↔ Sale Price (custom_shelf_price) vs Price After Taxes.
// - Edit Sale Price / cost → recompute GM % (actual margin).
// - Edit GM % → set Sale Price = round(cost / (1 - gm/100)) so KPOs can
//   type 20 / 15 / 10 and get a whole-rupee shelf price.
// Server before_save also sets custom_gm_percent from final prices.
//
// Never write on submitted/cancelled receipts: set_value dirties the form,
// Create Purchase Invoice then blocks on "unsaved changes", and Update fails
// because custom_gm_percent is not allow_on_submit (old PRs still store 0).

frappe.provide("aimatic.shelf_gm");

(function () {
	"use strict";

	const CHILD_DOCTYPE = "Purchase Receipt Item";
	let _gm_shelf_sync_lock = false;
	// Row names where the user typed GM % and Sale Price should follow cost + GM.
	const _gm_drives_shelf = new Set();
	const _gm_retry_timers = {};

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
		return Math.round(cost / denom);
	}

	function get_row(cdt, cdn) {
		return locals[cdt] && locals[cdt][cdn];
	}

	function gm_drives_shelf_row(row) {
		return Boolean(row && _gm_drives_shelf.has(row.name) && flt(row.custom_gm_percent) > 0);
	}

	function refresh_grid_row_fields(frm, cdn, fieldnames) {
		const grid = frm?.fields_dict?.items?.grid;
		const grid_row = grid?.grid_rows_by_docname?.[cdn];
		if (!grid_row) {
			return;
		}
		(fieldnames || []).forEach((fieldname) => {
			grid_row.refresh_field(fieldname);
		});
	}

	function set_row_gm_percent(frm, cdt, cdn) {
		if (_gm_shelf_sync_lock) {
			return Promise.resolve();
		}
		const row = get_row(cdt, cdn);
		if (!row) {
			return Promise.resolve();
		}
		const gm = compute_shelf_gm_percent(row.custom_shelf_price, row.custom_price_after_taxes);
		if (flt(row.custom_gm_percent) === gm) {
			return Promise.resolve();
		}
		_gm_shelf_sync_lock = true;
		return Promise.resolve(frappe.model.set_value(cdt, cdn, "custom_gm_percent", gm)).finally(() => {
			_gm_shelf_sync_lock = false;
			refresh_grid_row_fields(frm, cdn, ["custom_gm_percent"]);
		});
	}

	function set_row_shelf_from_gm(frm, cdt, cdn) {
		if (_gm_shelf_sync_lock) {
			return Promise.resolve();
		}
		const row = get_row(cdt, cdn);
		if (!row) {
			return Promise.resolve();
		}
		const shelf = compute_shelf_price_from_gm(row.custom_price_after_taxes, row.custom_gm_percent);
		if (shelf === null) {
			return Promise.resolve();
		}
		if (flt(row.custom_shelf_price) === shelf) {
			refresh_grid_row_fields(frm, cdn, ["custom_shelf_price", "custom_gm_percent"]);
			return Promise.resolve();
		}
		_gm_shelf_sync_lock = true;
		return Promise.resolve(frappe.model.set_value(cdt, cdn, "custom_shelf_price", shelf)).finally(() => {
			_gm_shelf_sync_lock = false;
			refresh_grid_row_fields(frm, cdn, ["custom_shelf_price", "custom_gm_percent"]);
		});
	}

	function sync_row_after_cost_change(frm, cdt, cdn) {
		const row = get_row(cdt, cdn);
		if (!row) {
			return;
		}
		if (gm_drives_shelf_row(row)) {
			set_row_shelf_from_gm(frm, cdt, cdn);
			return;
		}
		set_row_gm_percent(frm, cdt, cdn);
	}

	function mark_gm_drives_shelf(cdn, gm_percent) {
		if (flt(gm_percent) > 0) {
			_gm_drives_shelf.add(cdn);
		} else {
			_gm_drives_shelf.delete(cdn);
		}
	}

	function clear_gm_retry(cdn) {
		if (_gm_retry_timers[cdn]) {
			clearTimeout(_gm_retry_timers[cdn]);
			delete _gm_retry_timers[cdn];
		}
	}

	function schedule_gm_shelf_retries(frm, cdt, cdn) {
		clear_gm_retry(cdn);
		// Cost may land a beat after GM % in the spreadsheet grid (prv1 calc).
		_gm_retry_timers[cdn] = setTimeout(() => {
			delete _gm_retry_timers[cdn];
			if (!get_row(cdt, cdn)) {
				return;
			}
			set_row_shelf_from_gm(frm, cdt, cdn);
		}, 450);
	}

	function on_gm_percent_edited(frm, cdt, cdn) {
		if (!frm || frm.doc.docstatus !== 0) {
			return;
		}
		const row = get_row(cdt, cdn);
		if (!row) {
			return;
		}
		mark_gm_drives_shelf(cdn, row.custom_gm_percent);
		set_row_shelf_from_gm(frm, cdt, cdn);
		schedule_gm_shelf_retries(frm, cdt, cdn);
	}

	function refresh_initial_gm_display(frm) {
		if (!frm || frm.doc.docstatus !== 0) {
			return;
		}
		(frm.doc.items || []).forEach((row) => {
			if (gm_drives_shelf_row(row)) {
				if (flt(row.custom_price_after_taxes) > 0) {
					set_row_shelf_from_gm(frm, row.doctype, row.name);
				}
				return;
			}
			if (flt(row.custom_shelf_price) > 0) {
				set_row_gm_percent(frm, row.doctype, row.name);
			}
		});
	}

	function bind_grid_gm_listener(frm) {
		const grid = frm?.fields_dict?.items?.grid;
		const wrapper = grid?.wrapper;
		if (!wrapper || wrapper.dataset.aimaticGmBound === "1") {
			return;
		}
		wrapper.dataset.aimaticGmBound = "1";

		// Fallback for spreadsheet grid cells where the child-table trigger
		// can lag behind the committed input value.
		$(wrapper).on("change.aimatic_gm", `[data-fieldname="custom_gm_percent"] input`, function () {
			const grid_row = $(this).closest(".grid-row").data("grid_row");
			if (!grid_row?.doc?.name) {
				return;
			}
			setTimeout(() => {
				on_gm_percent_edited(frm, grid_row.doc.doctype, grid_row.doc.name);
			}, 0);
		});
	}

	// Called by prv1 after custom_price_after_taxes is written.
	aimatic.shelf_gm.after_cost_updated = function (frm, cdt, cdn) {
		if (!frm || frm.doctype !== "Purchase Receipt" || frm.doc.docstatus !== 0) {
			return;
		}
		sync_row_after_cost_change(frm, cdt, cdn);
	};

	aimatic.shelf_gm.on_gm_percent_edited = on_gm_percent_edited;

	frappe.ui.form.on("Purchase Receipt", {
		onload_post_render(frm) {
			bind_grid_gm_listener(frm);
			refresh_initial_gm_display(frm);
		},
		refresh(frm) {
			bind_grid_gm_listener(frm);
			refresh_initial_gm_display(frm);
		},
	});

	frappe.ui.form.on(CHILD_DOCTYPE, {
		custom_gm_percent(frm, cdt, cdn) {
			on_gm_percent_edited(frm, cdt, cdn);
		},
		custom_shelf_price(frm, cdt, cdn) {
			if (frm.doc.docstatus !== 0) {
				return;
			}
			if (_gm_shelf_sync_lock) {
				return;
			}
			_gm_drives_shelf.delete(cdn);
			clear_gm_retry(cdn);
			set_row_gm_percent(frm, cdt, cdn);
		},
		custom_price_after_taxes(frm, cdt, cdn) {
			if (frm.doc.docstatus !== 0) {
				return;
			}
			sync_row_after_cost_change(frm, cdt, cdn);
		},
	});
})();
