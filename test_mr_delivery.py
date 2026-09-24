"""Tests for mr_delivery — source MR vs translation-delivery MR.

Run:  python -m unittest test_mr_delivery
"""
from __future__ import annotations

import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mr_delivery as md


class ParseTests(unittest.TestCase):

    def test_parse_mr_iid_accepts_int_and_str(self):
        self.assertEqual(md.parse_mr_iid(4192), 4192)
        self.assertEqual(md.parse_mr_iid("4192"), 4192)
        self.assertIsNone(md.parse_mr_iid(""))
        self.assertIsNone(md.parse_mr_iid("abc"))
        self.assertIsNone(md.parse_mr_iid(None))

    def test_parse_mr_iid_from_url(self):
        self.assertEqual(
            md.parse_mr_iid_from_url(
                "https://git.ringcentral.com/common/uns/"
                "-/merge_requests/4192/commits"),
            4192)
        self.assertIsNone(md.parse_mr_iid_from_url(""))
        self.assertIsNone(md.parse_mr_iid_from_url("https://example.com/x"))

    def test_gitlab_mr_url(self):
        self.assertEqual(
            md.gitlab_mr_url("common/uns", 4192,
                             base_url="https://git.ringcentral.com"),
            "https://git.ringcentral.com/common/uns/-/merge_requests/4192")
        self.assertEqual(md.gitlab_mr_url("", 1), "")
        self.assertEqual(md.gitlab_mr_url("p", "x"), "")


class DeliveryFromTaskTests(unittest.TestCase):

    def test_delivery_mr_iid_wins(self):
        ref = md.delivery_from_task({
            "project_id": "common/uns",
            "merge_request_iid": 3930,
            "delivery_mr_iid": 4192,
            "import_mr_url": (
                "https://git.ringcentral.com/common/uns/"
                "-/merge_requests/4192"),
        })
        self.assertEqual(ref.iid, 4192)
        self.assertEqual(ref.project_id, "common/uns")
        self.assertIn("/merge_requests/4192", ref.url)

    def test_import_mr_url_used_when_iid_missing(self):
        ref = md.delivery_from_task({
            "project_id": "common/uns",
            "merge_request_iid": 3930,
            "import_mr_url": (
                "https://git.ringcentral.com/common/uns/"
                "-/merge_requests/4192"),
        })
        self.assertEqual(ref.iid, 4192)

    def test_same_as_source_is_not_a_follow_up(self):
        self.assertIsNone(md.delivery_from_task({
            "project_id": "common/uns",
            "merge_request_iid": 3930,
            "delivery_mr_iid": 3930,
        }))
        self.assertIsNone(md.delivery_from_task({
            "project_id": "common/uns",
            "merge_request_iid": 3930,
            "import_mr_url": (
                "https://git.ringcentral.com/common/uns/"
                "-/merge_requests/3930"),
        }))

    def test_missing_payload_is_none(self):
        self.assertIsNone(md.delivery_from_task({
            "project_id": "common/uns",
            "merge_request_iid": 3930,
        }))
        self.assertIsNone(md.delivery_from_task(None))

    def test_delivery_project_override(self):
        ref = md.delivery_from_task({
            "project_id": "group/target",
            "delivery_project_id": "group/source",
            "merge_request_iid": 10,
            "delivery_mr_iid": 11,
        })
        self.assertEqual(ref.project_id, "group/source")
        self.assertEqual(ref.iid, 11)


