// Live pre-save prefill only, mirroring foodpanda_price_prefill.js's exact
// pattern for custom_fp_price. Prefills custom_shelf_price (Sale Price) with
// the branch's *current* Selling Price List rate, and custom_mrp with the
// item's *current* global Item.custom_mrp, purely so the user has a starting
// point and a value to compare against/edit rather than a blank field - if
// the user leaves either untouched, the submit-time update just reasserts
// the same value (a no-op write), and a genuinely blank/zero value still
// skips the update exactly as before. Never overwrites a manually entered
// value. Deliberately scoped to Purchase Receipt only, per the shelf-pricing
// feature's scope (see shelf-pricing skill).

const helpers = {
	refresh_row(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row || !row.item_code) {
			return;
		}
		if (frm.doc.branch && !row.custom_shelf_price) {
			frappe.call({
				method: "aimatic.shelf_pricing.api.get_current_branch_sale_price",
				args: {
					item_code: row.item_code,
					branch: frm.doc.branch,
				},
				callback(r) {
					const rate = (r.message || {}).rate || 0;
					if (rate > 0 && !row.custom_shelf_price) {
						frappe.model.set_value(cdt, cdn, "custom_shelf_price", rate);
						frm.fields_dict.items.grid.refresh();
					}
				},
			});
		}
		// MRP is a single global value (not branch/price-list scoped), so unlike
		// Sale Price this doesn't need frm.doc.branch to already be set.
		if (!row.custom_mrp) {
			frappe.call({
				method: "aimatic.shelf_pricing.api.get_current_mrp",
				args: {
					item_code: row.item_code,
				},
				callback(r) {
					const mrp = (r.message || {}).mrp || 0;
					if (mrp > 0 && !row.custom_mrp) {
						frappe.model.set_value(cdt, cdn, "custom_mrp", mrp);
						frm.fields_dict.items.grid.refresh();
					}
				},
			});
		}
	},

	refresh_all_rows(frm) {
		(frm.doc.items || []).forEach((row) => {
			helpers.refresh_row(frm, row.doctype, row.name);
		});
	},
};

frappe.ui.form.on("Purchase Receipt", {
	branch(frm) {
		helpers.refresh_all_rows(frm);
	},
	// "Get Items From Purchase Order" (erpnext.utils.map_current_doc) maps rows
	// in bulk via frappe.model.sync + frm.refresh() - it never fires the child
	// table's own item_code change event, so that's the only place this ever
	// ran before. refresh_row() already no-ops for rows that already have a
	// value, so re-running here on every refresh only ever does real work for
	// genuinely blank rows.
	refresh(frm) {
		helpers.refresh_all_rows(frm);
	},
});

frappe.ui.form.on("Purchase Receipt Item", {
	item_code(frm, cdt, cdn) {
		helpers.refresh_row(frm, cdt, cdn);
	},
});
