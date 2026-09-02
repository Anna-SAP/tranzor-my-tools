"""
Key Origin —— 把字符串 Key 反查到源头翻译任务
==============================================

Bug Fix 通道尚未覆盖的产品（目前 ``common/uns``）无法直接开 bug-fix 任务。
Language Lead 的绕行办法是：找到这些 Key **当初是在哪一次 MR Pipeline
（或 File Translation）任务里译出来的**，再打开那次任务做后期修订。

本模块是无 Tk 依赖的纯逻辑层：解析粘贴进来的 Key / 路径、调用注入的
``search_fn``（默认包一层 ``GET /api/v1/translations/search``）、把命中
折叠成任务、挑出建议打开的源头任务。GUI 在 :mod:`gui_tab_key_origin`。

UNS 邮件模板的运行时身份是 ``{opus_id}:::seg:::{seg_uid}``，但库表
``translations.opus_id`` 只存分段前的基 Key、``seg_uid`` 另列。所以查找
前必须把 ``:::seg:::N`` 剥掉，否则 exact / fuzzy 都匹配不到。
"""
from __future__ import annotations

import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Iterable
from urllib.parse import urlencode

PIPELINE_SEP = ":::seg:::"
MAX_KEYS = 80
DEFAULT_PAGE_SIZE = 200
DEFAULT_MAX_PAGES = 5

