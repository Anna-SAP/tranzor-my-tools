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
        self._lock = threading.Lock()

    # ---- 写入 --------------------------------------------------------
    def ingest(self, opus_id: str, locale: str, value: str) -> None:
        if not opus_id or not locale or value is None:
            return
        product = parse_product(opus_id)
        with self._lock:
            prod_map = self.data.setdefault(product, {})
            loc_map = prod_map.setdefault(locale, {})
            # 后写覆盖前写，用于"取最新一条"去重策略
            loc_map[opus_id] = value

    def ingest_entries(
        self,
        entries: Iterable[dict],
        *,
        opus_key: str = "opus_id",
        locale_key: str = "target_language",
        value_key: str = "translated_text",
        source_locale: Optional[str] = None,
        source_value_key: str = "source_text",
    ) -> int:
        """批量写入。返回实际写入的目标语条目数（未计入空值或源语言副本）。

        当 source_locale 非空时，每条 entry 还会把 ``source_text`` 写一份到
        ``data[product][source_locale]``，让 AP.zip 输出可以包含 en-US 源语
        言目录（target_language 维度天然不包含 en-US）。
        """
        n = 0
        for entry in entries:
            oid = entry.get(opus_key) or ""
            loc = entry.get(locale_key) or ""
            val = entry.get(value_key)
            if oid and loc and val not in (None, ""):
                self.ingest(oid, loc, val)
                n += 1
            if source_locale and oid:
                src = entry.get(source_value_key)
                if src not in (None, ""):
                    self.ingest(oid, source_locale, src)
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
    ) -> Tuple[Set[str], Set[str]]:
        """Split a selection of product IDs into (legacy_projects, mr_projects).

        Each item in selected_ids matches LightInventory.products[*]["id"]
        which is encoded as ``<source>::<project_id>``.
        """
        legacy: Set[str] = set()
        mr: Set[str] = set()
        for sid in selected_ids:
            p = self.find(sid)
            if not p:
                continue
            if p["source"] == SRC_LEGACY:
                legacy.add(p["project_id"])
            elif p["source"] == SRC_MR:
                mr.add(p["project_id"])
        return legacy, mr


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


def build_light_inventory(
    sources: Iterable[str] = (SRC_LEGACY, SRC_MR),
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
    legacy_locales: Set[str] = set()
    mr_locales: Set[str] = set()

    if SRC_LEGACY in sources:
        legacy_products, legacy_locales = _build_legacy_light(progress_cb)
    if SRC_MR in sources:
        mr_products, mr_locales = _build_mr_light(progress_cb)

    # Sort: legacy block first (alphabetically), then MR block
    legacy_products.sort(key=lambda p: p["project_id"].lower())
    mr_products.sort(key=lambda p: p["project_id"].lower())

    inv.products = legacy_products + mr_products
    # Always offer en-US in the selector. Translation rows are stored by
    # target_language, so en-US is rarely returned by /dashboard/filters,
    # but the AP.zip output should still include the source-language copy
    # whenever the user picks it.
    inv.locales = sorted(legacy_locales | mr_locales | {SOURCE_LOCALE})

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
        n = inv.ingest_entries(
            trs,
            opus_key="opus_id",
            locale_key="target_language",
            value_key="translated_text",
            source_locale=SOURCE_LOCALE,
        )
        added += n
        if idx % 10 == 0 or n > 0:
            _log(progress_cb, f"  [MR {idx}/{len(all_tasks)}] +{n}")
    return added


def collect_full_translations(
    sources: Iterable[str] = ("legacy", "mr"),
    progress_cb: ProgressCb = None,
    legacy_project_filter: Optional[Iterable[str]] = None,
    mr_project_filter: Optional[Iterable[str]] = None,
) -> FullTranslationInventory:
    """从指定数据源聚合全量翻译。

    sources: {"legacy", "mr"} 的子集。默认两者都聚合。

    legacy_project_filter / mr_project_filter:
        若非 None/空，则在抓取阶段就只命中这些项目下的任务，避免在面板里
        只点了 1 个产品却把整个 completed 任务列表都拉一遍。

    返回 FullTranslationInventory。
    """
    inv = FullTranslationInventory()
    sources = [s.lower() for s in sources]
    legacy_set = set(legacy_project_filter) if legacy_project_filter else None
    mr_set = set(mr_project_filter) if mr_project_filter else None

    if "legacy" in sources:
        added = _collect_from_legacy(inv, progress_cb, legacy_set)
        _log(progress_cb, f"  ✓ [Legacy] 写入 {added} 条")

    if "mr" in sources:
        added = _collect_from_mr(inv, progress_cb, mr_set)
        _log(progress_cb, f"  ✓ [MR] 写入 {added} 条")

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
        # Stats for the GUI result dialog: total entries grouped by axis.
        "per_product": {},   # product -> total entries (sum across selected locales)
        "per_locale": {},    # locale -> total entries (sum across selected products)
    }
    per_product: Counter = Counter()
    per_locale: Counter = Counter()

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
                per_product[product] += len(entries)
                per_locale[locale] += len(entries)
                _log(progress_cb, f"  + {product}/{locale}: {len(entries)}")

    summary["products"] = len(per_product)
    summary["locales"] = len(per_locale)
    summary["per_product"] = dict(per_product)
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
    with inv._lock:  # noqa: SLF001 — snapshot under lock for consistency
        for product, loc_map in inv.data.items():
            if product_filter is not None and product not in product_filter:
                continue
            for locale, kv in loc_map.items():
                if locale_filter is not None and locale not in locale_filter:
                    continue
                for opus_id, value in kv.items():
                    if value in (None, ""):
                        continue
                    merged.setdefault(opus_id, {})[locale] = value

    def _ordered_locales(locs: Iterable[str]) -> List[str]:
        # Source language first, then alphabetic — matches the example file.
        rest = sorted(loc for loc in locs if loc != SOURCE_LOCALE)
        return ([SOURCE_LOCALE] if SOURCE_LOCALE in locs else []) + rest

    records: List[dict] = []
    per_product: Counter = Counter()
    per_locale: Counter = Counter()
    for opus_id in sorted(merged.keys()):
        loc_values = merged[opus_id]
        rec: Dict[str, str] = {"key": opus_id}
        for loc in _ordered_locales(loc_values.keys()):
            rec[loc] = loc_values[loc]
        records.append(rec)
        # Count one record per product, and one per locale present in record.
        per_product[parse_product(opus_id)] += 1
        for loc in loc_values.keys():
            per_locale[loc] += 1

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    summary = {
        "out_path": out_path,
        "records": len(records),
        "locales": len(per_locale),
        "products": len(per_product),
        # entries == records here so the dialog can show a single number; the
        # per-axis breakdowns mirror build_ap_zip's shape so the dialog can
        # render both modes the same way.
        "entries": len(records),
        "per_product": dict(per_product),
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
    parser.add_argument("--sources", default="legacy,mr",
                        help="数据源，逗号分隔，可选 legacy,mr（默认两者都要）")
    args = parser.parse_args(argv)

    sources = _parse_csv(args.sources) or ["legacy", "mr"]
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
