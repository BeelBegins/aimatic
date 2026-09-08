from __future__ import annotations

import json

import frappe

from aimaticlearning.lms_learning.analytics import build_learning_map
from aimaticlearning.lms_learning.content_generation import (
	build_module_assessment_blueprint,
	export_chapter_source_bundle,
	import_flashcard_drafts,
	import_mcq_drafts,
)
from aimaticlearning.lms_learning.course_presentation import repair_course_presentation
from aimaticlearning.lms_learning.import_pipeline import import_blp_module_from_file
from aimaticlearning.lms_learning.outline_sync import repair_course_content
from aimaticlearning.lms_learning.protected_notes import get_notes_for_profile
from aimaticlearning.lms_learning.revision import (
	build_revision_board,
	encode_recall_tags,
	filter_cards_for_user,
)
from aimaticlearning.lms_learning.utils import (
	throw_access_denied,
	user_can_access_chapter_profile,
	user_can_access_course,
)


@frappe.whitelist()
def get_protected_chapter_notes(chapter_profile: str):
	return get_notes_for_profile(chapter_profile)


@frappe.whitelist()
def get_chapter_notes_chunks(chapter_profile: str):
	"""Return semantic note blocks for chunked in-lesson display."""
	notes = get_notes_for_profile(chapter_profile)
	from aimaticlearning.lms_learning.content_format import parse_notes_blocks
	from aimaticlearning.lms_learning.lesson_macros import notes_paragraphs_from_html

	notes_html = notes.get("notes_html") or ""
	blocks = parse_notes_blocks(notes_html)
	paragraphs = notes_paragraphs_from_html(notes_html)
	return {
		"chapter_profile": chapter_profile,
		"chapter_title": notes.get("chapter_title"),
		"blocks": blocks,
		"paragraphs": paragraphs,
		"total": len(blocks),
		"chunk_size": 3,
	}


@frappe.whitelist()
def get_learning_map(learning_module: str):
	user = frappe.session.user
	course = frappe.db.get_value("Learning Module Config", learning_module, "lms_course")
	if not course or not user_can_access_course(course, user):
		throw_access_denied()
	return build_learning_map(learning_module, user)


@frappe.whitelist()
def get_revision_board(course: str | None = None, learning_module: str | None = None):
	user = frappe.session.user
	if user == "Guest":
		throw_access_denied()
	return build_revision_board(user, course=course, learning_module=learning_module)


@frappe.whitelist()
def record_attempt_detail(
	learning_module: str,
	lms_question: str,
	correct: int | bool,
	time_seconds: float | None = None,
	course_chapter: str | None = None,
	quiz_submission: str | None = None,
	attempt_order: int | None = None,
):
	user = frappe.session.user
	course = frappe.db.get_value("Learning Module Config", learning_module, "lms_course")
	if not course or not user_can_access_course(course, user):
		throw_access_denied()

	meta = frappe.db.get_value(
		"Learning Question Meta",
		{"lms_question": lms_question},
		["concept", "revision", "course_chapter"],
		as_dict=True,
	)
	doc = frappe.get_doc(
		{
			"doctype": "Learning Attempt Detail",
			"user": user,
			"learning_module": learning_module,
			"lms_question": lms_question,
			"correct": 1 if correct else 0,
			"time_seconds": time_seconds,
			"course_chapter": course_chapter or (meta and meta.course_chapter),
			"quiz_submission": quiz_submission,
			"attempt_order": attempt_order,
			"concept_tags": meta and meta.concept,
			"question_revision": meta and meta.revision,
		}
	)
	doc.insert(ignore_permissions=True)
	return {"name": doc.name}


@frappe.whitelist()
def get_flashcard_deck(
	learning_module: str,
	course_chapter: str | None = None,
	limit: int = 25,
	rating_filter: str | None = None,
):
	user = frappe.session.user
	course = frappe.db.get_value("Learning Module Config", learning_module, "lms_course")
	if not course or not user_can_access_course(course, user):
		throw_access_denied()

	filters = {"learning_module": learning_module, "status": "Published"}
	if course_chapter:
		filters["course_chapter"] = course_chapter

	wanted = (rating_filter or "").strip().lower()
	fetch_limit = 500 if wanted and wanted not in ("all", "*") else max(int(limit or 25), 1)
	cards = frappe.get_all(
		"Learning Flashcard",
		filters=filters,
		fields=["name", "front", "back", "concept", "difficulty", "course_chapter"],
		limit_page_length=min(fetch_limit, 500),
		order_by="modified desc",
	)
	cards = filter_cards_for_user(cards, user, learning_module, rating_filter)
	if wanted not in ("hard", "good", "easy", "unreviewed"):
		cards = cards[: max(int(limit or 25), 1)]
	return {"cards": cards, "count": len(cards), "rating_filter": wanted or "all"}


