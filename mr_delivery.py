"""Source MR vs translation-delivery MR for the MR Pipeline table.

Tranzor task identity is the **source** MR that triggered translation
(``merge_request_iid``). When that MR is already merged, IMPORT creates a
separate follow-up MR that actually receives the translation commits:

- title contains ``Translations for MR!{source_iid}`` (profile-dependent
  wrapping still keeps ``MR!{source_iid}``)
- source branch ``tranzor/translate-{source_iid}-{head12}-{proj8}-{task8}``

The platform persists ``delivery_mr_iid`` / ``delivery_project_id`` /
``import_mr_url`` on the task row, but GET ``/tasks`` historically omitted
them. This module:

1. Reads those fields when a newer payload supplies them.
2. Parses ``import_mr_url`` as a fallback.
3. Resolves the follow-up MR from GitLab when the payload is silent.

Pure logic (no Tkinter) so the GUI and unit tests share one implementation.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

# ``import_mr_url`` / GitLab web URL → iid.
_MR_IID_IN_URL = re.compile(r"/merge_requests/(\d+)(?:/|$|\?)")

# Title across commit-message profiles: legacy "[Tranzor] Translations for
# MR!3930", es_format "Tranzor | Translations for MR | MR!3930" (the iid is
# split onto the next pipe segment), chore_i18n
# "chore(i18n): translations for MR!3930".
DELIVERY_TITLE_RE = re.compile(
    r"translations for MR(?:\s*[|]\s*MR)?[!#](\d+)", re.IGNORECASE)
_MR_BANG_RE = re.compile(r"MR[!#](\d+)", re.IGNORECASE)

# Branch baked by TaskExecutor._build_delivery_source_branch.
DELIVERY_BRANCH_RE = re.compile(r"^tranzor/translate-(\d+)-")
# Language Lead fix MR opened after the original translation MR merged.
FIX_BRANCH_RE = re.compile(r"^tranzor-mr-fix-")

# GitLab title search that survives the es_format split
# ("Translations for MR" | "MR!3930").
DELIVERY_SEARCH_TERM = "MR!{iid}"


@dataclass(frozen=True)
class DeliveryRef:
    """The follow-up MR that received translation commits, if any."""

    project_id: str
    iid: int
    url: str = ""
    state: str = ""


def parse_mr_iid(value) -> Optional[int]:
    """Coerce a task/GitLab iid to ``int``; unparseable → ``None``."""
    if value is None or value is False:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def parse_mr_iid_from_url(url) -> Optional[int]:
    """Extract a GitLab MR iid from a web URL / ``import_mr_url``."""
    if not url:
        return None
    m = _MR_IID_IN_URL.search(str(url))
    return int(m.group(1)) if m else None


def gitlab_mr_url(project_id, mr_iid, base_url=None) -> str:
    """Build a GitLab MR web URL. Empty when project or iid is missing."""
    iid = parse_mr_iid(mr_iid)
    pid = str(project_id or "").strip().lstrip("/")
    if not pid or iid is None:
        return ""
    if base_url:
        root = str(base_url).rstrip("/")
    else:
        try:
            from gitlab_client import get_base_url
            root = (get_base_url() or "").rstrip("/")
        except Exception:
            root = ""
    if not root:
        return ""
    return f"{root}/{pid}/-/merge_requests/{iid}"


def task_digest(task_id) -> str:
    """Last segment of ``tranzor/translate-…-{task_digest}``.

    Mirrors ``TaskExecutor._build_delivery_source_branch`` so a GitLab
    fallback can pin the follow-up MR to *this* task when the same source
    MR has been translated more than once.
    """
    if not task_id:
        return ""
    return hashlib.sha256(str(task_id).encode("utf-8")).hexdigest()[:8]


def source_iid_from_delivery_mr(mr) -> Optional[int]:
    """Read the triggering source iid out of a follow-up MR's branch/title."""
    if not isinstance(mr, dict):
        return None
    branch = str(mr.get("source_branch") or "")
    m = DELIVERY_BRANCH_RE.match(branch)
    if m:
        return int(m.group(1))
    title = str(mr.get("title") or "")
    t = DELIVERY_TITLE_RE.search(title)
    if t:
        return int(t.group(1))
    if "translation" in title.lower():
        bang = _MR_BANG_RE.search(title)
        if bang:
            return int(bang.group(1))
    return None


def is_fix_branch(branch) -> bool:
    """True for Language Lead post-merge fix branches (``tranzor-mr-fix-*``)."""
    return bool(FIX_BRANCH_RE.match(str(branch or "")))


