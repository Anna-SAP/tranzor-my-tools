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

import getpass as _getpass
import hashlib as _hashlib
import json
import os
import re
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
    -- Review Worklist 用的 GitLab MR 状态字段（PR-A 新增）：
    -- 都是 nullable，旧任务 / 没 GitLab token 时保持 NULL；
    -- get_worklist_items 把 NULL 当 "未知" 处理而不是 0/false。
    mr_state         TEXT,                        -- "opened" | "merged" | "closed" | "locked"
    mr_merge_status  TEXT,                        -- "can_be_merged" | "cannot_be_merged" | "unchecked"
    mr_draft         INTEGER,                     -- 1/0 — Draft / Work-in-Progress
    mr_upvotes       INTEGER,                     -- 👍 数；近似 approvals 数
    mr_downvotes     INTEGER,
    mr_updated_at    TEXT,                        -- GitLab 侧 MR 最近更新时间 (ISO)；
                                                  -- 用作"最近活动"代理，估算 merge 紧迫度
    mr_web_url       TEXT,                        -- 一键跳转 Tranzor / GitLab 的 URL
    PRIMARY KEY (task_id, source_kind)
);
CREATE INDEX IF NOT EXISTS ix_task_checks_kind    ON task_checks(source_kind);
CREATE INDEX IF NOT EXISTS ix_task_checks_created ON task_checks(task_created_at);
-- ``ix_task_checks_mr_state`` 不在这里创建 —— 它依赖 PR-A 才加进来的
-- ``mr_state`` 列。fresh-DB 路径下 CREATE TABLE 已经带了该列，但升级路径
-- 下 executescript 跑在 ALTER 之前，会找不到列。统一把"依赖新列的索引"
-- 都放到 init_db 的 ALTER 段之后，详见下方注释。

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

-- Review log (PR-B): "Lillian 在 MR A 看过这条 (opus_id, lang, text) 的
-- 翻译" → 同样的 (opus_id, lang, text) 在 MR B / MR C 出现时自动算已审。
--
-- 主键用 (opus_id, target_language, text_hash, reviewer)：text_hash 让
-- 译文一旦被改动就视为新行（不会"上次看过老版本的 fr-FR 翻译，新版本
-- 也被当成已审"），reviewer 让多人评审场景下记录不互相覆盖。
--
-- ``decision`` / ``notes`` 是 v0.3 留的位（"ok" / "needs_fix" /
-- "skipped"）；PR-B 阶段总是写 NULL，UI 暂时只显示"reviewed / not"。
CREATE TABLE IF NOT EXISTS review_log (
    opus_id           TEXT NOT NULL,
    target_language   TEXT NOT NULL,
    text_hash         TEXT NOT NULL,
    reviewer          TEXT NOT NULL,
    reviewed_at       TEXT NOT NULL,
    decision          TEXT,
    notes             TEXT,
    -- Provenance — 不参与主键，仅供分析"用户在哪个 task / 哪类来源标的"。
    task_id           TEXT,
    source_kind       TEXT,
    PRIMARY KEY (opus_id, target_language, text_hash, reviewer)
);
CREATE INDEX IF NOT EXISTS ix_review_log_reviewer
    ON review_log(reviewer);
CREATE INDEX IF NOT EXISTS ix_review_log_opus_lang
    ON review_log(opus_id, target_language);
