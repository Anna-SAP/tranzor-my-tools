"""Regression tests for the trie-factored terminology regex and the
highlight memoization in terminology_highlight.

The trie pattern replaces the flat ~2.5k-way alternation for a large speedup,
but a highlight regression would silently corrupt every report — so we assert
the trie is byte-for-byte equivalent to the proven flat pattern across random
and adversarial inputs (prefix/suffix overlaps, non-word term endings, CJK,
case folds). We also assert the memo caches are correct and invalidated on a
term refresh.

Run:  python -m unittest test_terminology_highlight_trie
"""
from __future__ import annotations

import html
import os
import random
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terminology_highlight as th


# Tricky term set: shared prefixes (Call/Caller, Phone/Phonebook), multi-word
# phrases that overlap their own prefixes (Click / Click to Talk), terms that
# END in a non-word char (so they carry NO suffix guard while a shorter
# sibling does — the case that breaks naive (pre,suf)-grouped tries), and CJK.
TRICKY_TERMS = [
    "Call", "Caller", "Calling", "call queue", "call park", "Call.",
    "Phone", "Phonebook", "phone number",
    "Click", "Click to Talk",
    "AI", "API", "app", "apple", "application",
    "Account", "Account Settings",
    "C++", "etc.",
    "点击通话", "通话", "会议", "会议邀请",
]


def _flat(uniq):
    return re.compile(th._flat_alternation_pattern(uniq), re.IGNORECASE)


def _trie(uniq):
    return re.compile(th._trie_alternation_pattern(uniq), re.IGNORECASE)


def _dedupe(strings):
    uniq, seen = [], set()
    for s in strings:
        k = s.lower()
        if s and k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq


def _spans(pat, text):
    return [(m.start(), m.end()) for m in pat.finditer(text)]


class TestTrieEquivalence(unittest.TestCase):
    def test_trie_equals_flat_on_tricky_terms(self):
        uniq = _dedupe(TRICKY_TERMS)
        flat, trie = _flat(uniq), _trie(uniq)
        probes = []
        for t in uniq:
            probes += [t, t.lower(), t.upper(), t + "x", "x" + t,
                       t + " " + t, "(" + t + ")", t + "." + t,
                       "RingCentral" + t, t + "123"]
        for p in probes:
            self.assertEqual(_spans(flat, p), _spans(trie, p),
                             f"trie != flat for probe {p!r}")

    def test_trie_equals_flat_random_corpus(self):
        rng = random.Random(2026)
        uniq = _dedupe(TRICKY_TERMS)
        flat, trie = _flat(uniq), _trie(uniq)
        pool = TRICKY_TERMS + [" ", "  ", "-", "_", ".", "x", "(", ")",
                               "RingCentral", "123"]
        for _ in range(20000):
            k = rng.randint(0, 20)
            txt = "".join(rng.choice(pool) + (" " if rng.random() < .5 else "")
                          for _ in range(k))
            self.assertEqual(_spans(flat, txt), _spans(trie, txt),
                             f"trie != flat for {txt!r}")

    def test_build_alternation_regex_prefers_trie_and_matches(self):
        uniq = _dedupe(TRICKY_TERMS)
        pat = th._build_alternation_regex(uniq)
        self.assertIsNotNone(pat)
        # Longest-match-preferred: "Account Settings" wins over "Account".
        m = pat.search("open Account Settings now")
        self.assertEqual(m.group(0), "Account Settings")
        # Word-boundary guard: "Call" must not match inside "Calls"... but
        # "Caller" is itself a term, so use a non-term extension.
        self.assertIsNone(pat.fullmatch("Callx"))
        # CJK substring match (no boundary guard).
        self.assertTrue(pat.search("RingCentral点击通话"))

    def test_empty_input_returns_none(self):
        self.assertIsNone(th._build_alternation_regex([]))


class _TermFixture(unittest.TestCase):
    """Install a deterministic in-memory term list via the module's own
    loader so highlight_source exercises the real (trie) regex."""

    def setUp(self):
        self._saved = {
            "_list_loaded": th._list_loaded,
            "_source_re": th._source_re,
            "_name_to_meta": dict(th._name_to_meta),
            "fetch_list": th.term_api.fetch_terminology_list,
        }
        self._terms = [
            {"id": 1, "name": "Account Settings", "dnt": False},
            {"id": 2, "name": "Video Call", "dnt": False},
            {"id": 3, "name": "Voicemail", "dnt": True},
        ]
        th.term_api.fetch_terminology_list = lambda **k: list(self._terms)
        th._ensure_list_loaded(force_refresh=True)

    def tearDown(self):
        th.term_api.fetch_terminology_list = self._saved["fetch_list"]
        th._list_loaded = self._saved["_list_loaded"]
        th._source_re = self._saved["_source_re"]
        th._name_to_meta = self._saved["_name_to_meta"]
        th._src_hl_cache.clear()
        th._tr_hl_cache.clear()


class TestMemoization(_TermFixture):
    def test_repeated_highlight_is_cached_and_stable(self):
        src = html.escape("Open Account Settings then start a Video Call")
        out1 = th.highlight_source(src)
        self.assertIn("<mark", out1)
        self.assertIn(src, th._src_hl_cache)
        out2 = th.highlight_source(src)
        self.assertEqual(out1, out2)

    def test_oversized_not_cached(self):
        big = "x" * (th.MAX_HIGHLIGHT_SOURCE_CHARS + 1)
        self.assertEqual(th.highlight_source(big), big)
        self.assertNotIn(big, th._src_hl_cache)

    def test_force_refresh_invalidates_memo(self):
        src = html.escape("Account Settings")
        th.highlight_source(src)
        self.assertTrue(th._src_hl_cache)
        th._ensure_list_loaded(force_refresh=True)
        self.assertFalse(th._src_hl_cache,
                         "a term refresh must drop memoized renders")

    def test_memo_matches_uncached_render(self):
        # The cached output must equal a fresh, cache-bypassing render.
        src = html.escape("start a Video Call from Account Settings")
        cached = th.highlight_source(src)
        th._src_hl_cache.clear()
        fresh = th.highlight_source(src)
        self.assertEqual(cached, fresh)


class TestLocaleRegexFreshness(unittest.TestCase):
    """Regression for the adversarial-review finding: when new term details
    arrive (or the term list is refreshed), a previously-built per-locale
    regex must be rebuilt so newly-known term translations still highlight —
    invalidating only the render caches would leave a stale locale regex in
    place and silently drop those highlights."""

    def setUp(self):
        self._saved = {
            "_list_loaded": th._list_loaded,
            "_source_re": th._source_re,
            "_name_to_meta": dict(th._name_to_meta),
            "_detail_cache": dict(th._detail_cache),
            "_locale_re": dict(th._locale_re),
            "_locale_meta": dict(th._locale_meta),
            "fetch_list": th.term_api.fetch_terminology_list,
            "fetch_details": th.term_api.fetch_many_details,
        }
        th._detail_cache.clear()
        th._locale_re.clear()
        th._locale_meta.clear()
        th._src_hl_cache.clear()
        th._tr_hl_cache.clear()
        self._terms = [
            {"id": 1, "name": "Meeting", "dnt": False},
            {"id": 2, "name": "Account", "dnt": False},
        ]
        self._details = {
            1: {"id": 1, "name": "Meeting", "dnt": False,
                "translations": [{"language_code": "de-DE",
                                  "translated_name": "Besprechung"}]},
            2: {"id": 2, "name": "Account", "dnt": False,
                "translations": [{"language_code": "de-DE",
                                  "translated_name": "Konto"}]},
        }
        th.term_api.fetch_terminology_list = lambda **k: list(self._terms)
        th.term_api.fetch_many_details = (
            lambda ids, **k: {i: self._details[i] for i in ids
                              if i in self._details})
        th._ensure_list_loaded(force_refresh=True)

    def tearDown(self):
        th.term_api.fetch_terminology_list = self._saved["fetch_list"]
        th.term_api.fetch_many_details = self._saved["fetch_details"]
        th._list_loaded = self._saved["_list_loaded"]
        th._source_re = self._saved["_source_re"]
        th._name_to_meta = self._saved["_name_to_meta"]
        th._detail_cache.clear(); th._detail_cache.update(self._saved["_detail_cache"])
        th._locale_re.clear(); th._locale_re.update(self._saved["_locale_re"])
        th._locale_meta.clear(); th._locale_meta.update(self._saved["_locale_meta"])
        th._src_hl_cache.clear()
        th._tr_hl_cache.clear()

    def test_new_term_details_rebuild_existing_locale_regex(self):
        # First export only surfaces "Meeting" -> de regex knows "Besprechung".
        th.prefetch_for_rows(
            [{"source_text": "Open the Meeting", "target_language": "de-DE"}])
        out = th.highlight_translation(html.escape("Die Besprechung beginnt"),
                                       "de-DE")
        self.assertIn("<mark", out)
        self.assertIn("Besprechung", out)
        # "Konto" not known yet -> not highlighted.
        self.assertNotIn("<mark", th.highlight_translation(
            html.escape("Konto Saldo"), "de-DE"))

        # Second export surfaces "Account" -> new detail fetched -> the de
        # regex must be rebuilt to include "Konto".
        th.prefetch_for_rows(
            [{"source_text": "Open the Account", "target_language": "de-DE"}])
        out2 = th.highlight_translation(html.escape("Konto Saldo"), "de-DE")
        self.assertIn("<mark", out2)
        self.assertIn("Konto", out2)


if __name__ == "__main__":
    unittest.main()
