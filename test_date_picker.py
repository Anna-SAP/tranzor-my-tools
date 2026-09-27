"""Tests for date_picker's pure (Tk-free) helpers.

The calendar popup itself is UI and not unit-tested here; the date math
(parsing, month navigation, grid layout, title formatting) is pure and is
where bugs would actually bite.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import date_picker as dp


class IsSelectableTests(unittest.TestCase):
    def test_no_limit(self):
        self.assertTrue(dp.is_selectable(date(2099, 1, 1)))

    def test_limit_is_inclusive(self):
        cap = date(2026, 9, 28)
        self.assertTrue(dp.is_selectable(date(2026, 9, 27), cap))
        self.assertTrue(dp.is_selectable(cap, cap))
        self.assertFalse(dp.is_selectable(date(2026, 9, 29), cap))


class MaxDatePopupTests(unittest.TestCase):
    """Real-Tk check that days after ``max_date`` cannot be clicked."""

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception as exc:  # no display
            raise unittest.SkipTest(f"Tk unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_future_days_are_plain_labels(self):
        import tkinter as tk
        picked = []
        btn = tk.Button(self.root, text="x")
        popup = dp.open_calendar(
            btn, font_family="Segoe UI", get_value=lambda: "2026-09-27",
            set_value=picked.append, max_date=date(2026, 9, 28))
        try:
            cells = popup._grid_holder.winfo_children()
            clickable = sorted(int(w.cget("text")) for w in cells
                               if isinstance(w, tk.Button))
            greyed = sorted(int(w.cget("text")) for w in cells
                            if isinstance(w, tk.Label) and w.cget("text"))
            self.assertEqual(clickable, list(range(1, 29)))
            self.assertEqual(greyed, [29, 30])
        finally:
            popup._close()

    def test_injected_today_drives_highlight_and_today_button(self):
        import tkinter as tk
        picked = []
        btn = tk.Button(self.root, text="x")
        popup = dp.open_calendar(
            btn, font_family="Segoe UI", get_value=lambda: "",
            set_value=picked.append, max_date=date(2026, 9, 28),
            today=lambda: date(2026, 9, 28))
        self.assertEqual(popup._selected, date(2026, 9, 28))
        popup._pick_today()
        self.assertEqual(picked, ["2026-09-28"])

    def test_today_button_never_passes_max_date(self):
        import tkinter as tk
        picked = []
        btn = tk.Button(self.root, text="x")
        popup = dp.open_calendar(
            btn, font_family="Segoe UI", get_value=lambda: "",
            set_value=picked.append, max_date=date(2026, 9, 27),
            today=date(2026, 9, 28))
        popup._pick_today()
        self.assertEqual(picked, ["2026-09-27"])

    def test_seed_after_max_date_is_clamped(self):
        import tkinter as tk
        btn = tk.Button(self.root, text="x")
        popup = dp.open_calendar(
            btn, font_family="Segoe UI", get_value=lambda: "2026-12-05",
            set_value=lambda _s: None, max_date=date(2026, 9, 28))
        try:
            self.assertEqual((popup._view_year, popup._view_month), (2026, 9))
            self.assertEqual(popup._selected, date(2026, 9, 28))
        finally:
            popup._close()


class ParseDateTests(unittest.TestCase):
    def test_plain_iso(self):
        self.assertEqual(dp.parse_date("2026-05-20"), date(2026, 5, 20))

    def test_with_time_component(self):
        # 后端有时回 "2026-05-20T10:00:00" / "2026-05-20 10:00"，只取日期部分。
        self.assertEqual(dp.parse_date("2026-05-20T10:00:00"), date(2026, 5, 20))
        self.assertEqual(dp.parse_date("2026-05-20 23:59"), date(2026, 5, 20))

    def test_surrounding_whitespace(self):
        self.assertEqual(dp.parse_date("  2026-05-20  "), date(2026, 5, 20))

    def test_empty_and_none(self):
        self.assertIsNone(dp.parse_date(None))
        self.assertIsNone(dp.parse_date(""))
        self.assertIsNone(dp.parse_date("   "))

    def test_invalid(self):
        self.assertIsNone(dp.parse_date("not-a-date"))
        self.assertIsNone(dp.parse_date("2026/05/20"))
        self.assertIsNone(dp.parse_date("2026-13-40"))


class ShiftMonthTests(unittest.TestCase):
    def test_no_shift(self):
        self.assertEqual(dp.shift_month(2026, 5, 0), (2026, 5))

    def test_back_across_year(self):
        self.assertEqual(dp.shift_month(2026, 1, -1), (2025, 12))

    def test_forward_across_year(self):
        self.assertEqual(dp.shift_month(2026, 12, 1), (2027, 1))

    def test_multi_month(self):
        self.assertEqual(dp.shift_month(2026, 3, -5), (2025, 10))
        self.assertEqual(dp.shift_month(2026, 6, 12), (2027, 6))
        self.assertEqual(dp.shift_month(2026, 6, -12), (2025, 6))


class MonthWeeksTests(unittest.TestCase):
    def test_rows_are_full_weeks(self):
        weeks = dp.month_weeks(2026, 5)
        self.assertTrue(all(len(w) == 7 for w in weeks))

    def test_every_day_present_in_order(self):
        # May 2026 → 31 天，去掉占位 0 后应正好是 1..31 顺序。
        weeks = dp.month_weeks(2026, 5)
        flat = [d for w in weeks for d in w if d != 0]
        self.assertEqual(flat, list(range(1, 32)))

    def test_leap_february(self):
        weeks = dp.month_weeks(2024, 2)  # 闰年 2 月 = 29 天
        flat = [d for w in weeks for d in w if d != 0]
        self.assertEqual(flat, list(range(1, 30)))

    def test_monday_first(self):
        # firstweekday=0 → 周一起始：每周第一格对应周一。
        # 2026-06-01 是周一，所以 6 月首周第 0 格应为 1（无前导占位）。
        weeks = dp.month_weeks(2026, 6)
        self.assertEqual(weeks[0][0], 1)


class FormatTitleTests(unittest.TestCase):
    def test_english(self):
        self.assertEqual(dp.format_month_title(2026, 5, "en"), "May 2026")
        self.assertEqual(dp.format_month_title(2026, 12, "en"), "December 2026")

    def test_chinese(self):
        self.assertEqual(dp.format_month_title(2026, 5, "zh"), "2026年5月")


class ResolveLangTests(unittest.TestCase):
    def test_plain_string(self):
        self.assertEqual(dp._resolve_lang("zh"), "zh")
        self.assertEqual(dp._resolve_lang("en"), "en")

    def test_callable(self):
        self.assertEqual(dp._resolve_lang(lambda: "zh"), "zh")

    def test_unknown_falls_back_to_en(self):
        self.assertEqual(dp._resolve_lang("fr"), "en")
        self.assertEqual(dp._resolve_lang(None), "en")

    def test_callable_raising_falls_back(self):
        def _boom():
            raise RuntimeError("boom")
        self.assertEqual(dp._resolve_lang(_boom), "en")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
