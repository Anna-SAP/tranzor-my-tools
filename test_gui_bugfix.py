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


class TestBugFixSorting(unittest.TestCase):
    def setUp(self):
        self.tab = object.__new__(gui.BugFixTab)
        self.tab._sort_column = gui.BugFixTab._DEFAULT_SORT_COLUMN
        self.tab._sort_descending = gui.BugFixTab._DEFAULT_SORT_DESCENDING
        self.tab._t = lambda key: gui.STRINGS["en"][key]
        self.tab.tree = mock.Mock()
        self.tab._apply_filters = mock.Mock()

    def test_default_sort_is_created_newest_first(self):
        self.assertEqual(gui.BugFixTab._DEFAULT_SORT_COLUMN, "created")
        self.assertTrue(gui.BugFixTab._DEFAULT_SORT_DESCENDING)
        self.assertEqual(self.tab._sort_column, "created")
        self.assertTrue(self.tab._sort_descending)
        older = {
            "submission_id": "old",
            "created_at": "2026-06-01T16:00:31Z",
            "attention": {"priority": 0, "level": "action"},
        }
        newer = {
            "submission_id": "new",
            "created_at": "2026-09-15T13:15:10Z",
            "attention": {"priority": 4, "level": "done"},
        }
        mid = {
            "submission_id": "mid",
            "created_at": "2026-07-03T10:45:37Z",
            "attention": {"priority": 0, "level": "action"},
        }
        self.assertEqual(
            [row["submission_id"]
             for row in self.tab._sort_rows([older, mid, newer])],
            ["new", "mid", "old"],
        )
        self.tab._sort_column = ""
        self.tab._sort_descending = False
        self.assertEqual(
            [row["submission_id"]
             for row in self.tab._sort_rows([older, mid, newer])],
            ["new", "mid", "old"],
        )

    def test_every_requested_header_toggles_and_marks_direction(self):
        self.tab._refresh_sort_headings()
        commands = {call.args[0]: call.kwargs["command"]
                    for call in self.tab.tree.heading.call_args_list}
        sortable = (
            "created", "mr_state", "mr", "strings", "locale", "project",
            "bug", "submitter",
        )
        n_cols = len(gui.BugFixTab._COLS)
        for column in sortable:
            with self.subTest(column=column):
                self.tab._sort_column = ""
                self.tab._sort_descending = False
                commands[column]()
                self.assertEqual(self.tab._sort_column, column)
                self.assertEqual(self.tab._sort_descending, column == "created")
                commands[column]()
                self.assertEqual(self.tab._sort_descending, column != "created")
                current = {call.args[0]: call.kwargs["text"]
                           for call in self.tab.tree.heading.call_args_list[-n_cols:]}
                arrow = " ▼" if column != "created" else " ▲"
                self.assertTrue(current[column].endswith(arrow))
                self.assertEqual(sum(text.endswith((" ▲", " ▼")) for text in current.values()), 1)
        self.assertEqual(self.tab._apply_filters.call_count, len(sortable) * 2)
        self.assertIn("submitter", gui.BugFixTab._COLS)
        self.assertEqual(
            gui.STRINGS["en"]["bf_col_submitter"], "Submitter")
        self.assertEqual(
            gui.STRINGS["zh"]["bf_col_submitter"], "提交人")

    def test_numeric_natural_and_localized_state_order(self):
        cases = [
            ("mr", {"mr_iid": 9}, {"mr_iid": 100}),
            ("strings", {"string_count": 0}, {"string_count": 12}),
            ("bug", {"bug_id": "loc-9"}, {"bug_id": "LOC-10"}),
            ("project", {"project_id": "common/uns"}, {"project_id": "Web/jedi"}),
            ("locale", {"target_languages": ["de-DE", "fr-FR"]}, {"target_languages": ["en-US"]}),
            ("mr_state", {"mr_state": "closed"}, {"mr_state": "opened"}),
            ("submitter", {"created_by": "anna.su@ringcentral.com"},
             {"created_by": "derek.yan@ringcentral.com"}),
        ]
        for column, first, last in cases:
            with self.subTest(column=column):
                self.tab._sort_column = column
                self.tab._sort_descending = False
                self.assertEqual(self.tab._sort_rows([last, first]), [first, last])
                self.tab._sort_descending = True
                self.assertEqual(self.tab._sort_rows([first, last]), [last, first])

    def test_timezones_missing_values_and_stable_ties(self):
        early = {"created_at": "2026-09-09T08:00:00+08:00"}
        late = {"created_at": "2026-09-09T01:00:00Z"}
        tie = {"created_at": "2026-09-09T01:00:00", "submission_id": "tie"}
        invalid = {"created_at": "invalid"}
        missing = {}
        self.tab._sort_column = "created"
        self.tab._sort_descending = False
        rows = [late, invalid, early, tie, missing]
        self.assertEqual(self.tab._sort_rows(rows), [early, late, tie, invalid, missing])
        self.tab._sort_descending = True
        self.assertEqual(self.tab._sort_rows(rows), [late, tie, early, invalid, missing])
        self.assertEqual(rows, [late, invalid, early, tie, missing])
        for column, populated in [("mr", {"mr_iid": 5}), ("bug", {"bug_id": "LOC-1"})]:
            self.tab._sort_column = column
            for descending in (False, True):
                self.tab._sort_descending = descending
                self.assertEqual(self.tab._sort_rows([missing, populated]), [populated, missing])

    def test_filter_render_defaults_to_newest_created_first(self):
        tab = self.tab
        del tab._apply_filters
        tab._filter_raw = _empty_filters()
        tab._all_rows = [
            {
                "submission_id": "old",
                "created_at": "2026-06-01T16:00:31Z",
                "attention": {"level": "action", "code": "workflow_failed"},
            },
            {
                "submission_id": "new",
                "created_at": "2026-09-15T13:15:10Z",
                "attention": {"level": "done", "code": "no_action"},
            },
        ]
        tab._row_by_iid = {}
        tab.var_search = _ValueVar()
        tab._selected_submission_id = lambda: ""
        tab._update_kpis = mock.Mock()
        tab._show_detail = mock.Mock()
        tab.tree.get_children.return_value = []
        tab._apply_filters()
        self.assertEqual(list(tab._row_by_iid), ["new", "old"])
        tab._refresh_sort_headings()
        headings = {call.args[0]: call.kwargs["text"]
                    for call in tab.tree.heading.call_args_list}
        self.assertTrue(headings["created"].endswith(" ▼"))

    def test_filter_render_retains_sort_and_selection(self):
        tab = self.tab
        # Exercise the actual filter/render path, used by live and comment refresh.
        del tab._apply_filters
        tab._filter_raw = _empty_filters()
        tab._all_rows = [
            {"submission_id": "A", "summary": {"total": 12}, "project_id": "web/jedi"},
            {"submission_id": "B", "summary": {"total": 2}, "project_id": "web/jedi"},
            {"submission_id": "C", "summary": {"total": 1}, "project_id": "common/uns"},
        ]
        tab._row_by_iid = {}
        tab.var_search = _ValueVar()
        tab._selected_submission_id = lambda: "A"
        tab._update_kpis = mock.Mock()
        tab._show_detail = mock.Mock()
        tab.tree.get_children.return_value = []
        tab._sort_by("strings")
        self.assertEqual(list(tab._row_by_iid), ["C", "B", "A"])
        tab.tree.selection_set.assert_called_with("A")
        tab._filter_raw["project"] = ["web/jedi"]
        tab._apply_filters()
        self.assertEqual(list(tab._row_by_iid), ["B", "A"])
        tab._all_rows[0]["summary"]["total"] = 0
        tab._apply_filters()
        self.assertEqual(list(tab._row_by_iid), ["A", "B"])
        self.assertEqual(tab._sort_column, "strings")
        tab.tree.selection_set.assert_called_with("A")

    def test_reset_restores_default_newest_created_order(self):
        tab = self.tab
        tab._sort_by("strings")
        tab.var_search = _ValueVar("LOC-9")
        tab._refresh_filter_values = mock.Mock()
        tab._reset_filters()
        self.assertEqual(tab._sort_column, "created")
        self.assertTrue(tab._sort_descending)
        self.assertEqual(tab.var_search.get(), "")
        older = {"submission_id": "old", "created_at": "2026-06-01T16:00:31Z"}
        newer = {"submission_id": "new", "created_at": "2026-09-15T13:15:10Z"}
        self.assertEqual(tab._sort_rows([older, newer]), [newer, older])

    def test_submitter_column_renders_created_by_and_placeholder(self):
        tab = self.tab
        del tab._apply_filters
        tab._filter_raw = _empty_filters()
        tab._all_rows = [
            {
                "submission_id": "named",
                "created_by": "Derek Yan",
                "attention": {"level": "done", "code": "no_action"},
            },
            {
                "submission_id": "blank",
                "attention": {"level": "done", "code": "no_action"},
            },
        ]
        tab._row_by_iid = {}
        tab.var_search = _ValueVar()
        tab._selected_submission_id = lambda: ""
        tab._update_kpis = mock.Mock()
        tab._show_detail = mock.Mock()
        tab.tree.get_children.return_value = []
        tab._apply_filters()
        inserted = {
            call.kwargs["iid"]: call.kwargs["values"]
            for call in tab.tree.insert.call_args_list
        }
        submitter_index = gui.BugFixTab._COLS.index("submitter")
        self.assertEqual(inserted["named"][submitter_index], "Derek Yan")
        self.assertEqual(inserted["blank"][submitter_index], "—")
        tab._sort_column = "submitter"
        tab._sort_descending = False
        self.assertEqual(
            [row["submission_id"] for row in tab._sort_rows(tab._all_rows)],
            ["named", "blank"],
        )

    def test_double_click_header_does_not_open_selected_mr(self):
        self.tab._open_selected_mr = mock.Mock()
        self.tab.tree.identify_region.return_value = "heading"
        self.tab._on_double_click(mock.Mock(x=1, y=1))
        self.tab._open_selected_mr.assert_not_called()
        self.tab.tree.identify_region.return_value = "cell"
        self.tab._on_double_click(mock.Mock(x=1, y=25))
        self.tab._open_selected_mr.assert_called_once()


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


