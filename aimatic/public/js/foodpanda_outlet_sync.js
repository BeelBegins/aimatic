// "Sync Full Catalog" button: enqueues catalog.start_bulk_export for this
// outlet and polls status until done. This bulk path exists for onboarding a
// branch's whole catalog to Foodpanda - day-to-day availability stays in
// sync automatically via the Bin stock-change hook (see
// foodpanda_integration/events.py); this button is not needed for that.

frappe.ui.form.on("Foodpanda Outlet", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.catalog_sync_enabled) {
			return;
		}
		frm.add_custom_button(__("Sync Full Catalog"), () => {
			aimatic_start_foodpanda_bulk_export(frm);
		});
	},
});

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
