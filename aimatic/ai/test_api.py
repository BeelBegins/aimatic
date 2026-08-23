import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aimatic.ai.api import (
	_get_free_audio_model,
	_is_zero_price,
	export_table,
	refresh_saved_report,
	save_report,
	transcribe_audio,
)
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
			json=lambda: {
				"data": [
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
						"architecture": {
							"input_modalities": ["text", "audio"],
							"output_modalities": ["text"],
						},
					},
				]
			},
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
			json=lambda: {
				"data": [
					{
						"id": "vendor/paid-audio",
						"pricing": {"prompt": "0.001", "completion": "0.001"},
						"architecture": {"input_modalities": ["audio"], "output_modalities": ["text"]},
					}
				]
			},
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

	@patch("aimatic.ai.api._check_role")
	@patch("aimatic.ai.api.frappe.get_doc")
	def test_saved_report_snapshot_preserves_repeated_invocations(self, get_doc, _role):
		get_doc.return_value.insert.return_value = SimpleNamespace(name="AI-SAVED-1")
		invocations = [
			{"call_id": "current", "tool_name": "get_sales_overview", "sequence": 1},
			{"call_id": "previous", "tool_name": "get_sales_overview", "sequence": 2},
		]
		result = save_report(
			"Compare sales",
			"{}",
			json.dumps({"tool_invocations": invocations}),
		)
		self.assertEqual(result["name"], "AI-SAVED-1")
		payload = get_doc.call_args.args[0]
		self.assertEqual(json.loads(payload["tool_results_snapshot"]), invocations)

	@patch("aimatic.ai.api._check_role")
	@patch("aimatic.ai.api._check_saved_report_ownership")
	@patch("aimatic.ai.api.ask")
	@patch("aimatic.ai.api.frappe.get_doc")
	def test_saved_report_refresh_replaces_invocation_snapshot(self, get_doc, ask, _ownership, _role):
		doc = SimpleNamespace(
			question="Compare sales",
			response_snapshot=json.dumps({"kpis": [{"value": 1}]}),
			tool_results_snapshot="[]",
			last_refreshed=None,
			save=Mock(),
		)
		get_doc.return_value = doc
		invocations = [{"call_id": "new", "tool_name": "get_sales_overview", "sequence": 1}]
		ask.return_value = {
			"kpis": [{"value": 2}],
			"charts": [],
			"tables": [],
			"tool_invocations": invocations,
		}
		refresh_saved_report("AI-SAVED-1")
		self.assertEqual(json.loads(doc.tool_results_snapshot), invocations)
		doc.save.assert_called_once_with(ignore_permissions=True)

	@patch("aimatic.ai.api._check_role")
	@patch("aimatic.ai.api.build_csv_response")
	def test_export_uses_exact_structured_table_values(self, build_csv, _role):
		table = {
			"columns": [
				{"key": "scenario", "label": "Scenario"},
				{"key": "value", "label": "Value"},
			],
			"rows": [
				{"scenario": "Current", "value": 120},
				{"scenario": "Previous", "value": 100},
			],
		}
		export_table(json.dumps(table), "comparison.csv", "csv")
		build_csv.assert_called_once_with(
			[["Scenario", "Value"], ["Current", 120], ["Previous", 100]],
			"comparison",
		)
