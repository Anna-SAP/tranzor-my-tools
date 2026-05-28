"""
Tranzor Checks — 本地 SQLite 缓存 + Tranzor API 增量同步（任务检查/错误聚合视角）
=============================================================================

为 TranzorExporter 提供"全量 Task Checks 状态 + 细粒度错误关键词"的数据层。
与 :mod:`opus_id_monitor` 互补：

- opus_id_monitor 关注 **opus_id 层面**的资产盘点；
- tranzor_checks 关注 **issue 层面**的质量信号 —— 哪条术语、哪个占位符、
  哪个语种在哪个 task 出错了，让 QA / Language Lead 快速判断"误报"。

为什么独立一张 DB？
    现有 ``opus_index.db`` 的主键是 (opus_id, target_language, task_id)，
    每行最多一条记录。但一个 (opus_id, lang, task) 可以同时触发多种 check
    （Terminology + Parameter Format 都中招），主键模式会把它们挤掉。
    把 issue 视角放在独立的 ``checks_index.db`` 既不污染既有缓存、又能让
    schema 自由演化（v0.2 加 ignore_flag / cluster_id 都不影响主线）。

参考 Tranzor 后端 schema（**只读参考**，不可修改）：

- ``app/models/evaluation.py`` — error_category ∈ {Accuracy, Fluency,
  Terminology, Consistency, Locale Convention, None}，外加 reason 自由文本
- ``app/evaluation/common_check.py`` — Variable/Number Mismatch 通用检查
- Tranzor UI 的 "Terminology Inconsistency" / "Parameter Format" 过滤器
  实际上是基于 error_category + reason 文本启发式推断出的标签

暴露给上层（UI tab）的入口：

    - :func:`init_db` — 创建 schema（幂等）
    - :func:`sync_full` / :func:`sync_incremental` — 三类任务全/增量同步
    - :func:`get_summary` — 顶部统计卡数据
    - :func:`get_aggregated_issues` — 聚合表（按 error_type/lang/keyword）
    - :func:`get_issues_for_group` — 双击下钻：某一聚合组的全部 issue
    - :func:`get_issue_detail` — 详情面板单条 issue 的完整字段
    - :func:`classify_issue` — 独立的分类工具（测试友好）
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable

# 复用既有 HTTP 客户端 —— 不重复造鉴权 / 重试 / Session 池。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_mr_pipeline as mr_api


# ---------------------------------------------------------------------------
# 并行抓取上限 —— 与 opus_id_monitor 保持一致，确保后端不被两个面板
# 同时同步时打爆。如果未来要把两边合一，调一处即可。
# ---------------------------------------------------------------------------
MAX_FETCH_WORKERS = 8


# ---------------------------------------------------------------------------
# 数据库位置 — 用户主目录下独立 DB，与 opus_index.db 解耦
# ---------------------------------------------------------------------------
def _default_db_path() -> str:
    home = os.path.expanduser("~")
    base = os.path.join(home, ".tranzor_exporter")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "checks_index.db")


@contextmanager
def _connect(db_path: str | None = None):
    """SQLite 连接 —— 与 opus_id_monitor._connect 完全相同的并发模型。

    WAL + 无全局锁；多读 + 单写由 SQLite 内核排队；每个调用方独立连接。
    """
    path = db_path or _default_db_path()
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema —— 两张表：task_checks（任务级摘要）+ check_issues（行级细节）
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_checks (
    task_id          TEXT NOT NULL,
    source_kind      TEXT NOT NULL,    -- 'mr' | 'scan' | 'file'
    project_id       TEXT,
    project_name     TEXT,
    mr_iid           INTEGER,
    task_name        TEXT,
    task_status      TEXT,
    final_score_avg  REAL,
    total_issues     INTEGER NOT NULL DEFAULT 0,
    total_rows       INTEGER NOT NULL DEFAULT 0,  -- 该任务行数，便于"通过率"统计
    task_created_at  TEXT,
    fetched_at       TEXT NOT NULL,
    mr_labels        TEXT,                        -- GitLab MR labels (JSON list);
                                                  -- NULL = 未尝试拉过；"" / "[]" = 已尝试但无 labels；
                                                  -- 用于识别 SKIP_TRANSLATE_LABEL（默认 ``skip-translate``）
    PRIMARY KEY (task_id, source_kind)
);
CREATE INDEX IF NOT EXISTS ix_task_checks_kind    ON task_checks(source_kind);
CREATE INDEX IF NOT EXISTS ix_task_checks_created ON task_checks(task_created_at);

CREATE TABLE IF NOT EXISTS check_issues (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id             TEXT NOT NULL,
    source_kind         TEXT NOT NULL,
    opus_id             TEXT,
    target_language     TEXT,
    error_type          TEXT NOT NULL,           -- 显示用标签："Terminology Inconsistency" / "Parameter Format" / ...
    error_category      TEXT,                    -- Tranzor 原始 error_category（归一化）
    error_keyword       TEXT,                    -- 提取出的关键词："transcript" / "{(runNumber)}" / "(unparsed)"
    error_keyword_norm  TEXT,                    -- 关键词小写归一化形式，排序/聚合用
    source_text         TEXT,
    translated_text     TEXT,
    final_score         REAL,
    reason              TEXT,                    -- 完整 eval_reason 文本
    iteration           INTEGER,
    fetched_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_issues_task_kind   ON check_issues(task_id, source_kind);
CREATE INDEX IF NOT EXISTS ix_issues_error_type  ON check_issues(error_type);
CREATE INDEX IF NOT EXISTS ix_issues_keyword     ON check_issues(error_keyword_norm);
CREATE INDEX IF NOT EXISTS ix_issues_language    ON check_issues(target_language);

CREATE TABLE IF NOT EXISTS sync_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_db(db_path: str | None = None) -> None:
    """创建 schema（幂等 / 升级安全）。"""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        # 升级路径：旧 DB 没有 ``mr_labels`` 列。SQLite 不支持 IF NOT
        # EXISTS 的 ADD COLUMN，所以靠 except 兜底——已存在时 OperationalError
        # 是预期的，不打印、不向上抛。
        try:
            conn.execute("ALTER TABLE task_checks ADD COLUMN mr_labels TEXT")
        except sqlite3.OperationalError:
            pass


# ---------------------------------------------------------------------------
# 错误分类与关键词提取（v0.2 规则）
# ---------------------------------------------------------------------------
# v0.1 用启发式 regex 抽具体 token，结果在真实数据上大量落 "(unparsed)"
# —— Tranzor 的 reason 文本表达远比截图样本里那两种模板灵活。
#
# v0.2 改成用户给出的更稳健的规则：
#   - Terminology Inconsistency: 取描述里**第一次出现的双引号片段**。这条
#     规则同时覆盖了 "Source matched the term \"X\""（term 名）和 "the
#     product feature name \"X\""（产品名）两种自由表达，几乎不漏。
#   - Parameter Format: 直接把**完整 Eval Reason 文本**当关键字。reason 本身
#     就很短（"Missing named parameter: '$Brand_ShortName'" 一类），整段
#     当聚合 key 既保留全部上下文、又让相同/相似的 reason 自然落到同一组，
#     方便用户一眼扫到"同一参数反复出错"。
#
# 占位符 / 反引号的细粒度 token 抽取仍保留下来 —— 在分类决策（"是否归到
# Parameter Format"）里继续有用，只是不再作为最终的 error_keyword。
_FIRST_QUOTED = re.compile(
    # 兼容直引号 "..."、弯引号 “...”、单弯引号 ‘...’
    r'["“‘]([^"”’]{1,200})["”’]'
)
_PARAM_PATTERNS = (
    re.compile(r"`([^`]{1,80})`"),
    re.compile(r"(\{\{[^}]{1,60}\}\}|\{[^{}]{1,60}\})"),
    re.compile(r"(%\([^)]{1,40}\)[sdif]|%[sdif])"),
    re.compile(r"(\{__rc[a-z0-9-]+\})", re.IGNORECASE),
)
# 关键词暗示是 Parameter Format 问题（即便 error_category=Consistency 也归到这一类）
_PARAM_HINT = re.compile(
    r"\b(spacing|placeholder|variable|parameter|format|missing\s+space|extra\s+space|"
    r"missing\s+named\s+parameter|number\s+mismatch|url\s+mismatch|email)\b",
    re.IGNORECASE)
# 关键词暗示是 Terminology Inconsistency 问题
_TERM_HINT = re.compile(
    r"\b(term[s]?\b|glossary|terminolog\w*|preferred\s+translation|product\s+feature\s+name)",
    re.IGNORECASE)

# error_keyword 在 DB 里的硬上限 —— 既给 SQLite 留缓存空间、也让 GROUP BY
# 不至于因为一条几 KB 的 reason 把整个聚合表撑爆。
_MAX_KEYWORD_LEN = 240


def _norm_category(category) -> str:
    """归一化 error_category；None / "None" / "" 统一返回 ""。"""
    if category in (None, "", "None"):
        return ""
    return str(category).strip()


def _first_quoted_phrase(reason_text: str) -> str | None:
    """从 reason 中抽第一对双引号包裹的内容，去首尾空白后返回。"""
    m = _FIRST_QUOTED.search(reason_text)
    if not m:
        return None
    phrase = (m.group(1) or "").strip()
    return phrase or None


def classify_issue(error_category, reason, source_text=None) -> tuple[str, str]:
    """根据 (error_category, reason) 推断 (error_type, error_keyword)。

    Returns:
        (error_type, error_keyword)
        - error_type: 显示用的细分标签，与 Tranzor UI 过滤器一致
        - error_keyword: 用于排序/聚合的关键字

    决策顺序（与 v0.1 一致）：
      1. Parameter Format —— hint 词 / 反引号 / 占位符任一命中即归入此类；
         关键词 = 完整 reason 文本（截断至 ``_MAX_KEYWORD_LEN``）
      2. Terminology Inconsistency —— cat ∈ (Terminology, Consistency) 或
         TERM_HINT 命中；关键词 = reason 中第一对双引号包裹的片段；
         若 reason 没有任何双引号，退化为 reason 前 80 字符（仍可读、可聚合）
      3. 其他显式 category 透传；关键词 = reason 前 80 字符
      4. 完全无评估信号 —— "Other" + "(unparsed)"（调用方应已过滤）
    """
    reason_text = str(reason or "")
    cat = _norm_category(error_category)
    reason_stripped = reason_text.strip()

    # 1) Parameter Format —— hint 或 placeholder 任一命中
    if _PARAM_HINT.search(reason_text) or any(
            p.search(reason_text) for p in _PARAM_PATTERNS):
        # 用户偏好：关键字 = 完整 Eval Reason；让相同 reason 自然聚合到同组
        kw = reason_stripped[:_MAX_KEYWORD_LEN] if reason_stripped else "(unparsed)"
        return ("Parameter Format", kw)

    # 2) Terminology Inconsistency —— cat 或 hint 任一命中
    if cat in ("Terminology", "Consistency") or _TERM_HINT.search(reason_text):
        phrase = _first_quoted_phrase(reason_text)
        if phrase:
            return ("Terminology Inconsistency", phrase[:_MAX_KEYWORD_LEN])
        # reason 完全没有双引号时退化为 reason 前 80 字符 —— 仍比 "(unparsed)"
        # 信息量大得多，便于人工识别真实情形（如"术语缺失"这类无引用 reason）。
        fallback = reason_stripped[:80]
        return ("Terminology Inconsistency",
                fallback if fallback else "(unparsed)")

    # 3) 其他显式大类透传
    if cat:
        snippet = reason_stripped[:80]
        return (cat, snippet or "(unparsed)")

    # 4) 完全无评估信号
    return ("Other", "(unparsed)")


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


def get_last_sync_at() -> str | None:
    """供 UI 状态条用。"""
    with _connect() as conn:
        return _get_meta(conn, "last_sync_at")


# ---------------------------------------------------------------------------
# translation → issue 行 提取
# ---------------------------------------------------------------------------
def _translation_has_issue(t: dict) -> bool:
    """判定一条 translation 是否构成 issue。

    判据（任一命中即视为有问题）：
      - error_category 非空且非 "None"
      - reason 非空且不是 "OK" / "Pass" / 空白
    """
    cat = _norm_category(t.get("error_category"))
    if cat:
        return True
    reason = str(t.get("reason") or t.get("eval_reason") or "").strip()
    if not reason:
        return False
    return reason.lower() not in {"ok", "pass", "passed", "no error", "n/a", "-"}


def _extract_issues(translations: Iterable[dict]) -> list[dict]:
    """把 translation 列表筛 + 分类成 issue 行。

    一条 translation 在 v0.1 最多产生一条 issue（按主 reason 分类）。
    后续若 Tranzor 暴露多 issue/translation 的结构（如 issues: [...] 子列表），
    可以在这里展开循环、保持调用方不变。
    """
    out = []
    for t in translations:
        if not _translation_has_issue(t):
            continue
        reason = t.get("reason") or t.get("eval_reason") or ""
        cat_raw = t.get("error_category")
        error_type, keyword = classify_issue(cat_raw, reason, t.get("source_text"))
        out.append({
            "opus_id": str(t.get("opus_id") or "").strip(),
            "target_language": str(t.get("target_language") or "").strip(),
            "error_type": error_type,
            "error_category": _norm_category(cat_raw),
            "error_keyword": keyword,
            "error_keyword_norm": (keyword or "").lower(),
            "source_text": (t.get("source_text") or "")[:4096],
            "translated_text": (t.get("translated_text") or "")[:4096],
            "final_score": _safe_float(t.get("final_score")),
            "reason": (reason or "")[:4096],
            "iteration": _safe_int(t.get("iteration")),
        })
    return out


def _safe_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v):
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 落库：单任务的 task_checks 摘要 + check_issues 明细
# ---------------------------------------------------------------------------
def _persist_task_results(
    conn,
    *,
    task: dict,
    source_kind: str,
    translations: list[dict],
    mr_labels: list[str] | None = None,
) -> tuple[int, int]:
    """把一个 task 的 results 持久化。

    返回 (rows_total, issues_inserted)。
    "Zero Tolerance for Missing" 的关键：哪怕 issues=0 也要插入 task_checks，
    让用户看到"这个任务全部 pass"而不是"没同步"。

    采用先 DELETE 后 INSERT 的方式确保单任务可重入：同步同一个任务两次时，
    旧 issue 不会与新 issue 并存。task_checks 用 INSERT OR REPLACE。
    """
    task_id = str(task.get("task_id") or task.get("id") or "").strip()
    if not task_id:
        return (0, 0)

    issues = _extract_issues(translations)
    rows_total = len(list(translations))
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 计算 final_score 平均（仅对有评分的行）
    scores = [_safe_float(t.get("final_score")) for t in translations]
    scores = [s for s in scores if s is not None]
    avg_score = round(sum(scores) / len(scores), 2) if scores else None

    # 任务级状态：MR/legacy/scan API 字段名不一，做容错
    task_status = (task.get("status")
                   or task.get("task_status")
                   or "").lower() or None

    # ``mr_labels`` 序列化策略：
    # - None  → 旧行保留旧值（INSERT 路径下落 NULL）。允许"先入库再补 labels"的二阶段。
    # - []    → 落 "[]" 字符串，明确表达"已尝试拉过，确实没 labels"。
    # - list  → JSON 串。读侧 json.loads 后判断成员。
    mr_labels_json = (
        None if mr_labels is None
        else json.dumps(list(mr_labels), ensure_ascii=False)
    )

    conn.execute(
        """
        INSERT INTO task_checks(
            task_id, source_kind, project_id, project_name, mr_iid, task_name,
            task_status, final_score_avg, total_issues, total_rows,
            task_created_at, fetched_at, mr_labels
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id, source_kind) DO UPDATE SET
            project_id      = excluded.project_id,
            project_name    = excluded.project_name,
            mr_iid          = excluded.mr_iid,
            task_name       = excluded.task_name,
            task_status     = excluded.task_status,
            final_score_avg = excluded.final_score_avg,
            total_issues    = excluded.total_issues,
            total_rows      = excluded.total_rows,
            task_created_at = excluded.task_created_at,
            fetched_at      = excluded.fetched_at,
            -- 只在新值非 NULL 时覆盖；这样"拉 labels 失败"不会冲掉上一次成功值
            mr_labels       = COALESCE(excluded.mr_labels, task_checks.mr_labels)
        """,
        (
            task_id, source_kind,
            task.get("project_id") or "",
            task.get("project_name") or task.get("task_name") or "",
            _safe_int(task.get("merge_request_iid")),
            task.get("task_name") or "",
            task_status,
            avg_score,
            len(issues),
            rows_total,
            task.get("created_at") or "",
            now_iso,
            mr_labels_json,
        ),
    )

    # check_issues 重新写入：先删后插。issues 列表可能为空（全部 pass）。
    conn.execute(
        "DELETE FROM check_issues WHERE task_id = ? AND source_kind = ?",
        (task_id, source_kind),
    )
    if issues:
        conn.executemany(
            """
            INSERT INTO check_issues(
                task_id, source_kind, opus_id, target_language,
                error_type, error_category, error_keyword, error_keyword_norm,
                source_text, translated_text, final_score, reason,
                iteration, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    task_id, source_kind,
                    i["opus_id"], i["target_language"],
                    i["error_type"], i["error_category"],
                    i["error_keyword"], i["error_keyword_norm"],
                    i["source_text"], i["translated_text"],
                    i["final_score"], i["reason"],
                    i["iteration"], now_iso,
                )
                for i in issues
            ],
        )

    return (rows_total, len(issues))


