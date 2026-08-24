// Copyright (c) 2026, Ai Matic and contributors
// For license information, please see license.txt

const aimatic_pending_foodpanda_prices = new Map();

function aimatic_flush_grid_editor(report) {
	return report.datatable?.cellmanager?.submitEditing?.() || Promise.resolve();
}

function aimatic_save_foodpanda_prices(report) {
	return aimatic_flush_grid_editor(report).then(() => {
		const updates = Array.from(aimatic_pending_foodpanda_prices.values());
		if (!updates.length) {
			frappe.show_alert({
				message: __("No Foodpanda price changes to save"),
				indicator: "blue",
			});
			return;
		}

		frappe.confirm(
			__("Save {0} changed Foodpanda price(s) to this branch's Foodpanda Price List?", [
				updates.length,
			]),
			() => {
				frappe.call({
					method: "aimatic.price_export.api.save_foodpanda_grid_prices",
					args: {
						branch: report.get_filter_value("branch"),
						updates,
					},
					freeze: true,
					freeze_message: __("Saving Foodpanda prices..."),
					callback(r) {
						const result = r.message || {};
						aimatic_pending_foodpanda_prices.clear();
						frappe.show_alert(
							{
								message: __("Foodpanda prices saved: {0} updated, {1} created", [
									result.updated || 0,
									result.created || 0,
								]),
								indicator: "green",
							},
							7
						);
						report.refresh();
					},
				});
			}
		);
	});
}

function aimatic_inactive_if_qty_lte(report) {
	const raw = report.get_filter_value("inactive_if_qty_lte");
	if (raw === null || raw === undefined || raw === "") {
		return null;
	}
	const threshold = flt(raw);
	return threshold < 0 ? null : threshold;
}

function aimatic_foodpanda_active(quantity, inactive_if_qty_lte) {
	quantity = Math.max(flt(quantity), 0);
	if (
		inactive_if_qty_lte === null ||
		inactive_if_qty_lte === undefined ||
		inactive_if_qty_lte === ""
	) {
		return quantity > 0 ? 1 : 0;
	}
	return quantity <= flt(inactive_if_qty_lte) ? 0 : 1;
}

function aimatic_download_foodpanda_csv(report) {
	return aimatic_flush_grid_editor(report).then(() => {
		if (aimatic_pending_foodpanda_prices.size) {
			frappe.msgprint(
				__("Save the pending Foodpanda price changes before downloading the CSV.")
			);
			return;
		}

		const inactive_if_qty_lte = aimatic_inactive_if_qty_lte(report);
		const output = [["sku", "barcode", "price", "active", "quantity"]];
		let skipped = 0;
		(report.data || []).forEach((row) => {
			const barcode = row.barcode1 || "";
			const price = flt(row.foodpanda_price);
			if (!barcode || price <= 0) {
				skipped += 1;
				return;
			}
			const quantity = Math.max(Math.floor(flt(row.available_qty)), 0);
			output.push([
				"",
				barcode,
				price,
				aimatic_foodpanda_active(quantity, inactive_if_qty_lte),
				quantity,
			]);
		});

		const branch = report.get_filter_value("branch") || "branch";
		const filename = `foodpanda-${frappe.scrub(branch)}-${frappe.datetime.get_today()}`;
		frappe.tools.downloadify(output, null, filename);
		frappe.show_alert(
			{
				message: __(
					"Foodpanda CSV prepared: {0} rows; {1} rows skipped for missing barcode/price",
					[output.length - 1, skipped]
				),
				indicator: skipped ? "orange" : "green",
			},
			8
		);
	});
}

