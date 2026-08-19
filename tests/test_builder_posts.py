"""Tests for builder_posts commit filtering and commit -> article mapping."""
import unittest

import _bootstrap  # noqa: F401
import builder_posts as bp


class SkipCommitTests(unittest.TestCase):
    def _skipped(self, msg):
        return bool(bp._SKIP_COMMIT_RE.match(msg))

    def test_merge_and_wip_are_skipped(self):
        for msg in ("Merge pull request #12 from x", "merge: main into dev",
                    "merge(deps): bump requests", "wip", "wip: refactor scorer"):
            with self.subTest(msg=msg):
                self.assertTrue(self._skipped(msg))

    def test_words_merely_starting_with_wip_are_kept(self):
        for msg in ("wipe stale cache entries", "Wiping temp dirs on boot",
                    "merged config loader into one helper"):
            with self.subTest(msg=msg):
                self.assertFalse(self._skipped(msg))

    def test_normal_commits_are_kept(self):
        self.assertFalse(self._skipped("fix: URL encode the search query"))


class CommitToArticleTests(unittest.TestCase):
    def test_maps_core_fields(self):
        a = bp.commit_to_article({
            "repo": "linkedin-auto-poster",
            "message": "add TF-IDF scoring",
            "url": "https://github.com/u/r/commit/abc",
            "sha": "abc12345",
            "date": "2026-08-19T10:00:00Z",
        })
        self.assertEqual(a["title"], "Builder update: add TF-IDF scoring")
        self.assertEqual(a["type"], "builder")
        self.assertEqual(a["source"], "GitHub/linkedin-auto-poster")
        self.assertEqual(a["link"], "https://github.com/u/r/commit/abc")
        self.assertEqual(a["commit_sha"], "abc12345")

    def test_summary_includes_description_and_language(self):
        a = bp.commit_to_article({
            "repo": "r", "message": "m",
            "repo_description": "A news poster", "language": "Python",
        })
        self.assertIn("A news poster", a["summary"])
        self.assertIn("Python", a["summary"])

    def test_full_message_only_added_when_it_differs(self):
        same = bp.commit_to_article({"repo": "r", "message": "m", "full_message": "m"})
        self.assertNotIn("Full message:", same["summary"])
        longer = bp.commit_to_article({"repo": "r", "message": "m",
                                       "full_message": "m\n\nwhy it matters"})
        self.assertIn("why it matters", longer["summary"])

    def test_empty_commit_does_not_raise(self):
        a = bp.commit_to_article({})
        self.assertEqual(a["source"], "GitHub/unknown")
        self.assertEqual(a["link"], "")


if __name__ == "__main__":
    unittest.main()
