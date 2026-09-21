"""Tests for the MR Pipeline "🔀 Trans MR# exists" filter.

The filter keeps only tasks whose translation was delivered as a follow-up
MR. Unlike the ✏️ view filter it runs inside the fetch loop, so the unit
under test is _check_task_delivery_mr: given a task payload, does it resolve
a Trans MR the same way the table's Trans MR# column does?

Run:  python -m unittest test_mr_trans_mr_filter
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mr_delivery as _delivery  # noqa: E402


class TestTransMrFilterStrings(unittest.TestCase):
    def setUp(self):
        import export_gui
        self.STRINGS = export_gui.STRINGS

    def test_keys_present_in_both_languages(self):
        for lang in ("en", "zh"):
            for key in ("mr_trans_mr_only", "mr_trans_mr_only_tip",
                        "mr_trans_mr_token_required"):
                self.assertIn(key, self.STRINGS[lang], f"{lang}/{key}")

    def test_label_is_distinct_from_the_post_edit_filter(self):
        for lang in ("en", "zh"):
            self.assertNotEqual(self.STRINGS[lang]["mr_trans_mr_only"],
                                self.STRINGS[lang]["mr_post_edit_only"])


class _Tab:
    """MRPipelineTab with only what _check_task_delivery_mr touches."""

    def __new__(cls):
        from gui_tabs import MRPipelineTab
        return MRPipelineTab.__new__(MRPipelineTab)


class TestCheckTaskDeliveryMr(unittest.TestCase):

    def setUp(self):
        self.tab = _Tab()

    def test_payload_delivery_mr_needs_no_gitlab_call(self):
        task = {
            "task_id": "t1", "project_id": "common/uns",
            "merge_request_iid": 4215, "delivery_mr_iid": 4216,
        }
        with mock.patch("mr_jira.fetch_jira_metadata") as meta, \
                mock.patch("mr_delivery.find_follow_up_mrs") as search:
            self.tab._check_task_delivery_mr(task)

        self.assertTrue(task["_has_delivery_mr"])
        self.assertEqual(task["_delivery_ref"].iid, 4216)
        meta.assert_not_called()
        search.assert_not_called()

    def test_unmerged_source_mr_is_a_free_no(self):
        # IMPORT only opens a follow-up MR once the source MR merged, so an
        # open source must not cost a title search.
        import mr_jira
        task = {"task_id": "t1", "project_id": "web/web",
                "merge_request_iid": 42382}
        with mock.patch("mr_jira.fetch_jira_metadata",
                        return_value=mr_jira.JiraMetadata("", "", "opened")), \
                mock.patch("mr_delivery.find_follow_up_mrs") as search:
            self.tab._check_task_delivery_mr(task)

        self.assertFalse(task["_has_delivery_mr"])
        self.assertIsNone(task["_delivery_ref"])
        search.assert_not_called()

    def test_merged_source_resolves_through_the_title_search(self):
        import mr_jira
        task = {"task_id": "t1", "project_id": "web/web",
                "merge_request_iid": 42391}
        ref = _delivery.DeliveryRef(project_id="web/web", iid=42392,
                                    state="opened")
        with mock.patch("mr_jira.fetch_jira_metadata",
                        return_value=mr_jira.JiraMetadata("", "", "merged")), \
                mock.patch("mr_delivery.find_follow_up_mrs",
                           return_value=(ref, None)):
            self.tab._check_task_delivery_mr(task)

        self.assertTrue(task["_has_delivery_mr"])
        self.assertEqual(task["_delivery_ref"].iid, 42392)

    def test_merged_source_with_no_follow_up_mr_is_filtered_out(self):
        import mr_jira
        task = {"task_id": "t1", "project_id": "iva/iva-ui",
                "merge_request_iid": 2515}
        with mock.patch("mr_jira.fetch_jira_metadata",
                        return_value=mr_jira.JiraMetadata("", "", "merged")), \
                mock.patch("mr_delivery.find_follow_up_mrs",
                           return_value=(None, None)):
            self.tab._check_task_delivery_mr(task)

        self.assertFalse(task["_has_delivery_mr"])

    def test_a_fix_mr_alone_still_counts_as_a_trans_mr(self):
        # format_trans_mr_cell renders a lone fix MR, so the filter must
        # agree with what the column shows.
        import mr_jira
        fix = _delivery.DeliveryRef(project_id="common/uns", iid=4214,
                                    state="merged")
        task = {"task_id": "t1", "project_id": "common/uns",
                "merge_request_iid": 4003}
        with mock.patch("mr_jira.fetch_jira_metadata",
                        return_value=mr_jira.JiraMetadata("", "", "merged")), \
                mock.patch("mr_delivery.find_follow_up_mrs",
                           return_value=(None, fix)):
            self.tab._check_task_delivery_mr(task)

        self.assertTrue(task["_has_delivery_mr"])
        self.assertEqual(task["_delivery_ref"].iid, 4214)

    def test_gitlab_failure_degrades_to_no_rather_than_raising(self):
        task = {"task_id": "t1", "project_id": "common/uns",
                "merge_request_iid": 4215}
        with mock.patch("mr_jira.fetch_jira_metadata",
                        side_effect=RuntimeError("boom")):
            self.tab._check_task_delivery_mr(task)

        self.assertFalse(task["_has_delivery_mr"])

    def test_task_without_project_or_source_iid_is_skipped(self):
        task = {"task_id": "t1", "project_id": "", "merge_request_iid": None}
        with mock.patch("mr_delivery.find_follow_up_mrs") as search:
            self.tab._check_task_delivery_mr(task)

        self.assertFalse(task["_has_delivery_mr"])
        search.assert_not_called()


class TestScanStrings(unittest.TestCase):
    def setUp(self):
        import export_gui
        self.STRINGS = export_gui.STRINGS

    def test_scan_keys_present_and_format(self):
        for lang in ("en", "zh"):
            table = self.STRINGS[lang]
            self.assertIn("mr_stop_scan", table, lang)
            for key in ("mr_scan_progress", "mr_scan_done", "mr_scan_stopped"):
                self.assertIn(key, table, f"{lang}/{key}")
            out = table["mr_scan_progress"].format(
                scanned=300, total=67436, matched=4)
            self.assertIn("300", out)
            self.assertIn("4", out)
            for key in ("mr_scan_done", "mr_scan_stopped"):
                self.assertIn("7", table[key].format(scanned=900, matched=7))


class TestProbeMemo(unittest.TestCase):
    """A source MR GitLab can't resolve must be asked about once per scan."""

    def setUp(self):
        import threading
        self.tab = _Tab()
        self.tab._delivery_probe_misses = set()
        self.tab._delivery_probe_lock = threading.Lock()

    def test_warm_resolves_each_distinct_source_mr_once(self):
        import mr_jira
        # Four tasks, two distinct source MRs — the shape that made 8 workers
        # miss the same key at once.
        tasks = [
            {"task_id": "a", "project_id": "Fiji/Fiji", "merge_request_iid": 46441},
            {"task_id": "b", "project_id": "Fiji/Fiji", "merge_request_iid": 46441},
            {"task_id": "c", "project_id": "web/web", "merge_request_iid": 42391},
            {"task_id": "d", "project_id": "Fiji/Fiji", "merge_request_iid": 46441},
        ]
        seen = []

        def _fetch(project, iid, **kw):
            seen.append((project, iid))
            return mr_jira.JiraMetadata("", "", "opened")

        with mock.patch("mr_jira.get_cached_state", return_value=None),                 mock.patch("mr_jira.fetch_jira_metadata", side_effect=_fetch):
            self.tab._warm_delivery_probe(tasks)

        self.assertEqual(sorted(seen),
                         [("Fiji/Fiji", 46441), ("web/web", 42391)])

    def test_unresolvable_source_mr_is_memoised_and_not_retried(self):
        tasks = [{"task_id": "a", "project_id": "RND/rcvnc",
                  "merge_request_iid": 6745}]
        with mock.patch("mr_jira.get_cached_state", return_value=None),                 mock.patch("mr_jira.fetch_jira_metadata",
                           return_value=None) as fetch:
            self.tab._warm_delivery_probe(tasks)
            self.assertEqual(fetch.call_count, 1)
            self.assertTrue(self.tab._probe_is_dead(("RND/rcvnc", 6745)))
            # A second batch carrying the same dead MR must not re-ask.
            self.tab._warm_delivery_probe(tasks)
            self.assertEqual(fetch.call_count, 1)

    def test_per_task_probe_short_circuits_on_a_memoised_miss(self):
        self.tab._probe_mark_dead(("RND/rcvnc", 6745))
        task = {"task_id": "a", "project_id": "RND/rcvnc",
                "merge_request_iid": 6745}
        with mock.patch("mr_jira.fetch_jira_metadata") as fetch:
            self.tab._check_task_delivery_mr(task)
        self.assertFalse(task["_has_delivery_mr"])
        fetch.assert_not_called()

    def test_already_cached_state_needs_no_warm_call(self):
        tasks = [{"task_id": "a", "project_id": "web/web",
                  "merge_request_iid": 42391}]
        with mock.patch("mr_jira.get_cached_state", return_value="merged"),                 mock.patch("mr_jira.fetch_jira_metadata") as fetch:
            self.tab._warm_delivery_probe(tasks)
        fetch.assert_not_called()


