"""MR Pipeline Project dropdown: multi-select wiring.

Covers the Tk-free pieces:

- ``normalize_project_ids`` / multi-project ``fetch_mr_tasks`` merge+slice
- ``aggregate_dashboard_overviews`` weighted average
- ``MRPipelineTab`` filter kwargs, display, reset, stale-id prune

The popup itself is UI (same as searchable_combobox / date_picker) and is
not instantiated here.

Run:  python -m unittest test_mr_project_multiselect test_searchable_combobox
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_mr_pipeline as mr
from gui_tabs import MRPipelineTab


class NormalizeProjectIdsTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(mr.normalize_project_ids(), [])
        self.assertEqual(mr.normalize_project_ids("", None), [])
        self.assertEqual(mr.normalize_project_ids(None, []), [])

    def test_single_string(self):
        self.assertEqual(mr.normalize_project_ids("web/bui"), ["web/bui"])

    def test_project_ids_wins(self):
        self.assertEqual(
            mr.normalize_project_ids("ignored", ["a", "b"]),
            ["a", "b"])

    def test_list_passed_as_project_id(self):
        self.assertEqual(
            mr.normalize_project_ids(["common/uns", "web/bui"]),
            ["common/uns", "web/bui"])

    def test_drops_blanks_and_dupes_keeps_order(self):
        self.assertEqual(
            mr.normalize_project_ids(None, ["b", "", "a", "b", "  "]),
            ["b", "a"])


class FetchMrTasksMultiTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fake_one(project_id=None, release=None, status=None,
                     limit=50, offset=0, base_url=None):
            self.calls.append({
                "project_id": project_id, "limit": limit, "offset": offset,
                "release": release, "status": status,
            })
            rows = {
                "A": [
                    {"task_id": "a1", "created_at": "2026-08-05T10:00:00",
                     "project_id": "A"},
                    {"task_id": "a2", "created_at": "2026-08-03T10:00:00",
                     "project_id": "A"},
                    {"task_id": "a3", "created_at": "2026-08-01T10:00:00",
                     "project_id": "A"},
                ],
                "B": [
                    {"task_id": "b1", "created_at": "2026-08-04T10:00:00",
                     "project_id": "B"},
                    {"task_id": "b2", "created_at": "2026-08-02T10:00:00",
                     "project_id": "B"},
                ],
            }[project_id]
            return len(rows), rows[:limit]

        self._patch = mock.patch.object(
            mr, "_fetch_mr_tasks_one", side_effect=fake_one)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_single_id_uses_one_request_path(self):
        total, tasks = mr.fetch_mr_tasks(project_id="A", limit=2, offset=0)
        self.assertEqual(total, 3)
        self.assertEqual([t["task_id"] for t in tasks], ["a1", "a2"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["offset"], 0)
        self.assertEqual(self.calls[0]["limit"], 2)

    def test_multi_merges_newest_first(self):
        total, tasks = mr.fetch_mr_tasks(
            project_ids=["A", "B"], limit=10, offset=0)
        self.assertEqual(total, 5)  # 3 + 2
        self.assertEqual(
            [t["task_id"] for t in tasks],
            ["a1", "b1", "a2", "b2", "a3"])

    def test_multi_paginates_the_merged_stream(self):
        # Page 1 (offset 2, limit 2) of newest-first union:
        # a1, b1, [a2, b2], a3
        total, tasks = mr.fetch_mr_tasks(
            project_ids=["A", "B"], limit=2, offset=2)
        self.assertEqual(total, 5)
        self.assertEqual([t["task_id"] for t in tasks], ["a2", "b2"])
        # Each project is asked for offset+limit = 4 rows from offset 0,
        # never a per-project page 1 (that would skip the wrong rows).
        for call in self.calls:
            self.assertEqual(call["offset"], 0)
            self.assertEqual(call["limit"], 4)

    def test_one_element_project_ids_is_single_path(self):
        mr.fetch_mr_tasks(project_ids=["A"], limit=1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["project_id"], "A")
        self.assertEqual(self.calls[0]["limit"], 1)


class AggregateOverviewTests(unittest.TestCase):
    def test_sums_and_weighted_average(self):
        out = mr.aggregate_dashboard_overviews([
            {"total_tasks": 10, "completed_tasks": 8, "failed_tasks": 1,
             "average_score": 90},
            {"total_tasks": 6, "completed": 2, "failed": 1,
             "average_score": 60},
        ])
        self.assertEqual(out["total_tasks"], 16)
        self.assertEqual(out["completed_tasks"], 10)
        self.assertEqual(out["failed_tasks"], 2)
        # (90*8 + 60*2) / 10 = 84.0
        self.assertEqual(out["average_score"], 84.0)

    def test_skips_missing_scores(self):
        out = mr.aggregate_dashboard_overviews([
            {"total_tasks": 4, "completed_tasks": 4, "average_score": 80},
            {"total_tasks": 3, "completed_tasks": 3},  # no score
        ])
        self.assertEqual(out["total_tasks"], 7)
        self.assertEqual(out["average_score"], 80.0)

    def test_empty(self):
        out = mr.aggregate_dashboard_overviews([])
        self.assertEqual(out["total_tasks"], 0)
        self.assertIsNone(out["average_score"])


class _FakeVar:
    def __init__(self, value=""):
        self.value = value

    def set(self, v):
        self.value = v

    def get(self):
        return self.value


class _FakeCombo:
    def __init__(self):
        self.values = []

    def configure(self, values=None, **_kw):
        if values is not None:
            self.values = list(values)


class _FakeApp:
    lang = "en"


def _bare_tab():
    tab = MRPipelineTab.__new__(MRPipelineTab)
    tab.app = _FakeApp()
    tab._mr_selected_projects = []
    tab.mr_project_var = _FakeVar()
    tab.cmb_mr_project = _FakeCombo()
    tab.cmb_mr_release = _FakeCombo()
    return tab


class TabFilterKwargsTests(unittest.TestCase):
    def test_empty_is_unfiltered(self):
        tab = _bare_tab()
        self.assertEqual(tab._mr_project_filter_kwargs(),
                         {"project_id": None})

    def test_single_uses_project_id_only(self):
        tab = _bare_tab()
        tab._mr_selected_projects = ["common/uns"]
        self.assertEqual(tab._mr_project_filter_kwargs(),
                         {"project_id": "common/uns"})

    def test_multi_uses_project_ids(self):
        tab = _bare_tab()
        tab._mr_selected_projects = ["common/uns", "web/bui"]
        self.assertEqual(
            tab._mr_project_filter_kwargs(),
            {"project_id": None, "project_ids": ["common/uns", "web/bui"]})


class TabDisplayAndResetTests(unittest.TestCase):
    def test_set_selected_updates_summary(self):
        tab = _bare_tab()
        tab._set_mr_selected_projects(["a", "b"])
        self.assertEqual(tab._mr_selected_projects, ["a", "b"])
        self.assertEqual(tab.mr_project_var.value, "2 selected")

        tab.app.lang = "zh"
        tab._sync_mr_project_display()
        self.assertEqual(tab.mr_project_var.value, "已选 2 项")

        tab._set_mr_selected_projects(["common/uns"])
        self.assertEqual(tab.mr_project_var.value, "common/uns")

        tab._set_mr_selected_projects([])
        self.assertEqual(tab.mr_project_var.value, "")

    def test_on_reset_clears_selection(self):
        tab = _bare_tab()
        tab._mr_selected_projects = ["a", "b"]
        tab.mr_project_var.value = "2 selected"
        tab.mr_release_var = _FakeVar("R")
        tab.mr_status_var = _FakeVar("completed")
        tab.mr_iid_var = _FakeVar("1")
        tab.mr_task_id_var = _FakeVar("t")
        tab.mr_jira_var = _FakeVar("X-1")

        class _E:
            def delete(self, *_a):
                pass

        tab.mr_date_from = _E()
        tab.mr_date_to = _E()
        tab.mr_page = 3
        tab._loaded = 0
        tab._load_tasks = lambda: setattr(tab, "_loaded", 1)
        tab._invalidate_post_edit_cache = lambda: None

        tab._on_reset()

        self.assertEqual(tab._mr_selected_projects, [])
        self.assertEqual(tab.mr_project_var.value, "")
        self.assertEqual(tab.mr_page, 0)
        self.assertEqual(tab._loaded, 1)

    def test_filters_loaded_prunes_stale_ids(self):
        tab = _bare_tab()
        tab._mr_selected_projects = ["keep/me", "gone/me"]
        tab._on_filters_loaded({
            "project_ids": ["keep/me", "new/one"],
            "releases": ["R26.3"],
        })
        self.assertEqual(tab._mr_selected_projects, ["keep/me"])
        self.assertEqual(tab.mr_project_var.value, "keep/me")
        self.assertEqual(tab.cmb_mr_project.values[0], "")
        self.assertIn("keep/me", tab.cmb_mr_project.values)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
