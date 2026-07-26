from __future__ import annotations

import base64
import binascii
import json
import re
from decimal import Decimal, InvalidOperation

import frappe
import requests
from frappe import _
from frappe.utils import cint, now_datetime, today
from frappe.utils.csvutils import build_csv_response
from frappe.utils.xlsxutils import build_xlsx_response

from aimatic.ai.nemotron_client import NemotronError, get_chat_completion, get_completion
from aimatic.ai.tools import TOOL_DISPATCH as _CORE_DISPATCH, TOOL_SPECS as _CORE_SPECS
from aimatic.ai.tools import _resolve_company, _resolve_branch_filter
from aimatic.ai.tools_extended import TOOL_DISPATCH as _EXTENDED_DISPATCH, TOOL_SPECS as _EXTENDED_SPECS
from aimatic.ai.tools_accounts import TOOL_DISPATCH as _ACCOUNTS_DISPATCH, TOOL_SPECS as _ACCOUNTS_SPECS
from aimatic.ai.tools_expanded import TOOL_DISPATCH as _EXPANDED_DISPATCH, TOOL_SPECS as _EXPANDED_SPECS
from aimatic.ai.report_runner import TOOL_DISPATCH as _REPORT_RUNNER_DISPATCH, TOOL_SPECS as _REPORT_RUNNER_SPECS
from aimatic.ai.analytics_engine import TOOL_DISPATCH as _ANALYTICS_DISPATCH, TOOL_SPECS as _ANALYTICS_SPECS
from aimatic.ai.dynamic_report import DYNAMIC_REPORT_DISPATCH, TOOL_SPECS as _DYNAMIC_REPORT_SPECS
from aimatic.ai.abc_xyz_analysis import TOOL_DISPATCH as _ABC_XYZ_DISPATCH, TOOL_SPECS as _ABC_XYZ_SPECS
from aimatic.ai.invocation_response import build_invocation_response as build_response
from aimatic.ai.routing_engine import (
    ANALYSIS_PLAN_TOOL_SPEC,
    fallback_plan,
    parse_plan_message,
    route_for_tool,
    select_tool_specs,
)

TOOL_SPECS = _CORE_SPECS + _EXTENDED_SPECS + _ACCOUNTS_SPECS + _EXPANDED_SPECS + _REPORT_RUNNER_SPECS + _ANALYTICS_SPECS + _DYNAMIC_REPORT_SPECS + _ABC_XYZ_SPECS
TOOL_DISPATCH = {**_CORE_DISPATCH, **_EXTENDED_DISPATCH, **_ACCOUNTS_DISPATCH, **_EXPANDED_DISPATCH, **_REPORT_RUNNER_DISPATCH, **_ANALYTICS_DISPATCH, **DYNAMIC_REPORT_DISPATCH, **_ABC_XYZ_DISPATCH}

_ALLOWED_ROLES = {"System Manager", "Sales Manager", "Accounts Manager", "POS Supervisor"}
_MAX_TOOL_ITERATIONS = 8
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