class TestScanControl(unittest.TestCase):
    """Search doubles as Stop, and a superseded scan must not paint."""

    def test_search_cancels_a_running_scan_instead_of_reloading(self):
        import threading
        tab = _Tab()
        tab._scan_cancel = threading.Event()
        with mock.patch.object(type(tab), "_load_tasks",
                               create=True) as load:
            tab._on_search()
        self.assertTrue(tab._scan_cancel.is_set())
        load.assert_not_called()

    def test_progress_from_a_superseded_scan_is_dropped(self):
        tab = _Tab()
        tab._fetch_generation = 7
        tab._scan_progress_text = None
        tab._t = lambda key: "{scanned}/{total}/{matched}"
        tab._on_scan_progress(6, 100, 67436, 3)
        self.assertIsNone(tab._scan_progress_text)

    def test_progress_from_the_current_scan_is_shown(self):
        tab = _Tab()
        tab._fetch_generation = 7
        tab._scan_progress_text = None
        tab._t = lambda key: "{scanned}/{total}/{matched}"
        tab.lbl_mr_status_bar = mock.Mock()
        tab.mr_loading_overlay = mock.Mock()
        tab._on_scan_progress(7, 100, 67436, 3)
        self.assertEqual(tab._scan_progress_text, "100/67436/3")
        tab.lbl_mr_status_bar.configure.assert_called_once_with(text="100/67436/3")


