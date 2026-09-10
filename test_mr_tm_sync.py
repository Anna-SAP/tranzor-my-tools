"""Unit tests for mr_tm_sync — no network, no tkinter.

Run:  python -m unittest test_mr_tm_sync
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mr_tm_sync as sync


UNS_DE = (
    "uns-app/newTemplateStorage/billingStatement/"
    "billingStatement__email_html__3610__de_DE.hbs"
)
UNS_EN = (
    "uns-app/newTemplateStorage/billingStatement/"
    "billingStatement__email_html__3610__en_US.hbs"
)
TS_GB = "src/visualIVR/src/lang/visualIVR/index-en_GB.ts"
TS_US = "src/visualIVR/src/lang/visualIVR/index-en_US.ts"


class TestParseMrUrl(unittest.TestCase):
    def test_standard_url(self):
        self.assertEqual(
            sync.parse_mr_url(
                "https://git.ringcentral.com/common/uns/-/merge_requests/4184"),
            ("common/uns", 4184),
        )

    def test_url_with_trailing_path(self):
        self.assertEqual(
            sync.parse_mr_url(
                "https://git.ringcentral.com/web/web/-/merge_requests/41985/diffs"),
            ("web/web", 41985),
        )

    def test_rejects_non_mr(self):
        with self.assertRaises(ValueError):
            sync.parse_mr_url("https://git.ringcentral.com/common/uns")


class TestLocalePaths(unittest.TestCase):
    def test_hbs_locale_and_source_path(self):
        loc, raw = sync.locale_from_filename(UNS_DE)
        self.assertEqual(loc, "de-DE")
        self.assertEqual(raw, "de_DE")
        self.assertEqual(sync.source_path_for(UNS_DE), UNS_EN)

    def test_ts_index_locale(self):
        loc, raw = sync.locale_from_filename(TS_GB)
        self.assertEqual(loc, "en-GB")
        self.assertEqual(raw, "en_GB")
        self.assertEqual(sync.source_path_for(TS_GB), TS_US)

    def test_es_419_wins_over_es(self):
        path = "src/lang/index-es_419.ts"
        loc, raw = sync.locale_from_filename(path)
        self.assertEqual(loc, "es-419")
        self.assertEqual(raw, "es_419")

    def test_source_locale_detection(self):
        self.assertTrue(sync.is_source_locale_path(UNS_EN))
        self.assertFalse(sync.is_source_locale_path(UNS_DE))


class TestParsers(unittest.TestCase):
    def test_ts_export_default_flat_and_nested(self):
        text = """
import type { Messages } from './types';
export default {
  EMPTY_TEXT: 'Please enter Ext.{extension}',
  INVALID_TEXT: "Invalid ext.",
  NESTED: {
    A: 'inner',
  },
};
"""
        parsed = sync.parse_ts_locale(text)
        self.assertEqual(parsed["EMPTY_TEXT"], "Please enter Ext.{extension}")
        self.assertEqual(parsed["INVALID_TEXT"], "Invalid ext.")
        self.assertEqual(parsed["NESTED.A"], "inner")

    def test_ts_escapes(self):
        parsed = sync.parse_ts_locale(
            "export default { K: 'line\\nnext' };")
        self.assertEqual(parsed["K"], "line\nnext")

    def test_json_flat(self):
        parsed = sync.parse_json_locale('{"OK":"确定","CANCEL":"取消"}')
        self.assertEqual(parsed["OK"], "确定")

    def test_unified_diff_paths(self):
        diff = """
