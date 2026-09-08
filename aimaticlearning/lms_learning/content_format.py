"""Source-preserving semantic formatting for LMS chapter content.

Word sources used by the LMS are inconsistent: some use real Heading styles,
while others use Normal paragraphs with Word numbering metadata.  This module
turns both forms into a small, predictable HTML vocabulary without rewriting
the source wording.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from html.parser import HTMLParser
from typing import Any

from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

_HEADING_RE = re.compile(r"Heading\s+(\d+)$", re.I)
_NUMBERED_HEADING_RE = re.compile(r"^(?:\d+(?:\.\d+)*[.)]?|[A-Z][.)])\s+\S")
_TRAILING_PUNCTUATION = re.compile(r"[.,;:!?]$")


def clean_text(value: str | None) -> str:
	"""Collapse Word's layout whitespace while retaining all words and symbols."""
	return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def paragraph_kind(paragraph: Paragraph) -> dict[str, Any]:
	"""Classify one DOCX paragraph for the canonical HTML renderer.

	Heading styles take precedence.  Numbered/bulleted paragraphs are detected
	from the underlying Word numbering XML, because their visible marker is not
	part of ``paragraph.text``.  A conservative fallback catches the common
	Normal-style section labels found in legal study guides.
	"""
	text = clean_text(paragraph.text)
	if not text:
		return {"kind": "empty", "text": ""}

	style = paragraph.style.name if paragraph.style else ""
	if _HEADING_RE.fullmatch(style or ""):
		match = _HEADING_RE.fullmatch(style)
		return {"kind": "heading", "text": text, "level": _heading_level(int(match.group(1)))}

	list_info = paragraph_list_info(paragraph)
	if list_info:
		return {
			"kind": "list_item",
			"text": text,
			"ordered": list_info["ordered"],
			"level": list_info["level"],
		}

	if _looks_like_normal_heading(text):
		return {"kind": "heading", "text": text, "level": 3}
	return {"kind": "paragraph", "text": text}


def is_chapter_heading(paragraph: Paragraph) -> bool:
	"""Identify a document-level chapter heading without consuming subheadings."""
	text = clean_text(paragraph.text)
	style = paragraph.style.name if paragraph.style else ""
	return bool(re.match(r"^Chapter\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b", text, re.I)) or style == "Title"


def iter_docx_blocks(document: Any) -> Iterable[Paragraph | Table]:
	"""Yield Word paragraphs and tables in document order."""
	for child in document.element.body.iterchildren():
		if child.tag == qn("w:p"):
			yield Paragraph(child, document)
		elif child.tag == qn("w:tbl"):
			yield Table(child, document)


def _heading_level(style_level: int) -> int:
	# Chapter titles are emitted separately as h2.  Heading 1 inside a chapter
	# therefore becomes the first section level, h3; deeper levels cap at h4.
	return min(4, max(3, style_level + 2))


def _looks_like_normal_heading(text: str) -> bool:
	if len(text) < 3 or len(text) > 140 or _TRAILING_PUNCTUATION.search(text):
		return False
	if _NUMBERED_HEADING_RE.match(text):
		return True
	if text.isupper() and len(text.split()) <= 18:
		return True
	# Most source headings are title case, while ordinary legal prose contains
	# sentence punctuation or starts with a determiner followed by a verb.
	words = re.findall(r"[A-Za-z][A-Za-z'’-]*", text)
	if len(words) < 2 or len(words) > 14:
		return False
	title_case = sum(1 for word in words if word[0].isupper()) / len(words)
	return title_case >= 0.65 and not re.match(r"^(The|A|An)\s+\w+\s+(is|are|was|were|may|must|can|will|has|have|involves|provides)\b", text)


