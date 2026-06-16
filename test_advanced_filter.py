"""Tests for advanced_filter's pure (Tk-free) evaluator and serializers.

The GUI panel is UI and not unit-tested here; the matching engine is where a
bug would actually bite (it must stay byte-for-byte equivalent to the report's
JS engine), so that is what we pin down.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import advanced_filter as af


def _cond(**kw):
    c = af.empty_condition()
    c.update(kw)
    return c


def _state(string_key=None, source=None, translated=None):
    """Build a state from per-field (logic, [conditions]) tuples."""
    st = af.empty_state()
    for fid, val in (("string_key", string_key), ("source", source),
                     ("translated", translated)):
        if val is None:
            continue
        logic, conds = val
        st[fid] = {"logic": logic, "conditions": conds}
    return st


def _row(opus_id="", source_text="", translated_text=""):
    return {"opus_id": opus_id, "source_text": source_text,
            "translated_text": translated_text}


class TestMatchTests(unittest.TestCase):
    def test_empty_keyword_is_none(self):
        self.assertIsNone(af._test_match("hello", "", False, False, False))

    def test_substring_case_insensitive_default(self):
        self.assertTrue(af._test_match("Hello World", "hello", False, False, False))

    def test_case_sensitive(self):
        self.assertFalse(af._test_match("Hello", "hello", True, False, False))
        self.assertTrue(af._test_match("Hello", "Hello", True, False, False))

    def test_match_whole_word(self):
        self.assertFalse(af._test_match("submarine", "sub", False, False, True))
        self.assertTrue(af._test_match("a sub here", "sub", False, False, True))

    def test_match_whole_word_ascii_boundary_matches_js(self):
        # JS RegExp \b is ASCII-only and the report uses no `u` flag, so a
        # keyword whose edge is a non-ASCII word char (CJK / accented) never
        # whole-word matches — Python must agree (re.ASCII) or the HTML report
        # and the Excel/JSON pre-filter would silently disagree.
        self.assertFalse(af._test_match("发票", "发票", False, False, True))
        self.assertFalse(af._test_match("点击 发票 按钮", "发票", False, False, True))
        self.assertFalse(af._test_match("le café noir", "café", False, False, True))
        self.assertFalse(af._test_match("über-cool", "über", False, False, True))
        # Substring (match_whole off) still finds CJK / accented text.
        self.assertTrue(af._test_match("点击 发票 按钮", "发票", False, False, False))
        self.assertTrue(af._test_match("le café noir", "café", False, False, False))
        # A keyword with ASCII edges around an accent still whole-word matches.
        self.assertTrue(af._test_match("naïve idea", "naïve", False, False, True))

    def test_regex(self):
        self.assertTrue(af._test_match("err-503", r"err-\d+", False, True, False))
        self.assertFalse(af._test_match("err-abc", r"err-\d+", False, True, False))

    def test_regex_special_chars_are_literal_when_not_regex(self):
        # "a.b" must NOT match "axb" when regex is off (dot is escaped).
        self.assertFalse(af._test_match("axb", "a.b", False, False, False))
        self.assertTrue(af._test_match("a.b", "a.b", False, False, False))

    def test_invalid_regex_is_false_not_raise(self):
        self.assertFalse(af._test_match("anything", "(", False, True, False))


class ConditionTests(unittest.TestCase):
    def test_inactive_condition(self):
        self.assertFalse(af.condition_active(af.empty_condition()))
        self.assertTrue(af.condition_active(_cond(pos="x")))
        self.assertTrue(af.condition_active(_cond(neg="x")))

    def test_pos_is_required_include(self):
        self.assertTrue(af.evaluate_condition("hello", _cond(pos="ell")))
        self.assertFalse(af.evaluate_condition("hello", _cond(pos="zzz")))

    def test_neg_is_required_exclude(self):
        self.assertTrue(af.evaluate_condition("hello", _cond(neg="zzz")))
        self.assertFalse(af.evaluate_condition("hello", _cond(neg="ell")))

    def test_pos_and_neg_together(self):
        c = _cond(pos="hel", neg="xyz")
        self.assertTrue(af.evaluate_condition("hello", c))
        c2 = _cond(pos="hel", neg="lo")
        self.assertFalse(af.evaluate_condition("hello", c2))  # neg excludes


class FieldLogicTests(unittest.TestCase):
    def test_no_active_conditions_passes(self):
        self.assertTrue(af.evaluate_field("anything", af.empty_field_state()))

    def test_and_requires_all(self):
        fs = {"logic": "AND", "conditions": [_cond(pos="foo"), _cond(pos="bar")]}
        self.assertTrue(af.evaluate_field("foo and bar", fs))
        self.assertFalse(af.evaluate_field("only foo", fs))

    def test_or_requires_any(self):
        fs = {"logic": "OR", "conditions": [_cond(pos="foo"), _cond(pos="bar")]}
        self.assertTrue(af.evaluate_field("only bar", fs))
        self.assertFalse(af.evaluate_field("neither", fs))

    def test_inactive_conditions_are_ignored(self):
        fs = {"logic": "AND", "conditions": [_cond(pos="foo"), af.empty_condition()]}
        self.assertTrue(af.evaluate_field("foo", fs))


class RowAndFilterTests(unittest.TestCase):
    def test_blocks_and_together(self):
        st = _state(
            source=("AND", [_cond(pos="invoice")]),
            translated=("AND", [_cond(neg="发票")]),
        )
        # source has invoice, translated does NOT contain 发票 -> pass
        self.assertTrue(af.row_passes(
            _row(source_text="Invoice total", translated_text="Total"), st))
        # translated contains the excluded term -> fail
        self.assertFalse(af.row_passes(
            _row(source_text="Invoice total", translated_text="发票"), st))
        # source lacks invoice -> fail
        self.assertFalse(af.row_passes(
            _row(source_text="Receipt", translated_text="Total"), st))

    def test_string_key_matches_opus_id(self):
        st = _state(string_key=("AND", [_cond(pos="login")]))
        self.assertTrue(af.row_passes(_row(opus_id="auth.login.title"), st))
        self.assertFalse(af.row_passes(_row(opus_id="auth.logout.title"), st))

    def test_filter_translations(self):
        rows = [
            _row(source_text="Save"), _row(source_text="Cancel"),
            _row(source_text="Save as"),
        ]
        st = _state(source=("AND", [_cond(pos="Save")]))
        out = af.filter_translations(rows, st)
        self.assertEqual([r["source_text"] for r in out], ["Save", "Save as"])

    def test_empty_state_returns_input_unchanged(self):
        rows = [_row(source_text="x")]
        self.assertIs(af.filter_translations(rows, af.empty_state()), rows)


class StateHelpersTests(unittest.TestCase):
    def test_is_empty(self):
        self.assertTrue(af.is_empty(af.empty_state()))
        self.assertTrue(af.is_empty(None))
        self.assertTrue(af.is_empty({}))
        st = _state(source=("AND", [_cond(pos="x")]))
        self.assertFalse(af.is_empty(st))

    def test_count_active(self):
        st = _state(
            source=("OR", [_cond(pos="a"), _cond(neg="b"), af.empty_condition()]),
            translated=("AND", [_cond(pos="c")]),
        )
        self.assertEqual(af.count_active(st), 3)

    def test_to_js_initial_shape(self):
        st = _state(source=("OR", [_cond(pos="a", pos_regex=True, match_whole=True)]))
        js = af.to_js_initial(st)
        self.assertEqual(set(js.keys()), {"source"})
        self.assertEqual(js["source"]["logic"], "OR")
        c = js["source"]["conditions"][0]
        self.assertEqual(c["pos"], "a")
        self.assertTrue(c["posRegex"])
        self.assertTrue(c["matchWhole"])
        self.assertFalse(c["negRegex"])

    def test_to_js_initial_empty_is_none(self):
        self.assertIsNone(af.to_js_initial(af.empty_state()))

    def test_to_js_initial_drops_inactive_conditions(self):
        st = _state(source=("AND", [_cond(pos="a"), af.empty_condition()]))
        js = af.to_js_initial(st)
        self.assertEqual(len(js["source"]["conditions"]), 1)

    def test_summary_segments(self):
        st = _state(
            source=("AND", [_cond(pos="foo", neg="bar")]),
            translated=("OR", [_cond(pos="baz")]),
        )
        segs = af.summary_segments(st)
        labels = [s[0] for s in segs]
        self.assertEqual(labels, ["SOURCE (EN-US)", "TRANSLATED TEXT"])
        self.assertIn('contains "foo"', segs[0][2][0])
        self.assertIn('NOT "bar"', segs[0][2][0])
        self.assertEqual(segs[1][1], "OR")


if __name__ == "__main__":
    unittest.main()
