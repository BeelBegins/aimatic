// "Sync Now" pushes one item and always shows the sanitized ERPNext/
// Foodpanda response. The server deliberately excludes credentials/tokens.

function show_foodpanda_sync_response(result) {
	const status = result.status || "Unknown";
	const indicator = status === "Failed" ? "red" : status === "Pending" ? "orange" : "green";
	const response_json = frappe.utils.escape_html(JSON.stringify(result, null, 2));

	frappe.msgprint({
		title: __("Foodpanda Sync Response"),
		indicator,
		message: `<pre style="max-height: 420px; overflow: auto; white-space: pre-wrap;">${response_json}</pre>`,
	});
}

frappe.ui.form.on("Foodpanda Product", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frm.add_custom_button(__("Sync Now"), () => {
			frappe.call({
				method: "aimatic.foodpanda_integration.api.sync_catalog_item",
				args: {
					item_code: frm.doc.item_code,
					outlet: frm.doc.outlet,
				},
				freeze: true,
				freeze_message: __("Syncing with Foodpanda..."),
				callback(r) {
					const result = r.message || { status: "Unknown", error: __("No response returned") };
					frm.reload_doc().then(() => show_foodpanda_sync_response(result));
				},
				error(r) {
					show_foodpanda_sync_response({
						status: "Failed",
						source: "ERPNext server",
						http_status: r.status,
						error: r.statusText || __("The server request failed"),
					});
				},
			});
		});
	},
});
