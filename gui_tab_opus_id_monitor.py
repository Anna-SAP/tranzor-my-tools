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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opus_id_monitor as om
from export_gui import FONT_FAMILY, IS_MAC


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------
STRINGS = {
    "en": {
        "tab_opus_monitor":         "🧬 OPUS ID Monitor",
        "opus_sync_now":            "🔄 Sync now",
        "opus_sync_full":           "Full re-sync",
        "opus_sync_cancel":         "Cancel",
        "opus_last_sync":           "Last sync: {time}",
        "opus_last_sync_never":     "Never synced — click Sync to populate.",
        "opus_card_total":          "OPUS IDs",
        "opus_card_files":          "Source files (path hashes)",
        "opus_card_projects":       "Projects",
        "opus_card_new_today":      "New today",
        "opus_card_new_7d":         "+{n} last 7d",
        "opus_card_new_30d":        "+{n} last 30d",
        "opus_card_rows":           "{n:,} rows total",
        "opus_breakdown_title":     "📊 Breakdown by project",
        "opus_col_project":         "Project",
        "opus_col_alias":           "Alias",
        "opus_col_opus":            "OPUS IDs",
        "opus_col_files":           "Files",
        "opus_col_langs":           "Langs",
        "opus_col_last_added":      "Last added",
        "opus_trend_title":         "📈 New OPUS IDs · last 30 days",
        "opus_recent_title":        "📋 Recently added",
        "opus_recent_col_time":     "First seen",
        "opus_recent_col_project":  "Project",
        "opus_recent_col_alias":    "Alias",
        "opus_recent_col_opus":     "OPUS ID",
        "opus_status_idle":         "Idle.",
        "opus_status_syncing":      "Syncing… {stage} {cur}/{total}",
        "opus_status_done":         "✓ Sync done · MR +{mr} rows · Scan +{scan} rows",
        "opus_status_failed":       "❌ {error}",
        "opus_status_cancelled":    "⚠ Sync cancelled",
    },
    "zh": {
        "tab_opus_monitor":         "🧬 OPUS ID 监控",
        "opus_sync_now":            "🔄 立即同步",
        "opus_sync_full":           "全量重建",
        "opus_sync_cancel":         "取消",
        "opus_last_sync":           "上次同步：{time}",
        "opus_last_sync_never":     "尚未同步 — 点击「立即同步」以拉取数据。",
        "opus_card_total":          "OPUS ID 总数",
        "opus_card_files":          "源文件数（路径指纹）",
        "opus_card_projects":       "项目数",
        "opus_card_new_today":      "今日新增",
        "opus_card_new_7d":         "近 7 天 +{n}",
        "opus_card_new_30d":        "近 30 天 +{n}",
        "opus_card_rows":           "共 {n:,} 条记录",
        "opus_breakdown_title":     "📊 按项目分桶",
        "opus_col_project":         "项目",
        "opus_col_alias":           "Alias",
        "opus_col_opus":            "OPUS ID",
        "opus_col_files":           "源文件",
        "opus_col_langs":           "语言数",
        "opus_col_last_added":      "最近新增",
        "opus_trend_title":         "📈 近 30 天每日新增",
        "opus_recent_title":        "📋 最近新增",
        "opus_recent_col_time":     "首次出现",
        "opus_recent_col_project":  "项目",
        "opus_recent_col_alias":    "Alias",
        "opus_recent_col_opus":     "OPUS ID",
        "opus_status_idle":         "空闲。",
        "opus_status_syncing":      "正在同步… {stage} {cur}/{total}",
        "opus_status_done":         "✓ 同步完成 · MR +{mr} 行 · Scan +{scan} 行",
        "opus_status_failed":       "❌ {error}",
        "opus_status_cancelled":    "⚠ 同步已取消",
    },
}


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

        self.btn_sync_full = self.app._create_button(
            topbar, text="", command=self._on_sync_full,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        self.btn_sync_full.pack(side="left", padx=(8, 0))

        self.btn_sync_cancel = self.app._create_button(
            topbar, text="", command=self._on_cancel,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4, state="disabled")
        self.btn_sync_cancel.pack(side="left", padx=(8, 0))

        self.lbl_last_sync = ttk.Label(topbar, text="", style="Status.TLabel")
        self.lbl_last_sync.pack(side="left", padx=(16, 0))

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

    # ------------------------------------------------------------------
    # i18n refresh
    # ------------------------------------------------------------------
    def refresh_text(self):
        t = self._t
        self.btn_sync.configure(text=t("opus_sync_now"))
        self.btn_sync_full.configure(text=t("opus_sync_full"))
        self.btn_sync_cancel.configure(text=t("opus_sync_cancel"))
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

        # 上次同步时间
        last = summary.get("last_sync_at")
        if last:
            self.lbl_last_sync.configure(
                text=self._t("opus_last_sync").format(time=_fmt_iso_short(last)))
        else:
            self.lbl_last_sync.configure(text=self._t("opus_last_sync_never"))

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
        for r in rows:
            self.tree_breakdown.insert("", "end", values=(
                r.get("project_id", ""),
                r.get("alias", ""),
                f"{r.get('opus_count', 0):,}",
                f"{r.get('path_count', 0):,}",
                r.get("lang_count", 0),
                _fmt_iso_short(r.get("last_added", "")),
            ))

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
        for r in recent:
            opus = r.get("opus_id", "")
            # 中段太长不易看，做软截断
            if len(opus) > 60:
                opus = opus[:30] + "…" + opus[-25:]
            self.tree_recent.insert("", "end", values=(
                _fmt_iso_short(r.get("first_seen", "")),
                r.get("project_id", ""),
                r.get("alias", ""),
                opus,
            ))

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
                self.parent.after(
                    0, lambda: self.lbl_status.configure(
                        text=self._t("opus_status_done").format(
                            mr=mr_rows, scan=scan_rows)))
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
