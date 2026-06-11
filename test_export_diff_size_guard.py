"""Regression tests for the word-diff size guard shared by export_changes and
export_mr_pipeline.

UNS Handlebars units hydrate to whole-file templates (tens of KB). A
token-level diff there is the slow, near-unreadable worst case, so above
MAX_DIFF_CHARS the diff falls back to whole-line tokens. Both tokenizers must
preserve the reconstruction invariant ``"".join(tokens) == text`` so the diff
output never drops or corrupts characters.

Run:  python -m unittest test_export_diff_size_guard
"""
from __future__ import annotations

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_changes as ec
import export_mr_pipeline as mp


_TAG = re.compile(r"<[^>]+>")


def _strip_tags(s):
    return _TAG.sub("", s)


class _DiffSizeGuardMixin:
    """Shared assertions; subclasses bind the module under test as ``mod`` and
    the html/text diff callables."""

    mod = None
    diff_html = None
    diff_text = None

    def test_small_edit_uses_token_diff_and_marks_change(self):
        before = "You have a new Meeting invitation at 3 PM"
        after = "You have a new Meeting invitation at 4 PM"
        b_tok, a_tok = self.mod._diff_tokens(before, after)
        # token mode: more than one token (not whole-line)
        self.assertGreater(len(b_tok), 3)
        out = self.diff_html(before, after)
        self.assertIn("<del", out)
        self.assertIn("<ins", out)

    def test_oversized_falls_back_to_line_diff(self):
        big = "\n".join(f"<p>line {i} 会议邀请 review</p>" for i in range(500))
        before = big
        after = big.replace("line 250 会议邀请", "line 250 会议已读")
        self.assertGreater(len(before) + len(after), self.mod.MAX_DIFF_CHARS)
        b_tok, a_tok = self.mod._diff_tokens(before, after)
        # line mode: token count == number of lines
        self.assertEqual(b_tok, before.splitlines(keepends=True))
        out = self.diff_html(before, after)
        self.assertIn("会议已读", out)   # new content present
        self.assertIn("会议邀请", out)   # old content present (most lines equal)

    def test_reconstruction_invariant_small_and_big(self):
        for text in ("Plain UI string %1$s with tags {{x}}",
                     "会议邀请 mixed 中英文 text",
                     "x" * 100,
                     "\n".join(["row " + str(i) for i in range(2000)])):
            other = text  # join of either side must rebuild the text
            b_tok, a_tok = self.mod._diff_tokens(text, other)
            self.assertEqual("".join(b_tok), text)
            self.assertEqual("".join(a_tok), other)

    def test_equal_inputs_produce_no_del_ins(self):
        s = "No change here at all"
        out = self.diff_html(s, s)
        self.assertNotIn("<del", out)
        self.assertNotIn("<ins", out)


class TestExportChangesDiff(_DiffSizeGuardMixin, unittest.TestCase):
    mod = ec

    def diff_html(self, b, a):
        return ec.word_diff_html(b, a)

    def diff_text(self, b, a):
        return ec.word_diff_text(b, a)


class TestExportMrPipelineDiff(_DiffSizeGuardMixin, unittest.TestCase):
    mod = mp

    def diff_html(self, b, a):
        return mp._word_diff_html(b, a)

    def diff_text(self, b, a):
        return mp._word_diff_text(b, a)


if __name__ == "__main__":
    unittest.main()
