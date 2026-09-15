frappe.ui.form.on("Principal Historical Allocation", {
	setup(frm) {
		frm.set_query("voucher_no", () => {
			const filters = { docstatus: 1 };
			if (frm.doc.company) {
				filters.company = frm.doc.company;
			}
			if (frm.doc.supplier) {
				filters.supplier = frm.doc.supplier;
			}
			if (frm.doc.voucher_type && frm.doc.voucher_type !== "Purchase Order") {
				filters.is_return = 0;
			}
			return { filters };
		});
		frm.set_query("principal", "allocations", () => {
			const supplier = frm.doc.supplier;
			if (!supplier) {
				return { filters: { name: ["in", []] } };
			}
			return {
				query: "aimatic.purchase_principal.get_principal_query",
				filters: { supplier },
			};
		});
	},

	refresh(frm) {
		if (frm.is_new()) {
			if (!frm.doc.company) {
				frm.set_value(
					"company",
					frappe.defaults.get_user_default("Company") || frappe.defaults.get_default("company")
				);
			}
		}
		if (!frm.is_new() || frm.doc.docstatus !== 0) {
			return;
		}
		frm.add_custom_button(__("Generate Drafts for Supplier"), () => {
			aimatic_open_pha_generate_dialog({
				supplier: frm.doc.supplier,
				company: frm.doc.company,
				voucher_type: frm.doc.voucher_type || "Purchase Invoice",
			});
		});
	},

	supplier(frm) {
		if (frm.doc.voucher_no) {
			frm.set_value("voucher_no", "");
		}
	},

	voucher_type(frm) {
		if (frm.doc.voucher_no) {
			frm.set_value("voucher_no", "");
		}
	},

	company(frm) {
		if (frm.doc.voucher_no) {
			frm.set_value("voucher_no", "");
		}
	},
});

frappe.listview_settings["Principal Historical Allocation"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Generate Drafts for Supplier"), () => {
			aimatic_open_pha_generate_dialog({});
		});
		listview.page.add_inner_button(__("Auto-Apply Principals (no review)"), () => {
			frappe.confirm(
				__(
					"Auto-apply Principals with no human review?<br><br>" +
						"1) Seed supplier allow-lists from trailing (BRAND) names<br>" +
						"2) Tag blank PRs/PIs (sole / name / items / majority)<br>" +
						"3) Copy PR → PI when unique; no PR stays blank<br>" +
						"4) Map Items from unique evidence<br><br>" +
						"Mixed multi-brand docs stay blank. Writes submitted docs."
				),
				() => {
					frappe.call({
						method: "aimatic.principal_allocation_automation.auto_apply_principals",
						args: {
							company:
								frappe.defaults.get_user_default("Company") ||
								frappe.defaults.get_default("company"),
							dry_run: 0,
							update_items: 1,
							aggressive: 1,
							enqueue: 0,
						},
						freeze: true,
						freeze_message: __("Auto-applying Principals…"),
						callback(r) {
							const result = r.message || {};
							const seeded = result.supplier_principals_seeded || 0;
							const prs = result.purchase_receipts_updated || 0;
							const pis = result.purchase_invoices_updated || 0;
							const items = result.items_updated || 0;
							const changed = seeded + prs + pis + items;
							frappe.msgprint({
								title: changed
									? __("Principal Auto-Apply")
									: __("Already up to date"),
								message: changed
									? __(
											"{0}<br><br>Allow-lists seeded: <b>{1}</b><br>PRs: <b>{2}</b><br>PIs: <b>{3}</b><br>Items: <b>{4}</b>",
											[
												frappe.utils.escape_html(result.message || ""),
												seeded,
												prs,
												pis,
												items,
											]
									  )
									: __(
											"Nothing new to write — Principals were already auto-applied.<br><br>" +
												"Open <b>Purchase by Supplier Principal</b> (hard-refresh Desk first).<br>" +
												"Blank rows left are internal branches, no (BRAND) match, or mixed multi-brand docs."
									  ),
								indicator: changed ? "green" : "blue",
							});
							listview.refresh();
						},
					});
				}
			);
		});
	},
};

