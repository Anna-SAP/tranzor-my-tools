"""collect_all_mr_results must inherit the panel's basic filters.

"Export all" (no row selected) should scope the aggregated translations to the
current Project / Release / Status so the Advanced-Filters content search runs
over the same set the list shows — not across every project.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_mr_pipeline as mr


class CollectAllFilterPassthroughTests(unittest.TestCase):
    def setUp(self):
        self._orig_tasks = mr.fetch_mr_tasks
        self._orig_results = mr.fetch_mr_results
        self._orig_enrich = mr.enrich_translations_with_task
        self.calls = []

        def fake_tasks(project_id=None, release=None, status=None,
                       limit=50, offset=0, project_ids=None):
            self.calls.append({"project_id": project_id, "release": release,
                               "status": status, "project_ids": project_ids})
            if offset == 0:
                return 1, [{"task_id": "t1", "project_id": project_id or "p",
                            "merge_request_iid": "9"}]
            return 1, []

        mr.fetch_mr_tasks = fake_tasks
        mr.fetch_mr_results = lambda tid: {"translations": [
            {"opus_id": "k", "source_text": "s", "translated_text": "t",
             "target_language": "zh-CN"}], "summary": {}}
        mr.enrich_translations_with_task = lambda trs, task: None

    def tearDown(self):
        mr.fetch_mr_tasks = self._orig_tasks
        mr.fetch_mr_results = self._orig_results
        mr.enrich_translations_with_task = self._orig_enrich

    def test_filters_are_forwarded(self):
        out = mr.collect_all_mr_results(progress_callback=lambda _m: None,
                                        project_id="web/web", release="R26.3",
                                        status="completed")
        self.assertTrue(self.calls)
        self.assertEqual(self.calls[0]["project_id"], "web/web")
        self.assertEqual(self.calls[0]["release"], "R26.3")
        self.assertEqual(self.calls[0]["status"], "completed")
        self.assertEqual(len(out["translations"]), 1)

    def test_default_is_completed_all_projects(self):
        mr.collect_all_mr_results(progress_callback=lambda _m: None)
        self.assertEqual(self.calls[0]["project_id"], None)
        self.assertEqual(self.calls[0]["release"], None)
        self.assertEqual(self.calls[0]["status"], "completed")
        self.assertIsNone(self.calls[0]["project_ids"])

    def test_multi_project_ids_are_forwarded(self):
        ids = ["common/uns", "web/bui"]
        mr.collect_all_mr_results(progress_callback=lambda _m: None,
                                  project_ids=ids, status="completed")
        self.assertTrue(self.calls)
        self.assertEqual(self.calls[0]["project_ids"], ids)
        self.assertIsNone(self.calls[0]["project_id"])


if __name__ == "__main__":
    unittest.main()
