"""Unit tests for the oversized-source guard in terminology_highlight.

UNS handlebars email templates are whole-file translation units (~8KB of
HTML). Highlighting them used to scan the 8KB for glossary terms and fetch a
context-service detail per hit (up to 30s each) — for one MR Changes row that
is ~7-8s of API traffic and, if the context-service is slow/unreachable, the
HTML export hangs at "Exporting...". MAX_HIGHLIGHT_SOURCE_CHARS skips terms for
oversized texts so the export stays fast and never hangs.

Run:  python -m unittest test_terminology_highlight_size_guard
"""
from __future__ import annotations

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terminology_highlight as th


class _StateGuard(unittest.TestCase):
    """Save/restore the module-level caches so tests don't leak into each
    other (or into a real GUI session importing the same module)."""

    def setUp(self):
        self._saved = {
            "_list_loaded": th._list_loaded,
            "_source_re": th._source_re,
            "_name_to_meta": dict(th._name_to_meta),
            "_detail_cache": dict(th._detail_cache),
            "_locale_re": dict(th._locale_re),
            "fetch_many_details": th.term_api.fetch_many_details,
            "_build_locale_regex": th._build_locale_regex,
        }
        # Deterministic in-memory term list: one term "Widget" with id 42.
        th._list_loaded = True
        th._source_re = re.compile(r"\bWidget\b")
        th._name_to_meta = {"widget": {"id": 42, "name": "Widget"}}
        th._detail_cache = {}
        th._locale_re = {}
        # Record detail fetches; never hit the network.
        self.fetch_calls = []

        def fake_fetch(ids, *a, **k):
            self.fetch_calls.append(list(ids))
            return {}

        th.term_api.fetch_many_details = fake_fetch
        th._build_locale_regex = lambda locale: None  # isolate from regex build

    def tearDown(self):
        th._list_loaded = self._saved["_list_loaded"]
        th._source_re = self._saved["_source_re"]
        th._name_to_meta = self._saved["_name_to_meta"]
        th._detail_cache = self._saved["_detail_cache"]
        th._locale_re = self._saved["_locale_re"]
        th.term_api.fetch_many_details = self._saved["fetch_many_details"]
        th._build_locale_regex = self._saved["_build_locale_regex"]


class TestPrefetchSizeGuard(_StateGuard):
    def test_oversized_row_triggers_no_detail_fetch(self):
        """The whole point: an 8KB UNS template row must NOT fan out into
        context-service detail fetches (the source of the export hang)."""
        big = ("Widget " * 2000)  # ~14KB, contains the term many times
        self.assertGreater(len(big), th.MAX_HIGHLIGHT_SOURCE_CHARS)
        th.prefetch_for_rows([{"source_text": big, "target_language": "fr-CA"}])
        self.assertEqual(self.fetch_calls, [],
                         "oversized source must not fetch term details")

    def test_normal_row_still_fetches(self):
        """Normal-size strings keep working — terms are detected and fetched."""
        th.prefetch_for_rows(
            [{"source_text": "Please buy a Widget today", "target_language": "fr-CA"}])
        self.assertEqual(self.fetch_calls, [[42]])

    def test_mixed_rows_only_fetch_for_small(self):
        """A batch mixing a UNS template with a normal string fetches details
        only for the terms found in the normal string."""
        big = "Widget " * 2000
        th.prefetch_for_rows([
            {"source_text": big, "target_language": "fr-CA"},
            {"source_text": "A small Widget string", "target_language": "de-DE"},
        ])
        self.assertEqual(self.fetch_calls, [[42]])


class TestHighlightApplyGuard(_StateGuard):
    def test_highlight_source_skips_oversized(self):
        big = "x" * (th.MAX_HIGHLIGHT_SOURCE_CHARS + 1) + " Widget"
        self.assertEqual(th.highlight_source(big), big)  # unchanged, no <mark>

    def test_highlight_source_marks_normal(self):
        out = th.highlight_source("Buy a Widget")
        self.assertIn("<mark", out)
        self.assertIn("Widget", out)

    def test_highlight_translation_skips_oversized(self):
        big = "y" * (th.MAX_HIGHLIGHT_SOURCE_CHARS + 1)
        self.assertEqual(th.highlight_translation(big, "fr-CA"), big)


if __name__ == "__main__":
    unittest.main()
