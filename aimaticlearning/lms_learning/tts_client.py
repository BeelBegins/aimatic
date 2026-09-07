"""TTS clients for lesson-audio narration, fully driven by Lesson Audio
Settings (Desk-configurable: provider order, whether fallback is allowed at
all, models/voices, tokens, and the per-provider rates used for cost
estimates). No provider choice or credential is hardcoded here anymore -
Desk is the single source of truth for it.

Hugging Face routes through Inference Providers - Kokoro-82M is not served
by the old raw "serverless Inference API" endpoint. "deepinfra" is listed on
the model's HF page but does not implement text-to-speech in the installed
huggingface_hub SDK (confirmed by reading its provider registry: only ASR/
conversational/text-generation are wired for deepinfra) - "fal-ai" is what
actually works, at a real, verified $20/1M characters (fal.ai's own pricing
page), not the $0.80/1M DeepInfra rate this module originally assumed.

OpenRouter's TTS endpoint (POST /api/v1/audio/speech, OpenAI-compatible
body) is confirmed at ~$0.62/1M characters - about 25x cheaper. Tokens for
both live in Lesson Audio Settings (Password fields, encrypted at rest,
never logged or committed); the OpenRouter key falls back to the
openrouter_api_key already configured via bench set-config for the Nemotron
chat assistant if left blank in settings, so existing setup keeps working.
"""

from __future__ import annotations

import re

import frappe
import requests
from huggingface_hub import InferenceClient

OPENROUTER_TTS_URL = "https://openrouter.ai/api/v1/audio/speech"
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_CHUNK_CHARS = 600

_MAGIC_EXTENSIONS = (
	(b"RIFF", "wav"),
	(b"fLaC", "flac"),
	(b"OggS", "ogg"),
	(b"ID3", "mp3"),
)


class TTSError(Exception):
	pass


def get_settings():
	return frappe.get_cached_doc("Lesson Audio Settings")


def sniff_audio_extension(data: bytes) -> str:
	"""Identify the audio container from its magic bytes so the saved file
	gets a correct extension (Hugging Face's SDK returns raw bytes with no
	content-type; OpenRouter's mp3 response is also just sniffed for
	consistency rather than trusting response_format blindly)."""
	if data[:2] == b"\xff\xfb" or data[:2] == b"\xff\xf3":
		return "mp3"
	for magic, extension in _MAGIC_EXTENSIONS:
		if data.startswith(magic):
			return extension
	return "bin"


def split_into_chunks(text: str, max_chars: int | None = None) -> list[str]:
	"""Split plain narration text into speakable chunks, breaking on sentence
	boundaries where possible so playback doesn't cut off mid-sentence."""
	max_chars = max_chars or get_settings().max_chunk_chars or DEFAULT_MAX_CHUNK_CHARS
	text = " ".join((text or "").split())
	if not text:
		return []

	sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
	chunks: list[str] = []
	current = ""
	for sentence in sentences:
		candidate = f"{current} {sentence}".strip()
		if current and len(candidate) > max_chars:
			chunks.append(current)
			current = sentence
		else:
			current = candidate
	if current:
		chunks.append(current)
	return chunks


def estimate_cost(char_count: int, settings=None) -> dict:
	"""Estimated USD cost for synthesizing char_count characters with the
	currently configured primary provider, for a confirmation prompt before
	any spend happens."""
	settings = settings or get_settings()
	if settings.primary_provider == "Hugging Face":
		rate = settings.huggingface_rate_per_million_chars or 0
		provider = "Hugging Face"
	else:
		rate = settings.openrouter_rate_per_million_chars or 0
		provider = "OpenRouter"
	return {
		"provider": provider,
		"rate_per_million_chars": rate,
		"estimated_usd": round((char_count / 1_000_000) * rate, 4),
	}


def _synthesize_huggingface(text: str, settings) -> bytes:
	token = settings.get_password("huggingface_api_token", raise_exception=False) or frappe.conf.get(
		"huggingface_api_token"
	)
	if not token:
		raise TTSError(
			"No Hugging Face API token configured. Set it in Lesson Audio Settings, "
			"or bench set-config -g huggingface_api_token <your-token>"
		)
	provider = settings.huggingface_provider or "fal-ai"
	model = settings.huggingface_model or "hexgrad/Kokoro-82M"
	client = InferenceClient(provider=provider, api_key=token, timeout=DEFAULT_TIMEOUT)
	try:
		audio = client.text_to_speech(text, model=model)
	except Exception as e:
		raise TTSError(f"Hugging Face ({provider}) request failed: {e}")
	if not audio:
		raise TTSError("Hugging Face returned no audio data.")
	return audio


def _synthesize_openrouter(text: str, settings) -> bytes:
	api_key = settings.get_password("openrouter_api_key", raise_exception=False) or frappe.conf.get(
		"openrouter_api_key"
	)
	if not api_key:
		raise TTSError(
			"No OpenRouter API key configured. Set it in Lesson Audio Settings, "
			"or bench set-config -g openrouter_api_key <your-key>"
		)
	model = settings.openrouter_model or "hexgrad/kokoro-82m"
	voice = settings.openrouter_voice or "af_heart"
	try:
		response = requests.post(
			OPENROUTER_TTS_URL,
			headers={
				"Authorization": f"Bearer {api_key}",
				"Content-Type": "application/json",
			},
			json={"model": model, "input": text, "voice": voice, "response_format": "mp3"},
			timeout=DEFAULT_TIMEOUT,
		)
	except requests.RequestException as e:
		raise TTSError(f"OpenRouter TTS request failed: {e}")

	if response.status_code != 200:
		raise TTSError(f"OpenRouter TTS returned {response.status_code}: {response.text[:300]}")
	if not response.content:
		raise TTSError("OpenRouter TTS returned no audio data.")
	return response.content


def synthesize_chunk(text: str) -> bytes:
	"""Return audio bytes for one chunk of text using the Desk-configured
	primary provider. Only tries the other provider if Enable Fallback to
	Other Provider is checked in Lesson Audio Settings - otherwise a primary
	failure just fails, with no surprise spend on the non-primary provider.
	"""
	text = (text or "").strip()
	if not text:
		raise TTSError("No text to synthesize.")

	settings = get_settings()
	primary = _synthesize_huggingface if settings.primary_provider == "Hugging Face" else _synthesize_openrouter
	secondary = _synthesize_openrouter if primary is _synthesize_huggingface else _synthesize_huggingface

	try:
		return primary(text, settings)
	except TTSError as primary_error:
		if not settings.enable_fallback:
			raise
		try:
			return secondary(text, settings)
		except TTSError as secondary_error:
			raise TTSError(f"Primary provider failed ({primary_error}); fallback also failed ({secondary_error})")