function aimatic_show_sftp_upload_result(result) {
	const failed = result.status === "Failed";
	const lines = [
		`${__("Status")}: <b>${frappe.utils.escape_html(result.status || "")}</b>`,
		`${__("Filename")}: ${frappe.utils.escape_html(result.filename || "")}`,
		`${__("Rows uploaded")}: ${result.row_count || 0}`,
		`${__("Rows skipped")}: ${result.skipped_count || 0}`,
	];
	if (result.remote_path) {
		lines.push(`${__("Remote path")}: ${frappe.utils.escape_html(result.remote_path)}`);
	}
	if (result.error) {
		lines.push(`${__("Error")}: ${frappe.utils.escape_html(result.error)}`);
	}
	if (result.log) {
		lines.push(
			`<a href="/app/foodpanda-sftp-upload-log/${encodeURIComponent(
				result.log
			)}" target="_blank">${__("View SFTP upload log")}</a>`
		);
	}
	frappe.msgprint({
		title: failed ? __("Foodpanda SFTP Upload Failed") : __("Foodpanda SFTP Upload Complete"),
		message: lines.join("<br>"),
		indicator: failed ? "red" : "green",
	});
}

function aimatic_upload_foodpanda_csv_sftp(report) {
	return aimatic_flush_grid_editor(report).then(() => {
		if (aimatic_pending_foodpanda_prices.size) {
			frappe.msgprint(
				__("Save the pending Foodpanda price changes before uploading via SFTP.")
			);
			return;
		}

		const branch = report.get_filter_value("branch");
		if (!branch) {
			frappe.msgprint(__("Branch is required."));
			return;
		}

		const item_codes = (report.data || []).map((row) => row.item_code).filter(Boolean);
		frappe.confirm(
			__("Upload Foodpanda CSV for {0} ({1} filtered rows) via SFTP?", [
				branch,
				item_codes.length,
			]),
			() => {
				frappe.call({
					method: "aimatic.price_export.foodpanda_sftp.upload_branch_price_sheet_foodpanda_csv",
					args: {
						branch,
						item_codes,
						inactive_if_qty_lte: aimatic_inactive_if_qty_lte(report),
					},
					freeze: true,
					freeze_message: __("Uploading Foodpanda CSV via SFTP..."),
					callback(r) {
						aimatic_show_sftp_upload_result(r.message || {});
					},
				});
			}
		);
	});
}

function aimatic_show_excel_import_result(result) {
	const lines = [
		`${__("Price List")}: <b>${frappe.utils.escape_html(result.price_list || "")}</b>`,
		`${__("Accepted Excel rows")}: ${result.accepted_rows || 0}`,
		`${__("Created")}: ${result.created || 0}`,
		`${__("Updated")}: ${result.updated || 0}`,
		`${__("Unchanged")}: ${result.unchanged || 0}`,
		`${__("Skipped blank price")}: ${result.skipped_blank_price || 0}`,
		`${__("Skipped invalid price")}: ${result.skipped_bad_price || 0}`,
		`${__("Skipped invalid/disabled Item")}: ${result.skipped_invalid || 0}`,
	];
	if (result.log) {
		lines.push(
			`<a href="/app/foodpanda-price-import-log/${encodeURIComponent(
				result.log
			)}" target="_blank">${__("View import log")}</a>`
		);
	}
	frappe.msgprint({
		title: __("Branch Price Sheet Import Complete"),
		message: lines.join("<br>"),
		indicator: "green",
	});
}

function aimatic_import_updated_excel(report) {
	return aimatic_flush_grid_editor(report).then(() => {
		if (aimatic_pending_foodpanda_prices.size) {
			frappe.msgprint(
				__("Save the pending grid price changes before importing an Excel file.")
			);
			return;
		}

		const branch = report.get_filter_value("branch");
		const dialog = new frappe.ui.Dialog({
			title: __("Import Updated Branch Price Sheet — {0}", [branch]),
			fields: [
				{
					fieldname: "file",
					fieldtype: "Attach",
					label: __("Updated Branch Price Sheet (.xlsx)"),
					reqd: 1,
					description: __(
						"Upload the Excel file exported from this report. Only Item Code and Foodpanda Price (Editable) are imported."
					),
				},
			],
			primary_action_label: __("Import Foodpanda Prices"),
			primary_action(values) {
				frappe.confirm(
					__("Update {0}'s Foodpanda Price List from this Excel file?", [branch]),
					() => {
						dialog.get_primary_btn().prop("disabled", true);
						frappe.call({
							method: "aimatic.price_export.api.import_branch_price_sheet",
							args: { branch, file_url: values.file },
							freeze: true,
							freeze_message: __("Importing Foodpanda prices..."),
							callback(r) {
								dialog.hide();
								aimatic_show_excel_import_result(r.message || {});
								report.refresh();
							},
							always() {
								dialog.get_primary_btn().prop("disabled", false);
							},
						});
					}
				);
			},
		});
		dialog.show();
	});
}

