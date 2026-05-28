"""Unit tests for gitlab_client.py — focuses on the key-aware fix-commit
matcher introduced to fix silent wrong-fills in batch-fix scenarios.

Run:  python -m unittest test_gitlab_client
or:   python test_gitlab_client.py
"""
from __future__ import annotations

import os
import sys
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gitlab_client


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class FakeClient:
    """In-memory stand-in for :class:`gitlab_client.GitLabClient` — no network."""

    def __init__(self, branches=None, diffs=None):
        self._branches = branches or []
        self._diffs = diffs or {}

    def list_branches(self, project_id, search=None):
        # Mirror real behavior: when ``search`` is given, return only matching
        # names. Real GitLab does prefix-ish substring matching.
        if search:
            return [b for b in self._branches if search in b.get("name", "")]
        return list(self._branches)

    def get_commit_diff(self, project_id, sha):
        return self._diffs.get(sha, [])


def make_branch(name, sha):
    return {"name": name, "commit": {"id": sha}}


def make_diff(path, lines):
    """Build the GitLab ``/commits/:sha/diff`` shape for a single file."""
    return [{
        "new_path": path,
        "old_path": path,
        "diff": "\n".join(lines),
    }]


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
class TestFindFixCommitForKey(unittest.TestCase):
    """The new matcher must iterate candidates by time-delta and pick the
    one whose diff *actually* edits the target (opus_id, target_language)."""

    def test_picks_branch_whose_diff_contains_key_even_if_not_closest(self):
        """Closest-by-time branch is a *sibling* fix (different key). The
        next-closest branch is the real one for our key — that's what should
        be returned. This is the original silent-wrong-fill scenario."""
        client = FakeClient(
            branches=[
                # Both within the 60-min default window of fixed_at 10:00:00.
                make_branch("tranzor-fix/26-2-2_XMN/20260417100005", "sha_sibling"),  # +5s
                make_branch("tranzor-fix/26-2-2_XMN/20260417100015", "sha_match"),    # +15s
            ],
            diffs={
                "sha_sibling": make_diff("locale/es-419/messages.json", [
                    '-  "ns.otherKey": "stale",',
                    '+  "ns.otherKey": "fresh",',
                ]),
                "sha_match": make_diff("locale/es-419/messages.json", [
                    '-  "myKey": "old text",',
                    '+  "myKey": "new text",',
                ]),
            },
        )
        sha, pre, post = gitlab_client.find_fix_commit_for_key(
            client, "proj", "2026-04-17T10:00:00", "ns.myKey", "es-419")
        self.assertEqual(sha, "sha_match")
        self.assertEqual(pre, "old text")
        self.assertEqual(post, "new text")

    def test_returns_none_when_no_candidate_contains_key(self):
        """All candidates touch *other* keys — never silently fall through to
        the closest one. Downstream relies on (None, None, None) to mean
        'leave gitlab_recovery empty for this case'."""
        client = FakeClient(
            branches=[make_branch("tranzor-fix/foo/20260417100000", "sha_x")],
            diffs={"sha_x": make_diff("locale/es-419/messages.json", [
                '-  "differentKey": "old",',
                '+  "differentKey": "new",',
            ])},
        )
        sha, pre, post = gitlab_client.find_fix_commit_for_key(
            client, "proj", "2026-04-17T10:00:00", "ns.myKey", "es-419")
        self.assertIsNone(sha)
        self.assertIsNone(pre)
        self.assertIsNone(post)

    def test_returns_none_when_no_branches_in_window(self):
        client = FakeClient(branches=[
            # Way outside the 60-min window
            make_branch("tranzor-fix/foo/20260101100000", "sha_old"),
        ])
        sha, pre, post = gitlab_client.find_fix_commit_for_key(
            client, "proj", "2026-04-17T10:00:00", "ns.myKey", "es-419")
        self.assertIsNone(sha)
        self.assertIsNone(pre)
        self.assertIsNone(post)

    def test_returns_none_when_fixed_at_unparseable(self):
        sha, pre, post = gitlab_client.find_fix_commit_for_key(
            FakeClient(branches=[]), "proj", "not a date",
            "ns.myKey", "es-419")
        self.assertIsNone(sha)
        self.assertIsNone(pre)
        self.assertIsNone(post)

    def test_prefers_closest_when_multiple_candidates_match(self):
        """If two candidate branches both touch our key, pick the closer one —
        falling back to the heuristic when the time signal is the only
        differentiator left."""
        client = FakeClient(
            branches=[
                make_branch("tranzor-fix/A/20260417100020", "sha_far"),    # +20s
                make_branch("tranzor-fix/A/20260417100005", "sha_near"),   # +5s
            ],
            diffs={
                "sha_far": make_diff("locale/es-419/messages.json", [
                    '-  "myKey": "v0",',
                    '+  "myKey": "v1",',
                ]),
                "sha_near": make_diff("locale/es-419/messages.json", [
                    '-  "myKey": "v1",',
                    '+  "myKey": "v2",',
                ]),
            },
        )
        sha, pre, post = gitlab_client.find_fix_commit_for_key(
            client, "proj", "2026-04-17T10:00:00", "ns.myKey", "es-419")
        self.assertEqual(sha, "sha_near")
        self.assertEqual(pre, "v1")
        self.assertEqual(post, "v2")

    def test_diff_fetch_failure_on_one_candidate_skips_to_next(self):
        """A single broken-diff candidate shouldn't abort the whole search —
        the next candidate (with a working diff) should still be considered."""
        class FlakyClient(FakeClient):
            def get_commit_diff(self, project_id, sha):
                if sha == "sha_flaky":
                    raise requests.ConnectionError("simulated transient")
                return super().get_commit_diff(project_id, sha)

        client = FlakyClient(
            branches=[
                make_branch("tranzor-fix/A/20260417100005", "sha_flaky"),  # diff blows up
                make_branch("tranzor-fix/A/20260417100015", "sha_good"),   # diff OK + has key
            ],
            diffs={
                "sha_good": make_diff("locale/es-419/messages.json", [
                    '-  "myKey": "old",',
                    '+  "myKey": "new",',
                ]),
            },
        )
        sha, pre, post = gitlab_client.find_fix_commit_for_key(
            client, "proj", "2026-04-17T10:00:00", "ns.myKey", "es-419")
        self.assertEqual(sha, "sha_good")

    def test_propagates_gitlab_access_error(self):
        """List failure (401/403/404) must surface as GitLabAccessError so
        the caller can give up on the entire project without retrying."""
        class DeniedClient:
            def list_branches(self, *args, **kw):
                resp = requests.Response()
                resp.status_code = 403
                resp.url = "https://git.example.com/api/v4/projects/p/repository/branches"
                raise requests.HTTPError(response=resp)

            def get_commit_diff(self, *args, **kw):
                return []

        with self.assertRaises(gitlab_client.GitLabAccessError) as ctx:
            gitlab_client.find_fix_commit_for_key(
                DeniedClient(), "proj", "2026-04-17T10:00:00",
                "ns.myKey", "es-419")
        self.assertEqual(ctx.exception.status_code, 403)


