"""
GUI Tab Builders — MR Pipeline + Quality Overview tabs for export_gui.py
"""
import os
import sys
import threading
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import font as tkfont
from tkinter import ttk
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_mr_pipeline as mr_api
import mr_jira as _jira
import quality_overview as qa
import task_post_edit as _tpe
import advanced_filter
# Aliased so the ``llm_qa`` boolean flag threaded through the export handlers
# doesn't shadow the module inside those methods.
import llm_qa as llm_qa_module
from export_gui import FONT_FAMILY, IS_MAC, reveal_in_folder, sanitize_for_filename
from date_picker import attach_calendar
from searchable_combobox import attach_search, format_selection_summary
import project_presets as _presets


def _single_line_title(value):
    """Normalize a table title so it can never create a second row line."""
    return " ".join(str(value or "").split())


def _ellipsize_text(value, max_width, measure):
    """Pixel-fit one line of text using a literal CSS-style ``...`` suffix.

    ``measure`` is injected (normally ``tkinter.font.Font.measure``), keeping
    the sizing logic deterministic and independently testable.
    """
    text = _single_line_title(value)
    if not text:
        return "", False
    try:
        available = max(0, int(max_width))
    except (TypeError, ValueError):
        available = 0
    if measure(text) <= available:
        return text, False

    suffix = "..."
    if measure(suffix) > available:
        return suffix, True

    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid].rstrip() + suffix
        if measure(candidate) <= available:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + suffix, True


