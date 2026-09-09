frappe.provide("aimatic");

frappe.pages["ceo-dashboard-console"].on_page_load = (wrapper) => new aimatic.CEODashboardPage(wrapper);

aimatic.CEODashboardPage = class CEODashboardPage {
	constructor(wrapper) {
		this.page = frappe.ui.make_app_page({ parent: wrapper, title: __("CEO Dashboard"), single_column: true });
		this.page.set_primary_action(__("Refresh"), () => this.refresh());
		this.state = { period: "Today", branch: null, warehouse: null };
		this.$root = $('<div class="ceo-dashboard-page"></div>').appendTo(this.page.body);
		this.bind(); this.load_scope();
	}
	async load_scope() {
		this.$root.html(this.loading());
		try {
			this.scope = await frappe.xcall("aimatic.executive_dashboard.api.get_scope", {});
			this.currency = this.scope.currency;
			this.$root.html(`<section class="ceo-hero"><div><p>EXECUTIVE COMMAND CENTRE</p><h2>${__("CEO Dashboard")}</h2><small>${this.e(this.scope.company)} · ${__("Live business view")}</small></div><button class="btn btn-sm ceo-insights" data-insights>${__("All Insights reports")}</button></section><section class="ceo-scope" data-scope></section><section data-content></section>`);
			this.render_scope(); this.refresh();
		} catch (error) { this.fail(error); }
	}
	dates() {
		const now = frappe.datetime.get_today();
		if (this.state.period === "Last 7 Days") return [frappe.datetime.add_days(now, -6), now];
		if (this.state.period === "MTD") return [frappe.datetime.month_start(), now];
		if (this.state.period === "Last 30 Days") return [frappe.datetime.add_days(now, -29), now];
		if (this.state.period === "Custom") return [this.state.date_from || now, this.state.date_to || now];
		return [now, now];
	}
	chip(label, value, selected, attr) { return `<button class="ceo-chip ${selected === value ? "active" : ""}" ${attr}="${this.e(value)}">${this.e(label)}</button>`; }
	render_scope() {
		const warehouses = (this.scope.warehouses || []).filter((row) => !this.state.branch || row.branch === this.state.branch);
		this.$root.find("[data-scope]").html(`<div><b>${__("Period")}</b><span>${["Today", "Last 7 Days", "MTD", "Last 30 Days", "Custom"].map((v) => this.chip(__(v), v, this.state.period, "data-period")).join("")}</span></div><div><b>${__("Branches")}</b><span>${this.chip(__("All branches"), "", this.state.branch || "", "data-branch")}${(this.scope.branches || []).map((v) => this.chip(v, v, this.state.branch, "data-branch")).join("")}</span></div><div><b>${__("Warehouses")}</b><span>${this.chip(__("All mapped warehouses"), "", this.state.warehouse || "", "data-warehouse")}${warehouses.map((w) => this.chip(w.name, w.name, this.state.warehouse, "data-warehouse")).join("")}</span></div>`);
	}
	async custom_period() {
		const dialog = new frappe.ui.Dialog({ title: __("Custom period"), fields: [{ fieldname: "from", label: __("From"), fieldtype: "Date", reqd: 1, default: this.state.date_from || frappe.datetime.get_today() }, { fieldname: "to", label: __("To"), fieldtype: "Date", reqd: 1, default: this.state.date_to || frappe.datetime.get_today() }], primary_action_label: __("Apply"), primary_action: (v) => { this.state.date_from = v.from; this.state.date_to = v.to; dialog.hide(); this.refresh(); } });
		dialog.show();
	}
	async refresh() {
		if (!this.scope) return;
		const [date_from, date_to] = this.dates(); this.$root.find("[data-content]").html(this.loading());
		try { this.data = await frappe.xcall("aimatic.executive_dashboard.api.get_snapshot", { company: this.scope.company, date_from, date_to, branch: this.state.branch || undefined, warehouse: this.state.warehouse || undefined }); this.currency = this.data.currency; this.render(); } catch (error) { this.fail(error); }
	}
	bind() {
		this.$root.on("click", "[data-period]", async (e) => { this.state.period = $(e.currentTarget).attr("data-period"); this.render_scope(); if (this.state.period === "Custom") await this.custom_period(); else this.refresh(); });
		this.$root.on("click", "[data-branch]", (e) => { this.state.branch = $(e.currentTarget).attr("data-branch") || null; this.state.warehouse = null; this.render_scope(); this.refresh(); });
		this.$root.on("click", "[data-warehouse]", (e) => { this.state.warehouse = $(e.currentTarget).attr("data-warehouse") || null; const w = (this.scope.warehouses || []).find((row) => row.name === this.state.warehouse); if (w) this.state.branch = w.branch; this.render_scope(); this.refresh(); });
		this.$root.on("click", "[data-basket]", () => this.basket()); this.$root.on("click", "[data-insights]", () => window.location.assign("/insights"));
	}
	money(value) { return format_currency(flt(value || 0), this.currency); }
	num(value) { return frappe.format(value || 0, { fieldtype: "Int" }); }
	e(value) { return frappe.utils.escape_html(value || ""); }
	card(kind, label, value, note, alert) { return `<article class="ceo-card ${kind}${alert ? " alert" : ""}"><p>${this.e(label)}</p><strong>${value}</strong><small>${this.e(note)}</small></article>`; }
	render() {
		const d = this.data, k = d.kpis;
		this.$root.find("[data-content]").html(`<p class="ceo-period">${frappe.datetime.str_to_user(d.date_from)} — ${frappe.datetime.str_to_user(d.date_to)}${d.scope.warehouse ? ` · ${this.e(d.scope.warehouse)}` : ""}</p><div class="ceo-kpis">${this.card("sales", __("Net sales"), this.money(k.net_sales), __("Submitted POS after returns"))}${this.card("tickets", __("Tickets"), this.num(k.txn_count), `${__("Average basket")}: ${this.money(k.average_basket)}`)}${this.card("cash", __("Sales before returns"), this.money(k.gross_sales), __("Submitted sales tickets"))}${this.card("delivery", __("Returns"), this.money(k.returns_amount), __("Returns in selected period"))}${this.card("stock", __("Stock value"), this.money(k.stock_value), __("Current mapped warehouse snapshot"))}${this.card("risk", __("Negative stock"), this.num(k.negative_stock_skus), __("SKUs requiring investigation"), k.negative_stock_skus > 0)}${this.card("risk", __("Tax failures"), this.num(k.failed_tax_invoices), __("Failed FBR submissions"), k.failed_tax_invoices > 0)}${this.card("risk", __("Overdue payables"), this.money(k.overdue_supplier_bills), __("Company-wide supplier bills"), k.overdue_supplier_bills > 0)}</div><div class="ceo-columns"><section class="ceo-panel"><header><p>${__("Branch performance")}</p><h3>${__("Every branch, visible")}</h3></header>${this.branches(d.branches || [])}</section><section class="ceo-panel"><header><p>${__("Customer intelligence")}</p><h3>${__("Top customers")}</h3></header>${this.customers(d.top_customers || [])}</section></div><section class="ceo-panel ceo-basket" data-basket-panel><header><div><p>${__("Customer basket & item relevance")}</p><h3>${__("Which products belong together?")}</h3><small>${__("Lift, support and confidence use submitted POS sales.")}</small></div><button class="btn btn-primary btn-sm" data-basket>${__("Load item affinity")}</button></header><div class="ceo-empty">${__("Loads up to the 5,000 most recent sales in the selected scope.")}</div></section><p class="ceo-note">${this.e(d.notes.overdue_supplier_bills)} ${this.e(d.notes.stock_value)}</p>`);
	}
	branches(rows) { return rows.length ? `<div class="ceo-list">${rows.map((r) => `<button data-branch="${this.e(r.branch)}"><span><b>${this.e(r.branch)}</b><small>${this.money(r.stock_value)} ${__("stock")}</small></span><strong>${this.money(r.net_sales)}</strong></button>`).join("")}</div>` : `<div class="ceo-empty">${__("No submitted POS sales in this scope.")}</div>`; }
	customers(rows) { return rows.length ? `<ol class="ceo-customers">${rows.map((r) => `<li><span><b>${this.e(r.customer_name)}</b><small>${this.num(r.txn_count)} ${__("sales")}</small></span><strong>${this.money(r.net_sales)}</strong></li>`).join("")}</ol>` : `<div class="ceo-empty">${__("No customers with positive net sales in this scope.")}</div>`; }
	async basket() {
		const [date_from, date_to] = this.dates(), $panel = this.$root.find("[data-basket-panel]"); $panel.find(".ceo-empty").html(this.loading());
		try { const r = await frappe.xcall("aimatic.executive_dashboard.api.get_basket_report", { company: this.scope.company, date_from, date_to, branch: this.state.branch || undefined, warehouse: this.state.warehouse || undefined }); const body = r.warning ? `<div class="ceo-empty">${this.e(r.warning)}</div>` : `<div class="ceo-affinity"><div class="head"><span>${__("Item pair")}</span><span>${__("Together")}</span><span>${__("Support")}</span><span>${__("Confidence")}</span><span>${__("Lift")}</span></div>${(r.pairs || []).map((p) => `<div><span><b>${this.e(p.item_a_name)}</b> + <b>${this.e(p.item_b_name)}</b></span><span>${this.num(p.joint_transactions)}</span><span>${flt(p.support).toFixed(1)}%</span><span>${flt(p.confidence).toFixed(1)}%</span><span class="lift">${flt(p.lift).toFixed(2)}×</span></div>`).join("") || `<div class="ceo-empty">${__("No pairs met the confidence threshold.")}</div>`}</div>`; $panel.find(".ceo-empty").replaceWith(body); } catch (error) { $panel.find(".ceo-empty").html(this.error(error)); }
	}
	loading() { return `<div class="ceo-empty"><span class="spinner-border spinner-border-sm"></span> ${__("Loading executive data...")}</div>`; }
	error(error) { return `<div class="ceo-empty text-danger">${this.e(error && error.message ? error.message : __("Unable to load dashboard."))}</div>`; }
	fail(error) { this.$root.html(this.error(error)); }
};
