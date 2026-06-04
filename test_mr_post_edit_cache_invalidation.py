"""Regression tests for the MR Pipeline ✏️ (post-edit) badge staleness bug.

Root cause this guards against
------------------------------
The ✏️ badge on the MR Pipeline task list is served from a process-lifetime
cache (``task_post_edit.PostEditCache``). A Language Lead fixes a translation
in the Tranzor dashboard — which sets ``fixed_by_lead`` on the case — *after*
the list may have already cached a ``False`` "no human edit" answer for that
MR. The render only queues a re-fetch when the cached value is ``None`` (see
``MRPipelineTab._on_tasks_loaded``), so a cached ``False`` is sticky: the badge
never lights up, even though a fresh Changes export (``detect_mr_changes``,
which reads ``/dashboard/cases`` directly) correctly detects the edit. That was
the reported symptom — report shows the human post-edit, list does not.

The File Translation tab already drops its ``legacy`` cache on Refresh for the
identical go-edit-then-come-back reason; the MR tab was missing the equivalent
invalidation on Search / Reset. These tests pin the fix.

The tests construct ``MRPipelineTab`` via ``__new__`` (no Tk root / display
needed) and stub the widget/network touchpoints, matching the Tk-free style of
the rest of the suite.

Run:  python -m unittest test_mr_post_edit_cache_invalidation
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gui_tabs
import task_post_edit as tpe


class _FakeVar:
    """Minimal Tk StringVar stand-in (only ``.set`` is exercised)."""

    def __init__(self):
        self.value = "sentinel"

    def set(self, v):
        self.value = v


class _FakeEntry:
    """Minimal Tk Entry stand-in (only ``.delete`` is exercised)."""

    def __init__(self):
        self.deleted = False

    def delete(self, first, last):
        self.deleted = True


def _bare_tab():
    """An ``MRPipelineTab`` with no Tk widgets — just enough to drive the
    pure handler logic. ``_load_tasks`` is stubbed so calling a handler
    records the call instead of hitting the network / Tk tree."""
    tab = gui_tabs.MRPipelineTab.__new__(gui_tabs.MRPipelineTab)
    tab._loaded = 0

    def _fake_load():
        tab._loaded += 1

    tab._load_tasks = _fake_load
    tab.mr_page = 99  # handlers reset this to 0; prove they ran
    return tab


class InvalidatePostEditCacheTests(unittest.TestCase):

    def setUp(self):
        # Each test owns the shared singleton cache; wipe before and after.
        tpe.get_cache().clear()
        self.addCleanup(tpe.get_cache().clear)

    def test_drops_only_mr_kind(self):
        c = tpe.get_cache()
        c.set("mr", ("common/clw", 2899), False)   # the stale "no edit" answer
        c.set("mr", ("web/cic", 1111), True)
        c.set("legacy", "t-1", True)               # other tabs' caches survive
        c.set("scan", "s-1", True)

        _bare_tab()._invalidate_post_edit_cache()

        self.assertIsNone(c.get("mr", ("common/clw", 2899)))
        self.assertIsNone(c.get("mr", ("web/cic", 1111)))
        self.assertTrue(c.get("legacy", "t-1"))
        self.assertTrue(c.get("scan", "s-1"))

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


class SearchInvalidatesCacheTests(unittest.TestCase):
    """The reported bug, reproduced at the handler level: a stale ``False``
    must be cleared by an explicit re-query so the next render re-fetches."""

    def setUp(self):
        tpe.get_cache().clear()
        self.addCleanup(tpe.get_cache().clear)

    def test_on_search_clears_stale_false_and_reloads(self):
        c = tpe.get_cache()
        c.set("mr", ("common/clw", 2899), False)

        tab = _bare_tab()
        tab._on_search()

        # None => _on_tasks_loaded will queue a fresh _fetch_mr -> ✏️ appears.
        self.assertIsNone(c.get("mr", ("common/clw", 2899)))
        self.assertEqual(tab._loaded, 1)   # search still actually runs
        self.assertEqual(tab.mr_page, 0)

    def test_on_reset_clears_stale_false_and_reloads(self):
        c = tpe.get_cache()
        c.set("mr", ("common/clw", 2899), False)

        tab = _bare_tab()
        # _on_reset wipes the filter widgets before reloading — give it fakes.
        tab.mr_project_var = _FakeVar()
        tab.mr_release_var = _FakeVar()
        tab.mr_status_var = _FakeVar()
        tab.mr_iid_var = _FakeVar()
        tab.mr_task_id_var = _FakeVar()
        tab.mr_date_from = _FakeEntry()
        tab.mr_date_to = _FakeEntry()

        tab._on_reset()

        self.assertIsNone(c.get("mr", ("common/clw", 2899)))
        self.assertEqual(tab._loaded, 1)
        self.assertEqual(tab.mr_page, 0)
        self.assertEqual(tab.mr_project_var.value, "")  # reset really ran


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