"""


def init_db(db_path: str | None = None) -> None:
    """创建 schema（幂等 / 升级安全）。"""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        # 升级路径：SQLite 不支持 IF NOT EXISTS 的 ADD COLUMN，所以每列
        # 各试一次，OperationalError 是"列已存在"的预期信号，吞掉即可。
        # 顺序：先有 mr_labels（v0.2 引入），再有 PR-A 加的 GitLab MR 状态
        # 字段。新列追加在表尾不影响既有查询。
        for ddl in (
            "ALTER TABLE task_checks ADD COLUMN mr_labels TEXT",
            "ALTER TABLE task_checks ADD COLUMN mr_state TEXT",
            "ALTER TABLE task_checks ADD COLUMN mr_merge_status TEXT",
            "ALTER TABLE task_checks ADD COLUMN mr_draft INTEGER",
            "ALTER TABLE task_checks ADD COLUMN mr_upvotes INTEGER",
            "ALTER TABLE task_checks ADD COLUMN mr_downvotes INTEGER",
            "ALTER TABLE task_checks ADD COLUMN mr_updated_at TEXT",
            "ALTER TABLE task_checks ADD COLUMN mr_web_url TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        # 依赖新列的索引：必须在 ALTER 之后创建，否则升级路径下找不到列。
        # ``IF NOT EXISTS`` 让 fresh-DB 路径也安全地走这条逻辑。
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_task_checks_mr_state "
            "ON task_checks(mr_state)"
        )


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
    mr_state_info: dict | None = None,
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

    # PR-A: GitLab MR 状态字段。整个 mr_state_info=None 时全部落 NULL，
    # ON CONFLICT 段的 COALESCE 保护已有值，"这轮拉不到"绝不破坏上轮成果。
    info = mr_state_info or {}
    mr_state = info.get("state")
    mr_merge_status = info.get("merge_status")
    mr_draft = info.get("draft")
    mr_draft_int = (
        None if mr_draft is None else (1 if bool(mr_draft) else 0)
    )
    mr_upvotes = _safe_int(info.get("upvotes"))
    mr_downvotes = _safe_int(info.get("downvotes"))
    mr_updated_at = info.get("updated_at")
    mr_web_url = info.get("web_url")

    conn.execute(
        """
        INSERT INTO task_checks(
            task_id, source_kind, project_id, project_name, mr_iid, task_name,
            task_status, final_score_avg, total_issues, total_rows,
            task_created_at, fetched_at, mr_labels,
            mr_state, mr_merge_status, mr_draft, mr_upvotes, mr_downvotes,
            mr_updated_at, mr_web_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            mr_labels       = COALESCE(excluded.mr_labels, task_checks.mr_labels),
            mr_state        = COALESCE(excluded.mr_state,        task_checks.mr_state),
            mr_merge_status = COALESCE(excluded.mr_merge_status, task_checks.mr_merge_status),
            mr_draft        = COALESCE(excluded.mr_draft,        task_checks.mr_draft),
            mr_upvotes      = COALESCE(excluded.mr_upvotes,      task_checks.mr_upvotes),
            mr_downvotes    = COALESCE(excluded.mr_downvotes,    task_checks.mr_downvotes),
            mr_updated_at   = COALESCE(excluded.mr_updated_at,   task_checks.mr_updated_at),
            mr_web_url      = COALESCE(excluded.mr_web_url,      task_checks.mr_web_url)
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
            mr_state, mr_merge_status, mr_draft_int,
            mr_upvotes, mr_downvotes, mr_updated_at, mr_web_url,
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
    mr_info_fetcher=None,
) -> None:
    """HTTP 走线程池、SQLite 写入留在主线程的成熟模式（详见 opus_id_monitor）。

    ``mr_info_fetcher`` (optional):
        ``Callable[[dict], tuple[list[str] | None, dict | None]]``。
    每个 task fetch translations 完成后，在同一个 worker 上紧接着调一次，
    返回 ``(labels, state_info)``：

    - ``labels`` — GitLab MR labels (``list[str]`` / ``None``)。``None`` 让
      :func:`_persist_task_results` 保留旧值（``COALESCE`` 不冲）；``[]``
      显式表示"已尝试拉过、确实没 labels"。
    - ``state_info`` — GitLab MR 状态字典 (PR-A 引入)，键：``state`` /
      ``merge_status`` / ``draft`` / ``upvotes`` / ``downvotes`` /
      ``updated_at`` / ``web_url``。``None`` 同样走 COALESCE 保留旧值。

    仅 MR sync 路径传入，Scan / Legacy 留空。
    """
    if not tasks:
        return

    total = len(tasks)
    completed = 0

    def _worker(task):
        if cancel_event and cancel_event.is_set():
            return task, None, None, None
        try:
            translations = fetch_fn(task)
        except Exception as e:
            return task, e, None, None
        labels = None
        state_info = None
        if mr_info_fetcher is not None:
            try:
                labels, state_info = mr_info_fetcher(task)
            except Exception:
                # 副信息任何拉取失败都吞掉，绝不让 sync 因为它而失败。
                labels, state_info = None, None
        return task, translations, labels, state_info

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
                task, results, mr_labels, mr_state_info = future.result()
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
                    mr_state_info=mr_state_info,
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
        # 后端 /tasks 按 created_at DESC（最新在前）返回。一旦本页出现
        # 早于 since_iso 的任务，本页剩余 + 后续所有页都更旧，没必要再
        # 翻——直接收尾。否则每次增量同步都会把上千条历史任务全翻一遍
        # 才停（offset+page_size>=total），让"同步并刷新"无谓地慢上几十
        # 个列表往返。created 为空的任务视为"未知时间"按在窗口内处理，
        # 不触发提前结束。
        reached_window_end = False
        for t in batch:
            created = t.get("created_at") or ""
            if since_iso and created and created < since_iso:
                reached_window_end = True
                break
            all_tasks.append(t)
        if reached_window_end:
            break
        if offset + page_size >= total:
            break
        offset += page_size
        log("mr_list", len(all_tasks), total)

    log("mr_results", 0, len(all_tasks))
    # MR sync 比 scan/legacy 多做一件事：顺手抓 GitLab MR labels + 状态字段
    # 入库。PR-A 起 fetcher 同时返回 (labels, state_info)，让 Review
    # Worklist 能算出 merge 紧迫度。同一个 GitLab MR 详情请求复用 cache，
    # 不会引入额外 round-trip。
    mr_info_fetcher = _build_mr_info_fetcher()
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
        mr_info_fetcher=mr_info_fetcher,
    )
    return stats


# GitLab MR 详情里我们会落库的字段。集中在这里方便日后扩展（比如想加
# pipeline_status / assignees）只改一处。
_MR_STATE_FIELDS = (
    "state", "merge_status", "draft", "upvotes", "downvotes",
    "updated_at", "web_url",
)


