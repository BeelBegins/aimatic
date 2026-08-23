frappe.provide("aimatic.relationship_manager");

aimatic.relationship_manager.SUPPORTED = [
	"Material Request",
	"Purchase Order",
	"Purchase Receipt",
	"Purchase Invoice",
	"Quotation",
	"Sales Order",
	"Delivery Note",
	"Sales Invoice",
	"Payment Entry",
	"Landed Cost Voucher",
];

aimatic.relationship_manager.pill_tone = function (status) {
	const s = status || "";
	if (s.includes("Return")) return "orange";
	if (s === "Submitted") return "green";
	if (s === "Cancelled") return "red";
	if (s === "Draft") return "gray";
	return "gray";
};

aimatic.relationship_manager.mount = function (frm) {
	if (!frm || frm.is_new() || !frm.doc || !frm.doc.name) {
		return;
	}
	if (!aimatic.relationship_manager.SUPPORTED.includes(frm.doctype)) {
		return;
	}

	frappe.call({
		method: "aimatic.relationship_manager.api.get_relationship_tree",
		args: { doctype: frm.doctype, name: frm.doc.name },
		callback: (r) => {
			if (!r.message) {
				return;
			}
			aimatic.relationship_manager.render(frm, r.message);
		},
	});
};

aimatic.relationship_manager.render = function (frm, data) {
	frm.dashboard.parent
		.find(".aimatic-relationship-manager")
		.closest(".form-dashboard-section")
		.remove();

	const tree = data.tree || [];
	if (!tree.length) {
		return;
	}

	const lanes = aimatic.relationship_manager.flatten_lanes(tree);
	const body = $(`
		<div class="aimatic-relationship-manager">
			<div class="aimatic-rm-toolbar">
				<span class="aimatic-rm-count">
					${__("{0} documents in flow", [data.count || lanes.length])}
				</span>
			</div>
			<div class="aimatic-rm-flow" role="list"></div>
		</div>
	`);

	const $flow = body.find(".aimatic-rm-flow");
	lanes.forEach((node, idx) => {
		if (idx > 0) {
			$flow.append(`
				<div class="aimatic-rm-connector" aria-hidden="true">
					<span class="aimatic-rm-connector-arrow">→</span>
				</div>
			`);
		}
		$flow.append(aimatic.relationship_manager.card_html(node));
	});

	if (data.truncated) {
		body.append(
			`<div class="aimatic-rm-note">${__(
				"Flow truncated — too many linked documents."
			)}</div>`
		);
	}

	frm.dashboard.add_section(
		body,
		__("Relationship Manager"),
		"custom aimatic-relationship-manager-section"
	);
	frm.dashboard.show();
};

/** Prefer the path that includes the open document, then remaining branches. */
aimatic.relationship_manager.flatten_lanes = function (tree) {
	const lanes = [];

	const walk = (node) => {
		lanes.push(node);
		const children = node.children || [];
		if (!children.length) {
			return;
		}
		const current_idx = children.findIndex((c) =>
			aimatic.relationship_manager.contains_current(c)
		);
		const primary = current_idx >= 0 ? current_idx : 0;
		walk(children[primary]);
		children.forEach((child, i) => {
			if (i !== primary) {
				walk(child);
			}
		});
	};

	(tree || []).forEach(walk);
	return lanes;
};

aimatic.relationship_manager.contains_current = function (node) {
	if (node.current) {
		return true;
	}
	return (node.children || []).some((c) =>
		aimatic.relationship_manager.contains_current(c)
	);
};

aimatic.relationship_manager.card_html = function (node) {
	const current = node.current ? " is-current" : "";
	const restricted = node.restricted ? " is-restricted" : "";
	const tone = aimatic.relationship_manager.pill_tone(node.status);
	const status = frappe.utils.escape_html(node.status || "");
	const party = node.party ? frappe.utils.escape_html(node.party) : "";
	const date = node.date
		? frappe.utils.escape_html(frappe.datetime.str_to_user(node.date))
		: "";
	const amount =
		node.amount != null && node.amount !== ""
			? format_currency(node.amount)
			: "";

	let name_html = `<span class="aimatic-rm-card-name">${frappe.utils.escape_html(
		node.name
	)}</span>`;
	if (!node.restricted) {
		const href = `/app/${frappe.router.slug(node.doctype)}/${encodeURIComponent(
			node.name
		)}`;
		name_html = `<a class="aimatic-rm-card-name" href="${href}">${frappe.utils.escape_html(
			node.name
		)}</a>`;
	}

	const you = node.current
		? `<span class="aimatic-rm-you">${__("This document")}</span>`
		: "";

	return $(`
		<article class="aimatic-rm-card${current}${restricted}" role="listitem">
			<div class="aimatic-rm-card-top">
				<span class="aimatic-rm-doc-type">${frappe.utils.escape_html(
					__(node.doctype)
				)}</span>
				<span class="aimatic-rm-pill aimatic-rm-pill--${tone}">${status}</span>
			</div>
			${name_html}
			<div class="aimatic-rm-card-meta">
				${date ? `<span>${date}</span>` : "<span></span>"}
				${amount ? `<span class="aimatic-rm-amount">${amount}</span>` : ""}
			</div>
			${party ? `<div class="aimatic-rm-party" title="${party}">${party}</div>` : ""}
			${you}
		</article>
	`);
};

