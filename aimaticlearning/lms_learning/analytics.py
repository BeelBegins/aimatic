from __future__ import annotations

from collections import defaultdict

import frappe
from frappe.utils import now_datetime


def build_learning_map(learning_module: str, user: str) -> dict:
	chapters = frappe.get_all(
		"Learning Chapter Profile",
		filters={"learning_module": learning_module},
		fields=["name", "chapter_title", "course_chapter", "concept_tags"],
		order_by="creation asc",
	)
	attempts = frappe.get_all(
		"Learning Attempt Detail",
		filters={"learning_module": learning_module, "user": user},
		fields=[
			"course_chapter",
			"lms_question",
			"correct",
			"time_seconds",
			"concept_tags",
			"question_revision",
		],
	)
	quiz_rows = frappe.db.sql(
		"""
		select s.name, s.quiz, s.percentage, s.course, q.title as quiz_title
		from `tabLMS Quiz Submission` s
		inner join `tabLMS Quiz` q on q.name = s.quiz
		where s.member = %(user)s and s.course = %(course)s
		order by s.modified desc
		""",
		{
			"user": user,
			"course": frappe.db.get_value("Learning Module Config", learning_module, "lms_course"),
		},
		as_dict=True,
	)

	chapter_stats: dict[str, dict] = {}
	for chapter in chapters:
		chapter_stats[chapter.course_chapter or chapter.name] = {
			"chapter_profile": chapter.name,
			"chapter_title": chapter.chapter_title,
			"course_chapter": chapter.course_chapter,
			"attempts": 0,
			"correct": 0,
			"avg_time": 0.0,
			"mastery_pct": 0.0,
			"concept_tags": chapter.concept_tags or "",
		}

	concept_stats: dict[str, dict] = defaultdict(lambda: {"attempts": 0, "correct": 0})
	time_totals: dict[str, float] = defaultdict(float)
	time_counts: dict[str, int] = defaultdict(int)

	for row in attempts:
		key = row.course_chapter or "general"
		if key not in chapter_stats:
			chapter_stats[key] = {
				"chapter_profile": None,
				"chapter_title": key,
				"course_chapter": row.course_chapter,
				"attempts": 0,
				"correct": 0,
				"avg_time": 0.0,
				"mastery_pct": 0.0,
				"concept_tags": row.concept_tags or "",
			}
		chapter_stats[key]["attempts"] += 1
		if row.correct:
			chapter_stats[key]["correct"] += 1
		if row.time_seconds:
			time_totals[key] += float(row.time_seconds)
			time_counts[key] += 1
		for concept in _split_tags(row.concept_tags):
			concept_stats[concept]["attempts"] += 1
			if row.correct:
				concept_stats[concept]["correct"] += 1

	strengths: list[dict] = []
	weaknesses: list[dict] = []
	nodes: list[dict] = []
	links: list[dict] = []

	for key, stats in chapter_stats.items():
		if stats["attempts"]:
			stats["mastery_pct"] = round(100 * stats["correct"] / stats["attempts"], 1)
			if time_counts.get(key):
				stats["avg_time"] = round(time_totals[key] / time_counts[key], 1)
		nodes.append(
			{
				"id": key,
				"label": stats["chapter_title"],
				"mastery_pct": stats["mastery_pct"],
				"attempts": stats["attempts"],
				"group": _mastery_group(stats["mastery_pct"], stats["attempts"]),
			}
		)

	for concept, stats in concept_stats.items():
		if not stats["attempts"]:
			continue
		pct = round(100 * stats["correct"] / stats["attempts"], 1)
		entry = {"concept": concept, "mastery_pct": pct, "attempts": stats["attempts"]}
		if pct >= 75 and stats["attempts"] >= 3:
			strengths.append(entry)
		elif pct < 60 and stats["attempts"] >= 2:
			weaknesses.append(entry)

	strengths.sort(key=lambda x: (-x["mastery_pct"], -x["attempts"]))
	weaknesses.sort(key=lambda x: (x["mastery_pct"], -x["attempts"]))

	# Simple sequential learning path links between chapters.
	ordered_keys = [c.course_chapter or c.name for c in chapters]
	for idx in range(1, len(ordered_keys)):
		links.append({"source": ordered_keys[idx - 1], "target": ordered_keys[idx], "value": 1})

	return {
		"learning_module": learning_module,
		"user": user,
		"generated_at": now_datetime(),
		"chapter_stats": list(chapter_stats.values()),
		"strengths": strengths[:10],
		"weaknesses": weaknesses[:10],
		"visual_map": {"nodes": nodes, "links": links},
		"quiz_history": quiz_rows,
		"revision_recommendations": _revision_recommendations(weaknesses),
	}


def _split_tags(raw: str | None) -> list[str]:
	if not raw:
		return []
	return [
		part.strip()
		for part in raw.split(",")
		if part.strip() and not part.strip().lower().startswith("recall:")
	]


def _mastery_group(mastery_pct: float, attempts: int) -> str:
	if not attempts:
		return "not_started"
	if mastery_pct >= 75:
		return "strong"
	if mastery_pct >= 50:
		return "developing"
	return "needs_work"


def _revision_recommendations(weaknesses: list[dict]) -> list[str]:
	recommendations = []
	for item in weaknesses[:5]:
		recommendations.append(
			f"Review concept '{item['concept']}' ({item['mastery_pct']}% over {item['attempts']} attempts)."
		)
	if not recommendations:
		recommendations.append("Continue chapter MCQs and flashcards to build a fuller learning map.")
	return recommendations
