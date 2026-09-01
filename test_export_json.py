"""Regression tests for export_json's pivot + full-language densification.

The "All Translations → JSON (for QA Audit)" export must guarantee that every
key carries 100% of its configured target languages. Source data is sparse —
when a (key, language) has no translation the backend never returns a row (and
``fetch_all_translations`` further drops empty-translated rows), so a naive
pivot produces ragged coverage (one key has 16 languages, another only 13).
``build_json_entries(..., fill_missing=True)`` pads every key to its full
target-language set.

Two hard invariants are pinned here:
  * The *Changes* export shares the same pivot and must stay sparse
    (``fill_missing=False`` → byte-identical to the historical behaviour).
  * Densification is scoped per source task, so an all-tasks export never
    fills product A's keys with product B's languages.

Run:  python -m unittest test_export_json
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_json as ej


def _langs(entry):
    """Languages present in a pivoted entry (excludes the ``key`` column)."""
    return {k for k in entry if k != "key"}


def _by_key(entries):
    return {e["key"]: e for e in entries}


# A single task (292) with ragged coverage: key A has zh-CN/fi-FI, key B does
# not. This mirrors the reported bug (RingCentral.sci... missing zh-CN, fi-FI).
RAGGED_TASK_ROWS = [
    # key A — full house
    {"task_id": 292, "string_key": "K.A", "language": "en-US",
     "source_text": "Hours", "translated_text": "Hours"},
    {"task_id": 292, "string_key": "K.A", "language": "de-DE",
     "source_text": "Hours", "translated_text": "Stunden"},
    {"task_id": 292, "string_key": "K.A", "language": "zh-CN",
     "source_text": "Hours", "translated_text": "工时"},
    {"task_id": 292, "string_key": "K.A", "language": "fi-FI",
     "source_text": "Hours", "translated_text": "tunnit"},
    # key B — missing zh-CN and fi-FI in the source data
    {"task_id": 292, "string_key": "K.B", "language": "en-US",
     "source_text": "Pack", "translated_text": "Pack"},
    {"task_id": 292, "string_key": "K.B", "language": "de-DE",
     "source_text": "Pack", "translated_text": "Paket"},
]


class TestSparseBackwardCompat(unittest.TestCase):
    """Default (fill_missing=False) must reproduce the historical sparse pivot
    so the Changes export and any other caller is untouched."""

    def test_ragged_stays_ragged_by_default(self):
        entries = ej.build_json_entries(RAGGED_TASK_ROWS)
        by_key = _by_key(entries)
        self.assertEqual(_langs(by_key["K.A"]),
                         {"en-US", "de-DE", "zh-CN", "fi-FI"})
        # key B keeps ONLY what it had — no padding.
        self.assertEqual(_langs(by_key["K.B"]), {"en-US", "de-DE"})

    def test_key_order_and_source_first(self):
        entries = ej.build_json_entries(RAGGED_TASK_ROWS)
        self.assertEqual([e["key"] for e in entries], ["K.A", "K.B"])
        # en-US is always the first language column after the key.
        first_cols = list(entries[0].keys())
        self.assertEqual(first_cols[0], "key")
        self.assertEqual(first_cols[1], "en-US")

    def test_en_us_falls_back_to_source(self):
        rows = [{"string_key": "K.X", "language": "de-DE",
                 "source_text": "Save", "translated_text": "Speichern"}]
        entries = ej.build_json_entries(rows)
        self.assertEqual(entries[0]["en-US"], "Save")


class TestFillMissingObservedUnion(unittest.TestCase):
    """fill_missing=True with no configured list → every key padded to the
    union of languages observed within its own task."""

    def test_every_key_gets_full_observed_set(self):
        entries = ej.build_json_entries(RAGGED_TASK_ROWS, fill_missing=True)
        by_key = _by_key(entries)
        full = {"en-US", "de-DE", "zh-CN", "fi-FI"}
        self.assertEqual(_langs(by_key["K.A"]), full)
        self.assertEqual(_langs(by_key["K.B"]), full)

    def test_filled_values_are_empty_string(self):
        entries = ej.build_json_entries(RAGGED_TASK_ROWS, fill_missing=True)
        by_key = _by_key(entries)
        # The genuinely-absent translations are surfaced as "" (not fabricated).
        self.assertEqual(by_key["K.B"]["zh-CN"], "")
        self.assertEqual(by_key["K.B"]["fi-FI"], "")
        # Existing translations are preserved verbatim.
        self.assertEqual(by_key["K.A"]["zh-CN"], "工时")
        self.assertEqual(by_key["K.B"]["de-DE"], "Paket")


class TestFillMissingConfiguredLanguages(unittest.TestCase):
    """all_languages supplies the authoritative configured set, covering even
    a language that has ZERO translations anywhere in the task."""

    def test_zero_data_configured_language_is_added(self):
        # ja-JP is configured but appears in NO row → observed-union alone
        # would miss it; the configured list must still surface it.
        configured = ["en-US", "de-DE", "zh-CN", "fi-FI", "ja-JP"]
        entries = ej.build_json_entries(
            RAGGED_TASK_ROWS, fill_missing=True, all_languages=configured)
        for e in entries:
            self.assertIn("ja-JP", e)
            self.assertEqual(e["ja-JP"], "")
            self.assertEqual(_langs(e), set(configured))

    def test_observed_language_not_in_config_is_never_dropped(self):
        # A stray observed language (es-ES) absent from the configured list
        # must survive — we never drop real data.
        rows = RAGGED_TASK_ROWS + [
            {"task_id": 292, "string_key": "K.A", "language": "es-ES",
             "source_text": "Hours", "translated_text": "Horas"},
        ]
        entries = ej.build_json_entries(
            rows, fill_missing=True, all_languages=["en-US", "de-DE"])
        by_key = _by_key(entries)
        self.assertIn("es-ES", by_key["K.A"])
        self.assertEqual(by_key["K.A"]["es-ES"], "Horas")


class TestPerTaskScoping(unittest.TestCase):
    """All-tasks export: each key is padded only to its own task's languages.
    Cross-product false-fill (A's keys gaining B's languages) is forbidden."""

    def setUp(self):
        # Task 1 (product A) targets en-US/de-DE; task 2 (product B) targets
        # en-US/ja-JP. Different products, different configured languages.
        self.rows = [
            {"task_id": 1, "string_key": "A.1", "language": "en-US",
             "source_text": "Go", "translated_text": "Go"},
            {"task_id": 1, "string_key": "A.1", "language": "de-DE",
             "source_text": "Go", "translated_text": "Los"},
            {"task_id": 2, "string_key": "B.1", "language": "en-US",
             "source_text": "Stop", "translated_text": "Stop"},
            {"task_id": 2, "string_key": "B.1", "language": "ja-JP",
             "source_text": "Stop", "translated_text": "停止"},
        ]

    def test_observed_union_is_scoped_per_task(self):
        entries = ej.build_json_entries(self.rows, fill_missing=True)
        by_key = _by_key(entries)
        # A.1 must NOT gain ja-JP; B.1 must NOT gain de-DE.
        self.assertEqual(_langs(by_key["A.1"]), {"en-US", "de-DE"})
        self.assertEqual(_langs(by_key["B.1"]), {"en-US", "ja-JP"})

    def test_same_key_across_tasks_does_not_over_fill(self):
        # Regression for the reviewer-confirmed cross-task collision defect:
        # the SAME key under two tasks must NOT be padded with languages that
        # were never actually translated for it. It gets only the union of
        # languages genuinely OBSERVED across its tasks (all real values), and
        # nothing is fabricated beyond "" for languages it truly lacks.
        rows = [
            {"task_id": 1, "string_key": "SHARED", "language": "en-US",
             "source_text": "Go", "translated_text": "Go"},
            {"task_id": 1, "string_key": "SHARED", "language": "de-DE",
             "source_text": "Go", "translated_text": "Los"},
            {"task_id": 2, "string_key": "SHARED", "language": "en-US",
             "source_text": "Go", "translated_text": "Go"},
            {"task_id": 2, "string_key": "SHARED", "language": "fr-FR",
             "source_text": "Go", "translated_text": "Allez"},
        ]
        entries = ej.build_json_entries(rows, fill_missing=True)
        self.assertEqual(len(entries), 1)
        shared = entries[0]
        # Only the genuinely-observed languages — no spurious padding.
        self.assertEqual(_langs(shared), {"en-US", "de-DE", "fr-FR"})
        self.assertEqual(shared["de-DE"], "Los")
        self.assertEqual(shared["fr-FR"], "Allez")
        # No empty-string fabrications crept in.
        self.assertNotIn("", shared.values())


class TestMrScanRowSchema(unittest.TestCase):
    """MR/Scan rows use opus_id/target_language. Densification by observed
    union still works when rows carry no task_id (falls back to global)."""

    def test_opus_id_rows_densify_to_global_union(self):
        rows = [
            {"opus_id": "O.1", "target_language": "en-US",
             "source_text": "A", "translated_text": "A"},
            {"opus_id": "O.1", "target_language": "fr-FR",
             "source_text": "A", "translated_text": "Aa"},
            {"opus_id": "O.2", "target_language": "en-US",
             "source_text": "B", "translated_text": "B"},
        ]
        entries = ej.build_json_entries(rows, fill_missing=True)
        by_key = _by_key(entries)
        # No task_id anywhere → fall back to the global observed union.
        self.assertEqual(_langs(by_key["O.2"]), {"en-US", "fr-FR"})
        self.assertEqual(by_key["O.2"]["fr-FR"], "")


class TestSegmentStableKeys(unittest.TestCase):
    """UNS scan /results reuses the file-level opus_id across tu_id segments.
    The QA pivot must not last-write-wins them into one key."""

    SEG_ROWS = [
        {"opus_id": "common.uns.airLeadCaptured__email_html__1210",
         "has_seg_units": True, "tu_id": 2, "target_language": "de-DE",
         "source_text": "New lead from X", "translated_text": "Neues Lead von X"},
        {"opus_id": "common.uns.airLeadCaptured__email_html__1210",
         "has_seg_units": True, "tu_id": 5, "target_language": "de-DE",
         "source_text": "Hello,", "translated_text": "Hallo,"},
        {"opus_id": "common.uns.airLeadCaptured__email_html__1210",
         "has_seg_units": True, "tu_id": 2, "target_language": "zh-CN",
         "source_text": "New lead from X", "translated_text": "来自 X 的新潜在客户"},
    ]

    def test_segments_become_distinct_keys(self):
        entries = ej.build_json_entries(self.SEG_ROWS, fill_missing=True)
        keys = [e["key"] for e in entries]
        self.assertEqual(keys, [
            "common.uns.airLeadCaptured__email_html__1210:::seg:::2",
            "common.uns.airLeadCaptured__email_html__1210:::seg:::5",
        ])
        by_key = _by_key(entries)
        self.assertEqual(
            by_key["common.uns.airLeadCaptured__email_html__1210:::seg:::2"]["de-DE"],
            "Neues Lead von X")
        self.assertEqual(
            by_key["common.uns.airLeadCaptured__email_html__1210:::seg:::5"]["de-DE"],
            "Hallo,")
        # Same segment, other language, stays on the same key.
        self.assertEqual(
            by_key["common.uns.airLeadCaptured__email_html__1210:::seg:::2"]["zh-CN"],
            "来自 X 的新潜在客户")

    def test_already_segmented_opus_id_is_not_double_stamped(self):
        rows = [{"opus_id": "common.uns.foo__email_html__1:::seg:::9",
                 "has_seg_units": True, "tu_id": 9, "target_language": "en-US",
                 "source_text": "Hi", "translated_text": "Hi"}]
        entries = ej.build_json_entries(rows)
        self.assertEqual(entries[0]["key"],
                         "common.uns.foo__email_html__1:::seg:::9")

    def test_non_segmented_rows_keep_plain_opus_id(self):
        rows = [{"opus_id": "plain.key", "target_language": "en-US",
                 "source_text": "A", "translated_text": "A"}]
        entries = ej.build_json_entries(rows)
        self.assertEqual(entries[0]["key"], "plain.key")


class TestRobustness(unittest.TestCase):
    """Malformed input must be skipped gracefully, not crash the export."""

    def test_non_dict_rows_are_skipped(self):
        rows = [
            {"task_id": 1, "string_key": "K", "language": "en-US",
             "source_text": "A", "translated_text": "A"},
            ["garbage"],     # list
            "nonsense",      # str
            None,            # None
            42,              # int
        ]
        # Must not raise AttributeError; the one valid row survives.
        entries = ej.build_json_entries(rows, fill_missing=True)
        self.assertEqual([e["key"] for e in entries], ["K"])
        self.assertEqual(entries[0]["en-US"], "A")

    def test_non_dict_rows_skipped_in_default_mode(self):
        entries = ej.build_json_entries([["x"], None, 1])
        self.assertEqual(entries, [])


class TestChangesPayloadStaysSparse(unittest.TestCase):
    """The Changes export (and any caller using defaults) must remain sparse
    even when fed a dict payload — no language padding."""

    def test_dict_payload_default_is_sparse(self):
        payload = {"translations": [
            {"opus_id": "O.1", "target_language": "en-US",
             "source_text": "A", "translated_text": "A"},
            {"opus_id": "O.1", "target_language": "zh-CN",
             "source_text": "A", "translated_text": "甲"},
            {"opus_id": "O.2", "target_language": "en-US",
             "source_text": "B", "translated_text": "B"},
        ]}
        entries = ej.build_json_entries(payload["translations"])
        by_key = _by_key(entries)
        # O.2 stays at just en-US — Changes report is NOT rectangularized.
        self.assertEqual(_langs(by_key["O.2"]), {"en-US"})


class TestWriteTranslationsJson(unittest.TestCase):
    """End-to-end through write_translations_json (the real entry point)."""

    def test_dict_payload_with_fill(self):
        import json
        import tempfile
        payload = {"translations": [
            {"opus_id": "O.1", "target_language": "en-US",
             "source_text": "A", "translated_text": "A"},
            {"opus_id": "O.1", "target_language": "zh-CN",
             "source_text": "A", "translated_text": "甲"},
            {"opus_id": "O.2", "target_language": "en-US",
             "source_text": "B", "translated_text": "B"},
        ]}
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.json")
            entries = ej.write_translations_json(
                payload, out, fill_missing=True)
            with open(out, encoding="utf-8") as f:
                on_disk = json.load(f)
        # Both keys rectangular; O.2 gets an empty zh-CN.
        by_key = _by_key(on_disk)
        self.assertEqual(_langs(by_key["O.2"]), {"en-US", "zh-CN"})
        self.assertEqual(by_key["O.2"]["zh-CN"], "")
        self.assertEqual(entries, on_disk)


class TestSaveMrFileGating(unittest.TestCase):
    """MR Pipeline / Scan Tasks tabs route through export_mr_pipeline.save_mr_file.
    Its JSON branch must densify ONLY when fill_missing=True (All Translations)
    and stay sparse otherwise (Changes)."""

    def setUp(self):
        import export_mr_pipeline as mp
        self.mp = mp
        # Ragged dict payload, MR/Scan row schema (opus_id/target_language),
        # carrying task_id so per-task scoping applies.
        self.results = {"translations": [
            {"opus_id": "O.1", "task_id": "t1", "target_language": "en-US",
             "source_text": "A", "translated_text": "A"},
            {"opus_id": "O.1", "task_id": "t1", "target_language": "zh-CN",
             "source_text": "A", "translated_text": "甲"},
            {"opus_id": "O.2", "task_id": "t1", "target_language": "en-US",
             "source_text": "B", "translated_text": "B"},
        ]}

    def _export(self, fill_missing):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "mr.json")
            self.mp.save_mr_file(self.results, out, "label", "json",
                                 fill_missing=fill_missing)
            with open(out, encoding="utf-8") as f:
                return _by_key(json.load(f))

    def test_all_translations_densifies(self):
        by_key = self._export(fill_missing=True)
        self.assertEqual(_langs(by_key["O.2"]), {"en-US", "zh-CN"})
        self.assertEqual(by_key["O.2"]["zh-CN"], "")

    def test_changes_stays_sparse(self):
        by_key = self._export(fill_missing=False)
        self.assertEqual(_langs(by_key["O.2"]), {"en-US"})


if __name__ == "__main__":
    unittest.main()
