"""Tests for the MR Pipeline "MR Branch" / "Trans MR Branch" filters.

Both are text filters in the MR# mould: read on Search, cleared by Reset,
applied inside the fetch loop. Unlike MR# the value they match is not in the
task payload -- it is the target branch GitLab reports for the source MR and
for the Trans MR the row shows -- so the scan streams like "Trans MR#
exists" and resolves each distinct MR once.

Run:  python -m unittest test_mr_branch_filter
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gui_tabs as gt  # noqa: E402
import mr_delivery as _delivery  # noqa: E402
import mr_jira  # noqa: E402

TASK_4003 = "d00ff2ec-5b54-467e-acb0-098c4a8607f2"


class BranchMatchTests(unittest.TestCase):

    def test_keyword_is_a_case_insensitive_substring(self):
        self.assertTrue(gt._branch_matches("26-4-2_XMN-FT5", "xmn-ft5"))
        self.assertTrue(gt._branch_matches("SPAM-auto-deploy", "spam"))
        self.assertFalse(gt._branch_matches("master", "xmn"))

    def test_every_space_separated_keyword_must_match(self):
        self.assertTrue(gt._branch_matches("26-4-2_XMN-FT5", "26-4 ft5"))
        self.assertTrue(gt._branch_matches("26-4-2_XMN-FT5", "  FT5   26-4 "))
        self.assertFalse(gt._branch_matches("26-4-2_XMN-FT5", "26-4 ft6"))

    def test_empty_query_matches_everything(self):
        self.assertTrue(gt._branch_matches("master", ""))
        self.assertTrue(gt._branch_matches("", "   "))
        self.assertTrue(gt._branch_matches(None, None))

    def test_unknown_branch_never_matches_a_query(self):
        self.assertFalse(gt._branch_matches("", "master"))
        self.assertFalse(gt._branch_matches(None, "master"))


class _GitLab:
    """Fake shared GitLab client: MR iid -> target branch, requests counted.

    Goes through the real mr_jira.fetch_jira_metadata, so caching and the
    "never cache a failure" rule behave exactly as in the app.
    """

    def __init__(self, branches, states=None):
        self.branches = dict(branches)
        self.states = dict(states or {})
        self.calls = []

    def has_token(self):
        return True

    def get_merge_request(self, project, iid, **_kw):
        self.calls.append((project, iid))
        if iid not in self.branches:
            raise RuntimeError("404 Not Found")
        return {"title": "t", "state": self.states.get(iid, "merged"),
                "target_branch": self.branches[iid]}


def _install_gitlab(test, branches, states=None):
    gitlab = _GitLab(branches, states)
    patcher = mock.patch("task_post_edit._shared_gitlab_client",
                         return_value=gitlab)
    patcher.start()
    test.addCleanup(patcher.stop)
    return gitlab


def _bare_tab():
    tab = gt.MRPipelineTab.__new__(gt.MRPipelineTab)
    tab._delivery_probe_misses = set()
    tab._delivery_probe_lock = threading.Lock()
    return tab


def _task(task_id, iid, project="common/uns", status="completed", **extra):
    return {"task_id": task_id, "project_id": project,
            "merge_request_iid": iid, "status": status, **extra}


class FilterBatchTests(unittest.TestCase):

    def setUp(self):
        mr_jira.clear_cache()
        self.addCleanup(mr_jira.clear_cache)
        self.tab = _bare_tab()
        self.gitlab = _install_gitlab(self, {
            4003: "26-4_XMN-FT5", 42373: "SPAM-auto-deploy", 3432: "master",
            4215: "26-4-2_XMN-FT5", 4216: "26-4-2_XMN-FT5",
            42382: "master"}, states={42382: "opened"})

    def test_mr_branch_keeps_only_matching_source_branches(self):
        batch = [_task("a", 4003), _task("b", 42373, "web/web"),
                 _task("c", 3432, "web/bui")]
        kept = self.tab._filter_batch_by_branch(batch, "xmn", "")
        self.assertEqual([t["task_id"] for t in kept], ["a"])
        # The MR Branch cells of the rows it keeps paint from this cache.
        self.assertEqual(mr_jira.get_cached_branch("common/uns", 4003),
                         "26-4_XMN-FT5")

    def test_each_distinct_source_mr_is_asked_about_once(self):
        batch = [_task(str(n), 42373, "web/web") for n in range(5)]
        kept = self.tab._filter_batch_by_branch(batch, "spam", "")
        self.assertEqual(len(kept), 5)
        self.assertEqual(self.gitlab.calls, [("web/web", 42373)])

    def test_unresolvable_source_mr_does_not_match_and_is_not_retried(self):
        batch = [_task("a", 6745, "RND/rcvnc"), _task("b", 6745, "RND/rcvnc")]
        self.assertEqual(
            self.tab._filter_batch_by_branch(batch, "master", ""), [])
        self.assertEqual(self.gitlab.calls, [("RND/rcvnc", 6745)])

    def test_empty_queries_leave_the_batch_alone(self):
        batch = [_task("a", 1)]
        self.assertEqual(
            self.tab._filter_batch_by_branch(batch, "", ""), batch)
        self.assertEqual(self.gitlab.calls, [])

    def test_trans_branch_follows_the_mr_the_row_shows(self):
        # The payload names the newest fix MR only; the filter completes the
        # chain with one title search and reads the current MR's branch.
        chain = [
            _delivery.DeliveryRef("common/uns", 4213, state="merged",
                                  target_branch="26-4_XMN-FT5",
                                  source_branch="tranzor/translate-4003-x"),
            _delivery.DeliveryRef("common/uns", 4237, state="merged",
                                  target_branch="26-4-2_XMN-FT5",
                                  source_branch="tranzor-mr-fix-1-a932"),
        ]
        task = _task(TASK_4003, 4003, delivery_mr_iid=4237,
                     delivery_project_id="common/uns")
        with mock.patch("mr_delivery.find_trans_mrs",
                        return_value=chain) as search:
            kept = self.tab._filter_batch_by_branch([task], "", "26-4-2")
        self.assertEqual(kept, [task])
        self.assertEqual(task["_trans_mr_branch"], "26-4-2_XMN-FT5")
        # The whole chain rides along, so the row paints it at once.
        self.assertEqual([r.iid for r in task["_trans_mrs"]], [4213, 4237])
        known = search.call_args.kwargs["known"]
        self.assertEqual([r.iid for r in known], [4237])

    def test_trans_branch_does_not_match_the_source_branch(self):
        chain = [_delivery.DeliveryRef(
            "common/uns", 4237, state="merged",
            target_branch="26-4-2_XMN-FT5", source_branch="tranzor-mr-fix-1")]
        task = _task(TASK_4003, 4003, delivery_mr_iid=4237,
                     delivery_project_id="common/uns")
        with mock.patch("mr_delivery.find_trans_mrs", return_value=chain):
            kept = self.tab._filter_batch_by_branch([task], "", "26-4_xmn")
        self.assertEqual(kept, [])

    def test_trans_branch_fetches_a_branch_the_search_did_not_carry(self):
        payload_only = [_delivery.DeliveryRef("common/uns", 4216)]
        task = _task("t", 4215, delivery_mr_iid=4216,
                     delivery_project_id="common/uns")
        with mock.patch("mr_delivery.find_trans_mrs",
                        return_value=payload_only):
            kept = self.tab._filter_batch_by_branch([task], "", "ft5")
        self.assertEqual(kept, [task])
        self.assertIn(("common/uns", 4216), self.gitlab.calls)

    def test_a_skipped_task_never_matches_and_costs_no_lookup(self):
        task = _task("s", 4003, status="skipped")
        with mock.patch("mr_delivery.find_trans_mrs") as search:
            kept = self.tab._filter_batch_by_branch([task], "", "ft5")
        self.assertEqual(kept, [])
        search.assert_not_called()
        self.assertEqual(self.gitlab.calls, [])

    def test_a_task_without_a_trans_mr_never_matches(self):
        task = _task("o", 42382, "web/web")
        with mock.patch("mr_delivery.find_trans_mrs") as search:
            kept = self.tab._filter_batch_by_branch([task], "", "master")
        self.assertEqual(kept, [])
        search.assert_not_called()  # an open source MR has no Trans MR yet

    def test_both_filters_must_match(self):
        chain = [_delivery.DeliveryRef(
            "common/uns", 4237, state="merged",
            target_branch="26-4-2_XMN-FT5", source_branch="tranzor-mr-fix-1")]
        task = _task(TASK_4003, 4003, delivery_mr_iid=4237,
                     delivery_project_id="common/uns")
        with mock.patch("mr_delivery.find_trans_mrs", return_value=chain):
            self.assertEqual(self.tab._filter_batch_by_branch(
                [dict(task)], "26-4_xmn", "26-4-2"), [dict(task, **{
                    "_trans_mrs": chain, "_has_delivery_mr": True,
                    "_trans_mr_branch": "26-4-2_XMN-FT5"})])
            self.assertEqual(self.tab._filter_batch_by_branch(
                [dict(task)], "26-4-2", "26-4-2"), [])

    def test_trans_mr_filter_reuses_the_chain_already_resolved(self):
        chain = [_delivery.DeliveryRef("common/uns", 4237, state="merged")]
        task = _task("t", 4003, _trans_mrs=chain, _has_delivery_mr=True)
        with mock.patch.object(type(self.tab), "_resolve_task_delivery_mr",
                               create=True) as resolve:
            self.tab._check_task_delivery_mr(task)
        resolve.assert_not_called()
        self.assertTrue(task["_has_delivery_mr"])


class FindTransMrsKnownTests(unittest.TestCase):

    class _Client:
        def __init__(self, mrs, fail=False):
            self.mrs, self.fail = mrs, fail

        def has_token(self):
            return True

        def list_merge_requests(self, search, **kwargs):
            if self.fail:
                raise RuntimeError("network")
            return self.mrs

    def test_known_payload_mr_is_completed_by_the_search(self):
        payload = _delivery.DeliveryRef("common/uns", 4237)
        mrs = [
            {"iid": 4237, "state": "merged", "created_at": "2026-09-24T06:15Z",
             "title": "fix(LOC-1) for MR!4003",
             "source_branch": "tranzor-mr-fix-1-a932",
             "target_branch": "26-4-2_XMN-FT5",
             "references": {"full": "common/uns!4237"}},
            {"iid": 4213, "state": "merged", "created_at": "2026-09-20T08:03Z",
             "title": "[Tranzor] Translations for MR!4003",
             "source_branch": "tranzor/translate-4003-7729-28ad-71c8ec67",
             "target_branch": "26-4-2_XMN-FT5",
             "references": {"full": "common/uns!4213"}},
        ]
        chain = _delivery.find_trans_mrs(
            "common/uns", 4003, task_id=TASK_4003, known=[payload],
            client=self._Client(mrs))
        self.assertEqual([r.iid for r in chain], [4213, 4237])
        self.assertEqual(chain[-1].target_branch, "26-4-2_XMN-FT5")

    def test_a_failed_search_keeps_what_was_known(self):
        payload = _delivery.DeliveryRef("common/uns", 4237)
        chain = _delivery.find_trans_mrs(
            "common/uns", 4003, known=[payload],
            client=self._Client([], fail=True))
        self.assertEqual(chain, [payload])


# ---------------------------------------------------------------- fetch loop


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Parent:
    """Records every after() call instead of running it."""

    def __init__(self):
        self.calls = []

    def after(self, _ms, fn=None, *args):
        self.calls.append((getattr(fn, "__name__", repr(fn)), args))

    def named(self, name):
        return [args for fn, args in self.calls if fn == name]


def _fetch_tab(**values):
    import export_gui
    tab = _bare_tab()
    tab.parent = _Parent()
    tab.app = SimpleNamespace(
        _t=lambda key: export_gui.STRINGS["en"].get(key, key))
    tab.base_url = gt.mr_api.TRANZOR_URL
    tab._fetch_generation = 1
    tab._mr_selected_projects = []
    defaults = {
        "mr_release_var": "", "mr_status_var": "", "mr_iid_var": "",
        "mr_task_id_var": "", "mr_jira_var": "", "mr_hide_empty_var": False,
        "mr_trans_mr_open_var": False, "mr_trans_mr_only_var": False,
        "mr_branch_var": "", "mr_trans_branch_var": "",
    }
    defaults.update(values)
    for name, value in defaults.items():
        setattr(tab, name, _Var(value))
    tab._pending_append = False
    tab.mr_page = 0
    tab.mr_extra_pages = 0
    tab.mr_page_size = 25
    tab._scan_cancel = None
    tab._scan_cursor = None
    return tab


BRANCHES = {4003: "26-4_XMN-FT5", 42373: "SPAM-auto-deploy", 3432: "master",
            4043: "26-4-2_XMN-FT5"}


class FetchLoopTests(unittest.TestCase):

    def setUp(self):
        mr_jira.clear_cache()
        self.addCleanup(mr_jira.clear_cache)
        self.gitlab = _install_gitlab(self, BRANCHES)
        self.tasks = [
            _task("a", 4003), _task("b", 42373, "web/web"),
            _task("c", 3432, "web/bui"), _task("d", 4043),
            _task("e", 4003, status="skipped"),
        ]

    def _run(self, tab, tasks=None):
        pages = list(tasks if tasks is not None else self.tasks)

        def _fetch(release=None, status=None, limit=100, offset=0, **kw):
            return len(pages), pages[offset:offset + limit]

        with mock.patch.object(gt.mr_api, "fetch_mr_tasks",
                               side_effect=_fetch):
            tab._fetch_tasks()
        return tab.parent

    def _streamed_ids(self, parent):
        return [t["task_id"] for args in parent.named("_on_tasks_loaded")
                for t in args[1]]

    def test_mr_branch_filter_streams_only_matching_rows(self):
        tab = _fetch_tab(mr_branch_var="xmn")
        parent = self._run(tab)
        self.assertEqual(self._streamed_ids(parent), ["a", "d", "e"])
        self.assertIsInstance(tab._scan_cancel, threading.Event)
        self.assertEqual(tab._scan_msg_prefix, "mr_filter_scan")
        done = parent.named("_on_scan_done")
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0][3:5], (5, 3))  # scanned, matched

    def test_hide_empty_drops_skipped_tasks_before_any_lookup(self):
        tab = _fetch_tab(mr_branch_var="xmn", mr_hide_empty_var=True)
        tasks = [_task("a", 4003), _task("s", 4999, status="skipped")]
        with mock.patch.object(type(tab), "_check_task_translations",
                               lambda self, t: t.__setitem__(
                                   "_translations_count", 3)):
            parent = self._run(tab, tasks)
        self.assertEqual(self._streamed_ids(parent), ["a"])
        # The skipped task's source MR was never looked up.
        self.assertEqual(self.gitlab.calls, [("common/uns", 4003)])

    def test_filters_combine_with_mr_number(self):
        tab = _fetch_tab(mr_branch_var="xmn", mr_iid_var="4043")
        with mock.patch("mr_delivery.expand_mr_iid_filter",
                        return_value={4043}):
            parent = self._run(tab)
        self.assertEqual(self._streamed_ids(parent), ["d"])

    def test_no_gitlab_token_explains_itself(self):
        tab = _fetch_tab(mr_trans_branch_var="ft5")
        with mock.patch("mr_jira.can_fetch", return_value=False):
            parent = self._run(tab)
        errors = parent.named("_on_tasks_error")
        self.assertEqual(len(errors), 1)
        self.assertIn("GitLab token", errors[0][0])
        self.assertIsNone(tab._scan_cancel)

    def test_task_id_lookup_does_not_leave_a_scan_running(self):
        # Task ID answers with one lookup. Starting a streaming scan first
        # left Search stuck on "Stop" and the table on "Loading…".
        for values in ({"mr_branch_var": "xmn"},
                       {"mr_trans_mr_only_var": True}):
            with self.subTest(**values):
                tab = _fetch_tab(mr_task_id_var=TASK_4003, **values)
                detail = _task(TASK_4003, 4003, delivery_mr_iid=4237,
                               delivery_project_id="common/uns")
                with mock.patch.object(gt.mr_api, "fetch_mr_task_detail",
                                       return_value=detail):
                    tab._fetch_tasks()
                self.assertIsNone(tab._scan_cancel)
                self.assertEqual(tab.parent.named("_set_scan_button"), [])
                loaded = tab.parent.named("_on_tasks_loaded")
                self.assertEqual(len(loaded), 1)
                self.assertEqual([t["task_id"] for t in loaded[0][1]],
                                 [TASK_4003])

    def test_task_id_lookup_applies_the_branch_filter(self):
        tab = _fetch_tab(mr_task_id_var=TASK_4003, mr_branch_var="master")
        with mock.patch.object(gt.mr_api, "fetch_mr_task_detail",
                               return_value=_task(TASK_4003, 4003)):
            tab._fetch_tasks()
        self.assertEqual(tab.parent.named("_on_tasks_loaded")[0][1], [])

    def test_load_more_resumes_only_the_same_question(self):
        tab = _fetch_tab(mr_branch_var="xmn")
        self._run(tab)
        cursor = tab._scan_cursor
        self.assertIsNotNone(cursor)
        cursor["carry"] = [_task("carried", 4003)]

        # Same filters: the carried match is shown first.
        tab.parent = _Parent()
        tab._scan_cursor = dict(cursor)
        tab._pending_append = True
        parent = self._run(tab)
        self.assertEqual(self._streamed_ids(parent)[:1], ["carried"])

        # Branch edited without a new Search: the old matches don't answer it.
        tab.parent = _Parent()
        tab._scan_cursor = dict(cursor)
        tab._pending_append = True
        tab.mr_branch_var.set("spam")
        parent = self._run(tab)
        self.assertNotIn("carried", self._streamed_ids(parent))

    def test_trans_mr_scan_keeps_its_own_wording(self):
        tab = _fetch_tab(mr_trans_mr_only_var=True)
        with mock.patch("mr_delivery.find_trans_mrs", return_value=[]):
            self._run(tab)
        self.assertEqual(tab._scan_msg_prefix, "mr_scan")
        self.assertEqual(tab._scan_msg_key("done"), "mr_scan_done")


class StringTests(unittest.TestCase):

    def test_keys_present_and_formattable_in_both_languages(self):
        import export_gui
        for lang in ("en", "zh"):
            table = export_gui.STRINGS[lang]
            for key in ("mr_branch_filter_tip", "mr_trans_branch_filter_tip",
                        "mr_branch_token_required"):
                self.assertTrue(table.get(key), f"{lang}/{key}")
            self.assertIn("7", table["mr_filter_scan_progress"].format(
                scanned=100, total=69099, matched=7))
            for key in ("mr_filter_scan_done", "mr_filter_scan_stopped"):
                self.assertIn("7", table[key].format(scanned=900, matched=7))


if __name__ == "__main__":
    unittest.main()
