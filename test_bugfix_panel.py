"""Unit tests for the display-free BugFix panel synchronization layer."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import bugfix_panel as bp


class TestHistoryPagination(unittest.TestCase):

    def test_fetches_every_page_with_server_maximum(self):
        rows = [{"submission_id": f"s-{i}"} for i in range(205)]
        calls = []

        def fake_get(url, params):
            calls.append((url, dict(params)))
            start = (params["page"] - 1) * params["page_size"]
            end = start + params["page_size"]
            return {
                "submissions": rows[start:end],
                "total_submissions": len(rows),
                "available_statuses": ["Applied", "Processing"],
            }

        payload = bp.fetch_all_history(
            "http://platform", page_size=999, get_fn=fake_get)

        self.assertEqual(len(payload["submissions"]), 205)
        self.assertEqual(payload["pages_fetched"], 3)
        self.assertEqual([call[1]["page"] for call in calls], [1, 2, 3])
        self.assertTrue(all(call[1]["page_size"] == 100 for call in calls))
        self.assertEqual(
            calls[0][0],
            "http://platform/api/v1/bug-fix/history",
        )

    def test_underreported_total_does_not_truncate_full_page(self):
        calls = []

        def fake_get(_url, params):
            calls.append(params["page"])
            if params["page"] == 1:
                return {
                    "submissions": [
                        {"submission_id": f"s-{i}"} for i in range(100)
                    ],
                    "total_submissions": 1,
                }
            return {"submissions": [], "total_submissions": 1}

        payload = bp.fetch_all_history(
            "http://platform", get_fn=fake_get)

        self.assertEqual(len(payload["submissions"]), 100)
        self.assertEqual(calls, [1, 2])

    def test_forwards_filters_and_truncates_query(self):
        seen = {}

        def fake_get(_url, params):
            seen.update(params)
            return {"submissions": [], "total_submissions": 0}

        bp.fetch_all_history(
            "http://platform/api/v1",
            project_id="common/uns",
            target_language="fr-FR",
            status="Applied",
            query="x" * 400,
            get_fn=fake_get,
        )
        self.assertEqual(seen["project_id"], "common/uns")
        self.assertEqual(seen["target_language"], "fr-FR")
        self.assertEqual(seen["status"], "Applied")
        self.assertEqual(len(seen["q"]), 300)

    def test_rejects_malformed_submissions(self):
        with self.assertRaises(ValueError):
            bp.fetch_all_history(
                "http://platform",
                get_fn=lambda *_a, **_k: {"submissions": {}},
            )


class TestNormalization(unittest.TestCase):

    def test_applied_and_open_are_independent_axes(self):
        row = bp.normalize_submission({
            "submission_id": "s1",
            "project_id": "wrong/fallback",
            "bug_id": "LOC-1",
            "mr_url": (
                "https://git.ringcentral.com/common/uns/"
                "-/merge_requests/4155"
            ),
            "mr_iid": 4155,
            "summary": {"total": 2, "aggregate_status": "Applied"},
            "target_languages": ["de-DE"],
            "records": [{}, {}],
        })
        self.assertEqual(row["platform_status"], "applied")
        self.assertEqual(row["mr_state"], "unknown")
        self.assertEqual(row["mr_project_id"], "common/uns")
        self.assertEqual(row["mr_iid"], 4155)
        self.assertTrue(row["has_mr"])

        enriched = bp.enrich_submission(
            row,
            _FakeGitLab(states={4155: "opened"}),
        )
        self.assertEqual(enriched["platform_status"], "applied")
        self.assertEqual(enriched["mr_state"], "opened")
        self.assertEqual(enriched["attention"]["level"], "watch")

    def test_successful_record_without_mr_is_not_an_error(self):
        row = bp.normalize_submission({
            "submission_id": "direct",
            "summary": {"total": 1, "aggregate_status": "Applied"},
        })
        self.assertFalse(row["has_mr"])
        self.assertEqual(row["mr_state"], "none")
        self.assertEqual(row["mr_state_label"], "Direct / no MR")
        self.assertEqual(row["attention"]["level"], "direct")

    def test_extracts_iid_from_url_when_field_is_missing(self):
        identity = bp.extract_mr_identity({
            "project_id": "x",
            "mr_url": (
                "https://git.example/group%2Fproject/"
                "-/merge_requests/77"
            ),
        })
        self.assertEqual(identity["project_id"], "group/project")
        self.assertEqual(identity["mr_iid"], 77)


class TestImportantComments(unittest.TestCase):

    def test_prioritizes_unresolved_then_action_and_filters_noise(self):
        discussions = [
            {
                "id": "d1",
                "notes": [{
                    "id": 1,
                    "body": "Routine system update",
                    "system": True,
                    "author": {"username": "gitlab-bot"},
                }],
            },
            {
                "id": "d2",
                "notes": [{
                    "id": 2,
                    "body": "Please fix this before merge",
                    "resolvable": False,
                    "resolved": False,
                    "updated_at": "2026-09-08T01:00:00Z",
                    "author": {"name": "Reviewer"},
                }],
            },
            {
                "id": "d3",
                "notes": [{
                    "id": 3,
                    "body": "This thread still needs a decision",
                    "resolvable": True,
                    "resolved": False,
                    "updated_at": "2026-09-07T01:00:00Z",
                    "author": {"name": "Lead"},
                }],
            },
            {
                "id": "d4",
                "notes": [{
                    "id": 4,
                    "body": "FYI only",
                    "updated_at": "2026-09-09T01:00:00Z",
                    "author": {"name": "Human"},
                }],
            },
        ]
        comments = bp.classify_important_comments(discussions, limit=3)
        self.assertEqual([item["id"] for item in comments], ["3", "2", "4"])
        self.assertEqual(comments[0]["reason"], "unresolved discussion")
        self.assertTrue(comments[0]["unresolved"])
        self.assertNotIn("1", [item["id"] for item in comments])

    def test_unresolved_bot_thread_is_kept(self):
        rows = bp.classify_important_comments([{
            "id": "d",
            "notes": [{
                "id": 5,
                "body": "Automated review",
                "resolvable": True,
                "resolved": False,
                "author": {"username": "review-bot"},
            }],
        }])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["unresolved"])


class _FakeGitLab:

    def __init__(self, states=None, fail_iids=None, discussions=None,
                 discussion_fail_iids=None, token=True):
        self.states = states or {}
        self.fail_iids = set(fail_iids or [])
        self.discussions = discussions or {}
        self.discussion_fail_iids = set(discussion_fail_iids or [])
        self.token = token
        self.mr_calls = []
        self.discussion_calls = []

    def has_token(self):
        return self.token

    def get_merge_request(self, project, iid, force_refresh=False):
        self.mr_calls.append((project, iid, force_refresh))
        if iid in self.fail_iids:
            raise RuntimeError("denied")
        return {
            "iid": iid,
            "state": self.states.get(iid, "opened"),
            "web_url": f"https://git/{project}/-/merge_requests/{iid}",
            "title": f"MR {iid}",
            "updated_at": "2026-09-08T02:00:00Z",
        }

    def list_mr_discussions(self, project, iid, **kwargs):
        self.discussion_calls.append((project, iid, kwargs))
        if iid in self.discussion_fail_iids:
            raise RuntimeError("comments denied")
        return self.discussions.get(iid, [])


class TestEnrichment(unittest.TestCase):

    @staticmethod
    def _row(iid):
        return bp.normalize_submission({
            "submission_id": f"s{iid}",
            "project_id": "group/project",
            "mr_iid": iid,
            "mr_url": (
                f"https://git/group/project/-/merge_requests/{iid}"
            ),
            "summary": {"aggregate_status": "Applied"},
        })

    def test_partial_failure_does_not_hide_other_rows(self):
        rows = bp.enrich_submissions(
            [self._row(1), self._row(2)],
            _FakeGitLab(states={1: "merged"}, fail_iids={2}),
            max_workers=2,
        )
        by_iid = {row["mr_iid"]: row for row in rows}
        self.assertEqual(by_iid[1]["mr_state"], "merged")
        self.assertEqual(by_iid[1]["mr_sync_error"], "")
        self.assertIn("denied", by_iid[2]["mr_sync_error"])
        self.assertEqual(len(rows), 2)

    def test_discussions_are_opt_in_for_selected_row(self):
        client = _FakeGitLab(discussions={1: [{
            "id": "d",
            "notes": [{
                "id": 8,
                "body": "Please fix",
                "author": {"name": "Reviewer"},
            }],
        }]})
        ordinary = bp.enrich_submission(
            self._row(1), client, include_discussions=False)
        self.assertFalse(ordinary["comments_loaded"])
        self.assertEqual(client.discussion_calls, [])

        selected = bp.enrich_submission(
            ordinary, client, include_discussions=True)
        self.assertTrue(selected["comments_loaded"])
        self.assertEqual(selected["comments"][0]["id"], "8")
        self.assertEqual(len(client.discussion_calls), 1)

    def test_discussion_failure_finishes_loading_and_keeps_live_mr(self):
        row = bp.enrich_submission(
            self._row(1),
            _FakeGitLab(
                states={1: "opened"},
                discussion_fail_iids={1},
            ),
            include_discussions=True,
        )
        self.assertEqual(row["mr_state"], "opened")
        self.assertEqual(row["mr_sync_error"], "")
        self.assertTrue(row["comments_loaded"])
        self.assertIn("comments denied", row["comments_error"])

    def test_missing_token_marks_only_gitlab_axis(self):
        row = bp.enrich_submission(
            self._row(1), _FakeGitLab(token=False))
        self.assertEqual(row["platform_status"], "applied")
        self.assertEqual(row["mr_state"], "unknown")
        self.assertIn("token", row["mr_sync_error"].lower())


class TestCacheAndSync(unittest.TestCase):

    def test_cache_is_token_free_and_malformed_cache_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cache.json")
            bp.save_cache({
                "submissions": [{
                    "submission_id": "s",
                    "summary": {"aggregate_status": "Applied"},
                    "gitlab_token": "secret",
                }],
                "access_token": "also-secret",
            }, path)
            with open(path, encoding="utf-8") as handle:
                raw = handle.read()
            self.assertNotIn("secret", raw)
            self.assertIsNotNone(bp.load_cache(path))

            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{broken")
            self.assertIsNone(bp.load_cache(path))

    def test_sync_falls_back_to_last_good_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cache.json")
            bp.save_cache({
                "submissions": [{
                    "submission_id": "cached",
                    "summary": {"aggregate_status": "Applied"},
                }],
                "total_submissions": 1,
            }, path)

            result = bp.sync_panel(
                "http://platform",
                get_fn=lambda *_a, **_k: (
                    _ for _ in ()).throw(RuntimeError("offline")),
                gitlab_client=_FakeGitLab(),
                cache_path=path,
            )
            self.assertTrue(result["ok"])
            self.assertFalse(result["live_ok"])
            self.assertEqual(result["source"], "cache")
            self.assertEqual(
                result["submissions"][0]["submission_id"], "cached")
            self.assertIn("offline", result["error"])

    def test_cache_write_failure_keeps_fresh_live_result(self):
        with mock.patch.object(
                bp, "save_cache", side_effect=OSError("disk full")):
            result = bp.sync_panel(
                "http://platform",
                get_fn=lambda *_a, **_k: {
                    "submissions": [{
                        "submission_id": "fresh",
                        "summary": {"aggregate_status": "Applied"},
                    }],
                    "total_submissions": 1,
                },
                gitlab_client=_FakeGitLab(),
            )
        self.assertTrue(result["live_ok"])
        self.assertEqual(result["source"], "live")
        self.assertEqual(
            result["submissions"][0]["submission_id"], "fresh")
        self.assertIn("disk full", result["cache_error"])

    def test_successful_sync_saves_live_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cache.json")
            result = bp.sync_panel(
                "http://platform",
                get_fn=lambda *_a, **_k: {
                    "submissions": [{
                        "submission_id": "live",
                        "summary": {"aggregate_status": "Applied"},
                    }],
                    "total_submissions": 1,
                },
                gitlab_client=_FakeGitLab(),
                cache_path=path,
            )
            self.assertTrue(result["live_ok"])
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(bp.load_cache(path)["source"], "cache")



class TestAttentionStatusMatrix(unittest.TestCase):

    def test_platform_failure_aggregates_are_always_action(self):
        states = {
            "delivery_failed",
            "tm_failed",
            "create_failed",
            "mr_creation_failed",
            "partially_applied",
            "not_merged",
        }
        for state in states:
            with self.subTest(state=state):
                attention = bp.derive_attention({
                    "platform_status": state,
                    "mr_state": "merged",
                    "has_mr": True,
                })
                self.assertEqual(attention["level"], "action")
                self.assertEqual(attention["code"], "workflow_failed")

    def test_platform_in_progress_aggregates_are_always_watch(self):
        states = {
            "queued",
            "processing",
            "pending",
            "all_waiting",
            "all_applying",
            "in_progress",
            "applying_fix",
            "waiting_for_merge",
        }
        for state in states:
            with self.subTest(state=state):
                attention = bp.derive_attention({
                    "platform_status": state,
                    "mr_state": "merged",
                    "has_mr": True,
                })
                self.assertEqual(attention["level"], "watch")
                self.assertEqual(
                    attention["code"], "workflow_in_progress")

    def test_blocking_discussion_metadata_is_action_before_lazy_comments(self):
        for value, expected in (
            (False, "unresolved_discussion"),
            (True, "no_action"),
            (None, "no_action"),
        ):
            with self.subTest(blocking_discussions_resolved=value):
                attention = bp.derive_attention({
                    "platform_status": "applied",
                    "mr_state": "merged",
                    "has_mr": True,
                    "blocking_discussions_resolved": value,
                    "comments": [],
                })
                self.assertEqual(attention["code"], expected)
                self.assertEqual(
                    attention["level"],
                    "action" if value is False else "done",
                )


class TestCancellation(unittest.TestCase):

    def test_pre_cancelled_history_does_not_start_request(self):
        cancel = threading.Event()
        cancel.set()
        get_fn = mock.Mock()

        with self.assertRaises(bp.SyncCancelled):
            bp.fetch_all_history(
                "http://platform", get_fn=get_fn, cancel_event=cancel)

        get_fn.assert_not_called()

    def test_cancellation_between_pages_stops_pagination(self):
        cancel = threading.Event()
        calls = []

        def fake_get(_url, params):
            calls.append(params["page"])
            cancel.set()
            return {
                "submissions": [
                    {"submission_id": f"s-{index}"}
                    for index in range(100)
                ],
                "total_submissions": 200,
            }

        with self.assertRaises(bp.SyncCancelled):
            bp.fetch_all_history(
                "http://platform", get_fn=fake_get,
                cancel_event=cancel)

        self.assertEqual(calls, [1])

    def test_batch_cancellation_does_not_start_remaining_rows(self):
        cancel = threading.Event()

        class CancelAfterFirstClient:
            def __init__(self):
                self.calls = []

            def has_token(self):
                return True

            def get_merge_request(
                    self, project, iid, force_refresh=False):
                self.calls.append((project, iid, force_refresh))
                cancel.set()
                return {
                    "iid": iid,
                    "state": "opened",
                    "web_url": (
                        f"https://git/{project}/-/merge_requests/{iid}"
                    ),
                }

        client = CancelAfterFirstClient()
        rows = [
            {
                "submission_id": f"s-{iid}",
                "project_id": "common/uns",
                "mr_iid": iid,
                "mr_url": (
                    "https://git/common/uns/-/merge_requests/"
                    f"{iid}"
                ),
            }
            for iid in range(1, 21)
        ]

        with self.assertRaises(bp.SyncCancelled):
            bp.enrich_submissions(
                rows, client, max_workers=1, cancel_event=cancel)

        self.assertEqual(
            [iid for _project, iid, _force in client.calls], [1])

    def test_sync_cancellation_never_falls_back_to_cache(self):
        cancel = threading.Event()
        cancel.set()
        with (
            mock.patch.object(bp, "load_cache") as load_cache,
            mock.patch.object(bp, "save_cache") as save_cache,
        ):
            result = bp.sync_panel(
                "http://platform",
                get_fn=mock.Mock(),
                cancel_event=cancel,
            )

        self.assertEqual(result["source"], "cancelled")
        self.assertFalse(result["ok"])
        load_cache.assert_not_called()
        save_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
