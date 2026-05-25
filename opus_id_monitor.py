"""
OPUS ID Monitor — 本地 SQLite 缓存 + Tranzor API 增量同步
========================================================

为 TranzorExporter 提供"随时随地看 OPUS ID 全局态势"的数据层。
设计要点：

- **本地缓存**：所有 opus_id 数据落 SQLite，路径 ``~/.tranzor_exporter/opus_index.db``。
  断网也能看历史；首屏不依赖网络往返。
- **增量同步**：默认只拉 ``last_sync_at`` 之后创建的 MR / Scan 任务，秒级完成。
  首次或手动可触发全量。
- **结构化字段**：插入时按 Tranzor 的 ``LegacyTranslationKey.ts`` 算法预解析
  opus_id 为 (alias, path_hash, logical_key)，让任何按项目 / 按文件指纹的查询
  都不需要再做字符串切分。

参考 Tranzor 源码 ``export_import_cli/src/domains/LegacyTranslationKey.ts``:

    `RingCentral.${alias}.${md5(sourceRelativePath)}.${logicalKey}`

暴露给上层（UI tab）的入口：

    - :func:`init_db` — 创建 schema（幂等）
    - :func:`sync_full` / :func:`sync_incremental` — 数据同步
    - :func:`get_summary`、:func:`get_per_project_breakdown`、
      :func:`get_daily_trend`、:func:`get_recent_additions` — 查询
    - :func:`parse_opus_id` — 单独的解析工具（测试方便）
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterable

# 复用现有 Tranzor HTTP 封装，避免与 MR Pipeline tab 重复鉴权 / 重试逻辑。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_mr_pipeline as mr_api
# Legacy / File Translation API 走 export_translations 里的现有 helper；
# 它对应的是 /api/v1/legacy/tasks 那一条链路。
import export_translations as legacy_api


# ---------------------------------------------------------------------------
# 并行抓取上限 —— 8 个 worker 在实测里既能榨干 Tranzor API 的带宽
# 又不会让 requests.Session 出现明显的竞态。如果未来后端撑不住，调低即可。
# ---------------------------------------------------------------------------
MAX_FETCH_WORKERS = 8


# ---------------------------------------------------------------------------
# 数据库位置 — 用户主目录下的隐藏目录，跨重装 / 跨重启都能保留缓存
# ---------------------------------------------------------------------------
def _default_db_path() -> str:
    home = os.path.expanduser("~")
    base = os.path.join(home, ".tranzor_exporter")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "opus_index.db")


@contextmanager
def _connect(db_path: str | None = None):
    """打开一个 SQLite 连接。每个调用方拿到独立连接、独立事务边界。

    **不加全局锁**：SQLite 在 WAL 模式下原生支持"多读 + 单写"并发，
    并发的写入会被 SQLite 内部排队。我们只要保证：
      - 每个线程用自己的连接（``_connect`` 每次都新建一个）；
      - 同一连接不跨线程使用（默认 ``check_same_thread=True`` 会强校验）。

    早期版本在这里加过 ``threading.RLock``，结果"同步期间 UI 读不到任何
    数据"——因为同步主线程把 RLock 占了整段同步时间，UI 后台刷新被阻塞
    在 ``_connect`` 入口。改成无锁后 WAL 才真正生效。
    """
    path = db_path or _default_db_path()
    conn = sqlite3.connect(path, timeout=30.0)
    # timeout=30s：万一某次写真的撞上，SQLite 会自动重试这么久才报
    # "database is locked"。对我们的写量足够宽裕。
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema — 一次性创建，幂等
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS opus_index (
    opus_id          TEXT NOT NULL,
    target_language  TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    alias            TEXT,
    path_hash        TEXT,
    logical_key      TEXT,
    project_id       TEXT,
    release          TEXT,
    source_text      TEXT,
    translated_text  TEXT,            -- 译文，详情对话框展示用
    source_file_path TEXT,            -- 真实源文件相对路径（path_hash 的明文，debug 极有用）
    source_kind      TEXT NOT NULL,   -- 'mr' or 'scan' or 'file'
    mr_iid           INTEGER,
    task_created_at  TEXT,
    first_seen       TEXT NOT NULL,
    PRIMARY KEY (opus_id, target_language, task_id)
);
CREATE INDEX IF NOT EXISTS ix_opus_alias        ON opus_index(alias);
CREATE INDEX IF NOT EXISTS ix_opus_path_hash    ON opus_index(path_hash);
CREATE INDEX IF NOT EXISTS ix_opus_project_id   ON opus_index(project_id);
CREATE INDEX IF NOT EXISTS ix_opus_first_seen   ON opus_index(first_seen);
CREATE INDEX IF NOT EXISTS ix_opus_task_created ON opus_index(task_created_at);
CREATE INDEX IF NOT EXISTS ix_opus_opus_id      ON opus_index(opus_id);

CREATE TABLE IF NOT EXISTS sync_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_db(db_path: str | None = None) -> None:
    """创建表与索引（首次运行 / 升级都安全）。

    schema 演化：用 ``ALTER TABLE ... ADD COLUMN`` 而不是 DROP/recreate，
    保留用户已经攒下来的本地缓存。SQLite 不支持 ``IF NOT EXISTS`` 给
    ADD COLUMN，所以我们自己查 PRAGMA table_info 后再决定加不加。
    """
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        # 增量列：早期版本没有这俩，老用户升级时补齐
        cur = conn.execute("PRAGMA table_info(opus_index)")
        existing = {row["name"] for row in cur.fetchall()}
        if "translated_text" not in existing:
            conn.execute(
                "ALTER TABLE opus_index ADD COLUMN translated_text TEXT")
        if "source_file_path" not in existing:
            conn.execute(
                "ALTER TABLE opus_index ADD COLUMN source_file_path TEXT")


# ---------------------------------------------------------------------------
# OPUS ID 解析 —— 与 Tranzor LegacyTranslationKey.ts 算法对齐
# ---------------------------------------------------------------------------
def parse_opus_id(opus_id: str) -> dict:
    """把 ``RingCentral.{alias}.{md5(path)}.{logicalKey}`` 拆成 4 段。

    返回 dict 而非 tuple，方便扩展（未来若 Tranzor 加新段位也不必动调用方）。
    解析失败的不规范 ID 会在 ``alias`` / ``path_hash`` / ``logical_key`` 都为空，
    上层可据此判断要不要落库（默认仍然落，方便事后查脏数据）。
    """
    if not opus_id:
        return {"alias": "", "path_hash": "", "logical_key": ""}
    parts = opus_id.split(".", 3)
    # 形如 ["RingCentral", "<alias>", "<path_hash>", "<logical_key>"]
    # logical_key 内部允许有点号（极少见），所以用 maxsplit=3 守住前三段。
    if len(parts) < 4 or parts[0] != "RingCentral":
        return {"alias": "", "path_hash": "", "logical_key": opus_id}
    return {
        "alias": parts[1],
        "path_hash": parts[2],
        "logical_key": parts[3],
    }


# ---------------------------------------------------------------------------
# sync_meta 帮助方法
# ---------------------------------------------------------------------------
def _get_meta(conn, key: str, default: str | None = None) -> str | None:
    cur = conn.execute("SELECT value FROM sync_meta WHERE key = ?", (key,))
    row = cur.fetchone()
    return row["value"] if row else default


def _set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# ---------------------------------------------------------------------------
# 落库：批量插入翻译行
# ---------------------------------------------------------------------------
def _bulk_upsert(
    conn,
    translations: Iterable[dict],
    *,
    source_kind: str,
    default_task_id: str = "",
    default_project_id: str = "",
    default_release: str = "",
    default_mr_iid=None,
    task_created_at: str = "",
) -> int:
    """把一批 translation dict 落到 opus_index 表。

    Returns:
        实际新插入（不含重复主键）的行数。
    """
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for t in translations:
        opus_id = (t.get("opus_id") or "").strip()
        target_lang = (t.get("target_language") or "").strip()
        if not opus_id or not target_lang:
            continue
        task_id = (t.get("task_id") or default_task_id or "").strip()
        if not task_id:
            # task_id 是主键的一部分；缺了就丢掉，避免主键冲突毁掉整批
            continue
        parsed = parse_opus_id(opus_id)
        rows.append((
            opus_id,
            target_lang,
            task_id,
            parsed["alias"],
            parsed["path_hash"],
            parsed["logical_key"],
            t.get("project_id") or default_project_id or "",
            t.get("release") or default_release or "",
            (t.get("source_text") or "")[:8192],  # 防长文本爆缓存
            (t.get("translated_text") or "")[:8192],  # 译文同样限长
            t.get("source_file_path") or "",  # 真实源路径（path_hash 的明文）
            source_kind,
            t.get("mr_iid") if t.get("mr_iid") is not None else default_mr_iid,
            task_created_at,
            now_iso,
        ))

    if not rows:
        return 0

    # INSERT OR IGNORE 保留首次 first_seen 时间，重复任务再次同步不会覆盖。
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO opus_index(
            opus_id, target_language, task_id,
            alias, path_hash, logical_key,
            project_id, release, source_text,
            translated_text, source_file_path,
            source_kind, mr_iid, task_created_at, first_seen
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return cur.rowcount or 0


# ---------------------------------------------------------------------------
# 并行抓取共享逻辑：MR 和 Scan 同步都用它
# ---------------------------------------------------------------------------
def _drain_results_into_db(
    tasks: list[dict],
    *,
    fetch_fn,
    conn,
    source_kind: str,
    log,
    log_stage: str,
    cancel_event: threading.Event | None,
    stats: dict,
    default_project_fn=None,
) -> None:
    """把 ``tasks`` 列表的 results 用线程池并行拉取，按完成顺序串行落库。

    设计要点：
      - **HTTP 走线程池**（I/O bound，8 路并行能比串行快近一个数量级）；
      - **SQLite 写入留在主调线程**（_bulk_upsert + conn.commit），SQLite
        WAL 模式下的写入串行化由我们自己保证，绝不在 worker 里碰 conn；
      - **as_completed 一边出一边写**，首屏的"卡片在涨"动效随时可见；
      - **cancel_event**：触发后立刻停止派发未发出的 future，对于已经在
        飞的 HTTP（requests.get 不能安全中断），等它返回再丢弃。
    """
    if not tasks:
        return

    total = len(tasks)
    completed = 0

    # 给 worker 用的 fetch 包装：失败也要返回 (task, exception)，
    # 不能让某个坏 task 把整个 future 卡到永远 pending。
    def _worker(task):
        if cancel_event and cancel_event.is_set():
            return task, None  # 主线程会忽略 None
        try:
            return task, fetch_fn(task)
        except Exception as e:
            return task, e

    pool = ThreadPoolExecutor(
        max_workers=MAX_FETCH_WORKERS,
        thread_name_prefix=f"opus-{source_kind}",
    )
    try:
        futures = [pool.submit(_worker, t) for t in tasks]
        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                # 不再处理新的；in-flight 的也忽略结果，外层 finally 会
                # 用 cancel_futures=True 把还没开跑的全砍掉，加速退出。
                break

            try:
                task, results = future.result()
            except Exception as e:
                # _worker 已经吞掉了 fetch 异常，能到这里说明是更深的 bug。
                log(f"{log_stage}_error", completed, total, error=str(e))
                continue

            if results is None:
                # cancel 在 worker 入口就被检测到，没必要处理
                continue

            task_id = (task.get("task_id") or task.get("id") or "").strip()
            if isinstance(results, Exception):
                log(f"{log_stage}_error", completed + 1, total,
                    task_id=task_id, error=str(results))
                completed += 1
                continue

            translations = (results or {}).get("translations") or []
            # legacy 路径用 task_name 顶 project_id；MR/Scan 用真实 project_id。
            proj = task.get("project_id", "")
            if not proj and default_project_fn:
                try:
                    proj = default_project_fn(task) or ""
                except Exception:
                    proj = ""
            inserted = _bulk_upsert(
                conn,
                translations,
                source_kind=source_kind,
                default_task_id=task_id,
                default_project_id=proj,
                default_release=task.get("release", ""),
                default_mr_iid=task.get("merge_request_iid"),
                task_created_at=task.get("created_at") or "",
            )
            stats["tasks_seen"] += 1
            stats["rows_inserted"] += inserted
            completed += 1
            # 每个任务结束 commit 一次：哪怕同步中途崩，已处理的也安全落库；
            # 更重要的是 —— 主 GUI 的定时刷新走的是新连接，
            # 必须及时 commit 才能读到。
            conn.commit()
            log(log_stage, completed, total,
                task_id=task_id, inserted=inserted,
                rows_total=stats["rows_inserted"])
    finally:
        # 取消还没开跑的 future（Python 3.9+ 行为）；in-flight 的让它跑完
        # 但不再处理它的返回——避免阻塞 UI 上的"取消"按钮。
        pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# 同步：MR Pipeline
# ---------------------------------------------------------------------------
def _sync_mr_tasks(
    conn,
    *,
    since_iso: str | None,
    progress_callback=None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """同步所有 status=completed 的 MR 任务及其 translations。

    Args:
        since_iso: 仅处理 created_at >= since_iso 的任务；None 表示全量。
        progress_callback: callable(stage: str, current: int, total: int, **kwargs)
        cancel_event: 协程式取消标志位；置 set 后下一批边界处优雅退出。
    """
    log = progress_callback or (lambda *a, **kw: None)
    stats = {"tasks_seen": 0, "rows_inserted": 0}

    # 第一步：分页拉所有 completed 任务（按 created_at desc）
    log("mr_list", 0, 0)
    all_tasks = []
    offset = 0
    page_size = 100
    total_mr_tasks = 0
    while True:
        if cancel_event and cancel_event.is_set():
            return stats
        total, batch = mr_api.fetch_mr_tasks(
            status="completed", limit=page_size, offset=offset)
        total_mr_tasks = total
        if not batch:
            break
        # 按时间窗口提前裁剪，避免后面无谓的 results 请求
        for t in batch:
            created = t.get("created_at") or ""
            if since_iso and created and created < since_iso:
                # 已按 created_at desc 排序，遇到更早的可以提前停。
                # 注意：Tranzor 默认排序需要确认；保险起见仍走完一页。
                continue
            all_tasks.append(t)
        if offset + page_size >= total:
            break
        offset += page_size
        log("mr_list", len(all_tasks), total)

    log("mr_results", 0, len(all_tasks))

    # 第二步：并行拉 results、串行落库。
    # 设计要点：
    #   - HTTP fetch 是 I/O bound，开 8 个 worker 并行；
    #   - SQLite 写入回到主线程（这里就是 sync 调用方所在线程），
    #     避免 "database is locked" / WAL 写入竞态；
    #   - 用 as_completed 一边出结果一边落库，第一秒就有数据进 DB，
    #     UI 的定时刷新立刻能看到卡片往上跳；
    #   - cancel_event 触发后停止派发新任务，但已 in-flight 的 HTTP
    #     会让它跑完（无法安全中断 requests.get）。
    _drain_results_into_db(
        all_tasks,
        fetch_fn=lambda t: mr_api.fetch_mr_results(
            t.get("task_id") or t.get("id") or ""),
        conn=conn,
        source_kind="mr",
        log=log,
        log_stage="mr_results",
        cancel_event=cancel_event,
        stats=stats,
    )

    _set_meta(conn, "mr_total_tasks", str(total_mr_tasks))
    return stats


# ---------------------------------------------------------------------------
# 同步：Scan Tasks
# ---------------------------------------------------------------------------
def _sync_scan_tasks(
    conn,
    *,
    since_iso: str | None,
    progress_callback=None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """同步 Missing Translation Scan 任务及其 translations。"""
    log = progress_callback or (lambda *a, **kw: None)
    stats = {"tasks_seen": 0, "rows_inserted": 0}

    log("scan_list", 0, 0)
    all_tasks = []
    offset = 0
    page_size = 100
    total_scan = 0
    while True:
        if cancel_event and cancel_event.is_set():
            return stats
        try:
            total, batch = mr_api.fetch_scan_tasks(
                status="completed", limit=page_size, offset=offset)
        except Exception as e:
            log("scan_list_error", 0, 0, error=str(e))
            break
        total_scan = total
        if not batch:
            break
        for t in batch:
            created = t.get("created_at") or ""
            if since_iso and created and created < since_iso:
                continue
            all_tasks.append(t)
        if offset + page_size >= total:
            break
        offset += page_size
        log("scan_list", len(all_tasks), total)

    log("scan_results", 0, len(all_tasks))

    _drain_results_into_db(
        all_tasks,
        fetch_fn=lambda t: mr_api.fetch_scan_results(
            t.get("task_id") or t.get("id") or ""),
        conn=conn,
        source_kind="scan",
        log=log,
        log_stage="scan_results",
        cancel_event=cancel_event,
        stats=stats,
    )

    _set_meta(conn, "scan_total_tasks", str(total_scan))
    return stats


# ---------------------------------------------------------------------------
# 同步：File Translation（legacy task）
# ---------------------------------------------------------------------------
def _sync_legacy_tasks(
    conn,
    *,
    since_iso: str | None,
    progress_callback=None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """同步 File Translation（legacy）任务的 translations。

    与 MR/Scan 不同：
      - Legacy API 走 /api/v1/legacy/tasks，shape 是 ``{tasks: [...]}``，
        每个 task 没有 ``project_id`` 概念（用户上传文件，一个任务对应一份文件）；
      - 我们把 ``task_name`` 当作 ``project_id`` 落库，让 breakdown 仍能
        按"哪份上传"聚合，颗粒度对得上用户的心智模型。
      - entries 直接带 opus_id / target_language / source_text，
        和 MR/Scan 同一套 ``_bulk_upsert`` 完全复用。
    """
    log = progress_callback or (lambda *a, **kw: None)
    stats = {"tasks_seen": 0, "rows_inserted": 0}

    log("legacy_list", 0, 0)
    try:
        # Legacy API 不接收 since 过滤，只能拿全量后我们这边按 created_at 裁。
        all_legacy = legacy_api.fetch_tasks()
    except Exception as e:
        log("legacy_list_error", 0, 0, error=str(e))
        return stats

    # 按 created_at 裁剪（增量模式下）。Legacy task 字段名可能是 created_at
    # 也可能是其他；保险起见两个都试。
    filtered = []
    for t in all_legacy:
        created = t.get("created_at") or t.get("createdAt") or ""
        if since_iso and created and created < since_iso:
            continue
        filtered.append(t)
    log("legacy_list", len(filtered), len(all_legacy))

    log("legacy_results", 0, len(filtered))

    # Legacy API 的"results"对应 fetch_all_translations(task_id)。
    # 注意它**内部已经做了并发分页**，所以我们这层只用 1 个 worker
    # 防止把后端打挂；上面 MAX_FETCH_WORKERS 是 MR/Scan 的并发度，
    # legacy 路径在 fetch_all_translations 内部自带 6 个 page worker。
    def _fetch_legacy(task):
        task_id = task.get("id") or task.get("task_id") or ""
        if not task_id:
            return {"translations": []}
        try:
            entries = legacy_api.fetch_all_translations(task_id)
        except Exception:
            raise
        # 转译字段名让 _bulk_upsert 能直接用。translated_text 和
        # source_file_path 同样带过来：详情对话框要展示译文 + 真实路径。
        translations = [{
            "opus_id": e.get("opus_id") or "",
            "target_language": e.get("target_language") or "",
            "source_text": e.get("source_text") or "",
            "translated_text": e.get("translated_text") or "",
            "source_file_path": (e.get("source_file_path")
                                  or e.get("source_relative_path") or ""),
            "task_id": str(task_id),
        } for e in entries]
        return {"translations": translations}

    # legacy fetch 内部已经并发了，外层我们用更小的并发度（2）
    # 避免和它内部的 6 路并发叠乘，把 Tranzor 拍懵。
    original_workers = MAX_FETCH_WORKERS
    try:
        globals()["MAX_FETCH_WORKERS"] = 2
        _drain_results_into_db(
            filtered,
            fetch_fn=_fetch_legacy,
            conn=conn,
            source_kind="file",
            log=log,
            log_stage="legacy_results",
            cancel_event=cancel_event,
            stats=stats,
            # Legacy 没有 project_id，把 task_name 顶上去
            default_project_fn=lambda t: (
                t.get("task_name") or f"Task {t.get('id', '')}"),
        )
    finally:
        globals()["MAX_FETCH_WORKERS"] = original_workers

    _set_meta(conn, "legacy_total_tasks", str(len(all_legacy)))
    return stats


# ---------------------------------------------------------------------------
# 公开同步入口
# ---------------------------------------------------------------------------
def sync_incremental(
    *,
    progress_callback=None,
    cancel_event: threading.Event | None = None,
    db_path: str | None = None,
) -> dict:
    """只拉 ``last_sync_at`` 之后创建的任务（默认行为，快）。"""
    init_db(db_path)
    with _connect(db_path) as conn:
        last_sync = _get_meta(conn, "last_sync_at")
        sync_started = datetime.now(timezone.utc).isoformat(timespec="seconds")

        mr_stats = _sync_mr_tasks(
            conn, since_iso=last_sync,
            progress_callback=progress_callback,
            cancel_event=cancel_event)
        scan_stats = _sync_scan_tasks(
            conn, since_iso=last_sync,
            progress_callback=progress_callback,
            cancel_event=cancel_event)
        legacy_stats = _sync_legacy_tasks(
            conn, since_iso=last_sync,
            progress_callback=progress_callback,
            cancel_event=cancel_event)

        if not (cancel_event and cancel_event.is_set()):
            _set_meta(conn, "last_sync_at", sync_started)

        return {
            "mode": "incremental",
            "since": last_sync,
            "started_at": sync_started,
            "mr": mr_stats,
            "scan": scan_stats,
            "legacy": legacy_stats,
        }


def sync_full(
    *,
    progress_callback=None,
    cancel_event: threading.Event | None = None,
    db_path: str | None = None,
) -> dict:
    """全量同步（首次或手动触发）。比增量慢一个数量级，但保证一致性。"""
    init_db(db_path)
    with _connect(db_path) as conn:
        sync_started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        mr_stats = _sync_mr_tasks(
            conn, since_iso=None,
            progress_callback=progress_callback,
            cancel_event=cancel_event)
        scan_stats = _sync_scan_tasks(
            conn, since_iso=None,
            progress_callback=progress_callback,
            cancel_event=cancel_event)
        legacy_stats = _sync_legacy_tasks(
            conn, since_iso=None,
            progress_callback=progress_callback,
            cancel_event=cancel_event)
        if not (cancel_event and cancel_event.is_set()):
            _set_meta(conn, "last_sync_at", sync_started)
            _set_meta(conn, "last_full_sync_at", sync_started)
        return {
            "mode": "full",
            "started_at": sync_started,
            "mr": mr_stats,
            "scan": scan_stats,
            "legacy": legacy_stats,
        }


# ---------------------------------------------------------------------------
# 查询接口 —— 全部走索引，UI 直接消费
# ---------------------------------------------------------------------------
def get_summary(db_path: str | None = None) -> dict:
    """首屏 4 张大数字卡的数据。

    Returns:
        - total_opus_ids: distinct opus_id
        - total_path_hashes: distinct (alias, path_hash) 二元组
        - total_projects: distinct project_id
        - total_aliases: distinct alias
        - new_today / new_7d / new_30d: first_seen 在窗口内的 distinct opus_id
        - total_rows: 总行数（包含同 opus_id 多语言、多任务）
        - last_sync_at, last_full_sync_at
    """
    init_db(db_path)
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    iso_today = today_start.isoformat(timespec="seconds")
    iso_7d = (today_start - timedelta(days=7)).isoformat(timespec="seconds")
    iso_30d = (today_start - timedelta(days=30)).isoformat(timespec="seconds")

    with _connect(db_path) as conn:
        def scalar(sql, *args):
            cur = conn.execute(sql, args)
            row = cur.fetchone()
            return (row[0] if row else 0) or 0

        out = {
            "total_opus_ids": scalar(
                "SELECT COUNT(DISTINCT opus_id) FROM opus_index"),
            "total_path_hashes": scalar(
                "SELECT COUNT(*) FROM "
                "(SELECT DISTINCT alias, path_hash FROM opus_index "
                " WHERE path_hash != '')"),
            "total_projects": scalar(
                "SELECT COUNT(DISTINCT project_id) FROM opus_index "
                "WHERE project_id != ''"),
            "total_aliases": scalar(
                "SELECT COUNT(DISTINCT alias) FROM opus_index "
                "WHERE alias != ''"),
            "total_rows": scalar("SELECT COUNT(*) FROM opus_index"),
            "new_today": scalar(
                "SELECT COUNT(DISTINCT opus_id) FROM opus_index "
                "WHERE first_seen >= ?", iso_today),
            "new_7d": scalar(
                "SELECT COUNT(DISTINCT opus_id) FROM opus_index "
                "WHERE first_seen >= ?", iso_7d),
            "new_30d": scalar(
                "SELECT COUNT(DISTINCT opus_id) FROM opus_index "
                "WHERE first_seen >= ?", iso_30d),
            "last_sync_at": _get_meta(conn, "last_sync_at"),
            "last_full_sync_at": _get_meta(conn, "last_full_sync_at"),
        }
        return out


def get_per_project_breakdown(db_path: str | None = None) -> list[dict]:
    """每个 (project_id, source_kind) 的 opus_id 数 / 文件指纹数 / 最近新增。

    同一 project 在 MR 和 Scan 两侧都出过翻译会被拆成两行 —— 这是 by design：
    用户能立刻看清楚某个项目"哪条管线在跑、谁多谁少"。UI 端会把它渲染成
    "web/web (MR)" 这样带源头标签的可读形态。
    """
    init_db(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT
                project_id,
                source_kind,
                COALESCE(MAX(alias), '') AS alias,
                COUNT(DISTINCT opus_id)                  AS opus_count,
                COUNT(DISTINCT alias || ':' || path_hash) AS path_count,
                MAX(first_seen)                          AS last_added,
                COUNT(DISTINCT target_language)          AS lang_count,
                COUNT(*)                                 AS row_count
            FROM opus_index
            WHERE project_id != ''
            GROUP BY project_id, source_kind
            ORDER BY opus_count DESC
            """
        )
        return [dict(r) for r in cur.fetchall()]


