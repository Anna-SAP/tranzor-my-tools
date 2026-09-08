"""BugFix tracking tab for Tranzor Translation Exporter.

The UI renders Platform history first and treats GitLab enrichment as a
separate, fail-open data source. MR discussions are fetched only for the
selected row. All network work runs off the tkinter thread.
"""
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Any

import bugfix_panel as bf
import gitlab_client
from export_gui import FONT_FAMILY, FONT_MONO
from time_display import format_display_datetime


STRINGS = {
    "en": {
        "tab_bugfix": "🐞 BugFix",
        "bf_hint": (
            "Every Platform Bug Fix submission in one view. Bug Fix / TM "
            "status and GitLab MR status are independent: Applied does not "
            "mean Merged. Important discussions load only when a row is selected."
        ),
        "bf_project": "Project",
        "bf_workflow": "Bug Fix status",
        "bf_mr_state": "MR status",
        "bf_search": "Search Bug ID, MR, submission…",
        "bf_all": "All",
        "bf_refresh": "⟳ Refresh live",
        "bf_reset": "Reset",
        "bf_open_platform": "Open Tranzor",
        "bf_open_mr": "Open MR",
        "bf_total": "Submissions",
        "bf_action": "Needs action",
        "bf_open": "Open MR",
        "bf_direct": "Direct / no MR",
        "bf_ready": "Ready",
        "bf_loading_cache": "Loading last successful snapshot…",
        "bf_syncing": "Syncing Platform history and live MR states…",
        "bf_live": (
            "Live · {shown}/{total} shown · {pages} Platform page(s) · "
            "{errors} GitLab row error(s) · synced {time}"
        ),
        "bf_cached": (
            "Cached · {shown}/{total} shown · saved {time} · "
            "live refresh failed: {error}"
        ),
        "bf_failed": "Refresh failed: {error}",
        "bf_empty": "No Bug Fix submissions match these filters.",
        "bf_detail_placeholder": (
            "Select a submission to inspect its MR, important comments, "
            "warnings, and corrected strings."
        ),
        "bf_comments_loading": "Loading important MR discussions…",
        "bf_comments_none": "No high-signal human comments found.",
        "bf_comments_title": "IMPORTANT MR COMMENTS",
        "bf_records_title": "CORRECTION RECORDS",
        "bf_col_attention": "Attention",
        "bf_col_bug": "Bug / Jira",
        "bf_col_project": "Project",
        "bf_col_locale": "Locale",
        "bf_col_strings": "Strings",
        "bf_col_workflow": "Bug Fix / TM",
        "bf_col_mr": "MR",
        "bf_col_mr_state": "MR state",
        "bf_col_activity": "MR activity",
        "bf_col_created": "Created",
        "bf_comments_error": "Comments unavailable: {error}",
        "bf_no_mr_explain": (
            "No MR is linked. This can be a valid direct or Blob-based apply; "
            "it is not treated as an error."
        ),
    },
    "zh": {
        "tab_bugfix": "🐞 BugFix 面板",
        "bf_hint": (
            "集中查看 Platform 的全部 Bug Fix 记录。Bug Fix / TM 状态与 "
            "GitLab MR 状态彼此独立：Applied 不代表 Merged。选中记录后才读取"
            "重要 discussions，避免批量请求。"
        ),
        "bf_project": "项目",
        "bf_workflow": "Bug Fix 状态",
        "bf_mr_state": "MR 状态",
        "bf_search": "搜索 Bug ID、MR、Submission…",
        "bf_all": "全部",
        "bf_refresh": "⟳ 实时刷新",
        "bf_reset": "重置",
        "bf_open_platform": "打开 Tranzor",
        "bf_open_mr": "打开 MR",
        "bf_total": "记录总数",
        "bf_action": "需要处理",
        "bf_open": "Open MR",
        "bf_direct": "直写 / 无 MR",
        "bf_ready": "就绪",
        "bf_loading_cache": "正在读取上次成功快照…",
        "bf_syncing": "正在同步 Platform 历史与实时 MR 状态…",
        "bf_live": (
            "实时 · 显示 {shown}/{total} · Platform {pages} 页 · "
            "GitLab 行错误 {errors} · 同步于 {time}"
        ),
        "bf_cached": (
            "缓存 · 显示 {shown}/{total} · 保存于 {time} · "
            "实时刷新失败：{error}"
        ),
        "bf_failed": "刷新失败：{error}",
        "bf_empty": "当前筛选条件下没有 Bug Fix 记录。",
        "bf_detail_placeholder": (
            "选中记录可查看 MR、重要 comments、告警和具体修复内容。"
        ),
        "bf_comments_loading": "正在读取重要 MR discussions…",
        "bf_comments_none": "没有发现高信号的人类 comments。",
        "bf_comments_title": "重要 MR COMMENTS",
        "bf_records_title": "修复记录",
        "bf_col_attention": "关注",
        "bf_col_bug": "Bug / Jira",
        "bf_col_project": "项目",
        "bf_col_locale": "语种",
        "bf_col_strings": "字符串",
        "bf_col_workflow": "Bug Fix / TM",
        "bf_col_mr": "MR",
        "bf_col_mr_state": "MR 状态",
        "bf_col_activity": "MR 动态",
        "bf_col_created": "创建时间",
        "bf_comments_error": "Comments 暂不可用：{error}",
        "bf_no_mr_explain": (
            "没有关联 MR。这可能是合法的直写或 Blob-based apply，"
            "面板不会将它误判为错误。"
        ),
    },
}


