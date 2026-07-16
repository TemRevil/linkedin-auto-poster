"""Tests for news_discovery text helpers: strip_html, extract_keywords, clustering."""
import unittest

import _bootstrap  # noqa: F401
from news_discovery import strip_html, extract_keywords, _cluster_articles


class StripHtmlTests(unittest.TestCase):
    def test_removes_tags_and_collapses_whitespace(self):
        self.assertEqual(strip_html("<p>Hello   world</p>"), "Hello world")
        self.assertEqual(strip_html("<b>bold</b> and <i>italic</i>"), "bold and italic")

    def test_no_angle_brackets_remain(self):
        self.assertNotIn("<", strip_html("<div><span>hi</span></div>"))

    def test_empty(self):
        self.assertEqual(strip_html(""), "")

    def test_plain_text_untouched(self):
        self.assertEqual(strip_html("just words"), "just words")


class ExtractKeywordsTests(unittest.TestCase):
    def test_most_frequent_first(self):
        kws = extract_keywords("alpha alpha alpha beta gamma", top_n=3)
        self.assertEqual(kws[0], "alpha")

    def test_stopwords_excluded(self):
        self.assertNotIn("the", extract_keywords("the the the alpha beta", top_n=5))

    def test_respects_top_n(self):
        self.assertLessEqual(len(extract_keywords("alpha beta gamma delta epsilon", top_n=2)), 2)

    def test_empty_text(self):
        self.assertEqual(extract_keywords("", top_n=5), [])


class ClusterArticlesTests(unittest.TestCase):
    def test_near_duplicates_collapse_distinct_kept(self):
        arts = [
            {"title": "OpenAI releases new model", "summary": "short"},
            {"title": "OpenAI releases new model today", "summary": "a much longer summary body"},
            {"title": "Completely different subject matter entirely", "summary": "y"},
        ]
        out = _cluster_articles(arts)
        self.assertEqual(len(out), 2)  # the two OpenAI ones merge

    def test_keeps_longest_summary_in_cluster(self):
        arts = [
            {"title": "OpenAI releases new model", "summary": "short"},
            {"title": "OpenAI releases new model today", "summary": "a much longer summary body"},
        ]
        out = _cluster_articles(arts)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["summary"], "a much longer summary body")


if __name__ == "__main__":
    unittest.main()
