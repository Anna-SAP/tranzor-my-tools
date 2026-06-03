"""Tests for the MR Pipeline en-US source-string counters.

``distinct_source_string_count`` and ``count_mr_source_strings`` back the
``en-US Strings`` column added to the MR Pipeline task table. The count must be
*distinct opus_id* — the same en-US string is translated into every target
language, so the raw translation-row count over-states the work by the language
fan-out.

Run:  python -m unittest test_mr_source_count
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_mr_pipeline as mr_api


class DistinctSourceStringCountTests(unittest.TestCase):

    def test_empty_and_none(self):
        self.assertEqual(mr_api.distinct_source_string_count([]), 0)
        self.assertEqual(mr_api.distinct_source_string_count(None), 0)

    def test_counts_distinct_opus_ids(self):
        trs = [
            {"opus_id": "a", "target_language": "de-DE"},
            {"opus_id": "b", "target_language": "de-DE"},
            {"opus_id": "c", "target_language": "de-DE"},
        ]
        self.assertEqual(mr_api.distinct_source_string_count(trs), 3)

    def test_same_string_many_languages_counts_once(self):
        # 2 source strings × 3 languages = 6 rows, but only 2 en-US strings.
        trs = [
            {"opus_id": "a", "target_language": lang}
            for lang in ("de-DE", "fr-FR", "es-ES")
        ] + [
            {"opus_id": "b", "target_language": lang}
            for lang in ("de-DE", "fr-FR", "es-ES")
        ]
        self.assertEqual(len(trs), 6)
        self.assertEqual(mr_api.distinct_source_string_count(trs), 2)

    def test_rows_without_opus_id_ignored(self):
        trs = [
            {"opus_id": "a"},
            {"opus_id": ""},
            {"opus_id": None},
            {"target_language": "de-DE"},  # no opus_id key at all
        ]
        self.assertEqual(mr_api.distinct_source_string_count(trs), 1)


class CountMrSourceStringsTests(unittest.TestCase):

    def setUp(self):
        self._orig = mr_api.fetch_mr_results
        self.addCleanup(setattr, mr_api, "fetch_mr_results", self._orig)

    def test_counts_from_fetched_results(self):
        def fake_fetch(task_id):
            self.assertEqual(task_id, "task-1")
            return {"translations": [
                {"opus_id": "a", "target_language": "de-DE"},
                {"opus_id": "a", "target_language": "fr-FR"},
                {"opus_id": "b", "target_language": "de-DE"},
            ]}

        mr_api.fetch_mr_results = fake_fetch
        self.assertEqual(mr_api.count_mr_source_strings("task-1"), 2)

    def test_missing_translations_key_is_zero(self):
        mr_api.fetch_mr_results = lambda task_id: {"task_id": task_id}
        self.assertEqual(mr_api.count_mr_source_strings("task-1"), 0)

    def test_fetch_error_is_zero_not_raise(self):
        def boom(task_id):
            raise RuntimeError("network down")

        mr_api.fetch_mr_results = boom
        # Must degrade to 0 so the column renders a number instead of crashing
        # the prefetch worker.
        self.assertEqual(mr_api.count_mr_source_strings("task-1"), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
