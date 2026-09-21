"""Tests for the MR Branch / Trans MR Branch columns.

Both target branches ride along on the GitLab MR responses the table already
fetches, so the point of these tests is that the value reaches the right cell
-- in particular that the Trans MR branch follows the *current* translation
MR (the fix MR when the cell reads "4213 -> 4214", the import MR otherwise).

Run:  python -m unittest test_mr_branch_columns
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mr_delivery as _delivery  # noqa: E402
import mr_jira as _jira  # noqa: E402


class _Tree:
    def __init__(self):
        self.cells = {}

    def set(self, iid, column, value=None):
        if value is None:
            return self.cells.get((iid, column), "")
        self.cells[(iid, column)] = value


def _tab(tree=None):
    from gui_tabs import MRPipelineTab
    tab = MRPipelineTab.__new__(MRPipelineTab)
    tab.mr_tree = tree or _Tree()
    tab._delivery_row_iids = {}
    tab._jira_row_iids = {}
    tab._jira_titles_by_iid = {}
    tab._mr_link_meta = {}
    tab._delivery_status_refreshed = set()
    tab._prefetch_delivery_status = lambda keys: None
    return tab


class TestBranchPlumbing(unittest.TestCase):
    """target_branch must survive the GitLab payload to cache to ref hops."""

    def setUp(self):
        _jira.clear_cache()

    def tearDown(self):
        _jira.clear_cache()

    def test_metadata_fetch_caches_the_target_branch(self):
        class _Client:
            def has_token(self):
                return True

            def get_merge_request(self, project, iid, **kw):
                return {"title": "UIA-412397 UNS email", "state": "merged",
                        "target_branch": "26-4-2_XMN-FT5"}

        meta = _jira.fetch_jira_metadata("common/uns", 4215, client=_Client())
        self.assertEqual(meta.target_branch, "26-4-2_XMN-FT5")
        self.assertEqual(
            _jira.get_cached_branch("common/uns", 4215), "26-4-2_XMN-FT5")
        self.assertEqual(
            _jira.get_cached_metadata("common/uns", 4215).target_branch,
            "26-4-2_XMN-FT5")

    def test_payload_without_a_branch_caches_empty_not_none(self):
        class _Client:
            def has_token(self):
                return True

            def get_merge_request(self, project, iid, **kw):
                return {"title": "x", "state": "opened"}

        _jira.fetch_jira_metadata("p", 1, client=_Client())
        # "" means fetched-but-no-branch. None would mean "never fetched" and
        # would leave the cell spinning on its pending marker forever.
        self.assertEqual(_jira.get_cached_branch("p", 1), "")

    def test_never_fetched_branch_is_none(self):
        self.assertIsNone(_jira.get_cached_branch("p", 99))

    def test_delivery_ref_carries_the_branch(self):
        ref = _delivery.delivery_ref_from_mr(
            {"iid": 4216, "state": "merged", "target_branch": "26-4-2_XMN-FT5",
             "references": {"full": "common/uns!4216"}})
        self.assertEqual(ref.target_branch, "26-4-2_XMN-FT5")

    def test_delivery_ref_without_a_branch_is_empty(self):
        ref = _delivery.delivery_ref_from_mr(
            {"iid": 4216, "references": {"full": "common/uns!4216"}})
        self.assertEqual(ref.target_branch, "")


class TestSourceBranchCell(unittest.TestCase):

    def test_metadata_paints_the_mr_branch_cell(self):
        tab = _tab()
        tab._jira_row_iids = {("web/web", 42391): ["task-1"]}
        tab._set_title_cell = lambda iid, title: None
        tab._apply_jira_metadata(
            ("web/web", 42391),
            _jira.JiraMetadata("UIA-1", "t", "merged",
                               "eu_mqa_bug_challenge-auto-deploy"))
        self.assertEqual(
            tab.mr_tree.cells[("task-1", "mr_branch")],
            "eu_mqa_bug_challenge-auto-deploy")

    def test_unresolved_branch_paints_a_dash(self):
        tab = _tab()
        tab._jira_row_iids = {("web/web", 42391): ["task-1"]}
        tab._set_title_cell = lambda iid, title: None
        with mock.patch("mr_jira.get_cached_metadata", return_value=None), \
                mock.patch("mr_jira.get_cached_branch", return_value=None):
            tab._apply_jira_metadata(("web/web", 42391), None)
        self.assertEqual(tab.mr_tree.cells[("task-1", "mr_branch")], "—")


class TestTransBranchCell(unittest.TestCase):

    def test_branch_follows_the_import_mr_when_there_is_no_fix(self):
        tab = _tab()
        tab._mr_link_meta = {"task-1": {"project": "common/uns"}}
        tab._apply_follow_ups(
            "task-1",
            _delivery.DeliveryRef(project_id="common/uns", iid=4216,
                                  state="merged",
                                  target_branch="26-4-2_XMN-FT5"),
            None)
        self.assertEqual(tab.mr_tree.cells[("task-1", "delivery_mr")], "4216")
        self.assertEqual(
            tab.mr_tree.cells[("task-1", "delivery_branch")], "26-4-2_XMN-FT5")

    def test_branch_follows_the_fix_mr_when_the_cell_shows_an_arrow(self):
        # 4213 -> 4214: the row now describes 4214, so must the branch. Real
        # data: the source landed on 26-4_XMN-FT5 while the translation went
        # to 26-4-2_XMN-FT5 -- exactly the divergence these columns expose.
        tab = _tab()
        tab._mr_link_meta = {"task-1": {"project": "common/uns"}}
        tab._apply_follow_ups(
            "task-1",
            _delivery.DeliveryRef(project_id="common/uns", iid=4213,
                                  state="merged", target_branch="26-4_XMN-FT5"),
            _delivery.DeliveryRef(project_id="common/uns", iid=4214,
                                  state="merged",
                                  target_branch="26-4-2_XMN-FT5"))
        self.assertEqual(
            tab.mr_tree.cells[("task-1", "delivery_mr")], "4213 → 4214")
        self.assertEqual(
            tab.mr_tree.cells[("task-1", "delivery_branch")], "26-4-2_XMN-FT5")

    def test_known_mr_with_no_branch_yet_shows_the_pending_marker(self):
        tab = _tab()
        tab._mr_link_meta = {"task-1": {"project": "common/uns"}}
        tab._apply_follow_ups(
            "task-1",
            _delivery.DeliveryRef(project_id="common/uns", iid=4216),
            None)
        self.assertEqual(
            tab.mr_tree.cells[("task-1", "delivery_branch")], "…")

    def test_no_trans_mr_clears_the_branch_cell(self):
        tab = _tab()
        tab._mr_link_meta = {"task-1": {"delivery_iid": None}}
        tab._apply_follow_ups("task-1", None, None)
        self.assertEqual(
            tab.mr_tree.cells[("task-1", "delivery_branch")], "—")

    def test_live_status_refresh_also_lands_the_branch(self):
        tab = _tab()
        tab._delivery_row_iids = {("common/uns", 4216): ["task-1"]}
        tab._mr_link_meta = {"task-1": {"delivery_iid": 4216}}
        with mock.patch("mr_jira.get_cached_branch",
                        return_value="26-4-2_XMN-FT5"):
            tab._apply_delivery_status(("common/uns", 4216), "merged")
        self.assertEqual(
            tab.mr_tree.cells[("task-1", "delivery_mr_status")], "Merged")
        self.assertEqual(
            tab.mr_tree.cells[("task-1", "delivery_branch")], "26-4-2_XMN-FT5")
        self.assertEqual(
            tab._mr_link_meta["task-1"]["delivery_branch"], "26-4-2_XMN-FT5")


class TestBranchTooltip(unittest.TestCase):
    """A branch too long for its column must still be readable on hover."""

    def _tab_with_cell(self, column, value):
        tab = _tab()
        tab.mr_tree.cells[("task-1", column)] = value
        tab.mr_tree.identify_region = lambda x, y: "cell"
        tab.mr_tree.identify_column = lambda x: tab._col_ident(column)
        tab.mr_tree.identify_row = lambda y: "task-1"
        return tab

    def test_branch_cell_offers_its_full_value(self):
        for column in ("mr_branch", "delivery_branch"):
            tab = self._tab_with_cell(
                column, "eu_mqa_bug_challenge-auto-deploy")
            self.assertEqual(
                tab._title_tooltip_at(10, 20),
                ("task-1", "eu_mqa_bug_challenge-auto-deploy"), column)

    def test_placeholder_branch_cells_have_no_tooltip(self):
        for value in ("—", "…", ""):
            tab = self._tab_with_cell("mr_branch", value)
            self.assertIsNone(tab._title_tooltip_at(10, 20), repr(value))


if __name__ == "__main__":
    unittest.main()
