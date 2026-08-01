"""Tests for post_generator pure helpers: card->article mapping, image heuristic."""
import unittest

import _bootstrap  # noqa: F401
from post_generator import _card_to_article, determine_needs_image


class CardToArticleTests(unittest.TestCase):
    def test_maps_url_to_link_and_keeps_fields(self):
        art = _card_to_article({
            "title": "T", "summary": "S", "url": "http://u",
            "source": "Src", "type": "research",
        })
        self.assertEqual(art["title"], "T")
        self.assertEqual(art["summary"], "S")
        self.assertEqual(art["link"], "http://u")   # url -> link
        self.assertEqual(art["source"], "Src")
        self.assertEqual(art["type"], "research")

    def test_missing_fields_get_defaults(self):
        art = _card_to_article({})
        self.assertEqual(art["title"], "")
        self.assertEqual(art["link"], "")
        self.assertEqual(art["type"], "rss")


class DetermineNeedsImageTests(unittest.TestCase):
    def test_visual_topic_wants_image(self):
        self.assertTrue(determine_needs_image(
            {"title": "New robot chip hardware demo", "summary": ""}))

    def test_text_topic_does_not(self):
        self.assertFalse(determine_needs_image(
            {"title": "New policy on AI regulation and ethics", "summary": ""}))

    def test_neutral_topic_defaults_false(self):
        # No visual or text keywords -> visual_score (0) > text_score (0) is False.
        self.assertFalse(determine_needs_image({"title": "hello world", "summary": ""}))


if __name__ == "__main__":
    unittest.main()