class TestTransMrOpenFilter(unittest.TestCase):
    """The narrower "Trans MR# is open" view of the same scan."""

    def setUp(self):
        import threading
        self.tab = _Tab()
        self.tab._delivery_probe_misses = set()
        self.tab._delivery_probe_lock = threading.Lock()

    def test_strings_present_in_both_languages(self):
        import export_gui
        for lang in ("en", "zh"):
            table = export_gui.STRINGS[lang]
            self.assertIn("mr_trans_mr_open", table, lang)
            self.assertIn("mr_trans_mr_open_tip", table, lang)
            self.assertNotEqual(table["mr_trans_mr_open"],
                                table["mr_trans_mr_only"])

    def test_open_trans_mr_passes(self):
        import mr_jira
        task = {"task_id": "t1", "project_id": "web/web",
                "merge_request_iid": 42391, "delivery_mr_iid": 42392}
        with mock.patch("mr_jira.fetch_jira_metadata",
                        return_value=mr_jira.JiraMetadata("", "", "opened")):
            self.tab._check_task_delivery_mr(task, want_open=True)
        self.assertTrue(task["_has_delivery_mr"])
        self.assertTrue(task["_trans_mr_open"])

    def test_merged_trans_mr_is_filtered_out_but_still_exists(self):
        import mr_jira
        task = {"task_id": "t1", "project_id": "common/uns",
                "merge_request_iid": 4215, "delivery_mr_iid": 4216}
        with mock.patch("mr_jira.fetch_jira_metadata",
                        return_value=mr_jira.JiraMetadata("", "", "merged")):
            self.tab._check_task_delivery_mr(task, want_open=True)
        self.assertTrue(task["_has_delivery_mr"])
        self.assertFalse(task["_trans_mr_open"])

    def test_state_follows_the_fix_mr_not_the_import_mr(self):
        # 4213 -> 4214: the column's status follows 4214, so must the filter.
        task = {
            "_has_delivery_mr": True,
            "_delivery_ref": _delivery.DeliveryRef(
                project_id="common/uns", iid=4213, state="merged"),
            "_fix_ref": _delivery.DeliveryRef(
                project_id="common/uns", iid=4214, state="merged"),
        }
        asked = []

        def _fetch(project, iid, **kw):
            asked.append((project, iid, kw.get("force_refresh")))
            return None

        with mock.patch("mr_jira.fetch_jira_metadata", side_effect=_fetch):
            self.tab._resolve_trans_mr_state(task)

        self.assertEqual(asked, [("common/uns", 4214, True)])

    def test_open_state_is_not_resolved_when_not_asked_for(self):
        task = {"task_id": "t1", "project_id": "web/web",
                "merge_request_iid": 42391, "delivery_mr_iid": 42392}
        with mock.patch("mr_jira.fetch_jira_metadata") as fetch:
            self.tab._check_task_delivery_mr(task)
        self.assertTrue(task["_has_delivery_mr"])
        self.assertFalse(task["_trans_mr_open"])
        fetch.assert_not_called()

    def test_task_without_a_trans_mr_never_counts_as_open(self):
        task = {"task_id": "t1", "project_id": "RND/rcvnc",
                "merge_request_iid": 6745}
        self.tab._probe_mark_dead(("RND/rcvnc", 6745))
        self.tab._check_task_delivery_mr(task, want_open=True)
        self.assertFalse(task["_has_delivery_mr"])
        self.assertFalse(task["_trans_mr_open"])