def get_project_detail(
    project_id: str,
    source_kind: str | None = None,
    *,
    files_limit: int = 50,
    samples_per_file: int = 5,
    db_path: str | None = None,
) -> dict:
    """点击 "Breakdown by project" 某一行后展示的钻取数据。

    Returns:
        {
          "project_id": str,
          "source_kind": str,
          "summary": {opus_count, path_count, lang_count, row_count,
                      first_seen, last_added},
          "files": [{path_hash, opus_count, lang_count, last_added,
                     samples: [{opus_id, logical_key}, ...]}, ...]
        }
    """
    init_db(db_path)
    with _connect(db_path) as conn:
        where = ["project_id = ?"]
        params: list = [project_id]
        if source_kind:
            where.append("source_kind = ?")
            params.append(source_kind)
        where_sql = " AND ".join(where)

        # Project-level summary
        cur = conn.execute(
            f"""
            SELECT
                COUNT(DISTINCT opus_id)                  AS opus_count,
                COUNT(DISTINCT alias || ':' || path_hash) AS path_count,
                COUNT(DISTINCT target_language)          AS lang_count,
                COUNT(*)                                 AS row_count,
                MIN(first_seen)                          AS first_seen,
                MAX(first_seen)                          AS last_added
            FROM opus_index WHERE {where_sql}
            """, params)
        summary = dict(cur.fetchone() or {})

        # Per-file (path_hash) breakdown —— 同时取 source_file_path
        # 让 ProjectDetailDialog 不再只能展示 hash，能直观告诉用户"这是哪份文件"。
        cur = conn.execute(
            f"""
            SELECT
                alias, path_hash,
                COUNT(DISTINCT opus_id)         AS opus_count,
                COUNT(DISTINCT target_language) AS lang_count,
                MAX(first_seen)                 AS last_added,
                COALESCE(MAX(NULLIF(source_file_path, '')), '') AS source_file_path
            FROM opus_index
            WHERE {where_sql} AND path_hash != ''
            GROUP BY alias, path_hash
            ORDER BY opus_count DESC
            LIMIT ?
            """, params + [files_limit])
        files = [dict(r) for r in cur.fetchall()]

        # 给每个 file 配几条样本 opus_id，方便用户立刻看出"这文件里都是啥"
        for f in files:
            cur = conn.execute(
                """
                SELECT DISTINCT opus_id, logical_key, source_text
                FROM opus_index
                WHERE project_id = ? AND alias = ? AND path_hash = ?
                ORDER BY first_seen DESC
                LIMIT ?
                """, (project_id, f["alias"], f["path_hash"], samples_per_file))
            f["samples"] = [dict(r) for r in cur.fetchall()]

        return {
            "project_id": project_id,
            "source_kind": source_kind or "",
            "summary": summary,
            "files": files,
        }


