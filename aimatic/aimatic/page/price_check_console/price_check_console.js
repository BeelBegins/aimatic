frappe.provide("aimatic");

frappe.pages["price-check-console"].on_page_load = function (wrapper) {
	new aimatic.PriceCheckPage(wrapper);
};

aimatic.PriceCheckPage = class PriceCheckPage {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("Price Check"),
			single_column: true,
		});
		this.build_branch_field();
		this.build_layout();
		this.bind_events();
		this.focus_barcode();
	}

	build_branch_field() {
		this.branch_field = this.page.add_field({
			label: __("Branch"),
			fieldname: "branch",
			fieldtype: "Link",
			options: "Branch",
			reqd: 1,
			default: frappe.defaults.get_user_default("Branch"),
			change: () => this.focus_barcode(),
		});
	}

	build_layout() {
		this.$body = $(`
			<div class="price-check-console">
				<div class="price-check-scan-row">
					<input type="text" class="form-control price-check-barcode-input"
						placeholder="${__("Scan or type barcode, then press Enter")}" autocomplete="off">
				</div>
				<div class="price-check-result">
					<div class="price-check-placeholder text-muted">
						${__("Select a branch, then scan a barcode.")}
					</div>
				</div>
			</div>
		`).appendTo(this.page.body);

		this.$barcode_input = this.$body.find(".price-check-barcode-input");
		this.$result = this.$body.find(".price-check-result");
	}

	bind_events() {
		this.$barcode_input.on("keydown", (e) => {
			if (e.which === 13 || e.key === "Enter") {
				e.preventDefault();
				this.handle_scan();
			}
		});
	}

	focus_barcode() {
		setTimeout(() => this.$barcode_input && this.$barcode_input.focus(), 0);
	}

	handle_scan() {
		const barcode = (this.$barcode_input.val() || "").trim();
		if (!barcode) return;

		const branch = this.branch_field.get_value();
		if (!branch) {
			frappe.show_alert({ message: __("Select a branch first."), indicator: "orange" });
			this.clear_barcode();
			return;
		}

		frappe.call({
			method: "aimatic.price_check.api.lookup_price_by_barcode",
			args: { barcode, branch },
			callback: (r) => {
				this.render_result(barcode, r.message);
				this.clear_barcode();
			},
			error: (r) => {
				const msg =
					(r && r.message) ||
					(r &&
						r._server_messages &&
						(() => {
							try {
								const parsed = JSON.parse(r._server_messages);
								return (parsed || [])
									.map((m) => {
										try {
											return JSON.parse(m).message;
										} catch (e) {
											return m;
										}
									})
									.filter(Boolean)
									.join(" ");
							} catch (e) {
								return null;
							}
						})()) ||
					__("Price lookup failed. Try scanning again.");
				frappe.show_alert({ message: msg, indicator: "red" });
				this.clear_barcode();
			},
		});
	}

	clear_barcode() {
		this.$barcode_input.val("");
		this.focus_barcode();
	}

	render_result(barcode, data) {
		if (!data || !data.found || !data.items.length) {
			this.$result.html(`
				<div class="price-check-not-found">
					<div class="price-check-not-found-icon">&times;</div>
					<div class="price-check-not-found-text">
						${__("No item found for barcode")} "${frappe.utils.escape_html(barcode)}"
					</div>
				</div>
			`);
			return;
		}

		const cards = data.items.map((item) => this.render_item_card(item)).join("");
		this.$result.html(`<div class="price-check-items">${cards}</div>`);
	}

	render_item_card(item) {
		const rate = item.rate ? format_currency(item.rate) : __("Not priced yet");
		const mrp = item.mrp ? format_currency(item.mrp) : null;
		return `
			<div class="price-check-card">
				<div class="price-check-item-name">${frappe.utils.escape_html(
					item.item_name || item.item_code
				)}</div>
				<div class="price-check-item-code text-muted">${frappe.utils.escape_html(item.item_code)}</div>
				<div class="price-check-rate">${rate}</div>
				${mrp ? `<div class="price-check-mrp text-muted">${__("MRP")}: ${mrp}</div>` : ""}
			</div>
		`;
	}
};
