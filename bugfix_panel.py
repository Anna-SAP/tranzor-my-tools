"""Pure data and synchronization layer for the desktop BugFix panel.

The Platform history and GitLab MR lifecycle are intentionally modelled as
independent axes: a correction can be Applied to TM while its MR is still
opened (or later closed without merge).  This module has no tkinter imports so
pagination, status rules, comments and cache fallback remain unit-testable.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import unquote, urlparse

import atomic_io

DEFAULT_PLATFORM_URL = "http://tranzor-platform.int.rclabenv.com"
HISTORY_PATH = "/api/v1/bug-fix/history"
HISTORY_PAGE_SIZE = 100
MAX_HISTORY_PAGES = 1000
COMMENTS_PER_PAGE = 100
MAX_COMMENT_PAGES = 3
CACHE_SCHEMA_VERSION = 1
CACHE_PATH = os.path.expanduser(
    "~/.tranzor_exporter/bugfix_panel_cache.json")

_ACTION_RE = re.compile(
    r"\b(block(?:ed|er|ing)?|must|please|request(?:ed)? changes?|"
    r"needs? changes?|fix|failing|failure|cannot|can't|do not merge|"
    r"security|regression|incorrect|wrong|error)\b|"
    r"(阻塞|必须|请修改|需要修改|失败|错误|不能合并|回归)",
    re.IGNORECASE,
)
_APPROVAL_RE = re.compile(
    r"\b(approved?|lgtm|looks good|ready to merge)\b|"
    r"(已批准|可以合并|通过)",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normal(value: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(value or "").strip().lower())


def _label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Unknown"
    known = {
        "opened": "Open",
        "open": "Open",
        "merged": "Merged",
        "closed": "Closed",
        "locked": "Locked",
        "none": "Direct / no MR",
        "unknown": "Unknown",
        "queued": "Queued",
        "processing": "Processing",
        "waiting_for_merge": "Waiting for MR",
        "applied": "Applied",
        "failed": "Failed",
        "tm_failed": "TM failed",
        "mr_creation_failed": "MR creation failed",
    }
    return known.get(_normal(raw), raw.replace("_", " ").title())


def _response_json(response: Any) -> Mapping[str, Any]:
    if isinstance(response, Mapping):
        payload = response
    else:
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("Bug Fix history response must be an object")
    return payload


def _history_url(base_url: str | None) -> str:
    origin = (base_url or DEFAULT_PLATFORM_URL).rstrip("/")
    if origin.endswith("/api/v1"):
        return origin + "/bug-fix/history"
    return origin + HISTORY_PATH


def fetch_all_history(
    base_url: str | None = None,
    *,
    project_id: str = "",
    target_language: str = "",
    status: str = "",
    query: str = "",
    page_size: int = HISTORY_PAGE_SIZE,
    get_fn: Callable[..., Any] | None = None,
    max_pages: int = MAX_HISTORY_PAGES,
) -> dict[str, Any]:
    """Fetch every matching Platform submission using its grouped pagination."""
    if get_fn is None:
        from export_mr_pipeline import _api_get
        get_fn = _api_get

    size = max(1, min(int(page_size or HISTORY_PAGE_SIZE), 100))
    cap = max(1, int(max_pages or 1))
    common: dict[str, Any] = {}
    if project_id:
        common["project_id"] = project_id
    if target_language:
        common["target_language"] = target_language
    if status:
        common["status"] = status
    if query:
        common["q"] = str(query)[:300]

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    statuses: list[str] = []
    total: int | None = None
    last_page_was_full = False

    for page in range(1, cap + 1):
        params = dict(common)
        params.update({"page": page, "page_size": size})
        payload = _response_json(get_fn(_history_url(base_url), params=params))
        batch = payload.get("submissions") or []
        if not isinstance(batch, list):
            raise ValueError("Bug Fix history submissions must be a list")
        raw_statuses = payload.get("available_statuses") or []
        if isinstance(raw_statuses, list):
            statuses = [str(item) for item in raw_statuses if item]
        raw_total = payload.get("total_submissions")
        if raw_total is not None:
            try:
                total = max(0, int(raw_total))
            except (TypeError, ValueError):
                total = None

        for item in batch:
            if not isinstance(item, Mapping):
                continue
            row = dict(item)
            key = str(row.get("submission_id") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            out.append(row)

        last_page_was_full = len(batch) >= size
        if len(batch) < size or (total is not None and len(out) >= total):
            break
    else:
        if last_page_was_full and (total is None or len(out) < total):
            raise RuntimeError(
                f"Bug Fix history exceeded the safety cap of {cap} pages")

    return {
        "submissions": out,
        "total_submissions": total if total is not None else len(out),
        "available_statuses": statuses,
        "pages_fetched": page,
    }


def extract_mr_identity(submission: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the GitLab project path and IID, preferring the returned URL."""
    url = str(submission.get("mr_url") or "").strip()
    project = str(submission.get("project_id") or "").strip()
    iid = submission.get("mr_iid")

    if url:
        parsed = urlparse(url)
        marker = "/-/merge_requests/"
        if marker in parsed.path:
            left, right = parsed.path.split(marker, 1)
            url_project = unquote(left.strip("/"))
            url_iid = right.split("/", 1)[0]
            if url_project:
                project = url_project
            if url_iid.isdigit():
                iid = int(url_iid)

    try:
        iid = int(iid) if iid not in (None, "") else None
    except (TypeError, ValueError):
        iid = None
    return {"project_id": project, "mr_iid": iid, "mr_url": url}


