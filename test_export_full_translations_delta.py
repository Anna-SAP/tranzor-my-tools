"""Delta Day2Day date-window tests for Full Translation export.

The incremental export interprets From/To as inclusive UTC+8 calendar days
and filters tasks by ``created_at`` (naive timestamps = UTC). MR/Scan lists
are newest-first, so paging must stop once a task older than From is seen.

Run:  python -m unittest test_export_full_translations_delta
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_full_translations as ft


PRODUCT = "webModule"
HASH = "dba1c95d7993e5d977b34efe8eaa9ed2"
TZ_UTC8 = timezone(timedelta(hours=8))
TZ_UTC = timezone.utc


def _oid(key: str) -> str:
    return f"RingCentral.{PRODUCT}.{HASH}.app.{key}"


def _tr(key: str, lang: str, text: str) -> dict:
    return {
        "opus_id": _oid(key),
        "source_text": f"src-{key}",
        "target_language": lang,
        "translated_text": text,
    }


def _mr_task(task_id: str, created_at: str) -> dict:
    return {
        "task_id": task_id,
        "project_id": "web/web",
        "merge_request_iid": 1,
        "created_at": created_at,
    }


class CreatedWindowHelperTests(unittest.TestCase):
    def test_utc8_inclusive_bounds(self):
        start, end = ft.resolve_created_window("2026-09-18", "2026-09-25")
        self.assertEqual(
            start.astimezone(TZ_UTC),
            datetime(2026, 9, 17, 16, 0, tzinfo=TZ_UTC))
        self.assertEqual(
            end.astimezone(TZ_UTC),
            datetime(2026, 9, 25, 16, 0, tzinfo=TZ_UTC))

    def test_empty_window_is_none(self):
        self.assertEqual(ft.resolve_created_window(None, None), (None, None))
        self.assertEqual(ft.resolve_created_window("", ""), (None, None))

    def test_date_objects_accepted(self):
        from datetime import date
        start, end = ft.resolve_created_window(
            date(2026, 9, 18), date(2026, 9, 25))
        self.assertEqual(start.astimezone(TZ_UTC8).date().isoformat(),
                         "2026-09-18")
        self.assertEqual(
            (end - timedelta(seconds=1)).astimezone(TZ_UTC8).date().isoformat(),
            "2026-09-25")

    def test_from_boundary_included(self):
        start, end = ft.resolve_created_window("2026-09-18", "2026-09-25")
        # 2026-09-18 00:00 UTC+8 == 2026-09-17 16:00 UTC
        task = {"created_at": "2026-09-17 16:00:00"}
        self.assertTrue(ft.task_in_created_window(task, start, end))

    def test_before_from_excluded(self):
        start, end = ft.resolve_created_window("2026-09-18", "2026-09-25")
        task = {"created_at": "2026-09-17 15:59:59"}
        self.assertFalse(ft.task_in_created_window(task, start, end))

    def test_last_instant_of_to_date_included(self):
        start, end = ft.resolve_created_window("2026-09-18", "2026-09-25")
        # 2026-09-25 23:59:59 UTC+8 == 2026-09-25 15:59:59 UTC
        task = {"created_at": "2026-09-25 15:59:59"}
        self.assertTrue(ft.task_in_created_window(task, start, end))

    def test_to_date_end_exclusive(self):
        start, end = ft.resolve_created_window("2026-09-18", "2026-09-25")
        task = {"created_at": "2026-09-25 16:00:00"}
        self.assertFalse(ft.task_in_created_window(task, start, end))

    def test_aware_utc_plus_offset_converts(self):
        start, end = ft.resolve_created_window("2026-09-18", "2026-09-25")
        task = {"created_at": "2026-09-18T00:00:00+08:00"}
        self.assertTrue(ft.task_in_created_window(task, start, end))

    def test_missing_created_at_excluded_from_delta(self):
        start, end = ft.resolve_created_window("2026-09-18", "2026-09-25")
        self.assertFalse(ft.task_in_created_window({}, start, end))
        self.assertFalse(
            ft.task_in_created_window({"created_at": ""}, start, end))

    def test_missing_created_at_kept_when_no_window(self):
        self.assertTrue(ft.task_in_created_window({}, None, None))

    def test_apply_created_window_reports_floor(self):
        start, end = ft.resolve_created_window("2026-09-18", "2026-09-25")
        tasks = [
            {"created_at": "2026-09-26 00:00:00", "task_id": "new"},
            {"created_at": "2026-09-20 00:00:00", "task_id": "in"},
            {"created_at": "2026-09-01 00:00:00", "task_id": "old"},
        ]
        kept, hit_floor = ft.apply_created_window(tasks, start, end)
        self.assertEqual([t["task_id"] for t in kept], ["in"])
        self.assertTrue(hit_floor)

    def test_apply_created_window_noop_without_bounds(self):
        tasks = [{"created_at": "x", "task_id": "a"}]
        kept, hit_floor = ft.apply_created_window(tasks, None, None)
        self.assertEqual(kept, tasks)
        self.assertFalse(hit_floor)

    def test_legacy_list_params_widen_one_civil_day(self):
        start, end = ft.resolve_created_window("2026-09-18", "2026-09-25")
        params = ft._legacy_list_date_params(start, end)
        self.assertEqual(params["created_after"], "2026-09-17")
        self.assertEqual(params["created_before"], "2026-09-26")


class _PagingMR:
    """Newest-first paginated MR list, recording offsets and result fetches."""

    def __init__(self, tasks, results, page_size=2):
        self.tasks = list(tasks)
        self.results = dict(results)
        self.page_size = page_size
        self.list_offsets = []
        self.results_calls = []

    def fetch_mr_tasks(self, project_id=None, status=None, limit=50,
                       offset=0, **_kw):
        self.list_offsets.append(offset)
        total = len(self.tasks)
        # Honour the fake's page_size so early-stop tests can force
        # multiple round-trips even though the collector asks for 100.
        size = min(limit, self.page_size)
        return total, self.tasks[offset:offset + size]

    def fetch_mr_results(self, task_id, **_kw):
        self.results_calls.append(task_id)
        return {"translations": list(self.results.get(task_id, []))}


class _PagingScan(_PagingMR):
    def fetch_scan_tasks(self, project_id=None, status=None, limit=50,
                         offset=0, **_kw):
        return self.fetch_mr_tasks(
            project_id=project_id, status=status, limit=limit, offset=offset)

    def fetch_scan_results(self, task_id, **_kw):
        return self.fetch_mr_results(task_id)


class DeltaCollectTests(unittest.TestCase):
    def setUp(self):
        self._orig_retries = ft._TASK_FETCH_RETRIES
        self._orig_backoff = ft._TASK_FETCH_BACKOFF
        self._orig_sleep = ft.time.sleep
        self._orig_mr = ft._mr
        self._orig_legacy = ft._legacy
        ft._TASK_FETCH_RETRIES = 1
        ft._TASK_FETCH_BACKOFF = 0.0
        ft.time.sleep = lambda *_a, **_k: None

    def tearDown(self):
        ft._TASK_FETCH_RETRIES = self._orig_retries
        ft._TASK_FETCH_BACKOFF = self._orig_backoff
        ft.time.sleep = self._orig_sleep
        ft._mr = self._orig_mr
        ft._legacy = self._orig_legacy

    def test_mr_stops_paging_once_older_than_from(self):
        # Newest-first, page size 2:
        #   page0: too-new, in-window
        #   page1: too-old, older  → hit floor, do not request page2
        tasks = [
            _mr_task("too-new", "2026-09-26T00:00:00"),
            _mr_task("in", "2026-09-20T00:00:00"),
            _mr_task("too-old", "2026-09-01T00:00:00"),
            _mr_task("older", "2026-08-01T00:00:00"),
        ]
        be = _PagingMR(
            tasks,
            {"in": [_tr("KEY", "zh-CN", "新")],
             "too-new": [_tr("KEY", "zh-CN", "过新")],
             "too-old": [_tr("KEY", "zh-CN", "过旧")],
             "older": [_tr("KEY", "zh-CN", "更旧")]},
            page_size=2,
        )
        ft._mr = be
        inv = ft.collect_full_translations(
            sources=["mr"],
            created_after="2026-09-18",
            created_before="2026-09-25",
        )
        self.assertEqual(be.list_offsets, [0, 2])
        self.assertEqual(be.results_calls, ["in"])
        self.assertEqual(
            inv.data[PRODUCT]["zh-CN"][_oid("KEY")], "新")

    def test_full_export_still_fetches_every_task(self):
        tasks = [
            _mr_task("a", "2026-09-26T00:00:00"),
            _mr_task("b", "2026-09-01T00:00:00"),
        ]
        be = _PagingMR(
            tasks,
            {"a": [_tr("K", "de-DE", "A")],
             "b": [_tr("K", "de-DE", "B")]},
            page_size=10,
        )
        ft._mr = be
        inv = ft.collect_full_translations(sources=["mr"])
        self.assertEqual(sorted(be.results_calls), ["a", "b"])
        # Newest created_at wins.
        self.assertEqual(inv.data[PRODUCT]["de-DE"][_oid("K")], "A")

    def test_scan_filters_to_window(self):
        tasks = [
            _mr_task("in", "2026-09-20T00:00:00"),
            _mr_task("old", "2026-08-01T00:00:00"),
        ]
        be = _PagingScan(
            tasks,
            {"in": [_tr("S", "ja-JP", "中")],
             "old": [_tr("S", "ja-JP", "旧")]},
            page_size=10,
        )
        ft._mr = be
        inv = ft.collect_full_translations(
            sources=["scan"],
            created_after="2026-09-18",
            created_before="2026-09-25",
        )
        self.assertEqual(be.results_calls, ["in"])
        self.assertEqual(inv.data[PRODUCT]["ja-JP"][_oid("S")], "中")

    def test_legacy_forwards_date_params_and_filters(self):
        seen = {}

        def fake_tasks(**kw):
            seen.update(kw)
            return [
                {"id": 1, "project_name": "P", "task_name": "new",
                 "created_at": "2026-09-20 00:00:00"},
                {"id": 2, "project_name": "P", "task_name": "old",
                 "created_at": "2026-08-01 00:00:00"},
            ]

        def fake_translations(tid, **_kw):
            return [_tr("L", "fr-FR", f"t{tid}")]

        ft._legacy = mock.Mock()
        ft._legacy.fetch_tasks.side_effect = fake_tasks
        ft._legacy.fetch_all_translations.side_effect = fake_translations
        inv = ft.collect_full_translations(
            sources=["legacy"],
            created_after="2026-09-18",
            created_before="2026-09-25",
        )
        self.assertEqual(seen.get("created_after"), "2026-09-17")
        self.assertEqual(seen.get("created_before"), "2026-09-26")
        self.assertEqual(
            ft._legacy.fetch_all_translations.call_args[0][0], 1)
        self.assertEqual(inv.data[PRODUCT]["fr-FR"][_oid("L")], "t1")

    def test_legacy_typeerror_falls_back_to_client_filter(self):
        """Older fetch_tasks signatures without date kwargs still filter."""
        def fake_tasks(base_url=None):
            return [
                {"id": 1, "project_name": "P", "task_name": "new",
                 "created_at": "2026-09-20 00:00:00"},
                {"id": 2, "project_name": "P", "task_name": "old",
                 "created_at": "2026-08-01 00:00:00"},
            ]

        ft._legacy = mock.Mock()
        ft._legacy.fetch_tasks.side_effect = fake_tasks
        ft._legacy.fetch_all_translations.side_effect = (
            lambda tid, **_k: [_tr("L", "it-IT", f"t{tid}")])
        inv = ft.collect_full_translations(
            sources=["legacy"],
            created_after="2026-09-18",
            created_before="2026-09-25",
        )
        self.assertEqual(
            ft._legacy.fetch_all_translations.call_args[0][0], 1)
        self.assertEqual(inv.data[PRODUCT]["it-IT"][_oid("L")], "t1")


if __name__ == "__main__":
    unittest.main()