aimatic.relationship_manager.ensure_styles = function () {
	if (document.getElementById("aimatic-relationship-manager-style")) {
		return;
	}
	const style = document.createElement("style");
	style.id = "aimatic-relationship-manager-style";
	style.textContent = `
		.aimatic-relationship-manager {
			padding: 2px 0 8px;
		}
		.aimatic-rm-toolbar {
			display: flex;
			justify-content: flex-end;
			margin-bottom: 8px;
		}
		.aimatic-rm-count {
			font-size: 12px;
			color: var(--text-muted);
		}
		.aimatic-rm-flow {
			display: flex;
			flex-wrap: nowrap;
			align-items: stretch;
			overflow-x: auto;
			padding: 2px 2px 10px;
			scrollbar-width: thin;
		}
		.aimatic-rm-connector {
			display: flex;
			align-items: center;
			justify-content: center;
			flex: 0 0 28px;
			color: var(--text-muted);
			opacity: 0.55;
			font-size: 15px;
			font-weight: 600;
			user-select: none;
		}
		.aimatic-rm-card {
			flex: 0 0 220px;
			width: 220px;
			box-sizing: border-box;
			background: var(--card-bg, var(--fg-color, #fff));
			border: 1px solid var(--border-color);
			border-radius: 8px;
			padding: 10px 12px 12px;
			box-shadow: var(--shadow-xs, 0 1px 2px rgba(0,0,0,.04));
			display: flex;
			flex-direction: column;
			gap: 6px;
			min-height: 118px;
			transition: border-color .12s ease, box-shadow .12s ease;
		}
		.aimatic-rm-card:hover {
			border-color: var(--text-muted);
			box-shadow: var(--shadow-sm, 0 2px 6px rgba(0,0,0,.06));
		}
		.aimatic-rm-card.is-current {
			border-color: var(--text-color);
			box-shadow: inset 0 0 0 1px var(--text-color);
			background: var(--control-bg, #f8f8f8);
		}
		.aimatic-rm-card.is-restricted {
			opacity: 0.72;
		}
		.aimatic-rm-card-top {
			display: flex;
			align-items: center;
			justify-content: space-between;
			gap: 8px;
		}
		.aimatic-rm-doc-type {
			font-size: 10px;
			font-weight: 700;
			letter-spacing: 0.04em;
			text-transform: uppercase;
			color: var(--text-muted);
			white-space: nowrap;
			overflow: hidden;
			text-overflow: ellipsis;
		}
		.aimatic-rm-pill {
			flex: 0 0 auto;
			font-size: 10px;
			font-weight: 700;
			line-height: 1;
			padding: 4px 7px;
			border-radius: 999px;
			border: 1px solid transparent;
			white-space: nowrap;
		}
		.aimatic-rm-pill--green {
			color: #276749;
			background: #c6f6d5;
			border-color: #9ae6b4;
		}
		.aimatic-rm-pill--gray {
			color: var(--text-muted);
			background: var(--control-bg, #f3f3f3);
			border-color: var(--border-color);
		}
		.aimatic-rm-pill--red {
			color: #9b2c2c;
			background: #fed7d7;
			border-color: #feb2b2;
		}
		.aimatic-rm-pill--orange {
			color: #9c4221;
			background: #feebc8;
			border-color: #fbd38d;
		}
		.aimatic-rm-card-name {
			font-family: var(--font-stack-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
			font-size: 13px;
			font-weight: 600;
			color: var(--text-color);
			text-decoration: none;
			word-break: break-all;
			line-height: 1.35;
		}
		a.aimatic-rm-card-name:hover {
			text-decoration: underline;
		}
		.aimatic-rm-card-meta {
			display: flex;
			justify-content: space-between;
			gap: 8px;
			font-size: 12px;
			color: var(--text-muted);
		}
		.aimatic-rm-amount {
			font-variant-numeric: tabular-nums;
			font-weight: 700;
			color: var(--text-color);
		}
		.aimatic-rm-party {
			font-size: 12px;
			color: var(--text-muted);
			white-space: nowrap;
			overflow: hidden;
			text-overflow: ellipsis;
		}
		.aimatic-rm-you {
			margin-top: auto;
			align-self: flex-start;
			font-size: 10px;
			font-weight: 800;
			letter-spacing: 0.05em;
			text-transform: uppercase;
			color: var(--text-color);
			background: var(--fg-color, #fff);
			border: 1px solid var(--border-color);
			border-radius: 4px;
			padding: 3px 6px;
		}
		.aimatic-rm-note {
			font-size: 12px;
			color: var(--text-muted);
			margin-top: 6px;
		}
		@media (max-width: 768px) {
			.aimatic-rm-flow {
				flex-direction: column;
				overflow-x: visible;
			}
			.aimatic-rm-card {
				width: 100%;
				flex-basis: auto;
			}
			.aimatic-rm-connector {
				flex-basis: 22px;
				transform: rotate(90deg);
			}
		}
	`;
	document.head.appendChild(style);
};

aimatic.relationship_manager.SUPPORTED.forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			aimatic.relationship_manager.ensure_styles();
			aimatic.relationship_manager.mount(frm);
		},
	});
});
