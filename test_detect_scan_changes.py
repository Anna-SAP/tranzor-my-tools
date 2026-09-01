"""Tests for Scan Tasks Changes export + later-content-change detection.

Root cause this guards against
------------------------------
Clicking Export Selected with Export Type = Changes on a large UNS scan
(e.g. ``uns release_26-3-3 01``, 13k rows) sat on "Exporting..." forever:
``detect_scan_changes`` dumped the entire ``/results`` payload (1000-row
pages, ~10s each) while the ✏️ / en-US-Strings prefetches did the same
and held the process-wide HTTP gate of 2. The scan ``/results`` schema
also has no ``translation_type``, so the ✏️ badge never lit even when
``iteration_history`` clearly showed later text changes.

The rewrite:

  * pages ``/results`` at the backend cap (1000) and filters as it goes
    (no full dump before the first change row is known); ✏️ uses a
    200-row first page so a hit short-circuits fast
  * ✏️ short-circuits on the first iteration_history hit (or a GitLab
    Language Lead fix on the scan's import MR)
  * en-US Strings reads the cheap task-detail summary

Run:  python -m unittest test_detect_scan_changes
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_mr_pipeline as mr
import task_post_edit as tpe


def _row(opus, lang, curr, prev=None, ttype=""):
    hist = None
    iteration = 1
    if prev is not None:
        hist = {"iteration_1": {"translation": prev}}
        iteration = 2
    return {
        "opus_id": opus,
        "target_language": lang,
        "source_text": f"src-{opus}",
        "translated_text": curr,
        "iteration": iteration,
        "iteration_history": hist,
        "translation_type": ttype,
        "final_score": 99,
    }


class ParseMrIidTests(unittest.TestCase):
    def test_standard_gitlab_url(self):
        self.assertEqual(
            mr.parse_mr_iid_from_url(
                "https://git.ringcentral.com/common/uns/-/merge_requests/4095"),
            4095)

    def test_url_with_trailing_path(self):
        self.assertEqual(
            mr.parse_mr_iid_from_url(
                "https://git.example/p/-/merge_requests/12/diffs"),
            12)

    def test_empty_and_unrelated(self):
        self.assertIsNone(mr.parse_mr_iid_from_url(""))
        self.assertIsNone(mr.parse_mr_iid_from_url(None))
        self.assertIsNone(mr.parse_mr_iid_from_url("https://example/scans/abc"))


class ScanRowChangeTests(unittest.TestCase):
    def test_iteration_history_text_drift_is_a_change(self):
        pair = mr.scan_row_prev_and_curr(_row("k", "de-DE", "v2", prev="v1"))
        self.assertEqual(pair, ("v1", "v2"))
        self.assertTrue(mr.scan_row_has_content_change(
            _row("k", "de-DE", "v2", prev="v1")))

    def test_same_text_is_not_a_change(self):
        self.assertIsNone(mr.scan_row_prev_and_curr(
            _row("k", "de-DE", "v1", prev="v1")))
        self.assertFalse(mr.scan_row_has_content_change(
            _row("k", "de-DE", "v1", prev="v1")))

    def test_no_history_is_not_a_change(self):
        self.assertFalse(mr.scan_row_has_content_change(
            _row("k", "de-DE", "v1")))

    def test_manual_edit_label_counts_even_without_history(self):
        row = _row("k", "de-DE", "v2")
        row["translation_type"] = "Manual Edit"
        self.assertTrue(mr.scan_row_has_content_change(row))


class ScanTaskHasContentChangeTests(unittest.TestCase):
    def test_short_circuits_after_first_page_hit(self):
        """A 13k-row scan must not download every page just to light ✏️."""
        pages = []

        def _page(task_id, limit=200, offset=0, base_url=None):
            pages.append(offset)
            if offset == 0:
                return {"total": 400, "translations": [
                    _row("k1", "de-DE", "v1"),
                    _row("k2", "de-DE", "v2", prev="v1"),
                ]}
            raise AssertionError("must not request later pages after a hit")

        with mock.patch.object(mr, "fetch_scan_task_detail", return_value={}), \
             mock.patch.object(mr, "scan_import_mr_has_lead_fix",
                               return_value=False), \
             mock.patch.object(mr, "fetch_scan_results_page", side_effect=_page):
            self.assertTrue(mr.scan_task_has_content_change("t"))
        self.assertEqual(pages, [0])

    def test_import_mr_lead_fix_skips_results(self):
        with mock.patch.object(
            mr, "fetch_scan_task_detail",
            return_value={"import_mr_url": "https://git/x/-/merge_requests/1",
                          "project_id": "common/uns"},
        ), mock.patch.object(
            mr, "scan_import_mr_has_lead_fix", return_value=True,
        ) as lead, mock.patch.object(
            mr, "fetch_scan_results_page",
        ) as page:
            self.assertTrue(mr.scan_task_has_content_change("t"))
        lead.assert_called_once()
        page.assert_not_called()

    def test_no_change_anywhere_is_false(self):
        def _page(task_id, limit=200, offset=0, base_url=None):
            if offset == 0:
                return {"total": 2, "translations": [
                    _row("k1", "de-DE", "v1"),
                    _row("k2", "fr-FR", "v1"),
                ]}
            return {"total": 2, "translations": []}

        with mock.patch.object(mr, "fetch_scan_task_detail", return_value={}), \
             mock.patch.object(mr, "scan_import_mr_has_lead_fix",
                               return_value=False), \
             mock.patch.object(mr, "fetch_scan_results_page", side_effect=_page):
            self.assertFalse(mr.scan_task_has_content_change("t"))


class DetectScanChangesTests(unittest.TestCase):
    def test_collects_iteration_diffs_across_pages(self):
        def _detail(task_id, base_url=None):
            return {"task_id": task_id, "project_id": "common/uns",
                    "base_ref": "a", "head_ref": "b", "created_at": "t0",
                    "import_mr_url": None}

        def _page(task_id, limit=200, offset=0, base_url=None):
            if offset == 0:
                return {"total": 3, "translations": [
                    _row("k1", "de-DE", "Hallo"),
                    _row("k2", "de-DE", "Tschüss v2", prev="Tschüss"),
                ]}
            if offset == 2:
                return {"total": 3, "translations": [
                    _row("k3", "fr-FR", "Salut v2", prev="Salut"),
                ]}
            return {"total": 3, "translations": []}

        logs = []
        with mock.patch.object(mr, "fetch_scan_task_detail", side_effect=_detail), \
             mock.patch.object(mr, "fetch_scan_results_page", side_effect=_page), \
             mock.patch.object(mr, "_collect_scan_gitlab_lead_fix_changes",
                               return_value=[]):
            rows = mr.detect_scan_changes("scan-1", progress_callback=logs.append)

        opus = {(r["opus_id"], r["target_language"]) for r in rows}
        self.assertEqual(opus, {("k2", "de-DE"), ("k3", "fr-FR")})
        by_k2 = next(r for r in rows if r["opus_id"] == "k2")
        self.assertEqual(by_k2["prev_translated_text"], "Tschüss")
        self.assertEqual(by_k2["translated_text"], "Tschüss v2")
        self.assertEqual(by_k2["change_source"], "scan-refinement")
        self.assertEqual(by_k2["scan_task_id"], "scan-1")
        # Must not have waited to materialise a full 3-row dump via
        # fetch_scan_results — that's the hang we are fixing.
        self.assertTrue(any("已扫描" in m or "变更" in m for m in logs))

    def test_does_not_call_full_fetch_scan_results(self):
        with mock.patch.object(
            mr, "fetch_scan_task_detail",
            return_value={"project_id": "p", "base_ref": "", "head_ref": ""},
        ), mock.patch.object(
            mr, "fetch_scan_results_page",
            return_value={"total": 0, "translations": []},
        ), mock.patch.object(
            mr, "fetch_scan_results",
        ) as full, mock.patch.object(
            mr, "_collect_scan_gitlab_lead_fix_changes", return_value=[],
        ):
            mr.detect_scan_changes("t", progress_callback=lambda *_: None)
        full.assert_not_called()

    def test_merges_gitlab_lead_fix_rows(self):
        extra = [{
            "opus_id": "common.uns.foo__email_html__1",
            "target_language": "fr-CA",
            "prev_translated_text": "old",
            "translated_text": "new",
            "change_source": "language-lead-fix (gitlab-branch)",
            "scan_task_id": "t",
        }]
        with mock.patch.object(
            mr, "fetch_scan_task_detail",
            return_value={"project_id": "common/uns",
                          "import_mr_url": "https://git/x/-/merge_requests/9",
                          "base_ref": "a", "head_ref": "b"},
        ), mock.patch.object(
            mr, "fetch_scan_results_page",
            return_value={"total": 0, "translations": []},
        ), mock.patch.object(
            mr, "_collect_scan_gitlab_lead_fix_changes", return_value=extra,
        ):
            rows = mr.detect_scan_changes("t", progress_callback=lambda *_: None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["change_source"],
                         "language-lead-fix (gitlab-branch)")

    def test_manual_edit_without_history_is_kept(self):
        row = _row("k", "de-DE", "edited")
        row["translation_type"] = "Manual Edit"
        with mock.patch.object(
            mr, "fetch_scan_task_detail",
            return_value={"project_id": "p", "base_ref": "", "head_ref": ""},
        ), mock.patch.object(
            mr, "fetch_scan_results_page",
            return_value={"total": 1, "translations": [row]},
        ), mock.patch.object(
            mr, "_collect_scan_gitlab_lead_fix_changes", return_value=[],
        ):
            rows = mr.detect_scan_changes("t", progress_callback=lambda *_: None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["change_source"], "scan-manual-edit")
        self.assertEqual(rows[0]["translated_text"], "edited")


class FetchScanDelegatesTests(unittest.TestCase):
    def test_fetch_scan_uses_short_circuit_probe(self):
        with mock.patch.object(
            mr, "scan_task_has_content_change", return_value=True,
        ) as probe:
            self.assertTrue(tpe._fetch_scan("scan-1"))
        probe.assert_called_once_with("scan-1")

    def test_fetch_scan_swallows_errors(self):
        with mock.patch.object(
            mr, "scan_task_has_content_change",
            side_effect=RuntimeError("timeout"),
        ):
            self.assertFalse(tpe._fetch_scan("scan-1"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
