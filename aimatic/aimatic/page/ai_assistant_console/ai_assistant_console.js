/*
 * Phase 1: Smart Context Bar (Company, Branch, Date Range, folded into message
 * text since ask() has no structured filter params yet), role-aware suggested
 * questions, rich answer rendering (summary, KPI cards, charts, tables,
 * insights, warnings, follow-ups, sources). History bootstrap still renders
 * old turns as simple bubbles.
 * Phase 2: voice input (browser Web Speech API, no new backend - see
 * init_voice()/start_recording()), conversation management (see
 * conversation-panel code below - list/rename/pin/delete/resume, wired to
 * ai/api.py's start_conversation/list_conversations/etc), dynamic report
 * fallback (server-side only, aimatic.ai.dynamic_report - nothing to render
 * here beyond the generic table already handled by the rich-answer path).
 * NO dashboard save/pin, NO working export buttons yet (later phases).
 */

frappe.provide('aimatic');

frappe.pages['ai-assistant-console'].on_page_load = function (wrapper) {
    new aimatic.AiAssistantPage(wrapper);
};

aimatic.AI_ASSISTANT_KPI_BUTTONS = [
    { label: __('Today\'s Sales'), question: 'What were my sales today?' },
    { label: __('This Week\'s Sales'), question: 'What were my sales this week?' },
    { label: __('This Month\'s Sales'), question: 'What were my sales this month?' },
    { label: __('Branch Comparison'), question: 'Compare net sales across all branches this month.' },
    { label: __('Payment Mode Split'), question: 'What\'s the payment mode breakdown for this month?' },
    { label: __('Returns Today'), question: 'How many returns and how much refunded today?' },
    { label: __('Active Shifts'), question: 'What POS shifts are currently open and what are their running totals?' },
    { label: __('Best Vendors'), question: 'Who are my best vendors this year, by margin?' },
    { label: __('Worst Vendors'), question: 'Who are my worst vendors this year?' },
    { label: __('Outstanding Payables'), question: 'Which vendors do I owe the most money to right now?' },
    { label: __('Purchases This Month'), question: 'What were my purchases this month?' },
    { label: __('Top Selling Items'), question: 'What are my top selling items this month by revenue?' },
    { label: __('Overstocked Items'), question: 'Which items are overstocked relative to their sales?' },
    { label: __('Understocked Items'), question: 'Which items are at risk of running out of stock?' },
    { label: __('Gross Margin'), question: 'What\'s my overall gross margin this month?' },
];

aimatic.AI_ASSISTANT_ROLE_BUTTONS = {
    'Sales Manager': [
        'What were my sales today?',
        'What were my sales this week?',
        'What were my sales this month?',
        'Compare net sales across all branches this month.',
        'What\'s the payment mode breakdown for this month?',
        'How many returns and how much refunded today?',
        'What POS shifts are currently open and what are their running totals?',
        'What are my top selling items this month by revenue?',
        'Which items are at risk of running out of stock?',
        'Show dead stock worth more than 100000',
        'Who are my top customers this month',
    ],
    'Accounts Manager': [
        'What\'s my overall gross margin this month?',
        'Who are my best vendors this year, by margin?',
        'Who are my worst vendors this year?',
        'Which vendors do I owe the most money to right now?',
        'What were my purchases this month?',
        'Which items had a purchase cost increase recently',
        'How much do customers owe us',
    ],
    'POS Supervisor': [
        'What were my sales today?',
        'How many returns and how much refunded today?',
        'What POS shifts are currently open and what are their running totals?',
        'What\'s the payment mode breakdown for this month?',
    ],
    'System Manager': [
        'What were my sales today?',
        'What were my sales this week?',
        'What were my sales this month?',
        'Compare net sales across all branches this month.',
        'What\'s the payment mode breakdown for this month?',
        'How many returns and how much refunded today?',
        'What POS shifts are currently open and what are their running totals?',
        'Who are my best vendors this year, by margin?',
        'Who are my worst vendors this year?',
        'Which vendors do I owe the most money to right now?',
        'What were my purchases this month?',
        'What are my top selling items this month by revenue?',
        'Which items are overstocked relative to their sales?',
        'Which items are at risk of running out of stock?',
        'What\'s my overall gross margin this month?',
        'Show dead stock worth more than 100000',
        'Which items had a purchase cost increase recently',
        'Who are my top customers this month',
        'How much do customers owe us',
    ],
};

