"""Stage Full Translations: base_url routing and env isolation.

The Stage tab reuses the production Full Translations panel with an
explicit ``base_url``. These tests pin:

- legacy / scan URL helpers default to production and honour a Stage origin
- fetch functions hit the given host
- light-inventory and collect forward ``base_url`` to every source
- the GUI helper only injects ``base_url`` for the non-prod instance
- Stage presets / health-monitor attr / default filenames are namespaced
- the wrapper module imports without a circular import

Run:  python -m unittest test_full_translations_stage
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_full_translations as ft
import export_mr_pipeline as mr_api
import export_translations as legacy
import gui_tab_full_translations as gui


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


class LegacyUrlHelperTests(unittest.TestCase):
    def test_default_is_production(self):
        self.assertEqual(legacy.tranzor_url(), PROD)
        self.assertEqual(legacy.legacy_api_root(), f"{PROD}/api/v1/legacy")

    def test_stage_origin(self):
        self.assertEqual(legacy.tranzor_url(STAGE), STAGE)
        self.assertEqual(
            legacy.legacy_api_root(STAGE), f"{STAGE}/api/v1/legacy")

    def test_strips_trailing_slash(self):
        self.assertEqual(legacy.tranzor_url(STAGE + "/"), STAGE)

    def test_fwd_omits_empty(self):
        self.assertEqual(legacy._fwd(None), {})
        self.assertEqual(legacy._fwd(STAGE), {"base_url": STAGE})


class ScanUrlHelperTests(unittest.TestCase):
    def test_default_is_production(self):
        self.assertEqual(
            mr_api.scan_api_root(),
            f"{PROD}/api/v1/missing_translation_scan")

    def test_stage_origin(self):
        self.assertEqual(
            mr_api.scan_api_root(STAGE),
            f"{STAGE}/api/v1/missing_translation_scan")


class LegacyFetchHonoursBaseUrlTests(unittest.TestCase):
    def setUp(self):
        self.urls = []

        def _capture(url, **kwargs):
            self.urls.append(url)
            if url.endswith("/tasks"):
                return _FakeResp({"tasks": []})
            if "/translations" in url:
                return _FakeResp({"total": 0, "entries": []})
            return _FakeResp({"task_name": "t", "target_languages": []})

        self._orig = legacy._api_get
        legacy._api_get = _capture

    def tearDown(self):
        legacy._api_get = self._orig

    def test_prod_default_stays_on_prod_host(self):
        legacy.fetch_tasks()
        legacy.fetch_task_info(1)
        self.assertTrue(self.urls)
        for u in self.urls:
            self.assertTrue(u.startswith(f"{PROD}/api/v1/legacy"), u)
            self.assertNotIn("tranzor-platform-stage", u)

    def test_stage_base_url_hits_stage_host(self):
        legacy.fetch_tasks(base_url=STAGE)
        legacy.fetch_task_info(1, base_url=STAGE)
        legacy.fetch_task_languages(1, base_url=STAGE)
        self.assertTrue(self.urls)
        for u in self.urls:
            self.assertTrue(u.startswith(f"{STAGE}/api/v1/legacy"), u)

    def test_fetch_all_translations_stage_urls(self):
        orig_hydrate = legacy.hydrate_truncated_entries
        orig_langs = legacy.fetch_task_languages
        legacy.hydrate_truncated_entries = lambda *a, **k: 0
        legacy.fetch_task_languages = lambda task_id, **kw: ["de-DE"]
        try:
            self.urls.clear()
            legacy.fetch_all_translations("T", base_url=STAGE)
        finally:
            legacy.hydrate_truncated_entries = orig_hydrate
            legacy.fetch_task_languages = orig_langs
        self.assertTrue(self.urls)
        for u in self.urls:
            self.assertTrue(u.startswith(f"{STAGE}/api/v1/legacy"), u)


class ScanFetchHonoursBaseUrlTests(unittest.TestCase):
    def setUp(self):
        self.urls = []

        def _capture(url, **kwargs):
            self.urls.append(url)
            if url.endswith("/tasks"):
                return _FakeResp({"total": 0, "tasks": []})
            if "/results" in url:
                return _FakeResp({"translations": [], "total": 0})
            return _FakeResp({})

        self._orig = mr_api._api_get
        mr_api._api_get = _capture

    def tearDown(self):
        mr_api._api_get = self._orig

    def test_prod_default_stays_on_prod_host(self):
        mr_api.fetch_scan_tasks(limit=1)
        self.assertTrue(self.urls)
        for u in self.urls:
            self.assertTrue(
                u.startswith(f"{PROD}/api/v1/missing_translation_scan"), u)
            self.assertNotIn("tranzor-platform-stage", u)

    def test_stage_base_url_hits_stage_host(self):
        mr_api.fetch_scan_tasks(limit=1, base_url=STAGE)
        mr_api.fetch_scan_task_detail("abc", base_url=STAGE)
        mr_api.fetch_scan_results("abc", base_url=STAGE)
        self.assertTrue(self.urls)
        for u in self.urls:
            self.assertTrue(
                u.startswith(f"{STAGE}/api/v1/missing_translation_scan"), u)


class InventoryForwardsBaseUrlTests(unittest.TestCase):
    def test_build_light_inventory_forwards_stage_url(self):
        seen = {"legacy": [], "mr": [], "scan": []}

        def fake_tasks(**kw):
            seen["legacy"].append(kw.get("base_url"))
            return []

        def fake_filters(**kw):
            seen["mr"].append(kw.get("base_url"))
            return {"project_ids": [], "languages": []}

        def fake_scan(**kw):
            seen["scan"].append(kw.get("base_url"))
            return 0, []

        with mock.patch.object(ft._legacy, "fetch_tasks",
                               side_effect=fake_tasks), \
             mock.patch.object(ft._mr, "fetch_mr_filters_full",
                               side_effect=fake_filters), \
             mock.patch.object(ft._mr, "fetch_scan_tasks",
                               side_effect=fake_scan):
            ft.build_light_inventory(base_url=STAGE)
        self.assertEqual(seen["legacy"], [STAGE])
        self.assertEqual(seen["mr"], [STAGE])
        self.assertEqual(seen["scan"], [STAGE])

    def test_build_light_inventory_omits_base_url_on_prod(self):
        seen = []

        def fake_tasks(**kw):
            seen.append(kw)
            return []

        with mock.patch.object(ft._legacy, "fetch_tasks",
                               side_effect=fake_tasks), \
             mock.patch.object(ft._mr, "fetch_mr_filters_full",
                               return_value={"project_ids": [],
                                             "languages": []}), \
             mock.patch.object(ft._mr, "fetch_scan_tasks",
                               return_value=(0, [])):
            ft.build_light_inventory()
        self.assertEqual(seen, [{}])


class CollectForwardsBaseUrlTests(unittest.TestCase):
    def test_collect_forwards_stage_url_to_all_sources(self):
        seen = []

        def fake_legacy_tasks(**kw):
            seen.append(("legacy_list", kw.get("base_url")))
            return []

        def fake_mr_tasks(**kw):
            seen.append(("mr_list", kw.get("base_url")))
            return 0, []

        def fake_scan_tasks(**kw):
            seen.append(("scan_list", kw.get("base_url")))
            return 0, []

        with mock.patch.object(ft._legacy, "fetch_tasks",
                               side_effect=fake_legacy_tasks), \
             mock.patch.object(ft._mr, "fetch_mr_tasks",
                               side_effect=fake_mr_tasks), \
             mock.patch.object(ft._mr, "fetch_scan_tasks",
                               side_effect=fake_scan_tasks):
            ft.collect_full_translations(base_url=STAGE)
        self.assertEqual(
            {kind for kind, _ in seen},
            {"legacy_list", "mr_list", "scan_list"})
        self.assertTrue(all(bu == STAGE for _, bu in seen))

    def test_collect_mr_results_forwards_stage_url(self):
        seen = []

        def fake_tasks(**kw):
            self.assertEqual(kw.get("base_url"), STAGE)
            return 1, [{
                "task_id": "abc", "project_id": "p",
                "created_at": "2026-01-01T00:00:00",
            }]

        def fake_results(tid, **kw):
            seen.append(kw.get("base_url"))
            return {"translations": []}

        with mock.patch.object(ft._mr, "fetch_mr_tasks",
                               side_effect=fake_tasks), \
             mock.patch.object(ft._mr, "fetch_mr_results",
                               side_effect=fake_results):
            ft.collect_full_translations(sources=["mr"], base_url=STAGE)
        self.assertEqual(seen, [STAGE])


class TabHelperTests(unittest.TestCase):
    def test_api_kw_silent_on_prod(self):
        tab = gui.FullTranslationsTab.__new__(gui.FullTranslationsTab)
        tab.base_url = PROD
        tab.env_key = "prod"
        self.assertEqual(tab._api_kw(), {})

    def test_api_kw_injects_stage_url(self):
        tab = gui.FullTranslationsTab.__new__(gui.FullTranslationsTab)
        tab.base_url = STAGE
        tab.env_key = "stage"
        self.assertEqual(tab._api_kw(), {"base_url": STAGE})

    def test_api_kw_missing_base_url_is_empty(self):
        tab = gui.FullTranslationsTab.__new__(gui.FullTranslationsTab)
        self.assertEqual(tab._api_kw(), {})

    def test_monitor_attr_is_namespaced(self):
        tab = gui.FullTranslationsTab.__new__(gui.FullTranslationsTab)
        tab.env_key = "prod"
        self.assertEqual(tab._monitor_attr(), "health_monitor")
        tab.env_key = "stage"
        self.assertEqual(tab._monitor_attr(), "health_monitor_stage")

    def test_title_keys_follow_env(self):
        tab = gui.FullTranslationsTab.__new__(gui.FullTranslationsTab)
        tab.env_key = "prod"
        self.assertEqual(tab._title_key(), "ft_title")
        self.assertEqual(tab._subtitle_key(), "ft_subtitle")
        tab.env_key = "stage"
        self.assertEqual(tab._title_key(), "ft_title_stage")
        self.assertEqual(tab._subtitle_key(), "ft_subtitle_stage")

    def test_is_stage(self):
        tab = gui.FullTranslationsTab.__new__(gui.FullTranslationsTab)
        self.assertFalse(tab._is_stage())
        tab.env_key = "stage"
        self.assertTrue(tab._is_stage())


class StageTabModuleTests(unittest.TestCase):
    def test_module_imports_without_circular_import(self):
        import gui_tab_full_translations_stage as mod
        self.assertIn("tab_full_translations_stage", mod.STRINGS["en"])
        self.assertTrue(hasattr(mod, "FullTranslationsStageTab"))

    def test_wrapper_constructs_inner_tab_with_stage_url(self):
        import gui_tab_full_translations_stage as mod

        constructed = {}

        class _FakeInner:
            def __init__(self, parent, app, *, base_url=None, env_key="prod"):
                constructed["base_url"] = base_url
                constructed["env_key"] = env_key

            def refresh_text(self):
                constructed["refreshed"] = True

            def on_first_show(self):
                constructed["shown"] = True

        with mock.patch(
                "gui_tab_full_translations.FullTranslationsTab",
                _FakeInner):
            tab = mod.FullTranslationsStageTab(parent=None, app=None)
        self.assertEqual(constructed["base_url"], STAGE)
        self.assertEqual(constructed["env_key"], "stage")
        tab.refresh_text()
        tab.on_first_show()
        self.assertTrue(constructed["refreshed"])
        self.assertTrue(constructed["shown"])


if __name__ == "__main__":
    unittest.main()