_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_MODELS_CACHE_KEY = "aimatic_ai_free_models"
_MODELS_CACHE_SECONDS = 6 * 60 * 60
_FREE_AUDIO_MODEL_CACHE_KEY = "aimatic_ai_free_audio_model"
_PREFERRED_FREE_AUDIO_MODELS = (
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
)
_ALLOWED_AUDIO_FORMATS = {"wav", "mp3", "aiff", "aac", "ogg", "flac", "m4a", "pcm16", "pcm24"}
_MAX_AUDIO_BYTES = 2 * 1024 * 1024
_MAX_AUDIO_BASE64_CHARS = 4 * ((_MAX_AUDIO_BYTES + 2) // 3)


@frappe.whitelist()
def get_ai_integration_settings():
    """Available to any of the 4 assistant roles (not just System Manager) so
    the console can show every user whether the assistant is currently
    enabled, even though only System Manager can change it (see
    update_ai_integration_settings). Never returns the API key - it isn't
    stored on this doctype at all, only in site config."""
    _check_role()
    settings = frappe.get_cached_doc("AI Integration Settings")
    return {"enabled": bool(settings.enabled), "model": settings.model or ""}


@frappe.whitelist()
def update_ai_integration_settings(enabled, model: str | None = None):
    """System Manager only - this controls org-wide assistant availability and
    cost, not just the caller's own session. A bad `model` slug isn't
    validated here; it fails loudly on the next real ask()/ping() call via the
    normal NemotronError -> user-facing error path, same as any other
    OpenRouter error this module already surfaces."""
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    frappe.db.set_single_value(
        "AI Integration Settings",
        {"enabled": cint(enabled), "model": (model or "").strip()},
    )
    return {"ok": True}


@frappe.whitelist()
def list_available_free_models(force_refresh=False):
    """System Manager only. Returns OpenRouter's currently free, tool-calling-
    capable models (this assistant is entirely tool-call driven, so a model
    without function-calling support isn't usable here regardless of price).
    Cached for 6 hours in Redis (shared across every gunicorn worker and all
    sites on this bench) since this is a live external call, not something to
    repeat on every dialog open."""
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    if not cint(force_refresh):
        cached = frappe.cache().get_value(_MODELS_CACHE_KEY)
        if cached is not None:
            return cached

    try:
        response = requests.get(_OPENROUTER_MODELS_URL, timeout=15)
    except requests.RequestException as e:
        raise NemotronError(f"Could not reach OpenRouter: {e}")

    if response.status_code != 200:
        raise NemotronError(f"OpenRouter returned {response.status_code}: {response.text}")

    try:
        data = response.json()["data"]
    except (KeyError, TypeError, ValueError):
        raise NemotronError(f"Unexpected OpenRouter response shape: {response.text}")

    models = [
        {
            "id": m["id"],
            "name": m.get("name") or m["id"],
            "context_length": m.get("context_length") or 0,
        }
        for m in data
        if m.get("id", "").endswith(":free") and "tools" in (m.get("supported_parameters") or [])
    ]
    models.sort(key=lambda m: -m["context_length"])

    frappe.cache().set_value(_MODELS_CACHE_KEY, models, expires_in_sec=_MODELS_CACHE_SECONDS)
    return models


def _is_zero_price(value) -> bool:
    if value is None:
        return False
    try:
        return Decimal(str(value)) == 0
    except (InvalidOperation, ValueError):
        return False


def _get_free_audio_model(force_refresh=False) -> str:
    """Select an audio-input model that OpenRouter currently advertises at
    zero prompt and completion cost. There is deliberately no paid fallback:
    if the free catalogue changes or its only audio model disappears, voice
    transcription fails visibly instead of spending money unexpectedly."""
    if not cint(force_refresh):
        cached = frappe.cache().get_value(_FREE_AUDIO_MODEL_CACHE_KEY)
        if cached:
            return cached.decode() if isinstance(cached, bytes) else cached

    try:
        response = requests.get(_OPENROUTER_MODELS_URL, timeout=15)
    except requests.RequestException as e:
        raise NemotronError(f"Could not check OpenRouter's free audio models: {e}")

    if response.status_code != 200:
        raise NemotronError(
            f"OpenRouter model catalogue returned {response.status_code}: {response.text}"
        )

    try:
        models = response.json()["data"]
    except (KeyError, TypeError, ValueError):
        raise NemotronError("OpenRouter returned an unexpected model catalogue response.")

    free_audio_models = []
    for model in models:
        model_id = model.get("id", "")
        pricing = model.get("pricing") or {}
        architecture = model.get("architecture") or {}
        input_modalities = architecture.get("input_modalities") or []
        output_modalities = architecture.get("output_modalities") or []
        if (
            model_id.endswith(":free")
            and _is_zero_price(pricing.get("prompt"))
            and _is_zero_price(pricing.get("completion"))
            and "audio" in input_modalities
            and "text" in output_modalities
        ):
            free_audio_models.append(model_id)

    selected = next(
        (model_id for model_id in _PREFERRED_FREE_AUDIO_MODELS if model_id in free_audio_models),
        free_audio_models[0] if free_audio_models else None,
    )
    if not selected:
        raise NemotronError(
            "No zero-cost audio transcription model is currently available on OpenRouter. "
            "No paid fallback was used."
        )

    frappe.cache().set_value(
        _FREE_AUDIO_MODEL_CACHE_KEY,
        selected,
        expires_in_sec=_MODELS_CACHE_SECONDS,
    )
    return selected


@frappe.whitelist()
def transcribe_audio(audio_base64: str, audio_format: str = "wav", language_hint: str = "auto"):
    """Transcribe a short recording through a *currently free* OpenRouter
    audio model. Audio is held only in request memory; this endpoint does not
    create a File or Conversation record. The result remains editable in the
    browser and is not submitted as an assistant question automatically."""
    _check_role()

    audio_format = (audio_format or "").strip().lower()
    if audio_format not in _ALLOWED_AUDIO_FORMATS:
        frappe.throw(_("Unsupported audio format."))

    if not isinstance(audio_base64, str) or not audio_base64:
        frappe.throw(_("No audio recording was received."))
    if len(audio_base64) > _MAX_AUDIO_BASE64_CHARS:
        frappe.throw(_("Recording is too large. Record no more than 45 seconds."))

    try:
        audio_bytes = base64.b64decode(audio_base64, validate=True)
    except (binascii.Error, ValueError):
        frappe.throw(_("The audio recording is invalid."))
    if len(audio_bytes) < 512:
        frappe.throw(_("The recording is too short. Please speak and try again."))
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        frappe.throw(_("Recording is too large. Record no more than 45 seconds."))

    hint = (language_hint or "auto").strip()[:32]
    language_instruction = (
        "Auto-detect the spoken language"
        if hint.lower() == "auto"
        else f"The expected spoken language is {hint}"
    )
    prompt = (
        "Transcribe this audio exactly in its spoken language and original script. "
        "Do not translate or answer it. Return only the transcript."
    )
    if hint.lower() != "auto":
        prompt += f" {language_instruction}."

    try:
        model = _get_free_audio_model()
        completion = get_chat_completion(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": audio_base64, "format": audio_format},
                    },
                ],
            }],
            # NVIDIA's model card explicitly recommends temperature=1.0 and
            # non-thinking mode for ASR. OpenRouter exposes temperature and
            # reasoning for this free model (but not NVIDIA's top_k knob).
            temperature=1.0,
            max_tokens=1024,
            model=model,
            reasoning={"enabled": False},
            timeout=120,
            return_metadata=True,
        )
    except NemotronError as e:
        frappe.throw(str(e))

    usage = completion.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    if cint(prompt_details.get("audio_tokens")) <= 0:
        frappe.throw(_(
            "OpenRouter's current free provider did not accept the audio recording. "
            "No transcript was inserted and no paid fallback was used. Please try again later."
        ))

    message = completion["message"]
    transcript = message.get("content")
    if isinstance(transcript, list):
        transcript = " ".join(
            part.get("text", "")
            for part in transcript
            if isinstance(part, dict) and part.get("type") == "text"
        )
    if not isinstance(transcript, str) or not transcript.strip():
        frappe.throw(_("The free transcription model returned no transcript. Please try again."))

    transcript = transcript.strip()
    transcript = re.sub(r"^```(?:text)?\s*|\s*```$", "", transcript, flags=re.IGNORECASE).strip()
    transcript = re.sub(r"^transcript\s*:\s*", "", transcript, flags=re.IGNORECASE).strip()
    return {"transcript": transcript, "model": model, "free": True}


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
    """Phase 4e: restructured from a flat handful of named examples into named
    categories, plus an explicit fallback priority order. The flat-example
    pattern stopped scaling once the tool catalogue grew past ~25 (each new
    real-world confusion case required hand-adding one more named example, per
    this module's own documented history) - grouping by category gives the
    model a narrowing strategy (pick the category, then the tool within it)
    that keeps working as more tools are added, rather than one that only
    covers whichever handful of tools happen to be spelled out."""
    company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_default("company") or "the company"
    return (
        f"You are an analytics assistant for {company}, a retail business running on ERP. "
        f"Today's date is {today()}. "
        "Answer questions using ONLY the provided tools - never guess or estimate numbers "
        "yourself. All monetary figures returned by tools are in the company's default currency. "
        "When a question doesn't specify a date range, assume today; when it says things like "
        "\"this month\" or \"last week\", resolve them into concrete dates yourself using today's "
        "date above before calling a tool. Keep answers concise and concrete - lead with the "
        "number, then brief context.\n\n"
        "TOOL SELECTION - work top-down through these categories, and within a category prefer "
        "the tool whose own description most specifically matches the question:\n"
        "- Sales: get_sales_overview, get_sales_trend, get_branch_comparison, get_top_selling_items, "
        "get_sales_by_item_group, get_hourly_sales_pattern, get_payment_mode_split, "
        "get_discount_overview, get_returns_overview, get_active_shifts\n"
        "- Profitability: get_gross_margin_overview, get_selling_below_cost, get_item_price_history, "
        "get_price_increases\n"
        "- Purchasing: get_purchase_overview, rank_vendors, get_supplier_price_comparison, "
        "get_po_receipt_variance, get_purchase_concentration\n"
        "- Inventory: get_inventory_vs_sales, get_reorder_recommendations (THE tool for "
        "'what should I order/restock' questions), get_dead_stock_detail, get_stock_aging, "
        "get_negative_stock_check\n"
        "- Customers: get_top_customers, get_receivables_overview, get_customer_activity_segments\n"
        "- Finance/Accounts: get_outstanding_payables_overview, get_payables_aging, "
        "get_receivables_aging, get_cash_and_bank_balance, get_profit_and_loss_overview, "
        "get_expense_breakdown, get_trial_balance_summary, get_tax_liability_overview, "
        "get_payment_entry_summary, get_branch_profit_and_loss\n"
        "- Operational: get_open_documents_overview\n\n"
        "FALLBACK ORDER - only move to the next one if NO tool above matches the question, in "
        "this order: (1) run_analytics_query - a governed dataset/measure/dimension query "
        "(sales/purchases/inventory/payables) for combinations no fixed tool covers, e.g. "
        "'net sales by customer group this month vs last month'; pair it with "
        "drill_down_transactions when the question asks to see the underlying documents/"
        "invoices behind a figure. (2) list_frappe_reports then run_frappe_report - existing "
        "standard ERP reports (Stock Balance, Accounts Receivable, Sales Register, etc.) for "
        "questions that map to a well-known report by name. (3) run_dynamic_report - last resort "
        "only, a narrow whitelisted query over POS Invoice/Purchase Invoice/Purchase Receipt/Item/"
        "Customer/Supplier.\n\n"
        "A real business figure (like total outstanding payable, cash balance, or vendor margin) "
        "is almost always better sourced from a purpose-built tool or ledger-based query than from "
        "a single transactional doctype via run_dynamic_report, which can legitimately return zero "
        "rows even when the real figure is nonzero (e.g. a site whose purchase activity is recorded "
        "via Purchase Receipt rather than Purchase Invoice). If a purpose-built tool (or "
        "run_analytics_query) returns no data, trust that result and answer accordingly rather than "
        "retrying the same or a different doctype further down the fallback order. Every tool you "
        "can use is already provided to you through the function-calling mechanism - never write a "
        "tool's name out in your reply while reasoning about whether one exists or is available; if "
        "a question needs data, actually invoke a tool via a real function call before answering, "
        "even if you are unsure which one fits best - picking the closest match and calling it is "
        "always correct, describing tool options in plain text instead of calling one is never a "
        "valid answer."
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


def _derive_analysis_plan(message: str, history: str | None = None):
    """Ask the model only for business metadata, then validate it server-side.

    Planning failure is non-fatal: deterministic parsing still narrows the tool
    catalogue, so the reporting path remains available during provider flakiness.
    """
    company = _resolve_company()
    context_turns = _parse_history(history)[-4:]
    context_text = "\n".join(f"{turn['role']}: {turn['content']}" for turn in context_turns)
    planning_messages = [
        {
            "role": "system",
            "content": (
                "Understand the retail business question and submit the required "
                "analysis plan. Resolve relative dates using today=" + str(today()) + ". "
                "Use business concepts only; never include SQL, table names, field "
                "names, formulas, or calculated figures."
            ),
        },
        {"role": "user", "content": (context_text + "\nCurrent question: " + message).strip()},
    ]
    try:
        planned = get_chat_completion(
            planning_messages,
            tools=[ANALYSIS_PLAN_TOOL_SPEC],
            tool_choice={"type": "function", "function": {"name": "submit_analysis_plan"}},
            max_tokens=512,
        )
        return parse_plan_message(planned, message, company)
    except (NemotronError, TypeError, ValueError):
        return fallback_plan(message, company)


def _tool_call_arguments(call: dict) -> dict:
    function = call.get("function") or {}
    raw_args = function.get("arguments") or {}
    try:
        parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


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

    analysis_plan = _derive_analysis_plan(message, history)
    tool_invocations = []
    failed_tools = set()
    successful_routes = set()

    for _iteration in range(_MAX_TOOL_ITERATIONS):
        candidate_specs = select_tool_specs(
            message,
            analysis_plan,
            TOOL_SPECS,
            failed_tools=failed_tools,
            successful_routes=successful_routes,
        )
        if not candidate_specs and not successful_routes:
            frappe.throw(_("This question is outside the currently certified analytical tools."))
        try:
            assistant_message = get_chat_completion(
                messages,
                tools=candidate_specs or None,
                tool_choice="auto" if candidate_specs else None,
            )
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
            if _looks_like_tool_hallucination(
                reply, [i for i in tool_invocations if i["status"] == "success"]
            ):
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
            if not reply.strip():
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
                #
                # Deliberately NOT gated on `not tool_results` (the original condition
                # here) - a second, distinct sub-case reaches the exact same crash: the
                # model successfully calls a real tool (tool_results has data) but then
                # returns empty content on its closing turn. That sub-case skipped every
                # guard, fell through to _log_turn("assistant", "", ...), and threw
                # frappe.MandatoryError (content is mandatory on AI Assistant Message) -
                # swallowed by _log_turn's own except-Exception, so the turn silently
                # vanished from conversation history and the structured answer shipped
                # with a blank summary (Answer.summary is reply_text verbatim). Confirmed
                # via two real "AI Assistant Message log failed" Error Log entries on
                # siezal (2026-07-19, role='assistant', content=''). The retry costs one
                # iteration either way, and the model still has any tool results in its
                # own context, so it only needs to produce closing prose, not re-call
                # anything.
                frappe.log_error(
                    title="AI Assistant: empty non-answer corrected",
                    message=f"question={message!r} tool_invocations_so_far={[(i['tool_name'], i['status']) for i in tool_invocations]!r}",
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
            if not any(i["status"] == "success" for i in tool_invocations):
                messages.append({
                    "role": "user",
                    "content": (
                        "A factual business answer requires a successful certified tool call. "
                        "Call one of the provided tools now. If none can answer, state only that "
                        "the current certified analytics cannot answer the question; do not provide figures."
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

            structured = build_response(
                message,
                reply,
                tool_invocations,
                company,
                branch_names,
                user_role,
                analysis_plan,
            )
            return structured.to_dict()

        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name") or "unknown"
            arguments = _tool_call_arguments(call)
            result = _dispatch_tool_call(call)
            status = "error" if "error" in result else "success"
            route = route_for_tool(name)
            tool_invocations.append({
                "call_id": str(call.get("id") or f"call-{len(tool_invocations) + 1}"),
                "tool_name": name,
                "arguments": arguments,
                "result": result,
                "sequence": len(tool_invocations) + 1,
                "status": status,
                "route": route,
            })
            if status == "error":
                failed_tools.add(name)
            else:
                successful_routes.add(route)
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, default=str)})

    frappe.throw(_("The current certified analytics could not answer this question reliably. No business figures were produced."))


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

    try:
        response_snapshot = json.loads(response_json)
    except (TypeError, ValueError):
        frappe.throw(_("Response snapshot must be valid JSON."))
    if not isinstance(response_snapshot, dict):
        frappe.throw(_("Response snapshot must be a JSON object."))
    invocation_snapshot = response_snapshot.get("tool_invocations") or []

    doc = frappe.get_doc({
        "doctype": "AI Saved Report",
        "user": frappe.session.user,
        "title": (title or question.strip()[:60]),
        "question": question.strip(),
        "context_snapshot": context_json,
        "response_snapshot": response_json,
        "tool_results_snapshot": json.dumps(invocation_snapshot, default=str),
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
        "tool_results": json.loads(doc.tool_results_snapshot or "[]"),
        "tool_invocations": json.loads(doc.tool_results_snapshot or "[]"),
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
    doc.tool_results_snapshot = json.dumps(fresh.get("tool_invocations") or [], default=str)
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

    # build_csv_response/build_xlsx_response (via provide_binary_file) always
    # append their own ".csv"/".xlsx" suffix - every other core caller passes a
    # bare name. The client sends a name that already ends in the target
    # extension, so strip it here or the download lands as "name.xlsx.xlsx".
    base_filename = filename
    for ext in (".csv", ".xlsx"):
        if base_filename.lower().endswith(ext):
            base_filename = base_filename[: -len(ext)]
            break

    if format == "xlsx":
        build_xlsx_response(data, base_filename)
    else:
        build_csv_response(data, base_filename)
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
