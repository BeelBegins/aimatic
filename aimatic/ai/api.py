from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, today
from frappe.utils.csvutils import build_csv_response
from frappe.utils.xlsxutils import build_xlsx_response

from aimatic.ai.nemotron_client import NemotronError, get_chat_completion, get_completion
from aimatic.ai.tools import TOOL_DISPATCH as _CORE_DISPATCH, TOOL_SPECS as _CORE_SPECS
from aimatic.ai.tools import _resolve_company, _resolve_branch_filter
from aimatic.ai.tools_extended import TOOL_DISPATCH as _EXTENDED_DISPATCH, TOOL_SPECS as _EXTENDED_SPECS
from aimatic.ai.tools_accounts import TOOL_DISPATCH as _ACCOUNTS_DISPATCH, TOOL_SPECS as _ACCOUNTS_SPECS
from aimatic.ai.dynamic_report import DYNAMIC_REPORT_DISPATCH, TOOL_SPECS as _DYNAMIC_REPORT_SPECS
from aimatic.ai.answer_builder import build_response

TOOL_SPECS = _CORE_SPECS + _EXTENDED_SPECS + _ACCOUNTS_SPECS + _DYNAMIC_REPORT_SPECS
TOOL_DISPATCH = {**_CORE_DISPATCH, **_EXTENDED_DISPATCH, **_ACCOUNTS_DISPATCH, **DYNAMIC_REPORT_DISPATCH}

_ALLOWED_ROLES = {"System Manager", "Sales Manager", "Accounts Manager", "POS Supervisor"}
_MAX_TOOL_ITERATIONS = 5
_MAX_HISTORY_TURNS = 20
_HISTORY_RESTORE_LIMIT = 40
_MAX_RECIPIENTS = 20


def _validate_recipients(recipients: str) -> str:
    """Validates the comma-separated recipient list for Scheduled Question /
    AI Alert Rule email delivery. Every recipient must be an existing,
    enabled Frappe User who already holds one of _ALLOWED_ROLES - not just
    any registered account (e.g. a portal Customer/Supplier User) - since
    these emails carry the same confidential financial data (payables,
    margins, vendor performance, sales) the assistant itself is role-gated
    on. Without this, any of the 4 allowed roles could point a Scheduled
    Question/Alert Rule at an arbitrary address and have it silently
    exfiltrated on a recurring schedule with no guardrail at all - real gap
    found in the original create_scheduled_question/create_alert_rule
    (neither validated `recipients` in any way). Rejects the whole save
    (rather than silently dropping bad entries) so a typo doesn't quietly
    produce an empty recipient list."""
    if not recipients or not recipients.strip():
        return ""

    raw_list = [r.strip() for r in recipients.split(",") if r.strip()]
    if not raw_list:
        return ""
    if len(raw_list) > _MAX_RECIPIENTS:
        frappe.throw(_("Maximum {0} recipients allowed.").format(_MAX_RECIPIENTS))

    seen = set()
    deduped = []
    for r in raw_list:
        key = r.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    invalid = []
    for r in deduped:
        user = frappe.db.get_value("User", {"name": r, "enabled": 1}, "name")
        if not user or not (set(frappe.get_roles(user)) & _ALLOWED_ROLES):
            invalid.append(r)

    if invalid:
        frappe.throw(_(
            "These recipients are not valid: {0}. Each recipient must be an "
            "existing, enabled user with access to the AI Assistant."
        ).format(", ".join(invalid)))

    return ", ".join(deduped)


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


