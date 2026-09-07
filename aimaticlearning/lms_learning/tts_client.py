"""Free Hugging Face serverless Inference API client for lesson-audio narration.

Configuration comes from site_config (or common_site_config.json, which
applies to every site on this bench) - never hardcode a token here:
    bench set-config -g huggingface_api_token "hf_..."
"""

from __future__ import annotations

import re

import frappe
import requests

HF_INFERENCE_URL = "https://api-inference.huggingface.co/models/{model}"
DEFAULT_MODEL = "hexgrad/Kokoro-82M"
DEFAULT_TIMEOUT = 120

# The free serverless tier has no documented hard character cap, but long
# single requests are slower and more likely to hit a gateway timeout or a
# cold-start 503. Chunking at paragraph/sentence boundaries keeps each call
# small and lets a single failed chunk be retried without redoing the rest.
MAX_CHUNK_CHARS = 600


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


def synthesize_chunk(text: str, model: str = DEFAULT_MODEL, timeout: int = DEFAULT_TIMEOUT) -> tuple[bytes, str]:
	"""Return (audio_bytes, content_type) for one chunk of text, or raise TTSError.

	Hugging Face's free serverless tier can return 503 while a model is
	loading (cold start). Callers running this from a background job should
	let frappe.enqueue's own retry handle transient failures rather than
	looping here.
	"""
	text = (text or "").strip()
	if not text:
		raise TTSError("No text to synthesize.")

	try:
		response = requests.post(
			HF_INFERENCE_URL.format(model=model),
			headers={
				"Authorization": f"Bearer {_get_api_token()}",
				"Content-Type": "application/json",
			},
			json={"inputs": text},
			timeout=timeout,
		)
	except requests.RequestException as e:
		raise TTSError(f"Hugging Face request failed: {e}")

	if response.status_code == 503:
		raise TTSError(f"Hugging Face model is loading (cold start), retry later: {response.text[:300]}")
	if response.status_code != 200:
		raise TTSError(f"Hugging Face returned {response.status_code}: {response.text[:300]}")

	content_type = response.headers.get("content-type", "")
	if "audio" not in content_type:
		raise TTSError(f"Unexpected Hugging Face response content-type: {content_type}")
	return response.content, content_type
