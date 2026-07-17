"""GUI policy tests for best-effort Full Translation exports."""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
import zipfile

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

    def set_phase(self, phase):
        self.phases.append(phase)

    def show_success(self, summary, mode):
        self.success = (summary, mode)


class _PackForget:
    def pack_forget(self):
        pass


class PartialExportGuiTest(unittest.TestCase):
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
        tab._dialog_log = lambda *_args, **_kwargs: None
        return tab

    def test_run_export_writes_successful_rows_and_marks_partial(self):
        calls = {}
        inv = self.orig_exp.FullTranslationInventory()
        inv.ingest(
            "RingCentral.Product.hash.key", "en-US", "source text")
        inv.record_failure(
            "Legacy", 157, "503 Service Temporarily Unavailable")

        def collect(**kwargs):
            calls["collect"] = kwargs
            return inv

        def build_zip(received_inv, **kwargs):
            calls["build_inv"] = received_inv
            return self.orig_exp.build_ap_zip(received_inv, **kwargs)

        gui._exp = types.SimpleNamespace(
            collect_full_translations=collect,
            build_ap_zip=build_zip,
            build_merged_json=lambda *_a, **_k: self.fail("unexpected JSON"),
            AuthRequiredError=type("AuthRequiredError", (Exception,), {}),
        )
        tab = self._tab()
        done = {}
        tab._on_export_done = lambda summary, err: done.update(
            summary=summary, err=err)

        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = os.path.join(temp_dir, "partial.zip")
            tab._run_export(
                out_path, "zip", ["legacy"],
                {"Legacy Product"}, None, None, ["en-US"],
            )
            self.assertTrue(os.path.isfile(out_path))
            with zipfile.ZipFile(out_path) as archive:
                self.assertEqual(len(archive.namelist()), 1)
                payload = archive.read(archive.namelist()[0])
                self.assertIn(b"source text", payload)

        self.assertFalse(calls["collect"]["strict_complete"])
        self.assertIs(calls["build_inv"], inv)
        self.assertIsNone(done["err"])
        self.assertTrue(done["summary"]["_is_partial"])
        self.assertEqual(done["summary"]["_fetch_failure_count"], 1)
        self.assertEqual(
            done["summary"]["_fetch_failures"][0]["task_id"], 157)

    def test_export_done_uses_warning_status_for_partial_file(self):
        tab = self._tab()
        tab._set_busy = lambda _busy: None
        summary = {
            "out_path": "partial.zip",
            "_mode": "zip",
            "_is_partial": True,
            "_fetch_failure_count": 2,
            "_fetch_failures": [
                {"source": "Legacy", "task_id": 157, "error": "503"},
                {"source": "Legacy", "task_id": 162, "error": "503"},
            ],
        }

        tab._on_export_done(summary, None)

        self.assertIn("Partial export saved", tab.lbl_status.config["text"])
        self.assertEqual(tab.lbl_status.config["foreground"], "#fbbf24")
        self.assertEqual(tab._progress_dlg.success, (summary, "zip"))

    def test_result_dialog_labels_partial_export_as_warning(self):
        dlg = object.__new__(gui._ExportProgressDialog)
        dlg._closed = False
        dlg._t = lambda key: gui.STRINGS["en"][key]
        dlg.lbl_header = _Recorder()
        dlg.lbl_phase = _Recorder()
        dlg.frame_progress = _PackForget()
        dlg._stop_progressbar = lambda: None
        dlg._build_result_view = lambda *_args, **_kwargs: None
        dlg._build_result_buttons = lambda *_args, **_kwargs: None
        summary = {
            "out_path": "partial.zip",
            "_fetch_failure_count": 2,
        }

        dlg.show_success(summary, mode="zip")

        self.assertEqual(
            dlg.lbl_header.config["text"], "Partial export complete")
        self.assertEqual(dlg.lbl_header.config["foreground"], "#fbbf24")
        self.assertIn("partial result", dlg.lbl_phase.config["text"].lower())


if __name__ == "__main__":
    unittest.main()