def _check_conversation_ownership(conversation: str) -> None:
    """A conversation may only ever be read/written by the user who created it.
    Every conversation-scoped endpoint (including ask() itself when a
    conversation is passed) must call this before touching it - it's the one
    place that stops a user from passing another user's conversation name and
    having their messages logged into someone else's history."""
    owner = frappe.db.get_value("AI Assistant Conversation", conversation, "user")
    if not owner:
        frappe.throw(_("Conversation not found."))
    if owner != frappe.session.user:
        frappe.throw(_("Not permitted."), frappe.PermissionError)


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
        "concise and concrete - lead with the number, then brief context. "
        "Always prefer a purpose-built tool (e.g. get_outstanding_payables_overview, "
        "get_payables_aging, get_sales_overview, get_purchase_overview, get_receivables_overview, "
        "rank_vendors, get_cash_and_bank_balance, get_branch_profit_and_loss) over "
        "run_dynamic_report whenever one matches the question - "
        "check every purpose-built tool's own description first, since one almost always exists "
        "for a specific named business metric (a ranking, a balance, an aging breakdown, a "
        "margin). run_dynamic_report is a last-resort fallback only for questions no specific "
        "tool's description covers, never a first choice, and never chosen just because its own "
        "doctype list happens to include the relevant doctype. If a purpose-built tool returns no "
        "data, trust that result "
        "and answer accordingly rather than retrying the same or a different doctype via "
        "run_dynamic_report - a real business figure (like total outstanding payable) is almost "
        "always better sourced from ledger balances (a purpose-built tool) than from a single "
        "transactional doctype, which can legitimately have zero rows even when the real figure "
        "is nonzero. Every tool you can use is already provided to you through the function-"
        "calling mechanism - never write a tool's name out in your reply while reasoning about "
        "whether one exists or is available; if a question needs data, actually invoke a tool "
        "via a real function call before answering, even if you are unsure which one fits best - "
        "picking the closest match and calling it is always correct, describing tool options in "
        "plain text instead of calling one is never a valid answer."
    )


def _looks_like_raw_tool_json(content: str) -> bool:
    """The free-tier model occasionally malforms a tool call: instead of using
    the real OpenAI tool_calls protocol, it writes what should have been the
    call's arguments as plain assistant `content` text, e.g. literally
    '{"company": "Test Company", "days": 30}' (sometimes duplicated on
    separate lines). Since that message has no tool_calls, ask()'s loop would
    otherwise treat it as the final natural-language answer and ship raw JSON
    to the user - confirmed live, reproducible (intermittently) with phrasing
    like "what should I order tomorrow". Detected structurally: every
    non-blank line must itself be a valid JSON object with no surrounding
    prose - a real answer never satisfies that."""
    if not content or not content.strip():
        return False
    lines = [line.strip() for line in content.strip().splitlines() if line.strip()]
    if not lines:
        return False
    for line in lines:
        if not (line.startswith("{") and line.endswith("}")):
            return False
        try:
            parsed = json.loads(line)
        except (TypeError, ValueError):
            return False
        if not isinstance(parsed, dict):
            return False
    return True


_TOOL_NAME_PATTERN = re.compile(r"\b(?:get|rank|run)_[a-z_]+\b")


