"""Whitelisted Desk Help APIs — separate from aimatic.ai BI console."""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import now_datetime

from aimatic.ai.nemotron_client import NemotronError, get_chat_completion
from aimatic.help.prompt import build_system_prompt, infer_module
from aimatic.help.retriever import list_starter_questions, retrieve_topics
from aimatic.help.tools import TOOL_SPECS, run_tool

_MAX_TOOL_ITERATIONS = 4
_MAX_HISTORY_TURNS = 12
_MAX_MESSAGE_LEN = 4000


def _require_desk_user() -> None:
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required."), frappe.PermissionError)


def _parse_history(history: str | None) -> list[dict]:
	if not history:
		return []
	try:
		parsed = json.loads(history)
	except (TypeError, json.JSONDecodeError):
		return []
	if not isinstance(parsed, list):
		return []
	turns = []
	for turn in parsed[-_MAX_HISTORY_TURNS:]:
		if isinstance(turn, dict) and turn.get("role") in ("user", "assistant") and turn.get("content"):
			turns.append({"role": turn["role"], "content": str(turn["content"])[:_MAX_MESSAGE_LEN]})
	return turns


def _parse_context(context: str | dict | None) -> dict[str, Any]:
	if not context:
		return {}
	if isinstance(context, str):
		try:
			context = json.loads(context)
		except (TypeError, json.JSONDecodeError):
			return {}
	if not isinstance(context, dict):
		return {}
	help_links = context.get("help_links") or []
	if not isinstance(help_links, list):
		help_links = []
	clean_links = []
	for link in help_links[:8]:
		if isinstance(link, dict) and (link.get("label") or link.get("url")):
			clean_links.append(
				{
					"label": str(link.get("label") or "")[:120],
					"url": str(link.get("url") or "")[:500],
				}
			)
		elif isinstance(link, str) and link.strip():
			clean_links.append({"label": link.strip()[:120], "url": link.strip()[:500]})
	doctype = str(context.get("doctype") or "").strip()[:140] or None
	module = str(context.get("module") or "").strip()[:60] or None
	if not module:
		module = infer_module(doctype)
	return {
		"doctype": doctype,
		"docname": str(context.get("docname") or "").strip()[:140] or None,
		"route": str(context.get("route") or "").strip()[:240] or None,
		"module": module,
		"meta_description": str(context.get("meta_description") or "").strip()[:500] or None,
		"documentation_url": str(context.get("documentation_url") or "").strip()[:500] or None,
		"help_links": clean_links,
	}


def _check_conversation_ownership(conversation: str) -> None:
	owner = frappe.db.get_value("Help Conversation", conversation, "user")
	if not owner:
		frappe.throw(_("Conversation not found."))
	if owner != frappe.session.user:
		frappe.throw(_("Not permitted."), frappe.PermissionError)


def _log_turn(role: str, content: str, conversation: str | None = None) -> None:
	try:
		doc = frappe.get_doc(
			{
				"doctype": "Help Message",
				"user": frappe.session.user,
				"role": role,
				"content": content,
			}
		)
		if conversation:
			doc.conversation = conversation
		doc.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Help Message log failed")


def _extract_tool_calls(message: dict) -> list[dict]:
	raw = message.get("tool_calls") or []
	if not isinstance(raw, list):
		return []
	return [tc for tc in raw if isinstance(tc, dict) and tc.get("id") and tc.get("function")]