# uns-app/{newTemplateStorage|templateStorage}/<dir>/<file>[.hbs]
_UNS_PATH_RE = re.compile(
    r"(?:^|/)uns-app/(?P<store>newTemplateStorage|templateStorage)/"
    r"(?P<dir>[^/]+)/(?P<file>[^/]+)$",
    re.IGNORECASE,
)
_LOCALE_SUFFIX_RE = re.compile(r"__(?:[a-z]{2}[_-][A-Z]{2})$")
_SKIP_LINE_RE = re.compile(
    r"^(?://|#|\{code|\{noformat|/code|h[1-6]\.|----)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LookupQuery:
    """One user-supplied key after normalization."""

    original: str
    search_opus_id: str
    seg_uid: int | None = None
    kind: str = "key"  # "key" | "path" | "opus"


@dataclass
class OriginTask:
    task_id: str
    task_name: str = ""
    project_id: str = ""
    mr_iid: int | None = None
    created_at: str = ""
    source_type: str = "mr"
    langs: list[str] = field(default_factory=list)
    source_text: str = ""
    opus_id: str = ""
    row_count: int = 0


SearchFn = Callable[..., dict]


def split_pipeline_key(key: str) -> tuple[str, int | None]:
    """Split a runtime unit identity into ``(opus_id, seg_uid)``.

    Mirrors Tranzor ``build_pipeline_translation_key`` / ``split_pipeline_translation_key``:
    ``common.uns.foo:::seg:::10`` → ``("common.uns.foo", 10)``. A key without
    the separator is returned unchanged.
    """
    text = str(key or "")
    opus_id, separator, seg_uid = text.rpartition(PIPELINE_SEP)
    if separator and opus_id:
        if seg_uid.isdecimal() and int(seg_uid) > 0:
            return opus_id, int(seg_uid)
        raise ValueError(f"Invalid segmented pipeline UID: {seg_uid!r}")
    return text, None


def uns_path_to_key(line: str) -> str | None:
    """Convert a UNS template file path into the stored MR-pipeline opus_id.

    ``uns-app/newTemplateStorage/foo/foo__email_html__7710__de_DE.hbs``
    → ``common.uns.new.foo__email_html__7710``

    ``uns-app/templateStorage/foo/foo__email_html__7710__de_DE.hbs``
    → ``common.uns.foo__email_html__7710``
    """
    text = (line or "").strip().replace("\\", "/")
    if not text:
        return None
    match = _UNS_PATH_RE.search(text)
    if not match:
        return None
    store = match.group("store")
    fname = os.path.basename(match.group("file"))
    if "." in fname:
        fname = fname.rsplit(".", 1)[0]
    fname = _LOCALE_SUFFIX_RE.sub("", fname)
    if not fname:
        return None
    if store.lower() == "newtemplatestorage":
        return f"common.uns.new.{fname}"
    return f"common.uns.{fname}"


def _clean_line(line: str) -> str:
    text = (line or "").strip()
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        text = text[1:-1].strip()
    return text.strip(" \t\"'")


def parse_lookup_keys(raw, *, max_keys: int = MAX_KEYS) -> list[LookupQuery]:
    """Parse pasted text into de-duplicated :class:`LookupQuery` rows.

    Accepts a string (split on newlines) or an iterable of strings. Skips
    blanks, JIRA wiki chrome (``{code}``, ``h3.``, ``//NEW``), and keeps
    the first occurrence when two lines collapse to the same search id.
    """
    if raw is None:
        lines: list[str] = []
    elif isinstance(raw, str):
        lines = raw.splitlines()
    else:
        lines = [str(x) for x in raw]

    seen: set[str] = set()
    out: list[LookupQuery] = []
    for line in lines:
        text = _clean_line(line)
        if not text or _SKIP_LINE_RE.match(text):
            continue
        path_key = uns_path_to_key(text)
        if path_key:
            search_id, seg_uid = path_key, None
            kind = "path"
            original = text
        else:
            try:
                search_id, seg_uid = split_pipeline_key(text)
            except ValueError:
                search_id, seg_uid = text, None
            kind = "opus" if search_id.startswith("RingCentral.") else "key"
            original = text
        search_id = search_id.strip()
        if not search_id or search_id in seen:
            continue
        seen.add(search_id)
        out.append(LookupQuery(
            original=original,
            search_opus_id=search_id,
            seg_uid=seg_uid,
            kind=kind,
        ))
        if len(out) >= max_keys:
            break
    return out


def _created_sort_key(value: str) -> str:
    return str(value or "")


def collapse_entries_to_tasks(entries: Iterable[dict]) -> list[OriginTask]:
    """Fold translation-search rows into unique tasks, newest first."""
    by_id: OrderedDict[str, OriginTask] = OrderedDict()
    langs: dict[str, set[str]] = {}
    for row in entries or []:
        tid = str(row.get("task_id") or "").strip()
        if not tid:
            continue
        lang = (row.get("target_language") or "").strip()
        if tid not in by_id:
            mr_raw = row.get("mr_iid")
            try:
                mr_iid = int(mr_raw) if mr_raw not in (None, "") else None
            except (TypeError, ValueError):
                mr_iid = None
            by_id[tid] = OriginTask(
                task_id=tid,
                task_name=str(row.get("task_name") or ""),
                project_id=str(row.get("project_id") or ""),
                mr_iid=mr_iid,
                created_at=str(row.get("created_at") or ""),
                source_type=str(row.get("source_type") or "mr"),
                source_text=str(row.get("source_text") or ""),
                opus_id=str(row.get("opus_id") or ""),
                row_count=0,
            )
            langs[tid] = set()
        task = by_id[tid]
        task.row_count += 1
        if lang:
            langs[tid].add(lang)
        created = str(row.get("created_at") or "")
        if created and created > task.created_at:
            task.created_at = created
        if not task.opus_id and row.get("opus_id"):
            task.opus_id = str(row.get("opus_id"))
        if not task.source_text and row.get("source_text"):
            task.source_text = str(row.get("source_text"))
    for tid, task in by_id.items():
        task.langs = sorted(langs.get(tid) or [])
    return sorted(
        by_id.values(),
        key=lambda t: _created_sort_key(t.created_at),
        reverse=True,
    )


def pick_recommended(tasks: Iterable[OriginTask]) -> OriginTask | None:
    """Prefer the newest MR-pipeline task; fall back to the newest of any kind."""
    rows = list(tasks or [])
    if not rows:
        return None
    mr_rows = [t for t in rows if (t.source_type or "") == "mr"]
    pool = mr_rows or rows
    return sorted(
        pool,
        key=lambda t: _created_sort_key(t.created_at),
        reverse=True,
    )[0]


def tranzor_origin_url(
    *,
    base_url: str,
    source_type: str = "mr",
    project_id: str = "",
    mr_iid=None,
    task_id: str = "",
) -> str:
    """Deep-link into the Tranzor surface where a Language Lead can post-edit."""
    origin = (base_url or "").rstrip("/")
    if not origin:
        origin = "http://tranzor-platform.int.rclabenv.com"
    if (source_type or "mr") == "mr" and project_id and mr_iid not in (None, ""):
        query = urlencode({"project_id": project_id, "mr_id": mr_iid})
        return f"{origin}/static/?{query}"
    if task_id:
        return f"{origin}/static/legacy/tasks/{task_id}"
    return f"{origin}/static/"


def _collect_entries(
    search_fn: SearchFn,
    *,
    opus_id: str,
    match_mode: str,
    source_type: str,
    page_size: int,
    max_pages: int,
) -> tuple[list[dict], int]:
    entries: list[dict] = []
    total = 0
    size = max(1, min(int(page_size), 200))
    pages = max(1, int(max_pages))
    for page in range(pages):
        payload = search_fn(
            opus_id=opus_id,
            match_mode=match_mode,
            source_type=source_type,
            limit=size,
            offset=page * size,
        ) or {}
        try:
            total = int(payload.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        chunk = list(payload.get("entries") or [])
        entries.extend(chunk)
        if not chunk or len(entries) >= total:
            break
    return entries, total


def _search_one(
    search_fn: SearchFn,
    query: LookupQuery,
    *,
    source_type: str,
    fallback_file: bool,
    page_size: int,
    max_pages: int,
) -> dict:
    """Exact first, then fuzzy; optional File-Translation fallback."""
    tried: list[tuple[str, str]] = []
    last_error = None
    types_to_try = [source_type]
    if fallback_file and source_type == "mr":
        types_to_try.append("file")

    for stype in types_to_try:
        for mode in ("exact", "fuzzy"):
            tried.append((stype, mode))
            try:
                entries, total = _collect_entries(
                    search_fn,
                    opus_id=query.search_opus_id,
                    match_mode=mode,
                    source_type=stype,
                    page_size=page_size,
                    max_pages=max_pages,
                )
            except Exception as exc:  # noqa: BLE001 — surface per-key
                last_error = str(exc)
                continue
            if total or entries:
                tasks = collapse_entries_to_tasks(entries)
                return {
                    "query": query,
                    "match_mode": mode,
                    "source_type_used": stype,
                    "total_rows": total,
                    "tasks": tasks,
                    "recommended": pick_recommended(tasks),
                    "error": None,
                    "tried": tried,
                }
    return {
        "query": query,
        "match_mode": None,
        "source_type_used": source_type,
        "total_rows": 0,
        "tasks": [],
        "recommended": None,
        "error": last_error,
        "tried": tried,
    }


def group_origin_results(results: Iterable[dict], *, base_url: str = "") -> list[dict]:
    """Cluster per-key hits by the recommended origin task."""
    groups: OrderedDict[tuple, dict] = OrderedDict()
    for item in results or []:
        rec: OriginTask | None = item.get("recommended")
        query: LookupQuery = item["query"]
        if rec is None:
            key = ("missing", "", None)
            miss = groups.get(key)
            if miss is None:
                miss = {
                    "source_type": "",
                    "project_id": "",
                    "mr_iid": None,
                    "task_id": "",
                    "task_name": "",
                    "created_at": "",
                    "keys": [],
                    "recommended": None,
                    "url": "",
                    "missing": True,
                    "hit_count": 0,
                }
                groups[key] = miss
            miss["keys"].append(query)
            continue
        if rec.source_type == "mr" and rec.project_id and rec.mr_iid not in (None, ""):
            gkey = ("mr", rec.project_id, int(rec.mr_iid))
        else:
            gkey = (rec.source_type or "file", rec.task_id, None)
        group = groups.get(gkey)
        if group is None:
            group = {
                "source_type": rec.source_type,
                "project_id": rec.project_id,
                "mr_iid": rec.mr_iid,
                "task_id": rec.task_id,
                "task_name": rec.task_name,
                "created_at": rec.created_at,
                "keys": [],
                "recommended": rec,
                "url": tranzor_origin_url(
                    base_url=base_url,
                    source_type=rec.source_type,
                    project_id=rec.project_id,
                    mr_iid=rec.mr_iid,
                    task_id=rec.task_id,
                ),
                "missing": False,
                "hit_count": 0,
            }
            groups[gkey] = group
        group["keys"].append(query)
        group["hit_count"] += 1
        if rec.created_at and rec.created_at > (group["created_at"] or ""):
            group["created_at"] = rec.created_at
            group["recommended"] = rec
            group["task_id"] = rec.task_id
            group["task_name"] = rec.task_name
            group["url"] = tranzor_origin_url(
                base_url=base_url,
                source_type=rec.source_type,
                project_id=rec.project_id,
                mr_iid=rec.mr_iid,
                task_id=rec.task_id,
            )
    rows = list(groups.values())
    found = [g for g in rows if not g.get("missing")]
    found.sort(key=lambda g: g.get("created_at") or "", reverse=True)
    missing = [g for g in rows if g.get("missing")]
    return found + missing


def locate_keys(
    raw,
    search_fn: SearchFn,
    *,
    source_type: str = "mr",
    fallback_file: bool = True,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_keys: int = MAX_KEYS,
    base_url: str = "",
) -> dict:
    """Parse ``raw``, search each key, and return per-key hits plus groups.

    ``search_fn`` is injected so tests never touch the network. Production
    GUI passes :func:`export_mr_pipeline.search_translations`.
    """
    queries = parse_lookup_keys(raw, max_keys=max_keys + 1)
    truncated = len(queries) > max_keys
    queries = queries[:max_keys]
    results = [
        _search_one(
            search_fn,
            query,
            source_type=source_type,
            fallback_file=fallback_file,
            page_size=page_size,
            max_pages=max_pages,
        )
        for query in queries
    ]
    groups = group_origin_results(results, base_url=base_url)
    found = sum(1 for r in results if r.get("recommended") is not None)
    return {
        "queries": queries,
        "results": results,
        "groups": groups,
        "found": found,
        "missing": len(results) - found,
        "truncated": truncated,
    }


def format_origin_report(payload: dict) -> str:
    """Plain-text / TSV report suitable for clipboard or a JIRA comment."""
    lines = []
    groups = payload.get("groups") or []
    found = payload.get("found", 0)
    missing = payload.get("missing", 0)
    lines.append(f"found={found} missing={missing} groups={len([g for g in groups if not g.get('missing')])}")
    for group in groups:
        if group.get("missing"):
            for query in group.get("keys") or []:
                lines.append(f"MISSING\t{query.search_opus_id}")
            continue
        rec: OriginTask | None = group.get("recommended")
        label = group.get("task_name") or ""
        if rec and rec.source_type == "mr" and rec.project_id and rec.mr_iid:
            label = f"{rec.project_id} MR#{rec.mr_iid}"
        url = group.get("url") or ""
        for query in group.get("keys") or []:
            lines.append(
                f"{query.search_opus_id}\t{label}\t{rec.task_id if rec else ''}\t{url}"
            )
    return "\n".join(lines)