# ---------------------------------------------------------------------------
# 并行抓取共享逻辑 —— 与 opus_id_monitor._drain_results_into_db 同构
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
    mr_label_fetcher=None,
) -> None:
    """HTTP 走线程池、SQLite 写入留在主线程的成熟模式（详见 opus_id_monitor）。

    ``mr_label_fetcher`` (optional): ``Callable[[dict], list[str] | None]``。
    每个 task fetch translations 完成后，在同一个 worker 上紧接着调一次，
    返回 GitLab MR labels。失败返回 ``None`` 让 ``_persist_task_results``
    保留旧值（``COALESCE`` 不冲）；返回 ``[]`` 显式表示"已尝试拉过、确实
    没 labels"。仅 MR sync 路径传入，Scan / Legacy 留空。
    """
    if not tasks:
        return

    total = len(tasks)
    completed = 0

    def _worker(task):
        if cancel_event and cancel_event.is_set():
            return task, None, None
        try:
            translations = fetch_fn(task)
        except Exception as e:
            return task, e, None
        labels = None
        if mr_label_fetcher is not None:
            try:
                labels = mr_label_fetcher(task)
            except Exception:
                # labels 是装饰性增量数据：任何拉取失败都吞掉，绝不让 sync
                # 因为副信息缺失而失败。``None`` → 保留旧值（见 COALESCE）。
                labels = None
        return task, translations, labels

    pool = ThreadPoolExecutor(
        max_workers=MAX_FETCH_WORKERS,
        thread_name_prefix=f"checks-{source_kind}",
    )
    try:
        futures = [pool.submit(_worker, t) for t in tasks]
        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                break

            try:
                task, results, mr_labels = future.result()
            except Exception as e:
                log(f"{log_stage}_error", completed, total, error=str(e))
                continue

            if results is None:
                continue

            try:
                task_id = str(task.get("task_id") or task.get("id") or "").strip()
                if isinstance(results, Exception):
                    log(f"{log_stage}_error", completed + 1, total,
                        task_id=task_id, error=str(results))
                    completed += 1
                    continue

                # results shape：MR/Scan 是 {translations: [...]}, legacy 直接是列表
                if isinstance(results, dict):
                    translations = results.get("translations") or []
                elif isinstance(results, list):
                    translations = results
                else:
                    translations = []

                rows, issues = _persist_task_results(
                    conn,
                    task=task,
                    source_kind=source_kind,
                    translations=translations,
                    mr_labels=mr_labels,
                )
                stats["tasks_seen"] += 1
                stats["rows_total"] += rows
                stats["issues_inserted"] += issues
                completed += 1
                conn.commit()
                log(log_stage, completed, total,
                    task_id=task_id, issues=issues,
                    rows_total=stats["rows_total"])
            except Exception as per_task_err:
                # 单任务任何意外都只 skip，不让整个 sync 死
                log(f"{log_stage}_error", completed + 1, total,
                    error=f"per-task-fail: {per_task_err!r}")
                completed += 1
                continue
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# MR Pipeline 同步
# ---------------------------------------------------------------------------
def _sync_mr_tasks(
    conn, *, since_iso: str | None, progress_callback=None,
    cancel_event: threading.Event | None = None,
) -> dict:
    log = progress_callback or (lambda *a, **kw: None)
    stats = {"tasks_seen": 0, "rows_total": 0, "issues_inserted": 0}

    log("mr_list", 0, 0)
    all_tasks: list[dict] = []
    offset = 0
    page_size = 100
    while True:
        if cancel_event and cancel_event.is_set():
            return stats
        total, batch = mr_api.fetch_mr_tasks(
            status="completed", limit=page_size, offset=offset)
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
        log("mr_list", len(all_tasks), total)

    log("mr_results", 0, len(all_tasks))
    # MR sync 比 scan/legacy 多做一件事：顺手抓 GitLab MR labels 入库，让
    # 用户能在 GUI 里一眼识别"哪些 MR 被打了 skip-translate label 而被
    # Tranzor Platform 跳过翻译"。失败容忍策略由 _drain_results_into_db
    # 内的 worker 实现：单 MR 拉 labels 失败不影响主翻译数据持久化。
    mr_label_fetcher = _build_mr_label_fetcher()
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
        mr_label_fetcher=mr_label_fetcher,
    )
    return stats


