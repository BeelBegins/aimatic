import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aimatic.ai.api import _get_free_audio_model, _is_zero_price, transcribe_audio
from aimatic.ai.nemotron_client import NemotronError, get_chat_completion


FREE_AUDIO_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"


class TestFreeAudioTranscription(FrappeTestCase):
	def test_zero_price_requires_an_explicit_numeric_zero(self):
		self.assertTrue(_is_zero_price("0"))
		self.assertFalse(_is_zero_price(None))
		self.assertFalse(_is_zero_price("0.000001"))

	@patch("aimatic.ai.api.frappe.cache")
	@patch("aimatic.ai.api.requests.get")
	def test_model_selector_rejects_paid_and_non_audio_models(self, get, cache):
		cache.return_value.get_value.return_value = None
		get.return_value = SimpleNamespace(
			status_code=200,
			text="ok",
			json=lambda: {"data": [
				{
					"id": "vendor/paid-audio",
					"pricing": {"prompt": "0.001", "completion": "0.001"},
					"architecture": {"input_modalities": ["audio"], "output_modalities": ["text"]},
				},
				{
					"id": "vendor/free-text:free",
					"pricing": {"prompt": "0", "completion": "0"},
					"architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
				},
				{
					"id": FREE_AUDIO_MODEL,
					"pricing": {"prompt": "0", "completion": "0"},
					"architecture": {"input_modalities": ["text", "audio"], "output_modalities": ["text"]},
				},
			]},
		)

		self.assertEqual(_get_free_audio_model(force_refresh=True), FREE_AUDIO_MODEL)
		cache.return_value.set_value.assert_called_once()

	@patch("aimatic.ai.api.frappe.cache")
	@patch("aimatic.ai.api.requests.get")
	def test_model_selector_has_no_paid_fallback(self, get, cache):
		cache.return_value.get_value.return_value = None
		get.return_value = SimpleNamespace(
			status_code=200,
			text="ok",
			json=lambda: {"data": [{
				"id": "vendor/paid-audio",
				"pricing": {"prompt": "0.001", "completion": "0.001"},
				"architecture": {"input_modalities": ["audio"], "output_modalities": ["text"]},
			}]},
		)
		with self.assertRaises(NemotronError):
			_get_free_audio_model(force_refresh=True)

	@patch("aimatic.ai.api.get_chat_completion")
	@patch("aimatic.ai.api._get_free_audio_model", return_value=FREE_AUDIO_MODEL)
	@patch("aimatic.ai.api._check_role")
	def test_transcription_sends_audio_with_reasoning_disabled(self, _role, _model, completion):
		completion.return_value = {
			"message": {"content": "show today's sales"},
			"provider": "Nvidia",
			"usage": {"prompt_tokens_details": {"audio_tokens": 100}},
		}
		audio = base64.b64encode(b"a" * 512).decode()

		result = transcribe_audio(audio, "wav", "auto")

		self.assertEqual(result["transcript"], "show today's sales")
		self.assertTrue(result["free"])
		kwargs = completion.call_args.kwargs
		self.assertEqual(kwargs["model"], FREE_AUDIO_MODEL)
		self.assertEqual(kwargs["reasoning"], {"enabled": False})
		self.assertTrue(kwargs["return_metadata"])
		self.assertEqual(kwargs["temperature"], 1.0)
		self.assertEqual(kwargs["messages"][0]["content"][1]["type"], "input_audio")

	@patch("aimatic.ai.api.get_chat_completion")
	@patch("aimatic.ai.api._get_free_audio_model", return_value=FREE_AUDIO_MODEL)
	@patch("aimatic.ai.api._check_role")
	def test_provider_that_drops_audio_is_rejected(self, _role, _model, completion):
		completion.return_value = {
			"message": {"content": "I cannot hear audio."},
			"provider": "Nvidia",
			"usage": {"prompt_tokens_details": {"audio_tokens": 0}},
		}
		audio = base64.b64encode(b"a" * 512).decode()
		with self.assertRaises(frappe.ValidationError):
			transcribe_audio(audio, "wav", "auto")

	@patch("aimatic.ai.api._check_role")
	def test_invalid_base64_is_rejected_before_openrouter(self, _role):
		with self.assertRaises(frappe.ValidationError):
			transcribe_audio("not-base64!", "wav", "auto")

	@patch("aimatic.ai.nemotron_client._check_enabled")
	@patch("aimatic.ai.nemotron_client._get_api_key", return_value="test-key")
	@patch("aimatic.ai.nemotron_client.requests.post")
	def test_openrouter_client_forwards_reasoning_and_timeout(self, post, _key, _enabled):
		response = Mock(status_code=200)
		response.json.return_value = {"choices": [{"message": {"content": "text"}}]}
		post.return_value = response

		get_chat_completion(
			[{"role": "user", "content": "test"}],
			model=FREE_AUDIO_MODEL,
			reasoning={"enabled": False},
			timeout=120,
		)

		payload = json.loads(post.call_args.kwargs["data"])
		self.assertEqual(payload["reasoning"], {"enabled": False})
		self.assertEqual(post.call_args.kwargs["timeout"], 120)
