"""Tests for humanizer.humanize_text / detect_ai_patterns."""
import unittest

import _bootstrap  # noqa: F401  (puts src/ on sys.path)
from humanizer import humanize_text, detect_ai_patterns


class HumanizeTextTests(unittest.TestCase):
    def test_significance_words_are_softened(self):
        out = humanize_text("This is groundbreaking and revolutionary")
        self.assertIn("notable", out)
        self.assertIn("new", out)
        self.assertNotIn("groundbreaking", out.lower())

    def test_first_em_dash_kept_rest_get_period_space(self):
        # Non-first em-dashes must not run words together (audit-7 3.A).
        out = humanize_text("foo—bar—baz")
        self.assertEqual(out.count("—"), 1)        # only the first survives
        self.assertNotIn(".bar", out)
        self.assertNotIn("bar.baz", out)
        self.assertIn("bar. baz", out)

    def test_repeated_exclamations_collapse(self):
        self.assertNotIn("!!", humanize_text("Amazing news!!!"))

    def test_filler_transition_removed(self):
        self.assertNotIn("Moreover", humanize_text("Moreover, this matters."))

    def test_sycophantic_opener_removed(self):
        out = humanize_text("Great question. Here is the answer.")
        self.assertFalse(out.lower().startswith("great question"))

    def test_returns_stripped_string(self):
        self.assertEqual(humanize_text("  hi  "), "hi")


class DetectPatternsTests(unittest.TestCase):
    def test_detects_significance_inflation(self):
        names = {f["pattern"] for f in detect_ai_patterns("a groundbreaking result")}
        self.assertIn("significance_inflation", names)

    def test_clean_text_has_no_findings(self):
        self.assertEqual(detect_ai_patterns("I shipped a small fix today."), [])


if __name__ == "__main__":
    unittest.main()
