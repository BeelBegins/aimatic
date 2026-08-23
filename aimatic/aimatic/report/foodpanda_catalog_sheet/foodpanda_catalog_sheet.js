// Copyright (c) 2026, Ai Matic and contributors
// For license information, please see license.txt

const aimatic_fp_catalog_pending_prices = new Map();
const aimatic_fp_catalog_pending_active = new Map();

function aimatic_fp_flush_grid(report) {
	return report.datatable?.cellmanager?.submitEditing?.() || Promise.resolve();
}

function aimatic_fp_save_prices(report) {
	return aimatic_fp_flush_grid(report).then(() => {
		const updates = Array.from(aimatic_fp_catalog_pending_prices.values());
		if (!updates.length) {
			frappe.show_alert({
				message: __("No Foodpanda price changes to save"),
				indicator: "blue",
			});
			return;
		}
		const outlet = report.get_filter_value("outlet");
		frappe.db.get_value("Foodpanda Outlet", outlet, "branch").then((r) => {
			const branch = r.message && r.message.branch;
			if (!branch) {
				frappe.msgprint(__("Outlet has no branch."));
				return;
			}
			frappe.confirm(
				__("Save {0} changed Foodpanda price(s) to this branch price list?", [
					updates.length,
				]),
				() => {
					frappe.call({
						method: "aimatic.price_export.api.save_foodpanda_grid_prices",
						args: { branch, updates },
						freeze: true,
						freeze_message: __("Saving Foodpanda prices..."),
						callback(res) {
							const result = res.message || {};
							aimatic_fp_catalog_pending_prices.clear();
							frappe.show_alert(
								{
									message: __("Saved: {0} updated, {1} created", [
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
	});
}

function aimatic_fp_apply_and_push(report) {
	const outlet = report.get_filter_value("outlet");
	if (!outlet) {
		frappe.msgprint(__("Select a Foodpanda Outlet first."));
		return;
	}
	return aimatic_fp_flush_grid(report).then(() => {
		const price_updates = Array.from(aimatic_fp_catalog_pending_prices.values());
		const active_updates = Array.from(aimatic_fp_catalog_pending_active.values());
		const match_status = report.get_filter_value("match_status") || "All";
		const link_match_ready = match_status === "Match Ready" || match_status === "All" ? 1 : 0;
		frappe.confirm(
			__(
				"Save prices and portal active, link Match Ready items, then push price/stock/active to Foodpanda?"
			),
			() => {
				frappe.call({
					method: "aimatic.foodpanda_integration.catalog_sheet.apply_catalog_sheet_updates",
					args: {
						outlet,
						price_updates,
						active_updates,
						link_match_ready,
						seed_remote_prices: link_match_ready,
						push: 1,
					},
					freeze: true,
					freeze_message: __("Applying Catalog Sheet updates..."),
					callback(r) {
						const result = r.message || {};
						aimatic_fp_catalog_pending_prices.clear();
						aimatic_fp_catalog_pending_active.clear();
						const prices = result.prices || {};
						frappe.show_alert(
							{
								message: __(
									"Linked {0}; prices +{1}/~{2}; active {3}; pushing {4} item(s)",
									[
										result.linked || 0,
										prices.created || 0,
										prices.updated || 0,
										result.active_updated || 0,
										result.push_item_count || 0,
									]
								),
								indicator: "green",
							},
							10
						);
						const job = result.job || {};
						if (job.job_id) {
							aimatic_fp_poll_push(job.job_id, report);
						} else {
							report.refresh();
						}
					},
				});
			}
		);
	});
}

function aimatic_fp_push_prices_stock(report) {
	const outlet = report.get_filter_value("outlet");
	if (!outlet) {
		frappe.msgprint(__("Select a Foodpanda Outlet first."));
		return;
	}
	frappe.confirm(__("Push current prices and stock to Foodpanda for linked products?"), () => {
		frappe.call({
			method: "aimatic.foodpanda_integration.api.start_catalog_bulk_push",
			args: { outlet },
			freeze: true,
			freeze_message: __("Starting price and stock push..."),
			callback(r) {
				const status = r.message || {};
				if (!status.job_id) {
					frappe.show_alert({
						message: __("Could not start the push"),
						indicator: "red",
					});
					return;
				}
				aimatic_fp_poll_push(status.job_id, report);
			},
		});
	});
}

function aimatic_fp_poll_push(job_id, report) {
	const dialog = new frappe.ui.Dialog({
		title: __("Updating Foodpanda prices & stock"),
		fields: [{ fieldname: "progress_html", fieldtype: "HTML" }],
	});
	dialog.show();
	let timer;
	const poll = () => {
		frappe.call({
			method: "aimatic.foodpanda_integration.api.get_catalog_bulk_export_status",
			args: { job_id },
			callback(r) {
				const status = r.message;
				if (!status) {
					clearInterval(timer);
					dialog.hide();
					return;
				}
				dialog.fields_dict.progress_html.$wrapper.html(
					`<p>${__("Status")}: ${frappe.utils.escape_html(status.status || "")}</p>` +
						`<p>${__("Done")}: ${status.synced || 0} / ${status.total || 0}</p>` +
						(status.failed
							? `<p style="color:var(--red-500)">${__("Failed")}: ${
									status.failed
							  }</p>`
							: "")
				);
				if (status.status === "done") {
					clearInterval(timer);
					frappe.show_alert({
						message: __("Push complete: {0} ok, {1} failed", [
							status.synced || 0,
							status.failed || 0,
						]),
						indicator: status.failed ? "orange" : "green",
					});
					report.refresh();
				}
			},
		});
	};
	poll();
	timer = setInterval(poll, 3000);
	dialog.onhide = () => clearInterval(timer);
}

function aimatic_fp_refresh_links(report) {
	const outlet = report.get_filter_value("outlet");
	if (!outlet) {
		frappe.msgprint(__("Select a Foodpanda Outlet first."));
		return;
	}
	frappe.confirm(__("Download the latest Foodpanda list and refresh barcode links?"), () => {
		frappe.call({
			method: "aimatic.foodpanda_integration.api.start_import_and_map",
			args: { outlet },
			freeze: true,
			freeze_message: __("Starting catalog refresh..."),
			callback(r) {
				const job = (r.message || {}).job_id || "";
				frappe.msgprint({
					title: __("Refresh started"),
					indicator: "blue",
					message: __(
						"Job {0} is running in the background. Refresh this report in a few minutes.",
						[job]
					),
				});
			},
		});
	});
}

frappe.query_reports["Foodpanda Catalog Sheet"] = {
	filters: [
		{
			fieldname: "outlet",
			label: __("Foodpanda Outlet"),
			fieldtype: "Link",
			options: "Foodpanda Outlet",
			reqd: 1,
			get_query() {
				return { filters: { catalog_sync_enabled: 1 } };
			},
		},
		{
			fieldname: "item_search",
			label: __("Search SKU / Item / Barcode"),
			fieldtype: "Data",
		},
		{
			fieldname: "match_status",
			label: __("Match Status"),
			fieldtype: "Select",
			options: "All\nLinked\nMatch Ready\nNot Linked\nNo Barcode\nAmbiguous",
			default: "All",
		},
		{
			fieldname: "sync_status",
			label: __("Sync Status"),
			fieldtype: "Select",
			options: "All\nPending\nSynced\nFailed",
			default: "All",
		},
		{
			fieldname: "price_status",
			label: __("Our Foodpanda Price"),
			fieldtype: "Select",
			options: "\nWith Price\nMissing Price",
		},
	],
	onload(report) {
		report.page.add_inner_button(__("Save Foodpanda Prices"), () =>
			aimatic_fp_save_prices(report)
		);
		report.page.add_inner_button(__("Apply & push to Foodpanda"), () =>
			aimatic_fp_apply_and_push(report)
		);
		report.page.add_inner_button(__("Update prices & stock"), () =>
			aimatic_fp_push_prices_stock(report)
		);
		report.page.add_inner_button(__("Refresh product links"), () =>
			aimatic_fp_refresh_links(report)
		);
		report.page.add_inner_button(__("Open Catalog Console"), () => {
			frappe.route_options = { outlet: report.get_filter_value("outlet") };
			frappe.set_route("foodpanda-catalog-console");
		});
	},
	after_refresh() {
		aimatic_fp_catalog_pending_prices.clear();
		aimatic_fp_catalog_pending_active.clear();
	},
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "match_status" && data) {
			const status = data.match_status || "";
			if (status.startsWith("Linked") || status === "Match Ready") {
				value = `<span style="color:var(--green-600)">${frappe.utils.escape_html(
					status
				)}</span>`;
			} else if (status === "Failed" || status === "Ambiguous") {
				value = `<span style="color:var(--orange-600)">${frappe.utils.escape_html(
					status
				)}</span>`;
			} else if (status === "Not Linked" || status === "No Barcode") {
				value = `<span style="color:var(--text-muted)">${frappe.utils.escape_html(
					status
				)}</span>`;
			}
		}
		if (column.fieldname === "sync_status" && data && data.sync_status === "Failed") {
			value = `<span style="color:var(--red-600)">${frappe.utils.escape_html(
				data.sync_status
			)}</span>`;
		}
		return value;
	},
	get_datatable_options(options) {
		options.pasteFromClipboard = false;
		options.getEditor = (colIndex, rowIndex, value, parent, column, row, data) => {
			const fieldname = column.id || column.fieldname || column.docfield?.fieldname;
			if (!data?.item_code) {
				return false;
			}
			if (fieldname === "foodpanda_price") {
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
						data.foodpanda_price = price;
						aimatic_fp_catalog_pending_prices.set(data.item_code, {
							item_code: data.item_code,
							price,
							old_price: data._loaded_foodpanda_price,
						});
					},
				};
			}
			if (fieldname === "portal_active") {
				const input = document.createElement("input");
				input.className = "dt-input";
				input.type = "checkbox";
				parent.appendChild(input);
				return {
					initValue() {
						input.checked = cint(data.portal_active) === 1;
						input.focus();
					},
					getValue() {
						return input.checked ? 1 : 0;
					},
					setValue(active) {
						active = cint(active) ? 1 : 0;
						data.portal_active = active;
						aimatic_fp_catalog_pending_active.set(
							data.foodpanda_sku || data.item_code,
							{
								item_code: data.item_code,
								foodpanda_sku: data.foodpanda_sku,
								portal_active: active,
							}
						);
					},
				};
			}
			return false;
		};
		return options;
	},
};
