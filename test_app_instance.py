"""Tests for visible multi-instance window behaviour."""
from __future__ import annotations

import unittest

import app_instance


class ExistingInstanceTests(unittest.TestCase):

    def test_counts_only_exporter_main_windows(self):
        titles = [
            "Tranzor Translation Exporter",
            "Tranzor Translation Exporter · Instance 2",
            "Tranzor 翻译导出器 · 实例 3",
            "Export in progress",
            "Platform Sign-in",
            "Unrelated app",
        ]
        self.assertEqual(
            app_instance.count_existing_instances(titles=titles), 3)

    def test_non_windows_without_injected_titles_is_zero(self):
        self.assertEqual(
            app_instance.count_existing_instances(platform_name="Darwin"), 0)


class CascadePositionTests(unittest.TestCase):

    def test_first_instance_is_centered(self):
        self.assertEqual(
            app_instance.cascade_position(1920, 1080, 1280, 900, 0),
            (320, 90),
        )

    def test_later_instance_is_visibly_offset(self):
        self.assertEqual(
            app_instance.cascade_position(1920, 1080, 1280, 900, 1),
            (352, 122),
        )

    def test_small_screen_is_bounded(self):
        self.assertEqual(
            app_instance.cascade_position(800, 600, 1280, 900, 4),
            (0, 0),
        )


class _FakeRoot:

    def __init__(self):
        self.calls = []

    def after(self, delay, callback):
        self.calls.append(("after", delay))
        callback()

    def lift(self):
        self.calls.append(("lift",))

    def attributes(self, name, value):
        self.calls.append(("attributes", name, value))

    def focus_force(self):
        self.calls.append(("focus_force",))

    def winfo_exists(self):
        return True


class SurfaceTests(unittest.TestCase):

    def test_second_windows_instance_gets_short_activation_pulse(self):
        root = _FakeRoot()
        self.assertTrue(app_instance.surface_new_instance(
            root, 1, platform_name="Windows"))
        self.assertIn(("lift",), root.calls)
        self.assertIn(("focus_force",), root.calls)
        self.assertIn(("attributes", "-topmost", True), root.calls)
        self.assertIn(("attributes", "-topmost", False), root.calls)

    def test_first_instance_is_not_forced_to_foreground(self):
        root = _FakeRoot()
        self.assertFalse(app_instance.surface_new_instance(
            root, 0, platform_name="Windows"))
        self.assertEqual(root.calls, [])


if __name__ == "__main__":
    unittest.main()
