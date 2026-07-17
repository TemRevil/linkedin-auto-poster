"""Tests for clean_connections: session-file resolver guards + name cleaning."""
import json
import os
import pathlib
import tempfile
import unittest

import _bootstrap  # noqa: F401
import clean_connections as cc


class GetLinkedInSessionFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp())
        self._orig_sessions, self._orig_auth = cc.SESSIONS_FILE, cc.AUTH_DIR
        cc.SESSIONS_FILE = self._tmp / "sessions.json"
        cc.AUTH_DIR = self._tmp

    def tearDown(self):
        cc.SESSIONS_FILE, cc.AUTH_DIR = self._orig_sessions, self._orig_auth

    def test_corrupt_json_does_not_raise(self):
        cc.SESSIONS_FILE.write_text("{bad json", encoding="utf-8")
        self.assertIsNone(cc._get_linkedin_session_file())  # no legacy present

    def test_active_entry_missing_file_key_does_not_raise(self):
        cc.SESSIONS_FILE.write_text(
            json.dumps({"linkedin": [{"id": 1, "active": True}]}), encoding="utf-8")
        self.assertIsNone(cc._get_linkedin_session_file())

    def test_valid_active_entry_resolved(self):
        cc.SESSIONS_FILE.write_text(
            json.dumps({"linkedin": [{"id": 1, "active": True, "file": "linkedin.json"}]}),
            encoding="utf-8")
        self.assertEqual(cc._get_linkedin_session_file().name, "linkedin.json")

    def test_falls_back_to_legacy_when_no_config(self):
        (self._tmp / "linkedin.json").write_text("{}", encoding="utf-8")
        self.assertEqual(cc._get_linkedin_session_file().name, "linkedin.json")


class CleanNameTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(cc.clean_name(""), "")

    def test_plain_name_untouched(self):
        self.assertEqual(cc.clean_name("Jane Smith"), "Jane Smith")

    def test_splits_on_bullet_degree_marker(self):
        self.assertEqual(cc.clean_name("Ahmed Ali • من الدرجة الأولى"), "Ahmed Ali")


if __name__ == "__main__":
    unittest.main()
