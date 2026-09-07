from __future__ import annotations

import html
import json
import random
import re
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import get_site_path, now_datetime


def export_chapter_source_bundle(learning_module: str) -> dict:
	module = frappe.get_doc("Learning Module Config", learning_module)
	chapters = frappe.get_all(
		"Learning Chapter Profile",
		filters={"learning_module": learning_module},
		fields=["name", "chapter_title", "course_chapter", "notes_html", "concept_tags"],
		order_by="creation asc",
	)
	bundle = {
		"learning_module": learning_module,
		"course": module.lms_course,
		"exported_at": str(now_datetime()),
		"targets": {
			"chapter_mcq_per_chapter": module.target_chapter_mcq_count,
			"module_assessment_mcq": module.module_assessment_count,
			"flashcards": module.flashcard_target,
		},
		"chapters": chapters,
		"ai_prompts": {
			"chapter_mcq": _chapter_mcq_prompt(),
			"flashcards": _flashcard_prompt(),
		},
		"source_policy": (
			"Use only the supplied chapter notes_html. Do not add outside legal knowledge, "
			"jurisdictional assumptions, or model memory."
		),
	}
	site_path = Path(get_site_path("private", "files", "lms_learning_exports"))
	site_path.mkdir(parents=True, exist_ok=True)
	outfile = site_path / f"{learning_module}-source-bundle.json"
	outfile.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
	return {
		"path": str(outfile),
		"chapter_count": len(chapters),
		"download_hint": f"/private/files/lms_learning_exports/{outfile.name}",
	}


def import_mcq_drafts(learning_module: str, payload: dict | list) -> dict:
	module = frappe.get_doc("Learning Module Config", learning_module)
	items = payload if isinstance(payload, list) else payload.get("questions", [])
	created = 0
	linked = 0

	for item in items:
		chapter_profile = item.get("chapter_profile")
		course_chapter = item.get("course_chapter")
		if chapter_profile and not course_chapter:
			course_chapter = frappe.db.get_value(
				"Learning Chapter Profile", chapter_profile, "course_chapter"
			)
		question_name = _upsert_lms_question(item)
		meta_name = frappe.db.get_value("Learning Question Meta", {"lms_question": question_name})
		meta_fields = {
			"doctype": "Learning Question Meta",
			"lms_question": question_name,
			"learning_module": learning_module,
			"course_chapter": course_chapter,
			"concept": item.get("concept"),
			"learning_objective": item.get("learning_objective"),
			"difficulty": item.get("difficulty") or "Medium",
			"question_role": item.get("question_role") or "Chapter MCQ",
			"source_reference": item.get("source_reference"),
			"revision": item.get("revision") or 1,
			"ai_generated": 1 if item.get("ai_generated") else 0,
		}
		if meta_name:
			meta = frappe.get_doc("Learning Question Meta", meta_name)
			meta.update(meta_fields)
			meta.save(ignore_permissions=True)
		else:
			frappe.get_doc(meta_fields).insert(ignore_permissions=True)
			created += 1

		quiz = _chapter_quiz_for_profile(chapter_profile, learning_module, course_chapter)
		if quiz:
			_link_question_to_quiz(quiz, question_name)
			linked += 1

	module.refresh_counts()
	module.save(ignore_permissions=True)
	frappe.db.commit()
	return {"created_meta": created, "linked_to_quizzes": linked}


