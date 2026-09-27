"""Data Lookback — pure data layer (no Tk).

For one UTC+8 calendar day, the header's 📅 Data Lookback report shows per
Category → Project:

* **MRs** — distinct ``(project, MR#)`` whose MR translation task completed
  that day. One MR often completes several times a day (every push re-runs
  it), so tasks are de-duplicated by MR.
* **Target branches** — the GitLab ``target_branch`` of those MRs (the MR
  Pipeline "MR Branch" column), resolved through :mod:`mr_jira`'s shared
  process cache.
* **Bug Fix** — Bug Fix submissions with status Applied / Partially applied.
* **Scan** — completed Missing Translation Scan tasks.

Day assignment. None of the three Tranzor list endpoints has a date filter,
and Tranzor stores no completion timestamp: ``updated_at`` keeps moving after
completion (a Language Lead fix MR pushes it 12–34 h later), so a task is
counted on the UTC+8 day of its ``created_at``. That value never changes, so
past MR / Scan counts stay put, and a completed MR task runs about a minute
(median), so it almost always equals the completion day. A Bug Fix is
Applied to TM when it is submitted, so its submission day is its completion
day (a later status change can still move it in or out of the count).

The endpoints all return newest ``created_at`` first; :func:`collect_window`
walks them with an early stop, and binary-searches the offset when the day
lies far behind the first page.

The category table is the owner-supplied "项目与类别关系表" (65 projects).
Projects missing from it are never dropped: they land in an ``Unmapped``
group so new repositories stay visible until the table is updated.
"""
from __future__ import annotations

import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import unquote

# Fixed offset, not ZoneInfo("Asia/Shanghai"): the EXE ships without tzdata,
# and China has no DST, so the offset is exact.
TZ_UTC8 = timezone(timedelta(hours=8))
TZ_LABEL = "UTC+8"

PAGE_SIZE = 200          # /tasks and scan /tasks reject limit > 200
MAX_PAGES = 1000         # runaway guard for one day's paging
BRANCH_WORKERS = 6       # parallel GitLab MR lookups
UNKNOWN_BRANCH = None    # Counter key for MRs whose branch did not resolve

MR_DONE = "completed"
SCAN_DONE = "completed"
BUGFIX_DONE = frozenset({"applied", "partially_applied"})

UNMAPPED = "Unmapped"

# Owner-supplied category table, verbatim and in its order. The strings are
# GitLab ``path_with_namespace`` values exactly as Tranzor reports them.
CATEGORY_PROJECTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CoreLib", (
        "CoreLib/RoomsController",
        "CoreLib/guides-assets",
        "CoreLib/mthor",
        "CoreLib/rcvrooms",
        "CoreLib/rcvrooms-android",
        "CoreLib/rcvrooms-windows",
    )),
    ("RCV", (
        "Fiji/Fiji",
        "Fiji/resource-center",
        "Fiji/video",
        "Fiji/webinar",
        "RND/ai/ringsense/data-export-service",
        "RND/ai/ringsense/ringsense-ui",
        "RND/rcvnc",
        "RND/rwc",
        "RND/rwc-visual-assist",
        "loc-tools/gooddata-ringcx",
        "sean.zhuang/Fiji",
    )),
    ("WEB", (
        "admin-web/backend",
        "admin-web/frontend",
        "common/awp",
        "common/clw",
        "common/maa",
        "common/uns",
        "dash/dash",
        "dash/twilight",
        "es/express-setup-nova-next-generation",
        "es/express-setup-renaissance",
        "platform/i18n",
        "web-modules/web-modules-core",
        "web/bui",
        "web/chc",
        "web/cic",
        "web/i18n",
        "web/jedi",
        "web/npa",
        "web/push-to-talk",
        "web/stc",
        "web/web",
    )),
    ("Copilot", (
        "copilot-platform/business-components/copilot-web-widgets",
        "copilot-platform/business-components/nova-aa-sa",
        "copilot-platform/business-components/rex-ai/copilot-chat-web",
        "copilot-platform/nova/agentic-integration-frontend",
        "copilot-platform/nova/nova-studio-app",
        "copilot-platform/nova/webchat",
    )),
    ("DPW", (
        "dpw-xmn/apw-in-jupiter",
        "dpw-xmn/dpw",
        "dpw-xmn/ese",
        "dpw-xmn/rc-automator-server",
    )),
    ("ENGAGE", (
        "engage-digital/engage-digital",
        "engage-voice/frontend/agent/agent-service",
        "engage-voice/frontend/engage-unified-analytics",
        "engage-voice/frontend/frontend",
        "engage-voice/frontend/rcx-crm-integrations",
        "engage-voice/frontend/rcx-salesforce-byot-integration",
        "engage-voice/frontend/workflow-studio",
    )),
    ("INTEGRATION", (
        "integration/archiverFE",
        "integration/integration-apps",
        "integration/ips-backend",
        "integration/presence-sync-frontend",
        "integration/uif",
    )),
    ("IVA", (
        "iva/agent-service",
        "iva/assistant-runtime",
        "iva/iva-ui",
    )),
    ("RCW", (
        "rcw/client/webinar-sdk-engine",
        "rcw/client/whc",
    )),
)
CATEGORY_ORDER: tuple[str, ...] = tuple(c for c, _ in CATEGORY_PROJECTS)


