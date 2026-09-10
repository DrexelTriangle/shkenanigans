"""Tests for the author slug built from the WordPress login.

Run from the repo root:

    .venv/bin/python -m unittest tests.test_author_login_slugs
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Utils.Utility import Utility


def author(author_id, login, display_name=None):
    return {"id": author_id, "login": login, "display_name": display_name}


def logins(authors):
    Utility.canonicalizeAuthorLogins(authors)
    return [a["login"] for a in authors]


class AuthorLoginSlugs(unittest.TestCase):
    def test_by_prefix_from_the_login_is_dropped(self):
        # The regression: a few accounts were registered with the byline text
        # as the login, so the slug carried a "by-" the display name never had
        # and /author/nayab-iqbal 404'd while /author/by-nayab-iqbal served.
        self.assertEqual(
            logins([author(1, "By Nayab Iqbal", "Nayab Iqbal")]),
            ["nayab-iqbal"],
        )

    def test_separator_spelling_does_not_matter(self):
        # canonicalize_slug collapses the separator before the prefix is
        # stripped, so every spelling of the login arrives the same way.
        self.assertEqual(logins([author(1, "by-lena-tran")]), ["lena-tran"])
        self.assertEqual(logins([author(2, "by_jack_davis")]), ["jack-davis"])
        self.assertEqual(logins([author(3, "BY ANUM HASSAN")]), ["anum-hassan"])

    def test_a_surname_beginning_with_by_is_left_alone(self):
        # The prefix is dash-terminated precisely so this keeps working.
        self.assertEqual(logins([author(1, "Byrne")]), ["byrne"])
        self.assertEqual(logins([author(2, "byers")]), ["byers"])

    def test_falls_back_to_the_display_name_and_still_strips(self):
        self.assertEqual(
            logins([author(1, None, "By Jack Davis")]),
            ["jack-davis"],
        )

    def test_stripping_can_collide_and_still_dedupes(self):
        # "By Jack Davis" and "Jack Davis" both reduce to jack-davis, so the
        # second must still be given a distinct slug rather than silently
        # overwriting the first.
        self.assertEqual(
            logins([author(10, "jack-davis"), author(11, "by-jack-davis")]),
            ["jack-davis", "jack-davis-11"],
        )

    def test_stripping_never_empties_a_slug(self):
        # "by-" canonicalizes to "by" before the pattern is applied, and the
        # pattern needs the trailing dash, so nothing is stripped and the slug
        # stays non-empty. Worth pinning: a rule that could reduce a login to ""
        # would send every such author through the same author-<id> fallback.
        self.assertEqual(logins([author(7, "by-")]), ["by"])
        self.assertEqual(logins([author(8, "By")]), ["by"])


if __name__ == "__main__":
    unittest.main()
