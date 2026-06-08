"""
Auto-highlight Tranzor terminology in HTML reports.

Two surfaces:

1. :func:`highlight_source` — wraps occurrences of any term name (English
   source side) in <mark> tags. Uses a single compiled alternation regex
   built from the full term list (~2.5k entries) cached at module level.
   Cheap; needs only the LIST endpoint (paginated, ~13 calls).

2. :func:`highlight_translation` — wraps occurrences of approved
   translations in a given locale. Needs term DETAIL (translations[]),
   which the API only exposes per-term. To keep cost bounded, callers
   first invoke :func:`prefetch_for_rows` which (a) scans the source
   text of each row to learn which term IDs actually appear, then (b)
   fetches detail for those IDs only (parallel via fetch_many_details).
   The per-locale regex is then built from the cached details.

Both functions accept ALREADY-HTML-ESCAPED text and return text with
``<mark>`` tags inserted. Safe to call even if loading failed — they
degrade to returning the input unchanged.

Markup:
    <mark class="term-hl">term</mark>           regular term
    <mark class="term-hl dnt">term</mark>       DNT (do not translate)

Word boundary: ASCII-only (``(?<![A-Za-z0-9_])`` ... ``(?![A-Za-z0-9_])``).
That guards English terms from matching inside other words ("AI" won't
match inside "Mail") while letting CJK terms — which have no word
boundaries — match as substrings. Pure-CJK source rarely contains
ambiguous prefix/suffix overlap.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence

import tranzor_terminology as term_api
from terminology_watchtower import normalize_locale

# Output class names. Kept short — they appear inline on every match.
HL_CLASS = "term-hl"
HL_CLASS_DNT = "term-hl dnt"

# CSS block to embed in each report's <style> section. Hot pink for DNT
# wins the "most eye-catching" mandate — its white-on-magenta contrast
# is impossible to miss next to the softer amber used for regular terms.
#
# Braces are pre-doubled ("{{" / "}}") because every report writer in
# this codebase builds its <style> block inside an f-string. Embedding
# raw "{ ... }" would trip f-string parsing. Don't strip the doubling.
HIGHLIGHT_CSS = """
mark.term-hl {{
    background: #fef3c7;
    color: #78350f;
    padding: 0 3px;
    border-radius: 3px;
    box-shadow: inset 0 -2px 0 0 #fbbf24;
    font-weight: 500;
}}
mark.term-hl.dnt {{
    background: #ec4899;
    color: #ffffff;
    box-shadow: inset 0 -2px 0 0 #be185d, 0 0 0 1px #be185d;
    font-weight: 600;
}}
"""

def _parse_dnt(value: Any) -> bool:
    """Robustly interpret the API's DNT flag.

    The Tranzor terminology API has been observed returning DNT as:
    bool ``True``/``False``, int 0/1, or string ``"true"``/``"false"`` /
    ``"yes"``/``"no"``. A naive ``bool(value)`` mishandles the strings
    (``bool("false") == True``), so this normaliser is the single point
    of truth for "is this term DNT?".
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        return v in ("true", "yes", "y", "1")
    return False


# ---------------------------------------------------------------------------
# Module state (process-wide caches, lazy-initialised, thread-safe).
# ---------------------------------------------------------------------------
_lock = threading.Lock()

# Source side
_list_loaded: bool = False
_source_re: Optional[re.Pattern] = None
# lowercase name -> {"name", "id", "dnt"}
_name_to_meta: Dict[str, Dict[str, Any]] = {}

# Per-locale (translation) side
# id -> detail dict (with "translations" array)
_detail_cache: Dict[int, Dict[str, Any]] = {}
# normalized locale -> compiled regex
_locale_re: Dict[str, Optional[re.Pattern]] = {}
# normalized locale -> {lowercase_match -> {"name", "source_name", "dnt"}}
_locale_meta: Dict[str, Dict[str, Dict[str, Any]]] = {}

# Skip terminology highlighting for oversized source texts (e.g. UNS
# handlebars email templates: the whole file is ONE translation unit, ~8KB of
# HTML). Such a text matches dozens of glossary terms, and prefetch_for_rows
# then fetches a context-service detail for each (up to DEFAULT_TIMEOUT=30s
# per call). For a single MR Changes row this is ~7-8s of API traffic and, if
# the context-service is slow/unreachable on the operator's session, makes the
# export appear hung at "Exporting...". Normal UI strings are far below this
# bound, so the guard removes the cost+hang for whole-file templates while
# leaving highlighting intact everywhere else.
MAX_HIGHLIGHT_SOURCE_CHARS = 4000


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preload(force_refresh: bool = False) -> bool:
    """Eagerly fetch the term list and build the source-side regex.

    Returns True on success (regex ready), False on degraded mode (no
    network / API error). Safe to call from a background thread at GUI
    startup so the first export doesn't pay the latency.
    """
    return _ensure_list_loaded(force_refresh=force_refresh) is not None


