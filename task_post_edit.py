"""
Detect whether a Tranzor task contains any "post-edited" translation.

A task is considered post-edited when **a human** has changed at least one
translation after the machine produced it. We deliberately do NOT count
auto-refinement (iteration > 1 with text drift) — auto-refine is also
machine work, just from a later iteration. See PR description for the
rationale and the parallel with the Human Revisions tab's stricter mode.

Per-channel rule:

- File Translation (legacy) — translations whose
  ``translation_type in ("Manual Edit", "LLM Retranslate")``.
- Scan Tasks — same rule as legacy (the scan results endpoint emits
  translation entries with the same shape).
- MR Pipeline — **two paths in OR**, because Tranzor has two distinct
  human-revision mechanisms that land in different stores:

  1. **GitLab fix commit** — every Language Lead fix (single-row OR bulk)
     is pushed by the Tranzor service account as a commit on the MR
     (title ``[Tranzor] Language Lead fix: …`` / ``… batch fix: N
     translation(s)``). Detected in ONE round-trip via the MR's own
     commits — ``GET /merge_requests/:iid/commits`` — and the shared
     ``gitlab_client.is_lead_fix_commit`` fingerprint. This replaced the
     older get_merge_request + ``/repository/commits?ref_name=<branch>``
     pair (two slow GitLab calls → one) that dominated the
     "✏️ Post-edited only" filter's latency.

  2. **fixed_by_lead** — a dashboard fix recorded on
     ``MrTranslation.fixed_by_lead`` whose commit isn't visible on the MR
     (e.g. a merged MR, or a fix pushed to a separate branch). Detected
     via ``/dashboard/cases?mr_id=…`` (heavier, ~1-2MB) — only consulted
     when path 1 finds nothing.

  Both paths are checked; either ✏️ wins.

API surface:

- :func:`has_post_edit_legacy(translations)`  →  bool
- :func:`has_post_edit_scan(translations)`    →  bool
- :func:`has_post_edit_mr_from_cases(cases)`  →  bool
- :class:`PostEditCache`  — process-wide thread-safe cache so the GUI
  doesn't re-fetch when the user pages back and forth.
- :func:`prefetch_async(...)`  — fire-and-forget background fetch with
  a UI-thread callback per task.

The GUI plumbs ``prefetch_async`` after each page render: the tree
shows the page immediately, and ✏️ prefixes light up incrementally as
detail fetches return.
"""
from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# Sentinel used by the GUI to prefix task names. Kept here so all three
# tabs render the same glyph; if a user wants to swap it (e.g. for a CJK
# environment where ✏️ renders ugly) they change one constant.
POST_EDIT_PREFIX = "✏️ "


# Set of translation_type values that mean "a human touched this row".
# Sourced from export_mr_pipeline._collect_legacy_revisions: those two
# strings are how Tranzor's File Translation backend tags Language-Lead
# manual fixes and LLM-assisted retranslations performed by a reviewer.
_HUMAN_TYPES = frozenset({"Manual Edit", "LLM Retranslate"})

# The GitLab fix-commit fingerprint (service-account email + title prefixes)
# now lives in gitlab_client (LEAD_FIX_AUTHOR_EMAIL / is_lead_fix_commit), so
# the MR-commits probe and PR #103's source-branch scan share one definition.


# ---------------------------------------------------------------------------
# Pure logic — call these with already-fetched data so they're unit-testable
# without HTTP.
# ---------------------------------------------------------------------------

def has_post_edit_legacy(translations: Iterable[dict]) -> bool:
    """True iff any translation entry has ``translation_type`` in
    :data:`_HUMAN_TYPES`. Empty / None ⇒ False."""
    if not translations:
        return False
    for tr in translations:
        if (tr.get("translation_type") or "") in _HUMAN_TYPES:
            return True
    return False


# Scan results emit entries with the same ``translation_type`` shape.
has_post_edit_scan = has_post_edit_legacy


def has_post_edit_mr_from_cases(cases: Iterable[dict]) -> bool:
    """True iff any case has a non-empty ``fixed_by_lead`` — that's how
    Tranzor records Language Lead manual fixes on MR Pipeline. Auto-
    refinement (``iteration > 1``) is intentionally NOT counted; see
    module docstring."""
    if not cases:
        return False
    for c in cases:
        if c.get("fixed_by_lead"):
            return True
    return False


