"""Tests for the TM Panel pure-logic layer (:mod:`tm_panel`).

Covers OPUS parsing, ignore-hash needles, record/ICE/provenance shaping,
hash lineage grouping, and the search orchestrator with injected fakes.
No tkinter, no live HTTP.

Run:  python -m unittest test_tm_panel
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tm_panel as tm  # noqa: E402


LEGACY_ID = (
    "RingCentral.webModule.25cc40ac3437d65883ce5335edcfa062."
    "app.visualIVR.EMPTY_TEXT"
)
CURRENT_ID = (
    "RingCentral.webModule.f27bf4d784887bfa632947db9f86fe2d."
    "app.visualIVR.EMPTY_TEXT"
)
SOURCE = "No items to display"


def _record(opus_id, lang="zh-CN", text="暂无内容", **extra):
    row = {
        "translation_id": extra.pop(
            "translation_id", f"{hash(opus_id) & 0xFFFFFFFF:08x}:{lang}"),
        "task_id": extra.pop("task_id", "task-1"),
        "task_name": "webModule MR#1",
        "product_line": "webModule",
        "project_id": extra.pop("project_id", "web/webModule"),
        "opus_id": opus_id,
        "source_text": extra.pop("source_text", SOURCE),
        "target_language": lang,
        "translated_text": text,
        "translation_type": extra.pop("translation_type", None),
        "created_at": extra.pop("created_at", "2026-09-01T00:00:00Z"),
        "source_type": extra.pop("source_type", "mr"),
        "mr_iid": extra.pop("mr_iid", 42),
    }
    row.update(extra)
    return row


class ParseOpusIdTests(unittest.TestCase):

    def test_full_id(self):
        parsed = tm.parse_opus_id(LEGACY_ID)
        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["alias"], "webModule")
        self.assertEqual(parsed["path_hash"], "25cc40ac3437d65883ce5335edcfa062")
        self.assertEqual(parsed["logical_key"], "app.visualIVR.EMPTY_TEXT")

    def test_strips_quotes_and_seg_uid(self):
        parsed = tm.parse_opus_id(f'"{CURRENT_ID}:::seg:::10"')
        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["logical_key"], "app.visualIVR.EMPTY_TEXT")
        self.assertEqual(parsed["path_hash"], "f27bf4d784887bfa632947db9f86fe2d")

    def test_logical_key_only(self):
        parsed = tm.parse_opus_id("app.visualIVR.EMPTY_TEXT")
        self.assertFalse(parsed["valid"])
        self.assertEqual(parsed["logical_key"], "app.visualIVR.EMPTY_TEXT")

    def test_hash_only_needle(self):
        needle = tm.ignore_hash_needle("25cc40ac3437d65883ce5335edcfa062")
        self.assertEqual(needle, "25cc40ac3437d65883ce5335edcfa062")

    def test_ignore_hash_drops_hash(self):
        self.assertEqual(
            tm.ignore_hash_needle(LEGACY_ID),
            "app.visualIVR.EMPTY_TEXT",
        )
        self.assertEqual(
            tm.ignore_hash_needle(CURRENT_ID),
            "app.visualIVR.EMPTY_TEXT",
        )


class WordCountTests(unittest.TestCase):

    def test_short_string_threshold(self):
        self.assertTrue(tm.is_short_string("Cancel"))
        self.assertTrue(tm.is_short_string("No items"))
        self.assertFalse(tm.is_short_string("No items to display"))
        self.assertEqual(tm.word_count("No items to display"), 4)
        self.assertTrue(tm.ice_allows_content_only("No items to display"))
        self.assertFalse(tm.ice_allows_content_only("Cancel"))


class RecordSearchParamsTests(unittest.TestCase):

    def test_ignore_hash_uses_logical_key_fuzzy(self):
        intent = tm.QueryIntent(query=LEGACY_ID, match_mode="ignore_hash")
        params = tm.record_search_params(intent)
        self.assertEqual(params["opus_id"], "app.visualIVR.EMPTY_TEXT")
        self.assertEqual(params["match_mode"], "fuzzy")

    def test_exact_keeps_full_id(self):
        intent = tm.QueryIntent(query=CURRENT_ID, match_mode="exact")
        params = tm.record_search_params(intent)
        self.assertEqual(params["opus_id"], CURRENT_ID)
        self.assertEqual(params["match_mode"], "exact")

    def test_source_and_lang_pass_through(self):
        intent = tm.QueryIntent(
            source_text=SOURCE, target_language="zh-CN", product_line="webModule"
        )
        params = tm.record_search_params(intent)
        self.assertEqual(params["source_text"], SOURCE)
        self.assertEqual(params["target_language"], "zh-CN")
        self.assertEqual(params["product_line"], "webModule")

    def test_validate_requires_a_narrowing_field(self):
        self.assertEqual(tm.validate_intent(tm.QueryIntent()), "need_filter")
        self.assertIsNone(tm.validate_intent(tm.QueryIntent(query="EMPTY_TEXT")))

    def test_local_kwargs_ignore_hash_uses_logical_key(self):
        kwargs = tm.local_search_kwargs(
            tm.QueryIntent(query=LEGACY_ID, match_mode="ignore_hash")
        )
        self.assertEqual(kwargs["logical_key_contains"], "app.visualIVR.EMPTY_TEXT")
        self.assertEqual(kwargs["product"], "webModule")


class IceProbeTests(unittest.TestCase):

    def test_probes_unique_opus_source_pairs(self):
        records = [
            tm.parse_record_entry(_record(LEGACY_ID, "zh-CN")),
            tm.parse_record_entry(_record(LEGACY_ID, "ja-JP", "表示する項目がありません")),
            tm.parse_record_entry(_record(CURRENT_ID, "zh-CN", created_at="2026-09-08T00:00:00Z")),
        ]
        intent = tm.QueryIntent(query=CURRENT_ID, source_text=SOURCE)
        items = tm.ice_probe_items(intent, records)
        pairs = {(it["opusID"], it["stringValue"]) for it in items}
        self.assertIn((CURRENT_ID, SOURCE), pairs)
        self.assertIn((LEGACY_ID, SOURCE), pairs)
        self.assertLessEqual(len(items), 3)

    def test_dummy_probe_for_long_source_without_records(self):
        intent = tm.QueryIntent(source_text="No items to display in this view")
        items = tm.ice_probe_items(intent, [])
        self.assertEqual(items[0]["opusID"], tm.ICE_DUMMY_OPUS_ID)
        self.assertEqual(items[0]["stringValue"], intent.source_text)

    def test_no_dummy_for_short_source(self):
        items = tm.ice_probe_items(tm.QueryIntent(source_text="Cancel"), [])
        self.assertEqual(items, [])


class IceParseTests(unittest.TestCase):

    def test_hit_and_miss(self):
        hit = tm.parse_ice_item({
            "opusID": CURRENT_ID,
            "stringValue": SOURCE,
            "translationMemoryMatchingResult": {
                "type": "ICE",
                "translations": {"zh-CN": "暂无内容", "ja-JP": "なし"},
            },
        })
        self.assertTrue(hit["hit"])
        self.assertEqual(hit["match_type"], "ICE")
        self.assertEqual(hit["translations"]["zh-CN"], "暂无内容")

        miss = tm.parse_ice_item({
            "opusID": LEGACY_ID,
            "stringValue": SOURCE,
            "translationMemoryMatchingResult": {"type": "NO MATCH", "translations": {}},
        })
        self.assertFalse(miss["hit"])


class LineageTests(unittest.TestCase):

    def test_groups_two_hashes_same_logical_key(self):
        records = [
            tm.parse_record_entry(_record(
                LEGACY_ID, created_at="2026-01-01T00:00:00Z")),
            tm.parse_record_entry(_record(
                CURRENT_ID, created_at="2026-09-08T00:00:00Z")),
        ]
        groups = tm.group_lineages(
            records, query_hash="25cc40ac3437d65883ce5335edcfa062"
        )
        self.assertEqual(len(groups), 1)
        family = groups[0]
        self.assertEqual(family["logical_key"], "app.visualIVR.EMPTY_TEXT")
        self.assertEqual(family["hash_count"], 2)
        roles = {b["path_hash"]: b["role"] for b in family["hashes"]}
        self.assertIn("newest", roles["f27bf4d784887bfa632947db9f86fe2d"])
        self.assertIn("pasted", roles["25cc40ac3437d65883ce5335edcfa062"])

    def test_classify_hash_role(self):
        self.assertEqual(
            tm.classify_hash_role("aaa", query_hash="aaa", newest_hash="aaa"),
            "pasted+newest",
        )
        self.assertEqual(
            tm.classify_hash_role("bbb", query_hash="aaa", newest_hash="ccc"),
            "other",
        )


class ProvenanceTests(unittest.TestCase):

    def test_apply_provenance_by_translation_id(self):
        records = [tm.parse_record_entry(_record(CURRENT_ID, translation_id="t-1"))]
        cases = [{
            "translation_id": "t-1",
            "opus_id": CURRENT_ID,
            "target_language": "zh-CN",
            "translated_text": "暂无内容",
            "tm_match": True,
            "ice_match": False,
            "cached": False,
            "fixed_by_lead": "lead@rc",
            "tm_match_category": "exact",
        }]
        merged = tm.apply_provenance(records, cases)
        self.assertTrue(merged[0]["tm_match"])
        self.assertEqual(merged[0]["fixed_by_lead"], "lead@rc")
        self.assertTrue(tm.is_shared_tm_hit(merged[0]))

    def test_file_tm_type_counts_as_shared(self):
        row = tm.parse_record_entry(_record(
            CURRENT_ID, source_type="file", translation_type="TM", mr_iid=None
        ))
        self.assertTrue(row["file_tm"])
        self.assertTrue(tm.is_shared_tm_hit(row))

    def test_provenance_targets_unique_mrs(self):
        records = [
            tm.parse_record_entry(_record(CURRENT_ID, mr_iid=42, project_id="web/webModule")),
            tm.parse_record_entry(_record(LEGACY_ID, mr_iid=42, project_id="web/webModule", lang="ja-JP")),
            tm.parse_record_entry(_record(CURRENT_ID, mr_iid=7, project_id="web/webModule", translation_id="x")),
        ]
        targets = tm.provenance_targets(records)
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0][1], 42)


class MergeAndLocalTests(unittest.TestCase):

    def test_merge_prefers_unique_translation_id(self):
        a = tm.parse_record_entry(_record(CURRENT_ID, translation_id="t-1"))
        b = tm.parse_record_entry(_record(CURRENT_ID, translation_id="t-1", lang="de-DE"))
        # same id — second dropped
        merged = tm.merge_records([a, b])
        self.assertEqual(len(merged), 1)

    def test_flatten_local_cards(self):
        payload = {
            "results": [{
                "opus_id": CURRENT_ID,
                "alias": "webModule",
                "source_text": SOURCE,
                "source_kind": "mr",
                "translations": [
                    {"target_language": "zh-CN", "translated_text": "暂无内容",
                     "task_created_at": "2026-09-08"},
                ],
            }]
        }
        rows = tm.flatten_local_cards(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["origin"], "local")
        self.assertEqual(rows[0]["logical_key"], "app.visualIVR.EMPTY_TEXT")


class OrchestratorTests(unittest.TestCase):

    def test_need_filter_short_circuits(self):
        view = tm.search_tm_layers(tm.QueryIntent())
        self.assertFalse(view["ok"])
        self.assertEqual(view["error"], "need_filter")

    def test_ignore_hash_search_and_ice_and_shared(self):
        def search_fn(**kwargs):
            self.assertEqual(kwargs["opus_id"], "app.visualIVR.EMPTY_TEXT")
            self.assertEqual(kwargs["match_mode"], "fuzzy")
            return {
                "total": 2,
                "entries": [
                    _record(LEGACY_ID, created_at="2026-01-01T00:00:00Z"),
                    _record(CURRENT_ID, created_at="2026-09-08T00:00:00Z"),
                ],
            }

        def ice_fn(items, languages):
            self.assertIsNone(languages)
            out = []
            for it in items:
                hit = it["opusID"] == LEGACY_ID
                out.append({
                    "opusID": it["opusID"],
                    "stringValue": it["stringValue"],
                    "translationMemoryMatchingResult": {
                        "type": "ICE" if hit else "NO MATCH",
                        "translations": {"zh-CN": "暂无内容"} if hit else {},
                    },
                })
            return out

        def provenance_fn(**kwargs):
            self.assertEqual(kwargs["mr_iid"], 42)
            return [{
                "opus_id": CURRENT_ID,
                "raw_opus_id": CURRENT_ID,
                "target_language": "zh-CN",
                "translated_text": "暂无内容",
                "tm_match": True,
                "ice_match": False,
                "cached": False,
            }]

        view = tm.search_tm_layers(
            tm.QueryIntent(
                query=CURRENT_ID, match_mode="ignore_hash",
                source_text="Cancel",
            ),
            search_fn=search_fn,
            ice_fn=ice_fn,
            provenance_fn=provenance_fn,
        )
        self.assertTrue(view["ok"])
        self.assertEqual(view["kpis"]["hash_variants"], 2)
        self.assertEqual(view["kpis"]["ice_store_hits"], 1)
        self.assertGreaterEqual(view["kpis"]["shared_hits"], 1)
        self.assertIn("multiple_hashes", view["warnings"])
        self.assertIn("short_string_id_sensitive", view["warnings"])
        self.assertEqual(view["anatomy"]["needle"], "app.visualIVR.EMPTY_TEXT")
        self.assertEqual(view["status"]["ice"], "ok")
        self.assertEqual(view["status"]["shared"], "ok")

    def test_ice_layer_fail_open(self):
        def search_fn(**kwargs):
            return {"total": 1, "entries": [_record(CURRENT_ID)]}

        def ice_fn(items, languages):
            raise RuntimeError("context-service down")

        view = tm.search_tm_layers(
            tm.QueryIntent(query="EMPTY_TEXT", layers=frozenset({"ice", "records"})),
            search_fn=search_fn,
            ice_fn=ice_fn,
        )
        self.assertTrue(view["ok"])
        self.assertEqual(view["status"]["ice"], "error")
        self.assertIn("ice", view["errors"])
        self.assertEqual(view["kpis"]["record_rows"], 1)

    def test_records_pagination_stops_on_short_page(self):
        calls = []

        def search_fn(**kwargs):
            calls.append(kwargs["offset"])
            return {"total": 2, "entries": [_record(CURRENT_ID, translation_id=str(kwargs["offset"]))]}

        rows, total, err = tm.paginate_records(search_fn, {"limit": 200, "opus_id": "x"})
        self.assertEqual(err, "")
        self.assertEqual(total, 2)
        self.assertEqual(calls, [0])
        self.assertEqual(len(rows), 1)

    def test_handoff_lists_legacy_and_newest(self):
        records = [
            tm.parse_record_entry(_record(LEGACY_ID, created_at="2026-01-01T00:00:00Z")),
            tm.parse_record_entry(_record(CURRENT_ID, created_at="2026-09-08T00:00:00Z")),
        ]
        groups = tm.group_lineages(records)
        text = tm.format_handoff(
            tm.anatomy_card(tm.QueryIntent(query=LEGACY_ID), groups),
            groups[0],
            target_language="zh-CN",
            source_text=SOURCE,
        )
        self.assertIn("app.visualIVR.EMPTY_TEXT", text)
        self.assertIn(CURRENT_ID, text)
        self.assertIn(LEGACY_ID, text)
        self.assertIn("Blob-based Fix", text)

    def test_default_ice_match_uses_post_fn(self):
        captured = {}

        def post_fn(url, payload, timeout):
            captured["url"] = url
            captured["payload"] = payload
            return [{
                "opusID": payload["items"][0]["opusID"],
                "stringValue": payload["items"][0]["stringValue"],
                "translationMemoryMatchingResult": {
                    "type": "Exact Match",
                    "translations": {"zh-CN": "暂无内容"},
                },
            }]

        items = [{"opusID": CURRENT_ID, "stringValue": SOURCE}]
        out = tm.default_ice_match(
            items, ["zh-CN"], host="http://ice.test", post_fn=post_fn
        )
        self.assertIn("/api/v1/translation-memory/match", captured["url"])
        self.assertEqual(captured["payload"]["languages"], ["zh-CN"])
        self.assertEqual(out[0]["translationMemoryMatchingResult"]["type"], "Exact Match")


if __name__ == "__main__":
    unittest.main()
