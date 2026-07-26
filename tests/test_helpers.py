"""Tests for the small shared helpers extracted during refactoring."""
import unittest
from datetime import datetime

import _bootstrap  # noqa: F401
from news_discovery import _title_tokens
from humanizer import _reduce_em_dashes
from database import _delta_days


class TitleTokensTests(unittest.TestCase):
    def test_lowercases_and_drops_stopwords(self):
        toks = _title_tokens("The New Postgres Model")
        self.assertIn("postgres", toks)
        self.assertIn("model", toks)
        self.assertNotIn("the", toks)   # stop word
        self.assertNotIn("new", toks)   # stop word

    def test_returns_set(self):
        self.assertIsInstance(_title_tokens("alpha alpha beta"), set)


class ReduceEmDashesTests(unittest.TestCase):
    def test_single_dash_is_noop(self):
        self.assertEqual(_reduce_em_dashes("a—b"), "a—b")

    def test_later_dashes_become_period_space(self):
        self.assertEqual(_reduce_em_dashes("a—b—c"), "a—b. c")
        self.assertEqual(_reduce_em_dashes("a—b—c").count("—"), 1)

    def test_no_dashes(self):
        self.assertEqual(_reduce_em_dashes("plain text"), "plain text")


class DeltaDaysTests(unittest.TestCase):
    def test_valid_iso(self):
        now = datetime(2026, 7, 26, 16, 0, 0)
        self.assertAlmostEqual(_delta_days("2026-07-25T16:00:00", now), 1.0, places=4)

    def test_unparseable_returns_zero(self):
        self.assertEqual(_delta_days("not-a-date", datetime.now()), 0.0)

    def test_none_returns_zero(self):
        self.assertEqual(_delta_days(None, datetime.now()), 0.0)


if __name__ == "__main__":
    unittest.main()
