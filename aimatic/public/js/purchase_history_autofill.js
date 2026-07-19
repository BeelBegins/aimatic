// Live pre-save preview only. The real guarantee is the server-side
// before_validate hooks (aimatic.purchase_history_autofill.events) - this
// script just pre-fills blank grid cells so the user sees carried-over
// values before hitting Save, for the same Supplier + Branch + Item
// history match. Never touches qty/rate - the server never returns those
// fields either (see purchase_history_autofill/utils.py). Shared between
// the Purchase Receipt and Purchase Order forms.

function aimatic_register_history_autofill(doctype, child_doctype) {
	const helpers = {
		refresh_row(frm, cdt, cdn) {
			const row = locals[cdt][cdn];
			if (!row || !frm.doc.supplier || !frm.doc.branch || !row.item_code) {
				return;
			}
			frappe.call({
				method: "aimatic.purchase_history_autofill.api.preview_item_history",
				args: {
					supplier: frm.doc.supplier,
					branch: frm.doc.branch,
					item_code: row.item_code,
					target_doctype: child_doctype,
				},
				callback(r) {
					const history = r.message || {};
					let dirty = false;
					Object.keys(history).forEach((fieldname) => {
						const current = row[fieldname];
						if (current === undefined || current === null || current === "" || current === 0) {
							frappe.model.set_value(cdt, cdn, fieldname, history[fieldname]);
							dirty = true;
						}
					});
					if (dirty) {
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
		supplier(frm) {
			helpers.refresh_all_rows(frm);
		},
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

aimatic_register_history_autofill("Purchase Receipt", "Purchase Receipt Item");
aimatic_register_history_autofill("Purchase Order", "Purchase Order Item");
