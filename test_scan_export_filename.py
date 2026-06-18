"""Tests for the Scan Tasks export filename builder.

The same scan task can be re-run at different times. The export filename
used to stamp only the *export* date — identical for every same-day export —
so two exports of the same scan task collided in name and were impossible to
tell apart. ``ScanTasksTab._build_export_filename`` now stamps each task's
*Created* (trigger) time so per-run files stay distinct and recognizable,
mirroring the MR Pipeline fix; with no Created time it falls back to the
export date.

Run:  python -m unittest test_scan_export_filename
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui_tab_scan_tasks import ScanTasksTab

_build = ScanTasksTab._build_export_filename


class ScanCreatedTimeStampTests(unittest.TestCase):

    def test_created_time_replaces_export_date(self):
        name = _build(
            ".json", task_name="iva 260520", id_tag="75040f78",
            type_tag="all", created="2026-06-17 14:42:26",
            export_date="2026-06-18")
        self.assertEqual(
            name, "scan_task_iva_260520_75040f78_all_2026-06-17_14-42-26.json")

    def test_same_task_same_export_day_distinct_by_created(self):
        common = dict(task_name="iva 260520", type_tag="all",
                      export_date="2026-06-18")
        a = _build(".json", id_tag="75040f78",
                   created="2026-06-17 14:42:26", **common)
        b = _build(".json", id_tag="a1b2c3d4",
                   created="2026-06-15 14:06:48", **common)
        self.assertNotEqual(a, b)
        self.assertIn("2026-06-17", a)
        self.assertIn("2026-06-15", b)

    def test_same_day_different_time_still_distinct(self):
        common = dict(task_name="scan", id_tag="75040f78", type_tag="all",
                      export_date="2026-06-18")
        a = _build(".json", created="2026-06-18 09:01:02", **common)
        b = _build(".json", created="2026-06-18 17:30:59", **common)
        self.assertNotEqual(a, b)

    def test_no_created_falls_back_to_export_date(self):
        name = _build(
            ".json", task_name="iva", id_tag="75040f78", type_tag="changes",
            created="", export_date="2026-06-18")
        self.assertEqual(
            name, "scan_task_iva_75040f78_changes_2026-06-18.json")

    def test_all_formats_carry_the_date_segment(self):
        for ext in (".html", ".xlsx", ".json"):
            name = _build(
                ext, task_name="iva", id_tag="75040f78", type_tag="changes",
                created="2026-06-17 14:42:26", export_date="2026-06-18")
            self.assertTrue(name.endswith(ext))
            self.assertIn("2026-06-17_14-42-26", name)

    def test_no_filesystem_illegal_chars(self):
        name = _build(
            ".json", task_name="iva", id_tag="75040f78", type_tag="all",
            created="2026-06-17 14:42:26", export_date="2026-06-18")
        for bad in '<>:"/\\|?*':
            self.assertNotIn(bad, name)

    def test_name_tag_omitted_when_no_task_name(self):
        name = _build(
            ".json", task_name="", id_tag="75040f78", type_tag="changes",
            created="2026-06-17 14:42:26", export_date="2026-06-18")
        self.assertTrue(name.startswith("scan_task_75040f78_"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
