function show_foodpanda_import_result(result) {
	const lines = [
		`${__("Price List")}: <b>${frappe.utils.escape_html(result.price_list)}</b>`,
		`${__("Created")}: ${result.created}`,
		`${__("Updated")}: ${result.updated}`,
		`${__("Unchanged")}: ${result.unchanged}`,
		`${__("Skipped (Item disabled)")}: ${result.skipped_disabled}`,
		`${__("Unmatched (no barcode match)")}: ${result.unmatched}`,
	];

	if (result.unmatched_report) {
		lines.push(
			`<a href="${result.unmatched_report}" target="_blank">${__(
				"Download unmatched rows report"
			)}</a>`
		);
	}
	lines.push(
		`<a href="/app/foodpanda-price-import-log/${result.log}" target="_blank">${__(
			"View import log"
		)}</a>`
	);

	frappe.msgprint({
		title: __("Foodpanda Price Import Complete"),
		message: lines.join("<br>"),
		indicator: "green",
	});
}

function show_foodpanda_sftp_upload_result(result) {
	const failed = result.status === "Failed";
	const lines = [
		`${__("Status")}: <b>${frappe.utils.escape_html(result.status || "")}</b>`,
		`${__("Filename")}: ${frappe.utils.escape_html(result.filename || "")}`,
		`${__("Rows uploaded")}: ${result.row_count || 0}`,
		`${__("Rows skipped")}: ${result.skipped_count || 0}`,
	];
	if (result.remote_path) {
		lines.push(`${__("Remote path")}: ${frappe.utils.escape_html(result.remote_path)}`);
	}
	if (result.error) {
		lines.push(`${__("Error")}: ${frappe.utils.escape_html(result.error)}`);
	}
	if (result.log) {
		lines.push(
			`<a href="/app/foodpanda-sftp-upload-log/${encodeURIComponent(
				result.log
			)}" target="_blank">${__("View SFTP upload log")}</a>`
		);
	}
	frappe.msgprint({
		title: failed ? __("Foodpanda SFTP Upload Failed") : __("Foodpanda SFTP Upload Complete"),
		message: lines.join("<br>"),
		indicator: failed ? "red" : "green",
	});
}

frappe.ui.form.on("Branch", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(
			__("Import Foodpanda Price List"),
			() => {
				const dialog = new frappe.ui.Dialog({
					title: __("Import Foodpanda Price List — {0}", [frm.doc.name]),
					fields: [
						{
							fieldname: "file",
							fieldtype: "Attach",
							label: __("Foodpanda Product Export (.xlsx)"),
							reqd: 1,
							description: __(
								"The raw Products export downloaded from the Foodpanda vendor portal."
							),
						},
					],
					primary_action_label: __("Import"),
					primary_action: (values) => {
						dialog.set_df_property("file", "read_only", 1);
						frappe.call({
							method: "aimatic.foodpanda_price_import.api.import_price_list",
							args: { branch: frm.doc.name, file_url: values.file },
							freeze: true,
							freeze_message: __("Importing Foodpanda prices..."),
							callback: (r) => {
								dialog.hide();
								show_foodpanda_import_result(r.message);
							},
						});
					},
				});
				dialog.show();
			},
			__("Foodpanda")
		);

		frm.add_custom_button(
			__("Upload Catalog via SFTP"),
			() => {
				frappe.confirm(
					__("Upload {0} Foodpanda catalog CSV via SFTP?", [frm.doc.name]),
					() => {
						frappe.call({
							method: "aimatic.price_export.foodpanda_sftp.upload_branch_foodpanda_csv",
							args: { branch: frm.doc.name },
							freeze: true,
							freeze_message: __("Uploading Foodpanda CSV via SFTP..."),
							callback: (r) => {
								show_foodpanda_sftp_upload_result(r.message || {});
								frm.reload_doc();
							},
						});
					}
				);
			},
			__("Foodpanda")
		);
	},
});
