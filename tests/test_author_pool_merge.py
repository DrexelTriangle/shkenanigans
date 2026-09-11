"""Tests for folding the guest-author pool into the WP-user pool.

Run from the repo root:

    .venv/bin/python -m unittest tests.test_author_pool_merge
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from App import Pipeline
from Translator.Author import Author
from Utils.Utility import Utility


def pipeline():
    noop = lambda *a, **k: None
    return Pipeline(noop, noop, noop, noop, noop)


class CombineAuthorPools(unittest.TestCase):
    def test_casing_drift_does_not_emit_a_second_row(self):
        # The regression. The WP user's display name is title-cased off the
        # login; the Co-Authors Plus record is hand-typed. Same person.
        wpUser = Author(571, "Erik Heyman-meltzer", "Erik", "Heyman-meltzer",
                        "erik.heyman-meltzer@thetriangle.org", "erik-heyman-meltzer")
        guest = Author(394, "Erik Heyman-Meltzer", "Erik", "Heyman-Meltzer",
                       None, "erik-heyman-meltzer")

        combined = pipeline().combineAndReindexAuthors([wpUser], [guest])

        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0].data["id"], 571)

    def test_no_duplicate_login_is_left_for_dedupe_slug_to_number(self):
        # A missed match shows up downstream as a slug carrying the row id.
        wpUser = Author(571, "Erik Heyman-meltzer", "Erik", "Heyman-meltzer",
                        "erik.heyman-meltzer@thetriangle.org", "erik-heyman-meltzer")
        guest = Author(394, "Erik Heyman-Meltzer", "Erik", "Heyman-Meltzer",
                       None, "erik-heyman-meltzer")

        combined = pipeline().combineAndReindexAuthors([wpUser], [guest])
        Utility.canonicalizeAuthorLogins(combined)

        self.assertEqual([a.data["login"] for a in combined], ["erik-heyman-meltzer"])

    def test_hand_typed_spelling_wins_over_one_derived_from_the_login(self):
        wpUser = Author(571, "Erik Heyman-meltzer", "Erik", "Heyman-meltzer",
                        "erik.heyman-meltzer@thetriangle.org", "erik-heyman-meltzer")
        guest = Author(394, "Erik Heyman-Meltzer", "Erik", "Heyman-Meltzer",
                       None, "erik-heyman-meltzer")

        combined = pipeline().combineAndReindexAuthors([wpUser], [guest])

        self.assertEqual(combined[0].data["display_name"], "Erik Heyman-Meltzer")
        self.assertEqual(combined[0].data["last_name"], "Heyman-Meltzer")

    def test_the_email_survives_the_merge(self):
        # The WP user side is where the address lives, and cms_users links to
        # an author row BY EMAIL, so losing it unlinks the person's account.
        wpUser = Author(571, "Erik Heyman-meltzer", "Erik", "Heyman-meltzer",
                        "erik.heyman-meltzer@thetriangle.org", "erik-heyman-meltzer")
        guest = Author(394, "Erik Heyman-Meltzer", "Erik", "Heyman-Meltzer",
                       None, "erik-heyman-meltzer")

        combined = pipeline().combineAndReindexAuthors([wpUser], [guest])

        self.assertEqual(combined[0].data["email"], "erik.heyman-meltzer@thetriangle.org")

    def test_blank_fields_are_filled_from_the_guest_record(self):
        wpUser = Author(12, "Jane Doe", None, None, "jane.doe@thetriangle.org", "jane.doe")
        guest = Author(3, "Jane Doe", "Jane", "Doe", None, "jane-doe")

        combined = pipeline().combineAndReindexAuthors([wpUser], [guest])

        self.assertEqual(combined[0].data["first_name"], "Jane")
        self.assertEqual(combined[0].data["last_name"], "Doe")

    def test_a_guest_record_cannot_rename_somebody(self):
        # Only case/punctuation drift defers to the guest record. A genuinely
        # different value is a different person's data.
        wpUser = Author(12, "Jane Doe", "Jane", "Doe", "jane.doe@thetriangle.org", "jane.doe")
        guest = Author(3, "Jane Doe", "Janet", "Doe", None, "jane-doe")

        combined = pipeline().combineAndReindexAuthors([wpUser], [guest])

        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0].data["first_name"], "Jane")

    def test_an_unmatched_guest_author_still_gets_a_fresh_id(self):
        wpUser = Author(12, "Jane Doe", "Jane", "Doe", "jane.doe@thetriangle.org", "jane.doe")
        guest = Author(3, "Someone Else", "Someone", "Else", None, "someone-else")

        combined = pipeline().combineAndReindexAuthors([wpUser], [guest])

        self.assertEqual(len(combined), 2)
        self.assertEqual(combined[1].data["id"], 13)

    def test_fresh_ids_never_collide_with_a_wp_user_id(self):
        authors = [Author(0, "A A"), Author(5, "B B")]
        guests = [Author(1, "C C"), Author(2, "D D")]

        combined = pipeline().combineAndReindexAuthors(authors, guests)

        ids = [a.data["id"] for a in combined]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[2:], [6, 7])


if __name__ == "__main__":
    unittest.main()
