"""Tests for news_discovery._calculate_score (DB-free / file-free paths).

Passing gmail_prefs={} and audience_insights={} (falsy-but-not-None) skips the
file-read fallbacks, and source_quality_cache={source: quality} keeps
db.get_source_quality from ever being called — so scoring is deterministic.
"""
import unittest

import _bootstrap  # noqa: F401
import news_discovery as nd


def score(keywords, source="Src", atype="rss", kw_weights=None, src_weights=None,
          interests=None, blacklist=None, quality=0.5):
    return nd._calculate_score(
        keywords, source, atype,
        kw_weights or {}, src_weights or {}, interests or [], blacklist or [],
        gmail_prefs={}, audience_insights={},
        source_quality_cache={source: quality},
    )


class CalculateScoreTests(unittest.TestCase):
    def test_interest_boost(self):
        self.assertAlmostEqual(score(["ai"], interests=["ai"]), 3.0)

    def test_blacklist_penalty(self):
        self.assertAlmostEqual(score(["crypto"], blacklist=["crypto"]), -10.0)

    def test_keyword_weight_added(self):
        self.assertAlmostEqual(score(["ml"], kw_weights={"ml": 2.5}), 2.5)

    def test_source_weight_lowercased(self):
        self.assertAlmostEqual(score([], source="TechCrunch",
                                     src_weights={"techcrunch": 1.0}), 1.0)

    def test_research_bonus(self):
        self.assertAlmostEqual(score([], atype="research"), 1.5)

    def test_low_quality_penalty(self):
        self.assertAlmostEqual(score([], quality=0.2), -1.5)

    def test_neutral_is_zero(self):
        self.assertAlmostEqual(score([]), 0.0)


if __name__ == "__main__":
    unittest.main()
