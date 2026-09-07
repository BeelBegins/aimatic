"""Lesson-audio narration: generates and serves cached TTS audio per lesson.

Generation is a manual, explicit action - a "Generate Audio" button on the
Course Lesson form in Desk (wired via public/js/course_lesson_audio_button.js,
hooks.py doctype_js) - never automatic on save. Audio is cached as attached
files once generated, so playback itself costs nothing further.

Everything about how/whether generation runs is controlled from Desk via the
Lesson Audio Settings doctype: a master enable switch, an explicit course
allowlist, and provider/fallback configuration (see tts_client.py). A lesson
outside the allowlist, or the feature being disabled entirely, refuses to
generate even if the button is clicked. See tasks/lms-lesson-audio/task.md.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import now_datetime, strip_html
from frappe.utils.file_manager import save_file
from lms.lms.md import markdown_to_html

from aimaticlearning.lms_learning.tts_client import (
	TTSError,
	estimate_cost,
	get_settings,
	sniff_audio_extension,
	split_into_chunks,
	synthesize_chunk,
)
from aimaticlearning.lms_learning.utils import throw_access_denied, user_can_access_course

CONTENT_ROLES = ["System Manager", "LMS Content Reviewer"]


def _require_content_role():
	if not set(frappe.get_roles()) & set(CONTENT_ROLES):
		throw_access_denied()


def _narration_text(body: str) -> str:
	html = markdown_to_html(body or "")
	return " ".join(strip_html(html).split())


def _content_hash(text: str) -> str:
	return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _course_enabled(course: str, settings=None) -> bool:
	settings = settings or get_settings()
	return course in {row.course for row in settings.enabled_courses}


@frappe.whitelist()
def get_generation_estimate(lesson: str) -> dict[str, Any]:
	"""Character count and estimated cost for one lesson, for the confirm
	dialog before any spend happens."""
	_require_content_role()
	lesson_row = frappe.db.get_value("Course Lesson", lesson, ["course", "body"], as_dict=True)
	if not lesson_row:
		frappe.throw(_("This lesson could not be found."), frappe.DoesNotExistError)

	settings = get_settings()
	text = _narration_text(lesson_row.body)
	estimate = estimate_cost(len(text), settings=settings)
	return {
		"enabled": bool(settings.enabled),
		"course_enabled": _course_enabled(lesson_row.course, settings=settings),
		"char_count": len(text),
		**estimate,
	}


@frappe.whitelist()
def get_status_for_lesson(lesson: str) -> dict[str, Any]:
	_require_content_role()
	audio = frappe.db.get_value(
		"Learning Lesson Audio", lesson, ["status", "generated_at", "error_message"], as_dict=True
	)
	if not audio:
		return {"status": "Not generated"}
	return dict(audio)


@frappe.whitelist()
def generate_audio_for_lesson(lesson: str, force: bool = False) -> dict[str, Any]:
	"""Explicit, manual trigger - the only way generation ever starts. Called
	by the "Generate Audio" button on the Course Lesson form."""
	_require_content_role()
	settings = get_settings()
	if not settings.enabled:
		frappe.throw(_("Lesson audio is disabled in Lesson Audio Settings."))

	lesson_doc = frappe.get_doc("Course Lesson", lesson)
	if not _course_enabled(lesson_doc.course, settings=settings):
		frappe.throw(
			_("{0} is not in the enabled-courses list in Lesson Audio Settings.").format(lesson_doc.course)
		)

	text = _narration_text(lesson_doc.body)
	if not text:
		frappe.throw(_("This lesson has no narratable text."))
	content_hash = _content_hash(text)

	existing_hash = frappe.db.get_value("Learning Lesson Audio", lesson, "content_hash")
	if existing_hash == content_hash and not force:
		return {"status": "unchanged", "message": _("Audio already matches the current lesson text.")}

	if frappe.db.exists("Learning Lesson Audio", lesson):
		audio_doc = frappe.get_doc("Learning Lesson Audio", lesson)
	else:
		audio_doc = frappe.new_doc("Learning Lesson Audio")
		audio_doc.lesson = lesson

	audio_doc.course = lesson_doc.course
	audio_doc.status = "Queued"
	audio_doc.content_hash = content_hash
	audio_doc.audio_chunks = None
	audio_doc.error_message = None
	audio_doc.save(ignore_permissions=True)

	frappe.enqueue(
		"aimaticlearning.lms_learning.lesson_audio.generate_lesson_audio_job",
		queue="long",
		lesson=lesson,
		content_hash=content_hash,
		enqueue_after_commit=True,
		job_name=f"Lesson audio generation {lesson}",
	)
	return {"status": "queued"}


def generate_lesson_audio_job(lesson: str, content_hash: str):
	if not frappe.db.exists("Learning Lesson Audio", lesson):
		return
	audio_doc = frappe.get_doc("Learning Lesson Audio", lesson)
	# Content (or a repeat click) may have changed the target hash again
	# since this job was queued; only the most recently enqueued generation
	# for this lesson should win.
	if audio_doc.content_hash != content_hash:
		return

	audio_doc.status = "Generating"
	audio_doc.save(ignore_permissions=True)
	frappe.db.commit()

	lesson_row = frappe.db.get_value("Course Lesson", lesson, ["title", "body"], as_dict=True)
	text = _narration_text(lesson_row.body if lesson_row else "")
	chunks = split_into_chunks(text)
	if not chunks:
		audio_doc.reload()
		audio_doc.status = "Failed"
		audio_doc.error_message = "No narratable text found in this lesson."
		audio_doc.save(ignore_permissions=True)
		return

	chunk_urls: list[str] = []
	try:
		for i, chunk_text in enumerate(chunks, start=1):
			audio_bytes = synthesize_chunk(chunk_text)
			extension = sniff_audio_extension(audio_bytes)
			file_doc = save_file(
				f"{frappe.scrub(lesson)}-audio-{i:03d}.{extension}",
				audio_bytes,
				"Learning Lesson Audio",
				lesson,
				is_private=0,
			)
			chunk_urls.append(file_doc.file_url)
	except TTSError as e:
		frappe.log_error(title="Lesson audio generation failed", message=str(e))
		audio_doc.reload()
		audio_doc.status = "Failed"
		audio_doc.error_message = str(e)[:1000]
		audio_doc.save(ignore_permissions=True)
		return

	audio_doc.reload()
	audio_doc.status = "Ready"
	audio_doc.audio_chunks = json.dumps(chunk_urls)
	audio_doc.generated_at = now_datetime()
	audio_doc.error_message = None
	audio_doc.save(ignore_permissions=True)


def _resolve_lesson_name(course: str, chapter: int, lesson: int) -> str | None:
	chapter_name = frappe.db.get_value("Chapter Reference", {"parent": course, "idx": chapter}, "chapter")
	if not chapter_name:
		return None
	return frappe.db.get_value("Lesson Reference", {"parent": chapter_name, "idx": lesson}, "lesson")


@frappe.whitelist()
def get_lesson_audio(course: str, chapter: int, lesson: int) -> dict[str, Any]:
	"""Return {"status": ...} and, once Ready, an ordered "chunks" list of
	audio file URLs for the caller to play back-to-back. Access is gated the
	same way as Study Buddy: enrolled/instructor/moderator only. This is the
	read path only - it never triggers generation."""
	if not course or len(course) > 140:
		frappe.throw(_("Invalid lesson context."), frappe.ValidationError)
	if not user_can_access_course(course, frappe.session.user):
		throw_access_denied()

	try:
		chapter = int(chapter)
		lesson = int(lesson)
	except (TypeError, ValueError):
		frappe.throw(_("Invalid lesson context."), frappe.ValidationError)

	lesson_name = _resolve_lesson_name(course, chapter, lesson)
	if not lesson_name:
		frappe.throw(_("This lesson could not be found."), frappe.DoesNotExistError)

	audio = frappe.db.get_value(
		"Learning Lesson Audio",
		lesson_name,
		["status", "audio_chunks"],
		as_dict=True,
	)
	if not audio:
		return {"status": "Unavailable"}

	chunks = []
	if audio.status == "Ready" and audio.audio_chunks:
		try:
			chunks = json.loads(audio.audio_chunks)
		except (TypeError, ValueError):
			chunks = []

	return {"status": audio.status, "chunks": chunks}
