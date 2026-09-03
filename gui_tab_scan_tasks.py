"""
Scan Tasks — GUI Tab
====================
展示 Tranzor 平台中"Missing Translation Scan"手动触发的任务列表，
支持按 Project / Status / Task ID 过滤，以及选中后导出翻译结果。

API: /api/v1/missing_translation_scan/tasks
翻译结果 schema 与 MR 翻译任务一致，直接复用 export_mr_pipeline.save_mr_file。
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_mr_pipeline as mr_api
from export_gui import (
    FONT_FAMILY, IS_MAC, format_age_days, reveal_in_folder,
    sanitize_for_filename, export_output_dir,
)
from time_display import format_display_datetime
import task_post_edit as _tpe
import advanced_filter
# Aliased so the ``llm_qa`` boolean flag threaded through the export handlers
# doesn't shadow the module inside those methods.
import llm_qa as llm_qa_module


STRINGS = {
    "en": {
        "tab_scan_tasks":        "🔎 Scan Tasks",
        "scan_project":          "Project",
        "scan_status":           "Status",
        "scan_task_id":          "Task ID",
        "scan_search":           "🔍 Search",
        "scan_reset":            "Reset",
        "scan_export":           "📦 Export Selected",
        "scan_sidebar_title":    "🔎 Scan Task Stats",
        "scan_stat_total":       "Total Tasks",
        "scan_stat_completed":   "Completed",
        "scan_stat_running":     "Running",
        "scan_stat_failed":      "Failed",
        "scan_col_idx":          "#",
        "scan_col_task_name":    "Task Name",
        "scan_col_project":      "Project",
        "scan_col_base_ref":     "Base Ref",
        "scan_col_head_ref":     "Head Ref",
        "scan_col_status":       "Status",
        "scan_col_src_strings":  "en-US Strings",
        "scan_col_output_mode":  "Output Mode",
        "scan_col_created":      "Created",
        "scan_col_age":          "Age",
        "scan_post_edit_legend": "✏️ = later translation content change (post-edit or refined iteration)",
    },
    "zh": {
        "tab_scan_tasks":        "🔎 扫描任务",
        "scan_project":          "项目",
        "scan_status":           "状态",
        "scan_task_id":          "Task ID",
        "scan_search":           "🔍 查询",
        "scan_reset":            "重置",
        "scan_export":           "📦 导出选中",
        "scan_sidebar_title":    "🔎 扫描任务统计",
        "scan_stat_total":       "总任务数",
        "scan_stat_completed":   "已完成",
        "scan_stat_running":     "运行中",
        "scan_stat_failed":      "失败",
        "scan_col_idx":          "#",
        "scan_col_task_name":    "任务名称",
        "scan_col_project":      "项目",
        "scan_col_base_ref":     "Base Ref",
        "scan_col_head_ref":     "Head Ref",
        "scan_col_status":       "状态",
        "scan_col_src_strings":  "en-US 字符串数",
        "scan_col_output_mode":  "输出模式",
        "scan_col_created":      "创建时间",
        "scan_col_age":          "距今",
        "scan_post_edit_legend": "✏️ = 该任务后期发生过翻译内容变更（人工修订或迭代精修）",
    },
}


class ScanTasksTab:
    """Builds and manages the Scan Tasks tab content."""

    # Single source of truth for the task-list table columns. ``created``
    # (the scan task's trigger timestamp) is at index 7 and ``age`` (its
    # human-readable "N days ago" annotation) at index 8 — kept paired on
    # purpose. Resolve positional reads via ``.index(...)`` against this so a
    # future column reshuffle can't silently point them at the wrong cell.
    _SCAN_COLUMNS = ("idx", "task_name", "project", "base_ref", "head_ref",
                     "status", "src_strings", "output_mode", "created", "age")

    @staticmethod
    def _build_export_filename(ext, *, task_name="", id_tag="", type_tag="",
                               created="", export_date=""):
        """Compose the Scan Tasks export filename (HTML / Excel / JSON).

        Mirrors ``MRPipelineTab._build_export_filename``: the same scan task
        can be re-run at different times, so the date segment stamps the
        task's Created/trigger time (``created``, e.g. ``"2026-06-17
        14:42:26"`` → ``"2026-06-17_14-42-26"``) rather than only the export
        date — which is identical for every same-day export — keeping per-run
        files distinct and human-recognizable. Falls back to ``export_date``
        when no Created time is available.

        ``task_name`` is embedded before the task-uuid prefix so the name
        reads at a glance; ``id_tag`` (uuid prefix) and ``type_tag``
        (``changes`` / ``all``) follow, then the date segment.
        """
        date_tag = sanitize_for_filename(created) or sanitize_for_filename(export_date)
        name = sanitize_for_filename(task_name) if task_name else ""
        parts = ["scan_task"]
        if name:
            parts.append(name)
        parts.extend(seg for seg in (id_tag, type_tag, date_tag) if seg)
        return "_".join(parts) + ext

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self.scan_page = 0
        self.scan_page_size = 20
        self.scan_total = 0
        self.scan_filtered_total = 0
        self.scan_loading = False
        self._loading_anim_id = None
        self._loading_dot_count = 0
        # task_id → Treeview iid; populated by _on_tasks_loaded so the
        # async post-edit prefetch callback can find the row to mark.
        self._scan_row_iid_by_task: dict[str, str] = {}
        # task_id → distinct en-US source-string count. Cached so paging
        # back/forth and re-search don't re-hit the results API — a completed
        # scan's source-string count is immutable. Filled from worker threads,
        # so guard it with a lock. Mirrors MR Pipeline's src-count cache.
        self._scan_src_cache: dict[str, int] = {}
        self._scan_src_lock = threading.Lock()
        self._build(parent)

    def _t(self, key):
        return self.app._t(key)

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------
    def _build(self, parent):
        content = ttk.Frame(parent, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=16, pady=8)

        left = ttk.Frame(content, style="App.TFrame")
        left.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(content, style="App.TFrame", width=260)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        # ── Filter bar ──
        filt = ttk.Frame(left, style="Card.TFrame")
        filt.pack(fill="x", pady=(0, 8))
        filt.configure(borderwidth=1, relief="solid")
        fi = ttk.Frame(filt, style="Card.TFrame")
        fi.pack(fill="x", padx=12, pady=10)

        # Row 1: Project + Status
        r1 = ttk.Frame(fi, style="Card.TFrame")
        r1.pack(fill="x", pady=(0, 6))

        self.lbl_scan_project = ttk.Label(r1, text="", style="Card.TLabel", width=8)
        self.lbl_scan_project.pack(side="left")
        self.scan_project_var = tk.StringVar()
        self.ent_scan_project = tk.Entry(r1, textvariable=self.scan_project_var,
                                         width=24, font=(FONT_FAMILY, 10),
                                         bg="#0a0a1a", fg="#fff",
                                         insertbackground="#fff", relief="flat")
        self.ent_scan_project.pack(side="left", padx=(4, 12), ipady=3)

        self.lbl_scan_status = ttk.Label(r1, text="", style="Card.TLabel", width=8)
        self.lbl_scan_status.pack(side="left")
        self.scan_status_var = tk.StringVar()
        self.cmb_scan_status = ttk.Combobox(
            r1, textvariable=self.scan_status_var, width=12, state="readonly",
            values=["", "pending", "running", "completed", "failed"])
        self.cmb_scan_status.pack(side="left", padx=(4, 12))

        # Row 2: Task ID
        r2 = ttk.Frame(fi, style="Card.TFrame")
        r2.pack(fill="x", pady=(0, 6))
        self.lbl_scan_task_id = ttk.Label(r2, text="", style="Card.TLabel", width=8)
        self.lbl_scan_task_id.pack(side="left")
        self.scan_task_id_var = tk.StringVar()
        self.ent_scan_task_id = tk.Entry(r2, textvariable=self.scan_task_id_var,
                                          width=40, font=(FONT_FAMILY, 10),
                                          bg="#0a0a1a", fg="#fff",
                                          insertbackground="#fff", relief="flat")
        self.ent_scan_task_id.pack(side="left", padx=(4, 0), ipady=3)

        # Row 3: buttons
        r3 = ttk.Frame(fi, style="Card.TFrame")
        r3.pack(fill="x")
        self.btn_scan_search = self.app._create_button(
            r3, text="", command=self._on_search,
            style_name="AccentSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#e94560", fg="#fff", padx=14, pady=3)
        self.btn_scan_search.pack(side="left", padx=(0, 6))
        self.btn_scan_reset = self.app._create_button(
            r3, text="", command=self._on_reset,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=3)
        self.btn_scan_reset.pack(side="left")

        # ── Advanced Filters (collapsible) — content-level filter carried into
        #    the export (HTML pre-fills + auto-applies; Excel/JSON keep only
        #    matching rows). Shared widget with the MR Pipeline tab. ──
        self.adv_filter = None
        if advanced_filter.AdvancedFilterPanel is not None:
            self.adv_filter = advanced_filter.AdvancedFilterPanel(left, self.app)
            self.adv_filter.pack(fill="x", pady=(0, 8))

        # ── Action bar ──
        action = ttk.Frame(left, style="App.TFrame")
        action.pack(fill="x", pady=(6, 6))

        self.btn_scan_export = self.app._create_button(
            action, text="", command=self._on_export,
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#2ecc71", fg="#fff", padx=14, pady=4, state="disabled")
        self.btn_scan_export.pack(side="left")

        # One-click "export full-translation JSON + copy the LQA prompt" for the
        # /rc-core-products-trans-checker workflow. Forces JSON + All
        # Translations (see _on_export) then copies the prompt + pops a how-to
        # dialog. Enable/disable tracks btn_scan_export.
        self.btn_scan_llm_qa = self.app._create_button(
            action, text=llm_qa_module.button_label(self.app.lang),
            command=lambda: self._on_export(llm_qa=True),
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#7c5cff", fg="#fff", padx=14, pady=4, state="disabled")
        self.btn_scan_llm_qa.pack(side="left", padx=(8, 0))

        # Export Type selector — mirrors File Translation / MR Pipeline
        self.lbl_scan_export_type = ttk.Label(action, text="", style="Card.TLabel")
        self.lbl_scan_export_type.pack(side="left", padx=(16, 4))
        self.scan_export_type_var = tk.StringVar(value="translations")
        self.rb_scan_changes = ttk.Radiobutton(
            action, text="", variable=self.scan_export_type_var,
            value="changes", style="Card.TRadiobutton")
        self.rb_scan_changes.pack(side="left", padx=(0, 6))
        self.rb_scan_translations = ttk.Radiobutton(
            action, text="", variable=self.scan_export_type_var,
            value="translations", style="Card.TRadiobutton")
        self.rb_scan_translations.pack(side="left")

        self.lbl_scan_fmt = ttk.Label(action, text="", style="Card.TLabel")
        self.lbl_scan_fmt.pack(side="left", padx=(16, 4))
        self.scan_fmt_var = tk.StringVar(value="html")
        ttk.Radiobutton(action, text="HTML", variable=self.scan_fmt_var,
                         value="html", style="Card.TRadiobutton"
                         ).pack(side="left", padx=(0, 6))
        ttk.Radiobutton(action, text="Excel", variable=self.scan_fmt_var,
                         value="xlsx", style="Card.TRadiobutton"
                         ).pack(side="left", padx=(0, 6))
        # JSON 选项：透视为 {key, en-US, de-DE, ...} 供 LQA Skill 消费
        self.rb_scan_json = ttk.Radiobutton(
            action, text="", variable=self.scan_fmt_var,
            value="json", style="Card.TRadiobutton")
        self.rb_scan_json.pack(side="left")

        self.lbl_scan_status_bar = ttk.Label(action, text="", style="Status.TLabel")
        self.lbl_scan_status_bar.pack(side="left", padx=(16, 0))

        # Pagination on the right
        self.btn_scan_next = self.app._create_button(
            action, text="▶", command=self._next_page,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10), bg="#0f3460", fg="#ccc",
            padx=8, state="disabled")
        self.btn_scan_next.pack(side="right")
        self.lbl_scan_page = ttk.Label(action, text="", style="Status.TLabel")
        self.lbl_scan_page.pack(side="right", padx=4)
        self.btn_scan_prev = self.app._create_button(
            action, text="◀", command=self._prev_page,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10), bg="#0f3460", fg="#ccc",
            padx=8, state="disabled")
        self.btn_scan_prev.pack(side="right")
        self.btn_scan_refresh = self.app._create_button(
            action, text="", command=self._refresh_tasks,
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 9), bg="#0f3460", fg="#ccc",
            padx=10, pady=3)
        self.btn_scan_refresh.pack(side="right", padx=(0, 8))

        # Tiny legend for the ✏️ glyph the async post-edit prefetch may
        # prepend to Task Name. Permanent so first-time viewers know
        # what's coming before the first marker lights up.
        self.lbl_scan_post_edit_legend = ttk.Label(
            left, text="", style="Status.TLabel",
        )
        self.lbl_scan_post_edit_legend.pack(anchor="w", pady=(0, 4))

        # ── Task list table ──
        tree_frame = ttk.Frame(left, style="App.TFrame")
        tree_frame.pack(fill="both", expand=True, pady=(0, 6))

        cols = self._SCAN_COLUMNS
        self.scan_tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                       style="Summary.Treeview",
                                       height=14, selectmode="browse")
        # ``age`` 紧跟 ``created`` 之后是刻意的：raw 时间戳和"距今多久"放
        # 一起读，眼睛不用来回跳 —— 后者本质是前者的人类可读注解，主库
        # ``DB_SEARCH_EXPIRED_DAYS`` 默认 3650 后，列表里掺杂大量陈年 task
        # 是常态，必须给出"几年前的"瞬时信号。
        col_widths = {"idx": 35, "task_name": 150, "project": 130,
                      "base_ref": 120, "head_ref": 120, "status": 80,
                      "src_strings": 90, "output_mode": 100,
                      "created": 185, "age": 55}
        for c in cols:
            anchor = "w" if c in ("task_name", "project", "base_ref", "head_ref") else "center"
            self.scan_tree.column(c, width=col_widths.get(c, 80), anchor=anchor)

        # Warm gold row-tint applied to tasks the post-edit prefetch
        # marks. We pair this with the ✏️ prefix (rather than replacing
        # it) so colour-blind viewers and grey-scale screenshots still
        # carry the signal. Chosen to harmonise with the dark navy app
        # palette — vivid enough to spot mid-page, not loud enough to
        # fight the rest of the row.
        self.scan_tree.tag_configure(
            "post_edit", background="#3a2e1f", foreground="#fde68a",
        )

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.scan_tree.yview)
        self.scan_tree.configure(yscrollcommand=scroll.set)
        self.scan_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.scan_loading_overlay = tk.Label(
            tree_frame, text="",
            # 等待指示：亮金加粗，醒目告知"正在加载"，免得用户以为卡死。
            font=(FONT_FAMILY, 15, "bold"),
            fg="#fbbf24", bg=self.app.BG, anchor="center")

        # ── Right sidebar: stats ──
        self._build_scan_sidebar(right)

    def _build_scan_sidebar(self, parent):
        panel = ttk.Frame(parent, style="Summary.TFrame")
        panel.pack(fill="both", expand=True)
        panel.configure(borderwidth=1, relief="solid")
        inner = ttk.Frame(panel, style="Summary.TFrame")
        inner.pack(fill="both", expand=True, padx=14, pady=14)

        self.lbl_scan_sidebar_title = ttk.Label(inner, text="", style="SummaryTitle.TLabel")
        self.lbl_scan_sidebar_title.pack(anchor="w")
        tk.Frame(inner, bg="#2a2a4a", height=1).pack(fill="x", pady=(8, 10))

        stats = ttk.Frame(inner, style="Summary.TFrame")
        stats.pack(fill="x")
        self.scan_stat_labels = {}
        for key in ("total", "completed", "running", "failed"):
            row = ttk.Frame(stats, style="Summary.TFrame")
            row.pack(fill="x", pady=3)
            lbl = ttk.Label(row, text="", style="Card.TLabel")
            lbl.pack(side="left")
            val = ttk.Label(row, text="—", style="CardBold.TLabel")
            val.pack(side="right")
            self.scan_stat_labels[key] = (lbl, val)

    # ------------------------------------------------------------------
    # i18n
    # ------------------------------------------------------------------
    def refresh_text(self):
        t = self._t
        self.lbl_scan_project.configure(text=t("scan_project"))
        self.lbl_scan_status.configure(text=t("scan_status"))
        self.lbl_scan_task_id.configure(text=t("scan_task_id"))
        self.btn_scan_search.configure(text=t("scan_search"))
        self.btn_scan_reset.configure(text=t("scan_reset"))
        self.btn_scan_export.configure(text=t("scan_export"))
        self.btn_scan_llm_qa.configure(text=llm_qa_module.button_label(self.app.lang))
        self.lbl_scan_export_type.configure(text=t("export_type_label"))
        self.rb_scan_changes.configure(text=t("export_type_changes"))
        self.rb_scan_translations.configure(text=t("export_type_all"))
        self.lbl_scan_fmt.configure(text=t("output_fmt_label"))
        self.rb_scan_json.configure(text=t("output_fmt_json"))
        self.btn_scan_refresh.configure(text=t("summary_refresh"))

        for col in self._SCAN_COLUMNS:
            self.scan_tree.heading(col, text=t(f"scan_col_{col}"))
        self.lbl_scan_post_edit_legend.configure(
            text=t("scan_post_edit_legend"),
        )
        if self.adv_filter is not None:
            self.adv_filter.refresh_text()

        self.lbl_scan_sidebar_title.configure(text=t("scan_sidebar_title"))
        for key in ("total", "completed", "running", "failed"):
            self.scan_stat_labels[key][0].configure(text=t(f"scan_stat_{key}"))

    # ------------------------------------------------------------------
    # Loading lifecycle
    # ------------------------------------------------------------------
    def on_first_show(self):
        """Called when tab is first selected — load initial data."""
        self._load_tasks()

    def _invalidate_post_edit_cache(self):
        """Drop the cached ✏️ (post-edit) answers for the scan kind.

        The badge is served from a process-lifetime cache
        (:class:`task_post_edit.PostEditCache`). A reviewer who edits a
        scan-task translation in the Tranzor dashboard does so *after* we may
        have already cached a ``False`` "no edit" answer for that task.
        Without this invalidation, re-querying reuses the stale ``False`` —
        the render only re-fetches when the cached value is ``None`` (see the
        ``cached is None`` gate in ``_on_tasks_loaded``) — so the badge never
        lights up even though a fresh detail fetch would now detect the edit.

        A transient API failure during a prior fetch also lands here as a
        cached ``False``; clearing on an explicit re-query lets it self-heal.

        Mirrors the MR Pipeline tab
        (``MRPipelineTab._invalidate_post_edit_cache``) and the File
        Translation Refresh (``export_gui._load_summary_data``), which drop
        the ``mr`` / ``legacy`` kinds for the same go-edit-then-come-back
        reason. Scoped to an explicit Refresh / Search / Reset — paging
        (Prev / Next) keeps the cache so flipping pages stays free.
        Best-effort: never block the reload on housekeeping.
        """
        try:
            _tpe.get_cache().clear_kind("scan")
        except Exception:
            pass

    def _refresh_tasks(self):
        # Refresh is the canonical go-edit-then-come-back gesture — drop stale
        # ✏️ answers so a reviewer's just-made fixes surface (see
        # _invalidate_post_edit_cache).
        self._invalidate_post_edit_cache()
        self._load_tasks()

    def _on_search(self):
        # An explicit re-query means "give me fresh data" — drop stale ✏️
        # answers (see _invalidate_post_edit_cache).
        self._invalidate_post_edit_cache()
        self.scan_page = 0
        self._load_tasks()

    def _on_reset(self):
        self.scan_project_var.set("")
        self.scan_status_var.set("")
        self.scan_task_id_var.set("")
        self._invalidate_post_edit_cache()
        self.scan_page = 0
        self._load_tasks()

    def _prev_page(self):
        if self.scan_page > 0:
            self.scan_page -= 1
            self._load_tasks()

    def _next_page(self):
        effective = self.scan_filtered_total or self.scan_total
        if (self.scan_page + 1) * self.scan_page_size < effective:
            self.scan_page += 1
            self._load_tasks()

    def _load_tasks(self):
        if self.scan_loading:
            return
        self.scan_loading = True
        self.scan_loading_overlay.configure(text=self._t("status_loading") + "...")
        self.scan_loading_overlay.place(relx=0.5, rely=0.4, anchor="center")
        self._set_controls_enabled(False)
        self._loading_dot_count = 0
        self._animate_loading()
        threading.Thread(target=self._fetch_tasks, daemon=True).start()

    def _animate_loading(self):
        if not self.scan_loading:
            return
        self._loading_dot_count = (self._loading_dot_count % 3) + 1
        dots = "." * self._loading_dot_count
        base = self._t("status_loading")
        self.lbl_scan_status_bar.configure(text=f"{base}{dots}")
        self.scan_loading_overlay.configure(text=f"{base}{dots}")
        self._loading_anim_id = self.parent.after(500, self._animate_loading)

    def _stop_loading_anim(self):
        if self._loading_anim_id is not None:
            self.parent.after_cancel(self._loading_anim_id)
            self._loading_anim_id = None
        self.scan_loading_overlay.place_forget()

    def _set_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        if IS_MAC:
            flag = ["!disabled"] if enabled else ["disabled"]
            self.btn_scan_export.state(flag)
            self.btn_scan_llm_qa.state(flag)
            self.btn_scan_prev.state(flag)
            self.btn_scan_next.state(flag)
        else:
            self.btn_scan_export.configure(state=state)
            self.btn_scan_llm_qa.configure(state=state)
            self.btn_scan_prev.configure(state=state)
            self.btn_scan_next.configure(state=state)

    # ------------------------------------------------------------------
    # Fetch & render
    # ------------------------------------------------------------------
    def _fetch_tasks(self):
        try:
            proj = self.scan_project_var.get().strip() or None
            status = self.scan_status_var.get() or None
            task_id_filter = self.scan_task_id_var.get().strip()

            # Task ID short-circuit: direct GET /tasks/{task_id}
            if task_id_filter:
                try:
                    detail = mr_api.fetch_scan_task_detail(task_id_filter)
                except Exception:
                    detail = None
                collected = []
                if isinstance(detail, dict) and detail.get("task_id"):
                    if proj and str(detail.get("project_id", "")) != proj:
                        detail = None
                if isinstance(detail, dict) and detail.get("task_id"):
                    if status and str(detail.get("status", "")) != status:
                        detail = None
                if isinstance(detail, dict) and detail.get("task_id"):
                    collected.append(detail)
                matched_total = len(collected)
                self.parent.after(0, self._on_tasks_loaded,
                                  matched_total, collected, matched_total)
                return

            total, tasks = mr_api.fetch_scan_tasks(
                project_id=proj, status=status,
                limit=self.scan_page_size,
                offset=self.scan_page * self.scan_page_size)
            self.parent.after(0, self._on_tasks_loaded, total, tasks, total)

            # Update sidebar stats using separate calls (non-blocking UX:
            # stats reflect current filters)
            self._update_sidebar_stats(proj)
        except Exception as e:
            self.parent.after(0, self._on_tasks_error, str(e))

    def _update_sidebar_stats(self, proj):
        """Fire three lightweight count queries (total + completed + failed)."""
        try:
            total_all, _ = mr_api.fetch_scan_tasks(
                project_id=proj, limit=1, offset=0)
            total_done, _ = mr_api.fetch_scan_tasks(
                project_id=proj, status="completed", limit=1, offset=0)
            total_run, _ = mr_api.fetch_scan_tasks(
                project_id=proj, status="running", limit=1, offset=0)
            total_fail, _ = mr_api.fetch_scan_tasks(
                project_id=proj, status="failed", limit=1, offset=0)
            self.parent.after(0, self._on_sidebar_stats_loaded,
                              total_all, total_done, total_run, total_fail)
        except Exception:
            pass

    def _on_sidebar_stats_loaded(self, total, done, run, fail):
        self.scan_stat_labels["total"][1].configure(text=str(total))
        self.scan_stat_labels["completed"][1].configure(text=str(done))
        self.scan_stat_labels["running"][1].configure(text=str(run))
        self.scan_stat_labels["failed"][1].configure(text=str(fail))

    def _on_tasks_loaded(self, api_total, tasks, filtered_total):
        self.scan_loading = False
        self._stop_loading_anim()
        self.scan_total = api_total
        self.scan_filtered_total = filtered_total

        for item in self.scan_tree.get_children():
            self.scan_tree.delete(item)

        # Map task_id → row iid so the async post-edit prefetch can patch
        # the Task Name cell once the detail fetch returns. We use the
        # raw task_id as the iid (Tranzor task ids are UUIDs — safe as
        # Tk iids) and keep ``tags`` populated so existing selection code
        # (see _on_export) keeps working.
        self._scan_row_iid_by_task = {}
        prefetch_items: list[tuple[str, str]] = []
        src_prefetch_ids: list[str] = []
        for i, t in enumerate(tasks):
            idx = self.scan_page * self.scan_page_size + i + 1
            created_raw = t.get("created_at") or ""
            created = format_display_datetime(created_raw)
            # Format age from the *raw* ISO so timezone info isn't dropped.
            age = format_age_days(created_raw)
            task_id = t.get("task_id") or ""
            raw_name = t.get("task_name", "")
            # If we already know the answer for this task, render the
            # prefix synchronously — no flicker for users paging back.
            cached = _tpe.get_cache().get("scan", task_id) if task_id else None
            display_name = (
                _tpe.POST_EDIT_PREFIX + raw_name if cached else raw_name
            )
            # Same row-tint path as the async callback (_apply_post_edit_prefix)
            # — when paging back to a previously-fetched page, the synchronous
            # render must produce an identical-looking row, not a plain one.
            row_tags = (task_id, "post_edit") if cached else (task_id,)
            # en-US source-string count: render from cache when known, else a
            # "…" placeholder + queue an async fetch (mirrors MR Pipeline).
            with self._scan_src_lock:
                src_count = self._scan_src_cache.get(task_id)
            src_display = src_count if src_count is not None else "…"
            iid = self.scan_tree.insert(
                "", "end",
                iid=task_id or None,
                values=(
                    idx, display_name,
                    t.get("project_id", ""),
                    t.get("base_ref", ""),
                    t.get("head_ref", ""),
                    t.get("status", ""),
                    src_display,
                    t.get("output_mode", ""),
                    created,
                    age,
                ),
                tags=row_tags,
            )
            if task_id:
                self._scan_row_iid_by_task[task_id] = iid
                if cached is None:
                    prefetch_items.append(("scan", task_id))
                if src_count is None:
                    src_prefetch_ids.append(task_id)

        # Fire-and-forget: ✏️ markers appear incrementally as per-task
        # detail fetches return. The legend below the table tells users
        # what the glyph means while they wait.
        if prefetch_items:
            _tpe.prefetch_async(
                prefetch_items,
                on_result=self._on_post_edit_result,
            )

        # Fill the en-US source-string counts asynchronously so the page stays
        # responsive; cells flip from "…" to the number as each fetch returns.
        if src_prefetch_ids:
            self._prefetch_src_counts(src_prefetch_ids)

        effective_total = filtered_total
        total_pages = max(1, (effective_total + self.scan_page_size - 1) // self.scan_page_size)
        self.lbl_scan_page.configure(text=f"{self.scan_page + 1} / {total_pages}  ({effective_total})")
        has_next = (self.scan_page + 1) * self.scan_page_size < effective_total
        if IS_MAC:
            self.btn_scan_prev.state(["!disabled"] if self.scan_page > 0 else ["disabled"])
            self.btn_scan_next.state(["!disabled"] if has_next else ["disabled"])
            self.btn_scan_export.state(["!disabled"] if tasks else ["disabled"])
            self.btn_scan_llm_qa.state(["!disabled"] if tasks else ["disabled"])
        else:
            self.btn_scan_prev.configure(state="normal" if self.scan_page > 0 else "disabled")
            self.btn_scan_next.configure(state="normal" if has_next else "disabled")
            self.btn_scan_export.configure(state="normal" if tasks else "disabled")
            self.btn_scan_llm_qa.configure(state="normal" if tasks else "disabled")
        self.lbl_scan_status_bar.configure(text=self._t("status_ready"))

    # ------------------------------------------------------------------
    # Post-edit prefetch callback — fires on a worker thread; marshal back
    # to Tk before touching the Treeview (Tk widgets are NOT thread-safe).
    # ------------------------------------------------------------------
    def _on_post_edit_result(self, kind, task_id, has_post_edit):
        if not has_post_edit:
            return
        try:
            self.scan_tree.after(
                0, self._apply_post_edit_prefix, str(task_id),
            )
        except Exception:
            # Widget already destroyed (tab closed / app shutting down).
            pass

    def _apply_post_edit_prefix(self, task_id):
        iid = self._scan_row_iid_by_task.get(task_id)
        if not iid:
            return
        try:
            vals = list(self.scan_tree.item(iid, "values"))
            current_tags = list(self.scan_tree.item(iid, "tags") or ())
        except tk.TclError:
            # Row was deleted (user paged or re-searched between fetch
            # firing and callback arriving). Drop silently.
            return
        if len(vals) < 2:
            return
        name = vals[1] or ""
        if name.startswith(_tpe.POST_EDIT_PREFIX):
            return  # already marked
        vals[1] = _tpe.POST_EDIT_PREFIX + name
        # Append the "post_edit" tag so the row gets the warm gold tint
        # configured at build time. Tags is an ordered list; existing
        # tags (e.g. ``task_id`` used as the selection key) must be
        # preserved at their original positions — ``tree.item(iid,
        # tags=...)`` REPLACES the tuple, it doesn't append.
        if "post_edit" not in current_tags:
            current_tags.append("post_edit")
        try:
            self.scan_tree.item(iid, values=vals, tags=tuple(current_tags))
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # en-US source-string counts — filled asynchronously so the page paints
    # immediately (mirrors MRPipelineTab._prefetch_src_counts).
    # ------------------------------------------------------------------
    def _prefetch_src_counts(self, task_ids):
        ids = [tid for tid in task_ids if tid]
        if not ids:
            return

        def _run():
            def _work(tid):
                with self._scan_src_lock:
                    count = self._scan_src_cache.get(tid)
                if count is None:
                    count = mr_api.count_scan_source_strings(tid)
                    with self._scan_src_lock:
                        self._scan_src_cache[tid] = count
                try:
                    self.parent.after(0, self._apply_src_count, tid, count)
                except Exception:
                    pass

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(_work, ids))

        threading.Thread(target=_run, name="scan-src-count-prefetch",
                         daemon=True).start()

    def _apply_src_count(self, task_id, count):
        """Replace one row's "…" placeholder with its real count. Runs on the
        Tk thread. The row may be gone (user paged / re-searched mid-fetch); a
        stale write is harmless because iid == task_id, so guard regardless."""
        iid = self._scan_row_iid_by_task.get(task_id)
        if not iid:
            return
        try:
            self.scan_tree.set(iid, "src_strings", count)
        except tk.TclError:
            pass

    def _on_tasks_error(self, err):
        self.scan_loading = False
        self._stop_loading_anim()
        self._set_controls_enabled(True)
        self.lbl_scan_status_bar.configure(text=f"⚠ {err[:60]}")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _on_export(self, llm_qa=False):
        """Export the selected scan task.

        ``llm_qa=True`` is the "Send to LLM QA" path: it forces JSON + All
        Translations regardless of the radios and, on success, copies the LQA
        prompt to the clipboard and pops a how-to dialog (see _run_export).
        """
        sel = self.scan_tree.selection()
        if not sel:
            self.lbl_scan_status_bar.configure(text="⚠ 请先选择一条任务")
            return
        tags = self.scan_tree.item(sel[0], "tags")
        task_id = tags[0] if tags else None
        if not task_id:
            return
        # Pull task_name AND the scan task's Created time straight from the
        # visible row: the name keeps the filename matching what the user
        # clicked, the Created time stamps *which run* it is (see
        # _build_export_filename). Indices resolve from _SCAN_COLUMNS so a
        # column reshuffle can't silently point these reads at the wrong cell.
        values = self.scan_tree.item(sel[0], "values")
        task_name = ""
        scan_created = ""
        if values:
            name_col = self._SCAN_COLUMNS.index("task_name")
            created_col = self._SCAN_COLUMNS.index("created")
            if len(values) > name_col:
                task_name = str(values[name_col] or "")
                if task_name.startswith(_tpe.POST_EDIT_PREFIX):
                    task_name = task_name[len(_tpe.POST_EDIT_PREFIX):]
            if len(values) > created_col:
                scan_created = str(values[created_col] or "")
        # Send to LLM QA always writes the full-translation JSON audit shape.
        fmt = "json" if llm_qa else self.scan_fmt_var.get()
        export_type = "translations" if llm_qa else self.scan_export_type_var.get()
        # Read Advanced Filters on the main thread (Tk widgets aren't
        # thread-safe) and hand the snapshot to the worker.
        adv_state = self.adv_filter.get_state() if self.adv_filter else None
        if IS_MAC:
            self.btn_scan_export.state(["disabled"])
            self.btn_scan_llm_qa.state(["disabled"])
        else:
            self.btn_scan_export.configure(state="disabled")
            self.btn_scan_llm_qa.configure(state="disabled")
        self.lbl_scan_status_bar.configure(text=self._t("status_exporting"))
        threading.Thread(target=self._run_export,
                         args=(task_id, fmt, export_type, task_name, adv_state,
                               scan_created),
                         kwargs={"llm_qa": llm_qa},
                         daemon=True).start()

    def _run_export(self, task_id, fmt, export_type="changes", task_name="",
                    adv_state=None, scan_created="", llm_qa=False):
        try:
            def _progress(msg):
                # Worker-thread logs; Tk widgets are not thread-safe.
                text = f"{self._t('status_exporting')} {msg}".strip()
                self.parent.after(
                    0, lambda t=text: self.lbl_scan_status_bar.configure(
                        text=t[:80]))

            if export_type == "changes":
                changes = mr_api.detect_scan_changes(
                    task_id, progress_callback=_progress)
                results = {"translations": changes, "summary": {},
                           "task_id": task_id}
                type_tag = "changes"
            else:
                results = mr_api.fetch_scan_results(
                    task_id, progress_callback=_progress)
                type_tag = "all"

            id_tag = task_id[:8]
            ext = {"xlsx": ".xlsx", "json": ".json"}.get(fmt, ".html")
            today = date.today().isoformat()
            # Stamp the scan task's Created/trigger time into the filename so
            # re-runs of the same task neither collide nor look identical —
            # the export date alone is the same for every same-day export.
            # Falls back to today's date when no Created time is available.
            filename = self._build_export_filename(
                ext, task_name=task_name, id_tag=id_tag, type_tag=type_tag,
                created=scan_created, export_date=today)
            script_dir = export_output_dir()
            filepath = os.path.join(script_dir, filename)
            created_note = f"created {scan_created}, " if scan_created else ""
            label = (f"Scan Task {id_tag} — {type_tag} "
                     f"({created_note}exported {today})")
            # Stamp scan_task_id so write_mr_html → buildEnvelope →
            # sendToTranzor can route to /static/scans/<id> instead of
            # falling through to the File Translation legacy task URL.
            mr_api.enrich_translations_with_scan_task(
                results.get("translations") or [],
                task_id,
                task_name=label,
            )
            # Route the local bridge port + token into the report so its
            # Send-to-Tranzor button reaches the desktop GUI's HTTP bridge
            # instead of falling back to clipboard (which makes the
            # userscript sidebar sit on "Waiting for selections…").
            bridge_info = self.app._bridge_info_for_export() if hasattr(self.app, "_bridge_info_for_export") else None
            # Capture actual save_path so the status bar shows the real filename
            # (PermissionError renames it to ..._1.json on collision) and we
            # reveal that exact file in the OS file manager below.
            # 全量翻译 JSON 导出（非 changes）需要每个 key 100% 覆盖目标语言，
            # 启用 fill_missing 做缺失语言补齐；Changes 导出保持稀疏。
            # adv_state was read on the main thread in _on_export.
            saved = mr_api.save_mr_file(
                results, filepath, label, fmt, bridge_info=bridge_info,
                fill_missing=(export_type != "changes"),
                advanced_filter_state=adv_state) or filepath
            basename = os.path.basename(saved)
            self.parent.after(0, lambda b=basename: self.lbl_scan_status_bar.configure(
                text=self._t("status_saved").format(filename=b)))
            # Non-HTML formats don't auto-open in a browser, so the user has no
            # visual cue where the file landed — pop the file manager.
            if fmt != "html":
                self.parent.after(0, lambda p=saved: reveal_in_folder(p))
            # Send to LLM QA: JSON is out — now copy the LQA prompt to the
            # clipboard and tell the user to upload + paste in their LLM. Must
            # run on the main thread (Tk clipboard + dialog), hence after(0).
            if llm_qa:
                self.parent.after(0, lambda b=basename:
                    llm_qa_module.send_prompt_and_notify(self.parent, b, self.app.lang))
        except Exception as e:
            msg = str(e)[:50]
            self.parent.after(0, lambda: self.lbl_scan_status_bar.configure(
                text=f"❌ {msg}"))
        finally:
            def _restore():
                if IS_MAC:
                    self.btn_scan_export.state(["!disabled"])
                    self.btn_scan_llm_qa.state(["!disabled"])
                else:
                    self.btn_scan_export.configure(state="normal")
                    self.btn_scan_llm_qa.configure(state="normal")
            self.parent.after(0, _restore)
