"""
GUI Tab Builders — MR Pipeline + Quality Overview tabs for export_gui.py
"""
import os
import sys
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        self.mr_filtered_total = 0
        self.mr_loading = False
        self.mr_overview_loading = False
        self._recent_projects_loading = False
        self._loading_anim_id = None
        self._loading_dot_count = 0
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
        self.cmb_mr_status.pack(side="left", padx=(4, 12))

        ttk.Label(r1, text="MR#", style="Card.TLabel").pack(side="left")
        self.mr_iid_var = tk.StringVar()
        self.ent_mr_iid = tk.Entry(r1, textvariable=self.mr_iid_var, width=8, font=(FONT_FAMILY, 10),
                                    bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat")
        self.ent_mr_iid.pack(side="left", padx=(4, 0), ipady=3)

        # Row 1b: Task ID (UUID from Tranzor Bot notifications)
        r1b = ttk.Frame(fi, style="Card.TFrame")
        r1b.pack(fill="x", pady=(0, 6))
        self.lbl_mr_task_id = ttk.Label(r1b, text="", style="Card.TLabel", width=8)
        self.lbl_mr_task_id.pack(side="left")
        self.mr_task_id_var = tk.StringVar()
        self.ent_mr_task_id = tk.Entry(r1b, textvariable=self.mr_task_id_var, width=40,
                                        font=(FONT_FAMILY, 10),
                                        bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat")
        self.ent_mr_task_id.pack(side="left", padx=(4, 0), ipady=3)

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

        # Export Type selector (mirrors File Translation panel)
        self.lbl_mr_export_type = ttk.Label(action, text="", style="Card.TLabel")
        self.lbl_mr_export_type.pack(side="left", padx=(16, 4))
        self.mr_export_type_var = tk.StringVar(value="changes")
        self.rb_mr_changes = ttk.Radiobutton(
            action, text="", variable=self.mr_export_type_var, value="changes",
            style="Card.TRadiobutton")
        self.rb_mr_changes.pack(side="left", padx=(0, 6))
        self.rb_mr_translations = ttk.Radiobutton(
            action, text="", variable=self.mr_export_type_var, value="translations",
            style="Card.TRadiobutton")
        self.rb_mr_translations.pack(side="left")

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

        # Loading overlay — large centered text over the Treeview area
        self.mr_loading_overlay = tk.Label(
            tree_frame,
            text="",
            font=(FONT_FAMILY, 15),
            fg="#9aa0b0",
            bg=self.app.BG,
            anchor="center",
        )

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

        # ── Recently Added Projects section ──
        # Separator + section title live near the top of the remaining area.
        tk.Frame(inner, bg="#2a2a4a", height=1).pack(fill="x", pady=(14, 10))
        self.lbl_mr_recent_projects_title = ttk.Label(
            inner, text="", style="SummarySection.TLabel")
        self.lbl_mr_recent_projects_title.pack(anchor="w", pady=(0, 6))

        # Pack status + refresh button at the BOTTOM first so the recent
        # projects frame can take every pixel between section title and
        # these anchors via fill="both", expand=True.
        self.btn_mr_sidebar_refresh = self.app._create_button(
            inner, text="", command=self._load_overview,
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 9), bg="#0f3460", fg="#ccc",
            padx=10, pady=3)
        self.btn_mr_sidebar_refresh.pack(side="bottom", anchor="e", pady=(8, 0))

        self.lbl_mr_sidebar_status = ttk.Label(
            inner, text="", style="SummaryStatus.TLabel")
        self.lbl_mr_sidebar_status.pack(side="bottom", anchor="w", pady=(8, 0))

        # Recent projects Treeview — expands to fill all remaining sidebar
        # height so as many rows as possible are visible without scrolling.
        recent_frame = ttk.Frame(inner, style="Summary.TFrame")
        recent_frame.pack(fill="both", expand=True)
        self.mr_recent_tree = ttk.Treeview(
            recent_frame,
            columns=("project", "added"),
            show="headings",
            style="Summary.Treeview",
            height=3,  # initial request only — fill/expand will override
            selectmode="browse",
        )
        self.mr_recent_tree.heading("project", text="")
        self.mr_recent_tree.heading("added", text="")
        self.mr_recent_tree.column(
            "project", width=160, minwidth=90, stretch=True)
        self.mr_recent_tree.column(
            "added", width=78, minwidth=60, stretch=False, anchor="e")
        recent_scroll = ttk.Scrollbar(
            recent_frame, orient="vertical",
            command=self.mr_recent_tree.yview)
        self.mr_recent_tree.configure(yscrollcommand=recent_scroll.set)
        self.mr_recent_tree.pack(side="left", fill="both", expand=True)
        recent_scroll.pack(side="right", fill="y")
        self._last_recent_projects = []

    def refresh_text(self):
        """Update all text for current language."""
        t = self._t
        self.lbl_mr_project.configure(text=t("mr_project"))
        self.lbl_mr_release.configure(text=t("mr_release"))
        self.lbl_mr_status.configure(text=t("mr_status"))
        self.lbl_mr_date.configure(text=t("mr_date_range"))
        self.lbl_mr_task_id.configure(text=t("mr_task_id"))
        self.btn_mr_search.configure(text=t("mr_search"))
        self.btn_mr_reset.configure(text=t("mr_reset"))
        self.btn_mr_export.configure(text=t("mr_export"))
        self.lbl_mr_export_type.configure(text=t("export_type_label"))
        self.rb_mr_changes.configure(text=t("export_type_changes"))
        self.rb_mr_translations.configure(text=t("export_type_all"))
        self.lbl_mr_fmt.configure(text=t("output_fmt_label"))
        self.btn_mr_refresh.configure(text=t("summary_refresh"))

        for i, col in enumerate(("idx", "project", "mr", "release", "status", "avg_score", "created", "duration")):
            self.mr_tree.heading(col, text=t(f"mr_col_{col}"))

        self.lbl_mr_sidebar_title.configure(text=t("mr_sidebar_title"))
        for key in ("total", "completed", "failed", "avg_score"):
            self.mr_stat_labels[key][0].configure(text=t(f"mr_stat_{key}"))
        self.btn_mr_sidebar_refresh.configure(text=t("summary_refresh"))
        self.lbl_mr_recent_projects_title.configure(
            text=t("mr_recent_projects_title"))
        self.mr_recent_tree.heading("project", text=t("mr_recent_col_project"))
        self.mr_recent_tree.heading("added", text=t("mr_recent_col_added"))
        # Re-render relative timestamps / placeholders in the new language
        if self._recent_projects_loading:
            self._show_recent_projects_loading()
        else:
            self._render_recent_projects(self._last_recent_projects)

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
        self.mr_iid_var.set("")
        self.mr_task_id_var.set("")
        self.mr_date_from.delete(0, "end")
        self.mr_date_to.delete(0, "end")
        self.mr_page = 0
        self._load_tasks()

    def _prev_page(self):
        if self.mr_page > 0:
            self.mr_page -= 1
            self._load_tasks()

    def _next_page(self):
        filters_active = (
            self.mr_hide_empty_var.get()
            or self.mr_iid_var.get().strip()
            or self.mr_task_id_var.get().strip()
        )
        effective_total = self.mr_filtered_total if filters_active else self.mr_total
        if (self.mr_page + 1) * self.mr_page_size < effective_total:
            self.mr_page += 1
            self._load_tasks()

    def _load_tasks(self):
        if self.mr_loading:
            return
        self.mr_loading = True
        # Show prominent loading overlay in the data grid area
        self.mr_loading_overlay.configure(text=self._t("status_loading") + "...")
        self.mr_loading_overlay.place(relx=0.5, rely=0.4, anchor="center")
        # Disable interactive controls while loading
        self._set_controls_enabled(False)
        # Start animated dots in status bar
        self._loading_dot_count = 0
        self._animate_loading()
        threading.Thread(target=self._fetch_tasks, daemon=True).start()

    def _animate_loading(self):
        """Cycle dots in both status bar and overlay: Loading. → Loading.. → Loading..."""
        if not self.mr_loading:
            return
        self._loading_dot_count = (self._loading_dot_count % 3) + 1
        dots = "." * self._loading_dot_count
        base = self._t("status_loading")
        self.lbl_mr_status_bar.configure(text=f"{base}{dots}")
        self.mr_loading_overlay.configure(text=f"{base}{dots}")
        self._loading_anim_id = self.parent.after(500, self._animate_loading)

    def _stop_loading_anim(self):
        if self._loading_anim_id is not None:
            self.parent.after_cancel(self._loading_anim_id)
            self._loading_anim_id = None
        self.mr_loading_overlay.place_forget()

    def _set_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        if IS_MAC:
            flag = ["!disabled"] if enabled else ["disabled"]
            self.btn_mr_export.state(flag)
            self.btn_mr_prev.state(flag)
            self.btn_mr_next.state(flag)
        else:
            self.btn_mr_export.configure(state=state)
            self.btn_mr_prev.configure(state=state)
            self.btn_mr_next.configure(state=state)

    def _check_task_translations(self, t):
        """Check a task's translation count via API; attach _translations_count and average_score."""
        tid = t.get("task_id")
        if not tid:
            t["_translations_count"] = 0
            return
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

    def _fetch_tasks(self):
        try:
            proj = self.mr_project_var.get() or None
            rel = self.mr_release_var.get() or None
            status = self.mr_status_var.get() or None
            mr_iid_filter = self.mr_iid_var.get().strip()
            task_id_filter = self.mr_task_id_var.get().strip()
            hide_empty = self.mr_hide_empty_var.get()

            # Task ID short-circuit: if user pastes a UUID, look it up
            # directly via /tasks/{task_id} and intersect with the other
            # filters so results stay consistent with Project/Release/Status/MR#.
            if task_id_filter:
                try:
                    detail = mr_api.fetch_mr_task_detail(task_id_filter)
                except Exception:
                    detail = None
                collected = []
                if isinstance(detail, dict) and detail.get("task_id"):
                    if proj and str(detail.get("project_id", "")) != proj:
                        detail = None
                if isinstance(detail, dict) and detail.get("task_id"):
                    if rel and str(detail.get("release", "")) != rel:
                        detail = None
                if isinstance(detail, dict) and detail.get("task_id"):
                    if status and str(detail.get("status", "")) != status:
                        detail = None
                if isinstance(detail, dict) and detail.get("task_id"):
                    if mr_iid_filter and str(detail.get("merge_request_iid", "")) != mr_iid_filter:
                        detail = None
                if isinstance(detail, dict) and detail.get("task_id"):
                    if hide_empty:
                        self._check_task_translations(detail)
                        if detail.get("_translations_count", 0) == 0:
                            detail = None
                if isinstance(detail, dict) and detail.get("task_id"):
                    collected.append(detail)
                matched_total = len(collected)
                # Single result fits on page 0 — return directly.
                self.parent.after(0, self._on_tasks_loaded,
                                  matched_total, collected, matched_total)
                return

            need_filter = hide_empty or bool(mr_iid_filter)

            if not need_filter:
                # Simple path: no client-side filtering needed
                total, tasks = mr_api.fetch_mr_tasks(
                    project_id=proj, release=rel, status=status,
                    limit=self.mr_page_size, offset=self.mr_page * self.mr_page_size)
                self.parent.after(0, self._on_tasks_loaded, total, tasks, total)
            else:
                from concurrent.futures import ThreadPoolExecutor, as_completed

                # Accumulate non-empty / MR#-matched tasks across multiple API batches
                batch_size = 100
                target = self.mr_page_size
                skip_count = self.mr_page * self.mr_page_size  # items to skip for pagination
                collected = []
                offset = 0
                api_total = 0
                total_matched = 0
                total_scanned = 0

                while True:
                    api_total, batch = mr_api.fetch_mr_tasks(
                        project_id=proj, release=rel, status=status,
                        limit=batch_size, offset=offset)
                    if not batch:
                        break

                    # MR# client-side filter first (cheap, no API call)
                    if mr_iid_filter:
                        batch = [t for t in batch
                                 if str(t.get("merge_request_iid", "")) == mr_iid_filter]

                    # Parallel check translation counts (4x faster than sequential)
                    if hide_empty and batch:
                        with ThreadPoolExecutor(max_workers=4) as pool:
                            list(pool.map(self._check_task_translations, batch))

                    for t in batch:
                        total_scanned += 1

                        # Hide empty MRs: use pre-fetched count from parallel check
                        if hide_empty:
                            if t.get("_translations_count", 0) == 0:
                                continue

                        total_matched += 1

                        # Pagination: skip items for previous pages
                        if skip_count > 0:
                            skip_count -= 1
                            continue

                        if len(collected) < target:
                            collected.append(t)

                    offset += batch_size

                    # Stop as soon as we have enough items for this page
                    if len(collected) >= target:
                        break
                    if offset >= api_total:
                        break

                # Estimate total matches from scanned portion
                if total_scanned > 0 and total_scanned < api_total:
                    estimated_total = int(total_matched * api_total / total_scanned)
                else:
                    estimated_total = total_matched

                self.parent.after(0, self._on_tasks_loaded, api_total, collected, estimated_total)
        except Exception as e:
            self.parent.after(0, self._on_tasks_error, str(e))

    def _on_tasks_loaded(self, api_total, tasks, filtered_total):
        self.mr_loading = False
        self._stop_loading_anim()
        self.mr_total = api_total
        self.mr_filtered_total = filtered_total

        for item in self.mr_tree.get_children():
            self.mr_tree.delete(item)

        for i, t in enumerate(tasks):
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

        # Pagination — use filtered_total when filters are active
        effective_total = filtered_total
        total_pages = max(1, (effective_total + self.mr_page_size - 1) // self.mr_page_size)
        self.lbl_mr_page.configure(text=f"{self.mr_page + 1} / {total_pages}  ({effective_total})")
        has_next = (self.mr_page + 1) * self.mr_page_size < effective_total
        if IS_MAC:
            self.btn_mr_prev.state(["!disabled"] if self.mr_page > 0 else ["disabled"])
            self.btn_mr_next.state(["!disabled"] if has_next else ["disabled"])
            self.btn_mr_export.state(["!disabled"] if tasks else ["disabled"])
        else:
            self.btn_mr_prev.configure(state="normal" if self.mr_page > 0 else "disabled")
            self.btn_mr_next.configure(state="normal" if has_next else "disabled")
            self.btn_mr_export.configure(state="normal" if tasks else "disabled")
        self.lbl_mr_status_bar.configure(text=self._t("status_ready"))

    def _on_tasks_error(self, err):
        self.mr_loading = False
        self._stop_loading_anim()
        self._set_controls_enabled(True)
        self.lbl_mr_status_bar.configure(text=f"⚠ {err[:60]}")

    def _on_export(self):
        sel = self.mr_tree.selection()
        if sel:
            tags = self.mr_tree.item(sel[0], "tags")
            task_id = tags[0] if tags else None
        else:
            task_id = None  # Export all tasks
        fmt = self.mr_fmt_var.get()
        export_type = self.mr_export_type_var.get()
        if IS_MAC:
            self.btn_mr_export.state(["disabled"])
        else:
            self.btn_mr_export.configure(state="disabled")
        self.lbl_mr_status_bar.configure(text=self._t("status_exporting"))
        threading.Thread(target=self._run_export, args=(task_id, fmt, export_type), daemon=True).start()

    def _run_export(self, task_id, fmt, export_type="changes"):
        try:
            if export_type == "changes":
                if not task_id:
                    raise ValueError("请先选择一个翻译任务以导出变更")
                # 自动关联 MR，汇总该 MR 全部 task 的翻译变更
                changes = mr_api.detect_mr_changes(task_id)
                results = {"translations": changes, "summary": {}}
                id_tag = task_id[:8]
                type_tag = "changes"
            else:
                if task_id:
                    results = mr_api.fetch_mr_results(task_id)
                    id_tag = task_id[:8]
                else:
                    results = mr_api.collect_all_mr_results()
                    id_tag = "all_tasks"
                type_tag = "all"

            ext = ".xlsx" if fmt == "xlsx" else ".html"
            today = date.today().isoformat()
            filename = f"mr_pipeline_{id_tag}_{type_tag}_{today}{ext}"
            script_dir = os.path.dirname(os.path.abspath(__file__))
            filepath = os.path.join(script_dir, filename)
            label = f"MR Pipeline {id_tag} — {type_tag} (exported {today})"
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
        if not self.mr_overview_loading:
            self.mr_overview_loading = True
            self.lbl_mr_sidebar_status.configure(text=self._t("summary_loading"))
            threading.Thread(target=self._fetch_overview, daemon=True).start()
        # Recent projects loads independently so stats surface instantly.
        self._load_recent_projects()

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

    def _load_recent_projects(self):
        """Background fetch of the full project → first-seen map.
        Independent from the overview stats call so UI is not blocked."""
        if self._recent_projects_loading:
            return
        self._recent_projects_loading = True
        self._show_recent_projects_loading()
        threading.Thread(target=self._fetch_recent_projects, daemon=True).start()

    def _fetch_recent_projects(self):
        try:
            recent = mr_api.fetch_recently_added_projects()
        except Exception:
            recent = []
        self.parent.after(0, self._on_recent_projects_loaded, recent)

    def _on_recent_projects_loaded(self, recent):
        self._recent_projects_loading = False
        self._render_recent_projects(recent)

    def _show_recent_projects_loading(self):
        tree = self.mr_recent_tree
        for item in tree.get_children():
            tree.delete(item)
        tree.insert("", "end", values=(self._t("summary_loading"), ""))

    def _render_recent_projects(self, recent):
        """Repaint the Recently Added Projects treeview. Caches data for
        language re-render."""
        self._last_recent_projects = list(recent or [])
        tree = self.mr_recent_tree
        for item in tree.get_children():
            tree.delete(item)
        if not self._last_recent_projects:
            tree.insert("", "end",
                        values=(self._t("mr_recent_empty"), ""))
            return
        for r in self._last_recent_projects:
            pid = r.get("project_id", "") or ""
            ts = r.get("first_seen", "") or ""
            tree.insert("", "end", values=(pid, self._relative_time(ts)))

    def _relative_time(self, iso_ts):
        """Format an ISO-ish timestamp as i18n-aware relative time."""
        if not iso_ts:
            return ""
        try:
            dt = datetime.fromisoformat(iso_ts[:19])
        except Exception:
            return ""
        delta_s = max(0, int((datetime.now() - dt).total_seconds()))
        if delta_s < 60:
            return self._t("time_ago_now")
        if delta_s < 3600:
            return self._t("time_ago_minutes").format(n=delta_s // 60)
        if delta_s < 86400:
            return self._t("time_ago_hours").format(n=delta_s // 3600)
        if delta_s < 86400 * 60:
            return self._t("time_ago_days").format(n=delta_s // 86400)
        return self._t("time_ago_months").format(n=delta_s // (86400 * 30))


# ============================================================
# Quality Overview Tab
# ============================================================
class QualityOverviewTab:
    """Builds and manages the Quality Overview tab with MR / File sub-tabs."""

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self.qa_loading = False
        self.aggregated = None          # currently active sub-tab aggregated data
        self._mr_aggregated = None
        self._file_aggregated = None
        self._active_tab = "mr"         # "mr" or "file"
        self._threshold = qa.DEFAULT_THRESHOLD
        self._legacy_tasks_cache = []   # cached legacy task list
        self._build(parent)

    def _t(self, key):
        return self.app._t(key)

    def _quality_trend_title(self):
        return f"{self._t('qa_trend')} (By Date)"

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------
    def _build(self, parent):
        outer = ttk.Frame(parent, style="App.TFrame")
        outer.pack(fill="both", expand=True)

        self._qa_canvas = tk.Canvas(outer, bg="#1a1a2e", highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=self._qa_canvas.yview)
        self.scroll_frame = ttk.Frame(self._qa_canvas, style="App.TFrame")
        self.scroll_frame.bind("<Configure>",
            lambda e: self._qa_canvas.configure(scrollregion=self._qa_canvas.bbox("all")))
        self._qa_canvas_win = self._qa_canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        self._qa_canvas.configure(yscrollcommand=scrollbar.set)
        self._qa_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_canvas_resize(e):
            self._qa_canvas.itemconfig(self._qa_canvas_win, width=e.width)
        self._qa_canvas.bind("<Configure>", _on_canvas_resize)
        self._qa_canvas.bind_all("<MouseWheel>",
            lambda e: self._qa_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        content = self.scroll_frame

        # ── Sub-tab selector: MR / File ──
        tab_bar = ttk.Frame(content, style="App.TFrame")
        tab_bar.pack(fill="x", padx=16, pady=(8, 0))

        self.btn_mr_tab = self.app._create_button(
            tab_bar, text="MR Translation", command=lambda: self._switch_tab("mr"),
            style_name="TabActive", font=(FONT_FAMILY, 10, "bold"),
            bg="#e94560", fg="#fff", padx=18, pady=4)
        self.btn_mr_tab.pack(side="left", padx=(0, 4))

        self.btn_file_tab = self.app._create_button(
            tab_bar, text="File Translation", command=lambda: self._switch_tab("file"),
            style_name="TabInactive", font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=18, pady=4)
        self.btn_file_tab.pack(side="left")

        # ── Filter bar ──
        filt = ttk.Frame(content, style="Card.TFrame")
        filt.pack(fill="x", padx=16, pady=(8, 8))
        filt.configure(borderwidth=1, relief="solid")
        fi = ttk.Frame(filt, style="Card.TFrame")
        fi.pack(fill="x", padx=12, pady=10)

        # Row 1: Project, Release/Task, Language
        r1 = ttk.Frame(fi, style="Card.TFrame")
        r1.pack(fill="x")

        self.lbl_qa_project = ttk.Label(r1, text="Project", style="Card.TLabel", width=8)
        self.lbl_qa_project.pack(side="left")
        self.qa_project_var = tk.StringVar()
        self.cmb_qa_project = ttk.Combobox(r1, textvariable=self.qa_project_var, width=20, state="readonly")
        self.cmb_qa_project.pack(side="left", padx=(4, 12))

        # Release (MR) / Task (File) — shared slot
        self.lbl_qa_release = ttk.Label(r1, text="Release", style="Card.TLabel", width=8)
        self.lbl_qa_release.pack(side="left")
        self.qa_release_var = tk.StringVar()
        self.cmb_qa_release = ttk.Combobox(r1, textvariable=self.qa_release_var, width=14, state="readonly")
        self.cmb_qa_release.pack(side="left", padx=(4, 12))

        self.lbl_qa_lang = ttk.Label(r1, text="Language", style="Card.TLabel", width=8)
        self.lbl_qa_lang.pack(side="left")
        self.qa_lang_var = tk.StringVar()
        self.cmb_qa_lang = ttk.Combobox(r1, textvariable=self.qa_lang_var, width=12)
        self.cmb_qa_lang.pack(side="left", padx=(4, 12))

        self.btn_qa_search = self.app._create_button(
            r1, text="Search", command=self._on_search,
            style_name="AccentSmall", font=(FONT_FAMILY, 10, "bold"),
            bg="#e94560", fg="#fff", padx=14, pady=3)
        self.btn_qa_search.pack(side="left", padx=(12, 6))
        self.btn_qa_reset = self.app._create_button(
            r1, text="Reset", command=self._on_reset,
            style_name="SecondarySmall", font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=3)
        self.btn_qa_reset.pack(side="left")

        # Row 2: Threshold spinner
        r2 = ttk.Frame(fi, style="Card.TFrame")
        r2.pack(fill="x", pady=(6, 0))

        self.lbl_qa_threshold = ttk.Label(r2, text="Threshold", style="Card.TLabel", width=8)
        self.lbl_qa_threshold.pack(side="left")
        self.threshold_var = tk.IntVar(value=self._threshold)
        self.spn_threshold = tk.Spinbox(
            r2, from_=50, to=100, textvariable=self.threshold_var,
            width=5, font=(FONT_FAMILY, 10), bg="#16213e", fg="#ccc",
            buttonbackground="#0f3460", insertbackground="#ccc")
        self.spn_threshold.pack(side="left", padx=(4, 12))

        # ── Summary cards (6) ──
        cards = ttk.Frame(content, style="App.TFrame")
        cards.pack(fill="x", padx=16, pady=(0, 8))

        self.qa_cards = {}
        card_defs = [
            ("total_tasks",),
            ("total_items",),
            ("avg_score",),
            ("below_rate",),
            ("refined_rate",),
            ("human_rate",),
        ]
        for (key,) in card_defs:
            cf = ttk.Frame(cards, style="Card.TFrame", width=160)
            cf.pack(side="left", fill="x", expand=True, padx=4)
            cf.pack_propagate(False)
            cf.configure(borderwidth=1, relief="solid", height=90)
            val_lbl = ttk.Label(cf, text="—", style="SummaryCount.TLabel")
            val_lbl.pack(pady=(8, 2))
            name_lbl = ttk.Label(cf, text="", style="SummaryCountLabel.TLabel")
            name_lbl.pack(pady=(0, 6))
            self.qa_cards[key] = (val_lbl, name_lbl)

        # ── Charts Row 1: Score Distribution + Error Category ──
        chart_frame1 = ttk.Frame(content, style="App.TFrame")
        chart_frame1.pack(fill="x", padx=16, pady=(0, 8))

        bar_outer = ttk.Frame(chart_frame1, style="Card.TFrame")
        bar_outer.pack(side="left", fill="both", expand=True, padx=(0, 4))
        bar_outer.configure(borderwidth=1, relief="solid")
        self.lbl_bar_title = ttk.Label(bar_outer, text="Score Distribution", style="SummaryTitle.TLabel")
        self.lbl_bar_title.pack(anchor="w", padx=12, pady=(8, 0))
        self.bar_canvas = tk.Canvas(bar_outer, bg="#16213e", highlightthickness=0, height=200)
        self.bar_canvas.pack(fill="x", padx=8, pady=8)

        pie_outer = ttk.Frame(chart_frame1, style="Card.TFrame")
        pie_outer.pack(side="left", fill="both", expand=True, padx=(4, 0))
        pie_outer.configure(borderwidth=1, relief="solid")
        self.lbl_pie_title = ttk.Label(pie_outer, text="Error Category Distribution", style="SummaryTitle.TLabel")
        self.lbl_pie_title.pack(anchor="w", padx=12, pady=(8, 0))
        self.pie_canvas = tk.Canvas(pie_outer, bg="#16213e", highlightthickness=0, height=200)
        self.pie_canvas.pack(fill="x", padx=8, pady=8)

        # ── Charts Row 2: Trend + Errors by Language ──
        chart_frame2 = ttk.Frame(content, style="App.TFrame")
        chart_frame2.pack(fill="x", padx=16, pady=(0, 8))

        trend_outer = ttk.Frame(chart_frame2, style="Card.TFrame")
        trend_outer.pack(side="left", fill="both", expand=True, padx=(0, 4))
        trend_outer.configure(borderwidth=1, relief="solid")
        self.lbl_trend_title = ttk.Label(
            trend_outer,
            text=self._quality_trend_title(),
            style="SummaryTitle.TLabel",
        )
        self.lbl_trend_title.pack(anchor="w", padx=12, pady=(8, 0))
        self.trend_canvas = tk.Canvas(trend_outer, bg="#16213e", highlightthickness=0, height=200)
        self.trend_canvas.pack(fill="x", padx=8, pady=8)

        stacked_outer = ttk.Frame(chart_frame2, style="Card.TFrame")
        stacked_outer.pack(side="left", fill="both", expand=True, padx=(4, 0))
        stacked_outer.configure(borderwidth=1, relief="solid")
        self.lbl_stacked_title = ttk.Label(stacked_outer, text="Errors by Language", style="SummaryTitle.TLabel")
        self.lbl_stacked_title.pack(anchor="w", padx=12, pady=(8, 0))
        self.stacked_canvas = tk.Canvas(stacked_outer, bg="#16213e", highlightthickness=0, height=200)
        self.stacked_canvas.pack(fill="x", padx=8, pady=8)

        # ── Language detail table ──
        self.lbl_lang_title = ttk.Label(content, text="By Language Breakdown", style="Subtitle.TLabel")
        self.lbl_lang_title.pack(anchor="w", padx=16, pady=(0, 4))

        lang_cols = ("language", "count", "avg_score", "below_pct", "refined_pct", "human_pct", "warnings")
        self.lang_tree = ttk.Treeview(content, columns=lang_cols, show="headings",
                                       style="Summary.Treeview", height=6)
        lang_widths = {"language": 120, "count": 80, "avg_score": 90,
                       "below_pct": 100, "refined_pct": 90, "human_pct": 90, "warnings": 80}
        for c in lang_cols:
            self.lang_tree.column(c, width=lang_widths.get(c, 100),
                                  anchor="center" if c != "language" else "w")
        self.lang_tree.pack(fill="x", padx=16, pady=(0, 8))

        # ── Low-score items ──
        self.lbl_low_title = ttk.Label(content, text="Low-Score Items", style="Subtitle.TLabel")
        self.lbl_low_title.pack(anchor="w", padx=16, pady=(0, 4))

        low_cols = ("idx", "source_type", "scope", "opus_id", "language",
                    "source", "translated", "score", "error_cat", "reason")
        self.low_tree = ttk.Treeview(content, columns=low_cols, show="headings",
                                      style="Summary.Treeview", height=8)
        low_widths = {"idx": 35, "source_type": 50, "scope": 120, "opus_id": 160,
                      "language": 60, "source": 180, "translated": 180,
                      "score": 50, "error_cat": 110, "reason": 160}
        for c in low_cols:
            self.low_tree.column(c, width=low_widths.get(c, 100),
                                 anchor="center" if c in ("idx", "score", "language", "source_type") else "w")
        self.low_tree.pack(fill="x", padx=16, pady=(0, 8))

        # Double-click for detail popup
        self.low_tree.bind("<Double-1>", self._on_low_item_dblclick)

        # ── Export bar ──
        ebar = ttk.Frame(content, style="App.TFrame")
        ebar.pack(fill="x", padx=16, pady=(4, 24))

        self.lbl_qa_fmt = ttk.Label(ebar, text="Format:", style="Card.TLabel")
        self.lbl_qa_fmt.pack(side="left")
        self.qa_fmt_var = tk.StringVar(value="html")
        ttk.Radiobutton(ebar, text="HTML", variable=self.qa_fmt_var, value="html",
                         style="Card.TRadiobutton").pack(side="left", padx=(4, 6))
        ttk.Radiobutton(ebar, text="Excel", variable=self.qa_fmt_var, value="xlsx",
                         style="Card.TRadiobutton").pack(side="left")

        self.btn_qa_export = self.app._create_button(
            ebar, text="Export", command=self._on_export,
            style_name="SuccessSmall", font=(FONT_FAMILY, 10, "bold"),
            bg="#2ecc71", fg="#fff", padx=14, pady=4, state="disabled")
        self.btn_qa_export.pack(side="right")
        self.lbl_qa_status = ttk.Label(ebar, text="", style="Status.TLabel")
        self.lbl_qa_status.pack(side="right", padx=8)

    # ------------------------------------------------------------------
    # Sub-tab switching
    # ------------------------------------------------------------------
    def _switch_tab(self, tab):
        if tab == self._active_tab:
            return
        self._active_tab = tab
        if tab == "mr":
            self.btn_mr_tab.configure(bg="#e94560", fg="#fff")
            self.btn_file_tab.configure(bg="#0f3460", fg="#ccc")
            self.lbl_qa_release.configure(text=self._t("mr_release"))
        else:
            self.btn_mr_tab.configure(bg="#0f3460", fg="#ccc")
            self.btn_file_tab.configure(bg="#e94560", fg="#fff")
            self.lbl_qa_release.configure(text=self._t("qa_task"))

        # Reload filter options for the new tab
        self._reload_filters_for_tab()

        # Display cached data if available
        cached = self._mr_aggregated if tab == "mr" else self._file_aggregated
        if cached:
            self.aggregated = cached
            self._display_data(cached)

    def _reload_filters_for_tab(self):
        if self._active_tab == "mr":
            self.load_filters()
        else:
            threading.Thread(target=self._fetch_legacy_filters, daemon=True).start()

    def _fetch_legacy_filters(self):
        try:
            tasks = mr_api.fetch_all_legacy_tasks_for_quality()
            self._legacy_tasks_cache = tasks
            projects = sorted({
                t.get("project_name", "")
                for t in tasks
                if t.get("project_name")
            })
            task_names = sorted({
                t.get("task_name") or t.get("name", "")
                for t in tasks
                if t.get("task_name") or t.get("name")
            })
            langs = set()
            for t in tasks:
                for lang in (t.get("target_languages") or []):
                    langs.add(lang)
            self.parent.after(
                0,
                self._on_legacy_filters_loaded,
                [""] + projects,
                [""] + task_names,
                [""] + sorted(langs) if langs else [""],
            )
        except Exception:
            pass

    def _on_legacy_filters_loaded(self, projects, task_names, lang_list):
        self.cmb_qa_project.configure(values=projects)
        self.cmb_qa_release.configure(values=task_names)
        if self.qa_project_var.get() not in projects:
            self.qa_project_var.set("")
        if self.qa_release_var.get() not in task_names:
            self.qa_release_var.set("")
        if lang_list and len(lang_list) > 1:
            self.cmb_qa_lang.configure(values=lang_list)

    # ------------------------------------------------------------------
    # i18n refresh
    # ------------------------------------------------------------------
    def refresh_text(self):
        t = self._t
        self.lbl_qa_project.configure(text=t("mr_project"))
        if self._active_tab == "mr":
            self.lbl_qa_release.configure(text=t("mr_release"))
        else:
            self.lbl_qa_release.configure(text=t("qa_task"))
        self.lbl_qa_lang.configure(text=t("qa_language"))
        self.lbl_qa_threshold.configure(text=t("qa_threshold"))
        self.btn_qa_search.configure(text=t("mr_search"))
        self.btn_qa_reset.configure(text=t("mr_reset"))
        self.btn_qa_export.configure(text=t("qa_export"))
        self.lbl_qa_fmt.configure(text=t("output_fmt_label"))

        self.btn_mr_tab.configure(text=t("qa_mr_tab"))
        self.btn_file_tab.configure(text=t("qa_file_tab"))

        self.qa_cards["total_tasks"][1].configure(text=t("qa_total_tasks"))
        self.qa_cards["total_items"][1].configure(text=t("qa_total_items"))
        self.qa_cards["avg_score"][1].configure(text=t("qa_avg_score"))
        self.qa_cards["below_rate"][1].configure(text=t("qa_below_rate"))
        self.qa_cards["refined_rate"][1].configure(text=t("qa_refined_rate"))
        self.qa_cards["human_rate"][1].configure(text=t("qa_human_rate"))

        self.lbl_bar_title.configure(text=t("qa_score_dist"))
        self.lbl_pie_title.configure(text=t("qa_error_dist"))
        self.lbl_trend_title.configure(text=self._quality_trend_title())
        self.lbl_stacked_title.configure(text=t("qa_err_by_lang"))
        self.lbl_lang_title.configure(text=t("qa_lang_detail"))
        self.lbl_low_title.configure(text=t("qa_low_items"))

        for c in ("language", "count", "avg_score", "below_pct", "refined_pct", "human_pct", "warnings"):
            self.lang_tree.heading(c, text=t(f"qa_lang_col_{c}"))
        for c in ("idx", "source_type", "scope", "opus_id", "language",
                   "source", "translated", "score", "error_cat", "reason"):
            self.low_tree.heading(c, text=t(f"qa_low_col_{c}"))

    # ------------------------------------------------------------------
    # Filter loading (MR)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Search / Reset
    # ------------------------------------------------------------------
    def _on_search(self):
        self._threshold = self.threshold_var.get()
        self._load_data()

    def _on_reset(self):
        self.qa_project_var.set("")
        self.qa_release_var.set("")
        self.qa_lang_var.set("")
        self.threshold_var.set(qa.DEFAULT_THRESHOLD)
        self._threshold = qa.DEFAULT_THRESHOLD
        self._load_data()

    # ------------------------------------------------------------------
    # Data loading (dispatches to MR or Legacy)
    # ------------------------------------------------------------------
    def _load_data(self):
        if self.qa_loading:
            return
        self.qa_loading = True
        self.lbl_qa_status.configure(text=self._t("status_exporting"))
        if self._active_tab == "mr":
            threading.Thread(target=self._fetch_mr_data, daemon=True).start()
        else:
            threading.Thread(target=self._fetch_file_data, daemon=True).start()

    def _get_legacy_tasks(self):
        if not self._legacy_tasks_cache:
            self._legacy_tasks_cache = mr_api.fetch_all_legacy_tasks_for_quality()
        return list(self._legacy_tasks_cache)

    @staticmethod
    def _task_matches_language(task, language):
        if not language:
            return True
        task_langs = task.get("target_languages") or []
        return not task_langs or language in task_langs

    @staticmethod
    def _fetch_legacy_task_bundle(task_id, language):
        translations = mr_api.fetch_all_legacy_translations_quality(
            task_id,
            target_language=language,
        )
        try:
            warnings = mr_api.fetch_legacy_translation_warnings(task_id)
        except Exception:
            warnings = {"inconsistent": [], "untranslated": []}
        return task_id, translations, warnings

    def _fetch_mr_data(self):
        try:
            proj = self.qa_project_var.get() or None
            rel = self.qa_release_var.get() or None
            lang = self.qa_lang_var.get() or None
            overview = mr_api.fetch_dashboard_overview(project_id=proj, release=rel)
            cases = mr_api.fetch_all_dashboard_cases(
                project_id=proj,
                release=rel,
                language=lang,
            )
            agg = qa.aggregate_mr_quality(overview, cases, self._threshold)
            self._mr_aggregated = agg
            self.parent.after(0, self._on_data_loaded, agg)
        except Exception as e:
            self.parent.after(0, self._on_data_error, str(e))

    def _fetch_file_data(self):
        try:
            proj = self.qa_project_var.get() or None
            task_name_filter = self.qa_release_var.get() or None
            lang_filter = self.qa_lang_var.get() or None

            tasks = self._get_legacy_tasks()
            if proj:
                tasks = [
                    task for task in tasks
                    if (task.get("project_name") or "") == proj
                ]
            if task_name_filter:
                tasks = [
                    task for task in tasks
                    if task_name_filter in (task.get("task_name") or task.get("name", ""))
                ]
            if lang_filter:
                tasks = [
                    task for task in tasks
                    if self._task_matches_language(task, lang_filter)
                ]

            translations_map = {}
            warnings_map = {}
            if tasks:
                max_workers = min(6, len(tasks))
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {}
                    for task in tasks:
                        tid = str(task.get("task_id") or task.get("id", ""))
                        if tid:
                            futures[pool.submit(self._fetch_legacy_task_bundle, tid, lang_filter)] = tid

                    for future in as_completed(futures):
                        tid = futures[future]
                        try:
                            tid, translations, warnings = future.result()
                        except Exception:
                            continue
                        translations_map[tid] = translations
                        warnings_map[tid] = warnings

            agg = qa.aggregate_legacy_quality(tasks, translations_map, warnings_map,
                                              self._threshold)
            self._file_aggregated = agg
            self.parent.after(0, self._on_data_loaded, agg)
        except Exception as e:
            self.parent.after(0, self._on_data_error, str(e))

    # ------------------------------------------------------------------
    # Display data
    # ------------------------------------------------------------------
    def _on_data_loaded(self, agg):
        self.qa_loading = False
        self.aggregated = agg
        self.lbl_qa_status.configure(text=self._t("status_ready"))
        if IS_MAC:
            self.btn_qa_export.state(["!disabled"])
        else:
            self.btn_qa_export.configure(state="normal")
        self._display_data(agg)

    def _update_metric_cards(self, agg):
        self.qa_cards["total_tasks"][0].configure(text=str(agg.get("total_tasks", 0)))
        self.qa_cards["total_items"][0].configure(text=str(agg.get("total_items", 0)))
        self.qa_cards["avg_score"][0].configure(text=str(agg.get("overall_avg_score", 0)))
        self.qa_cards["below_rate"][0].configure(
            text=f'{agg.get("below_threshold_rate", 0)}%')
        self.qa_cards["refined_rate"][0].configure(
            text=f'{agg.get("refined_rate", 0)}%')
        self.qa_cards["human_rate"][0].configure(
            text=f'{agg.get("human_touch_rate", 0)}%')

    def _update_language_filter_options(self, agg):
        languages = sorted(
            row["language"]
            for row in agg.get("by_language", [])
            if row.get("language")
        )
        current = self.qa_lang_var.get()
        self.cmb_qa_lang.configure(values=[""] + languages)
        if current and current in languages:
            self.qa_lang_var.set(current)

    def _render_quality_charts(self, agg, threshold):
        self.bar_canvas.update_idletasks()
        chart_width = max(self.bar_canvas.winfo_width(), 300)
        qa.draw_bar_chart(
            self.bar_canvas,
            agg.get("score_distribution", {}),
            chart_width,
            200,
            title=self._t("qa_score_dist"),
        )
        qa.draw_pie_chart(
            self.pie_canvas,
            agg.get("error_distribution", {}),
            chart_width,
            200,
            title=self._t("qa_error_dist"),
        )

        self.trend_canvas.update_idletasks()
        trend_width = max(self.trend_canvas.winfo_width(), 300)
        qa.draw_trend_chart(
            self.trend_canvas,
            agg.get("trend_points", []),
            trend_width,
            200,
            threshold=threshold,
            title=self._quality_trend_title(),
        )

        self.stacked_canvas.update_idletasks()
        stacked_width = max(self.stacked_canvas.winfo_width(), 300)
        qa.draw_stacked_bar_chart(
            self.stacked_canvas,
            agg.get("error_by_language", {}),
            stacked_width,
            200,
            title=self._t("qa_err_by_lang"),
        )

    def _render_language_table(self, agg):
        for item in self.lang_tree.get_children():
            self.lang_tree.delete(item)
        for row in agg.get("by_language", []):
            avg = f'{row["average_score"]}' if row.get("average_score") is not None else "-"
            self.lang_tree.insert("", "end", values=(
                row["language"],
                row["count"],
                avg,
                f'{row["below_threshold_pct"]}%',
                f'{row["refined_pct"]}%',
                f'{row["human_touched_pct"]}%',
                row["warnings"],
            ))

    def _render_low_items_table(self, agg):
        for item in self.low_tree.get_children():
            self.low_tree.delete(item)
        for index, row in enumerate(agg.get("low_items", [])[:200], start=1):
            score = row.get("final_score", "-")
            self.low_tree.insert("", "end", values=(
                index,
                row.get("_source_type", ""),
                row.get("_scope_name", "")[:30],
                row.get("opus_id", ""),
                row.get("target_language", ""),
                (row.get("source_text") or "")[:80],
                (row.get("translated_text") or "")[:80],
                score,
                row.get("error_category") or "-",
                (row.get("reason") or "")[:60],
            ))

    def _display_data(self, agg):
        threshold = agg.get("threshold", self._threshold)

        self._update_metric_cards(agg)

        self._update_language_filter_options(agg)

        self._render_quality_charts(agg, threshold)
        self._render_language_table(agg)
        self._render_low_items_table(agg)
        self.lbl_low_title.configure(
            text=f'{self._t("qa_low_items")} (< {threshold})')

    def _on_data_error(self, err):
        self.qa_loading = False
        self.lbl_qa_status.configure(text=f"Error: {err[:60]}")

    # ------------------------------------------------------------------
    # Low-score item detail popup
    # ------------------------------------------------------------------
    def _on_low_item_dblclick(self, event):
        sel = self.low_tree.selection()
        if not sel:
            return
        item_idx_str = self.low_tree.item(sel[0], "values")[0]
        try:
            idx = int(item_idx_str) - 1
        except (ValueError, TypeError):
            return
        if not self.aggregated:
            return
        low_items = self.aggregated.get("low_items", [])
        if idx < 0 or idx >= len(low_items):
            return
        it = low_items[idx]
        self._show_quality_item_detail(it)

    @staticmethod
    def _set_text_widget_value(widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", str(value or "-"))
        widget.configure(state="disabled")

    def _load_legacy_edit_logs(self, task_id, translation_id, widget):
        try:
            logs = mr_api.fetch_legacy_translation_edit_logs(task_id, translation_id)
        except Exception:
            logs = []

        if not logs:
            text = "No edit logs"
        else:
            chunks = []
            for log in logs[:10]:
                user = log.get("user_name") or "Unknown"
                created_at = log.get("created_at") or ""
                notes = log.get("notes") or ""
                edited_text = log.get("edited_text") or ""
                chunks.append(f"[{created_at}] {user}\n{edited_text}")
                if notes:
                    chunks.append(f"Notes: {notes}")
            text = "\n\n".join(chunks)

        self.parent.after(0, lambda: self._set_text_widget_value(widget, text))

    def _show_quality_item_detail(self, it):
        """Show a normalized detail window for a low-score item."""
        win = tk.Toplevel(self.parent)
        win.title(f"Detail - {it.get('opus_id', '')[:40]}")
        win.geometry("700x560")
        win.configure(bg="#1a1a2e")

        pad = {"padx": 16, "pady": 4}

        def _add_row(parent, label, value, **kwargs):
            frame = ttk.Frame(parent, style="App.TFrame")
            frame.pack(fill="x", **pad)
            ttk.Label(frame, text=label, style="Card.TLabel", width=16,
                      anchor="e").pack(side="left")
            widget = tk.Text(
                frame,
                height=kwargs.get("height", 1),
                width=60,
                bg="#16213e",
                fg="#ccc",
                font=(FONT_FAMILY, 10),
                wrap="word",
                relief="flat",
                borderwidth=0,
            )
            widget.insert("1.0", str(value or "-"))
            widget.configure(state="disabled")
            widget.pack(side="left", padx=(8, 0), fill="x", expand=True)
            return widget

        _add_row(win, "String Key:", it.get("opus_id", ""))
        _add_row(win, "Language:", it.get("target_language", ""))
        _add_row(win, "Source:", it.get("source_text", ""), height=3)
        _add_row(win, "Translated:", it.get("translated_text", ""), height=3)
        _add_row(win, "Score:", it.get("final_score", "-"))
        _add_row(win, "Error Category:", it.get("error_category", "-"))
        _add_row(win, "Reason:", it.get("reason", ""), height=3)
        _add_row(win, "Iteration:", it.get("iteration", 1))

        iter1 = qa.get_iteration_snapshot(it, "iteration_1")
        if iter1.get("final_score") is not None:
            _add_row(win, "Iter 1 Score:", iter1.get("final_score"))
            if iter1.get("translation"):
                _add_row(win, "Iter 1 Text:", iter1.get("translation"), height=2)
            _add_row(win, "Iter 1 Reason:", iter1.get("reason", ""), height=2)

        comment = it.get("reviewer_comment") or it.get("reviewer_notes") or ""
        if comment:
            _add_row(win, "Reviewer:", comment, height=2)
        if it.get("fixed_by_lead"):
            _add_row(win, "Fixed by:", it.get("fixed_by_lead", ""))
            _add_row(win, "Fixed text:", it.get("fixed_text", ""), height=2)
        if it.get("warning_types"):
            _add_row(win, "Warnings:", ", ".join(it.get("warning_types", [])))

        if it.get("_source_type") == "File" and it.get("_task_id") and it.get("translation_id"):
            edit_widget = _add_row(win, "Edit Logs:", "Loading...", height=6)
            threading.Thread(
                target=self._load_legacy_edit_logs,
                args=(it.get("_task_id"), it.get("translation_id"), edit_widget),
                daemon=True,
            ).start()

        btn_close = self.app._create_button(
            win, text="Close", command=win.destroy,
            style_name="SecondarySmall", font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=20, pady=4)
        btn_close.pack(pady=12)

    def _show_item_detail(self, it):
        """Backward-compatible wrapper."""
        self._show_quality_item_detail(it)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
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
            tab_label = "MR" if self._active_tab == "mr" else "File"
            filename = f"quality_overview_{tab_label}_{today}{ext}"
            script_dir = os.path.dirname(os.path.abspath(__file__))
            filepath = os.path.join(script_dir, filename)
            label = f"Quality Overview — {tab_label} (exported {today})"
            qa.save_quality_file(self.aggregated, filepath, label, fmt)
            self.parent.after(0,
                lambda: self.lbl_qa_status.configure(text=self._t("status_done")))
        except Exception as e:
            self.parent.after(0,
                lambda: self.lbl_qa_status.configure(text=f"Error: {str(e)[:50]}"))
        finally:
            def _restore():
                if IS_MAC:
                    self.btn_qa_export.state(["!disabled"])
                else:
                    self.btn_qa_export.configure(state="normal")
            self.parent.after(0, _restore)
