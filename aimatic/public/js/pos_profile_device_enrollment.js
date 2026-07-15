frappe.ui.form.on("POS Profile", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		const allowed_roles = ["POS Supervisor", "System Manager"];
		if (!frappe.user_roles.some((role) => allowed_roles.includes(role))) {
			return;
		}

		frm.add_custom_button(__("Generate Device Enrollment Code"), () => {
			frappe.call({
				method: "aimatic.offline_pos.api.generate_device_enrollment_code",
				args: { pos_profile: frm.doc.name },
				freeze: true,
				freeze_message: __("Generating enrollment code..."),
				callback: (r) => {
					if (!r.message) {
						return;
					}
					show_device_enrollment_dialog(r.message);
				},
			});
		});
	},
});

function show_device_enrollment_dialog(payload) {
	const enrollment_value =
		payload.enrollment_value || JSON.stringify({ url: payload.url, token: payload.token });
	const qr_code = payload.qr_code || "";
	const dialog = new frappe.ui.Dialog({
		title: __("Device Enrollment Code — {0}", [payload.pos_profile]),
		fields: [
			{
				fieldname: "notice",
				fieldtype: "HTML",
				options: `<p>${__(
					"On the Android device, choose Scan enrollment QR. This code is valid for 10 minutes, can be used once, and expires at {0}.",
					[frappe.datetime.str_to_user(payload.expires_at)]
				)}</p>${
					qr_code
						? `<div style="display:flex;justify-content:center;padding:12px"><img src="${qr_code}" alt="${__(
								"Device enrollment QR code"
							)}" style="width:min(320px,100%);height:auto;background:#fff;padding:12px;border:1px solid var(--border-color);border-radius:8px"></div>`
						: ""
				}`,
			},
			{
				fieldname: "enrollment_value",
				fieldtype: "Small Text",
				label: __("Enrollment value"),
				default: enrollment_value,
				read_only: 1,
			},
		],
		primary_action_label: __("Copy enrollment value"),
		primary_action: () => {
			frappe.utils.copy_to_clipboard(enrollment_value);
		},
	});
	dialog.show();
}
