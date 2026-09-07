"""Safe, idempotent structure sync for the published SQE1 FLK1 pathway."""

from __future__ import annotations

import frappe


FLK1_CATEGORY = "SQE1 · FLK1"
FLK2_CATEGORY = "SQE1 · FLK2"

FLK1_SUBJECTS = (
	{
		"course": "business-law-practice-blp",
		"title": "Business Law and Practice",
		"summary": "Business structures, governance, finance, tax and insolvency.",
	},
	{
		"course": "dispute-resolution",
		"title": "Dispute Resolution",
		"summary": "Civil procedure, dispute strategy and resolution routes.",
	},
	{
		"course": "contract-law",
		"title": "Contract Law",
		"summary": "Formation, terms, vitiating factors, breach and remedies.",
	},
	{
		"course": "tort-law",
		"title": "Tort Law",
		"summary": "Negligence, occupiers’ liability, product liability, defences and remedies.",
	},
	{
		"course": "public-law",
		"title": "Legal System, Public Law & EU Law",
		"summary": "The legal system, constitutional and administrative law, and EU law in the UK.",
	},
	{
		"course": "legal-services",
		"title": "Legal Services",
		"summary": "Professional regulation, conduct, client money and anti-money laundering.",
	},
)

FLK2_SUBJECTS = (
	{
		"course": "criminal-law",
		"title": "Criminal Law",
		"summary": "The substantive criminal law principles tested in FLK2.",
	},
	{
		"course": "criminal-litigation",
		"title": "Criminal Litigation",
		"summary": "Criminal procedure, evidence, case management and advocacy.",
	},
	{
		"course": "equity-and-trust-law",
		"title": "Equity and Trust Law",
		"summary": "Equitable principles, trusts, fiduciary duties and remedies.",
	},
	{
		"course": "land-law",
		"title": "Land Law",
		"summary": "Estates, interests, co-ownership, registration and leases.",
	},
	{
		"course": "property-practice",
		"title": "Property Practice",
		"summary": "Residential and commercial property transactions and practice.",
	},
	{
		"course": "solicitors-accounts",
		"title": "Solicitors' Accounts",
		"summary": "Client money, accounting rules, ledgers and reconciliations.",
	},
	{
		"course": "wills-and-administration-of-estates",
		"title": "Wills and Administration of Estates",
		"summary": "Wills, probate, intestacy and estate administration.",
	},
)


TORT_CHAPTERS = (
	"Chapter 1: Classification Of Civil Wrongs And The Tort Of Negligence",
	"Chapter 2: Duty Of Care",
	"Chapter 3: Breach Of Duty Of Care",
	"Chapter 4: Causation",
	"Chapter 5: Negligence – Non-Physical Harm",
	"Chapter 6: Employers' Liability",
	"Chapter 7: Vicarious Liability",
	"Chapter 8: Nuisance And The Rule In Rylands V Fletcher",
	"Chapter 9: Occupiers' Liability",
	"Chapter 10: Statutory Liability For Defective Products",
	"Chapter 11: Defences",
	"Chapter 12: Remedies",
)


def sync_flk1_pathway() -> dict:
	"""Group verified FLK1 courses, remove draft labels, and fix the Tort outline."""
	frappe.only_for(("System Manager", "Course Creator", "Moderator", "LMS Content Reviewer"))
	category = _ensure_category()
	updated_courses = []
	for subject in FLK1_SUBJECTS:
		course = frappe.get_doc("LMS Course", subject["course"])
		course.category = category.name
		course.tags = "SQE1, FLK1, " + subject["title"]
		course.title = subject["title"]
		course.short_introduction = subject["summary"]
		course.save(ignore_permissions=True)
		_clean_draft_lesson_titles(course.name)
		updated_courses.append(course.name)

	tort = _split_tort_chapters()
	frappe.db.commit()
	return {"category": category.name, "courses": updated_courses, "tort": tort}


def sync_flk2_pathway() -> dict:
	"""Group the published FLK2 subjects without changing their chapter content."""
	frappe.only_for(("System Manager", "Course Creator", "Moderator", "LMS Content Reviewer"))
	category = _ensure_category(FLK2_CATEGORY)
	updated_courses = []
	for subject in FLK2_SUBJECTS:
		course = frappe.get_doc("LMS Course", subject["course"])
		course.category = category.name
		course.tags = "SQE1, FLK2, " + subject["title"]
		course.title = subject["title"]
		course.short_introduction = subject["summary"]
		course.save(ignore_permissions=True)
		updated_courses.append(course.name)
	frappe.db.commit()
	return {"category": category.name, "courses": updated_courses}


def _ensure_category(category_name=FLK1_CATEGORY):
	name = frappe.db.get_value("LMS Category", {"category": category_name}, "name")
	if name:
		return frappe.get_doc("LMS Category", name)
	return frappe.get_doc({"doctype": "LMS Category", "category": category_name}).insert(
		ignore_permissions=True
	)


