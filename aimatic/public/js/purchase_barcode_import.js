/**
 * Resolve Items from Barcodes — fills item_code for rows where the Barcode
 * column was filled via grid Upload/paste without triggering ERPNext's
 * single-scan barcode handler.
 *
 * Loaded via doctype_js and/or Client Script. Guard prevents duplicate buttons
 * when both paths are active (e.g. after a later gunicorn reload).
 */
(function () {
	"use strict";

	if (typeof frappe === "undefined") {
		return;
	}
	if (frappe.__aimatic_purchase_barcode_import) {
		return;
	}
	frappe.__aimatic_purchase_barcode_import = true;

	const METHOD = "aimatic.purchase_barcode_import.api.resolve_barcodes_for_purchase";

	function button_label() {
		return __("Resolve Items from Barcodes");
	}

	function row_needs_resolve(row) {
		const barcode = (row.barcode || "").toString().trim();
		if (!barcode) {
			return false;
		}
		return !(row.item_code || "").toString().trim();
	}

	function collect_pending_rows(frm) {
		return (frm.doc.items || []).filter(row_needs_resolve);
	}

	function format_summary(result, applied_count) {
		const unmatched = result.unmatched || [];
		const ambiguous = result.ambiguous || [];
		const disabled = result.disabled || [];
		const lines = [__("Resolved {0} row(s).", [applied_count])];

		if (unmatched.length) {
			lines.push(
				__("Unmatched ({0}): {1}", [
					unmatched.length,
					unmatched.slice(0, 20).join(", ") + (unmatched.length > 20 ? "…" : ""),
				])
			);
		}
		if (ambiguous.length) {
			const sample = ambiguous
				.slice(0, 10)
				.map((row) => `${row.barcode} → ${(row.item_codes || []).join("/")}`)
				.join("; ");
			lines.push(
				__("Ambiguous ({0}): {1}", [
					ambiguous.length,
					sample + (ambiguous.length > 10 ? "…" : ""),
				])
			);
		}
		if (disabled.length) {
			const sample = disabled
				.slice(0, 10)
				.map((row) => `${row.barcode} (${row.item_code})`)
				.join(", ");
			lines.push(
				__("Disabled items ({0}): {1}", [
					disabled.length,
					sample + (disabled.length > 10 ? "…" : ""),
				])
			);
		}
		return lines.join("<br>");
	}

	async function apply_match(frm, row, item_code, barcode) {
		const cdt = row.doctype;
		const cdn = row.name;
		const prior_qty = flt(row.qty);

		await frappe.model.set_value(cdt, cdn, "item_code", item_code);

		// process_item_selection clears barcode; restore without re-triggering
		// the single-scan barcode→item path (would force qty=1).
		frappe.flags.trigger_from_barcode_scanner = true;
		try {
			await frappe.model.set_value(cdt, cdn, "barcode", barcode);
		} finally {
			frappe.flags.trigger_from_barcode_scanner = false;
		}

		const current = locals[cdt] && locals[cdt][cdn];
		if (current && !flt(current.qty) && !prior_qty) {
			await frappe.model.set_value(cdt, cdn, "qty", 1);
		} else if (current && prior_qty && flt(current.qty) !== prior_qty) {
			await frappe.model.set_value(cdt, cdn, "qty", prior_qty);
		}
	}

	async function resolve_from_barcode_column(frm) {
		const pending = collect_pending_rows(frm);
		if (!pending.length) {
			frappe.msgprint({
				message: __("No rows with a barcode and empty Item Code."),
				title: button_label(),
				indicator: "blue",
			});
			return;
		}

		const barcodes = pending.map((row) => (row.barcode || "").toString().trim());

		const r = await frappe.call({
			method: METHOD,
			args: { barcodes },
			freeze: true,
			freeze_message: __("Resolving barcodes…"),
		});

		const result = r.message || {};
		const matched = result.matched || {};
		let applied = 0;

		for (const row of pending) {
			const barcode = (row.barcode || "").toString().trim();
			const item_code = matched[barcode];
			if (!item_code) {
				continue;
			}
			await apply_match(frm, row, item_code, barcode);
			applied += 1;
		}

		frm.refresh_field("items");
		frappe.msgprint({
			message: format_summary(result, applied),
			title: button_label(),
			indicator: applied ? "green" : "orange",
		});
	}

	["Purchase Order", "Purchase Receipt"].forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				if (frm.doc.docstatus !== 0) {
					return;
				}
				frm.add_custom_button(button_label(), () => {
					resolve_from_barcode_column(frm);
				});
			},
		});
	});
})();