def get_file_detail(
    project_id: str,
    alias: str,
    path_hash: str,
    source_kind: str | None = None,
    *,
    samples_limit: int = 200,
    db_path: str | None = None,
) -> dict:
    """点击 ProjectDetailDialog 里某个源文件行后的钻取数据。

    展示一个 (project, alias, path_hash) 三元组下所有 opus_id、覆盖的
    语言数、最近变化。OPUS ID Monitor 用它来回答"这个文件里都有哪些字符串"
    —— 对追踪 BUG（如 LOC-24722 同一 logical key 跨多个 path_hash）至关重要。
    """
    init_db(db_path)
    with _connect(db_path) as conn:
        where_sql = "project_id = ? AND alias = ? AND path_hash = ?"
        params = [project_id, alias, path_hash]
        if source_kind:
            where_sql += " AND source_kind = ?"
            params.append(source_kind)

        # File-level summary
        cur = conn.execute(f"""
            SELECT
                COUNT(DISTINCT opus_id)         AS opus_count,
                COUNT(DISTINCT target_language) AS lang_count,
                COUNT(*)                        AS row_count,
                MIN(first_seen)                 AS first_seen,
                MAX(first_seen)                 AS last_added,
                COALESCE(MAX(NULLIF(source_file_path, '')), '') AS source_file_path
            FROM opus_index WHERE {where_sql}
        """, params)
        summary = dict(cur.fetchone() or {})

        # 该文件下每个 opus_id：以 logical_key 为聚合键拿一行
        cur = conn.execute(f"""
            SELECT
                opus_id, logical_key,
                COUNT(DISTINCT target_language) AS lang_count,
                MAX(first_seen)                 AS last_added,
                MAX(source_text)                AS source_text
            FROM opus_index WHERE {where_sql}
            GROUP BY opus_id
            ORDER BY last_added DESC
            LIMIT ?
        """, params + [samples_limit])
        opus_rows = [dict(r) for r in cur.fetchall()]

        return {
            "project_id": project_id,
            "alias": alias,
            "path_hash": path_hash,
            "source_kind": source_kind or "",
            "summary": summary,
            "opus_ids": opus_rows,
        }


