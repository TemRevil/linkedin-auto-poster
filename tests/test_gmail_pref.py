"""Tests for news_discovery._extract_gmail_preferences (the revived signal)."""
import json
import pathlib
import tempfile
import unittest

import _bootstrap  # noqa: F401
import news_discovery as nd
import config


class GmailPreferenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp())
        self._orig_base, self._orig_posts = nd.BASE_DIR, config.POSTS_DIR
        nd.BASE_DIR = self._tmp          # cache_file + gmail_articles.json
        config.POSTS_DIR = self._tmp     # news_raw_*.json fallback glob

    def tearDown(self):
        nd.BASE_DIR, config.POSTS_DIR = self._orig_base, self._orig_posts

    def test_fallback_reads_gmail_items_from_news_raw(self):
        (self._tmp / "news_raw_20260719.json").write_text(json.dumps([
            {"type": "gmail", "title": "Weekly newsletter transformers agents", "summary": "retrieval"},
            {"type": "rss", "title": "unrelated headline", "summary": "x"},
        ]), encoding="utf-8")
        kw = nd._extract_gmail_preferences()
        self.assertTrue(kw)                      # signal is no longer dead
        self.assertNotIn("unrelated", kw)        # non-gmail items excluded

    def test_empty_when_no_sources(self):
        self.assertEqual(nd._extract_gmail_preferences(), {})

    def test_prefers_explicit_gmail_articles_file(self):
        (self._tmp / "gmail_articles.json").write_text(json.dumps([
            {"title": "Kubernetes operators deep dive", "summary": "controllers"},
        ]), encoding="utf-8")
        kw = nd._extract_gmail_preferences()
        self.assertTrue(any("kubernetes" in k for k in kw))

    def test_result_is_cached(self):
        (self._tmp / "news_raw_20260719.json").write_text(json.dumps([
            {"type": "gmail", "title": "Weekly newsletter transformers", "summary": "x"},
        ]), encoding="utf-8")
        nd._extract_gmail_preferences()
        self.assertTrue((self._tmp / "gmail_keyword_cache.json").exists())


if __name__ == "__main__":
    unittest.main()
