"""Unit tests for tranzor_auth — the platform Bearer-JWT auth layer.

Pure-Python; no real HTTP (requests is mocked where needed). Covers the
token-injection decision, persistence, JWT-expiry probing, login, and that
install() actually routes Session.request through the injector.

Run:  python -m unittest test_tranzor_auth
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tranzor_auth as ta


def _jwt(exp):
    """Build a fake JWT whose payload carries ``exp`` (no real signature)."""
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"hdr.{payload}.sig"


class _TAStateMixin:
    """Reset module-global token state around each test."""

    def setUp(self):
        self._saved_token = ta._token
        self._saved_user = ta._user
        self._saved_path = ta.AUTH_CONFIG_PATH
        self._saved_hosts = set(ta.PLATFORM_HOSTS)
        ta._token = None
        ta._user = None

    def tearDown(self):
        ta._token = self._saved_token
        ta._user = self._saved_user
        ta.AUTH_CONFIG_PATH = self._saved_path
        ta.PLATFORM_HOSTS.clear()
        ta.PLATFORM_HOSTS.update(self._saved_hosts)


class ApplyAuthTests(_TAStateMixin, unittest.TestCase):
    PLAT = "http://tranzor-platform.int.rclabenv.com/api/v1/legacy/tasks"
    OTHER = "https://git.ringcentral.com/api/v4/projects"

    def test_adds_bearer_for_platform_host_when_token_set(self):
        ta._token = "TKN"
        headers = ta.apply_auth(self.PLAT, None)
        self.assertEqual(headers.get("Authorization"), "Bearer TKN")

    def test_no_token_no_header(self):
        headers = ta.apply_auth(self.PLAT, {"X-Foo": "1"})
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers.get("X-Foo"), "1")  # passthrough preserved

    def test_non_platform_host_never_gets_token(self):
        ta._token = "TKN"
        headers = ta.apply_auth(self.OTHER, None)
        self.assertNotIn("Authorization", headers)

    def test_explicit_authorization_is_not_clobbered(self):
        ta._token = "TKN"
        headers = ta.apply_auth(self.PLAT, {"Authorization": "Bearer USERSET"})
        self.assertEqual(headers.get("Authorization"), "Bearer USERSET")

    def test_configure_hosts_extends_allowlist(self):
        ta._token = "TKN"
        ta.configure_hosts("new-platform.example.com")
        headers = ta.apply_auth("http://new-platform.example.com/x", None)
        self.assertEqual(headers.get("Authorization"), "Bearer TKN")


class TokenExpiryTests(_TAStateMixin, unittest.TestCase):
    def test_future_token_is_valid(self):
        ta._token = _jwt(time.time() + 3600)
        self.assertTrue(ta.has_valid_token())

    def test_expired_token_is_invalid(self):
        ta._token = _jwt(time.time() - 10)
        self.assertFalse(ta.has_valid_token())

    def test_undecodable_exp_treated_as_valid(self):
        # No exp claim we can read → let the server be the judge → valid.
        ta._token = "opaque-token-without-jwt-structure"
        self.assertTrue(ta.has_valid_token())

    def test_no_token_is_invalid(self):
        ta._token = None
        self.assertFalse(ta.has_valid_token())


class PersistenceTests(_TAStateMixin, unittest.TestCase):
    def test_set_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            ta.AUTH_CONFIG_PATH = os.path.join(d, "auth.json")
            ta.set_token("ABC", user={"email": "a@ringcentral.com"})
            # Wipe memory, then load from disk.
            ta._token = None
            ta._user = None
            loaded = ta.load()
            self.assertEqual(loaded, "ABC")
            self.assertEqual((ta.get_user() or {}).get("email"),
                             "a@ringcentral.com")

    def test_clear_removes_file_and_memory(self):
        with tempfile.TemporaryDirectory() as d:
            ta.AUTH_CONFIG_PATH = os.path.join(d, "auth.json")
            ta.set_token("ABC")
            self.assertTrue(os.path.isfile(ta.AUTH_CONFIG_PATH))
            ta.clear()
            self.assertIsNone(ta.get_token())
            self.assertFalse(os.path.isfile(ta.AUTH_CONFIG_PATH))


class LoginTests(_TAStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Don't write to the real home dir during login() -> _save().
        self._td = tempfile.TemporaryDirectory()
        ta.AUTH_CONFIG_PATH = os.path.join(self._td.name, "auth.json")

    def tearDown(self):
        self._td.cleanup()
        super().tearDown()

    def _resp(self, status, payload):
        m = mock.Mock()
        m.status_code = status
        m.json.return_value = payload
        m.text = json.dumps(payload)
        return m

    def test_login_success_stores_token(self):
        resp = self._resp(200, {"token": "JWT123",
                                "user": {"email": "x@ringcentral.com"}})
        with mock.patch.object(ta, "requests") as rq:
            rq.post.return_value = resp
            ok, msg = ta.login("x@ringcentral.com", "pw",
                               "http://platform.example.com")
        self.assertTrue(ok)
        self.assertEqual(ta.get_token(), "JWT123")
        # Hit the correct endpoint.
        called_url = rq.post.call_args[0][0]
        self.assertTrue(called_url.endswith("/api/v1/auth/login"))

    def test_login_bad_credentials_returns_detail(self):
        resp = self._resp(401, {"detail": "Invalid email or password"})
        with mock.patch.object(ta, "requests") as rq:
            rq.post.return_value = resp
            ok, msg = ta.login("x", "bad", "http://platform.example.com")
        self.assertFalse(ok)
        self.assertIn("Invalid email or password", msg)
        self.assertIsNone(ta.get_token())

    def test_login_network_error_is_handled(self):
        with mock.patch.object(ta, "requests") as rq:
            rq.post.side_effect = RuntimeError("connection refused")
            ok, msg = ta.login("x", "pw", "http://platform.example.com")
        self.assertFalse(ok)
        self.assertIn("connection refused", msg)


class InstallTests(_TAStateMixin, unittest.TestCase):
    def test_install_routes_session_request_through_injector(self):
        import requests as real_requests
        sessions = real_requests.sessions
        orig = sessions.Session.request
        saved_installed = ta._installed
        seen = {}

        def _recorder(self, method, url, **kwargs):
            seen["headers"] = kwargs.get("headers")
            return "OK"  # no real HTTP

        try:
            sessions.Session.request = _recorder
            ta._installed = False
            ta.install()
            ta._token = "TKN"
            ta.configure_hosts("platform.example.com")
            s = sessions.Session()
            # Platform host → token injected.
            s.request("GET", "http://platform.example.com/api")
            self.assertEqual(seen["headers"].get("Authorization"), "Bearer TKN")
            # Other host → untouched.
            s.request("GET", "https://git.ringcentral.com/api")
            self.assertNotIn("Authorization", seen["headers"] or {})
        finally:
            sessions.Session.request = orig
            ta._installed = saved_installed


if __name__ == "__main__":
    unittest.main()