class TestLegacyMatcherStillWorks(unittest.TestCase):
    """``find_fix_commit_sha`` is kept for backward compat — make sure the
    pagination refactor didn't break it."""

    def test_returns_closest_by_time(self):
        client = FakeClient(
            branches=[
                make_branch("tranzor-fix/A/20260417100030", "sha_far"),   # +30s
                make_branch("tranzor-fix/A/20260417100005", "sha_near"),  # +5s
            ],
        )
        sha = gitlab_client.find_fix_commit_sha(
            client, "proj", "2026-04-17T10:00:00")
        self.assertEqual(sha, "sha_near")

    def test_returns_none_outside_window(self):
        client = FakeClient(
            branches=[make_branch("tranzor-fix/A/20260101000000", "sha_x")],
        )
        sha = gitlab_client.find_fix_commit_sha(
            client, "proj", "2026-04-17T10:00:00")
        self.assertIsNone(sha)


class TestParseBranchTimestamp(unittest.TestCase):
    def test_canonical_form(self):
        ts = gitlab_client.parse_branch_timestamp(
            "tranzor-fix/26-2-2_XMN/20260417070703")
        self.assertEqual(ts.year, 2026)
        self.assertEqual(ts.hour, 7)

    def test_no_timestamp_returns_none(self):
        self.assertIsNone(gitlab_client.parse_branch_timestamp("master"))


