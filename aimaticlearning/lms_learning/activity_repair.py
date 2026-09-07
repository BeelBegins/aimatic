from __future__ import annotations

import frappe

from aimaticlearning.lms_learning.kinnu_course import (
	COURSE_NAME,
	MOCK_LESSON_BODY,
	MODULE_NAME,
	PRACTICE_LESSON_BODY,
	_flashcard_lesson_html,
)


def repair_blp_activity_lessons(
	course_name: str = COURSE_NAME,
	learning_module: str = MODULE_NAME,
) -> dict:
	"""Repair existing BLP activity shells without rebuilding their learning content."""
	module = frappe.get_doc("Learning Module Config", learning_module)
	course = frappe.get_doc("LMS Course", course_name)
	repaired_quizzes = []
	repaired_flashcards = []

	for chapter_row in course.chapters:
		profile_name = frappe.db.get_value(
			"Learning Chapter Profile",
			{"learning_module": learning_module, "course_chapter": chapter_row.chapter},
		)
		if not profile_name:
			continue
		profile = frappe.get_doc("Learning Chapter Profile", profile_name)
		practice_name = frappe.db.get_value(
			"Course Lesson",
			{"course": course_name, "chapter": chapter_row.chapter, "quiz_id": profile.chapter_quiz},
		)
		if practice_name:
			practice = frappe.get_doc("Course Lesson", practice_name)
			practice.body = PRACTICE_LESSON_BODY
			practice.content = ""
			practice.save(ignore_permissions=True)
			repaired_quizzes.append(practice.name)

		flashcard_name = frappe.db.get_value(
			"Course Lesson",
			{"course": course_name, "chapter": chapter_row.chapter, "title": "Flashcards"},
		)
		if flashcard_name:
			flashcards = frappe.get_doc("Course Lesson", flashcard_name)
			flashcards.body = _flashcard_lesson_html(profile)
			flashcards.content = ""
			flashcards.quiz_id = ""
			flashcards.save(ignore_permissions=True)
			repaired_flashcards.append(flashcards.name)

	if module.module_assessment_quiz:
		mock_name = frappe.db.get_value(
			"Course Lesson", {"course": course_name, "quiz_id": module.module_assessment_quiz}
		)
		if mock_name:
			mock = frappe.get_doc("Course Lesson", mock_name)
			mock.body = MOCK_LESSON_BODY
			mock.content = ""
			mock.save(ignore_permissions=True)
			repaired_quizzes.append(mock.name)

	frappe.db.commit()
	return {
		"course": course_name,
		"quiz_lessons": repaired_quizzes,
		"flashcard_lessons": repaired_flashcards,
		"quiz_lesson_count": len(repaired_quizzes),
		"flashcard_lesson_count": len(repaired_flashcards),
	}