def paragraph_list_info(paragraph: Paragraph) -> dict[str, Any] | None:
	"""Return Word list level/type, or ``None`` for an ordinary paragraph."""
	ppr = paragraph._p.pPr
	if ppr is None or ppr.numPr is None:
		return None
	num_pr = ppr.numPr
	if num_pr.numId is None:
		return None
	try:
		num_id = int(num_pr.numId.val)
		level = int(num_pr.ilvl.val) if num_pr.ilvl is not None else 0
	except (TypeError, ValueError):
		return None

	ordered = _numbering_is_ordered(paragraph, num_id, level)
	return {"ordered": ordered, "level": max(0, level)}


def _numbering_is_ordered(paragraph: Paragraph, num_id: int, level: int) -> bool:
	"""Resolve a Word numbering definition to ordered vs bullet semantics."""
	try:
		numbering = paragraph.part.numbering_part.element
		abstract_id = None
		for num in numbering.findall(qn("w:num")):
			if int(num.get(qn("w:numId"))) == num_id:
				abstract = num.find(qn("w:abstractNumId"))
				abstract_id = int(abstract.get(qn("w:val"))) if abstract is not None else None
				break
		if abstract_id is None:
			return True
		for abstract in numbering.findall(qn("w:abstractNum")):
			if int(abstract.get(qn("w:abstractNumId"))) != abstract_id:
				continue
			for lvl in abstract.findall(qn("w:lvl")):
				if int(lvl.get(qn("w:ilvl"))) != level:
					continue
				fmt = lvl.find(qn("w:numFmt"))
				return (fmt.get(qn("w:val")) if fmt is not None else "decimal") != "bullet"
	except (AttributeError, TypeError, ValueError):
		pass
	return True


def render_docx_blocks(blocks: Iterable[Paragraph | Table], chapter_title: str | None = None) -> str:
	"""Render DOCX body blocks as canonical, escaped semantic HTML."""
	formatted: list[dict[str, Any]] = []
	if chapter_title:
		formatted.append({"kind": "heading", "text": clean_text(chapter_title), "level": 2})
	for block in blocks:
		if isinstance(block, Table):
			formatted.append({"kind": "table", "html": render_table_html(block)})
		else:
			item = paragraph_kind(block)
			if item["kind"] != "empty":
				formatted.append(item)
	return render_content_blocks(formatted)


def render_content_blocks(blocks: Iterable[dict[str, Any] | tuple]) -> str:
	"""Render normalized content blocks, grouping contiguous list items."""
	parts = ['<article class="aimatic-notes">']
	items = list(blocks)
	index = 0
	while index < len(items):
		block = _coerce_block(items[index])
		if block["kind"] == "list_item":
			group = []
			ordered = bool(block.get("ordered"))
			base_level = int(block.get("level", 0))
			while index < len(items):
				candidate = _coerce_block(items[index])
				if candidate["kind"] != "list_item":
					break
				candidate_level = int(candidate.get("level", 0))
				if bool(candidate.get("ordered")) != ordered and candidate_level <= base_level:
					break
				group.append(candidate)
				index += 1
			parts.append(_render_list(group))
			continue
		index += 1
		if block["kind"] == "heading":
			level = min(4, max(2, int(block.get("level", 3))))
			parts.append(f'<h{level}>{html.escape(block["text"])}</h{level}>')
		elif block["kind"] == "table":
			parts.append(block.get("html", ""))
		elif block["kind"] == "paragraph":
			text = html.escape(block["text"])
			if block.get("strong"):
				text = f"<strong>{text}</strong>"
			parts.append(f"<p>{text}</p>")
	parts.append("</article>")
	return "".join(parts)


def _coerce_block(block: dict[str, Any] | tuple) -> dict[str, Any]:
	if isinstance(block, dict):
		return block
	if not block:
		return {"kind": "empty", "text": ""}
	if block[0] == "heading":
		return {"kind": "heading", "text": block[1], "level": block[2]}
	if block[0] in ("para", "paragraph"):
		return {"kind": "paragraph", "text": block[1], "strong": bool(len(block) > 2 and block[2])}
	if block[0] == "table":
		return {"kind": "table", "html": block[1]}
	if block[0] == "list_item":
		return {"kind": "list_item", "text": block[1], "ordered": block[2], "level": block[3]}
	return {"kind": "paragraph", "text": str(block[-1])}