# ---------------------------------------------------------------------------
# Per-channel fetchers — wrap the existing API client so we can mock at
# test time and so callers don't pull export_mr_pipeline into their imports.
# ---------------------------------------------------------------------------

def _fetch_legacy(task_id: str) -> bool:
    """Detect whether a File Translation task has any human-edited row.

    Uses the server-side ``label_types=post_edited`` filter so this is a
    single tiny request (``total > 0``) regardless of task size — the
    older path fetched ALL translations and filtered client-side, which
    on multi-thousand-row tasks took long enough that the GUI sometimes
    never showed the ✏️ badge before the user took action (e.g. Task
    253 / LOC-24765 in the PR #76 regression).

    Falls back to the full client-side scan ONLY when the server filter
    fails (older Tranzor backend that doesn't recognise
    ``label_types=post_edited``) — the fallback preserves correctness at
    the original cost.
    """
    import export_mr_pipeline as _mp
    try:
        return _mp.fetch_legacy_post_edit_total(task_id) > 0
    except Exception:
        # Backend doesn't expose the filter (e.g. pinned to an older
        # Tranzor) — fall back to the original full-scan behaviour so
        # the badge still appears, just more slowly.
        trs = _mp.fetch_all_legacy_translations_quality(task_id)
        return has_post_edit_legacy(trs)


def _fetch_scan(task_id: str) -> bool:
    import export_mr_pipeline as _mp
    results = _mp.fetch_scan_results(task_id) or {}
    return has_post_edit_scan(results.get("translations") or [])


_shared_client = None
_shared_client_lock = threading.Lock()


def _shared_gitlab_client():
    """One process-wide GitLabClient so the parallel post-edit prefetch reuses
    TCP/TLS connections to the (latency-heavy) GitLab host instead of paying a
    fresh handshake per MR. Returns None if the client can't be built. The
    client's caches are append-only, so concurrent double-fetches across the
    prefetch threads are harmless.
    """
    global _shared_client
    if _shared_client is not None:
        return _shared_client
    with _shared_client_lock:
        if _shared_client is None:
            try:
                import gitlab_client as _gc
                _shared_client = _gc.GitLabClient()
            except Exception:
                return None
    return _shared_client


def _fetch_mr(key) -> bool:
    """Detect whether an MR contains a human post-edit.

    ``key`` is ``(project_id, mr_iid)`` so we can drive the BATCH_FIX
    branch lookup (project + source_branch resolved from the MR detail)
    in addition to the single-row dashboard-cases check.

    Backward-compat: if ``key`` is a bare ``mr_iid``, we fall back to the
    dashboard-cases-only path (old behaviour from PR #72) so any caller
    that hasn't been updated still works — just without BATCH_FIX
    detection.

    Detection order:

    1. **GitLab fix commit** (single OR batch) via the MR's own commits —
       ONE round-trip (``GET /merge_requests/:iid/commits``), no separate
       source-branch lookup. This is the cheap, common-case signal.
    2. **fixed_by_lead** (dashboard cases) — heavier (~1-2MB response);
       only consulted when path 1 finds nothing.

    Both run only on miss; first hit short-circuits.
    """
    if isinstance(key, (tuple, list)) and len(key) == 2:
        project_id, mr_iid = key
    else:
        project_id, mr_iid = None, key

    # Path 1: a Tranzor Language Lead fix commit on the MR. One GitLab call
    # via the MR's own commits — half the latency of the old
    # get_merge_request + branch-scan pair, which dominated the post-edit
    # filter. No-op if GitLab isn't reachable (no token / restricted env).
    if project_id:
        try:
            import gitlab_client as _gc
            client = _shared_gitlab_client()
            if client is not None and client.has_token():
                if _gc.mr_has_lead_fix_commit(client, project_id, int(mr_iid)):
                    return True
        except Exception:
            # Any failure on the fix-commit path falls through to the
            # dashboard-cases path — they're independent signals.
            pass

    # Path 2: single-row UI fix via dashboard cases.
    import export_mr_pipeline as _mp
    try:
        data = _mp.fetch_dashboard_cases(mr_id=int(mr_iid), mr_limit=1) or {}
    except Exception:
        return False
    mrs = data.get("mrs") or []
    if not mrs:
        return False
    return has_post_edit_mr_from_cases(mrs[0].get("cases") or [])


