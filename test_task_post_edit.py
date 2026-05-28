"""Unit tests for task_post_edit — the post-edit detector + cache + async
prefetch used by the three task-list tabs.

Pure-Python; no HTTP. Where the prefetch path touches the channel fetchers
(``_fetch_legacy`` / ``_fetch_scan`` / ``_fetch_mr``), we monkeypatch them
so tests run offline and deterministically.

Run:  python -m unittest test_task_post_edit
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import task_post_edit as tpe


# ---------------------------------------------------------------------------
# Pure-logic rules
# ---------------------------------------------------------------------------
class LegacyAndScanRuleTests(unittest.TestCase):
    """``translation_type in {"Manual Edit", "LLM Retranslate"}`` is the
    sole signal. Anything else → False."""

    def test_empty_inputs_are_false(self):
        self.assertFalse(tpe.has_post_edit_legacy([]))
        self.assertFalse(tpe.has_post_edit_legacy(None))
        self.assertFalse(tpe.has_post_edit_scan([]))

    def test_all_machine_is_false(self):
        rows = [
            {"translation_type": "LLM"},
            {"translation_type": "LLM"},
            {"translation_type": ""},
            {},  # no key at all
        ]
        self.assertFalse(tpe.has_post_edit_legacy(rows))

    def test_one_manual_edit_flips_true(self):
        rows = [
            {"translation_type": "LLM"},
            {"translation_type": "Manual Edit"},
            {"translation_type": "LLM"},
        ]
        self.assertTrue(tpe.has_post_edit_legacy(rows))

    def test_llm_retranslate_counts(self):
        rows = [{"translation_type": "LLM Retranslate"}]
        self.assertTrue(tpe.has_post_edit_legacy(rows))

    def test_unknown_label_does_not_count(self):
        # Defensive: if Tranzor introduces a new tag we don't recognise,
        # we err on the side of "machine" — better silent than a false ✏️.
        rows = [{"translation_type": "AI Review Pass"}]
        self.assertFalse(tpe.has_post_edit_legacy(rows))

    def test_scan_uses_same_rule(self):
        # scan rule is the legacy rule by reference — guard against future
        # accidental divergence.
        rows = [{"translation_type": "Manual Edit"}]
        self.assertIs(tpe.has_post_edit_scan, tpe.has_post_edit_legacy)
        self.assertTrue(tpe.has_post_edit_scan(rows))


class MrRuleTests(unittest.TestCase):
    """``fixed_by_lead`` non-empty marks the case as human-edited.
    Auto-refine (iteration > 1) is intentionally NOT a signal — see
    PR description for the alignment-with-Human-Revisions-tab decision."""

    def test_empty_inputs_are_false(self):
        self.assertFalse(tpe.has_post_edit_mr_from_cases([]))
        self.assertFalse(tpe.has_post_edit_mr_from_cases(None))

    def test_auto_refine_alone_is_not_post_edit(self):
        # iteration > 1 with no fixer → still False. This is the strict
        # human-only mode the user chose.
        cases = [{"iteration": 3, "translated_text": "v3",
                  "fixed_by_lead": None}]
        self.assertFalse(tpe.has_post_edit_mr_from_cases(cases))

    def test_lead_fix_flips_true(self):
        cases = [
            {"iteration": 1, "fixed_by_lead": None},
            {"iteration": 1, "fixed_by_lead": "alice@example.com"},
        ]
        self.assertTrue(tpe.has_post_edit_mr_from_cases(cases))

    def test_empty_string_does_not_count(self):
        # Tranzor sometimes serializes "no fix" as "" rather than null.
        cases = [{"fixed_by_lead": ""}]
        self.assertFalse(tpe.has_post_edit_mr_from_cases(cases))


class BatchFixCommitFingerprintTests(unittest.TestCase):
    """``_is_batch_fix_commit`` is the strict gate before we mark an MR
    based on GitLab activity. Anchor both halves of the check."""

    def test_canonical_commit_matches(self):
        c = {
            "author_email": "tranzor.service@rcoffice.ringcentral.com",
            "author_name": "Tranzor",
            "title": "[Tranzor] Language Lead batch fix: 14 translation(s)",
        }
        self.assertTrue(tpe._is_batch_fix_commit(c))

    def test_wrong_author_rejected_even_if_title_matches(self):
        # Defensive: a developer impersonating the title MUST NOT trigger
        # a false positive.
        c = {
            "author_email": "human@example.com",
            "title": "[Tranzor] Language Lead batch fix: 5 translation(s)",
        }
        self.assertFalse(tpe._is_batch_fix_commit(c))

    def test_wrong_title_rejected_even_if_author_matches(self):
        # The service account pushes other automated commits too (e.g.
        # re-translations) — those mustn't be misclassified as fixes.
        c = {
            "author_email": "tranzor.service@rcoffice.ringcentral.com",
            "title": "[Tranzor] Auto-translation: 200 row(s) updated",
        }
        self.assertFalse(tpe._is_batch_fix_commit(c))

    def test_missing_fields_rejected(self):
        self.assertFalse(tpe._is_batch_fix_commit({}))
        self.assertFalse(tpe._is_batch_fix_commit(
            {"author_email": "tranzor.service@rcoffice.ringcentral.com"}
        ))


class HasBatchFixOnBranchTests(unittest.TestCase):
    """``_has_batch_fix_on_branch`` glues the fingerprint check to the
    GitLab commits call. Mock the client so tests stay offline."""

    def test_empty_inputs_are_false(self):
        self.assertFalse(tpe._has_batch_fix_on_branch("", "branch"))
        self.assertFalse(tpe._has_batch_fix_on_branch("proj", ""))
        self.assertFalse(tpe._has_batch_fix_on_branch(None, None))

    def _patch_client(self, *, has_token=True, commits=None, raise_exc=None):
        """Monkeypatch gitlab_client.GitLabClient with a fake instance."""
        fake = mock.MagicMock()
        fake.has_token.return_value = has_token
        if raise_exc is not None:
            fake.list_commits.side_effect = raise_exc
        else:
            fake.list_commits.return_value = commits or []
        return mock.patch("gitlab_client.GitLabClient", return_value=fake)

    def test_no_token_returns_false_without_calling_api(self):
        with self._patch_client(has_token=False) as p:
            self.assertFalse(
                tpe._has_batch_fix_on_branch("proj", "main"),
            )
        # The patched class was instantiated, but list_commits was never
        # called (no point hitting GitLab without auth).
        p.return_value.list_commits.assert_not_called()

    def test_matching_commit_returns_true(self):
        commits = [
            {"author_email": "tranzor.service@rcoffice.ringcentral.com",
             "title": "[Tranzor] Language Lead batch fix: 1 translation(s)"},
        ]
        with self._patch_client(commits=commits):
            self.assertTrue(
                tpe._has_batch_fix_on_branch("proj", "feature/x"),
            )

    def test_no_matching_commit_returns_false(self):
        # Two commits from the right author but neither has the
        # BATCH_FIX title — must not flip.
        commits = [
            {"author_email": "tranzor.service@rcoffice.ringcentral.com",
             "title": "[Tranzor] Auto retranslate batch: 50 rows"},
            {"author_email": "tranzor.service@rcoffice.ringcentral.com",
             "title": "ci: bump dependencies"},
        ]
        with self._patch_client(commits=commits):
            self.assertFalse(
                tpe._has_batch_fix_on_branch("proj", "feature/x"),
            )

    def test_gitlab_exception_returns_false_not_raise(self):
        # GitLab outage MUST NOT bubble — the user just doesn't see ✏️
        # for BATCH_FIX this session; they still see single-row fixes.
        with self._patch_client(raise_exc=RuntimeError("boom")):
            self.assertFalse(
                tpe._has_batch_fix_on_branch("proj", "feature/x"),
            )

    def test_uses_lookback_window_and_author_filter(self):
        """Sanity-check the parameters we pass to list_commits — these
        are what make the API call cheap (server-side filtering)."""
        with self._patch_client(commits=[]) as p:
            tpe._has_batch_fix_on_branch("proj", "feature/x")
        client = p.return_value
        client.list_commits.assert_called_once()
        kwargs = client.list_commits.call_args.kwargs
        self.assertEqual(kwargs.get("ref_name"), "feature/x")
        self.assertEqual(
            kwargs.get("author"),
            "tranzor.service@rcoffice.ringcentral.com",
        )
        self.assertIn("since", kwargs)


class FetchMrTwoPathTests(unittest.TestCase):
    """``_fetch_mr`` must check BATCH_FIX first (cheap) and fall through
    to dashboard cases only when needed. Either path can return True."""

    def setUp(self):
        # Always mock out dashboard cases so unit tests don't hit the
        # real Tranzor backend.
        self._cases_patch = mock.patch(
            "export_mr_pipeline.fetch_dashboard_cases"
        )
        self._cases = self._cases_patch.start()
        self.addCleanup(self._cases_patch.stop)

        # Mock out the BATCH_FIX path by default.
        self._batch_patch = mock.patch.object(
            tpe, "_has_batch_fix_on_branch", return_value=False,
        )
        self._batch = self._batch_patch.start()
        self.addCleanup(self._batch_patch.stop)

        # Mock the GitLab client so source_branch lookups are scripted.
        self._gc_patch = mock.patch("gitlab_client.GitLabClient")
        gc_cls = self._gc_patch.start()
        self.addCleanup(self._gc_patch.stop)
        self._client = mock.MagicMock()
        self._client.has_token.return_value = True
        self._client.get_merge_request.return_value = {
            "source_branch": "feature/NOVA-12118",
        }
        gc_cls.return_value = self._client

    def test_batch_fix_hit_short_circuits(self):
        """When BATCH_FIX returns True, we must not call dashboard
        cases at all (avoids the heavy 1-2MB response)."""
        self._batch.return_value = True
        self.assertTrue(tpe._fetch_mr(("proj", 1066)))
        self._cases.assert_not_called()

    def test_falls_through_to_dashboard_cases(self):
        """No BATCH_FIX → check single-row fixes."""
        self._batch.return_value = False
        self._cases.return_value = {
            "mrs": [{"cases": [
                {"fixed_by_lead": "alice@example.com"},
            ]}],
        }
        self.assertTrue(tpe._fetch_mr(("proj", 1066)))
        self._cases.assert_called_once()

    def test_no_signal_anywhere_returns_false(self):
        self._batch.return_value = False
        self._cases.return_value = {"mrs": []}
        self.assertFalse(tpe._fetch_mr(("proj", 1066)))

    def test_legacy_bare_iid_skips_batch_fix_path(self):
        """If a caller passes a bare mr_iid (the PR #72 shape), only
        the dashboard-cases path runs — preserves backward-compat."""
        self._cases.return_value = {
            "mrs": [{"cases": [
                {"fixed_by_lead": "alice@example.com"},
            ]}],
        }
        self.assertTrue(tpe._fetch_mr(1066))
        # No project_id available → BATCH_FIX path never invoked.
        self._batch.assert_not_called()


# ---------------------------------------------------------------------------
# PostEditCache
# ---------------------------------------------------------------------------
class CacheTests(unittest.TestCase):

    def test_missing_key_returns_none(self):
        c = tpe.PostEditCache()
        self.assertIsNone(c.get("legacy", "t-1"))
        self.assertFalse(c.has("legacy", "t-1"))

    def test_set_and_get_roundtrip(self):
        c = tpe.PostEditCache()
        c.set("legacy", "t-1", True)
        self.assertTrue(c.get("legacy", "t-1"))
        self.assertTrue(c.has("legacy", "t-1"))

    def test_false_is_distinguishable_from_unset(self):
        c = tpe.PostEditCache()
        c.set("scan", "t-2", False)
        self.assertFalse(c.get("scan", "t-2"))  # actual False
        self.assertTrue(c.has("scan", "t-2"))   # but recorded

    def test_distinct_kinds_dont_collide(self):
        c = tpe.PostEditCache()
        c.set("mr", "1066", True)
        c.set("legacy", "1066", False)
        self.assertTrue(c.get("mr", "1066"))
        self.assertFalse(c.get("legacy", "1066"))

    def test_clear_drops_all(self):
        c = tpe.PostEditCache()
        c.set("legacy", "t-1", True)
        c.clear()
        self.assertIsNone(c.get("legacy", "t-1"))


# ---------------------------------------------------------------------------
# prefetch_async
# ---------------------------------------------------------------------------
class PrefetchAsyncTests(unittest.TestCase):

    def _make_fetchers(self, legacy_map=None, scan_map=None, mr_map=None):
        """Build a mock _FETCHERS dict that resolves from in-memory tables.

        Missing keys raise to exercise the on_error path.
        """
        legacy_map = legacy_map or {}
        scan_map = scan_map or {}
        mr_map = mr_map or {}

        def _lookup(table, key):
            if key not in table:
                raise KeyError(f"no test data for {key}")
            return table[key]

        return {
            "legacy": lambda k: _lookup(legacy_map, k),
            "scan":   lambda k: _lookup(scan_map, k),
            "mr":     lambda k: _lookup(mr_map, k),
        }

    def test_fires_on_result_for_each_item(self):
        fetchers = self._make_fetchers(
            legacy_map={"t-1": True, "t-2": False},
        )
        cache = tpe.PostEditCache()
        seen: list[tuple] = []
        lock = threading.Lock()

        def _on_result(kind, key, value):
            with lock:
                seen.append((kind, key, value))

        with mock.patch.dict(tpe._FETCHERS, fetchers, clear=False):
            t = tpe.prefetch_async(
                [("legacy", "t-1"), ("legacy", "t-2")],
                on_result=_on_result,
                cache=cache,
            )
            t.join(timeout=5)

        self.assertCountEqual(seen, [
            ("legacy", "t-1", True),
            ("legacy", "t-2", False),
        ])
        # Both values cached.
        self.assertTrue(cache.get("legacy", "t-1"))
        self.assertFalse(cache.get("legacy", "t-2"))

    def test_cache_hits_skip_fetcher(self):
        """If we already know the answer, the fetcher must never be called.
        This is the core "page-back-and-forth is instant" guarantee."""
        cache = tpe.PostEditCache()
        cache.set("legacy", "t-1", True)
        cache.set("legacy", "t-2", False)

        call_count = {"n": 0}

        def _fake_legacy(_k):
            call_count["n"] += 1
            return False

        fetchers = {"legacy": _fake_legacy, "scan": _fake_legacy,
                    "mr": _fake_legacy}
        seen: list[tuple] = []
        lock = threading.Lock()

        def _on_result(kind, key, value):
            with lock:
                seen.append((kind, key, value))

        with mock.patch.dict(tpe._FETCHERS, fetchers, clear=True):
            t = tpe.prefetch_async(
                [("legacy", "t-1"), ("legacy", "t-2")],
                on_result=_on_result,
                cache=cache,
            )
            t.join(timeout=5)

        self.assertEqual(call_count["n"], 0)
        self.assertCountEqual(seen, [
            ("legacy", "t-1", True),
            ("legacy", "t-2", False),
        ])

    def test_fetcher_exception_calls_on_error_not_on_result(self):
        fetchers = self._make_fetchers(legacy_map={})  # always KeyError
        cache = tpe.PostEditCache()
        results, errors = [], []
        lock = threading.Lock()

        def _ok(kind, key, value):
            with lock:
                results.append((kind, key, value))

        def _err(kind, key, exc):
            with lock:
                errors.append((kind, key, type(exc).__name__))

        with mock.patch.dict(tpe._FETCHERS, fetchers, clear=False):
            t = tpe.prefetch_async(
                [("legacy", "missing")],
                on_result=_ok,
                on_error=_err,
                cache=cache,
            )
            t.join(timeout=5)

        self.assertEqual(results, [])
        self.assertEqual(errors, [("legacy", "missing", "KeyError")])
        # Failed fetches MUST NOT be cached — next attempt should retry.
        self.assertFalse(cache.has("legacy", "missing"))

    def test_cancel_event_short_circuits(self):
        """If the GUI's cancel_event is set before we start, no fetch is
        attempted at all — important when the user switches tabs faster
        than the prefetch can complete."""
        fetchers = self._make_fetchers(legacy_map={"t-1": True})
        call_count = {"n": 0}

        def _spy_legacy(k):
            call_count["n"] += 1
            return fetchers["legacy"](k)

        cancel = threading.Event()
        cancel.set()

        seen = []
        with mock.patch.dict(
            tpe._FETCHERS,
            {"legacy": _spy_legacy, "scan": _spy_legacy, "mr": _spy_legacy},
            clear=True,
        ):
            t = tpe.prefetch_async(
                [("legacy", "t-1")],
                on_result=lambda *a: seen.append(a),
                cancel_event=cancel,
            )
            t.join(timeout=5)

        self.assertEqual(call_count["n"], 0)
        self.assertEqual(seen, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
