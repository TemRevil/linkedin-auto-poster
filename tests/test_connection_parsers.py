"""Tests for clean_connections field classifiers/cleaners (pure functions)."""
import unittest

import _bootstrap  # noqa: F401
from clean_connections import (
    looks_like_headline, is_junk_location, clean_headline, clean_location,
)


class LooksLikeHeadlineTests(unittest.TestCase):
    def test_job_title_is_headline(self):
        self.assertTrue(looks_like_headline("Senior Software Engineer"))

    def test_plain_location_is_not_headline(self):
        self.assertFalse(looks_like_headline("Cairo, Egypt"))

    def test_markers_embedded_in_place_names_do_not_match(self):
        # Substring matching used to flag these: "hr" inside Bahrain and
        # Christchurch, "lead" inside Leadville, "at " inside "Rabat ".
        for place in ("Manama, Bahrain", "Christchurch, New Zealand",
                      "Leadville, Colorado", "Rabat, Morocco"):
            with self.subTest(place=place):
                self.assertFalse(looks_like_headline(place))

    def test_markers_as_whole_words_still_match(self):
        for headline in ("HR Specialist", "Data Analyst", "Team Leader at Acme",
                         "Internship at Google", "Software Engineer | React"):
            with self.subTest(headline=headline):
                self.assertTrue(looks_like_headline(headline))

    def test_empty_text_is_not_headline(self):
        self.assertFalse(looks_like_headline(""))


class IsJunkLocationTests(unittest.TestCase):
    def test_message_marker_is_junk(self):
        self.assertTrue(is_junk_location("رسالة"))

    def test_real_city_is_not_junk(self):
        self.assertFalse(is_junk_location("Cairo"))


class CleanHeadlineTests(unittest.TestCase):
    def test_plain_headline_kept(self):
        self.assertEqual(
            clean_headline("Senior Frontend Developer", "Ahmed Ali", "Cairo"),
            "Senior Frontend Developer",
        )

    def test_recovers_headline_from_location_field(self):
        # When headline is empty but the location field holds a job title.
        self.assertEqual(clean_headline("", "Ahmed", "Backend Engineer"), "Backend Engineer")

    def test_name_pipe_title_keeps_the_title(self):
        # "Name | Title" / "Name - Title" must not be discarded as just-the-name.
        self.assertEqual(
            clean_headline("Ahmed Ali | Senior Developer", "Ahmed Ali", "Cairo"),
            "Senior Developer",
        )

    def test_headline_equal_to_name_is_discarded(self):
        self.assertEqual(clean_headline("Ahmed Ali", "Ahmed Ali", "Cairo"), "")


class CleanLocationTests(unittest.TestCase):
    def test_real_location_kept(self):
        self.assertEqual(clean_location("Cairo, Egypt", "Ahmed"), "Cairo, Egypt")

    def test_job_title_in_location_field_cleared(self):
        self.assertEqual(clean_location("Software Engineer", "Ahmed"), "")


if __name__ == "__main__":
    unittest.main()
