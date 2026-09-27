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
        self.assertEqual(kpis, {"mrs": "2", "runs": "3", "branches": "1",
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
        self.assertIn(gdl.STRINGS["en"]["dl_today"], win.lbl_day.cget("text"))
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


class StringsTests(unittest.TestCase):
    def test_en_zh_keys_match(self):
        self.assertEqual(set(gdl.STRINGS["en"]), set(gdl.STRINGS["zh"]))

    def test_weekday_lists_have_seven_entries(self):
        for lang in ("en", "zh"):
            self.assertEqual(len(gdl.STRINGS[lang]["dl_weekdays"].split(",")), 7)


if __name__ == "__main__":
    unittest.main()
