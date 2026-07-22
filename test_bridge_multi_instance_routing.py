"""Regression tests for report-to-userscript multi-instance routing."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import tranzor_bridge as tb


ROOT = Path(__file__).resolve().parent
REPORT_SOURCES = (
    "export_changes.py",
    "export_translations.py",
    "export_mr_pipeline.py",
)
USERSCRIPT = ROOT / "userscript" / "tranzor_bridge.user.js"


class ReportEndpointHashTests(unittest.TestCase):

    def test_every_report_passes_token_port_and_instance(self):
        for filename in REPORT_SOURCES:
            with self.subTest(filename=filename):
                source = (ROOT / filename).read_text(encoding="utf-8")
                self.assertIn("#tzbridge_token=", source)
                self.assertIn("&tzbridge_port=", source)
                self.assertIn("&tzbridge_instance=", source)

    def test_userscript_switches_endpoint_and_resets_sequence(self):
        source = USERSCRIPT.read_text(encoding="utf-8")
        self.assertIn("new URLSearchParams", source)
        self.assertIn("params.get('tzbridge_port')", source)
        self.assertIn("params.get('tzbridge_instance')", source)
        self.assertIn("GM_setValue('bridge_endpoint', { port })", source)
        self.assertIn("if (switchedInstance) lastSeq = 0", source)


class UserscriptVersionTests(unittest.TestCase):

    def test_metadata_runtime_and_server_minimum_versions_match(self):
        source = USERSCRIPT.read_text(encoding="utf-8")
        metadata = re.search(
            r"^// @version\s+(\S+)", source, flags=re.MULTILINE)
        runtime = re.search(
            r"const USERSCRIPT_VERSION = '([^']+)'", source)
        self.assertIsNotNone(metadata)
        self.assertIsNotNone(runtime)
        self.assertEqual(metadata.group(1), runtime.group(1))
        self.assertEqual(metadata.group(1), tb.MIN_USERSCRIPT_VERSION)


if __name__ == "__main__":
    unittest.main()
