"""Tests for the Same Origin pure-logic layer (:mod:`same_origin`).

Covers the four things the GUI relies on but can't exercise headlessly:

- ``group_same_origin`` — grouping by (project, MR#), Core-product filtering,
  the "≥2 runs" duplicate gate, created-asc task order, latest-first groups.
- ``compute_mr_divergences`` — cross-run comparison per (opus_id, locale):
  flags only genuine divergences, distinguishes text-change vs added/removed,
  and leaves byte-identical runs out.
- ``scan_same_origin_groups`` — paginated/concurrent fetch fan-out and the
  ``max_pages`` truncation flag, driven by an injected ``fetch_tasks``.
- ``load/save_core_products`` — user-config round-trip, normalization/dedup,
  empty-list rejection, and default fallback — all against a temp path so the
  real ``~/.tranzor_exporter/core_products.json`` is never touched.

Run:  python -m unittest test_same_origin
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import same_origin as so


def _task(tid, pid, mr, created, **extra):
    d = {"task_id": tid, "project_id": pid, "merge_request_iid": mr,
         "created_at": created}
    d.update(extra)
    return d


class GroupSameOriginTests(unittest.TestCase):

    def test_only_repeat_core_mrs_survive(self):
        tasks = [
            _task("a1", "web/web", 40461, "2026-06-15T14:06:48"),
            _task("a2", "web/web", 40461, "2026-06-17T14:42:26"),
            _task("b1", "web/web", 999, "2026-06-10T00:00:00"),     # single run
            _task("c1", "not/core", 5, "2026-06-01T00:00:00"),       # non-core
            _task("c2", "not/core", 5, "2026-06-02T00:00:00"),       # non-core
        ]
        groups = so.group_same_origin(tasks, ["web/web"])
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual(g["mr_iid"], 40461)
        self.assertEqual(g["task_count"], 2)

    def test_tasks_sorted_created_ascending(self):
        tasks = [
            _task("late", "web/web", 1, "2026-06-17T00:00:00"),
            _task("early", "web/web", 1, "2026-06-15T00:00:00"),
        ]
        g = so.group_same_origin(tasks, ["web/web"])[0]
        self.assertEqual([t["task_id"] for t in g["tasks"]], ["early", "late"])
        self.assertTrue(g["earliest_created"].startswith("2026-06-15"))
        self.assertTrue(g["latest_created"].startswith("2026-06-17"))

    def test_groups_sorted_latest_activity_first(self):
        tasks = [
            _task("x1", "web/web", 1, "2026-01-01T00:00:00"),
            _task("x2", "web/web", 1, "2026-01-02T00:00:00"),
            _task("y1", "web/web", 2, "2026-05-01T00:00:00"),
            _task("y2", "web/web", 2, "2026-05-02T00:00:00"),
        ]
        groups = so.group_same_origin(tasks, ["web/web"])
        self.assertEqual([g["mr_iid"] for g in groups], [2, 1])

    def test_empty_core_set_means_no_product_filter(self):
        tasks = [
            _task("a1", "any/thing", 7, "2026-06-01T00:00:00"),
            _task("a2", "any/thing", 7, "2026-06-02T00:00:00"),
        ]
        self.assertEqual(len(so.group_same_origin(tasks, [])), 1)

    def test_tasks_missing_mr_or_project_are_skipped(self):
        tasks = [
            _task("a1", "web/web", None, "2026-06-01T00:00:00"),
            _task("a2", "web/web", "", "2026-06-02T00:00:00"),
            _task("a3", "", 5, "2026-06-03T00:00:00"),
        ]
        self.assertEqual(so.group_same_origin(tasks, ["web/web"]), [])

    def test_duplicate_task_id_counted_once(self):
        # Same task appearing twice (page-overlap race) must not fabricate a
        # phantom 2-run group.
        dup = _task("a1", "web/web", 1, "2026-06-01T00:00:00")
        self.assertEqual(so.group_same_origin([dup, dict(dup)], ["web/web"]), [])

    def test_mixed_type_mr_iid_merges_same_mr(self):
        # int 40461 and str "40461" are the same MR → must group together.
        tasks = [
            _task("a1", "web/web", 40461, "2026-06-15T00:00:00"),
            _task("a2", "web/web", "40461", "2026-06-17T00:00:00"),
        ]
        groups = so.group_same_origin(tasks, ["web/web"])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["task_count"], 2)

    def test_deterministic_order_on_tied_created_at(self):
        # identical created_at → stable, reproducible order via task_id tiebreak
        a = _task("zzz", "web/web", 1, "2026-06-01T00:00:00")
        b = _task("aaa", "web/web", 1, "2026-06-01T00:00:00")
        g1 = so.group_same_origin([a, b], ["web/web"])[0]
        g2 = so.group_same_origin([b, a], ["web/web"])[0]  # reversed input
        self.assertEqual([t["task_id"] for t in g1["tasks"]],
                         [t["task_id"] for t in g2["tasks"]])
        self.assertEqual(g1["tasks"][0]["task_id"], "aaa")  # task_id asc on tie


class ComputeDivergenceTests(unittest.TestCase):

    def setUp(self):
        self.tasks = [
            _task("a1", "web/web", 40461, "2026-06-15T14:06:48"),
            _task("a2", "web/web", 40461, "2026-06-17T14:42:26"),
        ]

    def _run(self, fake):
        return so.compute_mr_divergences(
            self.tasks, fetch_results=lambda tid: fake[tid])

    def test_text_change_flagged_identical_ignored(self):
        fake = {
            "a1": {"translations": [
                {"opus_id": "X.TEXT", "target_language": "zh-CN",
                 "translated_text": "留言", "source_text": "Message"},
                {"opus_id": "X.TITLE", "target_language": "zh-CN",
                 "translated_text": "标题", "source_text": "Title"},
            ]},
            "a2": {"translations": [
                {"opus_id": "X.TEXT", "target_language": "zh-CN",
                 "translated_text": "消息", "source_text": "Message"},
                {"opus_id": "X.TITLE", "target_language": "zh-CN",
                 "translated_text": "标题", "source_text": "Title"},
            ]},
        }
        res = self._run(fake)
        self.assertEqual(res["total_keys"], 2)
        self.assertEqual(res["total_divergent"], 1)
        self.assertEqual(res["locales"], ["zh-CN"])
        d = res["by_locale"]["zh-CN"][0]
        self.assertEqual(d["opus_id"], "X.TEXT")
        self.assertEqual(d["changed_kind"], "text")
        self.assertEqual([v["text"] for v in d["versions"]], ["留言", "消息"])
        self.assertEqual(d["source_text"], "Message")

    def test_missing_vs_present_empty_not_flagged(self):
        # key absent in a1, present-but-empty in a2 → both mean "no
        # translation" → must NOT be flagged as a divergence.
        fake = {
            "a1": {"translations": []},
            "a2": {"translations": [
                {"opus_id": "K", "target_language": "de-DE",
                 "translated_text": "   ", "source_text": "Hi"}]},
        }
        res = self._run(fake)
        self.assertEqual(res["locales"], [])
        self.assertEqual(res["total_divergent"], 0)

    def test_added_removed_key(self):
        fake = {
            "a1": {"translations": [
                {"opus_id": "K", "target_language": "de-DE",
                 "translated_text": "Hallo", "source_text": "Hi"}]},
            "a2": {"translations": []},
        }
        d = self._run(fake)["by_locale"]["de-DE"][0]
        self.assertEqual(d["changed_kind"], "added_removed")
        self.assertTrue(d["versions"][0]["present"])
        self.assertFalse(d["versions"][1]["present"])

    def test_no_divergence_returns_empty_locales(self):
        fake = {
            "a1": {"translations": [
                {"opus_id": "K", "target_language": "fr-FR",
                 "translated_text": "Bonjour", "source_text": "Hi"}]},
            "a2": {"translations": [
                {"opus_id": "K", "target_language": "fr-FR",
                 "translated_text": "Bonjour", "source_text": "Hi"}]},
        }
        res = self._run(fake)
        self.assertEqual(res["locales"], [])
        self.assertEqual(res["total_divergent"], 0)
        self.assertEqual(res["task_count"], 2)

    def test_locales_ordered_by_divergence_count(self):
        # de-DE has 2 divergent strings, zh-CN has 1 → de-DE listed first.
        fake = {
            "a1": {"translations": [
                {"opus_id": "A", "target_language": "de-DE",
                 "translated_text": "1", "source_text": "s"},
                {"opus_id": "B", "target_language": "de-DE",
                 "translated_text": "2", "source_text": "s"},
                {"opus_id": "C", "target_language": "zh-CN",
                 "translated_text": "3", "source_text": "s"},
            ]},
            "a2": {"translations": [
                {"opus_id": "A", "target_language": "de-DE",
                 "translated_text": "1x", "source_text": "s"},
                {"opus_id": "B", "target_language": "de-DE",
                 "translated_text": "2x", "source_text": "s"},
                {"opus_id": "C", "target_language": "zh-CN",
                 "translated_text": "3x", "source_text": "s"},
            ]},
        }
        res = self._run(fake)
        self.assertEqual(res["locales"], ["de-DE", "zh-CN"])

    def test_failed_fetch_excluded_not_false_divergence(self):
        # a2 fetch raises → must be excluded, NOT treated as "key missing"
        # (which would false-flag every a1 key as added/removed).
        def fetch(tid):
            if tid == "a2":
                raise RuntimeError("network down")
            return {"translations": [
                {"opus_id": "K", "target_language": "ja-JP",
                 "translated_text": "v1", "source_text": "s"}]}
        res = so.compute_mr_divergences(self.tasks, fetch_results=fetch)
        self.assertTrue(res["insufficient"])      # only 1 run fetched
        self.assertEqual(res["failed_count"], 1)
        self.assertEqual(res["group_task_count"], 2)
        self.assertEqual(res["locales"], [])      # no false divergence
        self.assertEqual(res["total_divergent"], 0)

    def test_partial_failure_compares_the_rest(self):
        tasks = [
            _task("a1", "web/web", 1, "2026-06-15T00:00:00"),
            _task("a2", "web/web", 1, "2026-06-16T00:00:00"),
            _task("a3", "web/web", 1, "2026-06-17T00:00:00"),
        ]

        def fetch(tid):
            if tid == "a2":
                raise RuntimeError("boom")
            text = "x" if tid == "a1" else "y"
            return {"translations": [
                {"opus_id": "K", "target_language": "ko-KR",
                 "translated_text": text, "source_text": "s"}]}
        res = so.compute_mr_divergences(tasks, fetch_results=fetch)
        self.assertFalse(res["insufficient"])
        self.assertEqual(res["failed_count"], 1)
        self.assertEqual(res["task_count"], 2)        # a1 + a3 compared
        self.assertEqual(res["locales"], ["ko-KR"])   # x vs y diverges
        d = res["by_locale"]["ko-KR"][0]
        self.assertEqual([v["text"] for v in d["versions"]], ["x", "y"])

    def test_three_run_version_chain(self):
        tasks = [
            _task("a1", "web/web", 1, "2026-06-15T00:00:00"),
            _task("a2", "web/web", 1, "2026-06-16T00:00:00"),
            _task("a3", "web/web", 1, "2026-06-17T00:00:00"),
        ]
        fake = {
            "a1": {"translations": [{"opus_id": "K", "target_language": "it-IT",
                                     "translated_text": "v1", "source_text": "s"}]},
            "a2": {"translations": [{"opus_id": "K", "target_language": "it-IT",
                                     "translated_text": "v2", "source_text": "s"}]},
            "a3": {"translations": [{"opus_id": "K", "target_language": "it-IT",
                                     "translated_text": "v3", "source_text": "s"}]},
        }
        res = so.compute_mr_divergences(tasks, fetch_results=lambda tid: fake[tid])
        d = res["by_locale"]["it-IT"][0]
        self.assertEqual([v["text"] for v in d["versions"]], ["v1", "v2", "v3"])
        self.assertEqual(d["distinct"], 3)


class DiffRunsTests(unittest.TestCase):

    def test_reconstructs_and_tags(self):
        runs = so.diff_runs("color screen EXP55", "color screen")
        kinds = {k for k, _ in runs}
        self.assertIn("delete", kinds)
        self.assertIn("equal", kinds)
        # the deleted span carries the removed text
        deleted = "".join(txt for k, txt in runs if k == "delete")
        self.assertIn("EXP55", deleted)

    def test_pure_insertion(self):
        runs = so.diff_runs("abc", "abc def")
        self.assertEqual([k for k, _ in runs if k == "delete"], [])
        self.assertTrue(any(k == "insert" for k, _ in runs))


class ScanTests(unittest.TestCase):

    def _tasks(self, n):
        out = []
        # two repeat groups + filler singletons
        out += [_task("g1a", "web/web", 1, "2026-06-01T00:00:00"),
                _task("g1b", "web/web", 1, "2026-06-02T00:00:00")]
        for i in range(n):
            out.append(_task(f"s{i}", "web/web", 1000 + i, f"2026-06-03T00:00:0{i%10}"))
        return out

    def test_paginated_fetch_and_grouping(self):
        tasks = self._tasks(8)  # 10 total

        def fetch(status=None, limit=100, offset=0):
            return len(tasks), tasks[offset:offset + limit]

        res = so.scan_same_origin_groups(["web/web"], fetch_tasks=fetch,
                                         page_size=3)
        self.assertEqual(res["total"], 10)
        self.assertEqual(res["scanned"], 10)
        self.assertFalse(res["truncated"])
        self.assertEqual(len(res["groups"]), 1)
        self.assertEqual(res["groups"][0]["mr_iid"], 1)

    def test_max_pages_truncates(self):
        tasks = self._tasks(18)  # 20 total

        def fetch(status=None, limit=100, offset=0):
            return len(tasks), tasks[offset:offset + limit]

        res = so.scan_same_origin_groups(["web/web"], fetch_tasks=fetch,
                                         page_size=5, max_pages=2)
        # only 2 pages * 5 = 10 of 20 scanned
        self.assertEqual(res["scanned"], 10)
        self.assertTrue(res["truncated"])

    def test_under_reported_total_still_scans_all(self):
        # Backend says total=3 but 10 rows exist → must still scan all 10
        # (sequential tail catches it), and not claim truncated.
        tasks = self._tasks(8)  # 10 rows

        def fetch(status=None, limit=100, offset=0):
            return 3, tasks[offset:offset + limit]  # lying total

        res = so.scan_same_origin_groups(["web/web"], fetch_tasks=fetch,
                                         page_size=5)
        self.assertEqual(res["scanned"], 10)
        self.assertFalse(res["truncated"])
        self.assertEqual(len(res["groups"]), 1)

    def test_zero_total_with_full_first_page_scans_all(self):
        # Backend omits total (→0) but returns a full first page → must keep
        # paging instead of stopping after page 1.
        tasks = self._tasks(2)  # 4 rows

        def fetch(status=None, limit=100, offset=0):
            return 0, tasks[offset:offset + limit]

        res = so.scan_same_origin_groups(["web/web"], fetch_tasks=fetch,
                                         page_size=2)
        self.assertEqual(res["scanned"], 4)
        self.assertFalse(res["truncated"])


class CoreProductsConfigTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = os.path.join(self._tmp.name, "core_products.json")
        self._patch = mock.patch.object(so, "config_path",
                                        return_value=self._path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_default_when_no_file(self):
        self.assertEqual(so.load_core_products(), list(so.DEFAULT_CORE_PRODUCTS))
        self.assertTrue(so.is_default_core_products(so.load_core_products()))

    def test_round_trip_with_normalization(self):
        saved = so.save_core_products(
            ["  web/web ", "web/web", "", "common/uns", None])  # dup + blank + None
        self.assertEqual(saved, ["web/web", "common/uns"])
        self.assertEqual(so.load_core_products(), ["web/web", "common/uns"])
        self.assertFalse(so.is_default_core_products(saved))

    def test_empty_save_rejected(self):
        with self.assertRaises(ValueError):
            so.save_core_products(["", "  ", None])

    def test_corrupt_file_falls_back_to_default(self):
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(so.load_core_products(), list(so.DEFAULT_CORE_PRODUCTS))

    def test_default_list_has_no_dupes_and_matches_request(self):
        self.assertEqual(len(so.DEFAULT_CORE_PRODUCTS),
                         len(set(so.DEFAULT_CORE_PRODUCTS)))
        self.assertEqual(len(so.DEFAULT_CORE_PRODUCTS), 26)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