def _looks_like_tool_hallucination(content: str, tool_results: dict) -> bool:
    """A second, distinct free-tier failure mode from _looks_like_raw_tool_json:
    instead of malforming a tool call, the model sometimes reasons about tool
    selection in plain prose instead of ever actually calling one. Confirmed
    live on siezal in two different shapes:
      1. Asking "What is our current cash and bank balance?" (a question the
         real get_cash_and_bank_balance tool directly answers) reproducibly
         (2/2 attempts) returned a wall of text listing dozens of variations
         of "get_branch_stock_valuation_summary_comparison" - a tool name
         that has never existed in this codebase.
      2. Asking "What are our overdue payables broken down by aging bucket?"
         (answered directly by the real get_payables_aging tool) returned
         several paragraphs of visible chain-of-thought ("Looking at the
         available tools... I don't see a specific get_payables_aging...
         Let me try using run_dynamic_report...") that correctly *named*
         several real tools (including get_payables_aging itself) while
         still never issuing an actual tool call.
    Both shapes left the answer with zero kpis/charts/tables - what actually
    made the "Cash & Bank Balance"/"Payables Aging" AI Dashboard widgets
    look permanently incomplete rather than just stale.

    Broadened to fire on ANY tool-name-shaped identifier in the content -
    not just ones absent from TOOL_DISPATCH - because case 2 shows the model
    can narrate real tool names while still failing to call one; a genuine
    natural-language answer to a data question has no reason to ever spell
    out a snake_case get_/rank_/run_ identifier verbatim (it talks about
    "cash balance", not "get_cash_and_bank_balance"). Only fires when
    tool_results is still completely empty this turn - a model that has
    already gathered real data from a genuine tool call is extremely
    unlikely to be narrating tool selection in its final answer."""
    if tool_results or not content:
        return False
    return bool(_TOOL_NAME_PATTERN.search(content))


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
def ask(message: str, history: str | None = None, conversation: str | None = None):
    """Role-gated conversational entrypoint over the read-only tools in tools.py.
    `history` is a JSON-encoded list of prior {role, content} user/assistant text
    turns that the browser tab resends each call, used only to build this one
    request's tool-calling scratchpad (rebuilt fresh every call, never stored
    between calls). Separately, on a successful reply, both the new question and
    the answer are persisted to AI Assistant Message (see _log_turn) - that's the
    durable record get_recent_history reads from and ai/tasks.py's daily job purges
    after 30 days; it is not what `history` here is sourced from.
    `conversation` is an optional AI Assistant Conversation name (Phase 2). If
    given, ownership is checked via _check_conversation_ownership before it's
    used for anything - the same protection every other conversation-scoped
    endpoint has, so a client can never tag messages into a conversation it
    doesn't own. If omitted, behavior is identical to before Phase 2: messages
    are logged with no conversation grouping at all."""
    _check_role()
    if not message or not message.strip():
        frappe.throw(_("Message is required."))
    if conversation:
        _check_conversation_ownership(conversation)

    messages = [{"role": "system", "content": _build_system_prompt()}]
    messages.extend(_parse_history(history))
    messages.append({"role": "user", "content": message})

    tool_results = {}

    for _iteration in range(_MAX_TOOL_ITERATIONS):
        try:
            assistant_message = get_chat_completion(messages, tools=TOOL_SPECS, tool_choice="auto")
        except NemotronError as e:
            frappe.throw(str(e))

        messages.append(assistant_message)
        tool_calls = assistant_message.get("tool_calls")
        if not tool_calls:
            reply = assistant_message.get("content") or ""
            if _looks_like_raw_tool_json(reply):
                frappe.log_error(
                    title="AI Assistant: malformed tool-call text corrected",
                    message=f"question={message!r} raw_content={reply!r}",
                )
                messages.append({
                    "role": "user",
                    "content": (
                        "Your last reply was not a valid answer - it looked like raw "
                        "tool-call arguments written as plain text instead of either a "
                        "real tool call or a natural-language answer. Please either call "
                        "the appropriate tool now, or answer the question in plain "
                        "language using data you already have from previous tool results."
                    ),
                })
                continue
            if _looks_like_tool_hallucination(reply, tool_results):
                frappe.log_error(
                    title="AI Assistant: tool hallucination corrected",
                    message=f"question={message!r} raw_content={reply!r}",
                )
                messages.append({
                    "role": "user",
                    "content": (
                        "You did not make a real tool call. Do not describe, list, or guess "
                        "at tool names in plain text - the tools you can use are already "
                        "provided to you through the function-calling mechanism, not named in "
                        "this conversation. Pick the tool that most closely matches the "
                        "question and call it now via a real tool call, even if you are "
                        "unsure it is the exact right one."
                    ),
                })
                continue
            if not tool_results and not reply.strip():
                # A third free-tier failure mode, distinct from the two above: the
                # model returns a completely empty content string with no tool_calls
                # at all - no tool-shaped text to catch, just nothing. Without this
                # guard ask() would treat "" as a valid final answer, and callers like
                # refresh_saved_report() save unconditionally on any non-throwing
                # result - silently downgrading a widget that previously had real
                # data to an empty NO_TOOL_DATA snapshot. Confirmed live on siezal
                # (2026-07-19): a refresh of the "Outstanding Receivables" widget hit
                # this exact path and overwrote a correct PKR 0.00 answer with blank
                # kpis/tables.
                frappe.log_error(
                    title="AI Assistant: empty non-answer corrected",
                    message=f"question={message!r}",
                )
                messages.append({
                    "role": "user",
                    "content": (
                        "You returned an empty reply without calling a tool. Call the "
                        "tool that best matches the question now, or if you already have "
                        "enough information from earlier tool results, answer in plain "
                        "language - do not reply with nothing."
                    ),
                })
                continue
            _log_turn("user", message, conversation)
            _log_turn("assistant", reply, conversation)

            if conversation:
                updates = {"last_activity": now_datetime()}
                if not frappe.db.get_value("AI Assistant Conversation", conversation, "title"):
                    updates["title"] = message.strip()[:60]
                frappe.db.set_value("AI Assistant Conversation", conversation, updates, update_modified=True)

            company = _resolve_company()
            branch_filter = _resolve_branch_filter(company, None)
            if branch_filter is None:
                branch_names = frappe.get_all("Branch", filters={"company": company}, pluck="name")
            else:
                branch_names = branch_filter

            user_role = next((r for r in frappe.get_roles() if r in _ALLOWED_ROLES), "User")

            structured = build_response(message, reply, tool_results, company, branch_names, user_role)
            return structured.to_dict()

        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name")
            result = _dispatch_tool_call(call)
            if name in TOOL_DISPATCH and "error" not in result and name not in tool_results:
                # Keep the FIRST call's result per tool name. A comparison-style
                # question ("this month vs last month") makes the model call the
                # same tool twice with different date ranges; the first call is
                # the period the question actually asked about, later calls are
                # supplementary context for the model's own prose. Overwriting
                # here (last-call-wins) let a zero-result comparison call clobber
                # the real KPI/table/chart data built from the first call.
                tool_results[name] = result
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, default=str)})

    frappe.throw(_("Could not produce an answer after several tool calls. Try rephrasing your question."))


