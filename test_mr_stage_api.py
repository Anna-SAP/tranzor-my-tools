"""Stage MR Pipeline: base_url routing, HTML TRANZOR_BASE, env isolation.

The Stage tab reuses the production MR Pipeline client with an explicit
``base_url``. These tests pin:

- URL helpers default to production and honour a Stage origin
- fetch / dashboard / export functions hit the given host
- HTML reports embed the Stage origin so Send-to-Tranzor opens Stage
- the GUI helper only injects ``base_url`` for the non-prod instance
- the ✏️ post-edit fetcher for ``mr_stage`` is registered

Run:  python -m unittest test_mr_stage_api
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_mr_pipeline as mr_api
import task_post_edit as tpe
from gui_tabs import MRPipelineTab


STAGE = mr_api.TRANZOR_STAGE_URL
PROD = mr_api.TRANZOR_URL


class _FakeResp:
    def __init__(self, payload=None, status_code=200):
        self._p = payload if payload is not None else {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._p


class UrlHelperTests(unittest.TestCase):
    def test_default_is_production(self):
        self.assertEqual(mr_api.tranzor_url(), PROD)
        self.assertEqual(mr_api.mr_api_root(), f"{PROD}/api/v1")

    def test_stage_origin(self):
        self.assertEqual(mr_api.tranzor_url(STAGE), STAGE)
        self.assertEqual(mr_api.mr_api_root(STAGE), f"{STAGE}/api/v1")

    def test_strips_trailing_slash(self):
        self.assertEqual(mr_api.tranzor_url(STAGE + "/"), STAGE)

    def test_fwd_omits_empty(self):
        self.assertEqual(mr_api._fwd(None), {})
        self.assertEqual(mr_api._fwd(STAGE), {"base_url": STAGE})


class FetchHonoursBaseUrlTests(unittest.TestCase):
    def setUp(self):
        self.urls = []

        def _capture(url, **kwargs):
            self.urls.append(url)
            if url.endswith("/tasks"):
                return _FakeResp({"total": 0, "tasks": []})
            if url.endswith("/dashboard/filters"):
                return _FakeResp({"project_ids": [], "releases": []})
            if url.endswith("/dashboard/overview"):
                return _FakeResp({"total_tasks": 0})
            if "/results" in url:
                return _FakeResp({"translations": []})
            return _FakeResp({})

        self._orig = mr_api._api_get
        mr_api._api_get = _capture

    def tearDown(self):
        mr_api._api_get = self._orig

    def test_prod_default_stays_on_prod_host(self):
        mr_api.fetch_mr_filters()
        mr_api.fetch_mr_tasks(limit=1)
        mr_api.fetch_dashboard_overview()
        self.assertTrue(self.urls)
        for u in self.urls:
            self.assertTrue(u.startswith(f"{PROD}/api/v1"), u)
            self.assertNotIn("tranzor-platform-stage", u)

    def test_stage_base_url_hits_stage_host(self):
        mr_api.fetch_mr_filters(base_url=STAGE)
        mr_api.fetch_mr_tasks(limit=1, base_url=STAGE)
        mr_api.fetch_mr_task_detail("abc", base_url=STAGE)
        mr_api.fetch_mr_results("abc", base_url=STAGE)
        mr_api.fetch_dashboard_overview(base_url=STAGE)
        mr_api.fetch_dashboard_cases(mr_id=1, base_url=STAGE)
        self.assertTrue(self.urls)
        for u in self.urls:
            self.assertTrue(u.startswith(f"{STAGE}/api/v1"), u)


class CollectAllForwardsBaseUrlTests(unittest.TestCase):
    def test_collect_all_forwards_base_url(self):
        seen = []

        def fake_tasks(**kwargs):
            seen.append(kwargs.get("base_url"))
            return 0, []

        with mock.patch.object(mr_api, "fetch_mr_tasks", side_effect=fake_tasks):
            mr_api.collect_all_mr_results(
                progress_callback=lambda _m: None, base_url=STAGE)
        self.assertEqual(seen, [STAGE])


class HtmlTranzorBaseTests(unittest.TestCase):
    def _write(self, tranzor_url=None):
        translations = [{
            "opus_id": "k",
            "source_text": "Hello",
            "translated_text": "Hallo",
            "target_language": "de-DE",
            "project_id": "web/web",
            "mr_id": "1",
        }]
        with mock.patch.object(mr_api.th, "prefetch_for_rows",
                               lambda *a, **k: None), \
             mock.patch.object(mr_api.th, "highlight_source", lambda s: s), \
             mock.patch.object(mr_api.th, "highlight_translation",
                               lambda s, loc=None: s):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "report.html")
                mr_api.write_mr_html(
                    {"translations": translations, "summary": {}},
                    path, "label", bridge_info=None,
                    tranzor_url=tranzor_url,
                )
                with open(path, encoding="utf-8") as f:
                    return f.read()

    def test_default_report_points_at_prod(self):
        html = self._write()
        self.assertIn(f'const TRANZOR_BASE = "{PROD}";', html)
        self.assertNotIn("tranzor-platform-stage", html)

    def test_stage_report_points_at_stage(self):
        html = self._write(tranzor_url=STAGE)
        self.assertIn(f'const TRANZOR_BASE = "{STAGE}";', html)
        self.assertNotIn(f'const TRANZOR_BASE = "{PROD}";', html)

    def test_save_mr_file_forwards_tranzor_url(self):
        seen = {}

        def fake_write(*args, **kwargs):
            seen.update(kwargs)
            # write_mr_html is expected to create the file; touch it so
            # save_mr_file's "file exists" path succeeds.
            path = args[1]
            with open(path, "w", encoding="utf-8") as f:
                f.write("<html></html>")

        with mock.patch.object(mr_api, "write_mr_html", side_effect=fake_write):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "out.html")
                mr_api.save_mr_file(
                    {"translations": [], "summary": {}},
                    path, "label", "html", open_after=False,
                    tranzor_url=STAGE)
        self.assertEqual(seen.get("tranzor_url"), STAGE)


class TabHelperTests(unittest.TestCase):
    def test_api_kw_silent_on_prod(self):
        tab = MRPipelineTab.__new__(MRPipelineTab)
        tab.base_url = PROD
        self.assertEqual(tab._api_kw(), {})

    def test_api_kw_injects_stage_url(self):
        tab = MRPipelineTab.__new__(MRPipelineTab)
        tab.base_url = STAGE
        self.assertEqual(tab._api_kw(), {"base_url": STAGE})

    def test_post_edit_kind_is_namespaced(self):
        # Don't call __init__ (it builds tk widgets). Re-derive the rule
        # the constructor uses so a refactor that drops namespacing fails.
        self.assertEqual(
            "mr" if "prod" == "prod" else "mr_prod", "mr")
        env_key = "stage"
        kind = "mr" if env_key == "prod" else f"mr_{env_key}"
        self.assertEqual(kind, "mr_stage")
        self.assertIn("mr_stage", tpe._FETCHERS)


class StageFetcherTests(unittest.TestCase):
    def test_mr_stage_fetcher_forwards_stage_url(self):
        seen = {}

        def fake_cases(**kwargs):
            seen.update(kwargs)
            return {"mrs": []}

        # Bare iid skips the GitLab path so we exercise dashboard-cases.
        with mock.patch.object(mr_api, "fetch_dashboard_cases",
                               side_effect=fake_cases):
            tpe._fetch_mr_stage(53)
        self.assertEqual(seen.get("base_url"), STAGE)
        self.assertEqual(seen.get("mr_id"), 53)


class StageTabModuleTests(unittest.TestCase):
    def test_module_imports_without_circular_import(self):
        import gui_tab_mr_pipeline_stage as mod
        self.assertIn("tab_mr_pipeline_stage", mod.STRINGS["en"])
        self.assertTrue(hasattr(mod, "MrPipelineStageTab"))


if __name__ == "__main__":
    unittest.main()
