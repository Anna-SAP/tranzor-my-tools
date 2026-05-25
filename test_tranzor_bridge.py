"""
Unit tests for tranzor_bridge.py (state tracking) and
bridge_setup_wizard.py (heuristic + state persistence).

Pure logic only — no HTTP, no Tk widgets. The wizard's BridgeSetupWizard
class is exercised separately by manual smoke runs.

Run:  python -m unittest test_tranzor_bridge
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bridge_setup_wizard as bsw
import tranzor_bridge as tb


class VersionLtTests(unittest.TestCase):

    def test_unknown_never_outdated(self):
        self.assertFalse(tb._version_lt(None, "0.6.0"))
        self.assertFalse(tb._version_lt("", "0.6.0"))

    def test_strict_less(self):
        self.assertTrue(tb._version_lt("0.5.0", "0.6.0"))
        self.assertTrue(tb._version_lt("0.5.9", "0.6.0"))
        self.assertTrue(tb._version_lt("0.6", "0.6.1"))

    def test_equal_not_less(self):
        self.assertFalse(tb._version_lt("0.6.0", "0.6.0"))
        self.assertFalse(tb._version_lt("0.6", "0.6.0"))  # padded compare

    def test_greater_not_less(self):
        self.assertFalse(tb._version_lt("0.6.1", "0.6.0"))
        self.assertFalse(tb._version_lt("1.0.0", "0.6.0"))

    def test_malformed_treated_as_unknown(self):
        # If we can't parse, we err toward "not outdated" — see docstring.
        self.assertFalse(tb._version_lt("not-a-version", "0.6.0"))
        self.assertFalse(tb._version_lt("0.6.0-beta", "0.6.0"))


class StatusSnapshotTests(unittest.TestCase):

    def setUp(self):
        # We never .start() the server — that would bind a real port.
        # All methods under test work on the in-memory state.
        self.bridge = tb.BridgeServer()
        self.bridge.port = 48217  # synth a value status_snapshot can echo

    def test_initial_snapshot_is_quiescent(self):
        s = self.bridge.status_snapshot()
        self.assertTrue(s["bridge_running"])
        self.assertEqual(s["port"], 48217)
        self.assertEqual(s["bridge_version"], tb.BRIDGE_VERSION)
        self.assertEqual(s["min_userscript_version"], tb.MIN_USERSCRIPT_VERSION)
        self.assertIsNone(s["last_handoff_at"])
        self.assertIsNone(s["last_userscript_pull_at"])
        self.assertIsNone(s["last_userscript_version"])
        self.assertFalse(s["userscript_live"])
        self.assertFalse(s["userscript_outdated"])
        self.assertIsNone(s["pending_handoff_age_sec"])

    def test_push_then_no_pull_creates_pending_handoff(self):
        before = time.time()
        seq = self.bridge.push({"items": []})
        s = self.bridge.status_snapshot()
        self.assertEqual(seq, 1)
        self.assertIsNotNone(s["last_handoff_at"])
        self.assertGreaterEqual(s["last_handoff_at"], before)
        # An envelope is queued and never delivered → pending age is set.
        self.assertIsNotNone(s["pending_handoff_age_sec"])
        self.assertGreaterEqual(s["pending_handoff_age_sec"], 0.0)

    def test_pull_clears_pending(self):
        self.bridge.push({"items": [{"k": "v"}]})
        # First pull "delivers" the envelope; status should no longer
        # report pending_handoff_age_sec.
        seq, env = self.bridge.pull(since=0)
        self.assertEqual(seq, 1)
        self.assertIsNotNone(env)
        s = self.bridge.status_snapshot()
        self.assertIsNone(s["pending_handoff_age_sec"])

    def test_note_pull_records_heartbeat_and_version(self):
        self.bridge.note_userscript_pull("0.6.0")
        s = self.bridge.status_snapshot()
        self.assertIsNotNone(s["last_userscript_pull_at"])
        self.assertEqual(s["last_userscript_version"], "0.6.0")
        self.assertTrue(s["userscript_live"])
        self.assertFalse(s["userscript_outdated"])

    def test_outdated_version_flagged(self):
        self.bridge.note_userscript_pull("0.5.0")
        s = self.bridge.status_snapshot()
        self.assertTrue(s["userscript_outdated"])

    def test_unknown_version_not_outdated(self):
        # Pre-versioning userscripts send no header → no flagging.
        self.bridge.note_userscript_pull(None)
        s = self.bridge.status_snapshot()
        self.assertFalse(s["userscript_outdated"])
        # But the heartbeat itself still counts as "live".
        self.assertTrue(s["userscript_live"])

    def test_stale_heartbeat_no_longer_live(self):
        self.bridge.note_userscript_pull("0.6.0")
        # Pretend the pull was long ago by rolling back the timestamp past
        # the live window. We touch the public attribute on purpose — the
        # snapshot reads it directly.
        self.bridge.last_userscript_pull_at = (
            time.time() - tb.USERSCRIPT_LIVE_WINDOW_SEC - 5.0
        )
        s = self.bridge.status_snapshot()
        self.assertFalse(s["userscript_live"])

    def test_version_string_is_capped(self):
        # Defensive: arbitrary attacker-supplied header values must not
        # blow out memory or break the dict serialization.
        self.bridge.note_userscript_pull("x" * 1000)
        self.assertLessEqual(
            len(self.bridge.last_userscript_version or ""), 32
        )


class AutoOpenHeuristicTests(unittest.TestCase):

    def setUp(self):
        self.bridge = tb.BridgeServer()
        self.bridge.port = 48217

    def test_none_bridge_never_triggers(self):
        self.assertFalse(bsw.should_auto_open_wizard(None))

    def test_idle_bridge_does_not_trigger(self):
        self.assertFalse(bsw.should_auto_open_wizard(self.bridge))

    def test_pending_envelope_without_userscript_triggers(self):
        self.bridge.push({"items": []})
        # Backdate the handoff so the pending age exceeds the threshold.
        self.bridge.last_handoff_at = (
            time.time() - bsw.AUTO_TRIGGER_PENDING_SEC - 1.0
        )
        self.assertTrue(bsw.should_auto_open_wizard(self.bridge))

    def test_fresh_pending_does_not_trigger_yet(self):
        self.bridge.push({"items": []})
        # The fresh handoff is well within the grace window — userscript
        # might just be slow to poll, don't pop the wizard yet.
        self.assertFalse(bsw.should_auto_open_wizard(self.bridge))

    def test_live_userscript_suppresses_trigger(self):
        self.bridge.push({"items": []})
        self.bridge.last_handoff_at = (
            time.time() - bsw.AUTO_TRIGGER_PENDING_SEC - 1.0
        )
        # A recent heartbeat means the userscript is working; even an old
        # pending envelope is the userscript's problem, not setup's.
        self.bridge.note_userscript_pull("0.6.0")
        self.assertFalse(bsw.should_auto_open_wizard(self.bridge))

    def test_outdated_userscript_triggers_even_when_live(self):
        self.bridge.note_userscript_pull("0.5.0")
        self.assertTrue(bsw.should_auto_open_wizard(self.bridge))


class SetupStateTests(unittest.TestCase):
    """Persistence round-trip for setup-complete state."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._patcher = mock.patch.object(
            bsw, "_state_dir", return_value=self.tmp,
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_returns_empty(self):
        self.assertEqual(bsw.load_setup_state(), {})
        self.assertFalse(bsw.is_setup_known_complete())

    def test_mark_then_load_roundtrip(self):
        bsw.mark_setup_complete("0.6.0")
        state = bsw.load_setup_state()
        self.assertEqual(state["userscript_version"], "0.6.0")
        self.assertIn("completed_at", state)
        self.assertEqual(state["min_version_at_completion"],
                         tb.MIN_USERSCRIPT_VERSION)
        self.assertTrue(bsw.is_setup_known_complete())

    def test_outdated_completion_is_not_known_complete(self):
        # If the user finished setup against a version that's now below the
        # current minimum, treat it as needing a refresh.
        bsw.mark_setup_complete("0.5.0")
        self.assertFalse(bsw.is_setup_known_complete())

    def test_corrupt_state_file_treated_as_missing(self):
        path = self.tmp / bsw.SETUP_STATE_FILENAME
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(bsw.load_setup_state(), {})
        self.assertFalse(bsw.is_setup_known_complete())


if __name__ == "__main__":
    unittest.main()
