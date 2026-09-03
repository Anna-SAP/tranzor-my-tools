"""Tests for mr_jira — the MR Pipeline JIRA / Title / MR Status logic layer.

Covers the load-bearing pieces:

1. :func:`mr_jira.extract_jira_id` — the regex that turns an MR title into
   a JIRA ID, including the version-number guard ("BUI-26.3.1" must NOT
   yield a fake "BUI-26").
2. :func:`mr_jira.fetch_jira_id` — cache semantics: success (even "no ID
   in title") is cached, failure is NOT (so transient errors self-heal on
   the next repaint).
3. :func:`mr_jira.extract_jira_title` — single-line display-title extraction.
4. :func:`mr_jira.find_merge_requests` — exact JIRA-title search semantics
   and conversion of GitLab's cross-project references into Tranzor task
   match keys.
5. :func:`mr_jira.display_mr_state` / cached GitLab ``state`` — MR Status
   column mapping (opened → Open) and ``force_refresh`` so a Search shows
   the live GitLab state instead of a session-stale Open.
6. ``MRPipelineTab._MR_COLUMNS`` plus Title ellipsis/Tooltip invariants.

Run:  python -m unittest test_mr_jira
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mr_jira


class ExtractJiraIdTests(unittest.TestCase):

    def test_title_leading_ticket(self):
        title = ("BUP-4360 - BUI:: Purchase - RingCX - Support standalone "
                 "add-ons with overages MR4 (final)")
        self.assertEqual(mr_jira.extract_jira_id(title), "BUP-4360")

    def test_ticket_mid_title(self):
        self.assertEqual(
            mr_jira.extract_jira_id("fix: resolve RLZ-12345 truncation"),
            "RLZ-12345")

    def test_bracketed_ticket(self):
        self.assertEqual(
            mr_jira.extract_jira_id("[BUP-4360] purchase flow"), "BUP-4360")

    def test_first_of_multiple_wins(self):
        self.assertEqual(
            mr_jira.extract_jira_id("BUP-4360 relates to LOC-999"),
            "BUP-4360")

    def test_digits_allowed_in_key_after_first_letter(self):
        self.assertEqual(
            mr_jira.extract_jira_id("I18N-42 pseudo-locale pass"), "I18N-42")

    def test_lowercase_is_not_a_ticket(self):
        self.assertEqual(
            mr_jira.extract_jira_id("feature/bup-4360-add-ons"), "")

    def test_single_letter_key_rejected(self):
        # JIRA project keys are >= 2 chars; "A-1" style fragments are noise.
        self.assertEqual(mr_jira.extract_jira_id("grade A-1 rollout"), "")

    def test_version_number_guard(self):
        # Release-style tokens must not be truncated into a fake ticket.
        self.assertEqual(mr_jira.extract_jira_id("Release BUI-26.3.1"), "")

    def test_ticket_followed_by_sentence_period(self):
        self.assertEqual(
            mr_jira.extract_jira_id("Closes BUP-4360."), "BUP-4360")

    def test_embedded_in_longer_word_takes_full_key(self):
        # "XBUP" is itself a valid-looking key — the whole run is the key.
        self.assertEqual(
            mr_jira.extract_jira_id("XBUP-4360 follow-up"), "XBUP-4360")

    def test_empty_and_none(self):
        self.assertEqual(mr_jira.extract_jira_id(""), "")
        self.assertEqual(mr_jira.extract_jira_id(None), "")

    def test_no_ticket_at_all(self):
        self.assertEqual(
            mr_jira.extract_jira_id("chore: bump dependencies"), "")

    # -- CJK adjacency: unicode \b treats ideographs as word chars, so the
    #    old \b-based pattern extracted NOTHING from unspaced Chinese
    #    titles; the ASCII lookarounds must accept these. --

    def test_cjk_adjacent_both_sides(self):
        self.assertEqual(
            mr_jira.extract_jira_id("修复BUP-4360购买流程"), "BUP-4360")

    def test_cjk_adjacent_one_side(self):
        self.assertEqual(
            mr_jira.extract_jira_id("BUP-4360修复购买流程"), "BUP-4360")
        self.assertEqual(
            mr_jira.extract_jira_id("修复BUP-4360"), "BUP-4360")

    def test_cjk_fullwidth_brackets(self):
        self.assertEqual(
            mr_jira.extract_jira_id("【BUP-4360】修复购买流程"), "BUP-4360")

    # -- Acronym denylist: ticket-shaped technical tokens must not be
    #    reported as JIRA IDs. --

    def test_common_acronyms_are_not_tickets(self):
        for title in ("Fix UTF-8 encoding in exporter",
                      "SHA-256 checksum fix",
                      "use ISO-8601 dates",
                      "see RFC-2616",
                      "patch CVE-2024-12345",
                      "Purchase flow fixes MR-2 (final)"):
            self.assertEqual(mr_jira.extract_jira_id(title), "", title)

    def test_denylisted_match_skips_to_real_ticket(self):
        self.assertEqual(
            mr_jira.extract_jira_id("Fix UTF-8 handling for BUP-4360"),
            "BUP-4360")


class ExtractJiraTitleTests(unittest.TestCase):

    def test_strips_leading_ticket_and_common_separators(self):
        cases = {
            "UIA-411423 - [Operator Connect] Error message is unclear":
                "[Operator Connect] Error message is unclear",
            "[BUP-4360] Purchase flow": "Purchase flow",
            "【RLZ-7】修复购买流程": "修复购买流程",
            "RCAC-9: Retry failed exports": "Retry failed exports",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(mr_jira.extract_jira_title(source), expected)

    def test_normalizes_whitespace_to_one_line(self):
        self.assertEqual(
            mr_jira.extract_jira_title("BUP-1  First line\n  second line"),
            "First line second line")

    def test_mid_sentence_ticket_keeps_readable_full_title(self):
        title = "fix: resolve RLZ-12345 truncation"
        self.assertEqual(mr_jira.extract_jira_title(title), title)

    def test_no_ticket_or_ticket_only_has_no_companion_title(self):
        self.assertEqual(mr_jira.extract_jira_title("chore: bump deps"), "")
        self.assertEqual(mr_jira.extract_jira_title("BUP-4360"), "")


class _FakeClient:
    """In-memory stand-in for gitlab_client.GitLabClient — no network."""

    def __init__(self, title=None, error=None, token=True, state="opened"):
        self._title = title
        self._error = error
        self._token = token
        self._state = state
        self.calls = 0
        self.force_refresh_flags = []

    def has_token(self):
        return self._token

    def get_merge_request(self, project_id, mr_iid, *, force_refresh=False):
        self.calls += 1
        self.force_refresh_flags.append(force_refresh)
        if self._error is not None:
            raise self._error
        return {"title": self._title, "state": self._state}


class FetchJiraIdTests(unittest.TestCase):

    def setUp(self):
        mr_jira.clear_cache()

    def tearDown(self):
        mr_jira.clear_cache()

    def test_success_is_cached(self):
        client = _FakeClient(title="BUP-4360 - purchase MR4")
        self.assertEqual(
            mr_jira.fetch_jira_id("web/bui", 3064, client=client), "BUP-4360")
        # Second call is served from the module cache — no client hit.
        self.assertEqual(
            mr_jira.fetch_jira_id("web/bui", 3064, client=client), "BUP-4360")
        self.assertEqual(client.calls, 1)
        self.assertEqual(mr_jira.get_cached("web/bui", 3064), "BUP-4360")
        self.assertEqual(
            mr_jira.get_cached_title("web/bui", 3064), "purchase MR4")

    def test_metadata_returns_id_and_title_from_one_client_call(self):
        client = _FakeClient(
            title="UIA-411423 - [Operator Connect] Error message is unclear")
        metadata = mr_jira.fetch_jira_metadata(
            "web/web", 41035, client=client)

        self.assertEqual(metadata.jira_id, "UIA-411423")
        self.assertEqual(
            metadata.title, "[Operator Connect] Error message is unclear")
        self.assertEqual(client.calls, 1)
        self.assertEqual(
            mr_jira.get_cached_metadata("web/web", 41035), metadata)

    def test_title_without_ticket_caches_empty(self):
        client = _FakeClient(title="chore: bump deps")
        self.assertEqual(
            mr_jira.fetch_jira_id("web/bui", 1, client=client), "")
        self.assertEqual(mr_jira.get_cached("web/bui", 1), "")
        self.assertEqual(client.calls, 1)

    def test_failure_returns_none_and_is_not_cached(self):
        boom = _FakeClient(error=RuntimeError("503"))
        self.assertIsNone(mr_jira.fetch_jira_id("web/bui", 2, client=boom))
        self.assertIsNone(mr_jira.get_cached("web/bui", 2))
        # A later healthy fetch self-heals.
        ok = _FakeClient(title="RLZ-7 fix")
        self.assertEqual(
            mr_jira.fetch_jira_id("web/bui", 2, client=ok), "RLZ-7")

    def test_no_token_returns_none_uncached(self):
        client = _FakeClient(title="BUP-1 x", token=False)
        self.assertIsNone(mr_jira.fetch_jira_id("web/bui", 3, client=client))
        self.assertEqual(client.calls, 0)
        self.assertIsNone(mr_jira.get_cached("web/bui", 3))

    def test_none_client_without_shared_fallback_is_safe(self):
        # No injected client + shared client unavailable → None, no crash.
        import task_post_edit as _tpe
        orig = _tpe._shared_gitlab_client
        _tpe._shared_gitlab_client = lambda: None
        try:
            self.assertIsNone(mr_jira.fetch_jira_id("web/bui", 4))
        finally:
            _tpe._shared_gitlab_client = orig

    def test_bad_keys_are_rejected(self):
        client = _FakeClient(title="BUP-1 x")
        self.assertIsNone(mr_jira.fetch_jira_id("", 1, client=client))
        self.assertIsNone(mr_jira.fetch_jira_id("web/bui", None, client=client))
        self.assertIsNone(
            mr_jira.fetch_jira_id("web/bui", "abc", client=client))
        self.assertIsNone(mr_jira.get_cached("", 1))
        self.assertEqual(client.calls, 0)

    def test_string_iid_normalizes_to_same_cache_slot(self):
        client = _FakeClient(title="BUP-9 y")
        self.assertEqual(
            mr_jira.fetch_jira_id("web/bui", "3064", client=client), "BUP-9")
        self.assertEqual(mr_jira.get_cached("web/bui", 3064), "BUP-9")
        self.assertEqual(client.calls, 1)

    def test_late_failure_returns_concurrently_cached_value(self):
        # Race shape: fetch A misses the cache, its HTTP call hangs, a
        # second fetch B for the same key succeeds and caches the ID, then
        # A's call fails. A must surface B's cached answer, not None —
        # otherwise the GUI would clobber the painted ID with "—".
        class _RacingClient(_FakeClient):
            def get_merge_request(self, project_id, mr_iid, **kwargs):
                self.calls += 1
                # Simulate the concurrent winner landing mid-flight.
                with mr_jira._cache_lock:
                    mr_jira._cache[(str(project_id), int(mr_iid))] = "BUP-4360"
                raise RuntimeError("late timeout")

        client = _RacingClient()
        self.assertEqual(
            mr_jira.fetch_jira_id("web/bui", 5, client=client), "BUP-4360")
        self.assertEqual(client.calls, 1)


class DisplayMrStateTests(unittest.TestCase):

    def test_gitlab_api_values_match_gitlab_ui_labels(self):
        self.assertEqual(mr_jira.display_mr_state("opened"), "Open")
        self.assertEqual(mr_jira.display_mr_state("merged"), "Merged")
        self.assertEqual(mr_jira.display_mr_state("closed"), "Closed")
        self.assertEqual(mr_jira.display_mr_state("locked"), "Locked")

    def test_empty_and_none_are_blank(self):
        self.assertEqual(mr_jira.display_mr_state(""), "")
        self.assertEqual(mr_jira.display_mr_state(None), "")
        self.assertEqual(mr_jira.display_mr_state("  "), "")

    def test_unknown_state_is_title_cased(self):
        self.assertEqual(mr_jira.display_mr_state("CHECKING"), "Checking")

    def test_case_insensitive_known_values(self):
        self.assertEqual(mr_jira.display_mr_state("OPENED"), "Open")
        self.assertEqual(mr_jira.display_mr_state("Merged"), "Merged")


class FetchMrStateTests(unittest.TestCase):

    def setUp(self):
        mr_jira.clear_cache()

    def tearDown(self):
        mr_jira.clear_cache()

    def test_state_is_cached_from_the_same_gitlab_call(self):
        client = _FakeClient(title="RLZ-77987 popup", state="opened")
        metadata = mr_jira.fetch_jira_metadata(
            "web/web", 41893, client=client)

        self.assertEqual(metadata.state, "opened")
        self.assertEqual(mr_jira.get_cached_state("web/web", 41893), "opened")
        self.assertEqual(mr_jira.display_mr_state(metadata.state), "Open")
        self.assertEqual(client.calls, 1)

        # Warm cache: no extra GitLab hit.
        again = mr_jira.fetch_jira_metadata(
            "web/web", 41893, client=client)
        self.assertEqual(again.state, "opened")
        self.assertEqual(client.calls, 1)

    def test_force_refresh_picks_up_a_state_change(self):
        client = _FakeClient(title="RLZ-77987 popup", state="opened")
        mr_jira.fetch_jira_metadata("web/web", 41893, client=client)
        client._state = "merged"

        stale = mr_jira.fetch_jira_metadata(
            "web/web", 41893, client=client)
        self.assertEqual(stale.state, "opened")
        self.assertEqual(client.calls, 1)

        fresh = mr_jira.fetch_jira_metadata(
            "web/web", 41893, client=client, force_refresh=True)
        self.assertEqual(fresh.state, "merged")
        self.assertEqual(mr_jira.get_cached_state("web/web", 41893), "merged")
        self.assertEqual(client.calls, 2)
        self.assertEqual(client.force_refresh_flags, [False, True])

    def test_force_refresh_failure_keeps_previous_state(self):
        client = _FakeClient(title="RLZ-7 fix", state="opened")
        mr_jira.fetch_jira_metadata("web/web", 1, client=client)
        client._error = RuntimeError("503")

        kept = mr_jira.fetch_jira_metadata(
            "web/web", 1, client=client, force_refresh=True)
        self.assertEqual(kept.state, "opened")
        self.assertEqual(mr_jira.get_cached_state("web/web", 1), "opened")

    def test_legacy_client_without_force_refresh_kwarg_still_works(self):
        class LegacyClient:
            def __init__(self):
                self.calls = 0

            def has_token(self):
                return True

            def get_merge_request(self, project_id, mr_iid):
                self.calls += 1
                return {"title": "RLZ-1 x", "state": "closed"}

        client = LegacyClient()
        metadata = mr_jira.fetch_jira_metadata(
            "web/web", 1, client=client, force_refresh=True)
        self.assertEqual(metadata.state, "closed")
        self.assertEqual(mr_jira.display_mr_state(metadata.state), "Closed")
        self.assertEqual(client.calls, 1)

    def test_missing_state_field_caches_empty_string(self):
        client = _FakeClient(title="chore: bump", state=None)
        metadata = mr_jira.fetch_jira_metadata(
            "web/bui", 2, client=client)
        self.assertEqual(metadata.state, "")
        self.assertEqual(mr_jira.get_cached_state("web/bui", 2), "")
        self.assertEqual(mr_jira.display_mr_state(metadata.state), "")


class NormalizeJiraIdTests(unittest.TestCase):

    def test_accepts_pasted_lowercase_and_whitespace(self):
        self.assertEqual(mr_jira.normalize_jira_id("  bup-4360\n"),
                         "BUP-4360")

    def test_rejects_free_text_urls_versions_and_acronyms(self):
        for value in ("fix BUP-4360",
                      "https://jira.example/browse/BUP-4360",
                      "BUI-26.3.1", "UTF-8", ""):
            self.assertEqual(mr_jira.normalize_jira_id(value), "", value)


class JiraBrowseUrlTests(unittest.TestCase):

    def test_builds_canonical_ringcentral_url(self):
        self.assertEqual(
            mr_jira.jira_browse_url("RA-132077"),
            "https://jira.ringcentral.com/browse/RA-132077")
        self.assertEqual(
            mr_jira.jira_browse_url("RCAC-4599"),
            "https://jira.ringcentral.com/browse/RCAC-4599")

    def test_normalizes_lowercase_and_rejects_placeholders(self):
        self.assertEqual(
            mr_jira.jira_browse_url(" uia-10000 "),
            "https://jira.ringcentral.com/browse/UIA-10000")
        for value in ("", "—", "…", "not a ticket"):
            self.assertEqual(mr_jira.jira_browse_url(value), "", value)


class _FakeSearchClient:
    def __init__(self, rows, token=True):
        self.rows = rows
        self.token = token
        self.calls = []

    def has_token(self):
        return self.token

    def list_merge_requests(self, search, **kwargs):
        self.calls.append((search, kwargs))
        return self.rows


class FindMergeRequestsTests(unittest.TestCase):

    def setUp(self):
        mr_jira.clear_cache()

    def tearDown(self):
        mr_jira.clear_cache()

    def test_global_search_uses_full_reference_and_exact_displayed_ticket(self):
        client = _FakeSearchClient([
            {"iid": 3064, "title": "BUP-4360 purchase MR4",
             "state": "merged",
             "references": {"full": "web/bui!3064"}},
            {"iid": 88, "title": "LOC-9 relates to BUP-4360",
             "references": {"full": "common/clw!88"}},
        ])

        matches = mr_jira.find_merge_requests("bup-4360", client=client)

        self.assertEqual(matches, {("web/bui", 3064)})
        self.assertEqual(client.calls, [
            ("BUP-4360", {"project_id": None, "in_field": "title"})])
        self.assertEqual(mr_jira.get_cached("web/bui", 3064), "BUP-4360")
        self.assertEqual(mr_jira.get_cached_state("web/bui", 3064), "merged")

    def test_project_search_uses_selected_project_when_reference_is_short(self):
        client = _FakeSearchClient([
            {"iid": "7", "title": "BUP-4360 checkout",
             "references": {"full": "!7"}},
        ])

        matches = mr_jira.find_merge_requests(
            "BUP-4360", project_id="Web/BUI", client=client)

        self.assertEqual(matches, {("web/bui", 7)})
        self.assertTrue(mr_jira.task_matches_mrs(
            {"project_id": "web/bui", "merge_request_iid": "7"},
            matches))
        self.assertFalse(mr_jira.task_matches_mrs(
            {"project_id": "web/bui", "merge_request_iid": 8},
            matches))

    def test_missing_token_is_an_explicit_error(self):
        with self.assertRaisesRegex(RuntimeError, "GitLab token"):
            mr_jira.find_merge_requests(
                "BUP-4360", client=_FakeSearchClient([], token=False))

class ColumnLayoutTests(unittest.TestCase):
    """Lock the _MR_COLUMNS invariants the positional reads depend on."""

    def test_layout_invariants(self):
        from gui_tabs import MRPipelineTab
        cols = MRPipelineTab._MR_COLUMNS
        # Positional reads elsewhere in gui_tabs: project @ 1 (post-edit
        # prefix), mr @ 2 (export filename); MR Status sits beside MR#;
        # JIRA and Title form a pair immediately after.
        self.assertEqual(cols.index("project"), 1)
        self.assertEqual(cols.index("mr"), 2)
        self.assertEqual(cols.index("mr_status"), 3)
        self.assertEqual(cols.index("jira"), 4)
        self.assertEqual(cols.index("title"), 5)
        # GitLab metadata columns sort as text, not as numbers.
        self.assertNotIn("mr_status", MRPipelineTab._MR_NUMERIC_COLS)
        self.assertNotIn("jira", MRPipelineTab._MR_NUMERIC_COLS)
        self.assertNotIn("title", MRPipelineTab._MR_NUMERIC_COLS)

    def test_heading_strings_exist_in_both_languages(self):
        from export_gui import STRINGS
        from gui_tabs import MRPipelineTab
        for lang in ("en", "zh"):
            for col in MRPipelineTab._MR_COLUMNS:
                self.assertIn(
                    f"mr_col_{col}", STRINGS[lang],
                    f"missing mr_col_{col} in STRINGS[{lang}]")
        self.assertEqual(STRINGS["en"]["mr_col_mr_status"], "MR Status")
        self.assertEqual(STRINGS["zh"]["mr_col_mr_status"], "MR 状态")


class TitleEllipsisTests(unittest.TestCase):

    def test_short_text_is_unchanged(self):
        from gui_tabs import _ellipsize_text
        self.assertEqual(
            _ellipsize_text("Short title", 20, len), ("Short title", False))

    def test_long_text_gets_literal_three_dot_suffix(self):
        from gui_tabs import _ellipsize_text
        display, truncated = _ellipsize_text(
            "Operator Connect export monitoring", 19, len)
        self.assertTrue(truncated)
        self.assertEqual(display, "Operator Connect...")
        self.assertLessEqual(len(display), 19)

    def test_newlines_are_collapsed_before_measurement(self):
        from gui_tabs import _ellipsize_text
        self.assertEqual(
            _ellipsize_text("First\n  second", 20, len),
            ("First second", False))


class _FakeTree:
    def __init__(self, *, region="cell", column="#5", row="task-1",
                 jira="RA-132077"):
        self.region = region
        self.column = column
        self.row = row
        self.jira = jira
        self.cursor = None

    def identify_region(self, _x, _y):
        return self.region

    def identify_column(self, _x):
        return self.column

    def identify_row(self, _y):
        return self.row

    def set(self, _iid, column):
        return self.jira if column == "jira" else ""

    def configure(self, **kwargs):
        self.cursor = kwargs.get("cursor")


class ApplyGitlabMetadataTests(unittest.TestCase):
    """Lock the Tk-thread paint of JIRA + Title + MR Status from one payload."""

    def test_opened_state_paints_as_open_next_to_jira(self):
        from gui_tabs import MRPipelineTab

        class _Tree:
            def __init__(self):
                self.cells = {}

            def set(self, iid, column, value=None):
                if value is None:
                    return self.cells.get((iid, column), "")
                self.cells[(iid, column)] = value

            def column(self, _name, _opt):
                return 400

        tab = MRPipelineTab.__new__(MRPipelineTab)
        tab.mr_tree = _Tree()
        tab._jira_row_iids = {("web/web", 41893): ["task-1"]}
        tab._jira_titles_by_iid = {}
        tab._truncated_title_iids = set()
        tab._mr_title_font = SimpleNamespace(measure=lambda s: len(s))

        tab._apply_jira_metadata(
            ("web/web", 41893),
            mr_jira.JiraMetadata("RLZ-77987", "apply Do not show again",
                                 "opened"),
        )

        self.assertEqual(tab.mr_tree.cells[("task-1", "mr_status")], "Open")
        self.assertEqual(tab.mr_tree.cells[("task-1", "jira")], "RLZ-77987")
        self.assertEqual(
            tab.mr_tree.cells[("task-1", "title")],
            "apply Do not show again")

    def test_merged_state_paints_as_merged(self):
        from gui_tabs import MRPipelineTab

        class _Tree:
            def __init__(self):
                self.cells = {}

            def set(self, iid, column, value=None):
                if value is None:
                    return self.cells.get((iid, column), "")
                self.cells[(iid, column)] = value

            def column(self, _name, _opt):
                return 400

        tab = MRPipelineTab.__new__(MRPipelineTab)
        tab.mr_tree = _Tree()
        tab._jira_row_iids = {("web/web", 1): ["task-1"]}
        tab._jira_titles_by_iid = {}
        tab._truncated_title_iids = set()
        tab._mr_title_font = SimpleNamespace(measure=lambda s: len(s))

        tab._apply_jira_metadata(
            ("web/web", 1),
            mr_jira.JiraMetadata("RLZ-1", "done", "merged"),
        )
        self.assertEqual(tab.mr_tree.cells[("task-1", "mr_status")], "Merged")


class JiraHyperlinkInteractionTests(unittest.TestCase):

    @staticmethod
    def _tab(tree):
        from gui_tabs import MRPipelineTab
        tab = MRPipelineTab.__new__(MRPipelineTab)
        tab.mr_tree = tree
        return tab

    def test_clicking_jira_cell_opens_exact_detail_url(self):
        tab = self._tab(_FakeTree())
        event = SimpleNamespace(x=10, y=20)

        with mock.patch("gui_tabs.webbrowser.open_new_tab") as opener:
            result = tab._on_mr_tree_click(event)

        opener.assert_called_once_with(
            "https://jira.ringcentral.com/browse/RA-132077")
        self.assertEqual(result, "break")

    def test_pointer_changes_only_for_valid_jira_cells(self):
        tree = _FakeTree()
        tab = self._tab(tree)
        tab._on_mr_tree_motion(SimpleNamespace(x=10, y=20))
        self.assertEqual(tree.cursor, "hand2")

        tree.jira = "…"
        tab._on_mr_tree_motion(SimpleNamespace(x=10, y=20))
        self.assertEqual(tree.cursor, "")

        tree.jira = "RA-132077"
        tree.column = "#3"  # MR# — not the JIRA column
        with mock.patch("gui_tabs.webbrowser.open_new_tab") as opener:
            result = tab._on_mr_tree_click(SimpleNamespace(x=10, y=20))
        opener.assert_not_called()
        self.assertIsNone(result)


class TitleTooltipInteractionTests(unittest.TestCase):

    def test_tooltip_only_targets_truncated_title_cell(self):
        from gui_tabs import MRPipelineTab
        tree = _FakeTree(column="#6")
        tab = MRPipelineTab.__new__(MRPipelineTab)
        tab.mr_tree = tree
        tab._jira_titles_by_iid = {"task-1": "Complete JIRA title"}
        tab._truncated_title_iids = {"task-1"}

        self.assertEqual(
            tab._title_tooltip_at(10, 20),
            ("task-1", "Complete JIRA title"))

        tree.column = "#5"  # JIRA, not Title
        self.assertIsNone(tab._title_tooltip_at(10, 20))
        tree.column = "#6"
        tab._truncated_title_iids.clear()
        self.assertIsNone(tab._title_tooltip_at(10, 20))


if __name__ == "__main__":
    unittest.main()
