"""Tests for gitlab_client.verify_connection + update_config (PR-I).

These back the "⚙ GitLab" settings dialog: verify_connection is what
the Test-connection button calls; update_config is what Save calls.
Both must be offline-testable — verify_connection's HTTP is mocked.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gitlab_client as gc


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class VerifyConnectionTests(unittest.TestCase):

    def test_empty_token_short_circuits(self):
        # Blank token falls back to get_token() by design; isolate it so
        # a real token on the test box doesn't turn this into a live call.
        with mock.patch.object(gc, "get_token", return_value=""):
            out = gc.verify_connection(base_url="https://gl", token="")
        self.assertFalse(out["ok"])
        self.assertIn("Token", out["error"])

    def test_empty_base_url_short_circuits(self):
        # Pass token but blank base — and make sure get_base_url can't
        # fill it from a stray config/env on the test box.
        with mock.patch.object(gc, "get_base_url", return_value=""):
            out = gc.verify_connection(base_url="", token="x")
        self.assertFalse(out["ok"])
        self.assertIn("Base URL", out["error"])

    def test_200_returns_identity(self):
        with mock.patch.object(
            gc.requests, "get",
            return_value=_Resp(200, {"username": "lillian",
                                     "name": "Lillian Ding"}),
        ) as g:
            out = gc.verify_connection(
                base_url="https://gl/", token="tok")
        self.assertTrue(out["ok"])
        self.assertEqual(out["username"], "lillian")
        self.assertEqual(out["name"], "Lillian Ding")
        # Trailing slash stripped, /api/v4/user appended, PRIVATE-TOKEN set.
        args, kwargs = g.call_args
        self.assertEqual(args[0], "https://gl/api/v4/user")
        self.assertEqual(kwargs["headers"]["PRIVATE-TOKEN"], "tok")

    def test_401_is_token_error(self):
        with mock.patch.object(gc.requests, "get",
                               return_value=_Resp(401)):
            out = gc.verify_connection(base_url="https://gl", token="bad")
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], 401)

    def test_403_mentions_scope(self):
        with mock.patch.object(gc.requests, "get",
                               return_value=_Resp(403)):
            out = gc.verify_connection(base_url="https://gl", token="t")
        self.assertFalse(out["ok"])
        self.assertIn("read_api", out["error"])

    def test_404_flags_base_url(self):
        with mock.patch.object(gc.requests, "get",
                               return_value=_Resp(404)):
            out = gc.verify_connection(base_url="https://not-gitlab",
                                       token="t")
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], 404)

    def test_network_error_is_caught_not_raised(self):
        with mock.patch.object(
            gc.requests, "get",
            side_effect=requests.exceptions.ConnectionError("no route"),
        ):
            out = gc.verify_connection(base_url="https://gl", token="t")
        self.assertFalse(out["ok"])
        self.assertIsNone(out["status"])
        self.assertIn("连接失败", out["error"])


class UpdateConfigTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="glcfg-")
        self._path = os.path.join(self._tmp, "config.json")
        self._orig = gc.CONFIG_PATH
        gc.CONFIG_PATH = self._path

    def tearDown(self):
        gc.CONFIG_PATH = self._orig
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass
        os.rmdir(self._tmp)

    def test_creates_file_and_writes_keys(self):
        gc.update_config(gitlab_token="tok", gitlab_base_url="https://gl")
        with open(self._path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["gitlab_token"], "tok")
        self.assertEqual(cfg["gitlab_base_url"], "https://gl")

    def test_merge_preserves_unrelated_keys(self):
        # Pre-existing setting from some other feature must survive.
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"bridge_port": 48217}, f)
        gc.update_config(gitlab_token="tok")
        with open(self._path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["bridge_port"], 48217)
        self.assertEqual(cfg["gitlab_token"], "tok")

    def test_none_values_skipped(self):
        # base_url=None means "don't change it" — must not write a null
        # that later reads back as the literal string base.
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"gitlab_base_url": "https://old"}, f)
        gc.update_config(gitlab_token="tok", gitlab_base_url=None)
        with open(self._path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["gitlab_base_url"], "https://old")
        self.assertEqual(cfg["gitlab_token"], "tok")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