def import_flashcard_drafts(learning_module: str, payload: dict | list) -> dict:
	items = payload if isinstance(payload, list) else payload.get("flashcards", [])
	created = 0
	for item in items:
		chapter_profile = item.get("chapter_profile")
		course_chapter = item.get("course_chapter")
		profile = _resolve_flashcard_profile(learning_module, chapter_profile, course_chapter)
		course_chapter = profile.course_chapter
		front = (item.get("front") or "").strip()
		back = (item.get("back") or "").strip()
		source_reference = (item.get("source_reference") or "").strip()
		source_quote = (item.get("source_quote") or "").strip()
		if not front or not back:
			frappe.throw(_("Each flashcard requires a non-empty front and back."))
		_validate_flashcard_source(profile, source_reference, source_quote)
		doc = frappe.get_doc(
			{
				"doctype": "Learning Flashcard",
				"learning_module": learning_module,
				"course_chapter": course_chapter,
				"concept": item.get("concept"),
				"front": front,
				"back": back,
				"difficulty": item.get("difficulty") or "Medium",
				"source_reference": source_reference,
				"revision": item.get("revision") or 1,
				"ai_generated": 1 if item.get("ai_generated") else 0,
				"status": item.get("status") or "Under Review",
			}
		)
		doc.insert(ignore_permissions=True)
		created += 1

	module = frappe.get_doc("Learning Module Config", learning_module)
	module.refresh_counts()
	module.save(ignore_permissions=True)
	frappe.db.commit()
	return {"created": created}


def _resolve_flashcard_profile(
	learning_module: str, chapter_profile: str | None, course_chapter: str | None
):
	filters = {"learning_module": learning_module}
	if chapter_profile:
		filters["name"] = chapter_profile
	elif course_chapter:
		filters["course_chapter"] = course_chapter
	else:
		frappe.throw(_("Each flashcard requires a chapter profile or course chapter."))

	profile = frappe.db.get_value(
		"Learning Chapter Profile",
		filters,
		["name", "course_chapter", "notes_html"],
		as_dict=True,
	)
	if not profile or not profile.course_chapter:
		frappe.throw(_("Each flashcard must resolve to an approved chapter with notes."))
	return profile


def _validate_flashcard_source(profile, source_reference: str, source_quote: str) -> None:
	if not source_reference:
		frappe.throw(_("Each flashcard requires a source reference."))
	quote = _normalise_source_text(source_quote)
	notes = _normalise_source_text(profile.notes_html, strip_html=True)
	if not quote or quote not in notes:
		frappe.throw(_("Flashcard source quote must be copied from the approved chapter notes."))


def _normalise_source_text(value: str | None, strip_html: bool = False) -> str:
	text = html.unescape(value or "")
	if strip_html:
		text = re.sub(r"<[^>]*>", " ", text)
	return re.sub(r"\s+", " ", text).strip()