class _Tip:
    def __init__(self):
        self.text = None

    def set_text(self, text):
        self.text = text


def _empty_filters():
    return {key: [] for key in gui._FILTER_KEYS}


def _filter_stub(rows=(), last_result=None, filter_raw=None, lang="en"):
    """A BugFixTab with only the multi-select filter state wired up."""
    tab = object.__new__(gui.BugFixTab)
    tab._all_rows = list(rows)
    tab._last_result = dict(last_result or {})
    tab._filter_raw = _empty_filters()
    tab._filter_raw.update(filter_raw or {})
    tab._filter_options = {key: [] for key in gui._FILTER_KEYS}
    tab._filter_tips = {key: _Tip() for key in gui._FILTER_KEYS}
    for combo_name, var_name in gui._FILTER_WIDGETS.values():
        setattr(tab, combo_name, _Combo())
        setattr(tab, var_name, _ValueVar())
    tab._t = lambda key, lang=lang: gui.STRINGS[lang][key]
    tab._schedule_filter = mock.Mock()
    return tab


def _refresh_with_current(tab):
    tab._refresh_filter_values(
        project_raw=tab._project_raw(),
        workflow_raw=tab._workflow_raw(),
        mr_raw=tab._mr_raw(),
        submitter_raw=tab._submitter_raw(),
    )


