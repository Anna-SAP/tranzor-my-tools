"""Hydrate truncated UNS source/translated text on Tranzor API entries.

Background
----------
Tranzor's paginated ``GET /api/v1/legacy/tasks/{id}/translations`` endpoint
returns previews (~500 chars + ``"..."``) for UNS tasks instead of the full
text. The list view in the task detail page intentionally uses this preview,
and the full text is reachable through an Eye-icon toggle that opens a
modal — see closed bug TRAN-161.

Our exporters (HTML / JSON / XLSX / TMX / merged AP zip / quality reports)
must reflect what is actually stored in Tranzor's database, not the UI
preview. This module fetches the full text in parallel for any entry whose
``source_text_truncated`` or ``translated_text_truncated`` is True, replacing
``source_text`` and ``translated_text`` on the entries in place.

The full text lives at::

    GET /api/v1/legacy/tasks/{task_id}/translations/{translation_id}/full-text

Non-UNS tasks set the truncation flags to None/False, so they short-circuit
out of this hydrator with no extra HTTP traffic.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover - requests is a hard dep of the exporters
    requests = None  # type: ignore[assignment]


DEFAULT_MAX_WORKERS = 6
DEFAULT_TIMEOUT = 30


def _is_truncated(entry: dict) -> bool:
    """Return True if either source or translation needs hydration."""
    return bool(
        entry.get("source_text_truncated")
        or entry.get("translated_text_truncated")
    )


def hydrate_truncated_entries(
    entries: List[dict],
    *,
    api_base: str,
    task_id: Any,
    session: Optional["requests.Session"] = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    timeout: int = DEFAULT_TIMEOUT,
) -> int:
    """Replace truncated ``source_text`` / ``translated_text`` with full DB text.

    Args:
        entries: Entry dicts as returned by the paginated translations API.
            Mutated in place.
        api_base: Legacy API root URL, e.g.
            ``http://tranzor-platform.int.rclabenv.com/api/v1/legacy``.
        task_id: The task whose translations are being hydrated.
        session: Optional ``requests.Session`` to reuse connection pooling.
            A new short-lived session is created if not supplied.
        max_workers: Maximum concurrent full-text fetches.
        timeout: Per-request timeout in seconds.

    Returns:
        The number of entries successfully hydrated.

    Notes:
        Failures on individual entries are logged to stderr and skipped so a
        single 404/timeout cannot break the whole export. Entries without a
        ``translation_id`` are skipped because the full-text endpoint requires
        it.
    """
    if requests is None:
        return 0
    targets = [
        e for e in entries
        if _is_truncated(e) and e.get("translation_id") is not None
    ]
    if not targets:
        return 0

    own_session = False
    if session is None:
        session = requests.Session()
        own_session = True

    def _hydrate_one(entry: dict) -> bool:
        tid = entry["translation_id"]
        url = f"{api_base}/tasks/{task_id}/translations/{tid}/full-text"
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        changed = False
        if "source_text" in data and data["source_text"] is not None:
            entry["source_text"] = data["source_text"]
            entry["source_text_truncated"] = False
            entry["source_text_length"] = len(data["source_text"])
            # Keep the preview field intact for any downstream consumer that
            # still wants the short version; only the canonical field flips.
            changed = True
        if "translated_text" in data and data["translated_text"] is not None:
            entry["translated_text"] = data["translated_text"]
            entry["translated_text_truncated"] = False
            entry["translated_text_length"] = len(data["translated_text"])
            changed = True
        return changed

    hydrated = 0
    workers = max(1, min(max_workers, len(targets)))
    try:
        if workers == 1:
            for entry in targets:
                try:
                    if _hydrate_one(entry):
                        hydrated += 1
                except Exception as exc:
                    _log_failure(entry, exc)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_hydrate_one, e): e for e in targets}
                for f in as_completed(futures):
                    try:
                        if f.result():
                            hydrated += 1
                    except Exception as exc:
                        _log_failure(futures[f], exc)
    finally:
        if own_session:
            session.close()

    return hydrated


def _log_failure(entry: dict, exc: Exception) -> None:
    tid = entry.get("translation_id")
    print(
        f"    ⚠ full-text 拉取失败 translation_id={tid}: {exc}",
        file=sys.stderr,
    )