class CandidateAndPickTests(unittest.TestCase):

    SOURCE = 3930
    TASK_ID = "b456021b-502a-4188-8c52-8a244417eb19"

    def _digest(self):
        return hashlib.sha256(self.TASK_ID.encode("utf-8")).hexdigest()[:8]

    def test_title_profiles_are_candidates(self):
        titles = [
            "[Tranzor] Translations for MR!3930",
            "Tranzor | Translations for MR | MR!3930",
            "chore(i18n): translations for MR!3930",
            "fix(apps): [misc] translations for MR!3930",
        ]
        for title in titles:
            mr = {"iid": 4192, "title": title, "source_branch": "x"}
            self.assertTrue(
                md.is_delivery_candidate(mr, self.SOURCE), title)

    def test_branch_prefix_is_a_candidate_without_title(self):
        mr = {
            "iid": 4192,
            "title": "unrelated",
            "source_branch": "tranzor/translate-3930-622b8bd8a7b6-aaaa-bbbb",
        }
        self.assertTrue(md.is_delivery_candidate(mr, self.SOURCE))

    def test_source_mr_itself_is_rejected(self):
        mr = {
            "iid": 3930,
            "title": "[L10nFeedback] ja-JP - Activate Account",
            "source_branch": "RLZ-81787",
        }
        self.assertFalse(md.is_delivery_candidate(mr, self.SOURCE))

    def test_pick_prefers_task_digest_then_opened_then_newest(self):
        digest = self._digest()
        mrs = [
            {"iid": 4000, "title": "[Tranzor] Translations for MR!3930",
             "source_branch": "tranzor/translate-3930-aaa-bbb-oldoldxx",
             "state": "merged"},
            {"iid": 4192, "title": "[Tranzor] Translations for MR!3930",
             "source_branch": f"tranzor/translate-3930-622b8bd8a7b6-28ad2d17-{digest}",
             "state": "opened"},
            {"iid": 4300, "title": "[Tranzor] Translations for MR!3930",
             "source_branch": "tranzor/translate-3930-ccc-ddd-otherxxx",
             "state": "opened"},
        ]
        picked = md.pick_delivery_mr(mrs, self.SOURCE, task_id=self.TASK_ID)
        self.assertEqual(picked["iid"], 4192)

    def test_source_iid_from_delivery_mr(self):
        self.assertEqual(
            md.source_iid_from_delivery_mr({
                "title": "[Tranzor] Translations for MR!3930",
                "source_branch": "tranzor/translate-3930-abc-def-ghi",
            }),
            3930)
        self.assertIsNone(md.source_iid_from_delivery_mr({
            "title": "[L10nFeedback] ja-JP",
            "source_branch": "RLZ-81787",
        }))

    def test_fix_branch_is_not_the_import_delivery_mr(self):
        mr = {
            "iid": 1225,
            "title": "[Tranzor] Translations for MR!1223",
            "source_branch": "tranzor-mr-fix-20260911152000",
            "state": "opened",
        }
        self.assertFalse(md.is_delivery_candidate(mr, 1223))
        self.assertTrue(md.is_fix_mr_candidate(mr, 1223))

    def test_pick_delivery_ignores_later_fix_mr(self):
        mrs = [
            {"iid": 1224, "title": "[Tranzor] Translations for MR!1223",
             "source_branch": "tranzor/translate-1223-aaa-bbb-cccccccc",
             "state": "merged"},
            {"iid": 1225, "title": "[Tranzor] Translations for MR!1223",
             "source_branch": "tranzor-mr-fix-20260911152000",
             "state": "opened"},
        ]
        picked = md.pick_delivery_mr(mrs, 1223)
        self.assertEqual(picked["iid"], 1224)

    def test_retitled_fix_mr_is_still_a_fix_candidate(self):
        # Real !4233: a Language Lead renamed the fix MR. The title lost the
        # "Translations for MR!" template (and the word "translation"), which
        # used to drop it from the Trans MR# chain.
        mr = {"iid": 4233,
              "title": "fix(LOC-25286) further fr-FR linguistic fixes "
                       "for MR!4003",
              "source_branch": "tranzor-mr-fix-20260923075431-d988"}
        self.assertTrue(md.is_fix_mr_candidate(mr, 4003))
        self.assertFalse(md.is_delivery_candidate(mr, 4003))

    def test_fix_candidate_needs_the_exact_source_iid(self):
        mr = {"iid": 4300, "title": "fix(LOC-1) fixes for MR!40031",
              "source_branch": "tranzor-mr-fix-20260923075431-d988"}
        self.assertFalse(md.is_fix_mr_candidate(mr, 4003))
        self.assertTrue(md.is_fix_mr_candidate(mr, 40031))

    def test_fix_title_on_a_non_fix_branch_is_not_a_fix_candidate(self):
        mr = {"iid": 4300, "title": "fix(LOC-1) fixes for MR!4003",
              "source_branch": "feature/LOC-1"}
        self.assertFalse(md.is_fix_mr_candidate(mr, 4003))

    def test_format_trans_mr_cell_lists_the_whole_chain(self):
        def ref(iid):
            return md.DeliveryRef(project_id="p", iid=iid)

        self.assertEqual(md.format_trans_mr_cell([ref(1224)]), "1224")
        self.assertEqual(
            md.format_trans_mr_cell([ref(4213), ref(4214), ref(4233),
                                     ref(4237)]),
            "4213 → 4214 → 4233 → 4237")
        self.assertEqual(md.format_trans_mr_cell([]), "—")
        self.assertEqual(md.format_trans_mr_cell(None), "—")
        self.assertEqual(
            md.trans_mr_sort_iid("4213 → 4214 → 4233 → 4237"), 4237)
        self.assertEqual(md.trans_mr_sort_iid("1224"), 1224)
        self.assertIsNone(md.trans_mr_sort_iid("—"))


