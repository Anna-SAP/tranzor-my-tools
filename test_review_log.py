"""Unit tests for the review_log API (PR-B) — mark/unmark + worklist
integration. Cross-MR dedup is the central behaviour: one Mark must
make the same (opus_id, lang, text) appear reviewed everywhere.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tranzor_checks as tc


NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def _hours_ago_iso(h):
    return (NOW - timedelta(hours=h)).isoformat().replace("+00:00", "Z")


class _IsolatedDb:
    def __enter__(self):
        self._tmp = tempfile.mkdtemp(prefix="reviewlog-test-")
        self.path = os.path.join(self._tmp, "checks_index.db")
        self._orig = tc._default_db_path
        tc._default_db_path = lambda: self.path
        os.environ["TRANZOR_REVIEWER"] = "lillian.ding"
        return self

    def __exit__(self, *exc):
        os.environ.pop("TRANZOR_REVIEWER", None)
        tc._default_db_path = self._orig
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(self._tmp)
        except OSError:
            pass


class HashAndDefaultReviewerTests(unittest.TestCase):

    def test_empty_and_none_share_hash(self):
        # All "no translation" cases collapse to one bucket so the worklist
        # doesn't double-count empty rows as separate review items.
        self.assertEqual(tc._hash_translation(None), tc._hash_translation(""))
        self.assertEqual(tc._hash_translation(""), "empty")

    def test_identical_text_same_hash(self):
        self.assertEqual(
            tc._hash_translation("Hallo Welt"),
            tc._hash_translation("Hallo Welt"),
        )

    def test_different_text_different_hash(self):
        self.assertNotEqual(
            tc._hash_translation("Hallo Welt"),
            tc._hash_translation("Hallo Welt!"),
        )

    def test_default_reviewer_uses_env_when_set(self):
        os.environ["TRANZOR_REVIEWER"] = "lillian.ding"
        try:
            self.assertEqual(tc._default_reviewer(), "lillian.ding")
        finally:
            os.environ.pop("TRANZOR_REVIEWER", None)

    def test_default_reviewer_falls_back_to_user(self):
        os.environ.pop("TRANZOR_REVIEWER", None)
        # Just sanity-check we return a non-empty string — the actual
        # value depends on the test environment.
        self.assertTrue(tc._default_reviewer())


class MarkUnmarkTests(unittest.TestCase):

    def test_mark_creates_row_and_get_summary_increments(self):
        with _IsolatedDb():
            tc.init_db()
            tc.mark_reviewed(
                opus_id="OK_BTN", target_language="zh-CN",
                translated_text="确定",
            )
            s = tc.get_review_summary()
            self.assertEqual(s["reviewer"], "lillian.ding")
            self.assertEqual(s["total"], 1)
            self.assertEqual(s["today"], 1)

    def test_mark_is_idempotent(self):
        # Marking the same row twice mustn't double-count or raise.
        with _IsolatedDb():
            tc.init_db()
            for _ in range(3):
                tc.mark_reviewed(
                    opus_id="OK_BTN", target_language="zh-CN",
                    translated_text="确定",
                )
            self.assertEqual(tc.get_review_summary()["total"], 1)

    def test_unmark_returns_true_only_when_row_existed(self):
        with _IsolatedDb():
            tc.init_db()
            # Nothing to delete yet.
            self.assertFalse(tc.unmark_reviewed(
                opus_id="X", target_language="zh-CN", translated_text="x",
            ))
            tc.mark_reviewed(
                opus_id="X", target_language="zh-CN", translated_text="x",
            )
            self.assertTrue(tc.unmark_reviewed(
                opus_id="X", target_language="zh-CN", translated_text="x",
            ))
            self.assertEqual(tc.get_review_summary()["total"], 0)

    def test_different_reviewers_dont_collide(self):
        # Two LLs reviewing the same string each keep their own state —
        # important for future multi-reviewer scenarios.
        with _IsolatedDb():
            tc.init_db()
            tc.mark_reviewed(
                opus_id="A", target_language="zh-CN", translated_text="x",
                reviewer="alice",
            )
            tc.mark_reviewed(
                opus_id="A", target_language="zh-CN", translated_text="x",
                reviewer="bob",
            )
            self.assertEqual(
                tc.get_review_summary(reviewer="alice")["total"], 1)
            self.assertEqual(
                tc.get_review_summary(reviewer="bob")["total"], 1)

    def test_changing_translated_text_invalidates_review(self):
        # If translators edit the string after Lillian reviewed it, the
        # new text must NOT be auto-marked reviewed — it's a fresh string.
        with _IsolatedDb():
            tc.init_db()
            tc.mark_reviewed(
                opus_id="OK_BTN", target_language="zh-CN",
                translated_text="确定",
            )
            # Same opus_id + lang but different translated_text → fresh
            # review state. The original mark is preserved (history),
            # the new text shows as unreviewed.
            keys = _reviewed_keys_for(tc._default_reviewer())
            self.assertIn(
                ("OK_BTN", "zh-CN", tc._hash_translation("确定")), keys,
            )
            self.assertNotIn(
                ("OK_BTN", "zh-CN", tc._hash_translation("好的")), keys,
            )


def _reviewed_keys_for(reviewer):
    """Helper: pull all (opus_id, lang, hash) tuples a reviewer has marked."""
    with tc._connect() as conn:
        rows = conn.execute(
            "SELECT opus_id, target_language, text_hash "
            "FROM review_log WHERE reviewer = ?", (reviewer,),
        ).fetchall()
    return {(r["opus_id"], r["target_language"], r["text_hash"]) for r in rows}


def _seed_mr(*, task_id, mr_iid, issues, state="opened",
             upvotes=2, updated_at=None, labels=None):
    """Reuse the same shape as test_review_worklist's seeder."""
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
            ) VALUES (?, 'mr', 'g/p', 'proj', ?, ?, 'completed', 80.0,
                      ?, 100, '2026-05-29T00:00:00', ?, ?,
                      ?, 'can_be_merged', 0, ?, 0, ?, ?)
            """,
            (
                task_id, mr_iid, f"feature/{mr_iid}",
                len(issues), now_iso,
                json.dumps(labels or []),
                state, upvotes, updated_at,
                f"https://gitlab/proj/-/merge_requests/{mr_iid}",
            ),
        )
        for idx, (opus_id, lang, text) in enumerate(issues):
            conn.execute(
                """
                INSERT INTO check_issues(
                    task_id, source_kind, opus_id, target_language,
                    error_type, error_category, error_keyword,
                    error_keyword_norm, source_text, translated_text,
                    final_score, reason, iteration, fetched_at
                ) VALUES (?, 'mr', ?, ?, 'Terminology Inconsistency',
                          'Terminology', ?, ?, ?, ?,
                          50.0, 'reason', 1, ?)
                """,
                (task_id, opus_id, lang, opus_id, opus_id.lower(),
                 f"src-{opus_id}", text, now_iso),
            )


class MarkTaskReviewedTests(unittest.TestCase):

    def test_marks_every_distinct_issue_in_one_pass(self):
        with _IsolatedDb():
            tc.init_db()
            _seed_mr(
                task_id="t-1", mr_iid=100,
                issues=[
                    ("OK", "zh-CN", "确定"),
                    ("OK", "fr-FR", "OK"),
                    ("CANCEL", "zh-CN", "取消"),
                ],
                updated_at=_hours_ago_iso(2),
            )
            n = tc.mark_task_reviewed("t-1")
            self.assertEqual(n, 3)
            keys = _reviewed_keys_for(tc._default_reviewer())
            self.assertEqual(len(keys), 3)

    def test_no_op_when_task_has_no_issues(self):
        # Clean MR (0 issues) → mark_task_reviewed is a no-op, NOT an error.
        with _IsolatedDb():
            tc.init_db()
            _seed_mr(
                task_id="t-clean", mr_iid=200, issues=[],
                updated_at=_hours_ago_iso(2),
            )
            self.assertEqual(tc.mark_task_reviewed("t-clean"), 0)

    def test_unmark_task_reviewed_drops_them_all(self):
        with _IsolatedDb():
            tc.init_db()
            _seed_mr(
                task_id="t-1", mr_iid=100,
                issues=[("OK", "zh-CN", "确定"), ("CANCEL", "zh-CN", "取消")],
                updated_at=_hours_ago_iso(2),
            )
            tc.mark_task_reviewed("t-1")
            self.assertEqual(tc.get_review_summary()["total"], 2)
            removed = tc.unmark_task_reviewed("t-1")
            self.assertEqual(removed, 2)
            self.assertEqual(tc.get_review_summary()["total"], 0)


class CrossMrDedupTests(unittest.TestCase):
    """The headline PR-B behaviour: marking once in MR A makes the same
    (opus_id, lang, translated_text) count as reviewed in MR B too."""

    def test_same_string_in_two_mrs_dedups(self):
        with _IsolatedDb():
            tc.init_db()
            _seed_mr(
                task_id="t-A", mr_iid=100,
                issues=[("OK_BTN", "zh-CN", "确定")],
                upvotes=2, updated_at=_hours_ago_iso(1),
            )
            _seed_mr(
                task_id="t-B", mr_iid=200,
                # Same opus_id + lang + text → cross-MR dedup target.
                issues=[("OK_BTN", "zh-CN", "确定")],
                upvotes=2, updated_at=_hours_ago_iso(1),
            )
            tc.mark_task_reviewed("t-A")

            # Inspect state with the hide-filter relaxed.
            items_all = tc.get_worklist_items(
                include_fully_reviewed=True, now_utc=NOW,
            )
            t_b = next(d for d in items_all if d["task_id"] == "t-B")
            # MR B's only issue shares the (opus_id, lang, hash) Lillian
            # already reviewed in MR A → reviewed_count = 1, MR B is
            # fully_reviewed.
            self.assertTrue(t_b["fully_reviewed"])
            self.assertEqual(t_b["reviewed_count"], 1)
            # Default Worklist view hides it — Lillian doesn't need to
            # see "already done" rows on her morning queue.
            items_default = tc.get_worklist_items(now_utc=NOW)
            self.assertNotIn(
                "t-B", [d["task_id"] for d in items_default],
            )

    def test_different_text_does_not_dedup(self):
        # The dedup key includes translated_text — if a translator edits
        # the string between MR A and MR B, MR B is fresh.
        with _IsolatedDb():
            tc.init_db()
            _seed_mr(
                task_id="t-A", mr_iid=100,
                issues=[("OK_BTN", "zh-CN", "确定")],
                upvotes=2, updated_at=_hours_ago_iso(1),
            )
            _seed_mr(
                task_id="t-B", mr_iid=200,
                # Same opus_id + lang, BUT different translated_text.
                issues=[("OK_BTN", "zh-CN", "好的")],
                upvotes=2, updated_at=_hours_ago_iso(1),
            )
            tc.mark_task_reviewed("t-A")

            items = tc.get_worklist_items(now_utc=NOW)
            self.assertIn("t-B", [d["task_id"] for d in items])

    def test_partial_dedup_lowers_priority_but_keeps_visible(self):
        with _IsolatedDb():
            tc.init_db()
            # MR A has 4 zh issues; we'll mark 2 as reviewed via MR P.
            _seed_mr(
                task_id="t-shared", mr_iid=99,
                issues=[
                    ("A1", "zh-CN", "a1"),
                    ("A2", "zh-CN", "a2"),
                ],
                upvotes=2, updated_at=_hours_ago_iso(2),
            )
            _seed_mr(
                task_id="t-target", mr_iid=100,
                issues=[
                    # Shared with t-shared (counts as already reviewed)
                    ("A1", "zh-CN", "a1"),
                    ("A2", "zh-CN", "a2"),
                    # Unique to this MR
                    ("B1", "zh-CN", "b1"),
                    ("B2", "zh-CN", "b2"),
                ],
                upvotes=2, updated_at=_hours_ago_iso(2),
            )
            tc.mark_task_reviewed("t-shared")

            items = tc.get_worklist_items(now_utc=NOW)
            t = next(d for d in items if d["task_id"] == "t-target")
            self.assertEqual(t["reviewed_count"], 2)
            self.assertEqual(t["unreviewed_count"], 2)
            self.assertFalse(t["fully_reviewed"])
            # Still visible — only 2 of 4 reviewed.
            self.assertIn("t-target", [d["task_id"] for d in items])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
