"""Tests for database swipe recording + manual keyword preferences.

Runs against a throwaway SQLite file so the real discovery.db is untouched.
"""
import os
import tempfile
import unittest

import _bootstrap  # noqa: F401
import database as db


def _count(conn, table, card_id):
    return conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE card_id = ?", (card_id,)
    ).fetchone()["n"]


class SwipeAndPrefsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_path = db.DB_PATH
        db.DB_PATH = os.path.join(self._tmp, "test.db")
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._orig_path

    def _add_card(self, cid="c1", keywords=("alpha", "beta"), source="SrcX"):
        db.insert_card({
            "id": cid, "title": "t", "summary": "s", "source": source,
            "url": "http://x/" + cid, "type": "rss", "keywords": list(keywords),
            "preference_score": 0.0, "is_exploration": 0,
            "discovered_at": "2026-07-15T10:00:00", "status": "pending",
        })

    def test_swipe_missing_card_returns_false(self):
        self.assertFalse(db.record_swipe_db("nope", "liked"))

    def test_reswipe_is_idempotent(self):
        # audit-4: a re-swipe must replace, not stack, the card's events.
        self._add_card("c1", keywords=("alpha", "beta"))
        self.assertTrue(db.record_swipe_db("c1", "liked"))
        conn = db.get_conn()
        try:
            self.assertEqual(_count(conn, "swipe_events", "c1"), 2)
            self.assertEqual(_count(conn, "source_events", "c1"), 1)
        finally:
            conn.close()
        # swipe again — counts must not double
        db.record_swipe_db("c1", "liked")
        conn = db.get_conn()
        try:
            self.assertEqual(_count(conn, "swipe_events", "c1"), 2)
            self.assertEqual(_count(conn, "source_events", "c1"), 1)
        finally:
            conn.close()

    def test_change_of_mind_replaces_status(self):
        self._add_card("c2", keywords=("alpha",))
        db.record_swipe_db("c2", "liked")
        db.record_swipe_db("c2", "disliked")
        conn = db.get_conn()
        try:
            self.assertEqual(_count(conn, "swipe_events", "c2"), 1)
            row = conn.execute(
                "SELECT status FROM discovery_cards WHERE id=?", ("c2",)
            ).fetchone()
            self.assertEqual(row["status"], "disliked")
            actions = [r["action"] for r in conn.execute(
                "SELECT action FROM swipe_events WHERE card_id=?", ("c2",)).fetchall()]
            self.assertEqual(set(actions), {"disliked"})  # no stale 'liked'
        finally:
            conn.close()

    def test_manual_keyword_add_and_remove(self):
        # Seed a small corpus so IDF (and thus the weight) is non-zero; with an
        # empty corpus IDF collapses to log(1)=0 and every weight would be 0.
        db.update_corpus([
            {"link": "http://d1", "title": "machine learning models", "summary": "deep nets"},
            {"link": "http://d2", "title": "web frontend react", "summary": "components"},
            {"link": "http://d3", "title": "cloud devops kubernetes", "summary": "scaling"},
        ])
        self.assertTrue(db.set_manual_keyword("python", "liked"))
        weights = db.get_all_keyword_weights()
        self.assertIn("python", weights)
        self.assertGreater(weights["python"], 0)
        self.assertTrue(db.remove_manual_keyword("python"))
        self.assertNotIn("python", db.get_all_keyword_weights())
        self.assertFalse(db.remove_manual_keyword("python"))

    def test_manual_keyword_rejects_bad_input(self):
        self.assertFalse(db.set_manual_keyword("", "liked"))
        self.assertFalse(db.set_manual_keyword("x", "bogus"))


if __name__ == "__main__":
    unittest.main()
