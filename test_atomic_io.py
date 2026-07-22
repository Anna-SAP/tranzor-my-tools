"""Regression tests for cross-process-safe atomic configuration writes."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import atomic_io


class AtomicWriteTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "shared.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_concurrent_writers_publish_one_complete_json_document(self):
        def _write(index):
            atomic_io.atomic_write_json(
                self.path,
                {"writer": index, "payload": "x" * 4096},
                indent=2,
            )

        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(_write, range(60)))

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn(payload["writer"], range(60))
        self.assertEqual(payload["payload"], "x" * 4096)
        leftovers = [
            p for p in Path(self.temp_dir.name).iterdir()
            if p != self.path
        ]
        self.assertEqual(leftovers, [])

    def test_failed_replace_cleans_unique_temp_file(self):
        with mock.patch.object(os, "replace", side_effect=OSError("boom")):
            with self.assertRaisesRegex(OSError, "boom"):
                atomic_io.atomic_write_json(self.path, {"ok": True})
        self.assertEqual(list(Path(self.temp_dir.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
