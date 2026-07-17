from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, today

from aimatic.ai.nemotron_client import NemotronError, get_chat_completion, get_completion
from aimatic.ai.tools import TOOL_DISPATCH, TOOL_SPECS

_ALLOWED_ROLES = {"System Manager", "Sales Manager", "Accounts Manager", "POS Supervisor"}
_MAX_TOOL_ITERATIONS = 5
# Caps how much prior conversation a single call resends to the model - independent
# of how much is persisted server-side (see AI Assistant Message / get_recent_history
# below), this just bounds one request's size/cost regardless of how long a
# conversation has gotten.
_MAX_HISTORY_TURNS = 20
# How many persisted turns get restored into the UI when the page is (re)opened -
# server-side storage itself is governed by the 30-day purge in ai/tasks.py, not by
# this constant.
_HISTORY_RESTORE_LIMIT = 40


@frappe.whitelist()
def ping():
    """System Manager only. Sends a trivial prompt to confirm the configured
    OpenRouter key/model actually works end-to-end, without exposing this to
    unauthenticated or unprivileged users (it spends real API credit)."""
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    try:
        reply = get_completion("Reply with exactly one word: pong")
    except NemotronError as e:
        frappe.throw(str(e))

    return {"reply": reply}


def _check_role():
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    if not (set(frappe.get_roles()) & _ALLOWED_ROLES):
        frappe.throw(_("Not permitted to use the AI assistant."), frappe.PermissionError)


def _build_system_prompt() -> str:
    company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_default("company") or "the company"
    return (
        f"You are an analytics assistant for {company}, a retail business running on ERPNext. "
        f"Today's date is {today()}. "
        "Answer questions about sales, purchases, vendors, and inventory using ONLY the provided "
        "tools - never guess or estimate numbers yourself. All monetary figures returned by tools "
        "are in the company's default currency. When a question doesn't specify a date range, "
        "assume today; when it says things like \"this month\" or \"last week\", resolve them into "
        "concrete dates yourself using today's date above before calling a tool. Keep answers "
        "concise and concrete - lead with the number, then brief context."
    )


def _parse_history(history: str | None) -> list[dict]:
    """Only ever accepts plain {role: user|assistant, content} text turns - never
    raw tool-call internals from a prior request - so a client can't smuggle
    fabricated tool results into a new conversation."""
    if not history:
        return []
    try:
        parsed = json.loads(history)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    turns = []
    for turn in parsed[-_MAX_HISTORY_TURNS:]:
        if isinstance(turn, dict) and turn.get("role") in ("user", "assistant") and turn.get("content"):
            turns.append({"role": turn["role"], "content": str(turn["content"])})
    return turns


@frappe.whitelist()
def ask(message: str, history: str | None = None):
    """Role-gated conversational entrypoint over the read-only tools in tools.py.
    `history` is a JSON-encoded list of prior {role, content} user/assistant text
    turns that the browser tab resends each call, used only to build this one
    request's tool-calling scratchpad (rebuilt fresh every call, never stored
    between calls). Separately, on a successful reply, both the new question and
    the answer are persisted to AI Assistant Message (see _log_turn) - that's the
    durable record get_recent_history reads from and ai/tasks.py's daily job purges
    after 30 days; it is not what `history` here is sourced from."""
    _check_role()
    if not message or not message.strip():
        frappe.throw(_("Message is required."))

    messages = [{"role": "system", "content": _build_system_prompt()}]
    messages.extend(_parse_history(history))
    messages.append({"role": "user", "content": message})

    for _iteration in range(_MAX_TOOL_ITERATIONS):
        try:
            assistant_message = get_chat_completion(messages, tools=TOOL_SPECS, tool_choice="auto")
        except NemotronError as e:
            frappe.throw(str(e))

        messages.append(assistant_message)
        tool_calls = assistant_message.get("tool_calls")
        if not tool_calls:
            reply = assistant_message.get("content") or ""
            _log_turn("user", message)
            _log_turn("assistant", reply)
            return {"reply": reply}

        for call in tool_calls:
            result = _dispatch_tool_call(call)
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, default=str)})

    frappe.throw(_("Could not produce an answer after several tool calls. Try rephrasing your question."))


def _log_turn(role: str, content: str):
    """Best-effort audit write - a logging failure must never break the chat
    response the user is actively waiting on, so errors here are swallowed (not
    raised) after being recorded via frappe's own error log."""
    try:
        frappe.get_doc({"doctype": "AI Assistant Message", "user": frappe.session.user, "role": role, "content": content}).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(title="AI Assistant Message log failed")


@frappe.whitelist()
def get_recent_history(limit: int = _HISTORY_RESTORE_LIMIT):
    """Restores the current user's own recent conversation (only their own - never
    another user's, regardless of role) when the chat page is (re)opened. Reads via
    ignore_permissions since AI Assistant Message's own doctype permissions are
    System Manager-only (for admin review of the full log); this is the API-layer
    enforcement that lets any allowed role see their own history, matching how the
    rest of this app gates whitelisted calls rather than relying on doctype perms."""
    _check_role()
    limit = max(1, min(cint(limit or _HISTORY_RESTORE_LIMIT), 100))
    rows = frappe.get_all(
        "AI Assistant Message",
        filters={"user": frappe.session.user},
        fields=["role", "content"],
        order_by="creation desc",
        limit=limit,
        ignore_permissions=True,
    )
    rows.reverse()
    return {"history": [{"role": r.role, "content": r.content} for r in rows]}


def _dispatch_tool_call(call: dict) -> dict:
    function = call.get("function") or {}
    name = function.get("name")
    handler = TOOL_DISPATCH.get(name)
    if not handler:
        return {"error": f"Unknown tool: {name}"}

    raw_args = function.get("arguments") or "{}"
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    except (TypeError, ValueError):
        return {"error": f"Could not parse arguments for {name}: {raw_args}"}

    try:
        return handler(**args)
    except frappe.PermissionError:
        raise
    except Exception as e:
        return {"error": f"{name} failed: {e}"}
