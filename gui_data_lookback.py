"""📅 Data Lookback — one day's translation activity, Category → Project.

Opened from the header's 📅 Data Lookback button: the calendar picks a UTC+8
day, then this non-modal window shows distinct MRs, their target branches,
Bug Fix and Scan task counts per category and project (see
:mod:`data_lookback` for the data rules). Tranzor rows arrive first; target
branches fill in afterwards from GitLab. All network work runs off the Tk
thread and a generation counter drops results for a day the user left.
"""
from __future__ import annotations

import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from datetime import datetime, timedelta
from tkinter import ttk

import data_lookback as dl
import date_picker


STRINGS = {
    "en": {
        "dl_entry": "📅 Data Lookback",
        "dl_entry_tip": ("Look back at one day: pick a date to see distinct "
                         "MRs, target branches, Bug Fix and Scan tasks per "
                         "category and project."),
        "dl_window_title": "Data Lookback",
        "dl_heading": "📅 Data Lookback",
        "dl_day": "{day} ({weekday}) · UTC+8",
        "dl_weekday_today": "{weekday}, today",
        "dl_prev": "◀ Previous day",
        "dl_next": "Next day ▶",
        "dl_pick": "📅 Pick date",
        "dl_refresh": "⟳ Refresh",
        "dl_copy": "📋 Copy table",
        "dl_copied": "Copied {n} rows to the clipboard.",
        "dl_show_idle": "Show projects with no activity",
        "dl_kpi_mrs": "Distinct MRs",
        "dl_kpi_runs": "Completed MR tasks",
        "dl_kpi_branches": "Target branches",
        "dl_kpi_bugfix": "Bug Fix",
        "dl_kpi_scan": "Scan tasks",
        "dl_kpi_projects": "Active projects",
        "dl_col_name": "Category / Project",
        "dl_col_mrs": "MRs",
        "dl_col_branches": "Target branches (MR Branch)",
        "dl_col_bugfix": "Bug Fix",
        "dl_col_scan": "Scan tasks",
        "dl_cat_label": "{name}   ·   {active}/{total} active",
        "dl_unmapped": "⚠ Unmapped (not in the category table)",
        "dl_mr_runs": "completed ×{n}",
        "dl_branch_pending": "…",
        "dl_branch_unknown": "(unknown)",
        "dl_loading": "Loading {day} from Tranzor…",
        "dl_resolving": "Resolving target branches from GitLab {done}/{total}…",
        "dl_done": "{day}: {mrs} MR(s), {bugfix} Bug Fix, {scan} Scan · "
                   "loaded {time}",
        "dl_done_empty": ("{day}: no completed MR translation, Bug Fix or "
                          "Scan task · loaded {time}"),
        "dl_failed": "Loading {day} failed: {error}",
        "dl_src_mr": "MR tasks",
        "dl_src_bugfix": "Bug Fix",
        "dl_src_scan": "Scan tasks",
        "dl_err_source": "⚠ {source} unavailable ({reason}) — shown as “—”.",
        "dl_err_auth": "sign-in expired: click Re-login, then ⟳ Refresh",
        "dl_err_forbidden": "needs the Language Lead role",
        "dl_note_no_token": ("⚠ Target branches need a GitLab token — "
                             "branch cells show “—”."),
        "dl_note_unknown": ("{n} MR(s) with unknown target branch "
                            "(no GitLab access, or the MR was deleted)."),
        "dl_note_unmapped": ("⚠ {n} project(s) are not in the category "
                             "table — listed under Unmapped."),
        "dl_note_today": "Today is still in progress; ⟳ Refresh for later "
                         "activity.",
        "dl_rules": (
            "How it counts — days are UTC+8, and a task counts on the day "
            "it was created (Tranzor keeps no completion time; an MR task "
            "finishes in about a minute). MRs: distinct (project, MR#) with "
            "a completed MR translation task — skipped / failed / cancelled "
            "runs are excluded. Target branches: each MR's current GitLab "
            "target branch. Bug Fix: submissions Applied or Partially "
            "applied. Scan tasks: completed Missing Translation Scans. "
            "Double-click an MR row to open it in GitLab."),
        "dl_weekdays": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
    },
    "zh": {
        "dl_entry": "📅 数据回溯",
        "dl_entry_tip": "回看某一天：选日期后按类别 → 项目查看去重 MR 数、"
                        "目标分支、Bug Fix 与 Scan 任务数。",
        "dl_window_title": "数据回溯",
        "dl_heading": "📅 数据回溯",
        "dl_day": "{day}（{weekday}）· UTC+8",
        "dl_weekday_today": "{weekday}·今天",
        "dl_prev": "◀ 前一天",
        "dl_next": "后一天 ▶",
        "dl_pick": "📅 选择日期",
        "dl_refresh": "⟳ 刷新",
        "dl_copy": "📋 复制表格",
        "dl_copied": "已复制 {n} 行到剪贴板。",
        "dl_show_idle": "显示当天无活动的项目",
        "dl_kpi_mrs": "去重 MR 数",
        "dl_kpi_runs": "完成的 MR 任务",
        "dl_kpi_branches": "目标分支",
        "dl_kpi_bugfix": "Bug Fix",
        "dl_kpi_scan": "Scan 任务",
        "dl_kpi_projects": "活跃项目",
        "dl_col_name": "类别 / 项目",
        "dl_col_mrs": "MR 数",
        "dl_col_branches": "目标分支（MR Branch）",
        "dl_col_bugfix": "Bug Fix",
        "dl_col_scan": "Scan 任务",
        "dl_cat_label": "{name}   ·   {active}/{total} 个项目有活动",
        "dl_unmapped": "⚠ 未归类（不在类别表中）",
        "dl_mr_runs": "完成 ×{n}",
        "dl_branch_pending": "…",
        "dl_branch_unknown": "（未知）",
        "dl_loading": "正在从 Tranzor 加载 {day}…",
        "dl_resolving": "正在从 GitLab 解析目标分支 {done}/{total}…",
        "dl_done": "{day}：{mrs} 个 MR，Bug Fix {bugfix}，Scan {scan} · "
                   "加载于 {time}",
        "dl_done_empty": "{day}：没有完成的 MR 翻译、Bug Fix 或 Scan 任务 · "
                         "加载于 {time}",
        "dl_failed": "加载 {day} 失败：{error}",
        "dl_src_mr": "MR 任务",
        "dl_src_bugfix": "Bug Fix",
        "dl_src_scan": "Scan 任务",
        "dl_err_source": "⚠ {source}不可用（{reason}），以“—”显示。",
        "dl_err_auth": "登录已过期：请点 Re-login 后再点 ⟳ 刷新",
        "dl_err_forbidden": "需要 Language Lead 角色",
        "dl_note_no_token": "⚠ 目标分支需要配置 GitLab token——分支列暂以“—”显示。",
        "dl_note_unknown": "{n} 个 MR 的目标分支未知（无 GitLab 权限或 MR 已删除）。",
        "dl_note_unmapped": "⚠ 有 {n} 个项目不在类别表中，已列在“未归类”下。",
        "dl_note_today": "今天尚未结束，稍后可点 ⟳ 刷新查看新增活动。",
        "dl_rules": (
            "统计口径：日期按 UTC+8；任务按创建日归日（Tranzor 不记录完成时间，"
            "MR 任务通常约 1 分钟完成）。MR 数：有已完成 MR 翻译任务的不重复 "
            "(项目, MR#)，skipped / failed / cancelled 不计入。目标分支：各 MR "
            "当前在 GitLab 上的目标分支。Bug Fix：状态为 Applied 或 Partially "
            "applied 的提交。Scan 任务：已完成的缺失翻译扫描。双击 MR 行可在 "
            "GitLab 中打开。"),
        "dl_weekdays": "周一,周二,周三,周四,周五,周六,周日",
    },
}

