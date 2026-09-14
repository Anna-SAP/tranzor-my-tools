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
