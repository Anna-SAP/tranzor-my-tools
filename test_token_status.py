"""Regression tests for ``export_gui.format_token_expiry_status``.

Locks down the header token-expiry pill the GUI relies on so a user can
judge "will my token outlive this long export?" BEFORE starting it:
state wording (signed-out / undecodable / expired / live countdown), the
24h / 1h urgency color ladder, and the zero-padding-free month/day
rendering (strftime's no-pad flag is platform-split, so it's hand-built).

Run:  python -m unittest test_token_status
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from export_gui import (
    TOKEN_STATUS_AMBER,
    TOKEN_STATUS_GRAY,
    TOKEN_STATUS_GREEN,
    TOKEN_STATUS_RED,
    format_token_expiry_status,
)

# A fixed "now" so tests stay stable across runs / clocks / timezones.
_NOW = datetime(2026, 7, 17, 10, 0, 0)


def _status(seconds_left, lang="en", signed_in=True):
    return format_token_expiry_status(
        seconds_left, now=_NOW, lang=lang, signed_in=signed_in)


class SignedOutAndUnknownTests(unittest.TestCase):

    def test_signed_out_en(self):
        text, color = _status(None, signed_in=False)
        self.assertEqual(text, "🕒 Not signed in")
        self.assertEqual(color, TOKEN_STATUS_GRAY)

    def test_signed_out_zh(self):
        text, color = _status(None, lang="zh", signed_in=False)
        self.assertEqual(text, "🕒 未登录")
        self.assertEqual(color, TOKEN_STATUS_GRAY)

    def test_signed_out_wins_over_seconds(self):
        # Defensive: no token means "not signed in", whatever seconds says.
        text, _ = _status(5 * 86400, signed_in=False)
        self.assertEqual(text, "🕒 Not signed in")

    def test_undecodable_exp_is_neutral(self):
        # Token present but exp unreadable — has_valid_token() treats it as
        # usable, so the pill must not cry wolf.
        text, color = _status(None)
        self.assertEqual(text, "🕒 Token expiry: unknown")
        self.assertEqual(color, TOKEN_STATUS_GRAY)

    def test_unknown_lang_falls_back_to_english(self):
        text, _ = _status(None, lang="fr", signed_in=False)
        self.assertEqual(text, "🕒 Not signed in")


class ExpiredTests(unittest.TestCase):

    def test_exactly_zero_is_expired(self):
        text, color = _status(0)
        self.assertEqual(text, "🕒 Token expired")
        self.assertEqual(color, TOKEN_STATUS_RED)

    def test_negative_is_expired(self):
        text, color = _status(-3600)
        self.assertEqual(text, "🕒 Token expired")
        self.assertEqual(color, TOKEN_STATUS_RED)

    def test_expired_zh(self):
        text, _ = _status(-1, lang="zh")
        self.assertEqual(text, "🕒 Token 已过期")


class CountdownTextTests(unittest.TestCase):

    def test_days_bucket_en(self):
        # 5d 3h from 7/17 10:00 → expires 7/22 13:00.
        text, color = _status(5 * 86400 + 3 * 3600)
        self.assertEqual(text, "🕒 Token expires: 7/22 13:00 (5d 3h left)")
        self.assertEqual(color, TOKEN_STATUS_GREEN)

    def test_days_bucket_zh(self):
        text, _ = _status(5 * 86400 + 3 * 3600, lang="zh")
        self.assertEqual(
            text, "🕒 Token 过期时间: 7/22 13:00（剩 5 天 3 小时）")

    def test_hours_bucket(self):
        text, color = _status(2 * 3600 + 30 * 60)
        self.assertEqual(text, "🕒 Token expires: 7/17 12:30 (2h 30m left)")
        self.assertEqual(color, TOKEN_STATUS_AMBER)

    def test_minutes_bucket(self):
        text, color = _status(45 * 60)
        self.assertEqual(text, "🕒 Token expires: 7/17 10:45 (45 min left)")
        self.assertEqual(color, TOKEN_STATUS_RED)

    def test_minutes_bucket_zh(self):
        text, _ = _status(45 * 60, lang="zh")
        self.assertEqual(text, "🕒 Token 过期时间: 7/17 10:45（剩 45 分钟）")

    def test_month_day_unpadded_clock_padded(self):
        # 9/8 20:05 + 12h → 9/9 08:05 — month/day bare, clock zero-padded.
        text, _ = format_token_expiry_status(
            12 * 3600, now=datetime(2026, 9, 8, 20, 5, 0))
        self.assertIn("9/9 08:05", text)

    def test_remaining_floors_not_rounds(self):
        # 1d 23h 59m 59s must NOT display as 2d.
        text, _ = _status(2 * 86400 - 1)
        self.assertIn("(1d 23h left)", text)


class ColorLadderTests(unittest.TestCase):

    def test_just_over_24h_is_green(self):
        _, color = _status(24 * 3600 + 1)
        self.assertEqual(color, TOKEN_STATUS_GREEN)

    def test_exactly_24h_is_amber(self):
        _, color = _status(24 * 3600)
        self.assertEqual(color, TOKEN_STATUS_AMBER)

    def test_just_over_1h_is_amber(self):
        _, color = _status(3601)
        self.assertEqual(color, TOKEN_STATUS_AMBER)

    def test_exactly_1h_is_red(self):
        _, color = _status(3600)
        self.assertEqual(color, TOKEN_STATUS_RED)

    def test_full_week_is_green(self):
        # Fresh 7-day token (JWT_EXPIRE_HOURS=168).
        text, color = _status(7 * 86400)
        self.assertEqual(color, TOKEN_STATUS_GREEN)
        self.assertIn("7/24 10:00", text)
        self.assertIn("7d 0h", text)


if __name__ == "__main__":
    unittest.main()
