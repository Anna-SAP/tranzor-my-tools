"""Source MR vs translation-delivery MR for the MR Pipeline table.

Tranzor task identity is the **source** MR that triggered translation
(``merge_request_iid``). When that MR is already merged, IMPORT creates a
separate follow-up MR that actually receives the translation commits:

- title contains ``Translations for MR!{source_iid}`` (profile-dependent
  wrapping still keeps ``MR!{source_iid}``)
- source branch ``tranzor/translate-{source_iid}-{head12}-{proj8}-{task8}``

The platform persists ``delivery_mr_iid`` / ``delivery_project_id`` /
``import_mr_url`` on the task row, but GET ``/tasks`` historically omitted
them. Every later Language Lead fix opens another MR on ``tranzor-mr-fix-*``
and re-points those fields at it, so the payload names the *newest* fix MR,
not the import MR. This module:

1. Reads those fields when a newer payload supplies them.
2. Parses ``import_mr_url`` as a fallback.
3. Resolves the whole chain — import MR plus every fix MR — from GitLab.

Pure logic (no Tkinter) so the GUI and unit tests share one implementation.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timezone
from typing import Optional
from urllib.parse import urlparse

from time_display import parse_iso_datetime

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

# Joins a task's Trans MRs in the Trans MR# cell, oldest first.
TRANS_MR_SEPARATOR = " → "


@dataclass(frozen=True)
class DeliveryRef:
    """One translation MR of a task: the import MR or a Language Lead fix."""

    project_id: str
    iid: int
    url: str = ""
    state: str = ""
    target_branch: str = ""
    # GitLab ``created_at``: a task's Trans MRs are listed in this order.
    created_at: str = ""
    # Tells the import MR (``tranzor/translate-*``) from a fix MR
    # (``tranzor-mr-fix-*``). Empty when only the task payload named the MR.
    source_branch: str = ""


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
    """True when ``mr`` is a post-merge Language Lead fix MR for ``source_iid``.

    The ``tranzor-mr-fix-*`` branch already says what the MR is, so an exact
    ``MR!{source_iid}`` anywhere in the title is enough to tie it to the
    source MR. Language Leads retitle these freely ("fix(LOC-25286) further
    fr-FR linguistic fixes for MR!4003"); requiring the "Translations for
    MR!…" template silently dropped such fixes from the Trans MR# chain.
    """
    want = parse_mr_iid(source_iid)
    if want is None or not isinstance(mr, dict):
        return False
    iid = parse_mr_iid(mr.get("iid"))
    if iid is None or iid == want:
        return False
    if not is_fix_branch(mr.get("source_branch")):
        return False
    return want in {
        int(n) for n in _MR_BANG_RE.findall(str(mr.get("title") or ""))}


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


def ref_from_iid(mrs, iid, fallback_project="") -> Optional[DeliveryRef]:
    """Find ``iid`` in a GitLab MR list and wrap it as :class:`DeliveryRef`."""
    want = parse_mr_iid(iid)
    if want is None:
        return None
    for mr in (mrs or []):
        if parse_mr_iid(mr.get("iid")) == want:
            return delivery_ref_from_mr(mr, fallback_project=fallback_project)
    return None


def trans_mr_chain(mrs, source_iid, task_id=None, known=(),
                   fallback_project="") -> list:
    """Every translation MR behind one task, oldest first.

    ``mrs`` is the ``MR!{source_iid}`` title search. The chain holds:

    - ``known`` — MRs already tied to the task (the payload's delivery MR, an
      earlier resolution), refreshed from ``mrs`` when the search has them;
    - the task's own import MR(s), pinned by :func:`task_digest`. With no
      digest match, :func:`pick_delivery_mr` stands in — unless ``known``
      already holds a non-fix MR, which is then taken to be the import MR;
    - every Language Lead fix MR for the source MR.

    Nothing is capped or folded: a source MR fixed three times shows all
    three fix MRs after its import MR.
    """
    mrs = [mr for mr in (mrs or []) if isinstance(mr, dict)]
    chain = {}
    for ref in known or ():
        if ref is not None:
            chain[ref.iid] = ref_from_iid(
                mrs, ref.iid, fallback_project=ref.project_id) or ref
    digest = task_digest(task_id)
    imports = [
        mr for mr in mrs
        if is_delivery_candidate(mr, source_iid) and digest
        and str(mr.get("source_branch") or "").endswith("-" + digest)
    ]
    if not imports and all(
            is_fix_branch(ref.source_branch) for ref in chain.values()):
        picked = pick_delivery_mr(mrs, source_iid, task_id=task_id)
        imports = [picked] if picked is not None else []
    fixes = [mr for mr in mrs if is_fix_mr_candidate(mr, source_iid)]
    for mr in imports + fixes:
        ref = delivery_ref_from_mr(mr, fallback_project=fallback_project)
        if ref is not None:
            chain[ref.iid] = ref
    return sort_chronologically(chain.values())


def _created_utc(ref):
    stamp = parse_iso_datetime(getattr(ref, "created_at", "") or None)
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def sort_chronologically(refs) -> list:
    """Order Trans MRs oldest first by GitLab ``created_at``.

    GitLab hands out iids in creation order within a project, so the iid
    breaks ties and stands in for every ref whenever one ``created_at`` is
    unknown (a ref built from the task payload alone).
    """
    refs = [ref for ref in (refs or ()) if ref is not None]
    stamps = [_created_utc(ref) for ref in refs]
    if refs and all(stamp is not None for stamp in stamps):
        order = sorted(range(len(refs)),
                       key=lambda i: (stamps[i], refs[i].iid))
        return [refs[i] for i in order]
    return sorted(refs, key=lambda ref: ref.iid)


def format_trans_mr_cell(chain) -> str:
    """Visible Trans MR# cell: every MR of ``chain`` (oldest first), e.g.
    ``4213`` or ``4213 → 4214 → 4233 → 4237``."""
    iids = [str(ref.iid) for ref in (chain or ()) if ref is not None]
    return TRANS_MR_SEPARATOR.join(iids) if iids else "—"


def current_trans_mr(chain) -> Optional[DeliveryRef]:
    """The Trans MR the status / branch cells and "is open" filter follow.

    ``chain`` is oldest first. The newest still-open MR wins — it is the
    translation work in flight — and otherwise simply the newest.
    """
    refs = [ref for ref in (chain or ()) if ref is not None]
    if not refs:
        return None
    opened = [ref for ref in refs if str(ref.state or "").lower() == "opened"]
    return (opened or refs)[-1]


def trans_mr_sort_iid(cell) -> Optional[int]:
    """Numeric sort key: the right-hand (newest) iid in a Trans MR# cell."""
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
    branch = ("" if mr.get("target_branch") is None
              else str(mr.get("target_branch")))
    return DeliveryRef(project_id=project, iid=iid, url=url, state=state,
                       target_branch=branch,
                       created_at=str(mr.get("created_at") or ""),
                       source_branch=str(mr.get("source_branch") or ""))


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


def find_trans_mrs(project_id, source_iid, task_id=None, known=(),
                   client=None) -> list:
    """One GitLab title search → the task's whole Trans MR chain.

    Oldest first (:func:`trans_mr_chain`): the translation-import MR, then
    every later Language Lead fix MR on ``tranzor-mr-fix-*``. A task counts
    as *having* a translation MR when the chain is non-empty — a fix MR can
    outlive an import MR the search no longer matches — which is exactly
    what the Trans MR# cell renders. ``known`` (e.g. the payload's delivery
    MR) is kept and completed. Any failure (no token, no project, network
    error) degrades to ``known`` alone.
    """
    fallback = sort_chronologically(known)
    pid = str(project_id or "").strip()
    src = parse_mr_iid(source_iid)
    if not pid or src is None:
        return fallback
    if client is None:
        client = _shared_client()
        if client is None:
            return fallback
    try:
        if not client.has_token():
            return fallback
        mrs = client.list_merge_requests(
            DELIVERY_SEARCH_TERM.format(iid=src),
            project_id=pid, in_field="title")
    except Exception:
        return fallback
    return trans_mr_chain(mrs, src, task_id=task_id, known=known,
                          fallback_project=pid)


def find_delivery_mr(project_id, source_iid, task_id=None,
                     client=None) -> Optional[DeliveryRef]:
    """GitLab title-search fallback when the task payload has no delivery MR."""
    for ref in find_trans_mrs(
            project_id, source_iid, task_id=task_id, client=client):
        if not is_fix_branch(ref.source_branch):
            return ref
    return None


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
