frappe.ui.form.on("Retail Discord Delivery", {
    refresh(frm) {
        if (!["Uncertain", "Failed", "Skipped"].includes(frm.doc.status)) return;
        frm.add_custom_button(__("Reconcile delivery"), () => frappe.prompt([
            {fieldname:"reason",label:__("Inspection result"),fieldtype:"Small Text",reqd:1},
            {fieldname:"message_id",label:__("Existing Discord message ID, if received"),fieldtype:"Data"},
            {fieldname:"confirmed_not_received",label:__("I checked the channel; the message was not received"),fieldtype:"Check"}
        ], async values => {
            await frappe.xcall("aimatic.retail_operations.api.reconcile_delivery", {name:frm.doc.name,...values});
            frm.reload_doc();
        }));
    }
});
