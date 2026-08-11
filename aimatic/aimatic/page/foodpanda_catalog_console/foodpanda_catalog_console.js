frappe.provide("aimatic");

frappe.pages["foodpanda-catalog-console"].on_page_load = function (wrapper) {
	frappe.require("/assets/aimatic/css/foodpanda_catalog_console.css", () => {
		new aimatic.FoodpandaCatalogConsole(wrapper);
	});
};

aimatic.FoodpandaCatalogConsole = class FoodpandaCatalogConsole {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("Foodpanda Catalog"),
			single_column: true,
		});
		this.dashboard = null;
		this.build_outlet_field();
		this.build_layout();
		this.load_dashboard();
	}

	build_outlet_field() {
		this.outlet_field = this.page.add_field({
			label: __("Branch / Outlet"),
			fieldname: "outlet",
			fieldtype: "Link",
			options: "Foodpanda Outlet",
			reqd: 1,
			change: () => this.load_dashboard(),
		});
		const preferred = (frappe.route_options && frappe.route_options.outlet) || null;
		frappe.route_options = null;
		if (preferred) {
			this.outlet_field.set_value(preferred);
			return;
		}
		frappe.db
			.get_list("Foodpanda Outlet", {
				filters: { catalog_sync_enabled: 1 },
				fields: ["name"],
				limit: 1,
				order_by: "name asc",
			})
			.then((rows) => {
				if (rows && rows[0] && !this.outlet_field.get_value()) {
					this.outlet_field.set_value(rows[0].name);
				}
			});
	}

	build_layout() {
		this.$body = $(`
			<div class="foodpanda-catalog-console">
				<div class="fp-hero"></div>
				<div class="fp-summary"></div>
				<div class="fp-primary"></div>
				<details class="fp-more">
					<summary>${__("More options")}</summary>
					<div class="fp-more-body">
						<div class="fp-secondary"></div>
						<div class="fp-links"></div>
						<div class="fp-meta"></div>
					</div>
				</details>
			</div>
		`).appendTo(this.page.body);

		this.$hero = this.$body.find(".fp-hero");
		this.$summary = this.$body.find(".fp-summary");
		this.$primary = this.$body.find(".fp-primary");
		this.$secondary = this.$body.find(".fp-secondary");
		this.$links = this.$body.find(".fp-links");
		this.$meta = this.$body.find(".fp-meta");
	}

	outlet() {
		return this.outlet_field.get_value();
	}

	load_dashboard() {
		const outlet = this.outlet();
		if (!outlet) {
			this.$hero.html(`<p class="text-muted">${__("Select a branch / outlet.")}</p>`);
			this.$summary.empty();
			this.$primary.empty();
			this.$secondary.empty();
			this.$links.empty();
			this.$meta.empty();
			return;
		}

		frappe.call({
			method: "aimatic.foodpanda_integration.api.get_outlet_catalog_dashboard",
			args: { outlet },
			freeze: true,
			freeze_message: __("Loading..."),
			callback: (r) => {
				this.dashboard = r.message || {};
				this.render();
			},
		});
	}

	render() {
		const d = this.dashboard || {};
		const fmt = (n) => frappe.format(n || 0, { fieldtype: "Int" });

		this.$hero.html(`
			<h3 class="fp-title">${frappe.utils.escape_html(d.outlet || "")}</h3>
			<p class="fp-help">${frappe.utils.escape_html(d.next_help || "")}</p>
		`);

		this.$summary.html(`
			<div class="fp-pills">
				<span class="fp-pill fp-pill-ok"><strong>${fmt(d.mapped_sku_count)}</strong> ${__("ready")}</span>
				<span class="fp-pill fp-pill-warn"><strong>${fmt(d.failed_count)}</strong> ${__("need attention")}</span>
				<span class="fp-pill"><strong>${fmt(d.unmapped_remote_count)}</strong> ${__("not in ERPNext")}</span>
			</div>
			<p class="text-muted fp-summary-line">${frappe.utils.escape_html(d.summary_line || "")}</p>
		`);

		this.$primary.empty();
		if (d.next_action === "enable") {
			this.add_button(this.$primary, d.next_label || __("Open outlet"), "primary", () => {
				frappe.set_route("Form", "Foodpanda Outlet", d.outlet);
			}, true);
		} else if (d.next_action === "refresh_links") {
			this.add_button(
				this.$primary,
				__("Refresh product links from Foodpanda"),
				"primary",
				() => this.refresh_links(),
				true
			);
		} else {
			this.add_button(
				this.$primary,
				__("Update prices & stock on Foodpanda"),
				"primary",
				() => this.push_catalog(),
				true
			);
			this.add_button(
				this.$primary,
				__("Refresh product links"),
				"default",
				() => this.refresh_links(),
				false
			);
		}
		this.add_button(this.$primary, __("Refresh status"), "default", () => this.load_dashboard(), false);

		this.$secondary.empty();
		this.add_button(this.$secondary, __("Open Catalog Sheet"), "default", () => {
			frappe.set_route("query-report", "Foodpanda Catalog Sheet", {
				outlet: d.outlet,
			});
		});
		this.add_button(this.$secondary, __("Open failed products"), "default", () => {
			frappe.set_route("List", "Foodpanda Product", {
				outlet: d.outlet,
				sync_status: "Failed",
			});
		});
		this.add_button(this.$secondary, __("Open linked products"), "default", () => {
			frappe.set_route("List", "Foodpanda Product", {
				outlet: d.outlet,
				foodpanda_product_id: ["is", "set"],
			});
		});
		if (d.latest_matching_report && d.latest_matching_report.file_url) {
			this.add_button(this.$secondary, __("Download matching report"), "default", () => {
				window.open(d.latest_matching_report.file_url, "_blank");
			});
		}

		this.$links.empty();
		this.add_link(__("Outlet settings"), () => frappe.set_route("Form", "Foodpanda Outlet", d.outlet));
		this.add_link(__("Catalog jobs"), () =>
			frappe.set_route("List", "Foodpanda Catalog Job", { outlet: d.outlet })
		);

		const exportJob = d.latest_export_job || {};
		this.$meta.html(`
			<div>${__("Last Foodpanda list import")}: ${frappe.utils.escape_html(
				String(d.last_catalog_import_at || "—")
			)}
			${
				exportJob.product_count
					? ` · ${fmt(exportJob.product_count)} ${__("products")}`
					: ""
			}</div>
			<div>${__("Synced")}: ${fmt(d.synced_count)} · ${__("Pending")}: ${fmt(d.pending_count)}</div>
		`);
	}

	add_button($parent, label, style, on_click, large) {
		const cls = large ? "btn btn-primary btn-lg fp-main-btn" : `btn btn-sm btn-${style === "primary" ? "primary" : "default"}`;
		const $btn = $(`<button type="button" class="${cls}">${frappe.utils.escape_html(label)}</button>`);
		$btn.on("click", on_click);
		$parent.append($btn);
	}

	add_link(label, on_click) {
		const $a = $(`<a href="#">${frappe.utils.escape_html(label)}</a>`);
		$a.on("click", (e) => {
			e.preventDefault();
			on_click();
		});
		this.$links.append($a);
	}

	refresh_links() {
		const outlet = this.outlet();
		frappe.confirm(
			__(
				"This downloads the latest Foodpanda product list and links matching barcodes to ERPNext items. Continue?"
			),
			() => {
				frappe.call({
					method: "aimatic.foodpanda_integration.api.start_import_and_map",
					args: { outlet },
					freeze: true,
					freeze_message: __("Starting Foodpanda list refresh..."),
					callback: (r) => {
						const job = (r.message || {}).job_id || "";
						frappe.msgprint({
							title: __("Refresh started"),
							indicator: "blue",
							message: __(
								"Job {0} is running in the background. Click Refresh status in a few minutes. A matching Excel report will be attached when mapping finishes.",
								[job]
							),
						});
						setTimeout(() => this.load_dashboard(), 5000);
					},
				});
			}
		);
	}

	push_catalog() {
		const outlet = this.outlet();
		const mapped = (this.dashboard && this.dashboard.mapped_sku_count) || 0;
		frappe.confirm(
			__("Send current prices and stock to Foodpanda for {0} linked products?", [mapped]),
			() => {
				frappe.call({
					method: "aimatic.foodpanda_integration.api.start_catalog_bulk_push",
					args: { outlet },
					freeze: true,
					freeze_message: __("Starting price and stock update..."),
					callback: (r) => {
						const status = r.message || {};
						if (!status.job_id) {
							frappe.show_alert({
								message: __("Could not start the update"),
								indicator: "red",
							});
							return;
						}
						this.poll_push(status.job_id);
					},
				});
			}
		);
	}

	poll_push(job_id) {
		const dialog = new frappe.ui.Dialog({
			title: __("Updating Foodpanda prices & stock"),
			fields: [{ fieldname: "progress_html", fieldtype: "HTML" }],
		});
		dialog.show();

		const render = (status) => {
			dialog.fields_dict.progress_html.$wrapper.html(
				`<p>${__("Status")}: ${frappe.utils.escape_html(status.status || "")}</p>` +
					`<p>${__("Done")}: ${status.synced || 0} / ${status.total || 0}</p>` +
					(status.failed
						? `<p style="color:var(--red-500)">${__("Failed")}: ${status.failed}</p>`
						: "")
			);
		};

		let timer;
		const poll = () => {
			frappe.call({
				method: "aimatic.foodpanda_integration.api.get_catalog_bulk_export_status",
				args: { job_id },
				callback: (r) => {
					const status = r.message;
					if (!status) {
						clearInterval(timer);
						dialog.hide();
						return;
					}
					render(status);
					if (status.status === "done") {
						clearInterval(timer);
						frappe.show_alert({
							message: __("Update complete: {0} ok, {1} failed", [
								status.synced || 0,
								status.failed || 0,
							]),
							indicator: status.failed ? "orange" : "green",
						});
						this.load_dashboard();
					}
				},
			});
		};
		poll();
		timer = setInterval(poll, 3000);
		dialog.onhide = () => clearInterval(timer);
	}
};