def _clean_draft_lesson_titles(course_name: str) -> None:
	for lesson_name in frappe.get_all("Course Lesson", filters={"course": course_name}, pluck="name"):
		lesson = frappe.get_doc("Course Lesson", lesson_name)
		if lesson.title.startswith("Draft notes — "):
			lesson.title = "Study notes — " + lesson.title.removeprefix("Draft notes — ")
			lesson.save(ignore_permissions=True)


def _split_tort_chapters() -> dict:
	"""Split Chapters 9–12 from a previously combined, source-backed Tort lesson."""
	course = frappe.get_doc("LMS Course", "tort-law")
	module_name = frappe.db.get_value("Learning Module Config", {"lms_course": course.name}, "name")
	if not module_name:
		frappe.throw("Tort Law does not have a learning-module configuration.")

	chapter_eight = frappe.db.get_value(
		"Course Chapter", {"course": course.name, "title": TORT_CHAPTERS[7]}, "name"
	)
	if not chapter_eight:
		frappe.throw("The original Tort Chapter 8 was not found.")
	lesson_name = frappe.db.get_value(
		"Learning Chapter Profile", {"learning_module": module_name, "course_chapter": chapter_eight}, "notes_lesson"
	)
	if not lesson_name:
		frappe.throw("The original Tort Chapter 8 study-notes lesson was not found.")
	original = frappe.get_doc("Course Lesson", lesson_name).body or ""
	sections = _tort_sections(original)

	chapters = []
	for position, title in enumerate(TORT_CHAPTERS, start=1):
		chapter_name = frappe.db.get_value("Course Chapter", {"course": course.name, "title": title}, "name")
		chapter = frappe.get_doc("Course Chapter", chapter_name) if chapter_name else frappe.get_doc(
			{"doctype": "Course Chapter", "course": course.name, "title": title}
		)
		chapter.idx = position
		if chapter_name:
			chapter.save(ignore_permissions=True)
		else:
			chapter.insert(ignore_permissions=True)
		chapters.append(chapter)

		if position < 8:
			continue
		body = sections[position]
		profile_name = frappe.db.get_value(
			"Learning Chapter Profile",
			{"learning_module": module_name, "course_chapter": chapter.name},
			"name",
		)
		profile = frappe.get_doc("Learning Chapter Profile", profile_name) if profile_name else frappe.get_doc(
			{
				"doctype": "Learning Chapter Profile",
				"learning_module": module_name,
				"course_chapter": chapter.name,
				"chapter_title": title,
			}
		)
		note_lesson_name = profile.notes_lesson or frappe.db.get_value(
			"Course Lesson", {"course": course.name, "chapter": chapter.name}, "name"
		)
		note_lesson = frappe.get_doc("Course Lesson", note_lesson_name) if note_lesson_name else frappe.get_doc(
			{"doctype": "Course Lesson", "course": course.name, "chapter": chapter.name}
		)
		note_lesson.title = "Study notes — " + title
		note_lesson.chapter = chapter.name
		note_lesson.course = course.name
		note_lesson.idx = 1
		note_lesson.body = body
		if note_lesson_name:
			note_lesson.save(ignore_permissions=True)
		else:
			note_lesson.insert(ignore_permissions=True)
		profile.notes_lesson = note_lesson.name
		profile.course_chapter = chapter.name
		profile.chapter_title = title
		profile.notes_html = body
		profile.concept_tags = title
		if profile_name:
			profile.save(ignore_permissions=True)
		else:
			profile.insert(ignore_permissions=True)

	course.reload()
	course.set("chapters", [])
	for index, chapter in enumerate(chapters, start=1):
		course.append("chapters", {"chapter": chapter.name, "idx": index})
	course.save(ignore_permissions=True)
	module = frappe.get_doc("Learning Module Config", module_name)
	module.chapter_count = len(chapters)
	module.save(ignore_permissions=True)
	return {"chapters": len(chapters), "source_preserved": True, "split": [9, 10, 11, 12]}


def _tort_sections(body: str) -> dict[int, str]:
	markers = {9: "<p>9.1 ", 10: "<p>10.1 ", 11: "<p>11.1 ", 12: "<p>12.1 "}
	starts = {number: body.find(marker) for number, marker in markers.items()}
	if any(offset <= 0 for offset in starts.values()):
		frappe.throw("The expected Tort chapter boundaries are missing; no content was changed.")
	if list(starts.values()) != sorted(starts.values()):
		frappe.throw("Tort chapter boundaries are out of order; no content was changed.")
	return {
		8: body[: starts[9]],
		9: body[starts[9] : starts[10]],
		10: body[starts[10] : starts[11]],
		11: body[starts[11] : starts[12]],
		12: body[starts[12] :],
	}
