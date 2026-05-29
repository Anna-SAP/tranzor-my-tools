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
        "rw_refresh":           "🔄 Sync & refresh",
        "rw_refresh_tip":       (
            "Pull recent MR Pipeline tasks from Tranzor, then recompute.\n"
            "First run looks back 14 days; for the full history use\n"
            "Full re-sync on the Tranzor Checks tab."),
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
        "rw_col_age":           "Age (created)",
        "rw_col_score":         "Avg score",
        "rw_col_activity":      "Last activity",
        "rw_col_state":         "State",
        "rw_empty":             (
            "No MRs need review right now. "
            "If the cache feels stale, click Sync & refresh."),
        "rw_loading":           "Loading worklist…",
        "rw_syncing":           "Syncing latest MRs… {msg}",
        "rw_sync_failed":       "Sync failed: {error} — showing cached data.",
        "rw_error":             "Failed to load: {error}",
        "rw_count":             "{shown} of {total} MR(s)",
        "rw_open":              "Open MR",
        "rw_open_tip":           "Open the MR in your default browser.",
        "rw_menu_mark":         "✓ Mark MR reviewed",
        "rw_menu_unmark":       "↻ Unmark MR",
        "rw_menu_copy_terms":   "📋 Copy unregistered terms",
        "rw_marked":            "Marked {n} issue(s) reviewed in MR #{mr}.",
        "rw_unmarked":          "Unmarked {n} issue(s) in MR #{mr}.",
        "rw_copied_terms":      "Copied {n} unregistered term(s) to clipboard.",
        "rw_no_terms":          "No unregistered terms found in this MR.",
        "rw_col_new_terms":     "🆕 New terms",
        "rw_watchdog_off":      "🛰 Watchdog: off",
        "rw_watchdog_idle":     "🛰 Watchdog: {red} red MR(s) · last check {age}",
        "rw_watchdog_error":    "🛰 Watchdog error: {error}",
        "rw_watchdog_never":    "🛰 Watchdog: running · waiting for first check",
        "rw_notif_title":       "Tranzor — MR state changed",
        "rw_notif_body":        (
            "{project} · MR #{mr} just went {old} → {new}\n"
            "{task}\n\nOpen in browser?"),
        "rw_legend":            (
            "🔴 Imminent merge · 🟡 Today · 🟢 Can wait · "
            "❔ State unknown — run Sync · ⚪ Merged / skipped"),
        "rw_unknown_hint":      (
            "⚠ {n} MR(s) have no GitLab state cached. "
            "Set a GitLab token (⚙ button) then Sync & refresh."),
        "rw_no_web_url":        "No MR URL stored — run Sync to populate.",
        # GitLab token settings dialog (PR-I)
        "rw_gitlab_btn":        "⚙ GitLab",
        "rw_gitlab_btn_tip":    (
            "Set the GitLab token used to read MR state (open / merged /\n"
            "approvals). Without it the Risk column stays ❔."),
        "rw_gl_title":          "GitLab connection",
        "rw_gl_base_url":       "Base URL",
        "rw_gl_token":          "Token",
        "rw_gl_token_ph_set":   "(configured — leave blank to keep current)",
        "rw_gl_token_ph_empty": "paste a read_api personal access token",
        "rw_gl_show":           "Show",
        "rw_gl_scope_hint":     (
            "Needs only the read_api scope. The token is stored in "
            "~/.tranzor_exporter_config.json."),
        "rw_gl_test":           "Test connection",
        "rw_gl_save":           "Save",
        "rw_gl_cancel":         "Cancel",
        "rw_gl_testing":        "Testing…",
        "rw_gl_test_ok":        "✓ Connected as {name} (@{username})",
        "rw_gl_test_fail":      "✗ {error}",
        "rw_gl_saved":          "✓ Saved. Click Sync & refresh to apply.",
        "rw_gl_need_token":     "Enter a token (or leave blank to keep the existing one).",
        "rw_gl_env_override":   (
            "⚠ Env var TRANZOR_GITLAB_TOKEN is set and overrides this dialog. "
            "Unset it for the saved token to take effect."),
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
        "rw_refresh":           "🔄 同步并刷新",
        "rw_refresh_tip":       (
            "从 Tranzor 拉取近期 MR Pipeline 任务，再重新计算清单。\n"
            "首次回看 14 天；要建完整历史基线请到「Tranzor Checks」\n"
            "标签做 Full re-sync。"),
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
        "rw_col_age":           "创建至今",
        "rw_col_score":         "平均分",
        "rw_col_activity":      "最近活动",
        "rw_col_state":         "状态",
        "rw_empty":             (
            "当前没有待看 MR。如果觉得缓存陈旧，点「同步并刷新」。"),
        "rw_loading":           "正在加载清单…",
        "rw_syncing":           "正在同步最新 MR… {msg}",
        "rw_sync_failed":       "同步失败：{error} — 显示缓存数据。",
        "rw_error":             "加载失败：{error}",
        "rw_count":             "共 {total} 条，显示 {shown} 条",
        "rw_open":              "打开 MR",
        "rw_open_tip":           "在默认浏览器中打开 MR。",
        "rw_menu_mark":         "✓ 标记 MR 已审",
        "rw_menu_unmark":       "↻ 撤回 MR 已审",
        "rw_menu_copy_terms":   "📋 复制未登记术语",
        "rw_marked":            "已将 MR #{mr} 中的 {n} 条 issue 标记为已审。",
        "rw_unmarked":          "已撤回 MR #{mr} 中的 {n} 条 issue 已审记录。",
        "rw_copied_terms":      "已复制 {n} 个未登记术语到剪贴板。",
        "rw_no_terms":          "该 MR 未发现未登记术语。",
        "rw_col_new_terms":     "🆕 新术语",
        "rw_watchdog_off":      "🛰 Watchdog：关闭",
        "rw_watchdog_idle":     "🛰 Watchdog：{red} 条红色 MR · {age} 前刚检查",
        "rw_watchdog_error":    "🛰 Watchdog 出错：{error}",
        "rw_watchdog_never":    "🛰 Watchdog：运行中 · 等首次检查",
        "rw_notif_title":       "Tranzor — MR 状态变更",
        "rw_notif_body":        (
            "{project} · MR #{mr} 状态从 {old} 变成 {new}\n"
            "{task}\n\n是否在浏览器中打开？"),
        "rw_legend":            (
            "🔴 即将合并 · 🟡 今日必看 · 🟢 可慢慢看 · "
            "❔ 状态未知 — 请 Sync · ⚪ 已合并 / 已跳过"),
        "rw_unknown_hint":      (
            "⚠ 有 {n} 条 MR 没有 GitLab 状态缓存，紧迫度只是估算。"
            "点 ⚙ 按钮配置 GitLab token，再「同步并刷新」。"),
        "rw_no_web_url":        "尚未存 MR URL — 请先 Sync。",
        # GitLab token 设置对话框 (PR-I)
        "rw_gitlab_btn":        "⚙ GitLab",
        "rw_gitlab_btn_tip":    (
            "配置读取 MR 状态（open / merged / 审批数）所用的 GitLab\n"
            "token。不配的话「风险」列会一直是 ❔。"),
        "rw_gl_title":          "GitLab 连接设置",
        "rw_gl_base_url":       "Base URL",
        "rw_gl_token":          "Token",
        "rw_gl_token_ph_set":   "（已配置 — 留空则保留当前 token）",
        "rw_gl_token_ph_empty": "粘贴一个 read_api 权限的 personal access token",
        "rw_gl_show":           "显示",
        "rw_gl_scope_hint":     (
            "只需 read_api scope。token 保存在 "
            "~/.tranzor_exporter_config.json。"),
        "rw_gl_test":           "测试连接",
        "rw_gl_save":           "保存",
        "rw_gl_cancel":         "取消",
        "rw_gl_testing":        "测试中…",
        "rw_gl_test_ok":        "✓ 已连接，身份：{name}（@{username}）",
        "rw_gl_test_fail":      "✗ {error}",
        "rw_gl_saved":          "✓ 已保存。点「同步并刷新」生效。",
        "rw_gl_need_token":     "请输入 token（或留空以保留现有 token）。",
        "rw_gl_env_override":   (
            "⚠ 环境变量 TRANZOR_GITLAB_TOKEN 已设置，会覆盖本对话框。"
            "要让保存的 token 生效，请先取消该环境变量。"),
        "rw_age_just_now":      "刚刚",
        "rw_age_minutes":       "{m} 分钟前",
        "rw_age_hours":         "{h} 小时前",
        "rw_age_days":          "{d} 天前",
    },
}


