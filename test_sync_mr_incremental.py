"""Tests for tranzor_checks.sync_mr_incremental (PR-H).

The Review Worklist's "Sync & refresh" button calls this. The contract
that matters most: it uses its OWN watermark (last_mr_sync_at) and must
never touch the shared last_sync_at that the Tranzor Checks tab relies
on for its three-channel incremental sync — otherwise Worklist refreshes
would silently make the Checks tab skip scan/legacy tasks.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tranzor_checks as tc


class _IsolatedDb:
    def __enter__(self):
        self._tmp = tempfile.mkdtemp(prefix="syncmr-test-")
        self.path = os.path.join(self._tmp, "checks_index.db")
        self._orig = tc._default_db_path
        tc._default_db_path = lambda: self.path
        return self

    def __exit__(self, *exc):
        tc._default_db_path = self._orig
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(self._tmp)
        except OSError:
            pass


class SyncMrIncrementalTests(unittest.TestCase):

    def _capture_since(self):
        """Patch _sync_mr_tasks to record the since_iso it was handed."""
        captured = {}

        def _fake(conn, *, since_iso, progress_callback=None,
                  cancel_event=None):
            captured["since"] = since_iso
            return {"tasks_seen": 0, "rows_total": 0, "issues_inserted": 0}

        return captured, mock.patch.object(
            tc, "_sync_mr_tasks", side_effect=_fake)

    def test_does_not_read_or_write_shared_last_sync_at(self):
        with _IsolatedDb():
            tc.init_db()
            with tc._connect() as conn:
                tc._set_meta(conn, "last_sync_at",
                             "2020-01-01T00:00:00+00:00")
            captured, patch = self._capture_since()
            with patch:
                tc.sync_mr_incremental()
            with tc._connect() as conn:
                # Shared watermark untouched.
                self.assertEqual(
                    tc._get_meta(conn, "last_sync_at"),
                    "2020-01-01T00:00:00+00:00",
                )
                # Own watermark written.
                self.assertIsNotNone(
                    tc._get_meta(conn, "last_mr_sync_at"))
            # And it did NOT use the shared watermark as its since —
            # first run must use the 14-day lookback, not 2020.
            self.assertNotEqual(captured["since"], "2020-01-01T00:00:00+00:00")

    def test_first_run_uses_14_day_lookback(self):
        with _IsolatedDb():
            tc.init_db()
            captured, patch = self._capture_since()
            with patch:
                tc.sync_mr_incremental()
            since = captured["since"]
            self.assertIsInstance(since, str)
            self.assertIn("T", since)  # ISO datetime
            # Roughly 14 days back — assert it's clearly in the past, not
            # "now" (which would mean we forgot the window).
            from datetime import datetime, timezone, timedelta
            ts = datetime.fromisoformat(since)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - ts
            self.assertGreater(delta, timedelta(days=13))
            self.assertLess(delta, timedelta(days=15))

    def test_second_run_uses_stored_watermark(self):
        with _IsolatedDb():
            tc.init_db()
            with tc._connect() as conn:
                tc._set_meta(conn, "last_mr_sync_at",
                             "2026-05-20T00:00:00+00:00")
            captured, patch = self._capture_since()
            with patch:
                tc.sync_mr_incremental()
            self.assertEqual(captured["since"], "2026-05-20T00:00:00+00:00")

    def test_cancel_does_not_advance_watermark(self):
        with _IsolatedDb():
            tc.init_db()
            ev = threading.Event()
            ev.set()
            with mock.patch.object(tc, "_sync_mr_tasks", return_value={}):
                tc.sync_mr_incremental(cancel_event=ev)
            with tc._connect() as conn:
                # Cancelled before completion → don't move the watermark,
                # so the next run retries the same window.
                self.assertIsNone(
                    tc._get_meta(conn, "last_mr_sync_at"))

    def test_progress_callback_forwarded(self):
        with _IsolatedDb():
            tc.init_db()
            seen = {}

            def _fake(conn, *, since_iso, progress_callback=None,
                      cancel_event=None):
                seen["cb"] = progress_callback
                return {}

            with mock.patch.object(tc, "_sync_mr_tasks", side_effect=_fake):
                sentinel = lambda *a, **k: None
                tc.sync_mr_incremental(progress_callback=sentinel)
            self.assertIs(seen["cb"], sentinel)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
