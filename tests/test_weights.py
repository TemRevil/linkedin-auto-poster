"""Single-item vs batch weight agreement for keyword and source scoring."""
import os
import tempfile
import unittest

import _bootstrap  # noqa: F401
import database as db


class WeightAgreementTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = db.DB_PATH
        db.DB_PATH = os.path.join(self._tmp, "t.db")
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._orig

    def _seed_swipes(self, rows):
        conn = db.get_conn()
        try:
            conn.execute("INSERT INTO meta (key, value) VALUES ('total_docs', '100')")
            conn.execute(
                "INSERT INTO corpus_stats (keyword, doc_count, updated_at) VALUES ('llm', 5, 't')"
            )
            for i, (action, swiped_at) in enumerate(rows):
                conn.execute(
                    "INSERT INTO swipe_events (card_id, keyword, action, swiped_at) "
                    "VALUES (?,?,?,?)",
                    (f"c{i}", "llm", action, swiped_at),
                )
            conn.commit()
        finally:
            conn.close()

    def _seed_sources(self, rows):
        conn = db.get_conn()
        try:
            for i, (action, swiped_at) in enumerate(rows):
                conn.execute(
                    "INSERT INTO source_events (card_id, source, action, swiped_at) "
                    "VALUES (?,?,?,?)",
                    (f"c{i}", "techcrunch", action, swiped_at),
                )
            conn.commit()
        finally:
            conn.close()

    def test_keyword_single_matches_batch(self):
        self._seed_swipes([("liked", "2026-08-01T10:00:00"),
                           ("disliked", "2026-08-02T10:00:00"),
                           ("liked", "2026-08-03T10:00:00")])
        self.assertAlmostEqual(db.get_keyword_weight("llm"),
                               db.get_all_keyword_weights()["llm"], places=6)

    def test_keyword_dislike_is_damped_not_full(self):
        # A dislike must weigh DISLIKE_DAMPER as much as a like from the same
        # instant, not the full -1.0 the single-keyword path used to apply.
        # Comparing the two cancels the shared time-decay factor.
        self._seed_swipes([("liked", "2026-08-01T10:00:00")])
        liked = db.get_keyword_weight("llm")

        db.DB_PATH = os.path.join(self._tmp, "t2.db")
        db.init_db()
        self._seed_swipes([("disliked", "2026-08-01T10:00:00")])
        disliked = db.get_keyword_weight("llm")

        self.assertGreater(liked, 0.0)
        self.assertAlmostEqual(-disliked / liked, db.DISLIKE_DAMPER, places=6)

    def test_source_single_matches_batch(self):
        self._seed_sources([("liked", "2026-08-01T10:00:00"),
                            ("disliked", "2026-08-02T10:00:00")])
        self.assertAlmostEqual(db.get_source_weight("TechCrunch"),
                               db.get_all_source_weights()["techcrunch"],
                               places=6)

    def test_source_consecutive_reject_penalty_applies_to_both(self):
        rows = [("liked", "2026-08-01T10:00:00")] + [
            ("disliked", f"2026-08-{d:02d}T10:00:00")
            for d in range(2, 2 + db.SOURCE_REJECT_THRESHOLD)
        ]
        self._seed_sources(rows)
        single = db.get_source_weight("techcrunch")
        self.assertAlmostEqual(single, db.get_all_source_weights()["techcrunch"],
                               places=6)
        self.assertLess(single, -2.0)  # penalty landed

    def test_no_events_is_zero_for_both_paths(self):
        self.assertEqual(db.get_keyword_weight("llm"), 0.0)
        self.assertEqual(db.get_source_weight("techcrunch"), 0.0)
        self.assertEqual(db.get_all_source_weights(), {})


if __name__ == "__main__":
    unittest.main()