def build_module_assessment_blueprint(learning_module: str) -> dict:
	module = frappe.get_doc("Learning Module Config", learning_module)
	target = int(module.module_assessment_count or 150)
	quiz_name = module.module_assessment_quiz
	if not quiz_name:
		frappe.throw(_("Module assessment quiz is not configured."))

	pool = frappe.get_all(
		"Learning Question Meta",
		filters={"learning_module": learning_module, "question_role": "Chapter MCQ"},
		fields=["lms_question", "course_chapter", "concept", "difficulty"],
	)
	if not pool:
		frappe.throw(_("No chapter MCQs found. Import chapter MCQs first."))

	# Deterministic spread: sample evenly across chapters without silent dedup beyond question identity.
	by_chapter: dict[str, list] = {}
	for row in pool:
		by_chapter.setdefault(row.course_chapter or "general", []).append(row)

	selected: list[str] = []
	chapter_keys = sorted(by_chapter.keys())
	per_chapter = max(1, target // max(len(chapter_keys), 1))
	for key in chapter_keys:
		rows = by_chapter[key]
		random.shuffle(rows)
		for row in rows[:per_chapter]:
			if row.lms_question not in selected:
				selected.append(row.lms_question)
			if len(selected) >= target:
				break
		if len(selected) >= target:
			break

	if len(selected) < target:
		for row in pool:
			if row.lms_question not in selected:
				selected.append(row.lms_question)
			if len(selected) >= target:
				break

	quiz = frappe.get_doc("LMS Quiz", quiz_name)
	quiz.set("questions", [])
	for idx, question in enumerate(selected, start=1):
		quiz.append("questions", {"question": question, "marks": 1})
		existing_meta = frappe.db.get_value("Learning Question Meta", {"lms_question": question})
		if existing_meta:
			meta = frappe.get_doc("Learning Question Meta", existing_meta)
			if meta.question_role == "Chapter MCQ":
				meta.question_role = "Both"
			meta.save(ignore_permissions=True)
		else:
			frappe.get_doc(
				{
					"doctype": "Learning Question Meta",
					"lms_question": question,
					"learning_module": learning_module,
					"question_role": "Module Assessment",
				}
			).insert(ignore_permissions=True)

	quiz.total_marks = len(selected)
	quiz.save(ignore_permissions=True)
	module.module_mcq_count = len(selected)
	module.import_status = "Content Ready"
	module.save(ignore_permissions=True)
	frappe.db.commit()
	return {
		"quiz": quiz_name,
		"selected_count": len(selected),
		"target": target,
		"chapter_spread": {k: len(v) for k, v in by_chapter.items()},
	}


def _upsert_lms_question(item: dict) -> str:
	question_text = item.get("question")
	if not question_text:
		frappe.throw(_("Each MCQ requires a question field."))

	existing = frappe.db.get_value("LMS Question", {"question": question_text}, "name")
	fields = {
		"doctype": "LMS Question",
		"question": question_text,
		"type": "Choices",
		"multiple": 0,
	}
	for idx in range(1, 5):
		opt = item.get(f"option_{idx}")
		if opt:
			fields[f"option_{idx}"] = opt
			fields[f"is_correct_{idx}"] = 1 if item.get("correct_option") == idx else 0
			if item.get(f"explanation_{idx}"):
				fields[f"explanation_{idx}"] = item.get(f"explanation_{idx}")
		elif item.get("options") and len(item["options"]) >= idx:
			fields[f"option_{idx}"] = item["options"][idx - 1]["text"]
			fields[f"is_correct_{idx}"] = 1 if item["options"][idx - 1].get("is_correct") else 0
			if item["options"][idx - 1].get("explanation"):
				fields[f"explanation_{idx}"] = item["options"][idx - 1]["explanation"]

	if existing:
		doc = frappe.get_doc("LMS Question", existing)
		doc.update(fields)
		doc.save(ignore_permissions=True)
		return doc.name

	doc = frappe.get_doc(fields)
	doc.insert(ignore_permissions=True)
	return doc.name


def _chapter_quiz_for_profile(
	chapter_profile: str | None, learning_module: str, course_chapter: str | None
) -> str | None:
	if chapter_profile:
		return frappe.db.get_value("Learning Chapter Profile", chapter_profile, "chapter_quiz")
	if course_chapter:
		return frappe.db.get_value(
			"Learning Chapter Profile",
			{"learning_module": learning_module, "course_chapter": course_chapter},
			"chapter_quiz",
		)
	return None


def _link_question_to_quiz(quiz_name: str, question_name: str) -> None:
	quiz = frappe.get_doc("LMS Quiz", quiz_name)
	for row in quiz.questions or []:
		if row.question == question_name:
			return
	quiz.append("questions", {"question": question_name, "marks": 1})
	quiz.total_marks = len(quiz.questions or [])
	quiz.save(ignore_permissions=True)


def _chapter_mcq_prompt() -> str:
	return (
		"Draft chapter MCQs from the supplied approved notes only (target ~7 per chapter, 150 module total). "
		"Do not use model memory, web knowledge, or another jurisdiction. If the notes do not support "
		"a question or explanation, omit it. Return JSON array with question, "
		"options[{text,is_correct,explanation}], concept, difficulty, source_reference, source_quote."
	)


def _flashcard_prompt() -> str:
	return (
		"Draft concise flashcards from the supplied chapter notes only. Do not use model memory, "
		"web knowledge, or another jurisdiction. If the notes do not support a card, omit it. "
		"source_quote must be copied verbatim from the notes_html and source_reference must identify "
		"its chapter/locator. Return JSON array with front, back, concept, difficulty, "
		"source_reference, source_quote."
	)