def md5_path(relative_path: str) -> str:
    """计算 ``RingCentral.{alias}.{md5}.{logical}`` 中段 md5，跟 Tranzor 的
    LegacyTranslationKey.ts ``createHash('md5').update(sourceRelativePath)``
    完全等价。供反查工具用。"""
    import hashlib
    if not relative_path:
        return ""
    return hashlib.md5(relative_path.encode("utf-8")).hexdigest()


def lookup_path_hash(path_hash: str, db_path: str | None = None) -> list[dict]:
    """反查：给一个 path_hash → 返回本地缓存里所有用它的 (project, alias)
    分组及其 opus_id 数、可能的 source_file_path（如果同步过）。

    用户输入 path_hash 后立刻能看到："这个 hash 对应 web/bui 的某文件，
    在那里产生了 421 个 opus_id" —— 配合 LOC-24722 这类 hash 漂移调查。
    """
    init_db(db_path)
    if not path_hash:
        return []
    with _connect(db_path) as conn:
        cur = conn.execute("""
            SELECT
                project_id, alias, source_kind,
                COUNT(DISTINCT opus_id) AS opus_count,
                COUNT(DISTINCT target_language) AS lang_count,
                MAX(first_seen) AS last_added,
                COALESCE(MAX(NULLIF(source_file_path, '')), '') AS source_file_path
            FROM opus_index
            WHERE path_hash = ?
            GROUP BY project_id, alias, source_kind
            ORDER BY opus_count DESC
        """, (path_hash,))
        return [dict(r) for r in cur.fetchall()]


