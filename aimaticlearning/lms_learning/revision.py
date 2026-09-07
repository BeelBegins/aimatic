from __future__ import annotations

from collections import Counter

import frappe

from aimaticlearning.lms_learning.analytics import build_learning_map
from aimaticlearning.lms_learning.utils import user_can_access_course

VALID_RATINGS = ("hard", "good", "easy")
RECALL_PREFIX = "recall:"
PREFERRED_COURSE = "business-law-practice-blp"


def encode_recall_tags(rating: str | None, concept: str | None = None) -> str:
	normalised = _normalise_rating(rating) or "hard"
	parts = [f"{RECALL_PREFIX}{normalised}"]
	if concept and str(concept).strip():
		parts.append(str(concept).strip())
	return ", ".join(parts)


def parse_recall_rating(concept_tags: str | None, correct: int | bool | None = None) -> str | None:
	for part in _split_parts(concept_tags):
		lowered = part.lower()
		if lowered.startswith(RECALL_PREFIX):
			value = lowered[len(RECALL_PREFIX) :].strip()
			if value in VALID_RATINGS:
				return value
	if correct is None:
		return None
	return "hard" if not int(correct) else "good"


def latest_ratings_from_attempts(attempts: list[dict]) -> dict[str, str]:
	latest: dict[str, str] = {}
	for row in attempts:
		name = row.get("learning_flashcard")
		if not name or name in latest:
			continue
		rating = parse_recall_rating(row.get("concept_tags"), row.get("correct"))
		if rating:
			latest[name] = rating
	return latest


def apply_rating_filter(cards: list[dict], ratings: dict[str, str], rating_filter: str | None) -> list[dict]:
	wanted = (rating_filter or "").strip().lower()
	filtered = []
	for card in cards:
		last = ratings.get(card.get("name"))
		card["last_rating"] = last
		if wanted in ("", "all", "*"):
			filtered.append(card)
		elif wanted == "unreviewed" and last is None:
			filtered.append(card)
		elif last == wanted:
			filtered.append(card)
	return filtered


def load_latest_ratings(user: str, learning_module: str) -> dict[str, str]:
	attempts = frappe.get_all(
		"Learning Attempt Detail",
		filters={
			"user": user,
			"learning_module": learning_module,
			"learning_flashcard": ["is", "set"],
		},
		fields=["learning_flashcard", "concept_tags", "correct", "modified"],
		order_by="modified desc",
		limit_page_length=5000,
	)
	return latest_ratings_from_attempts(attempts)


def filter_cards_for_user(
	cards: list[dict],
	user: str,
	learning_module: str,
	rating_filter: str | None,
) -> list[dict]:
	return apply_rating_filter(cards, load_latest_ratings(user, learning_module), rating_filter)


