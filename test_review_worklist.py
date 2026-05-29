"""Unit tests for the Review Worklist data layer (PR-A).

Covers:
  - :func:`tranzor_checks.compute_merge_urgency` — the pure scoring
    function. Every branch is anchored so refactors don't silently
    re-tune the Lillian-facing tier thresholds.
  - :func:`tranzor_checks.get_worklist_items` — the SQL aggregation
    that drives the GUI tab.

The compute function is deterministic given ``now_utc``; tests inject a
fixed timestamp so age-bucketing assertions don't drift over time.
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


class ComputeMergeUrgencyTests(unittest.TestCase):
    """The scoring contract — every branch of ``compute_merge_urgency``."""

    def _call(self, **overrides):
        defaults = dict(
            state="opened",
            merge_status="can_be_merged",
            draft=0,
            upvotes=0,
            updated_at_iso=_hours_ago_iso(48),
            labels=[],
            now_utc=NOW,
        )
        defaults.update(overrides)
        return tc.compute_merge_urgency(**defaults)

    # ---- state branch ----
    def test_merged_is_grey(self):
        score, tier = self._call(state="merged")
        self.assertEqual((score, tier), (0, "grey"))

    def test_closed_is_grey(self):
        score, tier = self._call(state="closed")
        self.assertEqual((score, tier), (0, "grey"))

    def test_locked_is_grey(self):
        score, tier = self._call(state="locked")
        self.assertEqual((score, tier), (0, "grey"))

    def test_unknown_state_uses_unknown_tier_not_grey(self):
        # GitLab fetch never ran (old sync / no token) → state=None
        # MUST still surface in the Worklist (otherwise the entire
        # default view goes blank for users who haven't re-synced yet).
        # The tier is "unknown" so the GUI can paint it distinctly
        # and hint "run Sync" without hiding the row.
        score, tier = self._call(state=None)
        self.assertEqual(tier, "unknown")
        # The score itself still reflects every other signal — we
        # only stripped the tier classification.
        self.assertGreater(score, 0)

    def test_unknown_state_still_grey_when_skip_translate_label(self):
        # Skip-translate beats unknown — explicit "don't review" wins
        # over "don't know yet".
        _, tier = self._call(state=None, labels=["skip-translate"])
        self.assertEqual(tier, "grey")

    def test_unknown_state_still_grey_when_merged(self):
        # Sanity: even unknown can become grey if upstream tells us
        # the MR is already merged.
        _, tier = self._call(state="merged")
        self.assertEqual(tier, "grey")

    # ---- skip-translate label is sticky ----
    def test_skip_translate_label_forces_grey(self):
        score, tier = self._call(
            labels=["skip-translate"], upvotes=5,
            updated_at_iso=_hours_ago_iso(0),
        )
        # Even with full upvotes + just-now activity, skip-translate
        # MRs must NEVER show in the worklist.
        self.assertEqual((score, tier), (0, "grey"))

    def test_skip_translate_label_case_insensitive(self):
        # GitLab labels are user-defined strings; if someone capitalises
        # it the worklist still must hide the MR.
        score, tier = self._call(labels=["Skip-Translate"])
        self.assertEqual(tier, "grey")

    # ---- draft penalty ----
    def test_draft_penalty(self):
        a, _ = self._call(draft=0)
        b, _ = self._call(draft=1)
        self.assertEqual(a - b, 5)

    # ---- upvotes ----
    def test_each_upvote_adds_1_5_capped_at_3(self):
        s0, _ = self._call(upvotes=0)
        s1, _ = self._call(upvotes=1)
        s3, _ = self._call(upvotes=3)
        s9, _ = self._call(upvotes=9)
        # int(round(...)) rounds .5 to even — we just need monotonic.
        self.assertLess(s0, s1)
        self.assertLess(s1, s3)
        # Cap at 3 upvotes — extra upvotes don't keep pushing higher.
        self.assertEqual(s3, s9)

    def test_negative_or_none_upvotes_ignored(self):
        s_none, _ = self._call(upvotes=None)
        s_neg, _ = self._call(upvotes=-5)
        s_zero, _ = self._call(upvotes=0)
        self.assertEqual(s_none, s_zero)
        self.assertEqual(s_neg, s_zero)

    # ---- recency buckets ----
    def test_just_now_gets_max_recency_boost(self):
        s, tier = self._call(updated_at_iso=_hours_ago_iso(0.5), upvotes=2)
        # base 5 + 2 upvotes * 1.5 = 5 + 3 = 8; +3 recency = 11 → clamp 10
        self.assertEqual(s, 10)
        self.assertEqual(tier, "red")

    def test_six_hour_recency_lower_than_one_hour(self):
        s1, _ = self._call(updated_at_iso=_hours_ago_iso(1))
        s6, _ = self._call(updated_at_iso=_hours_ago_iso(5))
        self.assertGreater(s1, s6)

    def test_week_old_gets_penalty(self):
        # Stale MRs (week-plus) shouldn't keep showing red just because
        # they're opened — most are forgotten / abandoned.
        s_fresh, _ = self._call(updated_at_iso=_hours_ago_iso(12))
        s_stale, _ = self._call(updated_at_iso=_hours_ago_iso(24 * 14))
        self.assertGreater(s_fresh, s_stale)

    def test_missing_updated_at_is_neutral(self):
        # GitLab didn't return updated_at → don't penalise; don't boost.
        s_with = self._call(updated_at_iso=_hours_ago_iso(48))
        s_without = self._call(updated_at_iso=None)
        self.assertEqual(s_with, s_without)

    def test_malformed_updated_at_does_not_raise(self):
        # Defensive: any bizarre string from upstream must not crash the
        # query — fall back to "neutral" same as missing.
        score, _ = self._call(updated_at_iso="not-a-date")
        self.assertEqual(
            score, self._call(updated_at_iso=None)[0]
        )

    # ---- tier boundaries ----
    def test_red_tier_at_8(self):
        # Build a case that lands exactly at 8: base 5 + 3 recency = 8.
        _, tier = self._call(
            upvotes=0, draft=0,
            updated_at_iso=_hours_ago_iso(0.5),
        )
        self.assertEqual(tier, "red")

    def test_amber_tier_at_4(self):
        # base 5 + nothing else → 5 → amber.
        _, tier = self._call(
            updated_at_iso=_hours_ago_iso(48),
        )
        self.assertEqual(tier, "amber")

    def test_green_tier_below_4(self):
        # Draft + stale → low score.
        _, tier = self._call(
            draft=1,
            updated_at_iso=_hours_ago_iso(24 * 14),
        )
        self.assertEqual(tier, "green")


# ---------------------------------------------------------------------------
# get_worklist_items — end-to-end with an isolated DB.
# ---------------------------------------------------------------------------
class _IsolatedDb:
    def __enter__(self):
        self._tmp = tempfile.mkdtemp(prefix="worklist-test-")
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


def _seed_mr_task(
    *,
    task_id,
    mr_iid,
    state="opened",
    upvotes=0,
    updated_at=None,
    labels=None,
    draft=0,
    issues=None,
    project_name="proj",
):
    """Insert one MR ``task_checks`` row + matching ``check_issues``.

    Returns the task_id so tests can refer back to it.
    """
    import json
    from datetime import datetime, timezone
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
            ) VALUES (?, 'mr', 'g/p', ?, ?, ?, 'completed', 80.0,
                      ?, 100, '2026-05-29T00:00:00', ?, ?,
                      ?, 'can_be_merged', ?, ?, 0, ?, ?)
            """,
            (
                task_id, project_name, mr_iid, f"feature/{mr_iid}",
                len(issues or []), now_iso,
                json.dumps(labels or []),
                state, draft, upvotes, updated_at,
                f"https://gitlab/proj/-/merge_requests/{mr_iid}",
            ),
        )
        for lang, n in (issues or {}).items():
            for i in range(n):
                conn.execute(
                    """
                    INSERT INTO check_issues(
                        task_id, source_kind, opus_id, target_language,
                        error_type, error_category, error_keyword,
                        error_keyword_norm, source_text, translated_text,
                        final_score, reason, iteration, fetched_at
                    ) VALUES (?, 'mr', ?, ?, 'Terminology Inconsistency',
                              'Terminology', 'X', 'x', 'src', 'tgt',
                              50.0, 'reason', 1, ?)
                    """,
                    (task_id, f"k{i}", lang, now_iso),
                )
    return task_id