def _extract_mr_state(mr: dict) -> dict:
    """从 GitLab MR 完整字典中只挑我们关心的字段。"""
    return {k: mr.get(k) for k in _MR_STATE_FIELDS}


def _build_mr_info_fetcher():
    """Wire a fresh GitLab client and return a per-task info fetcher.

    Returns ``None`` if GitLab isn't configured (no token) — without auth
    we'd 401 every request and burn the API budget for nothing. In that
    case Review Worklist degrades gracefully (no merge-risk column data;
    skip-label badge still won't appear).

    The closure captures one ``GitLabClient`` so the in-memory MR cache
    is shared across the whole sync run (rare cross-task MR repeats hit
    cache instead of GitLab).

    Returns a ``Callable[[dict], tuple[list[str] | None, dict | None]]``.
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
            # tuple (e.g. ad-hoc retranslations). Returning (None, None)
            # preserves any previously cached values via COALESCE upserts.
            return (None, None)
        try:
            mr = client.get_merge_request(project_id, mr_iid)
        except Exception:
            return (None, None)
        labels = [str(x) for x in (mr.get("labels") or []) if x]
        return (labels, _extract_mr_state(mr))

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
        # 同 _sync_mr_tasks：scan 端点也按 created_at DESC 返回，越过
        # since_iso 即可停止翻页（见那里的详细说明）。
        reached_window_end = False
        for t in batch:
            created = t.get("created_at") or ""
            if since_iso and created and created < since_iso:
                reached_window_end = True
                break
            all_tasks.append(t)
        if reached_window_end:
            break
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


# Review Worklist 的「同步并刷新」首次回看窗口。Worklist 只关心近期待
# merge 的 MR，没必要在刷新时全量拉 7000+ 历史 task（会卡几分钟、GitLab
# 调用爆炸）。要建完整基线请用 Tranzor Checks 的 Full re-sync。
_MR_INCREMENTAL_FIRST_WINDOW_DAYS = 14


def sync_mr_incremental(progress_callback=None,
                        cancel_event: threading.Event | None = None) -> dict:
    """只增量同步 MR Pipeline 任务 —— Review Worklist 的「同步并刷新」用。

    用**独立水位** ``last_mr_sync_at``，刻意不碰 :func:`sync_incremental`
    的 ``last_sync_at``。否则 Worklist 推进了公共水位，下次 Tranzor
    Checks 的三类增量 sync 会漏掉本窗口内的 scan / legacy 任务。

    首次（无 ``last_mr_sync_at``）只回看
    :data:`_MR_INCREMENTAL_FIRST_WINDOW_DAYS` 天。

    返回 ``_sync_mr_tasks`` 的 stats dict（tasks_seen / rows_total /
    issues_inserted）。
    """
    init_db()
    with _connect() as conn:
        since_iso = _get_meta(conn, "last_mr_sync_at")
        if not since_iso:
            since_iso = (
                datetime.now(timezone.utc)
                - timedelta(days=_MR_INCREMENTAL_FIRST_WINDOW_DAYS)
            ).isoformat(timespec="seconds")
        stats = _sync_mr_tasks(
            conn, since_iso=since_iso,
            progress_callback=progress_callback,
            cancel_event=cancel_event)
        # 仅在没被取消时推进水位 —— 取消意味着这次没拉全，下次应从
        # 同一 since 重来。
        if not (cancel_event and cancel_event.is_set()):
            _set_meta(conn, "last_mr_sync_at",
                      datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return stats


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


# ---------------------------------------------------------------------------
# Review Worklist —— Language Lead 每日的唯一入口
# ---------------------------------------------------------------------------
# Worklist 把 MR Pipeline 任务按"merge 紧迫度 × 翻译问题数"做加权排序，
# 替代 Lillian 现在"打开 MR Pipeline → 肉眼挑 → 一个个开"的工作流。
#
# 排序公式（详见 compute_merge_urgency / get_worklist_items 文档）：
#
#   priority = w_urg·merge_urgency
#            + w_zh ·has_zh_issue
#            + w_oth·has_other_issue
#            - w_rev·already_reviewed   # PR-B 接入；PR-A 阶段恒为 0
#
# 中文优先体现在 ``w_zh > w_oth``；项目不参与权重（每个项目都重要，
# 团队共识）。
# ---------------------------------------------------------------------------

# 中文 locale —— 计算 has_zh_issue 时按这些前缀匹配 ``target_language``。
# 用 startswith 比硬编码 ``"zh-CN"`` 更稳：Tranzor 历史上偶尔会传
# ``zh-Hans-CN`` 这类长格式，统一前缀匹配就 OK 了。
_CHINESE_LOCALE_PREFIXES = ("zh",)

# 用 fr / de / es 作为次重要语种 —— 与 Lillian 在群里描述的策略一致
# （中文为主，其次法德西）。其它语种走 "other" 权重。
_SECONDARY_LOCALE_PREFIXES = ("fr", "de", "es")

# Skip-translate 标签：与 Tranzor Platform 后端的 ``SKIP_TRANSLATE_LABEL``
# 默认值保持一致。Worklist 会把带它的 MR 一律压到底（urgency = 0）
# 并以灰色标记，避免占据视觉黄金位。
_SKIP_TRANSLATE_LABEL = "skip-translate"


def compute_merge_urgency(
    *,
    state: str | None,
    merge_status: str | None,
    draft: int | None,
    upvotes: int | None,
    updated_at_iso: str | None,
    labels: list[str] | None,
    now_utc: datetime | None = None,
) -> tuple[int, str]:
    """Pure function — 算一个 MR 的 merge 紧迫度。

    输出 ``(score, tier)``：

    - ``score`` ∈ ``[0, 10]``：UI 排序键，越高越紧迫。
    - ``tier`` ∈ ``{"red", "amber", "green", "unknown", "grey"}``：UI
      显示用桶。
      * ``red``：score ≥ 8，state 明确 ``opened``。即将合并。
      * ``amber``：score ∈ [4, 7]，state 明确 ``opened``。今日要看。
      * ``green``：score < 4，state 明确 ``opened``。可慢慢看。
      * ``unknown``：state 缺失（None / 空 / 旧 sync 没拉过 GitLab）。
        仍按公式打分参与排序，但 UI 加灰底警示——告诉 Lillian 这条
        的紧迫度是"基于不完整数据估算的"，建议 Sync 一次再看。
      * ``grey``：state ∈ {merged, closed, locked} 或带 skip-translate
        标签——彻底无需 review，默认隐藏。

    估算逻辑（保守、可解释、不依赖未实现的字段）：

    1. **state 终态**：``merged`` / ``closed`` / ``locked`` 已经定局，
       ``(0, "grey")``。
    2. **skip-translate label**：显式跳过，``(0, "grey")``。
    3. **draft**：草稿 MR 仍可能 merge 但概率极低，``-5``。
    4. **upvotes (👍)**：approval 的近似信号，每个 +1.5、上限 +4.5。
    5. **recency**：``updated_at`` 越新越紧迫——
       ``≤ 1h → +3``、``≤ 6h → +2``、``≤ 24h → +1``、``> 7d → -1``。
       updated_at 缺失（GitLab 没拉到）当作"未知"，不加不减。

    基线 ``+5``，让"任何 open MR"都至少落 amber 以上的起点，再让上面
    几条规则拨高/拨低。这种"先有底再加减"的实现比"从 0 累加" 更鲁棒
    ——单字段缺失时不会假性归零。

    历史 note：v0.1 把 state=None 也压 grey 当"安全保守"，但实测下用
    户经常在 GitLab token 缺失 / 旧 sync 字段未填的情况下打开 Worklist，
    结果整屏空白；改成 ``unknown`` 显示更符合"我要看到东西"的预期。

    ``now_utc`` 可显式注入，便于单测。生产路径默认 ``datetime.now``。
    """
    # state 优先：定局的 MR 不参与排序。
    s = (state or "").lower()
    if s in ("merged", "closed", "locked"):
        return (0, "grey")

    # skip-translate 优先：显式跳过的 MR 直接落底，避免霸占 worklist 头部。
    lbls = [str(x).lower() for x in (labels or [])]
    if _SKIP_TRANSLATE_LABEL in lbls:
        return (0, "grey")

    # state 是否明确 ``opened``。其他值（None / "" / "未知") 走打分但
    # tier 单独标记为 unknown。
    state_known_opened = (s == "opened")

    score = 5.0
    if draft:
        score -= 5

    upv = upvotes if isinstance(upvotes, int) and upvotes > 0 else 0
    score += min(upv, 3) * 1.5

    # Recency —— 用 updated_at 估算。GitLab MR 的 updated_at 包含 push /
    # comment / label 等任何变更，足够代表"活跃度"。
    if updated_at_iso:
        now = now_utc or datetime.now(timezone.utc)
        try:
            # GitLab 返回带 Z 的 ISO 串，python 直接 fromisoformat 不接受 Z
            # （3.11+ 才支持）。这里手工换 +00:00 保兼容。
            ts = datetime.fromisoformat(
                updated_at_iso.replace("Z", "+00:00")
            )
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_h = max(0.0, (now - ts).total_seconds() / 3600.0)
            if age_h <= 1:
                score += 3
            elif age_h <= 6:
                score += 2
            elif age_h <= 24:
                score += 1
            elif age_h > 24 * 7:
                score -= 1
        except (ValueError, TypeError):
            pass

    score_i = max(0, min(10, int(round(score))))
    if not state_known_opened:
        # 数据不全 → 强制 unknown，UI 单独标色，给 Lillian 一个"Sync
        # 一下再看"的 hint。score 仍参与排序，让她至少能看到有 issue
        # 的 MR 排前面。
        tier = "unknown"
    elif score_i >= 8:
        tier = "red"
    elif score_i >= 4:
        tier = "amber"
    else:
        tier = "green"
    return (score_i, tier)


def _issue_lang_breakdown(conn, task_id: str, source_kind: str) -> tuple[int, int, int]:
    """返回 (zh_issues, secondary_issues, other_issues)。"""
    rows = conn.execute(
        "SELECT target_language, COUNT(*) AS n "
        "FROM check_issues "
        "WHERE task_id = ? AND source_kind = ? "
        "GROUP BY target_language",
        (task_id, source_kind),
    ).fetchall()
    zh = sec = oth = 0
    for r in rows:
        lang = (r["target_language"] or "").lower()
        n = int(r["n"] or 0)
        if not lang:
            oth += n
        elif any(lang.startswith(p) for p in _CHINESE_LOCALE_PREFIXES):
            zh += n
        elif any(lang.startswith(p) for p in _SECONDARY_LOCALE_PREFIXES):
            sec += n
        else:
            oth += n
    return zh, sec, oth


def get_worklist_items(
    *,
    limit: int = 50,
    include_grey: bool = False,
    include_fully_reviewed: bool = False,
    reviewer: str | None = None,
    known_term_names_lower: frozenset[str] | set[str] | None = None,
    now_utc: datetime | None = None,
) -> list[dict]:
    """Review Worklist 主表的数据源。

    返回按 ``priority`` 降序排列的 MR 列表。每条包含足够 UI 直接渲染的
    字段：``priority`` / ``tier`` / ``zh_issues`` / ``secondary_issues`` /
    ``other_issues`` / ``mr_web_url`` / ``reviewed_count`` /
    ``unreviewed_count`` / ``fully_reviewed`` / ``unregistered_terms``
    (PR-C) 等。

    Args:
        limit: 上限。默认 50 —— Lillian 一天大概看 8-12 条，50 给她足够
            的下拉空间但不会一次拉几百条。
        include_grey: 是否包含 ``tier="grey"`` 的（merged / skip-translate）。
            默认 ``False``——Worklist 是"今天要看的"，已经定局的 MR 没必要
            霸占视觉空间。PR-D 的 watchdog 可以打开它做"事后回滚清单"。
        include_fully_reviewed: 是否包含"该 reviewer 名下所有 issue 都已审"
            的 MR。默认 ``False``，让 Worklist 真正只显示"还没看完"的。
        reviewer: 计算"已审 issue 数"用的 reviewer ID。``None`` 时回退到
            :func:`_default_reviewer`（``$TRANZOR_REVIEWER`` 优先于
            ``getpass.getuser()``）。多人共用同一 DB 时，传不同 reviewer
            得到各自的 worklist 视图。
        known_term_names_lower: PR-C 的 🆕 列数据源 —— 已登记术语集合
            （小写）。``None`` 表示"跳过 🆕 检查"，每行 ``unregistered_terms``
            落空列表。生产路径由 GUI 传入
            :func:`tranzor_terminology.load_known_term_names_lower` 的结果；
            单测可注入任意集合。
        now_utc: 可注入的当前时间，单测专用。

    数据源：仅 ``source_kind = 'mr'`` 的 ``task_checks`` 行。Scan / File
    任务有它们自己的入口（Scan Tasks tab / File Translation tab）。
    """
    init_db()
    reviewer_id = reviewer or _default_reviewer()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM task_checks
            WHERE source_kind = 'mr'
            """,
        ).fetchall()

        # zh / 次级语种 / 其它语种的 issue 数：用一次 GROUP BY 拿回来，避免
        # 行内再各 SELECT 一遍。任务多时这一步开销不可忽视。
        lang_rows = conn.execute(
            """
            SELECT task_id, source_kind, target_language, COUNT(*) AS n
            FROM check_issues
            WHERE source_kind = 'mr'
            GROUP BY task_id, source_kind, target_language
            """,
        ).fetchall()

        # PR-B 跨 MR 去重：取出 reviewer 名下所有已审 (opus_id, lang, hash)
        # 一次拉回来，行内 in-memory 去比 issue 是否被 reviewer 看过。
        # 比每行 EXISTS 子查询快得多，也方便后续 PR 用同一 dict 做"该 MR
        # 中已被在哪条 MR 看过的"提示。
        reviewed_keys: set[tuple[str, str, str]] = set()
        if reviewer_id:
            for rr in conn.execute(
                "SELECT opus_id, target_language, text_hash "
                "FROM review_log WHERE reviewer = ?",
                (reviewer_id,),
            ).fetchall():
                reviewed_keys.add((
                    rr["opus_id"], rr["target_language"], rr["text_hash"],
                ))

        # 拉出每个 MR 的 issue 详情，行内算 reviewed_count 和未登记术语。
        # source_text 也拉了——PR-C 的 🆕 列要在它上面做正则扫描。
        issue_rows = conn.execute(
            """
            SELECT task_id, source_kind, opus_id, target_language,
                   translated_text, source_text
            FROM check_issues
            WHERE source_kind = 'mr'
            """,
        ).fetchall()

    lang_map: dict[tuple[str, str], tuple[int, int, int]] = {}
    for r in lang_rows:
        key = (r["task_id"], r["source_kind"])
        zh, sec, oth = lang_map.get(key, (0, 0, 0))
        lang = (r["target_language"] or "").lower()
        n = int(r["n"] or 0)
        if not lang:
            oth += n
        elif any(lang.startswith(p) for p in _CHINESE_LOCALE_PREFIXES):
            zh += n
        elif any(lang.startswith(p) for p in _SECONDARY_LOCALE_PREFIXES):
            sec += n
        else:
            oth += n
        lang_map[key] = (zh, sec, oth)

    # MR → (reviewed_count, total_count)
    review_map: dict[tuple[str, str], tuple[int, int]] = {}
    # MR → ordered list[str] of unique unregistered terms (PR-C). Insertion
    # order preserved to keep the GUI display stable across reloads — same
    # data in, same output. dict-as-ordered-set is the cheap way.
    unregistered_map: dict[tuple[str, str], dict[str, None]] = {}

    # PR-C 的 🆕 计算 —— 只有 caller 传 known_term_names_lower 时才跑，
    # 单测 / 老调用方不传时跳过整段，省一次模块 import 和 regex 扫。
    do_unregistered = known_term_names_lower is not None
    if do_unregistered:
        import unregistered_terms as _ut
    for r in issue_rows:
        key = (r["task_id"], r["source_kind"])
        rc, tc = review_map.get(key, (0, 0))
        tc += 1
        hk = (r["opus_id"], r["target_language"],
              _hash_translation(r["translated_text"]))
        if hk in reviewed_keys:
            rc += 1
        review_map[key] = (rc, tc)

        if do_unregistered:
            src = r["source_text"]
            if src:
                bucket = unregistered_map.setdefault(key, {})
                for term in _ut.extract_unregistered(
                    src, known_term_names_lower,
                ):
                    bucket.setdefault(term, None)

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        # mr_labels 解析（与 get_aggregated_issues 同口径）
        raw_labels = d.get("mr_labels") or ""
        try:
            labels = json.loads(raw_labels) if raw_labels else []
            if not isinstance(labels, list):
                labels = []
        except Exception:
            labels = []
        d["mr_labels_list"] = [str(x) for x in labels]

        score, tier = compute_merge_urgency(
            state=d.get("mr_state"),
            merge_status=d.get("mr_merge_status"),
            draft=d.get("mr_draft"),
            upvotes=d.get("mr_upvotes"),
            updated_at_iso=d.get("mr_updated_at"),
            labels=d["mr_labels_list"],
            now_utc=now_utc,
        )
        d["merge_urgency"] = score
        d["merge_tier"] = tier

        zh, sec, oth = lang_map.get(
            (d["task_id"], d["source_kind"]), (0, 0, 0),
        )
        d["zh_issues"] = zh
        d["secondary_issues"] = sec
        d["other_issues"] = oth
        d["total_lang_issues"] = zh + sec + oth

        # PR-B: 已审 / 未审 issue 数。MR 没有 issue 时 (0, 0) → 不算
        # "全部 reviewed"——下面的 fully_reviewed 判断特意只在 total > 0
        # 时生效，避免把"0 issue 的 clean MR"误隐藏。
        reviewed_count, total_issue_count = review_map.get(
            (d["task_id"], d["source_kind"]), (0, 0),
        )
        d["reviewed_count"] = reviewed_count
        d["total_issue_count"] = total_issue_count
        d["unreviewed_count"] = max(0, total_issue_count - reviewed_count)
        d["fully_reviewed"] = (
            total_issue_count > 0 and reviewed_count >= total_issue_count
        )

        # PR-C: 未登记术语清单。``[]`` 表示要么没有要么 caller 没传 glossary。
        # 调用方分得清 ``do_unregistered`` 状态，这里不引入第三态字段。
        terms = unregistered_map.get(
            (d["task_id"], d["source_kind"]), {},
        )
        d["unregistered_terms"] = list(terms.keys())
        d["unregistered_term_count"] = len(terms)

        # 综合 priority：tier×权重 是主轴，issue 数把同 tier 内排序细分。
        # PR-B 起把 reviewed issue 的权重降低 —— 已审的 issue 不再为这条
        # MR 在排序上加分；未审仍按语言权重计算。
        unreviewed_share = (
            d["unreviewed_count"] / total_issue_count
            if total_issue_count else 1.0
        )
        d["priority"] = (
            score * 10
            + (zh * 3 + sec * 1 + oth * 0.3) * unreviewed_share
            - (100 if d["fully_reviewed"] else 0)
        )
        out.append(d)

    if not include_grey:
        out = [d for d in out if d["merge_tier"] != "grey"]
    if not include_fully_reviewed:
        out = [d for d in out if not d["fully_reviewed"]]

    out.sort(key=lambda x: (
        -x["priority"], x.get("task_created_at") or "",
    ))
    return out[:limit]


