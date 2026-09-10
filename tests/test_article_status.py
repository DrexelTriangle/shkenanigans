"""Tests that WordPress publication state survives the pipeline. Run from the
repo root:

    .venv/bin/python -m unittest tests.test_article_status

Regression: the extractor never collected wp:status, so every <item> became a
live article. 16 posts WordPress was holding as draft, pending or private were
published on thetriangle.org, including four of its five private posts.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Extractor import _POST_ITEM_KEYS
from Formatter.ArticleFormatter import ArticleFormatter
from Translator.ArticleTranslator import ArticleTranslator


class StatusIsExtracted(unittest.TestCase):
    def test_status_is_collected_from_the_export(self):
        # The root cause: the key was simply absent from the extracted set, so
        # there was no status downstream to filter on.
        self.assertIn("wp:status", _POST_ITEM_KEYS)


class StatusNormalization(unittest.TestCase):
    def _status(self, value):
        return ArticleTranslator([])._normalizeStatus(value)

    def test_publish_variants_normalize(self):
        for value in ("publish", "Publish", "  publish  "):
            with self.subTest(value=value):
                self.assertEqual(self._status(value), "publish")

    def test_withheld_states_keep_their_name(self):
        for value in ("draft", "pending", "private", "future", "trash"):
            with self.subTest(value=value):
                self.assertEqual(self._status(value), value)

    def test_absent_status_defaults_to_publish(self):
        # An export taken before the extractor collected the field must still
        # import its archive as published, not blank ten thousand articles.
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertEqual(self._status(value), "publish")


class PubDateReflectsStatus(unittest.TestCase):
    def _row(self, status):
        obj = {"pubDate": "2016-11-06 01:14:09"}
        if status is not None:
            obj["status"] = status
        return ArticleFormatter([])._to_cms_row(obj)

    def test_published_posts_keep_their_date(self):
        self.assertEqual(self._row("publish")["pub_date"], "2016-11-06 01:14:09")

    def test_withheld_posts_arrive_as_drafts(self):
        # The CMS reads a null pub_date as "draft": present for editors, absent
        # from every public path.
        for status in ("draft", "pending", "private", "future", "trash"):
            with self.subTest(status=status):
                self.assertIsNone(self._row(status)["pub_date"])

    def test_withheld_posts_keep_their_timeline(self):
        # creation_date is what preserves when the piece was written, so a
        # recovered draft still sorts correctly for an editor.
        self.assertEqual(self._row("private")["creation_date"], "2016-11-06 01:14:09")

    def test_missing_status_still_publishes(self):
        self.assertEqual(self._row(None)["pub_date"], "2016-11-06 01:14:09")


if __name__ == "__main__":
    unittest.main()
