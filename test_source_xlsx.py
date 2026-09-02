"""Tests for the MR Pipeline "Export Source XLSX" schema.

The workbook is a source-only companion to the QA JSON export:

  * three columns: Key / en-US Value / task name
  * one sheet named ``{JIRA} MR!{iid}`` (e.g. ``BUG-352 MR!4103``)
  * UNS email segments keep the ``:::seg:::{tu_id}`` key shape
  * task name is the companion All-Translations JSON filename

Run:  python -m unittest test_source_xlsx
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_json as ej
from gui_tabs import MRPipelineTab

_build = MRPipelineTab._build_export_filename

try:
    from openpyxl import load_workbook
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


SEG_ROWS = [
    {"opus_id": "common.uns.announcementsOnlyLoginInfo__email_html__3460",
     "has_seg_units": True, "tu_id": 10, "target_language": "de-DE",
     "source_text": "Click on login", "translated_text": "Klicken Sie"},
    {"opus_id": "common.uns.announcementsOnlyLoginInfo__email_html__3460",
     "has_seg_units": True, "tu_id": 4, "target_language": "zh-CN",
     "source_text": "Dear <ph-6-var-ExtensionFullName/>,",
     "translated_text": "亲爱的"},
    {"opus_id": "common.uns.announcementsOnlyLoginInfo__email_html__3460",
     "has_seg_units": True, "tu_id": 10, "target_language": "zh-CN",
     "source_text": "Click on login", "translated_text": "点击登录"},
    {"opus_id": "plain.key", "target_language": "en-US",
     "source_text": "Save", "translated_text": "Save"},
]


class TestSourceRows(unittest.TestCase):

    def test_unique_keys_with_en_us_fallback_and_task_name(self):
        json_name = (
            "mr_pipeline_MR4100_bd6bba88_all_2026-08-31_08-03-01.json")
        rows = ej.source_rows_from_payload(
            {"translations": SEG_ROWS}, task_name=json_name)
        by_key = {key: (en_us, task) for key, en_us, task in rows}
        self.assertEqual(
            by_key["common.uns.announcementsOnlyLoginInfo__email_html__3460:::seg:::10"],
            ("Click on login", json_name))
        self.assertEqual(
            by_key["common.uns.announcementsOnlyLoginInfo__email_html__3460:::seg:::4"],
            ("Dear <ph-6-var-ExtensionFullName/>,", json_name))
        self.assertEqual(by_key["plain.key"], ("Save", json_name))
        # One row per unique key — language fan-out is collapsed.
        self.assertEqual(len(rows), 3)

    def test_segment_keys_sort_lexicographically(self):
        # Screenshot 2 shows :::seg:::10 before :::seg:::4 (string sort).
        json_name = "mr_pipeline_MR4100_bd6bba88_all_2026-08-31_08-03-01.json"
        rows = ej.source_rows_from_payload(
            {"translations": SEG_ROWS}, task_name=json_name)
        keys = [key for key, _, _ in rows]
        self.assertEqual(keys, [
            "common.uns.announcementsOnlyLoginInfo__email_html__3460:::seg:::10",
            "common.uns.announcementsOnlyLoginInfo__email_html__3460:::seg:::4",
            "plain.key",
        ])

    def test_dict_or_list_payload(self):
        from_dict = ej.source_rows_from_payload({"translations": SEG_ROWS})
        from_list = ej.source_rows_from_payload(SEG_ROWS)
        self.assertEqual(from_dict, from_list)

    def test_empty_payload(self):
        self.assertEqual(ej.source_rows_from_payload({}), [])
        self.assertEqual(ej.source_rows_from_payload(None), [])


class TestSheetTitleForMr(unittest.TestCase):

    def test_jira_and_mr_match_screenshot_tabs(self):
        self.assertEqual(
            ej.sheet_title_for_mr("BUG-352", "4103"), "BUG-352 MR!4103")
        self.assertEqual(
            ej.sheet_title_for_mr("BUG-502", "4100"), "BUG-502 MR!4100")
        self.assertEqual(
            ej.sheet_title_for_mr("RLZ-56748", "4099"), "RLZ-56748 MR!4099")
        self.assertEqual(
            ej.sheet_title_for_mr("UIA-413515", "4098"), "UIA-413515 MR!4098")

    def test_missing_jira_falls_back_to_mr(self):
        self.assertEqual(ej.sheet_title_for_mr("—", "4090"), "MR!4090")
        self.assertEqual(ej.sheet_title_for_mr("…", "4090"), "MR!4090")
        self.assertEqual(ej.sheet_title_for_mr("", "4090"), "MR!4090")
        self.assertEqual(ej.sheet_title_for_mr(None, "4090"), "MR!4090")

    def test_strips_mr_prefix_and_hash(self):
        self.assertEqual(
            ej.sheet_title_for_mr("BUG-352", "MR!4103"), "BUG-352 MR!4103")
        self.assertEqual(
            ej.sheet_title_for_mr("BUG-352", "#4103"), "BUG-352 MR!4103")

    def test_both_missing(self):
        self.assertEqual(ej.sheet_title_for_mr("", ""), "Source")


class TestSanitizeExcelSheetTitle(unittest.TestCase):

    def test_exclamation_mark_is_kept(self):
        used = set()
        self.assertEqual(
            ej.sanitize_excel_sheet_title("BUG-352 MR!4103", used),
            "BUG-352 MR!4103")

    def test_invalid_chars_and_uniqueness(self):
        used = set()
        a = ej.sanitize_excel_sheet_title("A:B/C", used)
        b = ej.sanitize_excel_sheet_title("A:B/C", used)
        self.assertEqual(a, "A-B-C")
        self.assertEqual(b, "A-B-C (1)")

    def test_truncates_to_31_chars(self):
        used = set()
        long_name = "VERY-LONG-JIRA-TICKET-NAME MR!99999"
        title = ej.sanitize_excel_sheet_title(long_name, used)
        self.assertLessEqual(len(title), 31)


@unittest.skipUnless(HAS_OPENPYXL, "openpyxl not installed")
class TestWriteSourceXlsx(unittest.TestCase):

    def test_three_columns_header_and_sheet_name(self):
        json_name = (
            "mr_pipeline_MR4100_bd6bba88_all_2026-08-31_08-03-01.json")
        rows = ej.source_rows_from_payload(
            {"translations": SEG_ROWS}, task_name=json_name)
        title = ej.sheet_title_for_mr("BUG-502", "4100")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "source.xlsx")
            saved = ej.write_source_xlsx(
                [{"title": title, "rows": rows}], path)
            self.assertEqual(saved, path)
            wb = load_workbook(path)
            self.assertEqual(wb.sheetnames, ["BUG-502 MR!4100"])
            ws = wb.active
            self.assertEqual(
                [cell.value for cell in ws[1]],
                ["Key", "en-US Value", "task name"])
            self.assertEqual(
                ws.cell(2, 1).value,
                "common.uns.announcementsOnlyLoginInfo__email_html__3460:::seg:::10")
            self.assertEqual(ws.cell(2, 2).value, "Click on login")
            self.assertEqual(ws.cell(2, 3).value, json_name)
            # task name stays in a single cell (not split on underscores).
            self.assertIsNone(ws.cell(2, 4).value)
            self.assertEqual(ws.freeze_panes, "A2")

    def test_multiple_sheets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "multi.xlsx")
            ej.write_source_xlsx(
                [
                    {"title": "BUG-352 MR!4103",
                     "rows": [("k1", "Hello", "a.json")]},
                    {"title": "BUG-502 MR!4100",
                     "rows": [("k2", "World", "b.json")]},
                ],
                path,
            )
            wb = load_workbook(path)
            self.assertEqual(
                wb.sheetnames, ["BUG-352 MR!4103", "BUG-502 MR!4100"])
            self.assertEqual(wb["BUG-352 MR!4103"]["A2"].value, "k1")
            self.assertEqual(wb["BUG-502 MR!4100"]["B2"].value, "World")

    def test_save_source_xlsx_returns_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.xlsx")
            saved = ej.save_source_xlsx(
                [{"title": "MR!1", "rows": [("k", "v", "t.json")]}], path)
            self.assertEqual(saved, path)
            self.assertTrue(os.path.isfile(saved))


class TestI18n(unittest.TestCase):

    def test_button_strings_in_both_languages(self):
        from export_gui import STRINGS
        for lang in ("en", "zh"):
            self.assertIn("mr_source_xlsx", STRINGS[lang])
            self.assertIn("mr_source_xlsx_need_selection", STRINGS[lang])
            self.assertTrue(STRINGS[lang]["mr_source_xlsx"].strip())


class TestSourceExportFilename(unittest.TestCase):

    def test_source_type_tag_and_json_companion_name(self):
        xlsx = _build(
            ".xlsx", mr_iid="4100", id_tag="bd6bba88", type_tag="source",
            created="2026-08-31 08:03:01", export_date="2026-09-02")
        json_name = _build(
            ".json", mr_iid="4100", id_tag="bd6bba88", type_tag="all",
            created="2026-08-31 08:03:01", export_date="2026-09-02")
        self.assertEqual(
            xlsx,
            "mr_pipeline_MR4100_bd6bba88_source_2026-08-31_08-03-01.xlsx")
        self.assertEqual(
            json_name,
            "mr_pipeline_MR4100_bd6bba88_all_2026-08-31_08-03-01.json")


if __name__ == "__main__":
    unittest.main()
