"""Unit tests for the MR-source-branch Language Lead fix-commit scan.

This covers the P2 fix for the Changes report missing post-edits whose
``fixed_by_lead`` is NULL in Tranzor's DB (audit-trail bug / BATCH_FIX path)
but which DO exist as commits on the MR source branch. The headline case is
UNS: an entire ``.hbs`` template is one translation unit, so the key never
appears inside the file content — the legacy ``extract_diff_values`` resolver
can't help and we map ``path -> (opus_id, lang)`` + fetch full blobs instead.

Run:  python -m unittest test_mr_fix_commit_scan
"""
from __future__ import annotations

import os
import sys
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gitlab_client


LEAD = gitlab_client.LEAD_FIX_AUTHOR_EMAIL
UNS_FR_CA = "uns-app/templateStorage/local_test/local_test__email_html__3710__fr_CA.hbs"
UNS_KEY = ("common.uns.local_test__email_html__3710", "fr-CA")


# ---------------------------------------------------------------------------
# Test double — in-memory GitLab client with the three methods the scan needs.
# ---------------------------------------------------------------------------
class FakeClient:
    def __init__(self, commits=None, diffs=None, blobs=None,
                 list_error=None):
        self._commits = commits or []
        self._diffs = diffs or {}          # sha -> diff list
        self._blobs = blobs or {}          # (path, ref) -> text
        self._list_error = list_error      # Exception to raise from list_commits
        self.blob_calls = []

    def list_commits(self, project_id, *, ref_name=None, since=None,
                     until=None, author=None, per_page=100, max_pages=5):
        if self._list_error is not None:
            raise self._list_error
        # Mirror the server-side author substring filter so tests that pass a
        # non-Tranzor author still behave.
        if author:
            return [c for c in self._commits
                    if author in (c.get("author_email") or "")]
        return list(self._commits)

    def get_commit_diff(self, project_id, sha):
        return self._diffs.get(sha, [])

    def get_file_raw(self, project_id, path, ref):
        self.blob_calls.append((path, ref))
        return self._blobs.get((path, ref))


def commit(sha, *, title, author=LEAD, when="2026-06-08T10:00:00Z",
           parents=("addsha",)):
    return {
        "id": sha,
        "author_email": author,
        "title": title,
        "committed_date": when,
        "parent_ids": list(parents),
    }


def file_diff(path, lines=None):
    return {"new_path": path, "old_path": path, "diff": "\n".join(lines or [])}


# ---------------------------------------------------------------------------
# parse_uns_template_path
# ---------------------------------------------------------------------------
class TestParseUnsTemplatePath(unittest.TestCase):
    def test_template_storage_target_locale(self):
        self.assertEqual(
            gitlab_client.parse_uns_template_path(UNS_FR_CA),
            ("common.uns.local_test__email_html__3710", "fr-CA"),
        )

    def test_source_locale_en_us(self):
        path = ("uns-app/templateStorage/local_test/"
                "local_test__email_html__3710__en_US.hbs")
        self.assertEqual(
            gitlab_client.parse_uns_template_path(path),
            ("common.uns.local_test__email_html__3710", "en-US"),
        )

    def test_new_template_storage_namespace(self):
        path = ("uns-app/newTemplateStorage/accountActivation/"
                "accountActivation__email_html__1210__de_DE.hbs")
        self.assertEqual(
            gitlab_client.parse_uns_template_path(path),
            ("common.uns.new.accountActivation__email_html__1210", "de-DE"),
        )

    def test_partials_namespace_suffix(self):
        path = ("uns-app/templateStorage/_partials/footer/"
                "footer__email_html__3710__fr_FR.hbs")
        self.assertEqual(
            gitlab_client.parse_uns_template_path(path),
            ("common.uns.partials.footer__email_html__3710", "fr-FR"),
        )

    def test_non_uns_path_returns_none(self):
        self.assertIsNone(
            gitlab_client.parse_uns_template_path("locale/fr-CA/messages.json"))

    def test_wrong_segment_count_returns_none(self):
        # filename body must be exactly type__brand__locale (3 segments)
        path = ("uns-app/templateStorage/local_test/"
                "local_test__email_html__fr_CA.hbs")
        self.assertIsNone(gitlab_client.parse_uns_template_path(path))

    def test_empty_and_none(self):
        self.assertIsNone(gitlab_client.parse_uns_template_path(""))
        self.assertIsNone(gitlab_client.parse_uns_template_path(None))