def highlight_source(escaped_text: str) -> str:
    """Wrap English term-name matches in *escaped* text with <mark>.

    Pass HTML-escaped text. The function returns the input unchanged if
    the term list failed to load.
    """
    if not escaped_text or len(escaped_text) > MAX_HIGHLIGHT_SOURCE_CHARS:
        return escaped_text
    pat = _ensure_list_loaded()
    if pat is None:
        return escaped_text
    return pat.sub(_source_repl, escaped_text)


def highlight_translation(escaped_text: str, locale: Optional[str]) -> str:
    """Wrap matches against the per-locale translated-name set.

    No-op if :func:`prefetch_for_rows` was not called for this locale,
    or if the locale has no matched terms in the cache. DNT terms whose
    source name is expected to appear verbatim in the translation are
    included automatically.
    """
    if not escaped_text or not locale or len(escaped_text) > MAX_HIGHLIGHT_SOURCE_CHARS:
        return escaped_text
    norm = normalize_locale(locale)
    pat = _locale_re.get(norm)
    if pat is None:
        return escaped_text
    meta_map = _locale_meta.get(norm) or {}
    def repl(m: re.Match) -> str:
        meta = meta_map.get(m.group(0).lower(), {})
        is_dnt = bool(meta.get("dnt"))
        cls = HL_CLASS_DNT if is_dnt else HL_CLASS
        return (f'<mark class="{cls}" '
                f'data-dnt="{"true" if is_dnt else "false"}">'
                f'{m.group(0)}</mark>')
    return pat.sub(repl, escaped_text)


def prefetch_for_rows(
    rows: Sequence[Dict[str, Any]],
    *,
    source_field: str = "source_text",
    lang_field: str = "target_language",
) -> None:
    """Populate per-locale caches for the languages present in *rows*.

    Steps:
      1. Scan each row's source text for term-name hits → collect IDs.
      2. Fetch detail for those IDs (parallel, dedup-against-cache).
      3. Build per-locale regex for each locale in *rows*.

    Safe to call repeatedly; each subsequent call only fetches what's
    missing from the cache.
    """
    _ensure_list_loaded()
    if _source_re is None or not _name_to_meta:
        return

    # 1. Source scan to find which term IDs actually appear.
    hit_ids: set = set()
    for row in rows:
        src = row.get(source_field) or ""
        if not src or len(src) > MAX_HIGHLIGHT_SOURCE_CHARS:
            # Oversized (UNS whole-file template): scanning 8KB would trigger
            # dozens of context-service detail fetches and can hang the export.
            continue
        for m in _source_re.finditer(src):
            meta = _name_to_meta.get(m.group(0).lower())
            if meta and meta.get("id") is not None:
                hit_ids.add(meta["id"])

    # 2. Fetch detail for IDs we haven't cached yet.
    to_fetch = [i for i in hit_ids if i not in _detail_cache]
    if to_fetch:
        try:
            new_details = term_api.fetch_many_details(to_fetch)
        except Exception:
            new_details = {}
        with _lock:
            _detail_cache.update(new_details)

    # Always sync DNT from DETAIL back into the source-side meta map for
    # *every* hit term that has a detail cached — not just the ones we
    # just fetched. Earlier calls may have populated _detail_cache; this
    # ensures the source-side regex picks up the authoritative dnt flag
    # on every render. Sync by ID (not name) so a slight name mismatch
    # between LIST and DETAIL responses doesn't silently skip the sync.
    if _detail_cache:
        with _lock:
            id_to_meta = {
                m.get("id"): m
                for m in _name_to_meta.values()
                if m.get("id") is not None
            }
            for tid in hit_ids:
                detail = _detail_cache.get(tid)
                if not detail:
                    continue
                meta = id_to_meta.get(tid)
                if meta is None:
                    continue
                meta["dnt"] = _parse_dnt(detail.get("dnt"))

    # 3. Determine locales we still need a regex for.
    locales_needed = set()
    for row in rows:
        loc = (row.get(lang_field) or "").strip()
        if loc:
            locales_needed.add(normalize_locale(loc))

    for locale in locales_needed:
        if locale in _locale_re:
            continue
        _build_locale_regex(locale)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _source_repl(m: re.Match) -> str:
    meta = _name_to_meta.get(m.group(0).lower(), {})
    is_dnt = bool(meta.get("dnt"))
    cls = HL_CLASS_DNT if is_dnt else HL_CLASS
    # data-* attributes make View Source self-documenting: a user can
    # confirm which class was picked (and whether DNT was detected)
    # without having to re-instrument the build.
    tid = meta.get("id")
    tid_attr = f' data-term-id="{tid}"' if tid is not None else ""
    return (f'<mark class="{cls}"{tid_attr} '
            f'data-dnt="{"true" if is_dnt else "false"}">'
            f'{m.group(0)}</mark>')


