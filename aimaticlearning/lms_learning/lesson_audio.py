"""Lesson-audio narration: generates and serves cached TTS audio per lesson.

Audio is generated once per lesson (on save, when the narration text actually
changed) and cached as attached files - never synthesized on request - so
playback costs nothing beyond the one-time generation call.

Regeneration is gated to an explicit course allowlist
(frappe.conf `lesson_audio_enabled_courses`, defaulting to just
property-practice) so editing lesson content anywhere else in the LMS never
silently starts spending Hugging Face quota. See tasks/lms-lesson-audio/task.md.
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

from aimaticlearning.lms_learning.tts_client import TTSError, split_into_chunks, synthesize_chunk
from aimaticlearning.lms_learning.utils import throw_access_denied, user_can_access_course

DEFAULT_ENABLED_COURSES = ["property-practice"]


def _enabled_courses() -> set[str]:
	configured = frappe.conf.get("lesson_audio_enabled_courses")
	return set(configured or DEFAULT_ENABLED_COURSES)


def _narration_text(body: str) -> str:
	html = markdown_to_html(body or "")
	return " ".join(strip_html(html).split())


def _content_hash(text: str) -> str:
	return hashlib.sha256(text.encode("utf-8")).hexdigest()


def enqueue_lesson_audio_regeneration(doc, method=None):
	"""Course Lesson on_update hook: (re)generate audio only for lessons in
	an explicitly enabled course, and only when the narration text actually
	changed since the last generation."""
	if doc.course not in _enabled_courses():
		return

	text = _narration_text(doc.body)
	if not text:
		return
	content_hash = _content_hash(text)

	if frappe.db.get_value("Learning Lesson Audio", doc.name, "content_hash") == content_hash:
		return

	if frappe.db.exists("Learning Lesson Audio", doc.name):
		audio_doc = frappe.get_doc("Learning Lesson Audio", doc.name)
	else:
		audio_doc = frappe.new_doc("Learning Lesson Audio")
		audio_doc.lesson = doc.name

	audio_doc.course = doc.course
	audio_doc.status = "Queued"
	audio_doc.content_hash = content_hash
	audio_doc.audio_chunks = None
	audio_doc.error_message = None
	audio_doc.save(ignore_permissions=True)

	frappe.enqueue(
		"aimaticlearning.lms_learning.lesson_audio.generate_lesson_audio_job",
		queue="long",
		lesson=doc.name,
		content_hash=content_hash,
		enqueue_after_commit=True,
		job_name=f"Lesson audio generation {doc.name}",
	)


def generate_lesson_audio_job(lesson: str, content_hash: str):
	if not frappe.db.exists("Learning Lesson Audio", lesson):
		return
	audio_doc = frappe.get_doc("Learning Lesson Audio", lesson)
	# Content may have changed again since this job was queued; only the
	# most recently enqueued generation for this lesson should win.
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
			audio_bytes, content_type = synthesize_chunk(chunk_text)
			extension = "flac" if "flac" in content_type else "wav" if "wav" in content_type else "bin"
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
	same way as Study Buddy: enrolled/instructor/moderator only."""
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
