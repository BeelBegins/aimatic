/**
 * Open Selling Price Update console from a submitted Purchase Receipt or
 * Material Transfer Stock Entry (Stock Transfer Note).
 *
 * Keeps the existing one-click PR apply buttons untouched — these only route
 * to the review grid.
 */
(function () {
	"use strict";

	if (typeof frappe === "undefined" || frappe.__aimatic_spu_source_links) {
		return;
	}
	frappe.__aimatic_spu_source_links = true;

	const CONSOLE_ROUTE = "selling-price-update-console";

	function open_console(frm, mode) {
		const warehouse =
			frm.doc.set_warehouse ||
			((frm.doc.items || []).map((row) => row.t_warehouse).filter(Boolean)[0] || "");
		try {
			localStorage.removeItem("_page:selling-price-update-console");
		} catch (e) {
			// ignore
		}
		frappe.route_options = {
			mode: mode,
			source_doctype: frm.doctype,
			source_name: frm.doc.name,
			// Stock Entry.branch is the sender; the console resolves the receiving branch.
			branch: frm.doctype === "Stock Entry" ? "" : frm.doc.branch,
			vendor: frm.doc.supplier || "",
			warehouse: warehouse,
			posting_date: frm.doc.posting_date,
		};
		frappe.set_route(CONSOLE_ROUTE);
	}

	function add_pr_buttons(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.is_return) {
			return;
		}
		frm.add_custom_button(__("Review Store Prices"), () => open_console(frm, "Store Selling"), __("Selling Price Update"));
		frm.add_custom_button(__("Review Foodpanda Prices"), () => open_console(frm, "Foodpanda"), __("Selling Price Update"));
	}

	function add_stn_buttons(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.purpose !== "Material Transfer") {
			return;
		}
		frm.add_custom_button(__("Review Store Prices"), () => open_console(frm, "Store Selling"), __("Selling Price Update"));
		frm.add_custom_button(__("Review Foodpanda Prices"), () => open_console(frm, "Foodpanda"), __("Selling Price Update"));
	}

	frappe.ui.form.on("Purchase Receipt", {
		refresh(frm) {
			add_pr_buttons(frm);
		},
	});

	frappe.ui.form.on("Stock Entry", {
		refresh(frm) {
			add_stn_buttons(frm);
		},
	});
})();
