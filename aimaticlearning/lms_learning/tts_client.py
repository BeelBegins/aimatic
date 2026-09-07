"""TTS clients for lesson-audio narration: Hugging Face primary, OpenRouter fallback.

Primary: Hugging Face Inference Providers, routed to DeepInfra, via the
huggingface_hub SDK. Kokoro-82M is not served by the old raw "serverless
Inference API" endpoint - HF now routes it through a partner provider.
Billing is pay-as-you-go through your HF account (~$0.80 per 1M characters
for this model, with a small monthly free credit included) - not literally
free, but trivial at this scale. See
https://huggingface.co/docs/inference-providers/pricing.

Fallback: OpenRouter's TTS endpoint (POST /api/v1/audio/speech, OpenAI-
compatible body), used only when the Hugging Face call fails. Reuses the
openrouter_api_key already configured on this bench for the Nemotron chat
assistant (apps/aimatic/aimatic/ai/nemotron_client.py) - no separate setup
needed. Same model, listed at ~$0.62/1M characters on OpenRouter.

Configuration comes from site_config (or common_site_config.json, which
applies to every site on this bench) - never hardcode a token here:
    bench set-config -g huggingface_api_token "hf_..."
    bench set-config -g openrouter_api_key "sk-or-..."
"""

from __future__ import annotations

import re

import frappe
import requests
from huggingface_hub import InferenceClient

HF_MODEL = "hexgrad/Kokoro-82M"
HF_PROVIDER = "deepinfra"
OPENROUTER_MODEL = "hexgrad/kokoro-82m"
OPENROUTER_TTS_URL = "https://openrouter.ai/api/v1/audio/speech"
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


def _synthesize_huggingface(text: str) -> bytes:
	token = frappe.conf.get("huggingface_api_token")
	if not token:
		raise TTSError(
			"huggingface_api_token is not configured. Set it with: "
			"bench set-config -g huggingface_api_token <your-token>"
		)
	client = InferenceClient(provider=HF_PROVIDER, api_key=token, timeout=DEFAULT_TIMEOUT)
	try:
		audio = client.text_to_speech(text, model=HF_MODEL)
	except Exception as e:
		raise TTSError(f"Hugging Face ({HF_PROVIDER}) request failed: {e}")
	if not audio:
		raise TTSError("Hugging Face returned no audio data.")
	return audio


def _synthesize_openrouter(text: str) -> bytes:
	api_key = frappe.conf.get("openrouter_api_key")
	if not api_key:
		raise TTSError(
			"openrouter_api_key is not configured. Set it with: "
			"bench set-config -g openrouter_api_key <your-key>"
		)
	try:
		response = requests.post(
			OPENROUTER_TTS_URL,
			headers={
				"Authorization": f"Bearer {api_key}",
				"Content-Type": "application/json",
			},
			json={"model": OPENROUTER_MODEL, "input": text, "response_format": "mp3"},
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
	"""Return audio bytes for one chunk of text, trying Hugging Face/DeepInfra
	first and falling back to OpenRouter (paid, no free credit) only if that
	fails. Raises TTSError with both failures reported if neither works.

	Callers running this from a background job should let frappe.enqueue's
	own retry handle transient provider failures rather than looping here.
	"""
	text = (text or "").strip()
	if not text:
		raise TTSError("No text to synthesize.")

	try:
		return _synthesize_huggingface(text)
	except TTSError as hf_error:
		try:
			return _synthesize_openrouter(text)
		except TTSError as openrouter_error:
			raise TTSError(
				f"Hugging Face failed ({hf_error}); OpenRouter fallback also failed ({openrouter_error})"
			)
