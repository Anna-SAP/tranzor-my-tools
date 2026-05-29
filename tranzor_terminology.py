"""
Tranzor Platform Terminology client (read-only).

Provides three things:

- ``fetch_terminology_list(...)``: paginated listing of all terms
  (id / code / name / scope / dnt / translation_count).
- ``fetch_terminology_detail(numeric_id)``: full term incl. translations.
- ``term_detail_to_rules(detail, severity)``: convert one term into a
  list of :class:`terminology_watchtower.TermRule`, one per locale.

This is the *single source of truth* glossary for the team — all
selection / filtering / preview happens in the GUI dialog; this module
just talks to the platform.

The endpoint shape was reverse-engineered from the official Tranzor
Platform UI bundle (``/static/assets/index-*.js``):

    GET  /context/api/v1/terminology?page=N&page_size=M
    GET  /context/api/v1/terminology/{numeric_id}

It is intentionally tolerant of unexpected fields so a backend change
doesn't crash my-tools.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terminology_watchtower as tw

# ---------------------------------------------------------------------------
# Config (mirror existing Tranzor base URL convention used by other modules)
# ---------------------------------------------------------------------------

TRANZOR_URL = "http://tranzor-platform.int.rclabenv.com"
TERMINOLOGY_API = f"{TRANZOR_URL}/context/api/v1/terminology"
TERMINOLOGY_WEB = f"{TRANZOR_URL}/static/terminology"


def terminology_app_url() -> str:
    """Browser URL for the Tranzor Platform terminology SPA entry.

    The SPA renders per-term details inside a modal and does not expose
    a deep link per id, so callers that need to show a single term
    should fetch :func:`fetch_terminology_detail` and render the data
    in-app instead of opening a browser tab here.
    """
    return TERMINOLOGY_WEB

PAGE_SIZE_MAX = 200  # server caps anything higher at 200
DEFAULT_TIMEOUT = 30
DETAIL_FETCH_WORKERS = 8

_session = requests.Session()


def _get(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    return _session.get(url, **kwargs)


# ---------------------------------------------------------------------------
# List + detail
# ---------------------------------------------------------------------------

def fetch_terminology_page(
    page: int = 1, page_size: int = PAGE_SIZE_MAX,
) -> Dict[str, Any]:
    """Fetch one page of the terminology list. Raises on HTTP error."""
    page_size = max(1, min(page_size, PAGE_SIZE_MAX))
    r = _get(TERMINOLOGY_API,
             params={"page": page, "page_size": page_size})
    r.raise_for_status()
    return r.json()


def fetch_terminology_list(
    *, progress_cb: Optional[Callable[[int, int], None]] = None,
    max_terms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch the full terminology list (all pages).

    Each row is the LIST shape: ``{id, code, name, scope, dnt,
    translation_count, variant_count}``. Translation values are NOT
    included; call :func:`fetch_terminology_detail` per selected term.

    progress_cb(loaded, total) is called per page.
    """
    out: List[Dict[str, Any]] = []
    first = fetch_terminology_page(page=1, page_size=PAGE_SIZE_MAX)
    total = int(first.get("total") or 0)
    items = first.get("items") or []
    out.extend(items)
    if progress_cb:
        try:
            progress_cb(len(out), total)
        except Exception:
            pass

    if max_terms is not None and len(out) >= max_terms:
        return out[:max_terms]

    page = 2
    while len(out) < total:
        body = fetch_terminology_page(page=page, page_size=PAGE_SIZE_MAX)
        items = body.get("items") or []
        if not items:
            break
        out.extend(items)
        if progress_cb:
            try:
                progress_cb(len(out), total)
            except Exception:
                pass
        if max_terms is not None and len(out) >= max_terms:
            return out[:max_terms]
        page += 1
    return out


def fetch_terminology_detail(numeric_id: int) -> Dict[str, Any]:
    """Fetch one term's full detail including translations."""
    r = _get(f"{TERMINOLOGY_API}/{int(numeric_id)}")
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Lazy-cached "set of known term names" — drives the Review Worklist's 🆕
# column (PR-C). Owned here so the worklist code doesn't reach into the
# HTTP layer and the cache TTL is settable from one spot.
# ---------------------------------------------------------------------------
_KNOWN_NAMES_TTL_SECS = 6 * 3600   # 6h — terms change slowly; refresh is cheap.
_known_names_cache: frozenset[str] = frozenset()
_known_names_fetched_at: float = 0.0
_known_names_lock = threading.Lock()


