"""
One-off backfill: submit the POS Invoices on Siezal Supermarket-S1 from the
2026-08-13 evening FBR incident/degradation window that are still Not Sent
or Failed. Not registered as a patch - run manually via:

    bench --site szl execute aimatic.ops_backfill_fbr_gap_20260813.test_one
    bench --site szl execute aimatic.ops_backfill_fbr_gap_20260813.run

Safe to re-run: skips anything no longer Not Sent/Failed at read time.
LOWER_BOUND restricts to 2026-08-13 onward so this never touches the
unrelated ACC-PSINV-2026-01863 (2026-08-09, a pre-existing unrelated
failure).
"""

import time

import frappe

from aimatic.fbr_pos.api import submit_pos_invoice_to_fbr

LOWER_BOUND = "2026-08-13 00:00:00"  # exclude older, unrelated failures


def test_one(name=None):
    """Resubmit a single invoice and print the result, without touching the rest."""
    settings_enabled = frappe.db.get_value(
        "FBR Integration Settings",
        "Siezal Supermarket-S1 - Ghouri Town VIP",
        "enabled",
    )
    if not settings_enabled:
        print("ABORT: FBR Integration Settings is not enabled right now.")
        return

    if not name:
        name = frappe.db.get_value(
            "POS Invoice",
            {
                "company": "Siezal Supermarket",
                "custom_fbr_status": ["in", ["Not Sent", "Failed"]],
                "docstatus": 1,
                "is_return": 0,
                "creation": [">=", LOWER_BOUND],
            },
            "name",
            order_by="creation asc",
        )

    if not name:
        print("No candidate invoice found.")
        return

    doc = frappe.get_doc("POS Invoice", name)
    result = submit_pos_invoice_to_fbr(doc)

    fbr_fields = {f: doc.get(f) for f in doc.as_dict() if f.startswith("custom_fbr_")}
    frappe.db.set_value("POS Invoice", name, fbr_fields, update_modified=False)
    frappe.db.commit()

    print(f"{name}: {doc.custom_fbr_status}")
    print(f"HTTP status: {doc.custom_fbr_http_status}")
    print(f"Error: {doc.custom_fbr_error}")
    return result


def run():
    settings_enabled = frappe.db.get_value(
        "FBR Integration Settings",
        "Siezal Supermarket-S1 - Ghouri Town VIP",
        "enabled",
    )
    if not settings_enabled:
        print("ABORT: FBR Integration Settings is not enabled right now.")
        return

    names = frappe.get_all(
        "POS Invoice",
        filters={
            "company": "Siezal Supermarket",
            "custom_fbr_status": ["in", ["Not Sent", "Failed"]],
            "docstatus": 1,
            "is_return": 0,
            "creation": [">=", LOWER_BOUND],
        },
        pluck="name",
        order_by="creation asc",
    )

    print(f"Found {len(names)} invoices to backfill")

    accepted, failed, errored, skipped = 0, 0, 0, 0

    for name in names:
        doc = frappe.get_doc("POS Invoice", name)

        if doc.custom_fbr_status not in ("Not Sent", "Failed"):
            skipped += 1
            continue

        try:
            submit_pos_invoice_to_fbr(doc)
        except Exception as e:
            frappe.db.rollback()
            frappe.log_error(
                title=f"FBR gap backfill hard failure: {name}",
                message=frappe.get_traceback(),
            )
            print(f"{name}: ERROR {e}")
            errored += 1
            continue

        fbr_fields = {
            f: doc.get(f) for f in doc.as_dict() if f.startswith("custom_fbr_")
        }
        frappe.db.set_value("POS Invoice", name, fbr_fields, update_modified=False)
        frappe.db.commit()

        status = doc.custom_fbr_status
        print(f"{name}: {status}")

        if status == "Accepted":
            accepted += 1
        else:
            failed += 1

        time.sleep(1.5)

    print(
        f"Done. accepted={accepted} failed={failed} errored={errored} "
        f"skipped={skipped} total={len(names)}"
    )
    return {
        "accepted": accepted,
        "failed": failed,
        "errored": errored,
        "skipped": skipped,
        "total": len(names),
    }
