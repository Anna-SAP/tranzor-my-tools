"""build_light_inventory 的数据源级失败语义（source_errors）。

2026-08-06 RCA：三个数据源在 502/503 风暴下全部整体失败时，
build_light_inventory 仍"成功"返回空清单，GUI 以绿色 "0 products"
收场并把空清单缓存整个会话——Full Translations 面板等同瘫痪。
现在每个源的整体失败（非 401）记录进 LightInventory.source_errors，
由 GUI 区分"平台上没有产品"与"清单拉取被打断"。

Run:  python -m unittest test_light_inventory_errors
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_full_translations as ex


def _http_503():
    exc = RuntimeError(
        "503 Server Error: Service Temporarily Unavailable for url: "
        "http://tranzor-platform.int.rclabenv.com/api/v1/legacy/tasks")
    return exc


_MR_FILTERS = {"project_ids": ["proj/a"], "releases": [],
               "languages": ["de-DE"]}


class SourceErrorTests(unittest.TestCase):
    def test_single_source_failure_is_recorded_others_survive(self):
        with mock.patch.object(ex._legacy, "fetch_tasks",
                               side_effect=_http_503()), \
                mock.patch.object(ex._mr, "fetch_mr_filters_full",
                                  return_value=_MR_FILTERS), \
                mock.patch.object(ex._mr, "fetch_dashboard_overview",
                                  return_value={"total_cases": 5}), \
                mock.patch.object(ex._mr, "fetch_scan_tasks",
                                  return_value=(0, [])):
            inv = ex.build_light_inventory()
        self.assertEqual(sorted(inv.source_errors), ["legacy"])
        self.assertIn("503 Server Error", inv.source_errors["legacy"])
        # MR 源存活——产品照常进入清单。
        self.assertEqual([p["source"] for p in inv.products], ["mr"])
        self.assertEqual(inv.products[0]["entry_count"], 5)

    def test_all_sources_failed_yields_empty_products_with_errors(self):
        with mock.patch.object(ex._legacy, "fetch_tasks",
                               side_effect=_http_503()), \
                mock.patch.object(ex._mr, "fetch_mr_filters_full",
                                  side_effect=_http_503()), \
                mock.patch.object(ex._mr, "fetch_scan_tasks",
                                  side_effect=_http_503()):
            logs = []
            inv = ex.build_light_inventory(progress_cb=logs.append)
        self.assertEqual(inv.products, [])
        self.assertEqual(sorted(inv.source_errors),
                         ["legacy", "mr", "scan"])
        # 兜底 en-US 仍在（导出源语言用），但完成行必须如实标注不完整。
        self.assertEqual(inv.locales, [ex.SOURCE_LOCALE])
        self.assertTrue(any("轻量清单不完整" in ln for ln in logs))
        self.assertFalse(any("轻量清单完成" in ln for ln in logs))

    def test_clean_run_has_no_errors(self):
        tasks = [{"project_name": "P", "target_languages": ["ja-JP"]}]
        with mock.patch.object(ex._legacy, "fetch_tasks",
                               return_value=tasks), \
                mock.patch.object(ex._mr, "fetch_mr_filters_full",
                                  return_value=_MR_FILTERS), \
                mock.patch.object(ex._mr, "fetch_dashboard_overview",
                                  return_value={"total_cases": 1}), \
                mock.patch.object(ex._mr, "fetch_scan_tasks",
                                  return_value=(0, [])):
            logs = []
            inv = ex.build_light_inventory(progress_cb=logs.append)
        self.assertEqual(inv.source_errors, {})
        self.assertEqual(len(inv.products), 2)
        self.assertTrue(any("轻量清单完成" in ln for ln in logs))

    def test_per_project_overview_failure_is_soft(self):
        # 单个 /dashboard/overview 失败只让 entry_count 缺失，不判源失败。
        with mock.patch.object(ex._legacy, "fetch_tasks",
                               return_value=[]), \
                mock.patch.object(ex._mr, "fetch_mr_filters_full",
                                  return_value=_MR_FILTERS), \
                mock.patch.object(ex._mr, "fetch_dashboard_overview",
                                  side_effect=_http_503()), \
                mock.patch.object(ex._mr, "fetch_scan_tasks",
                                  return_value=(0, [])):
            inv = ex.build_light_inventory()
        self.assertNotIn("mr", inv.source_errors)
        self.assertEqual(inv.products[0]["entry_count"], None)

    def test_requested_sources_only(self):
        # 只请求 legacy 时，其它源既不拉取也不可能记错误。
        with mock.patch.object(ex._legacy, "fetch_tasks",
                               side_effect=_http_503()):
            inv = ex.build_light_inventory(sources=("legacy",))
        self.assertEqual(sorted(inv.source_errors), ["legacy"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
