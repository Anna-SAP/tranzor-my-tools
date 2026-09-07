"""MR Pipeline counterpart of ``test_scan_src_count_cache``: the en-US
source-string cache must only hold *final* answers.

``MRPipelineTab._resolve_src_count(task_id, status)``:

  - a finished task's count (completed / failed / cancelled) is cached;
  - a running task's count is returned for display but NOT cached — its
    ``/results`` payload is a partial snapshot;
  - ``None`` (fetch failed) is never cached; the cell shows "—" and the
    next render retries.

Tk-free: the tab is built via ``__new__`` with only the fields the helper
touches.

Run:  python -m unittest test_mr_src_count_cache
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_mr_pipeline as mr_api
import gui_tabs


class _FakeTree:
    def __init__(self):
        self.cells = {}

    def set(self, iid, column, value):
        self.cells[(iid, column)] = value


def _bare_tab():
    tab = gui_tabs.MRPipelineTab.__new__(gui_tabs.MRPipelineTab)
    tab._src_count_cache = {}
    tab._src_count_lock = threading.Lock()
    tab._mr_row_iid_by_task = {}
    tab.mr_tree = _FakeTree()
    tab._api_kw = lambda: {}
    return tab


class ResolveSrcCountTests(unittest.TestCase):

    def test_completed_task_count_is_cached(self):
        tab = _bare_tab()
        with mock.patch.object(
            mr_api, "count_mr_source_strings_or_none", return_value=130,
        ) as fetch:
            self.assertEqual(tab._resolve_src_count("t1", "completed"), 130)
        fetch.assert_called_once_with("t1")
        self.assertEqual(tab._src_count_cache["t1"], 130)

    def test_running_task_count_shown_but_not_cached(self):
        tab = _bare_tab()
        with mock.patch.object(
            mr_api, "count_mr_source_strings_or_none", return_value=12,
        ):
            self.assertEqual(tab._resolve_src_count("t1", "running"), 12)
        self.assertNotIn("t1", tab._src_count_cache)
        # Next render (task now completed, more rows) re-fetches.
        with mock.patch.object(
            mr_api, "count_mr_source_strings_or_none", return_value=130,
        ):
            self.assertEqual(tab._resolve_src_count("t1", "completed"), 130)
        self.assertEqual(tab._src_count_cache["t1"], 130)

    def test_unknown_status_is_never_cached(self):
        tab = _bare_tab()
        with mock.patch.object(
            mr_api, "count_mr_source_strings_or_none", return_value=5,
        ):
            self.assertEqual(tab._resolve_src_count("t1", None), 5)
        self.assertEqual(tab._src_count_cache, {})

    def test_failed_fetch_is_none_uncached_and_renders_dash(self):
        tab = _bare_tab()
        tab._mr_row_iid_by_task["t1"] = "t1"
        with mock.patch.object(
            mr_api, "count_mr_source_strings_or_none", return_value=None,
        ):
            count = tab._resolve_src_count("t1", "completed")
        self.assertIsNone(count)
        self.assertEqual(tab._src_count_cache, {})
        tab._apply_src_count("t1", count)
        self.assertEqual(tab.mr_tree.cells[("t1", "src_strings")], "—")
        # "—" is already ranked as a missing cell by the column sort key.
        key = gui_tabs.MRPipelineTab._mr_sort_key
        self.assertEqual(key("—", True, False)[0], True)

    def test_cached_answer_skips_network(self):
        tab = _bare_tab()
        tab._src_count_cache["t1"] = 9
        with mock.patch.object(
            mr_api, "count_mr_source_strings_or_none",
            side_effect=AssertionError("must not re-fetch"),
        ):
            self.assertEqual(tab._resolve_src_count("t1", "running"), 9)


class HideEmptySeedTests(unittest.TestCase):
    """``_check_task_translations`` (Hide-empty path) seeds the same cache
    from the results payload it already holds — only for finished tasks."""

    def _run(self, status, rows):
        tab = _bare_tab()
        t = {"task_id": "t1", "status": status}
        with mock.patch.object(
            mr_api, "fetch_mr_results", return_value={"translations": rows},
        ):
            tab._check_task_translations(t)
        return tab, t

    def test_completed_seeds_cache(self):
        tab, t = self._run("completed", [{"opus_id": "a"}, {"opus_id": "b"}])
        self.assertEqual(t["_src_string_count"], 2)
        self.assertEqual(tab._src_count_cache["t1"], 2)

    def test_running_annotates_but_does_not_seed(self):
        tab, t = self._run("running", [{"opus_id": "a"}])
        self.assertEqual(t["_src_string_count"], 1)
        self.assertEqual(tab._src_count_cache, {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
