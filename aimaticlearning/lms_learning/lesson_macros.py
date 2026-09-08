from __future__ import annotations

import html as html_module
import re
from html.parser import HTMLParser

import frappe
from frappe import _

from aimaticlearning.lms_learning.protected_notes import get_notes_for_profile


def chapter_hub_renderer(profile_name: str) -> str:
	"""Return the chapter notes directly; MCQs and flashcards are separate lessons."""
	if not frappe.db.exists("Learning Chapter Profile", profile_name):
		return f"<p>{_('Chapter content not found.')}</p>"
	profile = frappe.get_doc("Learning Chapter Profile", profile_name)
	return profile.notes_html or "<p>No study notes are available for this chapter yet.</p>"

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


class _NotesBlockParser(HTMLParser):
    """Parse the small, sanitised HTML vocabulary used by chapter notes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self._text: tuple[str, int | None, list[str]] | None = None
        self._list_stack: list[dict] = []
        self._table_rows: list[list[str]] | None = None
        self._row: list[str] | None = None

    @staticmethod
    def _clean(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._text = ("heading", int(tag[1:]), [])
        elif tag == "p":
            self._text = ("paragraph", None, [])
        elif tag in {"ul", "ol"}:
            self._list_stack.append({"ordered": tag == "ol", "items": []})
        elif tag == "li":
            self._text = ("list_item", None, [])
        elif tag == "table":
            self._table_rows = []
        elif tag == "tr":
            self._row = []
        elif tag in {"th", "td"}:
            self._text = ("cell", None, [])

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text[2].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p"} and self._text:
            kind, level, parts = self._text
            text = self._clean("".join(parts))
            if text:
                self.blocks.append({"kind": kind, "level": level, "text": text})
            self._text = None
        elif tag == "li" and self._text:
            text = self._clean("".join(self._text[2]))
            if text and self._list_stack:
                self._list_stack[-1]["items"].append(text)
            self._text = None
        elif tag in {"ul", "ol"} and self._list_stack:
            current = self._list_stack.pop()
            if current["items"]:
                self.blocks.append({"kind": "list", "ordered": current["ordered"], "items": current["items"]})
        elif tag in {"th", "td"} and self._text:
            text = self._clean("".join(self._text[2]))
            if text and self._row is not None:
                self._row.append(text)
            self._text = None
        elif tag == "tr" and self._row is not None:
            if self._table_rows is not None and self._row:
                self._table_rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table_rows is not None:
            if self._table_rows:
                self.blocks.append({"kind": "table", "rows": self._table_rows})
            self._table_rows = None


def notes_blocks_from_html(notes_html: str) -> list[dict]:
    """Return semantic note blocks without exposing raw HTML to the client."""
    if not notes_html:
        return []
    parser = _NotesBlockParser()
    parser.feed(notes_html)
    parser.close()
    return parser.blocks


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
