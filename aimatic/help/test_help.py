"""Unit tests for Desk Help retrieval and prompts (no BI console coupling)."""

from __future__ import annotations

import json
import unittest

from aimatic.help.prompt import DOCTYPE_TO_MODULE, build_system_prompt, infer_module
from aimatic.help.retriever import score_topic
from aimatic.help.tools import TOOL_DISPATCH, TOOL_SPECS


class TestHelpPrompt(unittest.TestCase):
	def test_infer_module_from_doctype(self):
		self.assertEqual(infer_module("Item"), "Item")
		self.assertEqual(infer_module("Payment Entry"), "Accounts")
		self.assertEqual(infer_module("Price List"), "Price List")

	def test_infer_module_from_message(self):
		self.assertEqual(infer_module(None, "how do I create a stock entry"), "Stock")
		self.assertEqual(infer_module(None, "set selling price list"), "Price List")

	def test_system_prompt_blocks_secrets(self):
		prompt = build_system_prompt({"doctype": "Item"}, [])
		self.assertIn("FBR Integration Settings", prompt)
		self.assertIn("Never execute", prompt)
		self.assertIn("Item", prompt)

	def test_doctype_map_has_core_retail(self):
		for dt in ("Item", "Item Price", "Stock Entry", "Purchase Order", "Payment Entry"):
			self.assertIn(dt, DOCTYPE_TO_MODULE)


class TestHelpRetrieverScoring(unittest.TestCase):
	def test_doctype_match_scores_highest(self):
		topic = {
			"title": "Create Item",
			"module": "Item",
			"doctypes": "Item, Item Group",
			"tags": "item",
			"body": "Create an item",
			"starter_questions": "",
			"priority": 10,
		}
		with_doctype = score_topic(topic, message="help", doctype="Item", module=None)
		without = score_topic(topic, message="help", doctype="Payment Entry", module="Accounts")
		self.assertGreater(with_doctype, without)


class TestHelpTools(unittest.TestCase):
	def test_tool_specs_are_help_only(self):
		names = {spec["function"]["name"] for spec in TOOL_SPECS}
		self.assertEqual(names, {"search_help", "get_topic", "list_related_doctypes"})
		# Never expose BI analytics tools
		self.assertNotIn("run_report", names)

	def test_list_related_doctypes_item(self):
		result = TOOL_DISPATCH["list_related_doctypes"]({"doctype": "Item"})
		urls = [row["url"] for row in result["links"]]
		self.assertTrue(any("/app/item" == u or u.startswith("/app/item") for u in urls))


class TestHelpApiHelpers(unittest.TestCase):
	def test_parse_context_json(self):
		from aimatic.help.api import _parse_context

		ctx = _parse_context(
			json.dumps(
				{
					"doctype": "Item",
					"route": "Form/Item/ITEM-1",
					"help_links": [{"label": "Docs", "url": "https://example.com"}],
				}
			)
		)
		self.assertEqual(ctx["doctype"], "Item")
		self.assertEqual(ctx["module"], "Item")
		self.assertEqual(ctx["help_links"][0]["label"], "Docs")

	def test_ask_module_separate_from_bi_tools(self):
		from aimatic.help import tools as help_tools
		from aimatic.ai import api as bi_api

		help_names = {s["function"]["name"] for s in help_tools.TOOL_SPECS}
		bi_names = {s["function"]["name"] for s in bi_api.TOOL_SPECS}
		self.assertTrue(help_names.isdisjoint(bi_names))
		self.assertIsNot(help_tools.TOOL_SPECS, bi_api.TOOL_SPECS)