class TestBugFixTabResilience(unittest.TestCase):

    def test_direct_filter_survives_en_zh_round_trip(self):
        tab = _filter_stub(filter_raw={"mr": ["none"]})

        tab._refresh_filter_values(mr_raw=tab._mr_raw())
        self.assertEqual(tab.var_mr_state.get(), "Direct / no MR")
        self.assertEqual(tab._mr_raw(), ["none"])

        tab._t = lambda key: gui.STRINGS["zh"][key]
        _refresh_with_current(tab)
        self.assertEqual(tab.var_mr_state.get(), "直写 / 无 MR")
        self.assertEqual(tab._mr_raw(), ["none"])

        tab._t = lambda key: gui.STRINGS["en"][key]
        _refresh_with_current(tab)
        self.assertEqual(tab.var_mr_state.get(), "Direct / no MR")
        self.assertEqual(tab._mr_raw(), ["none"])

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
        tab._project_raw = lambda: []
        tab._workflow_raw = lambda: []
        tab._mr_raw = lambda: []
        tab._submitter_raw = lambda: []
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
        self.assertEqual(
            export_gui.STRINGS["en"]["bf_col_submitter"], "Submitter")
        self.assertEqual(
            export_gui.STRINGS["zh"]["bf_col_submitter"], "提交人")
        self.assertEqual(
            export_gui.STRINGS["en"]["bf_detail_created_by"], "Submitter")
        self.assertEqual(
            export_gui.STRINGS["zh"]["bf_detail_created_by"], "提交人")


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



