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
from datetime import date
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# 将本目录加入 sys.path，确保无论从哪里启动都能 import 同级模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_translations as _legacy       # noqa: E402  — 只读 import
import export_mr_pipeline as _mr            # noqa: E402  — 只读 import


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
    ) -> int:
        """批量写入。返回实际写入的条目数（未计入空值）。"""
        n = 0
        for entry in entries:
            oid = entry.get(opus_key) or ""
            loc = entry.get(locale_key) or ""
            val = entry.get(value_key)
            if not oid or not loc or val in (None, ""):
                continue
            self.ingest(oid, loc, val)
            n += 1
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


# ---- 数据源适配 ----------------------------------------------------------

def _collect_from_legacy(inv: FullTranslationInventory, progress_cb: ProgressCb) -> int:
    """从 Legacy File Translation API 聚合。"""
    _log(progress_cb, "  [Legacy] 获取 File Translation task 列表...")
    try:
        tasks = _legacy.fetch_tasks()
    except Exception as e:
        _log(progress_cb, f"  ⚠ [Legacy] 获取 task 列表失败: {e}")
        return 0
    total = len(tasks)
    _log(progress_cb, f"  [Legacy] 共 {total} 个 Completed task")

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
        )
        added += n
        _log(progress_cb, f"  [Legacy {idx}/{total}] '{tname}' +{n}")
    return added


def _collect_from_mr(inv: FullTranslationInventory, progress_cb: ProgressCb) -> int:
    """从 MR Pipeline API 聚合。"""
    _log(progress_cb, "  [MR] 获取 MR Pipeline task 列表...")
    all_tasks: List[dict] = []
    try:
        offset = 0
        batch_size = 100
        while True:
            total, batch = _mr.fetch_mr_tasks(
                status="completed", limit=batch_size, offset=offset)
            all_tasks.extend(batch)
            if not batch or offset + batch_size >= total:
                break
            offset += batch_size
    except Exception as e:
        _log(progress_cb, f"  ⚠ [MR] 获取 task 列表失败: {e}")
        return 0
    _log(progress_cb, f"  [MR] 共 {len(all_tasks)} 个 Completed task")

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
        )
        added += n
        if idx % 10 == 0 or n > 0:
            _log(progress_cb, f"  [MR {idx}/{len(all_tasks)}] +{n}")
    return added


def collect_full_translations(
    sources: Iterable[str] = ("legacy", "mr"),
    progress_cb: ProgressCb = None,
) -> FullTranslationInventory:
    """从指定数据源聚合全量翻译。

    sources: {"legacy", "mr"} 的子集。默认两者都聚合。
    返回 FullTranslationInventory。
    """
    inv = FullTranslationInventory()
    sources = [s.lower() for s in sources]

    if "legacy" in sources:
        added = _collect_from_legacy(inv, progress_cb)
        _log(progress_cb, f"  ✓ [Legacy] 写入 {added} 条")

    if "mr" in sources:
        added = _collect_from_mr(inv, progress_cb)
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
    }
    touched_products: set = set()
    touched_locales: set = set()

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
                touched_products.add(product)
                touched_locales.add(locale)
                _log(progress_cb, f"  + {product}/{locale}: {len(entries)}")

    summary["products"] = len(touched_products)
    summary["locales"] = len(touched_locales)
    _log(progress_cb, f"\n  ✓ 写入 {out_path}")
    _log(progress_cb, f"    产品={summary['products']}, 语言={summary['locales']}, "
                      f"文件={summary['files']}, 条目={summary['entries']}")
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