# ---------------------------------------------------------------------------
# Review Log —— "Lillian 已经在 MR A 看过这条 (opus_id, lang, text)"
# ---------------------------------------------------------------------------
# 跨 MR 去重的核心：同一行翻译片段（按 (opus_id, target_language,
# text_hash) 去重）一旦被某个 reviewer 标过，再出现在别的 MR 时同样算
# "已审"。priority 公式里 unreviewed_share 就是这个数据来源。
#
# 设计要点：
# - text_hash 用 sha1 —— 不抗碰撞攻击但远超过去重需求；同时长度可控、
#   适合做 SQLite 主键的一部分。
# - reviewer 默认从 $TRANZOR_REVIEWER 取，回退到本机用户名。Anna 不
#   希望 Lillian 配 Profile 这种重资产，所以这层是隐式的。
# - decision / notes 现阶段全部 NULL；UI 不暴露这俩字段。留位置是因为
#   PR-C 的"加入待登记术语"和 PR-D 的 watchdog 都可能需要标记决策类型。


def _default_reviewer() -> str:
    """决定 review_log 里 reviewer 列写什么 ID。

    优先 ``$TRANZOR_REVIEWER`` 环境变量，方便 Lillian 在不同机器（家里
    / 公司）保持身份一致；回退到 ``getpass.getuser()`` —— 在她自己的
    Windows 上就是"lillian.ding"或类似的本机用户名，足够区分她和别人。
    都不可用时返回 ``"unknown"``，让 review_log 仍能写入（不丢数据）。
    """
    val = os.environ.get("TRANZOR_REVIEWER", "").strip()
    if val:
        return val
    try:
        return _getpass.getuser() or "unknown"
    except Exception:
        return "unknown"


