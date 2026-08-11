// Copyright (c) 2026, Ai Matic and contributors
// For license information, please see license.txt

frappe.ui.form.on("Foodpanda Settings", {
	refresh(frm) {
		frm.dashboard.clear_headline();
		frm.dashboard.set_headline(
			__(
				"Site-wide Partner API credentials for synchronized Foodpanda outlets. Open the README for setup, webhooks, and next steps."
			)
		);

		frm.add_custom_button(__("Show README"), () =>
			aimatic_show_foodpanda_markdown_help(
				"aimatic.foodpanda_integration.api.get_foodpanda_settings_readme",
				__("Foodpanda Settings — README")
			)
		);
		frm.add_custom_button(__("Show webhook URLs"), () => aimatic_show_foodpanda_webhook_urls());
		frm.add_custom_button(__("Full sync guide"), () =>
			aimatic_show_foodpanda_markdown_help(
				"aimatic.foodpanda_integration.api.get_foodpanda_sync_guide",
				__("Foodpanda synchronized outlet")
			)
		);
		frm.add_custom_button(__("Open Catalog Console"), () => {
			frappe.set_route("foodpanda-catalog-console");
		});
		frm.add_custom_button(__("Foodpanda Catalog Sheet"), () => {
			frappe.set_route("query-report", "Foodpanda Catalog Sheet");
		});
	},
});

function aimatic_show_foodpanda_markdown_help(method, title) {
	frappe.call({
		method,
		freeze: true,
		freeze_message: __("Loading guide..."),
		callback(r) {
			const markdown = (r.message && r.message.markdown) || "";
			const dialog = new frappe.ui.Dialog({
				title,
				size: "extra-large",
				fields: [{ fieldname: "body", fieldtype: "HTML" }],
			});
			dialog.fields_dict.body.$wrapper.html(
				`<div class="markdown-preview" style="max-height:70vh;overflow:auto;padding:0.5rem 0.25rem">${frappe.markdown(
					markdown
				)}</div>`
			);
			dialog.show();
		},
	});
}

function aimatic_show_foodpanda_webhook_urls() {
	frappe.call({
		method: "aimatic.foodpanda_integration.api.get_foodpanda_webhook_urls",
		freeze: true,
		callback(r) {
			const urls = r.message || {};
			const order = frappe.utils.escape_html(urls.order_webhook_url || "");
			const assortment = frappe.utils.escape_html(urls.assortment_webhook_url || "");
			const auth = frappe.utils.escape_html(urls.authorization || "");
			frappe.msgprint({
				title: __("Foodpanda webhook URLs"),
				indicator: "blue",
				message:
					`<p>${__("Register these URLs in the Foodpanda Vendor Portal.")}</p>` +
					`<p><b>${__("Order webhook")}</b><br><code style="word-break:break-all">${order}</code></p>` +
					`<p><b>${__("Assortment / catalog job webhook")}</b><br><code style="word-break:break-all">${assortment}</code></p>` +
					`<p><b>${__("Authorization")}</b><br>${auth}</p>`,
			});
		},
	});
}
