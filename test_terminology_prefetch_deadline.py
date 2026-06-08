"""Unit tests for the bounded terminology prefetch in export_mr_pipeline.

The HTML export loads the full glossary from the context-service (sequential
pagination, 30s timeout/page). If that service is slow/unreachable the load
runs for minutes and the export hangs at "Exporting...". _prefetch_terminology_
bounded caps it so the export always completes; highlighting just degrades.

Run:  python -m unittest test_terminology_prefetch_deadline
"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_mr_pipeline as mp
import terminology_highlight as th


class TestBoundedPrefetch(unittest.TestCase):
    def setUp(self):
        self._orig = th.prefetch_for_rows

    def tearDown(self):
        th.prefetch_for_rows = self._orig

    def test_returns_at_deadline_when_prefetch_slow(self):
        """The whole point: a slow prefetch (slow context-service) must not
        block the export beyond the deadline."""
        calls = {"n": 0}

        def slow(rows, **kw):
            calls["n"] += 1
            time.sleep(3)

        th.prefetch_for_rows = slow
        t0 = time.monotonic()
        mp._prefetch_terminology_bounded([{"source_text": "x"}], deadline_s=0.3)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.5,
                        "must return ~deadline, not wait for the slow prefetch")
        self.assertEqual(calls["n"], 1, "prefetch should still have been started")

    def test_fast_prefetch_completes_and_passes_rows(self):
        captured = {"rows": None}

        def fast(rows, **kw):
            captured["rows"] = rows

        th.prefetch_for_rows = fast
        rows = [{"source_text": "hi", "target_language": "fr-CA"}]
        t0 = time.monotonic()
        mp._prefetch_terminology_bounded(rows, deadline_s=5)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(captured["rows"], rows)

    def test_prefetch_exception_is_swallowed(self):
        def boom(rows, **kw):
            raise RuntimeError("context-service down")

        th.prefetch_for_rows = boom
        # Must not raise — a failing prefetch can never break the export.
        mp._prefetch_terminology_bounded([{"source_text": "x"}], deadline_s=2)


if __name__ == "__main__":
    unittest.main()
