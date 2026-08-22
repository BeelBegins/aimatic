frappe.pages["help-console"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Aimatic Help"),
		single_column: true,
	});

	const $body = $(page.body);
	$body.html(`
		<div class="aimatic-help-page" style="max-width:720px;margin:0 auto;padding:16px;">
			<p class="text-muted" style="margin-bottom:12px;">
				${__("How-to guidance for ERPNext. This is not the reporting AI Assistant.")}
			</p>
			<div class="aimatic-help-page-context text-muted" style="margin-bottom:8px;font-size:12px;"></div>
			<div class="aimatic-help-page-messages" style="min-height:320px;max-height:55vh;overflow:auto;border:1px solid var(--border-color);border-radius:8px;padding:12px;background:var(--fg-color);margin-bottom:12px;"></div>
			<div class="aimatic-help-page-chips" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;"></div>
			<div style="display:flex;gap:8px;">
				<textarea class="form-control aimatic-help-page-input" rows="2" placeholder="${__("Ask how to do something…")}"></textarea>
				<button class="btn btn-primary aimatic-help-page-send">${__("Send")}</button>
			</div>
		</div>
	`);

	const state = {
		conversation: null,
		history: [],
		sending: false,
		context: {},
	};

	const $msgs = $body.find(".aimatic-help-page-messages");
	const $chips = $body.find(".aimatic-help-page-chips");
	const $input = $body.find(".aimatic-help-page-input");
	const $send = $body.find(".aimatic-help-page-send");
	const $ctx = $body.find(".aimatic-help-page-context");

	function append(role, text) {
		const cls = role === "user" ? "alert-success" : "alert-secondary";
		const $el = $(`<div class="alert ${cls}" style="white-space:pre-wrap;"></div>`);
		$el.text(text);
		$msgs.append($el);
		$msgs.scrollTop($msgs[0].scrollHeight);
	}

	function refreshContext() {
		const route = frappe.get_route ? frappe.get_route() : [];
		state.context = {
			route: frappe.get_route_str ? frappe.get_route_str() : "",
			doctype: route[0] === "Form" || route[0] === "List" ? route[1] : null,
		};
		$ctx.text(__("Helping with: {0}", [state.context.doctype || "ERPNext"]));
	}

	function loadStarters() {
		frappe.call({
			method: "aimatic.help.api.list_starters",
			args: { context: JSON.stringify(state.context) },
			callback: (r) => {
				$chips.empty();
				((r.message && r.message.starters) || []).forEach((q) => {
					const $b = $(`<button class="btn btn-xs btn-default"></button>`).text(q);
					$b.on("click", () => send(q));
					$chips.append($b);
				});
			},
		});
	}

	function ensureConversation() {
		if (state.conversation) return Promise.resolve(state.conversation);
		return new Promise((resolve, reject) => {
			frappe.call({
				method: "aimatic.help.api.start_conversation",
				args: { context: JSON.stringify(state.context) },
				callback: (r) => {
					if (r.message && r.message.name) {
						state.conversation = r.message.name;
						resolve(state.conversation);
					} else reject();
				},
				error: () => reject(),
			});
		});
	}

	function send(preset) {
		const text = (preset || $input.val() || "").trim();
		if (!text || state.sending) return;
		state.sending = true;
		$send.prop("disabled", true).text("…");
		$input.val("");
		append("user", text);
		$chips.empty();
		ensureConversation()
			.then((conversation) => {
				frappe.call({
					method: "aimatic.help.api.ask",
					args: {
						message: text,
						history: JSON.stringify(state.history),
						conversation,
						context: JSON.stringify(state.context),
					},
					callback: (r) => {
						const reply = (r.message && r.message.reply) || __("No reply.");
						state.history.push({ role: "user", content: text });
						state.history.push({ role: "assistant", content: reply });
						append("assistant", reply);
					},
					error: () => append("assistant", __("Help is temporarily unavailable.")),
					always: () => {
						state.sending = false;
						$send.prop("disabled", false).text(__("Send"));
					},
				});
			})
			.catch(() => {
				append("assistant", __("Could not start help session."));
				state.sending = false;
				$send.prop("disabled", false).text(__("Send"));
			});
	}

	$send.on("click", () => send());
	$input.on("keydown", (e) => {
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	});

	append(
		"assistant",
		__("Hi! Ask how to use ERPNext — Item, Price List, Stock, Accounts, and more.")
	);
	refreshContext();
	loadStarters();
};