def _build_mr_label_fetcher():
    """Wire a fresh GitLab client and return a per-task labels fetcher.

    Returns ``None`` if GitLab isn't configured (no token) — without auth
    we'd 401 every request and burn the API budget for nothing. In that
    case the GUI simply won't show skip badges; ``mr_labels`` stays NULL
    in DB and downstream code treats it as "未知".

    The closure captures one ``GitLabClient`` so the in-memory MR cache
    is shared across the whole sync run (rare cross-task MR repeats hit
    cache instead of GitLab).
    """
    try:
        import gitlab_client as _gc  # local import: keeps cold start cheap
    except Exception:
        return None
    try:
        client = _gc.GitLabClient()
    except Exception:
        return None
    if not client.has_token():
        return None

    def _fetch(task):
        mr_iid = _safe_int(task.get("merge_request_iid"))
        project_id = task.get("project_id") or ""
        if not mr_iid or not project_id:
            # Some MR-pipeline tasks come back without a usable (project, iid)
            # tuple (e.g. ad-hoc retranslations). Returning None preserves any
            # previously cached labels via COALESCE in the upsert.
            return None
        return client.fetch_mr_labels(project_id, mr_iid)

    return _fetch


# ---------------------------------------------------------------------------
# Scan 同步
# ---------------------------------------------------------------------------
def _sync_scan_tasks(
    conn, *, since_iso: str | None, progress_callback=None,
    cancel_event: threading.Event | None = None,
) -> dict:
    log = progress_callback or (lambda *a, **kw: None)
    stats = {"tasks_seen": 0, "rows_total": 0, "issues_inserted": 0}

    log("scan_list", 0, 0)
    all_tasks: list[dict] = []
    offset = 0
    page_size = 100
    while True:
        if cancel_event and cancel_event.is_set():
            return stats
        try:
            total, batch = mr_api.fetch_scan_tasks(
                status="completed", limit=page_size, offset=offset)
        except Exception as e:
            log("scan_list_error", 0, 0, error=str(e))
            break
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
    return stats


