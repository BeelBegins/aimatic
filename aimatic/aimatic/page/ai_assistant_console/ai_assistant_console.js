frappe.provide('aimatic');

frappe.pages['ai-assistant-console'].on_page_load = function (wrapper) {
    new aimatic.AiAssistantPage(wrapper);
};

// Quick-access KPI buttons - each just sends its canned question through the same
// send_message() pipeline as manually typed text, so the model still does all the
// tool-routing itself (no separate direct-query path to keep in sync).
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

// Every exchange is persisted server-side (AI Assistant Message, 30-day retention -
// see ai/tasks.py), and `this.history` is bootstrapped from that on page load via
// get_recent_history, so reopening the page resumes the same conversation. Within
// a session, `this.history` is still what gets resent (capped) with every `ask`
// call - loading it from the server just changes what it starts as, not the
// resend mechanism itself.
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
        this.build_layout();
        this.bind_events();
        this.load_history();
    }

    load_history() {
        frappe.call({
            method: 'aimatic.ai.api.get_recent_history',
            callback: (r) => {
                const history = (r.message && r.message.history) || [];
                this.history = history;
                history.forEach((turn) => this.append_bubble(turn.role, turn.content));
            },
        });
    }

    build_layout() {
        this.$container = $(`
            <div class="ai-assistant-container">
                <div class="ai-assistant-intro">
                    ${__('Ask about sales, purchases, vendors, or inventory - e.g. "what were my sales today?", "who are my worst vendors?", "which items are overstocked?" - or use a quick button below.')}
                </div>
                <div class="ai-assistant-kpi-buttons"></div>
                <div class="ai-assistant-messages"></div>
                <div class="ai-assistant-input-row">
                    <textarea class="ai-assistant-input form-control" rows="1"
                        placeholder="${__('Ask a question…')}"></textarea>
                    <button class="btn btn-primary ai-assistant-send">${__('Send')}</button>
                </div>
            </div>
        `).appendTo(this.page.main.empty());

        this.$messages = this.$container.find('.ai-assistant-messages');
        this.$input = this.$container.find('.ai-assistant-input');
        this.$send = this.$container.find('.ai-assistant-send');
        this.$kpi_buttons = this.$container.find('.ai-assistant-kpi-buttons');
        this.build_kpi_buttons();
    }

    build_kpi_buttons() {
        aimatic.AI_ASSISTANT_KPI_BUTTONS.forEach((kpi) => {
            $(`<button class="btn btn-default btn-xs ai-assistant-kpi-btn">${frappe.utils.escape_html(kpi.label)}</button>`)
                .on('click', () => this.send_message(kpi.question))
                .appendTo(this.$kpi_buttons);
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

    clear() {
        // Only resets this tab's own view/context - the persisted server-side
        // record is never deleted from here (it's an audit log, see ai/tasks.py),
        // so reopening/reloading the page will restore the full history again.
        this.history = [];
        this.$messages.empty();
    }

    send_message(rawMessage) {
        const message = (rawMessage || '').trim();
        if (!message || this.sending) return;

        this.$input.val('');
        this.append_bubble('user', message);
        this.set_sending(true);

        frappe.call({
            method: 'aimatic.ai.api.ask',
            args: {
                message: message,
                history: JSON.stringify(this.history),
            },
            callback: (r) => {
                const reply = (r.message && r.message.reply) || __('(no answer)');
                this.history.push({ role: 'user', content: message });
                this.history.push({ role: 'assistant', content: reply });
                this.append_bubble('assistant', reply);
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
};
