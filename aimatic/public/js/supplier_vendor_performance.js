
frappe.ui.form.on('Supplier', {
    refresh(frm) {
        if (frm.is_new()) return;

        frm.add_custom_button(__('Vendor Performance'), () => {
            frappe.route_options = {
                supplier: frm.doc.name,
                company: frappe.defaults.get_user_default('Company') || undefined,
            };
            frappe.set_route('vendor-performance-console');
        });
    },
});