@frappe.whitelist()
def ask(message: str, history: str | None = None, conversation: str | None = None, context: str | None = None):
	"""Desk Help chat — curated how-to guidance only (not BI analytics)."""
	_require_desk_user()
	if not message or not str(message).strip():
		frappe.throw(_("Message is required."))
	message = str(message).strip()[:_MAX_MESSAGE_LEN]
	if conversation:
		_check_conversation_ownership(conversation)

	ctx = _parse_context(context)
	topics = retrieve_topics(
		message,
		doctype=ctx.get("doctype"),
		module=ctx.get("module"),
		limit=4,
	)
	messages = [{"role": "system", "content": build_system_prompt(ctx, topics)}]
	messages.extend(_parse_history(history))
	user_payload = message
	if ctx.get("doctype") or ctx.get("route"):
		user_payload = (
			f"[Desk context: doctype={ctx.get('doctype') or '-'}, "
			f"route={ctx.get('route') or '-'}, module={ctx.get('module') or '-'}]\n\n{message}"
		)
	messages.append({"role": "user", "content": user_payload})

	reply = ""
	try:
		for _attempt in range(_MAX_TOOL_ITERATIONS):
			assistant_msg = get_chat_completion(
				messages,
				tools=TOOL_SPECS,
				temperature=0.3,
				max_tokens=900,
				timeout=45,
			)
			tool_calls = _extract_tool_calls(assistant_msg)
			content = (assistant_msg.get("content") or "").strip()
			if tool_calls:
				messages.append(assistant_msg)
				for tc in tool_calls:
					fn = tc.get("function") or {}
					name = fn.get("name") or ""
					args = fn.get("arguments") or "{}"
					tool_result = run_tool(name, args)
					messages.append(
						{
							"role": "tool",
							"tool_call_id": tc["id"],
							"content": tool_result,
						}
					)
				continue
			reply = content
			break
		else:
			reply = content or _("I could not finish that answer. Please try a shorter question.")
	except NemotronError as exc:
		# Shared AI Integration Settings kill switch / provider failures
		frappe.throw(_(str(exc)))

	if not reply:
		reply = _("I do not have enough curated help for that yet. Try asking about Item, Price List, Stock Entry, or Payment Entry.")

	_log_turn("user", message, conversation)
	_log_turn("assistant", reply, conversation)
	if conversation:
		updates = {"last_activity": now_datetime()}
		if not frappe.db.get_value("Help Conversation", conversation, "title"):
			updates["title"] = message[:60]
		if ctx.get("doctype"):
			updates["context_doctype"] = ctx["doctype"]
		frappe.db.set_value("Help Conversation", conversation, updates, update_modified=True)

	return {
		"reply": reply,
		"conversation": conversation,
		"context": {
			"doctype": ctx.get("doctype"),
			"module": ctx.get("module"),
			"route": ctx.get("route"),
		},
		"topics_used": [{"name": t["name"], "title": t["title"]} for t in topics],
	}


@frappe.whitelist()
def start_conversation(context: str | None = None):
	_require_desk_user()
	ctx = _parse_context(context)
	doc = frappe.get_doc(
		{
			"doctype": "Help Conversation",
			"user": frappe.session.user,
			"last_activity": now_datetime(),
			"context_doctype": ctx.get("doctype"),
		}
	)
	doc.insert(ignore_permissions=True)
	return {"name": doc.name}


@frappe.whitelist()
def list_starters(context: str | None = None):
	_require_desk_user()
	ctx = _parse_context(context)
	return {
		"starters": list_starter_questions(doctype=ctx.get("doctype"), module=ctx.get("module"), limit=6),
		"context": {"doctype": ctx.get("doctype"), "module": ctx.get("module")},
	}


@frappe.whitelist()
def get_context_hint(context: str | None = None):
	"""Lightweight badge payload for the float without calling the LLM."""
	_require_desk_user()
	ctx = _parse_context(context)
	label = ctx.get("doctype") or ctx.get("module") or "ERPNext"
	return {"label": label, "module": ctx.get("module"), "doctype": ctx.get("doctype")}


@frappe.whitelist()
def submit_feedback(message_name: str, feedback: str, note: str | None = None):
	_require_desk_user()
	if feedback not in ("up", "down"):
		frappe.throw(_("Invalid feedback."))
	owner = frappe.db.get_value("Help Message", message_name, "user")
	if not owner:
		frappe.throw(_("Message not found."))
	if owner != frappe.session.user and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	frappe.db.set_value(
		"Help Message",
		message_name,
		{"feedback": feedback, "feedback_note": (note or "")[:500]},
		update_modified=False,
	)
	return {"ok": True}
