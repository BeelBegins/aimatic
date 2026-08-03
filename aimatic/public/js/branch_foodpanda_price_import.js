function show_foodpanda_import_result(result) {
	const lines = [
		`${__('Price List')}: <b>${frappe.utils.escape_html(result.price_list)}</b>`,
		`${__('Created')}: ${result.created}`,
		`${__('Updated')}: ${result.updated}`,
		`${__('Unchanged')}: ${result.unchanged}`,
		`${__('Skipped (Item disabled)')}: ${result.skipped_disabled}`,
		`${__('Unmatched (no barcode match)')}: ${result.unmatched}`,
	];

	if (result.unmatched_report) {
		lines.push(
			`<a href="${result.unmatched_report}" target="_blank">${__('Download unmatched rows report')}</a>`
		);
	}
	lines.push(
		`<a href="/app/foodpanda-price-import-log/${result.log}" target="_blank">${__('View import log')}</a>`
	);

	frappe.msgprint({
		title: __('Foodpanda Price Import Complete'),
		message: lines.join('<br>'),
		indicator: 'green',
	});
}

frappe.ui.form.on('Branch', {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__('Import Foodpanda Price List'), () => {
			const dialog = new frappe.ui.Dialog({
				title: __('Import Foodpanda Price List — {0}', [frm.doc.name]),
				fields: [
					{
						fieldname: 'file',
						fieldtype: 'Attach',
						label: __('Foodpanda Product Export (.xlsx)'),
						reqd: 1,
						description: __('The raw Products export downloaded from the Foodpanda vendor portal.'),
					},
				],
				primary_action_label: __('Import'),
				primary_action: (values) => {
					dialog.set_df_property('file', 'read_only', 1);
					frappe.call({
						method: 'aimatic.foodpanda_price_import.api.import_price_list',
						args: { branch: frm.doc.name, file_url: values.file },
						freeze: true,
						freeze_message: __('Importing Foodpanda prices...'),
						callback: (r) => {
							dialog.hide();
							show_foodpanda_import_result(r.message);
						},
					});
				},
			});
			dialog.show();
		}, __('Foodpanda'));
	},
});
