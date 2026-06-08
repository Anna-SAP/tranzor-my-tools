"""Tests for the MR Pipeline "✏️ Post-edited only" view filter.

Two parts:
  * i18n — the new label/status keys exist in both languages and format.
  * mechanism — the Treeview detach/reattach the filter relies on does what
    _apply_post_edit_filter assumes (detach hides + drops from get_children;
    move reattaches; insertion order is rebuilt). Skipped where Tk can't init
    (headless CI).

Run:  python -m unittest test_mr_post_edit_filter
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestPostEditFilterStrings(unittest.TestCase):
    def setUp(self):
        import export_gui
        self.STRINGS = export_gui.STRINGS

    def test_keys_present_in_both_languages(self):
        for lang in ("en", "zh"):
            self.assertIn("mr_post_edit_only", self.STRINGS[lang], lang)
            self.assertIn("mr_post_edit_filter_status", self.STRINGS[lang], lang)

    def test_filter_status_formats_with_n(self):
        for lang in ("en", "zh"):
            out = self.STRINGS[lang]["mr_post_edit_filter_status"].format(n=3)
            self.assertIn("3", out)

    def test_only_label_carries_pencil_glyph(self):
        for lang in ("en", "zh"):
            self.assertIn("✏️", self.STRINGS[lang]["mr_post_edit_only"])


class TestTreeviewDetachReattachMechanism(unittest.TestCase):
    """Validate the exact tk operations _apply_post_edit_filter uses, so a
    future tkinter behaviour change can't silently break the filter."""

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
            from tkinter import ttk
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"tkinter unavailable: {e}")
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()  # never show a window
        except Exception as e:  # pragma: no cover - headless / no display
            raise unittest.SkipTest(f"no display for Tk: {e}")
        cls.ttk = ttk

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def _tree(self):
        tree = self.ttk.Treeview(self.root, columns=("project",), show="headings")
        # t1/t3 are post-edits; t2 is not (mirrors the gold "post_edit" tag).
        tree.insert("", "end", iid="t1", values=("A",), tags=("t1", "post_edit"))
        tree.insert("", "end", iid="t2", values=("B",), tags=("t2",))
        tree.insert("", "end", iid="t3", values=("C",), tags=("t3", "post_edit"))
        return tree

    def test_detach_hides_only_non_post_edit(self):
        tree = self._tree()
        for iid in ("t1", "t2", "t3"):
            if "post_edit" not in tree.item(iid, "tags"):
                tree.detach(iid)
        self.assertEqual(set(tree.get_children("")), {"t1", "t3"})

    def test_reattach_restores_all_in_insertion_order(self):
        tree = self._tree()
        tree.detach("t2")
        self.assertEqual(set(tree.get_children("")), {"t1", "t3"})
        # Filter OFF path: move every row to end in insertion order.
        for iid in ("t1", "t2", "t3"):
            tree.move(iid, "", "end")
        self.assertEqual(list(tree.get_children("")), ["t1", "t2", "t3"])

    def test_detached_item_still_addressable_then_revealed(self):
        """A detached row keeps its data and can be reattached later — this is
        what lets an async post-edit confirmation reveal a hidden row."""
        tree = self._tree()
        tree.detach("t2")
        # item() still works on the detached row
        self.assertEqual(tree.item("t2", "values"), ("B",))
        # reveal it (as _apply_post_edit_prefix_mr does after tagging)
        tree.move("t2", "", "end")
        self.assertIn("t2", tree.get_children(""))


if __name__ == "__main__":
    unittest.main()
