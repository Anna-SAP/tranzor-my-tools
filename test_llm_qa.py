"""Tests for llm_qa's pure (Tk-free) helpers.

The "Send to LLM QA" feature's user-facing text and the fixed LQA prompt live
in ``llm_qa``. The prompt string is a contract with the downstream
``/rc-core-products-trans-checker`` skill, so it is pinned here verbatim — a
drift would silently break the workflow. The clipboard helper's control flow
(success vs. swallow-and-report-False) is exercised with lightweight fakes so
no real Tk display is needed.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llm_qa


# The exact prompt the user specified. Pinned verbatim: the downstream skill's
# trigger depends on this text, so any change must be deliberate and land here.
EXPECTED_PROMPT = (
    "/rc-core-products-trans-checker 检查附件JSON这批翻译的质量，重点关注 Critical 问题。"
)

# The exact core sentence the product requirement mandates for the toast.
EXPECTED_ZH_SENTENCE = (
    "JSON 文件已导出，且 LLM 提示词已复制到剪贴板，请前往 LLM 对话窗口"
    "上传附件并粘贴提示词。"
)


class PromptTests(unittest.TestCase):
    def test_prompt_is_pinned_verbatim(self):
        self.assertEqual(llm_qa.LLM_QA_PROMPT, EXPECTED_PROMPT)

    def test_prompt_starts_with_skill_slash_command(self):
        self.assertTrue(
            llm_qa.LLM_QA_PROMPT.startswith("/rc-core-products-trans-checker "))


class ButtonLabelTests(unittest.TestCase):
    def test_en_and_zh_differ_and_nonempty(self):
        en = llm_qa.button_label("en")
        zh = llm_qa.button_label("zh")
        self.assertTrue(en and zh)
        self.assertNotEqual(en, zh)
        self.assertIn("LLM QA", en)

    def test_unknown_lang_falls_back_to_en(self):
        self.assertEqual(llm_qa.button_label("fr"), llm_qa.button_label("en"))
        self.assertEqual(llm_qa.button_label(None), llm_qa.button_label("en"))

    def test_zh_variants_normalize(self):
        # "zh", "zh-CN", "ZH_hans" etc. all map to the Chinese label.
        zh = llm_qa.button_label("zh")
        self.assertEqual(llm_qa.button_label("zh-CN"), zh)
        self.assertEqual(llm_qa.button_label("ZH_Hans"), zh)


class BuildMessageTests(unittest.TestCase):
    def test_zh_ok_contains_required_sentence_and_filename(self):
        title, body = llm_qa.build_message(
            "tranzor_task_323_translations_2026-07-02.json",
            lang="zh", copied=True)
        self.assertTrue(title)
        self.assertIn(EXPECTED_ZH_SENTENCE, body)
        self.assertIn("tranzor_task_323_translations_2026-07-02.json", body)

    def test_en_ok_contains_filename_and_mentions_clipboard(self):
        title, body = llm_qa.build_message("foo.json", lang="en", copied=True)
        self.assertIn("foo.json", body)
        self.assertIn("clipboard", body.lower())

    def test_noclip_includes_prompt_for_manual_copy(self):
        # When the clipboard write failed the user must still be able to copy
        # the prompt by hand, so the full prompt has to appear in the body.
        _title, body = llm_qa.build_message("foo.json", lang="zh", copied=False)
        self.assertIn(llm_qa.LLM_QA_PROMPT, body)
        self.assertIn("foo.json", body)

    def test_missing_filename_does_not_crash(self):
        # Defensive: an empty filename must format cleanly (no KeyError / None).
        _title, body = llm_qa.build_message("", lang="en", copied=True)
        self.assertIsInstance(body, str)


class _FakeClipboardWidget:
    """Minimal stand-in for a Tk widget's clipboard surface (no real display)."""

    def __init__(self, fail_on=None):
        self.fail_on = fail_on            # method name that should raise
        self.appended = None
        self.updated = False
        self.cleared = False

    def _maybe_fail(self, name):
        if self.fail_on == name:
            raise RuntimeError(f"boom in {name}")

    def clipboard_clear(self):
        self._maybe_fail("clipboard_clear")
        self.cleared = True

    def clipboard_append(self, text):
        self._maybe_fail("clipboard_append")
        self.appended = text

    def update(self):
        self._maybe_fail("update")
        self.updated = True


class CopyPromptTests(unittest.TestCase):
    def test_success_path_clears_appends_and_updates(self):
        w = _FakeClipboardWidget()
        ok = llm_qa.copy_prompt_to_clipboard(w)
        self.assertTrue(ok)
        self.assertTrue(w.cleared)
        self.assertEqual(w.appended, llm_qa.LLM_QA_PROMPT)
        # update() is essential on Windows or the clipboard empties on exit.
        self.assertTrue(w.updated)

    def test_custom_text_is_written(self):
        w = _FakeClipboardWidget()
        llm_qa.copy_prompt_to_clipboard(w, "hello")
        self.assertEqual(w.appended, "hello")

    def test_any_failure_is_swallowed_and_returns_false(self):
        for stage in ("clipboard_clear", "clipboard_append", "update"):
            with self.subTest(stage=stage):
                w = _FakeClipboardWidget(fail_on=stage)
                self.assertFalse(llm_qa.copy_prompt_to_clipboard(w))


if __name__ == "__main__":
    unittest.main()
