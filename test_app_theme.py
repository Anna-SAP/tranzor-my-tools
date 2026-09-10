"""Tests for the Light/Dark theme engine.

The colour maths and option remapping are display-free.  The last class
exercises the real tkinter hooks and is skipped when no display is available
(headless CI).
"""
from __future__ import annotations

import unittest

import app_theme as theme


def _lum(c):
    return theme.luminance(c)


class TestColourMaths(unittest.TestCase):
    def test_parse_and_normalize(self):
        self.assertEqual(theme.normalize_hex("#fff"), "#ffffff")
        self.assertEqual(theme.normalize_hex("#1A1A2E"), "#1a1a2e")
        self.assertIsNone(theme.normalize_hex("white"))
        self.assertIsNone(theme.normalize_hex("#12"))
        self.assertIsNone(theme.normalize_hex(None))

    def test_accent_detection(self):
        for c in ("#e94560", "#2ecc71", "#fbbf24", "#0891b2", "#7c5cff", "#16a34a"):
            self.assertTrue(theme.is_accent(c), c)
        for c in ("#1a1a2e", "#16213e", "#0f3460", "#fff", "#ccc", "#888",
                  "#7f1d1d", "#1e3a8a", "#374151"):
            self.assertFalse(theme.is_accent(c), c)


class TestToLight(unittest.TestCase):
    def test_core_palette_overrides(self):
        self.assertEqual(theme.to_light("#1a1a2e", "bg"), "#eef0f5")
        self.assertEqual(theme.to_light("#16213e", "bg"), "#ffffff")
        self.assertEqual(theme.to_light("#fff", "fg"), "#1f2937")
        self.assertEqual(theme.to_light("#ffffff", "fg"), "#1f2937")
        self.assertEqual(theme.to_light("#ccc", "fg"), "#334155")

    def test_dark_backgrounds_become_light(self):
        for c in ("#0a0a1a", "#1f2a48", "#3a2e1f", "#3a1f24", "#7f1d1d",
                  "#1e3a8a", "#854d0e", "#1f3d7a", "#222"):
            out = theme.to_light(c, "bg")
            self.assertGreaterEqual(_lum(out), 0.75, (c, out))

    def test_light_foregrounds_become_dark(self):
        for c in ("#fff", "#e0e0e0", "#9aa0b0", "#fbbf24", "#fde68a",
                  "#fca5a5", "#86efac", "#7dd3fc", "#dcd0ff", "#c7d2fe"):
            out = theme.to_light(c, "fg")
            self.assertLessEqual(_lum(out), 0.45, (c, out))

    def test_accent_backgrounds_are_kept(self):
        for c in ("#e94560", "#2ecc71", "#27ae60", "#0891b2", "#16a34a", "#7c5cff"):
            self.assertEqual(theme.to_light(c, "bg"), theme.normalize_hex(c))

    def test_pale_backgrounds_are_kept(self):
        # Badge backgrounds that are already light stay light.
        self.assertEqual(theme.to_light("#fff1b8", "bg"), "#fff1b8")
        self.assertEqual(theme.to_light("#ececec", "bg"), "#ececec")

    def test_dark_foregrounds_are_kept(self):
        # Dark text on a pale/accent badge stays dark.
        self.assertEqual(theme.to_light("#0a0a1a", "fg"), "#0a0a1a")
        self.assertEqual(theme.to_light("#78350f", "fg"), "#78350f")

    def test_foreground_on_accent_background_is_untouched(self):
        self.assertEqual(theme.to_light("#fff", "fg", paired_bg="#e94560"), "#fff")
        self.assertEqual(theme.to_light("#ffffff", "fg", paired_bg="#2ecc71"), "#ffffff")
        # …but on a dark navy background it is darkened.
        self.assertEqual(theme.to_light("#fff", "fg", paired_bg="#0f3460"), "#1f2937")

    def test_idempotent_for_bg_and_fg(self):
        for c in ("#1a1a2e", "#0a0a1a", "#3a2e1f", "#0f3460", "#16213e"):
            once = theme.to_light(c, "bg")
            self.assertEqual(theme.to_light(once, "bg"), once, c)
        for c in ("#fff", "#ccc", "#9aa0b0", "#fbbf24", "#e4e7ef", "#888"):
            once = theme.to_light(c, "fg")
            self.assertEqual(theme.to_light(once, "fg"), once, c)

    def test_distinct_whites_stay_distinct(self):
        # Compression instead of clamping keeps the reverse map unambiguous.
        a = theme.to_light("#f0f0f0", "fg")
        b = theme.to_light("#d8d8d8", "fg")
        self.assertNotEqual(a, b)

    def test_non_colour_passthrough(self):
        self.assertEqual(theme.to_light("white", "bg"), "white")
        self.assertEqual(theme.to_light("", "fg"), "")

    def test_reverse_restores_original_literal(self):
        light = theme.to_light("#1f2a48", "bg")
        self.assertEqual(theme.to_dark(light, "bg"), "#1f2a48")
        light_fg = theme.to_light("#fff", "fg")
        self.assertIn(theme.to_dark(light_fg, "fg"), ("#fff", "#ffffff"))
        # Unknown light values are left alone.
        self.assertEqual(theme.to_dark("#123456", "bg"), "#123456")


