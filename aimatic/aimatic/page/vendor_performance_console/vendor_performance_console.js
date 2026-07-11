
frappe.provide('aimatic');

frappe.pages['vendor-performance-console'].on_page_load = function (wrapper) {
    new aimatic.VendorPerformancePage(wrapper);
};

aimatic.VendorPerformancePage = class VendorPerformancePage {
    constructor(wrapper) {
        this.wrapper = $(wrapper);
        this.page = frappe.ui.make_app_page({
            parent: wrapper,
            title: __('Vendor Performance'),
            single_column: true,
        });
        this.page.set_primary_action(__('Refresh'), () => this.refresh());
        this.drill = {};
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
        this.lookback_field = this.page.add_field({
            label: __('Days'),
            fieldname: 'lookback_days',
            fieldtype: 'Int',
            default: 30,
            reqd: 1,
            change: () => this.refresh_if_ready(),
        });
    }

    build_layout() {
        this.$container = $('<div class="vendor-performance-page"></div>').appendTo(this.page.body);
        this.render_empty_state();
    }

    bind_events() {
        this.$container.on('click', '[data-doctype][data-name]', (event) => {
            event.preventDefault();
            const $target = $(event.currentTarget);
            frappe.set_route('Form', $target.data('doctype'), $target.data('name'));
        });

        this.$container.on('click', '[data-vp-load]', (event) => {
            event.preventDefault();
            this.load_drill_section($(event.currentTarget).data('vp-load'));
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
        this.refresh_if_ready();
    }

    get_filters() {
        return {
            supplier: this.supplier_field.get_value(),
            company: this.company_field.get_value(),
            lookback_days: parseInt(this.lookback_field.get_value() || 30, 10) || 30,
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

        this.drill = {};
        this.render_loading();
        try {
            const data = await frappe.xcall('aimatic.vendor_performance.api.get_vendor_performance_summary', filters);
            this.currency = data.currency;
            this.render_summary(data);
        } catch (error) {
            console.error(error);
            this.render_error(error);
        }
    }

    async load_drill_section(section) {
        const spec = this.drill_sections[section];
        const $body = this.$container.find(`[data-vp-section="${section}"] .vp-section-body`);
        $body.html(`<div class="vp-loading"><div class="vp-state-icon">${frappe.utils.icon('loader', 'md')}</div>${__('Loading...')}</div>`);
        try {
            const data = await frappe.xcall(spec.method, spec.get_args());
            this.drill[section] = data;
            $body.html(spec.render(data));
        } catch (error) {
            console.error(error);
            const message = frappe.utils.escape_html(error?.message || __('Unable to load this section.'));
            $body.html(`<div class="vp-empty text-danger">${message}</div>`);
        }
    }

    render_empty_state() {
        this.$container.html(`
            <div class="vp-empty">
                <div class="vp-state-icon">${frappe.utils.icon('search', 'md')}</div>
                ${__('Choose a supplier and company to load vendor performance.')}
            </div>
        `);
    }

    render_loading() {
        this.$container.html(`
            <div class="vp-loading">
                <div class="vp-state-icon">${frappe.utils.icon('loader', 'md')}</div>
                ${__('Loading vendor performance...')}
            </div>
        `);
    }

    render_error(error) {
        const message = frappe.utils.escape_html(error?.message || __('Unable to load vendor performance.'));
        this.$container.html(`
            <div class="vp-empty text-danger">
                <div class="vp-state-icon">${frappe.utils.icon('alert-triangle', 'md')}</div>
                ${message}
            </div>
        `);
    }

    render_summary(data) {
        const summary = data.summary || {};
        const lastPayment = data.last_payment;
        const filters = this.get_filters();

        this.drill_sections = {
            origin: {
                method: 'aimatic.vendor_performance.api.get_vendor_origin_stock_detail',
                get_args: () => ({ supplier: filters.supplier, company: filters.company, item_limit: 15 }),
                render: (result) => this.render_origin_section(result, data),
            },
            stock: {
                method: 'aimatic.vendor_performance.api.get_vendor_stock_detail',
                get_args: () => ({ supplier: filters.supplier, company: filters.company, lookback_days: filters.lookback_days, item_limit: 15 }),
                render: (result) => this.renderStockTable(result.stock_items || []),
            },
            purchases: {
                method: 'aimatic.vendor_performance.api.get_vendor_recent_purchases',
                get_args: () => ({ supplier: filters.supplier, company: filters.company, limit: 3 }),
                render: (result) => this.renderPurchaseTable(result.recent_purchases || []),
            },
            sales: {
                method: 'aimatic.vendor_performance.api.get_vendor_recent_sales',
                get_args: () => ({ supplier: filters.supplier, company: filters.company, lookback_days: filters.lookback_days, limit: 3 }),
                render: (result) => this.renderSalesTable(result.recent_sales || []),
            },
            ratios: {
                method: 'aimatic.vendor_performance.api.get_vendor_abnormal_ratios',
                get_args: () => ({ supplier: filters.supplier, company: filters.company, lookback_days: 90 }),
                render: (result) => this.render_ratios_section(result),
            },
        };

        const noteHtml = `
            <div class="vp-note">
                <div class="vp-note-icon">${frappe.utils.icon('info', 'sm')}</div>
                <div class="vp-note-body">
                    <div><strong>${__('Stock values')}:</strong> ${frappe.utils.escape_html(data.stock_definition_note || '')}</div>
                    <div><strong>${__('Cost of goods sold')}:</strong> ${frappe.utils.escape_html(data.cogs_definition_note || '')}</div>
                    <div class="vp-item-meta">${frappe.utils.escape_html(data.item_sources_note || '')}</div>
                </div>
            </div>
        `;

        this.$container.html(`
            ${noteHtml}
            <div class="vp-grid">
                ${this.card('stock', 'package', __('Supplier-SKU Exposure Stock (at Cost)'), this.money(summary.stock_value), `${this.number(summary.stock_qty)} ${__('qty')} · ${this.number(summary.stock_item_count)} ${__('items')}`)}
                ${this.card('revenue', 'trending-up', __('Sales Revenue (Window)'), this.money(summary.sales_amount), `${this.number(summary.sales_qty)} ${__('qty')} · ${this.number(summary.sales_doc_count)} ${__('docs')}`)}
                ${this.card('cogs', 'shopping-bag', __('Cost of Goods Sold (Window)'), this.money(summary.cogs_amount), `${this.number(summary.cogs_qty)} ${__('qty')}`)}
                ${this.card('margin', 'percent', __('Gross Margin (Window)'), this.money(summary.gross_margin_amount), `${this.percent(summary.gross_margin_pct)} ${__('of revenue')}`, summary.gross_margin_amount)}
                ${this.card('purchase', 'truck', __('Purchase Invoices (Window, at Cost)'), this.money(summary.purchase_amount), `${this.number(summary.purchase_qty)} ${__('qty')} · ${this.number(summary.purchase_doc_count)} ${__('docs')}`)}
                ${this.card('receipt', 'package-check', __('Purchase Receipts (Window, at Cost)'), this.money(summary.purchase_receipt_amount), `${this.number(summary.purchase_receipt_qty)} ${__('qty')} · ${this.number(summary.purchase_receipt_doc_count)} ${__('docs')}`)}
                ${this.card('payable', 'wallet', __('Outstanding Payable'), this.money(summary.outstanding_amount), `${this.number(summary.outstanding_invoice_count)} ${__('open invoices')}`)}
                ${this.card('payment', 'credit-card', __('Last Payment'), lastPayment ? this.money(lastPayment.amount) : __('No payment'), lastPayment ? `${frappe.datetime.str_to_user(lastPayment.posting_date)} · ${frappe.utils.escape_html(lastPayment.payment_entry)}` : __('No submitted supplier payment found'))}
            </div>
            ${this.drill_section('origin', 'layers', __('Exact Remaining Stock by Vendor Origin (at Cost)'), `<span class="vp-badge">${__('Phase 2 FIFO attribution')}</span>`, data.origin_stock_definition_note, __('Compute exact vendor-origin stock'))}
            ${this.drill_section('stock', 'package', __('Current Stock of Supplier-Linked SKUs'), `<span class="vp-badge">${__('Top 15 by stock value')}</span>`, __('Loads current stock (at cost) plus sales revenue, cost of goods sold, and margin in the selected window for each supplier-linked SKU.'), __('Load stock items'))}
            ${this.drill_section('purchases', 'shopping-cart', __('Last 3 Purchase Invoices'), '', __('Loads the 3 most recent submitted purchase invoices for this supplier.'), __('Load recent purchases'))}
            ${this.drill_section('sales', 'receipt', __('Last 3 Sales'), `<span class="vp-badge">${__('Within selected window')}</span>`, __('Loads the 3 most recent sales of supplier-linked SKUs within the selected window, with cost of goods sold and margin.'), __('Load recent sales'))}
            ${this.drill_section('ratios', 'alert-triangle', __('Abnormal Purchase:Sale Ratios'), `<span class="vp-badge">${__('Last 90 days')}</span>`, __('Flags supplier-linked SKUs where units purchased are unusually high relative to units sold - a downstream signal for short deliveries or shrinkage that would not show up as a paperwork mismatch. Flagged automatically per item against this vendor\'s own item set, not a manually configured threshold.'), __('Check for abnormal ratios'))}
        `);
    }

    drill_section(key, icon, title, badgeHtml, description, buttonLabel) {
        return `
            <div class="vp-section" data-vp-section="${key}">
                <div class="vp-section-header">
                    <div class="vp-section-header-left">
                        <div class="vp-section-icon">${frappe.utils.icon(icon, 'sm')}</div>
                        <h4>${frappe.utils.escape_html(title)}</h4>
                    </div>
                    ${badgeHtml || ''}
                </div>
                <div class="vp-section-body">
                    <div class="vp-drill-placeholder">
                        <div class="vp-state-icon">${frappe.utils.icon(icon, 'md')}</div>
                        <p>${frappe.utils.escape_html(description || '')}</p>
                        <button class="btn btn-default btn-sm" data-vp-load="${key}">
                            ${frappe.utils.icon('play', 'xs')}
                            ${buttonLabel}
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    render_origin_section(result, summaryData) {
        if (result.too_large) {
            return `
                <div class="vp-empty">
                    <div class="vp-state-icon">${frappe.utils.icon('alert-triangle', 'md')}</div>
                    ${__('This vendor has {0} stock ledger entries across its linked items, too many to replay on demand.', [this.number(result.sle_row_count)])}
                    <div class="vp-item-meta">${frappe.utils.escape_html(summaryData.origin_stock_definition_note || '')}</div>
                </div>
            `;
        }
        const summary = result.summary || {};
        return `
            <div class="vp-grid vp-drill-summary">
                ${this.card('stock', 'layers', __('Exact Vendor-Origin Stock (at Cost)'), this.money(summary.stock_value), `${this.number(summary.stock_qty)} ${__('qty')} · ${this.number(summary.item_count)} ${__('items')}`)}
            </div>
            ${this.renderOriginStockTable(result.origin_stock_items || [], summary)}
        `;
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

    renderOriginStockTable(rows, summary) {
        const unattributed = `${this.number(summary.unattributed_qty)} ${__('qty')} · ${this.money(summary.unattributed_value)}`;
        if (!rows.length) {
            return `
                <div class="vp-empty">
                    <div class="vp-state-icon">${frappe.utils.icon('layers', 'md')}</div>
                    ${__('No remaining attributed stock from this supplier was found.')}
                    <div class="vp-item-meta">${__('Unattributed incoming stock currently in FIFO pool')}: ${unattributed}</div>
                </div>
            `;
        }
        const body = rows.map((row) => `
            <tr>
                <td>
                    <div class="vp-item-title">${frappe.utils.escape_html(row.item_code)}</div>
                    <div class="vp-item-meta">${frappe.utils.escape_html(row.item_name || '')}</div>
                </td>
                <td class="vp-num">${this.number(row.origin_stock_qty)}</td>
                <td class="vp-num">${this.money(row.origin_stock_value)}</td>
                <td>${row.last_origin_date ? frappe.datetime.str_to_user(row.last_origin_date) : ''}</td>
                <td>${row.last_origin_doc ? this.docLink(row.last_origin_doctype || 'Purchase Receipt', row.last_origin_doc) : ''}</td>
            </tr>
        `).join('');
        return `
            <div class="vp-item-meta" style="margin-bottom:8px;">${__('Unattributed incoming stock currently in FIFO pool')}: ${unattributed}</div>
            <div class="vp-table-wrap">
                <table class="vp-table">
                    <thead>
                        <tr>
                            <th>${__('Item')}</th>
                            <th class="vp-num">${__('Attributed Qty')}</th>
                            <th class="vp-num">${__('Attributed Value')}</th>
                            <th>${__('Last Origin Date')}</th>
                            <th>${__('Last Origin Doc')}</th>
                        </tr>
                    </thead>
                    <tbody>${body}</tbody>
                </table>
            </div>
        `;
    }

    render_ratios_section(result) {
        const items = result.items || [];
        const flaggedCount = result.flagged_count || 0;
        const methodLabel = result.method === 'iqr'
            ? __('Statistical outliers vs. this vendor\'s own item set (IQR method)')
            : __('Too few items with sales for statistics - fixed sanity multiple used instead');
        const summaryHtml = `
            <div class="vp-note" style="margin-bottom:14px;">
                <div class="vp-note-icon">${frappe.utils.icon(flaggedCount ? 'alert-triangle' : 'info', 'sm')}</div>
                <div class="vp-note-body">
                    <div><strong>${__('Flagged items')}:</strong> ${flaggedCount} ${__('of')} ${items.length}</div>
                    <div class="vp-item-meta">${__('Method')}: ${methodLabel}. ${__('Normal range for this vendor')}: 0 - ${this.number(result.upper_fence)} (${__('median')} ${this.number(result.median_ratio)})</div>
                </div>
            </div>
        `;
        return summaryHtml + this.renderRatiosTable(items);
    }

    renderRatiosTable(rows) {
        if (!rows.length) {
            return `
                <div class="vp-empty">
                    <div class="vp-state-icon">${frappe.utils.icon('alert-triangle', 'md')}</div>
                    ${__('No purchase or sale activity found for this supplier\'s linked items in the selected window.')}
                </div>
            `;
        }
        const body = rows.map((row) => `
            <tr class="${row.is_outlier ? 'vp-row-flagged' : ''}">
                <td>
                    <div class="vp-item-title">${frappe.utils.escape_html(row.item_code)}</div>
                    <div class="vp-item-meta">${frappe.utils.escape_html(row.item_name || '')}</div>
                </td>
                <td class="vp-num">${this.number(row.purchased_qty)}</td>
                <td class="vp-num">${this.number(row.sold_qty)}</td>
                <td class="vp-num">${row.ratio === null ? '—' : this.number(row.ratio)}</td>
                <td>${row.is_outlier
                    ? `<span class="vp-badge vp-badge-danger">${frappe.utils.icon('alert-triangle', 'xs')} ${__('Flagged')}</span>`
                    : `<span class="vp-badge">${__('Normal')}</span>`}
                </td>
                <td class="vp-item-meta">${frappe.utils.escape_html(row.flag_reason || '')}</td>
            </tr>
        `).join('');
        return `
            <div class="vp-table-wrap">
                <table class="vp-table">
                    <thead>
                        <tr>
                            <th>${__('Item')}</th>
                            <th class="vp-num">${__('Purchased Qty')}</th>
                            <th class="vp-num">${__('Sold Qty')}</th>
                            <th class="vp-num">${__('Ratio')}</th>
                            <th>${__('Status')}</th>
                            <th>${__('Reason')}</th>
                        </tr>
                    </thead>
                    <tbody>${body}</tbody>
                </table>
            </div>
        `;
    }

    renderStockTable(rows) {
        if (!rows.length) {
            return `
                <div class="vp-empty">
                    <div class="vp-state-icon">${frappe.utils.icon('package', 'md')}</div>
                    ${__('No current stock found for this supplier-linked SKU set.')}
                </div>
            `;
        }
        const body = rows.map((row) => `
            <tr>
                <td>
                    <div class="vp-item-title">${frappe.utils.escape_html(row.item_code)}</div>
                    <div class="vp-item-meta">${frappe.utils.escape_html(row.item_name || '')}</div>
                </td>
                <td class="vp-num">${this.number(row.stock_qty)}</td>
                <td class="vp-num">${this.money(row.stock_value)}</td>
                <td class="vp-num">${this.number(row.sales_qty_in_window)}</td>
                <td class="vp-num">${this.money(row.sales_amount_in_window)}</td>
                <td class="vp-num">${this.money(row.cogs_amount_in_window)}</td>
                <td class="vp-num ${this.marginClass(row.gross_margin_in_window)}">${this.money(row.gross_margin_in_window)}</td>
                <td>${row.last_purchase_date ? frappe.datetime.str_to_user(row.last_purchase_date) : ''}</td>
                <td>${row.last_purchase_doc ? this.docLink(row.last_purchase_doctype || 'Purchase Invoice', row.last_purchase_doc) : ''}</td>
                <td class="vp-num">${row.last_purchase_rate ? this.money(row.last_purchase_rate) : ''}</td>
            </tr>
        `).join('');
        return `
            <div class="vp-table-wrap">
                <table class="vp-table">
                    <thead>
                        <tr>
                            <th>${__('Item')}</th>
                            <th class="vp-num">${__('Stock Qty (at Cost)')}</th>
                            <th class="vp-num">${__('Stock Value (at Cost)')}</th>
                            <th class="vp-num">${__('Sales Qty')}</th>
                            <th class="vp-num">${__('Sales Revenue')}</th>
                            <th class="vp-num">${__('Cost of Goods Sold')}</th>
                            <th class="vp-num">${__('Gross Margin')}</th>
                            <th>${__('Last Purchase')}</th>
                            <th>${__('Last Purchase Doc')}</th>
                            <th class="vp-num">${__('Last Purchase Rate')}</th>
                        </tr>
                    </thead>
                    <tbody>${body}</tbody>
                </table>
            </div>
        `;
    }

    renderPurchaseTable(rows) {
        if (!rows.length) {
            return `
                <div class="vp-empty">
                    <div class="vp-state-icon">${frappe.utils.icon('shopping-cart', 'md')}</div>
                    ${__('No submitted purchases found for this supplier.')}
                </div>
            `;
        }
        const body = rows.map((row) => `
            <tr>
                <td>${this.docLink(row.doctype, row.name)}</td>
                <td>${frappe.datetime.str_to_user(row.posting_date)}</td>
                <td>${frappe.utils.escape_html(row.bill_no || '')}</td>
                <td class="vp-num">${this.money(row.amount)}</td>
                <td class="vp-num">${this.money(row.outstanding_amount)}</td>
                <td>${frappe.utils.escape_html(row.status || '')}</td>
            </tr>
        `).join('');
        return `
            <div class="vp-table-wrap">
                <table class="vp-table">
                    <thead>
                        <tr>
                            <th>${__('Document')}</th>
                            <th>${__('Date')}</th>
                            <th>${__('Bill No')}</th>
                            <th class="vp-num">${__('Amount')}</th>
                            <th class="vp-num">${__('Outstanding')}</th>
                            <th>${__('Status')}</th>
                        </tr>
                    </thead>
                    <tbody>${body}</tbody>
                </table>
            </div>
        `;
    }

    renderSalesTable(rows) {
        if (!rows.length) {
            return `
                <div class="vp-empty">
                    <div class="vp-state-icon">${frappe.utils.icon('receipt', 'md')}</div>
                    ${__('No submitted sales found for this supplier-linked SKU set in the selected window.')}
                </div>
            `;
        }
        const body = rows.map((row) => `
            <tr>
                <td>${this.docLink(row.doctype, row.name)}</td>
                <td>${frappe.datetime.str_to_user(row.posting_date)}</td>
                <td>${frappe.utils.escape_html(row.party || '')}</td>
                <td class="vp-num">${this.money(row.vendor_amount)}</td>
                <td class="vp-num">${this.money(row.cogs_amount)}</td>
                <td class="vp-num ${this.marginClass(row.gross_margin)}">${this.money(row.gross_margin)}</td>
                <td class="vp-num">${this.money(row.invoice_amount)}</td>
            </tr>
        `).join('');
        return `
            <div class="vp-table-wrap">
                <table class="vp-table">
                    <thead>
                        <tr>
                            <th>${__('Document')}</th>
                            <th>${__('Date')}</th>
                            <th>${__('Customer')}</th>
                            <th class="vp-num">${__('Supplier-SKU Revenue')}</th>
                            <th class="vp-num">${__('Cost of Goods Sold')}</th>
                            <th class="vp-num">${__('Gross Margin')}</th>
                            <th class="vp-num">${__('Invoice Total')}</th>
                        </tr>
                    </thead>
                    <tbody>${body}</tbody>
                </table>
            </div>
        `;
    }

    docLink(doctype, name) {
        const safeDoctype = frappe.utils.escape_html(doctype);
        const safeName = frappe.utils.escape_html(name);
        return `<a href="#" data-doctype="${safeDoctype}" data-name="${safeName}">${safeName}</a>`;
    }

    marginClass(value) {
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