frappe.query_reports["Branch Price Sheet"] = {
	filters: [
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Branch",
			default: frappe.defaults.get_user_default("Branch"),
			reqd: 1,
		},
		{
			fieldname: "item_search",
			label: __("Search Item / Barcode"),
			fieldtype: "Data",
		},
		{
			fieldname: "availability",
			label: __("Foodpanda Availability"),
			fieldtype: "Select",
			options: "\nIn Stock\nOut of Stock",
		},
		{
			fieldname: "foodpanda_price_status",
			label: __("Foodpanda Price Status"),
			fieldtype: "Select",
			options: "\nWith Price\nMissing Price",
			default: "With Price",
		},
		{
			fieldname: "inactive_if_qty_lte",
			label: __("Inactive if FP Qty ≤"),
			fieldtype: "Int",
			default: 3,
			description: __(
				"Foodpanda CSV/SFTP marks rows inactive when FP Available Qty is at or below this number. Clear the field to use stock-only active (qty > 0)."
			),
		},
	],
	onload(report) {
		report.page.add_inner_button(
			__("Save Foodpanda Prices"),
			() => aimatic_save_foodpanda_prices(report),
			__("Foodpanda")
		);
		report.page.add_inner_button(
			__("Import Updated Excel"),
			() => aimatic_import_updated_excel(report),
			__("Foodpanda")
		);
		report.page.add_inner_button(
			__("Download Foodpanda CSV"),
			() => aimatic_download_foodpanda_csv(report),
			__("Foodpanda")
		);
		report.page.add_inner_button(
			__("Upload Foodpanda CSV via SFTP"),
			() => aimatic_upload_foodpanda_csv_sftp(report),
			__("Foodpanda")
		);
	},
	after_refresh() {
		aimatic_pending_foodpanda_prices.clear();
	},
	get_datatable_options(options) {
		// Datatable's built-in multi-cell paste bypasses custom editors and can
		// make unsaved values appear in unrelated read-only columns. Keep it off;
		// Foodpanda prices are changed through the validated cell editor below.
		options.pasteFromClipboard = false;
		options.getEditor = (colIndex, rowIndex, value, parent, column, row, data) => {
			const fieldname = column.id || column.fieldname || column.docfield?.fieldname;
			if (fieldname !== "foodpanda_price" || !data?.item_code) {
				return false;
			}

			const input = document.createElement("input");
			input.className = "dt-input";
			input.type = "number";
			input.min = "0.01";
			input.step = "0.01";
			parent.appendChild(input);
			return {
				initValue() {
					input.value = flt(data.foodpanda_price) || "";
					input.focus();
					input.select();
				},
				getValue() {
					const price = flt(input.value);
					if (price <= 0) {
						frappe.show_alert({
							message: __("Foodpanda price must be greater than zero"),
							indicator: "red",
						});
						return flt(data.foodpanda_price);
					}
					return price;
				},
				setValue(price) {
					price = flt(price);
					const pending = aimatic_pending_foodpanda_prices.get(data.item_code);
					const oldPrice = pending ? pending.old_price : flt(data.foodpanda_price);
					data.foodpanda_price = price;
					if (price === oldPrice) {
						aimatic_pending_foodpanda_prices.delete(data.item_code);
					} else {
						aimatic_pending_foodpanda_prices.set(data.item_code, {
							item_code: data.item_code,
							old_price: oldPrice,
							price,
						});
					}
					input.value = price;
				},
			};
		};
		return options;
	},
};
