"""Regression tests for the "complete or fail loud" hardening of the Full
Translation export (export_full_translations).

Background (the "filmstrip missing" RCA): a translation that lives ONLY in the
newest run of an MR can vanish from the aggregated "full" export with zero
trace, because:

  1. Each per-task ``/results`` fetch ran in a thread pool and ANY exception
     was swallowed (the worker returned 0 rows) — so a transient miss dropped
     a whole run, and any key unique to that run disappeared entirely.
  2. Aggregation was last-write-wins by *thread-completion order*, so it never
     preferred the newest translation and never surfaced a lost run.

The fix, exercised here:
  - Per-task fetches retry with backoff; after retries a failure is RECORDED
    (inv.fetch_failures) and, in strict mode, raises IncompleteExportError so
    the export never silently emits a short file.
  - Ingest is deterministic "newest wins" by task created_at, independent of
    thread order.
  - Task-list pagination de-duplicates by id so head-insertion drift can't
    double-count (and the failure accounting stays honest).

Run:  python -m unittest test_export_full_translations_complete
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_full_translations as ft


PRODUCT = "webModule"
HASH = "dba1c95d7993e5d977b34efe8eaa9ed2"


def _oid(key: str) -> str:
    return f"RingCentral.{PRODUCT}.{HASH}.app.rooms.{key}"


def _tr(key: str, lang: str, text: str, src: str | None = None) -> dict:
    return {
        "opus_id": _oid(key),
        "source_text": src if src is not None else f"src-{key}",
        "target_language": lang,
        "translated_text": text,
    }


def _mr_task(task_id: str, created_at: str, iid: int = 40260) -> dict:
    return {
        "task_id": task_id,
        "project_id": "web/web",
        "merge_request_iid": iid,
        "release": "Release 26.3",
        "created_at": created_at,
    }


class _MRBackend:
    """Fake MR Pipeline endpoints.

    tasks:   list of task dicts returned by the (paginated) list endpoint.
    results: {task_id: translations-list}.
    fail:    {task_id: remaining_failure_count} — fetch_mr_results raises this
             many times before succeeding (models transient failures).
    always_fail: set of task_ids whose fetch always raises (permanent).
    """

    def __init__(self, tasks, results, fail=None, always_fail=None,
                 page_size_pages=None):
        self.tasks = tasks
        self.results = results
        self.fail = dict(fail or {})
        self.always_fail = set(always_fail or set())
        self.results_calls = []
        # Optional: explicit list of pages (each a list of tasks) to return for
        # successive offsets, to simulate pagination drift / duplicates.
        self.page_size_pages = page_size_pages

    def fetch_mr_tasks(self, project_id=None, status=None, limit=50, offset=0):
        if self.page_size_pages is not None:
            total = sum(len(p) for p in self.page_size_pages)
            # Map offset -> page index by cumulative length.
            cum = 0
            for page in self.page_size_pages:
                if offset == cum:
                    return total, list(page)
                cum += len(page)
            return total, []
        # Default single-page behaviour.
        total = len(self.tasks)
        batch = self.tasks[offset:offset + limit]
        return total, batch

    def fetch_mr_results(self, task_id, target_language=None,
                         min_score=None, max_score=None):
        self.results_calls.append(task_id)
        if task_id in self.always_fail:
            raise RuntimeError(f"boom-permanent {task_id}")
        left = self.fail.get(task_id, 0)
        if left > 0:
            self.fail[task_id] = left - 1
            raise RuntimeError(f"boom-transient {task_id} ({left} left)")
        return {"translations": list(self.results.get(task_id, [])),
                "summary": {}}


class FullExportCompleteTest(unittest.TestCase):
    def setUp(self):
        # Keep retries small and instant so tests are fast.
        self._orig_retries = ft._TASK_FETCH_RETRIES
        self._orig_backoff = ft._TASK_FETCH_BACKOFF
        self._orig_allow = ft._ALLOW_PARTIAL
        self._orig_sleep = ft.time.sleep
        ft._TASK_FETCH_RETRIES = 3
        ft._TASK_FETCH_BACKOFF = 0.0
        ft._ALLOW_PARTIAL = False
        ft.time.sleep = lambda *_a, **_k: None
        self._orig_mr = ft._mr

    def tearDown(self):
        ft._TASK_FETCH_RETRIES = self._orig_retries
        ft._TASK_FETCH_BACKOFF = self._orig_backoff
        ft._ALLOW_PARTIAL = self._orig_allow
        ft.time.sleep = self._orig_sleep
        ft._mr = self._orig_mr

    def _install(self, backend):
        ft._mr = backend

    def _collect(self, strict=False):
        return ft.collect_full_translations(
            sources=["mr"], progress_cb=lambda *_: None,
            strict_complete=strict)

    # ---- retry --------------------------------------------------------
    def test_transient_failure_is_retried_then_succeeds(self):
        t = _mr_task("task-A", "2026-06-11T04:00:00")
        be = _MRBackend(
            tasks=[t],
            results={"task-A": [_tr("FILM_STRIP", "zh-HK", "縮圖列")]},
            fail={"task-A": 2},  # fail twice, succeed on 3rd attempt
        )
        self._install(be)
        inv = self._collect(strict=True)
        self.assertEqual(inv.fetch_failures, [])
        self.assertEqual(be.results_calls.count("task-A"), 3)
        self.assertEqual(
            inv.data[PRODUCT]["zh-HK"][_oid("FILM_STRIP")], "縮圖列")

    # ---- permanent failure: loud, never silent -----------------------
    def test_permanent_failure_strict_raises_with_task_list(self):
        be = _MRBackend(
            tasks=[_mr_task("task-A", "2026-06-11T04:00:00")],
            results={}, always_fail={"task-A"})
        self._install(be)
        with self.assertRaises(ft.IncompleteExportError) as ctx:
            self._collect(strict=True)
        self.assertEqual(len(ctx.exception.failures), 1)
        self.assertEqual(ctx.exception.failures[0]["task_id"], "task-A")
        self.assertEqual(ctx.exception.failures[0]["source"], "MR")

    def test_permanent_failure_nonstrict_records_but_does_not_raise(self):
        be = _MRBackend(
            tasks=[_mr_task("task-A", "2026-06-11T04:00:00")],
            results={}, always_fail={"task-A"})
        self._install(be)
        inv = self._collect(strict=False)  # watchtower-style caller
        self.assertEqual(len(inv.fetch_failures), 1)
        self.assertEqual(inv.fetch_failures[0]["task_id"], "task-A")

    def test_allow_partial_env_downgrades_strict_to_warning(self):
        ft._ALLOW_PARTIAL = True
        be = _MRBackend(
            tasks=[_mr_task("task-A", "2026-06-11T04:00:00")],
            results={}, always_fail={"task-A"})
        self._install(be)
        inv = self._collect(strict=True)  # would normally raise
        self.assertEqual(len(inv.fetch_failures), 1)

    # ---- empty result is not a failure -------------------------------
    def test_empty_result_is_not_a_failure(self):
        be = _MRBackend(
            tasks=[_mr_task("task-A", "2026-06-11T04:00:00")],
            results={"task-A": []})
        self._install(be)
        inv = self._collect(strict=True)
        self.assertEqual(inv.fetch_failures, [])
        self.assertEqual(inv.total_entries(), 0)

    # ---- deterministic newest-wins -----------------------------------
    def test_newest_run_wins_value_and_source(self):
        older = _mr_task("task-old", "2026-06-09T09:00:00")
        newer = _mr_task("task-new", "2026-06-11T04:00:00")
        be = _MRBackend(
            tasks=[older, newer],
            results={
                "task-old": [_tr("SINGLE_SCREEN_SETTINGS", "zh-HK", "舊譯")],
                "task-new": [_tr("SINGLE_SCREEN_SETTINGS", "zh-HK", "新譯")],
            })
        self._install(be)
        inv = self._collect(strict=True)
        oid = _oid("SINGLE_SCREEN_SETTINGS")
        self.assertEqual(inv.data[PRODUCT]["zh-HK"][oid], "新譯")
        self.assertEqual(
            inv.sources[PRODUCT]["zh-HK"][oid]["task_id"], "task-new")

    # ---- the RCA scenario --------------------------------------------
    def test_key_unique_to_newest_run_is_present_when_fetch_succeeds(self):
        # newest run added FILM_STRIP keys; both runs share SINGLE_SCREEN.
        older = _mr_task("86a713fa", "2026-06-10T12:00:00")
        newer = _mr_task("4e25123c", "2026-06-11T04:00:00")
        be = _MRBackend(
            tasks=[older, newer],
            results={
                "86a713fa": [_tr("SINGLE_SCREEN_SETTINGS_DESCRIPTION",
                                 "zh-HK", "舊", src="Apply these settings")],
                "4e25123c": [
                    _tr("SINGLE_SCREEN_SETTINGS_DESCRIPTION", "zh-HK", "新",
                        src="Apply these settings"),
                    _tr("CONTENT_WITH_FILM_STRIP", "zh-HK", "內容連同縮圖列",
                        src="Content with filmstrip"),
                ],
            })
        self._install(be)
        inv = self._collect(strict=True)
        # filmstrip key present, with its en-US source copy too.
        self.assertIn(_oid("CONTENT_WITH_FILM_STRIP"),
                      inv.data[PRODUCT]["zh-HK"])
        self.assertEqual(
            inv.data[PRODUCT]["en-US"][_oid("CONTENT_WITH_FILM_STRIP")],
            "Content with filmstrip")

    def test_key_unique_to_newest_run_failing_raises_not_silent(self):
        # The exact regression: the newest run carries the only copy of the
        # filmstrip key, and its fetch fails. Old behaviour: key silently
        # vanishes. New behaviour: strict export refuses to emit.
        older = _mr_task("86a713fa", "2026-06-10T12:00:00")
        newer = _mr_task("4e25123c", "2026-06-11T04:00:00")
        be = _MRBackend(
            tasks=[older, newer],
            results={
                "86a713fa": [_tr("SINGLE_SCREEN_SETTINGS_DESCRIPTION",
                                 "zh-HK", "舊")],
            },
            always_fail={"4e25123c"})
        self._install(be)
        with self.assertRaises(ft.IncompleteExportError) as ctx:
            self._collect(strict=True)
        self.assertEqual(ctx.exception.failures[0]["task_id"], "4e25123c")

    # ---- pagination de-dup -------------------------------------------
    def test_paginated_task_list_is_deduped(self):
        # Simulate head-insertion drift: the task that was last on page 1
        # reappears first on page 2 (a duplicate). It must be fetched once.
        a = _mr_task("task-A", "2026-06-11T04:00:00")
        b = _mr_task("task-B", "2026-06-10T04:00:00")
        c = _mr_task("task-C", "2026-06-09T04:00:00")
        # page1=[A,B], page2=[B,C] -> B duplicated across the boundary.
        be = _MRBackend(
            tasks=[], results={
                "task-A": [_tr("K_A", "zh-HK", "a")],
                "task-B": [_tr("K_B", "zh-HK", "b")],
                "task-C": [_tr("K_C", "zh-HK", "c")],
            },
            page_size_pages=[[a, b], [b, c]])
        self._install(be)
        inv = self._collect(strict=True)
        self.assertEqual(be.results_calls.count("task-B"), 1)
        self.assertEqual(sorted(be.results_calls),
                         ["task-A", "task-B", "task-C"])
        # All three distinct keys present.
        for k in ("K_A", "K_B", "K_C"):
            self.assertIn(_oid(k), inv.data[PRODUCT]["zh-HK"])


if __name__ == "__main__":
    unittest.main()
