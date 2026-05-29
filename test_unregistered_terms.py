"""Tests for the unregistered-term extraction (PR-C).

The extraction is the entire signal — over-inclusion teaches Lillian to
ignore the 🆕 column, under-inclusion silently misses brand names. So
the test suite anchors every rule with at least one realistic example
and at least one "this must NOT trigger" case.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unregistered_terms as ut


class ExtractCamelCaseTests(unittest.TestCase):

    def test_classic_internal_caps(self):
        self.assertIn("RingCentral", ut.extract_candidate_terms("RingCentral"))

    def test_my_account(self):
        # MyAccount is a real Tranzor product surface.
        self.assertIn("MyAccount", ut.extract_candidate_terms("Open MyAccount."))

    def test_lowercase_first_letter(self):
        # iPhone-style: leading lowercase + internal uppercase.
        toks = ut.extract_candidate_terms("Use iPhone or macOS.")
        # iPhone is internal-caps; macOS has both lowercase start and internal cap.
        self.assertIn("iPhone", toks)
        self.assertIn("macOS", toks)

    def test_plain_word_not_extracted(self):
        # Single capital + all-lowercase suffix is just a normal title word.
        toks = ut.extract_candidate_terms("Hello world.")
        self.assertNotIn("Hello", toks)
        self.assertNotIn("World", toks)


class ExtractTitlePhraseTests(unittest.TestCase):

    def test_two_word_product(self):
        toks = ut.extract_candidate_terms("Engage Voice supports SMS.")
        self.assertIn("Engage Voice", toks)

    def test_three_word_feature_name(self):
        toks = ut.extract_candidate_terms("Open Live Reports Console now.")
        self.assertIn("Live Reports Console", toks)

    def test_phrase_with_stopwords_filtered(self):
        # "And Or" is two stopwords in titlecase — must not trigger.
        toks = ut.extract_candidate_terms("And Or text appears.")
        self.assertNotIn("And Or", toks)

    def test_sentence_start_title_not_a_product(self):
        # Single capitalized sentence-start word should NOT count.
        toks = ut.extract_candidate_terms("Welcome to the dashboard.")
        self.assertNotIn("Welcome", toks)


class ExtractAcronymTests(unittest.TestCase):

    def test_product_acronym(self):
        toks = ut.extract_candidate_terms("Open RCV settings to enable BUI.")
        self.assertIn("RCV", toks)
        self.assertIn("BUI", toks)

    def test_blocklist_drops_well_known_acronyms(self):
        # URL / API / JSON / SDK shouldn't pollute the 🆕 column.
        toks = ut.extract_candidate_terms(
            "Hit this URL via the API; the JSON SDK handles parsing.")
        for blocked in ("URL", "API", "JSON", "SDK"):
            self.assertNotIn(blocked, toks, f"{blocked} leaked through")

    def test_single_letter_not_acronym(self):
        # Single letter acronyms create constant noise.
        toks = ut.extract_candidate_terms("Press T to toggle.")
        self.assertNotIn("T", toks)

    def test_long_token_not_acronym(self):
        # 7+ chars all-caps is unusual for product acronyms; usually a
        # constant / log code. The min-len-6 cap stops it.
        toks = ut.extract_candidate_terms("LONGCODE was emitted.")
        self.assertNotIn("LONGCODE", toks)


class ExtractBrandWithDigitTests(unittest.TestCase):

    def test_oauth2_extracted(self):
        toks = ut.extract_candidate_terms("Use OAuth2 to sign in.")
        self.assertIn("OAuth2", toks)


class FilterUnregisteredTests(unittest.TestCase):

    def test_known_term_filtered_case_insensitive(self):
        known = {"ringcentral"}
        out = ut.filter_unregistered(["RingCentral", "MyAccount"], known)
        self.assertEqual(out, ["MyAccount"])

    def test_empty_known_keeps_all(self):
        out = ut.filter_unregistered(["RingCentral", "MyAccount"], set())
        self.assertEqual(out, ["RingCentral", "MyAccount"])

    def test_dedup_by_lowercase(self):
        # "VoiceCall" and "voicecall" collapse to one candidate.
        out = ut.filter_unregistered(
            ["VoiceCall", "voicecall", "VoiceCall"], set())
        self.assertEqual(out, ["VoiceCall"])


class ExtractUnregisteredEndToEndTests(unittest.TestCase):

    def test_real_ui_string(self):
        # Realistic UI string with a known term (RingCentral) and an
        # unknown one (LiveReports). Only LiveReports should surface.
        text = ("RingCentral now ships LiveReports — try Engage Voice "
                "for advanced metrics. URL: https://example.com/JSON")
        known = {"ringcentral", "engage voice"}
        out = ut.extract_unregistered(text, known)
        # LiveReports is the only new product name we expect.
        self.assertIn("LiveReports", out)
        # Known terms must NOT show up.
        self.assertNotIn("RingCentral", out)
        self.assertNotIn("Engage Voice", out)
        # Stopwords / blocklisted acronyms must NOT show up.
        for noise in ("URL", "JSON", "Try", "now"):
            self.assertNotIn(noise, out)

    def test_empty_text(self):
        self.assertEqual(ut.extract_unregistered("", set()), [])
        self.assertEqual(ut.extract_unregistered(None, set()), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
