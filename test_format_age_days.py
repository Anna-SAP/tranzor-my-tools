"""Regression tests for ``export_gui.format_age_days``.

Locks down the bucketing behaviour the GUI Cache Age columns depend on.

Run:  python -m unittest test_format_age_days
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from export_gui import format_age_days


# A fixed "now" so tests stay stable across runs / clocks / timezones.
_NOW = datetime(2026, 5, 28, 12, 0, 0)


class EmptyAndBadInputTests(unittest.TestCase):

    def test_empty_string(self):
        self.assertEqual(format_age_days("", now=_NOW), "")

    def test_none(self):
        self.assertEqual(format_age_days(None, now=_NOW), "")

    def test_whitespace_only(self):
        self.assertEqual(format_age_days("   ", now=_NOW), "")

    def test_unparsable(self):
        self.assertEqual(format_age_days("not-a-date", now=_NOW), "")
        self.assertEqual(format_age_days("2026-99-99", now=_NOW), "")


class TodayBucketTests(unittest.TestCase):

    def test_exact_same_instant(self):
        self.assertEqual(
            format_age_days("2026-05-28T12:00:00", now=_NOW), "today"
        )

    def test_earlier_same_day(self):
        self.assertEqual(
            format_age_days("2026-05-28 00:00:00", now=_NOW), "today"
        )

    def test_future_clamped(self):
        # Clock skew on remote servers can yield future timestamps; render
        # them as "today" instead of leaking "-3d" into the UI.
        self.assertEqual(
            format_age_days("2026-06-15T00:00:00", now=_NOW), "today"
        )


class DayBucketTests(unittest.TestCase):

    def test_one_day(self):
        self.assertEqual(
            format_age_days("2026-05-27T12:00:00", now=_NOW), "1d"
        )

    def test_three_days(self):
        self.assertEqual(
            format_age_days("2026-05-25T12:00:00", now=_NOW), "3d"
        )

    def test_thirty_days_still_in_day_bucket(self):
        # 30 d ≤ delta < 31 → still days
        self.assertEqual(
            format_age_days("2026-04-28T12:00:00", now=_NOW), "30d"
        )


class MonthBucketTests(unittest.TestCase):

    def test_thirtyone_days_rolls_to_one_month(self):
        self.assertEqual(
            format_age_days("2026-04-27T12:00:00", now=_NOW), "1mo"
        )

    def test_mid_range_month(self):
        # 270 / 30 → 9
        self.assertEqual(
            format_age_days("2025-08-31T12:00:00", now=_NOW), "9mo"
        )


class YearBucketTests(unittest.TestCase):

    def test_boundary_near_one_year_no_zero_year(self):
        """Regression: 361 days used to render "0y" because days // 365 == 0
        while days // 30 == 12. ``format_age_days`` must floor to 1y."""
        # 2025-06-01 → 361 days before _NOW
        self.assertEqual(
            format_age_days("2025-06-01T12:00:00", now=_NOW), "1y"
        )

    def test_exactly_one_year(self):
        self.assertEqual(
            format_age_days("2025-05-28T12:00:00", now=_NOW), "1y"
        )

    def test_multi_year(self):
        # 2024-01-01 → 878 days → 2y
        self.assertEqual(
            format_age_days("2024-01-01T12:00:00", now=_NOW), "2y"
        )

    def test_decade(self):
        # 2016-01-01 → ~3800 days → 10y
        self.assertEqual(
            format_age_days("2016-01-01T12:00:00", now=_NOW), "10y"
        )


class IsoFormatToleranceTests(unittest.TestCase):

    def test_space_separator(self):
        # Tranzor's _fmt_iso_short produces "YYYY-MM-DD HH:MM:SS" with space.
        self.assertEqual(
            format_age_days("2026-05-25 06:51:18", now=_NOW), "3d"
        )

    def test_T_separator(self):
        self.assertEqual(
            format_age_days("2026-05-25T06:51:18", now=_NOW), "3d"
        )

    def test_trailing_Z_accepted(self):
        # Python < 3.11 fromisoformat rejects "Z"; our helper rewrites it.
        # The result may differ from naive by timezone offset, so only assert
        # it produced *some* day-bucket value rather than empty.
        result = format_age_days("2026-05-25T06:51:18Z", now=_NOW)
        self.assertTrue(result.endswith("d"), f"got {result!r}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
