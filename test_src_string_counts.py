"""Tests for the en-US source-string counters that back the "en-US Strings"
column shared by MR Pipeline, Scan Tasks and File Translation.

The column shows *distinct source strings* (distinct ``opus_id``), NOT the raw
row count (strings × languages). These tests pin that semantics for the two new
counters added for the unified task views:

  - ``export_mr_pipeline.count_scan_source_strings``  (Scan Tasks)
  - ``export_translations.count_legacy_source_strings`` (File Translation)

Both must also degrade to 0 on any API error so the UI can always render a
number without special-casing failures.

Run:  python -m unittest test_src_string_counts
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_mr_pipeline as mr
import export_translations as et


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class CountScanSourceStringsTests(unittest.TestCase):
    def test_counts_distinct_opus_ids_not_rows(self):
        # 2 source strings × 3 languages = 6 rows, but distinct opus_id == 2.
        translations = [
            {"opus_id": "k1", "target_language": lang} for lang in ("de", "fr", "ja")
        ] + [
            {"opus_id": "k2", "target_language": lang} for lang in ("de", "fr", "ja")
        ]
        mr_ref = mr.fetch_scan_results
        mr.fetch_scan_results = lambda tid: {"translations": translations}
        try:
            self.assertEqual(mr.count_scan_source_strings("t"), 2)
        finally:
            mr.fetch_scan_results = mr_ref

    def test_ignores_rows_without_opus_id(self):
        translations = [{"opus_id": "k1"}, {"opus_id": ""}, {"target_language": "de"}]
        mr_ref = mr.fetch_scan_results
        mr.fetch_scan_results = lambda tid: {"translations": translations}
        try:
            self.assertEqual(mr.count_scan_source_strings("t"), 1)
        finally:
            mr.fetch_scan_results = mr_ref

    def test_empty_task_is_zero(self):
        mr_ref = mr.fetch_scan_results
        mr.fetch_scan_results = lambda tid: {"translations": []}
        try:
            self.assertEqual(mr.count_scan_source_strings("t"), 0)
        finally:
            mr.fetch_scan_results = mr_ref

    def test_api_error_degrades_to_zero(self):
        mr_ref = mr.fetch_scan_results

        def _boom(tid):
            raise RuntimeError("500")

        mr.fetch_scan_results = _boom
        try:
            self.assertEqual(mr.count_scan_source_strings("t"), 0)
        finally:
            mr.fetch_scan_results = mr_ref


class CountLegacySourceStringsTests(unittest.TestCase):
    def _install_backend(self, entries, page=200):
        """Patch et._api_get with a paginated /translations backend.

        Returns the original so the caller can restore it.
        """
        original = et._api_get

        def _fake(url, params=None, **kw):
            params = params or {}
            offset = int(params.get("offset", 0))
            limit = int(params.get("limit", page))
            chunk = entries[offset:offset + limit]
            return _FakeResp({"entries": chunk, "total": len(entries)})

        et._api_get = _fake
        return original

    def test_counts_distinct_opus_ids_across_pages(self):
        # 250 rows over 2 pages, but only 2 distinct opus_ids.
        entries = [{"opus_id": "k1", "target_language": f"l{i}"} for i in range(130)]
        entries += [{"opus_id": "k2", "target_language": f"l{i}"} for i in range(120)]
        original = self._install_backend(entries)
        try:
            self.assertEqual(et.count_legacy_source_strings("t"), 2)
        finally:
            et._api_get = original

    def test_counts_many_distinct(self):
        entries = [{"opus_id": f"k{i}", "target_language": "de"} for i in range(37)]
        original = self._install_backend(entries)
        try:
            self.assertEqual(et.count_legacy_source_strings("t"), 37)
        finally:
            et._api_get = original

    def test_ignores_missing_opus_id(self):
        entries = [{"opus_id": "k1"}, {"opus_id": None}, {"target_language": "de"}]
        original = self._install_backend(entries)
        try:
            self.assertEqual(et.count_legacy_source_strings("t"), 1)
        finally:
            et._api_get = original

    def test_api_error_degrades_to_zero(self):
        original = et._api_get

        def _boom(url, params=None, **kw):
            raise RuntimeError("timeout")

        et._api_get = _boom
        try:
            self.assertEqual(et.count_legacy_source_strings("t"), 0)
        finally:
            et._api_get = original


if __name__ == "__main__":
    unittest.main()
