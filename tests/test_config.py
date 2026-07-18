"""Tests for config: get_setting, topic resolution, and query derivation."""
import json
import pathlib
import tempfile
import unittest

import _bootstrap  # noqa: F401
import config


class GetSettingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp())
        self._orig = config.SETTINGS_FILE
        config.SETTINGS_FILE = self._tmp / "settings.json"

    def tearDown(self):
        config.SETTINGS_FILE = self._orig

    def test_reads_user_value(self):
        config.SETTINGS_FILE.write_text(json.dumps({"max_cards_per_day": 42}), encoding="utf-8")
        self.assertEqual(config.get_setting("max_cards_per_day"), 42)

    def test_default_when_absent(self):
        config.SETTINGS_FILE.write_text(json.dumps({}), encoding="utf-8")
        self.assertEqual(config.get_setting("does_not_exist", "fallback"), "fallback")

    def test_known_key_default_from_schema(self):
        config.SETTINGS_FILE.write_text(json.dumps({}), encoding="utf-8")
        self.assertEqual(config.get_setting("default_post_type"), "news")


class QueryDerivationTests(unittest.TestCase):
    def setUp(self):
        self._get_setting, self._topic = config.get_setting, config.get_content_topic

    def tearDown(self):
        config.get_setting, config.get_content_topic = self._get_setting, self._topic

    def test_custom_topic_drives_search_queries(self):
        config.get_setting = lambda k, d=None: []          # no custom_search_queries
        config.get_content_topic = lambda: "cybersecurity"
        qs = config.get_search_queries()
        self.assertTrue(any("cybersecurity" in q.lower() for q in qs))

    def test_ai_topic_uses_default_queries(self):
        config.get_setting = lambda k, d=None: []
        config.get_content_topic = lambda: "AI and technology"
        self.assertEqual(config.get_search_queries(), config.AI_SEARCH_QUERIES)

    def test_gmail_query_includes_topic_words(self):
        config.get_content_topic = lambda: "home finance"
        q = config.get_gmail_search_query()
        self.assertIn("subject:", q)
        self.assertIn("finance", q.lower())


if __name__ == "__main__":
    unittest.main()