def _clean_project(project_id: Any) -> str:
    """Trim whitespace, URL escapes, slashes and a trailing ``.git``."""
    s = unquote(str(project_id or "")).strip().strip("/")
    if s.lower().endswith(".git"):
        s = s[:-4].rstrip("/")
    return s


_EXACT: dict[str, tuple[str, str]] = {}
_FOLDED: dict[str, tuple[str, str]] = {}
for _cat, _projects in CATEGORY_PROJECTS:
    for _p in _projects:
        _EXACT[_p] = (_cat, _p)
        _FOLDED[_p.casefold()] = (_cat, _p)


def resolve_project(project_id: Any) -> tuple[Optional[str], str]:
    """``project_id`` → ``(category, canonical path)``.

    Exact match first, then a case-insensitive match on the cleaned path, so
    ``corelib/roomscontroller.git`` still counts under CoreLib as
    ``CoreLib/RoomsController``. Unknown projects return ``(None, cleaned)``.
    """
    raw = str(project_id or "")
    hit = _EXACT.get(raw)
    if hit is not None:
        return hit
    cleaned = _clean_project(raw)
    hit = _FOLDED.get(cleaned.casefold())
    if hit is not None:
        return hit
    return None, cleaned


# ---------------------------------------------------------------------------
# Day window
# ---------------------------------------------------------------------------
def today_utc8(now: Optional[datetime] = None) -> date:
    """Today's UTC+8 calendar date (``now`` injectable for tests)."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(TZ_UTC8).date()


SETTLE_GRACE = timedelta(hours=6)


def is_settled(day: Any, now: Optional[datetime] = None,
               grace: timedelta = SETTLE_GRACE) -> bool:
    """True once ``day`` ended at least ``grace`` ago (UTC+8).

    Only completed tasks are listed, and a task counts on the day it was
    created — so a scan created at 23:40 that finishes at 00:25 joins
    yesterday late. A day is safe to cache only after that tail is done.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment >= day_window(day)[1] + grace


def coerce_day(value: Any) -> Optional[date]:
    """``date`` / ``datetime`` / ``YYYY-MM-DD`` → ``date`` (else ``None``)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def day_window(day: Any) -> tuple[datetime, datetime]:
    """UTC+8 day → aware half-open ``[start, end)`` window.

    ``2026-09-27`` → ``[2026-09-26T16:00Z, 2026-09-27T16:00Z)``.
    """
    d = coerce_day(day)
    if d is None:
        raise ValueError(f"not a date: {day!r}")
    start = datetime.combine(d, datetime.min.time()).replace(tzinfo=TZ_UTC8)
    return start, start + timedelta(days=1)


def parse_api_ts(raw: Any) -> Optional[datetime]:
    """Parse a Tranzor timestamp; naive values are UTC (``utcnow_naive``)."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def in_window(raw: Any, window: tuple[datetime, datetime]) -> bool:
    ts = parse_api_ts(raw)
    return ts is not None and window[0] <= ts < window[1]


# ---------------------------------------------------------------------------
# Fetching (injectable page functions; no Tk)
# ---------------------------------------------------------------------------
class LookbackCancelled(Exception):
    """The report window closed or moved to another day mid-fetch."""


def _check_cancel(cancel_event: Any) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise LookbackCancelled()


def _created(item: Mapping[str, Any]) -> Optional[datetime]:
    return parse_api_ts((item or {}).get("created_at"))


