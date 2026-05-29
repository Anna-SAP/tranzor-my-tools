"""Tests for the build-time embedded GitLab credential (PR-J).

Covers the obfuscation round-trip, the precedence in get_token /
get_base_url (env > config > embedded), and write_embedded_credentials.
No real module is imported — _read_embedded is mocked so tests don't
depend on whether a build artifact happens to exist on disk.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gitlab_client as gc


class ObfuscateTests(unittest.TestCase):

    def test_round_trip(self):
        for s in ["glpat-abc123", "https://git.example.com", "你好-token",
                  "a", "x" * 200]:
            self.assertEqual(gc._deobfuscate(gc._obfuscate(s)), s)

    def test_empty_round_trips_to_empty(self):
        self.assertEqual(gc._obfuscate(""), "")
        self.assertEqual(gc._deobfuscate(""), "")

    def test_obfuscated_is_not_plaintext(self):
        # The whole point: the literal token must not appear verbatim in
        # the obfuscated string (so `strings` won't surface it directly).
        tok = "glpat-SECRETvalue123"
        self.assertNotIn(tok, gc._obfuscate(tok))

    def test_deobfuscate_bad_data_returns_empty(self):
        # Corrupt embed module must never crash the client.
        self.assertEqual(gc._deobfuscate("!!!not base64!!!"), "")


class TokenPrecedenceTests(unittest.TestCase):

    def setUp(self):
        # Neutralize ambient env + config so each test controls all layers.
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("TRANZOR_GITLAB_TOKEN", None)
        os.environ.pop("TRANZOR_GITLAB_BASE_URL", None)
        self.addCleanup(self._env.stop)

    def test_env_wins_over_config_and_embedded(self):
        os.environ["TRANZOR_GITLAB_TOKEN"] = "env-tok"
        with mock.patch.object(gc, "load_config",
                               return_value={"gitlab_token": "cfg-tok"}), \
                mock.patch.object(gc, "_read_embedded",
                                  return_value=("emb-tok", "")):
            self.assertEqual(gc.get_token(), "env-tok")

    def test_config_wins_over_embedded(self):
        with mock.patch.object(gc, "load_config",
                               return_value={"gitlab_token": "cfg-tok"}), \
                mock.patch.object(gc, "_read_embedded",
                                  return_value=("emb-tok", "")):
            self.assertEqual(gc.get_token(), "cfg-tok")

    def test_embedded_used_when_env_and_config_empty(self):
        # This is the language-reviewer path: nothing configured locally,
        # so the build-time embedded service-account token is used.
        with mock.patch.object(gc, "load_config", return_value={}), \
                mock.patch.object(gc, "_read_embedded",
                                  return_value=("emb-tok", "")):
            self.assertEqual(gc.get_token(), "emb-tok")

    def test_all_empty_returns_empty_string(self):
        with mock.patch.object(gc, "load_config", return_value={}), \
                mock.patch.object(gc, "_read_embedded",
                                  return_value=("", "")):
            self.assertEqual(gc.get_token(), "")

    def test_base_url_embedded_used_then_falls_back_to_default(self):
        with mock.patch.object(gc, "load_config", return_value={}):
            with mock.patch.object(gc, "_read_embedded",
                                   return_value=("", "https://emb.gitlab")):
                self.assertEqual(gc.get_base_url(), "https://emb.gitlab")
            with mock.patch.object(gc, "_read_embedded",
                                   return_value=("", "")):
                self.assertEqual(gc.get_base_url(), gc.DEFAULT_BASE_URL)


class ReadEmbeddedTests(unittest.TestCase):

    def test_missing_module_returns_empty_pair(self):
        # Ensure no stray gitlab_token_embed is importable, then assert
        # the graceful empty fallback.
        sys.modules.pop("gitlab_token_embed", None)
        with mock.patch.dict(sys.modules, {"gitlab_token_embed": None}):
            # importing None raises ImportError → caught → ("","")
            self.assertEqual(gc._read_embedded(), ("", ""))


class WriteEmbeddedCredentialsTests(unittest.TestCase):

    def test_generates_importable_module_with_round_trip(self):
        tmp = tempfile.mkdtemp(prefix="embed-")
        path = os.path.join(tmp, "gitlab_token_embed.py")
        try:
            gc.write_embedded_credentials(
                "glpat-xyz", "https://git.example.com", path=path)
            # Load the generated module and verify deobfuscation recovers
            # the originals.
            ns = {}
            with open(path, encoding="utf-8") as f:
                exec(compile(f.read(), path, "exec"), ns)
            self.assertEqual(gc._deobfuscate(ns["TOKEN_B64"]), "glpat-xyz")
            self.assertEqual(
                gc._deobfuscate(ns["BASE_URL_B64"]), "https://git.example.com")
            # And the raw token is not sitting in the file as plaintext.
            with open(path, encoding="utf-8") as f:
                self.assertNotIn("glpat-xyz", f.read())
        finally:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            os.rmdir(tmp)

    def test_empty_token_writes_empty_module(self):
        tmp = tempfile.mkdtemp(prefix="embed-")
        path = os.path.join(tmp, "gitlab_token_embed.py")
        try:
            gc.write_embedded_credentials("", "", path=path)
            ns = {}
            with open(path, encoding="utf-8") as f:
                exec(compile(f.read(), path, "exec"), ns)
            self.assertEqual(ns["TOKEN_B64"], "")
            self.assertEqual(gc._deobfuscate(ns["TOKEN_B64"]), "")
        finally:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            os.rmdir(tmp)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