def _log_turn(role: str, content: str, conversation: str | None = None):
    """Best-effort audit write - a logging failure must never break the chat
    response the user is actively waiting on, so errors here are swallowed (not
    raised) after being recorded via frappe's own error log. `conversation` is
    optional (Phase 2) - ownership is the caller's responsibility (ask() checks
    it once up front rather than this function re-checking it per turn)."""
    try:
        doc = frappe.get_doc({"doctype": "AI Assistant Message", "user": frappe.session.user, "role": role, "content": content})
        if conversation:
            doc.conversation = conversation
        doc.insert(ignore_permissions=True)
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


@frappe.whitelist()
def start_conversation():
    """Create a new, empty AI Assistant Conversation for the current user.
    The frontend calls this once when the user picks "New Analysis"; the
    returned name is then passed as `conversation` on every ask() call in
    that session so turns get grouped together."""
    _check_role()
    conversation = frappe.get_doc(
        {
            "doctype": "AI Assistant Conversation",
            "user": frappe.session.user,
            "title": "",
            "pinned": 0,
            "last_activity": now_datetime(),
        }
    ).insert(ignore_permissions=True)
    return {"conversation": conversation.name}


@frappe.whitelist()
def list_conversations(limit: int = 50):
    """The current user's own conversations only, pinned first then most
    recently active. Title falls back to "(Untitled)" for a conversation
    whose first ask() call hasn't landed yet (or somehow never set one)."""
    _check_role()
    limit = max(1, min(cint(limit or 50), 100))
    rows = frappe.get_all(
        "AI Assistant Conversation",
        filters={"user": frappe.session.user},
        fields=["name", "title", "pinned", "last_activity"],
        order_by="pinned desc, last_activity desc",
        limit=limit,
        ignore_permissions=True,
    )
    return {
        "conversations": [
            {"name": r.name, "title": r.title or _("(Untitled)"), "pinned": cint(r.pinned), "last_activity": str(r.last_activity) if r.last_activity else None}
            for r in rows
        ]
    }