_COLUMNS = ("mrs", "branches", "bugfix", "scan")
_COLUMN_KEYS = {
    "mrs": "dl_col_mrs", "branches": "dl_col_branches",
    "bugfix": "dl_col_bugfix", "scan": "dl_col_scan",
}
_KPIS = ("mrs", "runs", "branches", "bugfix", "scan", "projects")
_SOURCES = ("mr", "bugfix", "scan")
_UNAVAILABLE = "—"
_MR_TITLE_MAX = 70


def _num(value) -> str:
    if value is None:
        return _UNAVAILABLE
    return f"{value:,}" if isinstance(value, int) else str(value)


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


class DataLookbackWindow:
    """Non-modal report window; one instance per app, re-targeted by day."""

    def __init__(self, app, *, font_family, day, on_close=None,
                 fetch_day=None, resolve_meta=None, can_resolve=None):
        self.app = app
        self.ff = font_family
        self._on_close_cb = on_close
        # Injectable for tests; production uses data_lookback directly.
        self._fetch_day = fetch_day or dl.fetch_day
        self._resolve_meta = resolve_meta or dl.resolve_mr_meta
        self._can_resolve = can_resolve or dl.can_resolve_branches

        self.day = dl.coerce_day(day) or (dl.today_utc8() - timedelta(days=1))
        self._gen = 0
        self._cancel: threading.Event | None = None
        self._closed = False
        self._cache: dict = {}
        self._report = None
        self._state = "idle"          # idle | loading | resolving | done | failed
        self._status_args: dict = {}
        self._branch_state = "pending"  # pending | done | no_token
        self._progress = (0, 0)
        self._loaded_at = ""
        self._mr_urls: dict[str, str] = {}
        self._full_text: dict[str, str] = {}   # iid → untruncated #0 text
        self._cols_fitted = False
        self._open_state: dict[str, bool] = {}
        self._rendered_day = None

        self.win = tk.Toplevel(app.root)
        self.win.configure(bg=app.BG)
        self.win.geometry("1180x720")
        self.win.minsize(900, 480)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self.show_idle_var = tk.BooleanVar(value=False)
        self._build()
        self.refresh_text()
        self.load(self.day)

    # ------------------------------------------------------------------ ui
    def _t(self, key):
        return self.app._t(key)

    def _button(self, parent, command, accent=False):
        return self.app._create_button(
            parent, text="", command=command,
            style_name="Accent" if accent else "Secondary",
            font=(self.ff, 10, "bold" if accent else "normal"),
            bg=self.app.ACCENT_BTN if accent else self.app.ACCENT,
            fg="#ffffff" if accent else "#ccc",
            activebackground="#ff6b81" if accent else "#1a3a6a",
            activeforeground="#fff", padx=12, pady=3)

    def _build(self):
        app, ff = self.app, self.ff
        top = tk.Frame(self.win, bg=app.BG)
        top.pack(fill="x", padx=16, pady=(14, 6))
        self.lbl_heading = tk.Label(
            top, text="", bg=app.BG, fg="#ffffff", font=(ff, 15, "bold"))
        self.lbl_heading.pack(side="left")
        self.btn_prev = self._button(top, lambda: self._step(-1))
        self.btn_prev.pack(side="left", padx=(18, 4))
        self.lbl_day = tk.Label(
            top, text="", bg=app.BG, fg=app.ACCENT_BTN, font=(ff, 13, "bold"))
        self.lbl_day.pack(side="left", padx=6)
        self.btn_next = self._button(top, lambda: self._step(1))
        self.btn_next.pack(side="left", padx=(4, 4))
        self.btn_pick = self._button(top, self._pick_date, accent=True)
        self.btn_pick.pack(side="left", padx=(8, 0))

        kpi = ttk.Frame(self.win, style="Summary.TFrame")
        kpi.pack(fill="x", padx=16, pady=(4, 6))
        self.kpi_labels = {}
        for i, key in enumerate(_KPIS):
            cell = ttk.Frame(kpi, style="Summary.TFrame")
            cell.grid(row=0, column=i, sticky="w", padx=(14, 26), pady=8)
            # Fixed width: the cells don't jump as "…" turns into a number,
            # and a shrinking label leaves no stale pixels on Windows.
            val = ttk.Label(cell, text=_UNAVAILABLE, style="SummaryCount.TLabel",
                            font=(ff, 16, "bold"), width=7, anchor="w")
            val.pack(anchor="w")
            cap = ttk.Label(cell, text="", style="SummaryCountLabel.TLabel",
                            font=(ff, 9))
            cap.pack(anchor="w")
            self.kpi_labels[key] = (cap, val)

        opts = tk.Frame(self.win, bg=app.BG)
        opts.pack(fill="x", padx=16)
        # Right-hand buttons are packed first so a narrow window squeezes
        # the status text, never the buttons.
        self.btn_copy = self._button(opts, self._copy)
        self.btn_copy.pack(side="right")
        self.btn_refresh = self._button(
            opts, lambda: self.load(self.day, force=True))
        self.btn_refresh.pack(side="right", padx=(0, 8))
        self.chk_idle = ttk.Checkbutton(
            opts, text="", variable=self.show_idle_var,
            style="Card.TCheckbutton", command=self._render)
        self.chk_idle.pack(side="left")
        self.lbl_status = ttk.Label(opts, text="", style="Status.TLabel")
        self.lbl_status.pack(side="left", padx=(16, 0))

        self.lbl_notes = ttk.Label(self.win, text="", style="Status.TLabel",
                                   justify="left")
        self.lbl_notes.pack(fill="x", padx=16, pady=(4, 0))

        holder = tk.Frame(self.win, bg=app.BG)
        holder.pack(fill="both", expand=True, padx=16, pady=(6, 4))
        self.tree = ttk.Treeview(
            holder, columns=_COLUMNS, show="tree headings",
            style="Summary.Treeview", selectmode="browse")
        self.tree.column("#0", width=420, minwidth=240, stretch=False)
        self.tree.column("mrs", width=70, minwidth=50, anchor="center",
                         stretch=False)
        # branches absorbs width when the user drags a column divider.
        self.tree.column("branches", width=420, minwidth=160, stretch=True)
        self.tree.column("bugfix", width=80, minwidth=60, anchor="center",
                         stretch=False)
        self.tree.column("scan", width=90, minwidth=60, anchor="center",
                         stretch=False)
        vsb = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure(
            "cat", background="#1e2d50", foreground="#ffffff",
            font=(ff, 10, "bold"))
        self.tree.tag_configure(
            "unmapped", background="#3a2a1a", foreground="#fcd34d",
            font=(ff, 10, "bold"))
        self.tree.tag_configure("idle", foreground="#7a8199")
        self.tree.tag_configure("mr", foreground="#aab4cf")
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Configure>", self._fit_columns, add="+")
        # A dragged column divider can push the count columns off screen.
        self.tree.bind("<ButtonRelease-1>",
                       lambda _e: self._fit_columns(), add="+")
        self.tree.bind("<Motion>", self._on_tree_motion, add="+")
        # Scrolling / clicking moves rows under a still pointer.
        for seq in ("<Leave>", "<MouseWheel>", "<Button-4>", "<Button-5>",
                    "<ButtonPress>", "<KeyPress>"):
            self.tree.bind(seq, lambda _e: self._hide_tip(), add="+")
        self._tip = None
        self._tip_key = None

        self.lbl_rules = ttk.Label(self.win, text="", style="Status.TLabel",
                                   justify="left")
        self.lbl_rules.pack(fill="x", padx=16, pady=(2, 12))
        self.win.bind("<Configure>", self._on_resize, add="+")

    def _fit_columns(self, event=None):
        """Keep every column on screen (there is no horizontal scrollbar).

        The first fit gives the name column ~38% (240–420 px); later fits
        keep whatever width the user dragged it to, shrinking it only as
        far as needed. The count columns keep their width (capped so a
        dragged one cannot starve the rest) and branches get the remainder.
        """
        try:
            width = event.width if event is not None else self.tree.winfo_width()
        except tk.TclError:
            return
        if width <= 1:
            return
        tree = self.tree
        counts = ("mrs", "bugfix", "scan")
        for c in counts:
            if int(tree.column(c, "width")) > 200:
                tree.column(c, width=200)
        fixed = sum(int(tree.column(c, "width")) for c in counts)
        if self._cols_fitted:
            name = int(tree.column("#0", "width"))
        else:
            name = min(420, int(width * 0.38))
            self._cols_fitted = True
        name = max(240, min(name, width - fixed - 160 - 4))
        branches = max(160, width - fixed - name - 4)
        tree.column("#0", width=name)
        tree.column("branches", width=branches)

    def _on_resize(self, _event=None):
        try:
            width = max(400, self.win.winfo_width() - 40)
            self.lbl_rules.configure(wraplength=width)
            self.lbl_notes.configure(wraplength=width)
        except tk.TclError:
            pass

    # --------------------------------------------------------------- text
    def refresh_text(self):
        if self._closed:
            return
        t = self._t
        self.win.title(t("dl_window_title"))
        self.lbl_heading.configure(text=t("dl_heading"))
        self.btn_prev.configure(text=t("dl_prev"))
        self.btn_next.configure(text=t("dl_next"))
        self.btn_pick.configure(text=t("dl_pick"))
        self.btn_refresh.configure(text=t("dl_refresh"))
        self.btn_copy.configure(text=t("dl_copy"))
        self.chk_idle.configure(text=t("dl_show_idle"))
        self.tree.heading("#0", text=t("dl_col_name"), anchor="w")
        for col in _COLUMNS:
            self.tree.heading(col, text=t(_COLUMN_KEYS[col]),
                              anchor="w" if col == "branches" else "center")
        for key in _KPIS:
            self.kpi_labels[key][0].configure(text=t(f"dl_kpi_{key}"))
        self.lbl_rules.configure(text=t("dl_rules"))
        self._render_day()
        self._render_status()
        self._render()

    def _day_text(self, d) -> str:
        weekdays = self._t("dl_weekdays").split(",")
        weekday = weekdays[d.weekday()] if len(weekdays) == 7 else ""
        if d == dl.today_utc8():
            weekday = self._t("dl_weekday_today").format(weekday=weekday)
        return self._t("dl_day").format(day=d.isoformat(), weekday=weekday)

    def _render_day(self):
        self.lbl_day.configure(text=self._day_text(self.day))
        at_today = self.day >= dl.today_utc8()
        self._set_enabled(self.btn_next, not at_today)

    def _set_enabled(self, btn, enabled):
        try:
            if isinstance(btn, ttk.Button):
                btn.state(["!disabled"] if enabled else ["disabled"])
            else:
                btn.configure(state="normal" if enabled else "disabled")
        except tk.TclError:
            pass

    def _render_status(self):
        t, a = self._t, self._status_args
        self._set_enabled(self.btn_refresh,
                          self._state not in ("loading", "resolving"))
        if self._state == "loading":
            self.app._mark_busy(self.lbl_status, t("dl_loading").format(**a))
        elif self._state == "resolving":
            done, total = self._progress
            self.app._mark_busy(self.lbl_status, t("dl_resolving").format(
                done=done, total=total))
        elif self._state == "failed":
            self.app._mark_idle(self.lbl_status, t("dl_failed").format(**a))
        elif self._state == "done" and self._report is not None:
            tot = self._report["totals"]
            complete = all(self._report["sources"].values())
            empty = complete and not any(
                (tot["mr_count"], tot["bugfix"], tot["scan"]))
            key = "dl_done_empty" if empty else "dl_done"
            self.app._mark_idle(self.lbl_status, t(key).format(
                day=self.day.isoformat(), mrs=_num(tot["mr_count"]),
                bugfix=_num(tot["bugfix"]), scan=_num(tot["scan"]),
                time=self._loaded_at))
        else:
            self.app._mark_idle(self.lbl_status, "")

    # --------------------------------------------------------------- load
    def load(self, day, force=False):
        """Show ``day``. Tranzor rows of finished days are cached per window;
        target branches are re-resolved every time (free for MRs already in
        mr_jira's process cache, a retry for ones that failed before)."""
        d = dl.coerce_day(day)
        if d is None or self._closed:
            return
        today = dl.today_utc8()
        d = min(d, today)
        self.day = d
        self._gen += 1
        gen = self._gen
        if self._cancel is not None:
            self._cancel.set()
        self._render_day()
        cached = None if force else self._cache.get(d)
        cancel = self._cancel = threading.Event()
        self._report = None
        self._branch_state = "pending"
        self._state = "loading"
        self._status_args = {"day": d.isoformat()}
        self._render_status()
        self._render()
        # Cache only a day that had settled when the fetch started: today,
        # or a day that ended minutes ago, is still gaining late finishers.
        complete = dl.is_settled(d)
        threading.Thread(target=self._work,
                         args=(d, gen, cancel, cached, complete),
                         daemon=True, name="data-lookback").start()

    def _work(self, d, gen, cancel, cached=None, complete=False):
        if cached is not None:
            data, fetched_at = cached
        else:
            try:
                data = self._fetch_day(d, cancel_event=cancel)
            except dl.LookbackCancelled:
                return
            except Exception as exc:  # pragma: no cover - sources are isolated
                self._post(gen, self._on_failed, str(exc))
                return
            fetched_at = datetime.now().strftime("%H:%M:%S")
        self._post(gen, self._on_rows, d, data, fetched_at,
                   complete and cached is None)
        keys = dl.mr_keys(data.get("mr") or [])
        if not keys:
            self._post(gen, self._on_meta, d, data, {}, "done")
            return
        if not self._can_resolve():
            self._post(gen, self._on_meta, d, data, None, "no_token")
            return
        try:
            meta = self._resolve_meta(
                keys, cancel_event=cancel,
                on_progress=lambda n, total: self._post(
                    gen, self._on_progress, n, total))
        except dl.LookbackCancelled:
            return
        except Exception:  # pragma: no cover - resolve_mr_meta is fail-open
            meta = {}
        self._post(gen, self._on_meta, d, data, meta, "done")

    def _post(self, gen, fn, *args):
        if self._closed:
            return

        def run():
            if self._closed or gen != self._gen:
                return
            try:
                if not self.win.winfo_exists():
                    return
            except tk.TclError:
                return
            fn(*args)

        try:
            self.app.root.after(0, run)
        except (tk.TclError, RuntimeError):
            pass

    def _on_failed(self, error):
        self._state = "failed"
        self._status_args = {"day": self.day.isoformat(), "error": error}
        self._render_status()
        self._render()

    def _on_rows(self, d, data, fetched_at, cache_it):
        if cache_it and not data.get("errors"):
            self._cache[d] = (data, fetched_at)
        self._loaded_at = fetched_at
        self._report = dl.build_report(
            d, mr_tasks=data.get("mr"), bugfix_rows=data.get("bugfix"),
            scan_tasks=data.get("scan"), errors=data.get("errors"))
        self._state = "resolving"
        self._progress = (0, len(dl.mr_keys(data.get("mr") or [])))
        self._render_status()
        self._render()

    def _on_progress(self, done, total):
        if self._state == "resolving":
            self._progress = (done, total)
            self._render_status()

    def _on_meta(self, d, data, meta, branch_state):
        self._branch_state = branch_state
        self._report = dl.build_report(
            d, mr_tasks=data.get("mr"), bugfix_rows=data.get("bugfix"),
            scan_tasks=data.get("scan"), mr_meta=meta,
            errors=data.get("errors"))
        self._state = "done"
        self._render_status()
        self._render()

    # ------------------------------------------------------------- render
    def _branch_cell(self, counter, has_mr, count):
        if not has_mr:
            return _UNAVAILABLE
        if not count:
            return ""
        if self._branch_state == "no_token":
            return _UNAVAILABLE
        if self._report is None or not self._report["branches_resolved"]:
            return self._t("dl_branch_pending")
        return dl.format_branches(
            counter, unknown_label=self._t("dl_branch_unknown"))

    def _render(self):
        if self._closed:
            return
        self._hide_tip()
        tree = self.tree
        # Remember what the user expanded / collapsed; it carries over to
        # the next render and to other days. An empty tree (loading) must
        # not wipe it.
        for iid in self._all_items():
            if not iid.startswith("mr:"):
                self._open_state[iid] = str(tree.item(iid, "open")).lower() \
                    in ("1", "true")
        same_day = self._rendered_day == self.day
        try:
            yview = tree.yview()[0]
        except tk.TclError:
            yview = 0.0
        tree.delete(*tree.get_children(""))
        self._mr_urls = {}
        self._full_text = {}
        report = self._report
        self._render_kpis(report)
        self._render_notes(report)
        if report is None:
            return
        self._rendered_day = self.day
        t = self._t
        has = report["sources"]
        show_idle = bool(self.show_idle_var.get())

        def nums(row, blank_zero=False):
            def cell(value, source):
                if not has[source]:
                    return _UNAVAILABLE
                return "" if (blank_zero and not value) else _num(value)
            return (
                cell(row["mr_count"], "mr"),
                self._branch_cell(row["branches"], has["mr"], row["mr_count"]),
                cell(row["bugfix"], "bugfix"),
                cell(row["scan"], "scan"),
            )

        for cat in report["categories"]:
            cid = f"cat:{cat['name']}"
            if cat["mapped"]:
                label = t("dl_cat_label").format(
                    name=cat["name"],
                    active=self._partial(cat["active_projects"], has),
                    total=cat["project_total"])
                tags = ("cat",)
            else:
                label = f"{t('dl_unmapped')}   ·   {cat['project_total']}"
                tags = ("unmapped",)
            tree.insert("", "end", iid=cid, text=label, values=nums(cat),
                        tags=tags, open=self._open_state.get(cid, True))
            for p in cat["projects"]:
                if not (show_idle or p["active"]):
                    continue
                pid = f"prj:{p['project']}"
                # Zero cells stay blank on project rows so the non-zero
                # counts stand out; category rows keep their 0.
                tree.insert(cid, "end", iid=pid, text=p["project"],
                            values=nums(p, blank_zero=True),
                            tags=() if p["active"] else ("idle",),
                            open=self._open_state.get(pid, False))
                for mr in p["mrs"]:
                    self._insert_mr(pid, mr)
        if same_day:
            try:
                tree.yview_moveto(yview)
            except tk.TclError:
                pass

    def _insert_mr(self, parent, mr):
        t = self._t
        parts = [f"!{mr['iid']}"]
        if mr.get("jira"):
            parts.append(mr["jira"])
        full_parts = list(parts)
        if mr.get("title"):
            parts.append(_shorten(mr["title"], _MR_TITLE_MAX))
            full_parts.append(" ".join(str(mr["title"]).split()))
        suffix = ""
        if mr["runs"] > 1:
            suffix = f"   ({t('dl_mr_runs').format(n=mr['runs'])})"
        label = "  ".join(parts) + suffix
        full_label = "  ".join(full_parts) + suffix
        if self._branch_state == "no_token":
            branch = _UNAVAILABLE
        elif not self._report["branches_resolved"]:
            branch = t("dl_branch_pending")
        else:
            branch = mr["branch"] or t("dl_branch_unknown")
        iid = f"mr:{mr['project_id']}!{mr['iid']}"
        self.tree.insert(parent, "end", iid=iid, text=label,
                         values=("", branch, "", ""), tags=("mr",))
        if full_label != label:
            self._full_text[iid] = full_label
        try:
            import mr_delivery
            url = mr_delivery.gitlab_mr_url(mr["project_id"], mr["iid"])
        except Exception:
            url = ""
        if url:
            self._mr_urls[iid] = url

    def _all_items(self):
        out = []
        stack = list(self.tree.get_children(""))
        while stack:
            iid = stack.pop()
            out.append(iid)
            stack.extend(self.tree.get_children(iid))
        return out

    @staticmethod
    def _partial(value, sources):
        """Counts that span every source: "—" when none loaded, "≥n" when
        only some did (a failed source may hide more activity)."""
        if not any(sources.values()):
            return _UNAVAILABLE
        if not all(sources.values()):
            return f"≥{value:,}"
        return _num(value)

    def _render_kpis(self, report):
        values = dict.fromkeys(_KPIS, _UNAVAILABLE)
        if report is not None:
            tot = report["totals"]
            values.update({
                "mrs": _num(tot["mr_count"]),
                "runs": _num(tot["runs"]),
                "bugfix": _num(tot["bugfix"]),
                "scan": _num(tot["scan"]),
                "projects": self._partial(
                    tot["active_projects"], report["sources"]),
            })
            if self._branch_state == "no_token" or not report["sources"]["mr"]:
                values["branches"] = _UNAVAILABLE
            elif not report["branches_resolved"]:
                values["branches"] = self._t("dl_branch_pending")
            elif tot["unknown_branch_mrs"]:
                # Unresolved MRs may target branches not counted yet.
                values["branches"] = (f"≥{tot['branch_count']:,}"
                                      if tot["branch_count"] else "?")
            else:
                values["branches"] = _num(tot["branch_count"])
        elif self._state == "loading":
            values = dict.fromkeys(_KPIS, self._t("dl_branch_pending"))
        for key in _KPIS:
            self.kpi_labels[key][1].configure(text=values[key])

    def _render_notes(self, report):
        t = self._t
        notes = []
        warn = False
        if report is not None:
            for src in _SOURCES:
                err = report["errors"].get(src)
                if not err:
                    continue
                kind, message = err
                reason = {"auth": t("dl_err_auth"),
                          "forbidden": t("dl_err_forbidden")}.get(
                              kind, _shorten(message, 160))
                notes.append(t("dl_err_source").format(
                    source=t(f"dl_src_{src}"), reason=reason))
                warn = True
            tot = report["totals"]
            if self._branch_state == "no_token" and tot["mr_count"]:
                notes.append(t("dl_note_no_token"))
                warn = True
            elif report["branches_resolved"] and tot["unknown_branch_mrs"]:
                notes.append(t("dl_note_unknown").format(
                    n=tot["unknown_branch_mrs"]))
                warn = True
            if tot["unmapped_projects"]:
                notes.append(t("dl_note_unmapped").format(
                    n=tot["unmapped_projects"]))
                warn = True
        if self.day >= dl.today_utc8():
            notes.append(t("dl_note_today"))
        text = "\n".join(notes)
        if warn:
            self.app._mark_hint(self.lbl_notes, text)
        else:
            self.app._mark_idle(self.lbl_notes, text)

    # ------------------------------------------------------------ actions
    def _step(self, delta):
        self.load(self.day + timedelta(days=delta))

    def _pick_date(self):
        date_picker.open_calendar(
            self.btn_pick, font_family=self.ff,
            get_value=lambda: self.day.isoformat(),
            set_value=lambda s: self.app.root.after(0, self.load, s),
            lang=lambda: self.app.lang, max_date=dl.today_utc8(),
            today=dl.today_utc8)

    def _copy(self):
        if self._report is None:
            return
        t = self._t
        text = dl.report_to_tsv(
            self._report,
            headers=[t("dl_col_name").split("/")[0].strip(),
                     t("dl_col_name").split("/")[-1].strip(),
                     t("dl_col_mrs"), t("dl_col_branches"),
                     t("dl_col_bugfix"), t("dl_col_scan")],
            unknown_label=t("dl_branch_unknown"),
            include_idle=bool(self.show_idle_var.get()),
            unresolved=(_UNAVAILABLE if self._branch_state == "no_token"
                        else t("dl_branch_pending")))
        text = f"{self._day_text(self.day)}\n{text}"
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(text)
        except tk.TclError:
            return
        rows = text.count("\n") - 2
        self.app._mark_idle(self.lbl_status,
                            t("dl_copied").format(n=max(0, rows)))

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        url = self._mr_urls.get(iid)
        if url:
            webbrowser.open(url)

    # ---------------------------------------------------- cell tooltip
    def _cell_text(self, iid, column):
        """Full text of a tree cell, and the width its column shows."""
        tree = self.tree
        if column == "#0":
            return (self._full_text.get(iid) or str(tree.item(iid, "text")),
                    int(tree.column("#0", "width")))
        try:
            idx = int(column.lstrip("#")) - 1
        except ValueError:
            return "", 0
        values = tree.item(iid, "values") or ()
        if not 0 <= idx < len(values):
            return "", 0
        return str(values[idx]), int(tree.column(_COLUMNS[idx], "width"))

    def _on_tree_motion(self, event):
        """Hover a clipped cell (a category's branch set, a long MR title)
        to read it in full."""
        iid = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        key = (iid, column)
        if key == self._tip_key:
            return
        self._hide_tip()
        if not iid:
            return
        text, width = self._cell_text(iid, column)
        if not text:
            return
        bold = bool(set(self.tree.item(iid, "tags") or ()) & {"cat", "unmapped"})
        font = tkfont.Font(family=self.ff, size=10 if bold else 9,
                           weight="bold" if bold else "normal")
        indent = 60 if column == "#0" else 12
        shortened = (column == "#0" and iid in self._full_text)
        if not shortened and font.measure(text) + indent <= width:
            return
        self._tip_key = key
        tip = self._tip = tk.Toplevel(self.win)
        tip.wm_overrideredirect(True)
        tip.configure(bg="#0f3460")
        tk.Label(tip, text=text, bg="#16213e", fg="#e0e0e0",
                 font=(self.ff, 9), justify="left", wraplength=720,
                 padx=8, pady=5).pack(padx=1, pady=1)
        # Keep it on screen: an overrideredirect window is not pulled back
        # by the window manager (a maximized window's right edge).
        tip.update_idletasks()
        w, h = tip.winfo_reqwidth(), tip.winfo_reqheight()
        sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
        x, y = event.x_root + 14, event.y_root + 16
        if x + w > sw - 4:
            x = max(0, min(event.x_root - w - 8, sw - w - 4))
        if y + h > sh - 4:
            y = max(0, event.y_root - h - 8)
        tip.geometry(f"+{x}+{y}")

    def _hide_tip(self):
        self._tip_key = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    # -------------------------------------------------------------- close
    def exists(self) -> bool:
        if self._closed:
            return False
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def focus(self):
        try:
            if self.win.state() == "iconic":
                self.win.deiconify()
            self.win.lift()
            self.win.focus_set()
        except tk.TclError:
            pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._hide_tip()
        if self._cancel is not None:
            self._cancel.set()
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        if self._on_close_cb is not None:
            try:
                self._on_close_cb()
            except Exception:
                pass
