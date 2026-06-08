"""Unit tests for the Human Revisions File-Translation collector.

Focus: the date window must match the **revision time** (an edit-log's
``created_at``), NOT the parent task's creation time. Regression guard for
the LOC-24054 incident — a task created in March, fixed by a Language Lead
in June, was invisible because the old collector pre-filtered tasks by
``created_at`` and dropped the (old) task before ever reading its
edit-logs.

Pure-Python; no HTTP. The channel fetchers are monkeypatched so tests run
offline and deterministically.

Run:  python -m unittest test_human_revisions_window
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_mr_pipeline as mp


# ---------------------------------------------------------------------------
# _revision_in_window — pure boundary logic
# ---------------------------------------------------------------------------
class RevisionInWindowTests(unittest.TestCase):
    START = "2026-05-09T00:00:00"
    END = "2026-06-08T23:59:59"

    def test_inside_window(self):
        self.assertTrue(
            mp._revision_in_window("2026-06-08T07:03:00", self.START, self.END))

    def test_before_start_excluded(self):
        # Same-era as the March task but before the 30-day window.
        self.assertFalse(
            mp._revision_in_window("2026-03-07T10:00:00", self.START, self.END))

    def test_after_end_excluded(self):
        self.assertFalse(
            mp._revision_in_window("2026-06-09T00:00:01", self.START, self.END))

    def test_microseconds_do_not_clip_end_boundary(self):
        # An edit at 23:59:59.6 must still count for an end of 23:59:59.
        self.assertTrue(
            mp._revision_in_window(
                "2026-06-08T23:59:59.600000", self.START, self.END))

    def test_trailing_z_is_tolerated(self):
        self.assertTrue(
            mp._revision_in_window("2026-06-08T07:03:00Z", self.START, self.END))

    def test_open_ended_bounds(self):
        self.assertTrue(mp._revision_in_window("2020-01-01T00:00:00", None, None))

    def test_empty_timestamp_is_out(self):
        self.assertFalse(mp._revision_in_window("", self.START, self.END))
        self.assertFalse(mp._revision_in_window(None, self.START, self.END))


# ---------------------------------------------------------------------------
# _fetch_post_edited_entries — fast probe + older-backend fallback
# ---------------------------------------------------------------------------
class FetchPostEditedEntriesTests(unittest.TestCase):
    def test_fast_path_uses_server_filter(self):
        with mock.patch.object(
            mp, "fetch_all_legacy_translations_quality",
            return_value=[{"translation_id": 1, "translation_type": "Manual Edit"}],
        ) as fetch:
            rows = mp._fetch_post_edited_entries("2")
        self.assertEqual(len(rows), 1)
        fetch.assert_called_once_with("2", label_types=["post_edited"])

    def test_falls_back_to_full_scan_on_older_backend(self):
        # First call (label_types=...) raises like a 4xx; second (no
        # label_types) returns the full list, which we filter client-side.
        def _side_effect(task_id, **kwargs):
            if "label_types" in kwargs:
                raise RuntimeError("422 unknown query param on old backend")
            return [
                {"translation_id": 1, "translation_type": "Manual Edit"},
                {"translation_id": 2, "translation_type": "LLM"},  # machine
                {"translation_id": 3, "translation_type": "LLM Retranslate"},
            ]

        with mock.patch.object(
            mp, "fetch_all_legacy_translations_quality", side_effect=_side_effect,
        ):
            rows = mp._fetch_post_edited_entries("2")

        kept = {r["translation_id"] for r in rows}
        self.assertEqual(kept, {1, 3})  # machine row dropped

    def test_returns_empty_when_both_paths_fail(self):
        with mock.patch.object(
            mp, "fetch_all_legacy_translations_quality",
            side_effect=RuntimeError("network down"),
        ):
            self.assertEqual(mp._fetch_post_edited_entries("2"), [])


# ---------------------------------------------------------------------------
# _collect_legacy_revisions — the actual regression
# ---------------------------------------------------------------------------
class CollectLegacyRevisionsTests(unittest.TestCase):
    # A task created long before the window — exactly the LOC-24054 shape.
    OLD_TASK = {
        "id": 2,
        "task_name": "LOC-24054 Integration Salesforce 26.1.30 - Drop 1",
        "project_name": "Integration",
        "created_at": "2026-03-06T02:07:07",
    }
    WINDOW_START = "2026-05-09T00:00:00"
    WINDOW_END = "2026-06-08T23:59:59"

    def _post_edited(self, *_a, **_k):
        return [{
            "translation_id": 99,
            "opus_id": "sip.manual.provisioning",
            "target_language": "fr-FR",
            "source_text": "Getting the SIP settings for manual provisioning",
            "translated_text": "Obtention des paramètres SIP ...",
            "translation_type": "Manual Edit",
            "final_score": 95,
            "error_category": None,
        }]

    def test_recent_fix_on_old_task_is_captured(self):
        """The whole point: task from March, fix from June, default 30-day
        window → the revision MUST appear."""
        recent_log = [{
            "original_text": "Obtention des réglages SIP ...",
            "edited_text": "Obtention des paramètres SIP ...",
            "user_name": "sean.zhuang",
            "created_at": "2026-06-08T07:03:00",
            "notes": "",
        }]
        with mock.patch.object(
            mp, "fetch_all_legacy_tasks_for_quality",
            return_value=[self.OLD_TASK],
        ), mock.patch.object(
            mp, "_fetch_post_edited_entries", side_effect=self._post_edited,
        ), mock.patch.object(
            mp, "fetch_legacy_translation_edit_logs", return_value=recent_log,
        ):
            revs = mp._collect_legacy_revisions(
                start_time=self.WINDOW_START, end_time=self.WINDOW_END)

        self.assertEqual(len(revs), 1)
        r = revs[0]
        self.assertEqual(r["target_language"], "fr-FR")
        self.assertEqual(r["editor"], "sean.zhuang")
        self.assertEqual(r["revised_at"], "2026-06-08T07:03:00")
        self.assertEqual(r["channel"], "File Translation")

    def test_old_fix_on_old_task_is_excluded_by_window(self):
        """Same old task, but the edit happened back in March — outside the
        window → excluded. Proves we filter by revision time, not task age
        (and that we don't just return everything)."""
        old_log = [{
            "original_text": "x",
            "edited_text": "y",
            "user_name": "someone",
            "created_at": "2026-03-07T09:00:00",
            "notes": "",
        }]
        with mock.patch.object(
            mp, "fetch_all_legacy_tasks_for_quality",
            return_value=[self.OLD_TASK],
        ), mock.patch.object(
            mp, "_fetch_post_edited_entries", side_effect=self._post_edited,
        ), mock.patch.object(
            mp, "fetch_legacy_translation_edit_logs", return_value=old_log,
        ):
            revs = mp._collect_legacy_revisions(
                start_time=self.WINDOW_START, end_time=self.WINDOW_END)

        self.assertEqual(revs, [])

    def test_multiple_logs_windowed_independently(self):
        """One translation with two edits: only the in-window one survives."""
        logs = [
            {"original_text": "a", "edited_text": "b", "user_name": "u",
             "created_at": "2026-06-01T10:00:00", "notes": ""},   # in
            {"original_text": "c", "edited_text": "d", "user_name": "u",
             "created_at": "2026-03-07T10:00:00", "notes": ""},   # out
        ]
        with mock.patch.object(
            mp, "fetch_all_legacy_tasks_for_quality",
            return_value=[self.OLD_TASK],
        ), mock.patch.object(
            mp, "_fetch_post_edited_entries", side_effect=self._post_edited,
        ), mock.patch.object(
            mp, "fetch_legacy_translation_edit_logs", return_value=logs,
        ):
            revs = mp._collect_legacy_revisions(
                start_time=self.WINDOW_START, end_time=self.WINDOW_END)

        self.assertEqual(len(revs), 1)
        self.assertEqual(revs[0]["revised_at"], "2026-06-01T10:00:00")

    def test_all_completed_tasks_are_scanned(self):
        """No task-level date pre-filter: every Completed task is probed,
        even ones far older than the window."""
        tasks = [
            dict(self.OLD_TASK, id=2),
            dict(self.OLD_TASK, id=7, created_at="2024-01-01T00:00:00"),
        ]
        seen = []

        def _probe(task_id, *_a, **_k):
            seen.append(task_id)
            return []

        with mock.patch.object(
            mp, "fetch_all_legacy_tasks_for_quality", return_value=tasks,
        ), mock.patch.object(
            mp, "_fetch_post_edited_entries", side_effect=_probe,
        ), mock.patch.object(
            mp, "fetch_legacy_translation_edit_logs", return_value=[],
        ):
            revs = mp._collect_legacy_revisions(
                start_time=self.WINDOW_START, end_time=self.WINDOW_END)

        self.assertEqual(revs, [])
        self.assertEqual(set(seen), {"2", "7"})  # both probed, incl. 2024 task

    def test_progress_callback_reports_each_task(self):
        tasks = [dict(self.OLD_TASK, id=i) for i in range(3)]
        msgs = []
        with mock.patch.object(
            mp, "fetch_all_legacy_tasks_for_quality", return_value=tasks,
        ), mock.patch.object(
            mp, "_fetch_post_edited_entries", return_value=[],
        ), mock.patch.object(
            mp, "fetch_legacy_translation_edit_logs", return_value=[],
        ):
            mp._collect_legacy_revisions(
                start_time=self.WINDOW_START, end_time=self.WINDOW_END,
                progress_callback=msgs.append)

        # One "Loading..." + one progress line per completed task.
        scan_msgs = [m for m in msgs if m.startswith("Scanning File Translation")]
        self.assertEqual(len(scan_msgs), 3)
        self.assertIn("3/3", scan_msgs[-1])


if __name__ == "__main__":
    unittest.main()
