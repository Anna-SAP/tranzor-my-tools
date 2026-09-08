"""Display-free lifecycle tests for the BugFix tkinter controller."""
from __future__ import annotations

import threading
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

    def test_select_does_not_start_comments_during_full_sync(self):
        tab = object.__new__(gui.BugFixTab)
        tab._syncing = True
        tab._cache_loading = False
        tab._comment_loading = set()
        tab._comment_attempted = set()
        tab.btn_open_mr = _Button()
        tab._selected_row = lambda: {
            "submission_id": "A",
            "has_mr": True,
            "mr_url": "https://git/mr/1",
            "comments_loaded": False,
        }
        tab._show_detail = mock.Mock()
        tab._load_selected_comments = mock.Mock()

        gui.BugFixTab._on_select(tab)

        tab._load_selected_comments.assert_not_called()

    def test_comment_error_wins_over_loaded_empty_state(self):
        tab = object.__new__(gui.BugFixTab)
        captured = []
        tab._set_detail = captured.append
        tab._t = lambda key: {
            "bf_no_mr_explain": "no mr",
            "bf_comments_title": "comments",
            "bf_comments_loading": "loading",
            "bf_comments_none": "no comments",
            "bf_comments_error": "Comments unavailable: {error}",
            "bf_records_title": "records",
        }.get(key, key)

        gui.BugFixTab._show_detail(tab, {
            "submission_id": "A",
            "has_mr": True,
            "mr_iid": 1,
            "mr_state_label": "Open",
            "platform_status_label": "Applied",
            "comments_loaded": True,
            "comments": [],
            "comments_error": "403 denied",
            "records": [],
        })

        self.assertIn("Comments unavailable: 403 denied", captured[0])
        self.assertNotIn("no comments", captured[0])

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



class _ValueVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Combo:
    def __init__(self):
        self.values = []

    def configure(self, **kwargs):
        if "values" in kwargs:
            self.values = list(kwargs["values"])


