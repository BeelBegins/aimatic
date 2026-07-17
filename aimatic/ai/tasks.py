"""Scheduled maintenance for the ai/ module. Wired into hooks.py's scheduler_events."""

from __future__ import annotations

import frappe
from frappe.utils import add_days, now_datetime

RETENTION_DAYS = 30


def purge_old_ai_messages():
    """Daily job: deletes AI Assistant Message rows older than RETENTION_DAYS so the
    audit log doesn't grow unbounded. This is the only place these records are ever
    removed - nothing in api.py deletes them (Clear Conversation in the UI only
    clears that tab's own view, not the server-side record)."""
    cutoff = add_days(now_datetime(), -RETENTION_DAYS)
    frappe.db.delete("AI Assistant Message", {"creation": ["<", cutoff]})