def _seek_offset(fetch_page, end: datetime, lo: int, hi: int,
                 cancel_event: Any = None) -> int:
    """Binary-search the newest-first list for the window's upper edge.

    Invariant: the row at ``lo`` was created at or after ``end``; rows at
    ``hi`` and beyond are older (``hi`` may be the virtual row ``total``).
    Returns ``lo`` — paging from there re-reads one newer row, which the
    window filter drops. Rows inserted meanwhile only push the true edge
    further down, never above ``lo``, so no in-window row is skipped.
    """
    while hi - lo > 1:
        _check_cancel(cancel_event)
        mid = (lo + hi) // 2
        _total, items = fetch_page(1, mid)
        items = list(items or [])
        if not items:
            hi = mid
            continue
        ts = _created(items[0])
        if ts is None or ts >= end:
            lo = mid
        else:
            hi = mid
    return lo


def collect_window(fetch_page: Callable[[int, int], tuple[int, list]],
                   window: tuple[datetime, datetime], *,
                   page_size: int = PAGE_SIZE,
                   id_key: str = "task_id",
                   cancel_event: Any = None,
                   max_pages: int = MAX_PAGES) -> list[dict]:
    """Rows of a ``created_at``-desc list whose ``created_at`` is in ``window``.

    ``fetch_page(limit, offset)`` returns ``(total, rows)``. Paging stops at
    the first row older than the window. When the whole first page is newer
    than the window (an older day), the start offset is found by binary
    search with ``limit=1`` probes instead of walking every page in between.
    Rows are de-duplicated by ``id_key`` (a row can repeat across pages when
    new rows are inserted while paging).
    """
    start, end = window
    size = max(1, int(page_size))
    total, batch = fetch_page(size, 0)
    batch = list(batch or [])
    total = int(total or 0)
    offset = 0
    if batch and total > len(batch):
        last = _created(batch[-1])
        if last is not None and last >= end:
            offset = _seek_offset(fetch_page, end, len(batch) - 1, total,
                                  cancel_event)
            batch = None

    out: list[dict] = []
    seen: set = set()
    pages = 0
    while True:
        _check_cancel(cancel_event)
        if batch is None:
            total, batch = fetch_page(size, offset)
            batch = list(batch or [])
            total = int(total or 0)
        pages += 1
        reached_floor = False
        for row in batch:
            ts = _created(row)
            if ts is None or ts >= end:
                continue
            if ts < start:
                reached_floor = True
                break
            key = row.get(id_key) or id(row)
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        if reached_floor or len(batch) < size:
            break
        offset += len(batch)
        if total and offset >= total:
            break
        if pages >= max_pages:
            raise RuntimeError(
                f"Data Lookback paging exceeded {max_pages} pages")
        batch = None
    return out


def fetch_mr_day(window, *, base_url=None, fetch_tasks=None,
                 cancel_event=None) -> list[dict]:
    """Completed MR translation tasks created inside ``window``."""
    if fetch_tasks is None:
        from export_mr_pipeline import fetch_mr_tasks as fetch_tasks

    def page(limit, offset):
        return fetch_tasks(status=MR_DONE, limit=limit, offset=offset,
                           base_url=base_url)

    return [t for t in collect_window(page, window, cancel_event=cancel_event)
            if str(t.get("status") or "") == MR_DONE]


def fetch_scan_day(window, *, base_url=None, fetch_tasks=None,
                   cancel_event=None) -> list[dict]:
    """Completed Missing Translation Scan tasks created inside ``window``."""
    if fetch_tasks is None:
        from export_mr_pipeline import fetch_scan_tasks as fetch_tasks

    def page(limit, offset):
        return fetch_tasks(status=SCAN_DONE, limit=limit, offset=offset,
                           base_url=base_url)

    return [t for t in collect_window(page, window, cancel_event=cancel_event)
            if str(t.get("status") or "") == SCAN_DONE]


def fetch_bugfix_day(window, *, base_url=None, get_fn=None,
                     cancel_event=None) -> list[dict]:
    """Bug Fix submissions created inside ``window`` (any status, normalized).

    History is grouped per submission and ordered by its newest record, while
    a submission's ``created_at`` is one record's time — so paging stops only
    once a page ends a full day before the window, never on the edge itself.
    """
    import bugfix_panel as bf

    floor = window[0] - timedelta(days=1)

    def stop(batch):
        last = _created(batch[-1]) if batch else None
        return last is not None and last < floor

    try:
        res = bf.fetch_all_history(
            base_url, get_fn=get_fn, cancel_event=cancel_event,
            stop_after_page=stop)
    except bf.SyncCancelled as exc:
        raise LookbackCancelled() from exc
    return [bf.normalize_submission(s)
            for s in res.get("submissions") or []
            if in_window(s.get("created_at"), window)]


