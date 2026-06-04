"""Regression tests for the Scan Tasks ✏️ (post-edit) badge staleness bug.

Root cause this guards against
------------------------------
The ✏️ badge on the Scan Tasks list is served from a process-lifetime cache
(``task_post_edit.PostEditCache``). A reviewer fixes a scan-task translation in
the Tranzor dashboard *after* the list may have already cached a ``False`` "no
human edit" answer for that task. The render only queues a re-fetch when the
cached value is ``None`` (see the ``cached is None`` gate in
``ScanTasksTab._on_tasks_loaded``), so a cached ``False`` is sticky: the badge
never lights up even though a fresh detail fetch would now detect the edit.

This is the same defect fixed for the MR Pipeline tab in PR #97; the File
Translation tab already drops its ``legacy`` cache on Refresh for the identical
go-edit-then-come-back reason. The Scan tab was missing the equivalent
invalidation on Refresh / Search / Reset. These tests pin the fix.

The tests construct ``ScanTasksTab`` via ``__new__`` (no Tk root / display
needed) and stub the widget/network touchpoints, matching the Tk-free style of
the rest of the suite.

Run:  python -m unittest test_scan_post_edit_cache_invalidation
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gui_tab_scan_tasks
import task_post_edit as tpe


class _FakeVar:
    """Minimal Tk StringVar stand-in (only ``.set`` is exercised)."""

    def __init__(self):
        self.value = "sentinel"

    def set(self, v):
        self.value = v


def _bare_tab():
    """A ``ScanTasksTab`` with no Tk widgets — just enough to drive the pure
    handler logic. ``_load_tasks`` is stubbed so calling a handler records the
    call instead of hitting the network / Tk tree."""
    tab = gui_tab_scan_tasks.ScanTasksTab.__new__(gui_tab_scan_tasks.ScanTasksTab)
    tab._loaded = 0

    def _fake_load():
        tab._loaded += 1

    tab._load_tasks = _fake_load
    tab.scan_page = 99  # search/reset reset this to 0; prove they ran
    return tab


class InvalidatePostEditCacheTests(unittest.TestCase):

    def setUp(self):
        # Each test owns the shared singleton cache; wipe before and after.
        tpe.get_cache().clear()
        self.addCleanup(tpe.get_cache().clear)

    def test_drops_only_scan_kind(self):
        c = tpe.get_cache()
        c.set("scan", "scan-task-1", False)   # the stale "no edit" answer
        c.set("scan", "scan-task-2", True)
        c.set("mr", ("common/clw", 2899), True)   # other tabs' caches survive
        c.set("legacy", "t-1", True)

        _bare_tab()._invalidate_post_edit_cache()

        self.assertIsNone(c.get("scan", "scan-task-1"))
        self.assertIsNone(c.get("scan", "scan-task-2"))
        self.assertTrue(c.get("mr", ("common/clw", 2899)))
        self.assertTrue(c.get("legacy", "t-1"))

    def test_invalidate_is_best_effort(self):
        # Cache housekeeping must never raise out of the handler. Simulate a
        # broken cache and assert the method swallows it.
        tab = _bare_tab()
        orig = tpe.get_cache
        try:
            tpe.get_cache = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
            tab._invalidate_post_edit_cache()  # must not raise
        finally:
            tpe.get_cache = orig


class RequeryInvalidatesCacheTests(unittest.TestCase):
    """The bug, reproduced at the handler level: a stale ``False`` must be
    cleared by an explicit re-query so the next render re-fetches."""

    def setUp(self):
        tpe.get_cache().clear()
        self.addCleanup(tpe.get_cache().clear)

    def test_on_search_clears_stale_false_and_reloads(self):
        c = tpe.get_cache()
        c.set("scan", "scan-task-1", False)

        tab = _bare_tab()
        tab._on_search()

        # None => _on_tasks_loaded will queue a fresh _fetch_scan -> ✏️ appears.
        self.assertIsNone(c.get("scan", "scan-task-1"))
        self.assertEqual(tab._loaded, 1)   # search still actually runs
        self.assertEqual(tab.scan_page, 0)

    def test_refresh_clears_stale_false_and_reloads(self):
        # Refresh is the canonical go-edit-then-come-back gesture.
        c = tpe.get_cache()
        c.set("scan", "scan-task-1", False)

        tab = _bare_tab()
        tab._refresh_tasks()

        self.assertIsNone(c.get("scan", "scan-task-1"))
        self.assertEqual(tab._loaded, 1)

    def test_on_reset_clears_stale_false_and_reloads(self):
        c = tpe.get_cache()
        c.set("scan", "scan-task-1", False)

        tab = _bare_tab()
        # _on_reset wipes the filter widgets before reloading — give it fakes.
        tab.scan_project_var = _FakeVar()
        tab.scan_status_var = _FakeVar()
        tab.scan_task_id_var = _FakeVar()

        tab._on_reset()

        self.assertIsNone(c.get("scan", "scan-task-1"))
        self.assertEqual(tab._loaded, 1)
        self.assertEqual(tab.scan_page, 0)
        self.assertEqual(tab.scan_project_var.value, "")  # reset really ran


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
