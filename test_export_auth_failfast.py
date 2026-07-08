"""Unit tests for the 401 fail-fast path in export_full_translations.

Background (2026-07-08 RCA): the platform's Bearer-JWT expired mid-week, so
every list endpoint answered 401. The collectors logged a per-source warning,
carried on with zero tasks, and the GUI reported the all-zero aggregate as
"No translations matched the selection" — a misleading error for what is
really a login problem. These tests pin the new contract:

* ``_is_auth_error`` recognises a 401 both structurally (``.response``) and
  by message, and does NOT fire on unrelated failures.
* ``_fetch_task_with_retry`` raises ``AuthRequiredError`` immediately on a
  401 — no retry, no backoff sleep.
* List-level fetch failures raise ``AuthRequiredError`` when they are auth
  failures, and keep the old warn-and-continue behaviour otherwise.
* A per-task 401 propagates as ``AuthRequiredError`` instead of being
  recorded as an ordinary task failure.

Run:  python -m unittest test_export_auth_failfast
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_full_translations as ex


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


def _http_401(msg="401 Client Error: Unauthorized for url: http://x/api"):
    e = Exception(msg)
    e.response = _FakeResp(401)
    return e


class IsAuthErrorTests(unittest.TestCase):
    def test_structured_401_is_auth(self):
        self.assertTrue(ex._is_auth_error(_http_401()))

    def test_message_only_401_is_auth(self):
        self.assertTrue(ex._is_auth_error(
            Exception("401 Client Error: Unauthorized for url: http://x")))

    def test_unauthorized_word_is_auth(self):
        self.assertTrue(ex._is_auth_error(Exception("Unauthorized")))

    def test_timeout_is_not_auth(self):
        self.assertFalse(ex._is_auth_error(
            Exception("HTTPConnectionPool: Read timed out. (read timeout=30)")))

    def test_500_is_not_auth(self):
        e = Exception("500 Server Error: Internal Server Error for url")
        e.response = _FakeResp(500)
        self.assertFalse(ex._is_auth_error(e))

    def test_task_id_containing_401_is_not_auth(self):
        # Guard against the substring heuristic matching a stray number.
        self.assertFalse(ex._is_auth_error(
            Exception("task 40123 failed after 401 ms")))


class FetchTaskWithRetryTests(unittest.TestCase):
    def test_401_raises_auth_error_without_retry(self):
        calls = []

        def fetch(_tid):
            calls.append(1)
            raise _http_401()

        with mock.patch.object(ex.time, "sleep") as slept:
            with self.assertRaises(ex.AuthRequiredError):
                ex._fetch_task_with_retry(fetch, "t1")
        self.assertEqual(len(calls), 1)   # no retries burned on a dead token
        slept.assert_not_called()         # and no backoff sleeps either

    def test_transient_error_still_retries(self):
        calls = []
        logs = []  # capture the ⚠ retry line — a bare print() of U+26A0
        #            dies with UnicodeEncodeError on piped cp936 stdout

        def fetch(_tid):
            calls.append(1)
            if len(calls) < 2:
                raise Exception("Read timed out")
            return {"translations": []}

        with mock.patch.object(ex.time, "sleep"):
            out = ex._fetch_task_with_retry(
                fetch, "t1", progress_cb=logs.append)
        self.assertEqual(out, {"translations": []})
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("Read timed out" in ln for ln in logs))


class ListLevelAuthTests(unittest.TestCase):
    def test_legacy_collect_raises_on_401(self):
        inv = ex.FullTranslationInventory()
        with mock.patch.object(ex._legacy, "fetch_tasks",
                               side_effect=_http_401()):
            with self.assertRaises(ex.AuthRequiredError):
                ex._collect_from_legacy(inv, None)

    def test_legacy_collect_warns_and_continues_on_other_errors(self):
        inv = ex.FullTranslationInventory()
        logs = []
        with mock.patch.object(ex._legacy, "fetch_tasks",
                               side_effect=Exception("Read timed out")):
            self.assertEqual(ex._collect_from_legacy(inv, logs.append), 0)
        self.assertTrue(any("Read timed out" in ln for ln in logs))

    def test_mr_collect_raises_on_401(self):
        inv = ex.FullTranslationInventory()
        with mock.patch.object(ex._mr, "fetch_mr_tasks",
                               side_effect=_http_401()):
            with self.assertRaises(ex.AuthRequiredError):
                ex._collect_from_mr(inv, None)

    def test_scan_collect_raises_on_401(self):
        inv = ex.FullTranslationInventory()
        with mock.patch.object(ex._mr, "fetch_scan_tasks",
                               side_effect=_http_401()):
            with self.assertRaises(ex.AuthRequiredError):
                ex._collect_from_scan(inv, None)

    def test_light_builders_raise_on_401(self):
        with mock.patch.object(ex._legacy, "fetch_tasks",
                               side_effect=_http_401()):
            with self.assertRaises(ex.AuthRequiredError):
                ex._build_legacy_light(None)
        with mock.patch.object(ex._mr, "fetch_mr_filters_full",
                               side_effect=_http_401()):
            with self.assertRaises(ex.AuthRequiredError):
                ex._build_mr_light(None)
        with mock.patch.object(ex._mr, "fetch_scan_tasks",
                               side_effect=_http_401()):
            with self.assertRaises(ex.AuthRequiredError):
                ex._build_scan_light(None)


class PerTaskAuthTests(unittest.TestCase):
    def test_per_task_401_cancels_queued_fetches(self):
        """After the first 401 the still-queued per-task futures must be
        cancelled — draining hundreds of doomed fetches would stall the
        sign-in prompt behind an un-closable progress dialog."""
        inv = ex.FullTranslationInventory()
        logs = []
        fetched = []
        tasks = [{"id": i, "task_name": f"T{i}", "created_at": "2026-07-08"}
                 for i in range(50)]

        def fetch(tid):
            fetched.append(tid)
            raise _http_401()

        with mock.patch.object(ex._legacy, "fetch_tasks", return_value=tasks), \
                mock.patch.object(ex._legacy, "fetch_all_translations",
                                  side_effect=fetch), \
                mock.patch.object(ex, "_FETCH_WORKERS", 2), \
                mock.patch.object(ex.time, "sleep"):
            with self.assertRaises(ex.AuthRequiredError):
                ex._collect_from_legacy(inv, logs.append)
        # Only the in-flight fetches (≤ workers, plus scheduling slack) may
        # have run; the bulk of the 50 must have been cancelled unstarted.
        self.assertLess(len(fetched), 50)

    def test_per_task_401_propagates_not_recorded(self):
        """A 401 on the heavy /translations fetch must abort the run as a
        login problem — not be booked as a task failure that strict mode
        would then wrap into a confusing IncompleteExportError."""
        inv = ex.FullTranslationInventory()
        logs = []
        tasks = [{"id": 7, "task_name": "T", "created_at": "2026-07-08"}]
        with mock.patch.object(ex._legacy, "fetch_tasks", return_value=tasks), \
                mock.patch.object(ex._legacy, "fetch_all_translations",
                                  side_effect=_http_401()), \
                mock.patch.object(ex.time, "sleep"):
            with self.assertRaises(ex.AuthRequiredError):
                ex._collect_from_legacy(inv, logs.append)
        self.assertEqual(inv.fetch_failures, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
