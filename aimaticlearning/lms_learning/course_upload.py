"""Reusable LMS course-content upload from private Word sources.

Fill Course Lesson.body from a registered private File. Always clear
Course Lesson.content. Never publishes MCQs or assumes an answer key.
"""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

import frappe
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

CHAPTER_RE = re.compile(
	r"^chapter\s*(?:(\d+)|(one|two|three|four|five|six|seven|eight|nine|ten))\b\s*[:.\-–]?\s*(.*)$",
	re.I,
)
TRAILING_CHAPTER_RE = re.compile(r"^(.+?)\s*[-–]\s*chapter\s+(\d+)\s*$", re.I)
WORD_TO_NUM = {
	"one": 1,
	"two": 2,
	"three": 3,
	"four": 4,
	"five": 5,
	"six": 6,
	"seven": 7,
	"eight": 8,
	"nine": 9,
	"ten": 10,
}
CHAPTER_START_STYLES = {"Title", "Heading 1", "Heading 2"}
DRAFT_BANNER = "Internal review draft — not published to students."

FLK2_SUBJECT_SPECS = (
	{"title": "Criminal Law", "source_file": "Criminal Law Notes for SQE1.docx", "chapters": 6},
	{"title": "Criminal Litigation", "source_file": "Criminal Litigation.docx", "chapters": 11},
	{"title": "Equity and Trust Law", "source_file": "Eq nd trust.docx", "chapters": 8},
	{"title": "Land Law", "source_file": "Land Law.docx", "chapters": 8},
	{"title": "Property Practice", "source_file": "Property Practice Essentials for SQE1.docx", "chapters": 8},
	{"title": "Solicitors' Accounts", "source_file": "Solicitor ACC.docx", "chapters": 8},
	{
		"title": "Wills and Administration of Estates",
		"source_file": "WILLS AND THE ADMINISTRATION OF ESTATES.docx",
		"chapters": 6,
	},
)


def coverage_report(course: str, source_file: str) -> dict:
	"""Heading-by-heading source vs live lessons. Read-only."""
	source = _load_source(source_file)
	chapters = parse_note_chapters(source["path"])
	lessons = _course_lessons(course)
	rows = []
	for chapter in chapters:
		lesson = _match_lesson(lessons, chapter)
		body = (lesson.get("body") or "") if lesson else ""
		content = (lesson.get("content") or "") if lesson else ""
		gap = _gap_reason(body, content, lesson)
		rows.append(
			{
				"source_heading": chapter["title"],
				"chapter_number": chapter.get("number"),
				"paragraphs": chapter["paragraph_count"],
				"tables": chapter["table_count"],
				"source_chars": chapter["char_count"],
				"lesson": lesson["name"] if lesson else None,
				"lesson_title": lesson["title"] if lesson else None,
				"body_chars": len(body.strip()),
				"content_chars": len(content.strip()),
				"gap": gap,
			}
		)
	unmapped = [
		{"lesson": row["name"], "title": row["title"], "body_chars": len((row.get("body") or "").strip())}
		for row in lessons
		if not any(item.get("lesson") == row["name"] for item in rows)
	]
	return {
		"course": course,
		"source_file": source["name"],
		"source_hash": source["hash"],
		"source_chapters": len(chapters),
		"live_lessons": len(lessons),
		"gaps": [row for row in rows if row["gap"]],
		"rows": rows,
		"unmapped_lessons": unmapped,
	}


