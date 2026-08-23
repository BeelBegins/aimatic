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

	// Fields that must never be copied out of a get_item_details response onto
	// a child row (mirrors frappe.ui.form.Form.call's own child-update guard).
	function std_field_list() {
		return ["doctype"]
			.concat(frappe.model.std_fields_list)
			.concat(frappe.model.child_table_field_list);
	}

	function build_get_item_details_ctx(frm, row, item_code, barcode) {
		return {
			item_code: item_code,
			barcode: barcode,
			serial_no: row.serial_no,
			batch_no: row.batch_no,
			set_warehouse: frm.doc.set_warehouse,
			warehouse: row.warehouse,
			customer: frm.doc.customer || frm.doc.party_name,
			quotation_to: frm.doc.quotation_to,
			supplier: frm.doc.supplier,
			currency: frm.doc.currency,
			is_internal_supplier: frm.doc.is_internal_supplier,
			is_internal_customer: frm.doc.is_internal_customer,
			update_stock: cint(frm.doc.update_stock),
			conversion_rate: frm.doc.conversion_rate,
			price_list: frm.doc.selling_price_list || frm.doc.buying_price_list,
			price_list_currency: frm.doc.price_list_currency,
			plc_conversion_rate: frm.doc.plc_conversion_rate,
			company: frm.doc.company,
			order_type: frm.doc.order_type,
			is_pos: cint(frm.doc.is_pos),
			is_return: cint(frm.doc.is_return),
			is_subcontracted: frm.doc.is_subcontracted,
			ignore_pricing_rule: frm.doc.ignore_pricing_rule,
			doctype: frm.doc.doctype,
			name: frm.doc.name,
			project: row.project || frm.doc.project,
			qty: row.qty || 1,
			net_rate: row.rate,
			base_net_rate: row.base_net_rate,
			stock_qty: row.stock_qty,
			conversion_factor: row.conversion_factor,
			weight_per_unit: row.weight_per_unit,
			uom: row.uom,
			weight_uom: row.weight_uom,
			manufacturer: row.manufacturer,
			stock_uom: row.stock_uom,
			pos_profile: cint(frm.doc.is_pos) ? frm.doc.pos_profile : "",
			cost_center: row.cost_center,
			tax_category: frm.doc.tax_category,
			item_tax_template: row.item_tax_template,
			child_doctype: row.doctype,
			child_docname: row.name,
			is_old_subcontracting_flow: frm.doc.is_old_subcontracting_flow,
			use_serial_batch_fields: row.use_serial_batch_fields,
			serial_and_batch_bundle: row.serial_and_batch_bundle,
		};
	}

	// Populate one row using the same whitelisted endpoint ERPNext's item_code
	// handler uses (erpnext.stock.get_item_details.get_item_details), but call
	// it directly instead of going through frappe.model.set_value("item_code").
	// That avoids the per-row frm.refresh_fields() (full-form redraw) ERPNext
	// triggers on every item_code change — the O(n²) work that hangs large
	// documents. Field values are copied onto the row locally; the grid and
	// totals are refreshed once, after all rows are done.
	async function apply_match(frm, row, item_code, barcode) {
		row.weight_per_unit = 0;
		row.weight_uom = "";
		row.uom = null;
		row.conversion_factor = 0;
		row.pricing_rules = "";

		const r = await frappe.call({
			method: "erpnext.stock.get_item_details.get_item_details",
			args: {
				doc: frm.doc,
				ctx: build_get_item_details_ctx(frm, row, item_code, barcode),
			},
		});

		const details = r.message;
		if (!details) {
			return false;
		}

		const skip = std_field_list();
		for (const key in details) {
			if (skip.indexOf(key) === -1) {
				row[key] = details[key];
			}
		}
		row.item_code = item_code;
		row.barcode = barcode;

		if (details.has_batch_no || details.has_serial_no) {
			return { needs_tracking: true };
		}
		return true;
	}

	async function run_with_concurrency(items, limit, worker) {
		let cursor = 0;
		async function lane() {
			while (cursor < items.length) {
				const current = items[cursor];
				cursor += 1;
				await worker(current);
			}
		}
		const lanes = Array.from({ length: Math.min(limit, items.length) }, lane);
		await Promise.all(lanes);
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
		const needs_tracking = [];

		frappe.dom.freeze(__("Applying resolved items…"));
		try {
			await run_with_concurrency(pending, 8, async (row) => {
				const barcode = (row.barcode || "").toString().trim();
				const item_code = matched[barcode];
				if (!item_code) {
					return;
				}
				const outcome = await apply_match(frm, row, item_code, barcode);
				applied += 1;
				if (outcome && outcome.needs_tracking) {
					needs_tracking.push(item_code);
				}
				if (typeof frm.cscript.add_taxes_from_item_tax_template === "function") {
					frm.cscript.add_taxes_from_item_tax_template(row.item_tax_rate);
				}
			});
		} finally {
			frappe.dom.unfreeze();
		}

		frm.refresh_field("items");
		frm.refresh_field("taxes");
		if (typeof frm.cscript.calculate_taxes_and_totals === "function") {
			frm.cscript.calculate_taxes_and_totals();
		}

		let summary = format_summary(result, applied);
		if (needs_tracking.length) {
			summary +=
				"<br>" +
				__("Batch/Serial No required ({0}): {1}", [
					needs_tracking.length,
					needs_tracking.slice(0, 10).join(", ") + (needs_tracking.length > 10 ? "…" : ""),
				]);
		}

		frappe.msgprint({
			message: summary,
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
