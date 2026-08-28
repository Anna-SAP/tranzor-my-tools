"""Tests for searchable_combobox's pure (Tk-free) helpers.

The popup itself is UI and not unit-tested here; the filtering / label
logic is pure and is where behavior would actually regress.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import searchable_combobox as sc


class FilterOptionsTests(unittest.TestCase):
    OPTIONS = ["", "CoreLib/RoomsController", "CoreLib/guides-assets",
               "Fiji/Fiji", "Fiji/video", "web/bui"]

    def test_empty_keyword_returns_all(self):
        self.assertEqual(sc.filter_options(self.OPTIONS, ""), self.OPTIONS)

    def test_none_keyword_returns_all(self):
        self.assertEqual(sc.filter_options(self.OPTIONS, None), self.OPTIONS)

    def test_whitespace_keyword_returns_all(self):
        # 全空白视同未输入（避免误按空格后列表清空）。
        self.assertEqual(sc.filter_options(self.OPTIONS, "   "), self.OPTIONS)

    def test_case_insensitive_contains(self):
        self.assertEqual(sc.filter_options(self.OPTIONS, "fiji"),
                         ["Fiji/Fiji", "Fiji/video"])
        self.assertEqual(sc.filter_options(self.OPTIONS, "CORELIB"),
                         ["CoreLib/RoomsController", "CoreLib/guides-assets"])

    def test_substring_matches_anywhere(self):
        self.assertEqual(sc.filter_options(self.OPTIONS, "rooms"),
                         ["CoreLib/RoomsController"])
        self.assertEqual(sc.filter_options(self.OPTIONS, "/"),
                         self.OPTIONS[1:])  # 空占位项不含 "/"

    def test_keyword_drops_empty_placeholder(self):
        # 搜索时空字符串（"全部"占位）没有意义，应被滤掉。
        self.assertNotIn("", sc.filter_options(self.OPTIONS, "fiji"))

    def test_no_match_returns_empty(self):
        self.assertEqual(sc.filter_options(self.OPTIONS, "zzz"), [])

    def test_keyword_surrounding_whitespace_ignored(self):
        self.assertEqual(sc.filter_options(self.OPTIONS, "  fiji  "),
                         ["Fiji/Fiji", "Fiji/video"])

    def test_order_preserved(self):
        opts = ["b/x", "a/x", "c/x"]
        self.assertEqual(sc.filter_options(opts, "x"), opts)

    def test_non_string_options_coerced(self):
        # Tcl 有时把 values 里的数字还原成非 str，过滤结果统一为 str。
        self.assertEqual(sc.filter_options([123, "abc"], "12"), ["123"])

    def test_empty_options(self):
        self.assertEqual(sc.filter_options([], "x"), [])
        self.assertEqual(sc.filter_options([], ""), [])


class DisplayLabelTests(unittest.TestCase):
    def test_regular_option_unchanged(self):
        self.assertEqual(sc.display_label("Fiji/Fiji", "en"), "Fiji/Fiji")
        self.assertEqual(sc.display_label("Fiji/Fiji", "zh"), "Fiji/Fiji")

    def test_empty_shows_all_placeholder(self):
        self.assertEqual(sc.display_label("", "en"), "(All)")
        self.assertEqual(sc.display_label("", "zh"), "（全部）")

    def test_whitespace_only_treated_as_empty(self):
        self.assertEqual(sc.display_label("   ", "en"), "(All)")

    def test_unknown_lang_falls_back_to_en(self):
        self.assertEqual(sc.display_label("", "fr"), "(All)")


class CheckedLabelTests(unittest.TestCase):
    def test_prefixes_checkbox(self):
        self.assertEqual(sc.checked_label("Fiji/Fiji", False, "en"),
                         f"{sc.CHECK_OFF} Fiji/Fiji")
        self.assertEqual(sc.checked_label("Fiji/Fiji", True, "zh"),
                         f"{sc.CHECK_ON} Fiji/Fiji")

    def test_empty_uses_all_placeholder(self):
        self.assertEqual(sc.checked_label("", True, "en"),
                         f"{sc.CHECK_ON} (All)")
        self.assertEqual(sc.checked_label("", False, "zh"),
                         f"{sc.CHECK_OFF} （全部）")


class FormatSelectionSummaryTests(unittest.TestCase):
    def test_empty_is_blank(self):
        self.assertEqual(sc.format_selection_summary([]), "")
        self.assertEqual(sc.format_selection_summary(None), "")
        self.assertEqual(sc.format_selection_summary(["", "  "]), "")

    def test_single_is_the_name(self):
        # Identical to the pre-multi Combobox display so a 1-item
        # selection doesn't look like a new widget.
        self.assertEqual(
            sc.format_selection_summary(["es/express-setup-renaissance"]),
            "es/express-setup-renaissance")

    def test_multi_is_count_en(self):
        self.assertEqual(
            sc.format_selection_summary(["a", "b"], "en"), "2 selected")
        self.assertEqual(
            sc.format_selection_summary(["a", "b", "c"], "en"), "3 selected")

    def test_multi_is_count_zh(self):
        self.assertEqual(
            sc.format_selection_summary(["a", "b"], "zh"), "已选 2 项")

    def test_unknown_lang_falls_back_to_en(self):
        self.assertEqual(
            sc.format_selection_summary(["a", "b"], "fr"), "2 selected")

    def test_matching_preset_name_wins(self):
        self.assertEqual(
            sc.format_selection_summary(["a", "b"], "en", preset_name="UNS"),
            "UNS")
        # Even a single project shows the group name when it matches.
        self.assertEqual(
            sc.format_selection_summary(["common/uns"], "zh",
                                        preset_name="UNS"),
            "UNS")

    def test_blank_preset_name_ignored(self):
        self.assertEqual(
            sc.format_selection_summary(["a", "b"], "en", preset_name="  "),
            "2 selected")


class ToggleSelectedTests(unittest.TestCase):
    OPTIONS = ["Fiji/Fiji", "web/bui", "common/uns"]

    def test_all_clears(self):
        self.assertEqual(
            sc.toggle_selected(self.OPTIONS, ["web/bui", "Fiji/Fiji"], ""),
            [])
        self.assertEqual(
            sc.toggle_selected(self.OPTIONS, ["web/bui"], "   "),
            [])

    def test_add_preserves_options_order(self):
        self.assertEqual(
            sc.toggle_selected(self.OPTIONS, ["common/uns"], "Fiji/Fiji"),
            ["Fiji/Fiji", "common/uns"])

    def test_remove(self):
        self.assertEqual(
            sc.toggle_selected(self.OPTIONS, ["Fiji/Fiji", "web/bui"],
                               "Fiji/Fiji"),
            ["web/bui"])

    def test_drops_unknown_and_blanks(self):
        self.assertEqual(
            sc.toggle_selected(self.OPTIONS, ["", "gone", "web/bui"],
                               "common/uns"),
            ["web/bui", "common/uns"])

    def test_empty_options(self):
        self.assertEqual(sc.toggle_selected([], ["x"], "x"), [])


class AddVisibleTests(unittest.TestCase):
    OPTIONS = ["Fiji/Fiji", "Fiji/video", "web/bui", "common/uns"]

    def test_unions_visible_keeps_hidden_checks(self):
        # Search "fiji" then Check visible: both Fiji/* join, web/bui stays.
        self.assertEqual(
            sc.add_visible(self.OPTIONS, ["web/bui"],
                           ["Fiji/Fiji", "Fiji/video"]),
            ["Fiji/Fiji", "Fiji/video", "web/bui"])

    def test_skips_empty_placeholder(self):
        self.assertEqual(
            sc.add_visible(self.OPTIONS, [], ["", "web/bui"]),
            ["web/bui"])

    def test_idempotent(self):
        already = ["Fiji/Fiji", "web/bui"]
        self.assertEqual(
            sc.add_visible(self.OPTIONS, already, ["Fiji/Fiji"]),
            already)


class ResolveLangTests(unittest.TestCase):
    def test_plain_string(self):
        self.assertEqual(sc._resolve_lang("zh"), "zh")
        self.assertEqual(sc._resolve_lang("en"), "en")

    def test_callable(self):
        self.assertEqual(sc._resolve_lang(lambda: "zh"), "zh")

    def test_unknown_falls_back_to_en(self):
        self.assertEqual(sc._resolve_lang("fr"), "en")
        self.assertEqual(sc._resolve_lang(None), "en")

    def test_callable_raising_falls_back(self):
        def _boom():
            raise RuntimeError("boom")
        self.assertEqual(sc._resolve_lang(_boom), "en")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