function aimatic_open_pha_generate_dialog(defaults) {
	const dialog = new frappe.ui.Dialog({
		title: __("Generate Historical Allocation Drafts"),
		fields: [
			{
				fieldname: "company",
				label: __("Company"),
				fieldtype: "Link",
				options: "Company",
				reqd: 1,
				default:
					defaults.company ||
					frappe.defaults.get_user_default("Company") ||
					frappe.defaults.get_default("company"),
			},
			{
				fieldname: "supplier",
				label: __("Supplier / Vendor"),
				fieldtype: "Link",
				options: "Supplier",
				reqd: 1,
				default: defaults.supplier,
			},
			{
				fieldname: "voucher_type",
				label: __("Voucher Type"),
				fieldtype: "Select",
				options: "Purchase Invoice\nPurchase Receipt\nPurchase Order",
				reqd: 1,
				default: defaults.voucher_type || "Purchase Invoice",
			},
			{
				fieldname: "from_date",
				label: __("From Date"),
				fieldtype: "Date",
			},
			{
				fieldname: "to_date",
				label: __("To Date"),
				fieldtype: "Date",
			},
			{
				fieldname: "limit",
				label: __("Max Vouchers"),
				fieldtype: "Int",
				default: 200,
			},
			{
				fieldname: "preview_html",
				fieldtype: "HTML",
			},
		],
		primary_action_label: __("Preview"),
		primary_action(values) {
			aimatic_preview_pha_drafts(dialog, values);
		},
	});
	dialog.set_secondary_action_label(__("Create Drafts"));
	dialog.set_secondary_action(() => {
		const values = dialog.get_values();
		if (!values) {
			return;
		}
		frappe.confirm(
			__(
				"Create draft allocations for confident proposals only? Nothing is submitted. Review and submit what you accept; leave the rest as draft."
			),
			() => aimatic_create_pha_drafts(dialog, values)
		);
	});
	dialog.show();
}

function aimatic_preview_pha_drafts(dialog, values) {
	dialog.get_primary_btn().prop("disabled", true);
	frappe
		.xcall("aimatic.principal_allocation_automation.propose_supplier_historical_allocations", {
			supplier: values.supplier,
			company: values.company,
			voucher_type: values.voucher_type,
			from_date: values.from_date,
			to_date: values.to_date,
			limit: values.limit || 200,
		})
		.then((result) => {
			const ready = (result.proposals || []).filter((row) => row.status === "ready");
			const skipped = (result.proposals || []).filter((row) => row.status !== "ready");
			const readyRows = ready
				.slice(0, 25)
				.map(
					(row) =>
						`<tr><td>${frappe.utils.escape_html(row.voucher_no)}</td><td>${frappe.format(
							row.document_total,
							{ fieldtype: "Currency" }
						)}</td><td>${frappe.utils.escape_html(
							(row.allocations || []).map((a) => a.principal).join(", ")
						)}</td></tr>`
				)
				.join("");
			const skipSummary = {};
			skipped.forEach((row) => {
				const key = row.skip_reason || row.status;
				skipSummary[key] = (skipSummary[key] || 0) + 1;
			});
			const skipHtml = Object.keys(skipSummary)
				.map(
					(key) =>
						`<li>${frappe.utils.escape_html(key)}: <strong>${skipSummary[key]}</strong></li>`
				)
				.join("");
			dialog.fields_dict.preview_html.$wrapper.html(`
				<div class="text-muted" style="margin-bottom:8px;">
					${__("Ready drafts")}: <strong>${result.ready_count || 0}</strong> ·
					${__("Skipped")}: <strong>${result.skipped_count || 0}</strong>
				</div>
				${
					readyRows
						? `<div class="frappe-control"><table class="table table-bordered table-condensed">
							<thead><tr><th>${__("Voucher")}</th><th>${__("Total")}</th><th>${__("Principals")}</th></tr></thead>
							<tbody>${readyRows}</tbody>
						</table></div>`
						: `<p>${__("No confident proposals for this supplier/window.")}</p>`
				}
				${skipHtml ? `<p>${__("Skip reasons")}:</p><ul>${skipHtml}</ul>` : ""}
			`);
		})
		.finally(() => dialog.get_primary_btn().prop("disabled", false));
}

function aimatic_create_pha_drafts(dialog, values) {
	frappe
		.xcall("aimatic.principal_allocation_automation.create_supplier_historical_allocation_drafts", {
			supplier: values.supplier,
			company: values.company,
			voucher_type: values.voucher_type,
			from_date: values.from_date,
			to_date: values.to_date,
			limit: values.limit || 200,
		})
		.then((result) => {
			frappe.show_alert({
				message: __("Created {0} draft(s); skipped {1}.", [
					result.created_count || 0,
					result.skipped_count || 0,
				]),
				indicator: result.created_count ? "green" : "orange",
			});
			dialog.hide();
			frappe.set_route("List", "Principal Historical Allocation", {
				supplier: values.supplier,
				docstatus: 0,
			});
		});
}
