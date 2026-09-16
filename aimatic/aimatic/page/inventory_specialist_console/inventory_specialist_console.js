frappe.provide("aimatic");

frappe.pages["inventory-specialist-console"].on_page_load = function (wrapper) {
	if (!wrapper.inventory_specialist_page) {
		wrapper.inventory_specialist_page = new aimatic.InventorySpecialistPage(wrapper);
	}
};

aimatic.InventorySpecialistPage = class InventorySpecialistPage {
	constructor(wrapper) {
		this.page = frappe.ui.make_app_page({ parent: wrapper, title: __("Inventory Specialist"), single_column: true });
		this.page.set_primary_action(__("Refresh"), () => this.load());
		this.build_filters();
		this.$body = $("<div class='inventory-specialist'></div>").appendTo(this.page.body);
		this.render_empty();
	}

	build_filters() {
		this.branch = this.page.add_field({
			label: __("Branch"), fieldname: "branch", fieldtype: "Link", options: "Branch", reqd: 1,
			default: frappe.defaults.get_user_default("Branch"), change: () => this.load_if_ready(),
		});
		this.cover = this.page.add_field({
			label: __("Keep in stock for days"), fieldname: "cover", fieldtype: "Int",
			default: 7, change: () => this.load_if_ready(),
		});
		this.supplier = this.page.add_field({
			label: __("Vendor"), fieldname: "supplier", fieldtype: "Link", options: "Supplier",
			change: () => this.load_if_ready(),
		});
	}

	load_if_ready() { if (this.branch.get_value()) this.load(); }

	async load() {
		const branch = this.branch.get_value();
		if (!branch) return this.render_empty();
		this.$body.html(`<div class="is-empty">${__("Checking what needs attention…")}</div>`);
		try {
			this.data = await frappe.xcall("aimatic.inventory_specialist.engine.get_inventory_specialist", {
				branch, supplier: this.supplier.get_value(), target_cover_days: parseInt(this.cover.get_value(), 10) || 7,
			});
			this.render();
		} catch (error) {
			this.$body.html(`<div class="is-empty text-danger">${frappe.utils.escape_html(error?.message || __("Unable to load inventory advice."))}</div>`);
		}
	}

	render_empty() {
		this.$body.html(`<div class="is-empty">${__("Choose a branch. This page only suggests what to review; it never creates a purchase order.")}</div>`);
	}

	render() {
		const d = this.data;
		this.$body.html(`
			<div class="is-intro"><strong>${__("What should I buy today?")}</strong><span>${__("Based on the last {0} days of retail sales. No documents are created.", [d.history_days])}</span></div>
			<div class="is-tabs">
				<button class="active" data-is-tab="buy">${__("Buy today")}</button>
				<button data-is-tab="cover">${__("Cover plan")}</button>
				<button data-is-tab="checks">${__("Stock checks")} <em>${d.stock_checks.length}</em></button>
				<button data-is-tab="timing">${__("Supplier timing")}</button>
			</div>
			<div data-is-panel="buy">${this.buy_panel(d.supplier_groups)}</div>
			<div data-is-panel="cover" class="hide">${this.cover_panel(d.recommendations)}</div>
			<div data-is-panel="checks" class="hide">${this.checks_panel(d.stock_checks)}</div>
			<div data-is-panel="timing" class="hide">${this.timing_panel(d.supplier_timing)}</div>
		`);
		this.$body.find("[data-is-tab]").on("click", (event) => {
			const tab = $(event.currentTarget).data("is-tab");
			this.$body.find("[data-is-tab]").removeClass("active");
			$(event.currentTarget).addClass("active");
			this.$body.find("[data-is-panel]").addClass("hide");
			this.$body.find(`[data-is-panel='${tab}']`).removeClass("hide");
		});
	}

	buy_panel(groups) {
		if (!groups.length) return this.none(__("Nothing needs ordering from the configured external suppliers for this cover level."));
		return groups.map((group) => `
			<section class="is-card"><h4>${frappe.utils.escape_html(group.supplier)} <span>${group.item_count} ${__("items")}</span></h4>
			${this.table(group.items, true)}</section>`).join("");
	}

	cover_panel(rows) {
		return `<section class="is-card"><h4>${__("Suggested cover plan")} <span>${__("only items needing a top-up")}</span></h4>${rows.length ? this.table(rows, false) : this.none(__("No top-ups are needed."))}</section>`;
	}

	checks_panel(rows) {
		if (!rows.length) return this.none(__("No negative stock or missing-supplier checks in this branch."));
		return `<section class="is-card"><h4>${__("Fix these before trusting an order suggestion")}</h4><table><thead><tr><th>${__("Item")}</th><th>${__("In stock")}</th><th>${__("Sold recently")}</th><th>${__("Why")}</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${this.item(r)}</td><td>${this.qty(r.stock_qty)}</td><td>${this.qty(r.sales_qty)}</td><td>${__(r.reason)}</td></tr>`).join("")}</tbody></table></section>`;
	}

	timing_panel(rows) {
		const note = __("Lead time is learned automatically only from a real order date before its receipt date. Same-day PO and receipt records are not treated as lead time.");
		if (!rows.length) return `<section class="is-card"><h4>${__("Supplier timing")}</h4>${this.none(__("Learning — no real supplier lead-time observations yet."))}<p class="is-note">${note}</p></section>`;
		return `<section class="is-card"><h4>${__("Supplier timing")}</h4><table><thead><tr><th>${__("Supplier")}</th><th>${__("Lead time")}</th><th>${__("Observed deliveries")}</th><th>${__("Status")}</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${frappe.utils.escape_html(r.supplier)}</td><td>${r.lead_time_days == null ? "—" : `${r.lead_time_days} ${__("days")}`}</td><td>${r.observations}</td><td>${__(r.status)}</td></tr>`).join("")}</tbody></table><p class="is-note">${note}</p></section>`;
	}

	table(rows, grouped) {
		return `<table><thead><tr><th>${__("Item")}</th><th>${__("In stock")}</th><th>${__("Selling/day")}</th><th>${__("Will last")}</th><th>${__("Buy now")}</th>${grouped ? `<th>${__("Action")}</th>` : ""}</tr></thead><tbody>${rows.map((r) => `<tr><td>${this.item(r)}</td><td>${this.qty(r.stock_qty)}</td><td>${this.qty(r.daily_demand)}</td><td>${r.days_left} ${__("days")}</td><td><strong>${this.qty(r.suggested_qty)}</strong></td>${grouped ? `<td><span class="is-action ${r.action === "Order now" ? "urgent" : ""}">${__(r.action)}</span></td>` : ""}</tr>`).join("")}</tbody></table>`;
	}

	item(row) { return `<strong>${frappe.utils.escape_html(row.item_name || row.item_code)}</strong><small>${frappe.utils.escape_html(row.item_code)}</small>`; }
	qty(value) { return frappe.format(value || 0, { fieldtype: "Float", precision: 2 }); }
	none(message) { return `<div class="is-empty">${message}</div>`; }
};
