// "Map by Barcode" links Foodpanda's existing catalog to Items using barcode
// (never Item Code). "Sync Full Catalog" then PUTs price/stock for mapped
// rows only. Day-to-day availability still follows Bin updates via events.py.

frappe.ui.form.on("Foodpanda Outlet", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.catalog_sync_enabled) {
			return;
		}
		frm.add_custom_button(__("Map by Barcode"), () => {
			aimatic_map_foodpanda_catalog_by_barcode(frm);
		});
		frm.add_custom_button(__("Sync Full Catalog"), () => {
			aimatic_start_foodpanda_bulk_export(frm);
		});
	},
});

function aimatic_map_foodpanda_catalog_by_barcode(frm) {
	frappe.call({
		method: "aimatic.foodpanda_integration.api.map_foodpanda_catalog_by_barcode",
		args: { outlet: frm.doc.name },
		freeze: true,
		freeze_message: __("Matching Foodpanda catalog by barcode..."),
		callback(r) {
			const result = r.message || {};
			const file_url = result.file_url;
			let message =
				__(
					"Remote: {0}. Mapped: {1} (updated {2}). No barcode: {3}. Unmatched: {4}. Ambiguous: {5}.",
					[
						result.remote_total || 0,
						result.mapped || 0,
						result.updated || 0,
						result.skipped_no_barcode || 0,
						result.skipped_unmatched || 0,
						result.skipped_ambiguous || 0,
					]
				);
			if (file_url) {
				message +=
					`<p><a href="${frappe.utils.escape_html(file_url)}" target="_blank" rel="noopener">` +
					__("Download matching Excel") +
					"</a></p>";
				window.open(file_url);
			}
			frappe.msgprint({
				title: __("Barcode Mapping"),
				indicator: result.mapped ? "green" : "orange",
				message,
			});
			frm.reload_doc();
		},
	});
}

function aimatic_start_foodpanda_bulk_export(frm) {
	frappe.call({
		method: "aimatic.foodpanda_integration.api.start_catalog_bulk_export",
		args: { outlet: frm.doc.name },
		freeze: true,
		freeze_message: __("Starting catalog export..."),
		callback(r) {
			const status = r.message;
			if (!status || !status.job_id) {
				frappe.show_alert({ message: __("Could not start the export"), indicator: "red" });
				return;
			}
			aimatic_poll_foodpanda_bulk_export(status.job_id);
		},
	});
}

function aimatic_poll_foodpanda_bulk_export(job_id) {
	const dialog = new frappe.ui.Dialog({
		title: __("Foodpanda Catalog Export"),
		fields: [{ fieldname: "progress_html", fieldtype: "HTML" }],
	});
	dialog.show();

	const render = (status) => {
		const synced = status.synced || 0;
		const failed = status.failed || 0;
		const total = status.total || 0;
		dialog.fields_dict.progress_html.$wrapper.html(
			`<p>${__("Status")}: ${frappe.utils.escape_html(status.status || "")}</p>` +
				`<p>${__("Synced")}: ${synced} / ${total}</p>` +
				(failed ? `<p style="color:var(--red-500)">${__("Failed")}: ${failed}</p>` : "")
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
					frappe.show_alert({ message: __("Export job expired"), indicator: "orange" });
					return;
				}
				render(status);
				if (status.status === "done") {
					clearInterval(timer);
					frappe.show_alert({
						message: __("Catalog export finished: {0} synced, {1} failed", [status.synced || 0, status.failed || 0]),
						indicator: status.failed ? "orange" : "green",
					});
				}
			},
		});
	};

	poll();
	timer = setInterval(poll, 3000);
	dialog.onhide = () => clearInterval(timer);
}