def lookup_path_string(
    relative_path: str,
    db_path: str | None = None,
) -> dict:
    """正向：给一个文件相对路径 → 算 md5 → 查本地缓存。

    Returns:
        {
          "input_path": str,
          "path_hash": str,
          "matches": [{project_id, alias, opus_count, ...}, ...]
        }
    """
    h = md5_path(relative_path)
    return {
        "input_path": relative_path,
        "path_hash": h,
        "matches": lookup_path_hash(h, db_path=db_path),
    }


def get_opus_detail(opus_id: str, db_path: str | None = None) -> dict:
    """点击 "Recently added" 某一行后展示的 opus_id 详情。

    返回字段在 v3 扩展为：
      - ``source_file_path``：path_hash 的明文，是 debug "为什么 ID 变了"
        的关键证据，比 32 位 hex 直观一个量级。
      - ``target_languages[i].translated_text``：每个目标语言的最新译文，
        让用户在监控面板里就能预览翻译，不必跳到 Tranzor 平台。
    """
    init_db(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT
                opus_id, alias, path_hash, logical_key,
                project_id, source_kind, release, mr_iid,
                target_language, source_text, translated_text,
                source_file_path, task_id,
                task_created_at, first_seen
            FROM opus_index
            WHERE opus_id = ?
            ORDER BY target_language
            """, (opus_id,))
        rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return {"opus_id": opus_id, "found": False, "rows": []}
        # 顶层共用字段（取第一行就行 —— 同一 opus_id 的元数据相同）
        head = rows[0]
        # source_file_path 偶尔可能某些行没填（早期数据 / API 未返回），
        # 用 max(...) 取第一个非空，避免误把"该 ID 没路径信息"展示给用户
        path = next(
            (r["source_file_path"] for r in rows if r["source_file_path"]),
            "")
        return {
            "opus_id": opus_id,
            "found": True,
            "alias": head["alias"],
            "path_hash": head["path_hash"],
            "logical_key": head["logical_key"],
            "project_id": head["project_id"],
            "source_kind": head["source_kind"],
            "release": head["release"],
            "mr_iid": head["mr_iid"],
            "task_id": head["task_id"],
            "task_created_at": head["task_created_at"],
            "first_seen": head["first_seen"],
            "source_text": head["source_text"],
            "source_file_path": path,
            "target_languages": [
                {"target_language": r["target_language"],
                 "first_seen": r["first_seen"],
                 "translated_text": r["translated_text"] or ""}
                for r in rows
            ],
        }


def get_daily_trend(days: int = 30, db_path: str | None = None) -> list[dict]:
    """近 N 天每日新增 distinct opus_id 数。

    输出固定 N 行（含 0 行的日期），方便上层直接画图。
    """
    init_db(db_path)
    today = datetime.now(timezone.utc).date()
    out: "OrderedDict[str, int]" = OrderedDict()
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        out[d.isoformat()] = 0

    earliest = today - timedelta(days=days - 1)
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT
                substr(first_seen, 1, 10) AS day,
                COUNT(DISTINCT opus_id)   AS new_count
            FROM opus_index
            WHERE first_seen >= ?
            GROUP BY day
            ORDER BY day
            """,
            (earliest.isoformat(),),
        )
        for row in cur.fetchall():
            day = row["day"]
            if day in out:
                out[day] = row["new_count"] or 0

    return [{"date": d, "new_count": c} for d, c in out.items()]


