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
