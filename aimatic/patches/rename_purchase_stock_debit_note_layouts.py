import frappe

# "AIM Stock Debit Note - Purchase Invoice/Receipt" were needlessly long names
# for what are just this business's own custom purchase print layouts, now
# shipped under the shorter names below (their own module-doc files already
# exist as "Purchase Invoice/Receipt Custom Layout" - see
# aimatic/aimatic/print_format/). [post_model_sync] patches run *after* the
# module-doc sync step, so by the time this patch runs, the new-named record
# has already been created fresh from its file - this just deletes the
# now-orphaned old-named record left behind by the earlier rename.
OLD_NAMES = (
    "AIM Stock Debit Note - Purchase Invoice",
    "AIM Stock Debit Note - Purchase Receipt",
)


def execute():
    for old_name in OLD_NAMES:
        if frappe.db.exists("Print Format", old_name):
            frappe.delete_doc("Print Format", old_name, force=True, ignore_permissions=True)
    frappe.db.commit()
