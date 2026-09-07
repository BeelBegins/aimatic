"""Hugging Face Inference Providers client for lesson-audio narration.

Kokoro-82M is not served by the old raw "serverless Inference API" endpoint;
Hugging Face now routes it through a partner provider (DeepInfra) via the
huggingface_hub SDK. Billing is pay-as-you-go through your HF account
(~$0.80 per 1M characters for this model, with a small monthly free credit
included) - not literally free, but trivial at this scale. See
https://huggingface.co/docs/inference-providers/pricing.

Configuration comes from site_config (or common_site_config.json, which
applies to every site on this bench) - never hardcode a token here:
    bench set-config -g huggingface_api_token "hf_..."
"""

from __future__ import annotations

import re

import frappe
from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError

DEFAULT_MODEL = "hexgrad/Kokoro-82M"
DEFAULT_PROVIDER = "deepinfra"
DEFAULT_TIMEOUT = 120

# No documented hard character cap, but long single requests are slower and
# more likely to time out or fail outright. Chunking at sentence boundaries
# keeps each call small and lets one failed chunk be retried without redoing
# the rest of the lesson.
MAX_CHUNK_CHARS = 600

_MAGIC_EXTENSIONS = (
	(b"RIFF", "wav"),
	(b"fLaC", "flac"),
	(b"OggS", "ogg"),
	(b"ID3", "mp3"),
)


class TTSError(Exception):
	pass


def _get_api_token() -> str:
	token = frappe.conf.get("huggingface_api_token")
	if not token:
		raise TTSError(
			"huggingface_api_token is not configured. Set it with: "
			"bench set-config -g huggingface_api_token <your-token>"
		)
	return token


def _get_client(provider: str) -> InferenceClient:
	return InferenceClient(provider=provider, api_key=_get_api_token(), timeout=DEFAULT_TIMEOUT)


def sniff_audio_extension(data: bytes) -> str:
	"""Identify the audio container from its magic bytes so the saved file
	gets a correct extension (the SDK returns raw bytes with no content-type)."""
	if data[:2] == b"\xff\xfb" or data[:2] == b"\xff\xf3":
		return "mp3"
	for magic, extension in _MAGIC_EXTENSIONS:
		if data.startswith(magic):
			return extension
	return "bin"


def split_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
	"""Split plain narration text into speakable chunks, breaking on sentence
	boundaries where possible so playback doesn't cut off mid-sentence."""
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


def synthesize_chunk(text: str, model: str = DEFAULT_MODEL, provider: str = DEFAULT_PROVIDER) -> bytes:
	"""Return audio bytes for one chunk of text, or raise TTSError.

	Callers running this from a background job should let frappe.enqueue's
	own retry handle transient provider failures rather than looping here.
	"""
	text = (text or "").strip()
	if not text:
		raise TTSError("No text to synthesize.")

	try:
		audio = _get_client(provider).text_to_speech(text, model=model)
	except HfHubHTTPError as e:
		raise TTSError(f"Hugging Face ({provider}) request failed: {e}")
	except Exception as e:
		raise TTSError(f"Hugging Face ({provider}) request failed: {e}")

	if not audio:
		raise TTSError("Hugging Face returned no audio data.")
	return audio
