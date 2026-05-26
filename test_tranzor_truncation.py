"""Unit tests for tranzor_truncation.hydrate_truncated_entries.

Pure logic + mocked HTTP; never hits a live Tranzor server.

Run:  python -m unittest test_tranzor_truncation
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tranzor_truncation as tt


def _mk_truncated_entry(translation_id=1, *,
                        src_trunc=True, tr_trunc=True,
                        src_preview="src_preview...",
                        tr_preview="tr_preview...",
                        src_len=2479, tr_len=2530):
    return {
        "translation_id": translation_id,
        "opus_id": f"opus-{translation_id}",
        "source_text": src_preview,
        "translated_text": tr_preview,
        "source_text_truncated": src_trunc,
        "translated_text_truncated": tr_trunc,
        "source_text_length": src_len,
        "translated_text_length": tr_len,
    }


def _mk_full_response(translation_id, src_full, tr_full):
    """Mimic the /full-text endpoint JSON payload."""
    resp = mock.Mock()
    resp.raise_for_status = mock.Mock()
    resp.json = mock.Mock(return_value={
        "task_id": 244,
        "source_id": 1000 + translation_id,
        "translation_id": translation_id,
        "opus_id": f"opus-{translation_id}",
        "unit_id": None,
        "source_text": src_full,
        "translated_text": tr_full,
        "target_language": "es-ES",
        "translation_type": "LLM",
    })
    return resp


class IsTruncatedTests(unittest.TestCase):

    def test_neither_flag_set(self):
        self.assertFalse(tt._is_truncated({}))
        self.assertFalse(tt._is_truncated({
            "source_text_truncated": False,
            "translated_text_truncated": False,
        }))
        # None means "not a UNS task" — treat as not truncated.
        self.assertFalse(tt._is_truncated({
            "source_text_truncated": None,
            "translated_text_truncated": None,
        }))

    def test_either_flag_triggers(self):
        self.assertTrue(tt._is_truncated({"source_text_truncated": True}))
        self.assertTrue(tt._is_truncated({"translated_text_truncated": True}))
        self.assertTrue(tt._is_truncated({
            "source_text_truncated": True,
            "translated_text_truncated": True,
        }))


class HydrateTests(unittest.TestCase):

    def test_no_op_when_nothing_truncated(self):
        entries = [{
            "translation_id": 5,
            "source_text": "full",
            "translated_text": "completo",
            "source_text_truncated": False,
            "translated_text_truncated": False,
        }]
        fake_session = mock.Mock()
        n = tt.hydrate_truncated_entries(
            entries, api_base="http://x/api/v1/legacy", task_id=244,
            session=fake_session,
        )
        self.assertEqual(n, 0)
        fake_session.get.assert_not_called()
        # Entries untouched
        self.assertEqual(entries[0]["source_text"], "full")

    def test_skips_entries_without_translation_id(self):
        entries = [{
            "translation_id": None,
            "source_text": "preview...",
            "source_text_truncated": True,
        }]
        fake_session = mock.Mock()
        n = tt.hydrate_truncated_entries(
            entries, api_base="http://x/api/v1/legacy", task_id=244,
            session=fake_session,
        )
        self.assertEqual(n, 0)
        fake_session.get.assert_not_called()

    def test_replaces_truncated_text_and_flips_flags(self):
        entries = [
            _mk_truncated_entry(translation_id=590072,
                                src_preview="<?xml ...Welco...",
                                tr_preview="<?xml ...Bienv...",
                                src_len=2479, tr_len=2530),
            _mk_truncated_entry(translation_id=590073,
                                src_preview="<?xml ...other...",
                                tr_preview="<?xml ...autre...",
                                src_len=2549, tr_len=2610),
        ]
        full_src_1 = "<?xml ..." + "X" * 2470 + "Welcome</html>"
        full_tr_1  = "<?xml ..." + "Y" * 2521 + "Bienvenido</html>"
        full_src_2 = "<?xml ..." + "X" * 2540 + "Activation</html>"
        full_tr_2  = "<?xml ..." + "Y" * 2601 + "Activacion</html>"

        fake_session = mock.Mock()
        def _get(url, timeout=30):
            if "590072" in url:
                return _mk_full_response(590072, full_src_1, full_tr_1)
            if "590073" in url:
                return _mk_full_response(590073, full_src_2, full_tr_2)
            raise AssertionError(f"unexpected url: {url}")
        fake_session.get.side_effect = _get

        n = tt.hydrate_truncated_entries(
            entries,
            api_base="http://x/api/v1/legacy",
            task_id=244,
            session=fake_session,
            max_workers=2,
        )
        self.assertEqual(n, 2)

        by_id = {e["translation_id"]: e for e in entries}
        self.assertEqual(by_id[590072]["source_text"], full_src_1)
        self.assertEqual(by_id[590072]["translated_text"], full_tr_1)
        self.assertFalse(by_id[590072]["source_text_truncated"])
        self.assertFalse(by_id[590072]["translated_text_truncated"])
        self.assertEqual(by_id[590072]["source_text_length"], len(full_src_1))
        self.assertEqual(by_id[590072]["translated_text_length"], len(full_tr_1))

        self.assertEqual(by_id[590073]["source_text"], full_src_2)
        self.assertEqual(by_id[590073]["translated_text"], full_tr_2)

    def test_url_uses_api_base_task_and_translation_id(self):
        entries = [_mk_truncated_entry(translation_id=590072)]
        fake_session = mock.Mock()
        fake_session.get.return_value = _mk_full_response(
            590072, "full src", "full tr",
        )

        tt.hydrate_truncated_entries(
            entries,
            api_base="http://example/api/v1/legacy",
            task_id=244,
            session=fake_session,
            max_workers=1,
        )

        called_url = fake_session.get.call_args[0][0]
        self.assertEqual(
            called_url,
            "http://example/api/v1/legacy/tasks/244/translations/590072/full-text",
        )

    def test_individual_failure_does_not_break_others(self):
        entries = [
            _mk_truncated_entry(translation_id=1),
            _mk_truncated_entry(translation_id=2),
            _mk_truncated_entry(translation_id=3),
        ]
        good = _mk_full_response(2, "GOOD SRC 2", "GOOD TR 2")

        def _get(url, timeout=30):
            if "/2/full-text" in url:
                return good
            # 1 and 3 raise
            raise RuntimeError("simulated 500")

        fake_session = mock.Mock()
        fake_session.get.side_effect = _get

        with mock.patch("sys.stderr"):  # silence error log
            n = tt.hydrate_truncated_entries(
                entries,
                api_base="http://x/api/v1/legacy",
                task_id=244,
                session=fake_session,
                max_workers=3,
            )
        self.assertEqual(n, 1)
        by_id = {e["translation_id"]: e for e in entries}
        self.assertEqual(by_id[2]["source_text"], "GOOD SRC 2")
        self.assertFalse(by_id[2]["source_text_truncated"])
        # The two failing entries keep their preview values + truncation flags.
        self.assertTrue(by_id[1]["source_text_truncated"])
        self.assertTrue(by_id[3]["source_text_truncated"])

    def test_serial_mode_when_single_target(self):
        entries = [
            _mk_truncated_entry(translation_id=99),
            # second one already complete, so only 1 target
            {
                "translation_id": 100,
                "source_text": "full",
                "translated_text": "completo",
                "source_text_truncated": False,
                "translated_text_truncated": False,
            },
        ]
        fake_session = mock.Mock()
        fake_session.get.return_value = _mk_full_response(99, "S", "T")
        n = tt.hydrate_truncated_entries(
            entries,
            api_base="http://x/api/v1/legacy",
            task_id=244,
            session=fake_session,
            max_workers=4,
        )
        self.assertEqual(n, 1)
        self.assertEqual(entries[0]["source_text"], "S")


if __name__ == "__main__":
    unittest.main()