def normalize_submission(submission: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(submission or {})
    summary = row.get("summary") if isinstance(row.get("summary"), Mapping) else {}
    records = row.get("records") if isinstance(row.get("records"), list) else []
    identity = extract_mr_identity(row)

    status = (
        summary.get("aggregate_status")
        or row.get("platform_status")
        or row.get("user_status")
        or ""
    )
    langs = row.get("target_languages")
    if not isinstance(langs, list):
        langs = []
    langs = [str(item) for item in langs if item]
    if not langs and row.get("target_language"):
        langs = [str(row["target_language"])]

    has_mr = bool(identity["mr_iid"] and identity["project_id"])
    mr_state = _normal(row.get("mr_state")) or (
        "unknown" if has_mr else "none")
    if row.get("merged_at"):
        mr_state = "merged"

    row.update({
        "summary": dict(summary),
        "records": [dict(item) for item in records
                    if isinstance(item, Mapping)],
        "platform_status": _normal(status) or "unknown",
        "platform_status_label": _label(status),
        "target_languages": langs,
        "string_count": int(summary.get("total") or len(records)),
        "mr_project_id": identity["project_id"],
        "mr_iid": identity["mr_iid"],
        "mr_url": identity["mr_url"],
        "has_mr": has_mr,
        "mr_state": mr_state,
        "mr_state_label": _label(mr_state),
        "mr_sync_error": str(row.get("mr_sync_error") or ""),
        "comments": list(row.get("comments") or []),
        "comments_loaded": bool(row.get("comments_loaded")),
    })
    row["attention"] = derive_attention(row)
    return row


def _timestamp(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return 0.0


def _is_bot(author: Mapping[str, Any]) -> bool:
    username = str(author.get("username") or "").lower()
    name = str(author.get("name") or "").lower()
    return (
        username.endswith("-bot")
        or username.endswith("[bot]")
        or username in {"bot", "gitlab-bot"}
        or name.endswith(" bot")
    )


def classify_important_comments(
    discussions: Iterable[Mapping[str, Any]] | None,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Flatten discussions and rank actionable human comments deterministically."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    for discussion in discussions or []:
        if not isinstance(discussion, Mapping):
            continue
        notes = discussion.get("notes")
        if not isinstance(notes, list):
            notes = [discussion] if discussion.get("body") else []
        for note in notes:
            if not isinstance(note, Mapping) or note.get("system"):
                continue
            body = str(note.get("body") or "").strip()
            if not body:
                continue
            author = note.get("author") if isinstance(
                note.get("author"), Mapping) else {}
            resolvable = bool(note.get("resolvable"))
            resolved = bool(note.get("resolved"))
            unresolved = resolvable and not resolved
            action = bool(_ACTION_RE.search(body))
            approval = bool(_APPROVAL_RE.search(body))
            if _is_bot(author) and not (unresolved or action):
                continue

            if unresolved:
                importance, reason = 0, "unresolved discussion"
            elif action:
                importance, reason = 1, "action requested"
            elif approval:
                importance, reason = 2, "approval"
            else:
                importance, reason = 3, "recent human comment"

            note_id = str(note.get("id") or "")
            dedupe = note_id or "|".join((
                str(author.get("username") or author.get("name") or ""),
                str(note.get("created_at") or ""),
                body,
            ))
            if dedupe in seen:
                continue
            seen.add(dedupe)
            candidates.append({
                "id": note_id,
                "discussion_id": str(discussion.get("id") or ""),
                "author": str(
                    author.get("name") or author.get("username") or "Unknown"),
                "username": str(author.get("username") or ""),
                "body": body,
                "url": str(note.get("web_url")
                           or discussion.get("web_url") or ""),
                "created_at": str(note.get("created_at") or ""),
                "updated_at": str(
                    note.get("updated_at") or note.get("created_at") or ""),
                "resolvable": resolvable,
                "resolved": resolved,
                "unresolved": unresolved,
                "importance": importance,
                "reason": reason,
            })

    candidates.sort(
        key=lambda item: (
            item["importance"],
            -_timestamp(item.get("updated_at") or item.get("created_at")),
            item.get("id") or "",
        )
    )
    return candidates[:max(0, int(limit or 0))]


def derive_attention(submission: Mapping[str, Any]) -> dict[str, Any]:
    status = _normal(submission.get("platform_status"))
    mr_state = _normal(submission.get("mr_state"))
    comments = submission.get("comments") or []
    unresolved = any(
        isinstance(item, Mapping) and item.get("unresolved")
        for item in comments
    )

    if "fail" in status or "error" in status or submission.get("create_error"):
        return {"priority": 0, "level": "action",
                "reason": "Bug Fix workflow failed"}
    if mr_state == "closed":
        return {"priority": 0, "level": "action",
                "reason": "MR closed without merge"}
    if submission.get("has_conflicts"):
        return {"priority": 0, "level": "action",
                "reason": "MR has conflicts"}
    if unresolved:
        return {"priority": 0, "level": "action",
                "reason": "Unresolved MR discussion"}
    if mr_state in {"opened", "open"}:
        reason = "Draft MR needs review" if submission.get("draft") else "Open MR"
        return {"priority": 1, "level": "watch", "reason": reason}
    if status in {"queued", "processing", "waiting_for_merge", "pending"}:
        return {"priority": 1, "level": "watch",
                "reason": "Bug Fix is still in progress"}
    if mr_state == "unknown" and submission.get("has_mr"):
        return {"priority": 2, "level": "unknown",
                "reason": "MR state unavailable"}
    if not submission.get("has_mr"):
        return {"priority": 3, "level": "direct",
                "reason": "Direct / no MR"}
    return {"priority": 4, "level": "done", "reason": "No action detected"}


def enrich_submission(
    submission: Mapping[str, Any],
    client: Any,
    *,
    include_discussions: bool = False,
    force_refresh: bool = True,
) -> dict[str, Any]:
    """Add live GitLab metadata; a GitLab failure remains row-local."""
    row = normalize_submission(submission)
    if not row["has_mr"]:
        return row
    if client is None or (
            hasattr(client, "has_token") and not client.has_token()):
        row["mr_sync_error"] = "GitLab read token unavailable"
        row["attention"] = derive_attention(row)
        return row

    try:
        mr = client.get_merge_request(
            row["mr_project_id"], row["mr_iid"],
            force_refresh=force_refresh,
        )
        if not isinstance(mr, Mapping):
            raise ValueError("GitLab MR response must be an object")
        state = _normal(mr.get("state")) or "unknown"
        if mr.get("merged_at"):
            state = "merged"
        row.update({
            "mr_state": state,
            "mr_state_label": _label(state),
            "mr_url": str(mr.get("web_url") or row.get("mr_url") or ""),
            "mr_title": str(mr.get("title") or ""),
            "mr_updated_at": str(mr.get("updated_at") or ""),
            "merged_at": str(mr.get("merged_at") or ""),
            "closed_at": str(mr.get("closed_at") or ""),
            "draft": bool(mr.get("draft") or mr.get("work_in_progress")),
            "has_conflicts": bool(mr.get("has_conflicts")),
            "merge_status": str(mr.get("merge_status") or ""),
            "detailed_merge_status": str(
                mr.get("detailed_merge_status") or ""),
            "blocking_discussions_resolved": mr.get(
                "blocking_discussions_resolved"),
            "labels": [str(item) for item in (mr.get("labels") or []) if item],
            "author": dict(mr.get("author") or {})
                      if isinstance(mr.get("author"), Mapping) else {},
            "pipeline_status": str(
                (mr.get("head_pipeline") or {}).get("status") or "")
                if isinstance(mr.get("head_pipeline"), Mapping) else "",
            "mr_sync_error": "",
            "mr_synced_at": _utc_now(),
        })
        if include_discussions:
            discussions = client.list_mr_discussions(
                row["mr_project_id"], row["mr_iid"],
                per_page=COMMENTS_PER_PAGE,
                max_pages=MAX_COMMENT_PAGES,
                force_refresh=force_refresh,
            )
            row["comments"] = classify_important_comments(discussions)
            row["comments_loaded"] = True
            row["discussion_count"] = len(discussions)
    except Exception as exc:  # one inaccessible MR must not hide history
        row["mr_sync_error"] = f"{type(exc).__name__}: {exc}"[:240]

    row["attention"] = derive_attention(row)
    return row


def enrich_submissions(
    submissions: Iterable[Mapping[str, Any]],
    client: Any,
    *,
    max_workers: int = 4,
    force_refresh: bool = True,
) -> list[dict[str, Any]]:
    """Fetch live MR state concurrently while preserving input order."""
    rows = [normalize_submission(item) for item in submissions]
    indices = [i for i, row in enumerate(rows) if row["has_mr"]]
    if not indices:
        return rows
    if client is None or (
            hasattr(client, "has_token") and not client.has_token()):
        return [
            enrich_submission(row, client, force_refresh=force_refresh)
            if row["has_mr"] else row
            for row in rows
        ]

    workers = max(1, min(int(max_workers or 1), 8, len(indices)))
    with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="bugfix-mr") as pool:
        future_to_index = {
            pool.submit(
                enrich_submission, rows[index], client,
                include_discussions=False, force_refresh=force_refresh,
            ): index
            for index in indices
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                rows[index] = future.result()
            except Exception as exc:
                rows[index]["mr_sync_error"] = (
                    f"{type(exc).__name__}: {exc}")[:240]
                rows[index]["attention"] = derive_attention(rows[index])
    return rows


def stable_sort_submissions(
    submissions: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = [normalize_submission(item) for item in submissions]
    return sorted(
        rows,
        key=lambda row: (
            int((row.get("attention") or {}).get("priority", 9)),
            -_timestamp(
                row.get("mr_updated_at") or row.get("created_at")),
            str(row.get("submission_id") or ""),
        ),
    )


def filter_submissions(
    submissions: Iterable[Mapping[str, Any]],
    *,
    project: str = "",
    platform_status: str = "",
    mr_state: str = "",
    query: str = "",
) -> list[dict[str, Any]]:
    project_key = str(project or "").strip().lower()
    platform_key = _normal(platform_status)
    mr_key = _normal(mr_state)
    needle = str(query or "").strip().lower()
    out = []
    for raw in submissions:
        row = normalize_submission(raw)
        if project_key and str(row.get("project_id") or "").lower() != project_key:
            continue
        if platform_key and row["platform_status"] != platform_key:
            continue
        if mr_key and row["mr_state"] != mr_key:
            continue
        if needle:
            haystack = "\n".join(str(row.get(key) or "") for key in (
                "bug_id", "submission_id", "mr_iid", "mr_url",
                "branch_name", "created_by", "project_id",
            )).lower()
            if needle not in haystack:
                continue
        out.append(row)
    return out


def _cache_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for raw in payload.get("submissions") or []:
        row = normalize_submission(raw)
        # Comments are fetched only for the selected MR and are deliberately
        # not persisted. The cache stores no credentials or request headers.
        row.pop("comments", None)
        row["comments_loaded"] = False
        rows.append(row)
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "saved_at": _utc_now(),
        "available_statuses": list(payload.get("available_statuses") or []),
        "total_submissions": int(
            payload.get("total_submissions") or len(rows)),
        "submissions": rows,
    }


def save_cache(
    payload: Mapping[str, Any],
    path: str | None = None,
) -> str:
    target = path or CACHE_PATH
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    atomic_io.atomic_write_json(
        target, _cache_payload(payload), indent=2, mode=0o600)
    return target


def load_cache(path: str | None = None) -> dict[str, Any] | None:
    target = path or CACHE_PATH
    try:
        import json
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema_version") != CACHE_SCHEMA_VERSION
            or not isinstance(payload.get("submissions"), list)
        ):
            return None
        rows = stable_sort_submissions(payload["submissions"])
        return {
            "submissions": rows,
            "total_submissions": int(
                payload.get("total_submissions") or len(rows)),
            "available_statuses": list(
                payload.get("available_statuses") or []),
            "saved_at": str(payload.get("saved_at") or ""),
            "source": "cache",
            "stale": True,
        }
    except (OSError, ValueError, TypeError):
        return None


def sync_panel(
    base_url: str | None = None,
    *,
    project_id: str = "",
    target_language: str = "",
    status: str = "",
    query: str = "",
    get_fn: Callable[..., Any] | None = None,
    gitlab_client: Any = None,
    sync_gitlab: bool = True,
    cache_path: str | None = None,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Synchronize Platform history and current MR states with cache fallback."""
    try:
        history = fetch_all_history(
            base_url,
            project_id=project_id,
            target_language=target_language,
            status=status,
            query=query,
            get_fn=get_fn,
        )
        rows = [normalize_submission(item)
                for item in history["submissions"]]
        if sync_gitlab:
            if gitlab_client is None:
                from gitlab_client import GitLabClient
                gitlab_client = GitLabClient()
            rows = enrich_submissions(
                rows, gitlab_client, max_workers=max_workers,
                force_refresh=True)
        rows = stable_sort_submissions(rows)
        result = {
            "ok": True,
            "live_ok": True,
            "source": "live",
            "stale": False,
            "synced_at": _utc_now(),
            "submissions": rows,
            "total_submissions": history["total_submissions"],
            "available_statuses": history["available_statuses"],
            "pages_fetched": history["pages_fetched"],
            "gitlab_error_count": sum(
                bool(row.get("mr_sync_error")) for row in rows),
            "error": "",
        }
        save_cache(result, cache_path)
        return result
    except Exception as exc:
        cached = load_cache(cache_path)
        if cached is not None:
            cached.update({
                "ok": True,
                "live_ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "gitlab_error_count": sum(
                    bool(row.get("mr_sync_error"))
                    for row in cached["submissions"]),
            })
            return cached
        return {
            "ok": False,
            "live_ok": False,
            "source": "none",
            "stale": False,
            "submissions": [],
            "total_submissions": 0,
            "available_statuses": [],
            "gitlab_error_count": 0,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }
