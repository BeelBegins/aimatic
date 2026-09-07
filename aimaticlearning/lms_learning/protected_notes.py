import html
import re

import frappe
from frappe import _

from aimaticlearning.lms_learning.utils import (
	throw_access_denied,
	user_can_access_chapter_profile,
	user_can_access_course,
)


def render_notes_html(paragraphs: list[str]) -> str:
	parts = []
	for paragraph in paragraphs:
		text = paragraph.strip()
		if not text:
			continue
		parts.append(f"<p>{html.escape(text)}</p>")
	return "\n".join(parts)


def get_notes_for_profile(chapter_profile: str) -> dict:
	if not user_can_access_chapter_profile(chapter_profile):
		throw_access_denied()

	doc = frappe.get_doc("Learning Chapter Profile", chapter_profile)
	return {
		"chapter_profile": doc.name,
		"chapter_title": doc.chapter_title,
		"notes_html": doc.notes_html or "",
		"source_revision": doc.source_revision,
		"concept_tags": doc.concept_tags,
	}