class TestSkippedTaskPreFilter(unittest.TestCase):
    """85% of the pipeline's history is skipped tasks; the payload says so."""

    def setUp(self):
        from gui_tabs import MRPipelineTab
        self.ruled_out = MRPipelineTab._cannot_have_trans_mr

    def test_skipped_task_is_ruled_out_from_the_payload(self):
        self.assertTrue(self.ruled_out({"status": "skipped"}))
        self.assertTrue(self.ruled_out({"status": "SKIPPED"}))

    def test_every_other_status_still_gets_probed(self):
        for status in ("completed", "failed", "cancelled", "running", "", None):
            self.assertFalse(self.ruled_out({"status": status}), status)

    def test_missing_payload_is_not_ruled_out(self):
        self.assertFalse(self.ruled_out({}))
        self.assertFalse(self.ruled_out(None))


class TestScanScope(unittest.TestCase):
    """Load More continues a scan; everything else starts a new one."""

    def _tab(self, pending_append):
        import threading
        tab = _Tab()
        tab._pending_append = pending_append
        tab._scan_cursor = {"offset": 3200, "carry": [], "api_total": 67436,
                            "matched": 25, "scanned": 3200}
        tab._delivery_probe_misses = {("RND/rcvnc", 6745)}
        tab._delivery_probe_lock = threading.Lock()
        return tab

    def test_load_more_keeps_the_cursor_and_the_dead_mr_memo(self):
        tab = self._tab(pending_append=True)
        self.assertFalse(tab._reset_scan_scope_if_new())
        self.assertEqual(tab._scan_cursor["offset"], 3200)
        self.assertTrue(tab._probe_is_dead(("RND/rcvnc", 6745)))

    def test_a_fresh_search_drops_both(self):
        tab = self._tab(pending_append=False)
        self.assertTrue(tab._reset_scan_scope_if_new())
        self.assertIsNone(tab._scan_cursor)
        self.assertFalse(tab._probe_is_dead(("RND/rcvnc", 6745)))


class TestFindFollowUpMrs(unittest.TestCase):
    """One title search must yield both the import MR and any later fix MR."""

    class _Client:
        def __init__(self, mrs):
            self.mrs = mrs
            self.searches = []

        def has_token(self):
            return True

        def list_merge_requests(self, search, **kwargs):
            self.searches.append(search)
            return self.mrs

    def test_returns_import_and_fix_from_a_single_search(self):
        client = self._Client([
            {"iid": 4213, "title": "[Tranzor] Translations for MR!4003",
             "source_branch": "tranzor/translate-4003-772abc-p1-t1",
             "state": "merged",
             "references": {"full": "common/uns!4213"}},
            {"iid": 4214, "title": "[Tranzor] Translations for MR!4003",
             "source_branch": "tranzor-mr-fix-20260920093000",
             "state": "merged",
             "references": {"full": "common/uns!4214"}},
        ])

        import_ref, fix_ref = _delivery.find_follow_up_mrs(
            "common/uns", 4003, client=client)

        self.assertEqual(import_ref.iid, 4213)
        self.assertEqual(fix_ref.iid, 4214)
        self.assertEqual(len(client.searches), 1)

    def test_no_match_returns_a_pair_of_nones(self):
        client = self._Client([])
        self.assertEqual(
            _delivery.find_follow_up_mrs("common/uns", 4003, client=client),
            (None, None))

    def test_find_delivery_mr_still_returns_just_the_import_mr(self):
        client = self._Client([
            {"iid": 4213, "title": "[Tranzor] Translations for MR!4003",
             "source_branch": "tranzor/translate-4003-772abc-p1-t1",
             "references": {"full": "common/uns!4213"}},
        ])
        ref = _delivery.find_delivery_mr("common/uns", 4003, client=client)
        self.assertEqual(ref.iid, 4213)


if __name__ == "__main__":
    unittest.main()
