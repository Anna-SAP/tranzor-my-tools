"""Real-Tk tests for the Data Lookback window with injected data sources.

Skipped when Tk cannot open a display. The window is driven through its
public ``load`` / ``refresh_text`` / ``close`` plus the injected fetchers, and
the background thread's results are pumped with ``update()``.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_lookback as dl
import gui_data_lookback as gdl


class _FakeApp:
    BG = "#1a1a2e"
    BG_CARD = "#16213e"
    FG = "#e0e0e0"
    ACCENT = "#0f3460"
    ACCENT_BTN = "#e94560"

    def __init__(self, root):
        self.root = root
        self.lang = "en"

    def _t(self, key):
        return gdl.STRINGS[self.lang].get(key, key)

    @staticmethod
    def _create_button(parent, *, text="", command=None, **kw):
        import tkinter as tk
        return tk.Button(parent, text=text, command=command)

    def _mark_busy(self, label, text):
        label.configure(text=f"⏳ {text}")

    def _mark_idle(self, label, text=""):
        label.configure(text=text)

    def _mark_hint(self, label, text):
        label.configure(text=text)


def _day_data(errors=None, bugfix=True):
    return {
        "mr": [
            {"task_id": "a", "project_id": "web/web", "merge_request_iid": 1,
             "status": "completed", "created_at": "2026-09-24T01:00:00"},
            {"task_id": "b", "project_id": "web/web", "merge_request_iid": 1,
             "status": "completed", "created_at": "2026-09-24T02:00:00"},
            {"task_id": "c", "project_id": "Fiji/video", "merge_request_iid": 7,
             "status": "completed", "created_at": "2026-09-24T03:00:00"},
        ],
        "bugfix": ([{"project_id": "web/bui", "platform_status": "applied"}]
                   if bugfix else None),
        "scan": [{"project_id": "iva/iva-ui", "status": "completed"}],
        "errors": errors or {},
    }


class DataLookbackWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception as exc:
            raise unittest.SkipTest(f"Tk unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.app = _FakeApp(self.root)
        self.fetches = []
        self.meta_gate = threading.Event()
        self.meta_gate.set()
        self.data = _day_data()
        self.can_resolve = True

    def make(self, day="2026-09-24"):
        def fetch_day(d, cancel_event=None):
            self.fetches.append(d)
            return self.data

        def resolve(keys, cancel_event=None, on_progress=None):
            self.meta_gate.wait(5)
            if on_progress:
                on_progress(len(keys), len(keys))
            return {("web/web", 1): {"branch": "develop", "jira": "UIA-9",
                                     "title": "Add thing"}}

        win = gdl.DataLookbackWindow(
            self.app, font_family="Segoe UI", day=day, fetch_day=fetch_day,
            resolve_meta=resolve, can_resolve=lambda: self.can_resolve)
        self.addCleanup(win.close)
        return win

    def spin(self, until=lambda: False, timeout=5.0):
        """Run the real mainloop until ``until()`` or ``timeout``.

        Worker threads post with ``root.after``; Tkinter only accepts that
        from another thread while the main thread sits in ``mainloop``.
        """
        end = time.time() + timeout

        def check():
            if until() or time.time() > end:
                self.root.quit()
            else:
                self.root.after(10, check)

        self.root.after(0, check)
        self.root.mainloop()

    def pump(self, win, state="done", timeout=5):
        self.spin(lambda: win._state == state, timeout)
        if win._state != state:
            self.fail(f"window never reached {state!r} (at {win._state!r})")

    def rows(self, win, parent=""):
        return [(iid, win.tree.item(iid, "text"), tuple(win.tree.item(iid, "values")))
                for iid in win.tree.get_children(parent)]

    def test_hierarchy_counts_and_branches(self):
        win = self.make()
        self.pump(win)
        cats = self.rows(win)
        self.assertEqual([c[0] for c in cats],
                         [f"cat:{c}" for c in dl.CATEGORY_ORDER])
        web = dict((c[0], c) for c in cats)["cat:WEB"]
        self.assertEqual(web[2], ("1", "develop", "1", "0"))
        projects = self.rows(win, "cat:WEB")
        self.assertEqual([p[1] for p in projects], ["web/bui", "web/web"])
        self.assertEqual(dict((p[1], p[2]) for p in projects)["web/web"],
                         ("1", "develop", "", ""))
        mrs = self.rows(win, "prj:web/web")
        self.assertEqual(len(mrs), 1)
        self.assertIn("!1  UIA-9  Add thing", mrs[0][1])
        self.assertIn("×2", mrs[0][1])
        self.assertEqual(mrs[0][2][1], "develop")
        rcv = self.rows(win, "cat:RCV")
        self.assertEqual(rcv[0][2][1], gdl.STRINGS["en"]["dl_branch_unknown"])
        kpis = {k: v[1].cget("text") for k, v in win.kpi_labels.items()}
        # Fiji/video !7 did not resolve, so the branch count is a floor.
        self.assertEqual(kpis, {"mrs": "2", "runs": "3", "branches": "≥1",
                                "bugfix": "1", "scan": "1", "projects": "4"})
        self.assertIn("1 MR(s) with unknown target branch",
                      win.lbl_notes.cget("text"))

    def test_branches_show_pending_until_resolved(self):
        self.meta_gate.clear()
        win = self.make()
        self.pump(win, state="resolving")
        web = [c for c in self.rows(win) if c[0] == "cat:WEB"][0]
        self.assertEqual(web[2][1], "…")
        self.assertEqual(win.kpi_labels["branches"][1].cget("text"), "…")
        self.meta_gate.set()
        self.pump(win)
        web = [c for c in self.rows(win) if c[0] == "cat:WEB"][0]
        self.assertEqual(web[2][1], "develop")

    def test_show_idle_lists_all_projects(self):
        win = self.make()
        self.pump(win)
        self.assertEqual(len(self.rows(win, "cat:DPW")), 0)
        win.show_idle_var.set(True)
        win._render()
        self.assertEqual(sum(len(self.rows(win, c[0])) for c in self.rows(win)), 65)

    def test_failed_source_shows_dash_and_reason(self):
        self.data = _day_data(errors={"bugfix": ("forbidden", "403")},
                              bugfix=False)
        win = self.make()
        self.pump(win)
        web = [c for c in self.rows(win) if c[0] == "cat:WEB"][0]
        self.assertEqual(web[2][2], "—")
        self.assertEqual(win.kpi_labels["bugfix"][1].cget("text"), "—")
        self.assertIn("Language Lead", win.lbl_notes.cget("text"))
        # A day with a failed source is not cached: reopening refetches.
        n = len(self.fetches)
        win.load("2026-09-23")
        self.pump(win)
        win.load("2026-09-24")
        self.pump(win)
        self.assertEqual(len(self.fetches), n + 2)

    def test_no_gitlab_token(self):
        self.can_resolve = False
        win = self.make()
        self.pump(win)
        web = [c for c in self.rows(win) if c[0] == "cat:WEB"][0]
        self.assertEqual(web[2][1], "—")
        self.assertIn("GitLab token", win.lbl_notes.cget("text"))

    def test_past_days_are_cached_and_refresh_forces(self):
        win = self.make()
        self.pump(win)
        win.load("2026-09-23")
        self.pump(win)
        win.load("2026-09-24")
        self.pump(win)
        self.assertEqual(self.fetches, [dl.coerce_day("2026-09-24"),
                                        dl.coerce_day("2026-09-23")])
        win.load(win.day, force=True)
        self.pump(win)
        self.assertEqual(len(self.fetches), 3)

    def test_navigation_is_capped_at_today(self):
        today = dl.today_utc8()
        win = self.make(day=today - timedelta(days=1))
        self.pump(win)
        self.assertEqual(str(win.btn_next.cget("state")), "normal")
        win._step(1)
        self.pump(win)
        self.assertEqual(win.day, today)
        self.assertEqual(str(win.btn_next.cget("state")), "disabled")
        self.assertIn(", today)", win.lbl_day.cget("text"))
        win.load(today + timedelta(days=5))
        self.pump(win)
        self.assertEqual(win.day, today)
        # Today is never served from cache.
        n = len(self.fetches)
        win.load(today)
        self.pump(win)
        self.assertEqual(len(self.fetches), n + 1)

    def test_expansion_survives_rerender_and_language_switch(self):
        win = self.make()
        self.pump(win)
        win.tree.item("cat:RCV", open=False)
        win.tree.item("prj:web/web", open=True)
        self.app.lang = "zh"
        win.refresh_text()
        self.assertFalse(win.tree.item("cat:RCV", "open"))
        self.assertTrue(win.tree.item("prj:web/web", "open"))
        self.assertEqual(win.tree.heading("#0", "text"), "类别 / 项目")
        self.assertIn("周四", win.lbl_day.cget("text"))

    def test_stale_results_are_dropped_after_close(self):
        self.meta_gate.clear()
        win = self.make()
        self.pump(win, state="resolving")
        win.close()
        self.meta_gate.set()
        self.spin(timeout=0.3)
        self.assertFalse(win.exists())
        self.assertEqual(win._state, "resolving")

    def test_copy_puts_tsv_on_clipboard(self):
        win = self.make()
        self.pump(win)
        win._copy()
        text = self.root.clipboard_get()
        self.assertTrue(text.startswith("2026-09-24 (Thu)"))
        self.assertIn("WEB\tweb/web\t1\tdevelop\t0\t0", text)

    def test_copy_without_gitlab_token_marks_branches_unavailable(self):
        self.can_resolve = False
        win = self.make()
        self.pump(win)
        win._copy()
        self.assertIn("WEB\tweb/web\t1\t—\t0\t0", self.root.clipboard_get())

    def test_refresh_reenabled_when_leaving_a_slow_day_for_a_cached_one(self):
        win = self.make()
        self.pump(win)
        gate = threading.Event()
        fast = win._fetch_day

        def slow(d, cancel_event=None):
            gate.wait(5)
            return fast(d, cancel_event=cancel_event)

        win._fetch_day = slow
        win.load("2026-09-23")
        self.spin(timeout=0.1)
        self.assertEqual(str(win.btn_refresh.cget("state")), "disabled")
        win.load("2026-09-24")          # cached
        self.pump(win)
        gate.set()
        self.spin(timeout=0.2)
        self.assertEqual(win.day, dl.coerce_day("2026-09-24"))
        self.assertEqual(str(win.btn_refresh.cget("state")), "normal")

    def test_todays_snapshot_is_not_reused_after_midnight(self):
        from unittest import mock
        day = dl.coerce_day("2026-09-28")
        with mock.patch.object(dl, "today_utc8", return_value=day):
            win = self.make(day=day)
            self.pump(win)
        with mock.patch.object(dl, "today_utc8",
                               return_value=day + timedelta(days=1)):
            win.load(day)
            self.pump(win)
        self.assertEqual(self.fetches, [day, day])

    def test_failed_sources_are_not_reported_as_an_empty_day(self):
        self.data = {"mr": [], "bugfix": None, "scan": [],
                     "errors": {"bugfix": ("error", "boom")}}
        win = self.make()
        self.pump(win)
        status = win.lbl_status.cget("text")
        self.assertNotIn("no completed", status)
        self.assertIn("— Bug Fix", status)
        self.assertEqual(win.kpi_labels["projects"][1].cget("text"), "≥0")
        self.data = {"mr": None, "bugfix": None, "scan": None,
                     "errors": {s: ("error", "x") for s in ("mr", "bugfix", "scan")}}
        win.load(win.day, force=True)
        self.pump(win)
        self.assertEqual(win.kpi_labels["projects"][1].cget("text"), "—")

    def test_branch_kpi_is_a_lower_bound_when_some_are_unknown(self):
        win = self.make()
        self.pump(win)
        # web/web → develop; Fiji/video unresolved.
        self.assertEqual(win.kpi_labels["branches"][1].cget("text"), "≥1")
        self.data = _day_data()
        self.data["mr"] = [t for t in self.data["mr"]
                           if t["project_id"] == "Fiji/video"]
        win.load(win.day, force=True)
        self.pump(win)
        self.assertEqual(win.kpi_labels["branches"][1].cget("text"), "?")

    def test_clipped_cell_shows_full_text_on_hover(self):
        win = self.make()
        self.pump(win)
        long_branches = ", ".join(f"release/26-{i}-very-long-branch-name"
                                  for i in range(12))
        values = list(win.tree.item("cat:WEB", "values"))
        values[1] = long_branches
        win.tree.item("cat:WEB", values=values)
        win.win.update()
        x, y, w, h = win.tree.bbox("cat:WEB", "branches")
        event = type("E", (), {"x": x + 5, "y": y + h // 2,
                               "x_root": 0, "y_root": 0})()
        win._on_tree_motion(event)
        self.assertIsNotNone(win._tip)
        label = win._tip.winfo_children()[0]
        self.assertEqual(label.cget("text"), long_branches)
        x, y, w, h = win.tree.bbox("cat:WEB", "mrs")
        event.x, event.y = x + 2, y + h // 2
        win._on_tree_motion(event)
        self.assertIsNone(win._tip)      # "1" fits: no tooltip

    def test_all_columns_fit_at_minimum_width(self):
        win = self.make()
        self.pump(win)
        for width in (900, 1180, 1600):
            win.win.geometry(f"{width}x600")
            win.win.update()
            cols = ("#0",) + gdl._COLUMNS
            total = sum(int(win.tree.column(c, "width")) for c in cols)
            self.assertLessEqual(total, win.tree.winfo_width(), width)
            self.assertGreaterEqual(int(win.tree.column("branches", "width")), 160)

    def test_calendar_today_follows_utc8(self):
        import date_picker
        win = self.make()
        self.pump(win)
        win._pick_date()
        popup = date_picker._CalendarPopup._open_instance
        self.addCleanup(lambda: popup._close())
        self.assertEqual(popup._today(), dl.today_utc8())
        self.assertEqual(popup._max_date, dl.today_utc8())


class HeaderFitTests(unittest.TestCase):
    """ExportApp._fit_data_lookback_button on a stand-in header."""

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception as exc:
            raise unittest.SkipTest(f"Tk unavailable: {exc}")
        import export_gui
        cls.eg = export_gui

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def make_header(self, account_text, width):
        import tkinter as tk
        top = tk.Toplevel(self.root)
        self.addCleanup(top.destroy)
        top.geometry(f"{width}x80")
        header = tk.Frame(top)
        header.pack(fill="x")
        fake = type("App", (), {})()
        fake.DATA_LOOKBACK_ICON = self.eg.ExportApp.DATA_LOOKBACK_ICON
        fake._t = lambda key: gdl.STRINGS["en"][key]
        tk.Button(header, text=account_text).pack(side="right", padx=(0, 8))
        fake.btn_data_lookback = tk.Button(header, text=fake._t("dl_entry"))
        fake.btn_data_lookback.pack(side="right", padx=(0, 12))
        fake.lbl_title = tk.Label(header, text="Tranzor Translation Exporter",
                                  font=("Segoe UI", 18, "bold"))
        fake.lbl_title.pack(anchor="w")
        fake.lbl_subtitle = tk.Label(header, text="Export translation changes")
        fake.lbl_subtitle.pack(anchor="w")
        top.update()
        return fake

    def fit(self, fake):
        self.eg.ExportApp._fit_data_lookback_button(fake)
        return fake.btn_data_lookback.cget("text")

    def test_full_label_when_room(self):
        fake = self.make_header("🔑 anna.su", 1200)
        self.assertEqual(self.fit(fake), "📅 Data Lookback")

    def test_icon_only_when_title_would_clip(self):
        fake = self.make_header("🔑 " + "christopher.williams" * 3, 760)
        self.assertEqual(self.fit(fake), "📅")

    def test_pack_padx_total(self):
        import tkinter as tk
        f = tk.Frame(self.root)
        a = tk.Label(f)
        a.pack(padx=(3, 9))
        b = tk.Label(f)
        b.pack(padx=5)
        self.assertEqual(self.eg._pack_padx_total(a), 12)
        self.assertEqual(self.eg._pack_padx_total(b), 10)
        f.destroy()


class StringsTests(unittest.TestCase):
    def test_en_zh_keys_match(self):
        self.assertEqual(set(gdl.STRINGS["en"]), set(gdl.STRINGS["zh"]))

    def test_weekday_lists_have_seven_entries(self):
        for lang in ("en", "zh"):
            self.assertEqual(len(gdl.STRINGS[lang]["dl_weekdays"].split(",")), 7)


if __name__ == "__main__":
    unittest.main()