class GetWorklistItemsTests(unittest.TestCase):

    def test_empty_db_returns_empty_list(self):
        with _IsolatedDb():
            tc.init_db()
            self.assertEqual(tc.get_worklist_items(), [])

    def test_red_mr_ranks_above_amber(self):
        with _IsolatedDb():
            tc.init_db()
            _seed_mr_task(
                task_id="t-amber", mr_iid=100,
                upvotes=0, updated_at=_hours_ago_iso(48),
                issues={"zh-CN": 1},
            )
            _seed_mr_task(
                task_id="t-red", mr_iid=200,
                upvotes=2, updated_at=_hours_ago_iso(0.5),
                issues={"zh-CN": 1},
            )
            items = tc.get_worklist_items(now_utc=NOW)
            self.assertEqual(items[0]["task_id"], "t-red")
            self.assertEqual(items[1]["task_id"], "t-amber")

    def test_unknown_state_mr_visible_in_default_view(self):
        # This is the regression for "Worklist blank after install":
        # 7848 cached MR tasks had mr_state=NULL because the GitLab
        # state columns are PR-A additions, so the old sync never
        # populated them. The default view MUST show them so Lillian
        # sees something even before she re-syncs.
        with _IsolatedDb():
            tc.init_db()
            # Seed an MR without any GitLab state at all — the
            # seeder helper passes None defaults.
            _seed_mr_task(
                task_id="t-stale", mr_iid=42,
                state=None, upvotes=None, updated_at=None,
                issues={"zh-CN": 2},
            )
            items = tc.get_worklist_items(now_utc=NOW)
            self.assertEqual(
                [d["task_id"] for d in items], ["t-stale"],
            )
            self.assertEqual(items[0]["merge_tier"], "unknown")

    def test_skip_translate_label_filtered_when_include_grey_false(self):
        with _IsolatedDb():
            tc.init_db()
            _seed_mr_task(
                task_id="t-skip", mr_iid=300,
                labels=["skip-translate"],
                upvotes=3, updated_at=_hours_ago_iso(0.5),
                issues={"zh-CN": 5},
            )
            items = tc.get_worklist_items(now_utc=NOW)
            self.assertEqual(items, [])

    def test_merged_filtered_when_include_grey_false(self):
        with _IsolatedDb():
            tc.init_db()
            _seed_mr_task(
                task_id="t-merged", mr_iid=400,
                state="merged",
                issues={"zh-CN": 3},
            )
            items = tc.get_worklist_items(now_utc=NOW)
            self.assertEqual(items, [])

    def test_zh_issues_outweigh_other_issues_at_same_tier(self):
        with _IsolatedDb():
            tc.init_db()
            _seed_mr_task(
                task_id="t-zh", mr_iid=500,
                upvotes=1, updated_at=_hours_ago_iso(48),
                issues={"zh-CN": 1},
            )
            _seed_mr_task(
                task_id="t-other", mr_iid=600,
                upvotes=1, updated_at=_hours_ago_iso(48),
                issues={"ja-JP": 10},
            )
            items = tc.get_worklist_items(now_utc=NOW)
            # Same tier, but zh_issues counts more heavily (×3) than
            # the "other" bucket (×0.3). The zh row must come first.
            self.assertEqual(items[0]["task_id"], "t-zh")

    def test_lang_breakdown_populated_correctly(self):
        with _IsolatedDb():
            tc.init_db()
            _seed_mr_task(
                task_id="t-1", mr_iid=700,
                upvotes=0, updated_at=_hours_ago_iso(48),
                issues={"zh-CN": 2, "fr-FR": 3, "ja-JP": 4},
            )
            items = tc.get_worklist_items(now_utc=NOW)
            self.assertEqual(items[0]["zh_issues"], 2)
            self.assertEqual(items[0]["secondary_issues"], 3)
            self.assertEqual(items[0]["other_issues"], 4)

    def test_unregistered_terms_surface_when_glossary_provided(self):
        # PR-C: When the caller hands in a known-terms set, the worklist
        # row's source_text gets scanned for "probably product/feature"
        # names not in the glossary.
        with _IsolatedDb():
            tc.init_db()
            _seed_mr_task(
                task_id="t-pc", mr_iid=900,
                upvotes=1, updated_at=_hours_ago_iso(2),
                issues={"zh-CN": 1},
            )
            # Patch one source_text on the seeded issue so we can assert
            # on a stable extraction. The seeder sets source_text="src".
            with tc._connect() as conn:
                conn.execute(
                    "UPDATE check_issues SET source_text = ? "
                    "WHERE task_id = ?",
                    ("Open LiveReports for RingCentral users.", "t-pc"),
                )
            # RingCentral known → only LiveReports surfaces.
            items = tc.get_worklist_items(
                now_utc=NOW,
                known_term_names_lower=frozenset({"ringcentral"}),
            )
            self.assertEqual(items[0]["task_id"], "t-pc")
            self.assertIn("LiveReports", items[0]["unregistered_terms"])
            self.assertNotIn("RingCentral", items[0]["unregistered_terms"])
            self.assertEqual(items[0]["unregistered_term_count"], 1)

    def test_unregistered_terms_empty_when_glossary_not_provided(self):
        # No glossary handed in → no extraction work, fields stay empty.
        # This is what protects existing callers (PR-A / PR-B tests, the
        # old GUI path) from suddenly seeing 🆕 noise.
        with _IsolatedDb():
            tc.init_db()
            _seed_mr_task(
                task_id="t-nopc", mr_iid=901,
                upvotes=1, updated_at=_hours_ago_iso(2),
                issues={"zh-CN": 1},
            )
            items = tc.get_worklist_items(now_utc=NOW)
            self.assertEqual(items[0]["unregistered_terms"], [])
            self.assertEqual(items[0]["unregistered_term_count"], 0)

    def test_scan_and_legacy_tasks_excluded(self):
        # Worklist is MR-only; the legacy / scan tabs own their own
        # entry points and showing them here would just dilute focus.
        with _IsolatedDb():
            tc.init_db()
            _seed_mr_task(
                task_id="t-mr", mr_iid=800,
                upvotes=2, updated_at=_hours_ago_iso(1),
                issues={"zh-CN": 1},
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            with tc._connect() as conn:
                conn.execute(
                    "INSERT INTO task_checks(task_id, source_kind, "
                    "fetched_at, mr_state) VALUES ('t-legacy', 'file', "
                    "?, 'opened')", (now_iso,),
                )
                conn.execute(
                    "INSERT INTO task_checks(task_id, source_kind, "
                    "fetched_at, mr_state) VALUES ('t-scan', 'scan', "
                    "?, 'opened')", (now_iso,),
                )
            items = tc.get_worklist_items(now_utc=NOW)
            self.assertEqual([d["task_id"] for d in items], ["t-mr"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
