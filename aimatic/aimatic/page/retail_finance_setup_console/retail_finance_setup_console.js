frappe.provide('aimatic');

frappe.pages['retail-finance-setup-console'].on_page_load = function (wrapper) {
    new aimatic.RetailFinanceSetupPage(wrapper);
};

aimatic.RetailFinanceSetupPage = class RetailFinanceSetupPage {
    constructor(wrapper) {
        this.page = frappe.ui.make_app_page({
            parent: wrapper,
            title: __('Retail Finance Setup'),
            single_column: true,
        });
        this.page.set_primary_action(__('Refresh checks'), () => this.refresh());
        this.can_initialize = (frappe.user_roles || []).some((role) => ['Accounts Manager', 'System Manager'].includes(role));
        this.build_filters();
        this.$root = $('<div class="retail-finance-setup"></div>').appendTo(this.page.body);
        this.bind_events();
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
            change: () => this.refresh(),
        });
        this.status_field = this.page.add_field({
            label: __('Readiness'),
            fieldname: 'readiness',
            fieldtype: 'Select',
            options: ['', 'Blocked', 'Warning', 'Pass', 'Partial', 'Planned'].join('\n'),
            change: () => this.render_capabilities(),
        });
        this.phase_field = this.page.add_field({
            label: __('Phase'),
            fieldname: 'phase',
            fieldtype: 'Select',
            options: '',
            change: () => this.render_capabilities(),
        });
    }

    bind_events() {
        this.$root.on('click', '[data-rf-route]', (event) => {
            const route = $(event.currentTarget).attr('data-rf-route');
            if (route) frappe.set_route(...route.split('/'));
        });
        this.$root.on('click', '[data-rf-details]', (event) => {
            const id = $(event.currentTarget).attr('data-rf-details');
            this.$root.find(`[data-rf-detail-panel="${CSS.escape(id)}"]`).toggleClass('hidden');
        });
        this.$root.on('click', '[data-rf-initialize-price-lists]', () => this.initialize_branch_price_lists());
    }

    async refresh() {
        const company = this.company_field.get_value();
        if (!company) {
            this.render_state(__('Choose a company to run the finance readiness checks.'));
            return;
        }
        this.render_state(__('Running read-only readiness checks...'), 'loader');
        try {
            this.data = await frappe.xcall('aimatic.retail_finance_setup.api.get_readiness', { company });
            const phases = [...new Set(this.data.capabilities.map((row) => row.phase))];
            const current = this.phase_field.get_value();
            this.phase_field.df.options = ['', ...phases].join('\n');
            this.phase_field.refresh();
            if (current && phases.includes(current)) this.phase_field.set_value(current);
            this.render();
        } catch (error) {
            console.error(error);
            const message = error && error.message ? error.message : __('Unable to load finance readiness.');
            this.render_state(message, 'alert-triangle', true);
        }
    }

    render_state(message, icon = 'search', danger = false) {
        if (!this.$root) return;
        this.$root.html(`
            <div class="rf-state ${danger ? 'text-danger' : ''}">
                ${frappe.utils.icon(icon, 'md')}
                <span>${frappe.utils.escape_html(message)}</span>
            </div>
        `);
    }

    render() {
        const data = this.data;
        const readinessClass = data.forward_operations_ready ? 'pass' : 'blocked';
        const readinessText = data.forward_operations_ready
            ? __('Foundation ready for forward operations')
            : __('Critical setup requires attention');
        this.$root.html(`
            <section class="rf-hero">
                <div>
                    <div class="rf-eyebrow">${__('Registry version')} ${frappe.utils.escape_html(data.registry_version)}</div>
                    <h2>${frappe.utils.escape_html(data.company)}</h2>
                    <p>${frappe.utils.escape_html(data.cutover_note)}</p>
                </div>
                <span class="rf-badge rf-${readinessClass}">${readinessText}</span>
            </section>
            <section class="rf-summary">
                ${this.summary_card('blocked', __('Blocked'), data.counts.blocked || 0)}
                ${this.summary_card('warning', __('Warnings'), data.counts.warning || 0)}
                ${this.summary_card('pass', __('Passing'), data.counts.pass || 0)}
                ${this.summary_card('partial', __('Partial'), data.counts.partial || 0)}
                ${this.summary_card('planned', __('Planned / separate'), data.counts.planned || 0)}
            </section>
            <section class="rf-checks">
                <h3>${__('Foundation checks')}</h3>
                <div class="rf-check-grid">${data.checks.map((check) => this.check_card(check)).join('')}</div>
            </section>
            <section class="rf-capabilities">
                <div class="rf-section-heading">
                    <div>
                        <h3>${__('Complete capability register')}</h3>
                        <p>${__('Working, partial, and missing features remain visible here so future releases cannot silently omit them.')}</p>
                    </div>
                    <span>${data.capabilities.length} ${__('capabilities')}</span>
                </div>
                <div data-rf-capability-list></div>
            </section>
        `);
        this.render_capabilities();
    }

    summary_card(status, label, value) {
        return `<div class="rf-summary-card rf-summary-${status}"><strong>${value}</strong><span>${label}</span></div>`;
    }

    badge(status) {
        const labels = { blocked: __('Blocked'), warning: __('Warning'), pass: __('Pass'), partial: __('Partial'), planned: __('Planned'), info: __('Info') };
        return `<span class="rf-badge rf-${status}">${labels[status] || status}</span>`;
    }

    check_card(check) {
        const details = (check.details || []).map((detail) => `<li>${frappe.utils.escape_html(detail)}</li>`).join('');
        const initializeAction = check.id === 'stores' && this.can_initialize
            ? `<button class="btn btn-xs btn-primary" data-rf-initialize-price-lists>${__('Initialize branch price lists')}</button>`
            : '';
        return `
            <article class="rf-check-card">
                <div class="rf-card-title"><strong>${frappe.utils.escape_html(check.label)}</strong>${this.badge(check.status)}</div>
                <p>${frappe.utils.escape_html(check.message)}</p>
                ${details ? `<ul>${details}</ul>` : ''}
                <div class="rf-actions">
                    ${initializeAction}
                    ${check.route ? `<button class="btn btn-xs btn-default" data-rf-route="${frappe.utils.escape_html(check.route)}">${__('Open relevant records')}</button>` : ''}
                </div>
            </article>
        `;
    }

    initialize_branch_price_lists() {
        const company = this.company_field.get_value();
        if (!company) return;
        frappe.confirm(
            __('Create and link a selling-only Price List for every uninitialized branch in {0}?', [company]),
            async () => {
                frappe.dom.freeze(__('Initializing branch price lists...'));
                try {
                    const result = await frappe.xcall(
                        'aimatic.retail_finance_setup.api.initialize_branch_selling_price_lists',
                        { company }
                    );
                    frappe.show_alert({
                        message: __('Initialized {0}; already configured {1}.', [result.initialized_count, result.already_configured_count]),
                        indicator: 'green',
                    }, 7);
                    await this.refresh();
                } catch (error) {
                    console.error(error);
                    frappe.msgprint({
                        title: __('Branch price-list initialization failed'),
                        message: error && error.message ? error.message : __('Unable to initialize branch price lists.'),
                        indicator: 'red',
                    });
                } finally {
                    frappe.dom.unfreeze();
                }
            }
        );
    }

    render_capabilities() {
        if (!this.data || !this.$root) return;
        const status = (this.status_field.get_value() || '').toLowerCase();
        const phase = this.phase_field.get_value();
        const rows = this.data.capabilities.filter((row) => (!status || row.readiness_status === status) && (!phase || row.phase === phase));
        const html = rows.length ? rows.map((row) => this.capability_card(row)).join('') : `<div class="rf-state">${__('No capabilities match these filters.')}</div>`;
        this.$root.find('[data-rf-capability-list]').html(`<div class="rf-capability-list">${html}</div>`);
    }

    capability_card(row) {
        const details = (row.check_details || []).map((detail) => `<li>${frappe.utils.escape_html(detail)}</li>`).join('');
        return `
            <article class="rf-capability-card">
                <div class="rf-card-title">
                    <div>
                        <strong>${frappe.utils.escape_html(row.label)}</strong>
                        <div class="rf-meta">${frappe.utils.escape_html(row.category)} · ${frappe.utils.escape_html(row.phase)} · v${frappe.utils.escape_html(row.version)} · ${frappe.utils.escape_html(row.implementation_status)}</div>
                    </div>
                    ${this.badge(row.readiness_status)}
                </div>
                <p>${frappe.utils.escape_html(row.description)}</p>
                <div class="rf-actions">
                    <button class="btn btn-xs btn-default" data-rf-details="${frappe.utils.escape_html(row.id)}">${__('Guidance')}</button>
                    ${row.route ? `<button class="btn btn-xs btn-default" data-rf-route="${frappe.utils.escape_html(row.route)}">${__('Open records')}</button>` : ''}
                </div>
                <div class="rf-detail hidden" data-rf-detail-panel="${frappe.utils.escape_html(row.id)}">
                    <strong>${__('Required practice')}</strong>
                    <p>${frappe.utils.escape_html(row.guidance)}</p>
                    <p><strong>${__('Current result')}:</strong> ${frappe.utils.escape_html(row.readiness_message)}</p>
                    ${details ? `<ul>${details}</ul>` : ''}
                </div>
            </article>
        `;
    }
};
