from __future__ import annotations

import html as html_module
import re

import frappe
from frappe import _

from aimaticlearning.lms_learning.protected_notes import get_notes_for_profile


def chapter_hub_renderer(profile_name: str) -> str:
	"""Self-contained chapter hub HTML for LMS lesson body (no macros / no external JS)."""
	if not frappe.db.exists("Learning Chapter Profile", profile_name):
		return f"<p>{_('Chapter content not found.')}</p>"

	profile = frappe.get_doc("Learning Chapter Profile", profile_name)
	quiz_html = ""
	if profile.chapter_quiz and frappe.session.user != "Guest":
		try:
			from lms.plugins import quiz_renderer

			quiz_html = quiz_renderer(profile.chapter_quiz)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Chapter hub quiz render failed")

	return frappe.render_template(
		"templates/lms_learning/chapter_hub.html",
		{
			"profile_name": profile_name,
			"chapter_title": profile.chapter_title,
			"notes_html": _build_notes_details(profile),
			"flashcards_html": _build_flashcard_details(profile),
			"quiz_html": quiz_html,
		},
	)


def notes_paragraphs_from_html(notes_html: str) -> list[str]:
	if not notes_html:
		return []
	parts = re.findall(r"<p>(.*?)</p>", notes_html, flags=re.DOTALL)
	paragraphs = []
	for part in parts:
		text = re.sub(r"<[^>]+>", "", part)
		text = frappe.utils.strip_html(part).strip()
		if len(text) > 20:
			paragraphs.append(text)
	return paragraphs


def _build_notes_details(profile: frappe.Document) -> str:
	paragraphs = notes_paragraphs_from_html(profile.notes_html or "")
	if not paragraphs:
		return "<p class=\"ach-empty\">No study notes for this chapter yet.</p>"

	chunks: list[str] = []
	chunk_size = 3
	for start in range(0, len(paragraphs), chunk_size):
		chunk = paragraphs[start:start + chunk_size]
		section = start // chunk_size + 1
		inner = "".join(f"<p>{html_module.escape(p)}</p>" for p in chunk)
		open_attr = " open" if section == 1 else ""
		chunks.append(
			f"<details class=\"ach-details\"{open_attr}>"
			f"<summary><span class=\"ach-section-num\">{section:02d}</span>"
			f"<span class=\"ach-section-label\">Part {section}</span>"
			f"<span class=\"ach-section-chevron\">↘</span></summary>"
			f"<div class=\"ach-section-body\">{inner}</div></details>"
		)
	return "".join(chunks)


def _build_flashcard_details(profile: frappe.Document) -> str:
	cards = frappe.get_all(
		"Learning Flashcard",
		filters={
			"learning_module": profile.learning_module,
			"course_chapter": profile.course_chapter,
			"status": "Published",
		},
		fields=["name", "front", "back", "concept", "difficulty"],
		limit_page_length=30,
		order_by="creation asc",
	)
	if not cards:
		return "<p class=\"ach-empty\">No flashcards published for this chapter yet.</p>"

	parts = []
	for i, card in enumerate(cards, start=1):
		front = html_module.escape(card.front or "")
		back = html_module.escape(card.back or "")
		name = html_module.escape(card.name or "", quote=True)
		difficulty = html_module.escape(card.difficulty or "Medium")
		hidden = "" if i == 1 else " hidden"
		parts.append(
			f"<article class=\"ach-flash-item\" data-ach-card data-ach-card-name=\"{name}\""
			f" data-ach-difficulty=\"{difficulty}\"{hidden}>"
			f"<div class=\"ach-flash-card\" data-ach-flash-card role=\"button\" tabindex=\"0\""
			f" aria-label=\"Flashcard. Tap to flip.\">"
			f"<span class=\"ach-card-face ach-card-front\">{front}</span>"
			f"<span class=\"ach-card-face ach-card-back\" hidden>{back}</span>"
			f"<span class=\"ach-flip-label\">Tap to flip</span></div>"
			f"<div class=\"ach-flash-card-meta\"><span>{difficulty}</span>"
			f"<span data-ach-flash-progress>Card {i} of {len(cards)}</span></div></article>"
		)
	return (
		f"<div class=\"ach-flash-study\" data-ach-flash-study>"
		f"<div class=\"ach-flash-filter\"><span>Recall rating</span>"
		f"<span class=\"ach-flash-filter-hint\">Choose after revealing</span></div>"
		f"{''.join(parts)}"
		f"<div class=\"ach-flash-actions\">"
		f"<button type=\"button\" class=\"ach-rate ach-rate-hard\" data-ach-rating=\"hard\">Hard</button>"
		f"<button type=\"button\" class=\"ach-rate ach-rate-good\" data-ach-rating=\"good\">Good</button>"
		f"<button type=\"button\" class=\"ach-rate ach-rate-easy\" data-ach-rating=\"easy\">Easy</button>"
		f"</div></div>"
	)
