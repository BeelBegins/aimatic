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
				from_date: filters.from_date || undefined,
				to_date: filters.to_date || undefined,
				principal: filters.principal || undefined,
			};
			frappe.set_route('vendor-performance-console');
		});
		this.currency = null;
		this.build_filters();
		this.build_layout();
		this.bind_events();
		this.sync_mode_fields();
		this.apply_route_options();
	}

	build_filters() {
		this.mode_field = this.page.add_field({
			label: __('Mode'),
			fieldname: 'report_mode',
			fieldtype: 'Select',
			options: ['Single Vendor', 'All Vendors', 'By Barcodes'],
			default: 'Single Vendor',
			change: () => {
				this.sync_mode_fields();
				this.refresh_if_ready();
			},
		});
		this.stock_basis_field = this.page.add_field({
			label: __('Stock Basis'),
			fieldname: 'stock_basis',
			fieldtype: 'Select',
			options: ['Bin', 'FIFO Origin', 'Both'],
			default: 'Bin',
			change: () => this.refresh_if_ready(),
		});
		this.supplier_field = this.page.add_field({
			label: __('Supplier'),
			fieldname: 'supplier',
			fieldtype: 'Link',
			options: 'Supplier',
			reqd: 1,
			change: () => {
				if (this.principal_field.get_value()) {
					this.principal_field.set_value('');
				}
				this.refresh_if_ready();
			},
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
		this.principal_field = this.page.add_field({
			label: __('Principal'),
			fieldname: 'principal',
			fieldtype: 'Link',
			options: 'Principal',
			get_query: () => {
				const supplier = this.supplier_field.get_value();
				return supplier
					? {
							query: 'aimatic.purchase_principal.get_principal_query',
							filters: { supplier },
					  }
					: {};
			},
			change: () => this.refresh_if_ready(),
		});
		this.from_date_field = this.page.add_field({
			label: __('From Date'),
			fieldname: 'from_date',
			fieldtype: 'Date',
			default: frappe.datetime.month_start(),
			reqd: 1,
			change: () => this.refresh_if_ready(),
		});
		this.to_date_field = this.page.add_field({
			label: __('To Date'),
			fieldname: 'to_date',
			fieldtype: 'Date',
			default: frappe.datetime.get_today(),
			reqd: 1,
			change: () => this.refresh_if_ready(),
		});
		this.branch_field = this.page.add_field({
			label: __('Branch'),
			fieldname: 'branch',
			fieldtype: 'Link',
			options: 'Branch',
			default: frappe.defaults.get_user_default('Branch'),
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
		this.$barcode_wrap = $(`
			<div class="vsp-barcode-wrap" style="display:none;">
				<label class="control-label">${__('Barcodes / Item Codes')}</label>
				<textarea class="form-control vsp-barcode-input" rows="5"
					placeholder="${__('Paste barcodes or item codes from Excel (one per line, or comma / tab separated)')}"></textarea>
				<div class="vsp-barcode-hint text-muted">${__('Resolves via Item Barcode, then exact Item code. Unmatched values are listed after refresh.')}</div>
			</div>
		`).appendTo(this.page.body);
		this.$barcode_input = this.$barcode_wrap.find('textarea');
		this.$barcode_input.on('change', () => this.refresh_if_ready());
		this.render_empty_state();
	}

	sync_mode_fields() {
		const mode = this.get_mode();
		const $supplier = this.supplier_field.$wrapper;
		if (mode === 'single') {
			$supplier.show();
			this.supplier_field.df.reqd = 1;
			this.$barcode_wrap.hide();
		} else if (mode === 'all') {
			$supplier.hide();
			this.supplier_field.df.reqd = 0;
			this.$barcode_wrap.hide();
		} else {
			$supplier.hide();
			this.supplier_field.df.reqd = 0;
			this.$barcode_wrap.show();
		}
	}

	get_mode() {
		const label = this.mode_field.get_value() || 'Single Vendor';
		if (label === 'All Vendors') {
			return 'all';
		}
		if (label === 'By Barcodes') {
			return 'barcodes';
		}
		return 'single';
	}

	get_stock_basis() {
		const label = this.stock_basis_field.get_value() || 'Bin';
		if (label === 'FIFO Origin') {
			return 'fifo';
		}
		if (label === 'Both') {
			return 'both';
		}
		return 'bin';
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
		if (route_options.mode) {
			const modeMap = {
				single: 'Single Vendor',
				all: 'All Vendors',
				barcodes: 'By Barcodes',
			};
			this.mode_field.set_value(modeMap[route_options.mode] || route_options.mode);
		}
		if (route_options.stock_basis) {
			const basisMap = {
				bin: 'Bin',
				fifo: 'FIFO Origin',
				both: 'Both',
			};
			this.stock_basis_field.set_value(
				basisMap[route_options.stock_basis] || route_options.stock_basis
			);
		}
		if (route_options.supplier) {
			this.mode_field.set_value('Single Vendor');
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
		if (route_options.from_date) {
			this.from_date_field.set_value(route_options.from_date);
		}
		if (route_options.to_date) {
			this.to_date_field.set_value(route_options.to_date);
		}
		if (route_options.principal) {
			this.principal_field.set_value(route_options.principal);
		}
		if (route_options.barcodes_text) {
			this.mode_field.set_value('By Barcodes');
			this.$barcode_input.val(route_options.barcodes_text);
		}
		this.sync_mode_fields();
		this.refresh_if_ready();
	}

	get_filters() {
		const mode = this.get_mode();
		const filters = {
			company: this.company_field.get_value(),
			branch: this.branch_field.get_value(),
			warehouse: this.warehouse_field.get_value(),
			from_date: this.from_date_field.get_value(),
			to_date: this.to_date_field.get_value(),
			principal: this.principal_field.get_value(),
			group_by_warehouse: this.group_field.get_value() === 'Warehouse' ? 1 : 0,
			stock_basis: this.get_stock_basis(),
		};
		if (mode === 'single') {
			filters.supplier = this.supplier_field.get_value();
		}
		if (mode === 'barcodes') {
			filters.barcodes_text = (this.$barcode_input.val() || '').trim();
		}
		return filters;
	}

	filters_ready() {
		const filters = this.get_filters();
		const mode = this.get_mode();
		if (!filters.company || !filters.from_date || !filters.to_date) {
			return false;
		}
		if (mode === 'single' && !filters.supplier) {
			return false;
		}
		if (mode === 'barcodes' && !filters.barcodes_text) {
			return false;
		}
		return true;
	}

	refresh_if_ready() {
		if (this.filters_ready()) {
			this.refresh();
		}
	}

	api_method(kind) {
		const mode = this.get_mode();
		if (mode === 'all') {
			return kind === 'export'
				? 'aimatic.vendor_performance.api.export_all_vendor_stock_positions'
				: 'aimatic.vendor_performance.api.get_all_vendor_stock_positions';
		}
		if (mode === 'barcodes') {
			return kind === 'export'
				? 'aimatic.vendor_performance.api.export_stock_positions_by_barcodes'
				: 'aimatic.vendor_performance.api.get_stock_positions_by_barcodes';
		}
		return kind === 'export'
			? 'aimatic.vendor_performance.api.export_vendor_stock_positions'
			: 'aimatic.vendor_performance.api.get_vendor_stock_positions';
	}

	async refresh() {
		if (!this.filters_ready()) {
			this.render_empty_state();
			return;
		}

		this.render_loading();
		try {
			const data = await frappe.xcall(this.api_method('get'), this.get_filters());
			this.currency = data.currency;
			this.render_report(data);
		} catch (error) {
			console.error(error);
			this.render_error(error);
		}
	}

	export_excel() {
		if (!this.filters_ready()) {
			frappe.show_alert({
				message: __('Set the required filters for this mode first.'),
				indicator: 'orange',
			});
			return;
		}
		open_url_post('/api/method/' + this.api_method('export'), this.get_filters());
	}

	render_empty_state() {
		const mode = this.get_mode();
		let message = __('Choose a supplier and company to load vendor stock positions.');
		if (mode === 'all') {
			message = __('Choose a company and date range to load all-vendor stock positions.');
		} else if (mode === 'barcodes') {
			message = __('Paste barcodes or item codes, then refresh.');
		}
		this.$container.html(`
			<div class="vp-empty">
				<div class="vp-state-icon">${frappe.utils.icon('package', 'md')}</div>
				${message}
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
		const includeSupplier = !!data.include_supplier_columns;
		const includeOrigin = !!data.include_origin_columns;
		const rows = data.stock_items || [];
		const truncatedNote = summary.truncated
			? `<div class="vp-item-meta">${__(
					'Showing {0} of {1} rows (Excel export uses the same cap). Narrow branch or warehouse if needed.',
					[this.number(summary.row_count), this.number(summary.total_row_count)]
			  )}</div>`
			: '';
		const fifoNote = data.fifo_note
			? `<div class="vp-item-meta text-danger">${frappe.utils.escape_html(data.fifo_note)}</div>`
			: '';
		const unresolved = data.unresolved_barcodes || [];
		const unresolvedBlock = unresolved.length
			? `<div class="vsp-unresolved">
					<strong>${__('Unresolved barcodes / item codes')} (${unresolved.length}):</strong>
					${unresolved.map((token) => `<code>${frappe.utils.escape_html(token)}</code>`).join(' ')}
				</div>`
			: '';

		const modeLabel =
			data.mode === 'all_vendors'
				? __('All Vendors')
				: data.mode === 'by_barcodes'
					? __('By Barcodes')
					: frappe.utils.escape_html(data.supplier_name || data.supplier || '');

		const supplierCountMeta = summary.linked_supplier_count
			? ` · ${this.number(summary.linked_supplier_count)} ${__('vendors')}`
			: '';

		this.$container.html(`
			<div class="vp-note">
				<div class="vp-note-icon">${frappe.utils.icon('info', 'sm')}</div>
				<div class="vp-note-body">
					<div><strong>${__('Stock values')}:</strong> ${frappe.utils.escape_html(data.stock_definition_note || '')}</div>
					<div><strong>${__('Incl. taxes')}:</strong> ${frappe.utils.escape_html(data.stock_incl_tax_note || '')}</div>
					<div><strong>${__('Cost of goods sold')}:</strong> ${frappe.utils.escape_html(data.cogs_definition_note || '')}</div>
					<div class="vp-item-meta">${frappe.utils.escape_html(data.item_sources_note || '')}</div>
					<div class="vp-item-meta">${__('Window')}: ${frappe.datetime.str_to_user(data.date_from)} → ${frappe.datetime.str_to_user(data.date_to)}${data.principal ? ` · ${__('Principal')}: ${frappe.utils.escape_html(data.principal)}` : ''} · ${modeLabel}${supplierCountMeta} · ${__('Basis')}: ${frappe.utils.escape_html(data.stock_basis || 'bin')}</div>
					${truncatedNote}
					${fifoNote}
					${unresolvedBlock}
				</div>
			</div>
			<div class="vp-grid">
				${this.card('stock', 'package', __('Stock Value (at Cost)'), this.money(summary.stock_value), `${this.number(summary.stock_qty)} ${__('qty')} · ${this.number(summary.stock_item_count)} ${__('items')}`)}
				${this.card('receipt', 'tag', __('Stock Value (Incl Taxes)'), this.money(summary.stock_value_incl_tax), `${this.number(summary.stock_qty)} ${__('qty')} · ${__('estimated')}`)}
				${this.card('payable', 'percent', __('Tax on Stock'), this.money(summary.stock_tax_amount), __('Incl taxes − at cost'))}
				${includeOrigin ? this.card('origin', 'layers', __('Origin Stock Value'), this.money(summary.origin_stock_value), `${this.number(summary.origin_stock_qty)} ${__('qty')} · ${__('FIFO')}`) : ''}
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
					<input type="search" class="form-control input-xs vsp-search" data-vsp-search placeholder="${__('Search item / supplier / warehouse...')}" />
					<div class="vsp-meta">${__('Rows')}: <strong data-vsp-visible-count>${this.number(rows.length)}</strong></div>
				</div>
				${this.render_stock_table(rows, groupByWarehouse, includeSupplier, includeOrigin)}
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

	render_stock_table(rows, groupByWarehouse, includeSupplier, includeOrigin) {
		if (!rows.length) {
			return `
				<div class="vp-empty">
					<div class="vp-state-icon">${frappe.utils.icon('package', 'md')}</div>
					${__('No stock rows found for the current filters.')}
				</div>
			`;
		}

		const supplierHeaders = includeSupplier
			? `<th>${__('Supplier')}</th>`
			: '';
		const warehouseHeaders = groupByWarehouse
			? `<th>${__('Warehouse')}</th><th>${__('Branch')}</th>`
			: '';
		const originHeaders = includeOrigin
			? `<th class="vp-num">${__('Origin Stock Qty')}</th><th class="vp-num">${__('Origin Stock Value')}</th>`
			: '';

		const body = rows
			.map((row) => {
				const searchBits = [
					row.supplier,
					row.supplier_name,
					row.item_code,
					row.item_name,
					row.barcode1,
					row.barcode2,
					row.warehouse,
					row.branch,
				]
					.filter(Boolean)
					.join(' ');
				const supplierCells = includeSupplier
					? `<td>
							<div class="vp-item-title">${frappe.utils.escape_html(row.supplier || '')}</div>
							<div class="vp-item-meta">${frappe.utils.escape_html(row.supplier_name || '')}</div>
						</td>`
					: '';
				const warehouseCells = groupByWarehouse
					? `<td>${frappe.utils.escape_html(row.warehouse || '')}</td><td>${frappe.utils.escape_html(row.branch || '')}</td>`
					: '';
				const originCells = includeOrigin
					? `<td class="vp-num">${this.number(row.origin_stock_qty)}</td><td class="vp-num">${this.money(row.origin_stock_value)}</td>`
					: '';
				return `
				<tr data-vsp-row="${frappe.utils.escape_html(searchBits)}">
					${supplierCells}
					<td>
						<div class="vp-item-title">${frappe.utils.escape_html(row.item_code)}</div>
						<div class="vp-item-meta">${frappe.utils.escape_html(row.item_name || '')}</div>
					</td>
					<td>${frappe.utils.escape_html(row.barcode1 || '')}</td>
					<td>${frappe.utils.escape_html(row.barcode2 || '')}</td>
					${warehouseCells}
					<td class="vp-num">${this.number(row.stock_qty)}</td>
					<td class="vp-num">${this.money(row.stock_value)}</td>
					<td class="vp-num">${this.money(row.stock_value_incl_tax)}</td>
					<td class="vp-num">${this.money(row.stock_tax_amount)}</td>
					${originCells}
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
							${supplierHeaders}
							<th>${__('Item')}</th>
							<th>${__('Barcode 1')}</th>
							<th>${__('Barcode 2')}</th>
							${warehouseHeaders}
							<th class="vp-num">${__('Stock Qty')}</th>
							<th class="vp-num">${__('Stock Value (at Cost)')}</th>
							<th class="vp-num">${__('Stock Value (Incl Taxes)')}</th>
							<th class="vp-num">${__('Tax on Stock')}</th>
							${originHeaders}
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
