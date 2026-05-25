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
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterable

# 复用现有 Tranzor HTTP 封装，避免与 MR Pipeline tab 重复鉴权 / 重试逻辑。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_mr_pipeline as mr_api


# ---------------------------------------------------------------------------
# 数据库位置 — 用户主目录下的隐藏目录，跨重装 / 跨重启都能保留缓存
# ---------------------------------------------------------------------------
def _default_db_path() -> str:
    home = os.path.expanduser("~")
    base = os.path.join(home, ".tranzor_exporter")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "opus_index.db")


# 进程内单例锁 —— SQLite 在多线程下需要串行化写入，UI 后台线程会触发同步。
_DB_LOCK = threading.RLock()


@contextmanager
def _connect(db_path: str | None = None):
    """打开一个 SQLite 连接并保证写入串行化。

    我们刻意不用连接池：每个调用方拿到的都是新连接，事务边界由
    ``with _connect() as conn`` 自然界定。``RLock`` 保证多个后台线程
    并发同步时不会触发 ``database is locked`` 错误。
    """
    path = db_path or _default_db_path()
    with _DB_LOCK:
        conn = sqlite3.connect(path)
        # 让查询能 dict-style 取列名，对 UI 层更友好。
        conn.row_factory = sqlite3.Row
        # WAL 模式让"读不阻塞写、写不阻塞读"，对 GUI 边同步边查询很关键。
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
    source_kind      TEXT NOT NULL,   -- 'mr' or 'scan'
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
    """创建表与索引（首次运行 / 升级都安全）。"""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


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
            source_kind, mr_iid, task_created_at, first_seen
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return cur.rowcount or 0


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

    # 第二步：逐个拉 results 并落库
    for i, task in enumerate(all_tasks, 1):
        if cancel_event and cancel_event.is_set():
            return stats
        task_id = task.get("task_id") or task.get("id") or ""
        if not task_id:
            continue
        try:
            results = mr_api.fetch_mr_results(task_id)
        except Exception as e:
            log("mr_results_error", i, len(all_tasks),
                task_id=task_id, error=str(e))
            continue
        translations = results.get("translations") or []
        inserted = _bulk_upsert(
            conn,
            translations,
            source_kind="mr",
            default_task_id=task_id,
            default_project_id=task.get("project_id", ""),
            default_release=task.get("release", ""),
            default_mr_iid=task.get("merge_request_iid"),
            task_created_at=task.get("created_at") or "",
        )
        stats["tasks_seen"] += 1
        stats["rows_inserted"] += inserted
        # 每个任务结束 commit 一次：哪怕同步中途崩，已处理的也安全落库。
        conn.commit()
        log("mr_results", i, len(all_tasks),
            task_id=task_id, inserted=inserted,
            rows_total=stats["rows_inserted"])

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

    for i, task in enumerate(all_tasks, 1):
        if cancel_event and cancel_event.is_set():
            return stats
        task_id = task.get("task_id") or task.get("id") or ""
        if not task_id:
            continue
        try:
            results = mr_api.fetch_scan_results(task_id)
        except Exception as e:
            log("scan_results_error", i, len(all_tasks),
                task_id=task_id, error=str(e))
            continue
        translations = results.get("translations") or []
        inserted = _bulk_upsert(
            conn,
            translations,
            source_kind="scan",
            default_task_id=task_id,
            default_project_id=task.get("project_id", ""),
            task_created_at=task.get("created_at") or "",
        )
        stats["tasks_seen"] += 1
        stats["rows_inserted"] += inserted
        conn.commit()
        log("scan_results", i, len(all_tasks),
            task_id=task_id, inserted=inserted,
            rows_total=stats["rows_inserted"])

    _set_meta(conn, "scan_total_tasks", str(total_scan))
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

        if not (cancel_event and cancel_event.is_set()):
            _set_meta(conn, "last_sync_at", sync_started)

        return {
            "mode": "incremental",
            "since": last_sync,
            "started_at": sync_started,
            "mr": mr_stats,
            "scan": scan_stats,
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
        if not (cancel_event and cancel_event.is_set()):
            _set_meta(conn, "last_sync_at", sync_started)
            _set_meta(conn, "last_full_sync_at", sync_started)
        return {
            "mode": "full",
            "started_at": sync_started,
            "mr": mr_stats,
            "scan": scan_stats,
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
    """每个 project_id 的 opus_id 数 / 文件指纹数 / 最近一次新增时间。"""
    init_db(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT
                project_id,
                COALESCE(MAX(alias), '') AS alias,
                COUNT(DISTINCT opus_id)                  AS opus_count,
                COUNT(DISTINCT alias || ':' || path_hash) AS path_count,
                MAX(first_seen)                          AS last_added,
                COUNT(DISTINCT target_language)          AS lang_count,
                COUNT(*)                                 AS row_count
            FROM opus_index
            WHERE project_id != ''
            GROUP BY project_id
            ORDER BY opus_count DESC
            """
        )
        return [dict(r) for r in cur.fetchall()]


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


def get_recent_additions(
    limit: int = 50,
    db_path: str | None = None,
) -> list[dict]:
    """最近 N 个首次出现的 opus_id（按 first_seen 倒序）。"""
    init_db(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT
                opus_id, alias, path_hash, logical_key,
                project_id, target_language, source_kind, mr_iid,
                first_seen
            FROM opus_index
            ORDER BY first_seen DESC
            LIMIT ?
            """,
            (limit,),
        )
        # 同 opus_id 多语言会展开多行，去重保留首条
        seen: set[str] = set()
        out: list[dict] = []
        for r in cur.fetchall():
            if r["opus_id"] in seen:
                continue
            seen.add(r["opus_id"])
            out.append(dict(r))
            if len(out) >= limit:
                break
        return out


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
]