class TestBugFixTabResilience(unittest.TestCase):

    def test_direct_filter_survives_en_zh_round_trip(self):
        tab = object.__new__(gui.BugFixTab)
        tab._all_rows = []
        tab._last_result = {}
        tab._filter_raw = {"project": "", "workflow": "", "mr": "none"}
        tab.var_project = _ValueVar()
        tab.var_workflow = _ValueVar()
        tab.var_mr_state = _ValueVar()
        tab.cmb_project = _Combo()
        tab.cmb_workflow = _Combo()
        tab.cmb_mr_state = _Combo()

        tab._t = lambda key: gui.STRINGS["en"][key]
        tab._refresh_filter_values(
            project_raw="", workflow_raw="", mr_raw=tab._mr_raw())
        self.assertEqual(tab.var_mr_state.get(), "Direct / no MR")
        self.assertEqual(tab._mr_raw(), "none")

        tab._t = lambda key: gui.STRINGS["zh"][key]
        tab._refresh_filter_values(
            project_raw=tab._project_raw(),
            workflow_raw=tab._workflow_raw(),
            mr_raw=tab._mr_raw(),
        )
        self.assertEqual(tab.var_mr_state.get(), "直写 / 无 MR")
        self.assertEqual(tab._mr_raw(), "none")

        tab._t = lambda key: gui.STRINGS["en"][key]
        tab._refresh_filter_values(
            project_raw=tab._project_raw(),
            workflow_raw=tab._workflow_raw(),
            mr_raw=tab._mr_raw(),
        )
        self.assertEqual(tab.var_mr_state.get(), "Direct / no MR")
        self.assertEqual(tab._mr_raw(), "none")

    def test_failed_refresh_keeps_existing_in_memory_snapshot(self):
        tab = object.__new__(gui.BugFixTab)
        existing = [{"submission_id": "keep-me", "platform_status": "applied"}]
        tab._stopped = False
        tab._syncing = True
        tab.btn_refresh = _Button()
        tab._all_rows = existing
        tab._last_result = {
            "submissions": existing,
            "total_submissions": 1,
            "available_statuses": ["Applied"],
        }
        tab._project_raw = lambda: ""
        tab._workflow_raw = lambda: ""
        tab._mr_raw = lambda: ""
        tab._refresh_filter_values = mock.Mock()
        tab._apply_filters = mock.Mock(return_value=1)
        messages = []
        tab._idle = messages.append
        tab._t = lambda key: {
            "bf_failed_retained": (
                "Refresh failed; showing the existing snapshot: {error}"
            ),
            "bf_unknown": "Unknown",
        }.get(key, key)

        tab._apply_sync_result({
            "ok": False,
            "live_ok": False,
            "source": "none",
            "submissions": [],
            "total_submissions": 0,
            "available_statuses": [],
            "error": "offline",
        })

        self.assertIs(tab._all_rows, existing)
        self.assertEqual(tab._last_result["total_submissions"], 1)
        self.assertEqual(tab._last_result["available_statuses"], ["Applied"])
        self.assertIn("existing snapshot", messages[-1])
        self.assertIn("offline", messages[-1])

    def test_every_live_refresh_reloads_gitlab_configuration(self):
        tab = object.__new__(gui.BugFixTab)
        tab._syncing = False
        tab._cache_loading = False
        tab._stopped = False
        tab._data_generation = 0
        tab._comment_loading = set()
        tab._comment_attempted = set()
        tab._cancel_event = threading.Event()
        tab.btn_refresh = _Button()
        tab._busy = mock.Mock()
        tab._t = lambda key: key
        tab._base_url = lambda: "http://platform"

        def finish(_result, **_kwargs):
            tab._syncing = False

        tab._apply_sync_result = mock.Mock(side_effect=finish)
        tab._safe_after = lambda callback: callback()
        clients = [object(), object()]

        class ImmediateThread:
            def __init__(self, *, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        with (
            mock.patch.object(
                gui.gitlab_client, "GitLabClient",
                side_effect=clients,
            ) as client_cls,
            mock.patch.object(
                gui.bf, "sync_panel",
                return_value={"source": "live", "live_ok": True},
            ) as sync_panel,
            mock.patch.object(gui.threading, "Thread", ImmediateThread),
        ):
            tab.refresh_live()
            tab.refresh_live()

        self.assertEqual(
            client_cls.call_args_list,
            [
                mock.call(timeout=gui._GITLAB_TIMEOUT_SECONDS),
                mock.call(timeout=gui._GITLAB_TIMEOUT_SECONDS),
            ],
        )
        self.assertIs(
            sync_panel.call_args_list[0].kwargs["gitlab_client"],
            clients[0],
        )
        self.assertIs(
            sync_panel.call_args_list[1].kwargs["gitlab_client"],
            clients[1],
        )
        self.assertIs(
            sync_panel.call_args_list[1].kwargs["cancel_event"],
            tab._cancel_event,
        )

    def test_stop_sets_cancel_and_stops_comment_runner(self):
        tab = object.__new__(gui.BugFixTab)
        tab._stopped = False
        tab._cancel_event = mock.Mock()
        tab._comment_runner = mock.Mock()
        tab._comment_runner.stop.return_value = "queued"
        tab._comment_loading = {"queued"}
        tab._comment_attempted = {"queued"}
        tab._auto_after_id = "auto"
        tab._filter_after_id = "filter"
        tab.parent = mock.Mock()

        tab.stop()

        self.assertTrue(tab._stopped)
        tab._cancel_event.set.assert_called_once_with()
        tab._comment_runner.stop.assert_called_once_with()
        self.assertNotIn("queued", tab._comment_loading)
        self.assertNotIn("queued", tab._comment_attempted)
        self.assertEqual(
            tab.parent.after_cancel.call_args_list,
            [mock.call("auto"), mock.call("filter")],
        )

    def test_chinese_status_and_attention_values_are_localized(self):
        tab = object.__new__(gui.BugFixTab)
        tab._t = lambda key: gui.STRINGS["zh"][key]
        row = {
            "platform_status": "partially_applied",
            "platform_status_label": "Partially applied",
            "mr_state": "merged",
            "attention": {
                "code": "workflow_failed",
                "reason": "Bug Fix workflow failed",
            },
        }

        self.assertEqual(tab._platform_status_text(row), "部分应用")
        self.assertEqual(tab._mr_state_text(row), "已合并")
        self.assertEqual(tab._attention_text(row), "Bug Fix 工作流失败")

    def test_bugfix_strings_register_when_module_is_imported_first(self):
        import export_gui
        self.assertEqual(
            export_gui.STRINGS["zh"]["tab_bugfix"], "🐞 BugFix 面板")
        self.assertIn("bf_hint", export_gui.STRINGS["en"])


class TestLatestTaskRunner(unittest.TestCase):

    def test_workers_are_daemon_bounded_and_latest_pending_wins(self):
        cancel = threading.Event()
        runner = gui._LatestTaskRunner(
            max_workers=2, cancel_event=cancel)
        release = threading.Event()
        started = []
        started_events = {
            key: threading.Event() for key in ("A", "B", "C", "D")}
        lock = threading.Lock()

        def callback(key):
            def run():
                with lock:
                    started.append(key)
                started_events[key].set()
                release.wait(2)
            return run

        try:
            self.assertEqual(runner.submit_latest("A", callback("A")), "")
            self.assertTrue(started_events["A"].wait(1))
            self.assertEqual(runner.submit_latest("B", callback("B")), "")
            self.assertTrue(started_events["B"].wait(1))
            self.assertEqual(runner.submit_latest("C", callback("C")), "")
            self.assertEqual(runner.submit_latest("D", callback("D")), "C")

            self.assertTrue(all(thread.daemon for thread in runner._threads))
            self.assertEqual(len(runner._threads), 2)
            self.assertFalse(started_events["C"].is_set())

            release.set()
            self.assertTrue(started_events["D"].wait(1))
            self.assertNotIn("C", started)
        finally:
            cancel.set()
            release.set()
            runner.stop()



class TestBugFixFinalGuards(unittest.TestCase):

    def test_constructor_failure_does_not_start_comment_workers(self):
        with (
            mock.patch.object(
                gui.BugFixTab, "_build",
                side_effect=RuntimeError("build failed"),
            ),
            mock.patch.object(gui, "_LatestTaskRunner") as runner,
        ):
            with self.assertRaisesRegex(RuntimeError, "build failed"):
                gui.BugFixTab(None, None)

        runner.assert_not_called()

    def test_stopped_tab_ignores_already_queued_comment_callback(self):
        tab = object.__new__(gui.BugFixTab)
        tab._stopped = True
        tab._comment_loading = {"A"}
        tab._data_generation = 1
        original = [{"submission_id": "A", "mr_state": "opened"}]
        tab._all_rows = original
        tab._apply_filters = mock.Mock()

        tab._apply_comment_result(
            "A",
            {"submission_id": "A", "mr_state": "merged"},
            generation=1,
        )

        self.assertIs(tab._all_rows, original)
        self.assertEqual(tab._all_rows[0]["mr_state"], "opened")
        tab._apply_filters.assert_not_called()

    def test_chinese_workflow_options_use_localized_status_labels(self):
        tab = object.__new__(gui.BugFixTab)
        tab._t = lambda key: gui.STRINGS["zh"][key]
        tab._all_rows = [
            {"platform_status": "all_applying"},
            {"platform_status": "partially_applied"},
        ]
        tab._last_result = {
            "available_statuses": [
                "All Applying", "Partially Applied", "Not Merged"
            ]
        }

        options = dict(tab._workflow_options())

        self.assertEqual(options["all_applying"], "应用中")
        self.assertEqual(options["partially_applied"], "部分应用")
        self.assertEqual(options["not_merged"], "未合并")

    def test_chinese_comment_reason_codes_render_localized_badges(self):
        tab = object.__new__(gui.BugFixTab)
        tab._t = lambda key: gui.STRINGS["zh"][key]
        captured = []
        tab._set_detail = captured.append
        tab._show_detail({
            "submission_id": "A",
            "has_mr": True,
            "mr_iid": 1,
            "mr_state": "opened",
            "platform_status": "applied",
            "attention": {
                "code": "open_mr",
                "reason": "Open MR",
            },
            "comments_loaded": True,
            "comments": [
                {
                    "author": "Reviewer",
                    "reason": "action requested",
                    "body": "Please fix",
                },
                {
                    "author": "Lead",
                    "reason": "recent human comment",
                    "body": "FYI",
                },
            ],
            "records": [],
        })

        self.assertIn("[需处理]", captured[0])
        self.assertIn("[近期评论]", captured[0])



class TestLockedMrLocalization(unittest.TestCase):

    def test_locked_state_is_available_in_chinese_filter_and_detail(self):
        tab = object.__new__(gui.BugFixTab)
        tab._t = lambda key: gui.STRINGS["zh"][key]

        self.assertEqual(dict(tab._mr_options())["locked"], "已锁定")
        self.assertEqual(
            tab._mr_state_text({"mr_state": "locked"}), "已锁定")
        self.assertEqual(
            tab._attention_text({
                "attention": {
                    "code": "locked_mr",
                    "reason": "Locked MR",
                }
            }),
            "MR 已锁定",
        )


if __name__ == "__main__":
    unittest.main()