def _hash_translation(text: str | None) -> str:
    """规范化 + sha1 一段翻译文本，给 review_log 做去重键。

    ``None`` 和空串都映射到固定 ``"empty"`` 哈希——空翻译本身就是一种
    "已审过" 状态，统一对待。日常文本走 utf-8 + sha1 取前 16 hex 位
    （8 字节 = 64 bit 碰撞空间，远超过单 reviewer 一辈子 review 的条数）。
    """
    if not text:
        return "empty"
    return _hashlib.sha1(
        str(text).encode("utf-8"), usedforsecurity=False,
    ).hexdigest()[:16]


def mark_reviewed(
    *,
    opus_id: str,
    target_language: str,
    translated_text: str | None,
    reviewer: str | None = None,
    task_id: str | None = None,
    source_kind: str | None = None,
    decision: str | None = None,
    notes: str | None = None,
) -> None:
    """把一条 (opus_id, lang, text) 标记为某 reviewer 已审。

    INSERT OR REPLACE —— 重复标同一条不会报错，仅刷新 ``reviewed_at``。
    decision / notes 现阶段无 UI 写入路径，留接口。
    """
    init_db()
    rid = reviewer or _default_reviewer()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    th = _hash_translation(translated_text)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO review_log(
                opus_id, target_language, text_hash, reviewer,
                reviewed_at, decision, notes, task_id, source_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(opus_id, target_language, text_hash, reviewer)
            DO UPDATE SET
                reviewed_at = excluded.reviewed_at,
                decision    = COALESCE(excluded.decision,    review_log.decision),
                notes       = COALESCE(excluded.notes,       review_log.notes),
                task_id     = COALESCE(excluded.task_id,     review_log.task_id),
                source_kind = COALESCE(excluded.source_kind, review_log.source_kind)
            """,
            (opus_id, target_language, th, rid, now_iso,
             decision, notes, task_id, source_kind),
        )


def unmark_reviewed(
    *,
    opus_id: str,
    target_language: str,
    translated_text: str | None,
    reviewer: str | None = None,
) -> bool:
    """撤回一条已审记录。返回是否真的删除了一行（``False`` 表示之前就
    没有记录，并非错误）。"""
    init_db()
    rid = reviewer or _default_reviewer()
    th = _hash_translation(translated_text)
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM review_log "
            "WHERE opus_id = ? AND target_language = ? "
            "AND text_hash = ? AND reviewer = ?",
            (opus_id, target_language, th, rid),
        )
        return (cur.rowcount or 0) > 0


def mark_task_reviewed(
    task_id: str,
    *,
    source_kind: str = "mr",
    reviewer: str | None = None,
) -> int:
    """把一个 MR 任务里所有 issue 一次标为已审——Worklist 行右键
    "Mark MR reviewed" 用。

    返回新写入 / 更新的 review_log 行数。批量内部用一条 INSERT 多值，
    比逐条调 ``mark_reviewed`` 快得多。
    """
    init_db()
    rid = reviewer or _default_reviewer()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT opus_id, target_language, translated_text "
            "FROM check_issues WHERE task_id = ? AND source_kind = ?",
            (task_id, source_kind),
        ).fetchall()
        if not rows:
            return 0
        payload = [
            (
                r["opus_id"] or "",
                r["target_language"] or "",
                _hash_translation(r["translated_text"]),
                rid, now_iso, None, None, task_id, source_kind,
            )
            for r in rows
        ]
        conn.executemany(
            """
            INSERT INTO review_log(
                opus_id, target_language, text_hash, reviewer,
                reviewed_at, decision, notes, task_id, source_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(opus_id, target_language, text_hash, reviewer)
            DO UPDATE SET reviewed_at = excluded.reviewed_at,
                          task_id     = excluded.task_id,
                          source_kind = excluded.source_kind
            """,
            payload,
        )
        return len(payload)


def unmark_task_reviewed(
    task_id: str,
    *,
    source_kind: str = "mr",
    reviewer: str | None = None,
) -> int:
    """批量撤回某 task 名下该 reviewer 的所有 review 记录。返回删除行数。

    取所有 (opus_id, lang, hash) 一次 DELETE 比反复调 ``unmark_reviewed``
    快；用于"撤回 Mark MR reviewed"。
    """
    init_db()
    rid = reviewer or _default_reviewer()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT opus_id, target_language, translated_text "
            "FROM check_issues WHERE task_id = ? AND source_kind = ?",
            (task_id, source_kind),
        ).fetchall()
        if not rows:
            return 0
        keys = [
            (r["opus_id"] or "", r["target_language"] or "",
             _hash_translation(r["translated_text"]), rid)
            for r in rows
        ]
        cur = conn.executemany(
            "DELETE FROM review_log "
            "WHERE opus_id = ? AND target_language = ? "
            "AND text_hash = ? AND reviewer = ?",
            keys,
        )
        return cur.rowcount or 0


# ---------------------------------------------------------------------------
# PR-D: merge watchdog 用的写回 / 事件持久化辅助
# ---------------------------------------------------------------------------
def update_mr_state_fields(
    *,
    task_id: str,
    source_kind: str = "mr",
    state: str | None = None,
    upvotes: int | None = None,
    updated_at: str | None = None,
    web_url: str | None = None,
) -> None:
    """部分更新一个 task_checks 行的 GitLab MR 状态字段。

    与 :func:`_persist_task_results` 不同：那是 sync 路径走的全量 upsert，
    要 task 数据齐备；watchdog 只想刷新四个动态字段，task 必然已存在。
    用 ``COALESCE`` 让传 ``None`` 的字段保留旧值——传谁覆盖谁。
    """
    if not task_id:
        return
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE task_checks
            SET mr_state      = COALESCE(?, mr_state),
                mr_upvotes    = COALESCE(?, mr_upvotes),
                mr_updated_at = COALESCE(?, mr_updated_at),
                mr_web_url    = COALESCE(?, mr_web_url)
            WHERE task_id = ? AND source_kind = ?
            """,
            (state, upvotes, updated_at, web_url, task_id, source_kind),
        )


# sync_meta 里存事件历史的 key。PR-E 的 digest 用同一个 key 读。
_MERGE_EVENTS_META_KEY = "merge_watchdog_events"

# 事件环形缓冲容量。watchdog 每条事件就是"一次 state 转换"，正常一天
# 量级在几十；200 足以覆盖一周左右，不会让 sync_meta 这一行 JSON 过大。
_MERGE_EVENTS_RING_SIZE = 200


def append_merge_events(events: list[dict]) -> None:
    """把 watchdog 检测到的事件追加进 sync_meta 的事件环。

    新事件追到末尾；超过 ``_MERGE_EVENTS_RING_SIZE`` 时丢掉最旧的。
    多线程安全靠 SQLite 自身（_connect 取的连接独立、写有内部锁）。
    """
    if not events:
        return
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT value FROM sync_meta WHERE key = ?",
            (_MERGE_EVENTS_META_KEY,),
        ).fetchone()
        existing: list[dict] = []
        if cur and cur["value"]:
            try:
                existing = json.loads(cur["value"]) or []
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        existing.extend(events)
        if len(existing) > _MERGE_EVENTS_RING_SIZE:
            existing = existing[-_MERGE_EVENTS_RING_SIZE:]
        conn.execute(
            "INSERT INTO sync_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_MERGE_EVENTS_META_KEY,
             json.dumps(existing, ensure_ascii=False)),
        )


