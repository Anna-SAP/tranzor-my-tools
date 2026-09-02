"""Tests for the Key Origin pure-logic layer (:mod:`key_origin`).

Covers the things the GUI relies on but can't exercise headlessly:

- ``parse_lookup_keys`` — JIRA wiki chrome, ``:::seg:::`` stripping,
  UNS template-path conversion, de-dup, cap.
- ``uns_path_to_key`` / ``split_pipeline_key`` — the identity transform
  that makes UNS email keys findable in ``translations.opus_id``.
- ``collapse_entries_to_tasks`` / ``pick_recommended`` — newest MR wins.
- ``locate_keys`` — exact-then-fuzzy, File fallback, grouping, report.
- ``tranzor_origin_url`` — MR deep-link vs File Translation URL.

Run:  python -m unittest test_key_origin
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import key_origin as ko  # noqa: E402


LOC_25228_COMMENT = """
{code:java}
//NEW
uns-app/newTemplateStorage/announcementsOnlyLoginInfo/announcementsOnlyLoginInfo__email_html__7710__de_DE.hbs
uns-app/newTemplateStorage/callQueueManagerLoginInfo/callQueueManagerLoginInfo__email_html__7710__de_DE.hbs
uns-app/templateStorage/announcementsOnlyLoginInfo/announcementsOnlyLoginInfo__email_html__7710__de_DE.hbs
{code}

