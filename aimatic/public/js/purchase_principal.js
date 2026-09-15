frappe.ui.form.on("Purchase Order", {
	setup(frm) {
		aimatic_setup_principal_query(frm);
	},
	supplier(frm) {
		aimatic_clear_principal_on_supplier_change(frm);
	},
	items_on_form_rendered(frm) {
		aimatic_maybe_guess_principal(frm);
	},
});

frappe.ui.form.on("Purchase Receipt", {
	setup(frm) {
		aimatic_setup_principal_query(frm);
	},
	supplier(frm) {
		aimatic_clear_principal_on_supplier_change(frm);
	},
	items_on_form_rendered(frm) {
		aimatic_maybe_guess_principal(frm);
	},
});

frappe.ui.form.on("Purchase Invoice", {
	setup(frm) {
		aimatic_setup_principal_query(frm);
	},
	supplier(frm) {
		aimatic_clear_principal_on_supplier_change(frm);
	},
	items_on_form_rendered(frm) {
		aimatic_maybe_guess_principal(frm);
	},
});

frappe.ui.form.on("Purchase Order Item", {
	item_code(frm) {
		aimatic_maybe_guess_principal(frm);
	},
	items_remove(frm) {
		aimatic_maybe_guess_principal(frm);
	},
});

frappe.ui.form.on("Purchase Receipt Item", {
	item_code(frm) {
		aimatic_maybe_guess_principal(frm);
	},
	items_remove(frm) {
		aimatic_maybe_guess_principal(frm);
	},
});

frappe.ui.form.on("Purchase Invoice Item", {
	item_code(frm) {
		aimatic_maybe_guess_principal(frm);
	},
	items_remove(frm) {
		aimatic_maybe_guess_principal(frm);
	},
});

function aimatic_setup_principal_query(frm) {
	frm.set_query("custom_principal", () => {
		const supplier = frm.doc.supplier;
		if (!supplier) {
			return { filters: { name: ["in", []] } };
		}
		return {
			query: "aimatic.purchase_principal.get_principal_query",
			filters: { supplier },
		};
	});
}

function aimatic_clear_principal_on_supplier_change(frm) {
	if (frm.doc.custom_principal) {
		frm.set_value("custom_principal", "");
	}
}

function aimatic_maybe_guess_principal(frm) {
	if (frm.doc.docstatus !== 0 || !frm.doc.supplier || !frm.doc.items || !frm.doc.items.length) {
		return;
	}
	if (frm.doc.custom_principal) {
		return;
	}
	if (frm.__aimatic_guessing_principal) {
		return;
	}
	frm.__aimatic_guessing_principal = true;
	frappe
		.xcall("aimatic.purchase_principal.guess_principal_for_purchase_doc", {
			doctype: frm.doctype,
			doc: frm.doc,
		})
		.then((result) => {
			const principal = result && result.principal;
			if (principal && !frm.doc.custom_principal) {
				frm.set_value("custom_principal", principal);
			}
		})
		.finally(() => {
			frm.__aimatic_guessing_principal = false;
		});
}
