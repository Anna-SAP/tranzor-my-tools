"""Tests for the MR Pipeline export filename builder.

The same Project/MR is re-translated at different times, producing several
tasks. The export filename used to stamp only the *export* date — identical
for every same-day export — so two exports of the same MR collided in name
and were impossible to tell apart. ``MRPipelineTab._build_export_filename``
now stamps each task's *Created* time so per-run files stay distinct and
human-recognizable; the no-selection "export all" aggregate (no single
Created time) falls back to the export date.

Run:  python -m unittest test_mr_export_filename
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui_tabs import MRPipelineTab

_build = MRPipelineTab._build_export_filename


class CreatedTimeStampTests(unittest.TestCase):

    def test_created_time_replaces_export_date(self):
        # Selected single task → its Created timestamp drives the date
        # segment, not the export date. Colons/space are sanitized.
        name = _build(
            ".json", mr_iid="40461", id_tag="75040f78", type_tag="all",
            created="2026-06-17 14:42:26", export_date="2026-06-18")
        self.assertEqual(
            name, "mr_pipeline_MR40461_75040f78_all_2026-06-17_14-42-26.json")

    def test_same_mr_same_export_day_distinct_by_created(self):
        # The headline bug: two runs of the same MR exported on the same day
        # must yield different filenames.
        common = dict(mr_iid="40461", type_tag="all", export_date="2026-06-18")
        a = _build(".json", id_tag="75040f78",
                   created="2026-06-17 14:42:26", **common)
        b = _build(".json", id_tag="a1b2c3d4",
                   created="2026-06-15 14:06:48", **common)
        self.assertNotEqual(a, b)
        self.assertIn("2026-06-17", a)
        self.assertIn("2026-06-15", b)

    def test_same_day_different_time_still_distinct(self):
        # Two triggers on the *same* day are disambiguated by the time
        # component down to the second.
        common = dict(mr_iid="40461", id_tag="75040f78", type_tag="all",
                      export_date="2026-06-18")
        a = _build(".json", created="2026-06-18 09:01:02", **common)
        b = _build(".json", created="2026-06-18 17:30:59", **common)
        self.assertNotEqual(a, b)

    def test_no_created_falls_back_to_export_date(self):
        # "Export all" aggregate: no single Created time → export date.
        name = _build(
            ".json", mr_iid="", id_tag="all_web-web", type_tag="all",
            created="", export_date="2026-06-18")
        self.assertEqual(
            name, "mr_pipeline_all_web-web_all_2026-06-18.json")

    def test_all_formats_carry_the_date_segment(self):
        for ext in (".html", ".xlsx", ".json"):
            name = _build(
                ext, mr_iid="40461", id_tag="75040f78", type_tag="changes",
                created="2026-06-17 14:42:26", export_date="2026-06-18")
            self.assertTrue(name.endswith(ext))
            self.assertIn("2026-06-17_14-42-26", name)

    def test_no_filesystem_illegal_chars(self):
        # The Created string contains ':' (illegal on Windows); the result
        # must be a safe filename.
        name = _build(
            ".json", mr_iid="40461", id_tag="75040f78", type_tag="all",
            created="2026-06-17 14:42:26", export_date="2026-06-18")
        for bad in '<>:"/\\|?*':
            self.assertNotIn(bad, name)

    def test_created_tz_label_survives_sanitize(self):
        # GUI Created cells now carry "UTC+8"; the filename must keep the
        # offset and still be Windows-safe ('+' is legal, ':' is not).
        name = _build(
            ".json", mr_iid="40461", id_tag="75040f78", type_tag="all",
            created="2026-06-17 14:42:26 UTC+8", export_date="2026-06-18")
        self.assertIn("UTC+8", name)
        self.assertIn("2026-06-17_14-42-26", name)
        for bad in '<>:"/\\|?*':
            self.assertNotIn(bad, name)

    def test_mr_tag_omitted_when_no_iid(self):
        name = _build(
            ".json", mr_iid="", id_tag="75040f78", type_tag="changes",
            created="2026-06-17 14:42:26", export_date="2026-06-18")
        self.assertFalse(name.startswith("mr_pipeline_MR"))
        self.assertTrue(name.startswith("mr_pipeline_75040f78_"))

    def test_stage_env_tag_disambiguates_from_prod(self):
        common = dict(mr_iid="53", id_tag="abc12345", type_tag="all",
                      created="2026-08-18 10:00:00", export_date="2026-08-18")
        prod = _build(".html", **common)
        stage = _build(".html", env_tag="stage", **common)
        self.assertNotEqual(prod, stage)
        self.assertTrue(stage.startswith("mr_pipeline_stage_"))
        self.assertTrue(prod.startswith("mr_pipeline_MR53_"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