_AUTH_TEXT = re.compile(r"\b401\b|unauthori[sz]ed")
_FORBIDDEN_TEXT = re.compile(r"\b403\b|forbidden")


def error_kind(exc: BaseException) -> str:
    """``auth`` (401 — sign in again), ``forbidden`` (403) or ``error``.

    The HTTP status decides when there is one. Connection errors and bad JSON
    are plain errors even though their text may quote a URL like
    ``offset=7403`` or ``(char 4013)``.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        return {401: "auth", 403: "forbidden"}.get(status, "error")
    if isinstance(exc, (ConnectionError, TimeoutError, ValueError)):
        return "error"
    try:
        import requests
        # Transport failures (connection, timeout, a body cut off mid-way —
        # "IncompleteRead(401 bytes read)") carry no status to go by.
        if (isinstance(exc, requests.RequestException)
                and not isinstance(exc, requests.HTTPError)):
            return "error"
    except ImportError:  # pragma: no cover - requests ships with the app
        pass
    text = str(exc).lower()
    if _AUTH_TEXT.search(text):
        return "auth"
    if _FORBIDDEN_TEXT.search(text):
        return "forbidden"
    return "error"


def fetch_day(day, *, base_url=None, cancel_event=None,
              fetch_mr=None, fetch_bugfix=None, fetch_scan=None) -> dict:
    """Fetch the three Tranzor sources for ``day`` concurrently.

    Returns ``{"mr": rows|None, "bugfix": rows|None, "scan": rows|None,
    "errors": {source: (kind, message)}}``. A failed source is ``None`` —
    the report shows "—" for it rather than a misleading 0.
    """
    window = day_window(day)
    jobs = {
        "mr": fetch_mr or fetch_mr_day,
        "bugfix": fetch_bugfix or fetch_bugfix_day,
        "scan": fetch_scan or fetch_scan_day,
    }
    out: dict[str, Any] = {"errors": {}}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {
            name: pool.submit(fn, window, base_url=base_url,
                              cancel_event=cancel_event)
            for name, fn in jobs.items()
        }
        for name, fut in futures.items():
            try:
                out[name] = fut.result()
            except LookbackCancelled:
                raise
            except Exception as exc:  # one source down must not hide the rest
                out[name] = None
                out["errors"][name] = (error_kind(exc), str(exc))
    _check_cancel(cancel_event)
    return out


def mr_keys(tasks: Iterable[Mapping[str, Any]]) -> list[tuple[str, int]]:
    """Distinct ``(project_id, MR#)`` in first-seen order (raw project ids)."""
    seen: set = set()
    out = []
    for t in tasks or []:
        iid = _as_iid(t.get("merge_request_iid"))
        pid = str(t.get("project_id") or "").strip()
        if not pid or iid is None:
            continue
        _cat, canonical = resolve_project(pid)
        dedupe = (canonical, iid)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        out.append((pid, iid))
    return out


def resolve_mr_meta(keys: Iterable[tuple[str, int]], *, fetch_meta=None,
                    max_workers: int = BRANCH_WORKERS, cancel_event=None,
                    on_progress: Optional[Callable[[int, int], None]] = None
                    ) -> dict[tuple[str, int], dict]:
    """GitLab metadata per MR: ``{key: {"branch", "jira", "title"}}``.

    Uses :func:`mr_jira.fetch_jira_metadata` (the MR Pipeline's MR Branch
    source, process-cached). MRs that fail to resolve (no access, deleted,
    network) are simply absent — the report files them under "unknown".
    """
    if fetch_meta is None:
        import mr_jira
        fetch_meta = mr_jira.fetch_jira_metadata
    keys = list(keys or [])
    out: dict[tuple[str, int], dict] = {}
    if not keys:
        return out
    lock = threading.Lock()
    done = [0]

    def one(key):
        if cancel_event is not None and cancel_event.is_set():
            return
        try:
            meta = fetch_meta(key[0], key[1])
        except Exception:
            meta = None
        with lock:
            if meta is not None:
                branch = str(getattr(meta, "target_branch", "") or "").strip()
                out[key] = {
                    "branch": branch or UNKNOWN_BRANCH,
                    "jira": str(getattr(meta, "jira_id", "") or ""),
                    "title": str(getattr(meta, "title", "") or ""),
                }
            done[0] += 1
            n = done[0]
        if on_progress is not None:
            try:
                on_progress(n, len(keys))
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        list(pool.map(one, keys))
    _check_cancel(cancel_event)
    return out


def can_resolve_branches() -> bool:
    """True when a GitLab token is configured (MR target branches need it)."""
    try:
        import mr_jira
        return bool(mr_jira.can_fetch())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _as_iid(value: Any) -> Optional[int]:
    try:
        iid = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return iid if iid > 0 else None


def _new_project(category: str, project: str) -> dict:
    return {
        "project": project, "category": category,
        "mrs": {}, "runs": 0, "bugfix": 0, "scan": 0,
    }


def build_report(day: Any, *, mr_tasks=None, bugfix_rows=None,
                 scan_tasks=None, mr_meta=None,
                 errors: Optional[Mapping[str, Any]] = None) -> dict:
    """Aggregate one day's rows into the Category → Project report.

    ``None`` for a source means it failed to load: its totals are ``None``
    (shown as "—"), never 0. ``mr_meta`` is :func:`resolve_mr_meta` output,
    or ``None`` while branches are still resolving.

    Returns ``{"day", "categories": [...], "totals": {...}, "sources",
    "errors", "branches_resolved"}``. Every category of the table is present
    (in table order, with every project, active or not) so the view can
    toggle idle projects; an ``Unmapped`` category follows only when a
    project outside the table had activity.
    """
    d = coerce_day(day)
    projects: dict[str, dict] = {}
    for cat, plist in CATEGORY_PROJECTS:
        for p in plist:
            projects[p] = _new_project(cat, p)
    unmapped: dict[str, dict] = {}

    def slot(project_id: Any) -> Optional[dict]:
        cat, canonical = resolve_project(project_id)
        if not canonical:
            return None
        if cat is not None:
            return projects[canonical]
        if canonical not in unmapped:
            unmapped[canonical] = _new_project(UNMAPPED, canonical)
        return unmapped[canonical]

    for t in mr_tasks or []:
        if str(t.get("status") or MR_DONE) != MR_DONE:
            continue
        iid = _as_iid(t.get("merge_request_iid"))
        p = slot(t.get("project_id"))
        if p is None or iid is None:
            continue
        p["runs"] += 1
        mr = p["mrs"].get(iid)
        if mr is None:
            mr = p["mrs"][iid] = {
                "project_id": str(t.get("project_id") or ""), "iid": iid,
                "runs": 0, "first_created": t.get("created_at") or "",
            }
        mr["runs"] += 1
        created = str(t.get("created_at") or "")
        if created and (not mr["first_created"]
                        or created < mr["first_created"]):
            mr["first_created"] = created

    for s in scan_tasks or []:
        if str(s.get("status") or SCAN_DONE) != SCAN_DONE:
            continue
        p = slot(s.get("project_id"))
        if p is not None:
            p["scan"] += 1

    for b in bugfix_rows or []:
        status = str(b.get("platform_status") or "").strip().lower()
        if status not in BUGFIX_DONE:
            continue
        p = slot(b.get("project_id") or b.get("mr_project_id"))
        if p is not None:
            p["bugfix"] += 1

    has = {
        "mr": mr_tasks is not None,
        "bugfix": bugfix_rows is not None,
        "scan": scan_tasks is not None,
    }
    resolved = mr_meta is not None
    meta = mr_meta or {}

    def finish_project(p: dict) -> dict:
        mrs = []
        branches: Counter = Counter()
        for iid in sorted(p["mrs"]):
            mr = dict(p["mrs"][iid])
            info = meta.get((mr["project_id"], iid)) if resolved else None
            mr["branch"] = info["branch"] if info else UNKNOWN_BRANCH
            mr["jira"] = info["jira"] if info else ""
            mr["title"] = info["title"] if info else ""
            if resolved:
                branches[mr["branch"]] += 1
            mrs.append(mr)
        return {
            "project": p["project"], "category": p["category"],
            "mrs": mrs, "mr_count": len(mrs), "runs": p["runs"],
            "branches": branches,
            "bugfix": p["bugfix"], "scan": p["scan"],
            "active": bool(mrs or p["bugfix"] or p["scan"]),
        }

    def finish_category(name: str, rows: list[dict], mapped: bool) -> dict:
        branches: Counter = Counter()
        for r in rows:
            branches.update(r["branches"])
        return {
            "name": name, "mapped": mapped, "projects": rows,
            "project_total": len(rows),
            "active_projects": sum(1 for r in rows if r["active"]),
            "mr_count": sum(r["mr_count"] for r in rows),
            "runs": sum(r["runs"] for r in rows),
            "bugfix": sum(r["bugfix"] for r in rows),
            "scan": sum(r["scan"] for r in rows),
            "branches": branches,
        }

    categories = []
    for cat, plist in CATEGORY_PROJECTS:
        rows = [finish_project(projects[p]) for p in plist]
        categories.append(finish_category(cat, rows, True))
    if unmapped:
        rows = [finish_project(unmapped[k]) for k in sorted(
            unmapped, key=str.casefold)]
        categories.append(finish_category(UNMAPPED, rows, False))

    all_branches: Counter = Counter()
    for c in categories:
        all_branches.update(c["branches"])
    named = [b for b in all_branches if b is not UNKNOWN_BRANCH]
    totals = {
        "mr_count": sum(c["mr_count"] for c in categories) if has["mr"] else None,
        "runs": sum(c["runs"] for c in categories) if has["mr"] else None,
        "bugfix": sum(c["bugfix"] for c in categories) if has["bugfix"] else None,
        "scan": sum(c["scan"] for c in categories) if has["scan"] else None,
        "branch_count": len(named) if (resolved and has["mr"]) else None,
        "unknown_branch_mrs": all_branches.get(UNKNOWN_BRANCH, 0),
        "active_projects": sum(c["active_projects"] for c in categories),
        "unmapped_projects": len(unmapped),
    }
    return {
        "day": d,
        "categories": categories,
        "totals": totals,
        "sources": has,
        "errors": dict(errors or {}),
        "branches_resolved": resolved,
    }


def format_branches(counter: Mapping[Any, int], *, unknown_label: str = "?",
                    limit: int = 0) -> str:
    """``Counter({'develop': 3, 'master': 1})`` → ``develop ×3, master``.

    Most-used first, then by name; the unknown bucket goes last. ``limit``
    keeps the first N names and appends ``+k``.
    """
    items = [(b, n) for b, n in (counter or {}).items()
             if n and b is not UNKNOWN_BRANCH]
    items.sort(key=lambda kv: (-kv[1], str(kv[0]).casefold()))
    parts = [f"{b} ×{n}" if n > 1 else str(b) for b, n in items]
    extra = 0
    if limit and len(parts) > limit:
        extra = len(parts) - limit
        parts = parts[:limit]
    unknown = (counter or {}).get(UNKNOWN_BRANCH, 0)
    if extra:
        parts.append(f"+{extra}")
    if unknown:
        parts.append(f"{unknown_label} ×{unknown}" if unknown > 1
                     else unknown_label)
    return ", ".join(parts)


def report_to_tsv(report: Mapping[str, Any], *, headers: Iterable[str],
                  unknown_label: str = "?", include_idle: bool = False,
                  unavailable: str = "—", unresolved: str = "—") -> str:
    """Tab-separated copy of the report (category + project rows).

    ``unresolved`` fills the branch cell of rows that have MRs while branches
    are not resolved (the view passes "…" while resolving, "—" without a
    GitLab token), so the copy matches what the window shows.
    """
    has = report.get("sources") or {}

    def num(value, source):
        return str(value) if has.get(source) else unavailable

    def br(row):
        if not has.get("mr"):
            return unavailable
        if not report.get("branches_resolved"):
            return unresolved if row["mr_count"] else ""
        return format_branches(row["branches"], unknown_label=unknown_label)

    lines = ["\t".join(headers)]
    for c in report.get("categories") or []:
        lines.append("\t".join([
            c["name"], "", num(c["mr_count"], "mr"), br(c),
            num(c["bugfix"], "bugfix"), num(c["scan"], "scan")]))
        for p in c["projects"]:
            if not (include_idle or p["active"]):
                continue
            lines.append("\t".join([
                c["name"], p["project"], num(p["mr_count"], "mr"),
                br(p), num(p["bugfix"], "bugfix"),
                num(p["scan"], "scan")]))
    return "\n".join(lines) + "\n"
