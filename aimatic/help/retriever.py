"""Keyword / module / DocType retrieval over Help Topic (no vector DB)."""

from __future__ import annotations

import re

import frappe

from aimatic.help.prompt import infer_module

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.I)


def _tokens(text: str) -> set[str]:
	return {t.lower() for t in _TOKEN_RE.findall(text or "")}


def _parse_doctypes(raw: str | None) -> list[str]:
	if not raw:
		return []
	return [p.strip() for p in re.split(r"[,;\n]+", raw) if p.strip()]


def _parse_starters(raw: str | None) -> list[str]:
	if not raw:
		return []
	return [line.strip() for line in str(raw).splitlines() if line.strip()]


def score_topic(topic: dict, *, message: str, doctype: str | None, module: str | None) -> int:
	score = 0
	topic_module = (topic.get("module") or "").strip()
	topic_doctypes = _parse_doctypes(topic.get("doctypes"))
	hay = " ".join(
		[
			topic.get("title") or "",
			topic.get("tags") or "",
			topic.get("body") or "",
			topic.get("starter_questions") or "",
			" ".join(topic_doctypes),
		]
	).lower()

	if doctype and doctype in topic_doctypes:
		score += 50
	if module and topic_module == module:
		score += 25
	msg_tokens = _tokens(message)
	hay_tokens = _tokens(hay)
	overlap = msg_tokens & hay_tokens
	score += min(30, len(overlap) * 3)
	return score


def retrieve_topics(
	message: str,
	*,
	doctype: str | None = None,
	module: str | None = None,
	limit: int = 4,
) -> list[dict]:
	module = module or infer_module(doctype, message)
	rows = frappe.get_all(
		"Help Topic",
		filters={"enabled": 1},
		fields=[
			"name",
			"title",
			"module",
			"doctypes",
			"tags",
			"body",
			"starter_questions",
			"priority",
		],
		order_by="priority asc",
		limit_page_length=200,
		ignore_permissions=True,
	)
	scored: list[tuple[int, int, dict]] = []
	for row in rows:
		s = score_topic(row, message=message or "", doctype=doctype, module=module)
		if s > 0:
			scored.append((s, int(row.get("priority") or 100), row))

	if not scored and (doctype or module):
		for row in rows:
			if module and row.get("module") == module:
				scored.append((1, int(row.get("priority") or 100), row))
			elif doctype and doctype in _parse_doctypes(row.get("doctypes")):
				scored.append((1, int(row.get("priority") or 100), row))

	scored.sort(key=lambda x: (-x[0], x[1], x[2].get("title") or ""))
	out = []
	for _, _, row in scored[: max(1, min(limit, 8))]:
		out.append(
			{
				"name": row.name,
				"title": row.title,
				"module": row.module,
				"doctypes": _parse_doctypes(row.doctypes),
				"body": row.body,
				"starter_questions": _parse_starters(row.starter_questions),
			}
		)
	return out


def list_starter_questions(
	*, doctype: str | None = None, module: str | None = None, limit: int = 6
) -> list[str]:
	topics = retrieve_topics("", doctype=doctype, module=module or infer_module(doctype), limit=6)
	seen: set[str] = set()
	out: list[str] = []
	for topic in topics:
		for q in topic.get("starter_questions") or []:
			key = q.lower()
			if key in seen:
				continue
			seen.add(key)
			out.append(q)
			if len(out) >= limit:
				return out
	defaults = [
		"How do I create an Item?",
		"Where do I set selling prices?",
		"How do I do a Stock Entry?",
		"How do I record a Payment Entry?",
	]
	for q in defaults:
		if q.lower() not in seen:
			out.append(q)
		if len(out) >= limit:
			break
	return out
