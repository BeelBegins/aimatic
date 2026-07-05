frappe.ui.form.on("AIM Label Template", {
	refresh(frm) {
		frm.add_custom_button(
			__("View Base Template Code"),
			function () {
				frappe.call({
					method: "aimatic.label_printing.api.get_base_template_code",
					args: { label_type: frm.doc.label_type },
					callback: function (r) {
						if (!r.exc) {
							show_base_template_code(frm.doc.label_type, r.message || "");
						}
					},
				});
			},
			__("Advanced")
		);
	},
});

function show_base_template_code(label_type, code) {
	const dialog = new frappe.ui.Dialog({
		title: __("Base Template Code ({0})", [label_type]),
		size: "extra-large",
		fields: [
			{
				fieldname: "code",
				fieldtype: "Code",
				label: __("HTML / CSS / Jinja (read-only)"),
				read_only: 1,
			},
		],
	});
	dialog.set_value("code", code);
	dialog.show();
}