def is_delivery_candidate(mr, source_iid) -> bool:
    """True when ``mr`` is the original translation-import MR for ``source_iid``.

    Later Language Lead fix MRs reuse the same title template but live on
    ``tranzor-mr-fix-*``; those are :func:`is_fix_mr_candidate`, not this.
    """
    want = parse_mr_iid(source_iid)
    if want is None or not isinstance(mr, dict):
        return False
    iid = parse_mr_iid(mr.get("iid"))
    if iid is None or iid == want:
        return False
    if is_fix_branch(mr.get("source_branch")):
        return False
    got = source_iid_from_delivery_mr(mr)
    return got == want


def is_fix_mr_candidate(mr, source_iid) -> bool:
    """True when ``mr`` is a post-merge Language Lead fix MR for ``source_iid``."""
    want = parse_mr_iid(source_iid)
    if want is None or not isinstance(mr, dict):
        return False
    iid = parse_mr_iid(mr.get("iid"))
    if iid is None or iid == want:
        return False
    if not is_fix_branch(mr.get("source_branch")):
        return False
    got = source_iid_from_delivery_mr(mr)
    return got == want


def pick_delivery_mr(mrs, source_iid, task_id=None) -> Optional[dict]:
    """Choose the follow-up MR for one source iid (and optional task)."""
    candidates = [
        mr for mr in (mrs or []) if is_delivery_candidate(mr, source_iid)]
    if not candidates:
        return None
    digest = task_digest(task_id)
    if digest:
        matched = [
            mr for mr in candidates
            if str(mr.get("source_branch") or "").endswith("-" + digest)
        ]
        if matched:
            candidates = matched
    opened = [
        mr for mr in candidates
        if str(mr.get("state") or "").lower() == "opened"
    ]
    pool = opened or candidates

    def _iid_key(mr):
        return parse_mr_iid(mr.get("iid")) or 0

    return max(pool, key=_iid_key)


def pick_fix_mr(mrs, source_iid, exclude_iid=None) -> Optional[dict]:
    """Choose the latest Language Lead fix MR for one source iid.

    Prefers an opened MR, then the highest iid. ``exclude_iid`` drops the
    original translation-import MR when the same search hits both.
    """
    skip = parse_mr_iid(exclude_iid)
    candidates = []
    for mr in (mrs or []):
        if not is_fix_mr_candidate(mr, source_iid):
            continue
        iid = parse_mr_iid(mr.get("iid"))
        if skip is not None and iid == skip:
            continue
        candidates.append(mr)
    if not candidates:
        return None
    opened = [
        mr for mr in candidates
        if str(mr.get("state") or "").lower() == "opened"
    ]
    pool = opened or candidates

    def _iid_key(mr):
        return parse_mr_iid(mr.get("iid")) or 0

    return max(pool, key=_iid_key)


def ref_from_iid(mrs, iid, fallback_project="") -> Optional[DeliveryRef]:
    """Find ``iid`` in a GitLab MR list and wrap it as :class:`DeliveryRef`."""
    want = parse_mr_iid(iid)
    if want is None:
        return None
    for mr in (mrs or []):
        if parse_mr_iid(mr.get("iid")) == want:
            return delivery_ref_from_mr(mr, fallback_project=fallback_project)
    return None


def format_trans_mr_cell(delivery_iid, fix_iid=None) -> str:
    """Visible Trans MR# cell: ``1224`` or ``1224 → 1225``."""
    delivery = parse_mr_iid(delivery_iid)
    fix = parse_mr_iid(fix_iid)
    if delivery is None and fix is None:
        return "—"
    if delivery is None:
        return str(fix)
    if fix is None or fix == delivery:
        return str(delivery)
    return f"{delivery} → {fix}"


def current_trans_mr_iid(delivery_iid, fix_iid=None) -> Optional[int]:
    """The iid the Trans MR# click / status column should follow."""
    return parse_mr_iid(fix_iid) or parse_mr_iid(delivery_iid)


def trans_mr_sort_iid(cell) -> Optional[int]:
    """Numeric sort key: the right-hand (current) iid in a Trans MR# cell."""
    text = str(cell or "").strip()
    if text in ("", "—", "…"):
        return None
    parts = re.findall(r"\d+", text)
    return int(parts[-1]) if parts else parse_mr_iid(text)


