"""Regression tests for ``time_display`` — UI clocks always carry a tz label.

Locks down the compact ``UTC+8`` / ``UTC-5`` / ``UTC+5:30`` suffix, the
naive-UTC-to-local conversion contract, and the ISO-shape tolerance the GUI
relies on when Tranzor timestamps arrive without a zone.

Run:  python -m unittest test_time_display
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from time_display import (
    FMT_SHORT,
    format_display_datetime,
    format_display_now,
    format_tz_label,
    parse_iso_datetime,
)

TZ_EAST = timezone(timedelta(hours=8))       # UTC+8  (the screenshot case)
TZ_WEST = timezone(timedelta(hours=-5))      # UTC-5
TZ_INDIA = timezone(timedelta(hours=5, minutes=30))
TZ_UTC = timezone.utc


class TzLabelTests(unittest.TestCase):

    def test_whole_hour_east(self):
        self.assertEqual(format_tz_label(tz=TZ_EAST), "UTC+8")

    def test_whole_hour_west(self):
        self.assertEqual(format_tz_label(tz=TZ_WEST), "UTC-5")

    def test_half_hour(self):
        self.assertEqual(format_tz_label(tz=TZ_INDIA), "UTC+5:30")

    def test_utc_is_bare(self):
        self.assertEqual(format_tz_label(tz=TZ_UTC), "UTC")

    def test_default_matches_host_local(self):
        # Whatever zone the test machine is in, the label must be non-empty
        # and start with UTC so the UI never renders a bare clock.
        label = format_tz_label()
        self.assertTrue(label.startswith("UTC"), label)


class NaiveVsAwareTests(unittest.TestCase):

    def test_naive_tranzor_created_at_is_utc(self):
        # Screenshot bug: API "2026-09-03 05:06:57" (UTC, no offset) used
        # to render as "05:06:57 UTC+8". Convert, don't relabel.
        text = format_display_datetime(
            "2026-09-03 05:06:57", tz=TZ_EAST)
        self.assertEqual(text, "2026-09-03 13:06:57 UTC+8")

    def test_naive_space_separator(self):
        text = format_display_datetime(
            "2026-09-03 02:41:20", tz=TZ_EAST)
        self.assertEqual(text, "2026-09-03 10:41:20 UTC+8")

    def test_naive_T_separator(self):
        text = format_display_datetime(
            "2026-09-03T02:41:20", tz=TZ_EAST)
        self.assertEqual(text, "2026-09-03 10:41:20 UTC+8")

    def test_aware_utc_converts_into_display_zone(self):
        text = format_display_datetime(
            "2026-09-03T02:41:20+00:00", tz=TZ_EAST)
        self.assertEqual(text, "2026-09-03 10:41:20 UTC+8")

    def test_trailing_Z_is_utc(self):
        text = format_display_datetime(
            "2026-09-03T02:41:20Z", tz=TZ_EAST)
        self.assertEqual(text, "2026-09-03 10:41:20 UTC+8")

    def test_already_in_display_zone(self):
        text = format_display_datetime(
            "2026-09-03T02:41:20+08:00", tz=TZ_EAST)
        self.assertEqual(text, "2026-09-03 02:41:20 UTC+8")

    def test_datetime_object_naive(self):
        text = format_display_datetime(
            datetime(2026, 9, 3, 2, 41, 20), tz=TZ_EAST)
        self.assertEqual(text, "2026-09-03 10:41:20 UTC+8")

    def test_datetime_object_aware(self):
        text = format_display_datetime(
            datetime(2026, 9, 3, 2, 41, 20, tzinfo=TZ_UTC), tz=TZ_EAST)
        self.assertEqual(text, "2026-09-03 10:41:20 UTC+8")

    def test_naive_utc_rolls_local_date(self):
        # 16:00 UTC is midnight the next day in UTC+8.
        text = format_display_datetime(
            "2026-09-03T16:00:00", tz=TZ_EAST)
        self.assertEqual(text, "2026-09-04 00:00:00 UTC+8")


class FormatVariantsTests(unittest.TestCase):

    def test_short_fmt(self):
        text = format_display_datetime(
            "2026-09-03 02:41:20", fmt=FMT_SHORT, tz=TZ_EAST)
        self.assertEqual(text, "09-03 10:41 UTC+8")

    def test_microseconds_dropped(self):
        text = format_display_datetime(
            "2026-04-03T20:44:41.061939", tz=TZ_EAST)
        self.assertEqual(text, "2026-04-04 04:44:41 UTC+8")

    def test_empty_string(self):
        self.assertEqual(format_display_datetime("", tz=TZ_EAST), "")

    def test_none(self):
        self.assertEqual(format_display_datetime(None, tz=TZ_EAST), "")

    def test_custom_empty(self):
        self.assertEqual(
            format_display_datetime(None, empty="—", tz=TZ_EAST), "—")

    def test_unparsable_still_labelled(self):
        text = format_display_datetime("not-a-date", tz=TZ_EAST)
        self.assertTrue(text.endswith("UTC+8"), text)
        self.assertIn("not-a-date", text)

    def test_whitespace_only(self):
        self.assertEqual(format_display_datetime("   ", tz=TZ_EAST), "")


class ParseTests(unittest.TestCase):

    def test_passthrough_datetime(self):
        dt = datetime(2026, 9, 3, 2, 41, 20)
        self.assertIs(parse_iso_datetime(dt), dt)

    def test_none(self):
        self.assertIsNone(parse_iso_datetime(None))

    def test_garbage(self):
        self.assertIsNone(parse_iso_datetime("nope"))


class NowTests(unittest.TestCase):

    def test_now_carries_injected_tz(self):
        text = format_display_now(tz=TZ_EAST)
        self.assertTrue(text.endswith("UTC+8"), text)
        # Full format is "YYYY-MM-DD HH:MM:SS UTC+8"
        clock, _, label = text.rpartition(" ")
        self.assertEqual(label, "UTC+8")
        datetime.strptime(clock, "%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
