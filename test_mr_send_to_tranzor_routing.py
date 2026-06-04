"""Regression tests for MR Pipeline "Send to Tranzor" URL routing keys.

The HTML report embeds a per-row JSON blob (``const ROWS = …``) that the
Tranzor Bridge userscript turns into an envelope. The envelope picks the
Tranzor surface from the routing keys it finds:

  * /static/?project_id=…&mr_id=…   when project_id + mr_id are present (MR Pipeline)
  * /static/scans/<scan_task_id>    when scan_task_id is present (Scan Tasks)
  * /static/legacy/tasks/<task_id>  otherwise (File Translation)

A "changes" export builds rows from ``detect_mr_changes`` records, whose MR
IID lives under ``mr_iid`` (set by that function's ``mr_meta``), not under
``mr_id``. If ``write_mr_html`` only reads ``mr_id`` the changes report ships
an empty ``mr_id``, the envelope loses its MR coordinates, and Send-to-Tranzor
mis-routes the user to the File Translation page (the "Failed to load task
detail" symptom). This guards the ``mr_iid`` → ``mr_id`` fallback that keeps
the changes report routing to the MR Pipeline page like the all-translations
report already does.

Run:  python -m unittest test_mr_send_to_tranzor_routing
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_mr_pipeline as mr_api


def _extract_rows(html: str):
    """Pull the embedded ``const ROWS = [...]`` payload out of the report.

    json.dumps emits the array on a single physical line (string newlines are
    escaped), so the blob is exactly the text between ``const ROWS = `` and the
    trailing ``;``.
    """
    for line in html.splitlines():
        s = line.strip()
        if s.startswith("const ROWS = "):
            return json.loads(s[len("const ROWS = "):].rstrip(";"))
    raise AssertionError("embedded `const ROWS = …` blob not found in report")


def _render_rows(translations):
    """Render a report from *translations* and return the embedded JS rows.

    The terminology highlighter would hit the term API; stub the three public
    entry points to identity so the test stays offline and deterministic.
    """
    with mock.patch.object(mr_api.th, "prefetch_for_rows", lambda *a, **k: None), \
         mock.patch.object(mr_api.th, "highlight_source", lambda s: s), \
         mock.patch.object(mr_api.th, "highlight_translation", lambda s, loc=None: s):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "report.html")
            mr_api.write_mr_html(
                {"translations": translations, "summary": {}},
                path, "test label", bridge_info=None,
            )
            with open(path, encoding="utf-8") as f:
                return _extract_rows(f.read())


class ChangesExportRoutingTests(unittest.TestCase):

    def test_changes_record_mr_iid_backfills_mr_id(self):
        # Mirrors a detect_mr_changes record: project_id + mr_iid, NO mr_id.
        change = {
            "opus_id": "RingCentral.webModule.app.rooms.SECONDARY",
            "source_text": "Secondary",
            "target_language": "ja-JP",
            "translated_text": "代理会社",
            "prev_translated_text": "セカンダリ",
            "project_id": "web/web",
            "mr_iid": "40258",
            "task_id": "bfcd180b-6db9-4e83-8f41-92695a2c4b8a",
        }
        row = _render_rows([change])[0]
        self.assertEqual(row["project_id"], "web/web")
        # The fix: mr_iid backfills mr_id so the envelope keeps its MR
        # coordinates and routes to /static/?project_id=…&mr_id=…
        self.assertEqual(row["mr_id"], "40258")

    def test_all_translations_explicit_mr_id_wins(self):
        # enrich_translations_with_task stamps mr_id directly. An explicit
        # mr_id must take precedence over any mr_iid that happens to ride along.
        tr = {
            "opus_id": "RingCentral.webModule.app.rooms.PRIMARY",
            "source_text": "Primary",
            "target_language": "ja-JP",
            "translated_text": "メイン",
            "project_id": "web/web",
            "mr_id": "40258",
            "mr_iid": "999999",  # stale/duplicate — must not win over mr_id
        }
        row = _render_rows([tr])[0]
        self.assertEqual(row["mr_id"], "40258")

    def test_no_mr_coords_leaves_mr_id_empty(self):
        # A File Translation row carries neither mr_id nor mr_iid; the fallback
        # must not invent one, so routing correctly stays on the task_id path.
        tr = {
            "opus_id": "k",
            "source_text": "s",
            "target_language": "ja-JP",
            "translated_text": "t",
            "task_id": "abc-123",
        }
        row = _render_rows([tr])[0]
        self.assertEqual(row["mr_id"], "")
        self.assertEqual(row["task_id"], "abc-123")


if __name__ == "__main__":
    unittest.main()
