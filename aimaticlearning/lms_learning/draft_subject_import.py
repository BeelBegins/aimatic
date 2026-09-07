"""Private-source, unpublished-course importer for the remaining Examic subjects.

This deliberately creates review drafts only. It never creates scored questions,
flashcards, enrolments, or published courses from unreviewed legal source material.
"""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

import frappe
from docx import Document


ARCHIVE_ROOT = "/tmp/examic-archive-710d68f837"
SOURCE_FOLDER = "Home/examic-study"

SUBJECT_SPECS = (
	{
		"title": "Contract Law",
		"course_key": "contract-law-sqe1",
		"notes": "Data for Examic/Contract Law/SQE1 Contract Law Study Guide.docx",
		"questions": "Data for Examic/Contract Law/SQE1 Contract Law Practice Questions.docx",
	},
	{
		"title": "Dispute Resolution",
		"course_key": "dispute-resolution-flk1",
		"notes": "Data for Examic/Dispute Resolution/DR - Notes - FLK1 - SQE.docx",
		"questions": "Data for Examic/Dispute Resolution/Dispute Resolution - Quesiton Bank.docx",
	},
	{
		"title": "Legal Services",
		"course_key": "legal-services-flk1",
		"notes": "Data for Examic/Legal Services/LEGAL SERVICES.docx",
		"questions": None,
	},
	{
		"title": "Public Law",
		"course_key": "public-law-flk1",
		"notes": "Data for Examic/Public Law/Public Law.docx",
		"questions": None,
	},
	{
		"title": "Tort Law",
		"course_key": "tort-law-sqe1",
		"notes": "Data for Examic/Tort Law/Tort Law - STUDY GUIDE.docx",
		"questions": "Data for Examic/Tort Law/Tort Law Questions .docx",
	},
)


def import_remaining_subject_drafts(source_root: str = ARCHIVE_ROOT) -> dict:
	"""Register private sources and create unpublished, review-only subject drafts."""
	root = Path(source_root)
	if not root.is_dir():
		frappe.throw(f"Extracted Examic source directory is unavailable: {root}")

	results = []
	for spec in SUBJECT_SPECS:
		notes_path = root / spec["notes"]
		question_path = root / spec["questions"] if spec["questions"] else None
		if not notes_path.is_file() or (question_path and not question_path.is_file()):
			frappe.throw(f"Required source file is missing for {spec['title']}.")

		notes_file = _register_private_source(notes_path)
		question_file = _register_private_source(question_path) if question_path else None
		chapters = _parse_note_chapters(notes_path)
		course = _ensure_draft_course(spec, question_file is not None)
		module = _ensure_module_config(spec, course, notes_file)
		chapter_count = _sync_draft_chapters(course, module, chapters)
		module.chapter_count = chapter_count
		module.import_status = "Structure Imported"
		module.module_mcq_count = 0
		module.published_flashcard_count = 0
		module.save(ignore_permissions=True)
		results.append(
			{
				"subject": spec["title"],
				"course": course.name,
				"published": int(course.published or 0),
				"notes_source": notes_file.name,
				"question_source": question_file.name if question_file else None,
				"draft_chapters": chapter_count,
			}
		)

	frappe.db.commit()
	return {
		"mode": "review_drafts_only",
		"subjects": results,
		"safeguards": [
			"All Word sources are private Frappe Files.",
			"All subject courses remain unpublished.",
			"No MCQs, flashcards, answer keys, attempts, or enrolments are created.",
		],
	}


def publish_subject_notes_only() -> dict:
	"""Publish supplied note sources only; question banks remain private and unscored."""
	published = []
	for spec in SUBJECT_SPECS:
		course_name = frappe.db.get_value("LMS Course", {"title": spec["title"]})
		if not course_name:
			frappe.throw(f"Draft course is missing for {spec['title']}.")
		course = frappe.get_doc("LMS Course", course_name)
		if not course.chapters:
			frappe.throw(f"Draft course has no structured chapters: {spec['title']}.")
		course.published = 1
		course.disable_self_learning = 0
		course.short_introduction = "Structured SQE study notes, organised into focused lessons."
		course.description = (
			"<p>Work through structured study notes in order and return to the lesson you last studied. "
			"Practice questions and flashcards will appear only when their answers and explanations are ready.</p>"
		)
		course.save(ignore_permissions=True)
		for lesson_name in frappe.get_all("Course Lesson", filters={"course": course.name}, pluck="name"):
			lesson = frappe.get_doc("Course Lesson", lesson_name)
			lesson.body = (lesson.body or "").replace(
				"<p><strong>Internal review draft — not published to students.</strong></p>", ""
			)
			lesson.save(ignore_permissions=True)
		module_name = frappe.db.get_value("Learning Module Config", {"lms_course": course.name}, "name")
		if module_name:
			module = frappe.get_doc("Learning Module Config", module_name)
			module.import_status = "Published"
			module.save(ignore_permissions=True)
		published.append({"title": course.title, "course": course.name, "chapters": len(course.chapters or [])})
	frappe.db.commit()
	return {
		"mode": "published_notes_only",
		"courses": published,
		"safeguards": [
			"No MCQs, flashcards, mocks, answer keys, learner attempts, or enrolments were created.",
			"Question-bank Word documents remain private and are not linked from student lessons.",
		],
	}


