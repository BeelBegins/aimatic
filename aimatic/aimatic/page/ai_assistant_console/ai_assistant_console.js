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

// These are existing user dashboards, not hardcoded KPI calculations. When
// visible to the current user they get a direct, phone-friendly entry point at
// the top of the console; the normal dashboard drawer remains available too.
aimatic.AI_ASSISTANT_EXECUTIVE_DASHBOARDS = [
    { title: 'Executive KPIs', icon: 'chart-bar-big' },
    { title: 'Executive Overview', icon: 'chart-pie' },
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
        this.restore_panel_states();
        this.bind_events();
        this.bind_global_events();
        this.init_voice();
        this.init_settings();
    }

    init_settings() {
        // Available to every allowed role (not just System Manager) so a
        // disabled assistant shows a clear banner instead of every user
        // hitting a cryptic per-message error - see get_ai_integration_settings.
        frappe.call({ method: 'aimatic.ai.api.get_ai_integration_settings' }).then((r) => {
            this.apply_enabled_state(r.message && r.message.enabled !== false);
        });

        if ((frappe.user_roles || []).includes('System Manager')) {
            this.page.add_menu_item(__('AI Settings'), () => this.open_settings_dialog());
        }
    }

    apply_enabled_state(enabled) {
        this.$container.find('.ai-assistant-disabled-banner').remove();
        if (!enabled) {
            $(`<div class="ai-assistant-disabled-banner">${frappe.utils.icon('alert-triangle', 'xs')} ${__('AI Assistant is currently disabled by an administrator.')}</div>`)
                .insertBefore(this.$container.find('.ai-assistant-context-bar'));
        }
        this.$input.prop('disabled', !enabled);
        this.$send.prop('disabled', !enabled);
        this.$mic_btn.prop('disabled', !enabled);
    }

    open_settings_dialog() {
        frappe.call({ method: 'aimatic.ai.api.get_ai_integration_settings' }).then((settings_r) => {
            frappe.call({ method: 'aimatic.ai.api.list_available_free_models' }).then((models_r) => {
                const settings = settings_r.message || {};
                const models = models_r.message || [];
                const options = models.map((m) => ({
                    value: m.id,
                    label: `${m.name} — ${Math.round((m.context_length || 0) / 1000)}K context (free)`,
                }));

                const dialog = new frappe.ui.Dialog({
                    title: __('AI Assistant Settings'),
                    fields: [
                        {
                            fieldname: 'enabled',
                            fieldtype: 'Check',
                            label: __('Enabled'),
                            default: settings.enabled ? 1 : 0,
                        },
                        {
                            fieldname: 'model',
                            fieldtype: 'Select',
                            label: __('Model'),
                            options: options.map((o) => o.value).join('\n'),
                            default: settings.model || (options[0] && options[0].value) || '',
                            description: __('Only currently free, tool-calling-capable OpenRouter models are listed.'),
                        },
                        {
                            fieldname: 'model_note',
                            fieldtype: 'HTML',
                            options: `<p class="text-muted small">${__('The OpenRouter API key itself is configured on the server and is not shown or editable here.')}</p>`,
                        },
                    ],
                    primary_action_label: __('Save'),
                    primary_action: (values) => {
                        frappe.call({
                            method: 'aimatic.ai.api.update_ai_integration_settings',
                            args: { enabled: values.enabled, model: values.model },
                        }).then(() => {
                            frappe.show_alert({ message: __('Saved'), indicator: 'green' });
                            this.apply_enabled_state(!!values.enabled);
                            dialog.hide();
                        });
                    },
                });

                // Custom labels (with context length) don't fit a plain Select's
                // "\n"-joined options string, which only supports value==label -
                // swap in a real <select> with separate value/label after the
                // dialog builds the field from that options string.
                if (options.length) {
                    const $select = dialog.get_field('model').$input;
                    $select.empty();
                    options.forEach((o) => $select.append(`<option value="${frappe.utils.escape_html(o.value)}">${frappe.utils.escape_html(o.label)}</option>`));
                    $select.val(settings.model || options[0].value);
                }

                dialog.show();
            });
        });
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
                    <div class="ai-assistant-executive-shortcuts" aria-label="${__('Executive dashboards')}"></div>
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
                        <button class="btn btn-sm btn-primary ai-assistant-refresh-dashboard" title="${__('Refresh every widget with current data')}" disabled>
                            ${frappe.utils.icon('refresh', 'xs')} <span>${__('Refresh Dashboard')}</span>
                        </button>
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

        // Shared backdrop for the off-canvas drawers (tablet/mobile only, see CSS)
        // - position:fixed takes it out of .ai-assistant-layout's flex flow, so it
        // can just be a plain sibling here without any extra wrapper markup.
        this.$backdrop = $('<div class="ai-assistant-drawer-backdrop"></div>').appendTo(this.$layout);

        this.$container = this.$layout.find('.ai-assistant-container');
        this.$sidebar = this.$layout.find('.ai-assistant-sidebar');
        this.$right_panel = this.$layout.find('.ai-assistant-right-panel');
        this.$conv_list = this.$layout.find('.ai-assistant-conv-list');
        this.$conv_search = this.$layout.find('.ai-assistant-conv-search');
        this.$dashboard_list = this.$layout.find('.ai-assistant-dashboard-list');
        this.$dashboard_view = this.$layout.find('.ai-assistant-dashboard-view');
        this.$dashboard_grid = this.$layout.find('.ai-assistant-dashboard-grid');
        this.$dashboard_view_title = this.$layout.find('.ai-assistant-dashboard-view-title');
        this.$dashboard_refresh = this.$layout.find('.ai-assistant-refresh-dashboard');

        this.$messages = this.$container.find('.ai-assistant-messages');
        this.$executive_shortcuts = this.$container.find('.ai-assistant-executive-shortcuts');
        this.$input = this.$container.find('.ai-assistant-input');
        this.$send = this.$container.find('.ai-assistant-send');
        this.$context_bar = this.$container.find('.ai-assistant-context-bar');
        this.$suggested = this.$container.find('.ai-assistant-suggested-questions');
        this.$mic_btn = this.$container.find('.ai-assistant-mic-btn');
        this.$voice_status = this.$container.find('.ai-assistant-voice-status');

        this.build_context_bar();

        // Wrap the 5 Frappe fields build_context_bar() just added into a
        // collapsible container + prepend a toggle button (mobile only, see
        // CSS) - must run AFTER build_context_bar() populates $context_bar,
        // not before, or there'd be nothing yet to move into the wrapper.
        this.$context_bar_fields = $('<div class="ai-assistant-context-bar-fields"></div>').appendTo(this.$context_bar);
        this.$context_bar_toggle = $('<button type="button" class="ai-assistant-context-bar-toggle"></button>').prependTo(this.$context_bar);
        this.$context_bar.find('.frappe-control').appendTo(this.$context_bar_fields);
        this.update_context_bar_toggle();

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
        this.$layout.find('.ai-assistant-new-btn').on('click', () => {
            this.clear();
            this.close_drawers_if_mobile();
        });
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
            this.maybe_toggle_backdrop(this.$sidebar, collapsed);
            this.persist_panel_state('left', collapsed);
        });
        this.$layout.find('.ai-assistant-right-panel-collapse').on('click', (e) => {
            const collapsed = this.$right_panel.toggleClass('collapsed').hasClass('collapsed');
            const $btn = $(e.currentTarget);
            $btn.attr('title', collapsed ? __('Expand') : __('Collapse'));
            $btn.html(collapsed ? frappe.utils.icon('panel-left', 'sm') : frappe.utils.icon('panel-right', 'sm'));
            this.maybe_toggle_backdrop(this.$right_panel, collapsed);
            this.persist_panel_state('right', collapsed);
        });
        // Same rail fix as the sidebar toggle above - this button lives in its
        // own persistent .ai-assistant-right-panel-rail sibling, not inside
        // .ai-assistant-right-panel itself, so it stays clickable once collapsed.
        this.$conv_search.on('input', () => this.filter_conversation_list(this.$conv_search.val()));

        // Phase 3: Dashboard section events
        this.$layout.find('.ai-assistant-new-dashboard-btn').on('click', () => this.create_dashboard());
        this.$layout.find('.ai-assistant-back-to-chat').on('click', () => this.hide_dashboard_view());
        this.$dashboard_refresh.on('click', () => this.confirm_refresh_dashboard());
    }

    // ─────────────────────────────────────────────────────────────────────
    // Responsive drawer helpers (tablet/mobile off-canvas sidebar + right
    // panel, see the CSS breakpoints). Desktop keeps both panels as normal
    // flex columns toggled by the same rail buttons/`.collapsed` class this
    // app already used pre-redesign; below 1024px CSS turns `.collapsed`
    // into "off-canvas" instead of "display:none", and these helpers add the
    // backdrop + persistence on top of that same toggle.
    // ─────────────────────────────────────────────────────────────────────

    is_mobile_or_tablet() {
        return window.matchMedia('(max-width: 1023px)').matches;
    }

    maybe_toggle_backdrop($panel, collapsed) {
        if (!this.is_mobile_or_tablet()) return;
        if (collapsed) {
            const other_open = !this.$sidebar.hasClass('collapsed') || !this.$right_panel.hasClass('collapsed');
            if (!other_open) this.$backdrop.removeClass('visible');
        } else {
            this.$backdrop.addClass('visible');
        }
    }

    persist_panel_state(side, collapsed) {
        localStorage.setItem(`ai_assistant_${side}_collapsed`, collapsed ? '1' : '0');
    }

    restore_panel_states() {
        // Desktop: default COLLAPSED on first-ever visit (no stored value yet)
        // so the chat gets full width; tablet/mobile: default hidden always,
        // since an off-canvas drawer open by default on a small screen would
        // just cover the chat. Either way, an explicit stored '0'/'1' from a
        // previous manual toggle always wins.
        const left = localStorage.getItem('ai_assistant_left_collapsed');
        const right = localStorage.getItem('ai_assistant_right_collapsed');
        if (this.is_mobile_or_tablet()) {
            if (left !== '0') this.$sidebar.addClass('collapsed');
            if (right !== '0') this.$right_panel.addClass('collapsed');
        } else {
            if (left === null) { this.$sidebar.addClass('collapsed'); this.persist_panel_state('left', true); }
            else if (left === '1') this.$sidebar.addClass('collapsed');
            if (right === null) { this.$right_panel.addClass('collapsed'); this.persist_panel_state('right', true); }
            else if (right === '1') this.$right_panel.addClass('collapsed');
        }
        this.sync_rail_icons();
    }

    sync_rail_icons() {
        const left_collapsed = this.$sidebar.hasClass('collapsed');
        const right_collapsed = this.$right_panel.hasClass('collapsed');
        this.$layout.find('.ai-assistant-sidebar-collapse')
            .attr('title', left_collapsed ? __('Expand') : __('Collapse'))
            .html(left_collapsed ? frappe.utils.icon('chevrons-right', 'sm') : frappe.utils.icon('chevrons-left', 'sm'));
        this.$layout.find('.ai-assistant-right-panel-collapse')
            .attr('title', right_collapsed ? __('Expand') : __('Collapse'))
            .html(right_collapsed ? frappe.utils.icon('panel-left', 'sm') : frappe.utils.icon('panel-right', 'sm'));
    }

    // Closes whichever drawer is open (tablet/mobile only - a no-op on
    // desktop, where the panels are docked, not overlays) - called after the
    // user picks a conversation/dashboard/starts a new one, so they land on
    // the content instead of the drawer still covering it.
    close_drawers_if_mobile() {
        if (!this.is_mobile_or_tablet()) return;
        let closed_any = false;
        if (!this.$sidebar.hasClass('collapsed')) {
            this.$sidebar.addClass('collapsed');
            this.persist_panel_state('left', true);
            closed_any = true;
        }
        if (!this.$right_panel.hasClass('collapsed')) {
            this.$right_panel.addClass('collapsed');
            this.persist_panel_state('right', true);
            closed_any = true;
        }
        if (closed_any) {
            this.$backdrop.removeClass('visible');
            this.sync_rail_icons();
        }
    }

    bind_global_events() {
        this.$backdrop.on('click', () => this.close_drawers_if_mobile());

        $(document).on('keydown.ai_assistant', (e) => {
            if (e.key === 'Escape' && this.is_mobile_or_tablet()) this.close_drawers_if_mobile();
        });

        this.$context_bar_toggle.on('click', () => {
            this.$context_bar_fields.toggleClass('expanded');
            this.update_context_bar_toggle();
        });
    }

    update_context_bar_toggle() {
        const expanded = this.$context_bar_fields.hasClass('expanded');
        this.$context_bar_toggle.html(
            (expanded ? frappe.utils.icon('chevron-up', 'xs') : frappe.utils.icon('chevron-down', 'xs')) +
            ' ' + (expanded ? __('Hide Scope') : __('Show Scope'))
        );
        this.$context_bar_toggle.attr('aria-expanded', expanded);
    }

    // Datetime fields on this bench round-trip with microsecond precision
    // (e.g. "2026-07-19 01:27:33.151565" - confirmed live against real
    // AI Saved Report/AI Assistant Conversation data), which
    // frappe.datetime.str_to_obj's format-string-based moment parsing isn't
    // guaranteed to tolerate. Normalizing to ISO 8601 and using the native
    // Date parser (which handles fractional seconds natively) sidesteps that
    // risk entirely instead of depending on moment's leniency.
    relative_time(datetime_str) {
        if (!datetime_str) return '';
        const then = new Date(datetime_str.replace(' ', 'T'));
        if (isNaN(then.getTime())) return '';
        const diff_sec = Math.floor((Date.now() - then.getTime()) / 1000);
        if (diff_sec < 60) return __('Just now');
        const diff_min = Math.floor(diff_sec / 60);
        if (diff_min < 60) return __('{0} min ago', [diff_min]);
        const diff_hr = Math.floor(diff_min / 60);
        if (diff_hr < 24) return __('{0} hr ago', [diff_hr]);
        const diff_day = Math.floor(diff_hr / 24);
        if (diff_day < 7) return __('{0} day ago', [diff_day]);
        return frappe.datetime.str_to_user(datetime_str);
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
            $row.on('click', () => {
                this.open_dashboard(db.name);
                this.close_drawers_if_mobile();
            });
            this.$dashboard_list.append($row);
        });
    }

    render_executive_shortcuts(dashboards) {
        const byTitle = new Map((dashboards || []).map((dashboard) => [
            (dashboard.title || '').trim().toLowerCase(),
            dashboard,
        ]));
        const featured = aimatic.AI_ASSISTANT_EXECUTIVE_DASHBOARDS
            .map((definition) => ({
                definition,
                dashboard: byTitle.get(definition.title.toLowerCase()),
            }))
            .filter((entry) => entry.dashboard);

        this.$executive_shortcuts.empty().toggleClass('hidden', !featured.length);
        if (!featured.length) return;

        this.$executive_shortcuts.append(`
            <div class="ai-assistant-executive-label">
                ${frappe.utils.icon('briefcase', 'xs')}
                <span>${__('Executive')}</span>
            </div>
            <div class="ai-assistant-executive-actions"></div>
        `);
        const $actions = this.$executive_shortcuts.find('.ai-assistant-executive-actions');
        featured.forEach(({ definition, dashboard }) => {
            const $button = $(
                `<button type="button" class="btn btn-default ai-assistant-executive-shortcut">
                    <span class="ai-assistant-executive-icon">${frappe.utils.icon(definition.icon, 'sm')}</span>
                    <span class="ai-assistant-executive-text">
                        <strong>${frappe.utils.escape_html(dashboard.title)}</strong>
                        <small>${__('{0} widgets', [dashboard.widget_count || 0])}</small>
                    </span>
                    <span class="ai-assistant-executive-open">${frappe.utils.icon('chevron-right', 'xs')}</span>
                </button>`
            );
            $button.on('click', () => this.open_dashboard(dashboard.name));
            $actions.append($button);
        });
    }

    refresh_dashboard_list() {
        frappe.call({ method: 'aimatic.ai.api.list_dashboards' }).then((r) => {
            this.dashboards = (r.message && r.message.dashboards) || [];
            this.render_dashboard_list(this.dashboards);
            this.render_executive_shortcuts(this.dashboards);
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
        this.$dashboard_refresh.prop('disabled', true);
        return frappe.call({ method: 'aimatic.ai.api.get_dashboard', args: { name: name } }).then((r) => {
            // get_dashboard returns {"name", "title", "widgets"} directly -
            // r.message itself IS the dashboard object, not r.message.dashboard.
            const dashboard = r.message;
            if (dashboard) {
                this.current_dashboard = dashboard;
                this.$dashboard_view_title.text(dashboard.title);
                this.render_dashboard_view(dashboard);
                this.$dashboard_refresh.prop('disabled', !(dashboard.widgets || []).length);
            }
            return dashboard;
        }).catch((error) => {
            // Don't strand the control in a permanently-disabled state if
            // the dashboard read itself fails. frappe.call already surfaces
            // the server error; keep the previous dashboard usable for retry.
            this.$dashboard_refresh.prop(
                'disabled',
                !(this.current_dashboard && (this.current_dashboard.widgets || []).length)
            );
            console.error('Dashboard load failed:', name, error);
            return null;
        });
    }

    confirm_refresh_dashboard() {
        const dashboard = this.current_dashboard;
        if (!dashboard) return;

        const reportNames = [...new Set(
            (dashboard.widgets || []).map((widget) => widget.saved_report).filter(Boolean)
        )];
        if (!reportNames.length) {
            frappe.show_alert({ message: __('This dashboard has no widgets to refresh'), indicator: 'orange' });
            return;
        }

        frappe.confirm(
            __('Refresh {0} dashboard widgets with current data? This uses one or more OpenRouter requests per widget and may take a few minutes.', [reportNames.length]),
            () => this.refresh_dashboard(dashboard, reportNames)
        );
    }

    async refresh_dashboard(dashboard, reportNames) {
        if (this.dashboard_refresh_in_progress) return;
        this.dashboard_refresh_in_progress = true;

        const total = reportNames.length;
        let refreshed = 0;
        let noData = 0;
        let failed = 0;

        this.$dashboard_refresh.prop('disabled', true);

        try {
            // Refresh sequentially: all sites share one OpenRouter key and the
            // free tier has a tight concurrency cap. A Promise.all here makes
            // a multi-widget dashboard much more likely to hit HTTP 429s.
            for (let index = 0; index < total; index++) {
                this.$dashboard_refresh.html(
                    frappe.utils.icon('refresh', 'xs') +
                    ` <span>${__('Refreshing {0}/{1}', [index + 1, total])}</span>`
                );

                try {
                    const result = await frappe.call({
                        method: 'aimatic.ai.api.refresh_saved_report',
                        args: { name: reportNames[index] },
                    });
                    const response = result.message || {};
                    if ((response.kpis || []).length || (response.charts || []).length || (response.tables || []).length) {
                        refreshed++;
                    } else {
                        // refresh_saved_report protects an existing good
                        // snapshot when a transient model response has no
                        // structured data. Report it without aborting the rest.
                        noData++;
                    }
                } catch (error) {
                    failed++;
                    console.error('Dashboard widget refresh failed:', reportNames[index], error);
                }
            }

            const reloadedDashboard = await this.open_dashboard(dashboard.name);
            if (!reloadedDashboard) {
                frappe.show_alert({
                    message: __('Widget data was refreshed, but the dashboard could not reload. Please reopen it.'),
                    indicator: 'orange',
                }, 7);
                return;
            }

            const skipped = noData + failed;
            frappe.show_alert({
                message: skipped
                    ? __('Dashboard refreshed: {0} updated, {1} kept their previous snapshot.', [refreshed, skipped])
                    : __('Dashboard refreshed: {0} widgets updated.', [refreshed]),
                indicator: skipped ? 'orange' : 'green',
            }, 7);
        } finally {
            this.dashboard_refresh_in_progress = false;
            this.$dashboard_refresh
                .html(frappe.utils.icon('refresh', 'xs') + ` <span>${__('Refresh Dashboard')}</span>`)
                .prop('disabled', !(this.current_dashboard && (this.current_dashboard.widgets || []).length));
        }
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
            const meta_html = conv.last_activity
                ? `<div class="ai-assistant-conv-meta">${this.relative_time(conv.last_activity)}</div>`
                : '';
            const $row = $(`
                <div class="ai-assistant-conv-row ${conv.name === this.current_conversation ? 'active' : ''}" data-name="${frappe.utils.escape_html(conv.name)}">
                    <div class="ai-assistant-conv-row-top">
                        <span class="ai-assistant-conv-pin-icon">${conv.pinned ? frappe.utils.icon('pin', 'xs') : ''}</span>
                        <span class="ai-assistant-conv-title">${frappe.utils.escape_html(conv.title)}</span>
                        <span class="ai-assistant-conv-actions">
                            <button class="btn-reset ai-assistant-conv-pin" title="${__('Pin')}">${frappe.utils.icon('pin', 'xs')}</button>
                            <button class="btn-reset ai-assistant-conv-rename" title="${__('Rename')}">${frappe.utils.icon('pencil', 'xs')}</button>
                            <button class="btn-reset ai-assistant-conv-delete" title="${__('Delete')}">${frappe.utils.icon('trash', 'xs')}</button>
                        </span>
                    </div>
                    ${meta_html}
                </div>
            `);
            $row.find('.ai-assistant-conv-title').on('click', () => {
                this.open_conversation(conv.name);
                this.close_drawers_if_mobile();
            });
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
        this.current_conversation = name;
        this.history = [];
        this.$messages.empty();
        this._load_conversation_page(name, null, true);

        this.$sidebar.find('.ai-assistant-conv-row').removeClass('active');
        this.$sidebar.find(`.ai-assistant-conv-row[data-name="${name}"]`).addClass('active');
    }

    // Cursor-paginated conversation loading (get_conversation_messages caps
    // each page at 100 rather than returning a whole long-lived
    // conversation's unbounded transcript in one query/DOM append). The
    // first page (before=null) appends and scrolls to bottom exactly like
    // the old single-shot load; an older page (via the "Load older
    // messages" button) prepends above the existing bubbles instead.
    // Bubbles for one page are built into an array first and inserted as a
    // single batch (jQuery preserves array order on append/prepend) - doing
    // it one .prepend() call per message in a loop would insert each new
    // bubble at the very top and silently reverse that page's order.
    _load_conversation_page(conversation, before, is_first_page) {
        this.set_sending(true);
        frappe.call({
            method: 'aimatic.ai.api.get_conversation_messages',
            args: { conversation: conversation, limit: 100, before: before },
        }).then((r) => {
            this.set_sending(false);
            const data = r.message || {};
            const messages = data.messages || [];
            const has_more = !!data.has_more;

            this.$messages.find('.ai-assistant-load-older-btn').remove();

            const bubbles = messages.map((m) => {
                if (is_first_page) {
                    this.history.push({ role: m.role, content: m.content });
                } else {
                    this.history.unshift({ role: m.role, content: m.content });
                }
                return this.build_bubble(m.role, m.content)[0];
            });

            if (is_first_page) {
                this.$messages.append(bubbles);
                this.$messages.scrollTop(this.$messages[0].scrollHeight);
            } else if (bubbles.length) {
                this.$messages.prepend(bubbles);
            }

            if (has_more && messages.length) {
                const oldest_creation = messages[0].creation;
                const $btn = $(`<button type="button" class="btn btn-xs btn-default ai-assistant-load-older-btn">${__('Load older messages')}</button>`);
                $btn.on('click', () => {
                    $btn.prop('disabled', true).text(__('Loading…'));
                    this._load_conversation_page(conversation, oldest_creation, false);
                });
                this.$messages.prepend($btn);
            }
        }).catch(() => {
            this.set_sending(false);
        });
    }

    update_right_panel(response) {
        // Company/Branch/Date Range are deliberately NOT repeated here - they're
        // already live in the context bar right above the chat; this panel now
        // only carries the two facts that aren't visible anywhere else.
        if (!this.$right_panel) return;
        const ctx = response.context || {};
        const sources = response.sources || [];
        const sources_html = sources.length
            ? sources.map((s) => `<div class="ai-assistant-rp-source"><span class="ai-assistant-rp-source-type">${frappe.utils.escape_html(s.type)}</span> ${frappe.utils.escape_html(s.name)}</div>`).join('')
            : `<div class="small text-muted">${__('No specific data source (general knowledge answer).')}</div>`;

        this.$right_panel.find('.ai-assistant-right-panel-body').html(`
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

        const $container = this.$suggested.empty();
        const count = questions.length;

        const $header = $(`
            <div class="ai-assistant-suggested-header">
                <span class="ai-assistant-suggested-icon">${frappe.utils.icon('lightbulb', 'xs')}</span>
                <span class="ai-assistant-suggested-label">${__('Suggested Questions')}</span>
                <span class="ai-assistant-suggested-count">(${count})</span>
                <span class="ai-assistant-suggested-chevron">${frappe.utils.icon('chevron-down', 'xs')}</span>
            </div>
        `).appendTo($container);

        const $list = $('<div class="ai-assistant-suggested-list" hidden></div>').appendTo($container);

        questions.forEach((q) => {
            $(`<button class="btn btn-default btn-xs ai-assistant-suggested-btn">${frappe.utils.escape_html(q)}</button>`)
                .on('click', () => {
                    this.send_message(q);
                    this._collapse_suggested();
                })
                .appendTo($list);
        });

        $header.on('click', () => this._toggle_suggested($header, $list));
    }

    _toggle_suggested($header, $list) {
        const isExpanded = !$list.prop('hidden');
        $list.prop('hidden', isExpanded);
        $header.toggleClass('expanded', !isExpanded);
        $header.find('.ai-assistant-suggested-chevron').html(
            frappe.utils.icon(isExpanded ? 'chevron-down' : 'chevron-up', 'xs')
        );
    }

    _collapse_suggested() {
        const $header = this.$suggested.find('.ai-assistant-suggested-header');
        const $list = this.$suggested.find('.ai-assistant-suggested-list');
        if (!$list.prop('hidden')) {
            this._toggle_suggested($header, $list);
        }
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
    // Voice input records in the browser, converts the recording to a compact
    // mono WAV, then asks a live-verified zero-cost OpenRouter audio model for
    // text. The transcript is always editable and is never auto-submitted.
    // ─────────────────────────────────────────────────────────────────────

    init_voice() {
        this.voice_base_text = '';
        this.voice_committed_transcript = '';
        this.voice_model = '';
        this.voice_error = '';
        this.voice_request_id = 0;
        this.media_recorder = null;
        this.audio_chunks = [];
        this.voice_max_timer = null;

        if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder || !(window.AudioContext || window.webkitAudioContext)) {
            this.voice_state = 'unsupported';
            this.$mic_btn.prop('disabled', true).attr('title', __('Audio recording is not supported in this browser.'));
            this.render_voice_status();
            return;
        }
        this.$mic_btn.on('click', () => {
            if (this.voice_state === 'recording') {
                this.stop_recording();
            } else if (this.voice_state !== 'processing') {
                this.start_recording();
            }
        });
    }

    async start_recording() {
        if (this.voice_state === 'unsupported' || this.voice_state === 'processing' || this.sending) return;

        const requestId = ++this.voice_request_id;
        this.voice_base_text = (this.$input.val() || '').trimEnd();
        this.voice_committed_transcript = '';
        this.voice_model = '';
        this.voice_error = '';
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                },
            });
            if (requestId !== this.voice_request_id) {
                stream.getTracks().forEach((track) => track.stop());
                return;
            }

            const mimeType = this.get_recording_mime_type();
            this.audio_stream = stream;
            this.audio_chunks = [];
            this.media_recorder = mimeType
                ? new MediaRecorder(stream, { mimeType })
                : new MediaRecorder(stream);
            this.media_recorder.ondataavailable = (event) => {
                if (event.data?.size) this.audio_chunks.push(event.data);
            };
            this.media_recorder.onerror = () => {
                this.voice_request_id++;
                this.voice_error = __('The browser could not record audio.');
                this.voice_state = 'failed';
                this.cleanup_voice_capture();
                this.render_voice_status();
            };
            this.media_recorder.onstop = () => {
                const blob = new Blob(this.audio_chunks, {
                    type: this.media_recorder?.mimeType || mimeType || 'audio/webm',
                });
                this.media_recorder = null;
                this.audio_chunks = [];
                this.process_recorded_audio(blob, requestId);
            };
            this.media_recorder.start(250);
            this.voice_state = 'recording';
            this.recording_start_ms = Date.now();
            this.render_voice_status();
            this.start_duration_timer();
            this.start_level_meter(stream);
            this.voice_max_timer = setTimeout(() => this.stop_recording(), 45000);
        } catch (e) {
            this.voice_state = (e?.name === 'NotAllowedError' || e?.name === 'SecurityError')
                ? 'permission_denied'
                : 'failed';
            this.voice_error = e?.message || '';
            this.cleanup_voice_capture();
            this.render_voice_status();
        }
    }

    get_recording_mime_type() {
        return [
            'audio/webm;codecs=opus',
            'audio/webm',
            'audio/mp4',
            'audio/ogg;codecs=opus',
        ].find((type) => MediaRecorder.isTypeSupported(type)) || '';
    }

    async process_recorded_audio(blob, requestId) {
        this.cleanup_voice_capture();
        if (requestId !== this.voice_request_id) return;

        try {
            if (!blob?.size) throw new Error(__('No speech was recorded.'));
            const audioBase64 = await this.convert_blob_to_wav_base64(blob);
            if (requestId !== this.voice_request_id) return;
            const response = await frappe.call({
                method: 'aimatic.ai.api.transcribe_audio',
                args: {
                    audio_base64: audioBase64,
                    audio_format: 'wav',
                    language_hint: 'auto',
                },
            });
            if (requestId !== this.voice_request_id) return;
            const result = response.message || {};
            if (!result.transcript) throw new Error(__('No transcript was returned.'));

            this.voice_committed_transcript = result.transcript.trim();
            this.voice_model = result.model || '';
            this.update_voice_input();
            this.voice_state = 'ready';
        } catch (e) {
            if (requestId !== this.voice_request_id) return;
            this.voice_error = e?.message || __('Free OpenRouter transcription failed.');
            this.voice_state = 'failed';
        }
        this.render_voice_status();
    }

    async convert_blob_to_wav_base64(blob) {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        const context = new AudioContextClass();
        try {
            const decoded = await context.decodeAudioData(await blob.arrayBuffer());
            const targetRate = 16000;
            const sampleCount = Math.max(1, Math.round(decoded.length * targetRate / decoded.sampleRate));
            const samples = new Float32Array(sampleCount);
            const ratio = decoded.sampleRate / targetRate;
            for (let i = 0; i < sampleCount; i++) {
                const start = Math.floor(i * ratio);
                const end = Math.min(decoded.length, Math.max(start + 1, Math.floor((i + 1) * ratio)));
                let sum = 0;
                let count = 0;
                for (let channel = 0; channel < decoded.numberOfChannels; channel++) {
                    const channelData = decoded.getChannelData(channel);
                    for (let sourceIndex = start; sourceIndex < end; sourceIndex++) {
                        sum += channelData[sourceIndex];
                        count++;
                    }
                }
                samples[i] = count ? sum / count : 0;
            }
            return this.encode_pcm16_wav_base64(samples, targetRate);
        } finally {
            context.close().catch(() => {});
        }
    }

    encode_pcm16_wav_base64(samples, sampleRate) {
        const buffer = new ArrayBuffer(44 + samples.length * 2);
        const view = new DataView(buffer);
        const writeText = (offset, text) => {
            for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
        };
        writeText(0, 'RIFF');
        view.setUint32(4, 36 + samples.length * 2, true);
        writeText(8, 'WAVE');
        writeText(12, 'fmt ');
        view.setUint32(16, 16, true);
        view.setUint16(20, 1, true);
        view.setUint16(22, 1, true);
        view.setUint32(24, sampleRate, true);
        view.setUint32(28, sampleRate * 2, true);
        view.setUint16(32, 2, true);
        view.setUint16(34, 16, true);
        writeText(36, 'data');
        view.setUint32(40, samples.length * 2, true);
        for (let i = 0; i < samples.length; i++) {
            const value = Math.max(-1, Math.min(1, samples[i]));
            view.setInt16(44 + i * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true);
        }

        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let offset = 0; offset < bytes.length; offset += 0x8000) {
            binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
        }
        return btoa(binary);
    }

    update_voice_input() {
        const value = [this.voice_base_text, this.voice_committed_transcript]
            .filter(Boolean)
            .join(' ');
        this.$input.val(value).trigger('input');
        const input = this.$input.get(0);
        if (input) {
            // Keep the newest recognized words visible on a one-row mobile
            // textarea without focusing it (which would open the keyboard).
            input.setSelectionRange(value.length, value.length);
            input.scrollTop = input.scrollHeight;
        }
    }

    stop_recording() {
        if (this.voice_state !== 'recording' || !this.media_recorder) return;
        clearTimeout(this.voice_max_timer);
        this.voice_max_timer = null;
        this.stop_duration_timer();
        this.voice_state = 'processing';
        this.render_voice_status();
        try {
            this.media_recorder.stop();
        } catch (e) {
            this.voice_error = e?.message || __('The browser could not stop recording.');
            this.voice_state = 'failed';
            this.cleanup_voice_capture();
            this.render_voice_status();
        }
    }

    cleanup_voice_capture() {
        clearTimeout(this.voice_max_timer);
        this.voice_max_timer = null;
        this.stop_duration_timer();
        this.stop_level_meter();
    }

    finish_voice_for_send() {
        const message = this.$input.val() || '';
        this.cleanup_voice_capture();
        this.voice_base_text = '';
        this.voice_committed_transcript = '';
        this.voice_model = '';
        this.voice_error = '';
        this.voice_state = 'idle';
        this.render_voice_status();
        return message;
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

    start_level_meter(stream) {
        try {
            this.audio_context = new (window.AudioContext || window.webkitAudioContext)();
            const source = this.audio_context.createMediaStreamSource(stream);
            this.analyser = this.audio_context.createAnalyser();
            this.analyser.fftSize = 32;
            source.connect(this.analyser);
            this.tick_level_meter();
        } catch (e) {
            // The level meter is cosmetic; recording can continue without it.
        }
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
        const isRecording = this.voice_state === 'recording';
        const isProcessing = this.voice_state === 'processing';
        this.$mic_btn
            .toggleClass('recording', isRecording)
            .toggleClass('processing', isProcessing)
            .prop('disabled', this.voice_state === 'unsupported' || isProcessing || this.sending)
            .html(isRecording ? frappe.utils.icon('circle-stop', 'sm') : frappe.utils.icon('mic', 'sm'));
        this.$send.prop('disabled', Boolean(this.sending || isRecording || isProcessing));

        if (isRecording) {
            const secs = Math.floor((Date.now() - this.recording_start_ms) / 1000);
            const mm = String(Math.floor(secs / 60)).padStart(2, '0');
            const ss = String(secs % 60).padStart(2, '0');
            this.$voice_status.html(`
                <span class="ai-assistant-voice-rec-dot"></span>
                <span class="ai-assistant-voice-duration">${mm}:${ss}</span>
                <span class="ai-assistant-voice-bars">
                    ${[0, 1, 2, 3, 4].map(() => '<span class="ai-assistant-voice-bar"></span>').join('')}
                </span>
                <span class="small text-muted">${__('Recording — tap Stop to transcribe (maximum 45 seconds).')}</span>
            `);
        } else if (isProcessing) {
            this.$voice_status.html(`
                <span class="ai-assistant-voice-spinner" aria-hidden="true"></span>
                <span class="small text-muted">${__('Transcribing with a free OpenRouter model…')}</span>
            `);
        } else if (this.voice_state === 'ready') {
            const model = frappe.utils.escape_html(this.voice_model || '');
            this.$voice_status.html(`
                <span class="small text-muted">${__('Transcript ready — edit if needed, then Send.')}</span>
                ${model ? `<span class="small ai-assistant-voice-model" title="${model}">${__('Free model')}</span>` : ''}
                <span class="small text-muted">${__('Language is auto-detected; Urdu is best-effort on the current free model.')}</span>
                <button class="btn btn-xs ai-assistant-voice-discard">${__('Discard')}</button>
            `);
            this.$voice_status.find('.ai-assistant-voice-discard').on('click', () => {
                this.$input.val(this.voice_base_text || '').trigger('input');
                this.voice_committed_transcript = '';
                this.voice_model = '';
                this.voice_error = '';
                this.voice_state = 'idle';
                this.render_voice_status();
            });
        } else if (this.voice_state === 'permission_denied') {
            this.$voice_status.html(`<span class="small ai-assistant-voice-error">${__('Microphone permission denied. Allow microphone access in your browser to use voice input.')}</span>`);
        } else if (this.voice_state === 'network_error') {
            this.$voice_status.html(`<span class="small ai-assistant-voice-error">${__('Transcription needs a network connection. Try again or type your question.')}</span>`);
        } else if (this.voice_state === 'failed') {
            const detail = frappe.utils.escape_html(this.voice_error || '');
            this.$voice_status.html(`<span class="small ai-assistant-voice-error">${__('Free OpenRouter transcription failed. No paid fallback was used. Try again or type your question.')}${detail ? ` ${detail}` : ''}</span>`);
        } else if (this.voice_state === 'unsupported') {
            this.$voice_status.html(`<span class="small text-muted">${__('Audio recording is not supported in this browser.')}</span>`);
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
        if (this.voice_state === 'recording') {
            this.stop_recording();
            frappe.show_alert({ message: __('Wait for the transcript, review it, then tap Send.'), indicator: 'blue' });
            return;
        }
        if (this.voice_state === 'processing') {
            frappe.show_alert({ message: __('Transcription is still in progress.'), indicator: 'blue' });
            return;
        }
        if (this.voice_state === 'ready') {
            rawMessage = this.finish_voice_for_send();
        }
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
        this.$send
            .prop('disabled', Boolean(sending || this.voice_state === 'recording' || this.voice_state === 'processing'))
            .text(sending ? __('Thinking…') : __('Send'));
        this.render_voice_status();
    }

    build_bubble(role, content, is_error) {
        const $bubble = $(`
            <div class="ai-assistant-bubble ai-assistant-bubble-${role}${is_error ? ' ai-assistant-bubble-error' : ''}">
                <div class="ai-assistant-bubble-role">${role === 'user' ? __('You') : __('Assistant')}</div>
                <div class="ai-assistant-bubble-content"></div>
            </div>
        `);
        $bubble.find('.ai-assistant-bubble-content').text(content);
        return $bubble;
    }

    append_bubble(role, content, is_error) {
        this.$messages.append(this.build_bubble(role, content, is_error));
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
                    ${w.affected_metrics && w.affected_metrics.length ? `<span class="ai-assistant-warning-metrics small text-muted">(${frappe.utils.escape_html(w.affected_metrics.join(', '))})</span>` : ''}
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