class TransMrChainTests(unittest.TestCase):
    """The Trans MR# chain: every translation MR of a task, oldest first."""

    TASK_ID = "d00ff2ec-5b54-467e-acb0-098c4a8607f2"  # digest 71c8ec67

    # GitLab's MR!4003 title search, verbatim where it matters (2026-09-24).
    MRS = [
        {"iid": 4237, "state": "merged",
         "created_at": "2026-09-24T06:15:45.028Z",
         "title": "fix(LOC-25276) it-IT inconsistencies and duplicated "
                  "translation to 'service' for MR!4003",
         "source_branch": "tranzor-mr-fix-20260924061453-a932",
         "target_branch": "26-4-2_XMN-FT5",
         "references": {"full": "common/uns!4237"}},
        {"iid": 4233, "state": "merged",
         "created_at": "2026-09-23T07:55:30.473Z",
         "title": "fix(LOC-25286) further fr-FR linguistic fixes for MR!4003",
         "source_branch": "tranzor-mr-fix-20260923075431-d988",
         "target_branch": "26-4-2_XMN-FT5",
         "references": {"full": "common/uns!4233"}},
        {"iid": 4214, "state": "merged",
         "created_at": "2026-09-20T09:37:43.527Z",
         "title": "[Tranzor] Translations for MR!4003",
         "source_branch": "tranzor-mr-fix-20260920093712-fc5f",
         "target_branch": "26-4-2_XMN-FT5",
         "references": {"full": "common/uns!4214"}},
        {"iid": 4213, "state": "merged",
         "created_at": "2026-09-20T08:03:08.773Z",
         "title": "[Tranzor] Translations for MR!4003",
         "source_branch":
             "tranzor/translate-4003-77293d2ff50d-28ad2d17-71c8ec67",
         "target_branch": "26-4-2_XMN-FT5",
         "references": {"full": "common/uns!4213"}},
    ]

    def _iids(self, chain):
        return [ref.iid for ref in chain]

    def test_payload_pointing_at_the_newest_fix_yields_the_whole_chain(self):
        # The screenshot bug: the platform re-points delivery_mr_iid at every
        # new fix MR, so the payload named 4237. The cell read "4237 → 4214",
        # losing the import MR 4213 and the retitled fix 4233.
        payload = md.delivery_from_task({
            "project_id": "common/uns", "merge_request_iid": 4003,
            "delivery_mr_iid": 4237})
        chain = md.trans_mr_chain(
            self.MRS, 4003, task_id=self.TASK_ID, known=[payload],
            fallback_project="common/uns")
        self.assertEqual(self._iids(chain), [4213, 4214, 4233, 4237])
        self.assertEqual(md.format_trans_mr_cell(chain),
                         "4213 → 4214 → 4233 → 4237")
        # The payload ref is refreshed from the search, not left bare.
        self.assertEqual(chain[-1].created_at, "2026-09-24T06:15:45.028Z")
        self.assertEqual(chain[-1].state, "merged")

    def test_search_alone_yields_the_whole_chain(self):
        chain = md.trans_mr_chain(self.MRS, 4003, task_id=self.TASK_ID,
                                  fallback_project="common/uns")
        self.assertEqual(self._iids(chain), [4213, 4214, 4233, 4237])
        self.assertEqual(chain[0].source_branch,
                         self.MRS[3]["source_branch"])

    def test_order_follows_created_at_not_iid_or_search_order(self):
        refs = [
            md.DeliveryRef("a/b", 7, created_at="2026-09-21T00:00:00Z"),
            md.DeliveryRef("c/d", 9, created_at="2026-09-20T00:00:00Z"),
            md.DeliveryRef("a/b", 8, created_at="2026-09-22T00:00:00+08:00"),
        ]
        # 8 was created at 2026-09-21T16:00Z — after 7, despite the offset.
        self.assertEqual(self._iids(md.sort_chronologically(refs)), [9, 7, 8])

    def test_unknown_created_at_falls_back_to_iid_order(self):
        refs = [
            md.DeliveryRef("p", 4237),
            md.DeliveryRef("p", 4213, created_at="2026-09-20T08:03:08Z"),
        ]
        self.assertEqual(self._iids(md.sort_chronologically(refs)),
                         [4213, 4237])

    def test_known_import_mr_is_not_joined_by_another_tasks_import(self):
        # No digest match: the payload's MR (outside the search) is taken to
        # be this task's import MR, so another task's import MR stays out.
        mrs = [{"iid": 4300, "state": "merged",
                "created_at": "2026-09-22T00:00:00Z",
                "title": "[Tranzor] Translations for MR!4003",
                "source_branch": "tranzor/translate-4003-aaa-bbb-othertsk",
                "references": {"full": "common/uns!4300"}}]
        known = md.DeliveryRef("common/uns", 4213)
        chain = md.trans_mr_chain(mrs, 4003, task_id=self.TASK_ID,
                                  known=[known])
        self.assertEqual(self._iids(chain), [4213])

    def test_newest_import_mr_stands_in_without_a_digest_match(self):
        mrs = [
            {"iid": 4100, "state": "merged",
             "title": "[Tranzor] Translations for MR!4003",
             "source_branch": "tranzor/translate-4003-aaa-bbb-legacy01"},
            {"iid": 4200, "state": "merged",
             "title": "[Tranzor] Translations for MR!4003",
             "source_branch": "tranzor/translate-4003-ccc-ddd-legacy02"},
        ]
        chain = md.trans_mr_chain(mrs, 4003, task_id=self.TASK_ID,
                                  fallback_project="common/uns")
        self.assertEqual(self._iids(chain), [4200])

    def test_current_is_the_newest_open_mr_else_the_newest(self):
        def ref(iid, state):
            return md.DeliveryRef("p", iid, state=state)

        self.assertEqual(md.current_trans_mr(
            [ref(4213, "merged"), ref(4214, "merged"),
             ref(4233, "merged")]).iid, 4233)
        self.assertEqual(md.current_trans_mr(
            [ref(4213, "merged"), ref(4214, "opened"),
             ref(4233, "merged")]).iid, 4214)
        self.assertIsNone(md.current_trans_mr([]))