diff --git a/foo/index-en_GB.ts b/foo/index-en_GB.ts
--- a/foo/index-en_GB.ts
+++ b/foo/index-en_GB.ts
@@ -1 +1 @@
-old
+new
diff --git a/uns-app/newTemplateStorage/a/a__email_html__1210__de_DE.hbs b/uns-app/newTemplateStorage/a/a__email_html__1210__de_DE.hbs
+++ b/uns-app/newTemplateStorage/a/a__email_html__1210__de_DE.hbs
"""
        paths = sync.parse_unified_diff_paths(diff)
        self.assertEqual(
            paths,
            [
                "foo/index-en_GB.ts",
                "uns-app/newTemplateStorage/a/a__email_html__1210__de_DE.hbs",
            ],
        )


class TestExtractPairs(unittest.TestCase):
    def test_hbs_whole_file_is_one_pair(self):
        pairs, skipped = sync.extract_pairs_for_file(
            project_id="common/uns",
            target_path=UNS_DE,
            old_target="{{{$Brand_ShortName}}}Supportwebsite",
            new_target="{{{$Brand_ShortName}}} Support-Website",
            source_text_by_key={"__file__": "{{{$Brand_ShortName}}} Support site"},
            source_path=UNS_EN,
        )
        self.assertEqual(skipped, [])
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertEqual(pair.format, "hbs")
        self.assertEqual(pair.target_language, "de-DE")
        self.assertEqual(
            pair.logical_key,
            "common.uns.new.billingStatement__email_html__3610",
        )
        self.assertEqual(pair.string_key, "billingStatement__email_html__3610")
        self.assertEqual(pair.new_target_text, "{{{$Brand_ShortName}}} Support-Website")
        self.assertTrue(pair.reconstructed_opus_id.startswith("RingCentral.uns."))

    def test_ts_only_changed_keys(self):
        old = "export default { KEEP: 'ok', EMPTY_TEXT: 'enter ext.{extension}' };"
        new = "export default { KEEP: 'ok', EMPTY_TEXT: 'enter Ext.{extension}' };"
        source = "export default { KEEP: 'ok', EMPTY_TEXT: 'enter Ext.{extension}' };"
        pairs, skipped = sync.extract_pairs_for_file(
            project_id="web/web",
            target_path=TS_GB,
            old_target=old,
            new_target=new,
            source_text_by_key=sync.parse_ts_locale(source),
            source_path=TS_US,
        )
        self.assertEqual(skipped, [])
        self.assertEqual([p.string_key for p in pairs], ["EMPTY_TEXT"])
        self.assertEqual(pairs[0].target_language, "en-GB")
        self.assertEqual(pairs[0].new_target_text, "enter Ext.{extension}")
        self.assertEqual(pairs[0].source_text, "enter Ext.{extension}")

    def test_unchanged_file_yields_no_pairs(self):
        text = "export default { A: 'x' };"
        pairs, skipped = sync.extract_pairs_for_file(
            project_id="web/web",
            target_path=TS_GB,
            old_target=text,
            new_target=text,
            source_text_by_key={"A": "x"},
            source_path=TS_US,
        )
        self.assertEqual(pairs, [])
        self.assertEqual(skipped, [])


class _BlobStore:
    def __init__(self, blobs):
        self.blobs = blobs
        self.calls = []

    def __call__(self, project_id, path, ref):
        self.calls.append((project_id, path, ref))
        return self.blobs.get((path, ref))


class TestExtractPlan(unittest.TestCase):
    def test_skips_source_locale_and_non_locale(self):
        blobs = _BlobStore({
            (UNS_DE, "HEAD"): "{{{$Brand_ShortName}}} Support-Website",
            (UNS_DE, "BASE"): "{{{$Brand_ShortName}}}Supportwebsite",
            (UNS_EN, "HEAD"): "{{{$Brand_ShortName}}} Support site",
        })
        plan = sync.extract_plan_from_changes(
            project_id="common/uns",
            changes=[
                {"new_path": UNS_DE, "old_path": UNS_DE},
                {"new_path": UNS_EN, "old_path": UNS_EN},
                {"new_path": "README.md", "old_path": "README.md"},
            ],
            new_ref="HEAD",
            old_ref="BASE",
            get_file_raw=blobs,
            target_branch="26-4_L10n",
            source_branch="fix/LOC-24849",
        )
        self.assertEqual(len(plan.changed_pairs()), 1)
        reasons = {row["path"]: row["reason"] for row in plan.skipped}
        self.assertEqual(reasons[UNS_EN], "source-locale file")
        self.assertEqual(reasons["README.md"], "not a locale file")
        self.assertEqual(plan.target_branch, "26-4_L10n")

    def test_plan_roundtrip_json(self):
        blobs = _BlobStore({
            (TS_GB, "h"): "export default { A: 'Ext.' };",
            (TS_GB, "b"): "export default { A: 'ext.' };",
            (TS_US, "h"): "export default { A: 'Ext.' };",
        })
        plan = sync.extract_plan_from_changes(
            project_id="web/web",
            changes=[{"new_path": TS_GB, "old_path": TS_GB}],
            new_ref="h",
            old_ref="b",
            get_file_raw=blobs,
            mr_iid=41985,
            mr_state="merged",
        )
        restored = sync.plan_from_dict(sync.plan_to_dict(plan))
        self.assertEqual(restored.mr_iid, 41985)
        self.assertEqual(restored.changed_pairs()[0].string_key, "A")
        tmx = sync.plan_to_tmx(plan)
        self.assertIn('xml:lang="en-GB"', tmx)
        self.assertIn("Ext.", tmx)

    def test_diff_text_extract_uses_injected_blobs(self):
        blobs = _BlobStore({
            (TS_GB, "HEAD"): "export default { A: 'Ext.' };",
            (TS_GB, "HEAD^"): "export default { A: 'ext.' };",
            (TS_US, "HEAD"): "export default { A: 'Ext.' };",
        })
        diff = f"diff --git a/{TS_GB} b/{TS_GB}\n+++ b/{TS_GB}\n"
        plan = sync.extract_plan_from_diff_text(
            project_id="web/web",
            diff_text=diff,
            get_file_raw=blobs,
            new_ref="HEAD",
            old_ref="HEAD^",
        )
        self.assertEqual(plan.source_kind, "diff")
        self.assertEqual(plan.changed_pairs()[0].new_target_text, "Ext.")


class TestApplyGrouping(unittest.TestCase):
    def test_groups_locales_of_the_same_source_file(self):
        plan = sync.SyncPlan(
            source_kind="mr",
            project_id="common/uns",
            target_branch="26-4_L10n",
            gitlab_base="https://git.ringcentral.com",
            pairs=[
                sync.TranslationPair(
                    project_id="common/uns",
                    format="hbs",
                    target_path=UNS_DE,
                    source_path=UNS_EN,
                    logical_key="common.uns.new.billingStatement__email_html__3610",
                    string_key="billingStatement__email_html__3610",
                    target_language="de-DE",
                    source_text="en",
                    old_target_text="old-de",
                    new_target_text="new-de",
                ),
                sync.TranslationPair(
                    project_id="common/uns",
                    format="hbs",
                    target_path=UNS_DE.replace("de_DE", "fr_FR"),
                    source_path=UNS_EN,
                    logical_key="common.uns.new.billingStatement__email_html__3610",
                    string_key="billingStatement__email_html__3610",
                    target_language="fr-FR",
                    source_text="en",
                    old_target_text="old-fr",
                    new_target_text="new-fr",
                ),
            ],
        )
        batches = sync.group_apply_batches(plan)
        self.assertEqual(len(batches), 1)
        batch = batches[0]
        self.assertEqual(batch["target_languages"], ["de-DE", "fr-FR"])
        self.assertIn("/-/blob/26-4_L10n/", batch["git_blob_url"])
        self.assertTrue(batch["git_blob_url"].endswith(UNS_EN))

    def test_apply_dry_run_does_not_post(self):
        plan = sync.SyncPlan(
            source_kind="mr",
            project_id="web/web",
            target_branch="master",
            pairs=[
                sync.TranslationPair(
                    project_id="web/web",
                    format="ts",
                    target_path=TS_GB,
                    source_path=TS_US,
                    logical_key="EMPTY_TEXT",
                    string_key="EMPTY_TEXT",
                    target_language="en-GB",
                    source_text="Ext.",
                    old_target_text="ext.",
                    new_target_text="Ext.",
                ),
            ],
        )
        posted = []

        def post_fn(url, payload, timeout):
            posted.append(url)
            return 200, {}

        results = sync.apply_plan(
            plan, bug_id="LOC-25246", token="t",
            post_fn=post_fn, dry_run=True,
        )
        self.assertEqual(posted, [])
        self.assertEqual(results[0].status, "dry_run")

    def test_apply_posts_lookup_then_apply(self):
        plan = sync.SyncPlan(
            source_kind="mr",
            project_id="web/web",
            target_branch="master",
            pairs=[
                sync.TranslationPair(
                    project_id="web/web",
                    format="ts",
                    target_path=TS_GB,
                    source_path=TS_US,
                    logical_key="EMPTY_TEXT",
                    string_key="EMPTY_TEXT",
                    target_language="en-GB",
                    source_text="Ext.",
                    old_target_text="ext.",
                    new_target_text="Ext.",
                ),
            ],
        )
        calls = []

        def post_fn(url, payload, timeout):
            calls.append((url, payload))
            if url.endswith("/lookup"):
                return 200, {"lookup_token": "tok-1"}
            return 202, {"status": "queued", "submission_id": "sub-9",
                         "mr_url": None}

        results = sync.apply_plan(
            plan, bug_id="LOC-25246", token="t",
            post_fn=post_fn, dry_run=False,
        )
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0][0].endswith("/blob-based-fix/lookup"))
        self.assertEqual(calls[0][1]["string_keys"], ["EMPTY_TEXT"])
        self.assertTrue(calls[1][0].endswith("/blob-based-fix/apply"))
        self.assertEqual(calls[1][1]["bug_id"], "LOC-25246")
        self.assertEqual(calls[1][1]["lookup_token"], "tok-1")
        self.assertEqual(results[0].status, "queued")
        self.assertEqual(results[0].submission_id, "sub-9")


class TestIceProbe(unittest.TestCase):
    def test_stale_when_ice_returns_old_target(self):
        pair = sync.TranslationPair(
            project_id="web/web",
            format="ts",
            target_path=TS_GB,
            source_path=TS_US,
            logical_key="EMPTY_TEXT",
            string_key="EMPTY_TEXT",
            target_language="en-GB",
            source_text="Please enter Ext.{extension}",
            old_target_text="enter ext.",
            new_target_text="enter Ext.",
        )

        def post_fn(url, payload, timeout):
            self.assertIn("/translation-memory/match", url)
            return [{
                "opusID": payload["items"][0]["opusID"],
                "stringValue": pair.source_text,
                "translationMemoryMatchingResult": {
                    "type": "Exact Match",
                    "translations": {"en-GB": "enter ext."},
                },
            }]

        hits = sync.probe_ice([pair], post_fn=post_fn)
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].stale)
        self.assertEqual(hits[0].ice_target, "enter ext.")


class TestGuiStrings(unittest.TestCase):
    def test_tab_copy_exists_in_both_languages(self):
        import gui_tab_mr_tm_sync as gui
        for lang in ("en", "zh"):
            self.assertIn("tab_mr_tm_sync", gui.STRINGS[lang])
            self.assertIn("mts_apply", gui.STRINGS[lang])
            self.assertIn("mts_confirm", gui.STRINGS[lang])


class TestCliGuardrails(unittest.TestCase):
    def test_apply_without_yes_is_rejected(self):
        import tempfile
        plan = {
            "source_kind": "mr",
            "project_id": "web/web",
            "target_branch": "master",
            "pairs": [],
        }
        with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(plan, fh)
            path = fh.name
        try:
            code = sync.main(["--from-json", path, "--apply", "--bug-id", "LOC-1"])
        finally:
            os.remove(path)
        self.assertEqual(code, 3)

    def test_help_without_source(self):
        code = sync.main([])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