def build_revision_board(
	user: str,
	course: str | None = None,
	learning_module: str | None = None,
) -> dict:
	modules = accessible_modules(user)
	selected = _select_module(modules, course, learning_module)
	if not selected:
		return {
			"selected": None,
			"modules": modules,
			"empty_reason": "no_access",
			"flashcards": _empty_flashcard_counts(),
			"has_flashcards": False,
			"has_attempts": False,
			"weaknesses": [],
			"strengths": [],
			"recommendations": [
				"Ask your instructor to enrol you on a course before revision data can appear."
			],
			"chapters": [],
			"study_urls": {},
		}

	learning_map = build_learning_map(selected["name"], user)
	published_cards = frappe.get_all(
		"Learning Flashcard",
		filters={"learning_module": selected["name"], "status": "Published"},
		fields=["name", "course_chapter", "concept", "difficulty"],
		limit_page_length=2000,
	)
	ratings = load_latest_ratings(user, selected["name"])
	counts = Counter(ratings.values())
	flashcards = {
		"published": len(published_cards),
		"hard": int(counts.get("hard") or 0),
		"good": int(counts.get("good") or 0),
		"easy": int(counts.get("easy") or 0),
		"unreviewed": max(len(published_cards) - len(ratings), 0),
	}
	hard_by_chapter: dict[str, int] = Counter()
	for card in published_cards:
		if ratings.get(card.name) == "hard" and card.course_chapter:
			hard_by_chapter[card.course_chapter] += 1

	chapters = []
	for stats in learning_map.get("chapter_stats") or []:
		course_chapter = stats.get("course_chapter")
		chapters.append(
			{
				"chapter_title": stats.get("chapter_title"),
				"course_chapter": course_chapter,
				"mastery_pct": stats.get("mastery_pct") or 0,
				"attempts": stats.get("attempts") or 0,
				"group": _group(stats.get("mastery_pct") or 0, stats.get("attempts") or 0),
				"hard_cards": int(hard_by_chapter.get(course_chapter) or 0),
				"notes_url": _lesson_url(selected["lms_course"], course_chapter),
				"flashcard_url": _lesson_url(selected["lms_course"], course_chapter, "flashcard"),
			}
		)

	recommendations = list(learning_map.get("revision_recommendations") or [])
	if flashcards["hard"]:
		recommendations.insert(
			0,
			f"Revise {flashcards['hard']} flashcard{'s' if flashcards['hard'] != 1 else ''} you rated Hard.",
		)
	elif flashcards["published"] and not ratings:
		recommendations.insert(
			0,
			"Open a chapter Flashcards lesson and rate recall as Hard, Good, or Easy. This page then becomes your revision queue.",
		)
	elif not flashcards["published"]:
		recommendations.insert(
			0,
			"This subject does not have published flashcards yet. Use the notes and, when they appear, chapter MCQs.",
		)

	base = f"/learning-flashcards?learning_module={selected['name']}"
	return {
		"selected": selected,
		"modules": modules,
		"empty_reason": None,
		"flashcards": flashcards,
		"has_flashcards": flashcards["published"] > 0,
		"has_attempts": bool(ratings) or any((row.get("attempts") or 0) > 0 for row in chapters),
		"weaknesses": learning_map.get("weaknesses") or [],
		"strengths": learning_map.get("strengths") or [],
		"recommendations": recommendations[:6],
		"chapters": chapters,
		"study_urls": {
			"hard": f"{base}&rating=hard",
			"good": f"{base}&rating=good",
			"easy": f"{base}&rating=easy",
			"unreviewed": f"{base}&rating=unreviewed",
			"all": base,
			"course": f"/lms/courses/{selected['lms_course']}",
		},
	}


def accessible_modules(user: str) -> list[dict]:
	rows = frappe.get_all(
		"Learning Module Config",
		fields=["name", "title", "lms_course"],
		order_by="creation asc",
	)
	modules = []
	for row in rows:
		if not row.lms_course or not user_can_access_course(row.lms_course, user):
			continue
		published = frappe.db.count(
			"Learning Flashcard",
			{"learning_module": row.name, "status": "Published"},
		)
		modules.append(
			{
				"name": row.name,
				"title": row.title,
				"lms_course": row.lms_course,
				"published_flashcards": published,
			}
		)
	return modules


def _select_module(modules: list[dict], course: str | None, learning_module: str | None) -> dict | None:
	if learning_module:
		for module in modules:
			if module["name"] == learning_module:
				return module
	if course:
		for module in modules:
			if module["lms_course"] == course:
				return module
	for module in modules:
		if module["lms_course"] == PREFERRED_COURSE:
			return module
	return modules[0] if modules else None


def _lesson_url(course: str | None, course_chapter: str | None, title_contains: str | None = None) -> str | None:
	if not course or not course_chapter:
		return None
	chapter_idx = frappe.db.get_value(
		"Chapter Reference",
		{"parent": course, "chapter": course_chapter},
		"idx",
	)
	if not chapter_idx:
		return None
	refs = frappe.get_all(
		"Lesson Reference",
		filters={"parent": course_chapter},
		fields=["lesson", "idx"],
		order_by="idx asc",
	)
	if not refs:
		return None
	if title_contains:
		for ref in refs:
			title = (frappe.db.get_value("Course Lesson", ref.lesson, "title") or "").lower()
			if title_contains.lower() in title:
				return f"/lms/courses/{course}/learn/{int(chapter_idx)}-{int(ref.idx)}"
		return None
	return f"/lms/courses/{course}/learn/{int(chapter_idx)}-{int(refs[0].idx)}"


def _empty_flashcard_counts() -> dict:
	return {"published": 0, "hard": 0, "good": 0, "easy": 0, "unreviewed": 0}


def _group(mastery_pct: float, attempts: int) -> str:
	if not attempts:
		return "not_started"
	if mastery_pct >= 75:
		return "strong"
	if mastery_pct >= 50:
		return "developing"
	return "needs_work"


def _normalise_rating(rating: str | None) -> str | None:
	value = (rating or "").strip().lower()
	if value == "known":
		value = "easy"
	return value if value in VALID_RATINGS else None


def _split_parts(raw: str | None) -> list[str]:
	if not raw:
		return []
	return [part.strip() for part in str(raw).split(",") if part.strip()]
