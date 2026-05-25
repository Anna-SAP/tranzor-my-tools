"""
OPUS ID Monitor — GUI Tab
=========================
"随时随地看 OPUS ID 总量 / 新增 / 按项目分布"的面板。

数据全部来自本地 SQLite 缓存（``opus_id_monitor`` 模块），首屏不依赖网络。
用户点击 "🔄 Sync" 才会向 Tranzor 拉增量；首次或选择性触发可做全量。

布局：
    顶部状态条：Last sync · Sync 按钮 · Mode 切换（incremental / full）
    summary 卡：4 张大数字（总 opus / 文件指纹 / 项目 / 今日新增）
    左：按项目分桶表（可点击列头排序）
    右：30 天每日新增（简易 Canvas 柱状图）+ 最近新增 opus_id 列表
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
#
# export_gui 在它的"optional tab import"段里会反向 import 我们这个模块
# 来读 STRINGS 做合并。如果我们把 STRINGS 放在 from-import 之后，
# 第一次合并时拿到的 module 还是 partial 状态、STRINGS 还没绑定，
# merge 在 ``except Exception: pass`` 里被静默吞掉，i18n 永远不进
# export_gui.STRINGS —— 用户看到的就是 button.text 是 i18n key 本身
# 而不是翻译。把 STRINGS 提到最顶部就能让这条路径恒等于成功。
# ---------------------------------------------------------------------------
STRINGS = {
    "en": {
        "tab_opus_monitor":         "🧬 OPUS ID Monitor",
        "opus_sync_now":            "🔄 Sync now",
        "opus_sync_full":           "Full re-sync",
        "opus_sync_cancel":         "Cancel",
        "opus_sync_now_tip":        (
            "Pull only tasks created since the last sync.\n"
            "Fast (seconds) — use this for routine refreshes.\n\n"
            "New here? Click 'Full re-sync' first to build the baseline."),
        "opus_sync_full_tip":       (
            "Re-pull ALL completed MR / Scan / File-Translation tasks\n"
            "and rebuild the local cache from scratch.\n\n"
            "Required on first run (~5-10 min depending on backend load).\n"
            "Re-run later only if you suspect cache drift."),
        "opus_sync_cancel_tip":     (
            "Abort an in-flight sync. Already-saved data stays intact;\n"
            "next sync will resume from where this one stopped."),
        "opus_last_sync":           "{elapsed} since last sync · {time}",
        "opus_last_sync_never":     "Never synced — click 'Full re-sync' first to build the baseline.",
        "opus_elapsed_just_now":    "just now",
        "opus_elapsed_minutes":     "{m}m",
        "opus_elapsed_hours":       "{h}h {m}m",
        "opus_elapsed_days":        "{d}d {h}h {m}m",
        "opus_card_total":          "OPUS IDs",
        "opus_card_files":          "Source files (path hashes)",
        "opus_card_projects":       "Projects",
        "opus_card_new_today":      "New today",
        "opus_card_new_7d":         "+{n} last 7d",
        "opus_card_new_30d":        "+{n} last 30d",
        "opus_card_rows":           "{n:,} rows total",
        "opus_breakdown_title":     "📊 Breakdown by project · double-click to drill down",
        "opus_col_project":         "Project (source)",
        "opus_col_alias":           "Alias",
        "opus_col_opus":            "OPUS IDs",
        "opus_col_files":           "Files",
        "opus_col_langs":           "Langs",
        "opus_col_last_added":      "Last added",
        "opus_trend_title":         "📈 New OPUS IDs · last 30 days",
        "opus_recent_title":        "📋 Recently added · double-click row",
        "opus_recent_col_time":     "First seen",
        "opus_recent_col_project":  "Project (source)",
        "opus_recent_col_alias":    "Alias",
        "opus_recent_col_opus":     "OPUS ID",
        "opus_status_idle":         "Idle.",
        "opus_status_syncing":      "Syncing… {stage} {cur}/{total}",
        "opus_status_done":         "✓ Sync done · MR +{mr} · Scan +{scan} · File +{legacy} rows",
        "opus_status_failed":       "❌ {error}",
        "opus_status_cancelled":    "⚠ Sync cancelled",
        # Drill-down dialogs
        "opus_dlg_project_title":   "Project · {project} ({source})",
        "opus_dlg_project_summary": (
            "OPUS IDs: {opus}  ·  Files: {files}  ·  Langs: {langs}  ·  "
            "Rows: {rows}\nFirst seen: {first}   Last added: {last}"),
        "opus_dlg_files_title":     "Source files in this project (top {n} by OPUS ID count)",
        "opus_dlg_files_col_pathhash":  "Path hash",
        "opus_dlg_files_col_opus":      "OPUS IDs",
        "opus_dlg_files_col_langs":     "Langs",
        "opus_dlg_files_col_last":      "Last added",
        "opus_dlg_files_col_samples":   "Sample keys",
        "opus_dlg_opus_title":      "OPUS ID detail",
        "opus_dlg_opus_full":       "Full OPUS ID",
        "opus_dlg_opus_alias":      "Alias (segment 2)",
        "opus_dlg_opus_pathhash":   "Path hash (segment 3 · md5 of source file path)",
        "opus_dlg_opus_logkey":     "Logical key (segment 4 · string-level key)",
        "opus_dlg_opus_project":    "Project",
        "opus_dlg_opus_source":     "Source pipeline",
        "opus_dlg_opus_mr":         "MR / task",
        "opus_dlg_opus_first":      "First seen by Tranzor",
        "opus_dlg_opus_first_local":"First seen by this tool",
        "opus_dlg_opus_text":       "Source text",
        "opus_dlg_opus_langs":      "Target languages ({n})",
        "opus_dlg_close":           "Close",
        "opus_dlg_copy":            "Copy",
        "opus_src_mr":              "MR",
        "opus_src_scan":            "Scan",
        "opus_src_file":            "File",
    },
    "zh": {
        "tab_opus_monitor":         "🧬 OPUS ID 监控",
        "opus_sync_now":            "🔄 立即同步",
        "opus_sync_full":           "全量重建",
        "opus_sync_cancel":         "取消",
        "opus_sync_now_tip":        (
            "仅拉取「上次同步之后」新创建的任务，秒级完成。\n"
            "适合日常刷新。\n\n"
            "首次使用请先点「全量重建」建立基线。"),
        "opus_sync_full_tip":       (
            "重新拉取所有已完成的 MR / Scan / File Translation 任务，\n"
            "从零重建本地缓存。\n\n"
            "首次使用必须执行（约 5-10 分钟，取决于后端负载）。\n"
            "怀疑缓存与真实状态有差异时也可再次执行。"),
        "opus_sync_cancel_tip":     (
            "中止正在进行的同步。已落库的数据不会丢失；\n"
            "下次同步会从中断处继续。"),
        "opus_last_sync":           "距上次同步 {elapsed} · {time}",
        "opus_last_sync_never":     "尚未同步 — 请先点「全量重建」建立基线。",
        "opus_elapsed_just_now":    "刚刚",
        "opus_elapsed_minutes":     "{m} 分钟",
        "opus_elapsed_hours":       "{h} 小时 {m} 分钟",
        "opus_elapsed_days":        "{d} 天 {h} 小时 {m} 分钟",
        "opus_card_total":          "OPUS ID 总数",
        "opus_card_files":          "源文件数（路径指纹）",
        "opus_card_projects":       "项目数",
        "opus_card_new_today":      "今日新增",
        "opus_card_new_7d":         "近 7 天 +{n}",
        "opus_card_new_30d":        "近 30 天 +{n}",
        "opus_card_rows":           "共 {n:,} 条记录",
        "opus_breakdown_title":     "📊 按项目分桶 · 双击钻取",
        "opus_col_project":         "项目（源头）",
        "opus_col_alias":           "Alias",
        "opus_col_opus":            "OPUS ID",
        "opus_col_files":           "源文件",
        "opus_col_langs":           "语言数",
        "opus_col_last_added":      "最近新增",
        "opus_trend_title":         "📈 近 30 天每日新增",
        "opus_recent_title":        "📋 最近新增 · 双击查看",
        "opus_recent_col_time":     "首次出现",
        "opus_recent_col_project":  "项目（源头）",
        "opus_recent_col_alias":    "Alias",
        "opus_recent_col_opus":     "OPUS ID",
        "opus_status_idle":         "空闲。",
        "opus_status_syncing":      "正在同步… {stage} {cur}/{total}",
        "opus_status_done":         "✓ 同步完成 · MR +{mr} · Scan +{scan} · File +{legacy} 行",
        "opus_status_failed":       "❌ {error}",
        "opus_status_cancelled":    "⚠ 同步已取消",
        # 钻取对话框
        "opus_dlg_project_title":   "项目 · {project} ({source})",
        "opus_dlg_project_summary": (
            "OPUS ID：{opus}  ·  源文件：{files}  ·  语言：{langs}  ·  "
            "总行：{rows}\n首次出现：{first}   最近新增：{last}"),
        "opus_dlg_files_title":     "本项目下的源文件（按 OPUS ID 数 Top {n}）",
        "opus_dlg_files_col_pathhash":  "Path hash",
        "opus_dlg_files_col_opus":      "OPUS ID",
        "opus_dlg_files_col_langs":     "语言",
        "opus_dlg_files_col_last":      "最近新增",
        "opus_dlg_files_col_samples":   "样本 key",
        "opus_dlg_opus_title":      "OPUS ID 详情",
        "opus_dlg_opus_full":       "完整 OPUS ID",
        "opus_dlg_opus_alias":      "Alias（第 2 段）",
        "opus_dlg_opus_pathhash":   "Path hash（第 3 段 · 源文件路径的 md5）",
        "opus_dlg_opus_logkey":     "Logical key（第 4 段 · 字符串级 key）",
        "opus_dlg_opus_project":    "项目",
        "opus_dlg_opus_source":     "源头管线",
        "opus_dlg_opus_mr":         "MR / 任务",
        "opus_dlg_opus_first":      "Tranzor 首次出现",
        "opus_dlg_opus_first_local":"本地缓存首次记录",
        "opus_dlg_opus_text":       "源文本",
        "opus_dlg_opus_langs":      "目标语言（{n} 种）",
        "opus_dlg_close":           "关闭",
        "opus_dlg_copy":            "复制",
        "opus_src_mr":              "MR",
        "opus_src_scan":            "Scan",
        "opus_src_file":            "File",
    },
}


# ---------------------------------------------------------------------------
# 本地 import —— 放在 STRINGS 之后；因为 export_gui 加载时会反向 import
# 我们，必须先让 STRINGS 在我们的 namespace 里存在，再触发这些 import。
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opus_id_monitor as om
# 同理，``Tooltip`` 类在 export_gui 内部的定义位置比"optional tab import"段
# 还要靠后；想用它必须懒加载（见 _build() 里的 ``from export_gui import
# Tooltip``）。这里只拿稳定在文件顶部的常量。
from export_gui import FONT_FAMILY, FONT_MONO, IS_MAC


def _fmt_iso_short(iso_str: str | None) -> str:
    """ISO 时间字符串 → '05-25 14:32' 这样人类可读的紧凑形式。"""
    if not iso_str:
        return "—"
    try:
        # 兼容带时区和不带时区两种
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return iso_str[:16]


def _humanize_elapsed(iso_str: str | None, t) -> str:
    """ISO 时间 → '2 days 4 hours 17 minutes' 这样的"逝去时长"文案。

    ``t`` 是 i18n 取词函数（self._t），决定走中文还是英文模板。
    返回三种粒度：
      - < 1 分钟 → 'just now' / '刚刚'
      - < 1 小时 → '37m' / '37 分钟'
      - < 1 天   → '2h 15m' / '2 小时 15 分钟'
      - 其他     → '3d 4h 17m' / '3 天 4 小时 17 分钟'
    粒度卡到分钟够用了；秒级抖动反而吵眼睛。
    """
    if not iso_str:
        return "—"
    try:
        from datetime import timezone as _tz
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        now = datetime.now(_tz.utc)
        delta = now - dt
        total_minutes = int(delta.total_seconds() // 60)
    except Exception:
        return "—"

    if total_minutes < 1:
        return t("opus_elapsed_just_now")
    if total_minutes < 60:
        return t("opus_elapsed_minutes").format(m=total_minutes)
    if total_minutes < 60 * 24:
        return t("opus_elapsed_hours").format(
            h=total_minutes // 60, m=total_minutes % 60)
    days = total_minutes // (60 * 24)
    rem = total_minutes % (60 * 24)
    return t("opus_elapsed_days").format(
        d=days, h=rem // 60, m=rem % 60)


def _source_label(source_kind: str, t) -> str:
    """'mr' → 'MR'；'scan' → 'Scan'；'file' → 'File'。i18n 友好。"""
    key = {"mr": "opus_src_mr", "scan": "opus_src_scan",
           "file": "opus_src_file"}.get((source_kind or "").lower())
    return t(key) if key else (source_kind or "?")


def _project_label(project_id: str, source_kind: str, t) -> str:
    """渲染成 'web/web (MR)' 形态。空 source 时回退为纯 project_id。"""
    src = _source_label(source_kind, t)
    if not project_id:
        return f"— ({src})" if src else "—"
    return f"{project_id} ({src})" if src else project_id


class OpusIdMonitorTab:
    """OPUS ID 监控面板。"""

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self._sync_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._build(parent)
        # 启动后立即用本地缓存渲染首屏（不触发网络）
        self.parent.after(50, self._refresh_from_cache)

    def _t(self, key):
        return self.app._t(key)

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------
    def _build(self, parent):
        content = ttk.Frame(parent, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=16, pady=8)

        # ── Top bar: Sync controls + last-sync indicator ──
        topbar = ttk.Frame(content, style="App.TFrame")
        topbar.pack(fill="x", pady=(0, 8))

        self.btn_sync = self.app._create_button(
            topbar, text="", command=self._on_sync_incremental,
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#2ecc71", fg="#fff", padx=14, pady=4)
        self.btn_sync.pack(side="left")
        # 见文件头部注释：Tooltip 走懒加载避免循环 import。
        from export_gui import Tooltip
        # Hover infotip: tells first-time users what incremental sync does and
        # nudges them toward Full re-sync first (since incremental on an empty
        # cache silently does nothing — easy to mistake for "tool broken").
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

        self.lbl_last_sync = ttk.Label(topbar, text="", style="Status.TLabel")
        self.lbl_last_sync.pack(side="left", padx=(16, 0))
        # Tick the "X minutes since" label every minute so it stays honest
        # even when the user is staring at it for a while.
        self._schedule_elapsed_tick()

        self.lbl_status = ttk.Label(topbar, text="", style="Status.TLabel")
        self.lbl_status.pack(side="right")

        # ── Summary cards row ──
        cards_row = ttk.Frame(content, style="App.TFrame")
        cards_row.pack(fill="x", pady=(0, 10))

        self.card_total = _SummaryCard(cards_row, color="#4472C4")
        self.card_total.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.card_files = _SummaryCard(cards_row, color="#E67E22")
        self.card_files.pack(side="left", expand=True, fill="x", padx=6)
        self.card_projects = _SummaryCard(cards_row, color="#27AE60")
        self.card_projects.pack(side="left", expand=True, fill="x", padx=6)
        self.card_new = _SummaryCard(cards_row, color="#8E44AD")
        self.card_new.pack(side="left", expand=True, fill="x", padx=(6, 0))

        # ── Main body: left = breakdown table, right = trend + recent ──
        body = ttk.Frame(content, style="App.TFrame")
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body, style="App.TFrame")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        right = ttk.Frame(body, style="App.TFrame", width=420)
        right.pack(side="right", fill="both", padx=(8, 0))
        right.pack_propagate(False)

        # Left: Per-project breakdown
        self.lbl_breakdown = ttk.Label(left, text="", style="CardBold.TLabel")
        self.lbl_breakdown.pack(anchor="w", pady=(0, 4))
        bd_frame = ttk.Frame(left, style="App.TFrame")
        bd_frame.pack(fill="both", expand=True)
        cols = ("project", "alias", "opus", "files", "langs", "last_added")
        self.tree_breakdown = ttk.Treeview(
            bd_frame, columns=cols, show="headings",
            style="Summary.Treeview", selectmode="browse")
        widths = {"project": 240, "alias": 60, "opus": 80, "files": 70,
                  "langs": 60, "last_added": 110}
        for c in cols:
            anchor = "w" if c in ("project",) else "center"
            self.tree_breakdown.column(
                c, width=widths.get(c, 80), anchor=anchor)
            # 让列头点击触发排序
            self.tree_breakdown.heading(
                c, text="", command=lambda col=c: self._sort_breakdown(col))
        sb = ttk.Scrollbar(bd_frame, orient="vertical",
                           command=self.tree_breakdown.yview)
        self.tree_breakdown.configure(yscrollcommand=sb.set)
        self.tree_breakdown.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._breakdown_sort = ("opus", True)  # (col, desc)

        # Right top: Trend chart
        self.lbl_trend = ttk.Label(right, text="", style="CardBold.TLabel")
        self.lbl_trend.pack(anchor="w", pady=(0, 4))
        self.canvas_trend = tk.Canvas(
            right, height=140, bg="#0a0a1a", highlightthickness=0)
        self.canvas_trend.pack(fill="x", pady=(0, 10))
        # 监听 resize 后重画，柱状图自适应宽度
        self.canvas_trend.bind("<Configure>", lambda _e: self._draw_trend())

        # Right bottom: Recent additions
        self.lbl_recent = ttk.Label(right, text="", style="CardBold.TLabel")
        self.lbl_recent.pack(anchor="w", pady=(0, 4))
        rc_frame = ttk.Frame(right, style="App.TFrame")
        rc_frame.pack(fill="both", expand=True)
        rcols = ("time", "project", "alias", "opus")
        self.tree_recent = ttk.Treeview(
            rc_frame, columns=rcols, show="headings",
            style="Summary.Treeview", selectmode="browse", height=10)
        rwidths = {"time": 75, "project": 95, "alias": 50, "opus": 180}
        for c in rcols:
            anchor = "w" if c in ("project", "opus") else "center"
            self.tree_recent.column(
                c, width=rwidths.get(c, 80), anchor=anchor)
            self.tree_recent.heading(c, text="")
        rsb = ttk.Scrollbar(rc_frame, orient="vertical",
                            command=self.tree_recent.yview)
        self.tree_recent.configure(yscrollcommand=rsb.set)
        self.tree_recent.pack(side="left", fill="both", expand=True)
        rsb.pack(side="right", fill="y")

        # 持有数据以便 sort 时直接重画，避免再查 DB
        self._trend_data: list[dict] = []
        self._breakdown_data: list[dict] = []
        self._recent_data: list[dict] = []
        # 记下 tree row → 业务键的映射，双击时拿来打开详情对话框
        self._breakdown_row_keys: dict[str, tuple[str, str]] = {}
        self._recent_row_keys: dict[str, str] = {}

        # 双击钻取
        self.tree_breakdown.bind("<Double-1>", self._on_breakdown_dbl)
        self.tree_recent.bind("<Double-1>", self._on_recent_dbl)

    # ------------------------------------------------------------------
    # i18n refresh
    # ------------------------------------------------------------------
    def refresh_text(self):
        t = self._t
        self.btn_sync.configure(text=t("opus_sync_now"))
        self.btn_sync_full.configure(text=t("opus_sync_full"))
        self.btn_sync_cancel.configure(text=t("opus_sync_cancel"))
        # Re-bind tooltip text in the current language
        self._tip_sync.set_text(t("opus_sync_now_tip"))
        self._tip_sync_full.set_text(t("opus_sync_full_tip"))
        self._tip_sync_cancel.set_text(t("opus_sync_cancel_tip"))
        self.lbl_breakdown.configure(text=t("opus_breakdown_title"))
        self.lbl_trend.configure(text=t("opus_trend_title"))
        self.lbl_recent.configure(text=t("opus_recent_title"))
        for c, key in (
            ("project", "opus_col_project"),
            ("alias", "opus_col_alias"),
            ("opus", "opus_col_opus"),
            ("files", "opus_col_files"),
            ("langs", "opus_col_langs"),
            ("last_added", "opus_col_last_added"),
        ):
            self.tree_breakdown.heading(
                c, text=t(key),
                command=lambda col=c: self._sort_breakdown(col))
        for c, key in (
            ("time", "opus_recent_col_time"),
            ("project", "opus_recent_col_project"),
            ("alias", "opus_recent_col_alias"),
            ("opus", "opus_recent_col_opus"),
        ):
            self.tree_recent.heading(c, text=t(key))
        # Card titles (set on the card widget itself)
        self.card_total.set_title(t("opus_card_total"))
        self.card_files.set_title(t("opus_card_files"))
        self.card_projects.set_title(t("opus_card_projects"))
        self.card_new.set_title(t("opus_card_new_today"))
        # Re-render dynamic labels in the new language
        self._refresh_from_cache()

    # ------------------------------------------------------------------
    # 渲染：从本地 SQLite 拉数据填面板
    # ------------------------------------------------------------------
    def _refresh_from_cache(self):
        try:
            summary = om.get_summary()
            breakdown = om.get_per_project_breakdown()
            trend = om.get_daily_trend(days=30)
            recent = om.get_recent_additions(limit=50)
        except Exception as e:
            self.lbl_status.configure(
                text=self._t("opus_status_failed").format(error=str(e)[:60]))
            return

        # 卡片
        self.card_total.set_value(f"{summary['total_opus_ids']:,}")
        self.card_total.set_subtitle(
            self._t("opus_card_rows").format(n=summary["total_rows"]))
        self.card_files.set_value(f"{summary['total_path_hashes']:,}")
        self.card_files.set_subtitle(
            f"alias × {summary['total_aliases']}")
        self.card_projects.set_value(f"{summary['total_projects']:,}")
        self.card_projects.set_subtitle("")
        self.card_new.set_value(f"+{summary['new_today']:,}")
        self.card_new.set_subtitle(
            self._t("opus_card_new_7d").format(n=summary["new_7d"]) + " · "
            + self._t("opus_card_new_30d").format(n=summary["new_30d"]))

        # 上次同步时间 —— 显示 "X 天 Y 小时 Z 分钟前 · 05-25 15:58"
        # 这样用户既能感知时间流逝，也能精确看到具体什么时候同步的。
        self._last_sync_iso = summary.get("last_sync_at")
        self._render_last_sync_label()

        # 表格 + 图
        self._breakdown_data = breakdown
        self._render_breakdown()
        self._trend_data = trend
        self._draw_trend()
        self._render_recent(recent)

    def _render_breakdown(self):
        col, desc = self._breakdown_sort
        key = {
            "project": "project_id",
            "alias": "alias",
            "opus": "opus_count",
            "files": "path_count",
            "langs": "lang_count",
            "last_added": "last_added",
        }.get(col, "opus_count")
        rows = sorted(
            self._breakdown_data,
            key=lambda r: (r.get(key) or 0) if key != "project_id"
                            and key != "alias" and key != "last_added"
                          else (r.get(key) or ""),
            reverse=desc,
        )
        self.tree_breakdown.delete(*self.tree_breakdown.get_children())
        # 记下 iid → (project_id, source_kind)，双击展开时用得到。
        # 不再依赖列里渲染的"project_id (MR)"字符串去逆向解析（脆且歧义）。
        self._breakdown_row_keys.clear()
        t = self._t
        for r in rows:
            project = r.get("project_id", "")
            source = r.get("source_kind", "")
            label = _project_label(project, source, t)
            iid = self.tree_breakdown.insert("", "end", values=(
                label,
                r.get("alias", ""),
                f"{r.get('opus_count', 0):,}",
                f"{r.get('path_count', 0):,}",
                r.get("lang_count", 0),
                _fmt_iso_short(r.get("last_added", "")),
            ))
            self._breakdown_row_keys[iid] = (project, source)

    def _sort_breakdown(self, col):
        cur_col, cur_desc = self._breakdown_sort
        if col == cur_col:
            self._breakdown_sort = (col, not cur_desc)
        else:
            # 默认数字列降序、文本列升序
            self._breakdown_sort = (
                col, col in ("opus", "files", "langs", "last_added"))
        self._render_breakdown()

    def _render_recent(self, recent: list[dict]):
        self.tree_recent.delete(*self.tree_recent.get_children())
        self._recent_data = recent
        self._recent_row_keys.clear()
        t = self._t
        for r in recent:
            opus = r.get("opus_id", "")
            # 中段太长不易看，做软截断；双击仍能拿到完整 opus_id
            disp = opus if len(opus) <= 60 else opus[:30] + "…" + opus[-25:]
            iid = self.tree_recent.insert("", "end", values=(
                _fmt_iso_short(r.get("first_seen", "")),
                _project_label(r.get("project_id", ""),
                                r.get("source_kind", ""), t),
                r.get("alias", ""),
                disp,
            ))
            self._recent_row_keys[iid] = opus

    def _draw_trend(self):
        cv = self.canvas_trend
        cv.delete("all")
        if not self._trend_data:
            return
        w = cv.winfo_width() or 400
        h = cv.winfo_height() or 140
        pad_l, pad_r, pad_t, pad_b = 4, 4, 8, 18
        chart_w = max(40, w - pad_l - pad_r)
        chart_h = max(20, h - pad_t - pad_b)
        n = len(self._trend_data)
        max_v = max((d["new_count"] for d in self._trend_data), default=0) or 1
        bar_w = max(2, chart_w / n - 1)

        for i, d in enumerate(self._trend_data):
            v = d["new_count"]
            bh = (v / max_v) * chart_h if max_v else 0
            x0 = pad_l + i * (chart_w / n)
            x1 = x0 + bar_w
            y0 = pad_t + (chart_h - bh)
            y1 = pad_t + chart_h
            # 颜色用渐变：今天偏暖、越早越冷
            ratio = i / max(1, n - 1)
            color = "#%02x%02x%02x" % (
                int(60 + 195 * ratio),
                int(120 + 30 * ratio),
                int(220 - 40 * ratio),
            )
            cv.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
            if v > 0:
                cv.create_text(
                    (x0 + x1) / 2, y0 - 6,
                    text=str(v), fill="#ccc",
                    font=(FONT_FAMILY, 8))

        # 底部 x 轴：首日 / 中间 / 末日
        if n >= 2:
            for tick, label in (
                (0, self._trend_data[0]["date"][5:]),
                (n // 2, self._trend_data[n // 2]["date"][5:]),
                (n - 1, self._trend_data[-1]["date"][5:]),
            ):
                tx = pad_l + tick * (chart_w / n) + bar_w / 2
                cv.create_text(
                    tx, h - 6, text=label,
                    fill="#888", font=(FONT_FAMILY, 8))

    # ------------------------------------------------------------------
    # Sync — runs in background thread, UI updates via after()
    # ------------------------------------------------------------------
    def _on_sync_incremental(self):
        self._kickoff_sync(full=False)

    def _on_sync_full(self):
        self._kickoff_sync(full=True)

    def _on_cancel(self):
        self._cancel_event.set()

    # 同步期间的活体刷新节拍。3 秒一次：既能让用户看到卡片在涨，
    # 又不会因为反复查 SQLite 抢同步线程的 commit 时间。
    _LIVE_REFRESH_MS = 3000

    def _kickoff_sync(self, *, full: bool):
        if self._sync_thread and self._sync_thread.is_alive():
            return  # 不允许并发同步
        self._cancel_event.clear()
        self._set_sync_buttons(running=True)
        self.lbl_status.configure(
            text=self._t("opus_status_syncing").format(
                stage="init", cur=0, total=0))
        self._sync_thread = threading.Thread(
            target=self._run_sync, args=(full,), daemon=True)
        self._sync_thread.start()
        # 用户最大的痛点：同步期间卡片永远是 0，看上去像卡死。
        # 启动一个 after 链路，只要后台线程还活着，就每 3 秒把本地
        # SQLite 里已经落地的数据查一次刷上来。
        self.parent.after(
            self._LIVE_REFRESH_MS, self._tick_live_refresh)

    def _tick_live_refresh(self):
        """同步期间的定时刷新；线程死掉就自动停止。"""
        if not (self._sync_thread and self._sync_thread.is_alive()):
            return  # 同步线程已结束，最终刷新已由 _run_sync 触发
        try:
            self._refresh_from_cache()
        except Exception:
            pass  # 刷新失败不能影响同步本身
        self.parent.after(self._LIVE_REFRESH_MS, self._tick_live_refresh)

    # ------------------------------------------------------------------
    # "X minutes since last sync" 标签 —— 每分钟自刷
    # ------------------------------------------------------------------
    _ELAPSED_TICK_MS = 60_000  # 每分钟更新一次

    def _schedule_elapsed_tick(self):
        self.parent.after(self._ELAPSED_TICK_MS, self._tick_elapsed)

    def _tick_elapsed(self):
        try:
            self._render_last_sync_label()
        except Exception:
            pass
        self._schedule_elapsed_tick()

    def _render_last_sync_label(self):
        last = getattr(self, "_last_sync_iso", None)
        if not last:
            self.lbl_last_sync.configure(text=self._t("opus_last_sync_never"))
            return
        self.lbl_last_sync.configure(
            text=self._t("opus_last_sync").format(
                elapsed=_humanize_elapsed(last, self._t),
                time=_fmt_iso_short(last),
            ))

    # ------------------------------------------------------------------
    # 双击钻取 —— 弹出详情对话框
    # ------------------------------------------------------------------
    def _on_breakdown_dbl(self, _event=None):
        sel = self.tree_breakdown.selection()
        if not sel:
            return
        key = self._breakdown_row_keys.get(sel[0])
        if not key:
            return
        project_id, source_kind = key
        try:
            detail = om.get_project_detail(project_id, source_kind)
        except Exception as e:
            self.lbl_status.configure(
                text=self._t("opus_status_failed").format(error=str(e)[:60]))
            return
        ProjectDetailDialog(self.parent, self.app, detail)

    def _on_recent_dbl(self, _event=None):
        sel = self.tree_recent.selection()
        if not sel:
            return
        opus_id = self._recent_row_keys.get(sel[0])
        if not opus_id:
            return
        try:
            detail = om.get_opus_detail(opus_id)
        except Exception as e:
            self.lbl_status.configure(
                text=self._t("opus_status_failed").format(error=str(e)[:60]))
            return
        OpusDetailDialog(self.parent, self.app, detail)

    def _set_sync_buttons(self, *, running: bool):
        new_state = ["disabled"] if running else ["!disabled"]
        cancel_state = ["!disabled"] if running else ["disabled"]
        if IS_MAC:
            self.btn_sync.state(new_state)
            self.btn_sync_full.state(new_state)
            self.btn_sync_cancel.state(cancel_state)
        else:
            s = "disabled" if running else "normal"
            cs = "normal" if running else "disabled"
            self.btn_sync.configure(state=s)
            self.btn_sync_full.configure(state=s)
            self.btn_sync_cancel.configure(state=cs)

    def _run_sync(self, full: bool):
        try:
            def progress(stage, cur, total, **kw):
                # 后台线程只能用 after() 回主线程更新 UI
                self.parent.after(
                    0,
                    lambda s=stage, c=cur, tt=total: self.lbl_status.configure(
                        text=self._t("opus_status_syncing").format(
                            stage=s, cur=c, total=tt)))

            if full:
                result = om.sync_full(
                    progress_callback=progress,
                    cancel_event=self._cancel_event)
            else:
                result = om.sync_incremental(
                    progress_callback=progress,
                    cancel_event=self._cancel_event)

            if self._cancel_event.is_set():
                self.parent.after(0, lambda: self.lbl_status.configure(
                    text=self._t("opus_status_cancelled")))
            else:
                mr_rows = result.get("mr", {}).get("rows_inserted", 0)
                scan_rows = result.get("scan", {}).get("rows_inserted", 0)
                legacy_rows = result.get("legacy", {}).get("rows_inserted", 0)
                self.parent.after(
                    0, lambda: self.lbl_status.configure(
                        text=self._t("opus_status_done").format(
                            mr=mr_rows, scan=scan_rows, legacy=legacy_rows)))
        except Exception as e:
            err = str(e)[:80]
            self.parent.after(0, lambda: self.lbl_status.configure(
                text=self._t("opus_status_failed").format(error=err)))
        finally:
            self.parent.after(0, lambda: self._set_sync_buttons(running=False))
            # 同步完了刷新一次面板
            self.parent.after(100, self._refresh_from_cache)


# ---------------------------------------------------------------------------
# 简易卡片组件 —— 大数字 + 标题 + 副标题
# ---------------------------------------------------------------------------
class _SummaryCard(tk.Frame):
    """风格化的指标卡，与现有 sidebar 风格保持一致。"""

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
# 钻取对话框：点击 Breakdown 行 → 该项目下所有文件 + 样本 opus_id
# ---------------------------------------------------------------------------
class ProjectDetailDialog(tk.Toplevel):
    """模态对话框：展示某 (project_id, source_kind) 的下钻数据。

    不阻塞主面板（用户可继续点别的行打开多个），靠 Toplevel 自带的
    窗口管理就够了。
    """

    def __init__(self, parent, app, detail: dict):
        super().__init__(parent)
        self.app = app
        self.detail = detail
        t = app._t
        project = detail.get("project_id", "")
        source = _source_label(detail.get("source_kind", ""), t)
        self.title(t("opus_dlg_project_title").format(
            project=project, source=source))
        self.configure(bg="#16213e")
        self.geometry("840x540")

        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=16, pady=12)

        # Summary header
        summary = detail.get("summary") or {}
        lbl = tk.Label(
            outer,
            text=t("opus_dlg_project_summary").format(
                opus=summary.get("opus_count", 0),
                files=summary.get("path_count", 0),
                langs=summary.get("lang_count", 0),
                rows=summary.get("row_count", 0),
                first=_fmt_iso_short(summary.get("first_seen")),
                last=_fmt_iso_short(summary.get("last_added")),
            ),
            bg="#16213e", fg="#ccc",
            font=(FONT_FAMILY, 10), justify="left", anchor="w")
        lbl.pack(fill="x", pady=(0, 8))

        # Per-file table
        files = detail.get("files") or []
        files_title = ttk.Label(
            outer,
            text=t("opus_dlg_files_title").format(n=len(files)),
            style="CardBold.TLabel")
        files_title.pack(anchor="w", pady=(0, 4))

        tbl_frame = ttk.Frame(outer, style="App.TFrame")
        tbl_frame.pack(fill="both", expand=True)
        cols = ("pathhash", "opus", "langs", "last", "samples")
        tree = ttk.Treeview(
            tbl_frame, columns=cols, show="headings",
            style="Summary.Treeview", selectmode="browse")
        widths = {"pathhash": 240, "opus": 70, "langs": 50,
                  "last": 110, "samples": 280}
        for c in cols:
            anchor = "w" if c in ("pathhash", "samples") else "center"
            tree.column(c, width=widths.get(c, 80), anchor=anchor)
        tree.heading("pathhash", text=t("opus_dlg_files_col_pathhash"))
        tree.heading("opus",     text=t("opus_dlg_files_col_opus"))
        tree.heading("langs",    text=t("opus_dlg_files_col_langs"))
        tree.heading("last",     text=t("opus_dlg_files_col_last"))
        tree.heading("samples",  text=t("opus_dlg_files_col_samples"))

        sb = ttk.Scrollbar(tbl_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for f in files:
            # 把样本拼成一串短文本，列内能看到 3-5 个
            samples = f.get("samples") or []
            sample_keys = [
                (s.get("logical_key") or "")[:14] for s in samples[:5]
            ]
            sample_text = ", ".join(k for k in sample_keys if k)
            tree.insert("", "end", values=(
                f.get("path_hash", "")[:32],
                f"{f.get('opus_count', 0):,}",
                f.get("lang_count", 0),
                _fmt_iso_short(f.get("last_added")),
                sample_text,
            ))

        # Close button row
        btn_row = ttk.Frame(outer, style="App.TFrame")
        btn_row.pack(fill="x", pady=(8, 0))
        close_btn = app._create_button(
            btn_row, text=t("opus_dlg_close"), command=self.destroy,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        close_btn.pack(side="right")

        # 让 ESC / 窗口关都能优雅退出
        self.bind("<Escape>", lambda _e: self.destroy())
        self.transient(parent)


# ---------------------------------------------------------------------------
# 钻取对话框：点击 Recently added 行 → 该 opus_id 完整画像
# ---------------------------------------------------------------------------
class OpusDetailDialog(tk.Toplevel):
    """单个 opus_id 的"全身像"：完整 ID、4 段分解、所有目标语言、源文本。"""

    def __init__(self, parent, app, detail: dict):
        super().__init__(parent)
        self.app = app
        self.detail = detail
        t = app._t
        self.title(t("opus_dlg_opus_title"))
        self.configure(bg="#16213e")
        self.geometry("780x560")

        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=16, pady=12)

        opus_id = detail.get("opus_id", "")
        # 顶部：完整 opus_id + Copy 按钮（最常用的就是把它复制走）
        head = ttk.Frame(outer, style="App.TFrame")
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text=t("opus_dlg_opus_full") + ":",
                  style="CardBold.TLabel").pack(side="left")
        copy_btn = app._create_button(
            head, text=t("opus_dlg_copy"),
            command=lambda: self._copy(opus_id),
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 9),
            bg="#0f3460", fg="#ccc", padx=10, pady=2)
        copy_btn.pack(side="right")

        full_id_box = tk.Text(
            outer, height=2, wrap="word",
            bg="#0a0a1a", fg="#fff", relief="flat",
            font=(FONT_FAMILY, 10), padx=8, pady=6)
        full_id_box.insert("1.0", opus_id)
        full_id_box.configure(state="disabled")
        full_id_box.pack(fill="x", pady=(0, 8))

        # 4-段分解 + 元数据
        meta = [
            (t("opus_dlg_opus_alias"),     detail.get("alias", "")),
            (t("opus_dlg_opus_pathhash"),  detail.get("path_hash", "")),
            (t("opus_dlg_opus_logkey"),    detail.get("logical_key", "")),
            (t("opus_dlg_opus_project"),
                _project_label(detail.get("project_id", ""),
                                detail.get("source_kind", ""), t)),
            (t("opus_dlg_opus_source"),
                _source_label(detail.get("source_kind", ""), t)),
            (t("opus_dlg_opus_mr"),
                str(detail.get("mr_iid") or detail.get("task_id", ""))),
            (t("opus_dlg_opus_first"),
                _fmt_iso_short(detail.get("task_created_at"))),
            (t("opus_dlg_opus_first_local"),
                _fmt_iso_short(detail.get("first_seen"))),
        ]
        meta_frame = ttk.Frame(outer, style="App.TFrame")
        meta_frame.pack(fill="x", pady=(0, 8))
        for row_i, (label, value) in enumerate(meta):
            tk.Label(
                meta_frame, text=label + ":",
                bg="#16213e", fg="#9aa0b0",
                font=(FONT_FAMILY, 9), anchor="e",
                width=36,
            ).grid(row=row_i, column=0, sticky="e", padx=(0, 8), pady=1)
            tk.Label(
                meta_frame, text=value or "—",
                bg="#16213e", fg="#fff",
                font=(FONT_MONO if "logkey" in label.lower()
                      or "path" in label.lower()
                      or "alias" in label.lower()
                      else FONT_FAMILY, 9),
                anchor="w",
            ).grid(row=row_i, column=1, sticky="w", pady=1)

        # 源文本（可能很长）—— 单独一行可滚动 Text
        src_label = ttk.Label(
            outer, text=t("opus_dlg_opus_text") + ":",
            style="CardBold.TLabel")
        src_label.pack(anchor="w", pady=(8, 4))
        src_box = tk.Text(
            outer, height=3, wrap="word",
            bg="#0a0a1a", fg="#fff", relief="flat",
            font=(FONT_FAMILY, 10), padx=8, pady=6)
        src_box.insert("1.0", detail.get("source_text") or "")
        src_box.configure(state="disabled")
        src_box.pack(fill="x", pady=(0, 8))

        # 目标语言列表
        langs = detail.get("target_languages") or []
        ttk.Label(
            outer,
            text=t("opus_dlg_opus_langs").format(n=len(langs)),
            style="CardBold.TLabel").pack(anchor="w", pady=(0, 4))
        langs_box = tk.Text(
            outer, height=4, wrap="word",
            bg="#0a0a1a", fg="#ccc", relief="flat",
            font=(FONT_MONO, 10), padx=8, pady=6)
        langs_box.insert("1.0", ", ".join(
            l.get("target_language", "") for l in langs))
        langs_box.configure(state="disabled")
        langs_box.pack(fill="x", pady=(0, 8))

        # Close
        btn_row = ttk.Frame(outer, style="App.TFrame")
        btn_row.pack(fill="x", pady=(8, 0))
        close_btn = app._create_button(
            btn_row, text=t("opus_dlg_close"), command=self.destroy,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        close_btn.pack(side="right")

        self.bind("<Escape>", lambda _e: self.destroy())
        self.transient(parent)

    def _copy(self, text: str):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            # 不弹 toast；按钮文案瞬时变化就够
        except Exception:
            pass