class TestRemapOptions(unittest.TestCase):
    def test_dark_mode_is_passthrough(self):
        opts = {"bg": "#1a1a2e", "fg": "#fff"}
        self.assertIs(theme.remap_options(opts, theme.DARK), opts)

    def test_bg_and_fg_roles(self):
        out = theme.remap_options({"bg": "#1a1a2e", "fg": "#fff", "text": "x",
                                   "font": ("Segoe UI", 10)}, theme.LIGHT)
        self.assertEqual(out["bg"], "#eef0f5")
        self.assertEqual(out["fg"], "#1f2937")
        self.assertEqual(out["text"], "x")
        self.assertEqual(out["font"], ("Segoe UI", 10))

    def test_accent_button_keeps_white_label(self):
        out = theme.remap_options({"bg": "#e94560", "fg": "#fff",
                                   "activebackground": "#ff6b81",
                                   "activeforeground": "#fff"}, theme.LIGHT)
        self.assertEqual(out["bg"], "#e94560")
        self.assertEqual(out["fg"], "#fff")
        self.assertEqual(out["activeforeground"], "#fff")

    def test_secondary_button_darkens_label(self):
        out = theme.remap_options({"bg": "#0f3460", "fg": "#ccc",
                                   "activebackground": "#1a3a6a",
                                   "activeforeground": "#fff"}, theme.LIGHT)
        self.assertEqual(out["bg"], "#dde5f2")
        self.assertEqual(out["fg"], "#334155")
        self.assertEqual(out["activebackground"], "#cbd7ea")
        self.assertEqual(out["activeforeground"], "#1f2937")

    def test_insertbackground_follows_widget_bg(self):
        out = theme.remap_options({"bg": "#0a0a1a", "fg": "#fff",
                                   "insertbackground": "#fff"}, theme.LIGHT)
        self.assertEqual(out["insertbackground"], "#1f2937")

    def test_ttk_style_dict_with_dash_keys_and_none(self):
        out = theme.remap_options({"-background": "#16213e", "-foreground": None},
                                  theme.LIGHT)
        self.assertEqual(out["-background"], "#ffffff")
        self.assertIsNone(out["-foreground"])

    def test_current_bg_callback_pairs_lone_fg(self):
        out = theme.remap_options({"fg": "#fff"}, theme.LIGHT,
                                  current_bg=lambda opt: "#2ecc71")
        self.assertEqual(out["fg"], "#fff")
        out = theme.remap_options({"fg": "#fff"}, theme.LIGHT,
                                  current_bg=lambda opt: "#1a1a2e")
        self.assertEqual(out["fg"], "#1f2937")

    def test_unchanged_dict_returned_as_is(self):
        opts = {"text": "hello", "bg": "#e94560"}
        self.assertIs(theme.remap_options(opts, theme.LIGHT), opts)


