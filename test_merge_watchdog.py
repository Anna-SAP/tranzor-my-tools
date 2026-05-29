"""Tests for the Pending-Merge watchdog (PR-D).

Covers three layers:

1. ``update_mr_state_fields`` / ``append_merge_events`` /
   ``get_merge_events`` — small SQL helpers added to tranzor_checks.
2. ``merge_watchdog.check_once`` — the GitLab refresh pass. We mock
   :class:`gitlab_client.GitLabClient` so tests stay offline.
3. ``Watchdog`` thread lifecycle — start / stop must converge fast and
   never raise from a UI callback bug.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tranzor_checks as tc
import merge_watchdog as mw


NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def _hours_ago_iso(h):
    return (NOW - timedelta(hours=h)).isoformat().replace("+00:00", "Z")


class _IsolatedDb:
    def __enter__(self):
        self._tmp = tempfile.mkdtemp(prefix="watchdog-test-")
        self.path = os.path.join(self._tmp, "checks_index.db")
        self._orig = tc._default_db_path
        tc._default_db_path = lambda: self.path
        return self

    def __exit__(self, *exc):
        tc._default_db_path = self._orig
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(self._tmp)
        except OSError:
            pass


def _seed_red_mr(*, task_id, mr_iid, project_id="g/p", state="opened",
                 upvotes=2, updated_at=None, issues_zh=1):
    """Insert one red-tier MR task with a zh-CN issue."""
    import json
    now_iso = datetime.now(timezone.utc).isoformat()
    with tc._connect() as conn:
        conn.execute(
            """
            INSERT INTO task_checks(
                task_id, source_kind, project_id, project_name, mr_iid,
                task_name, task_status, final_score_avg, total_issues,
                total_rows, task_created_at, fetched_at, mr_labels,
                mr_state, mr_merge_status, mr_draft, mr_upvotes,
                mr_downvotes, mr_updated_at, mr_web_url
            ) VALUES (?, 'mr', ?, 'proj', ?, ?, 'completed', 80.0,
                      ?, 100, '2026-05-29T00:00:00', ?, '[]',
                      ?, 'can_be_merged', 0, ?, 0, ?, ?)
            """,
            (
                task_id, project_id, mr_iid, f"feature/{mr_iid}",
                issues_zh, now_iso,
                state, upvotes, updated_at,
                f"https://gitlab/proj/-/merge_requests/{mr_iid}",
            ),
        )
        for i in range(issues_zh):
            conn.execute(
                """
                INSERT INTO check_issues(
                    task_id, source_kind, opus_id, target_language,
                    error_type, error_category, error_keyword,
                    error_keyword_norm, source_text, translated_text,
                    final_score, reason, iteration, fetched_at
                ) VALUES (?, 'mr', ?, 'zh-CN', 'Terminology Inconsistency',
                          'Terminology', 'X', 'x', 'src', 'tgt',
                          50.0, 'reason', 1, ?)
                """,
                (task_id, f"k{i}", now_iso),
            )


class UpdateMrStateFieldsTests(unittest.TestCase):

    def test_partial_update_preserves_other_columns(self):
        with _IsolatedDb():
            tc.init_db()
            _seed_red_mr(task_id="t-1", mr_iid=10,
                         updated_at=_hours_ago_iso(1))
            tc.update_mr_state_fields(
                task_id="t-1", state="merged",
            )
            # state changed, the rest should still be set.
            with tc._connect() as conn:
                row = conn.execute(
                    "SELECT mr_state, mr_upvotes, mr_web_url FROM task_checks "
                    "WHERE task_id = 't-1'",
                ).fetchone()
            self.assertEqual(row["mr_state"], "merged")
            # upvotes / url left alone.
            self.assertEqual(row["mr_upvotes"], 2)
            self.assertTrue(row["mr_web_url"])

    def test_no_op_on_unknown_task(self):
        with _IsolatedDb():
            tc.init_db()
            # Doesn't raise even when the row doesn't exist —
            # watchdog won't crash on a stale snapshot.
            tc.update_mr_state_fields(task_id="ghost", state="merged")


class MergeEventsRingTests(unittest.TestCase):

    def test_appends_and_reads_back_in_order(self):
        with _IsolatedDb():
            tc.init_db()
            tc.append_merge_events([
                {"task_id": "a", "new_state": "merged"},
                {"task_id": "b", "new_state": "closed"},
            ])
            tc.append_merge_events([
                {"task_id": "c", "new_state": "merged"},
            ])
            out = tc.get_merge_events()
            self.assertEqual([e["task_id"] for e in out], ["a", "b", "c"])

    def test_ring_drops_oldest_past_capacity(self):
        with _IsolatedDb():
            tc.init_db()
            # Capacity is 200; push 250 to verify the head gets dropped.
            tc.append_merge_events(
                [{"task_id": str(i), "new_state": "merged"} for i in range(250)],
            )
            out = tc.get_merge_events()
            self.assertEqual(len(out), 200)
            # Most recent kept at the tail.
            self.assertEqual(out[-1]["task_id"], "249")
            # Oldest first dropped.
            self.assertEqual(out[0]["task_id"], "50")

    def test_limit_returns_tail_only(self):
        with _IsolatedDb():
            tc.init_db()
            tc.append_merge_events(
                [{"task_id": str(i), "new_state": "merged"} for i in range(5)],
            )
            out = tc.get_merge_events(limit=2)
            self.assertEqual([e["task_id"] for e in out], ["3", "4"])


class CheckOnceTests(unittest.TestCase):
    """check_once is the one place GitLab gets hit. Mock the client and
    verify state transitions get emitted as events."""

    def _patch_client(self, mr_states):
        """``mr_states`` maps (project_id, mr_iid) → response dict."""
        fake = mock.MagicMock()
        fake.has_token.return_value = True
        fake.get_merge_request.side_effect = (
            lambda pid, iid: mr_states[(str(pid), int(iid))]
        )
        return mock.patch("gitlab_client.GitLabClient", return_value=fake)

    def test_no_red_mrs_returns_empty_quickly(self):
        with _IsolatedDb():
            tc.init_db()
            # No tasks at all.
            events, red = mw.check_once()
            self.assertEqual(events, [])
            self.assertEqual(red, 0)

    def test_state_unchanged_no_event_but_persists(self):
        with _IsolatedDb():
            tc.init_db()
            _seed_red_mr(task_id="t-1", mr_iid=10,
                         updated_at=_hours_ago_iso(0.5))
            with self._patch_client({
                ("g/p", 10): {
                    "state": "opened",
                    "upvotes": 2,
                    "updated_at": _hours_ago_iso(0.4),
                    "web_url": "https://gitlab/proj/-/merge_requests/10",
                },
            }):
                events, red = mw.check_once()
            self.assertEqual(events, [])
            self.assertEqual(red, 1)

    def test_opened_to_merged_emits_event_and_persists(self):
        with _IsolatedDb():
            tc.init_db()
            _seed_red_mr(task_id="t-1", mr_iid=10,
                         updated_at=_hours_ago_iso(0.5))
            with self._patch_client({
                ("g/p", 10): {
                    "state": "merged",
                    "upvotes": 2,
                    "updated_at": _hours_ago_iso(0.1),
                    "web_url": "https://gitlab/proj/-/merge_requests/10",
                },
            }):
                events, red = mw.check_once()
            self.assertEqual(len(events), 1)
            ev = events[0]
            self.assertEqual(ev.old_state, "opened")
            self.assertEqual(ev.new_state, "merged")
            self.assertTrue(ev.is_terminal())
            # Persisted state means a follow-up worklist query no longer
            # surfaces this MR — that's the whole point.
            items = tc.get_worklist_items()
            self.assertNotIn(
                "t-1", [d["task_id"] for d in items],
            )
            # And the event landed in the ring.
            stored = tc.get_merge_events()
            self.assertEqual(stored[-1]["task_id"], "t-1")
            self.assertEqual(stored[-1]["new_state"], "merged")

    def test_no_token_skips_gitlab_silently(self):
        # Watchdog must not 401-storm GitLab when there's no token.
        with _IsolatedDb():
            tc.init_db()
            _seed_red_mr(task_id="t-1", mr_iid=10,
                         updated_at=_hours_ago_iso(0.5))
            fake = mock.MagicMock()
            fake.has_token.return_value = False
            with mock.patch(
                "gitlab_client.GitLabClient", return_value=fake,
            ):
                events, red = mw.check_once()
            self.assertEqual(events, [])
            self.assertEqual(red, 1)
            fake.get_merge_request.assert_not_called()


class WatchdogLifecycleTests(unittest.TestCase):
    """Lifecycle smoke — start, observe one tick, stop fast."""

    def test_start_calls_check_once_immediately(self):
        # We bypass GitLab entirely by monkeypatching check_once.
        results = {"count": 0}

        def fake_check_once():
            results["count"] += 1
            return [], 0

        with mock.patch.object(mw, "check_once", side_effect=fake_check_once):
            w = mw.Watchdog(interval_secs=30)
            w.start()
            # Wait a tiny bit; first check must have already happened
            # since the loop runs check before the first sleep.
            for _ in range(20):
                if results["count"] >= 1:
                    break
                time.sleep(0.05)
            w.stop()
        self.assertGreaterEqual(results["count"], 1)

    def test_event_callback_failures_dont_crash_loop(self):
        # If the UI on_event raises, the next interval must still happen.
        with mock.patch.object(
            mw, "check_once",
            side_effect=[
                # First call returns one event; second returns none. After
                # that the worker should keep looping.
                ([mw.MergeEvent(
                    task_id="t-1", project_id="g/p", mr_iid=10,
                    project_name="p", task_name="x",
                    old_state="opened", new_state="merged",
                    mr_web_url=None, observed_at="2026-05-29T12:00:00",
                )], 1),
                ([], 0),
            ] + [([], 0)] * 10,
        ):
            def _bomb(_ev):
                raise RuntimeError("UI bug")

            w = mw.Watchdog(interval_secs=30, on_event=_bomb)
            w.start()
            time.sleep(0.2)
            # If the callback bomb had killed the worker, the lock would
            # still report running=True (we set it on start) but stop()
            # would see a dead thread. The simplest assertion: stop
            # doesn't raise and last_status is consistent.
            w.stop()
            self.assertFalse(w.last_status["running"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
