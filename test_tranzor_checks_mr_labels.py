"""Regression tests for the ``mr_labels`` column in ``task_checks``.

Locks down:
- the ALTER TABLE migration is idempotent (old DB without column upgrades cleanly)
- ``_persist_task_results`` writes the JSON shape we expect
- the COALESCE-on-conflict rule preserves a previously good labels value
  when a subsequent sync fails to fetch labels (returns None)
- ``get_aggregated_issues`` parses ``mr_labels`` back into a ``list[str]``
  on ``latest_mr_labels``

Pure SQLite + Python; no HTTP, no GitLab.

Run:  python -m unittest test_tranzor_checks_mr_labels
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tranzor_checks as tc


class _IsolatedDb:
    """Context manager that swaps ``_default_db_path`` to a per-test temp file
    so production callers (``get_aggregated_issues`` etc.) hit our DB.

    Uses TemporaryDirectory rather than NamedTemporaryFile because SQLite
    WAL leaves ``*.db-shm`` / ``*.db-wal`` sibling files that Windows
    refuses to delete while another handle still holds them — the directory
    cleanup tolerates this where ``os.unlink`` would crash.
    """

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "checks_index.db")
        self._patch = mock.patch.object(
            tc, "_default_db_path", return_value=self.path,
        )
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        try:
            self._tmp.cleanup()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------
class MigrationTests(unittest.TestCase):

    def test_init_db_creates_mr_labels_column_from_scratch(self):
        with _IsolatedDb() as db:
            tc.init_db()
            with sqlite3.connect(db.path) as conn:
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(task_checks)"
                ).fetchall()]
            self.assertIn("mr_labels", cols)

    def test_init_db_alters_legacy_table_missing_mr_labels(self):
        """Simulate the upgrade path: a DB pre-populated with the legacy
        schema (no ``mr_labels`` column). init_db must add the column and
        preserve existing rows; a second call must be a no-op."""
        with _IsolatedDb() as db:
            with sqlite3.connect(db.path) as conn:
                conn.executescript("""
                    CREATE TABLE task_checks (
                        task_id          TEXT NOT NULL,
                        source_kind      TEXT NOT NULL,
                        project_id       TEXT,
                        project_name     TEXT,
                        mr_iid           INTEGER,
                        task_name        TEXT,
                        task_status      TEXT,
                        final_score_avg  REAL,
                        total_issues     INTEGER NOT NULL DEFAULT 0,
                        total_rows       INTEGER NOT NULL DEFAULT 0,
                        task_created_at  TEXT,
                        fetched_at       TEXT NOT NULL,
                        PRIMARY KEY (task_id, source_kind)
                    );
                """)
                conn.execute(
                    "INSERT INTO task_checks(task_id, source_kind, "
                    "fetched_at) VALUES('x', 'mr', '2026-01-01')"
                )
                conn.commit()

            # 1st migration adds the column.
            tc.init_db()
            with sqlite3.connect(db.path) as conn:
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(task_checks)"
                ).fetchall()]
                self.assertIn("mr_labels", cols)
                row = conn.execute(
                    "SELECT mr_labels FROM task_checks WHERE task_id='x'"
                ).fetchone()
                self.assertIsNone(row[0])  # legacy row gets NULL labels

            # 2nd call is idempotent.
            tc.init_db()


# ---------------------------------------------------------------------------
# _persist_task_results + the COALESCE rule
# ---------------------------------------------------------------------------
def _task():
    return {
        "task_id": "t-1",
        "task_name": "feature/x",
        "project_id": "group/proj",
        "merge_request_iid": 1066,
        "created_at": "2026-05-01T10:00:00",
    }


class PersistMrLabelsTests(unittest.TestCase):

    def test_writes_labels_as_json(self):
        with _IsolatedDb() as db:
            tc.init_db()
            with tc._connect() as conn:
                tc._persist_task_results(
                    conn, task=_task(), source_kind="mr",
                    translations=[],
                    mr_labels=["skip-translate", "needs-review"],
                )
            with sqlite3.connect(db.path) as conn:
                row = conn.execute(
                    "SELECT mr_labels FROM task_checks WHERE task_id='t-1'"
                ).fetchone()
            self.assertEqual(
                json.loads(row[0]),
                ["skip-translate", "needs-review"],
            )

    def test_empty_list_is_distinguishable_from_unknown(self):
        with _IsolatedDb() as db:
            tc.init_db()
            with tc._connect() as conn:
                tc._persist_task_results(
                    conn, task=_task(), source_kind="mr",
                    translations=[], mr_labels=[],
                )
            with sqlite3.connect(db.path) as conn:
                row = conn.execute(
                    "SELECT mr_labels FROM task_checks WHERE task_id='t-1'"
                ).fetchone()
            self.assertEqual(row[0], "[]")

    def test_none_does_not_clobber_previous_value(self):
        """The point of COALESCE in the ON CONFLICT clause: if a later sync
        fails to fetch labels (returns None), keep the previously cached
        value rather than reset to NULL."""
        with _IsolatedDb() as db:
            tc.init_db()
            with tc._connect() as conn:
                # 1st sync: labels successfully fetched
                tc._persist_task_results(
                    conn, task=_task(), source_kind="mr",
                    translations=[],
                    mr_labels=["skip-translate"],
                )
                # 2nd sync: GitLab API was unavailable; fetcher returned None
                tc._persist_task_results(
                    conn, task=_task(), source_kind="mr",
                    translations=[], mr_labels=None,
                )
            with sqlite3.connect(db.path) as conn:
                row = conn.execute(
                    "SELECT mr_labels FROM task_checks WHERE task_id='t-1'"
                ).fetchone()
            self.assertEqual(json.loads(row[0]), ["skip-translate"])

    def test_explicit_empty_list_clobbers_previous_value(self):
        """Conversely: a successful response with [] (someone removed the
        skip-translate label) DOES clear the cached labels — UI must not
        keep showing 🚫 forever."""
        with _IsolatedDb() as db:
            tc.init_db()
            with tc._connect() as conn:
                tc._persist_task_results(
                    conn, task=_task(), source_kind="mr",
                    translations=[], mr_labels=["skip-translate"],
                )
                tc._persist_task_results(
                    conn, task=_task(), source_kind="mr",
                    translations=[], mr_labels=[],
                )
            with sqlite3.connect(db.path) as conn:
                row = conn.execute(
                    "SELECT mr_labels FROM task_checks WHERE task_id='t-1'"
                ).fetchone()
            self.assertEqual(json.loads(row[0]), [])


# ---------------------------------------------------------------------------
# get_aggregated_issues — labels parsed back into a real list
# ---------------------------------------------------------------------------
class GetAggregatedIssuesParsesLabelsTests(unittest.TestCase):

    def _seed(self, db_path, mr_labels_json):
        """Insert one issue + its task_checks row with the given labels."""
        tc.init_db()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO task_checks("
                "  task_id, source_kind, project_id, project_name, mr_iid,"
                "  task_name, task_status, final_score_avg, total_issues,"
                "  total_rows, task_created_at, fetched_at, mr_labels"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("t-skip", "mr", "p", "p", 1066, "skip me", "completed",
                 None, 1, 1, "2026-05-01T10:00:00",
                 "2026-05-01T10:00:00", mr_labels_json),
            )
            conn.execute(
                "INSERT INTO check_issues("
                "  task_id, source_kind, opus_id, target_language,"
                "  error_type, error_category, error_keyword, "
                "  error_keyword_norm, source_text, translated_text,"
                "  final_score, reason, iteration, fetched_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("t-skip", "mr", "opus.x", "es-419",
                 "Other", None, "k", "k", "src", "tgt",
                 0.5, "r", 1, "2026-05-01T10:00:00"),
            )
            conn.commit()

    def _latest_labels(self, mr_labels_json):
        with _IsolatedDb() as db:
            self._seed(db.path, mr_labels_json)
            rows = tc.get_aggregated_issues()
        self.assertEqual(len(rows), 1)
        return rows[0]["latest_mr_labels"]

    def test_skip_translate_visible_in_latest_labels(self):
        labels = self._latest_labels('["skip-translate", "needs-review"]')
        self.assertIn("skip-translate", labels)

    def test_empty_json_array_parses_to_empty_list(self):
        self.assertEqual(self._latest_labels("[]"), [])

    def test_null_labels_parses_to_empty_list(self):
        # NULL in DB → empty string in the blob → [] after parsing. The GUI
        # must never see a None here.
        self.assertEqual(self._latest_labels(None), [])

    def test_malformed_json_falls_back_to_empty_list(self):
        # Defensive: if a stray non-JSON value sneaks in (manual SQL edit,
        # earlier bug), we degrade to [] rather than crash the agg view.
        self.assertEqual(self._latest_labels("not json"), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
