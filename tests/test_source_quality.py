"""Tests for database get_source_quality: neutral default, like ratio, last-20 window."""
import os
import tempfile
import unittest

import _bootstrap  # noqa: F401
import database as db


class SourceQualityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = db.DB_PATH
        db.DB_PATH = os.path.join(self._tmp, "t.db")
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._orig

    def _seed(self, rows):
        """Insert source_events rows: (action, swiped_at)."""
        conn = db.get_conn()
        try:
            for i, (action, swiped_at) in enumerate(rows):
                conn.execute(
                    "INSERT INTO source_events (card_id, source, action, swiped_at) VALUES (?,?,?,?)",
                    (f"c{i}", "techcrunch", action, swiped_at),
                )
            conn.commit()
        finally:
            conn.close()

    def test_unknown_source_defaults_to_neutral(self):
        self.assertAlmostEqual(db.get_source_quality("nosuchsource"), 0.5)

    def test_like_ratio_from_events(self):
        self._seed([("liked", "2026-08-01"), ("liked", "2026-08-02"),
                    ("disliked", "2026-08-03"), ("liked", "2026-08-04")])
        self.assertAlmostEqual(db.get_source_quality("TechCrunch"), 0.75)

    def test_keyword_is_lowercased(self):
        self._seed([("liked", "2026-08-01")])
        self.assertAlmostEqual(db.get_source_quality("TECHCRUNCH"),
                               db.get_source_quality("techcrunch"))

    def test_limit_20_window_ignores_oldest(self):
        # 5 disliked events with old dates + 20 liked with new dates = 1.0 (the 5 old
        # disliked fall outside DESC LIMIT 20, so only the 20 liked are counted).
        rows = (
            [("disliked", f"2026-07-{i:02d}") for i in range(1, 6)] +
            [("liked", f"2026-08-{i:02d}") for i in range(1, 21)]
        )
        self._seed(rows)
        self.assertAlmostEqual(db.get_source_quality("techcrunch"), 1.0)


if __name__ == "__main__":
    unittest.main()
