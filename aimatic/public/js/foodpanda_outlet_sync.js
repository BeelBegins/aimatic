// Foodpanda Outlet — simple catalog actions.
// Daily stock still follows Bin updates via events.py.

frappe.ui.form.on("Foodpanda Outlet", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Open Catalog Console"), () => {
			frappe.route_options = { outlet: frm.doc.name };
			frappe.set_route("foodpanda-catalog-console");
		});
		frm.add_custom_button(__("Upload Catalog via SFTP"), () => {
			frappe.confirm(
				__("Upload {0} Foodpanda catalog CSV via SFTP as {1}_{2}.csv?", [
					frm.doc.branch || frm.doc.name,
					frm.doc.sftp_filename_prefix || "catalog",
					frm.doc.vendor_id || "vendor_id",
				]),
				() => {
					frappe.call({
						method: "aimatic.price_export.foodpanda_sftp.upload_outlet_foodpanda_csv",
						args: { outlet: frm.doc.name },
						freeze: true,
						freeze_message: __("Uploading Foodpanda CSV via SFTP..."),
						callback: (r) => {
							const result = r.message || {};
							const failed = result.status === "Failed";
							frappe.msgprint({
								title: failed
									? __("Foodpanda SFTP Upload Failed")
									: __("Foodpanda SFTP Upload Complete"),
								message: [
									`${__("Status")}: <b>${frappe.utils.escape_html(result.status || "")}</b>`,
									`${__("Filename")}: ${frappe.utils.escape_html(result.filename || "")}`,
									`${__("Rows uploaded")}: ${result.row_count || 0}`,
									result.error
										? `${__("Error")}: ${frappe.utils.escape_html(result.error)}`
										: "",
								]
									.filter(Boolean)
									.join("<br>"),
								indicator: failed ? "red" : "green",
							});
							frm.reload_doc();
						},
					});
				}
			);
		}, __("SFTP"));

		if (!frm.doc.catalog_sync_enabled) {
			frm.dashboard.clear_headline();
			frm.dashboard.set_headline_alert(
				__("Catalog sync is disabled. Enable Catalog Sync to manage prices and stock."),
				"orange"
			);
			return;
		}

		aimatic_render_foodpanda_outlet_dashboard(frm);

		frm.add_custom_button(
			__("Update prices & stock"),
			() => {
				aimatic_start_foodpanda_bulk_push(frm);
			},
			__("Catalog")
		);
		frm.add_custom_button(
			__("Refresh product links"),
			() => {
				aimatic_start_foodpanda_import_and_map(frm);
			},
			__("Catalog")
		);
	},
});

function aimatic_render_foodpanda_outlet_dashboard(frm) {
	frappe.call({
		method: "aimatic.foodpanda_integration.api.get_outlet_catalog_dashboard",
		args: { outlet: frm.doc.name },
		callback(r) {
			const d = r.message || {};
			frm.dashboard.clear_headline();
			frm.dashboard.set_headline(
				d.summary_line ||
					__("{0} ready · {1} need attention · {2} not linked", [
						d.mapped_sku_count || 0,
						d.failed_count || 0,
						d.unmapped_remote_count || 0,
					])
			);
		},
	});
}

function aimatic_start_foodpanda_import_and_map(frm) {
	frappe.confirm(
		__(
			"Download the latest Foodpanda product list and link matching barcodes to ERPNext items?"
		),
		() => {
			frappe.call({
				method: "aimatic.foodpanda_integration.api.start_import_and_map",
				args: { outlet: frm.doc.name },
				freeze: true,
				freeze_message: __("Starting product link refresh..."),
				callback(r) {
					const result = r.message || {};
					frappe.msgprint({
						title: __("Refresh started"),
						indicator: "blue",
						message: __(
							"Job {0} is running in the background. Open Catalog Console and click Refresh status in a few minutes.",
							[result.job_id || ""]
						),
					});
					frm.reload_doc();
				},
			});
		}
	);
}

function aimatic_start_foodpanda_bulk_push(frm) {
	frappe.call({
		method: "aimatic.foodpanda_integration.api.get_outlet_catalog_dashboard",
		args: { outlet: frm.doc.name },
		callback(r) {
			const mapped = (r.message && r.message.mapped_sku_count) || 0;
			frappe.confirm(
				__("Send current prices and stock to Foodpanda for {0} linked products?", [
					mapped,
				]),
				() => {
					frappe.call({
						method: "aimatic.foodpanda_integration.api.start_catalog_bulk_push",
						args: { outlet: frm.doc.name },
						freeze: true,
						freeze_message: __("Starting price and stock update..."),
						callback(res) {
							const status = res.message;
							if (!status || !status.job_id) {
								frappe.show_alert({
									message: __("Could not start the update"),
									indicator: "red",
								});
								return;
							}
							aimatic_poll_foodpanda_bulk_push(status.job_id);
						},
						error() {
							frappe.show_alert({
								message: __("Could not start the update"),
								indicator: "red",
							});
						},
					});
				}
			);
		},
	});
}

function aimatic_poll_foodpanda_bulk_push(job_id) {
	const dialog = new frappe.ui.Dialog({
		title: __("Updating Foodpanda prices & stock"),
		fields: [{ fieldname: "progress_html", fieldtype: "HTML" }],
	});
	dialog.show();

	const render = (status) => {
		dialog.fields_dict.progress_html.$wrapper.html(
			`<p>${__("Status")}: ${frappe.utils.escape_html(status.status || "")}</p>` +
				`<p>${__("Done")}: ${status.synced || 0} / ${status.total || 0}</p>` +
				(status.failed
					? `<p style="color:var(--red-500)">${__("Failed")}: ${status.failed}</p>`
					: "")
		);
	};

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
					frappe.show_alert({ message: __("Update job expired"), indicator: "orange" });
					return;
				}
				render(status);
				if (status.status === "done") {
					clearInterval(timer);
					frappe.show_alert({
						message: __("Update complete: {0} ok, {1} failed", [
							status.synced || 0,
							status.failed || 0,
						]),
						indicator: status.failed ? "orange" : "green",
					});
				}
			},
			error() {
				clearInterval(timer);
				dialog.hide();
			},
		});
	};

	poll();
	timer = setInterval(poll, 3000);
	dialog.onhide = () => clearInterval(timer);
}