def fill_lessons_from_source(lesson_titles: list[str], source_file: str) -> dict:
	"""Write student HTML into named lessons from a private Word File."""
	source = _load_source(source_file)
	chapters = parse_note_chapters(source["path"])
	results = []
	for title in lesson_titles:
		lesson_name = _resolve_lesson(title)
		lesson = frappe.get_doc("Course Lesson", lesson_name)
		chapter = _match_chapter(chapters, lesson.title or title)
		if not chapter:
			results.append({"lesson": lesson_name, "title": lesson.title, "ok": False, "error": "no matching source chapter"})
			continue
		if chapter["char_count"] < 40:
			results.append(
				{
					"lesson": lesson_name,
					"title": lesson.title,
					"ok": False,
					"error": "source chapter too thin to publish",
					"locator": chapter["title"],
				}
			)
			continue
		body = render_chapter_html(chapter)
		lesson.body = body
		lesson.content = ""
		lesson.save(ignore_permissions=True)
		if lesson.chapter:
			from aimaticlearning.lms_learning.outline_sync import link_chapter_to_course, link_lesson_to_chapter

			link_chapter_to_course(lesson.course, lesson.chapter)
			link_lesson_to_chapter(lesson.chapter, lesson.name)
		_update_profile(lesson, source, chapter, body)
		results.append(
			{
				"lesson": lesson_name,
				"title": lesson.title,
				"ok": True,
				"locator": chapter["title"],
				"source_file": source["name"],
				"source_hash": source["hash"],
				"body_chars": len(body),
			}
		)
	frappe.db.commit()
	return {"source_file": source["name"], "source_hash": source["hash"], "results": results}


def audit_flk2_sources() -> dict:
	"""Read-only preflight for the seven private FLK2 Word sources."""
	results = []
	issues = []
	for spec in FLK2_SUBJECT_SPECS:
		file_name = frappe.db.get_value(
			"File",
			{
				"file_name": spec["source_file"],
				"folder": "Home/Attachments/FLK2",
				"is_private": 1,
			},
			"name",
		)
		if not file_name:
			issues.append(f"Missing private source: {spec['source_file']}")
			continue
		source = _load_source(file_name)
		chapters = parse_note_chapters(source["path"])
		chapter_numbers = [chapter.get("number") for chapter in chapters]
		if len(chapters) != spec["chapters"]:
			issues.append(
				f"{spec['title']}: expected {spec['chapters']} chapters, parsed {len(chapters)}"
			)
		if chapter_numbers != list(range(1, spec["chapters"] + 1)):
			issues.append(f"{spec['title']}: chapter sequence is {chapter_numbers}")
		thin = [chapter["title"] for chapter in chapters if chapter["char_count"] < 400]
		if thin:
			issues.append(f"{spec['title']}: thin chapters {thin}")
		results.append(
			{
				"subject": spec["title"],
				"source_file": file_name,
				"source_name": source["name"],
				"source_hash": source["hash"],
				"chapters": len(chapters),
				"chapter_titles": [chapter["title"] for chapter in chapters],
				"source_chars": sum(chapter["char_count"] for chapter in chapters),
				"tables": sum(chapter["table_count"] for chapter in chapters),
			}
		)
	return {"ready": not issues, "subjects": results, "issues": issues}


def import_flk2_course_drafts() -> dict:
	"""Create idempotent unpublished FLK2 note courses after a strict preflight."""
	audit = audit_flk2_sources()
	if not audit["ready"]:
		frappe.throw("FLK2 source preflight failed: " + "; ".join(audit["issues"]))

	results = []
	for spec, audit_row in zip(FLK2_SUBJECT_SPECS, audit["subjects"], strict=True):
		source = _load_source(audit_row["source_file"])
		chapters = parse_note_chapters(source["path"])
		course = _ensure_flk2_course(spec)
		module = _ensure_flk2_module(spec, course, source)
		chapter_names = _sync_flk2_chapters(course, module, chapters, source)
		module.source_file = source["name"]
		module.chapter_count = len(chapter_names)
		module.import_status = "Structure Imported"
		module.module_mcq_count = 0
		module.published_flashcard_count = 0
		module.save(ignore_permissions=True)
		results.append(
			{
				"subject": spec["title"],
				"course": course.name,
				"published": int(course.published or 0),
				"source_file": source["name"],
				"source_hash": source["hash"],
				"chapters": len(chapter_names),
			}
		)

	frappe.db.commit()
	return {
		"mode": "flk2_review_drafts_only",
		"subjects": results,
		"source_audit": audit,
		"safeguards": [
			"All seven Word sources remain private Frappe Files.",
			"All subject courses remain unpublished until coverage verification.",
			"No scored MCQs, flashcards, enrolments, or learner attempts are created.",
		],
	}