class TestRemapMapdict(unittest.TestCase):
    def test_statespec_values_are_translated(self):
        out = theme.remap_mapdict({
            "background": [("selected", "#1a3a6a"), ("active", "#e94560")],
            "foreground": [("selected", "#fff")],
        }, theme.LIGHT)
        self.assertEqual(out["background"], [("selected", "#cbd7ea"), ("active", "#e94560")])
        self.assertEqual(out["foreground"], [("selected", "#1f2937")])

    def test_dark_passthrough(self):
        m = {"background": [("selected", "#1a3a6a")]}
        self.assertIs(theme.remap_mapdict(m, theme.DARK), m)


class TestModeState(unittest.TestCase):
    def tearDown(self):
        theme.set_mode(theme.DARK)

    def test_set_and_toggle(self):
        self.assertEqual(theme.set_mode("LIGHT"), theme.LIGHT)
        self.assertEqual(theme.current_mode(), theme.LIGHT)
        self.assertEqual(theme.toggle_mode(), theme.DARK)
        self.assertEqual(theme.set_mode("garbage"), theme.DARK)

    def test_saved_mode_defaults_to_dark(self):
        from unittest import mock
        with mock.patch("gitlab_client.load_config", return_value={}):
            self.assertEqual(theme.load_saved_mode(), theme.DARK)
        with mock.patch("gitlab_client.load_config", return_value={"ui_theme": "light"}):
            self.assertEqual(theme.load_saved_mode(), theme.LIGHT)
        with mock.patch("gitlab_client.load_config", return_value={"ui_theme": "neon"}):
            self.assertEqual(theme.load_saved_mode(), theme.DARK)

    def test_save_mode_writes_config_key(self):
        from unittest import mock
        with mock.patch("gitlab_client.update_config") as upd:
            theme.save_mode(theme.LIGHT)
        upd.assert_called_once_with(ui_theme="light")


def _tk_root():
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        return root
    except Exception:
        return None