def delivery_from_task(task) -> Optional[DeliveryRef]:
    """Return a distinct delivery MR named by the task payload, if any.

    ``delivery_mr_iid`` wins; ``import_mr_url`` is the older/partial field.
    A value that merely repeats the source MR is treated as "no follow-up".
    """
    if not isinstance(task, dict):
        return None
    source_iid = parse_mr_iid(task.get("merge_request_iid"))
    project = str(
        task.get("delivery_project_id") or task.get("project_id") or ""
    ).strip()
    iid = parse_mr_iid(task.get("delivery_mr_iid"))
    url = str(task.get("import_mr_url") or task.get("delivery_mr_url") or "")
    if iid is None:
        iid = parse_mr_iid_from_url(url)
    if iid is None:
        return None
    if source_iid is not None and iid == source_iid:
        delivery_project = str(task.get("delivery_project_id") or "").strip()
        source_project = str(task.get("project_id") or "").strip()
        if not delivery_project or delivery_project == source_project:
            return None
    if not project:
        return None
    if not url:
        url = gitlab_mr_url(project, iid)
    elif parse_mr_iid_from_url(url) != iid:
        url = gitlab_mr_url(project, iid) or url
    return DeliveryRef(project_id=project, iid=iid, url=url)


def delivery_ref_from_mr(mr, fallback_project="") -> Optional[DeliveryRef]:
    """Build a :class:`DeliveryRef` from a GitLab MR payload."""
    if not isinstance(mr, dict):
        return None
    iid = parse_mr_iid(mr.get("iid"))
    if iid is None:
        return None
    project = _project_path_from_mr(mr, iid) or str(fallback_project or "").strip()
    if not project:
        return None
    url = str(mr.get("web_url") or "") or gitlab_mr_url(project, iid)
    state = "" if mr.get("state") is None else str(mr.get("state"))
    return DeliveryRef(project_id=project, iid=iid, url=url, state=state)


def _project_path_from_mr(mr, iid) -> str:
    refs = mr.get("references") if isinstance(mr, dict) else None
    full = refs.get("full", "") if isinstance(refs, dict) else ""
    suffix = f"!{iid}"
    if full.endswith(suffix):
        return full[:-len(suffix)]
    path = urlparse(str(mr.get("web_url") or "")).path
    marker = "/-/merge_requests/"
    if marker in path:
        return path.split(marker, 1)[0].lstrip("/")
    return ""


def find_delivery_mr(project_id, source_iid, task_id=None,
                     client=None) -> Optional[DeliveryRef]:
    """GitLab title-search fallback when the task payload has no delivery MR."""
    pid = str(project_id or "").strip()
    src = parse_mr_iid(source_iid)
    if not pid or src is None:
        return None
    if client is None:
        client = _shared_client()
        if client is None:
            return None
    try:
        if not client.has_token():
            return None
        mrs = client.list_merge_requests(
            DELIVERY_SEARCH_TERM.format(iid=src),
            project_id=pid, in_field="title")
    except Exception:
        return None
    picked = pick_delivery_mr(mrs, src, task_id=task_id)
    return delivery_ref_from_mr(picked, fallback_project=pid)


def expand_mr_iid_filter(mr_iid, project_ids=None, client=None) -> set:
    """Iids a typed MR# filter should match: the number itself, plus its
    source iid when the number is a follow-up translation MR.

    Lets "4192" find the task that still stores source ``3930``. Failures
    (no token, 404, no project) return ``{typed}`` so the cheap source-iid
    match still works.
    """
    typed = parse_mr_iid(mr_iid)
    if typed is None:
        return set()
    out = {typed}
    projects = [
        str(p).strip() for p in (project_ids or []) if str(p or "").strip()]
    if not projects:
        return out
    if client is None:
        client = _shared_client()
        if client is None:
            return out
    try:
        if not client.has_token():
            return out
    except Exception:
        return out
    for pid in projects:
        try:
            mr = client.get_merge_request(pid, typed)
        except Exception:
            continue
        source = source_iid_from_delivery_mr(mr)
        if source is not None:
            out.add(source)
            break
    return out


def task_matches_mr_iid(task, matching_iids) -> bool:
    """True when the task's source *or* delivery iid is in ``matching_iids``."""
    if not isinstance(task, dict) or not matching_iids:
        return False
    source = parse_mr_iid(task.get("merge_request_iid"))
    if source is not None and source in matching_iids:
        return True
    delivery = delivery_from_task(task)
    return delivery is not None and delivery.iid in matching_iids


def _shared_client():
    try:
        import task_post_edit as _tpe
        return _tpe._shared_gitlab_client()
    except Exception:
        return None