@frappe.whitelist()
def get_conversation_messages(conversation: str, limit: int = 100, before: str | None = None):
    """A specific conversation's transcript, cursor-paginated (newest page
    first) - used to resume a past analysis from the conversation-history
    list, distinct from get_recent_history's flat "last N messages
    regardless of grouping" view. Previously fetched the ENTIRE transcript
    unbounded in one query/DOM render - fine for a short conversation but an
    unbounded query plus an unbounded single-shot DOM append for a
    long-lived, heavily-used one. `limit` (capped at 200) bounds each page;
    `before` is the `creation` timestamp of the oldest message already
    loaded, used by the client to page further back via "Load older
    messages" (see ai_assistant_console.js's _load_conversation_page).
    Returns messages in ascending order within the page, plus `has_more` so
    the client knows whether an older page exists."""
    _check_role()
    _check_conversation_ownership(conversation)

    limit = max(1, min(cint(limit or 100), 200))
    filters = {"conversation": conversation}
    if before:
        filters["creation"] = ["<", before]

    rows = frappe.get_all(
        "AI Assistant Message",
        filters=filters,
        fields=["role", "content", "feedback", "creation"],
        order_by="creation desc",
        limit=limit + 1,
        ignore_permissions=True,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()

    return {
        "messages": [
            {"role": r.role, "content": r.content, "feedback": r.feedback, "creation": str(r.creation)}
            for r in rows
        ],
        "has_more": has_more,
    }


@frappe.whitelist()
def rename_conversation(conversation: str, title: str):
    _check_role()
    _check_conversation_ownership(conversation)
    frappe.db.set_value("AI Assistant Conversation", conversation, "title", title, update_modified=True)
    return {"ok": True}


@frappe.whitelist()
def pin_conversation(conversation: str, pinned):
    _check_role()
    _check_conversation_ownership(conversation)
    frappe.db.set_value("AI Assistant Conversation", conversation, "pinned", 1 if cint(pinned) else 0, update_modified=True)
    return {"ok": True}


@frappe.whitelist()
def delete_conversation(conversation: str):
    _check_role()
    _check_conversation_ownership(conversation)
    frappe.db.delete("AI Assistant Message", {"conversation": conversation})
    frappe.delete_doc("AI Assistant Conversation", conversation, ignore_permissions=True)
    return {"ok": True}


@frappe.whitelist()
def submit_feedback(message: str, feedback: str, note: str | None = None):
    """Thumbs up/down on one AI Assistant Message. Ownership is checked on the
    message itself (its own `user` field), not via a conversation - a message
    logged with no conversation (pre-Phase-2, or a conversation-less ask() call)
    can still receive feedback."""
    _check_role()
    if feedback not in ("up", "down"):
        frappe.throw(_("Invalid feedback value."))
    owner = frappe.db.get_value("AI Assistant Message", message, "user")
    if not owner:
        frappe.throw(_("Message not found."))
    if owner != frappe.session.user:
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    frappe.db.set_value("AI Assistant Message", message, {"feedback": feedback, "feedback_note": note or ""}, update_modified=True)
    return {"ok": True}


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


# Ownership check helpers (mirror _check_conversation_ownership pattern exactly)
def _check_saved_report_ownership(saved_report: str) -> None:
    """An AI Saved Report may only be read/written by the user who created it."""
    owner = frappe.db.get_value("AI Saved Report", saved_report, "user")
    if not owner:
        frappe.throw(_("Saved Report not found."))
    if owner != frappe.session.user:
        frappe.throw(_("Not permitted."), frappe.PermissionError)


def _check_dashboard_ownership(dashboard: str) -> None:
    """An AI Dashboard may only be read/written by the user who created it."""
    owner = frappe.db.get_value("AI Dashboard", dashboard, "user")
    if not owner:
        frappe.throw(_("Dashboard not found."))
    if owner != frappe.session.user:
        frappe.throw(_("Not permitted."), frappe.PermissionError)


# =============================================================================
# SAVE/PIN (AI Saved Report)
# =============================================================================

@frappe.whitelist()
def save_report(question: str, context_json: str, response_json: str, title: str | None = None):
    """Create an AI Saved Report from the client-provided JSON snapshots.
    The client stringifies Context.to_dict() and StructuredResponse.to_dict()
    from the last ask() response; we store them as-is."""
    _check_role()
    if not question or not question.strip():
        frappe.throw(_("Question is required."))
    if not context_json or not response_json:
        frappe.throw(_("Context and response snapshots are required."))

    doc = frappe.get_doc({
        "doctype": "AI Saved Report",
        "user": frappe.session.user,
        "title": (title or question.strip()[:60]),
        "question": question.strip(),
        "context_snapshot": context_json,
        "response_snapshot": response_json,
        "tool_results_snapshot": "{}",
        "pinned": 0,
    }).insert(ignore_permissions=True)
    return {"name": doc.name}


@frappe.whitelist()
def list_saved_reports(limit: int = 50):
    """Current user's own saved reports, pinned first then creation desc."""
    _check_role()
    limit = max(1, min(cint(limit or 50), 100))
    rows = frappe.get_all(
        "AI Saved Report",
        filters={"user": frappe.session.user},
        fields=["name", "title", "pinned", "last_refreshed", "creation"],
        order_by="pinned desc, creation desc",
        limit=limit,
        ignore_permissions=True,
    )
    return {
        "reports": [
            {
                "name": r.name,
                "title": r.title,
                "pinned": cint(r.pinned),
                "last_refreshed": str(r.last_refreshed) if r.last_refreshed else None,
                "creation": str(r.creation) if r.creation else None,
            }
            for r in rows
        ]
    }


@frappe.whitelist()
def get_saved_report(name: str):
    """Ownership-checked full saved report with JSON snapshots parsed back to objects."""
    _check_role()
    _check_saved_report_ownership(name)
    doc = frappe.get_doc("AI Saved Report", name)
    return {
        "name": doc.name,
        "title": doc.title,
        "question": doc.question,
        "context": json.loads(doc.context_snapshot or "{}"),
        "response": json.loads(doc.response_snapshot or "{}"),
        "tool_results": json.loads(doc.tool_results_snapshot or "{}"),
        "pinned": cint(doc.pinned),
        "last_refreshed": str(doc.last_refreshed) if doc.last_refreshed else None,
    }


@frappe.whitelist()
def rename_saved_report(name: str, title: str):
    _check_role()
    _check_saved_report_ownership(name)
    frappe.db.set_value("AI Saved Report", name, "title", title, update_modified=True)
    return {"ok": True}


@frappe.whitelist()
def pin_saved_report(name: str, pinned):
    _check_role()
    _check_saved_report_ownership(name)
    frappe.db.set_value("AI Saved Report", name, "pinned", 1 if cint(pinned) else 0, update_modified=True)
    return {"ok": True}


@frappe.whitelist()
def delete_saved_report(name: str):
    _check_role()
    _check_saved_report_ownership(name)
    frappe.delete_doc("AI Saved Report", name, ignore_permissions=True)
    return {"ok": True}


@frappe.whitelist()
def refresh_saved_report(name: str):
    """Re-run the saved question through the real ask() pipeline and update the snapshot."""
    _check_role()
    _check_saved_report_ownership(name)
    doc = frappe.get_doc("AI Saved Report", name)

    # Call the module-level ask() directly (same module) to get a fresh StructuredResponse dict
    fresh = ask(message=doc.question)

    # Don't let a transient free-tier degraded answer (no tool called at all, despite
    # the retry guards in ask()) permanently overwrite a snapshot that previously had
    # real data - ask() can still return a technically-valid, non-throwing but
    # data-empty response after exhausting its own retry budget. Persisting that would
    # silently regress a working widget to blank on every future dashboard load, worse
    # than just leaving the last good snapshot in place. The degraded result is still
    # returned to the caller for this one refresh so the UI can show the outcome/retry,
    # it's just not written to the document.
    fresh_is_empty = not (fresh.get("kpis") or fresh.get("charts") or fresh.get("tables"))
    old_had_data = False
    if fresh_is_empty and doc.response_snapshot:
        old = json.loads(doc.response_snapshot)
        old_had_data = bool(old.get("kpis") or old.get("charts") or old.get("tables"))
    if fresh_is_empty and old_had_data:
        frappe.log_error(
            title="AI Assistant: refresh produced no data, snapshot kept",
            message=f"saved_report={name!r} question={doc.question!r}",
        )
        return fresh

    # Update snapshots
    doc.response_snapshot = json.dumps(fresh, default=str)
    doc.last_refreshed = now_datetime()
    doc.save(ignore_permissions=True)

    return fresh


# =============================================================================
# DASHBOARD (AI Dashboard + AI Dashboard Widget)
# =============================================================================

@frappe.whitelist()
def create_dashboard(title: str):
    """Create an empty AI Dashboard for the current user."""
    _check_role()
    if not title or not title.strip():
        frappe.throw(_("Title is required."))
    doc = frappe.get_doc({
        "doctype": "AI Dashboard",
        "user": frappe.session.user,
        "title": title.strip(),
    }).insert(ignore_permissions=True)
    return {"name": doc.name}


@frappe.whitelist()
def list_dashboards():
    """Current user's own dashboards with widget counts. Widget counts are
    batched in one grouped query rather than one frappe.db.count per
    dashboard row (N+1 for a user with many dashboards)."""
    _check_role()
    rows = frappe.get_all(
        "AI Dashboard",
        filters={"user": frappe.session.user},
        fields=["name", "title"],
        order_by="creation desc",
        ignore_permissions=True,
    )
    if not rows:
        return {"dashboards": []}

    dashboard_names = [r.name for r in rows]
    counts = frappe.db.sql(
        """
        SELECT parent, COUNT(*) AS cnt
        FROM `tabAI Dashboard Widget`
        WHERE parent IN %(names)s
        GROUP BY parent
        """,
        {"names": dashboard_names},
        as_dict=True,
    )
    count_map = {c.parent: c.cnt for c in counts}

    return {
        "dashboards": [
            {"name": r.name, "title": r.title, "widget_count": count_map.get(r.name, 0)}
            for r in rows
        ]
    }


@frappe.whitelist()
def get_dashboard(name: str):
    """Ownership-checked dashboard with widgets enriched from linked saved
    reports. Saved reports are batch-fetched in one query rather than one
    frappe.get_doc per widget (N+1 for a dashboard with many widgets)."""
    _check_role()
    _check_dashboard_ownership(name)
    doc = frappe.get_doc("AI Dashboard", name)

    if not doc.widgets:
        return {"name": doc.name, "title": doc.title, "widgets": []}

    saved_report_names = [w.saved_report for w in doc.widgets]
    saved_reports = frappe.get_all(
        "AI Saved Report",
        filters={"name": ["in", saved_report_names]},
        fields=["name", "title", "response_snapshot"],
        ignore_permissions=True,
    )
    sr_map = {sr.name: sr for sr in saved_reports}

    widgets = []
    for w in doc.widgets:
        sr = sr_map.get(w.saved_report)
        if not sr:
            continue  # orphaned widget (saved report deleted out from under it) - skip
        widgets.append({
            "name": w.name,  # child row's own name for later removal
            "saved_report": w.saved_report,
            "size": w.size,
            "title": sr.title or "",
            "response_snapshot": json.loads(sr.response_snapshot or "{}"),
        })

    return {
        "name": doc.name,
        "title": doc.title,
        "widgets": widgets,
    }


@frappe.whitelist()
def add_widget_to_dashboard(dashboard: str, saved_report: str, size: str = "Medium"):
    """Add a widget linking to a saved report (must belong to same user)."""
    _check_role()
    _check_dashboard_ownership(dashboard)
    _check_saved_report_ownership(saved_report)  # ensures same user owns the saved report

    doc = frappe.get_doc("AI Dashboard", dashboard)
    doc.append("widgets", {"saved_report": saved_report, "size": size})
    doc.save(ignore_permissions=True)
    return {"ok": True}


@frappe.whitelist()
def remove_widget_from_dashboard(dashboard: str, widget_row_name: str):
    """Remove a specific child row by its own name."""
    _check_role()
    _check_dashboard_ownership(dashboard)
    doc = frappe.get_doc("AI Dashboard", dashboard)
    # Find and remove the child row by its name
    doc.widgets = [w for w in doc.widgets if w.name != widget_row_name]
    doc.save(ignore_permissions=True)
    return {"ok": True}


@frappe.whitelist()
def reorder_widgets(dashboard: str, ordered_widget_names: list):
    """Rebuild widgets list in the given order; Frappe re-assigns idx on save."""
    _check_role()
    _check_dashboard_ownership(dashboard)
    doc = frappe.get_doc("AI Dashboard", dashboard)
    by_name = {w.name: w for w in doc.widgets}
    new_widgets = [by_name[n] for n in ordered_widget_names if n in by_name]
    doc.widgets = new_widgets
    doc.save(ignore_permissions=True)
    return {"ok": True}


@frappe.whitelist()
def rename_dashboard(name: str, title: str):
    _check_role()
    _check_dashboard_ownership(name)
    frappe.db.set_value("AI Dashboard", name, "title", title, update_modified=True)
    return {"ok": True}


@frappe.whitelist()
def delete_dashboard(name: str):
    _check_role()
    _check_dashboard_ownership(name)
    frappe.delete_doc("AI Dashboard", name, ignore_permissions=True)
    return {"ok": True}


# =============================================================================
# EXPORT
# =============================================================================

@frappe.whitelist()
def export_table(table_json: str, filename: str, format: str = "csv"):
    """Export a table (from a live answer or saved report) to CSV or XLSX.
    `table_json` is a JSON string of one response_schema Table.to_dict() shape:
    {columns: [{key, label, ...}], rows: [...]}.
    The client already has the exact table it wants to export; no server lookup needed.
    Both build_csv_response and build_xlsx_response set frappe.response internally
    and return nothing - the whitelisted method just calls the helper and Frappe's
    response layer handles the download. frappe.call on the client side needs no
    special handling beyond triggering the call."""
    _check_role()
    if not table_json:
        frappe.throw(_("Table data is required."))
    if not filename:
        frappe.throw(_("Filename is required."))

    try:
        table = json.loads(table_json)
    except (TypeError, ValueError):
        frappe.throw(_("Invalid table JSON."))

    columns = table.get("columns") or []
    rows = table.get("rows") or []

    # Build data as list[list] with header row first
    header = [col.get("label", col.get("key", "")) for col in columns]
    data = [header]
    for row in rows:
        data.append([row.get(col.get("key", ""), "") for col in columns])

    if format == "xlsx":
        build_xlsx_response(data, filename)
    else:
        build_csv_response(data, filename)
    # No return value - response helpers set frappe.response directly
# NOTE: appended into the existing ai/api.py, which already imports
# frappe/json/frappe._ at module scope - not repeated here.

def _check_scheduled_question_ownership(docname: str) -> None:
    """Mirror of _check_conversation_ownership for Scheduled Question."""
    owner = frappe.db.get_value("Scheduled Question", docname, "user")
    if not owner:
        frappe.throw(_("Scheduled Question not found."))
    if owner != frappe.session.user:
        frappe.throw(_("Not permitted."), frappe.PermissionError)


def _check_alert_rule_ownership(docname: str) -> None:
    """Mirror of _check_conversation_ownership for AI Alert Rule."""
    owner = frappe.db.get_value("AI Alert Rule", docname, "user")
    if not owner:
        frappe.throw(_("Alert Rule not found."))
    if owner != frappe.session.user:
        frappe.throw(_("Not permitted."), frappe.PermissionError)


VALID_ALERT_RULES = {
    "dead_stock",
    "stockout_risk",
    "negative_vendor_margin",
    "branch_underperformance",
    "low_gross_margin",
    "payables_concentration",
    "high_return_rate",
    "price_increases",
}


@frappe.whitelist()
def create_scheduled_question(question: str, frequency: str, recipients: str) -> dict:
    _check_role()
    if frequency not in ("Daily", "Weekly", "Monthly"):
        frappe.throw(_("Invalid frequency. Must be Daily, Weekly, or Monthly."))
    doc = frappe.get_doc({
        "doctype": "Scheduled Question",
        "question": question,
        "frequency": frequency,
        "recipients": _validate_recipients(recipients),
        "user": frappe.session.user,
        "enabled": 1,
    })
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist()
def list_scheduled_questions() -> list[dict]:
    _check_role()
    return frappe.get_all(
        "Scheduled Question",
        filters={"user": frappe.session.user},
        fields=["name", "question", "frequency", "recipients", "enabled", "last_run", "next_run"],
        order_by="creation desc",
    )


@frappe.whitelist()
def update_scheduled_question(
    name: str,
    question: str | None = None,
    frequency: str | None = None,
    recipients: str | None = None,
    enabled: int | None = None,
) -> dict:
    _check_role()
    _check_scheduled_question_ownership(name)
    doc = frappe.get_doc("Scheduled Question", name)
    if question is not None:
        doc.question = question
    if frequency is not None:
        if frequency not in ("Daily", "Weekly", "Monthly"):
            frappe.throw(_("Invalid frequency. Must be Daily, Weekly, or Monthly."))
        doc.frequency = frequency
    if recipients is not None:
        doc.recipients = _validate_recipients(recipients)
    if enabled is not None:
        doc.enabled = enabled
    doc.save(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist()
def delete_scheduled_question(name: str) -> dict:
    _check_role()
    _check_scheduled_question_ownership(name)
    frappe.delete_doc("Scheduled Question", name, ignore_permissions=True)
    return {"ok": True}


@frappe.whitelist()
def create_alert_rule(rule_name: str, recipients: str, threshold_override: str | None = None) -> dict:
    _check_role()
    if rule_name not in VALID_ALERT_RULES:
        frappe.throw(_(f"Invalid rule_name. Must be one of: {', '.join(sorted(VALID_ALERT_RULES))}"))
    if threshold_override:
        try:
            json.loads(threshold_override)
        except json.JSONDecodeError:
            frappe.throw(_("threshold_override must be valid JSON"))
    doc = frappe.get_doc({
        "doctype": "AI Alert Rule",
        "rule_name": rule_name,
        "recipients": _validate_recipients(recipients),
        "user": frappe.session.user,
        "enabled": 1,
        "threshold_override": threshold_override or "",
    })
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist()
def list_alert_rules() -> list[dict]:
    _check_role()
    return frappe.get_all(
        "AI Alert Rule",
        filters={"user": frappe.session.user},
        fields=["name", "rule_name", "recipients", "enabled", "last_triggered", "threshold_override"],
        order_by="creation desc",
    )


@frappe.whitelist()
def update_alert_rule(
    name: str,
    recipients: str | None = None,
    enabled: int | None = None,
    threshold_override: str | None = None,
) -> dict:
    _check_role()
    _check_alert_rule_ownership(name)
    doc = frappe.get_doc("AI Alert Rule", name)
    if recipients is not None:
        doc.recipients = _validate_recipients(recipients)
    if enabled is not None:
        doc.enabled = enabled
    if threshold_override is not None:
        if threshold_override:
            try:
                json.loads(threshold_override)
            except json.JSONDecodeError:
                frappe.throw(_("threshold_override must be valid JSON"))
        doc.threshold_override = threshold_override
    doc.save(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist()
def delete_alert_rule(name: str) -> dict:
    _check_role()
    _check_alert_rule_ownership(name)
    frappe.delete_doc("AI Alert Rule", name, ignore_permissions=True)
    return {"ok": True}
