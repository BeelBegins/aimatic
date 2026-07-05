frappe.ui.form.on("AIM Label Print Job", {
	setup(frm) {
		frm.set_query("template", () => ({
			filters: {
				enabled: 1,
				label_type: frm.doc.label_type || ["is", "set"],
			},
		}));
	},

	refresh(frm) {
		render_totals(frm);

		if (!frm.doc.__islocal) {
			frm.add_custom_button(__("Add Item"), () => open_add_item_dialog(frm));

			frm.add_custom_button(__("Preview Labels"), () => print_job(frm, { preview: true }));

			const print_label = frm.doc.label_type === "Shelf Label" ? __("Print Shelf Labels") : __("Print Barcode Labels");
			frm.add_custom_button(print_label, () => print_job(frm, { preview: false }));

			frm.add_custom_button(__("Download PDF"), () => print_job(frm, { pdf: true }));

			frm.add_custom_button(__("Generate TSC TSPL"), () => generate_tspl(frm));
		}
	},

	label_type(frm) {
		frm.set_value("template", null);
	},

	validate(frm) {
		render_totals(frm);
	},
});

frappe.ui.form.on("AIM Label Print Job Item", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) return;

		frappe.call({
			method: "aimatic.label_printing.api.get_item_label_data",
			args: {
				item_code: row.item_code,
				label_type: frm.doc.label_type,
				uom: row.uom,
				batch_no: row.batch_no,
				price_list: frm.doc.price_list,
				company: frm.doc.company,
			},
			callback: (r) => {
				if (!r.message) return;
				Object.entries(r.message).forEach(([key, value]) => {
					if (value !== null && value !== undefined) {
						frappe.model.set_value(cdt, cdn, key, value);
					}
				});
				frm.refresh_field("items");
				render_totals(frm);
			},
		});
	},

	items_remove(frm) {
		render_totals(frm);
	},

	barcode_label_qty(frm) {
		render_totals(frm);
	},

	print_label(frm) {
		render_totals(frm);
	},
});

function render_totals(frm) {
	const rows = frm.doc.items || [];
	const row_count = rows.length;
	const missing_barcode = rows.filter((r) => !r.barcode).length;

	let label_count = 0;
	rows.forEach((r) => {
		if (!r.print_label) return;
		label_count += frm.doc.label_type === "Shelf Label" ? 1 : cint(r.barcode_label_qty);
	});

	const label_word = frm.doc.label_type === "Shelf Label" ? __("shelf labels") : __("barcode labels");

	let html = `<div class="text-muted small">
		${__("Item rows")}: <b>${row_count}</b> &nbsp;|&nbsp;
		${__("Total")} ${label_word}: <b>${label_count}</b>`;

	if (missing_barcode) {
		html += ` &nbsp;|&nbsp; <span class="text-danger">${__("Missing barcode")}: <b>${missing_barcode}</b></span>`;
	}

	html += "</div>";

	frm.dashboard.set_headline_alert(html);
}

function cint(v) {
	return parseInt(v) || 0;
}

function open_add_item_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Add Item"),
		fields: [
			{
				fieldname: "item_search",
				label: __("Search Item (code, name, or barcode)"),
				fieldtype: "Data",
				reqd: 1,
			},
			{
				fieldname: "item_results",
				fieldtype: "HTML",
			},
			{
				fieldname: "item_code",
				label: __("Selected Item"),
				fieldtype: "Link",
				options: "Item",
				read_only: 1,
			},
			{
				fieldname: "column_break_qty",
				fieldtype: "Column Break",
			},
			{
				fieldname: "batch_no",
				label: __("Batch No"),
				fieldtype: "Link",
				options: "Batch",
				depends_on: "eval:doc.item_code",
			},
			{
				fieldname: "qty",
				label: frm.doc.label_type === "Shelf Label" ? __("Shelf Labels (always 1)") : __("Barcode Label Qty"),
				fieldtype: "Int",
				default: 1,
				read_only: frm.doc.label_type === "Shelf Label",
			},
		],
		primary_action_label: __("Add"),
		primary_action: (values) => {
			if (!values.item_code) {
				frappe.msgprint(__("Please search and select an item first."));
				return;
			}
			add_item_to_job(frm, values);
			dialog.hide();
		},
	});

	let search_timeout = null;
	dialog.fields_dict.item_search.$input.on("input", () => {
		clearTimeout(search_timeout);
		const txt = dialog.get_value("item_search");
		search_timeout = setTimeout(() => render_item_results(dialog, frm, txt), 300);
	});

	dialog.show();
}

