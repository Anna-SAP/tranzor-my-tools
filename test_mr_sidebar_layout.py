"""MR Pipeline right-sidebar layout: width, wrapping, no clipped paths.

The sidebar used to be a fixed 280px pane whose ttk.Label titles had no
``wraplength`` and whose Recently Added list was a two-column Treeview.
On a maximized window that clipped "Recently Added Projects" and every
``group/subgroup/project`` path. Helpers here are display-free; the last
class builds the real widgets and is skipped without a Tk display.

Run:  python -m unittest test_mr_sidebar_layout
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gui_tabs as gt


class SidebarWidthTests(unittest.TestCase):

    def test_default_and_invalid_inputs_use_the_floor(self):
        self.assertEqual(gt._mr_sidebar_width(0), gt._MR_SIDEBAR_MIN_PX)
        self.assertEqual(gt._mr_sidebar_width(-10), gt._MR_SIDEBAR_MIN_PX)
        self.assertEqual(gt._mr_sidebar_width(None), gt._MR_SIDEBAR_MIN_PX)
        self.assertEqual(gt._mr_sidebar_width("nope"), gt._MR_SIDEBAR_MIN_PX)

    def test_default_1280_window_stays_near_the_floor(self):
        # 1280px default geometry: sidebar grows only a little past 280 so
        # the 15-column table still fits.
        width = gt._mr_sidebar_width(1280)
        self.assertGreaterEqual(width, gt._MR_SIDEBAR_MIN_PX)
        self.assertLess(width, 380)

    def test_maximized_window_gives_the_sidebar_the_spare_width(self):
        compact = gt._mr_sidebar_width(1280)
        wide = gt._mr_sidebar_width(1920)
        self.assertGreater(wide, compact)
        self.assertGreaterEqual(wide, 400)
        self.assertLessEqual(wide, gt._MR_SIDEBAR_MAX_PX)

    def test_ultra_wide_is_capped(self):
        self.assertEqual(gt._mr_sidebar_width(4000), gt._MR_SIDEBAR_MAX_PX)

    def test_narrow_pane_never_exceeds_table_reserve(self):
        width = gt._mr_sidebar_width(500)
        self.assertLessEqual(width, 500 * (1 - gt._MR_SIDEBAR_TABLE_RESERVE) + 1)
        self.assertGreaterEqual(width, 180)


class WraplengthTests(unittest.TestCase):

    def test_wraplength_is_strictly_inside_the_pane(self):
        wrap = gt._mr_sidebar_wraplength(360)
        self.assertLess(wrap, 360)
        self.assertGreaterEqual(wrap, 80)
        self.assertEqual(wrap, 360 - gt._MR_SIDEBAR_INNER_PAD_PX)

    def test_age_column_reserve_leaves_room_for_the_path(self):
        wrap = gt._mr_sidebar_wraplength(360, reserve=gt._MR_RECENT_AGE_RESERVE_PX)
        self.assertGreaterEqual(wrap, 80)
        self.assertEqual(
            wrap,
            360 - gt._MR_SIDEBAR_INNER_PAD_PX - gt._MR_RECENT_AGE_RESERVE_PX)

    def test_tiny_or_invalid_width_still_wraps(self):
        self.assertEqual(gt._mr_sidebar_wraplength(10), 80)
        self.assertEqual(gt._mr_sidebar_wraplength(None), 80)
        self.assertEqual(gt._mr_sidebar_wraplength("x"), 80)


class RecentTooltipTests(unittest.TestCase):

    def test_full_path_is_always_present(self):
        text = gt._recent_project_tooltip(
            "copilot-platform/business-analytics", "2d ago",
            "2026-09-14 10:00 UTC+8")
        self.assertIn("copilot-platform/business-analytics", text)
        self.assertIn("2d ago", text)
        self.assertIn("2026-09-14", text)
        self.assertEqual(text.count("\n"), 2)

    def test_blank_parts_are_dropped(self):
        self.assertEqual(
            gt._recent_project_tooltip("dash/dash"), "dash/dash")
        self.assertEqual(
            gt._recent_project_tooltip("dash/dash", "", ""), "dash/dash")
        self.assertEqual(gt._recent_project_tooltip(""), "")


class BreakProjectPathTests(unittest.TestCase):

    def test_short_path_stays_on_one_line(self):
        self.assertEqual(
            gt._break_project_path("dash/dash", 20, len), "dash/dash")

    def test_breaks_at_slash_and_hyphen_not_mid_token(self):
        out = gt._break_project_path(
            "copilot-platform/business-analytics", 10, len)
        self.assertEqual(
            out, "copilot-\nplatform/\nbusiness-\nanalytics")
        self.assertNotIn("platfo", out.split("\n")[0])

    def test_empty_passes_through(self):
        self.assertEqual(gt._break_project_path("", 10, len), "")
        self.assertEqual(gt._break_project_path("web", 10, len), "web")


class SidebarI18nTests(unittest.TestCase):

    def test_titles_exist_in_both_languages(self):
        from export_gui import STRINGS
        for lang in ("en", "zh"):
            for key in (
                    "mr_sidebar_title", "mr_sidebar_title_stage",
                    "mr_stat_total", "mr_stat_completed", "mr_stat_failed",
                    "mr_stat_avg_score", "mr_recent_projects_title",
                    "mr_recent_empty"):
                self.assertIn(key, STRINGS[lang], key)
                self.assertTrue(STRINGS[lang][key].strip(), key)

    def test_english_titles_are_the_strings_that_used_to_clip(self):
        from export_gui import STRINGS
        self.assertIn("Recently Added", STRINGS["en"]["mr_recent_projects_title"])
        self.assertIn("MR Pipeline", STRINGS["en"]["mr_sidebar_title"])


class SidebarWidgetSmokeTests(unittest.TestCase):
    """Build the real sidebar widgets; skipped when Tk has no display."""

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
            from tkinter import ttk
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"tkinter unavailable: {e}")
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"no display for Tk: {e}")
        cls.tk = tk
        cls.ttk = ttk

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def _fake_app(self):
        from export_gui import STRINGS

        class _App:
            BG = "#1a1a2e"
            BG_CARD = "#16213e"
            lang = "en"

            def _t(self, key):
                return STRINGS["en"].get(key, key)

            def _create_button(self, parent, **kw):
                return self_tk.Button(
                    parent, text=kw.get("text", "") or "↻")

        self_tk = self.tk
        return _App()

    def _sidebar(self):
        tab = gt.MRPipelineTab.__new__(gt.MRPipelineTab)
        tab.app = self._fake_app()
        tab.parent = self.root
        tab.env_key = "prod"
        tab._recent_projects_loading = False
        tab._last_recent_projects = []
        host = self.ttk.Frame(self.root, width=gt._MR_SIDEBAR_MIN_PX)
        host.pack_propagate(False)
        tab._mr_sidebar_frame = host
        tab._build_mr_sidebar(host)
        return tab, host

    def test_titles_have_wraplength_and_treeview_is_gone(self):
        tab, _host = self._sidebar()
        try:
            self.assertGreater(
                int(tab.lbl_mr_sidebar_title.cget("wraplength") or 0), 80)
            self.assertGreater(
                int(tab.lbl_mr_recent_projects_title.cget("wraplength") or 0),
                80)
            self.assertFalse(hasattr(tab, "mr_recent_tree"))
            self.assertIsNotNone(tab._recent_canvas)
            self.assertEqual(set(tab.mr_stat_labels), {
                "total", "completed", "failed", "avg_score"})
        finally:
            _host.destroy()

    def test_long_project_paths_are_stored_in_full_on_the_label(self):
        tab, _host = self._sidebar()
        try:
            long_pid = "copilot-platform/business-analytics-service"
            tab._t = tab.app._t
            tab._render_recent_projects([
                {"project_id": long_pid, "first_seen": "2026-09-14T10:00:00"},
                {"project_id": "dash/dash", "first_seen": "2026-09-16T01:00:00"},
            ])
            texts = [lbl.cget("text") for lbl in tab._recent_name_labels]
            self.assertEqual(texts[0].replace("\n", ""), long_pid)
            self.assertTrue(any(t.replace("\n", "") == "dash/dash" for t in texts))
            self.assertEqual(tab._recent_name_paths[0], long_pid)
            for lbl in tab._recent_name_labels:
                self.assertGreater(int(lbl.cget("wraplength") or 0), 80)
        finally:
            _host.destroy()

    def test_empty_and_loading_states_wrap(self):
        tab, _host = self._sidebar()
        try:
            tab._t = tab.app._t
            tab._show_recent_projects_loading()
            children = tab._recent_inner.winfo_children()
            self.assertTrue(children)
            self.assertGreater(
                int(children[0].cget("wraplength") or 0), 80)
            tab._render_recent_projects([])
            children = tab._recent_inner.winfo_children()
            self.assertIn("No data", children[0].cget("text"))
        finally:
            _host.destroy()

    def test_long_section_title_wraps_inside_a_cramped_pane(self):
        """The screenshot bug: 200px pane clipped 'Recently Added Projects'.

        wraplength must be inside the pane, and the label's requested width
        after wrapping must not exceed that wraplength (i.e. it wraps
        instead of painting a single overflowing line).
        """
        tab, _host = self._sidebar()
        try:
            title = "📦 Recently Added Projects"
            tab.lbl_mr_recent_projects_title.configure(text=title)
            tab._apply_sidebar_wraplengths(200)
            self.root.update_idletasks()
            wrap = int(tab.lbl_mr_recent_projects_title.cget("wraplength") or 0)
            self.assertLessEqual(wrap, 200)
            self.assertGreaterEqual(wrap, 80)
            self.assertLessEqual(
                int(tab.lbl_mr_recent_projects_title.winfo_reqwidth()),
                wrap + 8)
            self.assertGreater(
                int(tab.lbl_mr_recent_projects_title.winfo_reqheight()), 12)
        finally:
            _host.destroy()


if __name__ == "__main__":
    unittest.main()
