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


class TaskbarIdentityTests(unittest.TestCase):

    def test_app_id_is_unique_per_pid(self):
        self.assertEqual(
            app_instance.taskbar_app_id(1234),
            "Tranzor.TranslationExporter.1234",
        )
        self.assertNotEqual(
            app_instance.taskbar_app_id(1234),
            app_instance.taskbar_app_id(5678),
        )

    def test_app_id_defaults_to_current_pid(self):
        import os

        self.assertTrue(
            app_instance.taskbar_app_id().endswith(f".{os.getpid()}"))

    def test_non_windows_is_a_noop(self):
        self.assertIsNone(
            app_instance.ungroup_taskbar_icon(platform_name="Darwin"))


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
