from __future__ import annotations

import re

import frappe
from frappe import _

from aimaticlearning.lms_learning.lesson_macros import chapter_hub_renderer
from aimaticlearning.lms_learning.outline_sync import (
	link_chapter_to_course,
	link_lesson_to_chapter,
)
from aimaticlearning.lms_learning.protected_notes import render_notes_html

CHAPTER_NUM_RE = re.compile(r"Chapter\s+(\d+)\s*:", re.I)
INTRO_MAX_PARAGRAPHS = 8
INTRO_MAX_BYTES = 12000
MEGA_NOTES_BYTES = 50000


def repair_course_presentation(course_name: str | None = None) -> dict:
	"""Fix chapter order, trim broken intro blob, short lesson bodies, flashcard lessons."""
	if not course_name:
		course_name = frappe.db.get_value(
			"Learning Module Config",
			{"title": "Business Law & Practice (BLP)"},
			"lms_course",
		)
	if not course_name:
		frappe.throw(_("BLP course not found."))

	module_name = frappe.db.get_value("Learning Module Config", {"lms_course": course_name}, "name")
	profiles = frappe.get_all(
		"Learning Chapter Profile",
		filters={"learning_module": module_name},
		fields=["name", "chapter_title", "course_chapter", "notes_lesson", "chapter_quiz", "notes_html"],
		order_by="creation asc",
	)

	fixed_intro = _repair_introduction_blob(profiles)
	reordered = _reorder_course_chapters(course_name, profiles)
	hub_lessons = 0

	for profile in profiles:
		doc = frappe.get_doc("Learning Chapter Profile", profile.name)
		if _consolidate_chapter_hub_lesson(doc, course_name):
			hub_lessons += 1

	flashcards_seeded = _seed_chapter_flashcards(module_name, limit_chapters=8, per_chapter=6)

	_course = frappe.get_doc("LMS Course", course_name)
	_course.short_introduction = (
		"SQE Business Law & Practice — study notes, chapter MCQs, flashcards, and module assessment."
	)
	_course.description = (
		"<p>One lesson per chapter with tabs: <strong>Study notes</strong>, "
		"<strong>Practice MCQs</strong>, and <strong>Flashcards</strong>. "
		"Module assessment is in the final chapter.</p>"
	)
	_course.save(ignore_permissions=True)

	frappe.db.commit()
	return {
		"course": course_name,
		"fixed_intro": fixed_intro,
		"reordered_chapters": reordered,
		"hub_lessons": hub_lessons,
		"flashcards_seeded": flashcards_seeded,
	}


def _repair_introduction_blob(profiles: list[dict]) -> bool:
	for row in profiles:
		if row.chapter_title.strip().lower() != "introduction":
			continue
		html = row.notes_html or ""
		if len(html) <= MEGA_NOTES_BYTES:
			return False
		welcome = render_notes_html(
			[
				"Welcome to Business Law & Practice (BLP) on Examic Study.",
				"This module covers UK tax and VAT topics aligned to your SQE study path.",
				"Open each chapter in order: read the protected study notes, complete practice MCQs, "
				"then review flashcards before moving on.",
				"The full company-law question bank is linked separately in MCQ sections — "
				"it is not part of this introductory chapter.",
			]
		)
		frappe.db.set_value("Learning Chapter Profile", row.name, "notes_html", welcome)
		return True
	return False


def _profile_sort_key(profile: dict) -> tuple[int, str]:
	title = profile.get("chapter_title") or ""
	chapter_name = profile.get("course_chapter")
	if chapter_name:
		chapter_title = frappe.db.get_value("Course Chapter", chapter_name, "title") or title
		match = CHAPTER_NUM_RE.search(chapter_title)
		if match:
			return (int(match.group(1)), chapter_title.lower())
	return _chapter_sort_key(profile)