class TestBugFixMultiSelectFilters(unittest.TestCase):
    """Project / Bug Fix status / MR status / Submitter multi-select wiring."""

    ROWS = [
        {
            "submission_id": "bui-hanny",
            "project_id": "web/bui",
            "created_by": "hanny.han@ringcentral.com",
            "platform_status": "applied",
            "mr_state": "merged",
            "attention": {"level": "done", "code": "no_action"},
        },
        {
            "submission_id": "i18n-derek",
            "project_id": "web/i18n",
            "created_by": "Derek Yan",
            "platform_status": "mr_creation_failed",
            "mr_state": "opened",
            "attention": {"level": "watch", "code": "open_mr"},
        },
        {
            "submission_id": "uns-anna",
            "project_id": "common/uns",
            "created_by": "anna.su@ringcentral.com",
            "platform_status": "applied",
            "mr_state": "none",
            "attention": {"level": "direct", "code": "direct_no_mr"},
        },
        {
            "submission_id": "bui-blank",
            "project_id": "web/bui",
            "created_by": "",
            "platform_status": "failed",
            "mr_state": "closed",
            "attention": {"level": "action", "code": "workflow_failed"},
        },
    ]

    def test_options_drop_the_all_placeholder_and_list_distinct_submitters(self):
        tab = _filter_stub(self.ROWS)
        self.assertEqual(
            tab._project_options(),
            [("common/uns", "common/uns"), ("web/bui", "web/bui"),
             ("web/i18n", "web/i18n")])
        self.assertEqual(
            tab._submitter_options(),
            [("anna.su@ringcentral.com", "anna.su@ringcentral.com"),
             ("Derek Yan", "Derek Yan"),
             ("hanny.han@ringcentral.com", "hanny.han@ringcentral.com")])
        self.assertEqual(
            [raw for raw, _label in tab._mr_options()],
            ["opened", "merged", "closed", "locked", "none", "unknown"])
        self.assertEqual(
            tab._workflow_options(),
            [("applied", "Applied"), ("failed", "Failed"),
             ("mr_creation_failed", "MR creation failed")])
        for options in (tab._project_options(), tab._submitter_options(),
                        tab._mr_options(), tab._workflow_options()):
            self.assertNotIn("", dict(options))
            self.assertNotIn("All", dict(options).values())

    def test_popup_labels_round_trip_to_raw_keys_and_summary(self):
        tab = _filter_stub(self.ROWS)
        tab._refresh_filter_values()
        self.assertEqual(
            tab._filter_labels("mr"),
            ["Open", "Merged", "Closed", "Locked", "Direct / no MR", "Unknown"])
        self.assertEqual(tab.cmb_mr_state.values, tab._filter_labels("mr"))
        for key in gui._FILTER_KEYS:
            self.assertEqual(
                getattr(tab, gui._FILTER_WIDGETS[key][1]).get(), "All")
            self.assertEqual(tab._filter_tips[key].text, "")

        tab._set_filter_selection("mr", ["Direct / no MR", "Merged"])
        self.assertEqual(tab._mr_raw(), ["merged", "none"])
        self.assertEqual(tab._selected_labels("mr"), ["Merged", "Direct / no MR"])
        self.assertEqual(tab.var_mr_state.get(), "2 selected")
        self.assertEqual(
            tab._filter_tips["mr"].text, "Merged\nDirect / no MR")
        tab._schedule_filter.assert_called_once_with()

        tab._set_filter_selection("submitter", ["Derek Yan", "nobody"])
        self.assertEqual(tab._submitter_raw(), ["Derek Yan"])
        self.assertEqual(tab.var_submitter.get(), "Derek Yan")

        tab._set_filter_selection("mr", [])
        self.assertEqual(tab._mr_raw(), [])
        self.assertEqual(tab.var_mr_state.get(), "All")
        self.assertEqual(tab._filter_tips["mr"].text, "")

    def test_language_switch_relabels_a_kept_selection(self):
        tab = _filter_stub(
            self.ROWS, filter_raw={"mr": ["merged", "none"],
                                   "workflow": ["mr_creation_failed"]})
        _refresh_with_current(tab)
        self.assertEqual(tab.var_mr_state.get(), "2 selected")
        self.assertEqual(tab.var_workflow.get(), "MR creation failed")

        tab._t = lambda key: gui.STRINGS["zh"][key]
        _refresh_with_current(tab)
        self.assertEqual(tab._mr_raw(), ["merged", "none"])
        self.assertEqual(tab.var_mr_state.get(), "已选 2 项")
        self.assertEqual(tab._selected_labels("mr"), ["已合并", "直写 / 无 MR"])
        self.assertEqual(tab.var_workflow.get(), "MR 创建失败")
        self.assertEqual(tab.var_project.get(), "全部")

        tab._set_filter_selection("mr", ["已合并"])
        self.assertEqual(tab._mr_raw(), ["merged"])
        self.assertEqual(tab.var_mr_state.get(), "已合并")

    def test_refresh_prunes_vanished_keys_and_accepts_a_single_string(self):
        tab = _filter_stub(self.ROWS)
        tab._refresh_filter_values(
            project_raw=["web/bui", "gone/project", "web/bui", ""],
            submitter_raw="Derek Yan",
            mr_raw=["unknown", "merged"],
            workflow_raw=["not_a_status"],
        )
        self.assertEqual(tab._project_raw(), ["web/bui"])
        self.assertEqual(tab._submitter_raw(), ["Derek Yan"])
        self.assertEqual(tab._mr_raw(), ["unknown", "merged"])
        self.assertEqual(tab._workflow_raw(), [])
        self.assertEqual(tab.var_project.get(), "web/bui")
        self.assertEqual(tab.var_workflow.get(), "All")

        # The only selected submitter disappears from the data: the filter
        # is dropped rather than left hiding every row.
        tab._all_rows = [row for row in self.ROWS
                         if row["created_by"] != "Derek Yan"]
        _refresh_with_current(tab)
        self.assertEqual(tab._submitter_raw(), [])
        self.assertEqual(tab.var_submitter.get(), "All")
        self.assertEqual(tab._project_raw(), ["web/bui"])

    def _render_stub(self, filter_raw=None, search=""):
        tab = _filter_stub(self.ROWS, filter_raw=filter_raw)
        tab._sort_column = gui.BugFixTab._DEFAULT_SORT_COLUMN
        tab._sort_descending = gui.BugFixTab._DEFAULT_SORT_DESCENDING
        tab._filter_after_id = None
        tab._row_by_iid = {}
        tab.tree = mock.Mock()
        tab.tree.get_children.return_value = []
        tab.var_search = _ValueVar(search)
        tab._selected_submission_id = lambda: ""
        tab._update_kpis = mock.Mock()
        tab._show_detail = mock.Mock()
        tab._set_detail = mock.Mock()
        tab._syncing = False
        tab._idle = mock.Mock()
        return tab

    def test_apply_filters_ors_inside_a_filter_and_ands_across(self):
        tab = self._render_stub(filter_raw={
            "project": ["web/bui", "common/uns"],
            "mr": ["merged", "none", "closed"],
        })
        self.assertEqual(tab._apply_filters(), 3)
        self.assertEqual(
            set(tab._row_by_iid), {"bui-hanny", "uns-anna", "bui-blank"})

        tab._filter_raw["submitter"] = [
            "hanny.han@ringcentral.com", "anna.su@ringcentral.com"]
        tab._filter_raw["workflow"] = ["applied"]
        self.assertEqual(tab._apply_filters(), 2)
        self.assertEqual(set(tab._row_by_iid), {"bui-hanny", "uns-anna"})

    def test_search_box_no_longer_matches_the_submitter(self):
        # "yan" only occurs in Derek Yan's created_by, never in an id/url.
        tab = self._render_stub(search="yan")
        self.assertEqual(tab._apply_filters(), 0)
        tab.var_search.set("i18n")
        self.assertEqual(tab._apply_filters(), 1)
        self.assertEqual(list(tab._row_by_iid), ["i18n-derek"])
        self.assertEqual(gui.STRINGS["en"]["bf_search"], "Search Bug ID, MR…")
        self.assertEqual(gui.STRINGS["zh"]["bf_search"], "搜索 Bug ID、MR…")

    def test_reset_clears_every_multi_select_and_the_search(self):
        tab = _filter_stub(self.ROWS, filter_raw={
            "project": ["web/bui"], "workflow": ["applied"],
            "mr": ["merged"], "submitter": ["Derek Yan"],
        })
        _refresh_with_current(tab)
        self.assertEqual(tab.var_submitter.get(), "Derek Yan")
        tab.var_search = _ValueVar("LOC-1")
        tab._refresh_sort_headings = mock.Mock()
        tab._apply_filters = mock.Mock()
        tab._reset_filters()
        for key in gui._FILTER_KEYS:
            self.assertEqual(tab._filter_raw[key], [])
            self.assertEqual(
                getattr(tab, gui._FILTER_WIDGETS[key][1]).get(), "All")
        self.assertEqual(tab.var_search.get(), "")
        tab._apply_filters.assert_called_once_with()

    def test_popup_close_applies_at_once_and_drops_the_debounce(self):
        tab = _filter_stub(self.ROWS)
        tab.parent = mock.Mock()
        tab._filter_after_id = "pending"
        tab._apply_filters = mock.Mock()
        tab._on_filter_change()
        tab.parent.after_cancel.assert_called_once_with("pending")
        self.assertIsNone(tab._filter_after_id)
        tab._apply_filters.assert_called_once_with()

    def test_build_filter_wires_the_multi_select_popup(self):
        import types
        tab = _filter_stub(self.ROWS)
        tab.app = types.SimpleNamespace(lang="zh")
        tab._refresh_filter_values()
        with (
            mock.patch.object(gui.ttk, "Label"),
            mock.patch.object(gui.ttk, "Combobox") as combo_cls,
            mock.patch.object(gui.tk, "StringVar", _ValueVar),
            mock.patch.object(gui, "attach_search") as attach,
        ):
            _label, variable, combo = tab._build_filter(
                mock.Mock(), "mr", width=16)
        self.assertIs(combo, combo_cls.return_value)
        # The tooltip adds its own hover bindings; the selection hook must
        # still be there.
        self.assertIn(
            mock.call("<<ComboboxSelected>>", tab._on_filter_change),
            combo.bind.call_args_list)
        self.assertIsInstance(variable, _ValueVar)
        kwargs = attach.call_args.kwargs
        self.assertIs(attach.call_args.args[0], combo)
        self.assertTrue(kwargs["multi"])
        self.assertEqual(kwargs["lang"](), "zh")
        self.assertEqual(kwargs["hint"](), "Click to toggle · empty = all")
        self.assertEqual(kwargs["get_options"](), tab._filter_labels("mr"))
        self.assertEqual(kwargs["get_selected"](), [])
        kwargs["set_selected"](["Merged", "Locked"])
        self.assertEqual(tab._mr_raw(), ["merged", "locked"])
        self.assertEqual(kwargs["get_selected"](), ["Merged", "Locked"])
        self.assertIn("mr", tab._filter_tips)


