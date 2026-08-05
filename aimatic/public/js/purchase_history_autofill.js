// Live pre-save preview only. The real guarantee is the server-side
// before_validate hooks (aimatic.purchase_history_autofill.events) - this
// script pre-fills blank grid cells so KPO sees carried-over vendor terms
// before Save. Never touches qty/rate/custom_vendor_rate.

(function () {
	"use strict";

	if (typeof frappe === "undefined") {
		return;
	}

	const DEBOUNCE_MS = 250;

	function resolve_branch(frm) {
		return (
			frm.doc.branch ||
			frappe.defaults.get_user_default("Branch") ||
			(frappe.boot && frappe.boot.user && frappe.boot.user.branch) ||
			""
		);
	}

	function is_empty_value(value, fieldtype) {
		if (fieldtype === "Check") {
			return value === undefined || value === null || value === "";
		}
		return value === undefined || value === null || value === "" || value === 0 || value === 0.0;
	}

	function trigger_row_recalc(frm, cdt, cdn) {
		// Tax-calc client scripts listen to these input fields; firing one
		// after a history fill recomputes the read-only totals without a
		// full grid refresh (which fights the excel-grid UI).
		const row = locals[cdt] && locals[cdt][cdn];
		if (!row) {
			return;
		}
		const probe_fields = [
			"custom_gst_per",
			"custom_mrp",
			"custom_discount_per",
			"custom_trade_offer",
			"custom_gst_formula",
		];
		for (const fieldname of probe_fields) {
			if (row[fieldname] !== undefined && row[fieldname] !== null && row[fieldname] !== "") {
				frappe.model.trigger(cdt, fieldname, cdn);
				return;
			}
		}
	}

	function aimatic_register_history_autofill(doctype, child_doctype) {
		const state = { timer: null, request_id: 0 };

		const helpers = {
			refresh_row(frm, cdt, cdn) {
				helpers.refresh_rows(frm, [{ doctype: cdt, name: cdn }]);
			},

			refresh_all_rows(frm) {
				const rows = (frm.doc.items || [])
					.filter((row) => row.item_code)
					.map((row) => ({ doctype: row.doctype, name: row.name }));
				helpers.refresh_rows(frm, rows);
			},

			refresh_rows(frm, row_refs) {
				const supplier = frm.doc.supplier;
				const branch = resolve_branch(frm);
				if (!supplier || !branch || !row_refs.length) {
					return;
				}

				const item_codes = [];
				const refs_by_item = {};
				row_refs.forEach((ref) => {
					const row = locals[ref.doctype] && locals[ref.doctype][ref.name];
					if (row && row.item_code) {
						item_codes.push(row.item_code);
						(refs_by_item[row.item_code] ||= []).push(ref);
					}
				});
				if (!item_codes.length) {
					return;
				}

				clearTimeout(state.timer);
				state.timer = setTimeout(() => {
					const request_id = ++state.request_id;
					frappe.call({
						method: "aimatic.purchase_history_autofill.api.preview_items_history",
						args: {
							supplier,
							branch,
							item_codes,
							target_doctype: child_doctype,
						},
						callback(r) {
							if (request_id !== state.request_id) {
								return;
							}
							const payload = r.message || {};
							const history_map = payload.history || {};
							const fieldtypes = payload.fieldtypes || {};
							let any_changed = false;

							Object.keys(history_map).forEach((item_code) => {
								const refs = refs_by_item[item_code] || [];
								const history = history_map[item_code] || {};
								refs.forEach((ref) => {
									const row = locals[ref.doctype][ref.name];
									let row_changed = false;
									Object.keys(history).forEach((fieldname) => {
										const fieldtype = fieldtypes[fieldname];
										const current = row[fieldname];
										if (!is_empty_value(current, fieldtype)) {
											return;
										}
										frappe.model.set_value(
											ref.doctype,
											ref.name,
											fieldname,
											history[fieldname]
										);
										row_changed = true;
									});
									if (row_changed) {
										any_changed = true;
										trigger_row_recalc(frm, ref.doctype, ref.name);
									}
								});
							});

							if (any_changed) {
								frm.dirty();
							}
						},
					});
				}, DEBOUNCE_MS);
			},
		};

		frappe.ui.form.on(doctype, {
			onload(frm) {
				if (frm.doc.supplier && resolve_branch(frm)) {
					helpers.refresh_all_rows(frm);
				}
			},
			supplier(frm) {
				helpers.refresh_all_rows(frm);
			},
			branch(frm) {
				helpers.refresh_all_rows(frm);
			},
			items_add(frm, cdt, cdn) {
				setTimeout(() => helpers.refresh_row(frm, cdt, cdn), 0);
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
	aimatic_register_history_autofill("Purchase Invoice", "Purchase Invoice Item");
})();