def _chapter_sort_key(profile: dict) -> tuple[int, str]:
	title = profile.get("chapter_title") or ""
	match = CHAPTER_NUM_RE.search(title)
	if match:
		return (int(match.group(1)), title.lower())
	if title.lower() == "introduction":
		return (0, title.lower())
	return (999, title.lower())


def _reorder_course_chapters(course_name: str, profiles: list[dict]) -> int:
	ordered_profiles = sorted(profiles, key=_profile_sort_key)
	course = frappe.get_doc("LMS Course", course_name)
	course.set("chapters", [])

	for idx, profile in enumerate(ordered_profiles, start=1):
		chapter_name = profile.course_chapter
		if not chapter_name:
			continue
		frappe.db.set_value("Course Chapter", chapter_name, "idx", idx)
		link_chapter_to_course(course_name, chapter_name)
		course.append("chapters", {"chapter": chapter_name, "idx": idx})

	course.save(ignore_permissions=True)
	return len(ordered_profiles)


def _consolidate_chapter_hub_lesson(profile: frappe.Document, course_name: str) -> bool:
	"""One lesson per chapter: tabbed notes / MCQs / flashcards inline (Kinnu-style)."""
	if not profile.course_chapter:
		return False

	chapter_title = frappe.db.get_value("Course Chapter", profile.course_chapter, "title") or profile.chapter_title
	lesson_title = chapter_title if chapter_title.lower().startswith("chapter") else f"Chapter — {profile.chapter_title}"
	hub_html = chapter_hub_renderer(profile.name)
	# LMS lesson body does not run Aimatic macros — store collapsed HTML as one block.
	hub_body = hub_html.replace("\n\n", " ").replace("\n", " ").strip()

	lesson_name = profile.notes_lesson
	if lesson_name:
		lesson = frappe.get_doc("Course Lesson", lesson_name)
	else:
		lesson = frappe.get_doc(
			{
				"doctype": "Course Lesson",
				"course": course_name,
				"chapter": profile.course_chapter,
			}
		)
		lesson.insert(ignore_permissions=True)
		lesson_name = lesson.name
		frappe.db.set_value("Learning Chapter Profile", profile.name, "notes_lesson", lesson_name)

	lesson.title = lesson_title
	lesson.body = hub_body
	lesson.content = ""
	lesson.quiz_id = ""
	lesson.save(ignore_permissions=True)

	chapter = frappe.get_doc("Course Chapter", profile.course_chapter)
	lesson_rows = [{"lesson": lesson_name, "idx": 1}]
	assessment_lessons = frappe.get_all(
		"Course Lesson",
		filters={
			"course": course_name,
			"chapter": profile.course_chapter,
			"title": ["like", "%Module Assessment%"],
		},
		pluck="name",
	)
	for extra_name in assessment_lessons:
		if extra_name != lesson_name:
			lesson_rows.append({"lesson": extra_name, "idx": len(lesson_rows) + 1})

	chapter.set("lessons", lesson_rows)
	chapter.save(ignore_permissions=True)
	frappe.db.set_value("Course Lesson", lesson_name, "idx", 1)
	return True


def _lesson_rename(lesson_name: str, title: str) -> None:
	frappe.db.set_value("Course Lesson", lesson_name, "title", title)


def _find_quiz_lesson(course_name: str, chapter_name: str, quiz_id: str) -> str | None:
	return frappe.db.get_value(
		"Course Lesson",
		{"course": course_name, "chapter": chapter_name, "quiz_id": quiz_id},
		"name",
	)


