"""GUI-side tests for Delta Day2Day Merge-to-JSON.

Covers the date helpers (UTC+8 default window, validation) and that
``_run_export`` forwards the selected products / languages / date range
to ``collect_full_translations``. The Tk dialog itself is not instantiated.

Run:  python -m unittest test_gui_full_translations_delta
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_tab_full_translations as gui


class _ImmediateParent:
    def after(self, _delay, callback, *args):
        callback(*args)


class _Recorder:
    def __init__(self):
        self.config = {}

    def configure(self, **kwargs):
        self.config.update(kwargs)


class _ProgressDialog:
    def __init__(self):
        self.phases = []
        self.success = None
        self.error = None

    def set_phase(self, phase):
        self.phases.append(phase)

    def show_success(self, summary, mode):
        self.success = (summary, mode)

    def show_error(self, err):
        self.error = err


class DeltaDateHelperTests(unittest.TestCase):
    def test_default_range_is_seven_utc8_days(self):
        # Matches the mock-up: To = 2026-09-25, From = 2026-09-18.
        now = datetime(2026, 9, 25, 15, 0, tzinfo=gui.TZ_UTC8)
        self.assertEqual(
            gui.default_delta_date_range(now),
            ("2026-09-18", "2026-09-25"))

    def test_default_range_uses_utc8_date_not_utc(self):
        # 2026-09-25 00:30 UTC+8 is still 2026-09-24 in UTC.
        now = datetime(2026, 9, 24, 16, 30, tzinfo=gui.TZ_UTC8)
        self.assertEqual(
            gui.default_delta_date_range(now),
            ("2026-09-17", "2026-09-24"))

    def test_validate_accepts_inclusive_range(self):
        pair, err = gui.validate_delta_dates("2026-09-18", "2026-09-25")
        self.assertIsNone(err)
        self.assertEqual(pair, (date(2026, 9, 18), date(2026, 9, 25)))

    def test_validate_rejects_empty(self):
        pair, err = gui.validate_delta_dates("", "2026-09-25")
        self.assertIsNone(pair)
        self.assertEqual(err, "ft_err_delta_dates")

    def test_validate_rejects_inverted_range(self):
        pair, err = gui.validate_delta_dates("2026-09-25", "2026-09-18")
        self.assertIsNone(pair)
        self.assertEqual(err, "ft_err_delta_order")

    def test_validate_same_day_is_ok(self):
        pair, err = gui.validate_delta_dates("2026-09-18", "2026-09-18")
        self.assertIsNone(err)
        self.assertEqual(pair[0], pair[1])


class DeltaExportForwardTests(unittest.TestCase):
    def setUp(self):
        self.orig_exp = gui._exp

    def tearDown(self):
        gui._exp = self.orig_exp

    def _tab(self):
        tab = object.__new__(gui.FullTranslationsTab)
        tab.parent = _ImmediateParent()
        tab.lbl_status = _Recorder()
        tab._progress_dlg = _ProgressDialog()
        tab._t = lambda key: gui.STRINGS["en"][key]
        tab._dialog_log = lambda *_a, **_k: None
        tab._api_kw = lambda: {}
        return tab

    def test_run_export_forwards_date_window_and_selection(self):
        calls = {}
        inv = self.orig_exp.FullTranslationInventory()
        inv.ingest("RingCentral.P.hash.k", "de-DE", "Hallo")

        def collect(**kwargs):
            calls["collect"] = kwargs
            return inv

        def build_json(received_inv, **kwargs):
            calls["build"] = kwargs
            return {"out_path": kwargs["out_path"], "records": 1,
                    "locales": 1, "products": 1, "entries": 1,
                    "per_product": {}, "per_locale": {}}

        gui._exp = types.SimpleNamespace(
            collect_full_translations=collect,
            build_merged_json=build_json,
            build_ap_zip=lambda *_a, **_k: self.fail("zip"),
            AuthRequiredError=type("AuthRequiredError", (Exception,), {}),
        )
        tab = self._tab()
        done = {}
        tab._on_export_done = lambda summary, err: done.update(
            summary=summary, err=err)

        tab._run_export(
            "out.json", "json", ["mr"],
            None, {"web/web"}, None, ["de-DE", "zh-CN"],
            date(2026, 9, 18), date(2026, 9, 25),
        )

        self.assertEqual(calls["collect"]["created_after"], date(2026, 9, 18))
        self.assertEqual(calls["collect"]["created_before"], date(2026, 9, 25))
        self.assertEqual(calls["collect"]["mr_project_filter"], {"web/web"})
        self.assertEqual(calls["collect"]["sources"], ["mr"])
        self.assertTrue(calls["collect"]["track_all_sources"])
        self.assertEqual(calls["build"]["locales"], ["de-DE", "zh-CN"])
        self.assertIsNone(done["err"])
        self.assertEqual(done["summary"]["_mode"], "json")

    def test_empty_delta_uses_delta_empty_message(self):
        inv = self.orig_exp.FullTranslationInventory()

        gui._exp = types.SimpleNamespace(
            collect_full_translations=lambda **_k: inv,
            build_merged_json=lambda *_a, **_k: self.fail("json"),
            build_ap_zip=lambda *_a, **_k: self.fail("zip"),
            AuthRequiredError=type("AuthRequiredError", (Exception,), {}),
        )
        tab = self._tab()
        done = {}
        tab._on_export_done = lambda summary, err: done.update(
            summary=summary, err=err)

        tab._run_export(
            "out.json", "json", ["mr"],
            None, {"web/web"}, None, ["de-DE"],
            date(2026, 9, 18), date(2026, 9, 25),
        )
        self.assertIn("date range", (done["err"] or "").lower())

    def test_empty_full_export_keeps_original_message(self):
        inv = self.orig_exp.FullTranslationInventory()
        gui._exp = types.SimpleNamespace(
            collect_full_translations=lambda **_k: inv,
            build_merged_json=lambda *_a, **_k: self.fail("json"),
            build_ap_zip=lambda *_a, **_k: self.fail("zip"),
            AuthRequiredError=type("AuthRequiredError", (Exception,), {}),
        )
        tab = self._tab()
        done = {}
        tab._on_export_done = lambda summary, err: done.update(
            summary=summary, err=err)
        tab._run_export(
            "out.zip", "zip", ["legacy"],
            {"P"}, None, None, ["en-US"],
        )
        self.assertIn("matched the selection", (done["err"] or "").lower())


class DeltaI18nTests(unittest.TestCase):
    KEYS = (
        "ft_merge_delta", "ft_delta_title", "ft_delta_subtitle",
        "ft_delta_from", "ft_delta_to", "ft_delta_tz_hint",
        "ft_delta_selection_hint", "ft_delta_cancel", "ft_delta_run",
        "ft_err_delta_dates", "ft_err_delta_order", "ft_err_no_delta_data",
    )

    def test_en_and_zh_have_delta_keys(self):
        for lang in ("en", "zh"):
            for key in self.KEYS:
                self.assertIn(key, gui.STRINGS[lang], f"{lang}.{key}")
                self.assertTrue(gui.STRINGS[lang][key].strip(), f"{lang}.{key}")


if __name__ == "__main__":
    unittest.main()
