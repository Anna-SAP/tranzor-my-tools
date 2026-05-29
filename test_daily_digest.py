"""Tests for daily_digest (PR-E).

The renderer is the entire surface area — we don't test the CLI
plumbing because that's just argparse + webbrowser, the failure modes
of which are uninteresting. We do test ``build_digest`` end-to-end
against an isolated SQLite so a regression in any upstream query gets
caught here too.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tranzor_checks as tc
import daily_digest as dd


class _IsolatedDb:
    def __enter__(self):
        self._tmp = tempfile.mkdtemp(prefix="digest-test-")
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


def _seed_mr(*, task_id, mr_iid, **overrides):
    """Tiny MR seeder; lifts the heavy fields out so the test reads."""
    import json
    now_iso = datetime.now(timezone.utc).isoformat()
    defaults = {
        "state": "opened", "upvotes": 2,
        "updated_at": now_iso,
        "labels": [], "project_name": "proj",
        "task_name": f"feature/{mr_iid}",
        "zh_issues": 1,
    }
    defaults.update(overrides)
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
                      ?, 'can_be_merged', 0, ?, 0, ?, ?)
            """,
            (
                task_id, defaults["project_name"], mr_iid,
                defaults["task_name"],
                defaults["zh_issues"], now_iso,
                json.dumps(defaults["labels"]),
                defaults["state"], defaults["upvotes"],
                defaults["updated_at"],
                f"https://gitlab/proj/-/merge_requests/{mr_iid}",
            ),
        )
        for i in range(defaults["zh_issues"]):
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


class RenderDigestHtmlTests(unittest.TestCase):
    """Pure render tests — no DB."""

    def test_includes_doctype_and_title(self):
        html_text = dd.render_digest_html(
            worklist_items=[], merge_events=[],
            review_summary={"reviewer": "lillian.ding",
                            "today": 0, "total": 0},
            generated_at="2026-05-29T09:00:00Z",
        )
        self.assertIn("<!DOCTYPE html>", html_text)
        self.assertIn("Tranzor Review Digest", html_text)
        self.assertIn("2026-05-29", html_text)

    def test_empty_worklist_shows_friendly_msg(self):
        html_text = dd.render_digest_html(
            worklist_items=[], merge_events=[],
            review_summary={"reviewer": "x", "today": 0, "total": 0},
            generated_at="2026-05-29T09:00:00Z",
        )
        self.assertIn("Nothing to review right now", html_text)

    def test_worklist_row_renders_with_link(self):
        item = {
            "task_id": "t-1", "project_name": "nova",
            "mr_iid": 42, "task_name": "feature/x",
            "merge_tier": "red", "merge_urgency": 9,
            "mr_state": "opened", "mr_web_url": "https://gitlab/x/42",
            "zh_issues": 3, "secondary_issues": 1, "other_issues": 0,
            "reviewed_count": 1, "total_issue_count": 4,
            "unregistered_term_count": 2,
        }
        html_text = dd.render_digest_html(
            worklist_items=[item], merge_events=[],
            review_summary={"reviewer": "x", "today": 0, "total": 0},
            generated_at="2026-05-29T09:00:00Z",
        )
        # Tier dot, task hyperlink, the 🆕 count column.
        self.assertIn("🔴", html_text)
        self.assertIn('href="https://gitlab/x/42"', html_text)
        # 1/4 reviewed.
        self.assertIn("1/4", html_text)
        # 🆕 count cell.
        self.assertIn("<td class=\"num\">2</td>", html_text)
        # tier class on the row for styling.
        self.assertIn("tier-red", html_text)

    def test_events_section_omitted_when_empty(self):
        html_text = dd.render_digest_html(
            worklist_items=[], merge_events=[],
            review_summary={"reviewer": "x", "today": 0, "total": 0},
            generated_at="2026-05-29T09:00:00Z",
        )
        self.assertNotIn("Recent merge events", html_text)

    def test_events_section_renders_transitions(self):
        events = [{
            "task_id": "t-1", "project_name": "nova",
            "mr_iid": 42, "task_name": "feature/x",
            "old_state": "opened", "new_state": "merged",
            "mr_web_url": "https://gitlab/x/42",
            "observed_at": "2026-05-29T11:55:00Z",
        }]
        html_text = dd.render_digest_html(
            worklist_items=[], merge_events=events,
            review_summary={"reviewer": "x", "today": 0, "total": 0},
            generated_at="2026-05-29T12:00:00Z",
        )
        self.assertIn("Recent merge events", html_text)
        self.assertIn("opened → merged", html_text)
        self.assertIn("event-merged", html_text)

    def test_html_escapes_user_content(self):
        # Defensive: a malicious task_name with HTML chars must NOT
        # break out of the table cell. Cheap XSS sanity for a local
        # report, but still worth pinning.
        item = {
            "task_id": "t-1",
            "project_name": "nova<script>",
            "mr_iid": 1, "task_name": "<b>boom</b>",
            "merge_tier": "amber",
            "mr_web_url": "", "mr_state": "opened",
            "zh_issues": 0, "secondary_issues": 0, "other_issues": 0,
            "reviewed_count": 0, "total_issue_count": 0,
            "unregistered_term_count": 0,
        }
        html_text = dd.render_digest_html(
            worklist_items=[item], merge_events=[],
            review_summary={"reviewer": "x", "today": 0, "total": 0},
            generated_at="2026-05-29T09:00:00Z",
        )
        self.assertNotIn("<script>", html_text)
        self.assertNotIn("<b>boom</b>", html_text)
        self.assertIn("&lt;script&gt;", html_text)
        self.assertIn("&lt;b&gt;boom&lt;/b&gt;", html_text)


class BuildDigestEndToEndTests(unittest.TestCase):

    def test_builds_file_with_real_pipeline(self):
        with _IsolatedDb():
            tc.init_db()
            _seed_mr(task_id="t-1", mr_iid=10, zh_issues=2)
            tc.append_merge_events([{
                "task_id": "t-old", "project_name": "ax",
                "mr_iid": 9, "task_name": "feature/y",
                "old_state": "opened", "new_state": "merged",
                "mr_web_url": "https://gitlab/ax/9",
                "observed_at": "2026-05-29T08:00:00Z",
            }])
            out = os.path.join(self._tmpdir(), "digest.html")
            written = dd.build_digest(
                out_path=out, known_terms=False,
            )
            self.assertTrue(os.path.exists(written))
            with open(written, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("Daily Digest", text)
            # The MR row landed in the worklist section.
            self.assertIn("feature/10", text)
            # The historical merge event landed in the events section.
            self.assertIn("feature/y", text)
            self.assertIn("opened → merged", text)

    def _tmpdir(self):
        d = tempfile.mkdtemp(prefix="digest-out-")
        self.addCleanup(self._rm, d)
        return d

    @staticmethod
    def _rm(d):
        for name in os.listdir(d):
            try:
                os.remove(os.path.join(d, name))
            except OSError:
                pass
        try:
            os.rmdir(d)
        except OSError:
            pass


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
