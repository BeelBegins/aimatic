(function () {
	function open_tax_id_search(listview) {
		const dialog = new frappe.ui.Dialog({
			title: __("Search Supplier by Tax ID / NTN"),
			fields: [
				{
					fieldname: "tax_id",
					fieldtype: "Data",
					label: __("Tax ID / NTN"),
					reqd: 1,
					description: __("Enter the complete Tax ID / NTN saved on the Supplier."),
				},
			],
			primary_action_label: __("Search"),
			primary_action(values) {
				const tax_id = (values.tax_id || "").trim();
				if (!tax_id) return;

				dialog.hide();
				listview.filter_area.remove("tax_id").then(() =>
					listview.filter_area.add([["Supplier", "tax_id", "=", tax_id]])
				);
			},
		});

		dialog.show();
		dialog.get_field("tax_id").set_focus();
	}

	const settings = frappe.listview_settings["Supplier"] || {};
	if (settings.aimatic_tax_id_search) return;

	const existing_onload = settings.onload;
	settings.onload = function (listview) {
		if (existing_onload) existing_onload(listview);
		listview.page.add_inner_button(__("Search by Tax ID / NTN"), () =>
			open_tax_id_search(listview)
		);
	};
	settings.aimatic_tax_id_search = true;
	frappe.listview_settings["Supplier"] = settings;
})();
