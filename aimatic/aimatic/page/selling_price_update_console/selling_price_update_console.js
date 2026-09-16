frappe.provide("aimatic");

frappe.pages["selling-price-update-console"].on_page_load = function (wrapper) {
	if (!wrapper.spu_page) {
		wrapper.spu_page = new aimatic.SellingPriceUpdatePage(wrapper);
	}
};

frappe.pages["selling-price-update-console"].on_page_show = function (wrapper) {
	const page = wrapper.spu_page;
	if (page && frappe.route_options && Object.keys(frappe.route_options).length) {
		page.apply_route_options();
	}
};

aimatic.SellingPriceUpdatePage = class SellingPriceUpdatePage {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("Selling Price Update"),
			single_column: true,
		});
		this.rows = [];
		this.meta = {};
		this.branch_ctx = {};
		this.source_doc_options = [];
		this._busy = false;
		this.page.set_primary_action(__("Load"), () => this.load_rows());
		this.page.set_secondary_action(__("Apply Updates"), () => this.apply_updates());
		this.build_filters();
		this.build_layout();
		if (frappe.route_options && Object.keys(frappe.route_options).length) {
			this.apply_route_options();
		} else if (this.branch_field.get_value()) {
			this.on_branch_changed();
		}
	}

	build_filters() {
		this.branch_field = this.page.add_field({
			label: __("Branch"),
			fieldname: "branch",
			fieldtype: "Link",
			options: "Branch",
			reqd: 1,
			default: frappe.defaults.get_user_default("Branch"),
			change: () => {
				if (this._prefilling) return;
				this.on_branch_changed();
			},
		});
		this.warehouse_field = this.page.add_field({
			label: __("Warehouse"),
			fieldname: "warehouse",
			fieldtype: "Data",
			read_only: 1,
		});
		this.vendor_field = this.page.add_field({
			label: __("Vendor"),
			fieldname: "vendor",
			fieldtype: "Link",
			options: "Supplier",
			change: () => {
				if (this._prefilling) return;
				this.refresh_source_documents();
			},
		});
		this.source_type_field = this.page.add_field({
			label: __("Source Type"),
			fieldname: "source_doctype",
			fieldtype: "Select",
			options: "Purchase Receipt\nStock Transfer Note",
			default: "Purchase Receipt",
			reqd: 1,
			change: () => {
				if (this._prefilling) return;
				this.on_source_type_changed();
			},
		});
		this.source_name_field = this.page.add_field({
			label: __("Source Document"),
			fieldname: "source_name",
			fieldtype: "Select",
			options: "\n",
			reqd: 1,
			change: () => this.clear_grid(__("Source changed — click Load.")),
		});
		this.mode_field = this.page.add_field({
			label: __("Mode"),
			fieldname: "mode",
			fieldtype: "Select",
			options: ["Store Selling", "Foodpanda"],
			default: "Store Selling",
			reqd: 1,
			change: () => {
				this.update_target_price_list_display();
				this.clear_grid(__("Mode changed — load again."));
			},
		});
		this.page.page_form.addClass("spu-filters");
	}

	build_layout() {
		this.$body = $(`
			<div class="selling-price-update-page">
				<p class="spu-help">
					${__(
						"Branch → Warehouse → Vendor → latest source document. Mode only switches Store vs Foodpanda list."
					)}
				</p>
				<div class="spu-meta"></div>
				<div class="spu-table-wrap">
					<div class="spu-empty">${__("Select Branch, source document, then Load.")}</div>
				</div>
			</div>
		`).appendTo(this.page.body);
		this.$meta = this.$body.find(".spu-meta");
		this.$wrap = this.$body.find(".spu-table-wrap");
	}

	get_source_doctype() {
		const value = this.source_type_field.get_value();
		if (value === "Stock Transfer Note" || value === "Stock Entry") {
			return "Stock Entry";
		}
		return "Purchase Receipt";
	}

	set_source_type_from_doctype(doctype) {
		this.source_type_field.set_value(
			doctype === "Stock Entry" ? "Stock Transfer Note" : "Purchase Receipt"
		);
	}

	is_purchase_source() {
		return this.get_source_doctype() === "Purchase Receipt";
	}

	on_source_type_changed() {
		const is_pr = this.is_purchase_source();
		this.vendor_field.df.reqd = is_pr;
		this.vendor_field.refresh();
		if (!is_pr) {
			this.vendor_field.set_value("");
		}
		this.refresh_source_documents();
	}

	on_branch_changed() {
		const branch = this.branch_field.get_value();
		this.branch_ctx = {};
		this.source_doc_options = [];
		this.source_name_field.df.options = "\n";
		this.source_name_field.set_value("");
		this.source_name_field.refresh();
		this.warehouse_field.set_value("");
		this.current_target_list = "";
		this.clear_grid();

		if (!branch) {
			return;
		}

		frappe.call({
			method: "aimatic.shelf_pricing.engine.get_branch_console_context",
			args: { branch },
			callback: (r) => {
				this.branch_ctx = r.message || {};
				this.warehouse_field.set_value(this.branch_ctx.warehouse || "");
				this.update_target_price_list_display();
				this.refresh_source_documents();
			},
		});
	}

	update_target_price_list_display() {
		const ctx = this.branch_ctx || {};
		this.current_target_list =
			this.mode_field.get_value() === "Foodpanda"
				? ctx.foodpanda_price_list
				: ctx.selling_price_list;
	}

	refresh_source_documents(preferred_name) {
		const branch = this.branch_field.get_value();
		const source_doctype = this.get_source_doctype();
		if (!branch || !source_doctype) {
			return;
		}
		if (this.is_purchase_source() && !this.vendor_field.get_value()) {
			this.source_doc_options = [];
			this.source_name_field.df.options = "\n";
			this.source_name_field.last_options = null;
			this.source_name_field.set_value("");
			this.source_name_field.refresh();
			return;
		}

		frappe.call({
			method: "aimatic.shelf_pricing.engine.get_vendor_source_documents",
			args: {
				branch,
				vendor: this.vendor_field.get_value(),
				source_doctype,
			},
			callback: (r) => {
				const docs = (r.message && r.message.documents) || [];
				this.source_doc_options = docs;
				if (preferred_name && !docs.some((d) => d.source_name === preferred_name)) {
					this.source_doc_options.unshift({
						source_doctype,
						source_name: preferred_name,
						label: preferred_name,
					});
				}
				const options = ["", ...this.source_doc_options.map((d) => d.label || d.source_name)];
				this.source_name_field.df.options = options.join("\n");
				this.source_name_field.last_options = null;
				this.source_name_field.refresh();
				const match = preferred_name
					? this.source_doc_options.find((d) => d.source_name === preferred_name)
					: this.source_doc_options[0];
				if (match) {
					this.source_name_field.set_value(match.label || match.source_name);
				} else {
					this.source_name_field.set_value("");
					frappe.show_alert({
						message: __("No submitted source documents found for these filters."),
						indicator: "orange",
					});
				}
			},
		});
	}

	get_selected_source_name() {
		const value = this.source_name_field.get_value();
		const match = (this.source_doc_options || []).find(
			(d) => d.label === value || d.source_name === value
		);
		return match ? match.source_name : value;
	}

	get_filters() {
		return {
			mode: this.mode_field.get_value(),
			branch: this.branch_field.get_value(),
			source_doctype: this.get_source_doctype(),
			source_name: this.get_selected_source_name(),
		};
	}

	apply_route_options() {
		const opts = Object.assign({}, frappe.route_options || {});
		frappe.route_options = null;
		if (!opts.source_name && !opts.branch && !opts.vendor && !opts.mode) {
			return;
		}

		this._prefilling = true;
		if (opts.mode) {
			this.mode_field.set_value(opts.mode);
		}
		if (opts.source_doctype) {
			this.set_source_type_from_doctype(opts.source_doctype);
			this.vendor_field.df.reqd = opts.source_doctype === "Purchase Receipt";
			this.vendor_field.refresh();
		}

		const finish = (ctx) => {
			ctx = ctx || {};
			const branch = opts.branch || ctx.branch;
			const vendor = opts.vendor || ctx.vendor;
			const warehouse = opts.warehouse || ctx.warehouse;
			const source_name = opts.source_name || ctx.source_name;
			const posting_date = opts.posting_date || ctx.posting_date;
			const source_doctype = opts.source_doctype || ctx.source_doctype || this.get_source_doctype();

			if (branch) {
				this.branch_field.set_value(branch);
			}
			if (warehouse) {
				this.warehouse_field.set_value(warehouse);
			}
			if (vendor) {
				this.vendor_field.set_value(vendor);
			}

			const label = posting_date ? `${source_name} · ${posting_date}` : source_name;
			if (source_name) {
				this.source_doc_options = [
					{
						source_doctype,
						source_name,
						label,
						posting_date,
					},
				];
				this.source_name_field.df.options = ["", label].join("\n");
				this.source_name_field.last_options = null;
				this.source_name_field.refresh();
				this.source_name_field.set_value(label);
			}

			const after_branch = () => {
				this._prefilling = false;
				if (source_name) {
					this.refresh_source_documents(source_name);
					this.load_rows();
				} else {
					this.refresh_source_documents();
				}
			};

			if (!branch) {
				after_branch();
				return;
			}
			frappe.call({
				method: "aimatic.shelf_pricing.engine.get_branch_console_context",
				args: { branch },
				callback: (r) => {
					this.branch_ctx = r.message || {};
					if (!this.warehouse_field.get_value()) {
						this.warehouse_field.set_value(this.branch_ctx.warehouse || "");
					}
					this.update_target_price_list_display();
					after_branch();
				},
				error: () => after_branch(),
			});
		};

		if (opts.source_name && opts.source_doctype && (!opts.branch || !opts.vendor)) {
			frappe.call({
				method: "aimatic.shelf_pricing.engine.get_source_document_context",
				args: { source_doctype: opts.source_doctype, source_name: opts.source_name },
				callback: (r) => finish(r.message || {}),
				error: () => finish({}),
			});
			return;
		}
		finish({});
	}

	clear_grid(message) {
		this.rows = [];
		this.meta = {};
		this.$meta.empty();
		this.$wrap.html(
			`<div class="spu-empty">${frappe.utils.escape_html(message || __("Select Branch, source document, then Load."))}</div>`
		);
	}

	load_rows() {
		const filters = this.get_filters();
		if (!filters.branch) {
			frappe.show_alert({ message: __("Select a Branch."), indicator: "orange" });
			return;
		}
		if (!filters.source_name) {
			frappe.show_alert({ message: __("Select a source document."), indicator: "orange" });
			return;
		}
		if (this.is_purchase_source() && !this.vendor_field.get_value()) {
			frappe.show_alert({ message: __("Select a Vendor."), indicator: "orange" });
			return;
		}

		frappe.call({
			method: "aimatic.shelf_pricing.engine.get_selling_price_update_rows",
			args: filters,
			freeze: true,
			freeze_message: __("Loading prices..."),
			callback: (r) => {
				const data = r.message || {};
				this.meta = data;
				this.rows = (data.rows || []).map((row) => ({
					...row,
					_dirty: flt(row.new_selling_price) !== flt(row.current_selling_price),
				}));
				this.render_grid();
			},
		});
	}

	render_grid() {
		const mode = this.meta.mode || this.mode_field.get_value();
		const is_fp = mode === "Foodpanda";
		const count = this.rows.length;
		const doc = this.source_doc_options.find((d) => d.source_name === this.meta.source_name);
		const doc_label = doc ? doc.label : this.meta.source_name;
		let meta = __("Target: {0} · {1} row(s)", [this.meta.price_list || "—", String(count)]);
		if (doc_label) {
			meta = __("Source: {0} · ", [doc_label]) + meta;
		}
		this.$meta.text(meta);

		if (!count) {
			this.$wrap.html(`<div class="spu-empty">${__("No items on this source document.")}</div>`);
			return;
		}

		const head = `
			<thead>
				<tr>
					<th>${__("Item")}</th>
					<th>${__("Barcodes")}</th>
					<th class="spu-num">${__("Latest Price incl Taxes")}</th>
					<th>${__("UOM")}</th>
					<th class="spu-num">${__("MRP")}</th>
					${is_fp ? `<th class="spu-num">${__("Store Selling")}</th>` : ""}
					<th class="spu-num">${__("Current Selling")}</th>
					<th class="spu-num">${__("New Selling Price")}</th>
					<th class="spu-num">${__("GM % Current")}</th>
					<th class="spu-num">${__("GM % Helper")}</th>
				</tr>
			</thead>`;

		const body = this.rows
			.map((row, idx) => {
				const barcodes = [row.barcode1, row.barcode2, row.barcode3].filter(Boolean).join(", ");
				const gm = row.current_gm_percent != null ? flt(row.current_gm_percent, 2) : "";
				return `
				<tr data-idx="${idx}" class="${row._dirty ? "spu-dirty" : ""}">
					<td>
						<div class="spu-item-code">${frappe.utils.escape_html(row.item_code)}</div>
						<div class="spu-item-name">${frappe.utils.escape_html(row.item_name || "")}</div>
					</td>
					<td>${frappe.utils.escape_html(barcodes)}</td>
					<td class="spu-num">${format_currency(row.latest_price_incl_taxes || 0)}</td>
					<td>${frappe.utils.escape_html(row.uom || "")}</td>
					<td class="spu-num">${format_currency(row.mrp || 0)}</td>
					${
						is_fp
							? `<td class="spu-num">${format_currency(row.store_selling_price || 0)}</td>`
							: ""
					}
					<td class="spu-num">${format_currency(row.current_selling_price || 0)}</td>
					<td class="spu-num">
						<input type="number" step="1" min="0" class="form-control spu-new-price"
							value="${flt(row.new_selling_price || 0, 2)}" data-idx="${idx}">
					</td>
					<td class="spu-num spu-gm-cell">${gm}</td>
					<td class="spu-num">
						<input type="number" step="0.01" min="0" max="99.99" class="form-control spu-gm-helper"
							placeholder="%" data-idx="${idx}">
					</td>
				</tr>`;
			})
			.join("");

		this.$wrap.html(`<table class="spu-table">${head}<tbody>${body}</tbody></table>`);
		this.bind_grid_events();
	}

	bind_grid_events() {
		this.$wrap.find(".spu-new-price").on("change input", (e) => {
			const $input = $(e.currentTarget);
			const idx = cint($input.data("idx"));
			const row = this.rows[idx];
			if (!row) return;
			const value = flt($input.val());
			row.new_selling_price = value;
			row._dirty = flt(value) !== flt(row.current_selling_price);
			$input.closest("tr").toggleClass("spu-dirty", row._dirty);
			row.current_gm_percent = this.compute_gm(value, row.latest_price_incl_taxes);
			$input.closest("tr").find(".spu-gm-cell").text(flt(row.current_gm_percent, 2));
		});

		this.$wrap.find(".spu-gm-helper").on("change", (e) => {
			const $input = $(e.currentTarget);
			const idx = cint($input.data("idx"));
			const row = this.rows[idx];
			if (!row) return;
			const gm = flt($input.val());
			const price = this.sale_from_gm(row.latest_price_incl_taxes, gm);
			if (price == null) {
				frappe.show_alert({
					message: __("GM % needs cost > 0 and GM between 0 and 100."),
					indicator: "orange",
				});
				return;
			}
			row.new_selling_price = price;
			row._dirty = flt(price) !== flt(row.current_selling_price);
			row.current_gm_percent = this.compute_gm(price, row.latest_price_incl_taxes);
			const $tr = $input.closest("tr");
			$tr.find(".spu-new-price").val(price);
			$tr.toggleClass("spu-dirty", row._dirty);
			$tr.find(".spu-gm-cell").text(flt(row.current_gm_percent, 2));
		});
	}

	compute_gm(sale, cost) {
		sale = flt(sale);
		if (sale <= 0) return 0;
		return flt(((sale - flt(cost)) / sale) * 100, 2);
	}

	sale_from_gm(cost, gm) {
		cost = flt(cost);
		gm = flt(gm);
		if (cost <= 0 || gm <= 0 || gm >= 100) return null;
		const raw = cost / (1 - gm / 100);
		return Math.ceil(raw / 5) * 5;
	}

	collect_dirty_rows() {
		return this.rows.filter((row) => row._dirty && flt(row.new_selling_price) > 0);
	}

	apply_updates() {
		const filters = this.get_filters();
		if (!filters.branch || !filters.source_name) {
			frappe.show_alert({ message: __("Branch and source document are required."), indicator: "orange" });
			return;
		}
		const dirty = this.collect_dirty_rows();
		if (!dirty.length) {
			frappe.show_alert({ message: __("No changed prices to apply."), indicator: "orange" });
			return;
		}

		frappe.confirm(
			__("Apply {0} price update(s) to {1}?", [String(dirty.length), filters.mode]),
			() => {
				frappe.call({
					method: "aimatic.shelf_pricing.engine.apply_selling_price_updates",
					args: {
						mode: filters.mode,
						branch: filters.branch,
						source_doctype: filters.source_doctype,
						source_name: filters.source_name,
						rows: dirty.map((row) => ({
							item_code: row.item_code,
							uom: row.uom,
							new_selling_price: row.new_selling_price,
							mrp: row.mrp,
							latest_price_incl_taxes: row.latest_price_incl_taxes,
						})),
					},
					freeze: true,
					freeze_message: __("Updating prices..."),
					callback: (r) => {
						const result = r.message || {};
						let msg = __("Updated {0}, skipped {1}.", [
							String(result.updated || 0),
							String(result.skipped || 0),
						]);
						if (result.error_count) {
							msg += " " + __("{0} row(s) failed validation.", [String(result.error_count)]);
							frappe.msgprint({
								title: __("Some rows failed"),
								indicator: "orange",
								message: (result.errors || []).join("<br>"),
							});
						}
						frappe.show_alert({
							message: msg,
							indicator: result.error_count ? "orange" : "green",
						});
						this.load_rows();
					},
				});
			}
		);
	}
};
