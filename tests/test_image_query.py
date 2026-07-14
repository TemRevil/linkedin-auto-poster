"""Tests for post_generator.suggest_image_query / get_image_urls."""
import unittest

import _bootstrap  # noqa: F401
from post_generator import suggest_image_query, get_image_urls


class SuggestImageQueryTests(unittest.TestCase):
    def test_normal_title(self):
        q = suggest_image_query({"title": "OpenAI launches new reasoning model"})
        self.assertTrue(q.endswith("technology"))
        self.assertFalse(q.startswith(" "))
        self.assertEqual(q, q.strip())

    def test_stopword_only_title_has_no_leading_space(self):
        # audit-8 3.C: empty word list must yield "technology", not " technology".
        self.assertEqual(suggest_image_query({"title": "the a is on"}), "technology")

    def test_empty_title(self):
        self.assertEqual(suggest_image_query({"title": ""}), "technology")


class GetImageUrlsTests(unittest.TestCase):
    def test_no_leading_dash_from_empty_query(self):
        urls = get_image_urls(suggest_image_query({"title": "the a is on"}))
        for name, url in urls.items():
            self.assertNotIn("/-", url, f"{name} URL has a leading-dash query: {url}")

    def test_spaces_become_hyphens(self):
        urls = get_image_urls("new reasoning model")
        self.assertTrue(any("new-reasoning-model" in u for u in urls.values()))


if __name__ == "__main__":
    unittest.main()
