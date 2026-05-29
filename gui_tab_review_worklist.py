"""Review Worklist — GUI Tab (PR-A).

Language Lead 每日唯一入口：把 70+ MR 任务压成 5-10 条按 ``merge 紧迫度
× 翻译问题数`` 自动排序的待看清单。

数据全部来自本地 ``tranzor_checks`` SQLite 缓存（首屏纯本地读，零网络），
排序公式见 :func:`tranzor_checks.compute_merge_urgency`。GitLab 状态字段
（``mr_state`` / ``upvotes`` / ``updated_at`` / ``labels``）在 MR sync 时
顺手入库——不引入额外往返。

UI 约定：
- 主表第一列是 ``Risk`` 圆点（🔴 / 🟡 / 🟢），与 tier 一一对应；
- 双击行 → 默认浏览器打开 ``mr_web_url``（Tranzor / GitLab 都行）；
- ``🔄 Refresh`` 不重 sync，只重算 worklist（迅速）。要拉新数据请到
  Tranzor Checks tab 跑 Sync。这条决定来自"每日打开 → 当下看清单"的
  使用节奏，Sync 单独触发更不打扰人。
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
from datetime import datetime, timezone


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


STRINGS = {
    "en": {
        "tab_review_worklist":  "🎯 Review Worklist",
        "rw_title":             "Today's Review Queue · MR Pipeline",
        "rw_subtitle":          (
            "Sorted by merge urgency × language priority. "
            "Chinese issues weigh heaviest."),
        "rw_refresh":           "🔄 Refresh",
        "rw_refresh_tip":       (
            "Recompute the worklist from the local checks cache.\n"
            "To pull new MR data, run Sync on the Tranzor Checks tab."),
        "rw_show_grey":         "Include merged / skipped",
        "rw_show_grey_tip":     (
            "Show MRs that are already merged or have skip-translate.\n"
            "Off by default — those need no review action today."),
        "rw_show_reviewed":     "Show fully-reviewed",
        "rw_show_reviewed_tip": (
            "Show MRs whose every issue you've already marked.\n"
            "Off by default — finished MRs don't need attention."),
        "rw_col_risk":          "Risk",
        "rw_col_project":       "Project",
        "rw_col_mr":            "MR #",
        "rw_col_task":          "Task",
        "rw_col_zh":            "zh issues",
        "rw_col_other":         "other issues",
        "rw_col_reviewed":      "Reviewed",
        "rw_col_score":         "Avg score",
        "rw_col_activity":      "Last activity",
        "rw_col_state":         "State",
        "rw_empty":             (
            "No MRs need review right now. "
            "If the cache feels stale, run Sync on the Tranzor Checks tab."),
        "rw_loading":           "Loading worklist…",
        "rw_error":             "Failed to load: {error}",
        "rw_count":             "{shown} of {total} MR(s)",
        "rw_summary_reviewer":  "Reviewer: {reviewer}  ·  today {today} · total {total}",
        "rw_open":              "Open MR",
        "rw_open_tip":           "Open the MR in your default browser.",
        "rw_menu_mark":         "✓ Mark MR reviewed",
        "rw_menu_unmark":       "↻ Unmark MR",
        "rw_marked":            "Marked {n} issue(s) reviewed in MR #{mr}.",
        "rw_unmarked":          "Unmarked {n} issue(s) in MR #{mr}.",
        "rw_legend":            (
            "🔴 Imminent merge · 🟡 Today · 🟢 Can wait · ⚪ Merged / skipped"),
        "rw_no_web_url":        "No MR URL stored — run Sync to populate.",
        "rw_age_just_now":      "just now",
        "rw_age_minutes":       "{m}m ago",
        "rw_age_hours":         "{h}h ago",
        "rw_age_days":          "{d}d ago",
    },
    "zh": {
        "tab_review_worklist":  "🎯 待看清单",
        "rw_title":             "今日待看 · MR Pipeline",
        "rw_subtitle":          (
            "按 merge 紧迫度 × 语言优先级 自动排序，中文问题权重最高。"),
        "rw_refresh":           "🔄 刷新",
        "rw_refresh_tip":       (
            "用本地 checks 缓存重新计算清单。\n"
            "要拉取新 MR 数据请到「Tranzor Checks」标签运行 Sync。"),
        "rw_show_grey":         "包含已合并 / 跳过",
        "rw_show_grey_tip":     (
            "显示已 merged 或带 skip-translate 标签的 MR。\n"
            "默认关闭—这些今天无需 Review。"),
        "rw_show_reviewed":     "包含已全部审完",
        "rw_show_reviewed_tip": (
            "显示该 MR 所有 issue 都已被你 Mark 的。\n"
            "默认关闭—已审完的 MR 无需占视觉空间。"),
        "rw_col_risk":          "风险",
        "rw_col_project":       "项目",
        "rw_col_mr":            "MR #",
        "rw_col_task":          "任务",
        "rw_col_zh":            "中文问题",
        "rw_col_other":         "其它问题",
        "rw_col_reviewed":      "已审",
        "rw_col_score":         "平均分",
        "rw_col_activity":      "最近活动",
        "rw_col_state":         "状态",
        "rw_empty":             (
            "当前没有待看 MR。如果觉得缓存陈旧，到「Tranzor Checks」"
            "标签运行 Sync。"),
        "rw_loading":           "正在加载清单…",
        "rw_error":             "加载失败：{error}",
        "rw_count":             "共 {total} 条，显示 {shown} 条",
        "rw_summary_reviewer":  "审阅者：{reviewer}  ·  今日 {today} · 累计 {total}",
        "rw_open":              "打开 MR",
        "rw_open_tip":           "在默认浏览器中打开 MR。",
        "rw_menu_mark":         "✓ 标记 MR 已审",
        "rw_menu_unmark":       "↻ 撤回 MR 已审",
        "rw_marked":            "已将 MR #{mr} 中的 {n} 条 issue 标记为已审。",
        "rw_unmarked":          "已撤回 MR #{mr} 中的 {n} 条 issue 已审记录。",
        "rw_legend":            (
            "🔴 即将合并 · 🟡 今日必看 · 🟢 可慢慢看 · ⚪ 已合并 / 已跳过"),
        "rw_no_web_url":        "尚未存 MR URL — 请先 Sync。",
        "rw_age_just_now":      "刚刚",
        "rw_age_minutes":       "{m} 分钟前",
        "rw_age_hours":         "{h} 小时前",
        "rw_age_days":          "{d} 天前",
    },
}


# UI 风险圆点 —— 与 compute_merge_urgency 返回的 tier 一一对应。
# 单字符 emoji 让 Treeview 列宽紧凑；避免长文本撑表头。
_TIER_DOT = {
    "red":   "🔴",
    "amber": "🟡",
    "green": "🟢",
    "grey":  "⚪",
}


def _fmt_age(ts_iso: str | None, t, now: datetime | None = None) -> str:
    """把 ISO 时间戳格式化成"X 分钟/小时/天前"。``t`` 是 i18n 查询器。"""
    if not ts_iso:
        return "—"
    try:
        ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return "—"
    now = now or datetime.now(timezone.utc)
    secs = max(0, (now - ts).total_seconds())
    if secs < 60:
        return t("rw_age_just_now")
    mins = int(secs // 60)
    if mins < 60:
        return t("rw_age_minutes").format(m=mins)
    hours = int(mins // 60)
    if hours < 24:
        return t("rw_age_hours").format(h=hours)
    days = int(hours // 24)
    return t("rw_age_days").format(d=days)


class ReviewWorklistTab:
    """Worklist tab —— Lillian 看 MR 翻译质量的首屏。

    数据流极简：

    1. ``__init__`` → ``_build`` 起骨架，立刻调一次 ``_reload`` 让首屏
       有内容（同步、毫秒级，因为是 SQLite local read）。
    2. 用户点 🔄 / 切换 ``Include grey`` 复选框 → ``_reload``。
    3. ``_reload`` 跑在后台线程（即便很快——保留 UI 不卡），结果 marshal
       回 Tk 后由 ``_render`` 重绘 Treeview。

    Worklist 不发起任何同步——同步逻辑由 Tranzor Checks tab 持有，避免
    一份数据被两个 tab 各拉一遍。这条边界让 PR-D 的后台 watchdog 也只
    需要 hook 一个 sync 入口。
    """

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self.include_grey_var = tk.BooleanVar(value=False)
        self.include_reviewed_var = tk.BooleanVar(value=False)
        self._loading = False
        self._items: list[dict] = []
        self._build(parent)
        # 首屏立刻渲染——SQLite local read 是毫秒级的，不需要 spinner。
        self.parent.after(0, self._reload)

    def _t(self, key):
        return self.app._t(key)

    # ------------------------------------------------------------------
    # UI 构造
    # ------------------------------------------------------------------
    def _build(self, parent):
        from export_gui import FONT_FAMILY  # 局部 import，避免循环依赖

        content = ttk.Frame(parent, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=16, pady=8)

        # ── 顶部标题 + 副标题 ──
        head = ttk.Frame(content, style="App.TFrame")
        head.pack(fill="x", pady=(0, 8))
        self.lbl_title = ttk.Label(
            head, text="", font=(FONT_FAMILY, 14, "bold"),
            style="App.TLabel")
        self.lbl_title.pack(side="left")
        self.lbl_count = ttk.Label(
            head, text="", style="Status.TLabel")
        self.lbl_count.pack(side="right")

        self.lbl_subtitle = ttk.Label(
            content, text="", style="Status.TLabel")
        self.lbl_subtitle.pack(fill="x", pady=(0, 4))

        self.lbl_legend = ttk.Label(
            content, text="", style="Status.TLabel")
        self.lbl_legend.pack(fill="x", pady=(0, 8))

        # ── 操作栏 ──
        actions = ttk.Frame(content, style="App.TFrame")
        actions.pack(fill="x", pady=(0, 6))
        # 创建按钮不能用 export_gui 的辅助函数（循环依赖；那是 ExportApp
        # 的方法）。直接用 tk.Button 配深色主题即可。
        self.btn_refresh = tk.Button(
            actions, text="", command=self._reload,
            font=(FONT_FAMILY, 10), relief="flat",
            bg="#0f3460", fg="#fff", activebackground="#1a3a6a",
            activeforeground="#fff", padx=14, pady=4)
        self.btn_refresh.pack(side="left", padx=(0, 12))

        self.chk_grey = ttk.Checkbutton(
            actions, text="", variable=self.include_grey_var,
            command=self._reload, style="App.TCheckbutton")
        self.chk_grey.pack(side="left")

        self.chk_reviewed = ttk.Checkbutton(
            actions, text="", variable=self.include_reviewed_var,
            command=self._reload, style="App.TCheckbutton")
        self.chk_reviewed.pack(side="left", padx=(12, 0))

        # 顶右：审阅者徽章——"今日已审 X / 累计 Y"。Lillian 一眼看出
        # 她今天的工作量，也是给自己的"还差几条"提醒。
        self.lbl_reviewer = ttk.Label(
            actions, text="", style="Status.TLabel")
        self.lbl_reviewer.pack(side="right")

        # ── 表格 ──
        tree_frame = ttk.Frame(content, style="App.TFrame")
        tree_frame.pack(fill="both", expand=True, pady=(4, 0))

        cols = ("risk", "project", "mr", "task", "zh", "other",
                "reviewed", "score", "activity", "state")
        self.tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings",
            selectmode="browse", height=18)
        widths = {
            "risk": 50, "project": 130, "mr": 60, "task": 240,
            "zh": 80, "other": 80, "reviewed": 80,
            "score": 70, "activity": 100, "state": 80,
        }
        anchors = {
            "risk": "center", "mr": "center", "zh": "center",
            "other": "center", "reviewed": "center", "score": "center",
            "activity": "center", "state": "center",
        }
        for c in cols:
            self.tree.column(
                c, width=widths.get(c, 100),
                anchor=anchors.get(c, "w"),
                stretch=(c == "task"),
            )
        # tier 行底色：与 Tranzor Checks tab 的"post_edit"金色保持视觉
        # 体系。red 用警报红、amber 用琥珀、green 用静默灰、grey 用更深灰。
        self.tree.tag_configure(
            "tier_red", background="#3a1f1f", foreground="#fecaca")
        self.tree.tag_configure(
            "tier_amber", background="#3a2e1f", foreground="#fde68a")
        self.tree.tag_configure(
            "tier_green", background="#1f3a28", foreground="#bbf7d0")
        self.tree.tag_configure(
            "tier_grey", background="#222", foreground="#888")

        scroll = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_double_click)

        # 右键 / Ctrl+Click 弹 Mark / Unmark 菜单。
        # 行内 ✓ 按钮没用 —— Treeview 不支持嵌入控件，右键菜单是最少
        # 侵入又不增加 UI 体积的方案。
        self.context_menu = tk.Menu(self.tree, tearoff=0)
        # 文案在 refresh_text 里赋；这里先 placeholder。
        self.context_menu.add_command(label="", command=self._mark_selected)
        self.context_menu.add_command(label="", command=self._unmark_selected)
        # Windows 右键事件；macOS 自动 Ctrl+Click 也会触发 Button-3。
        self.tree.bind("<Button-3>", self._on_right_click)

        # ── 状态栏 ──
        self.lbl_status = ttk.Label(
            content, text="", style="Status.TLabel")
        self.lbl_status.pack(fill="x", pady=(6, 0))

        self.refresh_text()

    # ------------------------------------------------------------------
    # i18n —— 语言切换时主 GUI 会调
    # ------------------------------------------------------------------
    def refresh_text(self):
        t = self._t
        self.lbl_title.configure(text=t("rw_title"))
        self.lbl_subtitle.configure(text=t("rw_subtitle"))
        self.lbl_legend.configure(text=t("rw_legend"))
        self.btn_refresh.configure(text=t("rw_refresh"))
        self.chk_grey.configure(text=t("rw_show_grey"))
        self.chk_reviewed.configure(text=t("rw_show_reviewed"))
        self.tree.heading("risk",     text=t("rw_col_risk"))
        self.tree.heading("project",  text=t("rw_col_project"))
        self.tree.heading("mr",       text=t("rw_col_mr"))
        self.tree.heading("task",     text=t("rw_col_task"))
        self.tree.heading("zh",       text=t("rw_col_zh"))
        self.tree.heading("other",    text=t("rw_col_other"))
        self.tree.heading("reviewed", text=t("rw_col_reviewed"))
        self.tree.heading("score",    text=t("rw_col_score"))
        self.tree.heading("activity", text=t("rw_col_activity"))
        self.tree.heading("state",    text=t("rw_col_state"))
        # Context menu labels need refreshing too —— entryconfigure 用
        # 1-based 下标，跟 add_command 的顺序对应。
        self.context_menu.entryconfigure(0, label=t("rw_menu_mark"))
        self.context_menu.entryconfigure(1, label=t("rw_menu_unmark"))
        # 行内文案有时也带 i18n（"just now" 等），重绘一次确保跟当前语言。
        if self._items:
            self._render(self._items)

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def _reload(self):
        if self._loading:
            return
        self._loading = True
        self.lbl_status.configure(text=self._t("rw_loading"))
        include_grey = bool(self.include_grey_var.get())
        include_reviewed = bool(self.include_reviewed_var.get())
        threading.Thread(
            target=self._fetch_thread,
            args=(include_grey, include_reviewed),
            daemon=True, name="worklist-load",
        ).start()

    def _fetch_thread(self, include_grey, include_reviewed):
        import tranzor_checks as tc
        try:
            items = tc.get_worklist_items(
                limit=200,
                include_grey=include_grey,
                include_fully_reviewed=include_reviewed,
            )
            summary = tc.get_review_summary()
        except Exception as e:
            self.parent.after(0, self._on_error, str(e))
            return
        self.parent.after(0, self._on_loaded, items, summary)

    def _on_loaded(self, items, summary):
        self._loading = False
        self._items = items
        self._render(items)
        self.lbl_reviewer.configure(
            text=self._t("rw_summary_reviewer").format(
                reviewer=summary.get("reviewer", "—"),
                today=summary.get("today", 0),
                total=summary.get("total", 0),
            ),
        )
        self.lbl_status.configure(text="")

    def _on_error(self, err):
        self._loading = False
        self.lbl_status.configure(
            text=self._t("rw_error").format(error=err))

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _render(self, items):
        t = self._t
        for iid in self.tree.get_children():
            self.tree.delete(iid)

        if not items:
            self.lbl_count.configure(text="")
            self.lbl_status.configure(text=t("rw_empty"))
            return

        for i, d in enumerate(items):
            tier = d.get("merge_tier") or "grey"
            dot = _TIER_DOT.get(tier, "⚪")
            project = (d.get("project_name") or d.get("project_id") or "-")
            mr = d.get("mr_iid") or "-"
            task_name = d.get("task_name") or "-"
            zh = d.get("zh_issues") or 0
            sec = d.get("secondary_issues") or 0
            oth = d.get("other_issues") or 0
            other_total = sec + oth
            reviewed = d.get("reviewed_count") or 0
            total_iss = d.get("total_issue_count") or 0
            reviewed_disp = (
                f"{reviewed}/{total_iss}" if total_iss else "—"
            )
            score = d.get("final_score_avg")
            score_disp = f"{score:.0f}" if score is not None else "—"
            activity = _fmt_age(d.get("mr_updated_at"), t)
            state = d.get("mr_state") or "—"
            tag = f"tier_{tier}"
            self.tree.insert(
                "", "end",
                iid=str(d.get("task_id") or i),
                values=(dot, project, mr, task_name,
                        zh, other_total, reviewed_disp,
                        score_disp, activity, state),
                tags=(tag,),
            )
        # Count 行：显示"全部 N 条 / 表里 M 条"——超过 200 时让用户知道有更多。
        self.lbl_count.configure(
            text=t("rw_count").format(
                shown=len(items), total=len(items)))

    # ------------------------------------------------------------------
    # 行操作
    # ------------------------------------------------------------------
    def _on_double_click(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        task_id = sel[0]
        item = next(
            (d for d in self._items
             if str(d.get("task_id") or "") == task_id),
            None,
        )
        if not item:
            return
        url = item.get("mr_web_url")
        if not url:
            self.lbl_status.configure(text=self._t("rw_no_web_url"))
            return
        try:
            webbrowser.open(url, new=2)
        except Exception as e:
            self.lbl_status.configure(
                text=self._t("rw_error").format(error=str(e)))

    # ------------------------------------------------------------------
    # 右键菜单 → Mark / Unmark MR
    # ------------------------------------------------------------------
    def _on_right_click(self, event):
        """Treeview 没有原生 right-click-selects-row 行为；自己实现一下，
        否则用户右键当前未选中的行时菜单上下文就错了。"""
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self.tree.focus(row)
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def _selected_item(self):
        sel = self.tree.selection()
        if not sel:
            return None
        task_id = sel[0]
        return next(
            (d for d in self._items
             if str(d.get("task_id") or "") == task_id),
            None,
        )

    def _mark_selected(self):
        item = self._selected_item()
        if not item:
            return
        threading.Thread(
            target=self._mark_thread,
            args=(item.get("task_id"), item.get("mr_iid"), True),
            daemon=True, name="worklist-mark",
        ).start()

    def _unmark_selected(self):
        item = self._selected_item()
        if not item:
            return
        threading.Thread(
            target=self._mark_thread,
            args=(item.get("task_id"), item.get("mr_iid"), False),
            daemon=True, name="worklist-unmark",
        ).start()

    def _mark_thread(self, task_id, mr_iid, mark):
        """Mark / unmark 走线程，因为 mark_task_reviewed 要做一次 SELECT
        全部 issue 再 bulk INSERT；MR 体积大时几十毫秒，放 UI 线程会闪。"""
        import tranzor_checks as tc
        try:
            if mark:
                n = tc.mark_task_reviewed(str(task_id))
                msg_key = "rw_marked"
            else:
                n = tc.unmark_task_reviewed(str(task_id))
                msg_key = "rw_unmarked"
        except Exception as e:
            self.parent.after(
                0, self.lbl_status.configure,
                {"text": self._t("rw_error").format(error=str(e))},
            )
            return
        self.parent.after(0, self._after_mark, n, mr_iid, msg_key)

    def _after_mark(self, n, mr_iid, msg_key):
        self.lbl_status.configure(
            text=self._t(msg_key).format(n=n, mr=mr_iid or "—"),
        )
        # 重新加载——MR 可能因 fully_reviewed 被默认隐藏，要立即体现。
        self._reload()