common.uns.announcementsOnlyLoginInfo__email_html__7710:::seg:::10
common.uns.new.announcementsOnlyLoginInfo__email_html__7710:::seg:::10
common.uns.sharedLineGroupLoginInfo__email_html__7710:::seg:::10
"""


class SplitPipelineKeyTests(unittest.TestCase):

    def test_strips_seg_uid(self):
        opus, uid = ko.split_pipeline_key(
            "common.uns.foo__email_html__7710:::seg:::10")
        self.assertEqual(opus, "common.uns.foo__email_html__7710")
        self.assertEqual(uid, 10)

    def test_plain_key_unchanged(self):
        opus, uid = ko.split_pipeline_key("common.uns.foo")
        self.assertEqual(opus, "common.uns.foo")
        self.assertIsNone(uid)

    def test_rejects_non_positive_uid(self):
        with self.assertRaises(ValueError):
            ko.split_pipeline_key("common.uns.foo:::seg:::0")
        with self.assertRaises(ValueError):
            ko.split_pipeline_key("common.uns.foo:::seg:::abc")


class UnsPathToKeyTests(unittest.TestCase):

    def test_new_storage_strips_locale(self):
        path = ("uns-app/newTemplateStorage/announcementsOnlyLoginInfo/"
                "announcementsOnlyLoginInfo__email_html__7710__de_DE.hbs")
        self.assertEqual(
            ko.uns_path_to_key(path),
            "common.uns.new.announcementsOnlyLoginInfo__email_html__7710")

    def test_legacy_storage(self):
        path = ("uns-app/templateStorage/messagesOnlyLoginInfo/"
                "messagesOnlyLoginInfo__email_html__7710__de_DE.hbs")
        self.assertEqual(
            ko.uns_path_to_key(path),
            "common.uns.messagesOnlyLoginInfo__email_html__7710")

    def test_gitlab_prefixed_path(self):
        path = ("uns/uns-app/templateStorage/foo/"
                "foo__email_html__1210__fr_FR.hbs")
        self.assertEqual(
            ko.uns_path_to_key(path),
            "common.uns.foo__email_html__1210")

    def test_non_uns_path_is_none(self):
        self.assertIsNone(ko.uns_path_to_key("src/locales/en-US/common.json"))


class ParseLookupKeysTests(unittest.TestCase):

    def test_loc_25228_mix_of_paths_and_seg_keys(self):
        queries = ko.parse_lookup_keys(LOC_25228_COMMENT)
        ids = [q.search_opus_id for q in queries]
        self.assertIn(
            "common.uns.new.announcementsOnlyLoginInfo__email_html__7710", ids)
        self.assertIn(
            "common.uns.announcementsOnlyLoginInfo__email_html__7710", ids)
        self.assertIn(
            "common.uns.new.callQueueManagerLoginInfo__email_html__7710", ids)
        self.assertIn(
            "common.uns.sharedLineGroupLoginInfo__email_html__7710", ids)
        # Path + segmented key for the same base id collapse to one query.
        self.assertEqual(
            ids.count(
                "common.uns.new.announcementsOnlyLoginInfo__email_html__7710"),
            1)
        self.assertEqual(
            ids.count(
                "common.uns.announcementsOnlyLoginInfo__email_html__7710"),
            1)
        seg = next(
            q for q in queries
            if q.search_opus_id.endswith("sharedLineGroupLoginInfo__email_html__7710")
            and q.kind == "key")
        self.assertEqual(seg.seg_uid, 10)

    def test_skips_blank_and_wiki_chrome(self):
        queries = ko.parse_lookup_keys("\n\n# comment\nh3. Title\n\n")
        self.assertEqual(queries, [])

    def test_caps_at_max_keys(self):
        raw = "\n".join(f"key.{i}" for i in range(20))
        queries = ko.parse_lookup_keys(raw, max_keys=5)
        self.assertEqual(len(queries), 5)


def _entry(opus, task_id, *, mr=4100, project="common/uns",
           created="2026-08-31T11:37:24Z", lang="de-DE",
           source_type="mr", **extra):
    row = {
        "opus_id": opus,
        "task_id": task_id,
        "task_name": f"{project} MR#{mr}" if source_type == "mr" else "file-job",
        "project_id": project if source_type == "mr" else None,
        "mr_iid": mr if source_type == "mr" else None,
        "created_at": created,
        "target_language": lang,
        "source_type": source_type,
        "source_text": extra.get("source_text", "Thanks for using"),
    }
    row.update(extra)
    return row


class CollapseAndRecommendTests(unittest.TestCase):

    def test_collapses_langs_and_sorts_newest_first(self):
        entries = [
            _entry("k", "old", created="2026-01-01T00:00:00Z", lang="de-DE"),
            _entry("k", "old", created="2026-01-01T00:00:00Z", lang="fr-FR"),
            _entry("k", "new", created="2026-08-31T11:37:24Z", lang="de-DE"),
        ]
        tasks = ko.collapse_entries_to_tasks(entries)
        self.assertEqual([t.task_id for t in tasks], ["new", "old"])
        old = next(t for t in tasks if t.task_id == "old")
        self.assertEqual(old.langs, ["de-DE", "fr-FR"])
        self.assertEqual(old.row_count, 2)

    def test_recommended_prefers_newest_mr(self):
        tasks = ko.collapse_entries_to_tasks([
            _entry("k", "file1", source_type="file", mr=None,
                   created="2026-09-01T00:00:00Z", project=""),
            _entry("k", "mr-old", created="2026-01-01T00:00:00Z"),
            _entry("k", "mr-new", created="2026-08-31T00:00:00Z"),
        ])
        rec = ko.pick_recommended(tasks)
        self.assertEqual(rec.task_id, "mr-new")

    def test_recommended_falls_back_to_file(self):
        tasks = ko.collapse_entries_to_tasks([
            _entry("k", "file1", source_type="file", mr=None,
                   created="2026-09-01T00:00:00Z", project=""),
        ])
        rec = ko.pick_recommended(tasks)
        self.assertEqual(rec.task_id, "file1")

    def test_recommended_prefers_scan_over_file(self):
        tasks = ko.collapse_entries_to_tasks([
            _entry("k", "file1", source_type="file", mr=None,
                   created="2026-09-01T00:00:00Z", project=""),
            _entry("k", "scan1", source_type="scan", mr=None,
                   created="2026-08-01T00:00:00Z", project="common/uns"),
        ])
        rec = ko.pick_recommended(tasks)
        self.assertEqual(rec.task_id, "scan1")


class UrlTests(unittest.TestCase):

    def test_mr_deep_link(self):
        url = ko.tranzor_origin_url(
            base_url="http://tranzor-platform.int.rclabenv.com",
            source_type="mr",
            project_id="common/uns",
            mr_iid=4100,
            task_id="bd6bba88-870a-46de-8708-6c8a219f6c6e",
        )
        self.assertEqual(
            url,
            "http://tranzor-platform.int.rclabenv.com/static/"
            "?project_id=common%2Funs&mr_id=4100")

    def test_file_task_link(self):
        url = ko.tranzor_origin_url(
            base_url="http://tranzor-platform.int.rclabenv.com",
            source_type="file",
            task_id="265",
        )
        self.assertEqual(
            url,
            "http://tranzor-platform.int.rclabenv.com/static/legacy/tasks/265")

    def test_scan_task_link(self):
        url = ko.tranzor_origin_url(
            base_url="http://tranzor-platform.int.rclabenv.com",
            source_type="scan",
            project_id="common/uns",
            task_id="scan-abc-123",
        )
        self.assertEqual(
            url,
            "http://tranzor-platform.int.rclabenv.com/static/scans/scan-abc-123")


class LocateKeysTests(unittest.TestCase):

    def test_exact_then_groups_loc_25228_shape(self):
        calls = []

        def search_fn(**kwargs):
            calls.append(kwargs)
            opus = kwargs["opus_id"]
            if kwargs["match_mode"] != "exact":
                return {"total": 0, "entries": []}
            if "announcementsOnlyLoginInfo" not in opus:
                return {"total": 0, "entries": []}
            return {
                "total": 2,
                "entries": [
                    _entry(opus, "bd6bba88-870a-46de-8708-6c8a219f6c6e",
                           lang="de-DE"),
                    _entry(opus, "bd6bba88-870a-46de-8708-6c8a219f6c6e",
                           lang="fr-FR"),
                ],
            }

        raw = (
            "common.uns.announcementsOnlyLoginInfo__email_html__7710:::seg:::10\n"
            "common.uns.new.announcementsOnlyLoginInfo__email_html__7710:::seg:::10\n"
            "common.uns.doesNotExist__email_html__7710:::seg:::10\n"
        )
        payload = ko.locate_keys(
            raw, search_fn,
            base_url="http://tranzor-platform.int.rclabenv.com")
        self.assertEqual(payload["found"], 2)
        self.assertEqual(payload["missing"], 1)
        groups = payload["groups"]
        found = [g for g in groups if not g.get("missing")]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["mr_iid"], 4100)
        self.assertEqual(found[0]["project_id"], "common/uns")
        self.assertEqual(found[0]["hit_count"], 2)
        self.assertIn("mr_id=4100", found[0]["url"])
        self.assertIn("project_id=common%2Funs", found[0]["url"])
        # Exact is tried first — no fuzzy call once exact hits.
        exact_hits = [c for c in calls
                      if c["match_mode"] == "exact" and "doesNotExist" not in c["opus_id"]]
        self.assertTrue(exact_hits)
        report = ko.format_origin_report(payload)
        self.assertIn("MISSING", report)
        self.assertIn("common/uns MR#4100", report)

    def test_fuzzy_used_when_exact_empty(self):
        def search_fn(**kwargs):
            if kwargs["match_mode"] == "exact":
                return {"total": 0, "entries": []}
            return {
                "total": 1,
                "entries": [_entry("common.uns.foo", "t-fuzzy")],
            }

        payload = ko.locate_keys("common.uns.foo", search_fn)
        rec = payload["results"][0]["recommended"]
        self.assertEqual(rec.task_id, "t-fuzzy")
        self.assertEqual(payload["results"][0]["match_mode"], "fuzzy")

    def test_all_falls_back_to_scan(self):
        def search_fn(**kwargs):
            if kwargs.get("source_type") == "scan":
                return {
                    "total": 1,
                    "entries": [_entry(
                        "k", "scan-1", source_type="scan", mr=None,
                        project="common/uns")],
                }
            return {"total": 0, "entries": []}

        payload = ko.locate_keys(
            "k", search_fn, source_type="all", fallback_file=False)
        rec = payload["results"][0]["recommended"]
        self.assertEqual(rec.task_id, "scan-1")
        self.assertEqual(payload["results"][0]["source_type_used"], "scan")
        self.assertIn("/static/scans/scan-1", payload["groups"][0]["url"])

    def test_scan_channel_skips_mr_and_file(self):
        calls = []

        def search_fn(**kwargs):
            calls.append(kwargs.get("source_type"))
            if kwargs.get("source_type") == "scan":
                return {
                    "total": 1,
                    "entries": [_entry(
                        "k", "scan-9", source_type="scan", mr=None,
                        project="common/uns")],
                }
            return {"total": 1, "entries": [_entry("k", "mr-should-not-win")]}

        payload = ko.locate_keys("k", search_fn, source_type="scan")
        self.assertEqual(calls, ["scan"])
        self.assertEqual(payload["results"][0]["recommended"].task_id, "scan-9")

    def test_file_fallback_after_mr_miss(self):
        def search_fn(**kwargs):
            if kwargs.get("source_type") == "file":
                return {
                    "total": 1,
                    "entries": [_entry(
                        "RingCentral.uns.hash.welcome",
                        "legacy-9", source_type="file", mr=None, project="")],
                }
            return {"total": 0, "entries": []}

        payload = ko.locate_keys(
            "RingCentral.uns.hash.welcome", search_fn, source_type="mr")
        rec = payload["results"][0]["recommended"]
        self.assertEqual(rec.task_id, "legacy-9")
        self.assertEqual(payload["results"][0]["source_type_used"], "file")

    def test_paginates_until_total(self):
        def search_fn(**kwargs):
            offset = kwargs["offset"]
            if offset == 0:
                return {
                    "total": 3,
                    "entries": [
                        _entry("k", "t1", lang="de-DE"),
                        _entry("k", "t1", lang="fr-FR"),
                    ],
                }
            return {
                "total": 3,
                "entries": [_entry("k", "t1", lang="ja-JP")],
            }

        payload = ko.locate_keys("k", search_fn, page_size=2, max_pages=5)
        rec = payload["results"][0]["recommended"]
        self.assertEqual(sorted(rec.langs), ["de-DE", "fr-FR", "ja-JP"])
        self.assertEqual(rec.row_count, 3)

    def test_search_error_is_per_key_not_fatal(self):
        def search_fn(**kwargs):
            raise RuntimeError("boom")

        payload = ko.locate_keys("common.uns.foo", search_fn)
        self.assertEqual(payload["found"], 0)
        self.assertIn("boom", payload["results"][0]["error"] or "")


class SearchTranslationsClientTests(unittest.TestCase):
    """``export_mr_pipeline.search_translations`` builds the expected query."""

    def test_params_and_path(self):
        import export_mr_pipeline as mr_api

        captured = {}

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"total": 0, "entries": []}

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params")
            return _Resp()

        with mock.patch.object(mr_api, "_api_get", fake_get):
            mr_api.search_translations(
                opus_id="common.uns.foo",
                match_mode="exact",
                source_type="mr",
                limit=200,
                offset=0,
            )
        self.assertTrue(captured["url"].endswith("/translations/search"))
        self.assertEqual(captured["params"]["opus_id"], "common.uns.foo")
        self.assertEqual(captured["params"]["match_mode"], "exact")
        self.assertEqual(captured["params"]["source_type"], "mr")
        self.assertEqual(captured["params"]["limit"], 200)

    def test_scan_source_type_does_not_hit_translations_search(self):
        import export_mr_pipeline as mr_api

        def boom(*_a, **_k):
            raise AssertionError("translations/search must not be called")

        with mock.patch.object(mr_api, "_api_get", boom), \
             mock.patch.object(mr_api, "search_scan_translations",
                               return_value={"total": 0, "entries": []}) as scan:
            out = mr_api.search_translations(
                opus_id="RingCentral.uns.hash.key",
                source_type="scan")
        scan.assert_called_once()
        self.assertEqual(out["total"], 0)


class OpusIdMatchTests(unittest.TestCase):

    def test_exact_and_seg_suffix(self):
        self.assertTrue(ko.opus_id_matches("common.uns.foo", "common.uns.foo"))
        self.assertTrue(ko.opus_id_matches(
            "common.uns.foo:::seg:::10", "common.uns.foo"))
        self.assertFalse(ko.opus_id_matches("common.uns.foo", "common.uns.bar"))

    def test_fuzzy_substring(self):
        self.assertTrue(ko.opus_id_matches(
            "RingCentral.uns.hash.sharedLineGroupLoginInfo__email_subject__7710",
            "sharedLineGroupLoginInfo", "fuzzy"))


class SearchScanTranslationsTests(unittest.TestCase):

    def test_live_fanout_filters_exact_and_stamps_scan(self):
        import export_mr_pipeline as mr_api

        tasks = [
            {"task_id": "scan-a", "project_id": "common/uns",
             "task_name": "uns scan", "created_at": "2026-09-01T00:00:00Z"},
        ]

        def page(task_id, limit=200, offset=0, base_url=None, search=None):
            self.assertEqual(search, "common.uns.foo")
            return {"total": 2, "translations": [
                {"opus_id": "common.uns.foo", "target_language": "de-DE",
                 "source_text": "Hello"},
                {"opus_id": "common.uns.foobar", "target_language": "de-DE",
                 "source_text": "noise"},
            ]}

        with mock.patch.object(mr_api, "_scan_index_entries", return_value=[]), \
             mock.patch.object(mr_api, "_list_recent_scan_tasks",
                               return_value=tasks), \
             mock.patch.object(mr_api, "fetch_scan_results_page", side_effect=page):
            out = mr_api.search_scan_translations(
                opus_id="common.uns.foo", match_mode="exact")
        self.assertEqual(out["total"], 1)
        row = out["entries"][0]
        self.assertEqual(row["task_id"], "scan-a")
        self.assertEqual(row["source_type"], "scan")
        self.assertEqual(row["opus_id"], "common.uns.foo")

    def test_index_hit_skips_live_fanout(self):
        import export_mr_pipeline as mr_api

        indexed = [{
            "opus_id": "common.uns.foo",
            "task_id": "scan-idx",
            "task_name": "Scan common/uns",
            "project_id": "common/uns",
            "source_type": "scan",
            "created_at": "2026-08-01T00:00:00Z",
            "target_language": "de-DE",
            "source_text": "Hi",
            "mr_iid": None,
        }]
        with mock.patch.object(mr_api, "_scan_index_entries",
                               return_value=indexed), \
             mock.patch.object(mr_api, "_list_recent_scan_tasks") as live:
            out = mr_api.search_scan_translations(opus_id="common.uns.foo")
        live.assert_not_called()
        self.assertEqual(out["entries"][0]["task_id"], "scan-idx")


if __name__ == "__main__":
    unittest.main()