_FETCHERS: dict[str, Callable[[Any], bool]] = {
    "legacy": _fetch_legacy,
    "scan":   _fetch_scan,
    "mr":     _fetch_mr,
}


# ---------------------------------------------------------------------------
# Process-wide cache + async prefetch
# ---------------------------------------------------------------------------

class PostEditCache:
    """Thread-safe ``{(source_kind, key): bool}`` cache.

    Lifetime is process-scope; we deliberately do NOT persist across runs.
    Post-edit status can flip when reviewers touch a task, and a stale
    cache that says "no edit" when the truth is "now edited" is worse
    than a slightly slower second startup.
    """

    def __init__(self):
        self._data: dict[tuple[str, str], bool] = {}
        self._lock = threading.Lock()

    def get(self, source_kind: str, key) -> Optional[bool]:
        with self._lock:
            return self._data.get((source_kind, str(key)))

    def set(self, source_kind: str, key, value: bool) -> None:
        with self._lock:
            self._data[(source_kind, str(key))] = bool(value)

    def has(self, source_kind: str, key) -> bool:
        with self._lock:
            return (source_kind, str(key)) in self._data

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def clear_kind(self, source_kind: str) -> int:
        """Drop every cached answer for a given source kind. Returns the
        number of entries removed.

        Used by the GUI's Refresh button on the File Translation tab so
        a re-prefetch picks up edits the user made in the Tranzor
        Platform UI between the first render and the refresh — the
        previous ``False`` would otherwise stay cached forever.
        """
        with self._lock:
            stale = [k for k in self._data if k[0] == source_kind]
            for k in stale:
                self._data.pop(k, None)
            return len(stale)


# Module-level singleton — the GUI imports this name directly.
_cache = PostEditCache()


def get_cache() -> PostEditCache:
    return _cache


def prefetch_async(
    items: Iterable[tuple[str, Any]],
    *,
    on_result: Callable[[str, Any, bool], None],
    on_error: Optional[Callable[[str, Any, Exception], None]] = None,
    max_workers: int = 8,
    cache: Optional[PostEditCache] = None,
    cancel_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """Fire-and-forget prefetch.

    ``items`` is an iterable of ``(source_kind, key)``. ``key`` is the
    task_id for legacy/scan or the MR iid for mr.

    For each item we either:
      - serve from cache and call ``on_result(kind, key, value)`` straight
        away on the worker thread, OR
      - fetch via the per-channel API, write into the cache, then call
        ``on_result``. Failures invoke ``on_error`` (if given) — silence
        otherwise; the badge simply never appears for that row.

    Callbacks fire on the worker thread; the GUI must marshal back to Tk
    with ``widget.after(0, ...)``.

    Returns the spawned ``threading.Thread`` so the caller can join in
    tests. The thread is daemon — the GUI shutting down kills it.
    """
    items = list(items)
    cache = cache or _cache

    def _run():
        if not items:
            return
        # Short-circuit cache hits: notify synchronously, never spin up
        # threads for known answers.
        pending: list[tuple[str, Any]] = []
        for kind, key in items:
            if cancel_event and cancel_event.is_set():
                return
            cached = cache.get(kind, key)
            if cached is not None:
                try:
                    on_result(kind, key, cached)
                except Exception:
                    pass
                continue
            pending.append((kind, key))
        if not pending:
            return

        def _work(item):
            kind, key = item
            if cancel_event and cancel_event.is_set():
                return None
            fetcher = _FETCHERS.get(kind)
            if fetcher is None:
                return None
            try:
                value = fetcher(key)
            except Exception as e:
                if on_error is not None:
                    try:
                        on_error(kind, key, e)
                    except Exception:
                        pass
                return None
            cache.set(kind, key, value)
            try:
                on_result(kind, key, value)
            except Exception:
                pass
            return None

        with ThreadPoolExecutor(
            max_workers=max(1, min(max_workers, len(pending))),
            thread_name_prefix="post-edit",
        ) as pool:
            list(pool.map(_work, pending))

    t = threading.Thread(target=_run, name="post-edit-prefetch", daemon=True)
    t.start()
    return t
