"""Detect "probably product / feature names" in source text that are
NOT in the Tranzor Terminology glossary.

Lillian's instinct: brand / product names in a fresh MR are where
translation goes wrong fastest, because translators may not know
they're meant to stay English (DNT), or may pick an inconsistent
translation. If we surface them as a 🆕 count on the Review Worklist
she can spot-check those rows first.

This module is **pure** — no I/O, no caching. The two halves:

1. :func:`extract_candidate_terms` — regex-based extraction over a
   chunk of source text. Returns a list of token-or-phrase strings,
   case preserved. Designed to over-include slightly (better one
   false positive than one missed brand name).

2. :func:`filter_unregistered` — set-difference against the registered
   glossary. Caller passes ``known_term_names_lower`` once per batch.

The known-name cache lives in :mod:`tranzor_terminology` — this module
doesn't fetch anything so it stays unit-testable offline.
"""
from __future__ import annotations

import re
from typing import Iterable


# ---------------------------------------------------------------------------
# Extraction regexes — annotated so future maintainers know why each rule
# exists. Picking the right inclusion is the entire game; over-inclusion
# leads to a 🆕 count Lillian learns to ignore, under-inclusion silently
# misses the brand names we exist to surface.
# ---------------------------------------------------------------------------

# 1) CamelCase / PascalCase / InternalCaps tokens. "RingCentral",
#    "MyAccount", "iPhone", "macOS". Single token; >= 2 letter runs where
#    at least one uppercase letter sits inside.
#
# Two shapes:
#   - leading uppercase + lowercase run + another uppercase: "RingCentral"
#   - leading lowercase run(s) + uppercase: "iPhone", "macOS" (the lowercase
#     run can be more than 1 char; "macOS" has "ma" before the "OS").
_CAMEL_TOKEN = re.compile(
    r"\b(?:[A-Z][a-z]+[A-Z][A-Za-z0-9]*"
    r"|[a-z]+[A-Z][A-Za-z0-9]*)\b"
)

# 2) Multi-word TitleCase phrases of 2-4 words. "Voice Call", "Engage
#    Voice", "Live Reports". The (?:\s+...) repetition is bounded so we
#    don't grab whole sentences when an entire UI string is title-cased.
_TITLE_PHRASE = re.compile(
    r"\b[A-Z][A-Za-z0-9]{2,}"
    r"(?:\s+[A-Z][A-Za-z0-9]{2,}){1,3}\b"
)

# 3) All-caps acronyms of 2-6 chars. "MR", "RCV", "SMS", "BUI". 1-char
#    acronyms ("I", "A") give too many false positives; 6+ is unusual
#    for product acronyms (and likely catches all-caps log codes we
#    don't want).
_ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")

# 4) brand-y tokens with a digit suffix: "OAuth2", "iOS14". Common in
#    product names. Restricted to digits trailing for tightness.
_BRAND_WITH_DIGIT = re.compile(r"\b[A-Z][A-Za-z]+[0-9]+\b")


# ---------------------------------------------------------------------------
# Negative filters — once we have the candidate set we knock out the
# obvious false positives. These are conservative; new entries should be
# easy to demo against a real corpus.
# ---------------------------------------------------------------------------

# Common English / UI words that show up TitleCased in headlines and
# button labels — they are NOT product names. Lowercased to compare.
_STOPWORDS_LOWER = frozenset({
    # Articles / prepositions / conjunctions
    "a", "an", "the", "and", "or", "but", "for", "to", "of", "in",
    "on", "at", "by", "with", "from", "as", "is", "are", "be",
    # Bidirectional helpers
    "yes", "no", "ok", "cancel", "save", "delete", "edit", "new",
    "open", "close", "back", "next", "done", "skip", "more",
    # Days / months (often capitalized)
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    # Status / action labels
    "active", "inactive", "enabled", "disabled", "loading", "error",
    "success", "warning", "info", "warning", "true", "false",
    "all", "none", "any", "select", "submit", "send", "reply",
    "post", "share", "search", "filter", "sort", "view", "show",
    # Common HTTP verbs and statuses that pop up in UI
    "get", "put", "post", "patch", "delete", "head", "options",
})

# All-caps tokens that are common acronyms but NOT product names —
# they shouldn't trigger 🆕.
_ACRONYM_BLOCKLIST = frozenset({
    "URL", "URI", "ID", "IDS", "HTTP", "HTTPS", "JSON", "XML", "PDF",
    "CSV", "API", "SDK", "UI", "UX", "GUI", "CLI", "TCP", "UDP",
    "IP", "DNS", "TLS", "SSL", "AM", "PM", "GMT", "UTC", "USA",
    "EU", "UK", "EUR", "USD", "PNG", "JPG", "GIF", "SVG", "MP3",
    "MP4", "ZIP", "TAR", "GZ", "TBD", "TODO", "FIXME", "NA",
    "OK", "AI", "ML", "QA",
})


def _trim_stopwords(tok: str) -> str:
    """For multi-word phrases, strip leading/trailing stopword tokens
    so "Open Live Reports Console" becomes "Live Reports Console" — the
    sentence-start verb pollutes the candidate otherwise.

    Single-word inputs returned unchanged.
    """
    if " " not in tok:
        return tok
    words = [w for w in tok.split() if w]
    # Trim from the left
    while words and words[0].lower() in _STOPWORDS_LOWER:
        words.pop(0)
    # Trim from the right
    while words and words[-1].lower() in _STOPWORDS_LOWER:
        words.pop()
    return " ".join(words)


def _is_meaningful_candidate(tok: str) -> bool:
    """Apply the negative filters. ``tok`` is one extracted candidate."""
    if not tok or len(tok) < 2:
        return False
    low = tok.lower()
    # Multi-word phrases — at least 2 non-stopword tokens remain. Trim
    # has already removed boundary stopwords; this just guards against
    # phrases like "the the" that survive trim.
    if " " in tok:
        words = [w for w in tok.split() if w]
        meaningful = [w for w in words if w.lower() not in _STOPWORDS_LOWER]
        return len(meaningful) >= 2
    # Single-word stopword → drop.
    if low in _STOPWORDS_LOWER:
        return False
    # All-caps blocklist → drop.
    if tok.isupper() and tok in _ACRONYM_BLOCKLIST:
        return False
    return True


def extract_candidate_terms(text: str | None) -> list[str]:
    """Return the list of candidate product/feature names found in text.

    Preserves original casing. Order is preserved. Duplicates within the
    same call are merged (first occurrence wins).

    Designed to be called per-issue source_text; downstream code is
    expected to flatten / dedupe across many issues.
    """
    if not text:
        return []
    seen: dict[str, None] = {}

    def _add(tok):
        if not tok:
            return
        tok = _trim_stopwords(tok.strip())
        if tok and tok not in seen and _is_meaningful_candidate(tok):
            seen[tok] = None

    # Multi-word phrases first so they "consume" their words before
    # acronym / camelcase passes re-find their pieces.
    for m in _TITLE_PHRASE.finditer(text):
        _add(m.group(0))
    for m in _CAMEL_TOKEN.finditer(text):
        _add(m.group(0))
    for m in _BRAND_WITH_DIGIT.finditer(text):
        _add(m.group(0))
    for m in _ACRONYM.finditer(text):
        _add(m.group(0))

    return list(seen.keys())


def filter_unregistered(
    candidates: Iterable[str],
    known_term_names_lower: set[str] | frozenset[str],
) -> list[str]:
    """Return only candidates whose lowercased form is NOT in
    ``known_term_names_lower``.

    Order preserved; dedupe by lowercase form (so "VoiceCall" and
    "voicecall" don't both surface).
    """
    out: list[str] = []
    seen_lower: set[str] = set()
    for c in candidates:
        key = (c or "").lower()
        if not key or key in seen_lower:
            continue
        if key in known_term_names_lower:
            continue
        seen_lower.add(key)
        out.append(c)
    return out


def extract_unregistered(
    text: str | None,
    known_term_names_lower: set[str] | frozenset[str],
) -> list[str]:
    """Convenience: extract candidates from ``text`` and filter against
    the glossary in one call. Equivalent to::

        filter_unregistered(extract_candidate_terms(text), known)
    """
    return filter_unregistered(
        extract_candidate_terms(text), known_term_names_lower,
    )