class TestTkHooks(unittest.TestCase):
    """Real tkinter round-trip; skipped without a display."""

    @classmethod
    def setUpClass(cls):
        cls.root = _tk_root()
        if cls.root is None:
            raise unittest.SkipTest("no display for tkinter")
        theme.install()

    @classmethod
    def tearDownClass(cls):
        theme.set_mode(theme.DARK)
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        theme.set_mode(theme.DARK)

    def test_widgets_created_in_light_mode_are_light(self):
        import tkinter as tk
        theme.set_mode(theme.LIGHT)
        f = tk.Frame(self.root, bg="#1a1a2e")
        lbl = tk.Label(f, text="x", bg="#16213e", fg="#fff")
        btn = tk.Button(f, text="Run", bg="#e94560", fg="#fff")
        try:
            self.assertEqual(f.cget("bg"), "#eef0f5")
            self.assertEqual(lbl.cget("bg"), "#ffffff")
            self.assertEqual(lbl.cget("fg"), "#1f2937")
            self.assertEqual(btn.cget("bg"), "#e94560")
            self.assertEqual(btn.cget("fg"), "#fff")
            # configure() after creation is translated too
            lbl.configure(fg="#fbbf24")
            self.assertEqual(lbl.cget("fg"), "#b45309")
            lbl["bg"] = "#0a0a1a"
            self.assertEqual(lbl.cget("bg"), "#f3f5f9")
        finally:
            f.destroy()

    def test_ttk_style_and_treeview_tags(self):
        from tkinter import ttk
        theme.set_mode(theme.LIGHT)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("T1.Treeview", background="#0d1a30", foreground="#e0e0e0",
                        fieldbackground="#0d1a30")
        style.map("T1.Treeview", background=[("selected", "#1a3a6a")],
                  foreground=[("selected", "#fff")])
        self.assertEqual(str(style.lookup("T1.Treeview", "background")), "#fdfdff")
        self.assertEqual(str(style.lookup("T1.Treeview", "foreground")), "#1f2937")
        self.assertEqual(str(style.lookup("T1.Treeview", "background", ["selected"])),
                         "#cbd7ea")
        tree = ttk.Treeview(self.root, style="T1.Treeview")
        try:
            tree.tag_configure("bad", background="#3a1f24", foreground="#fca5a5")
            self.assertEqual(str(tree.tag_configure("bad", "foreground")), "#b91c1c")
            self.assertGreaterEqual(_lum(str(tree.tag_configure("bad", "background"))), 0.75)
        finally:
            tree.destroy()

    def test_live_switch_round_trip(self):
        import tkinter as tk
        from tkinter import ttk
        # Build in DARK, switch to LIGHT, back to DARK → original literals.
        f = tk.Frame(self.root, bg="#1a1a2e")
        lbl = tk.Label(f, text="x", bg="#16213e", fg="#fff")
        txt = tk.Text(f, bg="#0a0a1a", fg="#e0e0e0", insertbackground="#fff")
        txt.tag_configure("warn", foreground="#fbbf24")
        cv = tk.Canvas(f, bg="#16213e")
        rect = cv.create_rectangle(0, 0, 5, 5, fill="#2ecc71")
        label_item = cv.create_text(2, 2, text="t", fill="#fff")
        style = ttk.Style(self.root)
        style.configure("T2.TLabel", background="#16213e", foreground="#ccc")
        style.map("T2.TLabel", foreground=[("disabled", "#888")])
        tree = ttk.Treeview(f)
        tree.tag_configure("action", foreground="#fca5a5")
        tree.tag_configure("bad", background="#3a1f24", foreground="#fca5a5")
        hint = ttk.Label(f, text="hint", style="T2.TLabel")
        hint.configure(background="#16213e")
        try:
            theme.switch(self.root, theme.LIGHT, persist=False)
            self.assertEqual(str(tree.tag_configure("action", "foreground")), "#b91c1c")
            self.assertGreaterEqual(_lum(str(tree.tag_configure("bad", "background"))), 0.75)
            self.assertEqual(str(hint.cget("background")), "#ffffff")
            self.assertEqual(f.cget("bg"), "#eef0f5")
            self.assertEqual(lbl.cget("fg"), "#1f2937")
            self.assertEqual(txt.cget("bg"), "#f3f5f9")
            self.assertEqual(txt.cget("insertbackground"), "#1f2937")
            self.assertEqual(str(txt.tag_cget("warn", "foreground")), "#b45309")
            self.assertEqual(cv.itemcget(rect, "fill"), "#2ecc71")   # accent kept
            self.assertEqual(cv.itemcget(label_item, "fill"), "#1f2937")
            self.assertEqual(str(style.lookup("T2.TLabel", "background")), "#ffffff")
            self.assertEqual(str(style.lookup("T2.TLabel", "foreground", ["disabled"])),
                             "#6b7280")
            # Applying light twice is a no-op.
            theme.apply_to_tree(self.root, theme.LIGHT)
            self.assertEqual(lbl.cget("fg"), "#1f2937")

            theme.switch(self.root, theme.DARK, persist=False)
            self.assertEqual(str(tree.tag_configure("action", "foreground")), "#fca5a5")
            self.assertEqual(str(tree.tag_configure("bad", "background")), "#3a1f24")
            self.assertEqual(str(hint.cget("background")), "#16213e")
            self.assertEqual(f.cget("bg"), "#1a1a2e")
            self.assertEqual(lbl.cget("bg"), "#16213e")
            self.assertIn(lbl.cget("fg"), ("#fff", "#ffffff"))
            self.assertEqual(txt.cget("bg"), "#0a0a1a")
            self.assertEqual(str(txt.tag_cget("warn", "foreground")), "#fbbf24")
            self.assertIn(cv.itemcget(label_item, "fill"), ("#fff", "#ffffff"))
            self.assertEqual(str(style.lookup("T2.TLabel", "background")), "#16213e")
            self.assertIn(str(style.lookup("T2.TLabel", "foreground", ["disabled"])),
                          ("#888", "#888888"))
        finally:
            f.destroy()


if __name__ == "__main__":
    unittest.main()