def _ensure_flk2_course(spec: dict):
	course_name = frappe.db.get_value("LMS Course", {"title": spec["title"]})
	if course_name:
		course = frappe.get_doc("LMS Course", course_name)
		if course.published:
			frappe.throw(f"Refusing to resync already-published FLK2 course: {spec['title']}")
	else:
		course = frappe.get_doc(
			{
				"doctype": "LMS Course",
				"title": spec["title"],
				"short_introduction": "Internal review draft — not published to students.",
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
	course.description = (
		"<p><strong>Internal review draft.</strong> This course is not available to students. "
		"The supplied FLK2 notes have been structured for coverage review before publication. "
		"Embedded MCQs remain unscored until their answer mappings are verified.</p>"
	)
	course.save(ignore_permissions=True)
	return course


def _ensure_flk2_module(spec: dict, course, source: dict):
	module_name = frappe.db.get_value("Learning Module Config", {"lms_course": course.name})
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
	module.source_file = source["name"]
	return module


def _remove_stale_draft_content(course, module, linked_chapters: set[str]) -> None:
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


def _sync_flk2_chapters(course, module, chapters: list[dict], source: dict) -> list[str]:
	linked_chapters = []
	for index, chapter_data in enumerate(chapters, start=1):
		title = chapter_data["title"]
		chapter_name = frappe.db.get_value(
			"Course Chapter", {"course": course.name, "title": title}, "name"
		)
		if chapter_name:
			chapter = frappe.get_doc("Course Chapter", chapter_name)
		else:
			chapter = frappe.get_doc(
				{"doctype": "Course Chapter", "course": course.name, "title": title}
			)
			chapter.insert(ignore_permissions=True)
		chapter.idx = index
		chapter.save(ignore_permissions=True)

		profile_name = frappe.db.get_value(
			"Learning Chapter Profile",
			{"learning_module": module.name, "chapter_title": title},
			"name",
		)
		profile = (
			frappe.get_doc("Learning Chapter Profile", profile_name)
			if profile_name
			else frappe.get_doc(
				{
					"doctype": "Learning Chapter Profile",
					"learning_module": module.name,
					"chapter_title": title,
				}
			)
			)
		lesson_title = f"Draft notes — {title}"
		lesson_name = frappe.db.get_value(
			"Course Lesson",
			{"course": course.name, "chapter": chapter.name, "title": lesson_title},
			"name",
		)
		lesson = (
			frappe.get_doc("Course Lesson", lesson_name)
			if lesson_name
			else frappe.get_doc(
				{
					"doctype": "Course Lesson",
					"course": course.name,
					"chapter": chapter.name,
					"title": lesson_title,
				}
			)
			)
		notes_html = render_chapter_html(chapter_data)
		lesson.body = f"<p><strong>{DRAFT_BANNER}</strong></p>{notes_html}"
		lesson.content = ""
		lesson.quiz_id = ""
		if lesson_name:
			lesson.save(ignore_permissions=True)
		else:
			lesson.insert(ignore_permissions=True)

		source_tag = f"source:{source['name']} hash:{source['hash']} locator:{chapter_data['locator']}"
		profile.course_chapter = chapter.name
		profile.notes_lesson = lesson.name
		profile.chapter_quiz = None
		profile.notes_html = notes_html
		if profile.concept_tags != source_tag:
			profile.source_revision = int(profile.source_revision or 0) + 1
			profile.concept_tags = source_tag
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
	return linked_chapters


def publish_flk2_notes_only() -> dict:
	"""Publish FLK2 notes after a complete heading-to-lesson coverage check."""
	audit = audit_flk2_sources()
	if not audit["ready"]:
		frappe.throw("FLK2 source preflight failed: " + "; ".join(audit["issues"]))

	coverage = []
	for spec, audit_row in zip(FLK2_SUBJECT_SPECS, audit["subjects"], strict=True):
		course_name = frappe.db.get_value("LMS Course", {"title": spec["title"]}, "name")
		if not course_name:
			frappe.throw(f"Missing FLK2 draft course: {spec['title']}")
		report = coverage_report(course_name, audit_row["source_file"])
		if report["gaps"] or report["unmapped_lessons"]:
			frappe.throw(
				f"Coverage failed for {spec['title']}: "
				f"gaps={report['gaps']} unmapped={report['unmapped_lessons']}"
			)
		coverage.append(report)

	published = []
	for spec in FLK2_SUBJECT_SPECS:
		course_name = frappe.db.get_value("LMS Course", {"title": spec["title"]}, "name")
		course = frappe.get_doc("LMS Course", course_name)
		course.published = 1
		course.upcoming = 0
		course.disable_self_learning = 0
		course.short_introduction = "Structured SQE1 study notes organised into focused chapters."
		course.description = (
			"<p>Read the structured notes in order and return to the lesson you last studied. "
			"Practice questions and flashcards will appear only after their answers and explanations "
			"have been reviewed.</p>"
		)
		course.save(ignore_permissions=True)
		for lesson_name in frappe.get_all("Course Lesson", filters={"course": course.name}, pluck="name"):
			lesson = frappe.get_doc("Course Lesson", lesson_name)
			lesson.title = re.sub(r"^Draft notes\s*[—-]\s*", "", lesson.title or "").strip()
			lesson.body = (lesson.body or "").replace(
				"<p><strong>Internal review draft — not published to students.</strong></p>", ""
			)
			lesson.content = ""
			lesson.save(ignore_permissions=True)
		relink_notes_lessons(course.name)
		module_name = frappe.db.get_value("Learning Module Config", {"lms_course": course.name}, "name")
		module = frappe.get_doc("Learning Module Config", module_name)
		module.title = spec["title"]
		module.import_status = "Published"
		module.save(ignore_permissions=True)
		published.append(
			{
				"subject": spec["title"],
				"course": course.name,
				"chapters": len(course.chapters or []),
				"published": int(course.published or 0),
			}
		)

	frappe.db.commit()
	return {
		"mode": "flk2_published_notes_only",
		"courses": published,
		"coverage": coverage,
		"mcqs_published": False,
		"flashcards_published": False,
		"enrolments_created": False,
	}


FLK1_COURSES = (
	"contract-law",
	"dispute-resolution",
	"legal-services",
	"public-law",
	"tort-law",
)


def relink_notes_lessons(course: str | None = None) -> dict:
	"""Attach Course Lessons to Course Chapter.lessons so /learn/N-1 resolves."""
	from aimaticlearning.lms_learning.outline_sync import link_chapter_to_course, link_lesson_to_chapter

	courses = [course] if course else list(FLK1_COURSES)
	linked = 0
	missing_chapter = []
	for course_name in courses:
		if not frappe.db.exists("LMS Course", course_name):
			continue
		for lesson in frappe.get_all(
			"Course Lesson",
			filters={"course": course_name},
			fields=["name", "chapter", "title"],
		):
			if not lesson.chapter:
				missing_chapter.append(lesson.name)
				continue
			link_chapter_to_course(course_name, lesson.chapter)
			link_lesson_to_chapter(lesson.chapter, lesson.name)
			linked += 1
	frappe.db.commit()
	return {"courses": courses, "linked": linked, "missing_chapter": missing_chapter}


def parse_note_chapters(path: Path) -> list[dict]:
	"""Split only on Chapter N / Chapter Six title lines, not every Heading 1."""
	document = Document(str(path))
	chapters: list[dict] = []
	current = None
	for block in _iter_blocks(document):
		if isinstance(block, Table):
			if current is None:
				continue
			markup = _table_html(block)
			current["blocks"].append(("table", markup))
			current["table_count"] += 1
			current["char_count"] += len(re.sub(r"<[^>]+>", "", markup))
			continue
		text = (block.text or "").replace("\xa0", " ").strip()
		if not text:
			continue
		style = block.style.name if block.style else ""
		if "table of contents" in text.lower():
			continue
		start = _chapter_start(text, style)
		if start:
			number, title = start
			if current:
				chapters.append(current)
			current = {
				"title": title,
				"number": number,
				"locator": title,
				"blocks": [("heading", title, 2)],
				"paragraph_count": 0,
				"table_count": 0,
				"char_count": len(title),
			}
			continue
		if current is None:
			continue
		level = _heading_level(style)
		if level:
			current["blocks"].append(("heading", text, min(6, max(3, level + 1))))
		elif style == "Key Point":
			current["blocks"].append(("para", text, True))
			current["paragraph_count"] += 1
		else:
			current["blocks"].append(("para", text))
			current["paragraph_count"] += 1
		current["char_count"] += len(text)
	if current:
		chapters.append(current)
	return _collapse_chapter_duplicates(chapters)


def _collapse_chapter_duplicates(chapters: list[dict]) -> list[dict]:
	"""Keep the longest body per Chapter N so TOC/summary stubs do not win."""
	best: dict[int, dict] = {}
	rest: list[dict] = []
	for chapter in chapters:
		number = chapter.get("number")
		if number is None:
			rest.append(chapter)
			continue
		previous = best.get(number)
		if previous is None or chapter["char_count"] > previous["char_count"]:
			best[number] = chapter
	return [best[key] for key in sorted(best)] + rest


def _chapter_start(text: str, style: str) -> tuple[int, str] | None:
	match = CHAPTER_RE.match(text)
	trailing_match = TRAILING_CHAPTER_RE.match(text)
	if not match and not trailing_match:
		return None
	if trailing_match:
		number = int(trailing_match.group(2))
		title = trailing_match.group(1).strip()
		return number, f"Chapter {number}: {title}"
	title_suffix = (match.group(3) or "").strip()
	if re.search(
		r"\b(multiple\s+choice|mcq|practice\s+(?:mcq|questions?|test|assessment)|"
		r"assessment|self[- ]assessment|checklist)\b",
		title_suffix,
		re.I,
	) or re.match(
		r"^(summary|glossary|key\s+(?:terms|concepts))(?:\b|\s*[:&-])",
		title_suffix,
		re.I,
	):
		return None
	number = int(match.group(1)) if match.group(1) else WORD_TO_NUM[match.group(2).lower()]
	return number, _normalise_title(text)


def render_chapter_html(chapter: dict) -> str:
	parts = ['<article class="aimatic-notes">']
	for block in chapter["blocks"]:
		kind = block[0]
		if kind == "heading":
			text, level = block[1], block[2]
			parts.append(f"<h{level}>{html.escape(text)}</h{level}>")
		elif kind == "para":
			payload = html.escape(block[1])
			if len(block) > 2 and block[2]:
				payload = f"<strong>{payload}</strong>"
			parts.append(f"<p>{payload}</p>")
		elif kind == "table":
			parts.append(block[1])
	parts.append("</article>")
	html_out = "".join(parts)
	# LMS LessonContent.vue splits on blank lines; keep a single HTML block.
	return re.sub(r"\n{2,}", "\n", html_out)


def _iter_blocks(document: Document):
	parent = document.element.body
	for child in parent.iterchildren():
		if child.tag == qn("w:p"):
			yield Paragraph(child, document)
		elif child.tag == qn("w:tbl"):
			yield Table(child, document)


def _table_html(table: Table) -> str:
	rows = []
	for index, row in enumerate(table.rows):
		cells = "".join(
			f"<t{'h' if index == 0 else 'd'}>{html.escape((cell.text or '').replace(chr(160), ' ').strip())}</t{'h' if index == 0 else 'd'}>"
			for cell in row.cells
		)
		rows.append(f"<tr>{cells}</tr>")
	return f'<table class="aimatic-notes-table">{"".join(rows)}</table>'


def _heading_level(style: str) -> int | None:
	match = re.match(r"Heading\s+(\d+)$", style or "", re.I)
	return int(match.group(1)) if match else None


def _normalise_title(value: str) -> str:
	cleaned = re.sub(r"\s+", " ", value).strip()
	match = CHAPTER_RE.match(cleaned)
	if not match:
		return cleaned.title() if cleaned.isupper() else cleaned
	number = int(match.group(1)) if match.group(1) else WORD_TO_NUM[match.group(2).lower()]
	title = match.group(3).strip(" :.-–")
	if title.isupper():
		title = title.title()
	return f"Chapter {number}: {title}"


def _load_source(source_file: str) -> dict:
	doc = frappe.get_doc("File", source_file)
	if not int(doc.is_private or 0):
		frappe.throw("Course upload sources must be private Files.")
	path = Path(doc.get_full_path())
	if not path.is_absolute():
		path = Path(frappe.get_site_path("..")) / path
	if not path.is_file():
		path = Path(frappe.get_site_path("private", "files", doc.file_name or ""))
	if not path.is_file():
		frappe.throw(f"Private source is missing on disk: {source_file}")
	digest = hashlib.md5(path.read_bytes()).hexdigest()
	return {"name": doc.name, "path": path, "hash": digest, "file_name": doc.file_name}


def _course_lessons(course: str) -> list[dict]:
	names = frappe.get_all("Course Lesson", filters={"course": course}, pluck="name")
	rows = []
	for name in names:
		lesson = frappe.get_doc("Course Lesson", name)
		rows.append(
			{
				"name": lesson.name,
				"title": lesson.title,
				"chapter": lesson.chapter,
				"body": lesson.body or "",
				"content": lesson.content or "",
			}
		)
	return rows


def _resolve_lesson(title_or_name: str) -> str:
	if frappe.db.exists("Course Lesson", title_or_name):
		return title_or_name
	name = frappe.db.get_value("Course Lesson", {"title": title_or_name}, "name")
	if name:
		return name
	matches = frappe.get_all("Course Lesson", filters={"title": ["like", f"%{title_or_name}%"]}, pluck="name")
	if len(matches) == 1:
		return matches[0]
	frappe.throw(f"Course Lesson not found: {title_or_name}")


def _match_lesson(lessons: list[dict], chapter: dict) -> dict | None:
	for lesson in lessons:
		if _same_chapter(lesson.get("title") or "", chapter):
			return lesson
	return None


def _match_chapter(chapters: list[dict], lesson_title: str) -> dict | None:
	for chapter in chapters:
		if _same_chapter(lesson_title, chapter):
			return chapter
	return None


def _same_chapter(lesson_title: str, chapter: dict) -> bool:
	title = lesson_title or ""
	lesson_match = re.search(r"chapter\s+(\d+)\b", title, re.I)
	lesson_number = int(lesson_match.group(1)) if lesson_match else None
	number = chapter.get("number")
	if number is not None and lesson_number is not None:
		return number == lesson_number
	if number is not None and re.search(rf"chapter\s+{number}\b", title, re.I):
		return True
	heading = re.sub(r"^chapter\s+\d+\s*[:.\-–]\s*", "", chapter["title"], flags=re.I)
	return bool(heading) and heading.lower() in title.lower()


def _gap_reason(body: str, content: str, lesson: dict | None) -> str | None:
	if not lesson:
		return "no mapped lesson"
	text = re.sub(r"<[^>]+>", "", body or "").strip()
	if not text or text == DRAFT_BANNER or len(text) < 80:
		return "empty or banner-only body"
	if (content or "").strip():
		return "stale content field"
	return None


def _update_profile(lesson, source: dict, chapter: dict, body: str) -> None:
	filters = {"notes_lesson": lesson.name}
	name = frappe.db.get_value("Learning Chapter Profile", filters, "name")
	if not name:
		return
	profile = frappe.get_doc("Learning Chapter Profile", name)
	profile.notes_html = body
	profile.source_revision = int(profile.source_revision or 0) + 1
	profile.concept_tags = (
		f"source:{source['name']} hash:{source['hash']} locator:{chapter['locator']}"
	)
	profile.save(ignore_permissions=True)
