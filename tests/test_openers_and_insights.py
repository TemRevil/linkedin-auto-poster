"""Sycophantic-opener handling and the post_generator audience-insights guard."""
import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
import humanizer as h
import post_generator as pg


class SycophanticOpenerTests(unittest.TestCase):
    def _still_flagged(self, text):
        return any(f["pattern"] == "sycophantic_opener"
                   for f in h.detect_ai_patterns(text))

    def test_every_opener_is_removed_not_just_detected(self):
        # Detection and removal must agree: nothing humanizer reports may
        # survive humanize_text.
        for opener in h.SYCOPHANTIC_OPENERS:
            text = f"{opener}. The model ships today."
            with self.subTest(opener=opener):
                self.assertTrue(self._still_flagged(text))
                cleaned = h.humanize_text(text)
                self.assertFalse(self._still_flagged(cleaned))
                self.assertTrue(cleaned.startswith("The model ships"))

    def test_comma_form_is_removed(self):
        self.assertEqual(
            h.humanize_text("That's a great question, and the answer is no."),
            "and the answer is no.",
        )

    def test_longest_phrase_wins_no_dangling_word(self):
        self.assertEqual(
            h.humanize_text("That's a great point. Latency dropped."),
            "Latency dropped.",
        )

    def test_opener_without_punctuation_is_left_alone(self):
        # Not a preamble — an ordinary sentence that starts with the same word.
        for text in ("Absolutely no regressions this week.",
                     "Of course the build is green."):
            with self.subTest(text=text):
                self.assertEqual(h.humanize_text(text), text)
                self.assertFalse(self._still_flagged(text))


class LoadAudienceInsightsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._orig = pg.AUDIENCE_FILE
        pg.AUDIENCE_FILE = self._tmp / "audience_insights.json"

    def tearDown(self):
        pg.AUDIENCE_FILE = self._orig

    def test_missing_file_is_empty_dict(self):
        self.assertEqual(pg.load_audience_insights(), {})

    def test_valid_file_is_returned(self):
        pg.AUDIENCE_FILE.write_text(json.dumps({"total": 5}), encoding="utf-8")
        self.assertEqual(pg.load_audience_insights(), {"total": 5})

    def test_corrupt_file_degrades_to_empty_dict(self):
        pg.AUDIENCE_FILE.write_text('{"total": 5', encoding="utf-8")
        self.assertEqual(pg.load_audience_insights(), {})

    def test_non_dict_payload_degrades_to_empty_dict(self):
        pg.AUDIENCE_FILE.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(pg.load_audience_insights(), {})


if __name__ == "__main__":
    unittest.main()