def get_merge_events(*, limit: int | None = None) -> list[dict]:
    """读取 watchdog 事件环（最近的在最后）。``limit`` 给 PR-E 的 digest
    控制取多少；默认全量。"""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT value FROM sync_meta WHERE key = ?",
            (_MERGE_EVENTS_META_KEY,),
        ).fetchone()
    if not cur or not cur["value"]:
        return []
    try:
        items = json.loads(cur["value"]) or []
        if not isinstance(items, list):
            return []
    except Exception:
        return []
    if limit is not None:
        items = items[-int(limit):]
    return items


def get_review_summary(reviewer: str | None = None) -> dict:
    """返回 reviewer 的已审统计。UI 主要用 ``today`` / ``total`` 两个数：
    挂在 Worklist 顶部当"今日已审 X / 累计 Y"小徽章。"""
    init_db()
    rid = reviewer or _default_reviewer()
    today_iso = datetime.now(timezone.utc).date().isoformat()
    with _connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM review_log WHERE reviewer = ?",
            (rid,),
        ).fetchone()["n"]
        today = conn.execute(
            "SELECT COUNT(*) AS n FROM review_log "
            "WHERE reviewer = ? AND reviewed_at >= ?",
            (rid, today_iso),
        ).fetchone()["n"]
    return {"reviewer": rid, "today": int(today), "total": int(total)}