# ---------------------------------------------------------------------------
# is_lead_fix_commit / _operator_from_fix_title
# ---------------------------------------------------------------------------
class TestIsLeadFixCommit(unittest.TestCase):
    def test_single_fix(self):
        self.assertTrue(gitlab_client.is_lead_fix_commit(commit(
            "s", title="[Tranzor] Language Lead fix: fr-CA - common.uns.x__y__z")))

    def test_batch_fix(self):
        self.assertTrue(gitlab_client.is_lead_fix_commit(commit(
            "s", title="[Tranzor] Language Lead batch fix: 12 translation(s)")))

    def test_wrong_author(self):
        self.assertFalse(gitlab_client.is_lead_fix_commit(commit(
            "s", title="[Tranzor] Language Lead fix: fr-CA - x",
            author="someone.else@ringcentral.com")))

    def test_non_fix_title(self):
        self.assertFalse(gitlab_client.is_lead_fix_commit(commit(
            "s", title="[Tranzor] Add translations for task abc")))

    def test_non_dict(self):
        self.assertFalse(gitlab_client.is_lead_fix_commit(None))
        self.assertFalse(gitlab_client.is_lead_fix_commit("nope"))


class TestOperatorFromFixTitle(unittest.TestCase):
    def test_es_format_single(self):
        title = ("Tranzor | Language Lead anna.su@ringcentral.com fix | "
                 "Language fr-CA | Opus common.uns.x__y__z")
        self.assertEqual(
            gitlab_client._operator_from_fix_title(title),
            "anna.su@ringcentral.com")

    def test_es_format_batch(self):
        title = ("Tranzor | Language Lead bob@ringcentral.com batch fix | "
                 "Count 7")
        self.assertEqual(
            gitlab_client._operator_from_fix_title(title), "bob@ringcentral.com")

    def test_legacy_title_has_no_operator(self):
        title = "[Tranzor] Language Lead fix: fr-CA - common.uns.x__y__z"
        self.assertEqual(gitlab_client._operator_from_fix_title(title), "")


