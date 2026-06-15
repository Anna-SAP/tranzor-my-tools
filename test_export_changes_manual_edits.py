"""Regression tests for export_changes.fetch_manual_edits.

The Changes export collects human-edited rows (Manual Edit / LLM Retranslate)
from the legacy ``/translations`` endpoint. The old implementation paged the
UNFILTERED endpoint with ``offset += limit`` — inheriting both the source-level
ordering boundary bug (drops/duplicates (key,lang) rows) and a short-page skip.
A dropped row that happened to be a human edit silently vanished from the
Changes report.

The fix: use the server-side ``label_types=post_edited`` filter (so the result
set is tiny and usually one page), and when it exceeds a page, fetch per
``target_language`` (opus_id unique within a language → stable pagination).

Run:  python -m unittest test_export_changes_manual_edits
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_changes as ec


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _entry(opus, lang, ttype="Manual Edit"):
    return {"opus_id": opus, "target_language": lang,
            "translation_type": ttype, "translated_text": "x"}


class _Backend:
    """Fake legacy /translations honouring target_language + label_types."""

    def __init__(self, data):
        self.data = data
        self.calls = []

    def get(self, url, params=None, **kw):
        params = params or {}
        self.calls.append(dict(params))
        lang = params.get("target_language")
        label = params.get("label_types")
        limit = int(params.get("limit", 200))
        offset = int(params.get("offset", 0))
        rows = list(self.data)
        if label == "post_edited":
            rows = [e for e in rows
                    if e.get("translation_type") in ec._POST_EDIT_TYPES]
        if lang is not None:
            rows = [e for e in rows if e.get("target_language") == lang]
        return _FakeResp({"total": len(rows), "entries": rows[offset:offset + limit]})


class _Mixin(unittest.TestCase):
    def setUp(self):
        self._orig = ec._api_get

    def tearDown(self):
        ec._api_get = self._orig

    @staticmethod
    def _pairs(entries):
        return {(e["opus_id"], e["target_language"]) for e in entries}


class TestFetchManualEdits(_Mixin):
    def test_single_page_common_case(self):
        # A few edits among many machine translations -> one request, filtered.
        data = []
        for i in range(5):
            for lg in ("de-DE", "zh-CN"):
                data.append(_entry(f"K{i}", lg, "MT"))
        data.append(_entry("K0", "de-DE", "Manual Edit"))
        data.append(_entry("K2", "zh-CN", "LLM Retranslate"))
        backend = _Backend(data)
        ec._api_get = backend.get
        out = ec.fetch_manual_edits("T")
        self.assertEqual(self._pairs(out), {("K0", "de-DE"), ("K2", "zh-CN")})
        # Single request (the post_edited probe) sufficed.
        self.assertEqual(len(backend.calls), 1)

    def test_excludes_non_post_edit_types(self):
        data = [_entry("K0", "de-DE", "MT"), _entry("K1", "de-DE", "ICE Match")]
        backend = _Backend(data)
        ec._api_get = backend.get
        self.assertEqual(ec.fetch_manual_edits("T"), [])

    def test_over_one_page_fetches_per_language_completely(self):
        # 100 keys x 3 langs = 300 human edits (> one 200-row page). The
        # per-language path must recover all 300 with no drop/dup that the old
        # flat OFFSET/LIMIT pagination would have caused at page boundaries.
        langs = ("de-DE", "zh-CN", "fi-FI")
        data = [_entry(f"K{i:03d}", lg) for i in range(100) for lg in langs]
        backend = _Backend(data)
        ec._api_get = backend.get
        out = ec.fetch_manual_edits("T")
        self.assertEqual(len(out), 300)
        self.assertEqual(len(self._pairs(out)), 300)
        # Real fetches were language-scoped once we crossed a page.
        self.assertTrue(any("target_language" in c for c in backend.calls))


if __name__ == "__main__":
    unittest.main()
