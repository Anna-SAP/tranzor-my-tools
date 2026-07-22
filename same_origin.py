"""
Same Origin —— 同源（同一 MR 多次触发）翻译一致性分析的纯逻辑层
================================================================

Tranzor「MR Pipeline」翻译通道存在一类一致性隐患：**同一个 Merge Request
在不同时间被触发了多次翻译任务**（例如一次微小的源文修改重跑了一遍管线），
极易产生跨次结果不一致 —— 哪怕源文只删了一行，18 个语种的译文也可能被
大面积重写并引入偏差（详见会话记录 20260618 的 MR40461 分析）。

Tranzor 源头平台目前没有这类预警 / 监控，所以在 Exporter 内部自建一个
「Same Origin」面板来捕获、记录、分析这种「同一 MR 多次触发」的情况。

本模块只放 **纯逻辑**（不依赖 Tkinter），便于单测：

- Core products 配置：:func:`load_core_products` / :func:`save_core_products`
  —— 默认内置 26 个核心产品 (project_id)，用户可在面板内增删，落到用户家
  目录 ``~/.tranzor_exporter/core_products.json``（沿用 opus_id_monitor /
  gitlab_client 的用户配置目录约定）。
- 聚合：:func:`group_same_origin` —— 把任务按 (project_id, merge_request_iid)
  分组，只保留属于 Core products 且 **同 MR 出现 ≥2 次** 的组（即"同源多次"）。
- 扫描：:func:`scan_same_origin_groups` —— 分页 + 并发拉取 MR 任务列表后聚合。
- 差异：:func:`compute_mr_divergences` —— 拉取同组各任务的最新译文，按
  (opus_id, target_language) 交叉比对，找出跨任务版本不一致的串。
- 高亮分段：:func:`diff_runs` —— 复用 export_mr_pipeline 的 token 切分 +
  difflib，产出 (equal/delete/insert) 分段，供 Tk Text 上色。

纯加法：不修改任何现有模块；GUI 层 (:mod:`gui_tab_same_origin`) 引用本模块。
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Optional

import atomic_io


# ---------------------------------------------------------------------------
# Core products —— 默认核心产品清单（project_id，与 MR 任务的 project_id /
# 产品选择器里 "[MR] xxx" 后半段一致）。用户可在面板内覆盖。
# ---------------------------------------------------------------------------
DEFAULT_CORE_PRODUCTS: list[str] = [
    "admin-web/backend",
    "admin-web/frontend",
    "common/awp",
    "common/clw",
    "common/maa",
    "common/uns",
    "copilot-platform/business-components/copilot-web-widgets",
    "copilot-platform/business-components/rex-ai/copilot-chat-web",
    "copilot-platform/nova/agentic-integration-frontend",
    "copilot-platform/nova/nova-studio-app",
    "es/express-setup-nova-next-generation",
    "es/express-setup-renaissance",
    "iva/agent-service",
    "iva/assistant-runtime",
    "iva/iva-ui",
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
]


def _config_dir() -> str:
    """``~/.tranzor_exporter``（与 opus_index.db 同目录），不存在则创建。"""
    base = os.path.join(os.path.expanduser("~"), ".tranzor_exporter")
    os.makedirs(base, exist_ok=True)
    return base


def config_path() -> str:
    return os.path.join(_config_dir(), "core_products.json")


def _normalize_products(items: Iterable[str]) -> list[str]:
    """去空白、丢空串、去重（保序）。project_id 大小写敏感，原样保留。"""
    seen: set[str] = set()
    out: list[str] = []
    for raw in items or []:
        if raw is None:
            continue
        p = str(raw).strip()
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def load_core_products() -> list[str]:
    """读用户覆盖的 Core products；无文件 / 损坏 / 为空时回落到默认 26 项。

    永不抛异常 —— 配置坏了也要让面板能起来（回落默认），这比报错更可用。
    """
    path = config_path()
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("core_products") if isinstance(data, dict) else data
            norm = _normalize_products(items or [])
            if norm:
                return norm
    except Exception:
        pass
    return list(DEFAULT_CORE_PRODUCTS)


def save_core_products(items: Iterable[str]) -> list[str]:
    """把归一化后的清单写入用户配置文件，返回实际写入的清单。

    空清单不写（避免把面板锁死成「无可用产品」）—— 调用方应在 UI 层拦截。
    """
    norm = _normalize_products(items)
    if not norm:
        raise ValueError("core_products 不能为空")
    payload = {
        "_meta": {
            "purpose": "Same Origin 面板的核心产品 (project_id) 清单；"
                       "面板内可增删，删除本文件即回落到内置默认 26 项。",
        },
        "core_products": norm,
    }
    atomic_io.atomic_write_json(
        config_path(), payload, ensure_ascii=False, indent=2)
    return norm


def is_default_core_products(items: Iterable[str]) -> bool:
    """当前清单是否与内置默认一致（顺序无关）—— 供 UI 决定是否高亮"已自定义"。"""
    return set(_normalize_products(items)) == set(DEFAULT_CORE_PRODUCTS)


# ---------------------------------------------------------------------------
# 聚合：同一 (project_id, MR#) 的多次任务
# ---------------------------------------------------------------------------
def _task_created(task: dict) -> str:
    """ISO 时间串，缺失时退化为空串（排在最前）。"""
    return (task.get("created_at") or "")


def _task_sort_key(task: dict):
    """确定性排序键：(created_at, task_id)。

    单凭 created_at 排序时，两次运行若 created_at 完全相同（罕见但可能），
    stable sort 会保留输入顺序——而扫描时的输入顺序来自并发 as_completed，
    不确定。加 task_id 作为二级键，让版本链顺序在多次运行间可复现。
    """
    return (_task_created(task), str(task.get("task_id") or ""))


def _mr_bucket_key(pid, mr_iid):
    """聚合用的归一化分组键。

    JSON 跨页/跨端点可能把 merge_request_iid 给成 int 或 str（如 40461 vs
    "40461"）；不归一化会把同一个 MR 拆成两个 size-1 桶、双双过不了 ≥2 闸门，
    恰好漏掉本功能要抓的「同源对」。用 str 归一化分组键；展示用的原始
    typed 值仍从任务里取。
    """
    return (str(pid).strip(), str(mr_iid).strip())


def group_same_origin(tasks: Iterable[dict], core_products: Iterable[str],
                      *, min_count: int = 2) -> list[dict]:
    """把任务按 (project_id, merge_request_iid) 聚合，只保留「同源多次」组。

    Args:
        tasks: MR 任务 dict 列表（来自 ``fetch_mr_tasks``），需含 ``project_id``
            / ``merge_request_iid`` / ``created_at`` 等字段。
        core_products: 允许的 project_id 集合（Core products）。空 → 不限。
        min_count: 一个 (project, MR#) 组至少出现几次才算「同源多次」。默认 2。

    Returns:
        list[dict]，每组：
            {
              "project_id", "mr_iid", "release",
              "task_count", "latest_created", "earliest_created",
              "tasks": [<task dict>...]  # 按 created_at 升序
            }
        按 ``latest_created`` 降序排列（最近活动的组在最上）。
    """
    core_set = set(core_products or [])
    buckets: dict[tuple[str, str], list[dict]] = {}
    # 每个桶内已见过的 task_id —— 同一 task_id 只计一次。并发 offset 分页在
    # 列表实时变动时可能让同一任务落到两个重叠窗口里（线程 A 读 offset 0、
    # 线程 B 读 offset 100，期间插入了新任务 → 错位重叠），不去重就会把单次
    # 运行算成 2 次、伪造出「同源」组并自己跟自己比。
    seen_tids: dict[tuple[str, str], set] = {}

    for t in tasks or []:
        pid = t.get("project_id")
        mr_iid = t.get("merge_request_iid")
        if not pid or mr_iid is None or mr_iid == "":
            continue
        if core_set and pid not in core_set:
            continue
        key = _mr_bucket_key(pid, mr_iid)
        tid = t.get("task_id")
        if tid:
            seen = seen_tids.setdefault(key, set())
            if tid in seen:
                continue  # 同一任务的重复副本，跳过
            seen.add(tid)
        buckets.setdefault(key, []).append(t)

    groups: list[dict] = []
    for key, items in buckets.items():
        if len(items) < min_count:
            continue
        items_sorted = sorted(items, key=_task_sort_key)
        # project_id / mr_iid / release 等展示字段取最新一条的**原始 typed 值**
        # （分组键虽归一化成 str，展示仍用真实值）。
        latest = items_sorted[-1]
        groups.append({
            "project_id": latest.get("project_id"),
            "mr_iid": latest.get("merge_request_iid"),
            "release": latest.get("release", ""),
            "task_count": len(items_sorted),
            "earliest_created": _task_created(items_sorted[0]),
            "latest_created": _task_created(latest),
            "tasks": items_sorted,
        })

    groups.sort(key=lambda g: g["latest_created"], reverse=True)
    return groups


def scan_same_origin_groups(core_products: Iterable[str], *,
                            status: Optional[str] = "completed",
                            progress: Optional[Callable[[str], None]] = None,
                            page_size: int = 100,
                            max_pages: Optional[int] = None,
                            max_workers: int = 8,
                            fetch_tasks: Optional[Callable] = None,
                            cancel_event: Optional[threading.Event] = None,
                            ) -> dict:
    """分页 + 并发拉取 MR 任务列表，聚合出「同源多次」组。

    只拉**任务列表**（``/tasks``，每页 100 条、无 per-task 结果），所以即便
    全量扫描也只是 N/100 次轻量请求，不会触发昂贵的 per-task results 拉取。

    Args:
        core_products: Core products 清单（project_id）。
        status: 任务状态过滤；默认 ``"completed"``（只有 completed 才有可比
            的译文结果）。``None`` → 不限状态。
        progress: 进度回调 (msg)。
        page_size / max_pages / max_workers: 分页 / 安全上限 / 并发度。
        fetch_tasks: 注入点（测试用），签名同
            ``export_mr_pipeline.fetch_mr_tasks(status, limit, offset)`` →
            ``(total, tasks)``。默认懒加载真实实现。
        cancel_event: 置位后尽快停止后续分页。

    Returns:
        { "groups": [...], "scanned": int, "total": int, "truncated": bool }
    """
    log = progress or (lambda *_: None)
    if cancel_event is None:
        cancel_event = threading.Event()

    if fetch_tasks is None:
        def fetch_tasks(status=None, limit=100, offset=0):  # type: ignore
            import export_mr_pipeline as _mp
            return _mp.fetch_mr_tasks(status=status, limit=limit, offset=offset)

    log("正在拉取 MR 任务列表（第 1 页）…")
    total, first = fetch_tasks(status=status, limit=page_size, offset=0)
    tasks: list[dict] = list(first or [])
    total = int(total or 0)
    truncated = False
    pages_read = 1  # 已读页数（含第 1 页），用于 max_pages 限额

    def _fetch_page(off):
        if cancel_event.is_set():
            return []
        try:
            _t, batch = fetch_tasks(status=status, limit=page_size, offset=off)
            return list(batch or [])
        except Exception as e:  # noqa: BLE001
            log(f"⚠ 第 {off // page_size + 1} 页拉取失败: {e}")
            return []

    # Phase A —— 并发铺开「total 已知且可信」的中间页。total 只作为并发提速的
    # 提示，**不**作为停止判据：若它过小 / 为 0，Phase B 会兜底补齐。
    last_offset_read = 0
    if len(first) >= page_size:  # 第 1 页满 → 很可能还有
        planned_pages = ((total + page_size - 1) // page_size
                         if total and total > len(first) else 0)
        if max_pages is not None:
            planned_pages = min(planned_pages, max_pages)
        offsets = [p * page_size for p in range(1, planned_pages)]
        if offsets and not cancel_event.is_set():
            log(f"共约 {total} 个任务，正在并发拉取 {len(offsets)} 页…")
            done = 0
            with ThreadPoolExecutor(
                    max_workers=max(1, min(max_workers, len(offsets)))) as pool:
                futures = {pool.submit(_fetch_page, off): off for off in offsets}
                for fut in as_completed(futures):
                    tasks.extend(fut.result())
                    done += 1
                    if done % 5 == 0 or done == len(offsets):
                        log(f"  已拉取 {done + 1} 页（{len(tasks)} 个任务）")
            pages_read += len(offsets)
            last_offset_read = max(offsets)

    # Phase B —— 顺序续读，直到某页返回不足 page_size（真正到底）。这是
    # 完整性保证的关键：当后端 total 漏报 / 为 0 / 期间有新任务插入时，
    # Phase A 会少铺页，这里把尾巴补齐；total 准确时这里只多发 1 个请求即停。
    last_full = (len(first) >= page_size)  # 上一已读页是否满
    next_offset = last_offset_read + page_size
    while last_full and not cancel_event.is_set():
        if max_pages is not None and pages_read >= max_pages:
            # 到达页数上限仍未确认到底 → 可能还有，标记截断让 UI 提示
            truncated = True
            break
        batch = _fetch_page(next_offset)
        pages_read += 1
        tasks.extend(batch)
        last_full = len(batch) >= page_size
        if not last_full:
            break  # 到底
        next_offset += page_size
        if pages_read % 5 == 0:
            log(f"  续读至 {len(tasks)} 个任务…")

    groups = group_same_origin(tasks, core_products)
    log(f"✓ 扫描完成：{len(tasks)} 个任务中聚合出 {len(groups)} 个「同源多次」MR 组")
    return {
        "groups": groups,
        "scanned": len(tasks),
        "total": total,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# 差异：同组各任务的最新译文交叉比对
# ---------------------------------------------------------------------------
_MISSING = object()  # 哨兵：某 (opus_id, locale) 在该任务里根本不存在


def compute_mr_divergences(group_tasks: Iterable[dict], *,
                           fetch_results: Optional[Callable] = None,
                           progress: Optional[Callable[[str], None]] = None,
                           max_workers: int = 4,
                           ) -> dict:
    """拉取同组各任务的最新译文，按 (opus_id, target_language) 交叉比对。

    每个任务的 ``fetch_mr_results`` 返回的 ``translated_text`` 即该任务那次运行
    的最终译文。把同一 (opus_id, locale) 在各任务里的译文排成版本链，只要存在
    ≥2 个不同取值（缺失算一种取值），即判定为「跨任务不一致」。

    Args:
        group_tasks: 同一 MR 的任务 dict 列表（需 ``task_id`` / ``created_at``）。
        fetch_results: 注入点（测试用），签名 ``(task_id) -> {translations:[...]}``。
            默认懒加载 ``export_mr_pipeline.fetch_mr_results``。
        progress: 进度回调。
        max_workers: 并发拉取任务结果的线程数。

    Returns:
        {
          "tasks": [ {task_id, created_at, label} ... ],   # 实际参与比对的任务（按 created 升序）
          "by_locale": { locale: [ divergence ... ] },     # 仅含不一致的串
          "locales": [locale ...],                          # 有不一致的语种（有序）
          "total_divergent": int,                           # 不一致串总数（按 opus_id×locale）
          "total_keys": int,                                # 比对过的 (opus_id×locale) 总数
          "task_count": int,                                # 参与比对的任务数
          "group_task_count": int,                          # 该组任务总数（含拉取失败的）
          "failed_count": int,                              # 译文拉取失败、被排除比对的任务数
          "insufficient": bool,                             # 成功拉取的任务 <2，无法比对
        }
      其中每条 divergence：
        {
          "opus_id", "target_language", "source_text",
          "versions": [ {task_id, created_at, text, present(bool)} ... ],  # 按任务顺序
          "distinct": int,            # 去重后的不同取值数（含缺失）
          "changed_kind": "text" | "added_removed",
        }
    """
    log = progress or (lambda *_: None)
    all_tasks = sorted(
        [t for t in (group_tasks or []) if t.get("task_id")],
        key=_task_created,
    )
    if fetch_results is None:
        def fetch_results(task_id):  # type: ignore
            import export_mr_pipeline as _mp
            return _mp.fetch_mr_results(task_id)

    # Step 1: 并发拉取每个任务的译文。区分「拉取失败」与「拉到 0 条」——
    # 失败的任务必须被**排除**比对，否则它会让其它任务的所有 key 看起来
    # 「在该任务里缺失」→ 误报成 added/removed 差异。0 条是合法数据，保留。
    log(f"正在拉取 {len(all_tasks)} 个任务的译文…")
    results_by_tid: dict[str, list[dict]] = {}
    failed_tids: set[str] = set()

    def _fetch_one(task):
        tid = task.get("task_id")
        try:
            data = fetch_results(tid) or {}
            return tid, list(data.get("translations") or []), True
        except Exception as e:  # noqa: BLE001
            log(f"⚠ 任务 {str(tid)[:8]}… 译文拉取失败（已排除比对）: {e}")
            return tid, [], False

    if all_tasks:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(all_tasks)))) as pool:
            futures = [pool.submit(_fetch_one, t) for t in all_tasks]
            for fut in as_completed(futures):
                tid, trs, ok = fut.result()
                if ok:
                    results_by_tid[tid] = trs
                    log(f"  任务 {str(tid)[:8]}… — {len(trs)} 条译文")
                else:
                    failed_tids.add(tid)

    # 只在成功拉取的任务之间比对
    tasks_sorted = [t for t in all_tasks if t.get("task_id") not in failed_tids]

    # 成功拉取的任务不足 2 个 → 无法比对，直接返回（带失败计数让 UI 提示）
    if len(tasks_sorted) < 2:
        return {
            "tasks": [{
                "task_id": t.get("task_id"),
                "created_at": _task_created(t),
                "label": (t.get("created_at") or "")[:19].replace("T", " "),
            } for t in tasks_sorted],
            "by_locale": {}, "locales": [],
            "total_divergent": 0, "total_keys": 0,
            "task_count": len(tasks_sorted),
            "group_task_count": len(all_tasks),
            "failed_count": len(failed_tids),
            "insufficient": True,
        }

    # Step 2: 建索引 (opus_id, locale) -> { task_id: {text, source} }
    #         同时记录每个 key 的 source_text（取任一非空）。
    per_key: dict[tuple[str, str], dict[str, dict]] = {}
    source_by_key: dict[tuple[str, str], str] = {}
    for task in tasks_sorted:
        tid = task.get("task_id")
        for tr in results_by_tid.get(tid, []):
            opus_id = tr.get("opus_id") or ""
            locale = tr.get("target_language") or ""
            if not opus_id or not locale:
                continue
            key = (opus_id, locale)
            per_key.setdefault(key, {})[tid] = {
                "text": tr.get("translated_text") or "",
                "source": tr.get("source_text") or "",
            }
            if key not in source_by_key and (tr.get("source_text") or ""):
                source_by_key[key] = tr.get("source_text") or ""

    # Step 3: 逐 key 判定是否「跨任务不一致」
    by_locale: dict[str, list[dict]] = {}
    total_divergent = 0
    total_keys = len(per_key)

    for (opus_id, locale), tid_map in per_key.items():
        versions = []
        distinct_vals: set = set()
        any_missing = False
        for task in tasks_sorted:
            tid = task.get("task_id")
            entry = tid_map.get(tid)
            present = entry is not None
            text = entry["text"] if present else ""
            versions.append({
                "task_id": tid,
                "created_at": _task_created(task),
                "text": text,
                "present": present,
            })
            # 「键缺失」与「键在场但译文为空/全空白」对人而言都是「没有译文」，
            # 应折叠成同一种取值（_MISSING）。否则 缺失 vs 在场-空串 会被算成
            # 两种取值 → 误报成 added/removed。真实译文非空，不会被此规则误吞。
            if (text or "").strip():
                distinct_vals.add(text)
            else:
                distinct_vals.add(_MISSING)
                any_missing = True

        if len(distinct_vals) < 2:
            continue  # 全部任务一致 —— 不是问题，跳过

        total_divergent += 1
        present_distinct = {v for v in distinct_vals if v is not _MISSING}
        changed_kind = "added_removed" if (any_missing and len(present_distinct) <= 1) else "text"
        by_locale.setdefault(locale, []).append({
            "opus_id": opus_id,
            "target_language": locale,
            "source_text": source_by_key.get((opus_id, locale), ""),
            "versions": versions,
            "distinct": len(distinct_vals),
            "changed_kind": changed_kind,
        })

    # 每个语种内按 opus_id 排序，稳定可读
    for locale in by_locale:
        by_locale[locale].sort(key=lambda d: d["opus_id"])

    # 语种按"不一致条数"降序（问题多的语种排前面），并列按字母
    locales = sorted(by_locale.keys(),
                     key=lambda L: (-len(by_locale[L]), L))

    return {
        "tasks": [{
            "task_id": t.get("task_id"),
            "created_at": _task_created(t),
            "label": (t.get("created_at") or "")[:19].replace("T", " "),
        } for t in tasks_sorted],
        "by_locale": by_locale,
        "locales": locales,
        "total_divergent": total_divergent,
        "total_keys": total_keys,
        "task_count": len(tasks_sorted),
        "group_task_count": len(all_tasks),
        "failed_count": len(failed_tids),
        "insufficient": False,
    }


# ---------------------------------------------------------------------------
# 高亮分段：复用 export_mr_pipeline 的 token 切分 + difflib
# ---------------------------------------------------------------------------
def diff_runs(before: str, after: str) -> list[tuple[str, str]]:
    """词/字级 diff → ``[(kind, text), ...]``，kind ∈ {equal, delete, insert}。

    供 Tk Text 上色：delete=红（旧版删去），insert=绿（新版加入）。复用
    export_mr_pipeline 的 ``_diff_tokens``（CJK 按字、拉丁按词、占位符原子化，
    超长退化到按行）保证与 HTML / 文本报告的 diff 口径一致。
    """
    import difflib
    import export_mr_pipeline as _mp

    before_tokens, after_tokens = _mp._diff_tokens(before or "", after or "")
    sm = difflib.SequenceMatcher(None, before_tokens, after_tokens)
    runs: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            runs.append(("equal", "".join(before_tokens[i1:i2])))
        elif tag == "replace":
            runs.append(("delete", "".join(before_tokens[i1:i2])))
            runs.append(("insert", "".join(after_tokens[j1:j2])))
        elif tag == "delete":
            runs.append(("delete", "".join(before_tokens[i1:i2])))
        elif tag == "insert":
            runs.append(("insert", "".join(after_tokens[j1:j2])))
    return runs