def load_known_term_names_lower(
    *, force_refresh: bool = False,
) -> frozenset[str]:
    """Return the set of registered term names in lowercase form.

    Cached for :data:`_KNOWN_NAMES_TTL_SECS`. Falls back to the previous
    cache (or empty frozenset on first failure) if the platform is
    unreachable — the Worklist 🆕 column degrades to "everything looks
    unregistered" rather than crashing the tab.

    Safe to call from many threads; one slow refresh blocks the others
    but won't double-fetch.
    """
    global _known_names_cache, _known_names_fetched_at
    now = time.time()
    with _known_names_lock:
        if not force_refresh and _known_names_cache and (
            now - _known_names_fetched_at < _KNOWN_NAMES_TTL_SECS
        ):
            return _known_names_cache
        try:
            entries = fetch_terminology_list()
        except Exception:
            # Platform unreachable — keep the previous cache, just don't
            # refresh the timestamp so the next attempt happens sooner.
            return _known_names_cache
        names = {
            (e.get("name") or "").strip().lower()
            for e in entries if e.get("name")
        }
        names.discard("")
        _known_names_cache = frozenset(names)
        _known_names_fetched_at = now
        return _known_names_cache


def fetch_many_details(
    numeric_ids: Iterable[int],
    *, progress_cb: Optional[Callable[[int, int], None]] = None,
    max_workers: int = DETAIL_FETCH_WORKERS,
) -> Dict[int, Dict[str, Any]]:
    """Parallel detail fetch. Returns ``{id: detail_dict}``.

    Failures are dropped silently so a single 5xx doesn't tank the batch.
    Use ``progress_cb(done, total)`` to drive a UI bar.
    """
    ids = list(numeric_ids)
    out: Dict[int, Dict[str, Any]] = {}
    if not ids:
        return out
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_to_id = {pool.submit(fetch_terminology_detail, i): i for i in ids}
        for fut in as_completed(fut_to_id):
            tid = fut_to_id[fut]
            try:
                out[tid] = fut.result()
            except Exception:
                pass
            done += 1
            if progress_cb:
                try:
                    progress_cb(done, len(ids))
                except Exception:
                    pass
    return out


# ---------------------------------------------------------------------------
# Convert detail → TermRule[]
# ---------------------------------------------------------------------------

def term_detail_to_rules(
    detail: Dict[str, Any],
    *,
    severity: str = "High",
    include_dnt_as_passthrough: bool = False,
) -> List[tw.TermRule]:
    """Convert one Tranzor term detail into a list of :class:`TermRule`,
    one per translation locale.

    DNT (do not translate) terms are skipped by default — they have no
    "approved translation" semantics. Set ``include_dnt_as_passthrough``
    to True to emit rules that *expect the source term itself* to appear
    in target text (one rule per locale_code seen in the DB), but Phase 1
    keeps this off by default to avoid surprising the user.
    """
    name = (detail.get("name") or "").strip()
    if not name:
        return []
    code = (detail.get("code") or "").strip()
    notes_parts: List[str] = ["from Tranzor Terminology"]
    if detail.get("part_of_speech"):
        notes_parts.append(f"pos: {detail['part_of_speech']}")
    if detail.get("definition"):
        notes_parts.append(f"def: {detail['definition']}")
    if detail.get("context"):
        notes_parts.append(f"ctx: {detail['context']}")
    if detail.get("notes"):
        notes_parts.append(f"notes: {detail['notes']}")
    notes = " | ".join(notes_parts)

    is_dnt = bool(detail.get("dnt"))
    rules: List[tw.TermRule] = []
    translations = detail.get("translations") or []

    if is_dnt and not include_dnt_as_passthrough:
        return []

    for tr in translations:
        locale_raw = (tr.get("language_code") or "").strip()
        if not locale_raw:
            continue
        approved = (tr.get("translated_name") or "").strip()
        if not approved and is_dnt:
            approved = name  # DNT passthrough
        if not approved:
            continue
        rid = "TZ-" + (code or str(detail.get("id") or "")) + \
              "::" + tw.normalize_locale(locale_raw)
        rules.append(tw.TermRule(
            rule_id=rid,
            source_term=name,
            locale=tw.normalize_locale(locale_raw),
            approved_translation=approved,
            source_aliases=[],
            forbidden_translations=[],
            product_scope=[],
            severity=severity,
            case_sensitive=False,
            enabled=True,
            notes=notes,
            locale_display=locale_raw,
        ))
    return rules


def details_to_rules(
    details: Iterable[Dict[str, Any]],
    *,
    severity: str = "High",
    include_dnt_as_passthrough: bool = False,
    apply_locale_fallbacks: bool = True,
) -> List[tw.TermRule]:
    """Bulk convert Tranzor terminology details into ``TermRule``s.

    Tranzor's terminology library only stores a fixed set of locales,
    which currently does *not* include zh-HK / zh-MO etc. Real Tranzor
    translation output, however, *does* contain those locales. To avoid
    silently skipping zh-HK candidates, we automatically synthesize a
    fallback rule from the closest sister locale (zh-TW) per term —
    see :func:`terminology_watchtower.expand_locale_fallbacks`.

    Set ``apply_locale_fallbacks=False`` to opt out (e.g. for tests
    that want a strict 1:1 mapping with the API response).
    """
    out: List[tw.TermRule] = []
    for d in details:
        out.extend(term_detail_to_rules(
            d, severity=severity,
            include_dnt_as_passthrough=include_dnt_as_passthrough,
        ))
    if apply_locale_fallbacks:
        out = tw.expand_locale_fallbacks(out)
    return out
