// "Sync Now" button: pushes this one item to Foodpanda immediately via
// catalog.sync_item, then reloads the form so sync_status/last_synced/
// last_error reflect the result.

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
					const status = (r.message || {}).status;
					if (status === "Failed") {
						frappe.show_alert({ message: __("Sync failed - check Last Error"), indicator: "red" });
					} else {
						frappe.show_alert({ message: __("Synced"), indicator: "green" });
					}
					frm.reload_doc();
				},
			});
		});
	},
});
