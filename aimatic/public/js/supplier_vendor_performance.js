async function verify_fbr_ntn(frm) {
	const tax_id = frm.doc.tax_id || "";

	if (!tax_id.trim()) {
		frappe.msgprint(__("Please enter an NTN in Tax ID/NTN before verifying."));
		return;
	}

	const response = await frappe.call({
		method: "aimatic.api.fbr_taxpayer_verification.verify_supplier_ntn",
		args: { tax_id },
		freeze: true,
		freeze_message: __("Verifying FBR NTN..."),
	});

	const result = response.message || {};

	if (result.overwrite_fields && result.field_updates) {
		await frm.set_value(result.field_updates);
	}

	if (result.message) {
		frappe.show_alert(
			{
				message: __(result.message),
				indicator: result.indicator || "red",
			},
			7
		);
	}
}

frappe.ui.form.on("Supplier", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Verify FBR NTN"), async () => {
			try {
				await verify_fbr_ntn(frm);
			} catch (error) {
				frappe.show_alert(
					{
						message: __("Could not verify NTN due to a server or network error."),
						indicator: "red",
					},
					7
				);
			}
		});

		frm.add_custom_button(__("Vendor Performance"), () => {
			frappe.route_options = {
				supplier: frm.doc.name,
				company: frappe.defaults.get_user_default("Company") || undefined,
			};
			frappe.set_route("vendor-performance-console");
		});

		frm.add_custom_button(__("Vendor Stock Positions"), () => {
			frappe.route_options = {
				supplier: frm.doc.name,
				company: frappe.defaults.get_user_default("Company") || undefined,
			};
			frappe.set_route("vendor-stock-positions-console");
		});
	},
});