# ---------------------------------------------------------------------------
# scan_branch_fix_commits
# ---------------------------------------------------------------------------
class TestScanBranchFixCommits(unittest.TestCase):
    def test_uns_single_fix_maps_path_and_fetches_blobs(self):
        """LOC-24850 shape: a single-row fix commit rewrites the whole fr-CA
        .hbs. We map the path to its opus_id, then pull full pre/post blobs."""
        client = FakeClient(
            commits=[commit("fixsha",
                            title="[Tranzor] Language Lead fix: fr-CA - "
                                  "common.uns.local_test__email_html__3710",
                            parents=["addsha"])],
            diffs={"fixsha": [file_diff(UNS_FR_CA)]},
            blobs={
                (UNS_FR_CA, "fixsha"): "...{{{$Brand_DisplayName}}}...",   # post (fixed)
                (UNS_FR_CA, "addsha"): "...{{${Brand_DisplayName}}}...",   # pre (corrupt)
            },
        )
        cases = {UNS_KEY: {"fixed_by_lead": None}}  # NULL fixed_by_lead on purpose

        out = gitlab_client.scan_branch_fix_commits(
            client, "common/uns", "UIA-409073_v4", cases)

        self.assertIn(UNS_KEY, out)
        rec = out[UNS_KEY]
        self.assertEqual(rec["pre"], "...{{${Brand_DisplayName}}}...")
        self.assertEqual(rec["post"], "...{{{$Brand_DisplayName}}}...")
        self.assertEqual(rec["sha"], "fixsha")

    def test_out_of_scope_uns_key_is_skipped(self):
        """A fix touching a UNS file whose (opus_id, lang) isn't in this MR's
        cases must NOT be surfaced (and must NOT cost a blob fetch)."""
        client = FakeClient(
            commits=[commit("fixsha", title="[Tranzor] Language Lead batch fix: 1")],
            diffs={"fixsha": [file_diff(UNS_FR_CA)]},
            blobs={(UNS_FR_CA, "fixsha"): "x", (UNS_FR_CA, "addsha"): "y"},
        )
        out = gitlab_client.scan_branch_fix_commits(
            client, "common/uns", "br", cases_by_key={})
        self.assertEqual(out, {})
        self.assertEqual(client.blob_calls, [],
                         "must not fetch blobs for out-of-scope keys")

    def test_independent_of_fixed_by_lead(self):
        """The scan never reads fixed_by_lead — a case with it NULL is detected
        purely from the branch commit. This is the whole point of P2."""
        client = FakeClient(
            commits=[commit("fixsha", title="[Tranzor] Language Lead fix: fr-CA - "
                                            "common.uns.local_test__email_html__3710")],
            diffs={"fixsha": [file_diff(UNS_FR_CA)]},
            blobs={(UNS_FR_CA, "fixsha"): "post", (UNS_FR_CA, "addsha"): "pre"},
        )
        out = gitlab_client.scan_branch_fix_commits(
            client, "common/uns", "br", {UNS_KEY: {}})  # no fixed_by_lead at all
        self.assertEqual(out[UNS_KEY]["post"], "post")

    def test_generic_keyvalue_file_fallback(self):
        """Non-UNS resource files still resolve via extract_diff_values so the
        same NULL-fixed_by_lead gap is closed for ordinary projects too."""
        path = "locale/es-419/messages.json"
        # extract_diff_values matches on the opus_id's LAST segment ("myKey"),
        # which is the actual JSON key in the resource file.
        client = FakeClient(
            commits=[commit("fixsha",
                            title="[Tranzor] Language Lead batch fix: 1 translation(s)")],
            diffs={"fixsha": [file_diff(path, [
                '-  "myKey": "old text",',
                '+  "myKey": "new text",',
            ])]},
        )
        out = gitlab_client.scan_branch_fix_commits(
            client, "proj", "br", {("ns.myKey", "es-419"): {}})
        self.assertEqual(out[("ns.myKey", "es-419")]["pre"], "old text")
        self.assertEqual(out[("ns.myKey", "es-419")]["post"], "new text")

    def test_non_fix_commits_ignored(self):
        client = FakeClient(
            commits=[commit("addsha",
                            title="[Tranzor] Add translations for task abc",
                            parents=[])],
            diffs={"addsha": [file_diff(UNS_FR_CA)]},
            blobs={(UNS_FR_CA, "addsha"): "x"},
        )
        out = gitlab_client.scan_branch_fix_commits(
            client, "common/uns", "br", {UNS_KEY: {}})
        self.assertEqual(out, {})

    def test_merge_keeps_oldest_pre_newest_post(self):
        """Two fix commits touch the same key. pre is the oldest version,
        post is the newest — the full change across the branch."""
        client = FakeClient(
            commits=[
                commit("fix1", title="[Tranzor] Language Lead fix: fr-CA - "
                                     "common.uns.local_test__email_html__3710",
                       when="2026-06-08T10:00:00Z", parents=["addsha"]),
                commit("fix2", title="[Tranzor] Language Lead fix: fr-CA - "
                                     "common.uns.local_test__email_html__3710",
                       when="2026-06-08T11:00:00Z", parents=["fix1"]),
            ],
            diffs={"fix1": [file_diff(UNS_FR_CA)], "fix2": [file_diff(UNS_FR_CA)]},
            blobs={
                (UNS_FR_CA, "addsha"): "v0",
                (UNS_FR_CA, "fix1"): "v1",
                (UNS_FR_CA, "fix2"): "v2",
            },
        )
        out = gitlab_client.scan_branch_fix_commits(
            client, "common/uns", "br", {UNS_KEY: {}})
        self.assertEqual(out[UNS_KEY]["pre"], "v0")   # oldest fix's parent
        self.assertEqual(out[UNS_KEY]["post"], "v2")  # newest fix
        self.assertEqual(out[UNS_KEY]["sha"], "fix2")

    def test_missing_pre_blob_degrades_to_empty(self):
        """If the parent blob 404s (e.g. file added in the fix commit), pre is
        '' rather than blowing up the whole scan."""
        client = FakeClient(
            commits=[commit("fixsha", title="[Tranzor] Language Lead fix: fr-CA - "
                                            "common.uns.local_test__email_html__3710",
                            parents=["addsha"])],
            diffs={"fixsha": [file_diff(UNS_FR_CA)]},
            blobs={(UNS_FR_CA, "fixsha"): "post"},  # no parent blob entry -> None
        )
        out = gitlab_client.scan_branch_fix_commits(
            client, "common/uns", "br", {UNS_KEY: {}})
        self.assertEqual(out[UNS_KEY]["pre"], "")
        self.assertEqual(out[UNS_KEY]["post"], "post")

    def test_access_error_propagates(self):
        resp = requests.Response()
        resp.status_code = 403
        client = FakeClient(list_error=requests.HTTPError(response=resp))
        with self.assertRaises(gitlab_client.GitLabAccessError) as ctx:
            gitlab_client.scan_branch_fix_commits(
                client, "common/uns", "br", {UNS_KEY: {}})
        self.assertEqual(ctx.exception.status_code, 403)

    def test_empty_branch_returns_empty(self):
        self.assertEqual(
            gitlab_client.scan_branch_fix_commits(
                FakeClient(), "common/uns", "", {UNS_KEY: {}}),
            {})


if __name__ == "__main__":
    unittest.main()
