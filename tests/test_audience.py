"""Audience classification boundaries and audience-insight scoring resilience."""
import io
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import _bootstrap  # noqa: F401

# connections_scraper imports playwright at module level for the live scrape,
# which is not installed in the test environment. Only the pure classification
# helpers are exercised here, so a stub module is enough.
if "playwright" not in sys.modules:
    sys.modules["playwright"] = types.ModuleType("playwright")
    _api = types.ModuleType("playwright.async_api")
    _api.async_playwright = lambda *a, **k: None
    sys.modules["playwright.async_api"] = _api

import connections_scraper as cs  # noqa: E402
import news_discovery as nd  # noqa: E402


class AudienceCategoryTests(unittest.TestCase):
    def setUp(self):
        # analyze_audience writes its result to BASE_DIR/audience_insights.json.
        # Point that at a temp file so running the suite never overwrites the
        # user's real analysis, and silence its console report.
        self._tmp = Path(tempfile.mkdtemp())
        self._orig = cs.CONNECTIONS_SUMMARY_FILE
        cs.CONNECTIONS_SUMMARY_FILE = self._tmp / "audience_insights.json"

    def tearDown(self):
        cs.CONNECTIONS_SUMMARY_FILE = self._orig

    def classify(self, headline):
        """Category a single headline lands in, via the real analyze_audience."""
        with redirect_stdout(io.StringIO()):
            result = cs.analyze_audience([{"headline": headline}])
        for cat, info in result["breakdown"].items():
            if info["count"]:
                return cat
        return "other"

    def test_marker_inside_a_word_does_not_claim_the_connection(self):
        # "ui" is a designers keyword and hides in "building"; "hr" is a
        # recruiters keyword and hides in "Bahrain". First match wins, so a
        # substring hit permanently mislabels the connection.
        for headline in ("Full Stack Developer building web apps",
                         "Backend Engineer at Bahrain Tech",
                         "Senior Developer | Building tools"):
            with self.subTest(headline=headline):
                self.assertEqual(self.classify(headline), "developers")

    def test_real_category_keywords_still_match(self):
        expected = {
            "UI/UX Designer": "designers",
            "Product Designer": "designers",
            "Technical Recruiter": "recruiters",
            "HR Business Partner": "recruiters",
            "ML Engineer": "data_ai",
            "Data Scientist": "data_ai",
            "CEO and Co-Founder": "founders_ceos",
            "Engineering Manager": "managers",
            "Computer Science Student": "students",
        }
        for headline, cat in expected.items():
            with self.subTest(headline=headline):
                self.assertEqual(self.classify(headline), cat)

    def test_punctuation_led_keywords_still_match(self):
        # ".net" cannot take a leading \b — a space followed by "." is not a
        # word boundary — so anchors are applied per keyword.
        for headline in ("ASP.NET Developer", "Next.js Engineer"):
            with self.subTest(headline=headline):
                self.assertEqual(self.classify(headline), "developers")

    def test_unclassifiable_headline_falls_through_to_other(self):
        self.assertEqual(self.classify("Passionate about hiking and coffee"), "other")


class SafePrintTests(unittest.TestCase):
    class _Cp1252Stdout(io.StringIO):
        """Stand-in for a Windows console that cannot render non-latin text."""
        encoding = "cp1252"

        def write(self, s):
            s.encode("cp1252")  # raises UnicodeEncodeError like the real console
            return super().write(s)

    def test_unrenderable_text_does_not_raise(self):
        out = self._Cp1252Stdout()
        with redirect_stdout(out):
            cs._safe_print("    القاهرة: 12x")
        self.assertIn("12x", out.getvalue())

    def test_plain_text_is_printed_verbatim(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cs._safe_print("    cairo: 12x")
        self.assertEqual(out.getvalue().strip(), "cairo: 12x")


class AudienceScoreResilienceTests(unittest.TestCase):
    BREAKDOWN = {"developers": {"percentage": 40,
                                "sample_headlines": ["Senior React engineer"]}}

    def _score(self, insights):
        return nd._calculate_score(
            ["engineer"], "Src", "rss",
            kw_weights={}, src_weights={}, interests=[], blacklist=[],
            gmail_prefs={}, audience_insights=insights,
            source_quality_cache={"Src": 0.5},
        )

    def test_clean_insights_contribute(self):
        clean = {"top_titles": [{"word": "engineer", "count": 5}],
                 "breakdown": self.BREAKDOWN}
        self.assertGreater(self._score(clean), 0.0)

    def test_malformed_top_titles_rows_are_skipped_not_fatal(self):
        clean = {"top_titles": [{"word": "engineer"}], "breakdown": self.BREAKDOWN}
        baseline = self._score(clean)
        for bad_row in ({"count": 3}, {"word": None}, "garbage"):
            with self.subTest(bad_row=bad_row):
                insights = {"top_titles": [{"word": "engineer"}, bad_row],
                            "breakdown": self.BREAKDOWN}
                # Previously the KeyError/AttributeError escaped both loops and
                # the blanket except zeroed the entire audience contribution.
                self.assertAlmostEqual(self._score(insights), baseline)

    def test_null_percentage_keeps_the_keyword_boost(self):
        insights = {"top_titles": [{"word": "engineer"}],
                    "breakdown": {"developers": {"percentage": None,
                                                 "sample_headlines": ["Senior React engineer"]}}}
        self.assertGreater(self._score(insights), 0.0)

    def test_null_sample_headlines_keeps_the_keyword_boost(self):
        insights = {"top_titles": [{"word": "engineer"}],
                    "breakdown": {"developers": {"percentage": 40,
                                                 "sample_headlines": None}}}
        self.assertAlmostEqual(self._score(insights), 0.5)

    def test_empty_insights_scores_zero(self):
        self.assertAlmostEqual(self._score({}), 0.0)


if __name__ == "__main__":
    unittest.main()
