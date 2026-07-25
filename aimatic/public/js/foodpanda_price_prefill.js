// Live pre-save preview only, mirroring purchase_history_autofill.js's
// pattern. Prefills custom_fp_price with the branch's *current* Foodpanda
// Price List rate for this item, purely for user convenience - if the row
// is added another way (e.g. Get Items From Purchase Order), the field
// stays whatever it already was and apply_foodpanda_price_update on the
// Purchase Receipt falls back to its own "leave existing FP price
// untouched" behavior. Shared across Purchase Order, Purchase Receipt, and
// Purchase Invoice, all of which carry the identical custom_fp_price field.

function aimatic_register_foodpanda_price_prefill(doctype, child_doctype) {
	const helpers = {
		refresh_row(frm, cdt, cdn) {
			const row = locals[cdt][cdn];
			if (!row || !frm.doc.branch || !row.item_code) {
				return;
			}
			if (row.custom_fp_price) {
				// Only prefill blank rows - never overwrite a manually entered value.
				return;
			}
			frappe.call({
				method: "aimatic.shelf_pricing.api.get_current_foodpanda_price",
				args: {
					item_code: row.item_code,
					branch: frm.doc.branch,
				},
				callback(r) {
					const rate = (r.message || {}).rate || 0;
					if (rate > 0 && !row.custom_fp_price) {
						frappe.model.set_value(cdt, cdn, "custom_fp_price", rate);
						frm.fields_dict.items.grid.refresh();
					}
				},
			});
		},

		refresh_all_rows(frm) {
			(frm.doc.items || []).forEach((row) => {
				helpers.refresh_row(frm, row.doctype, row.name);
			});
		},
	};

	frappe.ui.form.on(doctype, {
		branch(frm) {
			helpers.refresh_all_rows(frm);
		},
	});

	frappe.ui.form.on(child_doctype, {
		item_code(frm, cdt, cdn) {
			helpers.refresh_row(frm, cdt, cdn);
		},
	});
}

aimatic_register_foodpanda_price_prefill("Purchase Order", "Purchase Order Item");
aimatic_register_foodpanda_price_prefill("Purchase Receipt", "Purchase Receipt Item");
aimatic_register_foodpanda_price_prefill("Purchase Invoice", "Purchase Invoice Item");