def get_anomaly_stats(
    db_path: str | None = None,
    *,
    baseline_days: int = 30,
    warning_ratio: float = 3.0,
    critical_ratio: float = 10.0,
) -> dict:
    """计算今日新增 opus_id vs 30 天日均的偏离度，给 UI 做异常提示。

    "异常"在我们这里的语义是：今日突然涌入比基线大得多的新 opus_id，
    可能意味着：
      - 上游有大规模重命名 / 路径迁移（path_hash 集体翻新）
      - 抽取规则被改动、命中了更多文件
      - Tranzor 后端被批量补跑了一批历史任务
      - 真的有一次大改动落地了

    无论哪一种，让用户立刻看见数字尖刺都比等他偶然刷到趋势图发现要早。

    Returns:
        {
          today_new, yesterday_new, baseline_days,
          daily_avg,                    # 不包含今天，避免今天的尖刺把基线拉高
          ratio,                         # today / max(daily_avg, 1)
          level: 'normal' | 'warning' | 'critical',
          warning_ratio, critical_ratio  # 给 UI 显示阈值
        }
    """
    init_db(db_path)
    today = datetime.now(timezone.utc).date()
    today_iso = today.isoformat()
    yesterday_iso = (today - timedelta(days=1)).isoformat()
    baseline_start = (today - timedelta(days=baseline_days)).isoformat()

    with _connect(db_path) as conn:
        def scalar(sql, *args):
            cur = conn.execute(sql, args)
            row = cur.fetchone()
            return (row[0] if row else 0) or 0

        today_new = scalar(
            "SELECT COUNT(DISTINCT opus_id) FROM opus_index "
            "WHERE substr(first_seen, 1, 10) = ?", today_iso)
        yesterday_new = scalar(
            "SELECT COUNT(DISTINCT opus_id) FROM opus_index "
            "WHERE substr(first_seen, 1, 10) = ?", yesterday_iso)

        # 基线：过去 baseline_days 天里每天的 distinct opus 数取均值，
        # **排除今天**避免今天的尖刺把均值拉高。
        # 如果索引不到 baseline_days 天的数据，按实际天数算（避免除以 0 显示 inf）。
        cur = conn.execute(
            """
            SELECT
                substr(first_seen, 1, 10) AS day,
                COUNT(DISTINCT opus_id)   AS n
            FROM opus_index
            WHERE substr(first_seen, 1, 10) >= ?
              AND substr(first_seen, 1, 10) < ?
            GROUP BY day
            """,
            (baseline_start, today_iso),
        )
        per_day = [row["n"] for row in cur.fetchall()]
        if per_day:
            daily_avg = sum(per_day) / len(per_day)
        else:
            daily_avg = 0.0

    # 计算偏离倍率 + 等级
    if daily_avg < 1:
        ratio = float(today_new)  # 没有基线时按绝对值判断
    else:
        ratio = today_new / daily_avg

    if today_new == 0:
        level = "normal"
    elif daily_avg < 1 and today_new < 100:
        # 缓存还很少的早期阶段，别让小数字误报红色
        level = "normal"
    elif ratio >= critical_ratio:
        level = "critical"
    elif ratio >= warning_ratio:
        level = "warning"
    else:
        level = "normal"

    return {
        "today_new": today_new,
        "yesterday_new": yesterday_new,
        "baseline_days": baseline_days,
        "daily_avg": round(daily_avg, 1),
        "ratio": round(ratio, 2),
        "level": level,
        "warning_ratio": warning_ratio,
        "critical_ratio": critical_ratio,
    }


