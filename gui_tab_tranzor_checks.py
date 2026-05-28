"""
Tranzor Checks — GUI Tab
=========================
"全量任务 Checks 状态 + 细粒度错误关键词聚合"的面板。

数据来自本地 SQLite 缓存（``tranzor_checks`` 模块）；首屏不依赖网络。
点 "🔄 立即同步" 才会增量拉 Tranzor 新任务；"全量重建" 用于首次或纠偏。

设计目标（来自需求文档）：
    1. 全量任务覆盖（含 pass 的任务也入库，避免遗漏）
    2. 错误类型 / 语言 / 错误关键词三维呈现
    3. **关键词列可排序** —— 让 QA 一眼归类相似问题、识别误报
    4. 渐进式扩展空间（v0.2 一键忽略、CSV 导出等留 hook）

布局：
    顶部状态条：Sync 按钮 · 上次同步 · 状态文案
    summary 卡：4 张大数字（总任务 / 通过任务 / 总 issue / 涉及语言）
    筛选条：错误类型 · 语言 · 来源 · 关键词模糊搜索
    主表：聚合视图（按 error_type / lang / keyword 分组）—— 列头可排序
    下钻面板：选中一组 → 该组对应的全部 issue 行
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk
from datetime import datetime


# ---------------------------------------------------------------------------
# i18n —— **必须在 ``from export_gui import …`` 之前定义**！
# 详见 gui_tab_opus_id_monitor.py 顶部注释；同一坑不复述。
# ---------------------------------------------------------------------------
STRINGS = {
    "en": {
        "tab_tranzor_checks":        "🩺 Tranzor Checks",
        "tc_sync_now":               "🔄 Sync now",
        "tc_sync_full":              "Full re-sync",
        "tc_sync_cancel":            "Cancel",
        "tc_sync_now_tip":           (
            "Pull only tasks created since the last sync.\n"
            "Fast — use this for routine refreshes.\n\n"
            "First time? Click 'Full re-sync' to build the baseline."),
        "tc_sync_full_tip":          (
            "Re-pull ALL completed MR / Scan / File Translation tasks\n"
            "and rebuild the local checks cache from scratch.\n\n"
            "Required on first run (~5-10 min depending on backend load)."),
        "tc_sync_cancel_tip":        "Abort the in-flight sync.",
        "tc_last_sync":              "{elapsed} since last sync · {time}",
        "tc_last_sync_never":        "Never synced — click 'Full re-sync' first.",
        "tc_elapsed_just_now":       "just now",
        "tc_elapsed_minutes":        "{m}m",
        "tc_elapsed_hours":          "{h}h {m}m",
        "tc_elapsed_days":           "{d}d {h}h {m}m",
        "tc_card_tasks":             "Tasks tracked",
        "tc_card_clean":              "Tasks clean (0 issues)",
        "tc_card_issues":            "Total issues",
        "tc_card_languages":         "Languages affected",
        "tc_card_pass_rate":          "Pass rate · {pct}%",
        "tc_filter_type":            "Error type",
        "tc_filter_lang":            "Language",
        "tc_filter_source":          "Source",
        "tc_filter_keyword":         "Keyword contains…",
        "tc_filter_any":             "(any)",
        "tc_filter_reset":           "Reset",
        "tc_agg_title":              "📊 Issues by error type · language · keyword — newest first · click column to sort",
        "tc_col_source":             "Source",
        "tc_col_error_type":         "Error type",
        "tc_col_language":           "Lang",
        "tc_col_keyword":            "Error keyword",
        "tc_col_count":              "Count",
        "tc_col_tasks":              "Tasks",
        "tc_col_latest_task":        "Latest task",
        "tc_col_latest_seen":        "Checked at",
        "tc_col_age":                "Age",
        "tc_detail_title":           "Selected group — {count} issue(s)",
        "tc_detail_empty":           "Select a row above to see the issues that match.",
        "tc_col_task":               "Task",
        "tc_col_opus":               "OPUS ID",
        "tc_col_score":              "Score",
        "tc_col_source_text":        "Source",
        "tc_col_translation":        "Translation",
        "tc_col_reason":             "Reason",
        "tc_status_idle":            "Idle.",
        "tc_status_syncing":         "Syncing… {stage} {cur}/{total}",
        "tc_status_done":            "✓ Sync done · MR {mr_t}/{mr_i} · Scan {scan_t}/{scan_i} · File {file_t}/{file_i} (tasks/issues)",
        "tc_status_failed":          "❌ {error}",
        "tc_status_cancelled":       "⚠ Sync cancelled",
        "tc_agg_empty":              "Local checks cache is empty — click 'Full re-sync' to populate.",
        "tc_src_mr":                 "MR",
        "tc_src_scan":               "Scan",
        "tc_src_file":                "File",
        "tc_copy":                   "Copy reason",
        "tc_open_tranzor":           "Open in Tranzor ↗",
        "tc_open_tranzor_tip":       "Open this task in the Tranzor platform (uses the platform URL from export_mr_pipeline).",
        "tc_reclassify":             "♻ Re-classify cached",
        "tc_reclassify_tip":         (
            "Re-apply the latest keyword-extraction rules to all rows already in the\n"
            "local cache — no network calls. Use this after upgrading my-tools to\n"
            "pick up rule improvements without re-running Full re-sync."),
        "tc_reclassify_running":     "♻ Re-classifying… {cur}/{total}",
        "tc_reclassify_done":        "✓ Re-classify done · {updated} issue(s) updated",
        # Notification → Checks bridge (task UUID lookup)
        "tc_filter_task":            "Task ID:",
        "tc_task_paste":             "📋 Paste",
        "tc_task_paste_tip":         (
            "Paste a Task UUID from the Tranzor Bot chat notification.\n"
            "The aggregation below will instantly filter to this task only."),
        "tc_task_summary_btn":       "🔎 Task summary",
        "tc_task_summary_tip":       (
            "Open a dialog that shows this task's full check summary in the\n"
            "same shape as the chat notification (e.g. \"16 Variable/Number\n"
            "Mismatch · 15 Terminology Inconsistency\") plus every issue row."),
        "tc_task_filter_no_match":   "task {tid}… (no matching rows in cache)",
        "tc_task_not_found":         "Task {tid}… not in local cache — run Sync first.",
        # Task summary dialog
        "tc_summary_title":          "Task summary · {label}",
        "tc_summary_meta":           (
            "Project: {project}\nTask name: {task_name}\nTask ID: {task_id}\n"
            "Status: {status}  ·  Avg score: {score}  ·  Created: {created}"),
        "tc_summary_checks_label":   "Checks:",
        "tc_summary_checks_pass":    "Pass · all {rows} translation(s) clean",
        "tc_summary_issues_title":   "Issues ({n})",
        "tc_summary_copy_ids":       "📋 Copy OPUS IDs",
        "tc_summary_copy_done":      "✓ Copied {n} OPUS IDs",
        "tc_summary_open_tranzor":   "Open in Tranzor ↗",
        "tc_summary_close":          "Close",
    },
    "zh": {
        "tab_tranzor_checks":        "🩺 Tranzor 检查",
        "tc_sync_now":               "🔄 立即同步",
        "tc_sync_full":              "全量重建",
        "tc_sync_cancel":            "取消",
        "tc_sync_now_tip":           (
            "仅拉取「上次同步之后」新创建的任务，秒级 - 分钟级完成。\n"
            "适合日常刷新。\n\n"
            "首次使用请先点「全量重建」建立基线。"),
        "tc_sync_full_tip":          (
            "重新拉取所有已完成的 MR / Scan / File Translation 任务，\n"
            "从零重建本地 checks 缓存（约 5-10 分钟，取决于后端负载）。"),
        "tc_sync_cancel_tip":        "中止正在进行的同步。已落库数据安全保留。",
        "tc_last_sync":              "距上次同步 {elapsed} · {time}",
        "tc_last_sync_never":        "尚未同步 — 请先点「全量重建」建立基线。",
        "tc_elapsed_just_now":       "刚刚",
        "tc_elapsed_minutes":        "{m} 分钟",
        "tc_elapsed_hours":          "{h} 小时 {m} 分钟",
        "tc_elapsed_days":           "{d} 天 {h} 小时 {m} 分钟",
        "tc_card_tasks":             "纳管任务数",
        "tc_card_clean":              "全通过任务（0 issue）",
        "tc_card_issues":            "Issue 总数",
        "tc_card_languages":         "涉及语言数",
        "tc_card_pass_rate":          "通过率 · {pct}%",
        "tc_filter_type":            "错误类型",
        "tc_filter_lang":            "语言",
        "tc_filter_source":          "来源",
        "tc_filter_keyword":         "关键词包含…",
        "tc_filter_any":             "(全部)",
        "tc_filter_reset":           "重置",
        "tc_agg_title":              "📊 按错误类型 · 语言 · 关键词聚合 — 默认最新检查在前 · 点击列头排序",
        "tc_col_source":             "来源",
        "tc_col_error_type":         "错误类型",
        "tc_col_language":           "语言",
        "tc_col_keyword":            "错误关键词",
        "tc_col_count":              "条数",
        "tc_col_tasks":              "影响任务数",
        "tc_col_latest_task":        "最近任务",
        "tc_col_latest_seen":        "最近检查时间",
        "tc_col_age":                "距今",
        "tc_detail_title":           "已选分组 — 共 {count} 条 issue",
        "tc_detail_empty":           "在上方选择一行以查看该分组的 issue 明细。",
        "tc_col_task":               "任务",
        "tc_col_opus":               "OPUS ID",
        "tc_col_score":              "评分",
        "tc_col_source_text":        "源文",
        "tc_col_translation":        "译文",
        "tc_col_reason":             "评估理由",
        "tc_status_idle":            "空闲。",
        "tc_status_syncing":         "正在同步… {stage} {cur}/{total}",
        "tc_status_done":            "✓ 同步完成 · MR {mr_t}/{mr_i} · Scan {scan_t}/{scan_i} · File {file_t}/{file_i}（任务数/issue 数）",
        "tc_status_failed":          "❌ {error}",
        "tc_status_cancelled":       "⚠ 同步已取消",
        "tc_agg_empty":              "本地 checks 缓存为空 —— 请点「全量重建」拉取数据。",
        "tc_src_mr":                 "MR",
        "tc_src_scan":               "Scan",
        "tc_src_file":                "文件",
        "tc_copy":                   "复制理由",
        "tc_open_tranzor":           "在 Tranzor 打开 ↗",
        "tc_open_tranzor_tip":       "在 Tranzor 平台打开此任务（URL 复用 export_mr_pipeline 配置）。",
        "tc_reclassify":             "♻ 重新分类缓存",
        "tc_reclassify_tip":         (
            "用最新的关键词提取规则把本地缓存里现有的所有 issue 重新分类，\n"
            "不调用任何网络接口。升级 my-tools 后无需重跑 Full re-sync，\n"
            "点这里几秒即可享受新规则。"),
        "tc_reclassify_running":     "♻ 重新分类中… {cur}/{total}",
        "tc_reclassify_done":        "✓ 重新分类完成 · 更新 {updated} 条",
        # 通知 → Checks 桥接（task UUID 入口）
        "tc_filter_task":            "任务 ID：",
        "tc_task_paste":             "📋 粘贴",
        "tc_task_paste_tip":         (
            "从 Tranzor Bot 群通知里复制 Task UUID 后点这里。\n"
            "下方聚合表会立即过滤为只显示这个任务的 issue。"),
        "tc_task_summary_btn":       "🔎 任务汇总",
        "tc_task_summary_tip":       (
            "弹出对话框：以与群通知一致的格式（如\"16 Variable/Number\n"
            "Mismatch · 15 Terminology Inconsistency\"）展示当前任务的\n"
            "完整 checks 汇总 + 全部 issue 明细。"),
        "tc_task_filter_no_match":   "任务 {tid}…（缓存中无匹配行）",
        "tc_task_not_found":         "任务 {tid}… 不在本地缓存 —— 请先点同步。",
        # 任务汇总对话框
        "tc_summary_title":          "任务汇总 · {label}",
        "tc_summary_meta":           (
            "项目：{project}\n任务名：{task_name}\n任务 ID：{task_id}\n"
            "状态：{status}  ·  平均分：{score}  ·  创建：{created}"),
        "tc_summary_checks_label":   "Checks：",
        "tc_summary_checks_pass":    "Pass · 全部 {rows} 条翻译均无 issue",
        "tc_summary_issues_title":   "Issue 明细（{n} 条）",
        "tc_summary_copy_ids":       "📋 复制 OPUS ID",
        "tc_summary_copy_done":      "✓ 已复制 {n} 条 OPUS ID",
        "tc_summary_open_tranzor":   "在 Tranzor 打开 ↗",
        "tc_summary_close":          "关闭",
    },
}


# ---------------------------------------------------------------------------
# 本地 import —— 必须在 STRINGS 之后；export_gui 反向 merge 才能拿到。
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tranzor_checks as tc
import export_mr_pipeline as mr_api  # 仅为 TRANZOR_URL 跳转
from export_gui import FONT_FAMILY, FONT_MONO, IS_MAC, format_age_days


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _fmt_iso_short(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        s = str(iso_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return str(iso_str)[:16]


def _humanize_elapsed(iso_str: str | None, t) -> str:
    if not iso_str:
        return "—"
    try:
        s = str(iso_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        delta = now - dt
        total_minutes = int(delta.total_seconds() // 60)
    except Exception:
        return "—"
    if total_minutes < 1:
        return t("tc_elapsed_just_now")
    if total_minutes < 60:
        return t("tc_elapsed_minutes").format(m=total_minutes)
    if total_minutes < 60 * 24:
        return t("tc_elapsed_hours").format(
            h=total_minutes // 60, m=total_minutes % 60)
    days = total_minutes // (60 * 24)
    rem = total_minutes % (60 * 24)
    return t("tc_elapsed_days").format(d=days, h=rem // 60, m=rem % 60)


def _source_label(source_kind: str, t) -> str:
    key = {"mr": "tc_src_mr", "scan": "tc_src_scan",
           "file": "tc_src_file"}.get((source_kind or "").lower())
    return t(key) if key else (source_kind or "?")


def _source_tag(source_kind: str) -> str:
    sk = (source_kind or "").lower()
    return f"src_{sk}" if sk in ("mr", "scan", "file") else "src_unknown"


def _short(text, n=80):
    if not text:
        return ""
    s = str(text)
    return s if len(s) <= n else s[:n - 1] + "…"


# ---------------------------------------------------------------------------
# 主 Tab 类
# ---------------------------------------------------------------------------
class TranzorChecksTab:
    """Tranzor Checks 面板（issue 聚合视角）。

    与 OpusIdMonitorTab 共享同一套"本地缓存 + 后台同步线程"模型；
    UI 简化为单聚合表 + 下钻面板，留出空间给后续 v0.2 增强（如行级
    一键忽略）。
    """

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self._sync_thread: threading.Thread | None = None
        self._reclassify_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        # 当前聚合 / 下钻数据持有，避免每次排序都重查 DB
        self._agg_data: list[dict] = []
        # 默认排序：按最新检查时间倒序 —— 没选具体错误类型时这是最有用的视图
        # （刚被检测到的问题先暴露，旧的归档在下面）。
        self._agg_sort = ("latest_seen", True)  # (col, desc)
        self._agg_row_keys: dict[str, dict] = {}
        self._issues_data: list[dict] = []
        self._issues_sort = ("score", False)  # (col, desc)
        self._issues_row_keys: dict[str, int] = {}
        self._last_sync_iso: str | None = None
        self._build(parent)
        # 启动后立即用本地缓存渲染首屏
        self.parent.after(50, self._refresh_from_cache)

    def _t(self, key):
        return self.app._t(key)

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------
    def _build(self, parent):
        content = ttk.Frame(parent, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=16, pady=8)

        # ── Top bar ──
        topbar = ttk.Frame(content, style="App.TFrame")
        topbar.pack(fill="x", pady=(0, 8))

        self.btn_sync = self.app._create_button(
            topbar, text="", command=self._on_sync_incremental,
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#2ecc71", fg="#fff", padx=14, pady=4)
        self.btn_sync.pack(side="left")
        from export_gui import Tooltip
        self._tip_sync = Tooltip(self.btn_sync, text="")

        self.btn_sync_full = self.app._create_button(
            topbar, text="", command=self._on_sync_full,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        self.btn_sync_full.pack(side="left", padx=(8, 0))
        self._tip_sync_full = Tooltip(self.btn_sync_full, text="")

        self.btn_sync_cancel = self.app._create_button(
            topbar, text="", command=self._on_cancel,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4, state="disabled")
        self.btn_sync_cancel.pack(side="left", padx=(8, 0))
        self._tip_sync_cancel = Tooltip(self.btn_sync_cancel, text="")

        # 重新分类按钮 —— 给"v0.1 留下大量 (unparsed)"的用户一条不重跑
        # Full re-sync 的快速通路；只在本地 SQLite 上重跑 classify_issue。
        self.btn_reclassify = self.app._create_button(
            topbar, text="", command=self._on_reclassify,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#3a2e5e", fg="#dcd0ff", padx=14, pady=4)
        self.btn_reclassify.pack(side="left", padx=(8, 0))
        self._tip_reclassify = Tooltip(self.btn_reclassify, text="")

        self.lbl_last_sync = ttk.Label(topbar, text="", style="Status.TLabel")
        self.lbl_last_sync.pack(side="left", padx=(16, 0))
        self._schedule_elapsed_tick()

        self.lbl_status = ttk.Label(topbar, text="", style="Status.TLabel")
        self.lbl_status.pack(side="right")

        # ── Cards ──
        cards_row = ttk.Frame(content, style="App.TFrame")
        cards_row.pack(fill="x", pady=(0, 10))

        self.card_tasks = _SummaryCard(cards_row, color="#4472C4")
        self.card_tasks.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.card_clean = _SummaryCard(cards_row, color="#27AE60")
        self.card_clean.pack(side="left", expand=True, fill="x", padx=6)
        self.card_issues = _SummaryCard(cards_row, color="#E74C3C")
        self.card_issues.pack(side="left", expand=True, fill="x", padx=6)
        self.card_langs = _SummaryCard(cards_row, color="#8E44AD")
        self.card_langs.pack(side="left", expand=True, fill="x", padx=(6, 0))

        # ── Filter row ──
        filt = ttk.Frame(content, style="Card.TFrame")
        filt.pack(fill="x", pady=(0, 8))
        filt.configure(borderwidth=1, relief="solid")
        fi = ttk.Frame(filt, style="Card.TFrame")
        fi.pack(fill="x", padx=10, pady=8)

        self.lbl_filter_type = ttk.Label(fi, text="", style="Card.TLabel")
        self.lbl_filter_type.pack(side="left")
        self.flt_type_var = tk.StringVar()
        self.cmb_flt_type = ttk.Combobox(
            fi, textvariable=self.flt_type_var, width=22, state="readonly")
        self.cmb_flt_type.pack(side="left", padx=(4, 12))
        self.cmb_flt_type.bind("<<ComboboxSelected>>", lambda _e: self._refresh_aggregation())

        self.lbl_filter_lang = ttk.Label(fi, text="", style="Card.TLabel")
        self.lbl_filter_lang.pack(side="left")
        self.flt_lang_var = tk.StringVar()
        self.cmb_flt_lang = ttk.Combobox(
            fi, textvariable=self.flt_lang_var, width=10, state="readonly")
        self.cmb_flt_lang.pack(side="left", padx=(4, 12))
        self.cmb_flt_lang.bind("<<ComboboxSelected>>", lambda _e: self._refresh_aggregation())

        self.lbl_filter_source = ttk.Label(fi, text="", style="Card.TLabel")
        self.lbl_filter_source.pack(side="left")
        self.flt_source_var = tk.StringVar()
        self.cmb_flt_source = ttk.Combobox(
            fi, textvariable=self.flt_source_var, width=8, state="readonly")
        self.cmb_flt_source.pack(side="left", padx=(4, 12))
        self.cmb_flt_source.bind("<<ComboboxSelected>>", lambda _e: self._refresh_aggregation())

        self.lbl_filter_kw = ttk.Label(fi, text="", style="Card.TLabel")
        self.lbl_filter_kw.pack(side="left")
        self.flt_kw_var = tk.StringVar()
        self.ent_flt_kw = tk.Entry(
            fi, textvariable=self.flt_kw_var, width=22,
            font=(FONT_FAMILY, 10),
            bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat")
        self.ent_flt_kw.pack(side="left", padx=(4, 12), ipady=3)
        # 输入即查（300ms 防抖避免每个字符都触发 SQL）
        self._kw_after_id: str | None = None
        self.flt_kw_var.trace_add("write", self._on_kw_change)

        self.btn_flt_reset = tk.Button(
            fi, text="", command=self._reset_filters,
            font=(FONT_FAMILY, 10), relief="flat",
            bg="#0f3460", fg="#ccc", padx=10, pady=2,
            activebackground="#1a3a6a", activeforeground="#fff",
            cursor="hand2")
        self.btn_flt_reset.pack(side="left")

        # ── Filter row 2: Task ID lookup (notification → checks bridge) ──
        # 群通知里能拿到的最唯一标识就是 task UUID。专门给它一行入口，让
        # "群里 Ctrl+C → 来这里 Ctrl+V" 工作流一目了然。粘贴按钮自动从
        # 剪贴板读 UUID，省得用户在两个窗口之间反复切。
        fi2 = ttk.Frame(filt, style="Card.TFrame")
        fi2.pack(fill="x", padx=10, pady=(0, 8))

        self.lbl_filter_task = ttk.Label(fi2, text="", style="Card.TLabel")
        self.lbl_filter_task.pack(side="left")
        self.flt_task_var = tk.StringVar()
        self.ent_flt_task = tk.Entry(
            fi2, textvariable=self.flt_task_var, width=42,
            font=(FONT_MONO, 10),  # UUID 等宽更易扫
            bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat")
        self.ent_flt_task.pack(side="left", padx=(4, 8), ipady=3)
        # 输入即查（300ms 防抖，同 keyword 字段）
        self._task_after_id: str | None = None
        self.flt_task_var.trace_add("write", self._on_task_id_change)
        # 回车键直接弹任务汇总对话框 —— 用户最快路径："粘贴 → Enter"
        self.ent_flt_task.bind("<Return>", lambda _e: self._open_task_summary())

        self.btn_paste_task = tk.Button(
            fi2, text="", command=self._paste_task_id,
            font=(FONT_FAMILY, 10), relief="flat",
            bg="#27AE60", fg="#fff", padx=10, pady=2,
            activebackground="#2ecc71", activeforeground="#fff",
            cursor="hand2")
        self.btn_paste_task.pack(side="left", padx=(0, 4))

        self.btn_open_task_summary = tk.Button(
            fi2, text="", command=self._open_task_summary,
            font=(FONT_FAMILY, 10), relief="flat",
            bg="#4472C4", fg="#fff", padx=10, pady=2,
            activebackground="#5482d4", activeforeground="#fff",
            cursor="hand2")
        self.btn_open_task_summary.pack(side="left", padx=(0, 4))

        self.btn_clear_task = tk.Button(
            fi2, text="✕", command=lambda: self.flt_task_var.set(""),
            font=(FONT_FAMILY, 10), relief="flat",
            bg="#0f3460", fg="#ccc", padx=8, pady=2,
            activebackground="#1a3a6a", activeforeground="#fff",
            cursor="hand2")
        self.btn_clear_task.pack(side="left")

        # Tooltip：解释这是给群通知粘贴 UUID 用的
        try:
            from export_gui import Tooltip
            self._tip_paste_task = Tooltip(self.btn_paste_task, text="")
            self._tip_open_task = Tooltip(self.btn_open_task_summary, text="")
        except Exception:
            self._tip_paste_task = None
            self._tip_open_task = None

        # ── Main body: split top (aggregation) / bottom (issues detail) ──
        body = ttk.PanedWindow(content, orient="vertical")
        body.pack(fill="both", expand=True)

        # Top half: aggregation table
        top_pane = ttk.Frame(body, style="App.TFrame")
        body.add(top_pane, weight=3)

        self.lbl_agg = ttk.Label(top_pane, text="", style="CardBold.TLabel")
        self.lbl_agg.pack(anchor="w", pady=(0, 4))

        agg_frame = ttk.Frame(top_pane, style="App.TFrame")
        agg_frame.pack(fill="both", expand=True)
        # 列顺序：来源 → 错误类型 → 语言 → 关键词 → 条数 → 任务数 → 最近任务 → 最近检查时间 → 距今
        # "最近检查时间" 放在 latest_task 之后保持原顺序；"距今" 紧接其后
        # 是 raw 时间戳的人类可读注解，挨着读最省眼力。主库
        # DB_SEARCH_EXPIRED_DAYS 默认 3650 后，聚合表里常掺杂多年前的
        # 陈年检查，"距今" 一眼能挑出来。
        self._agg_cols = ("source", "error_type", "language", "keyword",
                          "count", "tasks", "latest_task", "latest_seen",
                          "age")
        self.tree_agg = ttk.Treeview(
            agg_frame, columns=self._agg_cols, show="headings",
            style="Summary.Treeview", selectmode="browse", height=12)
        widths = {"source": 70, "error_type": 180, "language": 60,
                  "keyword": 240, "count": 60, "tasks": 60,
                  "latest_task": 140, "latest_seen": 120, "age": 55}
        for c in self._agg_cols:
            anchor = "w" if c in ("error_type", "keyword",
                                    "latest_task") else "center"
            self.tree_agg.column(c, width=widths.get(c, 80), anchor=anchor)
            self.tree_agg.heading(
                c, text="", command=lambda col=c: self._sort_agg(col))
        sb_agg = ttk.Scrollbar(agg_frame, orient="vertical",
                                command=self.tree_agg.yview)
        self.tree_agg.configure(yscrollcommand=sb_agg.set)
        self.tree_agg.pack(side="left", fill="both", expand=True)
        sb_agg.pack(side="right", fill="y")

        # 源行配色 —— 复用 Opus Monitor 同款配色，跨 tab 视觉一致
        _SRC_BG = {"src_mr": "#1b2c44", "src_scan": "#1f3a2b",
                   "src_file": "#3a2e1f", "src_unknown": "#1a1a2e"}
        _SRC_FG = {"src_mr": "#cfe1ff", "src_scan": "#caf0d3",
                   "src_file": "#f0d9b8", "src_unknown": "#ccc"}
        for tag, bg in _SRC_BG.items():
            self.tree_agg.tag_configure(tag, background=bg,
                                         foreground=_SRC_FG[tag])

        # 选中聚合行 → 拉下钻
        self.tree_agg.bind("<<TreeviewSelect>>",
                            lambda _e: self._on_agg_selected())

        # Bottom half: issues detail
        bottom_pane = ttk.Frame(body, style="App.TFrame")
        body.add(bottom_pane, weight=2)

        self.lbl_detail = ttk.Label(bottom_pane, text="",
                                      style="CardBold.TLabel")
        self.lbl_detail.pack(anchor="w", pady=(0, 4))

        issues_frame = ttk.Frame(bottom_pane, style="App.TFrame")
        issues_frame.pack(fill="both", expand=True)
        self._issues_cols = ("source", "task", "opus", "language",
                              "score", "source_text", "translation", "reason")
        self.tree_issues = ttk.Treeview(
            issues_frame, columns=self._issues_cols, show="headings",
            style="Summary.Treeview", selectmode="browse", height=8)
        iwidths = {"source": 50, "task": 110, "opus": 180, "language": 60,
                   "score": 55, "source_text": 220, "translation": 220,
                   "reason": 280}
        for c in self._issues_cols:
            anchor = "w" if c in ("opus", "source_text", "translation",
                                    "reason", "task") else "center"
            self.tree_issues.column(c, width=iwidths.get(c, 100),
                                     anchor=anchor)
            self.tree_issues.heading(
                c, text="", command=lambda col=c: self._sort_issues(col))
        sb_issues = ttk.Scrollbar(issues_frame, orient="vertical",
                                    command=self.tree_issues.yview)
        self.tree_issues.configure(yscrollcommand=sb_issues.set)
        self.tree_issues.pack(side="left", fill="both", expand=True)
        sb_issues.pack(side="right", fill="y")
        for tag, bg in _SRC_BG.items():
            self.tree_issues.tag_configure(tag, background=bg,
                                            foreground=_SRC_FG[tag])

        # 右键 / 双击 issue 行 → 操作（复制 reason / 打开 Tranzor）
        self.tree_issues.bind("<Double-1>", self._on_issue_dbl)
        self.tree_issues.bind(
            "<Button-3>" if not IS_MAC else "<Button-2>",
            self._on_issue_right_click)

    # ------------------------------------------------------------------
    # i18n refresh (called by export_gui when language toggles)
    # 命名跟随项目约定（其他 tab 都叫 ``refresh_text``）。
    # ------------------------------------------------------------------
    def refresh_text(self):
        t = self._t
        # 顶部按钮
        self.btn_sync.configure(text=t("tc_sync_now"))
        self._tip_sync.set_text(t("tc_sync_now_tip"))
        self.btn_sync_full.configure(text=t("tc_sync_full"))
        self._tip_sync_full.set_text(t("tc_sync_full_tip"))
        self.btn_sync_cancel.configure(text=t("tc_sync_cancel"))
        self._tip_sync_cancel.set_text(t("tc_sync_cancel_tip"))
        self.btn_reclassify.configure(text=t("tc_reclassify"))
        self._tip_reclassify.set_text(t("tc_reclassify_tip"))
        # 卡片标题
        self.card_tasks.set_title(t("tc_card_tasks"))
        self.card_clean.set_title(t("tc_card_clean"))
        self.card_issues.set_title(t("tc_card_issues"))
        self.card_langs.set_title(t("tc_card_languages"))
        # 筛选条
        self.lbl_filter_type.configure(text=t("tc_filter_type"))
        self.lbl_filter_lang.configure(text=t("tc_filter_lang"))
        self.lbl_filter_source.configure(text=t("tc_filter_source"))
        self.lbl_filter_kw.configure(text=t("tc_filter_keyword"))
        self.btn_flt_reset.configure(text=t("tc_filter_reset"))
        # Filter row 2 (Task ID 入口) i18n
        if hasattr(self, "lbl_filter_task"):
            self.lbl_filter_task.configure(text=t("tc_filter_task"))
            self.btn_paste_task.configure(text=t("tc_task_paste"))
            self.btn_open_task_summary.configure(text=t("tc_task_summary_btn"))
            if self._tip_paste_task:
                self._tip_paste_task.set_text(t("tc_task_paste_tip"))
            if self._tip_open_task:
                self._tip_open_task.set_text(t("tc_task_summary_tip"))
        # 主表标题与列头
        self.lbl_agg.configure(text=t("tc_agg_title"))
        for c, key in (
            ("source", "tc_col_source"),
            ("error_type", "tc_col_error_type"),
            ("language", "tc_col_language"),
            ("keyword", "tc_col_keyword"),
            ("count", "tc_col_count"),
            ("tasks", "tc_col_tasks"),
            ("latest_task", "tc_col_latest_task"),
            ("latest_seen", "tc_col_latest_seen"),
            ("age", "tc_col_age"),
        ):
            self.tree_agg.heading(
                c, text=t(key),
                command=lambda col=c: self._sort_agg(col))
        # 下钻表
        for c, key in (
            ("source", "tc_col_source"),
            ("task", "tc_col_task"),
            ("opus", "tc_col_opus"),
            ("language", "tc_col_language"),
            ("score", "tc_col_score"),
            ("source_text", "tc_col_source_text"),
            ("translation", "tc_col_translation"),
            ("reason", "tc_col_reason"),
        ):
            self.tree_issues.heading(
                c, text=t(key),
                command=lambda col=c: self._sort_issues(col))
        # 刷新动态文案
        self._refresh_from_cache()

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _refresh_from_cache(self):
        t = self._t
        try:
            summary = tc.get_summary()
            options = tc.get_filter_options()
        except Exception as e:
            self.lbl_status.configure(
                text=t("tc_status_failed").format(error=str(e)[:60]))
            return

        total_tasks = summary["total_tasks"]
        clean = summary["tasks_clean"]
        pct = round(100 * clean / total_tasks, 1) if total_tasks else 0

        self.card_tasks.set_value(f"{total_tasks:,}")
        self.card_tasks.set_subtitle("")
        self.card_clean.set_value(f"{clean:,}")
        self.card_clean.set_subtitle(
            t("tc_card_pass_rate").format(pct=pct) if total_tasks else "")
        self.card_issues.set_value(f"{summary['total_issues']:,}")
        self.card_issues.set_subtitle(
            f"{summary['error_types']} types" if summary['error_types'] else "")
        self.card_langs.set_value(f"{summary['languages']:,}")
        self.card_langs.set_subtitle("")

        self._last_sync_iso = summary.get("last_sync_at")
        self._render_last_sync_label()

        # 更新筛选下拉框选项（保留当前选择）
        self._populate_combos(options)

        # 主表 + 详情
        self._refresh_aggregation()

    def _populate_combos(self, options: dict):
        t = self._t
        any_label = t("tc_filter_any")
        types_vals = [any_label] + list(options.get("error_types") or [])
        langs_vals = [any_label] + list(options.get("languages") or [])
        kinds_vals = [any_label] + [_source_label(k, t)
                                      for k in (options.get("source_kinds") or [])]

        # 内部维护映射 label → 实际值（语言例外因为 label == value）
        self._source_label_map = {any_label: None}
        for k in (options.get("source_kinds") or []):
            self._source_label_map[_source_label(k, t)] = k

        # 设值时保留旧选择（若仍存在）
        def _preserve(combo, var, values):
            cur = var.get()
            combo["values"] = values
            if cur not in values:
                var.set(any_label)

        _preserve(self.cmb_flt_type, self.flt_type_var, types_vals)
        _preserve(self.cmb_flt_lang, self.flt_lang_var, langs_vals)
        _preserve(self.cmb_flt_source, self.flt_source_var, kinds_vals)

    def _refresh_aggregation(self):
        t = self._t
        any_label = t("tc_filter_any")
        et = self.flt_type_var.get() or any_label
        lang = self.flt_lang_var.get() or any_label
        src_label = self.flt_source_var.get() or any_label
        kw = (self.flt_kw_var.get() or "").strip()
        tid = (self.flt_task_var.get() or "").strip() \
            if hasattr(self, "flt_task_var") else ""
        src = getattr(self, "_source_label_map", {}).get(src_label)

        try:
            data = tc.get_aggregated_issues(
                error_type=None if et == any_label else et,
                language=None if lang == any_label else lang,
                source_kind=src,
                keyword_substring=kw or None,
                task_id=tid or None,
            )
        except Exception as e:
            self.lbl_status.configure(
                text=t("tc_status_failed").format(error=str(e)[:60]))
            return
        self._agg_data = data
        self._render_agg_table()
        # 重置下钻
        self._issues_data = []
        self._render_issues_table()
        self.lbl_detail.configure(text=t("tc_detail_empty"))
        # 主表标题追加 task 上下文提示，让用户随时看到"我现在过滤的是哪个任务"
        self._update_agg_title_with_task_context(tid)

    def _update_agg_title_with_task_context(self, tid: str):
        """有 task_id 过滤时，在主表标题后追加 "· 任务 <短> / MR #N" 标签。

        信息来自第一条聚合行的 latest_task_name / latest_mr_iid（属同一 task），
        让用户能马上确认自己粘进来的 UUID 到底是哪个任务。
        """
        t = self._t
        base = t("tc_agg_title")
        if not tid:
            self.lbl_agg.configure(text=base)
            return
        # 从已加载的聚合数据里拿一条 task 元信息
        ctx = ""
        if self._agg_data:
            first = self._agg_data[0]
            name = first.get("latest_task_name") or ""
            mr = first.get("latest_mr_iid") or ""
            short_tid = tid[:8] if len(tid) >= 8 else tid
            parts = [f"task {short_tid}…"]
            if mr:
                parts.append(f"MR #{mr}")
            elif name:
                parts.append(name[:30])
            ctx = " · " + " / ".join(parts)
        else:
            short_tid = tid[:8] if len(tid) >= 8 else tid
            ctx = " · " + t("tc_task_filter_no_match").format(tid=short_tid)
        self.lbl_agg.configure(text=base + ctx)

    def _render_agg_table(self):
        col, desc = self._agg_sort
        key = {
            "source":      "source_kinds",
            "error_type":  "error_type",
            "language":    "language",
            "keyword":     "error_keyword",
            "count":       "count",
            "tasks":       "tasks_affected",
            "latest_task": "latest_task_name",
            "latest_seen": "latest_seen",
        }.get(col, "latest_seen")
        numeric = col in ("count", "tasks")

        def _k(row):
            v = row.get(key)
            if numeric:
                return v or 0
            return (str(v or "")).lower()

        rows = sorted(self._agg_data, key=_k, reverse=desc)
        self.tree_agg.delete(*self.tree_agg.get_children())
        self._agg_row_keys.clear()
        t = self._t
        if not rows:
            # 空表友好提示：在 status 行而不是吃掉整张表
            self.lbl_status.configure(text=t("tc_agg_empty"))
            return
        for r in rows:
            src_kinds = (r.get("source_kinds") or "").split(",")
            # 多源混合时仍按主导 kind 上色
            main_src = src_kinds[0] if src_kinds else ""
            src_disp = " / ".join(
                _source_label(k, t) for k in src_kinds if k)
            # 最近任务列：优先 "MR #1066"（有 mr_iid 时最直观），其次任务名，
            # 最后是 task_id 前 12 字符。完整 task_id 在选中后下钻面板可见。
            latest_mr = r.get("latest_mr_iid") or ""
            latest_name = r.get("latest_task_name") or ""
            latest_tid = r.get("latest_task_id") or ""
            if latest_mr:
                latest_task_disp = f"MR #{latest_mr}"
            elif latest_name:
                latest_task_disp = _short(latest_name, 30)
            elif latest_tid:
                latest_task_disp = _short(latest_tid, 14)
            else:
                latest_task_disp = "—"
            latest_seen_raw = r.get("latest_seen") or ""
            iid = self.tree_agg.insert("", "end", values=(
                src_disp or "—",
                r.get("error_type", ""),
                r.get("language") or "—",
                _short(r.get("error_keyword") or "", 60),
                f"{r.get('count', 0):,}",
                f"{r.get('tasks_affected', 0):,}",
                latest_task_disp,
                _fmt_iso_short(latest_seen_raw),
                format_age_days(latest_seen_raw),
            ), tags=(_source_tag(main_src),))
            self._agg_row_keys[iid] = r

    def _sort_agg(self, col):
        cur_col, cur_desc = self._agg_sort
        if col == cur_col:
            self._agg_sort = (col, not cur_desc)
        else:
            # 数值列默认降序、时间列默认降序（最新在前）、文本列默认升序。
            # ``age`` 也走降序：用户点击 age 列头多半想看"最陈年的在前"，
            # 这正好对应 latest_seen 升序——但因为 age 和 latest_seen 的
            # 排序键都映射到 latest_seen（见 _render_agg_table），降序在
            # 这里语义反过来：desc=True → latest_seen 大的在前 → age 小
            # 的在前。所以 age 应该是 desc=False（升序 latest_seen = 最
            # 陈年在前）。
            self._agg_sort = (
                col,
                col in ("count", "tasks", "latest_seen"),
            )
        self._render_agg_table()

    def _on_agg_selected(self):
        sel = self.tree_agg.selection()
        if not sel:
            return
        row = self._agg_row_keys.get(sel[0])
        if not row:
            return
        # 下钻必须沿用主表上的 task_id 过滤，否则用户"先按 task 过滤、
        # 再点某分组"会看到该 keyword 下所有 task 的 issue，与上层视图不一致。
        tid = (self.flt_task_var.get() or "").strip() \
            if hasattr(self, "flt_task_var") else ""
        try:
            issues = tc.get_issues_for_group(
                error_type=row["error_type"],
                language=row.get("language") or None,
                error_keyword=row.get("error_keyword"),
                task_id=tid or None,
                limit=500,
            )
        except Exception as e:
            self.lbl_status.configure(
                text=self._t("tc_status_failed").format(error=str(e)[:60]))
            return
        self._issues_data = issues
        self.lbl_detail.configure(
            text=self._t("tc_detail_title").format(count=len(issues)))
        # 默认按 score 升序（最差的 issue 排最前）
        self._issues_sort = ("score", False)
        self._render_issues_table()

    def _render_issues_table(self):
        col, desc = self._issues_sort
        key = {
            "source": "source_kind",
            "task": "task_id",
            "opus": "opus_id",
            "language": "target_language",
            "score": "final_score",
            "source_text": "source_text",
            "translation": "translated_text",
            "reason": "reason",
        }.get(col, "final_score")
        numeric = col == "score"

        def _k(row):
            v = row.get(key)
            if numeric:
                # None 排最前（最差），保持与默认 ascending 一致的语义
                return float("inf") if v is None else float(v)
            return (str(v or "")).lower()

        rows = sorted(self._issues_data, key=_k, reverse=desc)
        self.tree_issues.delete(*self.tree_issues.get_children())
        self._issues_row_keys.clear()
        t = self._t
        for r in rows:
            score = r.get("final_score")
            score_disp = f"{score:.1f}" if isinstance(score, (int, float)) else "—"
            task_disp = ""
            mr_iid = r.get("mr_iid")
            if mr_iid:
                task_disp = f"MR #{mr_iid}"
            elif r.get("task_name"):
                task_disp = _short(r.get("task_name") or "", 40)
            else:
                task_disp = _short(r.get("task_id") or "", 12)
            src = r.get("source_kind", "")
            iid = self.tree_issues.insert("", "end", values=(
                _source_label(src, t),
                task_disp,
                _short(r.get("opus_id") or "", 50),
                r.get("target_language") or "—",
                score_disp,
                _short(r.get("source_text") or "", 60),
                _short(r.get("translated_text") or "", 60),
                _short(r.get("reason") or "", 80),
            ), tags=(_source_tag(src),))
            self._issues_row_keys[iid] = r.get("id") or 0

    def _sort_issues(self, col):
        cur_col, cur_desc = self._issues_sort
        if col == cur_col:
            self._issues_sort = (col, not cur_desc)
        else:
            # 详情表全部默认升序：文本按字典序，score 升序 = 最差分数排最前
            # （QA 一进来就先看烂的）。
            self._issues_sort = (col, False)
        self._render_issues_table()

    # ------------------------------------------------------------------
    # 输入框防抖
    # ------------------------------------------------------------------
    def _on_kw_change(self, *_):
        if self._kw_after_id is not None:
            try:
                self.parent.after_cancel(self._kw_after_id)
            except Exception:
                pass
        self._kw_after_id = self.parent.after(300, self._refresh_aggregation)

    def _on_task_id_change(self, *_):
        if self._task_after_id is not None:
            try:
                self.parent.after_cancel(self._task_after_id)
            except Exception:
                pass
        self._task_after_id = self.parent.after(300, self._refresh_aggregation)

    def _reset_filters(self):
        any_label = self._t("tc_filter_any")
        self.flt_type_var.set(any_label)
        self.flt_lang_var.set(any_label)
        self.flt_source_var.set(any_label)
        self.flt_kw_var.set("")
        if hasattr(self, "flt_task_var"):
            self.flt_task_var.set("")
        self._refresh_aggregation()

    # ------------------------------------------------------------------
    # 通知工作流：从剪贴板粘 UUID + 打开任务汇总对话框
    # ------------------------------------------------------------------
    def _paste_task_id(self):
        """读剪贴板内容，截到第一个空白前的子串（应对用户复制了带前后缀
        的格式）后填入输入框。trace_add 的写回调会自动触发主表过滤。
        """
        try:
            clip = self.parent.clipboard_get()
        except Exception:
            return  # 剪贴板为空或类型不兼容（如复制了图片），无声跳过
        if not clip:
            return
        # 取第一段连续非空白 —— Tranzor Bot 通知里 task UUID 周围有空格/换行，
        # 用户连带复制时也能干净落地。
        candidate = clip.strip().split()[0] if clip.strip() else ""
        if not candidate:
            return
        self.flt_task_var.set(candidate)
        # 立即触发一次（绕过 300ms 防抖，让用户感觉到"粘贴 → 出结果"是瞬时的）
        if self._task_after_id is not None:
            try:
                self.parent.after_cancel(self._task_after_id)
            except Exception:
                pass
        self._refresh_aggregation()

    def _open_task_summary(self):
        """根据当前 task_id 输入弹出 TaskSummaryDialog；为空则不动作。"""
        tid = (self.flt_task_var.get() or "").strip()
        if not tid:
            return
        try:
            summary = tc.get_task_summary(tid)
        except Exception as e:
            self.lbl_status.configure(
                text=self._t("tc_status_failed").format(error=str(e)[:60]))
            return
        if summary is None:
            # 本地缓存没这个 task —— 告诉用户去 Sync
            self.lbl_status.configure(
                text=self._t("tc_task_not_found").format(tid=tid[:8]))
            return
        TaskSummaryDialog(self.parent, self.app, summary)

    # ------------------------------------------------------------------
    # 右键菜单 / 双击：复制 reason · 在 Tranzor 打开
    # ------------------------------------------------------------------
    def _on_issue_dbl(self, _event=None):
        self._open_selected_issue_in_tranzor()

    def _on_issue_right_click(self, event):
        # 先选中点中的行
        row = self.tree_issues.identify_row(event.y)
        if row:
            self.tree_issues.selection_set(row)
        sel = self.tree_issues.selection()
        if not sel:
            return
        t = self._t
        menu = tk.Menu(self.tree_issues, tearoff=0)
        menu.add_command(label=t("tc_copy"),
                          command=self._copy_selected_reason)
        menu.add_command(label=t("tc_open_tranzor"),
                          command=self._open_selected_issue_in_tranzor)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _selected_issue_detail(self) -> dict | None:
        sel = self.tree_issues.selection()
        if not sel:
            return None
        issue_id = self._issues_row_keys.get(sel[0])
        if not issue_id:
            return None
        try:
            return tc.get_issue_detail(int(issue_id))
        except Exception:
            return None

    def _copy_selected_reason(self):
        d = self._selected_issue_detail()
        if not d:
            return
        text = d.get("reason") or ""
        try:
            self.parent.clipboard_clear()
            self.parent.clipboard_append(text)
            self.parent.update()  # 让剪贴板内容真正进入系统
        except Exception:
            pass

    def _open_selected_issue_in_tranzor(self):
        d = self._selected_issue_detail()
        if not d:
            return
        # 与 export_mr_pipeline 的 dashboard URL 格式对齐
        base = getattr(mr_api, "TRANZOR_URL", "")
        if not base:
            return
        kind = (d.get("source_kind") or "").lower()
        task_id = d.get("task_id") or ""
        project_id = d.get("project_id") or ""
        mr_iid = d.get("mr_iid")
        url = ""
        if kind == "mr" and project_id and mr_iid:
            # MR Pipeline 静态页路由（与 export_mr_pipeline 中 dashboard URL
            # 构造保持一致 —— 见 export_mr_pipeline.py 行 1933 附近）
            from urllib.parse import quote
            url = f"{base}/static/?project_id={quote(project_id)}&mr_id={mr_iid}"
        elif kind == "file":
            url = f"{base}/static/legacy/tasks/{task_id}"
        elif kind == "scan":
            url = f"{base}/static/scans/{task_id}"
        if not url:
            return
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception as e:
            self.lbl_status.configure(
                text=self._t("tc_status_failed").format(error=str(e)[:60]))

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------
    def _on_sync_incremental(self):
        self._kickoff_sync(full=False)

    def _on_sync_full(self):
        self._kickoff_sync(full=True)

    def _on_cancel(self):
        self._cancel_event.set()

    _LIVE_REFRESH_MS = 3000

    def _kickoff_sync(self, *, full: bool):
        if self._sync_thread and self._sync_thread.is_alive():
            return
        self._cancel_event.clear()
        self._set_sync_buttons(running=True)
        self.lbl_status.configure(
            text=self._t("tc_status_syncing").format(
                stage="init", cur=0, total=0))
        self._sync_thread = threading.Thread(
            target=self._run_sync, args=(full,), daemon=True)
        self._sync_thread.start()
        self.parent.after(
            self._LIVE_REFRESH_MS, self._tick_live_refresh)

    def _tick_live_refresh(self):
        if not (self._sync_thread and self._sync_thread.is_alive()):
            return
        try:
            self._refresh_from_cache()
        except Exception:
            pass
        self.parent.after(self._LIVE_REFRESH_MS, self._tick_live_refresh)

    def _set_sync_buttons(self, *, running: bool):
        if IS_MAC:
            running_state = ["disabled"] if running else ["!disabled"]
            cancel_state = ["!disabled"] if running else ["disabled"]
            self.btn_sync.state(running_state)
            self.btn_sync_full.state(running_state)
            self.btn_sync_cancel.state(cancel_state)
            self.btn_reclassify.state(running_state)
        else:
            s = "disabled" if running else "normal"
            cs = "normal" if running else "disabled"
            self.btn_sync.configure(state=s)
            self.btn_sync_full.configure(state=s)
            self.btn_sync_cancel.configure(state=cs)
            self.btn_reclassify.configure(state=s)

    # ------------------------------------------------------------------
    # 重新分类（不联网） —— 把新的提取规则跑到现有缓存上
    # ------------------------------------------------------------------
    def _on_reclassify(self):
        # 防并发：同步进行中、或已经在跑 reclassify 就直接吞掉
        if self._sync_thread and self._sync_thread.is_alive():
            return
        if self._reclassify_thread and self._reclassify_thread.is_alive():
            return
        self._set_sync_buttons(running=True)
        self.lbl_status.configure(
            text=self._t("tc_reclassify_running").format(cur=0, total=0))
        self._reclassify_thread = threading.Thread(
            target=self._run_reclassify, daemon=True)
        self._reclassify_thread.start()

    def _run_reclassify(self):
        t = self._t
        try:
            def progress(stage, cur, total, **kw):
                self.parent.after(
                    0,
                    lambda c=cur, tt=total: self.lbl_status.configure(
                        text=t("tc_reclassify_running").format(cur=c, total=tt)))
            result = tc.reclassify_existing_issues(progress_callback=progress)
            self.parent.after(0, lambda: self.lbl_status.configure(
                text=t("tc_reclassify_done").format(
                    updated=result.get("updated", 0))))
        except Exception as e:
            err = str(e)[:80]
            self.parent.after(0, lambda: self.lbl_status.configure(
                text=t("tc_status_failed").format(error=err)))
        finally:
            self.parent.after(0, lambda: self._set_sync_buttons(running=False))
            # 让聚合表立刻反映新分类结果
            self.parent.after(100, self._refresh_from_cache)

    def _run_sync(self, full: bool):
        try:
            def progress(stage, cur, total, **kw):
                self.parent.after(
                    0,
                    lambda s=stage, c=cur, tt=total: self.lbl_status.configure(
                        text=self._t("tc_status_syncing").format(
                            stage=s, cur=c, total=tt)))

            if full:
                result = tc.sync_full(
                    progress_callback=progress,
                    cancel_event=self._cancel_event)
            else:
                result = tc.sync_incremental(
                    progress_callback=progress,
                    cancel_event=self._cancel_event)

            if self._cancel_event.is_set():
                self.parent.after(0, lambda: self.lbl_status.configure(
                    text=self._t("tc_status_cancelled")))
            else:
                msg = self._t("tc_status_done").format(
                    mr_t=result.get("mr", {}).get("tasks_seen", 0),
                    mr_i=result.get("mr", {}).get("issues_inserted", 0),
                    scan_t=result.get("scan", {}).get("tasks_seen", 0),
                    scan_i=result.get("scan", {}).get("issues_inserted", 0),
                    file_t=result.get("file", {}).get("tasks_seen", 0),
                    file_i=result.get("file", {}).get("issues_inserted", 0),
                )
                self.parent.after(0, lambda: self.lbl_status.configure(text=msg))
        except Exception as e:
            err = str(e)[:80]
            self.parent.after(0, lambda: self.lbl_status.configure(
                text=self._t("tc_status_failed").format(error=err)))
        finally:
            self.parent.after(0, lambda: self._set_sync_buttons(running=False))
            self.parent.after(100, self._refresh_from_cache)

    # ------------------------------------------------------------------
    # Last-sync 标签的分钟级自刷
    # ------------------------------------------------------------------
    _ELAPSED_TICK_MS = 60_000

    def _schedule_elapsed_tick(self):
        self.parent.after(self._ELAPSED_TICK_MS, self._tick_elapsed)

    def _tick_elapsed(self):
        try:
            self._render_last_sync_label()
        except Exception:
            pass
        self._schedule_elapsed_tick()

    def _render_last_sync_label(self):
        last = self._last_sync_iso
        if not last:
            self.lbl_last_sync.configure(text=self._t("tc_last_sync_never"))
            return
        self.lbl_last_sync.configure(
            text=self._t("tc_last_sync").format(
                elapsed=_humanize_elapsed(last, self._t),
                time=_fmt_iso_short(last),
            ))


# ---------------------------------------------------------------------------
# 简易卡片组件 —— 与 OPUS ID Monitor 同款样式
# 单独定义而非跨 tab import，避免 v0.2 想改样式时连环动到别人。
# ---------------------------------------------------------------------------
class _SummaryCard(tk.Frame):
    """大数字 + 标题 + 副标题的卡片组件。"""

    def __init__(self, master, *, color: str, **kw):
        super().__init__(master, bg="#1a1a2e",
                          highlightthickness=1,
                          highlightbackground=color, **kw)
        inner = tk.Frame(self, bg="#1a1a2e")
        inner.pack(fill="both", expand=True, padx=12, pady=10)

        self._title = tk.Label(
            inner, text="", bg="#1a1a2e", fg="#9aa0b0",
            font=(FONT_FAMILY, 9), anchor="w")
        self._title.pack(fill="x")

        self._value = tk.Label(
            inner, text="—", bg="#1a1a2e", fg=color,
            font=(FONT_FAMILY, 20, "bold"), anchor="w")
        self._value.pack(fill="x", pady=(2, 0))

        self._subtitle = tk.Label(
            inner, text="", bg="#1a1a2e", fg="#666",
            font=(FONT_FAMILY, 9), anchor="w")
        self._subtitle.pack(fill="x", pady=(2, 0))

    def set_title(self, text: str):
        self._title.configure(text=text)

    def set_value(self, text: str):
        self._value.configure(text=text)

    def set_subtitle(self, text: str):
        self._subtitle.configure(text=text)


# ---------------------------------------------------------------------------
# 任务汇总对话框 —— 服务"从群通知粘 UUID → 直接看任务全貌"的工作流
# ---------------------------------------------------------------------------
class TaskSummaryDialog(tk.Toplevel):
    """以与 Tranzor Bot 群通知一致的格式展示单任务全貌。

    Layout（从上到下）：
      - 元数据卡：项目 / 任务名 / Task ID / 状态 / 平均分 / 创建时间
      - Checks 行：``Checks: 16 Variable/Number Mismatch · 15 Terminology
        Inconsistency`` —— 视觉上与通知像素级对齐，让用户立即建立映射
      - issue 明细表：按 error_type 排序，列同主面板下钻表
      - 底部按钮：复制 OPUS ID / 在 Tranzor 打开 / 关闭

    设计要点：
      - 非阻塞 Toplevel；点关闭即销毁
      - issue 表能展示 task 内全部 issue，不再受 500 行下钻限制（任务级
        天花板通常 < 几百）
      - "复制 OPUS ID" 只复制该任务的 opus_id 列表，方便用户拿去 grep
    """

    _COPY_FEEDBACK_MS = 1500

    def __init__(self, parent, app, summary: dict):
        super().__init__(parent)
        self.app = app
        self.summary = summary
        t = app._t

        # 标题：尽可能精简但能一眼定位 —— "MR #28671" 优先，否则 task_name
        mr = summary.get("mr_iid")
        label = (f"MR #{mr}" if mr else
                 (summary.get("task_name") or "")[:40] or
                 (summary.get("task_id") or "")[:8] + "…")
        self.title(t("tc_summary_title").format(label=label))
        self.configure(bg="#16213e")
        self.geometry("900x620")

        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=16, pady=12)

        # ── 元数据卡 ──
        score = summary.get("final_score_avg")
        score_disp = f"{score:.1f}" if isinstance(score, (int, float)) else "—"
        meta_text = t("tc_summary_meta").format(
            project=summary.get("project_id") or "—",
            task_name=summary.get("task_name") or "—",
            task_id=summary.get("task_id") or "—",
            status=summary.get("task_status") or "—",
            score=score_disp,
            created=_fmt_iso_short(summary.get("task_created_at") or ""),
        )
        tk.Label(
            outer, text=meta_text,
            bg="#16213e", fg="#ccc",
            font=(FONT_FAMILY, 10), justify="left", anchor="w",
        ).pack(fill="x", pady=(0, 10))

        # ── Checks 行：与通知格式一致 ──
        counts = summary.get("error_type_counts") or {}
        checks_line = tc.format_checks_line(counts)
        if not checks_line:
            checks_line = t("tc_summary_checks_pass").format(
                rows=summary.get("total_rows", 0))
            checks_color = "#27AE60"   # 全通过用绿色
        else:
            checks_color = "#E74C3C"   # 有 issue 用警示红
        checks_frame = ttk.Frame(outer, style="App.TFrame")
        checks_frame.pack(fill="x", pady=(0, 10))
        tk.Label(checks_frame, text=t("tc_summary_checks_label"),
                  bg="#16213e", fg="#9aa0b0",
                  font=(FONT_FAMILY, 11, "bold")).pack(side="left")
        tk.Label(checks_frame, text=" " + checks_line,
                  bg="#16213e", fg=checks_color,
                  font=(FONT_FAMILY, 11, "bold"),
                  justify="left", wraplength=820, anchor="w"
                  ).pack(side="left", fill="x", expand=True)

        # ── Issue 明细表 ──
        issues = summary.get("issues") or []
        ttk.Label(outer,
                  text=t("tc_summary_issues_title").format(n=len(issues)),
                  style="CardBold.TLabel").pack(anchor="w", pady=(0, 4))

        tbl_frame = ttk.Frame(outer, style="App.TFrame")
        tbl_frame.pack(fill="both", expand=True)
        cols = ("error_type", "language", "opus", "score",
                "source_text", "translation", "reason")
        tree = ttk.Treeview(
            tbl_frame, columns=cols, show="headings",
            style="Summary.Treeview", selectmode="browse")
        widths = {"error_type": 160, "language": 60, "opus": 200,
                  "score": 50, "source_text": 180, "translation": 180,
                  "reason": 220}
        for c in cols:
            anchor = "w" if c in ("error_type", "opus", "source_text",
                                    "translation", "reason") else "center"
            tree.column(c, width=widths[c], anchor=anchor)
        tree.heading("error_type",  text=t("tc_col_error_type"))
        tree.heading("language",    text=t("tc_col_language"))
        tree.heading("opus",        text=t("tc_col_opus"))
        tree.heading("score",       text=t("tc_col_score"))
        tree.heading("source_text", text=t("tc_col_source_text"))
        tree.heading("translation", text=t("tc_col_translation"))
        tree.heading("reason",      text=t("tc_col_reason"))

        sb = ttk.Scrollbar(tbl_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for r in issues:
            sc = r.get("final_score")
            sc_disp = f"{sc:.1f}" if isinstance(sc, (int, float)) else "—"
            tree.insert("", "end", values=(
                r.get("error_type", ""),
                r.get("target_language") or "—",
                _short(r.get("opus_id") or "", 50),
                sc_disp,
                _short(r.get("source_text") or "", 50),
                _short(r.get("translated_text") or "", 50),
                _short(r.get("reason") or "", 60),
            ))

        # ── 底部按钮 ──
        btn_row = ttk.Frame(outer, style="App.TFrame")
        btn_row.pack(fill="x", pady=(10, 0))

        # 收集全部 opus_id，复制用
        self._all_opus_ids = [
            (r.get("opus_id") or "").strip()
            for r in issues
            if (r.get("opus_id") or "").strip()
        ]
        self._copy_default_text = t("tc_summary_copy_ids")
        self.btn_copy = tk.Button(
            btn_row, text=self._copy_default_text,
            command=self._copy_opus_ids,
            font=(FONT_FAMILY, 10),
            bg="#27AE60", fg="#fff",
            activebackground="#2ecc71", activeforeground="#fff",
            relief="flat", padx=14, pady=4, cursor="hand2")
        if not self._all_opus_ids:
            self.btn_copy.configure(state="disabled")
        self.btn_copy.pack(side="left")

        self.btn_open_tranzor = tk.Button(
            btn_row, text=t("tc_summary_open_tranzor"),
            command=self._open_in_tranzor,
            font=(FONT_FAMILY, 10),
            bg="#4472C4", fg="#fff",
            activebackground="#5482d4", activeforeground="#fff",
            relief="flat", padx=14, pady=4, cursor="hand2")
        self.btn_open_tranzor.pack(side="left", padx=(8, 0))

        tk.Button(
            btn_row, text=t("tc_summary_close"),
            command=self.destroy,
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#fff",
            activebackground="#1a3a6a", activeforeground="#fff",
            relief="flat", padx=18, pady=4, cursor="hand2"
        ).pack(side="right")

        # Ctrl+C 复制全部 opus_id（与 SyncDeltaDialog 一致的快捷键语义）
        self.bind("<Control-c>", lambda _e: self._copy_opus_ids())
        self.bind("<Control-C>", lambda _e: self._copy_opus_ids())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.transient(parent)

    def _copy_opus_ids(self):
        if not self._all_opus_ids:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append("\n".join(self._all_opus_ids))
            self.update()
        except Exception:
            return
        n = len(self._all_opus_ids)
        self.btn_copy.configure(
            text=self.app._t("tc_summary_copy_done").format(n=n),
            state="disabled")
        self.after(self._COPY_FEEDBACK_MS, self._restore_copy)

    def _restore_copy(self):
        try:
            if not self.btn_copy.winfo_exists():
                return
        except Exception:
            return
        self.btn_copy.configure(text=self._copy_default_text, state="normal")

    def _open_in_tranzor(self):
        """跳转到 Tranzor 平台上对应任务的静态页。URL 形式与
        gui_tab_tranzor_checks._open_selected_issue_in_tranzor 保持一致。
        """
        base = getattr(mr_api, "TRANZOR_URL", "")
        if not base:
            return
        kind = (self.summary.get("source_kind") or "").lower()
        task_id = self.summary.get("task_id") or ""
        project_id = self.summary.get("project_id") or ""
        mr_iid = self.summary.get("mr_iid")
        url = ""
        if kind == "mr" and project_id and mr_iid:
            from urllib.parse import quote
            url = f"{base}/static/?project_id={quote(project_id)}&mr_id={mr_iid}"
        elif kind == "file":
            url = f"{base}/static/legacy/tasks/{task_id}"
        elif kind == "scan":
            url = f"{base}/static/scans/{task_id}"
        if not url:
            return
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