# ============================================================
# MR Pipeline Tab
# ============================================================
class MRPipelineTab:
    """Builds and manages the MR Pipeline tab content."""

    # Single source of truth for the task-list table columns. ``src_strings``
    # (distinct en-US source-string count) sits between Status and Avg Score
    # so the two per-task metrics read together; ``jira`` and its companion
    # ``title`` sit right after ``mr`` so same-origin tasks read as one group.
    # These insertions keep the critical positional reads elsewhere valid
    # (project @ idx 1 for the post-edit prefix, MR# @ idx 2 for the export
    # filename).
    _MR_COLUMNS = ("idx", "project", "mr", "jira", "title", "release",
                   "status", "src_strings", "avg_score", "created",
                   "duration")
    # Columns whose cells sort numerically; everything else sorts as text.
    _MR_NUMERIC_COLS = frozenset({"idx", "mr", "src_strings", "avg_score"})

    def __init__(self, parent, app, *, base_url=None, env_key="prod"):
        self.app = app
        self.parent = parent
        # None / omitted → production. Stage tab passes TRANZOR_STAGE_URL.
        self.base_url = (base_url or mr_api.TRANZOR_URL).rstrip("/")
        self.env_key = env_key or "prod"
        # Isolate the ✏️ cache so a Stage MR# cannot pick up a Prod answer
        # (or vice versa) — same project+iid routinely exists in both envs.
        self._post_edit_kind = (
            "mr" if self.env_key == "prod" else f"mr_{self.env_key}")
        self.mr_page = 0
        self.mr_page_size = 25
        # Extra pages appended below the anchor page via "Load More".
        # 0 == single-page view (Prev/Next semantics); > 0 == extended
        # view where the table also shows the next N pages worth of
        # rows. Reset whenever the user navigates (Prev/Next/Search/
        # Reset) since those replace the visible rows.
        self.mr_extra_pages = 0
        # One-shot flag set by ``_load_more`` and consumed by the next
        # ``_fetch_tasks`` call to switch the load into append mode.
        self._pending_append = False
        self.mr_total = 0
        self.mr_filtered_total = 0
        self.mr_loading = False
        self.mr_overview_loading = False
        self._recent_projects_loading = False
        self._loading_anim_id = None
        self._loading_dot_count = 0
        # task_id → Treeview iid; populated each time _on_tasks_loaded
        # repaints, used by the async post-edit prefetch callback to
        # find the row to mark.
        self._mr_row_iid_by_task: dict[str, str] = {}
        # (project_id, mr_iid) → [row iids]. One JIRA fetch must fill
        # *every* matching row: the same MR routinely triggers several
        # pipeline tasks (that's the whole same-origin premise), so a
        # single-iid mapping would light up only the last-inserted row.
        self._jira_row_iids: dict[tuple[str, int], list[str]] = {}
        # Full Title stays outside Treeview values so sorting and Tooltip use
        # the lossless text while the visible cell can carry a width-specific
        # ``...`` rendering.
        self._jira_titles_by_iid: dict[str, str] = {}
        self._truncated_title_iids: set[str] = set()
        self._title_resize_after_id = None
        self._title_tooltip_after_id = None
        self._title_tooltip_window = None
        self._title_tooltip_cell = None
        self._title_tooltip_pointer = (0, 0)
        # task_id → distinct en-US source-string count. Cached so paging
        # back/forth, re-search and language switches don't re-hit the
        # results API — a completed task's source-string count is immutable.
        # Filled from worker threads, so guard it with a lock.
        self._src_count_cache: dict[str, int] = {}
        self._src_count_lock = threading.Lock()
        # Active sort as (column_id, descending) or None. Tracked so the
        # async source-count prefetch can re-apply the user's sort once the
        # numbers land, and so the header redraw shows the ▲/▼ marker.
        self._mr_sort = None
        # Project dropdown is multi-select. Empty list = no project filter
        # ("All"), matching the historical empty Combobox value. The
        # Combobox StringVar is display-only (one name, or "N selected").
        self._mr_selected_projects = []
        self._build(parent)

    def _t(self, key):
        return self.app._t(key)

    def _api_kw(self):
        """Pass ``base_url`` only for non-prod so existing test fakes
        (which don't accept the kwarg) keep working on the prod tab."""
        if self.base_url.rstrip("/") != mr_api.TRANZOR_URL.rstrip("/"):
            return {"base_url": self.base_url}
        return {}

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

        # Stage instance: a compact env chip so the two otherwise-identical
        # panels can't be mistaken for each other.
        if self.env_key == "stage":
            self.lbl_mr_env_badge = tk.Label(
                fi, text="STAGE",
                font=(FONT_FAMILY, 8, "bold"),
                bg="#854d0e", fg="#fde68a",
                padx=6, pady=1)
            self.lbl_mr_env_badge.pack(anchor="e", pady=(0, 6))
        else:
            self.lbl_mr_env_badge = None

        # Row 1: Project + Release + Status
        r1 = ttk.Frame(fi, style="Card.TFrame")
        r1.pack(fill="x", pady=(0, 6))

        self.lbl_mr_project = ttk.Label(r1, text="", style="Card.TLabel", width=8)
        self.lbl_mr_project.pack(side="left")
        self.mr_project_var = tk.StringVar()
        self.cmb_mr_project = ttk.Combobox(r1, textvariable=self.mr_project_var, width=20, state="readonly")
        self.cmb_mr_project.pack(side="left", padx=(4, 12))
        # 项目列表长（上百个仓库路径），原生下拉只能滚动找 —— 换成顶部带
        # 关键字搜索框的**多选**过滤弹窗（选项仍每次现读 values，异步加载
        # 无感）。空选 = 全部项目，与改造前的空占位项同语义。
        attach_search(self.cmb_mr_project, font_family=FONT_FAMILY,
                      lang=lambda: self.app.lang, multi=True,
                      get_selected=self._selected_mr_projects,
                      set_selected=self._set_mr_selected_projects,
                      get_presets=self._load_mr_presets,
                      save_presets=self._save_mr_presets)

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

        self.lbl_mr_jira_id = ttk.Label(
            r1, text="", style="Card.TLabel")
        self.lbl_mr_jira_id.pack(side="left", padx=(16, 0))
        self.mr_jira_var = tk.StringVar()
        self.ent_mr_jira = tk.Entry(
            r1, textvariable=self.mr_jira_var, width=14,
            font=(FONT_FAMILY, 10), bg="#0a0a1a", fg="#fff",
            insertbackground="#fff", relief="flat")
        self.ent_mr_jira.pack(side="left", padx=(4, 0), ipady=3)
        self.ent_mr_jira.bind("<Return>", lambda _event: self._on_search())

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

        # Date fields keep manual typing; the 📅 button opens a calendar
        # popup for point-and-click selection.
        def _entry_setter(entry):
            def _set(s):
                entry.delete(0, "end")
                entry.insert(0, s)
            return _set

        self.mr_date_from = tk.Entry(r2, width=12, font=(FONT_FAMILY, 10),
                                      bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat")
        self.mr_date_from.pack(side="left", padx=(4, 2), ipady=3)
        attach_calendar(
            r2, self.mr_date_from, font_family=FONT_FAMILY,
            get_value=self.mr_date_from.get,
            set_value=_entry_setter(self.mr_date_from),
            lang=lambda: self.app.lang, padx=(0, 6))
        ttk.Label(r2, text="—", style="Card.TLabel").pack(side="left")
        self.mr_date_to = tk.Entry(r2, width=12, font=(FONT_FAMILY, 10),
                                    bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat")
        self.mr_date_to.pack(side="left", padx=(4, 2), ipady=3)
        attach_calendar(
            r2, self.mr_date_to, font_family=FONT_FAMILY,
            get_value=self.mr_date_to.get,
            set_value=_entry_setter(self.mr_date_to),
            lang=lambda: self.app.lang, padx=(0, 12))

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

        # Client-side "only post-edited (✏️) MRs" view filter. Post-edit
        # status is determined asynchronously by the ✏️ prefetch, so this
        # filters the rows already loaded rather than re-querying the server
        # (text set in refresh_text, like the legend above).
        self.mr_post_edit_only_var = tk.BooleanVar(value=False)
        self.chk_mr_post_edit_only = ttk.Checkbutton(
            r2, text="", variable=self.mr_post_edit_only_var,
            style="Card.TCheckbutton", command=self._on_post_edit_only_toggle)
        self.chk_mr_post_edit_only.pack(side="left", padx=(12, 0))

        # ── Advanced Filters (collapsible) — content-level filter carried into
        #    the export (HTML pre-fills + auto-applies; Excel/JSON keep only
        #    matching rows). See advanced_filter.AdvancedFilterPanel. ──
        self.adv_filter = None
        if advanced_filter.AdvancedFilterPanel is not None:
            self.adv_filter = advanced_filter.AdvancedFilterPanel(left, self.app)
            self.adv_filter.pack(fill="x", pady=(0, 8))

        # ── Action bar (Export + Pagination) — above table for visibility on macOS ──
        action = ttk.Frame(left, style="App.TFrame")
        action.pack(fill="x", pady=(6, 6))

        self.btn_mr_export = self.app._create_button(
            action, text="", command=self._on_export,
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#2ecc71", fg="#fff", padx=14, pady=4, state="disabled")
        self.btn_mr_export.pack(side="left")

        # One-click "export full-translation JSON + copy the LQA prompt" for the
        # /rc-core-products-trans-checker workflow. Forces JSON + All
        # Translations (see _on_export) then copies the prompt + pops a how-to
        # dialog. Enable/disable tracks btn_mr_export (needs rows to export).
        self.btn_mr_llm_qa = self.app._create_button(
            action, text=llm_qa_module.button_label(self.app.lang),
            command=lambda: self._on_export(llm_qa=True),
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#7c5cff", fg="#fff", padx=14, pady=4, state="disabled")
        self.btn_mr_llm_qa.pack(side="left", padx=(8, 0))

        # Export Type selector (mirrors File Translation panel)
        self.lbl_mr_export_type = ttk.Label(action, text="", style="Card.TLabel")
        self.lbl_mr_export_type.pack(side="left", padx=(16, 4))
        self.mr_export_type_var = tk.StringVar(value="translations")
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
                         style="Card.TRadiobutton").pack(side="left", padx=(0, 6))
        # JSON 选项：透视为 {key, en-US, de-DE, ...} 供 LQA Skill 消费
        self.rb_mr_json = ttk.Radiobutton(
            action, text="", variable=self.mr_fmt_var, value="json",
            style="Card.TRadiobutton")
        self.rb_mr_json.pack(side="left")

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

        # Legend for the ✏️ marker the async post-edit prefetch may
        # prepend to the Project column once detail fetches return.
        self.lbl_mr_post_edit_legend = ttk.Label(
            left, text="", style="Status.TLabel",
        )
        self.lbl_mr_post_edit_legend.pack(anchor="w", pady=(0, 4))

        # ── Footer: Load More button anchored at the bottom of `left` ──
        # Packed BEFORE tree_frame with side="bottom" so the tree's
        # expand=True fills every pixel between the legend and this
        # footer — mirrors the pattern used in _build_mr_sidebar for
        # the refresh button anchor.
        footer = ttk.Frame(left, style="App.TFrame")
        footer.pack(side="bottom", fill="x", pady=(6, 0))

        self.btn_mr_load_more = self.app._create_button(
            footer, text="", command=self._load_more,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10, "bold"), bg="#0f3460", fg="#ccc",
            padx=18, pady=4, state="disabled")
        self.btn_mr_load_more.pack(side="left")

        self.lbl_mr_load_more_hint = ttk.Label(
            footer, text="", style="Status.TLabel")
        self.lbl_mr_load_more_hint.pack(side="left", padx=(12, 0))

        # ── Task list table ──
        tree_frame = ttk.Frame(left, style="App.TFrame")
        tree_frame.pack(fill="both", expand=True, pady=(0, 6))

        cols = self._MR_COLUMNS
        self.mr_tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                     style="Summary.Treeview", height=14, selectmode="browse")
        col_widths = {"idx": 35, "project": 140, "mr": 60, "jira": 90,
                      "title": 260, "release": 60, "status": 80,
                      "src_strings": 90, "avg_score": 70, "created": 130,
                      "duration": 70}
        for c in cols:
            width = col_widths.get(c, 80)
            is_title = c == "title"
            self.mr_tree.column(
                c,
                width=width,
                minwidth=140 if is_title else width,
                anchor="w" if c in ("project", "title") else "center",
                # Only Title absorbs spare horizontal space and contracts
                # with the window; the compact metric columns stay stable.
                stretch=is_title,
            )
            # Clickable header → sort the visible rows by that column. Wired
            # once here; refresh_text only swaps heading *text*, which leaves
            # the command intact.
            self.mr_tree.heading(c, command=lambda col=c: self._sort_by(col))

        # Warm gold tint for MRs the post-edit prefetch marks. See
        # gui_tab_scan_tasks for the colour-rationale; the three tabs
        # share the same palette intentionally so the signal reads
        # identically across them.
        self.mr_tree.tag_configure(
            "post_edit", background="#3a2e1f", foreground="#fde68a",
        )

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.mr_tree.yview)
        self.mr_tree.configure(yscrollcommand=scroll.set)
        self.mr_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # A ttk.Treeview cannot host a real HTML <a> element, so make the
        # JIRA cell itself the hyperlink hit target. The pointer changes only
        # over a resolved, valid ticket ID; clicking it opens the canonical
        # RingCentral JIRA detail page in the user's default browser.
        self.mr_tree.bind("<Motion>", self._on_mr_tree_motion, add="+")
        self.mr_tree.bind("<Leave>", self._on_mr_tree_leave, add="+")
        self.mr_tree.bind("<ButtonPress-1>", self._on_mr_tree_press, add="+")
        self.mr_tree.bind(
            "<ButtonRelease-1>", self._on_mr_tree_click, add="+")
        self.mr_tree.bind(
            "<Configure>", self._schedule_title_ellipsis, add="+")
        tree_font = (ttk.Style().lookup("Summary.Treeview", "font")
                     or "TkDefaultFont")
        self._mr_title_font = tkfont.Font(root=self.mr_tree, font=tree_font)

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
        self._sync_mr_project_display()
        self.lbl_mr_release.configure(text=t("mr_release"))
        self.lbl_mr_status.configure(text=t("mr_status"))
        self.lbl_mr_date.configure(text=t("mr_date_range"))
        self.lbl_mr_task_id.configure(text=t("mr_task_id"))
        self.lbl_mr_jira_id.configure(text=t("mr_jira_id"))
        self.btn_mr_search.configure(text=t("mr_search"))
        self.btn_mr_reset.configure(text=t("mr_reset"))
        self.btn_mr_export.configure(text=t("mr_export"))
        self.btn_mr_llm_qa.configure(text=llm_qa_module.button_label(self.app.lang))
        self.btn_mr_load_more.configure(text=t("mr_load_more"))
        self.lbl_mr_export_type.configure(text=t("export_type_label"))
        self.rb_mr_changes.configure(text=t("export_type_changes"))
        self.rb_mr_translations.configure(text=t("export_type_all"))
        self.lbl_mr_fmt.configure(text=t("output_fmt_label"))
        self.rb_mr_json.configure(text=t("output_fmt_json"))
        self.btn_mr_refresh.configure(text=t("summary_refresh"))

        for col in self._MR_COLUMNS:
            self.mr_tree.heading(col, text=self._sort_heading_text(col))
        self.lbl_mr_post_edit_legend.configure(text=t("mr_post_edit_legend"))
        self.chk_mr_post_edit_only.configure(text=t("mr_post_edit_only"))
        if self.adv_filter is not None:
            self.adv_filter.refresh_text()

        sidebar_key = (
            "mr_sidebar_title_stage" if self.env_key == "stage"
            else "mr_sidebar_title")
        self.lbl_mr_sidebar_title.configure(text=t(sidebar_key))
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
        """Load the latest ``mr_page_size`` tasks (no filters) on first tab selection."""
        self._load_tasks()

    def _refresh_tasks(self):
        """Refresh the current task list."""
        self._load_tasks()

    def load_filters(self):
        threading.Thread(target=self._fetch_filters, daemon=True).start()

    def _fetch_filters(self):
        try:
            data = mr_api.fetch_mr_filters(**self._api_kw())
            self.parent.after(0, self._on_filters_loaded, data)
        except Exception:
            pass

    def _on_filters_loaded(self, data):
        pids = [""] + data.get("project_ids", [])
        rels = [""] + data.get("releases", [])
        self.cmb_mr_project.configure(values=pids)
        self.cmb_mr_release.configure(values=rels)
        valid = {str(p) for p in (data.get("project_ids") or []) if p}
        self._mr_selected_projects = [
            p for p in self._selected_mr_projects() if p in valid]
        self._sync_mr_project_display()

    def _invalidate_post_edit_cache(self):
        """Drop the cached ✏️ (post-edit) answers for the MR kind.

        The badge is served from a process-lifetime cache
        (:class:`task_post_edit.PostEditCache`). A Language Lead who fixes a
        translation in the Tranzor dashboard sets ``fixed_by_lead`` on the
        case *after* we may have already cached a ``False`` "no edit" answer
        for that MR. Without this invalidation, re-searching reuses the stale
        ``False`` — the render only re-fetches when the cached value is
        ``None`` (see ``_on_tasks_loaded``) — so the badge never lights up
        even though a fresh Changes export (which reads ``/dashboard/cases``
        directly, bypassing the cache) correctly detects the edit.

        A transient API failure during a prior fetch also lands here as a
        cached ``False``; clearing on an explicit re-query lets it self-heal.

        Mirrors the File Translation Refresh, which drops the ``legacy`` kind
        for exactly the same go-edit-then-come-back reason
        (``export_gui._load_summary_data``). Scoped to an explicit Search /
        Reset — paging (Prev / Next / Load More) keeps the cache so flipping
        pages stays free. Best-effort: never block the search on housekeeping.
        """
        try:
            _tpe.get_cache().clear_kind(
                getattr(self, "_post_edit_kind", "mr"))
        except Exception:
            pass

    def _on_search(self):
        # An explicit re-query means "give me fresh data" — drop stale ✏️
        # answers so a Language Lead's just-made fixes surface (see
        # _invalidate_post_edit_cache).
        self._invalidate_post_edit_cache()
        self.mr_page = 0
        self._load_tasks()

    def _selected_mr_projects(self):
        """Currently checked Project ids. Empty = no project filter."""
        return [p for p in getattr(self, "_mr_selected_projects", []) if p]

    def _set_mr_selected_projects(self, selected):
        self._mr_selected_projects = [
            str(p) for p in (selected or []) if str(p).strip()]
        self._sync_mr_project_display()

    def _load_mr_presets(self):
        return _presets.load_presets(getattr(self, "env_key", "prod"))

    def _save_mr_presets(self, rows):
        _presets.save_presets(getattr(self, "env_key", "prod"), rows)

    def _sync_mr_project_display(self):
        """Push the multi-select summary into the Project Combobox."""
        lang = "en"
        try:
            lang = self.app.lang
        except Exception:
            pass
        selected = self._selected_mr_projects()
        match = None
        try:
            match = _presets.matching_name(selected, self._load_mr_presets())
        except Exception:
            match = None
        try:
            self.mr_project_var.set(
                format_selection_summary(selected, lang, preset_name=match))
        except Exception:
            pass

    def _mr_project_filter_kwargs(self):
        """Kwargs for fetch_mr_tasks / collect_all_mr_results.

        Zero or one selected project uses the historical ``project_id``
        argument (so test fakes that don't take ``project_ids`` keep
        working). Two or more go through ``project_ids``.
        """
        projs = self._selected_mr_projects()
        if len(projs) > 1:
            return {"project_id": None, "project_ids": projs}
        return {"project_id": (projs[0] if projs else None)}

    def _on_reset(self):
        self._mr_selected_projects = []
        self.mr_project_var.set("")
        self.mr_release_var.set("")
        self.mr_status_var.set("")
        self.mr_iid_var.set("")
        self.mr_task_id_var.set("")
        self.mr_jira_var.set("")
        self.mr_date_from.delete(0, "end")
        self.mr_date_to.delete(0, "end")
        self._invalidate_post_edit_cache()
        self.mr_page = 0
        self._load_tasks()

    def _prev_page(self):
        if self.mr_page > 0:
            self.mr_page -= 1
            # _on_tasks_loaded clears mr_extra_pages whenever append=False
            self._load_tasks()

    def _next_page(self):
        filters_active = (
            self.mr_hide_empty_var.get()
            or self.mr_iid_var.get().strip()
            or self.mr_task_id_var.get().strip()
            or self.mr_jira_var.get().strip()
        )
        effective_total = self.mr_filtered_total if filters_active else self.mr_total
        # Skip past every page already visible in the current extended
        # view so Next never re-shows rows the user just scrolled through
        # via Load More.
        next_page = self.mr_page + 1 + self.mr_extra_pages
        if next_page * self.mr_page_size < effective_total:
            self.mr_page = next_page
            self._load_tasks()

    def _load_more(self):
        """Append the next page of rows below the current view.

        Unlike Next (which jumps the anchor page and replaces the tree),
        Load More keeps the existing rows and tacks on the next batch
        — giving users a continuous-scroll way to dig into older MR
        history without paging back-and-forth.
        """
        if self.mr_loading:
            return
        filters_active = (
            self.mr_hide_empty_var.get()
            or self.mr_iid_var.get().strip()
            or self.mr_task_id_var.get().strip()
            or self.mr_jira_var.get().strip()
        )
        effective_total = self.mr_filtered_total if filters_active else self.mr_total
        items_shown = (self.mr_page + 1 + self.mr_extra_pages) * self.mr_page_size
        if items_shown >= effective_total:
            return
        self._pending_append = True
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
            self.btn_mr_llm_qa.state(flag)
            self.btn_mr_prev.state(flag)
            self.btn_mr_next.state(flag)
            self.btn_mr_load_more.state(flag)
        else:
            self.btn_mr_export.configure(state=state)
            self.btn_mr_llm_qa.configure(state=state)
            self.btn_mr_prev.configure(state=state)
            self.btn_mr_next.configure(state=state)
            self.btn_mr_load_more.configure(state=state)

    def _check_task_translations(self, t):
        """Check a task's translation count via API; attach _translations_count,
        _src_string_count and average_score.

        Runs only on the ``Hide empty MRs`` path, but it already has the full
        results payload in hand, so it computes the distinct en-US source-string
        count here too and seeds the shared cache — that way the
        ``src_strings`` column renders without a second round-trip per row."""
        tid = t.get("task_id")
        if not tid:
            t["_translations_count"] = 0
            t["_src_string_count"] = 0
            return
        try:
            results = mr_api.fetch_mr_results(tid, **self._api_kw())
            trs = results.get("translations", [])
            t["_translations_count"] = len(trs)
            src = mr_api.distinct_source_string_count(trs)
            t["_src_string_count"] = src
            with self._src_count_lock:
                self._src_count_cache[tid] = src
            if trs and t.get("average_score") is None:
                scores = [tr.get("score") for tr in trs if tr.get("score") is not None]
                if scores:
                    t["average_score"] = round(sum(scores) / len(scores), 2)
        except Exception:
            t["_translations_count"] = 0

    def _fetch_tasks(self):
        try:
            projs = self._selected_mr_projects()
            proj_set = set(projs)
            proj = projs[0] if len(projs) == 1 else None
            project_kw = self._mr_project_filter_kwargs()
            rel = self.mr_release_var.get() or None
            status = self.mr_status_var.get() or None
            mr_iid_filter = self.mr_iid_var.get().strip()
            task_id_filter = self.mr_task_id_var.get().strip()
            jira_filter_raw = self.mr_jira_var.get().strip()
            jira_filter = _jira.normalize_jira_id(jira_filter_raw)
            if jira_filter_raw and not jira_filter:
                raise ValueError("JIRA ID must look like BUP-4360.")
            if jira_filter and not _jira.can_fetch():
                raise RuntimeError(
                    "A GitLab token is required to filter by JIRA ID.")
            hide_empty = self.mr_hide_empty_var.get()

            # Capture-and-clear the one-shot append flag set by
            # _load_more. _on_tasks_loaded uses ``append`` to decide
            # whether to replace or extend the tree, and ``base_offset``
            # to assign correct row indices (idx column) for the new
            # batch.
            append = self._pending_append
            self._pending_append = False
            if append:
                base_offset = (self.mr_page + 1 + self.mr_extra_pages) * self.mr_page_size
            else:
                base_offset = self.mr_page * self.mr_page_size

            # Task ID short-circuit: if user pastes a UUID, look it up
            # directly via /tasks/{task_id} and intersect with the other
            # filters so results stay consistent with Project/Release/Status/MR#.
            if task_id_filter:
                try:
                    detail = mr_api.fetch_mr_task_detail(
                        task_id_filter, **self._api_kw())
                except Exception:
                    detail = None
                collected = []
                if isinstance(detail, dict) and detail.get("task_id"):
                    if proj_set and str(detail.get("project_id", "")) not in proj_set:
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
                if (isinstance(detail, dict) and detail.get("task_id")
                        and jira_filter):
                    actual_jira = _jira.fetch_jira_id(
                        detail.get("project_id"),
                        detail.get("merge_request_iid"))
                    if actual_jira != jira_filter:
                        detail = None
                    else:
                        detail["_jira_ticket_id"] = jira_filter
                if isinstance(detail, dict) and detail.get("task_id"):
                    if hide_empty:
                        self._check_task_translations(detail)
                        if detail.get("_translations_count", 0) == 0:
                            detail = None
                if isinstance(detail, dict) and detail.get("task_id"):
                    collected.append(detail)
                matched_total = len(collected)
                # Single result fits on page 0 — return directly. Force
                # append=False because a single-result short-circuit
                # always replaces the view (never extends it).
                self.parent.after(0, self._on_tasks_loaded,
                                  matched_total, collected, matched_total,
                                  False, 0)
                return

            matching_jira_mrs = set()
            if jira_filter:
                if len(projs) > 1:
                    for pid in projs:
                        matching_jira_mrs |= _jira.find_merge_requests(
                            jira_filter, project_id=pid)
                else:
                    matching_jira_mrs = _jira.find_merge_requests(
                        jira_filter, project_id=proj)
                if not matching_jira_mrs:
                    self.parent.after(
                        0, self._on_tasks_loaded, self.mr_total, [], 0,
                        False, 0)
                    return

            need_filter = (
                hide_empty or bool(mr_iid_filter) or bool(jira_filter))

            if not need_filter:
                # Simple path: no client-side filtering needed
                total, tasks = mr_api.fetch_mr_tasks(
                    release=rel, status=status,
                    limit=self.mr_page_size, offset=base_offset,
                    **project_kw, **self._api_kw())
                self.parent.after(0, self._on_tasks_loaded,
                                  total, tasks, total, append, base_offset)
            else:
                from concurrent.futures import ThreadPoolExecutor, as_completed

                # Accumulate non-empty / MR#-matched tasks across multiple API batches
                batch_size = 100
                target = self.mr_page_size
                skip_count = base_offset  # items to skip for pagination
                collected = []
                offset = 0
                api_total = 0
                total_matched = 0
                total_scanned = 0

                while True:
                    api_total, batch = mr_api.fetch_mr_tasks(
                        release=rel, status=status,
                        limit=batch_size, offset=offset,
                        **project_kw, **self._api_kw())
                    if not batch:
                        break
                    total_scanned += len(batch)

                    if jira_filter:
                        batch = [t for t in batch
                                 if _jira.task_matches_mrs(
                                     t, matching_jira_mrs)]
                        for task in batch:
                            task["_jira_ticket_id"] = jira_filter

                    # MR# client-side filter first (cheap, no API call)
                    if mr_iid_filter:
                        batch = [t for t in batch
                                 if str(t.get("merge_request_iid", "")) == mr_iid_filter]

                    # Parallel check translation counts (4x faster than sequential)
                    if hide_empty and batch:
                        with ThreadPoolExecutor(max_workers=4) as pool:
                            list(pool.map(self._check_task_translations, batch))

                    for t in batch:
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

                self.parent.after(0, self._on_tasks_loaded,
                                  api_total, collected, estimated_total,
                                  append, base_offset)
        except Exception as e:
            self.parent.after(0, self._on_tasks_error, str(e))

    def _on_tasks_loaded(self, api_total, tasks, filtered_total,
                          append=False, base_offset=0):
        self.mr_loading = False
        self._stop_loading_anim()
        self.mr_total = api_total
        self.mr_filtered_total = filtered_total

        if not append:
            # Replace mode: clear the tree and reset row mapping +
            # extended-view counter so we're back to a single-page view.
            for item in self.mr_tree.get_children():
                self.mr_tree.delete(item)
            self._mr_row_iid_by_task = {}
            self._jira_row_iids = {}
            self._jira_titles_by_iid = {}
            self._truncated_title_iids = set()
            self._hide_title_tooltip()
            self.mr_extra_pages = 0
            # A fresh result set arrives in API order (created desc); drop any
            # active sort so the ▲/▼ marker doesn't lie about the row order.
            # An Append (Load More) keeps the sort — _on_src_counts_done folds
            # the new rows in once their counts land.
            if self._mr_sort is not None:
                self._mr_sort = None
                self._refresh_sort_indicators()
        # Append mode: keep existing rows + row mapping intact so
        # post-edit tagging on older rows still works.
        prefetch_items: list[tuple[str, int]] = []
        # task_ids whose en-US source-string count isn't cached yet — filled
        # asynchronously after this page renders (see _prefetch_src_counts).
        src_prefetch_ids: list[str] = []
        # (project_id, mr_iid) keys whose JIRA metadata isn't cached yet —
        # deduped because one GitLab MR-title fetch supplies both JIRA and
        # Title to every row belonging to that MR.
        jira_prefetch: list[tuple[str, int]] = []
        jira_seen: set[tuple[str, int]] = set()
        # Resolved once per repaint: when GitLab is unreachable (no token)
        # the JIRA cells render "—" up front instead of a "…" spinner that
        # would never resolve.
        jira_fetchable = _jira.can_fetch()

        for i, t in enumerate(tasks):
            idx = base_offset + i + 1
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
            task_id = t.get("task_id") or ""
            mr_iid = t.get("merge_request_iid")
            raw_project = t.get("project_id", "")
            # Cache key must match what _fetch_mr keys on so the
            # synchronous-render path doesn't miss a previously-cached
            # answer — see _fetch_mr in task_post_edit for the tuple
            # shape ``(project_id, mr_iid)``.
            cache_key = (
                (raw_project, mr_iid) if (raw_project and mr_iid is not None)
                else None
            )
            cached = (_tpe.get_cache().get(self._post_edit_kind, cache_key)
                      if cache_key is not None else None)
            display_project = (
                _tpe.POST_EDIT_PREFIX + raw_project if cached else raw_project
            )
            # Synchronous render must visually match the async callback's
            # output — see _apply_post_edit_prefix_mr for the tag-replace
            # caveat.
            row_tags = (task_id, "post_edit") if cached else (task_id,)
            # en-US source-string count: render from cache when known (the
            # Hide-empty path may have just seeded it), else show a "…"
            # placeholder and queue an async fetch.
            with self._src_count_lock:
                src_count = self._src_count_cache.get(task_id)
            if src_count is None:
                src_count = t.get("_src_string_count")
            src_display = src_count if src_count is not None else "…"
            # JIRA + Title are resolved together. Prefer explicit task fields
            # when a newer backend supplies them, otherwise use the shared
            # GitLab-MR metadata cache populated by mr_jira.
            jira_key = _jira._normalize_key(raw_project, mr_iid)
            metadata_cached = (_jira.get_cached_metadata(*jira_key)
                               if jira_key is not None else None)
            jira_cached = t.get("_jira_ticket_id")
            if jira_cached is None:
                jira_cached = t.get("jira_ticket_id")
            if jira_cached is None and metadata_cached is not None:
                jira_cached = metadata_cached.jira_id
            if jira_cached is None and jira_key is not None:
                jira_cached = _jira.get_cached(*jira_key)

            title_cached = None
            for field in ("_jira_title", "jira_title", "jira_ticket_title",
                          "jira_summary"):
                if field in t:
                    title_cached = t.get(field)
                    break
            if title_cached is None and metadata_cached is not None:
                title_cached = metadata_cached.title
            if title_cached is None and jira_key is not None:
                title_cached = _jira.get_cached_title(*jira_key)

            needs_fetch = jira_cached is None or title_cached is None
            can_resolve = jira_key is not None and jira_fetchable
            jira_display = (
                (jira_cached or "—") if jira_cached is not None
                else ("…" if can_resolve else "—")
            )
            title_display = (
                (_single_line_title(title_cached) or "—")
                if title_cached is not None
                else ("…" if can_resolve else "—")
            )

            iid = self.mr_tree.insert(
                "", "end",
                iid=task_id or None,
                values=(
                    idx, display_project, mr_iid, jira_display, title_display,
                    t.get("release", ""), t.get("status", ""),
                    src_display,
                    avg if avg is not None else "—", created, duration,
                ),
                tags=row_tags,
            )
            normalized_title = _single_line_title(title_cached)
            if normalized_title:
                self._jira_titles_by_iid[iid] = normalized_title
            if jira_key is not None:
                self._jira_row_iids.setdefault(jira_key, []).append(iid)
                if (can_resolve and needs_fetch
                        and jira_key not in jira_seen):
                    jira_seen.add(jira_key)
                    jira_prefetch.append(jira_key)
            if task_id:
                self._mr_row_iid_by_task[task_id] = iid
                if src_count is None:
                    src_prefetch_ids.append(task_id)
                if cache_key is not None and cached is None:
                    prefetch_items.append((self._post_edit_kind, cache_key))
                    # Stash mr_iid → iid so the callback (which carries
                    # mr_iid in the key tuple) can find this row again.
                    self._mr_row_iid_by_task[f"mr:{mr_iid}"] = iid

        # Kick off the post-edit probe for newly-seen MRs. Each probe now
        # leads with a single cheap GitLab MR-commits call and only falls
        # through to the heavy ~1-2 MB dashboard-cases response when no fix
        # commit is found, so 8 workers (was 4) roughly halves the wall-clock
        # for the "✏️ Post-edited only" filter without hammering the platform.
        if prefetch_items:
            _tpe.prefetch_async(
                prefetch_items,
                on_result=self._on_post_edit_result,
                max_workers=8,
            )

        # Fill the en-US source-string counts asynchronously so the page
        # stays responsive; cells flip from "…" to the number as each
        # task's results fetch returns.
        if src_prefetch_ids:
            self._prefetch_src_counts(src_prefetch_ids)

        # Resolve JIRA + Title asynchronously from the same MR response.
        if jira_prefetch:
            self._prefetch_jira_metadata(jira_prefetch)

        # Treeview clips text but does not draw an ellipsis itself. Repaint
        # after geometry settles so Title uses the final elastic column width.
        self._schedule_title_ellipsis()

        # Keep an active sort applied across Load More appends: the new rows
        # were just inserted at the bottom in API order, so fold them into the
        # current order now (using whatever counts are already known). The
        # async prefetch will re-sort again once the remaining counts land.
        # On a replace load _mr_sort was reset to None above, so this is a
        # no-op there — fresh result sets render unsorted.
        if self._mr_sort is not None:
            self._apply_sort(*self._mr_sort)

        if append:
            # We just appended one more page worth of rows; track that
            # so Prev/Next/Load More can compute the correct boundary.
            self.mr_extra_pages += 1

        # Pagination — use filtered_total when filters are active
        effective_total = filtered_total
        # items_shown_max == upper bound on the items currently visible
        # (may be slightly above effective_total when the last page is
        # not full — we cap the display value below).
        items_shown_max = (self.mr_page + 1 + self.mr_extra_pages) * self.mr_page_size
        has_next = items_shown_max < effective_total
        has_prev = self.mr_page > 0
        has_more = has_next  # Load More uses the same boundary as Next

        if self.mr_extra_pages > 0:
            # Extended view: show "start - end / total" so the user can
            # tell at a glance how far they've scrolled through history.
            start_idx = self.mr_page * self.mr_page_size + 1
            end_idx = min(items_shown_max, effective_total)
            self.lbl_mr_page.configure(
                text=f"{start_idx} - {end_idx} / {effective_total}")
        else:
            total_pages = max(1, (effective_total + self.mr_page_size - 1) // self.mr_page_size)
            self.lbl_mr_page.configure(
                text=f"{self.mr_page + 1} / {total_pages}  ({effective_total})")

        has_rows = bool(self.mr_tree.get_children())
        if IS_MAC:
            self.btn_mr_prev.state(["!disabled"] if has_prev else ["disabled"])
            self.btn_mr_next.state(["!disabled"] if has_next else ["disabled"])
            self.btn_mr_load_more.state(["!disabled"] if has_more else ["disabled"])
            self.btn_mr_export.state(["!disabled"] if has_rows else ["disabled"])
            self.btn_mr_llm_qa.state(["!disabled"] if has_rows else ["disabled"])
        else:
            self.btn_mr_prev.configure(state="normal" if has_prev else "disabled")
            self.btn_mr_next.configure(state="normal" if has_next else "disabled")
            self.btn_mr_load_more.configure(state="normal" if has_more else "disabled")
            self.btn_mr_export.configure(state="normal" if has_rows else "disabled")
            self.btn_mr_llm_qa.configure(state="normal" if has_rows else "disabled")
        self.lbl_mr_status_bar.configure(text=self._t("status_ready"))

        # Re-apply the "✏️ only" view filter to the freshly rendered rows
        # (hides pending / non-edit rows; the prefetch above reveals the
        # post-edits as their checks confirm).
        if self.mr_post_edit_only_var.get():
            self._apply_post_edit_filter()

    # ------------------------------------------------------------------
    # Post-edit prefetch callback. The fetcher runs on a worker thread,
    # so we must marshal back to Tk via after() before touching widgets.
    # ------------------------------------------------------------------
    def _on_post_edit_result(self, kind, key, has_post_edit):
        if not has_post_edit:
            return
        # ``key`` is the (project_id, mr_iid) tuple we registered above;
        # ``mr_iid`` is what we use to look the row up. Support the bare-iid
        # legacy shape too in case some path skipped wiring project_id.
        if isinstance(key, (tuple, list)) and len(key) == 2:
            mr_iid = key[1]
        else:
            mr_iid = key
        if mr_iid is None:
            return
        try:
            self.mr_tree.after(
                0, self._apply_post_edit_prefix_mr, int(mr_iid),
            )
        except Exception:
            pass

    def _apply_post_edit_prefix_mr(self, mr_iid: int):
        iid = self._mr_row_iid_by_task.get(f"mr:{mr_iid}")
        if not iid:
            return
        try:
            vals = list(self.mr_tree.item(iid, "values"))
            current_tags = list(self.mr_tree.item(iid, "tags") or ())
        except tk.TclError:
            return
        if len(vals) < 2:
            return
        project = vals[1] or ""
        if project.startswith(_tpe.POST_EDIT_PREFIX):
            return
        vals[1] = _tpe.POST_EDIT_PREFIX + project
        # Append the "post_edit" tag — see scan_tasks._apply_post_edit_prefix
        # for the "tags is replaced, not appended" caveat.
        if "post_edit" not in current_tags:
            current_tags.append("post_edit")
        try:
            self.mr_tree.item(iid, values=vals, tags=tuple(current_tags))
        except tk.TclError:
            pass
        # If the "✏️ only" filter is active, this row just qualified — it was
        # detached as a pending/non-edit row, so reveal it now.
        if self.mr_post_edit_only_var.get():
            try:
                self.mr_tree.move(iid, "", "end")
                if self._mr_sort is not None:
                    self._apply_sort(*self._mr_sort)
                self._update_post_edit_filter_status()
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # "✏️ Post-edited only" client-side view filter.
    #
    # Post-edit status is resolved asynchronously by the ✏️ prefetch (a row
    # gains the gold ``post_edit`` tag once a fix is confirmed). This filter
    # therefore acts on the rows already loaded on the current page(s): with
    # the box checked, only confirmed post-edit rows stay attached; pending /
    # non-edit rows are detached and reappear as the prefetch confirms them
    # (see _apply_post_edit_prefix_mr). It does NOT re-query the server — use
    # Load More to pull older MRs, and the filter applies to them too.
    # ------------------------------------------------------------------
    def _on_post_edit_only_toggle(self):
        self._apply_post_edit_filter()

    def _apply_post_edit_filter(self):
        only = self.mr_post_edit_only_var.get()
        # Iterate task rows in insertion order; skip the "mr:<iid>" alias keys
        # that _on_tasks_loaded also stashes in the same dict. Detached items
        # stay in the dict and remain reachable via item()/move().
        for key, iid in list(self._mr_row_iid_by_task.items()):
            if isinstance(key, str) and key.startswith("mr:"):
                continue
            try:
                tags = self.mr_tree.item(iid, "tags") or ()
            except tk.TclError:
                continue
            is_post_edit = "post_edit" in tags
            try:
                if only and not is_post_edit:
                    self.mr_tree.detach(iid)
                else:
                    # Reattach at end; iterating in insertion order rebuilds
                    # the original row order among the visible rows.
                    self.mr_tree.move(iid, "", "end")
            except tk.TclError:
                continue
        if self._mr_sort is not None:
            self._apply_sort(*self._mr_sort)
        self._update_post_edit_filter_status()

    def _update_post_edit_filter_status(self):
        """Reflect the filtered view in the export button + status bar."""
        visible = self.mr_tree.get_children("")
        has_rows = bool(visible)
        if IS_MAC:
            self.btn_mr_export.state(["!disabled"] if has_rows else ["disabled"])
            self.btn_mr_llm_qa.state(["!disabled"] if has_rows else ["disabled"])
        else:
            self.btn_mr_export.configure(state="normal" if has_rows else "disabled")
            self.btn_mr_llm_qa.configure(state="normal" if has_rows else "disabled")
        if self.mr_post_edit_only_var.get():
            self.lbl_mr_status_bar.configure(
                text=self._t("mr_post_edit_filter_status").format(n=len(visible)))
        else:
            self.lbl_mr_status_bar.configure(text=self._t("status_ready"))

    def _on_tasks_error(self, err):
        self.mr_loading = False
        self._stop_loading_anim()
        self._set_controls_enabled(True)
        self.lbl_mr_status_bar.configure(text=f"⚠ {err[:60]}")

    # ------------------------------------------------------------------
    # en-US source-string count — async column fill.
    #
    # ``/tasks`` carries no string count, so the only source of truth is
    # each task's full results payload. We fetch those on worker threads
    # (capped at 4 — same politeness budget as the post-edit prefetch) and
    # marshal each count back to Tk via after(). Counts are cached by
    # task_id; a completed task's source-string count never changes, so the
    # cache makes paging and re-search effectively free.
    # ------------------------------------------------------------------
    def _prefetch_src_counts(self, task_ids):
        ids = [tid for tid in task_ids if tid]
        if not ids:
            return

        def _run():
            def _work(tid):
                with self._src_count_lock:
                    count = self._src_count_cache.get(tid)
                if count is None:
                    count = mr_api.count_mr_source_strings(
                        tid, **self._api_kw())
                    with self._src_count_lock:
                        self._src_count_cache[tid] = count
                try:
                    self.parent.after(0, self._apply_src_count, tid, count)
                except Exception:
                    pass

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(_work, ids))
            try:
                self.parent.after(0, self._on_src_counts_done)
            except Exception:
                pass

        threading.Thread(target=_run, name="mr-src-count-prefetch",
                         daemon=True).start()

    def _apply_src_count(self, task_id, count):
        """Replace one row's "…" placeholder with its real count. Runs on the
        Tk thread. The row may be gone (user paged / re-searched mid-fetch);
        a stale write is harmless because iid == task_id, so it can only ever
        land on the same task — guard the lookup and the set regardless."""
        iid = self._mr_row_iid_by_task.get(task_id)
        if not iid:
            return
        try:
            self.mr_tree.set(iid, "src_strings", count)
        except tk.TclError:
            pass

    def _on_src_counts_done(self):
        # Once the numbers have landed, re-apply an active source-string sort
        # so the final order reflects real workloads — the user may have
        # clicked the header while cells still read "…".
        if self._mr_sort and self._mr_sort[0] == "src_strings":
            self._apply_sort(*self._mr_sort)

    # ------------------------------------------------------------------
    # JIRA metadata prefetch — one GitLab MR-title fetch per distinct
    # (project_id, mr_iid), fanned back out to every matching row.
    # ------------------------------------------------------------------
    def _prefetch_jira_metadata(self, keys):
        if not keys:
            return

        def _run():
            def _work(key):
                metadata = _jira.fetch_jira_metadata(*key)
                try:
                    self.parent.after(
                        0, self._apply_jira_metadata, key, metadata)
                except Exception:
                    pass

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(_work, keys))
            try:
                self.parent.after(0, self._on_jira_prefetch_done)
            except Exception:
                pass

        threading.Thread(target=_run, name="mr-jira-prefetch",
                         daemon=True).start()

    def _apply_jira_metadata(self, key, metadata):
        """Paint JIRA + Title for every row of one MR on the Tk thread."""
        if metadata is None:
            # Close the late-failure race: a concurrent fetch may have filled
            # both caches after this worker began.
            metadata = _jira.get_cached_metadata(*key)
        jira = (metadata.jira_id if metadata is not None
                else (_jira.get_cached(*key) or ""))
        title = (metadata.title if metadata is not None
                 else (_jira.get_cached_title(*key) or ""))
        for iid in self._jira_row_iids.get(key, ()):
            try:
                self.mr_tree.set(iid, "jira", jira or "—")
                # Preserve an authoritative task-payload title when only its
                # missing JIRA ID required the fallback GitLab lookup.
                row_title = self._jira_titles_by_iid.get(iid) or title
                self._set_title_cell(iid, row_title)
            except tk.TclError:
                pass

    def _on_jira_prefetch_done(self):
        # If the user sorted a still-loading metadata column, fold the final
        # values into the requested order once all workers have returned.
        if self._mr_sort and self._mr_sort[0] in ("jira", "title"):
            self._apply_sort(*self._mr_sort)

    # ------------------------------------------------------------------
    # Elastic one-line Title rendering. ttk.Treeview clips overflowing text
    # but does not render an ellipsis, so the visible value is pixel-fitted
    # while the lossless value remains in _jira_titles_by_iid.
    # ------------------------------------------------------------------
    def _schedule_title_ellipsis(self, _event=None):
        after_id = getattr(self, "_title_resize_after_id", None)
        if after_id is not None:
            try:
                self.mr_tree.after_cancel(after_id)
            except Exception:
                pass
        try:
            self._title_resize_after_id = self.mr_tree.after(
                60, self._refresh_title_ellipsis)
        except Exception:
            self._title_resize_after_id = None

    def _refresh_title_ellipsis(self):
        self._title_resize_after_id = None
        self._hide_title_tooltip()
        for iid, title in list(self._jira_titles_by_iid.items()):
            try:
                self._set_title_cell(iid, title)
            except tk.TclError:
                # A page change may remove a row between scheduling and paint.
                self._jira_titles_by_iid.pop(iid, None)
                self._truncated_title_iids.discard(iid)

    def _set_title_cell(self, iid, full_title):
        title = _single_line_title(full_title)
        if not title:
            self._jira_titles_by_iid.pop(iid, None)
            self._truncated_title_iids.discard(iid)
            self.mr_tree.set(iid, "title", "—")
            return

        self._jira_titles_by_iid[iid] = title
        column_width = int(self.mr_tree.column("title", "width"))
        display, truncated = _ellipsize_text(
            title, max(0, column_width - 16), self._mr_title_font.measure)
        self.mr_tree.set(iid, "title", display)
        if truncated:
            self._truncated_title_iids.add(iid)
        else:
            self._truncated_title_iids.discard(iid)

    # ------------------------------------------------------------------
    # JIRA hyperlink + Title Tooltip interaction
    # ------------------------------------------------------------------
    def _jira_link_at(self, x, y):
        """Return the JIRA URL under a Treeview pointer position, if any."""
        if self.mr_tree.identify_region(x, y) != "cell":
            return ""
        jira_column = f"#{self._MR_COLUMNS.index('jira') + 1}"
        if self.mr_tree.identify_column(x) != jira_column:
            return ""
        iid = self.mr_tree.identify_row(y)
        if not iid:
            return ""
        try:
            return _jira.jira_browse_url(self.mr_tree.set(iid, "jira"))
        except tk.TclError:
            return ""

    def _title_tooltip_at(self, x, y):
        """Return ``(iid, full_title)`` only for a truncated Title cell."""
        if self.mr_tree.identify_region(x, y) != "cell":
            return None
        title_column = f"#{self._MR_COLUMNS.index('title') + 1}"
        if self.mr_tree.identify_column(x) != title_column:
            return None
        iid = self.mr_tree.identify_row(y)
        if not iid or iid not in self._truncated_title_iids:
            return None
        title = self._jira_titles_by_iid.get(iid, "")
        return (iid, title) if title else None

    def _update_title_tooltip_hover(self, event):
        target = self._title_tooltip_at(event.x, event.y)
        if target == getattr(self, "_title_tooltip_cell", None):
            self._title_tooltip_pointer = (
                getattr(event, "x_root", 0), getattr(event, "y_root", 0))
            return
        self._hide_title_tooltip()
        if target is None:
            return
        self._title_tooltip_cell = target
        self._title_tooltip_pointer = (
            getattr(event, "x_root", 0), getattr(event, "y_root", 0))
        try:
            self._title_tooltip_after_id = self.mr_tree.after(
                450, self._show_title_tooltip)
        except Exception:
            self._title_tooltip_after_id = None

    def _show_title_tooltip(self):
        self._title_tooltip_after_id = None
        target = getattr(self, "_title_tooltip_cell", None)
        if not target:
            return
        iid, title = target
        if (iid not in self._truncated_title_iids
                or self._jira_titles_by_iid.get(iid) != title):
            return

        tw = tk.Toplevel(self.mr_tree)
        tw.wm_overrideredirect(True)
        try:
            tw.wm_attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(
            tw,
            text=title,
            background="#1e2a44",
            foreground="#e4e7ef",
            relief="solid",
            borderwidth=1,
            font=(FONT_FAMILY, 9),
            justify="left",
            wraplength=650,
            padx=8,
            pady=5,
        ).pack()
        tw.update_idletasks()
        pointer_x, pointer_y = self._title_tooltip_pointer
        x = pointer_x + 12
        y = pointer_y + 18
        x = max(0, min(x, tw.winfo_screenwidth() - tw.winfo_reqwidth() - 8))
        y = max(0, min(y, tw.winfo_screenheight() - tw.winfo_reqheight() - 8))
        tw.wm_geometry(f"+{x}+{y}")
        self._title_tooltip_window = tw

    def _hide_title_tooltip(self):
        after_id = getattr(self, "_title_tooltip_after_id", None)
        if after_id is not None:
            try:
                self.mr_tree.after_cancel(after_id)
            except Exception:
                pass
        self._title_tooltip_after_id = None
        tip = getattr(self, "_title_tooltip_window", None)
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass
        self._title_tooltip_window = None
        self._title_tooltip_cell = None

    def _on_mr_tree_motion(self, event):
        cursor = "hand2" if self._jira_link_at(event.x, event.y) else ""
        try:
            self.mr_tree.configure(cursor=cursor)
        except tk.TclError:
            pass
        self._update_title_tooltip_hover(event)

    def _on_mr_tree_leave(self, _event):
        self._hide_title_tooltip()
        try:
            self.mr_tree.configure(cursor="")
        except tk.TclError:
            pass

    def _on_mr_tree_press(self, _event):
        self._hide_title_tooltip()

    def _on_mr_tree_click(self, event):
        # Also catches a user-dragged column separator: recalculate against the
        # newly selected Title width after the heading interaction finishes.
        self._schedule_title_ellipsis()
        url = self._jira_link_at(event.x, event.y)
        if not url:
            return None
        webbrowser.open_new_tab(url)
        return "break"

    # ------------------------------------------------------------------
    # Column sorting — click a header to reorder the visible rows.
    # ------------------------------------------------------------------
    def _sort_heading_text(self, col):
        """Heading label for ``col`` with a ▲/▼ marker when it's the active
        sort column. Driven off ``mr_col_*`` so it follows the UI language."""
        base = self._t(f"mr_col_{col}")
        if self._mr_sort and self._mr_sort[0] == col:
            return base + ("  ▼" if self._mr_sort[1] else "  ▲")
        return base

    def _refresh_sort_indicators(self):
        """Redraw every header so only the active column carries the marker."""
        for col in self._MR_COLUMNS:
            try:
                self.mr_tree.heading(col, text=self._sort_heading_text(col))
            except tk.TclError:
                pass

    def _sort_by(self, col):
        """Header-click handler. The first click on the source-string column
        shows the biggest workload first (descending — that's what "sort by
        workload" means in practice); the first click on any other column is
        ascending. Repeated clicks on the same column flip the direction."""
        if self._mr_sort and self._mr_sort[0] == col:
            descending = not self._mr_sort[1]
        else:
            descending = (col == "src_strings")
        self._apply_sort(col, descending)

    def _apply_sort(self, col, descending):
        rows = list(self.mr_tree.get_children(""))
        prev = self._mr_sort
        self._mr_sort = (col, descending)
        if rows:
            numeric = col in self._MR_NUMERIC_COLS
            def _value(iid):
                if col == "title":
                    return self._jira_titles_by_iid.get(
                        iid, self.mr_tree.set(iid, col))
                return self.mr_tree.set(iid, col)

            rows.sort(
                key=lambda iid: self._mr_sort_key(
                    _value(iid), numeric, descending),
                reverse=descending,
            )
            for pos, iid in enumerate(rows):
                self.mr_tree.move(iid, "", pos)
        if prev != self._mr_sort:
            self._refresh_sort_indicators()

    @staticmethod
    def _mr_sort_key(value, numeric, descending):
        """Sort key that keeps "missing" cells (—, …, blank) at the bottom in
        both directions. ``reverse=descending`` is applied by the caller, so
        the missing-rank flag is flipped for descending to survive the
        reversal."""
        s = ("" if value is None else str(value)).strip()
        missing = s in ("", "—", "…")
        missing_rank = (not missing) if descending else missing
        if numeric:
            try:
                primary = float(s)
            except ValueError:
                primary = float("-inf")
        else:
            primary = s.lower()
        return (missing_rank, primary)

    @staticmethod
    def _build_export_filename(ext, *, mr_iid="", id_tag="", type_tag="",
                               created="", export_date="", env_tag=""):
        """Compose the MR Pipeline export filename (HTML / Excel / JSON).

        The date segment must identify *which* translation run the file
        holds: the same Project/MR is re-translated at different times, so
        previously two such exports collided in name — only the export date
        was stamped, and that is identical for every same-day export. We
        instead stamp the task's Created time (``created``, e.g.
        ``"2026-06-17 14:42:26"`` → ``"2026-06-17_14-42-26"``), so per-run
        files stay distinct and human-recognizable down to the second.

        The no-selection "export all" aggregate spans many tasks and has no
        single Created time, so it falls back to ``export_date``.

        ``mr_iid`` is embedded as ``MR<iid>`` before the task-uuid prefix so
        the name reads at a glance; ``id_tag`` (uuid prefix or ``all_*``) and
        ``type_tag`` (``changes`` / ``all``) follow, then the date segment.
        """
        date_tag = sanitize_for_filename(created) or sanitize_for_filename(export_date)
        mr_tag = sanitize_for_filename(f"MR{mr_iid}") if mr_iid else ""
        parts = ["mr_pipeline"]
        if env_tag:
            parts.append(sanitize_for_filename(env_tag))
        if mr_tag:
            parts.append(mr_tag)
        parts.extend(seg for seg in (id_tag, type_tag, date_tag) if seg)
        return "_".join(parts) + ext

    def _on_export(self, llm_qa=False):
        """Export the selected MR (or all rows when nothing is selected).

        ``llm_qa=True`` is the "Send to LLM QA" path: it forces JSON + All
        Translations regardless of the radios and, on success, copies the LQA
        prompt to the clipboard and pops a how-to dialog (see _run_export).
        """
        sel = self.mr_tree.selection()
        mr_iid = ""
        mr_created = ""
        if sel:
            tags = self.mr_tree.item(sel[0], "tags")
            task_id = tags[0] if tags else None
            # Pull MR# and the task's Created time straight from the visible
            # row (no extra HTTP round-trip) so the export filename can be
            # stamped with both — see _build_export_filename. Indices are
            # resolved from _MR_COLUMNS so a future column reshuffle can't
            # silently point these reads at the wrong cell.
            values = self.mr_tree.item(sel[0], "values")
            if values:
                mr_col = self._MR_COLUMNS.index("mr")
                created_col = self._MR_COLUMNS.index("created")
                if len(values) > mr_col:
                    mr_iid = str(values[mr_col] or "")
                if len(values) > created_col:
                    mr_created = str(values[created_col] or "")
        else:
            task_id = None  # Export all tasks
        # Send to LLM QA always writes the full-translation JSON audit shape.
        fmt = "json" if llm_qa else self.mr_fmt_var.get()
        export_type = "translations" if llm_qa else self.mr_export_type_var.get()
        # Read the Advanced Filters state on the main thread (Tk widgets are
        # not thread-safe) and hand it to the worker.
        adv_state = self.adv_filter.get_state() if self.adv_filter else None
        # When nothing is selected ("export all"), inherit the panel's basic
        # filters so the export — and thus the Advanced-Filters content search
        # — is scoped to the same Project / Release / Status the list shows
        # (read here on the main thread). Date is not a list filter, so it is
        # intentionally not inherited.
        project_kw = self._mr_project_filter_kwargs()
        basic_filters = {
            "project_id": project_kw.get("project_id"),
            "project_ids": project_kw.get("project_ids"),
            "release": self.mr_release_var.get() or None,
            "status": self.mr_status_var.get() or None,
        }
        if IS_MAC:
            self.btn_mr_export.state(["disabled"])
            self.btn_mr_llm_qa.state(["disabled"])
        else:
            self.btn_mr_export.configure(state="disabled")
            self.btn_mr_llm_qa.configure(state="disabled")
        self.lbl_mr_status_bar.configure(text=self._t("status_exporting"))
        threading.Thread(target=self._run_export,
                         args=(task_id, fmt, export_type, mr_iid, adv_state,
                               basic_filters, mr_created),
                         kwargs={"llm_qa": llm_qa},
                         daemon=True).start()

    def _run_export(self, task_id, fmt, export_type="changes", mr_iid="",
                    adv_state=None, basic_filters=None, mr_created="",
                    llm_qa=False):
        try:
            if export_type == "changes":
                if not task_id:
                    raise ValueError("请先选择一个翻译任务以导出变更")
                # 自动关联 MR，汇总该 MR 全部 task 的翻译变更
                changes = mr_api.detect_mr_changes(task_id, **self._api_kw())
                results = {"translations": changes, "summary": {}}
                id_tag = task_id[:8]
                type_tag = "changes"
            else:
                if task_id:
                    results = mr_api.fetch_mr_results(task_id, **self._api_kw())
                    # fetch_mr_results doesn't include MR coordinates
                    # (project_id, mr_id) on the translations it returns,
                    # so the HTML report can't build the right
                    # /static/?project_id=…&mr_id=… URL on its own. Fetch
                    # the task detail and stamp them in. Best-effort:
                    # without this, the report falls back to the (wrong
                    # for MR) /static/legacy/tasks/<id> route.
                    try:
                        detail = mr_api.fetch_mr_task_detail(
                            task_id, **self._api_kw())
                        if detail and results.get("translations"):
                            mr_api.enrich_translations_with_task(
                                results["translations"], detail)
                    except Exception:
                        pass
                    id_tag = task_id[:8]
                else:
                    # "Export all" inherits the panel's basic filters so the
                    # exported set (and the Advanced-Filters content search over
                    # it) matches what the list shows. status defaults to
                    # "completed" (only completed tasks have results) unless the
                    # user explicitly picked another status.
                    bf = basic_filters or {}
                    ids = mr_api.normalize_project_ids(
                        bf.get("project_id"), bf.get("project_ids"))
                    results = mr_api.collect_all_mr_results(
                        project_id=bf.get("project_id"),
                        project_ids=bf.get("project_ids"),
                        release=bf.get("release"),
                        status=bf.get("status") or "completed",
                        **self._api_kw())
                    if not ids:
                        id_tag = "all_tasks"
                    elif len(ids) == 1:
                        proj_tag = sanitize_for_filename(ids[0])
                        id_tag = f"all_{proj_tag}" if proj_tag else "all_tasks"
                    else:
                        joined = sanitize_for_filename("+".join(ids))
                        id_tag = (f"all_{joined}" if joined
                                  else f"all_{len(ids)}projects")
                type_tag = "all"

            ext = {"xlsx": ".xlsx", "json": ".json"}.get(fmt, ".html")
            today = date.today().isoformat()
            # Stamp the selected task's Created time into the filename so
            # re-translations of the same MR neither collide nor look
            # identical — the export date alone is the same for every
            # same-day export. "Export all" (no selection) has no single
            # Created time and falls back to today's date.
            filename = self._build_export_filename(
                ext, mr_iid=mr_iid, id_tag=id_tag, type_tag=type_tag,
                created=mr_created, export_date=today,
                env_tag="" if self.env_key == "prod" else self.env_key)
            script_dir = os.path.dirname(os.path.abspath(__file__))
            filepath = os.path.join(script_dir, filename)
            created_note = f"created {mr_created}, " if mr_created else ""
            env_label = "" if self.env_key == "prod" else f" ({self.env_key})"
            label = (f"MR Pipeline{env_label} {id_tag} — {type_tag} "
                     f"({created_note}exported {today})")
            # Route the local bridge port + token into the report so its
            # Send-to-Tranzor button can reach the desktop GUI's HTTP bridge.
            bridge_info = self.app._bridge_info_for_export() if hasattr(self.app, "_bridge_info_for_export") else None
            # Advanced Filters (content-level) carried into the export: HTML
            # pre-fills + auto-applies; Excel/JSON pre-filter matching rows.
            # (adv_state was read on the main thread in _on_export.)
            # Capture the actual saved path so we can both display its basename
            # and reveal it in the OS file manager — otherwise the user sees
            # only "Export complete" with no clue where the JSON / Excel went.
            # 全量翻译 JSON 导出（非 changes）需要每个 key 100% 覆盖目标语言，
            # 启用 fill_missing 做缺失语言补齐；Changes 导出保持稀疏。
            saved = mr_api.save_mr_file(
                results, filepath, label, fmt, bridge_info=bridge_info,
                fill_missing=(export_type != "changes"),
                advanced_filter_state=adv_state,
                tranzor_url=self.base_url) or filepath
            basename = os.path.basename(saved)
            self.parent.after(0, lambda b=basename: self.lbl_mr_status_bar.configure(
                text=self._t("status_saved").format(filename=b)))
            # Non-HTML exports don't auto-open a browser tab, so the user has
            # no visual confirmation of the destination. Pop the file manager.
            if fmt != "html":
                self.parent.after(0, lambda p=saved: reveal_in_folder(p))
            # Send to LLM QA: JSON is out — now copy the LQA prompt to the
            # clipboard and tell the user to upload + paste in their LLM. Must
            # run on the main thread (Tk clipboard + dialog), hence after(0).
            if llm_qa:
                self.parent.after(0, lambda b=basename:
                    llm_qa_module.send_prompt_and_notify(self.parent, b, self.app.lang))
        except Exception as e:
            self.parent.after(0, lambda: self.lbl_mr_status_bar.configure(text=f"❌ {str(e)[:50]}"))
        finally:
            def _restore():
                if IS_MAC:
                    self.btn_mr_export.state(["!disabled"])
                    self.btn_mr_llm_qa.state(["!disabled"])
                else:
                    self.btn_mr_export.configure(state="normal")
                    self.btn_mr_llm_qa.configure(state="normal")
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
            projs = self._selected_mr_projects()
            rel = self.mr_release_var.get() or None
            if len(projs) > 1:
                parts = [
                    mr_api.fetch_dashboard_overview(
                        project_id=pid, release=rel, **self._api_kw())
                    for pid in projs
                ]
                data = mr_api.aggregate_dashboard_overviews(parts)
            else:
                data = mr_api.fetch_dashboard_overview(
                    project_id=(projs[0] if projs else None),
                    release=rel, **self._api_kw())
            self.parent.after(0, self._on_overview_loaded, data)
        except Exception as e:
            self.parent.after(0, self._on_overview_error, str(e))

    def _on_overview_loaded(self, data):
        self.mr_overview_loading = False
        self.lbl_mr_sidebar_status.configure(text="")
        self.mr_stat_labels["total"][1].configure(text=str(data.get("total_tasks", 0)))
        # Tranzor renamed `completed` → `completed_tasks` etc. on /dashboard/overview;
        # keep the old keys as fallback in case an older deployment is reached.
        self.mr_stat_labels["completed"][1].configure(text=str(data.get("completed_tasks", data.get("completed", 0))))
        self.mr_stat_labels["failed"][1].configure(text=str(data.get("failed_tasks", data.get("failed", 0))))
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
            recent = mr_api.fetch_recently_added_projects(**self._api_kw())
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
