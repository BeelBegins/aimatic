(function () {
	const doctypes = ["Item", "Item Price"];

	function open_barcode_search(listview) {
		const dialog = new frappe.ui.Dialog({
			title: __("Search by Barcode"),
			fields: [
				{
					fieldname: "barcode",
					fieldtype: "Data",
					label: __("Barcode"),
					reqd: 1,
					description: __("Scan or enter the complete barcode"),
				},
			],
			primary_action_label: __("Search"),
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
						const fieldname = listview.doctype === "Item" ? "name" : "item_code";
						const value = item_codes.length === 1 ? item_codes[0] : item_codes;

						listview.filter_area.remove(fieldname).then(() => {
							const operator = item_codes.length === 1 ? "=" : "in";
							return listview.filter_area.add([
								[listview.doctype, fieldname, operator, value],
							]);
						});
					},
					error() {
						dialog.enable_primary_action();
					},
				});
			},
		});

		dialog.show();
		dialog.get_field("barcode").set_focus();
	}

	function register(doctype) {
		const settings = frappe.listview_settings[doctype] || {};
		if (settings.aimatic_barcode_search) return;

		const existing_onload = settings.onload;
		settings.onload = function (listview) {
			if (existing_onload) existing_onload(listview);
			listview.page.add_inner_button(__("Search Barcode"), () =>
				open_barcode_search(listview)
			);
		};
		settings.aimatic_barcode_search = true;
		frappe.listview_settings[doctype] = settings;
	}

	doctypes.forEach(register);
})();
