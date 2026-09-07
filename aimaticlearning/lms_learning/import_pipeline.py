from __future__ import annotations

import re
from pathlib import Path

import frappe
from docx import Document
from frappe import _
from frappe.utils import get_site_path

from aimaticlearning.lms_learning.outline_sync import repair_course_content, sync_profile_outline
from aimaticlearning.lms_learning.protected_notes import render_notes_html

DEFAULT_SOURCE_FILE = "/private/files/BLP notes.docx"
MODULE_TITLE = "Business Law & Practice (BLP)"
COURSE_SHORT = "BLP"


def import_blp_module_from_file(source_file: str | None = None) -> dict:
	path = _resolve_source_path(source_file)
	chapters = _parse_docx_chapters(path)
	course = _ensure_course()
	module_config = _ensure_module_config(course, source_file or DEFAULT_SOURCE_FILE)
	module_assessment_quiz = _ensure_module_assessment_quiz(course, module_config.name)

	created_profiles: list[str] = []
	for idx, chapter in enumerate(chapters, start=1):
		profile = _ensure_chapter_bundle(
			module_config=module_config,
			course=course,
			chapter_index=idx,
			chapter_title=chapter["title"],
			paragraphs=chapter["paragraphs"],
		)
		created_profiles.append(profile.name)

	module_config.import_status = "Structure Imported"
	module_config.chapter_count = len(created_profiles)
	module_config.module_assessment_quiz = module_assessment_quiz
	module_config.save(ignore_permissions=True)

	repair_course_content(course.name)
	frappe.db.commit()
	return {
		"learning_module": module_config.name,
		"course": course.name,
		"chapters": len(created_profiles),
		"chapter_profiles": created_profiles,
		"module_assessment_quiz": module_assessment_quiz,
		"next_steps": [
			"Export chapter source bundle for AI MCQ/flashcard drafting",
			"Import reviewed MCQ JSON (20 per chapter)",
			"Import reviewed flashcard JSON (200 total target)",
			"Build module assessment blueprint (150 MCQs)",
		],
	}


def _resolve_source_path(source_file: str | None) -> Path:
	file_url = source_file or DEFAULT_SOURCE_FILE
	if file_url.startswith("/private/files/"):
		relative = file_url.split("/private/files/", 1)[1]
		return Path(get_site_path("private", "files", relative))
	if file_url.startswith("/files/"):
		relative = file_url.split("/files/", 1)[1]
		return Path(get_site_path("public", "files", relative))
	return Path(file_url)


def _parse_docx_chapters(path: Path) -> list[dict]:
	document = Document(str(path))
	chapters: list[dict] = []
	current: dict | None = None

	for paragraph in document.paragraphs:
		text = (paragraph.text or "").strip()
		if not text:
			continue
		style = paragraph.style.name if paragraph.style else ""
		chapter_match = re.match(r"^Chapter\s+(\d+)\s*[:.\-–]\s*(.+)$", text, re.I)
		if style.startswith("Heading") or chapter_match:
			if current:
				chapters.append(current)
			if chapter_match:
				title = f"Chapter {chapter_match.group(1)}: {chapter_match.group(2).strip()}"
			else:
				title = text
			current = {"title": title, "paragraphs": []}
			continue
		if current is None:
			current = {"title": "Introduction", "paragraphs": []}
		current["paragraphs"].append(text)

	if current:
		chapters.append(current)

	if not chapters:
		frappe.throw(_("No chapters found in source document."))
	return chapters


def _ensure_course() -> frappe.Document:
	existing = frappe.db.get_value("LMS Course", {"title": MODULE_TITLE})
	if existing:
		return frappe.get_doc("LMS Course", existing)

	course = frappe.get_doc(
		{
			"doctype": "LMS Course",
			"title": MODULE_TITLE,
			"short_introduction": "Examic Study BLP learning module with protected notes, chapter MCQs, flashcards, and module assessment.",
			"description": "Business Law & Practice study module imported from approved source notes.",
			"published": 1,
			"upcoming": 0,
			"disable_self_learning": 0,
		}
	)
	course.append("instructors", {"instructor": frappe.session.user or "Administrator"})
	course.insert(ignore_permissions=True)
	return course


