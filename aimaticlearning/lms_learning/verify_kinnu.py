from __future__ import annotations

import frappe

from aimaticlearning.lms_learning.kinnu_course import CHAPTER_SPECS, COURSE_NAME, MODULE_NAME


def verify_live_blp(course_name: str = COURSE_NAME, learning_module: str = MODULE_NAME) -> dict:
	course = frappe.get_doc("LMS Course", course_name)
	chapter_names = [row.chapter for row in course.chapters]
	chapter_rows = []

	for spec, chapter_name in zip(CHAPTER_SPECS, chapter_names):
		chapter = frappe.get_doc("Course Chapter", chapter_name)
		lessons = [frappe.get_doc("Course Lesson", row.lesson) for row in chapter.lessons]
		profile_name = frappe.db.get_value(
			"Learning Chapter Profile",
			{"learning_module": learning_module, "course_chapter": chapter_name},
		)
		profile = frappe.get_doc("Learning Chapter Profile", profile_name)
		quiz = frappe.get_doc("LMS Quiz", profile.chapter_quiz)
		chapter_rows.append(
			{
				"chapter": chapter.title,
				"lesson_titles": [lesson.title for lesson in lessons],
				"topic_lessons": sum(1 for lesson in lessons if "data-aimatic-topic-note" in (lesson.body or "")),
				"practice_quiz_lessons": sum(1 for lesson in lessons if lesson.quiz_id == profile.chapter_quiz),
				"mcqs": len(quiz.questions or []),
				"published_flashcards": frappe.db.count(
					"Learning Flashcard",
					{"learning_module": learning_module, "course_chapter": chapter_name, "status": "Published"},
				),
			}
		)

	mock_quiz = frappe.get_doc("LMS Quiz", frappe.db.get_value("Learning Module Config", learning_module, "module_assessment_quiz"))
	return {
		"course_title": course.title,
		"chapter_count": len(chapter_names),
		"chapter_titles_match": [row["chapter"] for row in chapter_rows] == [spec.title for spec in CHAPTER_SPECS],
		"chapters": chapter_rows,
		"published_flashcards": frappe.db.count(
			"Learning Flashcard", {"learning_module": learning_module, "status": "Published"}
		),
		"mock_mcqs": len(mock_quiz.questions or []),
	}