# ---------------------------------------------------------------------------
# get_merge_request / fetch_mr_labels — added for SKIP_TRANSLATE_LABEL viz
# ---------------------------------------------------------------------------
class _FakeResponse:
    """Just enough of requests.Response for the MR helpers."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class _FakeSession:
    """In-memory stand-in for requests.Session — records calls so tests can
    assert the cache short-circuits subsequent fetches."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self._status_code = status_code
        self.calls = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append(url)
        return _FakeResponse(self._payload, self._status_code)


def _make_client_with_session(session):
    """Build a GitLabClient and swap in our fake session so no HTTP goes out."""
    client = gitlab_client.GitLabClient(
        base_url="https://git.example.com", token="t",
    )
    client._session = session
    return client


class TestGetMergeRequest(unittest.TestCase):

    def test_returns_full_payload(self):
        payload = {
            "iid": 1066, "labels": ["skip-translate", "needs-review"],
            "title": "do not translate me",
        }
        session = _FakeSession(payload)
        client = _make_client_with_session(session)

        mr = client.get_merge_request("group/proj", 1066)

        self.assertEqual(mr["iid"], 1066)
        self.assertEqual(mr["labels"], ["skip-translate", "needs-review"])
        self.assertEqual(len(session.calls), 1)
        self.assertIn("merge_requests/1066", session.calls[0])

    def test_caches_within_client(self):
        session = _FakeSession({"iid": 1, "labels": []})
        client = _make_client_with_session(session)

        client.get_merge_request("p", 1)
        client.get_merge_request("p", 1)  # same key → cache hit

        self.assertEqual(
            len(session.calls), 1,
            "Second call should be served from _mr_cache, not re-fetched",
        )

    def test_distinct_keys_dont_share_cache(self):
        session = _FakeSession({"iid": 1, "labels": []})
        client = _make_client_with_session(session)

        client.get_merge_request("p", 1)
        client.get_merge_request("p", 2)
        client.get_merge_request("q", 1)

        self.assertEqual(len(session.calls), 3)

    def test_http_error_propagates(self):
        session = _FakeSession({}, status_code=404)
        client = _make_client_with_session(session)
        with self.assertRaises(requests.HTTPError):
            client.get_merge_request("p", 999)


class TestFetchMrLabels(unittest.TestCase):

    def test_extracts_labels_list(self):
        session = _FakeSession(
            {"iid": 1, "labels": ["a", "b", "skip-translate"]}
        )
        client = _make_client_with_session(session)
        self.assertEqual(
            client.fetch_mr_labels("p", 1),
            ["a", "b", "skip-translate"],
        )

    def test_no_labels_field_returns_empty_list(self):
        session = _FakeSession({"iid": 1})
        client = _make_client_with_session(session)
        self.assertEqual(client.fetch_mr_labels("p", 1), [])

    def test_drops_falsy_entries(self):
        # Defensive: a malformed payload with nulls / empty strings shouldn't
        # poison the labels list with bogus "skip-translate"-look-alikes.
        session = _FakeSession({"iid": 1, "labels": ["a", None, "", "b"]})
        client = _make_client_with_session(session)
        self.assertEqual(client.fetch_mr_labels("p", 1), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
