
frappe.provide('aimatic');

frappe.pages['sales-dashboard-console'].on_page_load = function (wrapper) {
    new aimatic.SalesDashboardPage(wrapper);
};

aimatic.SalesDashboardPage = class SalesDashboardPage {
    constructor(wrapper) {
        this.wrapper = $(wrapper);
        this.page = frappe.ui.make_app_page({
            parent: wrapper,
            title: __('Sales Dashboard'),
            single_column: true,
        });
        this.page.set_primary_action(__('Refresh'), () => this.refresh());
        this.auto_refresh_enabled = true;
        this.auto_refresh_interval_ms = 60000;
        this.build_filters();
        this.build_layout();
        this.bind_events();
        this.update_pause_action();
        this.start_auto_refresh();
        this.refresh();
    }

    build_filters() {
        this.company_field = this.page.add_field({
            label: __('Company'),
            fieldname: 'company',
            fieldtype: 'Link',
            options: 'Company',
            default: frappe.defaults.get_user_default('Company') || frappe.defaults.get_default('company'),
            reqd: 1,
            change: () => {
                if (this.branch_field.get_value()) this.branch_field.set_value('');
                this.refresh();
            },
        });
        this.branch_field = this.page.add_field({
            label: __('Branch'),
            fieldname: 'branch',
            fieldtype: 'Link',
            options: 'Branch',
            get_query: () => ({ filters: { company: this.company_field.get_value() } }),
            change: () => this.refresh(),
        });
        this.date_range_field = this.page.add_field({
            label: __('Date Range'),
            fieldname: 'date_range',
            fieldtype: 'Select',
            options: [__('Today'), __('Yesterday'), __('Last 7 Days'), __('Last 30 Days'), __('This Month'), __('Last Month'), __('Custom')].join('\n'),
            default: __('Today'),
            change: () => {
                this.toggle_custom_date_fields();
                this.refresh();
            },
        });
        this.date_from_field = this.page.add_field({
            label: __('From Date'),
            fieldname: 'date_from',
            fieldtype: 'Date',
            change: () => this.refresh(),
        });
        this.date_to_field = this.page.add_field({
            label: __('To Date'),
            fieldname: 'date_to',
            fieldtype: 'Date',
            change: () => this.refresh(),
        });
        this.toggle_custom_date_fields();
    }

    toggle_field(field, show) {
        if (field && field.wrapper) {
            $(field.wrapper).toggle(!!show);
        }
    }

    toggle_custom_date_fields() {
        const isCustom = this.date_range_field.get_value() === __('Custom');
        this.toggle_field(this.date_from_field, isCustom);
        this.toggle_field(this.date_to_field, isCustom);
    }

    build_layout() {
        this.$container = $('<div class="sales-dashboard-page"></div>').appendTo(this.page.body);
        this.render_loading();
    }

    bind_events() {
        this.$container.on('click', '[data-doctype][data-name]', (event) => {
            event.preventDefault();
            const $target = $(event.currentTarget);
            frappe.set_route('Form', $target.data('doctype'), $target.data('name'));
        });

        this.$container.on('click', '[data-sd-detail]', (event) => {
            event.preventDefault();
            this.open_kpi_detail($(event.currentTarget).data('sd-detail'));
        });

        this.$container.on('click', '[data-sd-profile]', (event) => {
            event.preventDefault();
            this.open_profile_detail($(event.currentTarget).data('sd-profile'));
        });
    }

    toDateStr(value) {
        return (value || '').slice(0, 10);
    }

    get_date_range() {
        const preset = this.date_range_field.get_value();
        const todayStr = frappe.datetime.get_today();
        if (preset === __('Yesterday')) {
            const y = this.toDateStr(frappe.datetime.add_days(todayStr, -1));
            return [y, y];
        }
        if (preset === __('Last 7 Days')) {
            return [this.toDateStr(frappe.datetime.add_days(todayStr, -6)), todayStr];
        }
        if (preset === __('Last 30 Days')) {
            return [this.toDateStr(frappe.datetime.add_days(todayStr, -29)), todayStr];
        }
        if (preset === __('This Month')) {
            return [this.toDateStr(frappe.datetime.month_start()), todayStr];
        }
        if (preset === __('Last Month')) {
            const thisMonthStart = this.toDateStr(frappe.datetime.month_start());
            const lastMonthStart = this.toDateStr(frappe.datetime.add_months(thisMonthStart, -1));
            const lastMonthEnd = this.toDateStr(frappe.datetime.add_days(thisMonthStart, -1));
            return [lastMonthStart, lastMonthEnd];
        }
        if (preset === __('Custom')) {
            return [this.date_from_field.get_value() || todayStr, this.date_to_field.get_value() || todayStr];
        }
        return [todayStr, todayStr];
    }

    get_filters() {
        const [date_from, date_to] = this.get_date_range();
        return {
            company: this.company_field.get_value(),
            branch: this.branch_field.get_value() || undefined,
            date_from,
            date_to,
        };
    }

    start_auto_refresh() {
        if (this.auto_refresh_timer) clearInterval(this.auto_refresh_timer);
        this.auto_refresh_timer = setInterval(() => {
            if (this.auto_refresh_enabled) this.refresh({ silent: true });
        }, this.auto_refresh_interval_ms);
    }

    update_pause_action() {
        this.page.set_secondary_action(
            this.auto_refresh_enabled ? __('Pause Auto-Refresh') : __('Resume Auto-Refresh'),
            () => {
                this.auto_refresh_enabled = !this.auto_refresh_enabled;
                this.update_pause_action();
            },
            this.auto_refresh_enabled ? 'pause' : 'play'
        );
    }

    async refresh(opts) {
        const silent = opts && opts.silent;
        const filters = this.get_filters();
        if (!filters.company) {
            this.render_empty_state(__('Choose a company to load the sales dashboard.'));
            return;
        }
        if (!silent) this.render_loading();
        try {
            const data = await frappe.xcall('aimatic.sales_dashboard.api.get_dashboard_summary', filters);
            this.last_summary = data;
            this.currency = data.currency;
            this.render_summary(data);
        } catch (error) {
            console.error(error);
            this.render_error(error);
        }
    }

    render_loading() {
        this.$container.html(`
            <div class="sd-empty">
                <div class="sd-state-icon">${frappe.utils.icon('loader', 'md')}</div>
                ${__('Loading sales dashboard...')}
            </div>
        `);
    }

    render_empty_state(message) {
        this.$container.html(`
            <div class="sd-empty">
                <div class="sd-state-icon">${frappe.utils.icon('search', 'md')}</div>
                ${frappe.utils.escape_html(message || __('No data to show.'))}
            </div>
        `);
    }

    render_error(error) {
        const message = frappe.utils.escape_html(error && error.message ? error.message : __('Unable to load the sales dashboard.'));
        this.$container.html(`
            <div class="sd-empty text-danger">
                <div class="sd-state-icon">${frappe.utils.icon('alert-triangle', 'md')}</div>
                ${message}
            </div>
        `);
    }

    render_summary(data) {
        const kpis = data.kpis || {};
        this.$container.html(`
            <div class="sd-status-bar">
                <span class="sd-last-updated" data-sd-last-updated></span>
            </div>
            <div class="sd-grid">
                ${this.card('revenue', 'trending-up', __('Net Sales'), this.money(kpis.net_sales), `${__('Selected range')}: ${frappe.datetime.str_to_user(data.date_from)} - ${frappe.datetime.str_to_user(data.date_to)}`)}
                ${this.card('stock', 'shopping-bag', __('Transactions'), this.number(kpis.txn_count), `${__('Avg. basket')}: ${this.money(kpis.average_basket)}`)}
                ${this.card('purchase', 'credit-card', __('Average Basket'), this.money(kpis.average_basket), __('Per sale, selected range'))}
                ${this.card('payable', 'rotate-ccw', __('Returns'), this.money(kpis.returns_amount), `${this.number(kpis.returns_count)} ${__('return(s)')}`, undefined, 'returns')}
                ${this.card('payment', 'clock', __('Active Shifts'), this.number(kpis.active_shift_count), __('Currently open, live totals'), undefined, 'active_shifts')}
                ${this.card('margin', 'award', __('Top Branch'), kpis.top_branch || __('No sales yet'), kpis.top_branch ? this.money(kpis.top_branch_amount) : '', undefined, 'top_branch')}
            </div>
            ${this.render_payment_mix(data.payment_split)}
            <div class="sd-charts-row">
                <div class="sd-chart-card">
                    <h4>${__('Sales Trend')} <span class="sd-item-meta">${__('Last {0} days', [data.trend_days || 14])}</span></h4>
                    <div class="sd-trend-chart"></div>
                </div>
                <div class="sd-chart-card">
                    <h4>${__('Branch Comparison')} <span class="sd-item-meta">${__('Selected range')}</span></h4>
                    <div class="sd-branch-chart"></div>
                </div>
            </div>
            <div class="sd-branches">${this.render_branch_sections(data.branches || [])}</div>
        `);
        this.render_trend_chart(data.trend || []);
        this.render_branch_chart(data.branch_comparison || []);
        this.update_last_updated();
    }

    render_payment_mix(paymentSplit) {
        if (!paymentSplit || !paymentSplit.length) return '';
        const total = paymentSplit.reduce((sum, row) => sum + flt(row.amount), 0);
        if (!total) return '';
        const bars = paymentSplit
            .map((row) => {
                const pct = total ? (flt(row.amount) / total) * 100 : 0;
                return `
                    <div class="sd-payment-row">
                        <div class="sd-payment-label">${frappe.utils.escape_html(row.mode_of_payment || __('Unknown'))}</div>
                        <div class="sd-payment-bar-wrap">
                            <div class="sd-payment-bar" style="width:${pct.toFixed(1)}%"></div>
                        </div>
                        <div class="sd-payment-value">${this.money(row.amount)} <span class="sd-item-meta">(${pct.toFixed(0)}%)</span></div>
                    </div>
                `;
            })
            .join('');
        return `
            <div class="sd-payment-mix">
                <h4>${__('Payment Mix')} <span class="sd-item-meta">${__('Selected range')}</span></h4>
                ${bars}
            </div>
        `;
    }

    render_trend_chart(trend) {
        const el = this.$container.find('.sd-trend-chart').get(0);
        if (!el) return;
        if (!trend.length || !trend.some((r) => flt(r.net_sales) !== 0)) {
            $(el).html(`<div class="sd-empty sd-empty-inline">${__('No sales recorded in this window yet.')}</div>`);
            return;
        }
        new frappe.Chart(el, {
            data: {
                labels: trend.map((r) => frappe.datetime.str_to_user(r.date)),
                datasets: [{ name: __('Net Sales'), values: trend.map((r) => flt(r.net_sales)) }],
            },
            type: 'line',
            height: 220,
            colors: ['#3b82f6'],
            lineOptions: { regionFill: 1 },
            tooltipOptions: { formatTooltipY: (d) => this.money(d) },
        });
    }

    render_branch_chart(branchComparison) {
        const el = this.$container.find('.sd-branch-chart').get(0);
        if (!el) return;
        if (!branchComparison.length) {
            $(el).html(`<div class="sd-empty sd-empty-inline">${__('No branches with sales in this window yet.')}</div>`);
            return;
        }
        new frappe.Chart(el, {
            data: {
                labels: branchComparison.map((r) => r.branch),
                datasets: [{ name: __('Net Sales'), values: branchComparison.map((r) => flt(r.net_sales)) }],
            },
            type: 'bar',
            height: 220,
            colors: ['#8b5cf6'],
            tooltipOptions: { formatTooltipY: (d) => this.money(d) },
        });
    }

    render_branch_sections(branches) {
        if (!branches.length) {
            return `
                <div class="sd-empty">
                    <div class="sd-state-icon">${frappe.utils.icon('inbox', 'md')}</div>
                    ${__('No branches or enabled POS Profiles are configured for this company yet.')}
                </div>
            `;
        }
        return branches.map((section) => this.render_branch_section(section)).join('');
    }

    render_branch_section(section) {
        const profilesHtml = section.profiles.length
            ? section.profiles.map((profile) => this.render_profile_card(profile)).join('')
            : `<div class="sd-empty sd-empty-inline">${__('No enabled POS Profiles for this branch.')}</div>`;
        return `
            <div class="sd-branch-section">
                <div class="sd-branch-heading">
                    <h3>${frappe.utils.escape_html(section.branch)}</h3>
                    <div class="sd-branch-total">${this.money(section.today_total)} <span class="sd-item-meta">${__('today')}</span></div>
                </div>
                <div class="sd-profile-grid">${profilesHtml}</div>
            </div>
        `;
    }

    render_profile_card(profile) {
        const shift = profile.shift;
        const statusBadge = shift
            ? `<span class="sd-badge sd-badge-open">${__('Open')} · ${frappe.utils.escape_html(shift.cashier_full_name)} · ${__('since')} ${frappe.datetime.str_to_user(shift.opened_at)}</span>`
            : `<span class="sd-badge sd-badge-closed">${__('Closed')}</span>`;
        const lastSale = profile.last_sale_at ? comment_when(profile.last_sale_at) : __('No sales yet today');
        const shiftLine = shift
            ? `<div class="sd-profile-shift-line">${__('Current shift')}: <strong>${this.money(shift.running_total)}</strong></div>`
            : '';
        return `
            <div class="sd-profile-card" data-sd-profile="${frappe.utils.escape_html(profile.pos_profile)}">
                <div class="sd-profile-top">
                    <div class="sd-profile-name">${frappe.utils.escape_html(profile.pos_profile)}</div>
                </div>
                <div class="sd-profile-value">${this.money(profile.today_sales)}</div>
                <div class="sd-profile-meta">${this.number(profile.txn_count)} ${__('transactions')} · ${lastSale}</div>
                ${shiftLine}
                <div class="sd-profile-status">${statusBadge}</div>
            </div>
        `;
    }

    card(accent, icon, label, value, subvalue, colorizeValue, detailKey) {
        let valueClass = '';
        if (colorizeValue !== undefined) {
            valueClass = flt(colorizeValue) < 0 ? ' sd-value-negative' : ' sd-value-positive';
        }
        const clickable = detailKey ? ' sd-card-clickable' : '';
        const detailAttr = detailKey ? ` data-sd-detail="${detailKey}"` : '';
        return `
            <div class="sd-card sd-accent-${accent}${clickable}"${detailAttr}>
                <div class="sd-card-top">
                    <div class="sd-card-label">${frappe.utils.escape_html(label)}</div>
                    <div class="sd-card-icon">${frappe.utils.icon(icon, 'sm')}</div>
                </div>
                <div class="sd-card-value${valueClass}">${value}</div>
                <div class="sd-card-subvalue">${frappe.utils.escape_html(subvalue || '')}</div>
            </div>
        `;
    }

    docLink(doctype, name) {
        const safeDoctype = frappe.utils.escape_html(doctype);
        const safeName = frappe.utils.escape_html(name);
        return `<a href="#" data-doctype="${safeDoctype}" data-name="${safeName}">${safeName}</a>`;
    }

    open_dialog(title) {
        const dialog = new frappe.ui.Dialog({ title, size: 'large' });
        dialog.$body.html(`
            <div class="sd-loading-inline">${frappe.utils.icon('loader', 'md')} ${__('Loading...')}</div>
        `);
        dialog.show();
        return dialog;
    }

    async open_kpi_detail(key) {
        const filters = this.get_filters();
        if (key === 'returns') {
            const dialog = this.open_dialog(__('Returns'));
            try {
                const data = await frappe.xcall('aimatic.sales_dashboard.api.get_returns_detail', filters);
                this.currency = data.currency;
                dialog.$body.html(this.render_returns_table(data.returns || []));
            } catch (error) {
                this.render_dialog_error(dialog, error);
            }
            return;
        }
        if (key === 'active_shifts') {
            const dialog = this.open_dialog(__('Active Shifts'));
            try {
                const data = await frappe.xcall('aimatic.sales_dashboard.api.get_active_shifts_detail', {
                    company: filters.company,
                    branch: filters.branch,
                });
                this.currency = data.currency;
                dialog.$body.html(this.render_shifts_table(data.active_shifts || []));
            } catch (error) {
                this.render_dialog_error(dialog, error);
            }
            return;
        }
        if (key === 'top_branch') {
            const dialog = this.open_dialog(__('Branch Comparison'));
            const rows = (this.last_summary && this.last_summary.branch_comparison) || [];
            dialog.$body.html(this.render_branch_comparison_table(rows));
        }
    }

    async open_profile_detail(posProfile) {
        const dialog = this.open_dialog(posProfile);
        try {
            const data = await frappe.xcall('aimatic.sales_dashboard.api.get_pos_profile_detail', { pos_profile: posProfile });
            this.currency = data.currency;
            dialog.$body.html(this.render_profile_detail(data));
            this.render_profile_hourly_chart(dialog, data.hourly || []);
        } catch (error) {
            this.render_dialog_error(dialog, error);
        }
    }

    render_dialog_error(dialog, error) {
        console.error(error);
        const message = frappe.utils.escape_html(error && error.message ? error.message : __('Unable to load this detail.'));
        dialog.$body.html(`<div class="sd-empty text-danger">${message}</div>`);
    }

    render_returns_table(rows) {
        if (!rows.length) {
            return `<div class="sd-empty sd-empty-inline">${__('No returns in the selected range.')}</div>`;
        }
        const body = rows
            .map(
                (row) => `
            <tr>
                <td>${this.docLink('POS Invoice', row.name)}</td>
                <td>${frappe.datetime.str_to_user(row.posting_date)}</td>
                <td>${frappe.utils.escape_html(row.branch || '')}</td>
                <td>${frappe.utils.escape_html(row.customer || '')}</td>
                <td>${row.return_against ? this.docLink('POS Invoice', row.return_against) : ''}</td>
                <td class="sd-num">${this.money(row.amount)}</td>
            </tr>
        `
            )
            .join('');
        return `
            <div class="sd-table-wrap">
                <table class="sd-table">
                    <thead>
                        <tr>
                            <th>${__('Return Invoice')}</th>
                            <th>${__('Date')}</th>
                            <th>${__('Branch')}</th>
                            <th>${__('Customer')}</th>
                            <th>${__('Against')}</th>
                            <th class="sd-num">${__('Amount')}</th>
                        </tr>
                    </thead>
                    <tbody>${body}</tbody>
                </table>
            </div>
        `;
    }

    render_shifts_table(rows) {
        if (!rows.length) {
            return `<div class="sd-empty sd-empty-inline">${__('No shifts are currently open.')}</div>`;
        }
        const body = rows
            .map(
                (row) => `
            <tr>
                <td>${frappe.utils.escape_html(row.branch || '')}</td>
                <td>${frappe.utils.escape_html(row.pos_profile || '')}</td>
                <td>${frappe.utils.escape_html(row.cashier_full_name || '')}</td>
                <td>${frappe.datetime.str_to_user(row.opened_at)}</td>
                <td class="sd-num">${this.number(row.txn_count)}</td>
                <td class="sd-num">${this.money(row.running_total)}</td>
            </tr>
        `
            )
            .join('');
        return `
            <div class="sd-table-wrap">
                <table class="sd-table">
                    <thead>
                        <tr>
                            <th>${__('Branch')}</th>
                            <th>${__('POS Profile')}</th>
                            <th>${__('Cashier')}</th>
                            <th>${__('Opened At')}</th>
                            <th class="sd-num">${__('Transactions')}</th>
                            <th class="sd-num">${__('Live Running Total')}</th>
                        </tr>
                    </thead>
                    <tbody>${body}</tbody>
                </table>
            </div>
        `;
    }

    render_branch_comparison_table(rows) {
        if (!rows.length) {
            return `<div class="sd-empty sd-empty-inline">${__('No branches with sales in this window yet.')}</div>`;
        }
        const body = rows
            .map(
                (row) => `
            <tr>
                <td>${frappe.utils.escape_html(row.branch)}</td>
                <td class="sd-num">${this.money(row.net_sales)}</td>
            </tr>
        `
            )
            .join('');
        return `
            <div class="sd-table-wrap">
                <table class="sd-table">
                    <thead>
                        <tr>
                            <th>${__('Branch')}</th>
                            <th class="sd-num">${__('Net Sales')}</th>
                        </tr>
                    </thead>
                    <tbody>${body}</tbody>
                </table>
            </div>
        `;
    }

    render_profile_detail(data) {
        const today = data.today_total || {};
        const yesterday = data.yesterday_total || {};
        const diff = flt(today.amount) - flt(yesterday.amount);
        const diffClass = diff < 0 ? 'sd-value-negative' : 'sd-value-positive';
        const paymentHtml = this.render_payment_mix(data.payment_split);
        const invoicesHtml = this.render_recent_invoices_table(data.recent_invoices || []);
        return `
            <div class="sd-grid sd-drill-summary">
                ${this.card('revenue', 'trending-up', data.is_today ? __('Today So Far') : __('Selected Day'), this.money(today.amount), `${this.number(today.txn_count)} ${__('transactions')}`)}
                ${this.card('purchase', 'rotate-ccw', __('Same Time Yesterday'), this.money(yesterday.amount), `${this.number(yesterday.txn_count)} ${__('transactions')}`)}
                ${this.card('margin', 'percent', __('Difference'), this.money(diff), '', diff)}
            </div>
            <div class="sd-chart-card">
                <h4>${__('Hourly Breakdown')}</h4>
                <div class="sd-hourly-chart"></div>
            </div>
            ${paymentHtml}
            <h4 class="sd-dialog-subheading">${__('Last 10 Invoices')}</h4>
            ${invoicesHtml}
        `;
    }

    render_profile_hourly_chart(dialog, hourly) {
        const el = dialog.$body.find('.sd-hourly-chart').get(0);
        if (!el) return;
        if (!hourly.length) {
            $(el).html(`<div class="sd-empty sd-empty-inline">${__('No sales recorded yet.')}</div>`);
            return;
        }
        const byHour = {};
        hourly.forEach((row) => {
            byHour[row.hour] = flt(row.amount);
        });
        const labels = [];
        const values = [];
        for (let hour = 0; hour < 24; hour++) {
            labels.push(`${hour}:00`);
            values.push(byHour[hour] || 0);
        }
        new frappe.Chart(el, {
            data: { labels, datasets: [{ name: __('Sales'), values }] },
            type: 'bar',
            height: 200,
            colors: ['#16a34a'],
            tooltipOptions: { formatTooltipY: (d) => this.money(d) },
        });
    }

    render_recent_invoices_table(rows) {
        if (!rows.length) {
            return `<div class="sd-empty sd-empty-inline">${__('No invoices yet.')}</div>`;
        }
        const body = rows
            .map(
                (row) => `
            <tr class="${row.is_return ? 'sd-row-return' : ''}">
                <td>${this.docLink('POS Invoice', row.name)}</td>
                <td>${frappe.datetime.str_to_user(row.posting_date)} ${frappe.utils.escape_html(row.posting_time || '').slice(0, 5)}</td>
                <td>${frappe.utils.escape_html(row.customer || '')}</td>
                <td class="sd-num">${this.money(row.grand_total)}</td>
            </tr>
        `
            )
            .join('');
        return `
            <div class="sd-table-wrap">
                <table class="sd-table">
                    <thead>
                        <tr>
                            <th>${__('Invoice')}</th>
                            <th>${__('Date/Time')}</th>
                            <th>${__('Customer')}</th>
                            <th class="sd-num">${__('Amount')}</th>
                        </tr>
                    </thead>
                    <tbody>${body}</tbody>
                </table>
            </div>
        `;
    }

    update_last_updated() {
        this.$container.find('[data-sd-last-updated]').text(`${__('Last updated')}: ${frappe.datetime.get_time ? frappe.datetime.get_time(frappe.datetime.now_datetime()) : new Date().toLocaleTimeString()}`);
    }

    money(value) {
        return format_currency(value || 0, this.currency);
    }

    number(value) {
        return format_number(value || 0, null, 0);
    }
};
