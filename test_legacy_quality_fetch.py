"""Regression tests for export_mr_pipeline.fetch_all_legacy_translations_quality.

This is the *twin* of the export_translations bug: it paginates the SAME legacy
``/translations`` endpoint and, when called WITHOUT a target_language (the QA
checks DB drain in tranzor_checks, the post-edit fallbacks), it inherited the
same source-level-ordering boundary bug that drops/duplicates (key, language)
rows. The fix fetches per target_language when none is specified.

Run:  python -m unittest test_legacy_quality_fetch
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_mr_pipeline as mp


def _entry(opus, lang, text="t", label=None):
    e = {"opus_id": opus, "target_language": lang,
         "source_text": f"src-{opus}", "translated_text": text}
    if label:
        e["translation_type"] = label
    return e


class _FakeEndpoint:
    """Stand-in for fetch_legacy_translations_quality(task_id, limit, offset,
    target_language, label_types) -> (entries, total).

    drop_on_flat reproduces the boundary bug on the UNFILTERED query; the
    per-language query is always clean. label_types filters by translation_type.
    """

    def __init__(self, data, drop_on_flat=True):
        self.data = data
        self.drop_on_flat = drop_on_flat
        self.calls = []

    def __call__(self, task_id, limit=200, offset=0, target_language=None,
                 label_types=None):
        self.calls.append({"offset": offset, "limit": limit,
                           "target_language": target_language,
                           "label_types": label_types})
        rows = list(self.data)
        if label_types == ["post_edited"]:
            rows = [e for e in rows if e.get("translation_type") in
                    ("Manual Edit", "LLM Retranslate")]
        if target_language is not None:
            rows = [e for e in rows if e["target_language"] == target_language]
            return rows[offset:offset + limit], len(rows)
        # unfiltered
        total = len(rows)
        page = rows[offset:offset + limit]
        if self.drop_on_flat and offset > 0 and page:
            page = [rows[offset - 1]] + page[:-1]
        return page, total


class _Mixin(unittest.TestCase):
    def setUp(self):
        self._orig = mp.fetch_legacy_translations_quality
        self._orig_hyd = mp.hydrate_truncated_entries
        mp.hydrate_truncated_entries = lambda *a, **k: 0

    def tearDown(self):
        mp.fetch_legacy_translations_quality = self._orig
        mp.hydrate_truncated_entries = self._orig_hyd

    @staticmethod
    def _pairs(entries):
        return {(e["opus_id"], e["target_language"]) for e in entries}


class TestTwinPathPerLanguage(_Mixin):
    def test_no_language_fetches_per_language_and_recovers_all(self):
        langs = ["de-DE", "zh-CN", "fi-FI"]
        data = [_entry(f"K{i}", lg) for i in range(6) for lg in langs]
        fake = _FakeEndpoint(data, drop_on_flat=True)
        mp.fetch_legacy_translations_quality = fake
        out = mp.fetch_all_legacy_translations_quality("T")
        self.assertEqual(len(self._pairs(out)), 18)
        # Real fetches were language-scoped (not the buggy flat enumeration).
        scoped = [c for c in fake.calls if c["target_language"] is not None]
        self.assertTrue(scoped)

    def test_explicit_language_uses_single_language_path(self):
        langs = ["de-DE", "zh-CN"]
        data = [_entry(f"K{i}", lg) for i in range(3) for lg in langs]
        fake = _FakeEndpoint(data)
        mp.fetch_legacy_translations_quality = fake
        out = mp.fetch_all_legacy_translations_quality(
            "T", target_language="zh-CN")
        self.assertEqual(self._pairs(out),
                         {("K0", "zh-CN"), ("K1", "zh-CN"), ("K2", "zh-CN")})
        # Never probed/iterated other languages.
        self.assertTrue(all(c["target_language"] == "zh-CN"
                            for c in fake.calls))

    def test_label_types_forwarded_through_per_language(self):
        langs = ["de-DE", "zh-CN"]
        data = []
        for i in range(3):
            for lg in langs:
                data.append(_entry(f"K{i}", lg,
                                   label="Manual Edit" if i == 0 else "MT"))
        fake = _FakeEndpoint(data)
        mp.fetch_legacy_translations_quality = fake
        out = mp.fetch_all_legacy_translations_quality(
            "T", label_types=["post_edited"])
        # Only K0's edited rows survive the server-side label filter.
        self.assertEqual(self._pairs(out), {("K0", "de-DE"), ("K0", "zh-CN")})
        self.assertTrue(all(c["label_types"] == ["post_edited"]
                            for c in fake.calls))


if __name__ == "__main__":
    unittest.main()
