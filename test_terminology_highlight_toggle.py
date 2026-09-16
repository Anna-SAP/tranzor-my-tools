"""Tests for the HTML report terminology-highlight toggle.

The <mark class="term-hl"> markup stays in the report; a toolbar switch
strips the yellow paint via html.term-hl-off so a reviewer can read a
clean screen without re-exporting.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import terminology_highlight as th
import export_mr_pipeline as mr_api
import export_translations
import export_changes
import quality_overview
import gui_tab_human_revisions as hr


def _offline_highlight():
    return (
        mock.patch.object(th, "prefetch_for_rows", lambda *a, **k: None),
        mock.patch.object(th, "highlight_source", lambda s: s),
        mock.patch.object(th, "highlight_translation", lambda s, loc=None: s),
    )


class ToggleSnippetTests(unittest.TestCase):

    def test_css_has_on_and_off_states(self):
        css = th.HIGHLIGHT_CSS
        self.assertIn("mark.term-hl", css)
        self.assertIn("html.term-hl-off mark.term-hl", css)
        self.assertIn(".term-hl-toggle", css)
        self.assertIn("background: transparent", css)
        # Must be valid CSS: doubled braces leak into the HTML <style>
        # block and the UA yellow <mark> style wins over a broken rule.
        self.assertNotIn("{{", css)
        self.assertNotIn("}}", css)

    def test_toggle_control_is_a_checkbox_switch(self):
        html = th.HIGHLIGHT_TOGGLE_HTML
        self.assertIn('id="termHlToggle"', html)
        self.assertIn('class="term-hl-toggle"', html)
        self.assertIn("Term highlight", html)
        self.assertIn("checked", html)

    def test_js_toggles_root_class_and_persists(self):
        js = th.HIGHLIGHT_TOGGLE_JS
        self.assertIn("term-hl-off", js)
        self.assertIn("tranzor-term-highlight", js)
        self.assertIn("setTermHighlight", js)
        self.assertIn('localStorage.setItem(KEY, on ? "on" : "off")', js)

    def test_head_snippet_applies_off_before_paint(self):
        self.assertIn("term-hl-off", th.HIGHLIGHT_TOGGLE_HEAD)
        self.assertIn("tranzor-term-highlight", th.HIGHLIGHT_TOGGLE_HEAD)
        self.assertNotIn("{{", th.HIGHLIGHT_TOGGLE_HEAD)
        self.assertNotIn("{{", th.HIGHLIGHT_TOGGLE_JS)


class ReportEmbedTests(unittest.TestCase):

    def _assert_toggle_embedded(self, html: str):
        self.assertIn('id="termHlToggle"', html)
        self.assertIn("Term highlight", html)
        self.assertIn("html.term-hl-off mark.term-hl", html)
        self.assertIn("mark.term-hl {", html)
        self.assertNotIn("mark.term-hl {{", html)
        self.assertIn("window.setTermHighlight", html)
        self.assertIn('localStorage.getItem("tranzor-term-highlight")', html)
        # Head + body scripts both present (anti-flash + bind).
        self.assertGreaterEqual(html.count("tranzor-term-highlight"), 2)

    def test_mr_pipeline_html_embeds_toggle_at_toolbar_end(self):
        pre, src, tr = _offline_highlight()
        with pre, src, tr, tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mr.html")
            mr_api.write_mr_html(
                {"translations": [{
                    "opus_id": "k",
                    "source_text": "Voice Message",
                    "translated_text": "음성 메시지",
                    "target_language": "ko-KR",
                    "project_id": "web/web",
                    "mr_id": "1",
                }], "summary": {}},
                path, "label", bridge_info=None,
            )
            with open(path, encoding="utf-8") as handle:
                html = handle.read()
        self._assert_toggle_embedded(html)
        self.assertLess(
            html.index('id="btnFilterToggle"'),
            html.index('id="termHlToggle"'),
        )
        toolbar_html = html.split('<div class="tmx-toolbar">', 1)[1]
        toolbar_html = toolbar_html.split("<!-- Filter Panel -->", 1)[0]
        self.assertIn('id="termHlToggle"', toolbar_html)

    def test_translations_html_embeds_toggle(self):
        pre, src, tr = _offline_highlight()
        with pre, src, tr, tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tr.html")
            export_translations.write_html(
                [{
                    "source_text": "Hello",
                    "translated_text": "Hallo",
                    "language": "de-DE",
                    "string_key": "k",
                    "translation_type": "MT",
                    "task_id": "1",
                    "task_name": "t",
                }],
                path, "label",
            )
            with open(path, encoding="utf-8") as handle:
                html = handle.read()
        self._assert_toggle_embedded(html)

    def test_changes_html_embeds_toggle(self):
        pre, src, tr = _offline_highlight()
        with pre, src, tr, tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ch.html")
            export_changes.write_html(
                [{
                    "source_text": "Hello",
                    "before": "Hallo",
                    "after": "Guten Tag",
                    "language": "de-DE",
                    "string_key": "k",
                    "edit_time": "2026-09-16T12:00:00",
                    "editor": "Ada",
                    "task_id": "1",
                    "task_name": "t",
                    "notes": "",
                }],
                path, "label",
            )
            with open(path, encoding="utf-8") as handle:
                html = handle.read()
        self._assert_toggle_embedded(html)

    def test_human_revisions_html_embeds_floating_toggle(self):
        pre, src, tr = _offline_highlight()
        with pre, src, tr:
            html = hr._render_html_report([], {
                "start_time": "2026-09-01T00:00:00",
                "end_time": "2026-09-16T23:59:59",
            }).decode("utf-8")
        self._assert_toggle_embedded(html)
        self.assertIn("<body>", html)
        body = html.split("<body>", 1)[1]
        self.assertTrue(
            body.lstrip().startswith('<label class="term-hl-toggle"'),
            "standalone reports pin the toggle as a body child so CSS "
            "can float it to the top-right",
        )

    def test_quality_overview_html_embeds_floating_toggle(self):
        pre, src, tr = _offline_highlight()
        aggregated = {
            "threshold": 98,
            "error_distribution": {},
            "score_distribution": {},
            "by_language": [],
            "low_items": [],
            "total_tasks": 0,
            "total_items": 0,
            "overall_avg_score": 0,
            "below_threshold_rate": 0,
            "refined_rate": 0,
            "human_touch_rate": 0,
        }
        with pre, src, tr, tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "qo.html")
            quality_overview.write_quality_html(aggregated, path, "label")
            with open(path, encoding="utf-8") as handle:
                html = handle.read()
        self._assert_toggle_embedded(html)


if __name__ == "__main__":
    unittest.main()