function render_item_results(dialog, frm, txt) {
	if (!txt || txt.length < 2) {
		dialog.fields_dict.item_results.$wrapper.html("");
		return;
	}

	frappe.call({
		method: "aimatic.label_printing.api.search_items",
		args: { txt },
		callback: (r) => {
			const items = r.message || [];
			if (!items.length) {
				dialog.fields_dict.item_results.$wrapper.html(`<div class="text-muted">${__("No items found")}</div>`);
				return;
			}

			const rows = items
				.map(
					(item) => `
					<div class="label-search-row" data-item-code="${frappe.utils.escape_html(item.item_code)}"
						style="padding:4px 8px; cursor:pointer; border-bottom:1px solid var(--border-color);">
						<b>${frappe.utils.escape_html(item.item_code)}</b> - ${frappe.utils.escape_html(item.item_name || "")}
						<span class="text-muted small"> (${frappe.utils.escape_html(item.item_group || "")})</span>
					</div>`
				)
				.join("");

			dialog.fields_dict.item_results.$wrapper.html(
				`<div style="max-height:200px; overflow-y:auto; border:1px solid var(--border-color);">${rows}</div>`
			);

			dialog.fields_dict.item_results.$wrapper.find(".label-search-row").on("click", function () {
				const item_code = $(this).attr("data-item-code");
				dialog.set_value("item_code", item_code);
				dialog.fields_dict.item_results.$wrapper.html("");
				dialog.set_value("item_search", item_code);
			});
		},
	});
}

function add_item_to_job(frm, values) {
	frappe.call({
		method: "aimatic.label_printing.api.get_item_label_data",
		args: {
			item_code: values.item_code,
			label_type: frm.doc.label_type,
			batch_no: values.batch_no,
			price_list: frm.doc.price_list,
			company: frm.doc.company,
		},
		freeze: true,
		callback: (r) => {
			if (!r.message) return;
			const row = frappe.model.add_child(frm.doc, "AIM Label Print Job Item", "items");
			Object.entries(r.message).forEach(([key, value]) => {
				row[key] = value;
			});
			if (frm.doc.label_type !== "Shelf Label" && values.qty) {
				row.barcode_label_qty = cint(values.qty);
			}
			frm.refresh_field("items");
			render_totals(frm);
			frappe.show_alert({
				message: __("{0} added", [values.item_code]),
				indicator: "green",
			});
		},
	});
}

function print_job(frm, { preview = false, pdf = false } = {}) {
	if (frm.is_dirty()) {
		frm.save().then(() => do_print(frm, { preview, pdf }));
	} else {
		do_print(frm, { preview, pdf });
	}
}

function do_print(frm, { preview, pdf }) {
	frappe.call({
		method: "aimatic.label_printing.api.check_before_print",
		args: { label_print_job: frm.doc.name },
		callback: (r) => {
			if (!r.message || !r.message.ok) return;

			const format_name = frm.doc.label_type === "Shelf Label" ? "AIM Shelf Label A4" : "AIM Barcode Label";

			if (pdf) {
				const pdf_url = frappe.urllib.get_full_url(
					`/api/method/frappe.utils.print_format.download_pdf?doctype=${encodeURIComponent(frm.doc.doctype)}` +
						`&name=${encodeURIComponent(frm.doc.name)}&format=${encodeURIComponent(format_name)}&no_letterhead=1`
				);
				window.open(pdf_url, "_blank");
			} else if (preview) {
				const preview_url = frappe.urllib.get_full_url(
					`/printview?doctype=${encodeURIComponent(frm.doc.doctype)}&name=${encodeURIComponent(frm.doc.name)}` +
						`&format=${encodeURIComponent(format_name)}&no_letterhead=1`
				);
				window.open(preview_url, "_blank");
			} else {
				frappe.utils.print(frm.doc.doctype, frm.doc.name, format_name, null, null);
			}

			if (!preview) {
				frappe.call({
					method: "aimatic.label_printing.api.mark_printed",
					args: { label_print_job: frm.doc.name },
					callback: () => frm.reload_doc(),
				});
			}
		},
	});
}

function generate_tspl(frm) {
	if (frm.is_dirty()) {
		frm.save().then(() => do_generate_tspl(frm));
	} else {
		do_generate_tspl(frm);
	}
}

function do_generate_tspl(frm) {
	frappe.call({
		method: "aimatic.label_printing.api.generate_tspl",
		args: { label_print_job: frm.doc.name },
		freeze: true,
		callback: (r) => {
			if (typeof r.message !== "string") return;

			const blob = new Blob([r.message], { type: "text/plain" });
			const url = window.URL.createObjectURL(blob);
			const link = document.createElement("a");
			link.href = url;
			link.download = `${frm.doc.name}.tspl.txt`;
			document.body.appendChild(link);
			link.click();
			link.remove();
			window.URL.revokeObjectURL(url);
		},
	});
}