class FilterMatchTests(unittest.TestCase):

    def test_matches_source_or_delivery(self):
        task = {
            "project_id": "common/uns",
            "merge_request_iid": 3930,
            "delivery_mr_iid": 4192,
        }
        self.assertTrue(md.task_matches_mr_iid(task, {3930}))
        self.assertTrue(md.task_matches_mr_iid(task, {4192}))
        self.assertFalse(md.task_matches_mr_iid(task, {1}))

    def test_expand_filter_adds_source_when_lookup_is_a_delivery_mr(self):
        class _Client:
            def has_token(self):
                return True

            def get_merge_request(self, project_id, iid, **_kw):
                self.seen = (project_id, iid)
                return {
                    "iid": 4192,
                    "title": "[Tranzor] Translations for MR!3930",
                    "source_branch": "tranzor/translate-3930-abc-def-ghi",
                }

        client = _Client()
        iids = md.expand_mr_iid_filter(
            "4192", project_ids=["common/uns"], client=client)
        self.assertEqual(client.seen, ("common/uns", 4192))
        self.assertEqual(iids, {4192, 3930})

    def test_expand_filter_keeps_typed_iid_when_lookup_is_the_source(self):
        class _Client:
            def has_token(self):
                return True

            def get_merge_request(self, project_id, iid, **_kw):
                return {
                    "iid": 3930,
                    "title": "[L10nFeedback] ja-JP",
                    "source_branch": "RLZ-81787",
                }

        iids = md.expand_mr_iid_filter(
            3930, project_ids=["common/uns"], client=_Client())
        self.assertEqual(iids, {3930})


class FindDeliveryMrTests(unittest.TestCase):

    def test_search_term_and_pick(self):
        class _Client:
            def has_token(self):
                return True

            def list_merge_requests(self, search, **kwargs):
                self.search = search
                self.kwargs = kwargs
                return [{
                    "iid": 4192,
                    "title": "[Tranzor] Translations for MR!3930",
                    "source_branch": "tranzor/translate-3930-abc-def-ghi",
                    "web_url": (
                        "https://git.ringcentral.com/common/uns/"
                        "-/merge_requests/4192"),
                    "state": "opened",
                    "references": {"full": "common/uns!4192"},
                }]

        client = _Client()
        ref = md.find_delivery_mr(
            "common/uns", 3930, task_id="t1", client=client)
        self.assertEqual(client.search, "MR!3930")
        self.assertEqual(client.kwargs["project_id"], "common/uns")
        self.assertEqual(ref.iid, 4192)
        self.assertEqual(ref.project_id, "common/uns")
        self.assertEqual(ref.state, "opened")

    def test_no_token_returns_none(self):
        class _Client:
            def has_token(self):
                return False

            def list_merge_requests(self, *_a, **_k):
                raise AssertionError("must not search without a token")

        self.assertIsNone(md.find_delivery_mr(
            "common/uns", 3930, client=_Client()))


if __name__ == "__main__":
    unittest.main()
