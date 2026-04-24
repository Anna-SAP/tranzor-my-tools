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
from tkinter import ttk
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_mr_pipeline as mr_api
from export_gui import FONT_FAMILY, IS_MAC


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
        "scan_col_output_mode":  "Output Mode",
        "scan_col_created":      "Created",
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
        "scan_col_output_mode":  "输出模式",
        "scan_col_created":      "创建时间",
    },
}


class ScanTasksTab:
    """Builds and manages the Scan Tasks tab content."""

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

        # ── Action bar ──
        action = ttk.Frame(left, style="App.TFrame")
        action.pack(fill="x", pady=(6, 6))

        self.btn_scan_export = self.app._create_button(
            action, text="", command=self._on_export,
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#2ecc71", fg="#fff", padx=14, pady=4, state="disabled")
        self.btn_scan_export.pack(side="left")

        # Export Type selector — mirrors File Translation / MR Pipeline
        self.lbl_scan_export_type = ttk.Label(action, text="", style="Card.TLabel")
        self.lbl_scan_export_type.pack(side="left", padx=(16, 4))
        self.scan_export_type_var = tk.StringVar(value="changes")
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
                         ).pack(side="left")

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

        # ── Task list table ──
        tree_frame = ttk.Frame(left, style="App.TFrame")
        tree_frame.pack(fill="both", expand=True, pady=(0, 6))

        cols = ("idx", "task_name", "project", "base_ref", "head_ref",
                "status", "output_mode", "created")
        self.scan_tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                       style="Summary.Treeview",
                                       height=14, selectmode="browse")
        col_widths = {"idx": 35, "task_name": 150, "project": 130,
                      "base_ref": 120, "head_ref": 120, "status": 80,
                      "output_mode": 100, "created": 140}
        for c in cols:
            anchor = "w" if c in ("task_name", "project", "base_ref", "head_ref") else "center"
            self.scan_tree.column(c, width=col_widths.get(c, 80), anchor=anchor)

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.scan_tree.yview)
        self.scan_tree.configure(yscrollcommand=scroll.set)
        self.scan_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.scan_loading_overlay = tk.Label(
            tree_frame, text="",
            font=(FONT_FAMILY, 15),
            fg="#9aa0b0", bg=self.app.BG, anchor="center")

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
        self.lbl_scan_export_type.configure(text=t("export_type_label"))
        self.rb_scan_changes.configure(text=t("export_type_changes"))
        self.rb_scan_translations.configure(text=t("export_type_all"))
        self.lbl_scan_fmt.configure(text=t("output_fmt_label"))
        self.btn_scan_refresh.configure(text=t("summary_refresh"))

        for col in ("idx", "task_name", "project", "base_ref", "head_ref",
                    "status", "output_mode", "created"):
            self.scan_tree.heading(col, text=t(f"scan_col_{col}"))

        self.lbl_scan_sidebar_title.configure(text=t("scan_sidebar_title"))
        for key in ("total", "completed", "running", "failed"):
            self.scan_stat_labels[key][0].configure(text=t(f"scan_stat_{key}"))

    # ------------------------------------------------------------------
    # Loading lifecycle
    # ------------------------------------------------------------------
    def on_first_show(self):
        """Called when tab is first selected — load initial data."""
        self._load_tasks()

    def _refresh_tasks(self):
        self._load_tasks()

    def _on_search(self):
        self.scan_page = 0
        self._load_tasks()

    def _on_reset(self):
        self.scan_project_var.set("")
        self.scan_status_var.set("")
        self.scan_task_id_var.set("")
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
            self.btn_scan_prev.state(flag)
            self.btn_scan_next.state(flag)
        else:
            self.btn_scan_export.configure(state=state)
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

        for i, t in enumerate(tasks):
            idx = self.scan_page * self.scan_page_size + i + 1
            created = (t.get("created_at") or "")[:19].replace("T", " ")
            self.scan_tree.insert("", "end", values=(
                idx,
                t.get("task_name", ""),
                t.get("project_id", ""),
                t.get("base_ref", ""),
                t.get("head_ref", ""),
                t.get("status", ""),
                t.get("output_mode", ""),
                created,
            ), tags=(t.get("task_id", ""),))

        effective_total = filtered_total
        total_pages = max(1, (effective_total + self.scan_page_size - 1) // self.scan_page_size)
        self.lbl_scan_page.configure(text=f"{self.scan_page + 1} / {total_pages}  ({effective_total})")
        has_next = (self.scan_page + 1) * self.scan_page_size < effective_total
        if IS_MAC:
            self.btn_scan_prev.state(["!disabled"] if self.scan_page > 0 else ["disabled"])
            self.btn_scan_next.state(["!disabled"] if has_next else ["disabled"])
            self.btn_scan_export.state(["!disabled"] if tasks else ["disabled"])
        else:
            self.btn_scan_prev.configure(state="normal" if self.scan_page > 0 else "disabled")
            self.btn_scan_next.configure(state="normal" if has_next else "disabled")
            self.btn_scan_export.configure(state="normal" if tasks else "disabled")
        self.lbl_scan_status_bar.configure(text=self._t("status_ready"))

    def _on_tasks_error(self, err):
        self.scan_loading = False
        self._stop_loading_anim()
        self._set_controls_enabled(True)
        self.lbl_scan_status_bar.configure(text=f"⚠ {err[:60]}")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _on_export(self):
        sel = self.scan_tree.selection()
        if not sel:
            self.lbl_scan_status_bar.configure(text="⚠ 请先选择一条任务")
            return
        tags = self.scan_tree.item(sel[0], "tags")
        task_id = tags[0] if tags else None
        if not task_id:
            return
        fmt = self.scan_fmt_var.get()
        export_type = self.scan_export_type_var.get()
        if IS_MAC:
            self.btn_scan_export.state(["disabled"])
        else:
            self.btn_scan_export.configure(state="disabled")
        self.lbl_scan_status_bar.configure(text=self._t("status_exporting"))
        threading.Thread(target=self._run_export,
                         args=(task_id, fmt, export_type), daemon=True).start()

    def _run_export(self, task_id, fmt, export_type="changes"):
        try:
            if export_type == "changes":
                changes = mr_api.detect_scan_changes(task_id)
                results = {"translations": changes, "summary": {},
                           "task_id": task_id}
                type_tag = "changes"
            else:
                results = mr_api.fetch_scan_results(task_id)
                type_tag = "all"

            id_tag = task_id[:8]
            ext = ".xlsx" if fmt == "xlsx" else ".html"
            today = date.today().isoformat()
            filename = f"scan_task_{id_tag}_{type_tag}_{today}{ext}"
            script_dir = os.path.dirname(os.path.abspath(__file__))
            filepath = os.path.join(script_dir, filename)
            label = f"Scan Task {id_tag} — {type_tag} (exported {today})"
            mr_api.save_mr_file(results, filepath, label, fmt)
            self.parent.after(0, lambda: self.lbl_scan_status_bar.configure(
                text=self._t("status_done")))
        except Exception as e:
            msg = str(e)[:50]
            self.parent.after(0, lambda: self.lbl_scan_status_bar.configure(
                text=f"❌ {msg}"))
        finally:
            def _restore():
                if IS_MAC:
                    self.btn_scan_export.state(["!disabled"])
                else:
                    self.btn_scan_export.configure(state="normal")
            self.parent.after(0, _restore)