class TestBugFixFilterBarLayout(unittest.TestCase):
    """Search box + buttons share the dropdown row only while they fit."""

    def _tab(self, available, head, tail):
        tab = object.__new__(gui.BugFixTab)
        tab._filter_box = mock.Mock()
        tab._filter_box.winfo_width.return_value = available
        tab._filter_head = mock.Mock()
        tab._filter_head.winfo_reqwidth.return_value = head
        tab._filter_tail = mock.Mock()
        tab._filter_tail.winfo_reqwidth.return_value = tail
        tab._filter_row1 = mock.Mock()
        tab._filter_row2 = mock.Mock()
        tab._filter_wrapped = False
        return tab

    def test_wraps_when_too_narrow_and_unwraps_when_wide(self):
        tab = self._tab(1196, 900, 580)
        tab._sync_filter_layout()
        self.assertTrue(tab._filter_wrapped)
        tab._filter_tail.pack_forget.assert_called_once_with()
        tab._filter_row2.pack.assert_called_once_with(fill="x", pady=(6, 0))
        tab._filter_tail.pack.assert_called_once_with(
            in_=tab._filter_row2, fill="x")

        # The <Configure> storm during a resize must not repack every tick.
        tab._filter_tail.pack.reset_mock()
        tab._filter_row2.pack.reset_mock()
        tab._sync_filter_layout()
        tab._filter_tail.pack.assert_not_called()
        tab._filter_row2.pack.assert_not_called()

        tab._filter_box.winfo_width.return_value = 1806
        tab._sync_filter_layout()
        self.assertFalse(tab._filter_wrapped)
        tab._filter_row2.pack_forget.assert_called_once_with()
        tab._filter_tail.pack.assert_called_once_with(
            in_=tab._filter_row1, side="left", fill="x", expand=True)

    def test_exact_fit_stays_on_one_row(self):
        tab = self._tab(1480, 900, 580)
        tab._sync_filter_layout()
        self.assertFalse(tab._filter_wrapped)
        tab._filter_tail.pack.assert_not_called()
        tab._filter_row2.pack.assert_not_called()

    def test_unmeasured_box_and_missing_widgets_are_ignored(self):
        tab = self._tab(1, 900, 580)
        tab._sync_filter_layout()
        self.assertFalse(tab._filter_wrapped)
        tab._filter_tail.pack.assert_not_called()
        bare = object.__new__(gui.BugFixTab)
        bare._sync_filter_layout()  # no filter bar built: silently no-op


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
                    "code": "no_action",
                    "reason": "No action detected",
                }
            }),
            "无需处理",
        )


if __name__ == "__main__":
    unittest.main()