def get_recent_additions(
    days: int = 7,
    hard_limit: int = 1000,
    db_path: str | None = None,
) -> list[dict]:
    """近 N 天内首次出现的 opus_id（按 first_seen 倒序，去重）。

    Args:
        days: 时间窗口（天）。默认 7 天 —— 用户想看一周内的全部新增。
        hard_limit: 防爆：哪怕这周突然涌进几万个 opus_id，
            UI 也只渲染前 ``hard_limit`` 行避免 Tk 卡死。
    """
    init_db(db_path)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds")
    with _connect(db_path) as conn:
        # 内层窗口函数：每个 opus_id 取 first_seen 最早的一行，
        # 避免同 opus_id 多语言展开重复，又能保留正确的 first_seen 排序。
        cur = conn.execute(
            """
            SELECT
                opus_id, alias, path_hash, logical_key,
                project_id, target_language, source_kind, mr_iid,
                source_file_path, MIN(first_seen) AS first_seen
            FROM opus_index
            WHERE first_seen >= ?
            GROUP BY opus_id
            ORDER BY first_seen DESC
            LIMIT ?
            """,
            (cutoff, hard_limit),
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# 单元测试方便：暴露一个 reset 入口给测试用
# ---------------------------------------------------------------------------
def _reset_db_for_test(db_path: str) -> None:
    """测试专用：删表重建，绝对不要在生产代码里调用。"""
    with _connect(db_path) as conn:
        conn.executescript(
            "DROP TABLE IF EXISTS opus_index;"
            "DROP TABLE IF EXISTS sync_meta;"
        )
        conn.executescript(_SCHEMA)


__all__ = [
    "init_db",
    "parse_opus_id",
    "sync_incremental",
    "sync_full",
    "get_summary",
    "get_per_project_breakdown",
    "get_daily_trend",
    "get_recent_additions",
    "get_project_detail",
    "get_opus_detail",
    "get_file_detail",
    "get_anomaly_stats",
    "lookup_path_hash",
    "lookup_path_string",
    "md5_path",
]
