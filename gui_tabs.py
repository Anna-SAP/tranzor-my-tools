"""
GUI Tab Builders — MR Pipeline + Quality Overview tabs for export_gui.py
"""
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_mr_pipeline as mr_api
import quality_overview as qa
from export_gui import FONT_FAMILY, IS_MAC


# ============================================================
# MR Pipeline Tab
# ============================================================
class MRPipelineTab:
    """Builds and manages the MR Pipeline tab content."""

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self.mr_page = 0
        self.mr_page_size = 20
        self.mr_total = 0
        self.mr_loading = False
        self.mr_overview_loading = False
        self._build(parent)

    def _t(self, key):
        return self.app._t(key)

    def _build(self, parent):
        content = ttk.Frame(parent, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=16, pady=8)

        left = ttk.Frame(content, style="App.TFrame")
        left.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(content, style="App.TFrame", width=280)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        # ── Filter bar ──
        filt = ttk.Frame(left, style="Card.TFrame")
        filt.pack(fill="x", pady=(0, 8))
        filt.configure(borderwidth=1, relief="solid")
        fi = ttk.Frame(filt, style="Card.TFrame")
        fi.pack(fill="x", padx=12, pady=10)

        # Row 1: Project + Release + Status
        r1 = ttk.Frame(fi, style="Card.TFrame")
        r1.pack(fill="x", pady=(0, 6))

        self.lbl_mr_project = ttk.Label(r1, text="", style="Card.TLabel", width=8)
        self.lbl_mr_project.pack(side="left")
        self.mr_project_var = tk.StringVar()
        self.cmb_mr_project = ttk.Combobox(r1, textvariable=self.mr_project_var, width=20, state="readonly")
        self.cmb_mr_project.pack(side="left", padx=(4, 12))

        self.lbl_mr_release = ttk.Label(r1, text="", style="Card.TLabel", width=8)
        self.lbl_mr_release.pack(side="left")
        self.mr_release_var = tk.StringVar()
        self.cmb_mr_release = ttk.Combobox(r1, textvariable=self.mr_release_var, width=12, state="readonly")
        self.cmb_mr_release.pack(side="left", padx=(4, 12))

        self.lbl_mr_status = ttk.Label(r1, text="", style="Card.TLabel", width=8)
        self.lbl_mr_status.pack(side="left")
        self.mr_status_var = tk.StringVar()
        self.cmb_mr_status = ttk.Combobox(r1, textvariable=self.mr_status_var, width=12, state="readonly",
                                           values=["", "pending", "running", "completed", "failed", "skipped"])
        self.cmb_mr_status.pack(side="left", padx=(4, 0))

        # Row 2: Date range + buttons
        r2 = ttk.Frame(fi, style="Card.TFrame")
        r2.pack(fill="x")

        self.lbl_mr_date = ttk.Label(r2, text="", style="Card.TLabel", width=8)
        self.lbl_mr_date.pack(side="left")
        self.mr_date_from = tk.Entry(r2, width=12, font=(FONT_FAMILY, 10),
                                      bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat")
        self.mr_date_from.pack(side="left", padx=(4, 4), ipady=3)
        ttk.Label(r2, text="—", style="Card.TLabel").pack(side="left")
        self.mr_date_to = tk.Entry(r2, width=12, font=(FONT_FAMILY, 10),
                                    bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat")
        self.mr_date_to.pack(side="left", padx=(4, 12), ipady=3)

        self.btn_mr_search = self.app._create_button(
            r2, text="", command=self._on_search,
            style_name="AccentSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#e94560", fg="#fff", padx=14, pady=3)
        self.btn_mr_search.pack(side="left", padx=(0, 6))
        self.btn_mr_reset = self.app._create_button(
            r2, text="", command=self._on_reset,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=3)
        self.btn_mr_reset.pack(side="left")

        self.mr_hide_empty_var = tk.BooleanVar(value=True)
        self.chk_mr_hide_empty = ttk.Checkbutton(
            r2, text="Hide empty MRs", variable=self.mr_hide_empty_var,
            style="Card.TCheckbutton", command=self._on_search)
        self.chk_mr_hide_empty.pack(side="left", padx=(16, 0))

        # ── Action bar (Export + Pagination) — above table for visibility on macOS ──
        action = ttk.Frame(left, style="App.TFrame")
        action.pack(fill="x", pady=(6, 6))

        self.btn_mr_export = self.app._create_button(
            action, text="", command=self._on_export,
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#2ecc71", fg="#fff", padx=14, pady=4, state="disabled")
        self.btn_mr_export.pack(side="left")

        self.lbl_mr_fmt = ttk.Label(action, text="", style="Card.TLabel")
        self.lbl_mr_fmt.pack(side="left", padx=(16, 4))
        self.mr_fmt_var = tk.StringVar(value="html")
        ttk.Radiobutton(action, text="HTML", variable=self.mr_fmt_var, value="html",
                         style="Card.TRadiobutton").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(action, text="Excel", variable=self.mr_fmt_var, value="xlsx",
                         style="Card.TRadiobutton").pack(side="left")

        self.lbl_mr_status_bar = ttk.Label(action, text="", style="Status.TLabel")
        self.lbl_mr_status_bar.pack(side="left", padx=(16, 0))

        # Pagination (right-aligned in action bar)
        self.btn_mr_next = self.app._create_button(
            action, text="▶", command=self._next_page,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10), bg="#0f3460", fg="#ccc",
            padx=8, state="disabled")
        self.btn_mr_next.pack(side="right")
        self.lbl_mr_page = ttk.Label(action, text="", style="Status.TLabel")
        self.lbl_mr_page.pack(side="right", padx=4)
        self.btn_mr_prev = self.app._create_button(
            action, text="◀", command=self._prev_page,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10), bg="#0f3460", fg="#ccc",
            padx=8, state="disabled")
        self.btn_mr_prev.pack(side="right")
        self.btn_mr_refresh = self.app._create_button(
            action, text="", command=self._refresh_tasks,
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 9), bg="#0f3460", fg="#ccc",
            padx=10, pady=3)
        self.btn_mr_refresh.pack(side="right", padx=(0, 8))

        # ── Task list table ──
        tree_frame = ttk.Frame(left, style="App.TFrame")
        tree_frame.pack(fill="both", expand=True, pady=(0, 6))

        cols = ("idx", "project", "mr", "release", "status", "avg_score", "created", "duration")
        self.mr_tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                     style="Summary.Treeview", height=14, selectmode="browse")
        col_widths = {"idx": 35, "project": 140, "mr": 60, "release": 60,
                      "status": 80, "avg_score": 70, "created": 130, "duration": 70}
        for c in cols:
            self.mr_tree.column(c, width=col_widths.get(c, 80), anchor="center" if c != "project" else "w")

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.mr_tree.yview)
        self.mr_tree.configure(yscrollcommand=scroll.set)
        self.mr_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # ── Right sidebar: overview stats ──
        self._build_mr_sidebar(right)

    def _build_mr_sidebar(self, parent):
        panel = ttk.Frame(parent, style="Summary.TFrame")
        panel.pack(fill="both", expand=True)
        panel.configure(borderwidth=1, relief="solid")
        inner = ttk.Frame(panel, style="Summary.TFrame")
        inner.pack(fill="both", expand=True, padx=14, pady=14)

        self.lbl_mr_sidebar_title = ttk.Label(inner, text="", style="SummaryTitle.TLabel")
        self.lbl_mr_sidebar_title.pack(anchor="w")
        tk.Frame(inner, bg="#2a2a4a", height=1).pack(fill="x", pady=(8, 10))

        # Stats
        stats = ttk.Frame(inner, style="Summary.TFrame")
        stats.pack(fill="x")

        self.mr_stat_labels = {}
        for key in ("total", "completed", "failed", "avg_score"):
            row = ttk.Frame(stats, style="Summary.TFrame")
            row.pack(fill="x", pady=3)
            lbl = ttk.Label(row, text="", style="Card.TLabel")
            lbl.pack(side="left")
            val = ttk.Label(row, text="—", style="CardBold.TLabel")
            val.pack(side="right")
            self.mr_stat_labels[key] = (lbl, val)

        self.lbl_mr_sidebar_status = ttk.Label(inner, text="", style="SummaryStatus.TLabel")
        self.lbl_mr_sidebar_status.pack(anchor="w", pady=(8, 0))

        self.btn_mr_sidebar_refresh = self.app._create_button(
            inner, text="", command=self._load_overview,
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 9), bg="#0f3460", fg="#ccc",
            padx=10, pady=3)
        self.btn_mr_sidebar_refresh.pack(anchor="e", pady=(8, 0))

    def refresh_text(self):
        """Update all text for current language."""
        t = self._t
        self.lbl_mr_project.configure(text=t("mr_project"))
        self.lbl_mr_release.configure(text=t("mr_release"))
        self.lbl_mr_status.configure(text=t("mr_status"))
        self.lbl_mr_date.configure(text=t("mr_date_range"))
        self.btn_mr_search.configure(text=t("mr_search"))
        self.btn_mr_reset.configure(text=t("mr_reset"))
        self.btn_mr_export.configure(text=t("mr_export"))
        self.lbl_mr_fmt.configure(text=t("output_fmt_label"))
        self.btn_mr_refresh.configure(text=t("summary_refresh"))

        for i, col in enumerate(("idx", "project", "mr", "release", "status", "avg_score", "created", "duration")):
            self.mr_tree.heading(col, text=t(f"mr_col_{col}"))

        self.lbl_mr_sidebar_title.configure(text=t("mr_sidebar_title"))
        for key in ("total", "completed", "failed", "avg_score"):
            self.mr_stat_labels[key][0].configure(text=t(f"mr_stat_{key}"))
        self.btn_mr_sidebar_refresh.configure(text=t("summary_refresh"))

    def load_initial_tasks(self):
        """Load the latest 20 tasks (no filters) on first tab selection."""
        self._load_tasks()

    def _refresh_tasks(self):
        """Refresh the current task list."""
        self._load_tasks()

    def load_filters(self):
        threading.Thread(target=self._fetch_filters, daemon=True).start()

    def _fetch_filters(self):
        try:
            data = mr_api.fetch_mr_filters()
            self.parent.after(0, self._on_filters_loaded, data)
        except Exception:
            pass

    def _on_filters_loaded(self, data):
        pids = [""] + data.get("project_ids", [])
        rels = [""] + data.get("releases", [])
        self.cmb_mr_project.configure(values=pids)
        self.cmb_mr_release.configure(values=rels)

    def _on_search(self):
        self.mr_page = 0
        self._load_tasks()

    def _on_reset(self):
        self.mr_project_var.set("")
        self.mr_release_var.set("")
        self.mr_status_var.set("")
        self.mr_date_from.delete(0, "end")
        self.mr_date_to.delete(0, "end")
        self.mr_page = 0
        self._load_tasks()

    def _prev_page(self):
        if self.mr_page > 0:
            self.mr_page -= 1
            self._load_tasks()

    def _next_page(self):
        if (self.mr_page + 1) * self.mr_page_size < self.mr_total:
            self.mr_page += 1
            self._load_tasks()

    def _load_tasks(self):
        if self.mr_loading:
            return
        self.mr_loading = True
        self.lbl_mr_status_bar.configure(text=self._t("status_exporting"))
        threading.Thread(target=self._fetch_tasks, daemon=True).start()

    def _fetch_tasks(self):
        try:
            proj = self.mr_project_var.get() or None
            rel = self.mr_release_var.get() or None
            status = self.mr_status_var.get() or None
            total, tasks = mr_api.fetch_mr_tasks(
                project_id=proj, release=rel, status=status,
                limit=self.mr_page_size, offset=self.mr_page * self.mr_page_size)

            # When "Hide empty MRs" is checked, fetch results for each task
            # to determine translation count (task list API lacks this data)
            hide_empty = self.mr_hide_empty_var.get()
            if hide_empty:
                for t in tasks:
                    tid = t.get("task_id")
                    if not tid:
                        t["_translations_count"] = 0
                        continue
                    try:
                        results = mr_api.fetch_mr_results(tid)
                        trs = results.get("translations", [])
                        t["_translations_count"] = len(trs)
                        if trs and t.get("average_score") is None:
                            scores = [tr.get("score") for tr in trs if tr.get("score") is not None]
                            if scores:
                                t["average_score"] = round(sum(scores) / len(scores), 2)
                    except Exception:
                        t["_translations_count"] = 0

            self.parent.after(0, self._on_tasks_loaded, total, tasks)
        except Exception as e:
            self.parent.after(0, self._on_tasks_error, str(e))

    def _on_tasks_loaded(self, total, tasks):
        self.mr_loading = False
        self.mr_total = total

        for item in self.mr_tree.get_children():
            self.mr_tree.delete(item)

        hide_empty = self.mr_hide_empty_var.get()
        for i, t in enumerate(tasks):
            # Skip tasks with 0 translations when Hide empty MRs is checked
            if hide_empty and t.get("_translations_count", -1) == 0:
                continue
            idx = self.mr_page * self.mr_page_size + i + 1
            created = (t.get("created_at") or "")[:19].replace("T", " ")
            updated = t.get("updated_at") or ""
            duration = ""
            try:
                if created and updated:
                    c = datetime.fromisoformat(t["created_at"][:19])
                    u = datetime.fromisoformat(updated[:19])
                    secs = int((u - c).total_seconds())
                    if secs < 60:
                        duration = f"{secs}s"
                    else:
                        duration = f"{secs // 60}m{secs % 60}s"
            except Exception:
                pass

            avg = t.get("average_score")
            self.mr_tree.insert("", "end", values=(
                idx, t.get("project_id", ""), t.get("merge_request_iid", ""),
                t.get("release", ""), t.get("status", ""),
                avg if avg is not None else "—", created, duration
            ), tags=(t.get("task_id", ""),))


        # Pagination
        total_pages = max(1, (total + self.mr_page_size - 1) // self.mr_page_size)
        self.lbl_mr_page.configure(text=f"{self.mr_page + 1} / {total_pages}  ({total})")
        if IS_MAC:
            self.btn_mr_prev.state(["!disabled"] if self.mr_page > 0 else ["disabled"])
            self.btn_mr_next.state(["!disabled"] if (self.mr_page + 1) * self.mr_page_size < total else ["disabled"])
            self.btn_mr_export.state(["!disabled"] if tasks else ["disabled"])
        else:
            self.btn_mr_prev.configure(state="normal" if self.mr_page > 0 else "disabled")
            self.btn_mr_next.configure(state="normal" if (self.mr_page + 1) * self.mr_page_size < total else "disabled")
            self.btn_mr_export.configure(state="normal" if tasks else "disabled")
        self.lbl_mr_status_bar.configure(text=self._t("status_ready"))

    def _on_tasks_error(self, err):
        self.mr_loading = False
        self.lbl_mr_status_bar.configure(text=f"⚠ {err[:60]}")

    def _on_export(self):
        sel = self.mr_tree.selection()
        if not sel:
            return
        tags = self.mr_tree.item(sel[0], "tags")
        if not tags:
            return
        task_id = tags[0]
        fmt = self.mr_fmt_var.get()
        if IS_MAC:
            self.btn_mr_export.state(["disabled"])
        else:
            self.btn_mr_export.configure(state="disabled")
        self.lbl_mr_status_bar.configure(text=self._t("status_exporting"))
        threading.Thread(target=self._run_export, args=(task_id, fmt), daemon=True).start()

    def _run_export(self, task_id, fmt):
        try:
            results = mr_api.fetch_mr_results(task_id)
            ext = ".xlsx" if fmt == "xlsx" else ".html"
            today = date.today().isoformat()
            filename = f"mr_pipeline_{task_id[:8]}_{today}{ext}"
            script_dir = os.path.dirname(os.path.abspath(__file__))
            filepath = os.path.join(script_dir, filename)
            label = f"MR Pipeline Task {task_id[:8]} (exported {today})"
            mr_api.save_mr_file(results, filepath, label, fmt)
            self.parent.after(0, lambda: self.lbl_mr_status_bar.configure(text=self._t("status_done")))
        except Exception as e:
            self.parent.after(0, lambda: self.lbl_mr_status_bar.configure(text=f"❌ {str(e)[:50]}"))
        finally:
            def _restore():
                if IS_MAC:
                    self.btn_mr_export.state(["!disabled"])
                else:
                    self.btn_mr_export.configure(state="normal")
            self.parent.after(0, _restore)

    def _load_overview(self):
        if self.mr_overview_loading:
            return
        self.mr_overview_loading = True
        self.lbl_mr_sidebar_status.configure(text=self._t("summary_loading"))
        threading.Thread(target=self._fetch_overview, daemon=True).start()

    def _fetch_overview(self):
        try:
            proj = self.mr_project_var.get() or None
            rel = self.mr_release_var.get() or None
            data = mr_api.fetch_dashboard_overview(project_id=proj, release=rel)
            self.parent.after(0, self._on_overview_loaded, data)
        except Exception as e:
            self.parent.after(0, self._on_overview_error, str(e))

    def _on_overview_loaded(self, data):
        self.mr_overview_loading = False
        self.lbl_mr_sidebar_status.configure(text="")
        self.mr_stat_labels["total"][1].configure(text=str(data.get("total_tasks", 0)))
        self.mr_stat_labels["completed"][1].configure(text=str(data.get("completed", 0)))
        self.mr_stat_labels["failed"][1].configure(text=str(data.get("failed", 0)))
        avg = data.get("average_score")
        self.mr_stat_labels["avg_score"][1].configure(text=f"{avg}" if avg else "—")

    def _on_overview_error(self, err):
        self.mr_overview_loading = False
        self.lbl_mr_sidebar_status.configure(text=self._t("summary_error"))


# ============================================================
# Quality Overview Tab
# ============================================================
class QualityOverviewTab:
    """Builds and manages the Quality Overview tab content."""

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self.qa_loading = False
        self.aggregated = None
        self._build(parent)

    def _t(self, key):
        return self.app._t(key)

    def _build(self, parent):
        # Use a canvas with scrollbar for the whole tab
        outer = ttk.Frame(parent, style="App.TFrame")
        outer.pack(fill="both", expand=True)

        self._qa_canvas = tk.Canvas(outer, bg="#1a1a2e", highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=self._qa_canvas.yview)
        self.scroll_frame = ttk.Frame(self._qa_canvas, style="App.TFrame")
        self.scroll_frame.bind("<Configure>", lambda e: self._qa_canvas.configure(scrollregion=self._qa_canvas.bbox("all")))
        self._qa_canvas_win = self._qa_canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        self._qa_canvas.configure(yscrollcommand=scrollbar.set)
        self._qa_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        # Sync scroll_frame width to canvas width so content fills horizontally
        def _on_canvas_resize(e):
            self._qa_canvas.itemconfig(self._qa_canvas_win, width=e.width)
        self._qa_canvas.bind("<Configure>", _on_canvas_resize)
        # Mouse wheel
        self._qa_canvas.bind_all("<MouseWheel>", lambda e: self._qa_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        content = self.scroll_frame

        # ── Filter bar ──
        filt = ttk.Frame(content, style="Card.TFrame")
        filt.pack(fill="x", padx=16, pady=(8, 8))
        filt.configure(borderwidth=1, relief="solid")
        fi = ttk.Frame(filt, style="Card.TFrame")
        fi.pack(fill="x", padx=12, pady=10)

        r1 = ttk.Frame(fi, style="Card.TFrame")
        r1.pack(fill="x")

        self.lbl_qa_project = ttk.Label(r1, text="", style="Card.TLabel", width=8)
        self.lbl_qa_project.pack(side="left")
        self.qa_project_var = tk.StringVar()
        self.cmb_qa_project = ttk.Combobox(r1, textvariable=self.qa_project_var, width=20, state="readonly")
        self.cmb_qa_project.pack(side="left", padx=(4, 12))

        self.lbl_qa_release = ttk.Label(r1, text="", style="Card.TLabel", width=8)
        self.lbl_qa_release.pack(side="left")
        self.qa_release_var = tk.StringVar()
        self.cmb_qa_release = ttk.Combobox(r1, textvariable=self.qa_release_var, width=12, state="readonly")
        self.cmb_qa_release.pack(side="left", padx=(4, 12))

        self.lbl_qa_lang = ttk.Label(r1, text="", style="Card.TLabel", width=8)
        self.lbl_qa_lang.pack(side="left")
        self.qa_lang_var = tk.StringVar()
        self.cmb_qa_lang = ttk.Combobox(r1, textvariable=self.qa_lang_var, width=12)
        self.cmb_qa_lang.pack(side="left", padx=(4, 12))

        self.btn_qa_search = self.app._create_button(
            r1, text="", command=self._on_search,
            style_name="AccentSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#e94560", fg="#fff", padx=14, pady=3)
        self.btn_qa_search.pack(side="left", padx=(12, 6))
        self.btn_qa_reset = self.app._create_button(
            r1, text="", command=self._on_reset,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=3)
        self.btn_qa_reset.pack(side="left")

        # ── Summary cards ──
        cards = ttk.Frame(content, style="App.TFrame")
        cards.pack(fill="x", padx=16, pady=(0, 8))

        self.qa_cards = {}
        card_defs = [
            ("total_tasks", "📋", "#4472C4"),
            ("total_translations", "📝", "#16A085"),
            ("avg_score", "⭐", "#27AE60"),
            ("low_score", "⚠", "#E74C3C"),
        ]
        for key, icon, color in card_defs:
            cf = ttk.Frame(cards, style="Card.TFrame", width=200)
            cf.pack(side="left", fill="x", expand=True, padx=4)
            cf.pack_propagate(False)
            cf.configure(borderwidth=1, relief="solid", height=100)
            val_lbl = ttk.Label(cf, text="—", style="SummaryCount.TLabel")
            val_lbl.pack(pady=(10, 2))
            name_lbl = ttk.Label(cf, text="", style="SummaryCountLabel.TLabel")
            name_lbl.pack(pady=(0, 8))
            self.qa_cards[key] = (val_lbl, name_lbl)

        # ── Charts: bar + pie side by side ──
        chart_frame = ttk.Frame(content, style="App.TFrame")
        chart_frame.pack(fill="x", padx=16, pady=(0, 8))

        # Bar chart (score distribution)
        bar_outer = ttk.Frame(chart_frame, style="Card.TFrame")
        bar_outer.pack(side="left", fill="both", expand=True, padx=(0, 4))
        bar_outer.configure(borderwidth=1, relief="solid")
        self.lbl_bar_title = ttk.Label(bar_outer, text="", style="SummaryTitle.TLabel")
        self.lbl_bar_title.pack(anchor="w", padx=12, pady=(8, 0))
        self.bar_canvas = tk.Canvas(bar_outer, bg="#16213e", highlightthickness=0, height=200)
        self.bar_canvas.pack(fill="x", padx=8, pady=8)

        # Pie chart (error distribution)
        pie_outer = ttk.Frame(chart_frame, style="Card.TFrame")
        pie_outer.pack(side="left", fill="both", expand=True, padx=(4, 0))
        pie_outer.configure(borderwidth=1, relief="solid")
        self.lbl_pie_title = ttk.Label(pie_outer, text="", style="SummaryTitle.TLabel")
        self.lbl_pie_title.pack(anchor="w", padx=12, pady=(8, 0))
        self.pie_canvas = tk.Canvas(pie_outer, bg="#16213e", highlightthickness=0, height=200)
        self.pie_canvas.pack(fill="x", padx=8, pady=8)

        # ── Language detail table ──
        self.lbl_lang_title = ttk.Label(content, text="", style="Subtitle.TLabel")
        self.lbl_lang_title.pack(anchor="w", padx=16, pady=(0, 4))

        lang_cols = ("language", "count", "avg_score", "critical", "major", "minor")
        self.lang_tree = ttk.Treeview(content, columns=lang_cols, show="headings",
                                       style="Summary.Treeview", height=6)
        for c in lang_cols:
            self.lang_tree.column(c, width=100, anchor="center" if c != "language" else "w")
        self.lang_tree.pack(fill="x", padx=16, pady=(0, 8))

        # ── Low-score items ──
        self.lbl_low_title = ttk.Label(content, text="", style="Subtitle.TLabel")
        self.lbl_low_title.pack(anchor="w", padx=16, pady=(0, 4))

        low_cols = ("idx", "opus_id", "language", "source", "translated", "score", "error_cat", "reason")
        self.low_tree = ttk.Treeview(content, columns=low_cols, show="headings",
                                      style="Summary.Treeview", height=8)
        low_widths = {"idx": 35, "opus_id": 180, "language": 70, "source": 200,
                      "translated": 200, "score": 60, "error_cat": 120, "reason": 180}
        for c in low_cols:
            self.low_tree.column(c, width=low_widths.get(c, 100),
                                 anchor="center" if c in ("idx", "score", "language") else "w")
        self.low_tree.pack(fill="x", padx=16, pady=(0, 8))

        # ── Export bar ──
        ebar = ttk.Frame(content, style="App.TFrame")
        ebar.pack(fill="x", padx=16, pady=(4, 24))

        self.lbl_qa_fmt = ttk.Label(ebar, text="", style="Card.TLabel")
        self.lbl_qa_fmt.pack(side="left")
        self.qa_fmt_var = tk.StringVar(value="html")
        ttk.Radiobutton(ebar, text="HTML", variable=self.qa_fmt_var, value="html",
                         style="Card.TRadiobutton").pack(side="left", padx=(4, 6))
        ttk.Radiobutton(ebar, text="Excel", variable=self.qa_fmt_var, value="xlsx",
                         style="Card.TRadiobutton").pack(side="left")

        self.btn_qa_export = self.app._create_button(
            ebar, text="", command=self._on_export,
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#2ecc71", fg="#fff", padx=14, pady=4, state="disabled")
        self.btn_qa_export.pack(side="right")
        self.lbl_qa_status = ttk.Label(ebar, text="", style="Status.TLabel")
        self.lbl_qa_status.pack(side="right", padx=8)

    def refresh_text(self):
        t = self._t
        self.lbl_qa_project.configure(text=t("mr_project"))
        self.lbl_qa_release.configure(text=t("mr_release"))
        self.lbl_qa_lang.configure(text=t("qa_language"))
        self.btn_qa_search.configure(text=t("mr_search"))
        self.btn_qa_reset.configure(text=t("mr_reset"))
        self.btn_qa_export.configure(text=t("qa_export"))
        self.lbl_qa_fmt.configure(text=t("output_fmt_label"))

        self.qa_cards["total_tasks"][1].configure(text=t("qa_total_tasks"))
        self.qa_cards["total_translations"][1].configure(text=t("qa_total_items"))
        self.qa_cards["avg_score"][1].configure(text=t("qa_avg_score"))
        self.qa_cards["low_score"][1].configure(text=t("qa_low_score"))

        self.lbl_bar_title.configure(text=t("qa_score_dist"))
        self.lbl_pie_title.configure(text=t("qa_error_dist"))
        self.lbl_lang_title.configure(text=t("qa_lang_detail"))
        self.lbl_low_title.configure(text=t("qa_low_items"))

        for c in ("language", "count", "avg_score", "critical", "major", "minor"):
            self.lang_tree.heading(c, text=t(f"qa_lang_col_{c}"))
        for c in ("idx", "opus_id", "language", "source", "translated", "score", "error_cat", "reason"):
            self.low_tree.heading(c, text=t(f"qa_low_col_{c}"))

    def load_filters(self):
        threading.Thread(target=self._fetch_filters, daemon=True).start()

    def _fetch_filters(self):
        try:
            data = mr_api.fetch_mr_filters()
            langs = mr_api.fetch_languages()
            self.parent.after(0, self._on_filters_loaded, data, langs)
        except Exception:
            pass

    def _on_filters_loaded(self, data, langs):
        pids = [""] + data.get("project_ids", [])
        rels = [""] + data.get("releases", [])
        self.cmb_qa_project.configure(values=pids)
        self.cmb_qa_release.configure(values=rels)
        if langs:
            self.cmb_qa_lang.configure(values=[""] + langs)

    def _on_search(self):
        self._load_data()

    def _on_reset(self):
        self.qa_project_var.set("")
        self.qa_release_var.set("")
        self.qa_lang_var.set("")
        self._load_data()

    def _load_data(self):
        if self.qa_loading:
            return
        self.qa_loading = True
        self.lbl_qa_status.configure(text=self._t("status_exporting"))
        threading.Thread(target=self._fetch_data, daemon=True).start()

    def _fetch_data(self):
        try:
            proj = self.qa_project_var.get() or None
            rel = self.qa_release_var.get() or None
            lang = self.qa_lang_var.get() or None
            overview = mr_api.fetch_dashboard_overview(project_id=proj, release=rel)
            cases = mr_api.fetch_dashboard_cases(project_id=proj, release=rel, language=lang)
            agg = qa.aggregate_quality_data(overview, cases)
            self.parent.after(0, self._on_data_loaded, agg)
        except Exception as e:
            self.parent.after(0, self._on_data_error, str(e))

    def _on_data_loaded(self, agg):
        self.qa_loading = False
        self.aggregated = agg
        self.lbl_qa_status.configure(text=self._t("status_ready"))
        if IS_MAC:
            self.btn_qa_export.state(["!disabled"])
        else:
            self.btn_qa_export.configure(state="normal")

        # Update cards
        self.qa_cards["total_tasks"][0].configure(text=str(agg["total_tasks"]))
        self.qa_cards["total_translations"][0].configure(text=str(agg["total_translations"]))
        self.qa_cards["avg_score"][0].configure(text=str(agg["overall_avg_score"]))
        self.qa_cards["low_score"][0].configure(text=str(agg["low_score_count"]))

        # Populate Language combobox from aggregated data
        langs = sorted(ld["language"] for ld in agg.get("by_language", []) if ld.get("language"))
        current = self.qa_lang_var.get()
        self.cmb_qa_lang.configure(values=[""] + langs)
        if current and current in langs:
            self.qa_lang_var.set(current)

        # Draw charts
        self.bar_canvas.update_idletasks()
        w = max(self.bar_canvas.winfo_width(), 300)
        qa.draw_bar_chart(self.bar_canvas, agg["score_distribution"], w, 200,
                          title=self._t("qa_score_dist"))
        qa.draw_pie_chart(self.pie_canvas, agg["error_distribution"], w, 200,
                          title=self._t("qa_error_dist"))

        # Language table
        for item in self.lang_tree.get_children():
            self.lang_tree.delete(item)
        for ld in agg["by_language"]:
            avg = f'{ld["average_score"]}' if ld["average_score"] else "—"
            self.lang_tree.insert("", "end", values=(
                ld["language"], ld["count"], avg,
                ld["critical"], ld["major"], ld["minor"]))

        # Low-score table
        for item in self.low_tree.get_children():
            self.low_tree.delete(item)
        for i, it in enumerate(agg["low_items"][:200]):
            score = it.get("final_score", "—")
            self.low_tree.insert("", "end", values=(
                i + 1, it.get("opus_id", ""), it.get("target_language", ""),
                it.get("source_text", "")[:80], it.get("translated_text", "")[:80],
                score, it.get("error_category") or "—", (it.get("reason") or "")[:60]))

    def _on_data_error(self, err):
        self.qa_loading = False
        self.lbl_qa_status.configure(text=f"⚠ {err[:60]}")

    def _on_export(self):
        if not self.aggregated:
            return
        fmt = self.qa_fmt_var.get()
        if IS_MAC:
            self.btn_qa_export.state(["disabled"])
        else:
            self.btn_qa_export.configure(state="disabled")
        self.lbl_qa_status.configure(text=self._t("status_exporting"))
        threading.Thread(target=self._run_export, args=(fmt,), daemon=True).start()

    def _run_export(self, fmt):
        try:
            ext = ".xlsx" if fmt == "xlsx" else ".html"
            today = date.today().isoformat()
            filename = f"quality_overview_{today}{ext}"
            script_dir = os.path.dirname(os.path.abspath(__file__))
            filepath = os.path.join(script_dir, filename)
            label = f"Quality Overview (exported {today})"
            qa.save_quality_file(self.aggregated, filepath, label, fmt)
            self.parent.after(0, lambda: self.lbl_qa_status.configure(text=self._t("status_done")))
        except Exception as e:
            self.parent.after(0, lambda: self.lbl_qa_status.configure(text=f"❌ {str(e)[:50]}"))
        finally:
            def _restore():
                if IS_MAC:
                    self.btn_qa_export.state(["!disabled"])
                else:
                    self.btn_qa_export.configure(state="normal")
            self.parent.after(0, _restore)