@frappe.whitelist()
def review_flashcard(name: str, rating: str):
	user = frappe.session.user
	card = frappe.get_doc("Learning Flashcard", name)
	course = frappe.db.get_value("Learning Module Config", card.learning_module, "lms_course")
	if not course or not user_can_access_course(course, user):
		throw_access_denied()

	# Rating is stored as a lightweight attempt signal for analytics.
	correct = 1 if rating in ("easy", "good", "known") else 0
	doc = frappe.get_doc(
		{
			"doctype": "Learning Attempt Detail",
			"user": user,
			"learning_module": card.learning_module,
			"learning_flashcard": card.name,
			"correct": correct,
			"course_chapter": card.course_chapter,
			"concept_tags": encode_recall_tags(rating, card.concept),
		}
	)
	doc.insert(ignore_permissions=True)
	return {"ok": True, "name": doc.name, "rating": rating}


@frappe.whitelist()
def repair_blp_course_content():
	frappe.only_for(("System Manager", "LMS Content Reviewer", "Course Creator"))
	return repair_course_content()


@frappe.whitelist()
def configure_student_access():
	frappe.only_for(("System Manager", "Course Creator", "Moderator"))
	from aimaticlearning.lms_learning.enrollment import configure_lms_student_access

	return configure_lms_student_access()


@frappe.whitelist()
def invite_student(email: str, full_name: str | None = None, course: str | None = None):
	from aimaticlearning.lms_learning.enrollment import invite_student_to_course

	return invite_student_to_course(email=email, full_name=full_name, course=course)


@frappe.whitelist()
def bulk_invite(emails: str, course: str | None = None):
	from aimaticlearning.lms_learning.enrollment import bulk_invite_students

	return bulk_invite_students(emails=emails, course=course)


@frappe.whitelist()
def run_blp_import(source_file: str | None = None):
	frappe.only_for(("System Manager", "LMS Content Reviewer", "Course Creator"))
	return import_blp_module_from_file(source_file=source_file)


@frappe.whitelist()
def export_content_bundle(learning_module: str):
	frappe.only_for(("System Manager", "LMS Content Reviewer", "Course Creator"))
	return export_chapter_source_bundle(learning_module)


@frappe.whitelist()
def import_generated_mcq_json(learning_module: str, payload: str):
	frappe.only_for(("System Manager", "LMS Content Reviewer"))
	return import_mcq_drafts(learning_module, json.loads(payload))


@frappe.whitelist()
def import_generated_flashcard_json(learning_module: str, payload: str):
	frappe.only_for(("System Manager", "LMS Content Reviewer"))
	return import_flashcard_drafts(learning_module, json.loads(payload))


@frappe.whitelist()
def import_blp_mcqs(learning_module: str | None = None, limit: int | None = None):
	frappe.only_for(("System Manager", "LMS Content Reviewer", "Course Creator"))
	from aimaticlearning.lms_learning.mcq_import import import_mcqs_for_module

	if not learning_module:
		learning_module = frappe.db.get_value(
			"Learning Module Config", {"lms_course": "business-law-practice-blp"}, "name"
		)
	return import_mcqs_for_module(
		learning_module,
		limit=int(limit) if limit else None,
		per_chapter_target=20,
		module_assessment_target=150,
	)


@frappe.whitelist()
def import_review_mcqs(learning_module: str, source_file: str):
	frappe.only_for(("System Manager", "LMS Content Reviewer", "Course Creator"))
	from aimaticlearning.lms_learning.mcq_import import import_review_mcqs_for_module

	return import_review_mcqs_for_module(learning_module, source_file)


@frappe.whitelist()
def publish_review_mcqs(learning_module: str):
	frappe.only_for(("System Manager", "LMS Content Reviewer", "Course Creator"))
	from aimaticlearning.lms_learning.mcq_import import publish_review_mcqs_for_module

	return publish_review_mcqs_for_module(learning_module)


@frappe.whitelist()
def repair_blp_presentation():
	frappe.only_for(("System Manager", "Course Creator", "LMS Content Reviewer"))
	return repair_course_presentation("business-law-practice-blp")


@frappe.whitelist()
def build_module_assessment(learning_module: str):
	frappe.only_for(("System Manager", "LMS Content Reviewer"))
	return build_module_assessment_blueprint(learning_module)
