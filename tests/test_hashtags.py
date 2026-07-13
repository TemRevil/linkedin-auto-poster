"""Tests for post_generator.generate_hashtags (AI and custom-topic behavior)."""
import unittest

import _bootstrap  # noqa: F401
import config
from post_generator import generate_hashtags


class GenerateHashtagsTests(unittest.TestCase):
    def setUp(self):
        self._orig = config.get_content_topic

    def tearDown(self):
        config.get_content_topic = self._orig

    def _topic(self, t):
        config.get_content_topic = lambda: t

    def test_all_tags_start_with_hash_and_respect_max(self):
        self._topic("AI and technology")
        tags = generate_hashtags("New OpenAI model and agent framework", 3).split()
        self.assertTrue(tags)
        self.assertLessEqual(len(tags), 3)
        self.assertTrue(all(t.startswith("#") for t in tags))

    def test_non_ai_topic_gets_multiple_tags(self):
        # Regression for audit-5 3.D: custom topics used to get a lone #Topic.
        self._topic("urban gardening")
        tags = generate_hashtags("Raised bed composting techniques for balconies", 3).split()
        self.assertGreater(len(tags), 1)
        self.assertEqual(tags[0], "#UrbanGardening")

    def test_non_ai_thin_title_degrades_to_topic_tag(self):
        self._topic("urban gardening")
        self.assertEqual(generate_hashtags("the and for", 3), "#UrbanGardening")

    def test_max_count_is_upper_bound(self):
        self._topic("home finance")
        tags = generate_hashtags("Budgeting savings investing taxes retirement", 4).split()
        self.assertLessEqual(len(tags), 4)


if __name__ == "__main__":
    unittest.main()