aimatic.AiAssistantPage = class AiAssistantPage {
    constructor(wrapper) {
        this.wrapper = $(wrapper);
        this.page = frappe.ui.make_app_page({
            parent: wrapper,
            title: __('AI Assistant'),
            single_column: true,
        });
        this.page.set_secondary_action(__('Clear Conversation'), () => this.clear());
        this.history = [];
        this.sending = false;
        this.current_conversation = null;
        this.context_values = { company: '', branch: '', date_range: '', date_from: '', date_to: '' };
        // Phase 2 voice state - see init_voice()/start_recording()/stop_recording().
        this.voice_state = 'idle'; // idle|recording|processing|ready|failed|permission_denied|unsupported|network_error
        this.recognition = null;
        this.audio_stream = null;
        this.audio_context = null;
        this.analyser = null;
        this.level_raf_id = null;
        this.recording_start_ms = null;
        this.duration_interval = null;
        this.build_layout();
        this.bind_events();
        this.init_voice();
    }

    build_layout() {
        this.$layout = $(`
            <div class="ai-assistant-layout">
                <div class="ai-assistant-sidebar-rail">
                    <button class="btn-reset ai-assistant-sidebar-collapse" title="${__('Collapse')}">${frappe.utils.icon('chevrons-left', 'sm')}</button>
                </div>
                <div class="ai-assistant-sidebar">
                    <div class="ai-assistant-sidebar-header">
                        <button class="btn btn-sm btn-primary ai-assistant-new-btn">${frappe.utils.icon('plus', 'xs')} ${__('New Analysis')}</button>
                    </div>
                    <input type="text" class="form-control input-xs ai-assistant-conv-search" placeholder="${__('Search conversations…')}">
                    <div class="ai-assistant-conv-list"></div>
                    <div class="ai-assistant-dashboard-section">
                        <div class="ai-assistant-dashboard-header">
                            <span>${__('Dashboards')}</span>
                            <button class="btn-reset ai-assistant-new-dashboard-btn" title="${__('New Dashboard')}">${frappe.utils.icon('plus', 'xs')}</button>
                        </div>
                        <div class="ai-assistant-dashboard-list"></div>
                    </div>
                </div>
                <div class="ai-assistant-container">
                    <div class="ai-assistant-intro">
                        ${__('Ask about sales, purchases, vendors, inventory, or customers. Rich answers now include KPIs, charts, tables, and insights. Use the context bar to scope your question.')}
                    </div>
                    <div class="ai-assistant-context-bar"></div>
                    <div class="ai-assistant-suggested-questions"></div>
                    <div class="ai-assistant-messages"></div>
                    <div class="ai-assistant-voice-status"></div>
                    <div class="ai-assistant-input-row">
                        <button class="btn btn-default ai-assistant-mic-btn" title="${__('Record a voice question')}">${frappe.utils.icon('mic', 'sm')}</button>
                        <textarea class="ai-assistant-input form-control" rows="1"
                            placeholder="${__('Ask a question…')}"></textarea>
                        <button class="btn btn-primary ai-assistant-send">${__('Send')}</button>
                    </div>
                </div>
                <div class="ai-assistant-dashboard-view" style="display:none;">
                    <div class="ai-assistant-dashboard-view-header">
                        <button class="btn btn-sm btn-default ai-assistant-back-to-chat">${frappe.utils.icon('chevrons-left', 'xs')} ${__('Back to Chat')}</button>
                        <h4 class="ai-assistant-dashboard-view-title"></h4>
                    </div>
                    <div class="ai-assistant-dashboard-grid"></div>
                </div>
                <div class="ai-assistant-right-panel">
                    <div class="ai-assistant-right-panel-header">
                        <span>${__('Scope & Sources')}</span>
                    </div>
                    <div class="ai-assistant-right-panel-body">
                        <div class="small text-muted">${__('Ask a question to see its data sources and scope here.')}</div>
                    </div>
                </div>
                <div class="ai-assistant-right-panel-rail">
                    <button class="btn-reset ai-assistant-right-panel-collapse" title="${__('Collapse')}">${frappe.utils.icon('panel-right', 'sm')}</button>
                </div>
            </div>
        `).appendTo(this.page.main.empty());

        this.$container = this.$layout.find('.ai-assistant-container');
        this.$sidebar = this.$layout.find('.ai-assistant-sidebar');
        this.$right_panel = this.$layout.find('.ai-assistant-right-panel');
        this.$conv_list = this.$layout.find('.ai-assistant-conv-list');
        this.$conv_search = this.$layout.find('.ai-assistant-conv-search');
        this.$dashboard_list = this.$layout.find('.ai-assistant-dashboard-list');
        this.$dashboard_view = this.$layout.find('.ai-assistant-dashboard-view');
        this.$dashboard_grid = this.$layout.find('.ai-assistant-dashboard-grid');
        this.$dashboard_view_title = this.$layout.find('.ai-assistant-dashboard-view-title');

        this.$messages = this.$container.find('.ai-assistant-messages');
        this.$input = this.$container.find('.ai-assistant-input');
        this.$send = this.$container.find('.ai-assistant-send');
        this.$context_bar = this.$container.find('.ai-assistant-context-bar');
        this.$suggested = this.$container.find('.ai-assistant-suggested-questions');
        this.$mic_btn = this.$container.find('.ai-assistant-mic-btn');
        this.$voice_status = this.$container.find('.ai-assistant-voice-status');

        this.build_context_bar();
        this.build_suggested_questions();
        this.build_sidebar_events();
        this.refresh_dashboard_list();

        // Initial page load: auto-resume the most recent conversation (if any)
        // so reopening the page feels continuous, same as Phase 1's flat
        // history restore did - just via the proper per-conversation model
        // now. Subsequent refresh_conversation_list() calls (after send/
        // rename/pin/delete) must NOT repeat this, or sending a message
        // would re-open and re-render the conversation the user is already in.
        frappe.call({ method: 'aimatic.ai.api.list_conversations' }).then((r) => {
            this.conversations = (r.message && r.message.conversations) || [];
            this.render_conversation_list(this.conversations);
            if (this.conversations.length) {
                this.open_conversation(this.conversations[0].name);
            }
        });
    }

    build_sidebar_events() {
        this.$layout.find('.ai-assistant-new-btn').on('click', () => this.clear());
        // The toggle button lives in the always-visible rail (a sibling of
        // .ai-assistant-sidebar, never itself collapsed) specifically so it
        // stays clickable when the sidebar it controls is hidden - it used to
        // live inside .ai-assistant-sidebar itself, which made it unclickable
        // once collapsed (confirmed live: the click fell through to Frappe's
        // own standard Desk sidebar underneath).
        this.$layout.find('.ai-assistant-sidebar-collapse').on('click', (e) => {
            const collapsed = this.$sidebar.toggleClass('collapsed').hasClass('collapsed');
            const $btn = $(e.currentTarget);
            $btn.attr('title', collapsed ? __('Expand') : __('Collapse'));
            $btn.html(collapsed ? frappe.utils.icon('chevrons-right', 'sm') : frappe.utils.icon('chevrons-left', 'sm'));
        });
        this.$layout.find('.ai-assistant-right-panel-collapse').on('click', () => this.$right_panel.toggleClass('collapsed'));
        // Same rail fix as the sidebar toggle above - this button lives in its
        // own persistent .ai-assistant-right-panel-rail sibling, not inside
        // .ai-assistant-right-panel itself, so it stays clickable once collapsed.
        this.$conv_search.on('input', () => this.filter_conversation_list(this.$conv_search.val()));

        // Phase 3: Dashboard section events
        this.$layout.find('.ai-assistant-new-dashboard-btn').on('click', () => this.create_dashboard());
        this.$layout.find('.ai-assistant-back-to-chat').on('click', () => this.hide_dashboard_view());
    }

    refresh_conversation_list() {
        frappe.call({ method: 'aimatic.ai.api.list_conversations' }).then((r) => {
            this.conversations = (r.message && r.message.conversations) || [];
            this.render_conversation_list(this.conversations);
        });
    }

    // ─────────────────────────────────────────────────────────────────────
    // Phase 3: Save/Pin, Export, Dashboard
    // ─────────────────────────────────────────────────────────────────────

    save_current_answer(response, question) {
        const defaultTitle = response.answer && response.answer.summary
            ? response.answer.summary.substring(0, 80)
            : __('AI Assistant Report');
        const title = prompt(__('Save this answer as a report'), defaultTitle);
        if (!title || !title.trim()) return;

        frappe.call({
            method: 'aimatic.ai.api.save_report',
            args: {
                title: title.trim(),
                question: question,
                context_json: JSON.stringify(response.context || {}),
                response_json: JSON.stringify(response),
            },
            callback: (r) => {
                // save_report returns {"name": doc.name} - a flat shape, not
                // nested under a "saved_report" key.
                if (r.message && r.message.name) {
                    frappe.show_alert({ message: __('Saved'), indicator: 'green' });
                    this.prompt_add_to_dashboard(r.message.name);
                }
            },
        });
    }

    prompt_add_to_dashboard(savedReportName) {
        frappe.call({ method: 'aimatic.ai.api.list_dashboards' }).then((r) => {
            const dashboards = (r.message && r.message.dashboards) || [];
            if (!dashboards.length) return;

            const titles = dashboards.map(d => d.title).join(', ');
            const chosen = prompt(__('Add to dashboard? Type dashboard title (available: {0})', [titles]));
            if (!chosen || !chosen.trim()) return;

            const match = dashboards.find(d => d.title.toLowerCase() === chosen.trim().toLowerCase());
            if (!match) {
                frappe.show_alert({ message: __('Dashboard not found'), indicator: 'orange' });
                return;
            }

            frappe.call({
                method: 'aimatic.ai.api.add_widget_to_dashboard',
                args: {
                    dashboard: match.name,
                    saved_report: savedReportName,
                    size: 'Medium',
                },
                callback: () => {
                    frappe.show_alert({ message: __('Added to dashboard'), indicator: 'green' });
                    // Sidebar shows each dashboard's widget_count - refresh it
                    // so that count doesn't stay stale until the next reload.
                    this.refresh_dashboard_list();
                },
            });
        });
    }

    render_dashboard_list(dashboards) {
        this.$dashboard_list.empty();
        if (!dashboards.length) {
            this.$dashboard_list.append(`<div class="small text-muted p-2">${__('No dashboards yet. Create one to get started.')}</div>`);
            return;
        }
        dashboards.forEach((db) => {
            const $row = $(`
                <div class="ai-assistant-dashboard-row" data-name="${frappe.utils.escape_html(db.name)}">
                    <span class="ai-assistant-dashboard-title">${frappe.utils.escape_html(db.title)}</span>
                    <span class="ai-assistant-dashboard-count small text-muted">(${db.widget_count || 0})</span>
                </div>
            `);
            $row.on('click', () => this.open_dashboard(db.name));
            this.$dashboard_list.append($row);
        });
    }

    refresh_dashboard_list() {
        frappe.call({ method: 'aimatic.ai.api.list_dashboards' }).then((r) => {
            this.dashboards = (r.message && r.message.dashboards) || [];
            this.render_dashboard_list(this.dashboards);
        });
    }

    create_dashboard() {
        const title = prompt(__('New dashboard title'));
        if (!title || !title.trim()) return;
        frappe.call({
            method: 'aimatic.ai.api.create_dashboard',
            args: { title: title.trim() },
            callback: (r) => {
                // create_dashboard returns {"name": doc.name} - flat, not
                // nested under a "dashboard" key.
                if (r.message && r.message.name) {
                    this.refresh_dashboard_list();
                    this.open_dashboard(r.message.name);
                }
            },
        });
    }

    open_dashboard(name) {
        this.show_dashboard_view();
        frappe.call({ method: 'aimatic.ai.api.get_dashboard', args: { name: name } }).then((r) => {
            // get_dashboard returns {"name", "title", "widgets"} directly -
            // r.message itself IS the dashboard object, not r.message.dashboard.
            const dashboard = r.message;
            if (dashboard) {
                this.$dashboard_view_title.text(dashboard.title);
                this.render_dashboard_view(dashboard);
            }
        });
    }

    show_dashboard_view() {
        this.$container.hide();
        this.$dashboard_view.show();
        this.$right_panel.hide();
        this.$layout.find('.ai-assistant-right-panel-rail').hide();
    }

    hide_dashboard_view() {
        this.$dashboard_view.hide();
        this.$container.show();
        this.$right_panel.show();
        this.$layout.find('.ai-assistant-right-panel-rail').show();
    }

    render_dashboard_view(dashboard) {
        this.$dashboard_grid.empty();
        const widgets = dashboard.widgets || [];
        if (!widgets.length) {
            this.$dashboard_grid.append(`<div class="small text-muted p-3">${__('No widgets yet - save an answer from the chat and add it to this dashboard.')}</div>`);
            return;
        }

        const sizeClass = { Small: 'ai-assistant-widget-sm', Medium: 'ai-assistant-widget-md', Large: 'ai-assistant-widget-lg' };
        const pendingCharts = [];

        widgets.forEach((widget) => {
            const resp = widget.response_snapshot || {};
            const $card = $(`
                <div class="ai-assistant-dashboard-widget ${sizeClass[widget.size] || 'ai-assistant-widget-md'}">
                    <div class="ai-assistant-widget-header">
                        <span class="ai-assistant-widget-title">${frappe.utils.escape_html(widget.title || '')}</span>
                        <button class="btn-reset ai-assistant-widget-remove" title="${__('Remove from dashboard')}">${frappe.utils.icon('x', 'xs')}</button>
                    </div>
                    <div class="ai-assistant-widget-body"></div>
                </div>
            `);
            const $body = $card.find('.ai-assistant-widget-body');

            // Widget cards reuse the same KPI/chart/table renderers as a live
            // rich answer - insights/follow-ups/sources are skipped here to
            // keep cards compact, matching the design intent (a dashboard
            // widget is a focused glance, not a full answer replay).
            if (resp.kpis && resp.kpis.length) {
                $body.append(this.render_kpis(resp.kpis));
            }
            if (resp.charts && resp.charts.length) {
                resp.charts.forEach((chart) => {
                    const { $wrap, $mount } = this.build_chart_wrap(chart);
                    $body.append($wrap);
                    pendingCharts.push({ chart, $mount });
                });
            }
            if (resp.tables && resp.tables.length) {
                resp.tables.forEach((table) => {
                    $body.append(this.render_table(table));
                });
            }

            $card.find('.ai-assistant-widget-remove').on('click', () => {
                frappe.call({
                    method: 'aimatic.ai.api.remove_widget_from_dashboard',
                    args: { dashboard: dashboard.name, widget_row_name: widget.name },
                    callback: () => {
                        this.open_dashboard(dashboard.name);
                        // Same staleness reasoning as prompt_add_to_dashboard -
                        // the sidebar's widget_count badge needs a refresh too.
                        this.refresh_dashboard_list();
                    },
                });
            });

            this.$dashboard_grid.append($card);
        });

        // Same reasoning as render_rich_answer: charts need to be attached to
        // the document (inside $dashboard_grid, already appended above)
        // before frappe.Chart can measure their container's width.
        pendingCharts.forEach(({ chart, $mount }) => this.init_chart(chart, $mount));
    }


    filter_conversation_list(query) {
        const q = (query || '').toLowerCase();
        const filtered = (this.conversations || []).filter((c) => c.title.toLowerCase().includes(q));
        this.render_conversation_list(filtered);
    }

    render_conversation_list(conversations) {
        this.$conv_list.empty();
        if (!conversations.length) {
            this.$conv_list.append(`<div class="ai-assistant-conv-empty small text-muted">${__('No conversations yet.')}</div>`);
            return;
        }
        conversations.forEach((conv) => {
            const $row = $(`
                <div class="ai-assistant-conv-row ${conv.name === this.current_conversation ? 'active' : ''}" data-name="${frappe.utils.escape_html(conv.name)}">
                    <span class="ai-assistant-conv-pin-icon">${conv.pinned ? frappe.utils.icon('pin', 'xs') : ''}</span>
                    <span class="ai-assistant-conv-title">${frappe.utils.escape_html(conv.title)}</span>
                    <span class="ai-assistant-conv-actions">
                        <button class="btn-reset ai-assistant-conv-pin" title="${__('Pin')}">${frappe.utils.icon('pin', 'xs')}</button>
                        <button class="btn-reset ai-assistant-conv-rename" title="${__('Rename')}">${frappe.utils.icon('pencil', 'xs')}</button>
                        <button class="btn-reset ai-assistant-conv-delete" title="${__('Delete')}">${frappe.utils.icon('trash', 'xs')}</button>
                    </span>
                </div>
            `);
            $row.find('.ai-assistant-conv-title').on('click', () => this.open_conversation(conv.name));
            $row.find('.ai-assistant-conv-pin').on('click', (e) => {
                e.stopPropagation();
                frappe.call({ method: 'aimatic.ai.api.pin_conversation', args: { conversation: conv.name, pinned: conv.pinned ? 0 : 1 } }).then(() => this.refresh_conversation_list());
            });
            $row.find('.ai-assistant-conv-rename').on('click', (e) => {
                e.stopPropagation();
                const new_title = prompt(__('Rename conversation'), conv.title);
                if (new_title && new_title.trim()) {
                    frappe.call({ method: 'aimatic.ai.api.rename_conversation', args: { conversation: conv.name, title: new_title.trim() } }).then(() => this.refresh_conversation_list());
                }
            });
            $row.find('.ai-assistant-conv-delete').on('click', (e) => {
                e.stopPropagation();
                frappe.confirm(__('Delete this conversation? This cannot be undone.'), () => {
                    frappe.call({ method: 'aimatic.ai.api.delete_conversation', args: { conversation: conv.name } }).then(() => {
                        if (this.current_conversation === conv.name) this.clear();
                        this.refresh_conversation_list();
                    });
                });
            });
            this.$conv_list.append($row);
        });
    }

    open_conversation(name) {
        this.set_sending(true);
        frappe.call({ method: 'aimatic.ai.api.get_conversation_messages', args: { conversation: name } }).then((r) => {
            this.set_sending(false);
            this.current_conversation = name;
            this.history = [];
            this.$messages.empty();
            const messages = (r.message && r.message.messages) || [];
            messages.forEach((m) => {
                this.history.push({ role: m.role, content: m.content });
                this.append_bubble(m.role, m.content);
            });
            this.$sidebar.find('.ai-assistant-conv-row').removeClass('active');
            this.$sidebar.find(`.ai-assistant-conv-row[data-name="${name}"]`).addClass('active');
        });
    }

    update_right_panel(response) {
        if (!this.$right_panel) return;
        const ctx = response.context || {};
        const sources = response.sources || [];
        const branch_text = (ctx.branch && ctx.branch.length) ? ctx.branch.join(', ') : __('All branches');
        const date_text = ctx.date_range ? `${ctx.date_range.from} — ${ctx.date_range.to}` : '';
        const sources_html = sources.length
            ? sources.map((s) => `<div class="ai-assistant-rp-source"><span class="ai-assistant-rp-source-type">${frappe.utils.escape_html(s.type)}</span> ${frappe.utils.escape_html(s.name)}</div>`).join('')
            : `<div class="small text-muted">${__('No specific data source (general knowledge answer).')}</div>`;

        this.$right_panel.find('.ai-assistant-right-panel-body').html(`
            <div class="ai-assistant-rp-section">
                <div class="ai-assistant-rp-label">${__('Company')}</div>
                <div class="ai-assistant-rp-value">${frappe.utils.escape_html(ctx.company || '')}</div>
            </div>
            <div class="ai-assistant-rp-section">
                <div class="ai-assistant-rp-label">${__('Branch')}</div>
                <div class="ai-assistant-rp-value">${frappe.utils.escape_html(branch_text)}</div>
            </div>
            <div class="ai-assistant-rp-section">
                <div class="ai-assistant-rp-label">${__('Date Range')}</div>
                <div class="ai-assistant-rp-value">${frappe.utils.escape_html(date_text)}</div>
            </div>
            <div class="ai-assistant-rp-section">
                <div class="ai-assistant-rp-label">${__('Data Freshness')}</div>
                <div class="ai-assistant-rp-value">${frappe.utils.escape_html(ctx.data_freshness || '')}</div>
            </div>
            <div class="ai-assistant-rp-section">
                <div class="ai-assistant-rp-label">${__('Data Sources')}</div>
                ${sources_html}
            </div>
        `);
    }

    build_context_bar() {
        const company_default = frappe.defaults.get_user_default('Company') || frappe.defaults.get_default('company');

        this.company_field = this.page.add_field({
            label: __('Company'),
            fieldname: 'company',
            fieldtype: 'Link',
            options: 'Company',
            default: company_default,
            reqd: 1,
            change: () => {
                if (this.branch_field.get_value()) this.branch_field.set_value('');
            },
        }, this.$context_bar);

        this.branch_field = this.page.add_field({
            label: __('Branch'),
            fieldname: 'branch',
            fieldtype: 'Link',
            options: 'Branch',
            get_query: () => ({ filters: { company: this.company_field.get_value() } }),
        }, this.$context_bar);

        this.date_range_field = this.page.add_field({
            label: __('Date Range'),
            fieldname: 'date_range',
            fieldtype: 'Select',
            options: [__('Today'), __('Yesterday'), __('Last 7 Days'), __('Last 30 Days'), __('This Month'), __('Last Month'), __('Custom')].join('\n'),
            default: __('Last 30 Days'),
            change: () => this.toggle_custom_date_fields(),
        }, this.$context_bar);

        this.date_from_field = this.page.add_field({
            label: __('From Date'),
            fieldname: 'date_from',
            fieldtype: 'Date',
        }, this.$context_bar);

        this.date_to_field = this.page.add_field({
            label: __('To Date'),
            fieldname: 'date_to',
            fieldtype: 'Date',
        }, this.$context_bar);

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

    build_suggested_questions() {
        const roles = frappe.user_roles || [];
        let questions = [];

        if (roles.includes('System Manager')) {
            questions = aimatic.AI_ASSISTANT_ROLE_BUTTONS['System Manager'];
        } else if (roles.includes('Sales Manager')) {
            questions = aimatic.AI_ASSISTANT_ROLE_BUTTONS['Sales Manager'];
        } else if (roles.includes('Accounts Manager')) {
            questions = aimatic.AI_ASSISTANT_ROLE_BUTTONS['Accounts Manager'];
        } else if (roles.includes('POS Supervisor')) {
            questions = aimatic.AI_ASSISTANT_ROLE_BUTTONS['POS Supervisor'];
        } else {
            questions = aimatic.AI_ASSISTANT_ROLE_BUTTONS['Sales Manager'];
        }

        questions.forEach((q) => {
            $(`<button class="btn btn-default btn-xs ai-assistant-suggested-btn">${frappe.utils.escape_html(q)}</button>`)
                .on('click', () => this.send_message(q))
                .appendTo(this.$suggested);
        });
    }

    bind_events() {
        this.$send.on('click', () => this.send_message(this.$input.val()));
        this.$input.on('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.send_message(this.$input.val());
            }
        });
    }

    // ─────────────────────────────────────────────────────────────────────
    // Phase 2 voice input. Uses the browser's own SpeechRecognition (Web
    // Speech API) - no new backend, per the architecture constraint that this
    // app has no dedicated speech-to-text service. Note this means audio may
    // be sent to the BROWSER VENDOR's own cloud recognition service (e.g.
    // Chrome routes to Google) - not to any aimatic/OpenRouter backend. The
    // transcript is never auto-submitted; it lands in the same editable
    // textarea as typed text, so the user reviews/edits before Send.
    // Mixed Urdu-English in one utterance isn't reliably supported by this
    // API (it recognizes against one selected language per session) - a
    // language toggle is offered instead of true code-switching detection.
    // ─────────────────────────────────────────────────────────────────────

    init_voice() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            this.voice_state = 'unsupported';
            this.$mic_btn.prop('disabled', true).attr('title', __('Voice input is not supported in this browser.'));
            return;
        }
        this.voice_lang = 'en-US';
        this.$mic_btn.on('click', () => {
            if (this.voice_state === 'recording') {
                this.stop_recording();
            } else {
                this.start_recording();
            }
        });
    }

    start_recording() {
        if (this.voice_state === 'unsupported' || this.sending) return;

        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        this.recognition = new SpeechRecognition();
        this.recognition.lang = this.voice_lang;
        this.recognition.interimResults = true;
        this.recognition.continuous = true;

        let finalTranscript = this.$input.val() ? this.$input.val() + ' ' : '';

        this.recognition.onresult = (event) => {
            let interim = '';
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const transcript = event.results[i][0].transcript;
                if (event.results[i].isFinal) {
                    finalTranscript += transcript + ' ';
                } else {
                    interim += transcript;
                }
            }
            this.$input.val(finalTranscript + interim);
        };

        this.recognition.onerror = (event) => {
            if (event.error === 'not-allowed' || event.error === 'permission-denied') {
                this.voice_state = 'permission_denied';
            } else if (event.error === 'network') {
                this.voice_state = 'network_error';
            } else if (event.error === 'no-speech') {
                // Not a real failure - just nothing heard yet, keep listening state as-is.
                return;
            } else {
                this.voice_state = 'failed';
            }
            this.stop_recording(true);
        };

        this.recognition.onend = () => {
            if (this.voice_state === 'recording') {
                this.voice_state = 'ready';
                this.render_voice_status();
            }
        };

        try {
            this.recognition.start();
        } catch (e) {
            this.voice_state = 'failed';
            this.render_voice_status();
            return;
        }

        this.voice_state = 'recording';
        this.recording_start_ms = Date.now();
        this.render_voice_status();
        this.start_duration_timer();
        this.start_level_meter();
    }

    stop_recording(fromError) {
        if (this.recognition) {
            try {
                this.recognition.stop();
            } catch (e) {
                // already stopped
            }
        }
        this.stop_duration_timer();
        this.stop_level_meter();
        if (!fromError && this.voice_state === 'recording') {
            this.voice_state = 'ready';
        }
        this.render_voice_status();
    }

    start_duration_timer() {
        // Only patches the duration text, not a full render_voice_status() -
        // that would rebuild the waveform bar elements every 500ms, fighting
        // the requestAnimationFrame loop in tick_level_meter() that's
        // continuously animating those same elements' transforms.
        this.duration_interval = setInterval(() => {
            if (this.voice_state !== 'recording') return;
            const secs = Math.floor((Date.now() - this.recording_start_ms) / 1000);
            const mm = String(Math.floor(secs / 60)).padStart(2, '0');
            const ss = String(secs % 60).padStart(2, '0');
            this.$voice_status.find('.ai-assistant-voice-duration').text(`${mm}:${ss}`);
        }, 500);
    }

    stop_duration_timer() {
        if (this.duration_interval) {
            clearInterval(this.duration_interval);
            this.duration_interval = null;
        }
    }

    start_level_meter() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
        navigator.mediaDevices.getUserMedia({ audio: true })
            .then((stream) => {
                this.audio_stream = stream;
                this.audio_context = new (window.AudioContext || window.webkitAudioContext)();
                const source = this.audio_context.createMediaStreamSource(stream);
                this.analyser = this.audio_context.createAnalyser();
                this.analyser.fftSize = 32;
                source.connect(this.analyser);
                this.tick_level_meter();
            })
            .catch(() => {
                // Level meter is cosmetic only - recognition itself already
                // requested (and may have been denied) mic access separately,
                // so a failure here doesn't change voice_state on its own.
            });
    }

    tick_level_meter() {
        if (!this.analyser || this.voice_state !== 'recording') return;
        const data = new Uint8Array(this.analyser.frequencyBinCount);
        this.analyser.getByteFrequencyData(data);
        const avg = data.reduce((a, b) => a + b, 0) / data.length;
        this.$voice_status.find('.ai-assistant-voice-bar').each((i, el) => {
            const scale = Math.max(0.15, Math.min(1, (avg / 255) * (1 + i * 0.4)));
            $(el).css('transform', `scaleY(${scale})`);
        });
        this.level_raf_id = requestAnimationFrame(() => this.tick_level_meter());
    }

    stop_level_meter() {
        if (this.level_raf_id) {
            cancelAnimationFrame(this.level_raf_id);
            this.level_raf_id = null;
        }
        if (this.audio_stream) {
            this.audio_stream.getTracks().forEach((t) => t.stop());
            this.audio_stream = null;
        }
        if (this.audio_context) {
            this.audio_context.close().catch(() => {});
            this.audio_context = null;
        }
        this.analyser = null;
    }

    render_voice_status() {
        this.$mic_btn
            .toggleClass('recording', this.voice_state === 'recording')
            .html(this.voice_state === 'recording' ? frappe.utils.icon('circle-stop', 'sm') : frappe.utils.icon('mic', 'sm'));

        if (this.voice_state === 'recording') {
            const secs = Math.floor((Date.now() - this.recording_start_ms) / 1000);
            const mm = String(Math.floor(secs / 60)).padStart(2, '0');
            const ss = String(secs % 60).padStart(2, '0');
            this.$voice_status.html(`
                <span class="ai-assistant-voice-rec-dot"></span>
                <span class="ai-assistant-voice-duration">${mm}:${ss}</span>
                <span class="ai-assistant-voice-bars">
                    ${[0, 1, 2, 3, 4].map(() => '<span class="ai-assistant-voice-bar"></span>').join('')}
                </span>
                <button class="btn btn-xs ai-assistant-voice-lang" data-lang="ur-PK">${__('Switch to Urdu')}</button>
                <span class="small text-muted">${__('Recording - audio may be sent to your browser\'s speech service. Review the transcript before sending.')}</span>
            `);
            this.$voice_status.find('.ai-assistant-voice-lang').on('click', (e) => {
                const lang = $(e.currentTarget).attr('data-lang');
                this.voice_lang = lang;
                this.stop_recording();
                this.start_recording();
            });
        } else if (this.voice_state === 'ready') {
            this.$voice_status.html(`<span class="small text-muted">${__('Transcript ready below - edit if needed, then Send.')}</span> <button class="btn btn-xs ai-assistant-voice-discard">${__('Discard')}</button>`);
            this.$voice_status.find('.ai-assistant-voice-discard').on('click', () => {
                this.$input.val('');
                this.voice_state = 'idle';
                this.render_voice_status();
            });
        } else if (this.voice_state === 'permission_denied') {
            this.$voice_status.html(`<span class="small ai-assistant-voice-error">${__('Microphone permission denied. Allow microphone access in your browser to use voice input.')}</span>`);
        } else if (this.voice_state === 'network_error') {
            this.$voice_status.html(`<span class="small ai-assistant-voice-error">${__('Voice recognition needs a network connection. Try again or type your question.')}</span>`);
        } else if (this.voice_state === 'failed') {
            this.$voice_status.html(`<span class="small ai-assistant-voice-error">${__('Voice recognition failed. Try again or type your question.')}</span>`);
        } else {
            this.$voice_status.empty();
        }
    }

    clear() {
        // "Clear Conversation" (secondary page action) and the sidebar's "New
        // Analysis" button both call this - resetting current_conversation to
        // null means the *next* message lazily starts a brand new
        // AI Assistant Conversation (see send_message), rather than
        // continuing to append to the one just cleared from view.
        this.history = [];
        this.current_conversation = null;
        this.$messages.empty();
        this.$right_panel && this.$right_panel.find('.ai-assistant-right-panel-body').html(`<div class="small text-muted">${__('Ask a question to see its data sources and scope here.')}</div>`);
        this.$sidebar && this.$sidebar.find('.ai-assistant-conv-row').removeClass('active');
    }

    get_context_prefix() {
        const parts = [];
        const company = this.company_field.get_value();
        const branch = this.branch_field.get_value();
        const date_range = this.date_range_field.get_value();
        const date_from = this.date_from_field.get_value();
        const date_to = this.date_to_field.get_value();

        if (company) parts.push(`company ${company}`);
        if (branch) parts.push(`branch ${branch}`);
        if (date_range && date_range !== __('Custom')) {
            parts.push(date_range.toLowerCase());
        } else if (date_range === __('Custom') && date_from && date_to) {
            parts.push(`from ${date_from} to ${date_to}`);
        }
        return parts.length ? `For ${parts.join(', ')}, ` : '';
    }

    send_message(rawMessage) {
        const message = (rawMessage || '').trim();
        if (!message || this.sending) return;

        // Phase 2: every message belongs to a conversation, so it shows up in
        // the sidebar's history list. The very first message of a session has
        // none yet - create one lazily rather than forcing an explicit "New
        // Analysis" click before the user can ask anything.
        if (!this.current_conversation) {
            this.set_sending(true);
            frappe.call({ method: 'aimatic.ai.api.start_conversation' }).then((r) => {
                this.current_conversation = r.message.conversation;
                this.refresh_conversation_list();
                this.set_sending(false);
                this._send_message_now(message);
            });
            return;
        }
        this._send_message_now(message);
    }

    _send_message_now(message) {
        const prefix = this.get_context_prefix();
        const full_message = prefix + message;

        this.$input.val('');
        if (this.voice_state === 'ready') {
            this.voice_state = 'idle';
            this.render_voice_status();
        }
        this.append_bubble('user', message);
        this.set_sending(true);

        frappe.call({
            method: 'aimatic.ai.api.ask',
            args: {
                message: full_message,
                history: JSON.stringify(this.history),
                conversation: this.current_conversation,
            },
            callback: (r) => {
                const response = r.message;
                if (response && typeof response === 'object' && response.answer) {
                    this.history.push({ role: 'user', content: full_message });
                    this.history.push({ role: 'assistant', content: response.answer.summary || '' });
                    this.render_rich_answer(response, full_message);
                    this.update_right_panel(response);
                    this.refresh_conversation_list();
                } else {
                    const reply = (response && response.reply) || __('(no answer)');
                    this.history.push({ role: 'user', content: full_message });
                    this.history.push({ role: 'assistant', content: reply });
                    this.append_bubble('assistant', reply);
                }
            },
            error: () => {
                this.append_bubble('assistant', __('Sorry, something went wrong answering that. Try rephrasing your question.'), true);
            },
            always: () => this.set_sending(false),
        });
    }

    set_sending(sending) {
        this.sending = sending;
        this.$send.prop('disabled', sending).text(sending ? __('Thinking…') : __('Send'));
    }

    append_bubble(role, content, is_error) {
        const $bubble = $(`
            <div class="ai-assistant-bubble ai-assistant-bubble-${role}${is_error ? ' ai-assistant-bubble-error' : ''}">
                <div class="ai-assistant-bubble-role">${role === 'user' ? __('You') : __('Assistant')}</div>
                <div class="ai-assistant-bubble-content"></div>
            </div>
        `);
        $bubble.find('.ai-assistant-bubble-content').text(content);
        this.$messages.append($bubble);
        this.$messages.scrollTop(this.$messages[0].scrollHeight);
    }

    render_rich_answer(response, question) {
        // data-question isn't read from the DOM anywhere (save_current_answer
        // gets it as a plain closure argument instead) - kept as a visible
        // attribute anyway so it's inspectable in devtools when debugging a
        // specific turn's save behavior.
        const $turn = $(`<div class="ai-assistant-rich-turn"></div>`).attr('data-question', frappe.utils.escape_html(question || ''));

        if (response.answer && response.answer.summary) {
            $turn.append(this.render_summary(response.answer.summary));
        }

        if (response.kpis && response.kpis.length) {
            $turn.append(this.render_kpis(response.kpis));
        }

        // Charts need a real, DOM-attached container to measure width against -
        // frappe.Chart injects an SVG sized off the container's offsetWidth, which
        // is 0/NaN on a detached element. So build empty placeholders now and only
        // call `new frappe.Chart(...)` on them after $turn is appended to the DOM
        // below (same two-phase build-then-attach-then-instantiate pattern used by
        // sales_dashboard_console.js's render_trend_chart/render_branch_chart).
        const pendingCharts = [];
        if (response.charts && response.charts.length) {
            response.charts.forEach((chart) => {
                const { $wrap, $mount } = this.build_chart_wrap(chart);
                $turn.append($wrap);
                pendingCharts.push({ chart, $mount });
            });
        }

        if (response.tables && response.tables.length) {
            response.tables.forEach((table) => {
                $turn.append(this.render_table(table));
            });
        }

        if (response.insights && response.insights.length) {
            $turn.append(this.render_insights(response.insights));
        }

        if (response.warnings && response.warnings.length) {
            $turn.append(this.render_warnings(response.warnings));
        }

        if (response.follow_up_questions && response.follow_up_questions.length) {
            $turn.append(this.render_follow_ups(response.follow_up_questions));
        }

        // Phase 3: Save this answer as an AI Saved Report (question + full
        // response snapshot) - see save_current_answer.
        const $saveBtn = $(`<button class="btn btn-xs btn-default ai-assistant-save-report" title="${__('Save this answer as a report')}">${frappe.utils.icon('save', 'xs')} ${__('Save')}</button>`);
        $saveBtn.on('click', () => this.save_current_answer(response, question));
        $turn.append($(`<div class="ai-assistant-save-wrap"></div>`).append($saveBtn));

        if (response.sources && response.sources.length) {
            $turn.append(this.render_sources(response.sources));
        }

        this.$messages.append($turn);

        // A rich turn (summary + KPIs + chart + table + insights + ...) is often
        // taller than the visible message pane. Scrolling to the container's
        // bottom (as append_bubble does for plain turns) would hide the summary/
        // KPIs/chart at the TOP of this answer above the fold - scroll so the new
        // turn's own top edge is what comes into view, matching natural reading order.
        $turn[0].scrollIntoView({ block: 'start', behavior: 'smooth' });

        pendingCharts.forEach(({ chart, $mount }) => this.init_chart(chart, $mount));
    }

    render_summary(summary) {
        const html = this.markdown_lite(summary);
        return $(`<div class="ai-assistant-summary">${html}</div>`);
    }

    markdown_lite(text) {
        if (!text) return '';
        let escaped = frappe.utils.escape_html(text);
        escaped = escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        escaped = escaped.replace(/^\s*-\s+(.+)$/gm, '<li>$1</li>');
        escaped = escaped.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');
        escaped = escaped.replace(/\n/g, '<br>');
        return escaped;
    }

    render_kpis(kpis) {
        const $row = $('<div class="ai-assistant-kpi-row"></div>');
        kpis.forEach((kpi) => {
            let formatted = '';
            if (kpi.format === 'currency') {
                formatted = format_currency(kpi.value || 0, kpi.currency || 'PKR');
            } else if (kpi.format === 'percent') {
                formatted = format_number(kpi.value || 0, null, 2) + '%';
            } else if (kpi.format === 'qty') {
                formatted = format_number(kpi.value || 0, null, 0);
            } else {
                formatted = format_number(kpi.value || 0, null, 2);
            }

            let badge = '';
            if (kpi.severity) {
                badge = `<span class="ai-assistant-kpi-severity severity-${frappe.utils.escape_html(kpi.severity)}">${frappe.utils.escape_html(kpi.severity)}</span>`;
            }

            let comparison = '';
            if (kpi.comparison !== undefined && kpi.variance_pct !== undefined) {
                const compFormatted = kpi.format === 'currency'
                    ? format_currency(kpi.comparison, kpi.currency || 'PKR')
                    : format_number(kpi.comparison, null, 2);
                const varianceClass = kpi.variance_pct >= 0 ? 'text-success' : 'text-danger';
                const trendIcon = kpi.trend === 'up' ? '↑' : kpi.trend === 'down' ? '↓' : '→';
                comparison = `<div class="ai-assistant-kpi-comparison ${varianceClass}">${trendIcon} ${kpi.variance_pct >= 0 ? '+' : ''}${kpi.variance_pct}% vs ${compFormatted}</div>`;
            }

            const $card = $(`
                <div class="ai-assistant-kpi-card">
                    <div class="ai-assistant-kpi-label">${frappe.utils.escape_html(kpi.label)}</div>
                    <div class="ai-assistant-kpi-value">${formatted}</div>
                    ${badge}
                    ${comparison}
                    ${kpi.tooltip ? `<div class="ai-assistant-kpi-tooltip">${frappe.utils.escape_html(kpi.tooltip)}</div>` : ''}
                </div>
            `);
            $row.append($card);
        });
        return $row;
    }

    build_chart_wrap(chart) {
        const $wrap = $(`<div class="ai-assistant-chart-wrap" data-chart-id="${frappe.utils.escape_html(chart.id)}"></div>`);
        if (chart.title) {
            $wrap.append(`<div class="ai-assistant-chart-title">${frappe.utils.escape_html(chart.title)}</div>`);
        }
        // frappe.Chart renders an SVG into a plain block element (not an HTML5
        // <canvas> - the "Chart" name is misleading here), and it must already be
        // attached to the document when instantiated or it measures 0/NaN width.
        const $mount = $(`<div class="ai-assistant-chart-mount"></div>`).appendTo($wrap);
        return { $wrap, $mount };
    }

    init_chart(chart, $mount) {
        let chartType = chart.type;
        if (chartType === 'donut') {
            chartType = 'pie';
        } else if (!['line', 'bar', 'pie'].includes(chartType)) {
            chartType = 'bar';
        }

        const datasets = (chart.data && chart.data.datasets) || [];
        const labels = (chart.data && chart.data.labels) || [];

        const chartData = {
            labels: labels,
            datasets: datasets.map((ds) => ({
                name: ds.label,
                values: ds.data,
            })),
        };

        // options.yAxis/xAxis carry a {format: 'currency'|'percent'|...} hint from the
        // backend, not a frappe.Chart axisOptions mode string - use it to drive
        // tooltip formatting (formatTooltipY) instead, same as sales_dashboard_console.js.
        const options = chart.options || {};
        const yFormat = options.yAxis && options.yAxis.format;
        const chartOptions = {
            type: chartType,
            data: chartData,
            height: 300,
            colors: ['#7cd6fd', '#743ee2', '#ff5858', '#ffa00a', '#28d094'],
            lineOptions: {
                hideDots: chartType === 'line' ? 0 : 1,
                regionFill: chartType === 'line' ? 1 : 0,
            },
            barOptions: {
                stacked: false,
                spaceRatio: 0.5,
            },
            tooltipOptions: {
                formatTooltipY: (d) => this.format_chart_value(d, yFormat),
            },
        };

        try {
            new frappe.Chart($mount[0], chartOptions);
        } catch (e) {
            console.error('Chart render error:', e);
            $mount.after(`<div class="text-muted small">${__('Chart could not be rendered')}</div>`);
        }
    }

    format_chart_value(value, format) {
        if (format === 'currency') return format_currency(value, 'PKR');
        if (format === 'percent') return format_number(value, null, 2) + '%';
        return format_number(value, null, 2);
    }

    render_table(table) {
        const $wrap = $(`<div class="ai-assistant-table-wrap" data-table-id="${frappe.utils.escape_html(table.id)}"></div>`);
        if (table.title) {
            const $titleRow = $(`<div class="ai-assistant-table-title-row"></div>`);
            $titleRow.append(`<span class="ai-assistant-table-title">${frappe.utils.escape_html(table.title)}</span>`);
            const $toolbar = $(`
                <span class="ai-assistant-table-toolbar">
                    <button class="btn btn-xs btn-default ai-assistant-export-csv" title="${__('Export CSV')}">CSV</button>
                    <button class="btn btn-xs btn-default ai-assistant-export-xlsx" title="${__('Export Excel')}">XLSX</button>
                </span>
            `);
            $titleRow.append($toolbar);
            $wrap.append($titleRow);

            // Exporting a file from a frappe.whitelist() method that sets
            // frappe.response directly (build_csv_response/build_xlsx_response
            // server-side) does NOT work via a normal frappe.call() AJAX call,
            // which expects a JSON response. open_url_post (frappe's own
            // established pattern, e.g. data_exporter.js) builds a real HTML
            // form and POSTs it, which the browser treats as a normal
            // navigation and triggers an actual file download.
            $toolbar.find('.ai-assistant-export-csv').on('click', () => {
                open_url_post('/api/method/aimatic.ai.api.export_table', {
                    table_json: JSON.stringify({ columns: table.columns, rows: table.rows }),
                    filename: `${table.title || table.id || 'export'}.csv`,
                    format: 'csv',
                });
            });
            $toolbar.find('.ai-assistant-export-xlsx').on('click', () => {
                open_url_post('/api/method/aimatic.ai.api.export_table', {
                    table_json: JSON.stringify({ columns: table.columns, rows: table.rows }),
                    filename: `${table.title || table.id || 'export'}.xlsx`,
                    format: 'xlsx',
                });
            });
        }

        const columns = table.columns || [];
        const rows = table.rows || [];

        let thead = '<thead><tr>';
        columns.forEach((col, idx) => {
            thead += `<th class="sortable" data-col-idx="${idx}" data-col-key="${frappe.utils.escape_html(col.key)}">${frappe.utils.escape_html(col.label)}</th>`;
        });
        thead += '</tr></thead>';

        let tbody = '<tbody>';
        rows.forEach((row) => {
            tbody += '<tr>';
            columns.forEach((col) => {
                const val = row[col.key];
                let formatted = this.format_cell(val, col);
                tbody += `<td>${formatted}</td>`;
            });
            tbody += '</tr>';
        });
        tbody += '</tbody>';

        const $table = $(`
            <div class="table-responsive">
                <table class="table table-sm table-bordered ai-assistant-table">${thead}${tbody}</table>
            </div>
        `).appendTo($wrap);

        $table.find('.ai-assistant-link').on('click', (e) => {
            const $el = $(e.currentTarget);
            frappe.set_route('Form', $el.attr('data-doctype'), $el.attr('data-name'));
        });

        $table.find('th').on('click', (e) => {
            const $th = $(e.currentTarget);
            const idx = parseInt($th.attr('data-col-idx'), 10);
            const key = $th.attr('data-col-key');
            const asc = !$th.hasClass('sort-asc');
            $table.find('th').removeClass('sort-asc sort-desc');
            $th.addClass(asc ? 'sort-asc' : 'sort-desc');

            const $tbody = $table.find('tbody');
            const rowsArr = $tbody.find('tr').toArray();
            rowsArr.sort((a, b) => {
                const aVal = $(a).find('td').eq(idx).text();
                const bVal = $(b).find('td').eq(idx).text();
                const aNum = parseFloat(aVal.replace(/[^0-9.-]/g, ''));
                const bNum = parseFloat(bVal.replace(/[^0-9.-]/g, ''));
                let cmp = 0;
                if (!isNaN(aNum) && !isNaN(bNum)) {
                    cmp = aNum - bNum;
                } else {
                    cmp = aVal.localeCompare(bVal);
                }
                return asc ? cmp : -cmp;
            });
            $tbody.empty().append(rowsArr);
        });

        return $wrap;
    }

    format_cell(val, col) {
        if (val === null || val === undefined || val === '') return '<span class="text-muted">—</span>';
        const type = col.type || 'text';
        if (type === 'currency') {
            return format_currency(val, col.currency || 'PKR');
        } else if (type === 'percent') {
            return format_number(val, null, 2) + '%';
        } else if (type === 'float') {
            return format_number(val, null, 2);
        } else if (type === 'int' || type === 'qty') {
            return format_number(val, null, 0);
        } else if (type === 'date') {
            return frappe.datetime.str_to_user(val);
        } else if (type === 'link' && col.doctype) {
            return `<span class="ai-assistant-link" data-doctype="${frappe.utils.escape_html(col.doctype)}" data-name="${frappe.utils.escape_html(val)}">${frappe.utils.escape_html(val)}</span>`;
        }
        return frappe.utils.escape_html(String(val));
    }

    render_insights(insights) {
        const $wrap = $('<div class="ai-assistant-insights"></div>');
        insights.forEach((insight) => {
            let borderColor = '#172b4d';
            if (insight.severity === 'critical' || insight.severity === 'high') borderColor = '#ff5858';
            else if (insight.severity === 'medium') borderColor = '#ffa00a';
            else if (insight.severity === 'low') borderColor = '#28d094';
            else if (insight.type === 'info') borderColor = '#7cd6fd';

            const $item = $(`
                <div class="ai-assistant-insight" style="border-left-color:${borderColor};">
                    <div class="ai-assistant-insight-header">
                        <span class="ai-assistant-insight-type">${frappe.utils.escape_html(insight.type)}</span>
                        <span class="ai-assistant-insight-title">${frappe.utils.escape_html(insight.title)}</span>
                    </div>
                    <div class="ai-assistant-insight-desc">${this.markdown_lite(insight.description)}</div>
                    ${insight.actionable ? `<div class="ai-assistant-insight-action text-success small">${__('Actionable')}</div>` : ''}
                </div>
            `);
            $wrap.append($item);
        });
        return $wrap;
    }

    render_warnings(warnings) {
        const $wrap = $('<div class="ai-assistant-warnings"></div>');
        warnings.forEach((w) => {
            const $item = $(`
                <div class="ai-assistant-warning">
                    <span class="ai-assistant-warning-code">${frappe.utils.escape_html(w.code)}</span>
                    <span class="ai-assistant-warning-msg">${frappe.utils.escape_html(w.message)}</span>
                    ${w.affected_metrics ? `<span class="ai-assistant-warning-metrics small text-muted">(${frappe.utils.escape_html(w.affected_metrics.join(', '))})</span>` : ''}
                </div>
            `);
            $wrap.append($item);
        });
        return $wrap;
    }

    render_follow_ups(questions) {
        const $wrap = $('<div class="ai-assistant-followups"></div>');
        questions.forEach((q) => {
            $(`<span class="ai-assistant-followup-chip">${frappe.utils.escape_html(q)}</span>`)
                .on('click', () => this.send_message(q))
                .appendTo($wrap);
        });
        return $wrap;
    }

    render_sources(sources) {
        const names = sources.map((s) => frappe.utils.escape_html(s.name)).join(', ');
        return $(`<div class="ai-assistant-sources small text-muted">${__('Sources:')} ${names}</div>`);
    }
};