def _register_private_source(path: Path):
	content = path.read_bytes()
	content_hash = hashlib.md5(content).hexdigest()
	existing = frappe.db.get_value(
		"File", {"content_hash": content_hash, "folder": SOURCE_FOLDER, "is_private": 1}, "name"
	)
	if existing:
		return frappe.get_doc("File", existing)
	return frappe.get_doc(
		{
			"doctype": "File",
			"file_name": path.name,
			"content": content,
			"folder": SOURCE_FOLDER,
			"is_private": 1,
		}
	).insert(ignore_permissions=True)


def _ensure_draft_course(spec: dict, has_question_bank: bool):
	course_name = frappe.db.get_value("LMS Course", {"title": spec["title"]})
	if course_name:
		course = frappe.get_doc("LMS Course", course_name)
	else:
		course = frappe.get_doc(
			{
				"doctype": "LMS Course",
				"title": spec["title"],
				"short_introduction": "Review draft — not available to students.",
				"description": "<p><strong>Internal review draft.</strong> This course is not available to students.</p>",
				"published": 0,
				"upcoming": 0,
				"disable_self_learning": 1,
			}
		)
		course.append("instructors", {"instructor": frappe.session.user or "Administrator"})
		course.insert(ignore_permissions=True)

	course.published = 0
	course.disable_self_learning = 1
	question_note = " A private question-bank source is attached for answer-key review." if has_question_bank else " No question bank was supplied."
	course.description = (
		"<p><strong>Internal review draft.</strong> This course is not available to students. "
		"The source notes have been structured for legal-content review before publication."
		+ question_note
		+ "</p>"
	)
	course.save(ignore_permissions=True)
	return course


def _ensure_module_config(spec: dict, course, notes_file):
	module_name = frappe.db.get_value("Learning Module Config", {"lms_course": course.name}, "name")
	if module_name:
		module = frappe.get_doc("Learning Module Config", module_name)
	else:
		module = frappe.get_doc(
			{
				"doctype": "Learning Module Config",
				"title": f"{spec['title']} review draft",
				"lms_course": course.name,
				"target_chapter_mcq_count": 0,
				"module_assessment_count": 0,
				"flashcard_target": 0,
			}
		).insert(ignore_permissions=True)
	module.source_file = notes_file.name
	return module


def _sync_draft_chapters(course, module, chapters: list[dict]) -> int:
	linked_chapters = []
	for index, chapter_data in enumerate(chapters, start=1):
		title = chapter_data["title"]
		chapter_name = frappe.db.get_value("Course Chapter", {"course": course.name, "title": title}, "name")
		if chapter_name:
			chapter = frappe.get_doc("Course Chapter", chapter_name)
		else:
			chapter = frappe.get_doc({"doctype": "Course Chapter", "course": course.name, "title": title})
			chapter.insert(ignore_permissions=True)
		chapter.idx = index
		chapter.save(ignore_permissions=True)

		profile_name = frappe.db.get_value(
			"Learning Chapter Profile", {"learning_module": module.name, "chapter_title": title}, "name"
		)
		profile = frappe.get_doc("Learning Chapter Profile", profile_name) if profile_name else frappe.get_doc(
			{"doctype": "Learning Chapter Profile", "learning_module": module.name, "chapter_title": title}
		)
		lesson_title = f"Draft notes — {title}"
		lesson_name = frappe.db.get_value(
			"Course Lesson", {"course": course.name, "chapter": chapter.name, "title": lesson_title}, "name"
		)
		lesson = frappe.get_doc("Course Lesson", lesson_name) if lesson_name else frappe.get_doc(
			{"doctype": "Course Lesson", "course": course.name, "chapter": chapter.name, "title": lesson_title}
		)
		notes_html = _render_notes(chapter_data["paragraphs"])
		lesson.body = "<p><strong>Internal review draft — not published to students.</strong></p>" + notes_html
		if lesson_name:
			lesson.save(ignore_permissions=True)
		else:
			lesson.insert(ignore_permissions=True)
		profile.course_chapter = chapter.name
		profile.notes_lesson = lesson.name
		profile.chapter_quiz = None
		profile.concept_tags = title
		profile.notes_html = notes_html
		if profile_name:
			profile.save(ignore_permissions=True)
		else:
			profile.insert(ignore_permissions=True)
		linked_chapters.append(chapter.name)

	_remove_stale_draft_content(course, module, set(linked_chapters))
	course.reload()
	course.set("chapters", [])
	for index, chapter_name in enumerate(linked_chapters, start=1):
		course.append("chapters", {"chapter": chapter_name, "idx": index})
	course.save(ignore_permissions=True)
	return len(linked_chapters)