# ---------------------------------------------------------------------------
# File Translation (legacy) 同步
# ---------------------------------------------------------------------------
def _sync_legacy_tasks(
    conn, *, since_iso: str | None, progress_callback=None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """File Translation 用 ``/legacy/tasks`` + ``/legacy/tasks/{id}/translations``。

    legacy API 的 fetch_legacy_translations_quality 一次只返回一页，
    全量需要分页累积；为了让 _drain_results_into_db 透明，封装成
    list[translation] 返回。
    """
    log = progress_callback or (lambda *a, **kw: None)
    stats = {"tasks_seen": 0, "rows_total": 0, "issues_inserted": 0}

    log("legacy_list", 0, 0)
    try:
        all_tasks_raw = mr_api.fetch_all_legacy_tasks_for_quality(
            status="Completed")
    except Exception as e:
        log("legacy_list_error", 0, 0, error=str(e))
        return stats

    all_tasks = []
    for t in all_tasks_raw:
        created = t.get("created_at") or ""
        if since_iso and created and created < since_iso:
            continue
        # legacy 的 id 字段名是 ``task_id``（部分接口也用 id）
        if not (t.get("task_id") or t.get("id")):
            continue
        all_tasks.append(t)

    log("legacy_results", 0, len(all_tasks))

    def _fetch_legacy(task):
        tid = str(task.get("task_id") or task.get("id") or "").strip()
        # 走 fetch_all_legacy_translations_quality，它内部会自动:
        #   1) 分页累积
        #   2) 对 UNS 长文本任务的 truncated preview 拉取完整正文
        #      （见 tranzor_truncation.hydrate_truncated_entries / TRAN-161）
        # 注意：fetch_all_legacy_translations_quality 已带 100k 兜底之外的
        # while-until-total 行为；超大任务由调用方上层并发控制。
        return mr_api.fetch_all_legacy_translations_quality(tid)

    _drain_results_into_db(
        all_tasks,
        fetch_fn=_fetch_legacy,
        conn=conn,
        source_kind="file",
        log=log,
        log_stage="legacy_results",
        cancel_event=cancel_event,
        stats=stats,
    )
    return stats


# ---------------------------------------------------------------------------
# 对外：sync_full / sync_incremental
# ---------------------------------------------------------------------------
def sync_full(progress_callback=None,
              cancel_event: threading.Event | None = None) -> dict:
    """重新拉全部 completed 任务 + issues。

    用于首次同步或确认缓存漂移时。耗时 5-10 分钟级别（取决于后端任务量）。
    """
    init_db()
    overall = {"mr": {}, "scan": {}, "file": {}}
    with _connect() as conn:
        overall["mr"] = _sync_mr_tasks(
            conn, since_iso=None,
            progress_callback=progress_callback,
            cancel_event=cancel_event)
        if cancel_event and cancel_event.is_set():
            return overall
        overall["scan"] = _sync_scan_tasks(
            conn, since_iso=None,
            progress_callback=progress_callback,
            cancel_event=cancel_event)
        if cancel_event and cancel_event.is_set():
            return overall
        overall["file"] = _sync_legacy_tasks(
            conn, since_iso=None,
            progress_callback=progress_callback,
            cancel_event=cancel_event)
        _set_meta(conn, "last_sync_at",
                  datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return overall


def reclassify_existing_issues(progress_callback=None,
                                batch_size: int = 2000) -> dict:
    """对本地 check_issues 表里所有行**重跑 classify_issue**，原地更新
    error_type / error_keyword / error_keyword_norm，**不重新调 Tranzor API**。

    诞生背景：v0.1 的关键词提取规则在真实数据上漏抓很多，留下大量
    "(unparsed)" 标签。v0.2 规则升级后，与其逼用户再花数小时跑一次
    Full re-sync，不如在本地直接刷一遍 —— 几秒到几十秒搞定。

    Args:
        progress_callback: callable(stage, current, total, **kw)
        batch_size: 每批读 / 写多少行，权衡内存与 commit 频率。

    Returns:
        {"updated": <int>, "total": <int>}
    """
    log = progress_callback or (lambda *a, **kw: None)
    init_db()
    updated = 0
    total = 0
    with _connect() as conn:
        total_row = conn.execute(
            "SELECT COUNT(*) AS n FROM check_issues").fetchone()
        total = int(total_row["n"]) if total_row else 0
        log("reclassify", 0, total)

        offset = 0
        while True:
            rows = conn.execute(
                "SELECT id, error_category, reason FROM check_issues "
                "ORDER BY id LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break

            updates = []
            for r in rows:
                et, kw = classify_issue(r["error_category"], r["reason"])
                updates.append((et, kw, (kw or "").lower(), r["id"]))

            conn.executemany(
                "UPDATE check_issues SET error_type=?, error_keyword=?, "
                "error_keyword_norm=? WHERE id=?",
                updates,
            )
            conn.commit()

            updated += len(updates)
            offset += len(rows)
            log("reclassify", updated, total)

    return {"updated": updated, "total": total}


def sync_incremental(progress_callback=None,
                     cancel_event: threading.Event | None = None) -> dict:
    """只拉自 ``last_sync_at`` 之后创建的任务。秒级 - 分钟级别。"""
    init_db()
    overall = {"mr": {}, "scan": {}, "file": {}}
    with _connect() as conn:
        since_iso = _get_meta(conn, "last_sync_at")
        overall["mr"] = _sync_mr_tasks(
            conn, since_iso=since_iso,
            progress_callback=progress_callback,
            cancel_event=cancel_event)
        if cancel_event and cancel_event.is_set():
            return overall
        overall["scan"] = _sync_scan_tasks(
            conn, since_iso=since_iso,
            progress_callback=progress_callback,
            cancel_event=cancel_event)
        if cancel_event and cancel_event.is_set():
            return overall
        overall["file"] = _sync_legacy_tasks(
            conn, since_iso=since_iso,
            progress_callback=progress_callback,
            cancel_event=cancel_event)
        _set_meta(conn, "last_sync_at",
                  datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return overall


# ---------------------------------------------------------------------------
# 查询：顶部统计卡
# ---------------------------------------------------------------------------
def get_summary() -> dict:
    """返回顶部 4 卡数据 + 上次同步时间。

    所有数字都允许为 0（首次没同步时）。UI 端不要因为 None / 0 而崩。
    """
    init_db()
    with _connect() as conn:
        row_t = conn.execute(
            "SELECT COUNT(*) AS n FROM task_checks").fetchone()
        row_i = conn.execute(
            "SELECT COUNT(*) AS n FROM check_issues").fetchone()
        row_et = conn.execute(
            "SELECT COUNT(DISTINCT error_type) AS n FROM check_issues").fetchone()
        row_lang = conn.execute(
            "SELECT COUNT(DISTINCT target_language) AS n "
            "FROM check_issues WHERE target_language <> ''").fetchone()
        last_sync = _get_meta(conn, "last_sync_at")
        # 任务通过率（issues=0 视为通过）
        row_pass = conn.execute(
            "SELECT COUNT(*) AS n FROM task_checks WHERE total_issues = 0"
        ).fetchone()
    return {
        "total_tasks": row_t["n"] if row_t else 0,
        "tasks_clean": row_pass["n"] if row_pass else 0,
        "total_issues": row_i["n"] if row_i else 0,
        "error_types": row_et["n"] if row_et else 0,
        "languages": row_lang["n"] if row_lang else 0,
        "last_sync_at": last_sync,
    }


# ---------------------------------------------------------------------------
# 查询：聚合表（核心 UI 数据）
# ---------------------------------------------------------------------------
#: 把"分组里时间最新的那条 task 信息"沿 MAX() 透传出来的分隔符。
#: 用 ASCII 31（Unit Separator）—— 不可能在正常 task_name / reason 里出现，
#: split() 之后能稳定还原 5 段字段。
_LATEST_SEP = "\x1f"


def get_aggregated_issues(
    *,
    error_type: str | None = None,
    language: str | None = None,
    source_kind: str | None = None,
    keyword_substring: str | None = None,
    task_id: str | None = None,
    limit: int = 5000,
) -> list[dict]:
    """按 (error_type, language, keyword) 三维聚合，每组返回最新发生时间与
    最新任务信息，便于 UI 默认"按最新检查时间倒序"展示。

    返回字典字段：
      - error_type / language / error_keyword
      - count           : 命中条数
      - tasks_affected  : distinct task 个数
      - source_kinds    : "mr,scan" 这种逗号串
      - latest_seen     : 该分组里最新的 task_created_at（缺省时回退到 fetched_at）
      - latest_task_name / latest_mr_iid / latest_task_id / latest_source_kind:
        最新那条 task 的元信息，UI 直接拿来渲染"Latest task"列

    Args:
        task_id: 用 substring LIKE 过滤 ``check_issues.task_id``。设计上
            兼容两种使用方式：
              - 用户从群通知复制完整 UUID（36 字符精确匹配）
              - 用户只记得前几位（如 "48e17681"），前缀也能命中
            空串 / None 视为不过滤。

    SQL 技巧：用 ``MAX(timestamp || \\x1f || other_fields)`` 让 SQLite 在
    GROUP BY 时同时携带"时间最新那一行"的所有元字段，避免上层 N+1 查询，
    也不需要 SQLite 3.25+ 的窗口函数。
    """
    init_db()
    where = ["1=1"]
    params: list = []
    if error_type:
        where.append("i.error_type = ?")
        params.append(error_type)
    if language:
        where.append("i.target_language = ?")
        params.append(language)
    if source_kind:
        where.append("i.source_kind = ?")
        params.append(source_kind)
    if keyword_substring:
        where.append("i.error_keyword_norm LIKE ?")
        params.append(f"%{keyword_substring.lower()}%")
    if task_id and task_id.strip():
        where.append("i.task_id LIKE ?")
        params.append(f"%{task_id.strip()}%")

    sep = _LATEST_SEP
    # 用 SQL 参数传 sep（避免 f-string 里转义混乱），下方 placeholder 用 ?。
    sql = f"""
        SELECT
            i.error_type,
            i.target_language AS language,
            i.error_keyword,
            COUNT(*) AS count,
            COUNT(DISTINCT i.task_id || ':' || i.source_kind) AS tasks_affected,
            GROUP_CONCAT(DISTINCT i.source_kind) AS source_kinds,
            MAX(COALESCE(t.task_created_at, i.fetched_at, '')) AS latest_seen,
            MAX(
                COALESCE(t.task_created_at, i.fetched_at, '') || ? ||
                COALESCE(t.task_name, '') || ? ||
                COALESCE(CAST(t.mr_iid AS TEXT), '') || ? ||
                COALESCE(i.task_id, '') || ? ||
                COALESCE(i.source_kind, '') || ? ||
                COALESCE(t.mr_labels, '')
            ) AS _latest_blob
        FROM check_issues i
        LEFT JOIN task_checks t
            ON t.task_id = i.task_id AND t.source_kind = i.source_kind
        WHERE {' AND '.join(where)}
        GROUP BY i.error_type, i.target_language, i.error_keyword
        ORDER BY latest_seen DESC
        LIMIT ?
    """
    # 5 个 sep 占位符在前，原 where 参数中间，limit 在尾。``mr_labels`` 段
    # 追加在末尾让旧 blob 解析继续兼容（split 后 parts[5] 是新字段）。
    final_params: list = [sep, sep, sep, sep, sep, *params, limit]
    with _connect() as conn:
        rows = conn.execute(sql, final_params).fetchall()

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        blob = d.pop("_latest_blob", None) or ""
        parts = blob.split(_LATEST_SEP)
        # parts[0] = latest_seen, parts[1] = task_name, parts[2] = mr_iid,
        # parts[3] = task_id,     parts[4] = source_kind,
        # parts[5] = mr_labels JSON (新增；旧 DB 升级前为空串)
        d["latest_task_name"]   = parts[1] if len(parts) > 1 else ""
        d["latest_mr_iid"]      = parts[2] if len(parts) > 2 else ""
        d["latest_task_id"]     = parts[3] if len(parts) > 3 else ""
        d["latest_source_kind"] = parts[4] if len(parts) > 4 else ""
        # ``latest_mr_labels`` 是 list[str]，调用方直接看成员（避免重复 parse）。
        # 空串 / 无效 JSON 都退化为 []，绝不抛。
        raw_labels = parts[5] if len(parts) > 5 else ""
        try:
            d["latest_mr_labels"] = (
                json.loads(raw_labels) if raw_labels else []
            )
            if not isinstance(d["latest_mr_labels"], list):
                d["latest_mr_labels"] = []
        except Exception:
            d["latest_mr_labels"] = []
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# 查询：聚合组下钻 —— 选中一行后看到具体的 issue 列表
# ---------------------------------------------------------------------------
def get_issues_for_group(
    *,
    error_type: str,
    language: str | None,
    error_keyword: str | None,
    task_id: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """返回某 (error_type, language, keyword) 组合下的全部 issue 行。

    ``task_id`` 与 :func:`get_aggregated_issues` 同义 —— substring LIKE，
    用来让"先按 task 过滤、再选某分组下钻"的工作流保持过滤一致性。
    """
    init_db()
    where = ["i.error_type = ?"]
    params: list = [error_type]
    if language is not None:
        where.append("i.target_language = ?")
        params.append(language)
    if error_keyword is not None:
        where.append("i.error_keyword = ?")
        params.append(error_keyword)
    if task_id and task_id.strip():
        where.append("i.task_id LIKE ?")
        params.append(f"%{task_id.strip()}%")
    sql = f"""
        SELECT i.*, t.project_id, t.project_name, t.mr_iid, t.task_name
        FROM check_issues i
        LEFT JOIN task_checks t
            ON t.task_id = i.task_id AND t.source_kind = i.source_kind
        WHERE {' AND '.join(where)}
        ORDER BY i.final_score ASC, i.task_id
        LIMIT ?
    """
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_issue_detail(issue_id: int) -> dict | None:
    """单条 issue 的完整字段（用于详情面板）。"""
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT i.*, t.project_id, t.project_name, t.mr_iid, t.task_name,
                   t.task_created_at, t.task_status
            FROM check_issues i
            LEFT JOIN task_checks t
                ON t.task_id = i.task_id AND t.source_kind = i.source_kind
            WHERE i.id = ?
            """,
            (issue_id,),
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# 查询：单任务汇总（按通知格式渲染）
# ---------------------------------------------------------------------------
def get_task_summary(task_id: str) -> dict | None:
    """对应"用户从群通知拿到 task UUID → 想看汇总和明细"的工作流。

    输入是 ``check_issues.task_id``（substring LIKE 兼容前缀），返回结构
    刻意与 Tranzor Bot 推送的通知格式对齐 —— 这样 UI 端弹出对话框时
    用户能一眼把"Checks: 16 Variable/Number Mismatch · 15 Terminology
    Inconsistency"对上号。

    Returns:
        None  —— 本地缓存里查无此 task（提示用户先 Sync）
        dict  —— 命中，字段：
          - task_id (完整 UUID，可能在前缀输入下被解析成第一个匹配)
          - source_kind / project_id / project_name / task_name / mr_iid
          - task_status / final_score_avg / task_created_at / fetched_at
          - total_rows / total_issues          —— 与 task_checks 表对齐
          - error_type_counts : OrderedDict[error_type → count]
              （按 count 倒序，便于 UI 直接拼 "16 X · 15 Y" 字符串）
          - issues            : list[dict]  —— 该 task 的全部 issue 行
              （字段同 ``get_issues_for_group`` 的输出）
    """
    if not task_id or not task_id.strip():
        return None
    init_db()
    tid_like = f"%{task_id.strip()}%"

    with _connect() as conn:
        # 1) 先在 task_checks 表里找到唯一匹配；substring 模式下有多个
        #    候选时取 fetched_at 最新的一个（最近一次同步该 task 的记录）。
        meta_row = conn.execute(
            """
            SELECT * FROM task_checks
            WHERE task_id LIKE ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (tid_like,),
        ).fetchone()

        if meta_row is None:
            # task_checks 表里没有，但兜底再去 check_issues 看一眼 ——
            # 极端情况下任务级摘要丢了但 issues 还在，能给用户看一些总比
            # 完全说"找不到"友好。
            cnt = conn.execute(
                "SELECT COUNT(*) AS n FROM check_issues WHERE task_id LIKE ?",
                (tid_like,),
            ).fetchone()
            if not cnt or cnt["n"] == 0:
                return None
            # 构造极简 meta 以让 UI 至少能展示 issues
            meta = {"task_id": task_id.strip(), "source_kind": "",
                    "project_id": "", "project_name": "", "task_name": "",
                    "mr_iid": None, "task_status": None,
                    "final_score_avg": None, "task_created_at": "",
                    "fetched_at": "", "total_rows": 0,
                    "total_issues": int(cnt["n"])}
        else:
            meta = dict(meta_row)

        # 解出真实 task_id（如果用户输的是前缀）
        real_tid = meta["task_id"]
        real_src = meta.get("source_kind") or ""

        # 2) 该 task 下按 error_type 的计数 —— 与通知"Checks: ..."一行对齐
        ct_rows = conn.execute(
            """
            SELECT error_type, COUNT(*) AS n
            FROM check_issues
            WHERE task_id = ? AND (? = '' OR source_kind = ?)
            GROUP BY error_type
            ORDER BY n DESC, error_type
            """,
            (real_tid, real_src, real_src),
        ).fetchall()
        from collections import OrderedDict
        error_type_counts = OrderedDict(
            (r["error_type"], int(r["n"])) for r in ct_rows)

        # 3) 完整 issue 列表（JOIN task_checks 拿任务元字段，与
        #    get_issues_for_group 的形状一致）
        issue_rows = conn.execute(
            """
            SELECT i.*, t.project_id AS t_project_id,
                   t.project_name AS t_project_name,
                   t.mr_iid AS t_mr_iid,
                   t.task_name AS t_task_name
            FROM check_issues i
            LEFT JOIN task_checks t
                ON t.task_id = i.task_id AND t.source_kind = i.source_kind
            WHERE i.task_id = ? AND (? = '' OR i.source_kind = ?)
            ORDER BY i.error_type, i.final_score ASC, i.target_language
            """,
            (real_tid, real_src, real_src),
        ).fetchall()
        issues = [dict(r) for r in issue_rows]

    meta["error_type_counts"] = error_type_counts
    meta["issues"] = issues
    return meta


def format_checks_line(error_type_counts: dict) -> str:
    """把 ``{error_type: count}`` 渲染成与 Tranzor Bot 通知一致的一行字符串。

    例: ``{"Variable/Number Mismatch": 16, "Terminology Inconsistency": 15}``
    →   ``"16 Variable/Number Mismatch · 15 Terminology Inconsistency"``

    没有 issue（全通过）时返回空串，调用方可以渲染 "Pass" 之类的占位。
    """
    if not error_type_counts:
        return ""
    return " · ".join(f"{n} {et}" for et, n in error_type_counts.items())


# ---------------------------------------------------------------------------
# 查询：错误类型 / 语言下拉框可选值
# ---------------------------------------------------------------------------
def get_filter_options() -> dict:
    """UI 顶部筛选器的下拉框选项。"""
    init_db()
    with _connect() as conn:
        types = [r["error_type"] for r in conn.execute(
            "SELECT DISTINCT error_type FROM check_issues "
            "ORDER BY error_type").fetchall()]
        langs = [r["target_language"] for r in conn.execute(
            "SELECT DISTINCT target_language FROM check_issues "
            "WHERE target_language <> '' "
            "ORDER BY target_language").fetchall()]
        kinds = [r["source_kind"] for r in conn.execute(
            "SELECT DISTINCT source_kind FROM check_issues "
            "ORDER BY source_kind").fetchall()]
    return {"error_types": types, "languages": langs, "source_kinds": kinds}
