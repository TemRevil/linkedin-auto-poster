"""Tests for database TF-IDF scoring: get_idf / get_idf_batch."""
import math
import os
import tempfile
import unittest

import _bootstrap  # noqa: F401
import database as db


class IdfTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = db.DB_PATH
        db.DB_PATH = os.path.join(self._tmp, "t.db")
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._orig

    def _seed(self):
        conn = db.get_conn()
        try:
            conn.execute("INSERT INTO meta (key, value) VALUES ('total_docs', '100')")
            conn.execute("INSERT INTO corpus_stats (keyword, doc_count, updated_at) VALUES ('rare', 1, 't')")
            conn.execute("INSERT INTO corpus_stats (keyword, doc_count, updated_at) VALUES ('common', 50, 't')")
            conn.commit()
        finally:
            conn.close()

    def test_idf_formula_and_ordering(self):
        self._seed()
        self.assertAlmostEqual(db.get_idf("rare"), math.log(100 / 2))
        self.assertAlmostEqual(db.get_idf("common"), math.log(100 / 51))
        self.assertGreater(db.get_idf("rare"), db.get_idf("common"))

    def test_unseen_keyword_uses_smoothing(self):
        self._seed()
        self.assertAlmostEqual(db.get_idf("unseen"), math.log(100 / 1))

    def test_keyword_is_lowercased(self):
        self._seed()
        self.assertAlmostEqual(db.get_idf("RARE"), db.get_idf("rare"))

    def test_batch_matches_single(self):
        self._seed()
        b = db.get_idf_batch(["rare", "common", "unseen"])
        self.assertAlmostEqual(b["rare"], db.get_idf("rare"))
        self.assertAlmostEqual(b["common"], db.get_idf("common"))
        self.assertAlmostEqual(b["unseen"], db.get_idf("unseen"))

    def test_empty_corpus_collapses_to_zero(self):
        # Fresh DB: init_db seeds no total_docs, so IDF collapses to log(1)=0.
        self.assertEqual(db.get_idf("anything"), 0.0)


if __name__ == "__main__":
    unittest.main()