_MR_FILTERS = ("", "opened", "merged", "closed", "none", "unknown")
_MR_LABEL_KEYS = {
    "": "bf_all",
    "opened": "bf_open",
    "merged": "bf_col_mr_state",
    "closed": "bf_col_mr_state",
    "none": "bf_direct",
    "unknown": "bf_col_mr_state",
}
_AUTO_REFRESH_MS = 5 * 60 * 1000
_MAX_DETAIL_RECORDS = 200


def _clip(value: Any, limit: int = 64) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _display_time(value: Any) -> str:
    if not value:
        return "—"
    try:
        return format_display_datetime(str(value))
    except Exception:
        return str(value)


class BugFixTab:
    """Lazy, cached Bug Fix history + live GitLab tracking panel."""

    _COLS = (
        "attention", "bug", "project", "locale", "strings",
        "workflow", "mr", "mr_state", "activity", "created",
    )

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self._first_shown = False
        self._syncing = False
        self._stopped = False
        self._auto_after_id = None
        self._filter_after_id = None
        self._all_rows: list[dict[str, Any]] = []
        self._row_by_iid: dict[str, dict[str, Any]] = {}
        self._comment_loading: set[str] = set()
        self._comment_attempted: set[str] = set()
        self._gitlab = gitlab_client.GitLabClient()
        self._last_result: dict[str, Any] = {}
        self._build(parent)
        self.refresh_text()

    def _t(self, key):
        return self.app._t(key)

    def _base_url(self) -> str:
        import export_mr_pipeline as mr_api
        return mr_api.TRANZOR_URL

    def _button(self, parent, *, command, accent=False):
        return self.app._create_button(
            parent,
            text="",
            command=command,
            style_name="AccentSmall" if accent else "SecondarySmall",
            font=(FONT_FAMILY, 10, "bold" if accent else "normal"),
            bg="#e94560" if accent else "#0f3460",
            fg="#fff" if accent else "#ccc",
            padx=12,
            pady=4,
        )

    def _build(self, parent):
        content = ttk.Frame(parent, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=16, pady=8)

        self.lbl_hint = ttk.Label(
            content, text="", style="Status.TLabel",
            wraplength=1450, justify="left")
        self.lbl_hint.pack(fill="x", pady=(0, 8))

        filters = ttk.Frame(content, style="App.TFrame")
        filters.pack(fill="x", pady=(0, 7))

        self.lbl_project = ttk.Label(
            filters, text="", style="Status.TLabel")
        self.lbl_project.pack(side="left")
        self.var_project = tk.StringVar()
        self.cmb_project = ttk.Combobox(
            filters, textvariable=self.var_project,
            state="readonly", width=18)
        self.cmb_project.pack(side="left", padx=(5, 12))
        self.cmb_project.bind("<<ComboboxSelected>>", self._on_filter_change)

        self.lbl_workflow = ttk.Label(
            filters, text="", style="Status.TLabel")
        self.lbl_workflow.pack(side="left")
        self.var_workflow = tk.StringVar()
        self.cmb_workflow = ttk.Combobox(
            filters, textvariable=self.var_workflow,
            state="readonly", width=17)
        self.cmb_workflow.pack(side="left", padx=(5, 12))
        self.cmb_workflow.bind(
            "<<ComboboxSelected>>", self._on_filter_change)

        self.lbl_mr_state = ttk.Label(
            filters, text="", style="Status.TLabel")
        self.lbl_mr_state.pack(side="left")
        self.var_mr_state = tk.StringVar()
        self.cmb_mr_state = ttk.Combobox(
            filters, textvariable=self.var_mr_state,
            state="readonly", width=16)
        self.cmb_mr_state.pack(side="left", padx=(5, 12))
        self.cmb_mr_state.bind(
            "<<ComboboxSelected>>", self._on_filter_change)

        self.var_search = tk.StringVar()
        self.ent_search = ttk.Entry(
            filters, textvariable=self.var_search, width=31)
        self.ent_search.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.ent_search.bind("<Return>", self._on_filter_change)
        self.var_search.trace_add("write", self._schedule_filter)

        self.btn_refresh = self._button(
            filters, command=self.refresh_live, accent=True)
        self.btn_refresh.pack(side="left")
        self.btn_reset = self._button(filters, command=self._reset_filters)
        self.btn_reset.pack(side="left", padx=(6, 0))
        self.btn_platform = self._button(
            filters, command=self._open_platform)
        self.btn_platform.pack(side="left", padx=(6, 0))

        kpis = ttk.Frame(content, style="App.TFrame")
        kpis.pack(fill="x", pady=(0, 7))
        self._kpis = {}
        for key in ("total", "action", "open", "direct"):
            card = ttk.Frame(kpis, style="Card.TFrame")
            card.pack(side="left", fill="x", expand=True, padx=(0, 7))
            title = ttk.Label(card, text="", style="CardBold.TLabel")
            title.pack(anchor="w", padx=10, pady=(7, 0))
            value = ttk.Label(card, text="—", style="Card.TLabel")
            value.pack(anchor="w", padx=10, pady=(0, 7))
            self._kpis[key] = (title, value)

        self.lbl_status = ttk.Label(
            content, text="", style="Status.TLabel",
            wraplength=1450, justify="left")
        self.lbl_status.pack(fill="x", pady=(0, 6))

        pane = ttk.Panedwindow(content, orient="horizontal")
        pane.pack(fill="both", expand=True)

        left = ttk.Frame(pane, style="App.TFrame")
        right = ttk.Frame(pane, style="Card.TFrame")
        pane.add(left, weight=4)
        pane.add(right, weight=2)

        self.tree = ttk.Treeview(
            left, columns=self._COLS, show="headings",
            style="Summary.Treeview", selectmode="browse", height=18)
        widths = {
            "attention": 105, "bug": 90, "project": 135,
            "locale": 90, "strings": 60, "workflow": 105,
            "mr": 70, "mr_state": 90, "activity": 135, "created": 135,
        }
        anchors = {"strings": "center", "mr": "center"}
        for col in self._COLS:
            self.tree.column(
                col, width=widths[col], minwidth=50,
                anchor=anchors.get(col, "w"),
                stretch=col in {"project", "activity"})
        self.tree.tag_configure("action", foreground="#fca5a5")
        self.tree.tag_configure("watch", foreground="#fbbf24")
        self.tree.tag_configure("done", foreground="#86efac")
        self.tree.tag_configure("direct", foreground="#7dd3fc")
        self.tree.tag_configure("unknown", foreground="#d1d5db")

        ybar = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(
            yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self._open_selected_mr())

        actions = ttk.Frame(right, style="Card.TFrame")
        actions.pack(fill="x", padx=8, pady=(8, 4))
        self.btn_open_mr = self._button(
            actions, command=self._open_selected_mr, accent=True)
        self.btn_open_mr.pack(side="right")
        self.btn_open_mr.configure(state="disabled")
        self.detail = tk.Text(
            right, wrap="word", bg="#0a0a1a", fg="#e4e7ef",
            insertbackground="#fff", relief="flat",
            font=(FONT_MONO, 9), padx=10, pady=8)
        detail_scroll = ttk.Scrollbar(
            right, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side="right", fill="y", padx=(0, 4), pady=(0, 8))
        self.detail.pack(
            side="left", fill="both", expand=True,
            padx=(8, 0), pady=(0, 8))
        self._set_detail("")

    def refresh_text(self):
        t = self._t
        self.lbl_hint.configure(text=t("bf_hint"))
        self.lbl_project.configure(text=t("bf_project"))
        self.lbl_workflow.configure(text=t("bf_workflow"))
        self.lbl_mr_state.configure(text=t("bf_mr_state"))
        self.btn_refresh.configure(text=t("bf_refresh"))
        self.btn_reset.configure(text=t("bf_reset"))
        self.btn_platform.configure(text=t("bf_open_platform"))
        self.btn_open_mr.configure(text=t("bf_open_mr"))
        self._kpis["total"][0].configure(text=t("bf_total"))
        self._kpis["action"][0].configure(text=t("bf_action"))
        self._kpis["open"][0].configure(text=t("bf_open"))
        self._kpis["direct"][0].configure(text=t("bf_direct"))

        headings = {
            "attention": "bf_col_attention",
            "bug": "bf_col_bug",
            "project": "bf_col_project",
            "locale": "bf_col_locale",
            "strings": "bf_col_strings",
            "workflow": "bf_col_workflow",
            "mr": "bf_col_mr",
            "mr_state": "bf_col_mr_state",
            "activity": "bf_col_activity",
            "created": "bf_col_created",
        }
        for col, key in headings.items():
            self.tree.heading(col, text=t(key))

        project_raw = self._project_raw()
        workflow_raw = self._workflow_raw()
        mr_raw = self._mr_raw()
        self._refresh_filter_values(
            project_raw=project_raw,
            workflow_raw=workflow_raw,
            mr_raw=mr_raw,
        )
        if not self._all_rows:
            self.ent_search.configure()
            self._set_detail(t("bf_detail_placeholder"))
            if not self._syncing:
                self._idle(t("bf_ready"))
        else:
            self._apply_filters()

    def _project_options(self):
        projects = sorted({
            str(row.get("project_id") or "")
            for row in self._all_rows if row.get("project_id")
        }, key=str.lower)
        return [("", self._t("bf_all"))] + [
            (item, item) for item in projects]

    def _workflow_options(self):
        values = {
            str(row.get("platform_status") or "")
            for row in self._all_rows if row.get("platform_status")
        }
        values.update(
            str(item or "").strip().lower().replace(" ", "_")
            for item in (self._last_result.get("available_statuses") or [])
            if item
        )
        return [("", self._t("bf_all"))] + [
            (item, item.replace("_", " ").title())
            for item in sorted(values)
        ]

    def _mr_options(self):
        labels = {
            "": self._t("bf_all"),
            "opened": "Open",
            "merged": "Merged",
            "closed": "Closed",
            "none": self._t("bf_direct"),
            "unknown": "Unknown",
        }
        return [(raw, labels[raw]) for raw in _MR_FILTERS]

    @staticmethod
    def _raw_from_display(display, options):
        for raw, label in options:
            if display == label or display == raw:
                return raw
        return ""

    def _project_raw(self):
        return self._raw_from_display(
            self.var_project.get(), self._project_options())

    def _workflow_raw(self):
        return self._raw_from_display(
            self.var_workflow.get(), self._workflow_options())

    def _mr_raw(self):
        return self._raw_from_display(
            self.var_mr_state.get(), self._mr_options())

    def _refresh_filter_values(
            self, *, project_raw="", workflow_raw="", mr_raw=""):
        for combo, variable, options, raw in (
            (self.cmb_project, self.var_project,
             self._project_options(), project_raw),
            (self.cmb_workflow, self.var_workflow,
             self._workflow_options(), workflow_raw),
            (self.cmb_mr_state, self.var_mr_state,
             self._mr_options(), mr_raw),
        ):
            combo.configure(values=[label for _key, label in options])
            label = next(
                (label for key, label in options if key == raw),
                options[0][1],
            )
            variable.set(label)

    def _busy(self, text):
        try:
            self.app._mark_busy(self.lbl_status, text)
        except Exception:
            self.lbl_status.configure(text=text)

    def _idle(self, text):
        try:
            self.app._mark_idle(self.lbl_status, text)
        except Exception:
            self.lbl_status.configure(text=text)

    def _set_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text or "")
        self.detail.configure(state="disabled")

    def on_first_show(self):
        if self._first_shown:
            return
        self._first_shown = True
        self._busy(self._t("bf_loading_cache"))
        cached = bf.load_cache()
        if cached and cached.get("submissions"):
            cached.update({
                "ok": True, "live_ok": False, "source": "cache",
                "stale": True, "error": "",
            })
            self._apply_sync_result(cached, cache_only=True)
        else:
            self._idle(self._t("bf_ready"))
        self.refresh_live()
        self._schedule_auto_refresh()

    def refresh_live(self):
        if self._syncing or self._stopped:
            return
        self._syncing = True
        self._comment_attempted.clear()
        self.btn_refresh.configure(state="disabled")
        self._busy(self._t("bf_syncing"))

        def work():
            result = bf.sync_panel(
                self._base_url(), gitlab_client=self._gitlab)
            self._safe_after(lambda: self._apply_sync_result(result))

        threading.Thread(
            target=work, daemon=True, name="bugfix-panel-sync").start()

    def _safe_after(self, callback):
        if self._stopped:
            return
        try:
            self.parent.after(0, callback)
        except Exception:
            pass

    def _apply_sync_result(self, result, cache_only=False):
        if self._stopped:
            return
        self._syncing = False
        self.btn_refresh.configure(state="normal")
        self._last_result = dict(result or {})
        rows = result.get("submissions") if isinstance(result, dict) else []
        self._all_rows = bf.stable_sort_submissions(rows or [])
        current_project = self._project_raw()
        current_workflow = self._workflow_raw()
        current_mr = self._mr_raw()
        self._refresh_filter_values(
            project_raw=current_project,
            workflow_raw=current_workflow,
            mr_raw=current_mr,
        )
        shown = self._apply_filters()

        if cache_only:
            saved = result.get("saved_at") or "—"
            self._idle(self._t("bf_cached").format(
                shown=shown,
                total=result.get("total_submissions") or len(self._all_rows),
                time=_display_time(saved),
                error=self._t("bf_syncing"),
            ))
        elif result.get("live_ok"):
            self._idle(self._t("bf_live").format(
                shown=shown,
                total=result.get("total_submissions") or len(self._all_rows),
                pages=result.get("pages_fetched") or 0,
                errors=result.get("gitlab_error_count") or 0,
                time=_display_time(result.get("synced_at")),
            ))
        elif result.get("source") == "cache":
            self._idle(self._t("bf_cached").format(
                shown=shown,
                total=result.get("total_submissions") or len(self._all_rows),
                time=_display_time(result.get("saved_at")),
                error=result.get("error") or "unknown",
            ))
        else:
            self._idle(self._t("bf_failed").format(
                error=result.get("error") or "unknown"))

    def _schedule_filter(self, *_args):
        if self._filter_after_id is not None:
            try:
                self.parent.after_cancel(self._filter_after_id)
            except Exception:
                pass
        try:
            self._filter_after_id = self.parent.after(
                180, self._apply_filters)
        except Exception:
            self._filter_after_id = None

    def _on_filter_change(self, _event=None):
        self._apply_filters()

    def _apply_filters(self, select_submission=""):
        self._filter_after_id = None
        selected = select_submission or self._selected_submission_id()
        rows = bf.filter_submissions(
            self._all_rows,
            project=self._project_raw(),
            platform_status=self._workflow_raw(),
            mr_state=self._mr_raw(),
            query=self.var_search.get(),
        )

        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._row_by_iid.clear()
        select_iid = ""
        for index, row in enumerate(rows):
            submission_id = str(row.get("submission_id") or f"row-{index}")
            iid = submission_id
            suffix = 1
            while iid in self._row_by_iid:
                suffix += 1
                iid = f"{submission_id}-{suffix}"
            attn = row.get("attention") or {}
            mr_number = (
                f"!{row.get('mr_iid')}" if row.get("mr_iid") else "—")
            locale = ", ".join(row.get("target_languages") or []) or "—"
            tag = str(attn.get("level") or "unknown")
            if tag not in {"action", "watch", "done", "direct", "unknown"}:
                tag = "unknown"
            self.tree.insert(
                "", "end", iid=iid,
                values=(
                    attn.get("reason") or "—",
                    row.get("bug_id") or "—",
                    row.get("project_id") or "—",
                    locale,
                    row.get("string_count") or 0,
                    row.get("platform_status_label") or "Unknown",
                    mr_number,
                    row.get("mr_state_label") or "Unknown",
                    _display_time(row.get("mr_updated_at")),
                    _display_time(row.get("created_at")),
                ),
                tags=(tag,),
            )
            self._row_by_iid[iid] = row
            if submission_id == selected:
                select_iid = iid

        self._update_kpis(rows)
        if select_iid:
            self.tree.selection_set(select_iid)
            self.tree.focus(select_iid)
            self.tree.see(select_iid)
            self._show_detail(self._row_by_iid[select_iid])
        elif not rows:
            self._set_detail(self._t("bf_detail_placeholder"))
            if self._all_rows and not self._syncing:
                self._idle(self._t("bf_empty"))
        return len(rows)

    def _update_kpis(self, rows):
        action = sum(
            (row.get("attention") or {}).get("priority") == 0
            for row in rows)
        opened = sum(row.get("mr_state") == "opened" for row in rows)
        direct = sum(not row.get("has_mr") for row in rows)
        for key, value in (
            ("total", len(rows)), ("action", action),
            ("open", opened), ("direct", direct),
        ):
            self._kpis[key][1].configure(text=str(value))

    def _reset_filters(self):
        self.var_search.set("")
        self._refresh_filter_values()
        self._apply_filters()

    def _selected_row(self):
        selected = self.tree.selection()
        if not selected:
            return None
        return self._row_by_iid.get(selected[0])

    def _selected_submission_id(self):
        row = self._selected_row()
        return str((row or {}).get("submission_id") or "")

    def _on_select(self, _event=None):
        row = self._selected_row()
        if not row:
            self.btn_open_mr.configure(state="disabled")
            self._set_detail(self._t("bf_detail_placeholder"))
            return
        self.btn_open_mr.configure(
            state="normal" if row.get("mr_url") else "disabled")
        self._show_detail(row)
        if (
            row.get("has_mr")
            and not row.get("comments_loaded")
            and row.get("submission_id") not in self._comment_loading
            and row.get("submission_id") not in self._comment_attempted
        ):
            self._load_selected_comments(row)

    def _load_selected_comments(self, row):
        sid = str(row.get("submission_id") or "")
        if not sid:
            return
        self._comment_loading.add(sid)
        self._comment_attempted.add(sid)
        self._show_detail(row, comments_loading=True)

        def work():
            enriched = bf.enrich_submission(
                row, self._gitlab, include_discussions=True,
                force_refresh=True)
            self._safe_after(
                lambda: self._apply_comment_result(sid, enriched))

        threading.Thread(
            target=work, daemon=True,
            name=f"bugfix-comments-{sid[:12]}").start()

    def _apply_comment_result(self, sid, enriched):
        self._comment_loading.discard(sid)
        replaced = False
        for index, row in enumerate(self._all_rows):
            if str(row.get("submission_id") or "") == sid:
                self._all_rows[index] = enriched
                replaced = True
                break
        if not replaced:
            return
        self._all_rows = bf.stable_sort_submissions(self._all_rows)
        self._apply_filters(select_submission=sid)

    def _show_detail(self, row, comments_loading=False):
        attn = row.get("attention") or {}
        lines = [
            f"{attn.get('reason') or '—'}",
            "",
            f"Bug / Jira:       {row.get('bug_id') or '—'}",
            f"Submission:       {row.get('submission_id') or '—'}",
            f"Project:          {row.get('project_id') or '—'}",
            f"Target branch:    {row.get('target_branch') or '—'}",
            f"Locales:          {', '.join(row.get('target_languages') or []) or '—'}",
            f"Created by:       {row.get('created_by') or '—'}",
            f"Created:          {_display_time(row.get('created_at'))}",
            "",
            "TWO INDEPENDENT STATUS AXES",
            "──────────────────────────",
            f"Bug Fix / TM:     {row.get('platform_status_label') or 'Unknown'}",
            f"GitLab MR:        {row.get('mr_state_label') or 'Unknown'}",
        ]

        if row.get("has_mr"):
            lines.extend([
                f"MR:               !{row.get('mr_iid') or '?'}",
                f"MR title:         {row.get('mr_title') or '—'}",
                f"Draft:            {'yes' if row.get('draft') else 'no'}",
                f"Conflicts:        {'yes' if row.get('has_conflicts') else 'no'}",
                f"Merge detail:     {row.get('detailed_merge_status') or row.get('merge_status') or '—'}",
                f"Pipeline:         {row.get('pipeline_status') or '—'}",
                f"MR updated:       {_display_time(row.get('mr_updated_at'))}",
                f"MR URL:           {row.get('mr_url') or '—'}",
            ])
        else:
            lines.extend(["", self._t("bf_no_mr_explain")])

        if row.get("create_error"):
            lines.extend([
                "",
                f"Create error:     {row.get('create_error')}",
                f"Error code:       {row.get('create_error_code') or '—'}",
            ])
        if row.get("auto_approval_warning"):
            lines.extend([
                "",
                f"Approval warning: {row.get('auto_approval_warning')}",
            ])
        if row.get("mr_sync_error"):
            lines.extend([
                "",
                f"GitLab sync:      {row.get('mr_sync_error')}",
            ])

        if row.get("has_mr"):
            lines.extend(["", self._t("bf_comments_title"),
                          "──────────────────────────"])
            if comments_loading:
                lines.append(self._t("bf_comments_loading"))
            elif row.get("comments_loaded"):
                comments = row.get("comments") or []
                if not comments:
                    lines.append(self._t("bf_comments_none"))
                for comment in comments:
                    badge = (
                        "UNRESOLVED" if comment.get("unresolved")
                        else str(comment.get("reason") or "COMMENT").upper())
                    lines.extend([
                        f"[{badge}] {comment.get('author') or 'Unknown'}"
                        f" · {_display_time(comment.get('updated_at') or comment.get('created_at'))}",
                        str(comment.get("body") or ""),
                        "",
                    ])
            else:
                lines.append(self._t("bf_comments_loading"))

        lines.extend(["", self._t("bf_records_title"),
                      "──────────────────────────"])
        records = row.get("records") or []
        if not records:
            lines.append("(none)")
        for index, record in enumerate(records[:_MAX_DETAIL_RECORDS], 1):
            key = (
                record.get("logical_key")
                or record.get("current_opus_id")
                or record.get("legacy_opus_id")
                or f"record {index}"
            )
            lines.extend([
                f"{index}. {_clip(key, 100)}"
                f"  [{record.get('user_status') or '—'}]",
                f"   {record.get('target_language') or ''}: "
                f"{_clip(record.get('old_target_text'), 180)}",
                f"   → {_clip(record.get('corrected_text'), 180)}",
            ])
            if record.get("create_error"):
                lines.append(f"   ERROR: {record.get('create_error')}")
        if len(records) > _MAX_DETAIL_RECORDS:
            lines.append(
                f"… {len(records) - _MAX_DETAIL_RECORDS} more record(s)")

        self._set_detail("\n".join(lines))

    def _open_platform(self):
        webbrowser.open(self._base_url().rstrip("/") + "/static/bug-fix")

    def _open_selected_mr(self):
        row = self._selected_row()
        url = str((row or {}).get("mr_url") or "")
        if url:
            webbrowser.open(url)

    def _schedule_auto_refresh(self):
        if self._stopped:
            return
        if self._auto_after_id is not None:
            try:
                self.parent.after_cancel(self._auto_after_id)
            except Exception:
                pass
        try:
            self._auto_after_id = self.parent.after(
                _AUTO_REFRESH_MS, self._auto_refresh)
        except Exception:
            self._auto_after_id = None

    def _auto_refresh(self):
        self._auto_after_id = None
        if self._stopped:
            return
        try:
            visible = bool(self.parent.winfo_ismapped())
        except Exception:
            visible = False
        if visible:
            self.refresh_live()
        self._schedule_auto_refresh()

    def stop(self):
        self._stopped = True
        for after_id in (self._auto_after_id, self._filter_after_id):
            if after_id is not None:
                try:
                    self.parent.after_cancel(after_id)
                except Exception:
                    pass
        self._auto_after_id = None
        self._filter_after_id = None