# UI 风险圆点 —— 与 compute_merge_urgency 返回的 tier 一一对应。
# 单字符 emoji 让 Treeview 列宽紧凑；避免长文本撑表头。
_TIER_DOT = {
    "red":     "🔴",
    "amber":   "🟡",
    "green":   "🟢",
    "unknown": "❔",   # PR-F: GitLab state 缺失，按估算显示但带提示
    "grey":    "⚪",
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
    2. 用户切换 ``Include grey`` 复选框 → ``_reload``（纯本地重算）。
    3. 用户点「同步并刷新」→ ``_sync_and_reload``：先增量拉近期 MR
       （PR-H），再 ``_reload`` 重算。
    4. ``_reload`` 跑在后台线程（即便很快——保留 UI 不卡），结果 marshal
       回 Tk 后由 ``_render`` 重绘 Treeview。

    PR-H 起 Worklist 自己也能发起一次**轻量 MR 增量同步**（独立水位
    ``last_mr_sync_at``，不动 Tranzor Checks 的三类全量 sync），这样
    用户点刷新就能拉到新数据，而不必先切到 Tranzor Checks tab。
    """

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self.include_grey_var = tk.BooleanVar(value=False)
        self.include_reviewed_var = tk.BooleanVar(value=False)
        self._loading = False
        self._syncing = False          # PR-H: MR 增量 sync 进行中
        self._last_sync_error = None   # PR-H: 上次 sync 的错误（供 _on_loaded 让位）
        self._items: list[dict] = []
        self._watchdog = None  # PR-D
        self._build(parent)
        # 首屏渲染延迟 500ms —— 让 ExportApp 其他 tab 先完成 paint，
        # 主窗 deiconify 后用户立即看到完整 GUI；worklist 数据这点延迟
        # 在视觉上没区别。PR-F 修白屏的一部分。
        self.parent.after(500, self._reload)
        # PR-D / PR-F: 延迟 3s 启动 merge watchdog —— daemon 线程本身
        # 不阻 UI，但首轮 check_once() 会对所有 red MR 调 GitLab，遇
        # token 不存在会快速失败但仍占启动窗口的网络/CPU。3s 后用户已
        # 在与 GUI 交互，启动体感不被打扰。
        self.parent.after(3000, self._start_watchdog)

    def _t(self, key):
        return self.app._t(key)

    # ------------------------------------------------------------------
    # UI 构造
    # ------------------------------------------------------------------
    def _build(self, parent):
        from export_gui import FONT_FAMILY, Tooltip  # 局部 import，避免循环依赖

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
            actions, text="", command=self._sync_and_reload,
            font=(FONT_FAMILY, 10), relief="flat",
            bg="#0f3460", fg="#fff", activebackground="#1a3a6a",
            activeforeground="#fff", padx=14, pady=4)
        self.btn_refresh.pack(side="left", padx=(0, 12))

        # PR-I: GitLab token 设置入口。配 token 才能把 ❔ 变成真实
        # open/merged 状态，所以放在最显眼的同步按钮旁边。
        self.btn_gitlab = tk.Button(
            actions, text="", command=self._open_gitlab_settings,
            font=(FONT_FAMILY, 10), relief="flat",
            bg="#16213e", fg="#cbd5e1", activebackground="#1a3a6a",
            activeforeground="#fff", padx=12, pady=4)
        self.btn_gitlab.pack(side="left", padx=(0, 12))
        self._tip_gitlab = Tooltip(self.btn_gitlab)

        self.chk_grey = ttk.Checkbutton(
            actions, text="", variable=self.include_grey_var,
            command=self._reload, style="App.TCheckbutton")
        self.chk_grey.pack(side="left")

        self.chk_reviewed = ttk.Checkbutton(
            actions, text="", variable=self.include_reviewed_var,
            command=self._reload, style="App.TCheckbutton")
        self.chk_reviewed.pack(side="left", padx=(12, 0))

        # 顶右 (PR-D)：watchdog 状态 —— 跑没跑、上次检查、当前红色 MR 数。
        # （PR-H 删掉了原来的"审阅者: xxx"徽章 —— 用户反馈那个本机
        #  用户名既无来源说明也无实际价值。reviewer 身份仍由 review_log
        #  在后台用于去重，只是不再占界面。）
        self.lbl_watchdog = ttk.Label(
            actions, text="", style="Status.TLabel")
        self.lbl_watchdog.pack(side="right", padx=(0, 12))

        # ── 表格 ──
        tree_frame = ttk.Frame(content, style="App.TFrame")
        tree_frame.pack(fill="both", expand=True, pady=(4, 0))

        cols = ("risk", "project", "mr", "task", "zh", "other",
                "reviewed", "new_terms", "score", "age", "activity",
                "state")
        self.tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings",
            selectmode="browse", height=18)
        widths = {
            "risk": 50, "project": 130, "mr": 60, "task": 180,
            "zh": 70, "other": 70, "reviewed": 70, "new_terms": 70,
            "score": 60, "age": 100, "activity": 100, "state": 70,
        }
        anchors = {
            "risk": "center", "mr": "center", "zh": "center",
            "other": "center", "reviewed": "center",
            "new_terms": "center", "score": "center",
            "age": "center", "activity": "center", "state": "center",
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
        # PR-F: unknown 用深蓝紫 —— 与暖色 red/amber 视觉拉开，让 Lillian
        # 一眼看出"这条是估算的，建议 Sync 一下再下判断"。
        self.tree.tag_configure(
            "tier_unknown", background="#1f2540", foreground="#c7d2fe")
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
        self.context_menu.add_separator()
        self.context_menu.add_command(label="", command=self._copy_terms_selected)
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
        self.btn_gitlab.configure(text=t("rw_gitlab_btn"))
        self._tip_gitlab.set_text(t("rw_gitlab_btn_tip"))
        self.chk_grey.configure(text=t("rw_show_grey"))
        self.chk_reviewed.configure(text=t("rw_show_reviewed"))
        self.tree.heading("risk",     text=t("rw_col_risk"))
        self.tree.heading("project",  text=t("rw_col_project"))
        self.tree.heading("mr",       text=t("rw_col_mr"))
        self.tree.heading("task",     text=t("rw_col_task"))
        self.tree.heading("zh",       text=t("rw_col_zh"))
        self.tree.heading("other",    text=t("rw_col_other"))
        self.tree.heading("reviewed", text=t("rw_col_reviewed"))
        self.tree.heading("new_terms", text=t("rw_col_new_terms"))
        self.tree.heading("score",    text=t("rw_col_score"))
        self.tree.heading("age",      text=t("rw_col_age"))
        self.tree.heading("activity", text=t("rw_col_activity"))
        self.tree.heading("state",    text=t("rw_col_state"))
        # Context menu labels need refreshing too —— entryconfigure 用
        # 0-based 下标。Separator (index 2) 不需要文案。
        self.context_menu.entryconfigure(0, label=t("rw_menu_mark"))
        self.context_menu.entryconfigure(1, label=t("rw_menu_unmark"))
        self.context_menu.entryconfigure(3, label=t("rw_menu_copy_terms"))
        # Watchdog 状态行：用最近一次 snapshot 重渲染，否则切语言后旧
        # 文案会残留。
        if self._watchdog is not None:
            self._apply_watchdog_status(self._watchdog.last_status)
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
        self.app._mark_busy(self.lbl_status, self._t("rw_loading"))
        include_grey = bool(self.include_grey_var.get())
        include_reviewed = bool(self.include_reviewed_var.get())
        threading.Thread(
            target=self._fetch_thread,
            args=(include_grey, include_reviewed),
            daemon=True, name="worklist-load",
        ).start()

    # ------------------------------------------------------------------
    # PR-H: 「同步并刷新」—— 先增量拉近期 MR，再重算
    # ------------------------------------------------------------------
    def _sync_and_reload(self):
        """按钮入口：增量同步近期 MR，完成后重算 worklist。

        sync 跑后台线程，进度写状态栏；无论成功失败都接 ``_reload`` 重算
        （失败时展示缓存数据 + 错误提示）。sync 期间禁用按钮防重复点。
        """
        if self._loading or self._syncing:
            return
        self._syncing = True
        self._last_sync_error = None
        try:
            self.btn_refresh.configure(state="disabled")
        except Exception:
            pass
        self.app._mark_busy(
            self.lbl_status, self._t("rw_syncing").format(msg=""))
        threading.Thread(
            target=self._sync_thread, daemon=True, name="worklist-sync",
        ).start()

    def _sync_thread(self):
        import tranzor_checks as tc

        def _progress(stage, done=0, total=0, **kw):
            # 节流：只在每 10 个 / 收尾时更新 UI，避免 after() 洪泛。
            if total and (done % 10 == 0 or done >= total):
                msg = f"{done}/{total}"
                self.parent.after(
                    0, lambda m=msg: self.app._mark_busy(
                        self.lbl_status,
                        self._t("rw_syncing").format(msg=m)))

        err = None
        try:
            tc.sync_mr_incremental(progress_callback=_progress)
        except Exception as e:
            err = str(e)
        self.parent.after(0, self._after_sync, err)

    def _after_sync(self, err):
        self._syncing = False
        try:
            self.btn_refresh.configure(state="normal")
        except Exception:
            pass
        self._last_sync_error = err
        if err:
            self.app._mark_idle(
                self.lbl_status,
                self._t("rw_sync_failed").format(error=err[:80]))
        # 无论同步成败都重算一次 —— 成功则展示新数据，失败则至少刷新
        # 缓存视图。``_last_sync_error`` 让 _on_loaded 不要用 unknown
        # 提示盖掉这条 sync 失败信息。
        self._reload()

    def _fetch_thread(self, include_grey, include_reviewed):
        import tranzor_checks as tc
        # PR-C: 拉一次已登记术语集合（6h 缓存，几乎无成本）；platform 不可达
        # 时返回上次的或空集合，Worklist 仍能加载，只是 🆕 列显示 "—"。
        try:
            import tranzor_terminology as tt
            known = tt.load_known_term_names_lower()
        except Exception:
            known = None
        try:
            items = tc.get_worklist_items(
                limit=200,
                include_grey=include_grey,
                include_fully_reviewed=include_reviewed,
                known_term_names_lower=known,
            )
        except Exception as e:
            self.parent.after(0, self._on_error, str(e))
            return
        self.parent.after(0, self._on_loaded, items)

    def _on_loaded(self, items):
        self._loading = False
        self._items = items
        self._render(items)
        # PR-F: 如果当前视图里有 unknown-tier 行，提示一下"建议 Sync"。
        # 数量 > 0 才显示——避免在正常状态下也唠叨。但 sync 失败的提示
        # 优先级更高，不要被这条覆盖（_after_sync 设了就让位一轮）。
        if getattr(self, "_last_sync_error", None):
            # 一次性让位：保留 _after_sync 写的"同步失败"提示这一轮，
            # 读完即清，下轮纯 reload（如切复选框）恢复正常 unknown 提示。
            self._last_sync_error = None
            return
        n_unknown = sum(
            1 for d in items if d.get("merge_tier") == "unknown"
        )
        # 只有在「没有任何可用 GitLab token」时才提示去配置 token——
        # 一旦用户已配置（或 env / 构建内嵌了 token），这条"请设置 token"
        # 的引导就没有意义，继续显示只会打扰用户，故直接收起。
        if n_unknown and not self._has_gitlab_token():
            self.app._mark_hint(
                self.lbl_status,
                self._t("rw_unknown_hint").format(n=n_unknown),
            )
        else:
            self.app._mark_idle(self.lbl_status, "")

    @staticmethod
    def _has_gitlab_token() -> bool:
        """是否已有可用的 GitLab token（用户配置 / 环境变量 / 构建内嵌）。

        用 :func:`gitlab_client.get_token` 统一判断——它本身就按
        env > 配置文件 > 内嵌 的优先级取值，任一非空即视为"已配置"。
        取不到（模块缺失等）时按"未配置"处理，宁可多提示一次也不误吞。
        """
        try:
            import gitlab_client as _gc
            return bool(_gc.get_token())
        except Exception:
            return False

    def _on_error(self, err):
        self._loading = False
        self.app._mark_idle(
            self.lbl_status, self._t("rw_error").format(error=err))

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _render(self, items):
        t = self._t
        for iid in self.tree.get_children():
            self.tree.delete(iid)

        if not items:
            self.lbl_count.configure(text="")
            self.app._mark_idle(self.lbl_status, t("rw_empty"))
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
            new_count = d.get("unregistered_term_count") or 0
            new_terms_disp = str(new_count) if new_count else "—"
            score = d.get("final_score_avg")
            score_disp = f"{score:.0f}" if score is not None else "—"
            # PR-H: "创建至今" —— task_created_at（≈ MR 翻译任务建立时间）
            # 到现在多久，回答"这个 MR 已经挂了多久没人管"。不依赖
            # GitLab，所以即便 state 是 unknown 这列也有值。
            age = _fmt_age(d.get("task_created_at"), t)
            activity = _fmt_age(d.get("mr_updated_at"), t)
            state = d.get("mr_state") or "—"
            tag = f"tier_{tier}"
            self.tree.insert(
                "", "end",
                iid=str(d.get("task_id") or i),
                values=(dot, project, mr, task_name,
                        zh, other_total, reviewed_disp, new_terms_disp,
                        score_disp, age, activity, state),
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
            self.app._mark_idle(self.lbl_status, self._t("rw_no_web_url"))
            return
        try:
            webbrowser.open(url, new=2)
        except Exception as e:
            self.app._mark_idle(
                self.lbl_status, self._t("rw_error").format(error=str(e)))

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
                0, lambda: self.app._mark_idle(
                    self.lbl_status,
                    self._t("rw_error").format(error=str(e))),
            )
            return
        self.parent.after(0, self._after_mark, n, mr_iid, msg_key)

    def _after_mark(self, n, mr_iid, msg_key):
        self.app._mark_idle(
            self.lbl_status,
            self._t(msg_key).format(n=n, mr=mr_iid or "—"),
        )
        # 重新加载——MR 可能因 fully_reviewed 被默认隐藏，要立即体现。
        self._reload()

    # ------------------------------------------------------------------
    # PR-C: 把当前 MR 的未登记术语复制到剪贴板（一行一个），方便 Lillian
    # 直接粘到 Tranzor Terminology 录入页或自己的待办清单。
    # ------------------------------------------------------------------
    def _copy_terms_selected(self):
        item = self._selected_item()
        if not item:
            return
        terms = item.get("unregistered_terms") or []
        if not terms:
            self.app._mark_idle(self.lbl_status, self._t("rw_no_terms"))
            return
        try:
            self.parent.clipboard_clear()
            self.parent.clipboard_append("\n".join(terms))
            # update_idletasks 才能让剪贴板内容真的进 OS 缓冲——Tk 的
            # clipboard_append 否则会被关 GUI 时丢掉。
            self.parent.update_idletasks()
        except Exception as e:
            self.app._mark_idle(
                self.lbl_status, self._t("rw_error").format(error=str(e)))
            return
        self.app._mark_idle(
            self.lbl_status,
            self._t("rw_copied_terms").format(n=len(terms)),
        )

    # ------------------------------------------------------------------
    # PR-D: Pending-Merge watchdog
    # ------------------------------------------------------------------
    def _start_watchdog(self):
        if self._watchdog is not None:
            return
        try:
            import merge_watchdog as _mw
        except Exception:
            return
        self._watchdog = _mw.Watchdog(
            on_event=self._on_watchdog_event,
            on_status_change=self._on_watchdog_status,
        )
        self._watchdog.start()

    def stop_watchdog(self):
        """让 ExportApp 在关闭时调一次——避免后台线程持续转。"""
        if self._watchdog is not None:
            try:
                self._watchdog.stop()
            except Exception:
                pass

    def _on_watchdog_status(self, snapshot):
        """工作线程调用——marshal 回 Tk 再更新 label。"""
        try:
            self.parent.after(0, self._apply_watchdog_status, snapshot)
        except Exception:
            pass

    def _apply_watchdog_status(self, snapshot):
        if not snapshot.get("running"):
            self.lbl_watchdog.configure(text=self._t("rw_watchdog_off"))
            return
        if snapshot.get("last_error"):
            self.lbl_watchdog.configure(
                text=self._t("rw_watchdog_error").format(
                    error=snapshot["last_error"][:80],
                ),
            )
            return
        last = snapshot.get("last_checked_at")
        if not last:
            self.lbl_watchdog.configure(text=self._t("rw_watchdog_never"))
            return
        age = _fmt_age(last, self._t)
        self.lbl_watchdog.configure(
            text=self._t("rw_watchdog_idle").format(
                red=snapshot.get("red_count", 0), age=age,
            ),
        )

    def _on_watchdog_event(self, event):
        """工作线程调用——marshal 回 Tk 再 messagebox。"""
        try:
            self.parent.after(0, self._present_event, event)
        except Exception:
            pass

    def _present_event(self, event):
        """主线程：弹窗通知 + 重新加载 worklist。

        terminal state (merged/closed/locked) → 用 messagebox 让 Lillian
        立刻知道；非 terminal 的状态变化（比如 opened → 又被 reopen）只
        触发 worklist 重新加载，不打扰。
        """
        # 任何 state 变化都先刷新一次 worklist 让排序立即更新。
        self._reload()
        if not event.is_terminal():
            return
        # 用标准 messagebox.askyesno —— 不引入额外 toast 依赖，所有平台
        # 都能用。Yes → 在浏览器打开；No → 关掉继续工作。
        import tkinter.messagebox as messagebox
        ans = messagebox.askyesno(
            title=self._t("rw_notif_title"),
            message=self._t("rw_notif_body").format(
                project=event.project_name or "—",
                mr=event.mr_iid or "—",
                old=event.old_state or "?",
                new=event.new_state,
                task=event.task_name or "",
            ),
            parent=self.parent,
        )
        if ans and event.mr_web_url:
            try:
                webbrowser.open(event.mr_web_url, new=2)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # PR-I: GitLab token 设置对话框
    # ------------------------------------------------------------------
    def _open_gitlab_settings(self):
        """弹一个 modal 对话框：填 base_url + token，测试连接，写配置文件。

        - token 输入框默认 password 模式（show=●），带"显示"复选框。
        - 已配置 token 时输入框留空表示"保持不变"，只有填了新值才覆盖。
        - 测试连接走后台线程（verify_connection 是网络调用），结果回主
          线程渲染。
        - 检测到 TRANZOR_GITLAB_TOKEN 环境变量时给醒目提示——它的优先级
          高于配置文件，否则用户会困惑"保存了怎么不生效"。
        - 保存后自动触发一次「同步并刷新」让新 token 立即生效。
        """
        import os as _os
        import tkinter as tk
        import gitlab_client as gc
        from export_gui import FONT_FAMILY

        t = self._t
        dlg = tk.Toplevel(self.parent)
        dlg.title(t("rw_gl_title"))
        dlg.configure(bg="#1a1a2e")
        try:
            dlg.transient(self.parent.winfo_toplevel())
        except Exception:
            pass
        # grab_set 在不可见的 root 下会抛 TclError（"window not
        # viewable"）。生产环境 root 已 deiconify 不会触发，但包 try 让
        # 对话框在任何状态下都能弹出（最坏退化为非模态）。
        try:
            dlg.grab_set()
        except Exception:
            pass
        dlg.resizable(False, False)

        frm = tk.Frame(dlg, bg="#1a1a2e")
        frm.pack(fill="both", expand=True, padx=20, pady=16)

        def _label(text, fg="#e0e0e0", size=10, bold=False, pady=(0, 2)):
            tk.Label(
                frm, text=text, bg="#1a1a2e", fg=fg,
                font=(FONT_FAMILY, size, "bold" if bold else "normal"),
                wraplength=460, justify="left",
            ).pack(anchor="w", pady=pady)

        def _entry(var, show=None):
            e = tk.Entry(
                frm, textvariable=var, font=(FONT_FAMILY, 10),
                bg="#0a0a1a", fg="#fff", insertbackground="#fff",
                relief="flat", show=show or "")
            e.pack(fill="x", ipady=4, pady=(0, 8))
            return e

        # Base URL
        _label(t("rw_gl_base_url"), bold=True)
        base_var = tk.StringVar(value=gc.get_base_url())
        _entry(base_var)

        # Token
        has_token = bool(gc.get_token())
        _label(t("rw_gl_token"), bold=True)
        token_var = tk.StringVar()
        ent_token = _entry(token_var, show="●")
        _label(
            t("rw_gl_token_ph_set") if has_token
            else t("rw_gl_token_ph_empty"),
            fg="#7a7d99", size=8, pady=(0, 4))

        show_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            frm, text=t("rw_gl_show"), variable=show_var,
            command=lambda: ent_token.configure(
                show="" if show_var.get() else "●"),
            bg="#1a1a2e", fg="#cbd5e1", selectcolor="#0a0a1a",
            activebackground="#1a1a2e", activeforeground="#fff",
            font=(FONT_FAMILY, 9),
        ).pack(anchor="w", pady=(0, 8))

        _label(t("rw_gl_scope_hint"), fg="#7a7d99", size=8, pady=(0, 8))

        status = tk.Label(
            frm, text="", bg="#1a1a2e", fg="#9aa0bf",
            font=(FONT_FAMILY, 9), wraplength=460, justify="left")
        status.pack(anchor="w", pady=(0, 10))

        # 环境变量覆盖警告——优先级高于配置文件，最容易让用户困惑。
        if _os.getenv("TRANZOR_GITLAB_TOKEN"):
            status.configure(text=t("rw_gl_env_override"), fg="#fbbf24")

        state = {"busy": False}

        def _effective():
            base = base_var.get().strip()
            tok = token_var.get().strip() or gc.get_token()
            return base, tok

        def _set_status(text, color):
            status.configure(text=text, fg=color)

        def _test():
            if state["busy"]:
                return
            base, tok = _effective()
            if not tok:
                _set_status(t("rw_gl_need_token"), "#fbbf24")
                return
            state["busy"] = True
            _set_status(t("rw_gl_testing"), "#9aa0bf")

            def _work():
                res = gc.verify_connection(base_url=base, token=tok)

                def _show():
                    state["busy"] = False
                    if res.get("ok"):
                        _set_status(
                            t("rw_gl_test_ok").format(
                                name=res.get("name") or "?",
                                username=res.get("username") or "?"),
                            "#34d399")
                    else:
                        _set_status(
                            t("rw_gl_test_fail").format(
                                error=res.get("error") or "?"),
                            "#f87171")
                try:
                    dlg.after(0, _show)
                except Exception:
                    pass

            threading.Thread(target=_work, daemon=True).start()

        def _save():
            base = base_var.get().strip()
            new_token = token_var.get().strip()
            if not new_token and not gc.get_token():
                _set_status(t("rw_gl_need_token"), "#fbbf24")
                return
            kwargs = {"gitlab_base_url": base or None}
            if new_token:
                kwargs["gitlab_token"] = new_token
            gc.update_config(**kwargs)
            _set_status(t("rw_gl_saved"), "#34d399")
            # 关对话框 + 触发一次同步刷新让新 token 立即生效。
            def _close_and_apply():
                try:
                    dlg.grab_release()
                    dlg.destroy()
                except Exception:
                    pass
                self._sync_and_reload()
            dlg.after(700, _close_and_apply)

        def _cancel():
            try:
                dlg.grab_release()
                dlg.destroy()
            except Exception:
                pass

        btns = tk.Frame(frm, bg="#1a1a2e")
        btns.pack(fill="x", pady=(4, 0))

        def _btn(parent, text, cmd, bg, fg="#fff"):
            return tk.Button(
                parent, text=text, command=cmd, font=(FONT_FAMILY, 10),
                relief="flat", bg=bg, fg=fg, activebackground="#1a3a6a",
                activeforeground="#fff", padx=14, pady=4)

        _btn(btns, t("rw_gl_test"), _test, "#16213e", "#cbd5e1").pack(
            side="left")
        _btn(btns, t("rw_gl_save"), _save, "#0f3460").pack(
            side="left", padx=(8, 0))
        _btn(btns, t("rw_gl_cancel"), _cancel, "#16213e", "#cbd5e1").pack(
            side="right")

        # 居中到父窗。先 update 让 dlg 拿到真实尺寸。
        try:
            dlg.update_idletasks()
            top = self.parent.winfo_toplevel()
            x = top.winfo_rootx() + (top.winfo_width() - dlg.winfo_width()) // 2
            y = top.winfo_rooty() + (top.winfo_height() - dlg.winfo_height()) // 3
            dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass
