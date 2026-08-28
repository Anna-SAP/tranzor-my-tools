"""Tests for named MR Pipeline Project presets.

Pure list ops (no Tk / no disk). Persistence tests patch gitlab_client
load/save so we never touch ~/.tranzor_exporter_config.json.

Run:  python -m unittest test_project_presets
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_presets as pp


def _p(name, ids, ts=1.0):
    return {"name": name, "project_ids": list(ids), "updated_at": ts}


class CoerceTests(unittest.TestCase):
    def test_drops_junk_and_sorts_mru(self):
        raw = [
            {"name": "old", "project_ids": ["a"], "updated_at": 1},
            {"name": "new", "project_ids": ["b"], "updated_at": 9},
            {"name": "", "project_ids": ["c"]},
            {"name": "empty", "project_ids": []},
            "nope",
            {"name": "dup-ids", "project_ids": ["x", "x", " y "]},
        ]
        out = pp.coerce_presets(raw)
        self.assertEqual([p["name"] for p in out], ["new", "old", "dup-ids"])
        self.assertEqual(out[2]["project_ids"], ["x", "y"])

    def test_caps_and_dedupes_names(self):
        raw = [_p(f"n{i}", ["a"], ts=i) for i in range(15)]
        raw.append(_p("N0", ["b"], ts=99))  # same name, different case — skip
        out = pp.coerce_presets(raw)
        self.assertEqual(len(out), pp.MAX_PRESETS)
        self.assertEqual(out[0]["name"], "n14")


class MatchAndApplyTests(unittest.TestCase):
    def test_matching_name_is_mru_set_equality(self):
        presets = [
            _p("UNS", ["common/uns", "dash/dash"], ts=2),
            _p("Also", ["dash/dash", "common/uns"], ts=1),
        ]
        self.assertEqual(
            pp.matching_name(["dash/dash", "common/uns"], presets), "UNS")
        self.assertIsNone(pp.matching_name(["common/uns"], presets))
        self.assertIsNone(pp.matching_name([], presets))

    def test_apply_ids_drops_gone_keeps_order(self):
        self.assertEqual(
            pp.apply_ids(["gone", "keep/me", "keep/me", "also"],
                         ["also", "keep/me", "other"]),
            ["keep/me", "also"])


class MutateTests(unittest.TestCase):
    def test_upsert_inserts_front(self):
        rows, err = pp.upsert_preset([], "UNS", ["a", "b"], now=5)
        self.assertIsNone(err)
        self.assertEqual(rows[0]["name"], "UNS")
        self.assertEqual(rows[0]["project_ids"], ["a", "b"])
        self.assertEqual(rows[0]["updated_at"], 5)

    def test_upsert_replaces_case_insensitive(self):
        start = [_p("uns", ["old"], ts=1), _p("Dash", ["d"], ts=2)]
        rows, err = pp.upsert_preset(start, "UNS", ["new"], now=9)
        self.assertIsNone(err)
        self.assertEqual([p["name"] for p in rows], ["UNS", "Dash"])
        self.assertEqual(rows[0]["project_ids"], ["new"])

    def test_upsert_rejects_empty_and_limit(self):
        _, err = pp.upsert_preset([], "  ", ["a"])
        self.assertEqual(err, "empty_name")
        _, err = pp.upsert_preset([], "x", [])
        self.assertEqual(err, "empty_ids")
        full = [_p(f"n{i}", ["a"], ts=i) for i in range(pp.MAX_PRESETS)]
        _, err = pp.upsert_preset(full, "new", ["a"])
        self.assertEqual(err, "limit")
        rows, err = pp.upsert_preset(full, "n0", ["z"])  # replace is ok
        self.assertIsNone(err)
        self.assertEqual(rows[0]["project_ids"], ["z"])

    def test_delete_and_rename_and_touch(self):
        start = [_p("UNS", ["a"], ts=1), _p("Dash", ["b"], ts=2)]
        gone = pp.delete_preset(start, "uns")
        self.assertEqual([p["name"] for p in gone], ["Dash"])

        rows, err = pp.rename_preset(start, "UNS", "Core", now=8)
        self.assertIsNone(err)
        self.assertEqual(rows[0]["name"], "Core")

        _, err = pp.rename_preset(start, "UNS", "Dash")
        self.assertEqual(err, "duplicate")

        touched = pp.touch_preset(start, "UNS", now=99)
        self.assertEqual(touched[0]["name"], "UNS")
        self.assertEqual(touched[0]["updated_at"], 99)


class PersistenceTests(unittest.TestCase):
    def test_load_namespaces_env_and_migrates_flat_list(self):
        cfg = {pp.PRESETS_KEY: [_p("flat", ["a"], ts=1)]}
        with mock.patch("gitlab_client.load_config", return_value=cfg):
            prod = pp.load_presets("prod")
            stage = pp.load_presets("stage")
        self.assertEqual([p["name"] for p in prod], ["flat"])
        self.assertEqual(stage, [])

    def test_save_merges_env_without_clobbering_sibling(self):
        existing = {
            "gitlab_token": "SECRET",
            pp.PRESETS_KEY: {"prod": [_p("keep", ["x"], ts=1)]},
        }
        writes = []

        def fake_update(**kwargs):
            writes.append(kwargs)

        with mock.patch("gitlab_client.load_config", return_value=existing), \
             mock.patch("gitlab_client.update_config", side_effect=fake_update):
            pp.save_presets("stage", [_p("S", ["y"], ts=2)])
        self.assertEqual(len(writes), 1)
        blob = writes[0][pp.PRESETS_KEY]
        self.assertEqual([p["name"] for p in blob["prod"]], ["keep"])
        self.assertEqual([p["name"] for p in blob["stage"]], ["S"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
