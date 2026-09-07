import unittest

from aimaticlearning.lms_learning.revision import (
	apply_rating_filter,
	encode_recall_tags,
	latest_ratings_from_attempts,
	parse_recall_rating,
)


class TestRevisionRatings(unittest.TestCase):
	def test_encode_and_parse_explicit_rating(self):
		tags = encode_recall_tags("Hard", "Contract formation")
		self.assertTrue(tags.startswith("recall:hard"))
		self.assertEqual(parse_recall_rating(tags, correct=1), "hard")

	def test_legacy_correct_flag(self):
		self.assertEqual(parse_recall_rating("Offer and acceptance", 0), "hard")
		self.assertEqual(parse_recall_rating("Offer and acceptance", 1), "good")
		self.assertIsNone(parse_recall_rating("Offer and acceptance"))

	def test_latest_attempt_wins(self):
		ratings = latest_ratings_from_attempts(
			[
				{"learning_flashcard": "FC-1", "concept_tags": "recall:easy", "correct": 1},
				{"learning_flashcard": "FC-1", "concept_tags": "recall:hard", "correct": 0},
				{"learning_flashcard": "FC-2", "concept_tags": "recall:good", "correct": 1},
			]
		)
		self.assertEqual(ratings, {"FC-1": "easy", "FC-2": "good"})

	def test_filter_hard_and_unreviewed(self):
		cards = [{"name": "FC-1"}, {"name": "FC-2"}, {"name": "FC-3"}]
		ratings = {"FC-1": "hard", "FC-2": "easy"}
		hard = apply_rating_filter(cards, ratings, "hard")
		self.assertEqual([card["name"] for card in hard], ["FC-1"])
		unreviewed = apply_rating_filter(list(cards), ratings, "unreviewed")
		self.assertEqual([card["name"] for card in unreviewed], ["FC-3"])
