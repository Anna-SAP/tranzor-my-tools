"""
Tranzor 全量翻译导出模块（Full Translation Export）
====================================================

目的:
    在 Tranzor AI 迁移后，File Translation / MR Pipeline 的翻译结果不再自动
    回写到旧 l10n portal 的"全量翻译库"。本模块通过现有 Tranzor HTTP API
    端到端聚合出"按产品 × 按语言"的全量翻译数据包，结构对齐历史 AP.zip：

        <Product>/<locale>/trunk/opus_jsons/source.json
        每条 = {"opusID": "...", "pseudoHash": "", "stringValue": "..."}

    产品名从 opusID 解析而来：
        RingCentral.<product>.<hash>.<keyPath>  →  product = 第 2 段

用法（CLI）:
    python export_full_translations.py --out FullTranslations.zip
    python export_full_translations.py --out tmp.zip --products analyticsPortal,apTwilight
    python export_full_translations.py --out tmp.zip --locales en-US,de-DE
    python export_full_translations.py --out tmp.zip --sources legacy   # 只走 File Translation
    python export_full_translations.py --out tmp.zip --sources mr       # 只走 MR Pipeline

设计约束:
    - 纯加法：不修改任何现有模块。仅通过 import 只读地复用它们的函数。
    - 线程安全：聚合结果用锁保护，供 GUI 后台线程调用。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

# 将本目录加入 sys.path，确保无论从哪里启动都能 import 同级模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_translations as _legacy       # noqa: E402  — 只读 import
import export_mr_pipeline as _mr            # noqa: E402  — 只读 import


# Source kind tags used in LightInventory.products[*]["source"]
SRC_LEGACY = "legacy"
SRC_MR = "mr"
SRC_SCAN = "scan"  # Missing Translation Scan (manual scans that fill i18n gaps)

# Source language for the entire Tranzor pipeline. Translation rows store
# their original en-US copy in the ``source_text`` field; the AP.zip
# convention requires that text to live under <product>/en-US/.
SOURCE_LOCALE = "en-US"


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_RC_PREFIX = "RingCentral."
_UNKNOWN_PRODUCT = "_unknown"

ProgressCb = Optional[Callable[[str], None]]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def parse_product(opus_id: str) -> str:
    """从 opusID 解析产品名。

    预期格式: RingCentral.<product>.<hash>.<keyPath...>
    非 RingCentral 前缀或段数不足时返回 _UNKNOWN_PRODUCT。
    """
    if not opus_id or not isinstance(opus_id, str):
        return _UNKNOWN_PRODUCT
    if not opus_id.startswith(_RC_PREFIX):
        return _UNKNOWN_PRODUCT
    parts = opus_id.split(".", 2)
    if len(parts) < 2 or not parts[1]:
        return _UNKNOWN_PRODUCT
    return parts[1]


def _log(cb: ProgressCb, msg: str) -> None:
    if cb is None:
        print(msg)
    else:
        try:
            cb(msg)
        except Exception:
            # 进度回调绝不应该中断聚合
            pass


# ---------------------------------------------------------------------------
# 核心聚合
# ---------------------------------------------------------------------------

class FullTranslationInventory:
    """聚合结果容器。

    data[product][locale] = { opusID -> stringValue }
    product_key_counts[product] = 去重后的 opusID 总数（跨语言）
    all_locales = 所有出现过的 locale 集合
    """

    def __init__(self) -> None:
        self.data: Dict[str, Dict[str, Dict[str, str]]] = {}
        # Parallel structure tracking where each (product, locale, opus_id)
        # value came from. Follows the same "last write wins" semantics as
        # ``data`` so the metadata always points at the task that produced
        # the currently exported translation.
        #   sources[product][locale][opus_id] = {"source": "MR"|"Legacy",
        #                                        "task_id": "...",
        #                                        "task_name": "..."}
        # This is intentionally a sibling of ``data`` (not embedded in it)
        # so AP.zip / any existing consumer that iterates ``data`` stays
        # byte-identical.
        self.sources: Dict[str, Dict[str, Dict[str, dict]]] = {}
        self._lock = threading.Lock()

    # ---- 写入 --------------------------------------------------------
    def ingest(self, opus_id: str, locale: str, value: str,
               source_meta: Optional[dict] = None) -> None:
        if not opus_id or not locale or value is None:
            return
        product = parse_product(opus_id)
        with self._lock:
            prod_map = self.data.setdefault(product, {})
            loc_map = prod_map.setdefault(locale, {})
            # 后写覆盖前写，用于"取最新一条"去重策略
            loc_map[opus_id] = value
            if source_meta:
                src_prod = self.sources.setdefault(product, {})
                src_loc = src_prod.setdefault(locale, {})
                src_loc[opus_id] = source_meta

    def ingest_entries(
        self,
        entries: Iterable[dict],
        *,
        opus_key: str = "opus_id",
        locale_key: str = "target_language",
        value_key: str = "translated_text",
        source_locale: Optional[str] = None,
        source_value_key: str = "source_text",
        source_meta: Optional[dict] = None,
    ) -> int:
        """批量写入。返回实际写入的目标语条目数（未计入空值或源语言副本）。

        当 source_locale 非空时，每条 entry 还会把 ``source_text`` 写一份到
        ``data[product][source_locale]``，让 AP.zip 输出可以包含 en-US 源语
        言目录（target_language 维度天然不包含 en-US）。

        source_meta: 可选的 provenance 字典（如 {"source": "MR",
        "task_id": "...", "task_name": "..."}）。会跟随每条写入的译文记录，
        用于合并 JSON 导出时标注每条翻译的源头任务。
        """
        n = 0
        for entry in entries:
            oid = entry.get(opus_key) or ""
            loc = entry.get(locale_key) or ""
            val = entry.get(value_key)
            if oid and loc and val not in (None, ""):
                self.ingest(oid, loc, val, source_meta=source_meta)
                n += 1
            if source_locale and oid:
                src = entry.get(source_value_key)
                if src not in (None, ""):
                    self.ingest(oid, source_locale, src, source_meta=source_meta)
        return n

    # ---- 查询 --------------------------------------------------------
    def product_key_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        with self._lock:
            for product, locales in self.data.items():
                seen = set()
                for loc_map in locales.values():
                    seen.update(loc_map.keys())
                counts[product] = len(seen)
        return counts

    def all_locales(self) -> List[str]:
        locales = set()
        with self._lock:
            for prod in self.data.values():
                locales.update(prod.keys())
        return sorted(locales)

    def products_sorted_by_key_count(self) -> List[Tuple[str, int]]:
        counts = self.product_key_counts()
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def total_entries(self) -> int:
        n = 0
        with self._lock:
            for prod in self.data.values():
                for loc_map in prod.values():
                    n += len(loc_map)
        return n


# ---------------------------------------------------------------------------
# 轻量清单（LightInventory）
# ---------------------------------------------------------------------------
#
# 用途:
#     "Full Translations" 面板初始化时，仅加载"产品 + 语言"两个选择维度，绝不
#     拉取任何翻译正文。这里聚合的全部数据来自:
#       - GET /api/v1/dashboard/filters       (单条 distinct SQL — ms 级)
#       - GET /api/v1/legacy/tasks            (任务列表，自带 target_languages)
#       - GET /api/v1/dashboard/overview      (按 project_id 的 count 聚合)
#     这些接口在 backend 都是聚合查询，不会为获取列表而 join translations 全表。
#
# 输出结构:
#     LightInventory.products = [
#         {
#             "id": "<source>::<project_id_or_name>",
#             "label": "[Legacy] CoreLib/mthor",
#             "source": "legacy" | "mr",
#             "project_id": "CoreLib/mthor",
#             "task_count": 12,                 # int 或 None（未填）
#             "entry_count": 5238,              # int 或 None（未填）
#             "languages": ["zh-CN", "ja-JP"],  # 仅本产品/项目的语言（hint）
#         },
#         ...
#     ]
#     LightInventory.locales = ["de-DE", "es-ES", ...]
#

class LightInventory:
    """Lightweight Product × Language index used by the Full Translations tab.

    Constructed by :func:`build_light_inventory`. Does **not** contain any
    translation text. Used purely for populating selectors so the panel can
    render in <2s on first show.
    """

    def __init__(self) -> None:
        self.products: List[dict] = []
        self.locales: List[str] = []
        self._lock = threading.Lock()

    # ---- helpers ----------------------------------------------------
    def product_ids(self) -> List[str]:
        return [p["id"] for p in self.products]

    def find(self, product_id: str) -> Optional[dict]:
        for p in self.products:
            if p["id"] == product_id:
                return p
        return None

    def split_selection(
        self, selected_ids: Iterable[str]
    ) -> Tuple[Set[str], Set[str], Set[str]]:
        """Split a selection of product IDs into
        (legacy_projects, mr_projects, scan_projects).

        Each item in selected_ids matches LightInventory.products[*]["id"]
        which is encoded as ``<source>::<project_id>``.
        """
        legacy: Set[str] = set()
        mr: Set[str] = set()
        scan: Set[str] = set()
        for sid in selected_ids:
            p = self.find(sid)
            if not p:
                continue
            if p["source"] == SRC_LEGACY:
                legacy.add(p["project_id"])
            elif p["source"] == SRC_MR:
                mr.add(p["project_id"])
            elif p["source"] == SRC_SCAN:
                scan.add(p["project_id"])
        return legacy, mr, scan


def _make_product_id(source: str, project_id: str) -> str:
    return f"{source}::{project_id}"


def _build_legacy_light(
    progress_cb: ProgressCb,
) -> Tuple[List[dict], Set[str]]:
    """Discover legacy products+languages without fetching translation text.

    Strategy: enumerate the legacy task list once (paginated). Each task row
    already carries ``project_name`` and ``target_languages``, so we can
    aggregate the project → tasks/languages mapping without ever calling the
    per-task ``/translations`` endpoint.
    """
    _log(progress_cb, "  [Legacy] 拉取 Completed task 列表（仅 metadata）...")
    try:
        tasks = _legacy.fetch_tasks()
    except Exception as e:
        _log(progress_cb, f"  ⚠ [Legacy] 获取 task 列表失败: {e}")
        return [], set()

    by_project: Dict[str, dict] = {}
    locales: Set[str] = set()

    for t in tasks:
        pname = (t.get("project_name") or "").strip() or "(unknown)"
        langs = [str(x) for x in (t.get("target_languages") or []) if x]
        for lng in langs:
            locales.add(lng)
        bucket = by_project.setdefault(
            pname,
            {
                "id": _make_product_id(SRC_LEGACY, pname),
                "label": f"[Legacy] {pname}",
                "source": SRC_LEGACY,
                "project_id": pname,
                "task_count": 0,
                "entry_count": None,  # legacy task list 不携带 source 计数
                "languages": set(),
            },
        )
        bucket["task_count"] += 1
        bucket["languages"].update(langs)

    products = []
    for p in by_project.values():
        p["languages"] = sorted(p["languages"])
        products.append(p)

    _log(
        progress_cb,
        f"  ✓ [Legacy] {len(products)} 个项目 / {len(locales)} 种语言",
    )
    return products, locales


def _build_mr_light(
    progress_cb: ProgressCb,
) -> Tuple[List[dict], Set[str]]:
    """Discover MR Pipeline products+languages via cheap dashboard endpoints.

    Two backend calls:
      1. /dashboard/filters — distinct project_ids + languages, single SQL
      2. /dashboard/overview?project_id=X — total_cases per project (parallel)

    Neither call ever loads translation text.
    """
    _log(progress_cb, "  [MR] 拉取 dashboard filters（distinct projects+languages）...")
    try:
        filters = _mr.fetch_mr_filters_full()
    except Exception as e:
        _log(progress_cb, f"  ⚠ [MR] 获取 filters 失败: {e}")
        return [], set()

    project_ids = [str(x) for x in filters.get("project_ids", []) if x]
    locales = set(str(x) for x in filters.get("languages", []) if x)

    products: List[dict] = []
    for pid in project_ids:
        products.append(
            {
                "id": _make_product_id(SRC_MR, pid),
                "label": f"[MR] {pid}",
                "source": SRC_MR,
                "project_id": pid,
                "task_count": None,
                "entry_count": None,
                "languages": [],
            }
        )

    if not products:
        return products, locales

    # Parallel hydrate of per-project entry_count via /dashboard/overview.
    # Each call is a single SQL aggregate (count Translation rows for tasks
    # under that project_id) — typically tens of milliseconds.
    def _fetch_one(pid: str) -> Tuple[str, Optional[int]]:
        try:
            data = _mr.fetch_dashboard_overview(project_id=pid)
            return pid, int(data.get("total_cases") or 0)
        except Exception as e:
            _log(progress_cb, f"  ⚠ [MR] overview {pid} 失败: {e}")
            return pid, None

    workers = min(8, len(products))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, p["project_id"]): p for p in products}
        for f in as_completed(futures):
            pid, count = f.result()
            for p in products:
                if p["project_id"] == pid:
                    p["entry_count"] = count
                    break

    _log(
        progress_cb,
        f"  ✓ [MR] {len(products)} 个项目 / {len(locales)} 种语言",
    )
    return products, locales


def _build_scan_light(
    progress_cb: ProgressCb,
) -> Tuple[List[dict], Set[str]]:
    """Discover Scan Tasks products (by scan.project_id) without pulling text.

    The scan list endpoint is already cheap (no translations joined), so we
    paginate it once and aggregate distinct ``project_id`` values. Languages
    are left empty here — Scan tasks don't advertise their target_languages
    at the task level; the UI will simply offer the combined locale list
    from the other sources plus whatever the heavy fetch surfaces later.
    """
    _log(progress_cb, "  [Scan] 拉取 Missing Translation Scan task 列表...")
    tasks: List[dict] = []
    try:
        offset = 0
        batch_size = 100
        while True:
            total, batch = _mr.fetch_scan_tasks(
                limit=batch_size, offset=offset)
            tasks.extend(batch)
            if not batch or offset + batch_size >= total:
                break
            offset += batch_size
    except Exception as e:
        _log(progress_cb, f"  ⚠ [Scan] 获取 task 列表失败: {e}")
        return [], set()

    by_project: Dict[str, dict] = {}
    for t in tasks:
        pid = (t.get("project_id") or "").strip()
        if not pid:
            continue
        bucket = by_project.setdefault(
            pid,
            {
                "id": _make_product_id(SRC_SCAN, pid),
                "label": f"[Scan] {pid}",
                "source": SRC_SCAN,
                "project_id": pid,
                "task_count": 0,
                "entry_count": None,  # unknown without touching /results
                "languages": [],
            },
        )
        bucket["task_count"] += 1

    products = list(by_project.values())
    _log(
        progress_cb,
        f"  ✓ [Scan] {len(products)} 个项目",
    )
    return products, set()


def build_light_inventory(
    sources: Iterable[str] = (SRC_LEGACY, SRC_MR, SRC_SCAN),
    progress_cb: ProgressCb = None,
) -> LightInventory:
    """Build the lightweight Product × Language index for the GUI selector.

    This is the **only** function the Full Translations panel calls on init.
    It must NOT touch any /translations endpoint.
    """
    inv = LightInventory()
    sources = {str(s).lower() for s in sources}

    legacy_products: List[dict] = []
    mr_products: List[dict] = []
    scan_products: List[dict] = []
    legacy_locales: Set[str] = set()
    mr_locales: Set[str] = set()
    scan_locales: Set[str] = set()

    if SRC_LEGACY in sources:
        legacy_products, legacy_locales = _build_legacy_light(progress_cb)
    if SRC_MR in sources:
        mr_products, mr_locales = _build_mr_light(progress_cb)
    if SRC_SCAN in sources:
        scan_products, scan_locales = _build_scan_light(progress_cb)

    # Sort: legacy block first (alphabetically), then MR block, then Scan
    legacy_products.sort(key=lambda p: p["project_id"].lower())
    mr_products.sort(key=lambda p: p["project_id"].lower())
    scan_products.sort(key=lambda p: p["project_id"].lower())

    inv.products = legacy_products + mr_products + scan_products
    # Always offer en-US in the selector. Translation rows are stored by
    # target_language, so en-US is rarely returned by /dashboard/filters,
    # but the AP.zip output should still include the source-language copy
    # whenever the user picks it.
    inv.locales = sorted(
        legacy_locales | mr_locales | scan_locales | {SOURCE_LOCALE})

    _log(
        progress_cb,
        f"\n  ✓ 轻量清单完成：{len(inv.products)} 个产品 · {len(inv.locales)} 种语言",
    )
    return inv


# ---- 数据源适配 ----------------------------------------------------------

def _collect_from_legacy(
    inv: FullTranslationInventory,
    progress_cb: ProgressCb,
    project_filter: Optional[Set[str]] = None,
) -> int:
    """从 Legacy File Translation API 聚合。

    project_filter: 若非 None/空，则只抓取 project_name ∈ project_filter 的任务。
    """
    _log(progress_cb, "  [Legacy] 获取 File Translation task 列表...")
    try:
        tasks = _legacy.fetch_tasks()
    except Exception as e:
        _log(progress_cb, f"  ⚠ [Legacy] 获取 task 列表失败: {e}")
        return 0

    if project_filter:
        tasks = [
            t for t in tasks
            if (t.get("project_name") or "(unknown)") in project_filter
        ]
    total = len(tasks)
    _log(progress_cb, f"  [Legacy] 命中 {total} 个 Completed task")

    added = 0
    for idx, task in enumerate(tasks, 1):
        tid = task.get("id")
        tname = task.get("task_name", "")
        if tid is None:
            continue
        try:
            entries = _legacy.fetch_all_translations(tid)
        except Exception as e:
            _log(progress_cb, f"  ⚠ [Legacy {idx}/{total}] task {tid} 失败: {e}")
            continue
        if not entries:
            continue
        n = inv.ingest_entries(
            entries,
            opus_key="opus_id",
            locale_key="target_language",
            value_key="translated_text",
            source_locale=SOURCE_LOCALE,
            source_meta={
                "source": "Legacy",
                "task_id": str(tid),
                "task_name": tname or f"Legacy Task {tid}",
            },
        )
        added += n
        _log(progress_cb, f"  [Legacy {idx}/{total}] '{tname}' +{n}")
    return added


def _collect_from_mr(
    inv: FullTranslationInventory,
    progress_cb: ProgressCb,
    project_filter: Optional[Set[str]] = None,
) -> int:
    """从 MR Pipeline API 聚合。

    project_filter: 若非 None/空，则按 project_id 一次只取该项目下的 completed
    任务，避免拉取整张 completed 任务列表后再做客户端过滤。
    """
    _log(progress_cb, "  [MR] 获取 MR Pipeline task 列表...")
    all_tasks: List[dict] = []

    def _paginate(project_id: Optional[str]) -> List[dict]:
        out: List[dict] = []
        try:
            offset = 0
            batch_size = 100
            while True:
                total, batch = _mr.fetch_mr_tasks(
                    project_id=project_id,
                    status="completed",
                    limit=batch_size,
                    offset=offset,
                )
                out.extend(batch)
                if not batch or offset + batch_size >= total:
                    break
                offset += batch_size
        except Exception as e:
            _log(progress_cb, f"  ⚠ [MR] 获取 task 列表失败 ({project_id}): {e}")
        return out

    if project_filter:
        for pid in sorted(project_filter):
            all_tasks.extend(_paginate(pid))
    else:
        all_tasks.extend(_paginate(None))

    _log(progress_cb, f"  [MR] 命中 {len(all_tasks)} 个 Completed task")

    added = 0
    for idx, task in enumerate(all_tasks, 1):
        tid = task.get("task_id")
        if not tid:
            continue
        try:
            results = _mr.fetch_mr_results(tid)
        except Exception as e:
            _log(progress_cb, f"  ⚠ [MR {idx}/{len(all_tasks)}] task {tid[:8]}… 失败: {e}")
            continue
        trs = (results or {}).get("translations", [])
        if not trs:
            continue
        pid = task.get("project_id", "")
        iid = task.get("merge_request_iid", "")
        rel = task.get("release") or ""
        # MR tasks have no task_name; synthesise something humans can grep:
        #   "MR#42848 Fiji/Fiji" — paste-friendly and points to the GitLab MR.
        mr_label_parts = []
        if iid:
            mr_label_parts.append(f"MR#{iid}")
        if pid:
            mr_label_parts.append(pid)
        mr_label = " ".join(mr_label_parts) or f"MR Task {tid[:8]}"
        n = inv.ingest_entries(
            trs,
            opus_key="opus_id",
            locale_key="target_language",
            value_key="translated_text",
            source_locale=SOURCE_LOCALE,
            source_meta={
                "source": "MR",
                "task_id": str(tid),
                "task_name": mr_label,
                "project_id": pid,
                "merge_request_iid": iid,
                "release": rel,
            },
        )
        added += n
        if idx % 10 == 0 or n > 0:
            _log(progress_cb, f"  [MR {idx}/{len(all_tasks)}] +{n}")
    return added


def _collect_from_scan(
    inv: FullTranslationInventory,
    progress_cb: ProgressCb,
    project_filter: Optional[Set[str]] = None,
) -> int:
    """从 Missing Translation Scan API 聚合已完成扫描任务的全部译文。

    project_filter: 若非 None/空，则只拉取这些 project_id 下的 completed
    scan 任务，避免面板里只勾了 1 个产品却扫全量。
    """
    _log(progress_cb, "  [Scan] 获取 Missing Translation Scan 已完成任务列表...")
    all_tasks: List[dict] = []

    def _paginate(project_id: Optional[str]) -> List[dict]:
        out: List[dict] = []
        try:
            offset = 0
            batch_size = 100
            while True:
                total, batch = _mr.fetch_scan_tasks(
                    project_id=project_id,
                    status="completed",
                    limit=batch_size,
                    offset=offset,
                )
                out.extend(batch)
                if not batch or offset + batch_size >= total:
                    break
                offset += batch_size
        except Exception as e:
            _log(progress_cb, f"  ⚠ [Scan] 获取 task 列表失败 ({project_id}): {e}")
        return out

    if project_filter:
        for pid in sorted(project_filter):
            all_tasks.extend(_paginate(pid))
    else:
        all_tasks.extend(_paginate(None))

    _log(progress_cb, f"  [Scan] 命中 {len(all_tasks)} 个 Completed task")

    added = 0
    for idx, task in enumerate(all_tasks, 1):
        tid = task.get("task_id")
        if not tid:
            continue
        try:
            results = _mr.fetch_scan_results(tid)
        except Exception as e:
            _log(progress_cb, f"  ⚠ [Scan {idx}/{len(all_tasks)}] task {tid[:8]}… 失败: {e}")
            continue
        trs = (results or {}).get("translations", [])
        if not trs:
            continue
        pid = task.get("project_id", "")
        base_ref = task.get("base_ref", "")
        head_ref = task.get("head_ref", "")
        tname = task.get("task_name", "") or f"Scan {tid[:8]}"
        n = inv.ingest_entries(
            trs,
            opus_key="opus_id",
            locale_key="target_language",
            value_key="translated_text",
            source_locale=SOURCE_LOCALE,
            source_meta={
                "source": "Scan",
                "task_id": str(tid),
                "task_name": tname,
                "project_id": pid,
                "base_ref": base_ref,
                "head_ref": head_ref,
            },
        )
        added += n
        if idx % 10 == 0 or n > 0:
            _log(progress_cb, f"  [Scan {idx}/{len(all_tasks)}] '{tname}' +{n}")
    return added


def collect_full_translations(
    sources: Iterable[str] = ("legacy", "mr", "scan"),
    progress_cb: ProgressCb = None,
    legacy_project_filter: Optional[Iterable[str]] = None,
    mr_project_filter: Optional[Iterable[str]] = None,
    scan_project_filter: Optional[Iterable[str]] = None,
) -> FullTranslationInventory:
    """从指定数据源聚合全量翻译。

    sources: {"legacy", "mr", "scan"} 的子集。默认三者都聚合。

    legacy_project_filter / mr_project_filter / scan_project_filter:
        若非 None/空，则在抓取阶段就只命中这些项目下的任务，避免在面板里
        只点了 1 个产品却把整个 completed 任务列表都拉一遍。

    返回 FullTranslationInventory。
    """
    inv = FullTranslationInventory()
    sources = [s.lower() for s in sources]
    legacy_set = set(legacy_project_filter) if legacy_project_filter else None
    mr_set = set(mr_project_filter) if mr_project_filter else None
    scan_set = set(scan_project_filter) if scan_project_filter else None

    if "legacy" in sources:
        added = _collect_from_legacy(inv, progress_cb, legacy_set)
        _log(progress_cb, f"  ✓ [Legacy] 写入 {added} 条")

    if "mr" in sources:
        added = _collect_from_mr(inv, progress_cb, mr_set)
        _log(progress_cb, f"  ✓ [MR] 写入 {added} 条")

    if "scan" in sources:
        added = _collect_from_scan(inv, progress_cb, scan_set)
        _log(progress_cb, f"  ✓ [Scan] 写入 {added} 条")

    _log(progress_cb, f"\n  ✓ 聚合完成：{len(inv.data)} 个产品 / "
                      f"{len(inv.all_locales())} 种语言 / "
                      f"{inv.total_entries()} 条翻译")
    return inv


# ---------------------------------------------------------------------------
# 输出 zip（严格镜像 AP.zip 结构）
# ---------------------------------------------------------------------------

def _source_json_path(product: str, locale: str) -> str:
    return f"{product}/{locale}/trunk/opus_jsons/source.json"


def build_ap_zip(
    inv: FullTranslationInventory,
    out_path: str,
    products: Optional[Iterable[str]] = None,
    locales: Optional[Iterable[str]] = None,
    progress_cb: ProgressCb = None,
) -> dict:
    """将聚合结果写入 AP.zip 风格的 zip 文件。

    products / locales 传 None 或空表示全选。

    返回 summary: {
        "out_path": ..., "products": N, "locales": N,
        "files": N, "entries": N
    }
    """
    product_filter = set(products) if products else None
    locale_filter = set(locales) if locales else None

    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    summary = {
        "out_path": out_path,
        "products": 0,
        "locales": 0,
        "files": 0,
        "entries": 0,
        # Stats for the GUI result dialog. To stay consistent with the
        # merged-JSON view (and what the user actually cares about), the
        # per_product breakdown reports en-US source key counts — i.e.
        # how many distinct opus_ids each product contributes — rather
        # than the sum across selected target languages.
        "per_product": {},   # product -> en-US source key count
        "per_locale": {},    # locale  -> total entries written for this locale
    }
    per_locale: Counter = Counter()
    exported_products: Set[str] = set()

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        with inv._lock:  # noqa: SLF001  — 内部快照，避免写 zip 期间数据变动
            data_snapshot = {
                p: {l: dict(m) for l, m in locs.items()}
                for p, locs in inv.data.items()
            }

        for product in sorted(data_snapshot.keys()):
            if product_filter is not None and product not in product_filter:
                continue
            for locale in sorted(data_snapshot[product].keys()):
                if locale_filter is not None and locale not in locale_filter:
                    continue
                loc_map = data_snapshot[product][locale]
                if not loc_map:
                    continue

                entries = [
                    {
                        "opusID": opus_id,
                        "pseudoHash": "",
                        "stringValue": value,
                    }
                    for opus_id, value in loc_map.items()
                ]
                payload = json.dumps(entries, ensure_ascii=False, indent=2)
                zf.writestr(_source_json_path(product, locale), payload)

                summary["files"] += 1
                summary["entries"] += len(entries)
                per_locale[locale] += len(entries)
                exported_products.add(product)
                _log(progress_cb, f"  + {product}/{locale}: {len(entries)}")

    # per_product = number of distinct opus_ids per product whose en-US
    # source value exists in the inventory snapshot. Computed from the
    # inventory directly (not from what was written) so the column stays
    # meaningful even when the user excludes en-US from the locale filter.
    per_product: Dict[str, int] = {}
    for product in exported_products:
        src_map = (data_snapshot.get(product) or {}).get(SOURCE_LOCALE) or {}
        per_product[product] = sum(
            1 for v in src_map.values() if v not in (None, ""))

    summary["products"] = len(exported_products)
    summary["locales"] = len(per_locale)
    summary["per_product"] = per_product
    summary["per_locale"] = dict(per_locale)
    _log(progress_cb, f"\n  ✓ 写入 {out_path}")
    _log(progress_cb, f"    产品={summary['products']}, 语言={summary['locales']}, "
                      f"文件={summary['files']}, 条目={summary['entries']}")
    return summary


# ---------------------------------------------------------------------------
# 输出 merged JSON（按 opus_id 对齐多语言列）
# ---------------------------------------------------------------------------

def build_merged_json(
    inv: FullTranslationInventory,
    out_path: str,
    products: Optional[Iterable[str]] = None,
    locales: Optional[Iterable[str]] = None,
    progress_cb: ProgressCb = None,
) -> dict:
    """把聚合结果写成单个扁平 JSON 文件，供后续质量检查 / 全局搜索使用。

    输出结构对齐 ``merged_translations_example.json``：

        [
            {
                "key": "<opus_id>",
                "en-US": "<source text>",
                "de-DE": "<translation>",
                ...
            },
            ...
        ]

    每条 record 的 ``key`` 后是该 opus_id 在每个被选中的语言下的字符串。
    en-US（源语言）若存在固定排在最前，其余语言按字母序排列。某条记录
    在某语言下没有翻译时，对应字段会被省略而不是写入空字符串。

    products / locales 传 None 或空表示不再做客户端过滤（建议在 fetch
    阶段就按 project_id 预过滤好；这里的 locales 主要用来裁剪用户当前
    勾选的目标语言）。
    """
    product_filter = set(products) if products else None
    locale_filter = set(locales) if locales else None

    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    # opus_id -> {locale: value}
    merged: Dict[str, Dict[str, str]] = {}
    # opus_id -> {locale: source_meta}
    merged_sources: Dict[str, Dict[str, dict]] = {}
    # Snapshot of en-US source key counts per product, taken under the
    # same lock so per_product stays consistent with build_ap_zip.
    src_count_by_product: Dict[str, int] = {}
    with inv._lock:  # noqa: SLF001 — snapshot under lock for consistency
        for product, loc_map in inv.data.items():
            if product_filter is not None and product not in product_filter:
                continue
            src_map = loc_map.get(SOURCE_LOCALE) or {}
            n = sum(1 for v in src_map.values() if v not in (None, ""))
            if n > 0:
                src_count_by_product[product] = n
            prov_prod = inv.sources.get(product, {})
            for locale, kv in loc_map.items():
                if locale_filter is not None and locale not in locale_filter:
                    continue
                prov_loc = prov_prod.get(locale, {})
                for opus_id, value in kv.items():
                    if value in (None, ""):
                        continue
                    merged.setdefault(opus_id, {})[locale] = value
                    meta = prov_loc.get(opus_id)
                    if meta:
                        merged_sources.setdefault(opus_id, {})[locale] = meta

    def _ordered_locales(locs: Iterable[str]) -> List[str]:
        # Source language first, then alphabetic — matches the example file.
        rest = sorted(loc for loc in locs if loc != SOURCE_LOCALE)
        return ([SOURCE_LOCALE] if SOURCE_LOCALE in locs else []) + rest

    def _collapse_sources(src_by_loc: Dict[str, dict]) -> dict:
        """Collapse per-locale source metadata into a compact section.

        When every locale for a key was produced by the same task we emit a
        single ``_source`` object — the common case, and keeps the JSON
        slim. Otherwise we fall back to ``_sources`` keyed by locale so
        nothing is lost for bug fixing.
        """
        if not src_by_loc:
            return {}
        unique = {}
        for meta in src_by_loc.values():
            key = (meta.get("source"), meta.get("task_id"))
            unique[key] = meta
        if len(unique) == 1:
            return {"_source": next(iter(unique.values()))}
        return {"_sources": dict(src_by_loc)}

    records: List[dict] = []
    per_locale: Counter = Counter()
    exported_products: Set[str] = set()
    for opus_id in sorted(merged.keys()):
        loc_values = merged[opus_id]
        rec: Dict[str, object] = {"key": opus_id}
        for loc in _ordered_locales(loc_values.keys()):
            rec[loc] = loc_values[loc]
        # Append provenance section at the END of each record so the
        # existing human-readable layout (key + en-US + de-DE + ...) is
        # preserved verbatim.
        rec.update(_collapse_sources(merged_sources.get(opus_id, {})))
        records.append(rec)
        exported_products.add(parse_product(opus_id))
        for loc in loc_values.keys():
            per_locale[loc] += 1

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # per_product = en-US source key count per product, restricted to
    # products that contributed at least one record to the export. Mirrors
    # build_ap_zip so all three export actions report the same number.
    per_product: Dict[str, int] = {
        p: src_count_by_product.get(p, 0) for p in exported_products
    }

    summary = {
        "out_path": out_path,
        "records": len(records),
        "locales": len(per_locale),
        "products": len(exported_products),
        # entries == records here so the dialog can show a single number; the
        # per-axis breakdowns mirror build_ap_zip's shape so the dialog can
        # render both modes the same way.
        "entries": len(records),
        "per_product": per_product,
        "per_locale": dict(per_locale),
    }
    _log(progress_cb, f"\n  ✓ 写入合并 JSON: {out_path}")
    _log(
        progress_cb,
        f"    records={summary['records']}, locales={summary['locales']}, "
        f"products={summary['products']}",
    )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_out_path() -> str:
    return f"FullTranslations_{date.today().strftime('%Y%m%d')}.zip"


def _parse_csv(s: Optional[str]) -> Optional[List[str]]:
    if not s:
        return None
    return [x.strip() for x in s.split(",") if x.strip()]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export full translation inventory (AP.zip-style) "
                    "from Tranzor APIs.")
    parser.add_argument("--out", default=_default_out_path(),
                        help="输出 zip 路径（默认 FullTranslations_YYYYMMDD.zip）")
    parser.add_argument("--products", default=None,
                        help="逗号分隔的产品白名单（默认全部）")
    parser.add_argument("--locales", default=None,
                        help="逗号分隔的语言白名单（默认全部）")
    parser.add_argument("--sources", default="legacy,mr,scan",
                        help="数据源，逗号分隔，可选 legacy,mr,scan（默认三者都要）")
    args = parser.parse_args(argv)

    sources = _parse_csv(args.sources) or ["legacy", "mr", "scan"]
    inv = collect_full_translations(sources=sources, progress_cb=print)

    if not inv.data:
        print("⚠ 未聚合到任何数据，跳过写 zip")
        return 1

    build_ap_zip(
        inv,
        out_path=args.out,
        products=_parse_csv(args.products),
        locales=_parse_csv(args.locales),
        progress_cb=print,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