def _render_list(items: list[dict[str, Any]]) -> str:
	if not items:
		return ""
	# Lists in the source are usually flat. Keep nested levels as nested lists;
	# ordered/bullet semantics are resolved from the DOCX numbering definition.
	def render_level(start: int, level: int, ordered: bool) -> tuple[str, int]:
		tag = "ol" if ordered else "ul"
		out = [f"<{tag}>"]
		pos = start
		while pos < len(items):
			item = items[pos]
			item_level = int(item.get("level", 0))
			if item_level < level or (item_level == level and bool(item.get("ordered")) != ordered):
				break
			if item_level > level:
				if out[-1].endswith("</li>"):
					nested, pos = render_level(pos, item_level, bool(item.get("ordered")))
					out[-1] = out[-1][:-5] + nested + "</li>"
					continue
				item_level = level
			text = html.escape(item.get("text", ""))
			out.append(f"<li>{text}</li>")
			pos += 1
		out.append(f"</{tag}>")
		return "".join(out), pos

	result, _ = render_level(0, int(items[0].get("level", 0)), bool(items[0].get("ordered")))
	return result


def render_table_html(table: Table) -> str:
	"""Render a DOCX table with an accessible header row."""
	rows = []
	for row_index, row in enumerate(table.rows):
		cell_tag = "th" if row_index == 0 else "td"
		cells = "".join(
			f"<{cell_tag}>{html.escape(clean_text(cell.text))}</{cell_tag}>" for cell in row.cells
		)
		rows.append(f"<tr>{cells}</tr>")
	if not rows:
		return ""
	return f'<table class="aimatic-notes-table"><thead>{rows[0]}</thead><tbody>{"".join(rows[1:])}</tbody></table>'


class _NotesBlockParser(HTMLParser):
    """Parse canonical notes HTML for API consumers without discarding lists."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, Any]] = []
        self._text: tuple[str, int | None, list[str]] | None = None
        self._lists: list[dict[str, Any]] = []
        self._rows: list[list[str]] | None = None
        self._row: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._text = ("heading", int(tag[1:]), [])
        elif tag == "p":
            self._text = ("paragraph", None, [])
        elif tag in {"ul", "ol"}:
            self._lists.append({"ordered": tag == "ol", "items": []})
        elif tag == "li":
            self._text = ("list_item", None, [])
        elif tag == "table":
            self._rows = []
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
            text = clean_text("".join(parts))
            if text:
                block = {"kind": kind, "text": text}
                if kind == "heading":
                    block["level"] = level
                self.blocks.append(block)
            self._text = None
        elif tag == "li" and self._text:
            text = clean_text("".join(self._text[2]))
            if text and self._lists:
                self._lists[-1]["items"].append(text)
            self._text = None
        elif tag in {"ul", "ol"} and self._lists:
            current = self._lists.pop()
            if current["items"]:
                self.blocks.append({"kind": "list", "ordered": current["ordered"], "items": current["items"]})
        elif tag in {"th", "td"} and self._text:
            text = clean_text("".join(self._text[2]))
            if text and self._row is not None:
                self._row.append(text)
            self._text = None
        elif tag == "tr" and self._row is not None:
            if self._rows is not None and self._row:
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._rows is not None:
            if self._rows:
                self.blocks.append({"kind": "table", "rows": self._rows})
            self._rows = None


def parse_notes_blocks(notes_html: str) -> list[dict[str, Any]]:
    """Return stable semantic blocks for chunked/API note consumers."""
    parser = _NotesBlockParser()
    parser.feed(notes_html or "")
    parser.close()
    return parser.blocks

\n