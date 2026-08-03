frappe.ui.form.on("Price List", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Search Barcode"), () => {
			const dialog = new frappe.ui.Dialog({
				title: __("Search this Price List by Barcode"),
				fields: [
					{
						fieldname: "barcode",
						fieldtype: "Data",
						label: __("Barcode"),
						reqd: 1,
						description: __("Scan or enter the complete barcode"),
					},
				],
				primary_action_label: __("Open Price"),
				primary_action(values) {
					const barcode = (values.barcode || "").trim();
					if (!barcode) return;

					dialog.disable_primary_action();
					frappe.call({
						method: "aimatic.barcode_search.resolve_item_codes",
						args: { barcode },
						callback(response) {
							const item_codes = response.message || [];
							if (!item_codes.length) {
								frappe.show_alert({
									message: __("No item found for barcode {0}", [barcode]),
									indicator: "orange",
								});
								dialog.enable_primary_action();
								dialog.get_field("barcode").set_focus();
								return;
							}

							dialog.hide();
							frappe.route_options = {
								price_list: frm.doc.name,
								item_code:
									item_codes.length === 1 ? item_codes[0] : ["in", item_codes],
							};
							frappe.set_route("Report", "Item Price");
						},
						error() {
							dialog.enable_primary_action();
						},
					});
				},
			});

			dialog.show();
			dialog.get_field("barcode").set_focus();
		});
	},
});
