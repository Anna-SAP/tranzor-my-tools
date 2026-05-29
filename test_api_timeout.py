"""Tests for export_mr_pipeline._api_get timeout + retry semantics (PR-G).

The user hit "Read timed out (read timeout=30)" on the TM Insight and
Human Revisions panels — both pull the heavy /dashboard/cases endpoint
through _api_get. PR-G lengthens the read timeout and stops retrying
ReadTimeout (retrying a slow backend just multiplies the wait). These
tests pin that contract.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
import export_mr_pipeline as mp


class DefaultTimeoutTests(unittest.TestCase):

    def test_default_timeout_is_connect_read_tuple(self):
        # (connect, read) — connect short so "can't reach host" fails
        # fast; read long so slow endpoints don't trip at 30s.
        self.assertEqual(mp._DEFAULT_TIMEOUT, (10, 120))

    def test_default_timeout_passed_through_when_caller_omits_it(self):
        with mock.patch.object(mp, "_session") as sess:
            sess.get.return_value = mock.MagicMock()
            mp._api_get("http://x/y")
        _, kwargs = sess.get.call_args
        self.assertEqual(kwargs.get("timeout"), (10, 120))

    def test_caller_timeout_overrides_default(self):
        with mock.patch.object(mp, "_session") as sess:
            sess.get.return_value = mock.MagicMock()
            mp._api_get("http://x/y", timeout=5)
        _, kwargs = sess.get.call_args
        self.assertEqual(kwargs.get("timeout"), 5)


class RetrySemanticsTests(unittest.TestCase):

    def test_read_timeout_is_not_retried(self):
        # Slow backend → raise immediately, don't multiply the wait.
        with mock.patch.object(mp, "_session") as sess, \
                mock.patch.object(mp.time, "sleep") as slept:
            sess.get.side_effect = requests.exceptions.ReadTimeout("slow")
            with self.assertRaises(requests.exceptions.ReadTimeout):
                mp._api_get("http://x/cases")
        # Exactly one attempt, no backoff sleep.
        self.assertEqual(sess.get.call_count, 1)
        slept.assert_not_called()

    def test_connection_error_retries_then_raises(self):
        with mock.patch.object(mp, "_session") as sess, \
                mock.patch.object(mp.time, "sleep") as slept:
            sess.get.side_effect = requests.exceptions.ConnectionError("down")
            with self.assertRaises(requests.exceptions.ConnectionError):
                mp._api_get("http://x/y")
        # MAX_RETRIES attempts, MAX_RETRIES-1 backoff sleeps.
        self.assertEqual(sess.get.call_count, mp.MAX_RETRIES)
        self.assertEqual(slept.call_count, mp.MAX_RETRIES - 1)

    def test_connect_timeout_retries(self):
        # ConnectTimeout subclasses both ConnectionError and Timeout —
        # it must take the retry path, not the ReadTimeout fast-fail.
        with mock.patch.object(mp, "_session") as sess, \
                mock.patch.object(mp.time, "sleep") as slept:
            sess.get.side_effect = requests.exceptions.ConnectTimeout("c")
            with self.assertRaises(requests.exceptions.ConnectTimeout):
                mp._api_get("http://x/y")
        self.assertEqual(sess.get.call_count, mp.MAX_RETRIES)

    def test_transient_then_success_returns_response(self):
        # One blip, then OK — caller gets the response, no exception.
        ok = mock.MagicMock(name="response")
        with mock.patch.object(mp, "_session") as sess, \
                mock.patch.object(mp.time, "sleep"):
            sess.get.side_effect = [
                requests.exceptions.ConnectionError("blip"),
                ok,
            ]
            out = mp._api_get("http://x/y")
        self.assertIs(out, ok)
        self.assertEqual(sess.get.call_count, 2)

    def test_success_first_try_no_sleep(self):
        ok = mock.MagicMock(name="response")
        with mock.patch.object(mp, "_session") as sess, \
                mock.patch.object(mp.time, "sleep") as slept:
            sess.get.return_value = ok
            out = mp._api_get("http://x/y")
        self.assertIs(out, ok)
        self.assertEqual(sess.get.call_count, 1)
        slept.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
