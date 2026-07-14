import frappe


def on_pos_device_update(doc, method):
    """Audit trail only — actual enforcement of a disabled device is done by
    require_active_device(hardware_id), called on every POS endpoint that
    matters (see offline_pos.api). No OAuth Bearer Token bookkeeping happens
    here: OAuth Bearer Token's primary key is the plaintext access token
    itself (autoname: field:access_token), so there is no non-secret way to
    reference "this device's token" from POS Device — relying on
    require_active_device to reject the device's next call is both simpler
    and avoids storing a live bearer credential a second time."""
    if not doc.has_value_changed("enabled"):
        return

    frappe.get_doc({
        "doctype": "POS Device Audit Log",
        "user": frappe.session.user if frappe.session.user != "Guest" else None,
        "hardware_id": doc.hardware_id,
        "pos_profile": doc.pos_profile,
        "status": "device_enabled" if doc.enabled else "device_disabled",
    }).insert(ignore_permissions=True)
