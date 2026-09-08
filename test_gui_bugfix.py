"""Display-free lifecycle tests for the BugFix tkinter controller."""
from __future__ import annotations

import unittest
from unittest import mock

import gui_tab_bugfix as gui


class _Button:
    def __init__(self):
        self.states = []

    def configure(self, **kwargs):
        self.states.append(kwargs)


class TestBugFixTabAsyncGuards(unittest.TestCase):

    def test_first_show_does_not_read_cache_on_tk_thread(self):
        tab = object.__new__(gui.BugFixTab)
        tab._first_shown = False
        tab._cache_loading = False
        tab._stopped = False
        tab.btn_refresh = _Button()
        tab._busy = mock.Mock()
        tab._t = lambda key: key
        tab._safe_after = mock.Mock()
        tab._schedule_auto_refresh = mock.Mock()

        fake_thread = mock.Mock()
        with (
            mock.patch.object(gui.bf, "load_cache") as load_cache,
            mock.patch.object(
                gui.threading, "Thread", return_value=fake_thread
            ) as thread_cls,
        ):
            gui.BugFixTab.on_first_show(tab)

        load_cache.assert_not_called()
        fake_thread.start.assert_called_once_with()
        self.assertTrue(tab._cache_loading)
        thread_cls.assert_called_once()
        tab._schedule_auto_refresh.assert_called_once_with()

    def test_stale_comment_result_cannot_overwrite_new_sync(self):
        tab = object.__new__(gui.BugFixTab)
        tab._comment_loading = {"A"}
        tab._data_generation = 2
        tab._all_rows = [
            {"submission_id": "A", "mr_state": "merged"},
            {"submission_id": "B", "mr_state": "opened"},
        ]
        tab._apply_filters = mock.Mock()
        tab._selected_submission_id = lambda: "B"

        gui.BugFixTab._apply_comment_result(
            tab,
            "A",
            {"submission_id": "A", "mr_state": "closed"},
            generation=1,
        )

        self.assertEqual(tab._all_rows[0]["mr_state"], "merged")
        tab._apply_filters.assert_not_called()
        self.assertNotIn("A", tab._comment_loading)

    def test_comment_result_preserves_current_selection(self):
        tab = object.__new__(gui.BugFixTab)
        tab._comment_loading = {"A"}
        tab._data_generation = 3
        tab._all_rows = [
            {"submission_id": "A", "mr_state": "opened"},
            {"submission_id": "B", "mr_state": "opened"},
        ]
        tab._selected_submission_id = lambda: "B"
        tab._apply_filters = mock.Mock()

        with mock.patch.object(
            gui.bf, "stable_sort_submissions",
            side_effect=lambda rows: list(rows),
        ):
            gui.BugFixTab._apply_comment_result(
                tab,
                "A",
                {
                    "submission_id": "A",
                    "mr_state": "opened",
                    "comments_loaded": True,
                },
                generation=3,
            )

        self.assertTrue(tab._all_rows[0]["comments_loaded"])
        tab._apply_filters.assert_called_once_with(
            select_submission="B")


if __name__ == "__main__":
    unittest.main()
