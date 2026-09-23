"""MR Pipeline layout: KPI block in the filter card + Recently Added drawer.

The sidebar used to be a fixed 280px pane whose ttk.Label titles had no
``wraplength`` and whose Recently Added list was a two-column Treeview.
On a maximized window that clipped "Recently Added Projects" and every
``group/subgroup/project`` path. Later the Trans MR columns pushed the
table's requested width past the window and the sidebar (packed after the
table) was squeezed to 0px; the KPIs moved into the filter card and the
project list became a collapsible drawer packed before the table.
Helpers here are display-free; the widget classes build real widgets and
are skipped without a Tk display.

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

    def test_default_1280_window_stays_at_the_floor(self):
        # The drawer only holds the project list now; on the 1280px default
        # geometry it stays at its floor so the table keeps the width.
        self.assertEqual(gt._mr_sidebar_width(1280), gt._MR_SIDEBAR_MIN_PX)

    def test_maximized_window_gives_the_drawer_some_spare_width(self):
        compact = gt._mr_sidebar_width(1280)
        wide = gt._mr_sidebar_width(1920)
        self.assertGreater(wide, compact)
        self.assertLessEqual(wide, gt._MR_SIDEBAR_MAX_PX)

    def test_ultra_wide_is_capped(self):
        self.assertEqual(gt._mr_sidebar_width(4000), gt._MR_SIDEBAR_MAX_PX)

    def test_narrow_pane_never_exceeds_table_reserve(self):
        width = gt._mr_sidebar_width(500)
        self.assertLessEqual(width, 500 * (1 - gt._MR_SIDEBAR_TABLE_RESERVE) + 1)
        self.assertGreaterEqual(width, 180)


class DrawerStateTests(unittest.TestCase):

    def test_follows_the_pane_width_until_the_user_chooses(self):
        self.assertFalse(gt._mr_drawer_should_open(1280))
        self.assertFalse(gt._mr_drawer_should_open(
            gt._MR_DRAWER_AUTO_OPEN_PX - 1))
        self.assertTrue(gt._mr_drawer_should_open(gt._MR_DRAWER_AUTO_OPEN_PX))
        self.assertTrue(gt._mr_drawer_should_open(1880))

    def test_saved_choice_wins_over_width(self):
        self.assertTrue(gt._mr_drawer_should_open(900, saved=True))
        self.assertFalse(gt._mr_drawer_should_open(2400, saved=False))

    def test_non_bool_saved_value_is_ignored(self):
        self.assertTrue(gt._mr_drawer_should_open(2000, saved="no"))
        self.assertFalse(gt._mr_drawer_should_open(None))
        self.assertFalse(gt._mr_drawer_should_open("x"))


class KpiPlacementTests(unittest.TestCase):

    def test_inline_when_rows_and_kpis_fit(self):
        self.assertTrue(gt._mr_kpi_fits_inline(1800, 1150, 240))

    def test_drops_under_the_rows_when_cramped(self):
        self.assertFalse(gt._mr_kpi_fits_inline(1250, 1150, 240))

    def test_gap_is_counted(self):
        self.assertTrue(gt._mr_kpi_fits_inline(1000, 700, 276, gap=24))
        self.assertFalse(gt._mr_kpi_fits_inline(999, 700, 276, gap=24))

    def test_invalid_input_is_not_inline(self):
        self.assertFalse(gt._mr_kpi_fits_inline(0, 0, 0))
        self.assertFalse(gt._mr_kpi_fits_inline(None, 100, 100))


class ToggleTextAndNumberTests(unittest.TestCase):

    def test_toggle_text_carries_count_and_direction(self):
        self.assertEqual(
            gt._mr_drawer_toggle_text("📦 Recently Added", 40, False),
            "📦 Recently Added (40) ◂")
        self.assertEqual(
            gt._mr_drawer_toggle_text("📦 Recently Added", 0, True),
            "📦 Recently Added (0) ▸")

    def test_unknown_count_is_omitted(self):
        self.assertEqual(
            gt._mr_drawer_toggle_text("📦 Recently Added", None, True),
            "📦 Recently Added ▸")

    def test_kpi_numbers_get_thousands_separators(self):
        self.assertEqual(gt._format_kpi_number(68351), "68,351")
        self.assertEqual(gt._format_kpi_number("14636"), "14,636")
        self.assertEqual(gt._format_kpi_number(898), "898")
        self.assertEqual(gt._format_kpi_number("97.09"), "97.09")
        self.assertEqual(gt._format_kpi_number(""), "—")
        self.assertEqual(gt._format_kpi_number(None), "—")


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
                    "mr_recent_empty", "mr_recent_toggle",
                    "mr_recent_toggle_tip_show", "mr_recent_toggle_tip_hide"):
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
                int(tab.lbl_mr_recent_projects_title.cget("wraplength") or 0),
                80)
            self.assertFalse(hasattr(tab, "mr_recent_tree"))
            self.assertIsNotNone(tab._recent_canvas)
            # The KPIs no longer live in the drawer.
            self.assertFalse(hasattr(tab, "mr_stat_labels"))
        finally:
            _host.destroy()

    def _kpis(self):
        tab = gt.MRPipelineTab.__new__(gt.MRPipelineTab)
        tab.app = self._fake_app()
        tab.parent = self.root
        body = self.ttk.Frame(self.root)
        body.columnconfigure(0, weight=1)
        rows = self.ttk.Frame(body, width=600, height=40)
        rows.grid(row=0, column=0, sticky="nw")
        tab._mr_card_body = body
        tab._mr_filter_rows = rows
        tab._build_mr_kpis(body)
        return tab, body

    def test_kpis_switch_between_beside_and_under_the_rows(self):
        tab, body = self._kpis()
        try:
            self.assertEqual(set(tab.mr_stat_labels), {
                "total", "completed", "failed", "avg_score"})
            self.assertEqual(
                int(tab._mr_kpi_frame.grid_info()["column"]), 1)
            tab._layout_mr_kpis(inline=False)
            info = tab._mr_kpi_frame.grid_info()
            self.assertEqual((int(info["row"]), int(info["column"])), (1, 0))
            cols = {int(c.grid_info()["column"]) for c in tab._mr_kpi_cells}
            self.assertEqual(cols, {0, 1, 2, 3})
            tab._layout_mr_kpis(inline=True)
            rows = {int(c.grid_info()["row"]) for c in tab._mr_kpi_cells}
            self.assertEqual(rows, {0, 1})
        finally:
            body.destroy()

    def test_open_drawer_is_not_squeezed_by_a_too_wide_table(self):
        """The screenshot bug: a table wider than the window hid the pane."""
        tk, ttk = self.tk, self.ttk
        top = tk.Toplevel(self.root)
        top.geometry("900x300")
        try:
            content = ttk.Frame(top, width=900, height=300)
            content.pack(fill="both", expand=True)
            right = ttk.Frame(content, width=gt._MR_SIDEBAR_MIN_PX)
            right.pack_propagate(False)
            left = ttk.Frame(content)
            left.pack(side="left", fill="both", expand=True)
            # A child that asks for far more than the window has, like the
            # 17-column Treeview.
            ttk.Frame(left, width=2400, height=100).pack()

            tab = gt.MRPipelineTab.__new__(gt.MRPipelineTab)
            tab.app = self._fake_app()
            tab._mr_sidebar_frame = right
            tab._mr_left = left
            tab._apply_mr_drawer(True)
            top.update_idletasks()
            top.update()
            self.assertTrue(right.winfo_ismapped())
            self.assertGreater(right.winfo_width(), 100)

            tab._apply_mr_drawer(False)
            top.update()
            self.assertFalse(right.winfo_ismapped())
            self.assertFalse(tab._mr_drawer_open)
        finally:
            top.destroy()

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
