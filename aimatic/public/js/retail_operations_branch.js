frappe.ui.form.on("Branch", {
    refresh(frm) {
        if (frm.is_new() || !frappe.user.has_role("System Manager")) return;
        frm.add_custom_button(__("Copy Discord channels"), async () => {
            if (frm.is_dirty()) return frappe.msgprint(__("Save this branch's webhook settings first."));
            const {branches} = await frappe.xcall("aimatic.retail_operations.api.context");
            const choices = branches.filter(b => b.name !== frm.doc.name).map(b => ({value:b.name,label:b.name}));
            const dialog = new frappe.ui.Dialog({title:__("Share this branch's Discord destinations"),fields:[
                {fieldname:"targets",label:__("Branches"),fieldtype:"MultiSelectList",options:choices,get_data:txt=>choices.filter(b=>b.label.toLowerCase().includes((txt || "").toLowerCase())),reqd:1},
                {fieldname:"replace_existing",label:__("Replace destinations already configured on selected branches"),fieldtype:"Check",default:0}
            ],primary_action_label:__("Copy configured channels"),primary_action:async values=>{
                const result=await frappe.xcall("aimatic.retail_operations.api.copy_branch_channels",{source_branch:frm.doc.name,target_branches:values.targets,replace_existing:values.replace_existing});
                dialog.hide();frappe.msgprint(__("Copied {0} configured destinations. Webhook values were kept on the server.",[result.reduce((sum,b)=>sum+b.channels_copied,0)]));
            }});dialog.show();
        },__("Discord"));
    }
});
