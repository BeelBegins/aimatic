/**
 * Desk-wide Aimatic Help float — how-to guidance only.
 * Independent of /app/ai-assistant-console (BI).
 */
frappe.provide("aimatic.help_float");

(function () {
	"use strict";

	if (window.__aimaticHelpFloatBooted) {
		return;
	}
	window.__aimaticHelpFloatBooted = true;

	const ICON =
		'<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3.5a8 8 0 0 0-8 8c0 2.6 1.2 4.9 3.1 6.4V21l3.2-1.7c.55.1 1.12.2 1.7.2a8 8 0 0 0 0-16Z" stroke="#fff" stroke-width="1.6"/><path d="M9.2 11.2h5.6M9.2 14h3.8" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/></svg>';

	const LAUNCHER_STYLE =
		"position:fixed;right:16px;bottom:20px;z-index:2147483000;width:56px;height:56px;" +
		"border-radius:14px;border:2px solid rgba(255,255,255,0.45);background:#0f766e;color:#fff;" +
		"cursor:pointer;display:flex;align-items:center;justify-content:center;" +
		"box-shadow:0 10px 28px rgba(15,118,110,0.45);";

	function el(tag, attrs, html) {
		const node = document.createElement(tag);
		if (attrs) {
			Object.keys(attrs).forEach((key) => {
				if (key === "className") node.className = attrs[key];
				else if (key === "text") node.textContent = attrs[key];
				else if (key === "style") node.setAttribute("style", attrs[key]);
				else node.setAttribute(key, attrs[key]);
			});
		}
		if (html) node.innerHTML = html;
		return node;
	}

	function escapeHtml(text) {
		if (frappe.utils && frappe.utils.escape_html) {
			return frappe.utils.escape_html(String(text || ""));
		}
		return String(text || "")
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;");
	}

	function linkify(text) {
		let html = escapeHtml(text);
		html = html.replace(
			/\[([^\]]+)\]\((\/app\/[^)\s]+)\)/g,
			'<a href="$2" data-ah-route="$2">$1</a>'
		);
		html = html.replace(
			/(^|[\s(])(\/app\/[a-z0-9\-\/_%]+)/gi,
			'$1<a href="$2" data-ah-route="$2">$2</a>'
		);
		return html;
	}

	function collectHelpLinks(routeStr) {
		const links = [];
		const map = (frappe.help && frappe.help.help_links) || {};
		if (!routeStr) return links;
		const parts = routeStr.split("/");
		for (let i = parts.length; i > 0; i--) {
			const key = parts.slice(0, i).join("/");
			const rows = map[key];
			if (Array.isArray(rows)) {
				rows.forEach((row) => {
					if (row && (row.label || row.url)) {
						links.push({ label: row.label || row.url, url: row.url || "" });
					}
				});
				break;
			}
		}
		return links.slice(0, 6);
	}

	function readDeskContext() {
		const route = frappe.get_route ? frappe.get_route() : [];
		const routeStr = frappe.get_route_str
			? frappe.get_route_str()
			: (route || []).join("/");
		let doctype = null;
		let docname = null;
		if (Array.isArray(route) && route.length >= 2 && (route[0] === "Form" || route[0] === "List")) {
			doctype = route[1];
			if (route[0] === "Form" && route[2]) docname = route[2];
		} else if (typeof cur_frm !== "undefined" && cur_frm && cur_frm.doctype) {
			doctype = cur_frm.doctype;
			docname = cur_frm.docname || (cur_frm.doc && cur_frm.doc.name) || null;
		}
		let meta_description = null;
		let documentation_url = null;
		if (doctype && frappe.get_meta) {
			try {
				const meta = frappe.get_meta(doctype);
				if (meta) {
					meta_description = meta.description || null;
					documentation_url = meta.documentation || null;
				}
			} catch (e) {
				/* ignore */
			}
		}
		return {
			route: routeStr,
			doctype,
			docname,
			meta_description,
			documentation_url,
			help_links: collectHelpLinks(routeStr),
		};
	}

	function currentUser() {
		return (
			(frappe.session && frappe.session.user) ||
			(frappe.boot && frappe.boot.user && frappe.boot.user.name) ||
			null
		);
	}

	function onDesk() {
		const path = window.location.pathname || "";
		if (path.indexOf("/login") === 0 || path === "/login") return false;
		// Frappe v16 Desk is /desk; older builds used /app
		if (path.indexOf("/app") === 0 || path === "/app" || path.indexOf("/desk") === 0) {
			return true;
		}
		// Logged-in Desk shell sometimes keeps a short path during boot
		if (document.body && document.body.getAttribute("data-path") === "login") return false;
		if (frappe.boot && frappe.session && frappe.session.user && frappe.session.user !== "Guest") {
			return Boolean(document.getElementById("body") || document.querySelector(".frappe-container, #page-container, .desk-sidebar"));
		}
		return false;
	}

	class HelpFloat {
		constructor() {
			this.open = false;
			this.sending = false;
			this.conversation = null;
			this.history = [];
			this.context = {};
			this.mount();
			this.bindRouter();
			this.refreshContext();
		}

		mount() {
			this.root = el("div", { "data-aimatic-help-root": "1" });
			this.launcher = el(
				"button",
				{
					className: "aimatic-help-launcher",
					type: "button",
					"aria-label": "Help",
					"aria-expanded": "false",
					"aria-controls": "aimatic-help-panel",
					title: "Help",
					style: LAUNCHER_STYLE,
				},
				ICON
			);
			this.panel = el("div", {
				className: "aimatic-help-panel",
				id: "aimatic-help-panel",
				role: "dialog",
				"aria-label": "Help",
			});

			const head = el("div", { className: "aimatic-help-head" });
			const headText = el("div");
			headText.appendChild(el("div", { className: "aimatic-help-head-title", text: "Help" }));
			this.contextEl = el("div", { className: "aimatic-help-context" });
			headText.appendChild(this.contextEl);
			const closeBtn = el("button", {
				className: "aimatic-help-close",
				type: "button",
				"aria-label": "Close",
				text: "×",
			});
			head.appendChild(headText);
			head.appendChild(closeBtn);

			this.body = el("div", { className: "aimatic-help-body", "aria-live": "polite" });
			this.appendBot(
				"Hi! Ask how to use the screen you are on — Item, Price List, Stock, Accounts, and more."
			);

			this.suggestions = el("div", { className: "aimatic-help-suggestions" });

			const foot = el("div", { className: "aimatic-help-foot" });
			this.input = el("textarea", {
				className: "aimatic-help-input",
				rows: "1",
				placeholder: "Ask how to do something…",
			});
			this.sendBtn = el("button", {
				className: "aimatic-help-send",
				type: "button",
				text: "Send",
			});
			foot.appendChild(this.input);
			foot.appendChild(this.sendBtn);

			this.panel.appendChild(head);
			this.panel.appendChild(this.body);
			this.panel.appendChild(this.suggestions);
			this.panel.appendChild(foot);
			this.root.appendChild(this.launcher);
			this.root.appendChild(this.panel);
			document.body.appendChild(this.root);

			this.launcher.addEventListener("click", () => this.toggle(true));
			closeBtn.addEventListener("click", () => this.toggle(false));
			this.sendBtn.addEventListener("click", () => this.send());
			this.input.addEventListener("keydown", (e) => {
				if (e.key === "Enter" && !e.shiftKey) {
					e.preventDefault();
					this.send();
				}
			});
			this.body.addEventListener("click", (e) => {
				const a = e.target.closest("a[data-ah-route]");
				if (!a) return;
				const href = a.getAttribute("data-ah-route") || a.getAttribute("href");
				if (href && href.startsWith("/app/") && frappe.set_route) {
					e.preventDefault();
					const path = href.replace(/^\/app\//, "");
					frappe.set_route(path.split("/"));
				}
			});
		}

		bindRouter() {
			if (frappe.router && frappe.router.on) {
				frappe.router.on("change", () => this.refreshContext());
			}
			if (window.jQuery) {
				jQuery(document).on("page-change", () => this.refreshContext());
			}
		}

		refreshContext() {
			this.context = readDeskContext();
			const label = this.context.doctype || this.context.module || "ERPNext";
			this.contextEl.textContent = "";
			this.contextEl.appendChild(document.createTextNode("Helping with: "));
			const strong = document.createElement("strong");
			strong.textContent = label;
			this.contextEl.appendChild(strong);
			if (this.open) this.loadStarters();
		}

		toggle(force) {
			this.open = typeof force === "boolean" ? force : !this.open;
			this.panel.classList.toggle("aimatic-help-open", this.open);
			this.launcher.setAttribute("aria-expanded", this.open ? "true" : "false");
			if (this.open) {
				this.refreshContext();
				this.loadStarters();
				this.input.focus();
			}
		}

		loadStarters() {
			frappe.call({
				method: "aimatic.help.api.list_starters",
				args: { context: JSON.stringify(this.context) },
				callback: (r) => {
					const starters = (r.message && r.message.starters) || [];
					this.suggestions.innerHTML = "";
					starters.forEach((q) => {
						const chip = el("button", {
							className: "aimatic-help-chip",
							type: "button",
							text: q,
						});
						chip.addEventListener("click", () => this.send(q));
						this.suggestions.appendChild(chip);
					});
				},
			});
		}

		appendBot(text) {
			const node = el("div", { className: "aimatic-help-msg aimatic-help-msg-bot" });
			node.innerHTML = linkify(text);
			this.body.appendChild(node);
			this.body.scrollTop = this.body.scrollHeight;
		}

		appendUser(text) {
			const node = el("div", { className: "aimatic-help-msg aimatic-help-msg-user", text });
			this.body.appendChild(node);
			this.body.scrollTop = this.body.scrollHeight;
		}

		ensureConversation() {
			if (this.conversation) {
				return Promise.resolve(this.conversation);
			}
			return new Promise((resolve, reject) => {
				frappe.call({
					method: "aimatic.help.api.start_conversation",
					args: { context: JSON.stringify(this.context) },
					callback: (r) => {
						if (r.message && r.message.name) {
							this.conversation = r.message.name;
							resolve(this.conversation);
						} else {
							reject(new Error("Could not start help conversation"));
						}
					},
					error: () => reject(new Error("Could not start help conversation")),
				});
			});
		}

		send(preset) {
			const text = (preset || this.input.value || "").trim();
			if (!text || this.sending) return;
			this.sending = true;
			this.sendBtn.disabled = true;
			this.sendBtn.textContent = "…";
			this.input.value = "";
			this.appendUser(text);
			this.suggestions.innerHTML = "";

			this.ensureConversation()
				.then((conversation) => {
					frappe.call({
						method: "aimatic.help.api.ask",
						args: {
							message: text,
							history: JSON.stringify(this.history),
							conversation,
							context: JSON.stringify(this.context),
						},
						callback: (r) => {
							const reply = (r.message && r.message.reply) || "No reply.";
							this.history.push({ role: "user", content: text });
							this.history.push({ role: "assistant", content: reply });
							if (this.history.length > 24) {
								this.history = this.history.slice(-24);
							}
							this.appendBot(reply);
						},
						error: () => {
							this.appendBot("Help is temporarily unavailable. Try again shortly.");
						},
						always: () => {
							this.sending = false;
							this.sendBtn.disabled = false;
							this.sendBtn.textContent = "Send";
						},
					});
				})
				.catch(() => {
					this.appendBot("Could not start a help session. Please refresh Desk.");
					this.sending = false;
					this.sendBtn.disabled = false;
					this.sendBtn.textContent = "Send";
				});
		}
	}

	function tryMount() {
		try {
			if (aimatic.help_float.instance) return true;
			if (document.querySelector("[data-aimatic-help-root]")) return true;
			const user = currentUser();
			if (!user || user === "Guest") return false;
			if (!onDesk()) return false;
			if (!document.body) return false;
			aimatic.help_float.instance = new HelpFloat();
			window.__aimaticHelpFloatMounted = true;
			return true;
		} catch (e) {
			console.error("Help float failed to mount", e);
			return false;
		}
	}

	function scheduleMount() {
		if (tryMount()) return;
		let tries = 0;
		const timer = setInterval(function () {
			tries += 1;
			if (tryMount() || tries >= 120) {
				clearInterval(timer);
			}
		}, 500);
	}

	if (window.jQuery) {
		jQuery(document).on("app_ready", tryMount);
		jQuery(document).on("page-change", tryMount);
	} else {
		document.addEventListener("app_ready", tryMount);
	}
	if (frappe.router && frappe.router.on) {
		frappe.router.on("change", tryMount);
	}
	window.addEventListener("popstate", tryMount);
	scheduleMount();
})();
