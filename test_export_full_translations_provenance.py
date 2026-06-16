"""Tests for the full-provenance Merge-to-JSON enhancements:

  - winner policy: prefer human-reviewed / high-score, then newest
  - ``_all_sources``: every task that translated a key, newest first
  - ``inconsistencies_in_new``: "yes" when the kept authoritative value is not
    the newest divergent one, else "no"

Run:  python -m unittest test_export_full_translations_provenance
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_full_translations as ft

OID = "RingCentral.webModule.dba1c95d7993e5d977b34efe8eaa9ed2.app.rooms.CONTENT_WITH_FILM_STRIP"


def _mr_meta(task_id, iid, created_at):
    return {
        "source": "MR",
        "task_id": task_id,
        "task_name": f"MR#{iid} web/web",
        "project_id": "web/web",
        "merge_request_iid": iid,
        "release": "Release 26.3",
        "created_at": created_at,
    }


def _legacy_meta(task_id, created_at):
    return {
        "source": "Legacy",
        "task_id": task_id,
        "task_name": f"Legacy {task_id}",
        "created_at": created_at,
    }


def _ingest_mr(inv, value, *, lang="zh-HK", src="S", score=None,
               task_id="t", iid=1, created_at="2026-06-10T00:00:00"):
    inv.ingest_entries(
        [{"opus_id": OID, "target_language": lang, "translated_text": value,
          "source_text": src, "final_score": score}],
        source_locale="en-US",
        source_meta=_mr_meta(task_id, iid, created_at),
        recency=created_at,
        score_key="final_score",
    )


def _ingest_legacy(inv, value, *, lang="zh-HK", src="S", ttype="LLM",
                   task_id="L1", created_at="2026-06-10T00:00:00"):
    inv.ingest_entries(
        [{"opus_id": OID, "target_language": lang, "translated_text": value,
          "source_text": src, "translation_type": ttype}],
        source_locale="en-US",
        source_meta=_legacy_meta(task_id, created_at),
        recency=created_at,
        type_key="translation_type",
        fixed_key="fixed_by_lead",
    )


def _build(inv):
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "merged.json")
    ft.build_merged_json(inv, out, products=None, locales=None,
                         progress_cb=lambda *_: None)
    with open(out, encoding="utf-8") as f:
        records = json.load(f)
    return {r["key"]: r for r in records}


class ProvenanceTest(unittest.TestCase):
    def _inv(self):
        inv = ft.FullTranslationInventory()
        inv.track_all_sources = True
        return inv

    # ---- _all_sources lists every contributing task --------------------
    def test_all_sources_lists_every_task_newest_first(self):
        inv = self._inv()
        _ingest_mr(inv, "舊", score=80, task_id="taskA", iid=40258,
                   created_at="2026-06-10T00:00:00")
        _ingest_mr(inv, "新", score=80, task_id="taskB", iid=40490,
                   created_at="2026-06-11T00:00:00")
        rec = _build(inv)[OID]
        srcs = rec["_all_sources"]
        self.assertEqual([s["task_id"] for s in srcs], ["taskB", "taskA"])
        # taskB is newest -> wins zh-HK and en-US.
        b = next(s for s in srcs if s["task_id"] == "taskB")
        self.assertIn("zh-HK", b["won_locales"])
        self.assertEqual(b["translations"]["zh-HK"], "新")
        a = next(s for s in srcs if s["task_id"] == "taskA")
        self.assertEqual(a["translations"]["zh-HK"], "舊")
        self.assertEqual(a["merge_request_iid"], 40258)

    # ---- winner policy: high score beats newer low score ---------------
    def test_higher_score_beats_newer_lower_score(self):
        inv = self._inv()
        _ingest_mr(inv, "高分舊", score=95, task_id="hi", iid=1,
                   created_at="2026-06-10T00:00:00")
        _ingest_mr(inv, "低分新", score=70, task_id="lo", iid=2,
                   created_at="2026-06-12T00:00:00")
        rec = _build(inv)[OID]
        self.assertEqual(rec["zh-HK"], "高分舊")
        # winner (older) diverges from newest -> flag yes.
        self.assertEqual(rec["inconsistencies_in_new"], "yes")

    # ---- winner policy: human-edited beats newer machine ---------------
    def test_human_edit_beats_newer_machine(self):
        inv = self._inv()
        _ingest_legacy(inv, "人工审校", ttype="Manual Edit", task_id="L_old",
                       created_at="2026-06-09T00:00:00")
        _ingest_legacy(inv, "机翻新", ttype="LLM", task_id="L_new",
                       created_at="2026-06-13T00:00:00")
        rec = _build(inv)[OID]
        self.assertEqual(rec["zh-HK"], "人工审校")
        self.assertEqual(rec["inconsistencies_in_new"], "yes")

    # ---- inconsistency flag = no when newest also wins -----------------
    def test_flag_no_when_newest_wins(self):
        inv = self._inv()
        _ingest_mr(inv, "v1", score=80, task_id="t1", iid=1,
                   created_at="2026-06-10T00:00:00")
        _ingest_mr(inv, "v2", score=80, task_id="t2", iid=2,
                   created_at="2026-06-11T00:00:00")
        rec = _build(inv)[OID]
        self.assertEqual(rec["zh-HK"], "v2")  # newest, equal score
        self.assertEqual(rec["inconsistencies_in_new"], "no")

    def test_flag_no_for_single_candidate(self):
        inv = self._inv()
        _ingest_mr(inv, "only", score=88, task_id="solo", iid=1,
                   created_at="2026-06-10T00:00:00")
        rec = _build(inv)[OID]
        self.assertEqual(rec["inconsistencies_in_new"], "no")
        self.assertEqual(len(rec["_all_sources"]), 1)

    # ---- en-US source: newest spelling wins, all variants in sources ---
    def test_en_us_newest_spelling_wins_and_variants_listed(self):
        inv = self._inv()
        _ingest_mr(inv, "x", src="Content with film strip", task_id="old",
                   iid=40258, created_at="2026-06-10T00:00:00")
        _ingest_mr(inv, "y", src="Content with filmstrip", task_id="new",
                   iid=40490, created_at="2026-06-11T00:00:00")
        rec = _build(inv)[OID]
        self.assertEqual(rec["en-US"], "Content with filmstrip")
        en_variants = {s["translations"].get("en-US")
                       for s in rec["_all_sources"]}
        self.assertEqual(
            en_variants, {"Content with film strip", "Content with filmstrip"})

    # ---- provenance off by default (zip / watchtower path) -------------
    def test_provenance_absent_when_not_tracking(self):
        inv = ft.FullTranslationInventory()  # track_all_sources stays False
        _ingest_mr(inv, "v", score=80, task_id="t", iid=1,
                   created_at="2026-06-10T00:00:00")
        rec = _build(inv)[OID]
        self.assertNotIn("_all_sources", rec)
        self.assertNotIn("inconsistencies_in_new", rec)
        # but the plain _source provenance still works.
        self.assertIn("_source", rec)


if __name__ == "__main__":
    unittest.main()