def _remove_stale_draft_content(course, module, linked_chapters: set[str]) -> None:
	"""Delete only orphaned records from an unpublished review draft on resync."""
	if course.published:
		frappe.throw("Draft cleanup cannot run against a published course.")
	for stale_chapter in frappe.get_all("Course Chapter", filters={"course": course.name}, pluck="name"):
		if stale_chapter in linked_chapters:
			continue
		for profile_name in frappe.get_all(
			"Learning Chapter Profile",
			filters={"learning_module": module.name, "course_chapter": stale_chapter},
			pluck="name",
		):
			frappe.delete_doc("Learning Chapter Profile", profile_name, ignore_permissions=True, force=True)
		for lesson_name in frappe.get_all(
			"Course Lesson", filters={"course": course.name, "chapter": stale_chapter}, pluck="name"
		):
			frappe.delete_doc("Course Lesson", lesson_name, ignore_permissions=True, force=True)
		frappe.delete_doc("Course Chapter", stale_chapter, ignore_permissions=True, force=True)


def _parse_note_chapters(path: Path) -> list[dict]:
	document = Document(str(path))
	chapters: list[dict] = []
	current = None
	in_table_of_contents = False
	toc_numbers: set[int] = set()
	toc_content_lines = 0
	last_chapter_number = 0
	for paragraph in document.paragraphs:
		text = (paragraph.text or "").strip()
		if not text:
			continue
		if "table of contents" in text.lower():
			in_table_of_contents = True
			continue
		style = paragraph.style.name if paragraph.style else ""
		number_match = re.match(r"^chapter\s+(\d+)\s*[:.\-–]", text, re.I)
		is_chapter = style == "Heading 1" or bool(number_match)
		if in_table_of_contents and not is_chapter:
			toc_content_lines += 1
		if is_chapter:
			chapter_number = int(number_match.group(1)) if number_match else None
			if in_table_of_contents and chapter_number is not None:
				if (not toc_numbers and toc_content_lines >= 3) or chapter_number in toc_numbers:
					in_table_of_contents = False
				else:
					toc_numbers.add(chapter_number)
					continue
			if chapter_number is not None:
				if last_chapter_number and chapter_number <= last_chapter_number:
					break
				last_chapter_number = chapter_number
			if current:
				chapters.append(current)
			current = {"title": _normalise_chapter_title(text), "paragraphs": []}
			continue
		if current is not None:
			current["paragraphs"].append((text, style))
	if current:
		chapters.append(current)
	if not chapters:
		chapters = [{"title": "Source review", "paragraphs": [((p.text or "").strip(), p.style.name if p.style else "") for p in document.paragraphs if (p.text or "").strip()]}]
	return chapters


def _normalise_chapter_title(value: str) -> str:
	return re.sub(r"\s+", " ", value).strip().title() if value.isupper() else value


def _render_notes(paragraphs: list[tuple[str, str]]) -> str:
	parts = []
	for text, style in paragraphs:
		escaped = html.escape(text)
		if style.startswith("Heading"):
			level = min(4, max(3, int(style.rsplit(" ", 1)[-1]) + 1)) if style.rsplit(" ", 1)[-1].isdigit() else 3
			parts.append(f"<h{level}>{escaped}</h{level}>")
		else:
			parts.append(f"<p>{escaped}</p>")
	return "".join(parts)
