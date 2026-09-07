"""Regression tests for the Scan Tasks "en-US Strings" column reading 0 for a
completed task (LOC-25243, 2026-09-07).

Root cause this guards against
------------------------------
``ScanTasksTab`` caches each task's en-US source-string count for the life
of the process and only re-fetches when the cached value is ``None``. The
old counter (``count_scan_source_strings``) collapsed three very different
situations into the number 0:

  1. a genuinely empty scan (nothing to translate),
  2. a task that had not finished yet — its ``summary`` rows are written by
     the final ANALYZE step and ``/results`` is still filling up, and
  3. a transient API failure (502 storm, timeout).

LOC-25243 was created 10:41 and completed 10:50 (local). The Scan Tasks list
was rendered in between, cached the 0 from (2), and — because Search / Reset /
Refresh only cleared the ✏️ post-edit cache, never this one — kept showing
0 for the rest of the session while the Tranzor Strings tab showed 42 per
language.

The fix: ``count_scan_source_strings_or_none`` returns ``None`` for (2) and
(3); ``_resolve_src_count`` never caches ``None``; the cell shows "—" and
the next render asks again. Only final answers (a finished task's count —
including a real 0) are cached.

The tests construct ``ScanTasksTab`` via ``__new__`` (no Tk root / display
needed) and stub the widget/network touchpoints, matching the Tk-free style
of the rest of the suite.

Run:  python -m unittest test_scan_src_count_cache
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_mr_pipeline as mr_api
import gui_tab_scan_tasks


class _FakeTree:
    """Records ``tree.set(iid, column, value)`` calls."""

    def __init__(self):
        self.cells = {}

    def set(self, iid, column, value):
        self.cells[(iid, column)] = value


def _bare_tab():
    tab = gui_tab_scan_tasks.ScanTasksTab.__new__(gui_tab_scan_tasks.ScanTasksTab)
    tab._scan_src_cache = {}
    tab._scan_src_lock = threading.Lock()
    tab._scan_row_iid_by_task = {}
    tab.scan_tree = _FakeTree()
    return tab


TASK = "320cac08-fabb-43d9-8f7b-09b8b396e2d5"


class ResolveSrcCountTests(unittest.TestCase):

    def test_running_task_is_not_cached_and_renders_dash(self):
        tab = _bare_tab()
        tab._scan_row_iid_by_task[TASK] = TASK
        with mock.patch.object(
            mr_api, "count_scan_source_strings_or_none", return_value=None,
        ) as fetch:
            count = tab._resolve_src_count(TASK)
        self.assertIsNone(count)
        fetch.assert_called_once_with(TASK)
        self.assertNotIn(TASK, tab._scan_src_cache)

        tab._apply_src_count(TASK, count)
        self.assertEqual(tab.scan_tree.cells[(TASK, "src_strings")],
                         gui_tab_scan_tasks.SRC_COUNT_UNKNOWN)

    def test_first_seen_mid_run_then_completed_shows_real_count(self):
        """The LOC-25243 timeline: listed while running, re-listed after
        completion. The second render must show 42, not the first 0."""
        tab = _bare_tab()
        tab._scan_row_iid_by_task[TASK] = TASK

        # 10:45 — task running, no summary yet.
        with mock.patch.object(
            mr_api, "count_scan_source_strings_or_none", return_value=None,
        ):
            self.assertIsNone(tab._resolve_src_count(TASK))
        self.assertEqual(tab._scan_src_cache, {})

        # 11:00 — user hits Search/Refresh; task completed with 42 strings.
        with mock.patch.object(
            mr_api, "count_scan_source_strings_or_none", return_value=42,
        ) as fetch:
            self.assertEqual(tab._resolve_src_count(TASK), 42)
        fetch.assert_called_once()
        self.assertEqual(tab._scan_src_cache[TASK], 42)

        tab._apply_src_count(TASK, 42)
        self.assertEqual(tab.scan_tree.cells[(TASK, "src_strings")], 42)

        # Paging back: served from cache, no further API call.
        with mock.patch.object(
            mr_api, "count_scan_source_strings_or_none",
            side_effect=AssertionError("must not re-fetch a cached count"),
        ):
            self.assertEqual(tab._resolve_src_count(TASK), 42)

    def test_genuine_zero_for_finished_task_is_cached(self):
        # Scheduled integration scans routinely find nothing; their 0 is
        # final and must stay cheap (no re-fetch on every page flip).
        tab = _bare_tab()
        with mock.patch.object(
            mr_api, "count_scan_source_strings_or_none", return_value=0,
        ):
            self.assertEqual(tab._resolve_src_count("empty-task"), 0)
        self.assertEqual(tab._scan_src_cache["empty-task"], 0)
        with mock.patch.object(
            mr_api, "count_scan_source_strings_or_none",
            side_effect=AssertionError("must not re-fetch a cached 0"),
        ):
            self.assertEqual(tab._resolve_src_count("empty-task"), 0)

    def test_transient_failure_is_retried_next_render(self):
        tab = _bare_tab()
        with mock.patch.object(
            mr_api, "count_scan_source_strings_or_none", return_value=None,
        ):
            self.assertIsNone(tab._resolve_src_count(TASK))
        with mock.patch.object(
            mr_api, "count_scan_source_strings_or_none", return_value=7,
        ):
            self.assertEqual(tab._resolve_src_count(TASK), 7)
        self.assertEqual(tab._scan_src_cache[TASK], 7)

    def test_apply_ignores_rows_no_longer_on_screen(self):
        tab = _bare_tab()
        tab._apply_src_count("gone", 5)  # must not raise
        self.assertEqual(tab.scan_tree.cells, {})

    def test_display_helper(self):
        disp = gui_tab_scan_tasks.ScanTasksTab._src_count_display
        self.assertEqual(disp(None), gui_tab_scan_tasks.SRC_COUNT_UNKNOWN)
        self.assertEqual(disp(0), 0)
        self.assertEqual(disp(42), 42)


class LegendTests(unittest.TestCase):
    def test_dash_legend_present_in_both_languages(self):
        for lang in ("en", "zh"):
            text = gui_tab_scan_tasks.STRINGS[lang]["scan_src_count_legend"]
            self.assertTrue(text.startswith(gui_tab_scan_tasks.SRC_COUNT_UNKNOWN),
                            (lang, text))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