def _ensure_module_config(course: frappe.Document, source_file: str) -> frappe.Document:
	existing = frappe.db.get_value("Learning Module Config", {"lms_course": course.name})
	if existing:
		return frappe.get_doc("Learning Module Config", existing)

	file_name = frappe.db.get_value("File", {"file_url": source_file}, "name")
	doc = frappe.get_doc(
		{
			"doctype": "Learning Module Config",
			"title": MODULE_TITLE,
			"lms_course": course.name,
			"source_file": file_name,
			"target_chapter_mcq_count": 20,
			"module_assessment_count": 150,
			"flashcard_target": 200,
			"import_status": "Not Started",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _ensure_module_assessment_quiz(course: frappe.Document, module_name: str) -> str:
	title = f"{COURSE_SHORT} Module Assessment (150 MCQs)"
	existing = frappe.db.get_value("LMS Quiz", {"course": course.name, "title": title})
	if existing:
		return existing

	quiz = frappe.get_doc(
		{
			"doctype": "LMS Quiz",
			"title": title,
			"course": course.name,
			"max_attempts": 3,
			"show_answers": 1,
			"total_marks": 150,
			"passing_percentage": 60,
		}
	)
	quiz.insert(ignore_permissions=True)
	return quiz.name


def _ensure_chapter_bundle(
	module_config: frappe.Document,
	course: frappe.Document,
	chapter_index: int,
	chapter_title: str,
	paragraphs: list[str],
) -> frappe.Document:
	slug = _slugify(chapter_title)
	profile_name = frappe.db.get_value(
		"Learning Chapter Profile",
		{"learning_module": module_config.name, "chapter_title": chapter_title},
	)
	if profile_name:
		profile = frappe.get_doc("Learning Chapter Profile", profile_name)
	else:
		profile = frappe.get_doc(
			{
				"doctype": "Learning Chapter Profile",
				"learning_module": module_config.name,
				"chapter_title": chapter_title,
			}
		)

	chapter_name = profile.course_chapter or _ensure_course_chapter(course, chapter_index, chapter_title)
	lesson_name = profile.notes_lesson or _ensure_notes_lesson(course, chapter_name, chapter_title, slug)
	quiz_name = profile.chapter_quiz or _ensure_chapter_quiz(course, chapter_name, chapter_title)

	profile.course_chapter = chapter_name
	profile.notes_lesson = lesson_name
	profile.chapter_quiz = quiz_name
	profile.notes_html = render_notes_html(paragraphs)
	profile.concept_tags = chapter_title
	if not profile_name:
		profile.insert(ignore_permissions=True)
	else:
		profile.save(ignore_permissions=True)

	_update_lesson_with_notes_link(lesson_name, profile.name, chapter_title)
	sync_profile_outline(profile.name)
	return profile


def _ensure_course_chapter(course: frappe.Document, chapter_index: int, chapter_title: str) -> str:
	title = f"Chapter {chapter_index}: {chapter_title}"
	existing = frappe.db.get_value(
		"Course Chapter",
		{"course": course.name, "title": title},
	)
	if existing:
		return existing

	chapter = frappe.get_doc(
		{
			"doctype": "Course Chapter",
			"title": title,
			"course": course.name,
		}
	)
	chapter.insert(ignore_permissions=True)
	return chapter.name


def _ensure_notes_lesson(
	course: frappe.Document, chapter_name: str, chapter_title: str, slug: str
) -> str:
	title = f"Notes — {chapter_title}"
	existing = frappe.db.get_value(
		"Course Lesson",
		{"course": course.name, "chapter": chapter_name, "title": title},
	)
	if existing:
		return existing

	lesson = frappe.get_doc(
		{
			"doctype": "Course Lesson",
			"title": title,
			"course": course.name,
			"chapter": chapter_name,
			"body": _lesson_markdown_placeholder(chapter_title, slug),
		}
	)
	lesson.insert(ignore_permissions=True)
	return lesson.name


def _ensure_chapter_quiz(course: frappe.Document, chapter_name: str, chapter_title: str) -> str:
	title = f"{chapter_title} — Chapter MCQ"
	existing = frappe.db.get_value(
		"LMS Quiz",
		{"course": course.name, "title": title},
	)
	if existing:
		return existing

	quiz = frappe.get_doc(
		{
			"doctype": "LMS Quiz",
			"title": title,
			"course": course.name,
			"max_attempts": 0,
			"show_answers": 1,
			"total_marks": 0,
			"passing_percentage": 60,
		}
	)
	quiz.insert(ignore_permissions=True)
	return quiz.name


def _lesson_markdown_placeholder(chapter_title: str, slug: str) -> str:
	return (
		f"## {chapter_title}\n\n"
		"Read the protected chapter notes in the portal viewer (download disabled).\n\n"
		f"[Open protected notes](/learning-notes/{slug})"
	)


def _update_lesson_with_notes_link(lesson_name: str, profile_name: str, chapter_title: str) -> None:
	lesson = frappe.get_doc("Course Lesson", lesson_name)
	lesson.body = (
		f"## {chapter_title}\n\n"
		"Protected notes are rendered server-side after enrolment checks.\n\n"
		f"[Open protected notes](/learning-notes/{profile_name})"
	)
	lesson.save(ignore_permissions=True)


def _slugify(value: str) -> str:
	value = value.lower().strip()
	value = re.sub(r"[^a-z0-9]+", "-", value)
	return value.strip("-") or "chapter"