def _ensure_list_loaded(force_refresh: bool = False) -> Optional[re.Pattern]:
    global _list_loaded, _source_re, _name_to_meta
    if _list_loaded and not force_refresh:
        return _source_re
    with _lock:
        if _list_loaded and not force_refresh:
            return _source_re
        try:
            terms = term_api.fetch_terminology_list()
        except Exception:
            # Mark loaded so we don't retry on every call; degraded mode.
            _list_loaded = True
            return None
        name_to_meta: Dict[str, Dict[str, Any]] = {}
        for t in terms:
            name = (t.get("name") or "").strip()
            if not name:
                continue
            # First write wins on case-insensitive collisions — terms
            # that differ only in case are vanishingly rare; if any
            # appear, both will still highlight identically.
            key = name.lower()
            if key in name_to_meta:
                continue
            name_to_meta[key] = {
                "name": name,
                "id": t.get("id"),
                "dnt": _parse_dnt(t.get("dnt")),
            }
        _name_to_meta = name_to_meta
        _source_re = _build_alternation_regex(
            [m["name"] for m in name_to_meta.values()]
        )
        _list_loaded = True
        return _source_re


def _build_locale_regex(locale: str) -> None:
    """Populate _locale_re[locale] and _locale_meta[locale] from
    _detail_cache. Called once per locale; subsequent invocations are
    no-ops because _locale_re[locale] gets set unconditionally (None on
    empty)."""
    items: Dict[str, Dict[str, Any]] = {}
    for _tid, detail in _detail_cache.items():
        src_name = (detail.get("name") or "").strip()
        is_dnt = _parse_dnt(detail.get("dnt"))
        translated_for_locale = ""
        for tr in detail.get("translations") or []:
            if normalize_locale(tr.get("language_code") or "") == locale:
                translated_for_locale = (tr.get("translated_name") or "").strip()
                break
        if translated_for_locale:
            key = translated_for_locale.lower()
            # On collision keep the first — see _ensure_list_loaded.
            if key not in items:
                items[key] = {
                    "name": translated_for_locale,
                    "source_name": src_name,
                    "dnt": is_dnt,
                }
        if is_dnt and src_name:
            # DNT terms are expected to appear verbatim in translations.
            # Add the source name itself to the match set so DNT shows up
            # in translation cells too.
            key = src_name.lower()
            if key not in items:
                items[key] = {
                    "name": src_name,
                    "source_name": src_name,
                    "dnt": True,
                }
    with _lock:
        _locale_meta[locale] = items
        _locale_re[locale] = _build_alternation_regex(
            [v["name"] for v in items.values()]
        )


def _build_alternation_regex(strings: Iterable[str]) -> Optional[re.Pattern]:
    """Build a single case-insensitive alternation regex with ASCII
    word-boundary guards. Longer matches come first so multi-word terms
    win against their shorter prefixes ("Click to Talk" beats "Click").

    Each alternative carries only the boundary assertions that match
    its own edge characters. A term that *starts* with an ASCII word
    char gets a leading ``(?<![A-Za-z0-9_])``; one that *ends* with
    one gets a trailing ``(?![A-Za-z0-9_])``. A pure-CJK term gets
    neither, so it still matches when wedged between English and CJK
    text (e.g. ``RingCentral点击通话`` — without per-term boundaries
    the trailing ``l`` of ``RingCentral`` would block ``点击通话``).

    Returns None if there are no strings to match.
    """
    uniq: List[str] = []
    seen: set = set()
    for s in strings:
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    if not uniq:
        return None
    uniq.sort(key=len, reverse=True)
    _ASCII_WORD = re.compile(r"[A-Za-z0-9_]")
    pieces: List[str] = []
    for s in uniq:
        esc = re.escape(s)
        prefix = r"(?<![A-Za-z0-9_])" if _ASCII_WORD.match(s[0]) else ""
        suffix = r"(?![A-Za-z0-9_])" if _ASCII_WORD.match(s[-1]) else ""
        pieces.append(prefix + esc + suffix)
    pattern = "(?:" + "|".join(pieces) + ")"
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None