def _ensure_flashcard_lesson(profile: frappe.Document, course_name: str) -> str | None:
	if not profile.course_chapter:
		return None

	title = f"Flashcards — {profile.chapter_title}"
	existing = frappe.db.get_value(
		"Course Lesson",
		{"course": course_name, "chapter": profile.course_chapter, "title": title},
		"name",
	)
	link = f"/learning-flashcards?chapter_profile={profile.name}"
	body = (
		f"## Flashcards\n\n"
		f"Review key concepts for **{profile.chapter_title}**.\n\n"
		f"[Open flashcard deck →]({link})"
	)
	content = build_editorjs_content(
		title,
		[
			f"Review memory cards for {profile.chapter_title}. "
			"Rate each card to track your revision."
		],
	)

	if existing:
		lesson = frappe.get_doc("Course Lesson", existing)
		lesson.body = body
		lesson.content = content
		lesson.save(ignore_permissions=True)
		lesson_name = existing
	else:
		lesson = frappe.get_doc(
			{
				"doctype": "Course Lesson",
				"title": title,
				"course": course_name,
				"chapter": profile.course_chapter,
				"body": body,
				"content": content,
			}
		)
		lesson.insert(ignore_permissions=True)
		lesson_name = lesson.name

	link_lesson_to_chapter(profile.course_chapter, lesson_name)
	return lesson_name


def _seed_chapter_flashcards(
	learning_module: str, limit_chapters: int = 8, per_chapter: int = 6
) -> int:
	"""Seed published flashcards from chapter note paragraphs (skip mega intro)."""
	profiles = frappe.get_all(
		"Learning Chapter Profile",
		filters={"learning_module": learning_module},
		fields=["name", "chapter_title", "course_chapter", "notes_html"],
		order_by="creation asc",
	)
	created = 0
	used_chapters = 0

	for profile in profiles:
		if used_chapters >= limit_chapters:
			break
		html = profile.notes_html or ""
		if len(html) > MEGA_NOTES_BYTES or len(html) < 200:
			continue

		paragraphs = re.findall(r"<p>(.*?)</p>", html, flags=re.DOTALL)
		paragraphs = [re.sub(r"<[^>]+>", "", p).strip() for p in paragraphs]
		paragraphs = [p for p in paragraphs if len(p) > 40][:per_chapter]
		if not paragraphs:
			continue

		for i, paragraph in enumerate(paragraphs, start=1):
			front = paragraph[:120].rsplit(" ", 1)[0] + "…" if len(paragraph) > 120 else paragraph
			back = paragraph
			source = f"{profile.chapter_title} para {i}"
			existing = frappe.db.exists(
				"Learning Flashcard",
				{
					"learning_module": learning_module,
					"course_chapter": profile.course_chapter,
					"source_reference": source,
				},
			)
			if existing:
				continue
			frappe.get_doc(
				{
					"doctype": "Learning Flashcard",
					"learning_module": learning_module,
					"course_chapter": profile.course_chapter,
					"concept": profile.chapter_title,
					"front": front,
					"back": back,
					"difficulty": "Medium",
					"source_reference": source,
					"status": "Published",
					"ai_generated": 0,
				}
			).insert(ignore_permissions=True)
			created += 1

		used_chapters += 1

	return created


def _reorder_chapter_lessons(chapter_name: str, course_name: str) -> None:
	"""Notes → MCQs → Flashcards within each chapter."""
	if not chapter_name:
		return
	lessons = frappe.get_all(
		"Course Lesson",
		filters={"course": course_name, "chapter": chapter_name},
		fields=["name", "title", "quiz_id"],
	)
	order = {0: [], 1: [], 2: []}
	for lesson in lessons:
		title = (lesson.title or "").lower()
		if lesson.quiz_id:
			order[1].append(lesson.name)
		elif "flashcard" in title:
			order[2].append(lesson.name)
		else:
			order[0].append(lesson.name)

	chapter = frappe.get_doc("Course Chapter", chapter_name)
	chapter.set("lessons", [])
	idx = 1
	for bucket in (0, 1, 2):
		for lesson_name in order[bucket]:
			chapter.append("lessons", {"lesson": lesson_name, "idx": idx})
			frappe.db.set_value("Course Lesson", lesson_name, "idx", idx)
			idx += 1
	chapter.save(ignore_permissions=True)
