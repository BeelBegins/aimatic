frappe.provide('aimatic');

frappe.pages['vendor-stock-positions-console'].on_page_load = function (wrapper) {
	new aimatic.VendorStockPositionsPage(wrapper);
};

aimatic.VendorStockPositionsPage = class VendorStockPositionsPage {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __('Vendor Stock Positions'),
			single_column: true,
		});
		this.page.set_primary_action(__('Refresh'), () => this.refresh());
		this.page.set_secondary_action(__('Export Excel'), () => this.export_excel());
		this.page.add_inner_button(__('Vendor Performance'), () => {
			const filters = this.get_filters();
			frappe.route_options = {
				supplier: filters.supplier || undefined,
				company: filters.company || undefined,
				branch: filters.branch || undefined,
				warehouse: filters.warehouse || undefined,
			};
			frappe.set_route('vendor-performance-console');
		});
		this.currency = null;
		this.build_filters();
		this.build_layout();
		this.bind_events();
		this.apply_route_options();
	}

	build_filters() {
		this.supplier_field = this.page.add_field({
			label: __('Supplier'),
			fieldname: 'supplier',
			fieldtype: 'Link',
			options: 'Supplier',
			reqd: 1,
			change: () => this.refresh_if_ready(),
		});
		this.company_field = this.page.add_field({
			label: __('Company'),
			fieldname: 'company',
			fieldtype: 'Link',
			options: 'Company',
			default: frappe.defaults.get_user_default('Company') || frappe.defaults.get_default('company'),
			reqd: 1,
			change: () => this.refresh_if_ready(),
		});
		this.branch_field = this.page.add_field({
			label: __('Branch'),
			fieldname: 'branch',
			fieldtype: 'Link',
			options: 'Branch',
			change: () => this.refresh_if_ready(),
		});
		this.warehouse_field = this.page.add_field({
			label: __('Warehouse'),
			fieldname: 'warehouse',
			fieldtype: 'Link',
			options: 'Warehouse',
			get_query: () => {
				const branch = this.branch_field.get_value();
				return branch ? { filters: { custom_branch: branch } } : {};
			},
			change: () => {
				const warehouse = this.warehouse_field.get_value();
				if (warehouse) {
					frappe.db.get_value('Warehouse', warehouse, 'custom_branch').then((r) => {
						const derived_branch = r.message && r.message.custom_branch;
						if (derived_branch && this.branch_field.get_value() !== derived_branch) {
							this.branch_field.set_value(derived_branch);
						}
					});
				}
				this.refresh_if_ready();
			},
		});
		this.lookback_field = this.page.add_field({
			label: __('Days'),
			fieldname: 'lookback_days',
			fieldtype: 'Int',
			default: 30,
			reqd: 1,
			change: () => this.refresh_if_ready(),
		});
		this.group_field = this.page.add_field({
			label: __('Group By'),
			fieldname: 'group_by',
			fieldtype: 'Select',
			options: ['Item', 'Warehouse'],
			default: 'Item',
			change: () => this.refresh_if_ready(),
		});
	}

	build_layout() {
		this.$container = $('<div class="vendor-stock-positions-page"></div>').appendTo(this.page.body);
		this.render_empty_state();
	}

	bind_events() {
		this.$container.on('click', '[data-doctype][data-name]', (event) => {
			event.preventDefault();
			const $target = $(event.currentTarget);
			frappe.set_route('Form', $target.data('doctype'), $target.data('name'));
		});
		this.$container.on('input', '[data-vsp-search]', (event) => {
			this.filter_table($(event.currentTarget).val() || '');
		});
	}

	apply_route_options() {
		const route_options = frappe.route_options || {};
		if (route_options.supplier) {
			this.supplier_field.set_value(route_options.supplier);
			frappe.route_options = null;
		}
		if (route_options.company && !this.company_field.get_value()) {
			this.company_field.set_value(route_options.company);
		}
		if (route_options.branch && !this.branch_field.get_value()) {
			this.branch_field.set_value(route_options.branch);
		}
		if (route_options.warehouse && !this.warehouse_field.get_value()) {
			this.warehouse_field.set_value(route_options.warehouse);
		}
		this.refresh_if_ready();
	}

	get_filters() {
		return {
			supplier: this.supplier_field.get_value(),
			company: this.company_field.get_value(),
			branch: this.branch_field.get_value(),
			warehouse: this.warehouse_field.get_value(),
			lookback_days: parseInt(this.lookback_field.get_value() || 30, 10) || 30,
			group_by_warehouse: this.group_field.get_value() === 'Warehouse' ? 1 : 0,
		};
	}

	refresh_if_ready() {
		const filters = this.get_filters();
		if (filters.supplier && filters.company) {
			this.refresh();
		}
	}

	async refresh() {
		const filters = this.get_filters();
		if (!filters.supplier || !filters.company) {
			this.render_empty_state();
			return;
		}

		this.render_loading();
		try {
			const data = await frappe.xcall(
				'aimatic.vendor_performance.api.get_vendor_stock_positions',
				filters
			);
			this.currency = data.currency;
			this.render_report(data);
		} catch (error) {
			console.error(error);
			this.render_error(error);
		}
	}

	export_excel() {
		const filters = this.get_filters();
		if (!filters.supplier || !filters.company) {
			frappe.show_alert({
				message: __('Choose a supplier and company first.'),
				indicator: 'orange',
			});
			return;
		}
		open_url_post(
			'/api/method/aimatic.vendor_performance.api.export_vendor_stock_positions',
			filters
		);
	}

	render_empty_state() {
		this.$container.html(`
			<div class="vp-empty">
				<div class="vp-state-icon">${frappe.utils.icon('package', 'md')}</div>
				${__('Choose a supplier and company to load vendor stock positions.')}
			</div>
		`);
	}

	render_loading() {
		this.$container.html(`
			<div class="vp-loading">
				<div class="vp-state-icon">${frappe.utils.icon('loader', 'md')}</div>
				${__('Loading vendor stock positions...')}
			</div>
		`);
	}

	render_error(error) {
		const message = frappe.utils.escape_html(
			error?.message || __('Unable to load vendor stock positions.')
		);
		this.$container.html(`
			<div class="vp-empty text-danger">
				<div class="vp-state-icon">${frappe.utils.icon('alert-triangle', 'md')}</div>
				${message}
			</div>
		`);
	}

	render_report(data) {
		const summary = data.summary || {};
		const groupByWarehouse = !!data.group_by_warehouse;
		const rows = data.stock_items || [];
		const truncatedNote = summary.truncated
			? `<div class="vp-item-meta">${__(
					'Showing {0} of {1} rows (Excel export uses the same cap). Narrow branch or warehouse if needed.',
					[this.number(summary.row_count), this.number(summary.total_row_count)]
			  )}</div>`
			: '';

		this.$container.html(`
			<div class="vp-note">
				<div class="vp-note-icon">${frappe.utils.icon('info', 'sm')}</div>
				<div class="vp-note-body">
					<div><strong>${__('Stock values')}:</strong> ${frappe.utils.escape_html(data.stock_definition_note || '')}</div>
					<div><strong>${__('Cost of goods sold')}:</strong> ${frappe.utils.escape_html(data.cogs_definition_note || '')}</div>
					<div class="vp-item-meta">${frappe.utils.escape_html(data.item_sources_note || '')}</div>
					<div class="vp-item-meta">${__('Window')}: ${frappe.datetime.str_to_user(data.date_from)} → ${frappe.datetime.str_to_user(data.date_to)} · ${frappe.utils.escape_html(data.supplier_name || data.supplier || '')}</div>
					${truncatedNote}
				</div>
			</div>
			<div class="vp-grid">
				${this.card('stock', 'package', __('Stock Value (at Cost)'), this.money(summary.stock_value), `${this.number(summary.stock_qty)} ${__('qty')} · ${this.number(summary.stock_item_count)} ${__('items')}`)}
				${this.card('purchase', 'layers', __('Linked SKUs'), this.number(summary.linked_item_count), groupByWarehouse ? `${this.number(summary.warehouse_count)} ${__('warehouses with stock')}` : `${this.number(summary.row_count)} ${__('stock rows')}`)}
				${this.card('revenue', 'trending-up', __('Sales Revenue (Window)'), this.money(summary.sales_amount), `${this.number(summary.sales_qty)} ${__('qty')} · ${this.number(summary.sales_doc_count)} ${__('docs')}`)}
				${this.card('cogs', 'shopping-bag', __('Cost of Goods Sold (Window)'), this.money(summary.cogs_amount), `${this.number(summary.cogs_qty)} ${__('qty')}`)}
				${this.card('margin', 'percent', __('Gross Margin (Window)'), this.money(summary.gross_margin_amount), `${this.percent(summary.gross_margin_pct)} ${__('of revenue')}`, summary.gross_margin_amount)}
			</div>
			<div class="vp-section">
				<div class="vp-section-header">
					<div class="vp-section-header-left">
						<div class="vp-section-icon">${frappe.utils.icon('package', 'sm')}</div>
						<h4>${__('Stock Positions')}</h4>
					</div>
					<span class="vp-badge">${groupByWarehouse ? __('By warehouse') : __('By item')}</span>
				</div>
				<div class="vsp-toolbar">
					<input type="search" class="form-control input-xs vsp-search" data-vsp-search placeholder="${__('Search item / warehouse...')}" />
					<div class="vsp-meta">${__('Rows')}: <strong data-vsp-visible-count>${this.number(rows.length)}</strong></div>
				</div>
				${this.render_stock_table(rows, groupByWarehouse)}
			</div>
		`);
	}

	filter_table(query) {
		const needle = (query || '').trim().toLowerCase();
		let visible = 0;
		this.$container.find('[data-vsp-row]').each((_, el) => {
			const hay = ($(el).attr('data-vsp-row') || '').toLowerCase();
			const show = !needle || hay.includes(needle);
			$(el).toggle(show);
			if (show) {
				visible += 1;
			}
		});
		this.$container.find('[data-vsp-visible-count]').text(format_number(visible || 0, null, 0));
	}

	card(accent, icon, label, value, subvalue, colorizeValue) {
		let valueClass = '';
		if (colorizeValue !== undefined) {
			valueClass = flt(colorizeValue) < 0 ? ' vp-value-negative' : ' vp-value-positive';
		}
		return `
			<div class="vp-card vp-accent-${accent}">
				<div class="vp-card-top">
					<div class="vp-card-label">${frappe.utils.escape_html(label)}</div>
					<div class="vp-card-icon">${frappe.utils.icon(icon, 'sm')}</div>
				</div>
				<div class="vp-card-value${valueClass}">${value}</div>
				<div class="vp-card-subvalue">${frappe.utils.escape_html(subvalue || '')}</div>
			</div>
		`;
	}

	render_stock_table(rows, groupByWarehouse) {
		if (!rows.length) {
			return `
				<div class="vp-empty">
					<div class="vp-state-icon">${frappe.utils.icon('package', 'md')}</div>
					${__('No current stock found for this supplier-linked SKU set.')}
				</div>
			`;
		}

		const warehouseHeaders = groupByWarehouse
			? `<th>${__('Warehouse')}</th><th>${__('Branch')}</th>`
			: '';

		const body = rows
			.map((row) => {
				const searchBits = [row.item_code, row.item_name, row.warehouse, row.branch]
					.filter(Boolean)
					.join(' ');
				const warehouseCells = groupByWarehouse
					? `<td>${frappe.utils.escape_html(row.warehouse || '')}</td><td>${frappe.utils.escape_html(row.branch || '')}</td>`
					: '';
				return `
				<tr data-vsp-row="${frappe.utils.escape_html(searchBits)}">
					<td>
						<div class="vp-item-title">${frappe.utils.escape_html(row.item_code)}</div>
						<div class="vp-item-meta">${frappe.utils.escape_html(row.item_name || '')}</div>
					</td>
					${warehouseCells}
					<td class="vp-num">${this.number(row.stock_qty)}</td>
					<td class="vp-num">${this.money(row.stock_value)}</td>
					<td class="vp-num">${this.number(row.purchase_qty_in_window)}</td>
					<td class="vp-num">${this.money(row.purchase_amount_in_window)}</td>
					<td class="vp-num">${this.money(row.purchase_tax_amount_in_window)}</td>
					<td class="vp-num">${this.money(row.purchase_amount_incl_tax_in_window)}</td>
					<td class="vp-num">${this.number(row.sales_qty_in_window)}</td>
					<td class="vp-num">${this.money(row.sales_amount_in_window)}</td>
					<td class="vp-num">${this.money(row.cogs_amount_in_window)}</td>
					<td class="vp-num ${this.margin_class(row.gross_margin_in_window)}">${this.money(row.gross_margin_in_window)}</td>
					<td class="vp-num">${this.number(row.adjustment_qty_in_window)}</td>
					<td class="vp-num">${this.money(row.adjustment_value_in_window)}</td>
					<td>${row.last_purchase_date ? frappe.datetime.str_to_user(row.last_purchase_date) : ''}</td>
					<td>${row.last_purchase_doc ? this.doc_link(row.last_purchase_doctype || 'Purchase Invoice', row.last_purchase_doc) : ''}</td>
					<td class="vp-num">${row.last_purchase_rate ? this.money(row.last_purchase_rate) : ''}</td>
					<td class="vp-num">${row.last_purchase_rate_incl_tax ? this.money(row.last_purchase_rate_incl_tax) : ''}</td>
					<td>${row.last_sale_date ? frappe.datetime.str_to_user(row.last_sale_date) : ''}</td>
					<td>${row.last_sale_doc ? this.doc_link(row.last_sale_doctype || 'POS Invoice', row.last_sale_doc) : ''}</td>
					<td class="vp-num">${row.last_sale_rate ? this.money(row.last_sale_rate) : ''}</td>
				</tr>
			`;
			})
			.join('');

		return `
			<div class="vp-table-wrap">
				<table class="vp-table">
					<thead>
						<tr>
							<th>${__('Item')}</th>
							${warehouseHeaders}
							<th class="vp-num">${__('Stock Qty')}</th>
							<th class="vp-num">${__('Stock Value')}</th>
							<th class="vp-num">${__('Total Purchase Qty')}</th>
							<th class="vp-num">${__('Purchase Amount (Excl Tax)')}</th>
							<th class="vp-num">${__('Purchase Tax / GST')}</th>
							<th class="vp-num">${__('Purchase Amount (Incl Tax)')}</th>
							<th class="vp-num">${__('Total Sale Qty')}</th>
							<th class="vp-num">${__('Total Sale Amount')}</th>
							<th class="vp-num">${__('Cost of Goods Sold')}</th>
							<th class="vp-num">${__('Gross Margin')}</th>
							<th class="vp-num">${__('Adjustment Qty')}</th>
							<th class="vp-num">${__('Adjustment Value')}</th>
							<th>${__('Last Purchase')}</th>
							<th>${__('Last Purchase Doc')}</th>
							<th class="vp-num">${__('Last Purchase Rate (Excl Tax)')}</th>
							<th class="vp-num">${__('Last Purchase Rate (Incl Tax)')}</th>
							<th>${__('Last Sale')}</th>
							<th>${__('Last Sale Doc')}</th>
							<th class="vp-num">${__('Last Sale Rate')}</th>
						</tr>
					</thead>
					<tbody>${body}</tbody>
				</table>
			</div>
		`;
	}

	doc_link(doctype, name) {
		const safeDoctype = frappe.utils.escape_html(doctype);
		const safeName = frappe.utils.escape_html(name);
		return `<a href="#" data-doctype="${safeDoctype}" data-name="${safeName}">${safeName}</a>`;
	}

	margin_class(value) {
		return flt(value) < 0 ? 'vp-value-negative' : 'vp-value-positive';
	}

	money(value) {
		return format_currency(value || 0, this.currency);
	}

	number(value) {
		return format_number(value || 0, null, 2);
	}

	percent(value) {
		return `${format_number(value || 0, null, 1)}%`;
	}
};
