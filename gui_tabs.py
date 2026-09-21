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
import mr_delivery as _delivery
import mr_jira as _jira
import quality_overview as qa
import task_post_edit as _tpe
import advanced_filter
# Aliased so the ``llm_qa`` boolean flag threaded through the export handlers
# doesn't shadow the module inside those methods.
import llm_qa as llm_qa_module
from export_gui import (
    FONT_FAMILY, IS_MAC, reveal_in_folder, sanitize_for_filename,
    export_output_dir,
)
from time_display import format_display_datetime
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


# MR Pipeline right sidebar: grow with the window so a maximized desktop
# no longer leaves the filter card empty while project paths clip at 280px.
# Floor / cap keep the 15-column table readable on the 1280px default size.
_MR_SIDEBAR_MIN_PX = 320
_MR_SIDEBAR_MAX_PX = 500
_MR_SIDEBAR_RATIO = 0.22
_MR_SIDEBAR_TABLE_RESERVE = 0.58
_MR_SIDEBAR_INNER_PAD_PX = 24
# Room for the longest relative-time string ("59 分钟前" / "12 个月前") plus
# a small gap so the project path wraps instead of colliding with the age.
_MR_RECENT_AGE_RESERVE_PX = 96


def _mr_sidebar_width(content_width):
    """Pixel width of the MR Pipeline right sidebar for a given pane width."""
    try:
        pane = max(0, int(content_width))
    except (TypeError, ValueError):
        pane = 0
    if pane <= 0:
        return _MR_SIDEBAR_MIN_PX
    target = int(pane * _MR_SIDEBAR_RATIO)
    hard_max = min(
        _MR_SIDEBAR_MAX_PX,
        max(180, pane - int(pane * _MR_SIDEBAR_TABLE_RESERVE)),
    )
    width = min(hard_max, max(_MR_SIDEBAR_MIN_PX, target))
    return max(180, width)


def _mr_sidebar_wraplength(sidebar_width, reserve=0):
    """``wraplength`` for labels inside the sidebar so text wraps, not clips."""
    try:
        width = int(sidebar_width)
    except (TypeError, ValueError):
        width = 0
    try:
        extra = int(reserve)
    except (TypeError, ValueError):
        extra = 0
    return max(80, width - _MR_SIDEBAR_INNER_PAD_PX - extra)


def _recent_project_tooltip(project_id, relative="", absolute=""):
    """Hover text for a Recently Added row: full path + relative + absolute."""
    parts = [str(project_id or "").strip()]
    if relative:
        parts.append(str(relative).strip())
    if absolute:
        parts.append(str(absolute).strip())
    return "\n".join(p for p in parts if p)


def _split_project_path_tokens(text):
    """Split a GitLab path, keeping ``/`` and ``-`` as their own tokens."""
    tokens = []
    buf = []
    for ch in str(text or ""):
        if ch in "/-":
            if buf:
                tokens.append("".join(buf))
                buf = []
            tokens.append(ch)
        else:
            buf.append(ch)
    if buf:
        tokens.append("".join(buf))
    return tokens


def _break_project_path(text, max_width, measure):
    """Insert newlines at ``/`` or ``-`` so each line fits ``max_width``.

    ttk.Label ``wraplength`` wraps on character width when there is no
    space, which is how ``copilot-platform/business`` became
    ``copilot-platfo`` / ``rm/busine``. Breaking on path separators keeps
    the full path visible and readable. ``measure`` is injected
    (normally ``tkinter.font.Font.measure``).
    """
    text = str(text or "")
    if not text:
        return ""
    try:
        available = max(0, int(max_width))
    except (TypeError, ValueError):
        available = 0
    if available <= 0:
        return text
    try:
        if measure(text) <= available:
            return text
    except Exception:
        return text
    lines = []
    current = ""
    for tok in _split_project_path_tokens(text):
        candidate = current + tok
        fits = True
        if current:
            try:
                fits = measure(candidate) <= available
            except Exception:
                fits = True
        if not fits:
            lines.append(current)
            current = tok
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines) if lines else text


def _mr_time_cells(task, *, tz=None):
    """Created / Ended / Duration cells for one MR Pipeline task.

    Tranzor ``GET /tasks`` exposes ``created_at`` and ``updated_at``; there
    is no separate ``completed_at``. Duration is already ``updated_at −
    created_at``, so Ended is that same ``updated_at``, formatted with the
    same UTC→local conversion as Created. Missing ``updated_at`` → ``—``.
    """
    created_raw = (task or {}).get("created_at") or ""
    updated_raw = (task or {}).get("updated_at") or ""
    created = format_display_datetime(created_raw, tz=tz)
    ended = format_display_datetime(updated_raw, empty="—", tz=tz)
    duration = ""
    try:
        if created_raw and updated_raw:
            c = datetime.fromisoformat(str(created_raw)[:19])
            u = datetime.fromisoformat(str(updated_raw)[:19])
            secs = int((u - c).total_seconds())
            if secs < 60:
                duration = f"{secs}s"
            else:
                duration = f"{secs // 60}m{secs % 60}s"
    except Exception:
        pass
    return created, ended, duration


# ============================================================
# MR Pipeline Tab
# ============================================================
class MRPipelineTab:
    """Builds and manages the MR Pipeline tab content."""

    # Single source of truth for the task-list table columns. ``src_strings``
    # (distinct en-US source-string count) sits between Status and Avg Score
    # so the two per-task metrics read together. Source MR and its live GitLab
    # state sit as a pair (``mr``, ``mr_status``); the follow-up translation
    # MR and *its* live GitLab state sit as the next pair (``delivery_mr``,
    # ``delivery_mr_status``). ``jira`` and ``title`` follow so same-origin
    # tasks still group together. These insertions keep the critical
    # positional reads elsewhere valid (project @ idx 1 for the post-edit
    # prefix, MR# @ idx 2 for the export filename).
    # ``ended`` is Tranzor ``updated_at`` (no separate completed_at exists).
    _MR_COLUMNS = ("idx", "project", "mr", "mr_status",
                   "delivery_mr", "delivery_mr_status",
                   "jira", "title",
                   "release", "status", "src_strings", "avg_score",
                   "created", "ended", "duration")
    # Columns whose cells sort numerically; everything else sorts as text.
    _MR_NUMERIC_COLS = frozenset({
        "idx", "mr", "delivery_mr", "src_strings", "avg_score"})

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
        self._recent_name_labels = []
        self._recent_name_paths = []
        self._recent_tooltips = []
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
        # tree iid → {project, source_iid, source_url, delivery_iid,
        # delivery_url}. Drives the clickable MR# / Trans MR# cells.
        self._mr_link_meta: dict[str, dict] = {}
        # (delivery_project, delivery_iid) → [row iids]. One GitLab fetch
        # paints Trans MR Status for every row that shares that follow-up MR.
        self._delivery_row_iids: dict[tuple[str, int], list[str]] = {}
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
        # ── Streaming scan state (the "Trans MR# exists" path) ──────────
        # That filter is a needle-in-haystack scan: ~1% of tasks qualify, so
        # a page costs thousands of task probes. It streams matches to the
        # table as it finds them instead of blocking on a full page.
        # Bumped per load; every after() callback carries the generation it
        # was queued under so a superseded scan cannot paint into a newer one.
        self._fetch_generation = 0
        # Where the running Trans MR# scan got to, so Load More resumes:
        # {"offset", "carry", "api_total", "matched", "scanned"}.
        self._scan_cursor = None
        # threading.Event while a streaming scan runs; Search doubles as Stop.
        self._scan_cancel = None
        # Replaces the "Loading…" text while a scan reports progress.
        self._scan_progress_text = None
        # Source MRs GitLab could not resolve (404 / no access) during this
        # scan. mr_jira deliberately never caches a failed lookup so transient
        # errors self-heal, but inside one scan the same dead MR recurs often
        # enough that re-asking dominates the runtime. Scan-scoped, so the
        # next Search still retries.
        self._delivery_probe_misses: set[tuple[str, int]] = set()
        self._delivery_probe_lock = threading.Lock()
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
        self._mr_content = content

        left = ttk.Frame(content, style="App.TFrame")
        left.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(
            content, style="App.TFrame", width=_MR_SIDEBAR_MIN_PX)
        right.pack(side="right", fill="y", padx=(8, 0))
        right.pack_propagate(False)
        self._mr_sidebar_frame = right
        content.bind("<Configure>", self._sync_mr_sidebar_width, add="+")

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

        # "Trans MR# exists" — keep only tasks whose translation went out as a
        # follow-up MR. Unlike the ✏️ filter this runs inside the fetch loop
        # (like Hide empty MRs), because a qualifying task is rare: filtering
        # only the rows already on screen would leave a handful of rows per
        # page. See _check_task_delivery_mr for the resolution order.
        self.mr_trans_mr_only_var = tk.BooleanVar(value=False)
        self.chk_mr_trans_mr_only = ttk.Checkbutton(
            r2, text="", variable=self.mr_trans_mr_only_var,
            style="Card.TCheckbutton", command=self._on_search)
        self.chk_mr_trans_mr_only.pack(side="left", padx=(12, 0))
        # "Trans MR# is open" — the narrower view of the same scan: only the
        # translation MRs still waiting to land. Implies "exists".
        self.mr_trans_mr_open_var = tk.BooleanVar(value=False)
        self.chk_mr_trans_mr_open = ttk.Checkbutton(
            r2, text="", variable=self.mr_trans_mr_open_var,
            style="Card.TCheckbutton", command=self._on_search)
        self.chk_mr_trans_mr_open.pack(side="left", padx=(12, 0))
        self._trans_mr_only_tip = None
        self._trans_mr_open_tip = None
        try:
            from export_gui import Tooltip as _Tooltip
            self._trans_mr_only_tip = _Tooltip(self.chk_mr_trans_mr_only, "")
            self._trans_mr_open_tip = _Tooltip(self.chk_mr_trans_mr_open, "")
        except Exception:
            pass

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

        # Source-only XLSX: unique Key / en-US Value / companion JSON
        # filename, one sheet named "{JIRA} MR!{iid}". Independent of the
        # Export Type / Output Format radios (always source xlsx).
        self.btn_mr_source_xlsx = self.app._create_button(
            action, text=self._t("mr_source_xlsx"),
            command=self._on_export_source_xlsx,
            style_name="InfoSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#0891b2", fg="#fff", padx=14, pady=4, state="disabled")
        self.btn_mr_source_xlsx.pack(side="left", padx=(8, 0))

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
        col_widths = {"idx": 35, "project": 140, "mr": 60, "mr_status": 90,
                      "delivery_mr": 120, "delivery_mr_status": 110,
                      "jira": 90, "title": 260, "release": 60, "status": 80,
                      "src_strings": 90, "avg_score": 70, "created": 185,
                      "ended": 185, "duration": 70}
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
        # JIRA / MR# / Trans MR# cells themselves the hyperlink hit targets.
        # The pointer changes only over a resolved, valid target; clicking
        # opens JIRA or the matching GitLab MR in the user's default browser.
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
        inner.pack(fill="both", expand=True, padx=10, pady=10)
        self._mr_sidebar_inner = inner

        self.lbl_mr_sidebar_title = ttk.Label(
            inner, text="", style="SummaryTitle.TLabel",
            wraplength=_mr_sidebar_wraplength(_MR_SIDEBAR_MIN_PX),
            justify="left", anchor="w")
        self.lbl_mr_sidebar_title.pack(anchor="w", fill="x")
        tk.Frame(inner, bg="#2a2a4a", height=1).pack(fill="x", pady=(6, 8))

        # 2×2 KPI grid — uses the extra sidebar width instead of four
        # stacked rows, and leaves more vertical room for project paths.
        stats = ttk.Frame(inner, style="Summary.TFrame")
        stats.pack(fill="x")
        stats.columnconfigure(0, weight=1)
        stats.columnconfigure(1, weight=1)
        self.mr_stat_labels = {}
        for i, key in enumerate(("total", "completed", "failed", "avg_score")):
            r, c = divmod(i, 2)
            cell = ttk.Frame(stats, style="Summary.TFrame")
            cell.grid(
                row=r, column=c, sticky="nsew",
                padx=(0, 8) if c == 0 else (8, 0), pady=(0, 6))
            val = ttk.Label(
                cell, text="—", style="SummaryCount.TLabel",
                font=(FONT_FAMILY, 16, "bold"))
            val.pack(anchor="w")
            lbl = ttk.Label(
                cell, text="", style="SummaryCountLabel.TLabel",
                wraplength=max(80, _mr_sidebar_wraplength(
                    _MR_SIDEBAR_MIN_PX) // 2),
                justify="left", anchor="w")
            lbl.pack(anchor="w")
            self.mr_stat_labels[key] = (lbl, val)

        tk.Frame(inner, bg="#2a2a4a", height=1).pack(fill="x", pady=(4, 8))
        self.lbl_mr_recent_projects_title = ttk.Label(
            inner, text="", style="SummarySection.TLabel",
            wraplength=_mr_sidebar_wraplength(_MR_SIDEBAR_MIN_PX),
            justify="left", anchor="w")
        self.lbl_mr_recent_projects_title.pack(anchor="w", fill="x", pady=(0, 4))

        # Status + refresh sit at the BOTTOM first so the project list
        # expands into every remaining pixel.
        self.btn_mr_sidebar_refresh = self.app._create_button(
            inner, text="", command=self._load_overview,
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 9), bg="#0f3460", fg="#ccc",
            padx=10, pady=3)
        self.btn_mr_sidebar_refresh.pack(side="bottom", anchor="e", pady=(6, 0))

        self.lbl_mr_sidebar_status = ttk.Label(
            inner, text="", style="SummaryStatus.TLabel",
            wraplength=_mr_sidebar_wraplength(_MR_SIDEBAR_MIN_PX),
            justify="left", anchor="w")
        self.lbl_mr_sidebar_status.pack(side="bottom", anchor="w", fill="x",
                                        pady=(6, 0))

        recent_frame = ttk.Frame(inner, style="Summary.TFrame")
        recent_frame.pack(fill="both", expand=True)
        self._recent_canvas = tk.Canvas(
            recent_frame, highlightthickness=0, bd=0,
            bg=self.app.BG_CARD, takefocus=0)
        recent_scroll = ttk.Scrollbar(
            recent_frame, orient="vertical",
            command=self._recent_canvas.yview)
        self._recent_canvas.configure(yscrollcommand=recent_scroll.set)
        recent_scroll.pack(side="right", fill="y")
        self._recent_canvas.pack(side="left", fill="both", expand=True)
        self._recent_inner = ttk.Frame(
            self._recent_canvas, style="Summary.TFrame")
        self._recent_inner_id = self._recent_canvas.create_window(
            (0, 0), window=self._recent_inner, anchor="nw")
        self._recent_inner.bind(
            "<Configure>", self._on_recent_inner_configure, add="+")
        self._recent_canvas.bind(
            "<Configure>", self._on_recent_canvas_configure, add="+")
        self._bind_recent_mousewheel(self._recent_canvas)
        self._bind_recent_mousewheel(self._recent_inner)
        self._recent_name_font = tkfont.Font(
            root=self._recent_canvas, font=(FONT_FAMILY, 10))
        self._last_recent_projects = []
        self._recent_name_labels = []
        self._recent_name_paths = []
        self._recent_tooltips = []

    def _sync_mr_sidebar_width(self, event=None):
        """Grow/shrink the sidebar with the pane so extra monitor width is
        spent on project paths instead of an empty filter card."""
        frame = getattr(self, "_mr_sidebar_frame", None)
        content = getattr(self, "_mr_content", None)
        if frame is None or content is None:
            return
        try:
            pane_w = int(content.winfo_width() or 0)
        except tk.TclError:
            return
        if pane_w < 200:
            return
        width = _mr_sidebar_width(pane_w)
        try:
            current = int(str(frame.cget("width") or 0))
        except (TypeError, ValueError, tk.TclError):
            current = 0
        if abs(current - width) >= 2:
            frame.configure(width=width)
        self._apply_sidebar_wraplengths(width)

    def _apply_sidebar_wraplengths(self, sidebar_width=None):
        """Keep every sidebar label wrapping inside the live pane width."""
        if sidebar_width is None:
            frame = getattr(self, "_mr_sidebar_frame", None)
            if frame is None:
                return
            try:
                sidebar_width = int(str(frame.cget("width") or 0))
            except (TypeError, ValueError, tk.TclError):
                sidebar_width = _MR_SIDEBAR_MIN_PX
        wrap = _mr_sidebar_wraplength(sidebar_width)
        for attr in (
                "lbl_mr_sidebar_title",
                "lbl_mr_recent_projects_title",
                "lbl_mr_sidebar_status"):
            lbl = getattr(self, attr, None)
            if lbl is not None:
                try:
                    lbl.configure(wraplength=wrap)
                except tk.TclError:
                    pass
        caption_wrap = max(80, wrap // 2)
        for pair in getattr(self, "mr_stat_labels", {}).values():
            try:
                pair[0].configure(wraplength=caption_wrap)
            except (tk.TclError, TypeError, IndexError):
                pass
        self._apply_recent_name_wraplengths()

    def _apply_recent_name_wraplengths(self, canvas_width=None):
        labels = getattr(self, "_recent_name_labels", None) or []
        if not labels:
            return
        if canvas_width is None:
            canvas = getattr(self, "_recent_canvas", None)
            if canvas is None:
                return
            try:
                canvas_width = int(canvas.winfo_width() or 0)
            except tk.TclError:
                canvas_width = 0
        if canvas_width < 40:
            frame = getattr(self, "_mr_sidebar_frame", None)
            try:
                canvas_width = int(str(frame.cget("width") or 0)) - (
                    _MR_SIDEBAR_INNER_PAD_PX + 18)
            except (TypeError, ValueError, tk.TclError, AttributeError):
                canvas_width = _MR_SIDEBAR_MIN_PX - _MR_SIDEBAR_INNER_PAD_PX
        wrap = max(80, int(canvas_width) - _MR_RECENT_AGE_RESERVE_PX)
        paths = getattr(self, "_recent_name_paths", None) or []
        font = getattr(self, "_recent_name_font", None)
        measure = font.measure if font is not None else len
        for i, lbl in enumerate(labels):
            try:
                pid = paths[i] if i < len(paths) else lbl.cget("text")
                lbl.configure(
                    wraplength=wrap,
                    text=_break_project_path(pid, wrap, measure))
            except tk.TclError:
                pass

    def _on_recent_inner_configure(self, _event=None):
        canvas = getattr(self, "_recent_canvas", None)
        if canvas is None:
            return
        try:
            bbox = canvas.bbox("all")
        except tk.TclError:
            return
        if bbox:
            canvas.configure(scrollregion=bbox)

    def _on_recent_canvas_configure(self, event):
        canvas = getattr(self, "_recent_canvas", None)
        inner_id = getattr(self, "_recent_inner_id", None)
        if canvas is None or inner_id is None:
            return
        try:
            canvas.itemconfigure(inner_id, width=event.width)
        except tk.TclError:
            return
        self._apply_recent_name_wraplengths(event.width)

    def _bind_recent_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self._on_recent_mousewheel, add="+")
        widget.bind("<Button-4>", self._on_recent_mousewheel, add="+")
        widget.bind("<Button-5>", self._on_recent_mousewheel, add="+")

    def _on_recent_mousewheel(self, event):
        canvas = getattr(self, "_recent_canvas", None)
        if canvas is None:
            return
        try:
            if getattr(event, "num", None) == 4:
                canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(1, "units")
            else:
                delta = int(getattr(event, "delta", 0) or 0)
                if delta:
                    canvas.yview_scroll(-1 if delta > 0 else 1, "units")
        except tk.TclError:
            return
        return "break"

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
        self.btn_mr_source_xlsx.configure(text=t("mr_source_xlsx"))
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
        self.chk_mr_trans_mr_only.configure(text=t("mr_trans_mr_only"))
        self.chk_mr_trans_mr_open.configure(text=t("mr_trans_mr_open"))
        if getattr(self, "_trans_mr_only_tip", None) is not None:
            self._trans_mr_only_tip.set_text(t("mr_trans_mr_only_tip"))
        if getattr(self, "_trans_mr_open_tip", None) is not None:
            self._trans_mr_open_tip.set_text(t("mr_trans_mr_open_tip"))
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
        # Re-render relative timestamps / placeholders in the new language
        if self._recent_projects_loading:
            self._show_recent_projects_loading()
        else:
            self._render_recent_projects(self._last_recent_projects)
        self._apply_sidebar_wraplengths()

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
        # While a streaming scan is running this button is the Stop button
        # (see _load_tasks / _set_scan_button). Keeping one control avoids a
        # second widget on an already busy filter row.
        scan = getattr(self, "_scan_cancel", None)
        if scan is not None:
            scan.set()
            return
        # An explicit re-query means "give me fresh data" — drop stale ✏️
        # answers so a Language Lead's just-made fixes surface (see
        # _invalidate_post_edit_cache).
        self._invalidate_post_edit_cache()
        self.mr_page = 0
        self._load_tasks()

    def _set_scan_button(self, scanning):
        """Flip the Search button between Search and Stop."""
        try:
            self.btn_mr_search.configure(
                text=self._t("mr_stop_scan" if scanning else "mr_search"))
        except Exception:
            pass

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
            or self.mr_trans_mr_only_var.get()
            or self.mr_trans_mr_open_var.get()
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
        # Supersede anything a previous load still has queued on the Tk loop.
        self._fetch_generation += 1
        self._scan_progress_text = None
        self._reset_scan_scope_if_new()
        # Show prominent loading overlay in the data grid area
        self.mr_loading_overlay.configure(text=self._t("status_loading") + "...")
        self.mr_loading_overlay.place(relx=0.5, rely=0.4, anchor="center")
        # Disable interactive controls while loading
        self._set_controls_enabled(False)
        # Start animated dots in status bar
        self._loading_dot_count = 0
        self._animate_loading()
        threading.Thread(target=self._fetch_tasks, daemon=True).start()

    def _reset_scan_scope_if_new(self) -> bool:
        """Drop the scan cursor and dead-MR memo unless this is a Load More.

        Both are scoped to one Trans MR# scan. A Load More continues that same
        scan, so it keeps them and resumes; anything else (Search, Reset, a
        page jump) starts over, which also means retrying the source MRs
        GitLab could not resolve last time.

        Returns True when the scope was reset, for tests and callers that
        want to know which of the two happened.
        """
        if self._pending_append:
            return False
        self._scan_cursor = None
        self._delivery_probe_misses = set()
        return True

    def _animate_loading(self):
        """Cycle dots in both status bar and overlay: Loading. → Loading.. → Loading..."""
        if not self.mr_loading:
            return
        self._loading_dot_count = (self._loading_dot_count % 3) + 1
        dots = "." * self._loading_dot_count
        # A streaming scan reports real progress; show that instead of a
        # bare "Loading" the user cannot read anything into.
        base = self._scan_progress_text or self._t("status_loading")
        self.lbl_mr_status_bar.configure(text=f"{base}{dots}")
        self.mr_loading_overlay.configure(text=f"{base}{dots}")
        self._loading_anim_id = self.parent.after(500, self._animate_loading)

    def _stop_loading_anim(self):
        if self._loading_anim_id is not None:
            self.parent.after_cancel(self._loading_anim_id)
            self._loading_anim_id = None
        self.mr_loading_overlay.place_forget()

    def _mr_export_buttons(self):
        return (self.btn_mr_export, self.btn_mr_llm_qa, self.btn_mr_source_xlsx)

    def _set_mr_export_buttons_enabled(self, enabled):
        if IS_MAC:
            flag = ["!disabled"] if enabled else ["disabled"]
            for btn in self._mr_export_buttons():
                btn.state(flag)
        else:
            state = "normal" if enabled else "disabled"
            for btn in self._mr_export_buttons():
                btn.configure(state=state)

    def _set_controls_enabled(self, enabled):
        self._set_mr_export_buttons_enabled(enabled)
        if IS_MAC:
            flag = ["!disabled"] if enabled else ["disabled"]
            self.btn_mr_prev.state(flag)
            self.btn_mr_next.state(flag)
            self.btn_mr_load_more.state(flag)
        else:
            state = "normal" if enabled else "disabled"
            self.btn_mr_prev.configure(state=state)
            self.btn_mr_next.configure(state=state)
            self.btn_mr_load_more.configure(state=state)

    @staticmethod
    def _cannot_have_trans_mr(task) -> bool:
        """True when the task payload alone rules a Trans MR out.

        A ``skipped`` task never ran translation, so it produced neither
        translations nor an import MR. 85% of the pipeline's history is
        skipped tasks, and probing one costs a full results fetch — the
        status field answers for free.
        """
        return str((task or {}).get("status") or "").lower() == "skipped"

    def _warm_delivery_probe(self, tasks):
        """Resolve each distinct source MR once before the per-task probe.

        :meth:`_check_task_delivery_mr` runs per task, but tasks routinely
        share a source MR — one MR triggers a task per language. Pointing 8
        workers straight at the task list makes them all miss the same cache
        key simultaneously, and mr_jira deliberately never caches a *failed*
        lookup, so an MR GitLab cannot resolve (404 / no access) is re-fetched
        on every encounter. Measured on a real page: 62 task probes over only
        35 distinct MRs, 32 of those calls spent on 15 dead MRs.

        Warming distinct keys first collapses both into one call each; the
        per-task probe then runs against warm caches and keeps its task-level
        precision (``task_digest`` still picks the right MR when one source
        was translated more than once).
        """
        keys, seen = [], set()
        for t in tasks:
            project = str(t.get("project_id") or "").strip()
            src = _delivery.parse_mr_iid(t.get("merge_request_iid"))
            if not project or src is None:
                continue
            key = (project, src)
            if key in seen:
                continue
            seen.add(key)
            if self._probe_is_dead(key) or _jira.get_cached_state(*key) is not None:
                continue
            keys.append(key)
        if not keys:
            return

        def _warm(key):
            try:
                resolved = _jira.fetch_jira_metadata(*key)
            except Exception:
                resolved = None
            if resolved is None:
                self._probe_mark_dead(key)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_warm, keys))

    def _probe_is_dead(self, key) -> bool:
        """True when this scan already found that source MR unresolvable."""
        lock = getattr(self, "_delivery_probe_lock", None)
        if lock is None:
            return False
        with lock:
            return key in self._delivery_probe_misses

    def _probe_mark_dead(self, key):
        lock = getattr(self, "_delivery_probe_lock", None)
        if lock is None:
            return
        with lock:
            self._delivery_probe_misses.add(key)

    def _check_task_delivery_mr(self, t, want_open=False):
        """Resolve a task's translation MR, and optionally whether it is open.

        ``_has_delivery_mr`` answers "Trans MR# exists"; ``_trans_mr_open``
        answers the narrower "Trans MR# is open". The open state is only
        resolved when asked for, because it costs one extra GitLab call —
        affordable precisely because it runs on the ~1% of scanned tasks that
        got this far.
        """
        self._resolve_task_delivery_mr(t)
        t["_trans_mr_state"] = ""
        t["_trans_mr_open"] = False
        if want_open and t.get("_has_delivery_mr"):
            state = self._resolve_trans_mr_state(t)
            t["_trans_mr_state"] = state
            t["_trans_mr_open"] = state.lower() == "opened"

    def _resolve_trans_mr_state(self, t) -> str:
        """Live GitLab state of the Trans MR this task's row will show.

        The Trans MR# cell follows the fix MR when there is one and the import
        MR otherwise (:func:`mr_delivery.current_trans_mr_iid`), so the filter
        has to ask about the same one. Force-refresh rather than reusing the
        title search's copy of ``state``: that search is cached, and an
        "is open" filter driven by a value that may be minutes stale would
        list MRs that already merged — the exact bug #202 fixed in the column.
        Seeding the state cache here also lets the column paint without its
        own round trip.
        """
        current = t.get("_fix_ref") or t.get("_delivery_ref")
        if current is None:
            return ""
        try:
            metadata = _jira.fetch_jira_metadata(
                current.project_id, current.iid, force_refresh=True)
        except Exception:
            return ""
        return "" if metadata is None else str(metadata.state or "")

    def _resolve_task_delivery_mr(self, t):
        """Resolve whether one task has a translation (Trans) MR.

        Runs only on the "Trans MR# exists" path, on the same worker pool as
        :meth:`_check_task_translations`, and mutates the task in place:
        ``_has_delivery_mr`` drives the filter, ``_delivery_ref`` lets the
        render paint Trans MR# straight away instead of resolving it a second
        time asynchronously. Never raises — an unresolvable task is treated as
        having no translation MR rather than failing the whole page.

        Resolution mirrors what the table itself does, cheapest first:

        1. the task payload (``delivery_mr_iid`` / ``import_mr_url``);
        2. the source MR's state — IMPORT only opens a follow-up MR once the
           source MR has merged, so a non-merged source is a free "no";
        3. a GitLab title search for ``MR!{source_iid}``.

        Steps 2 and 3 are cached process-wide (mr_jira / GitLabClient), so
        tasks sharing a source MR cost one round trip between them.
        """
        t["_fix_ref"] = None
        ref = _delivery.delivery_from_task(t)
        if ref is not None:
            t["_delivery_ref"] = ref
            t["_has_delivery_mr"] = True
            return
        t["_delivery_ref"] = None
        t["_has_delivery_mr"] = False
        project = str(t.get("project_id") or "").strip()
        source_iid = _delivery.parse_mr_iid(t.get("merge_request_iid"))
        if not project or source_iid is None:
            return
        if self._probe_is_dead((project, source_iid)):
            return
        try:
            # Not force_refresh: this is a coarse "has it merged yet" gate,
            # and it seeds the JIRA / Title / MR Status caches the render
            # reads a moment later. The live state refresh still happens
            # per Search (see _claim_delivery_status_refresh).
            metadata = _jira.fetch_jira_metadata(project, source_iid)
            if metadata is None or str(metadata.state).lower() != "merged":
                return
            import_ref, fix_ref = _delivery.find_follow_up_mrs(
                project, source_iid, task_id=t.get("task_id"))
        except Exception:
            return
        # Either one alone still renders a Trans MR# — a fix MR can outlive
        # an import MR the search no longer matches.
        t["_delivery_ref"] = import_ref or fix_ref
        t["_fix_ref"] = fix_ref if (fix_ref is not None
                                    and fix_ref is not t["_delivery_ref"]) else None
        t["_has_delivery_mr"] = t["_delivery_ref"] is not None

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
            # Seed the shared cache only for finished tasks — a running
            # task's results are a partial snapshot and would otherwise be
            # pinned for the session (see _resolve_src_count).
            if mr_api.is_terminal_task_status(t.get("status")):
                with self._src_count_lock:
                    self._src_count_cache[tid] = src
            if trs and t.get("average_score") is None:
                scores = [tr.get("score") for tr in trs if tr.get("score") is not None]
                if scores:
                    t["average_score"] = round(sum(scores) / len(scores), 2)
        except Exception:
            t["_translations_count"] = 0

    def _fetch_tasks(self):
        gen = self._fetch_generation
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
            trans_mr_open = self.mr_trans_mr_open_var.get()
            trans_mr_only = self.mr_trans_mr_only_var.get() or trans_mr_open
            if trans_mr_only and not _jira.can_fetch():
                raise RuntimeError(self._t("mr_trans_mr_token_required"))
            # Only the Trans MR# path streams: it is the one filter whose
            # hit rate (~1%) makes a page cost thousands of probes. Every
            # other path fills a page in one or two batches, where streaming
            # would just add flicker.
            if trans_mr_only:
                self._scan_cancel = threading.Event()
                self.parent.after(0, self._set_scan_button, True)
            matching_mr_iids = set()
            if mr_iid_filter:
                expand_projects = (
                    list(proj_set) if proj_set
                    else ([proj] if proj else []))
                matching_mr_iids = _delivery.expand_mr_iid_filter(
                    mr_iid_filter, project_ids=expand_projects)

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
                    if (mr_iid_filter and not _delivery.task_matches_mr_iid(
                            detail, matching_mr_iids)):
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
                    if trans_mr_only:
                        self._check_task_delivery_mr(
                            detail, want_open=trans_mr_open)
                        if not detail.get("_has_delivery_mr"):
                            detail = None
                        elif trans_mr_open and not detail.get("_trans_mr_open"):
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
                hide_empty or trans_mr_only
                or bool(mr_iid_filter) or bool(jira_filter))

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

                # Load More on a Trans MR# scan resumes where the last one
                # stopped. Without this it restarts at offset 0 and re-scans
                # every task it already rejected, only to throw the first
                # page's matches away via skip_count — so page 2 costs page 1
                # plus page 2, page 3 costs 1+2+3, and so on.
                cursor = self._scan_cursor if (append and trans_mr_only) else None
                carry = []
                if cursor is not None:
                    offset = cursor["offset"]
                    skip_count = 0
                    api_total = cursor["api_total"]
                    total_matched = cursor["matched"]
                    total_scanned = cursor["scanned"]
                    carry = list(cursor["carry"])

                # Streaming bookkeeping: rows are handed to the table batch
                # by batch, so track where the next chunk's "#" column starts
                # and whether the first chunk has replaced the old rows yet.
                stream_offset = base_offset
                streamed_any = False
                cancelled = False

                # Matches the previous scan found beyond its page boundary are
                # already paid for — show them before asking the API again.
                if carry:
                    chunk, carry = carry[:target], carry[target:]
                    collected.extend(chunk)
                    self.parent.after(
                        0, self._on_tasks_loaded, api_total, chunk,
                        max(total_matched, len(collected)),
                        append, stream_offset, gen)
                    stream_offset += len(chunk)
                    streamed_any = True

                while len(collected) < target:
                    if self._scan_cancel is not None and self._scan_cancel.is_set():
                        cancelled = True
                        break
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

                    # MR# client-side filter first (cheap, no API call).
                    # Matches the source iid *or* the follow-up translation MR.
                    if mr_iid_filter:
                        batch = [t for t in batch
                                 if _delivery.task_matches_mr_iid(
                                     t, matching_mr_iids)]

                    # A Trans MR# scan can rule most of the batch out from
                    # the payload alone, before paying for a results fetch.
                    if trans_mr_only:
                        for t in batch:
                            if self._cannot_have_trans_mr(t):
                                t["_translations_count"] = 0
                                t["_has_delivery_mr"] = False
                                t["_trans_mr_open"] = False

                    # Parallel check translation counts (4x faster than sequential)
                    if hide_empty and batch:
                        countable = [t for t in batch
                                     if "_translations_count" not in t]
                        if countable:
                            with ThreadPoolExecutor(max_workers=4) as pool:
                                list(pool.map(
                                    self._check_task_translations, countable))

                    # Resolve Trans MR# for what survives the cheaper filters
                    # — no point asking GitLab about a task Hide empty MRs is
                    # about to drop. Mostly GitLab round trips rather than
                    # platform ones, and heavily cache-served, so this takes
                    # the wider pool the ✏️ probe uses rather than the 4 above.
                    if trans_mr_only and batch:
                        pending = [
                            t for t in batch
                            if not self._cannot_have_trans_mr(t)
                            and (not hide_empty
                                 or t.get("_translations_count", 0) > 0)
                        ]
                        if pending:
                            self._warm_delivery_probe(pending)
                            with ThreadPoolExecutor(max_workers=8) as pool:
                                list(pool.map(
                                    lambda task: self._check_task_delivery_mr(
                                        task, want_open=trans_mr_open),
                                    pending))

                    chunk = []
                    for t in batch:
                        # Hide empty MRs: use pre-fetched count from parallel check
                        if hide_empty:
                            if t.get("_translations_count", 0) == 0:
                                continue
                        # Trans MR# exists / is open: pre-resolved just above.
                        if trans_mr_only and not t.get("_has_delivery_mr"):
                            continue
                        if trans_mr_open and not t.get("_trans_mr_open"):
                            continue

                        total_matched += 1

                        # Pagination: skip items for previous pages
                        if skip_count > 0:
                            skip_count -= 1
                            continue

                        if len(collected) < target:
                            collected.append(t)
                            chunk.append(t)
                        elif trans_mr_only:
                            # Already paid for; hand it to the next Load More
                            # instead of re-finding it (see _scan_cursor).
                            carry.append(t)

                    offset += batch_size

                    if trans_mr_only:
                        # Hand this batch's matches to the table now. The
                        # first chunk replaces the previous result set; the
                        # rest extend it, all forming one page (see the
                        # ``streaming`` branch of _on_tasks_loaded).
                        if chunk:
                            self.parent.after(
                                0, self._on_tasks_loaded, api_total, chunk,
                                max(total_matched, len(collected)),
                                streamed_any or append, stream_offset, gen)
                            stream_offset += len(chunk)
                            streamed_any = True
                        self.parent.after(
                            0, self._on_scan_progress, gen,
                            total_scanned, api_total, total_matched)

                    # The while condition stops us once the page is full.
                    if offset >= api_total:
                        break

                # Estimate total matches from scanned portion
                if total_scanned > 0 and total_scanned < api_total:
                    estimated_total = int(total_matched * api_total / total_scanned)
                else:
                    estimated_total = total_matched

                if trans_mr_only:
                    # Remember where to pick up, plus the matches already
                    # found past this page, so Load More doesn't re-scan.
                    self._scan_cursor = {
                        "offset": offset, "carry": carry,
                        "api_total": api_total,
                        "matched": total_matched, "scanned": total_scanned,
                    }
                    # Rows are already on screen; just close the scan out.
                    self.parent.after(
                        0, self._on_scan_done, gen, api_total, estimated_total,
                        total_scanned, total_matched, cancelled,
                        append, streamed_any)
                else:
                    self.parent.after(0, self._on_tasks_loaded,
                                      api_total, collected, estimated_total,
                                      append, base_offset, gen)
        except Exception as e:
            self._scan_cancel = None
            self.parent.after(0, self._set_scan_button, False)
            self.parent.after(0, self._on_tasks_error, str(e))

    def _on_tasks_loaded(self, api_total, tasks, filtered_total,
                          append=False, base_offset=0, gen=None):
        # A streaming scan calls this once per batch while still running, so
        # the "load finished" bookkeeping moves to _on_scan_done.
        streaming = getattr(self, "_scan_cancel", None) is not None
        if gen is not None and gen != self._fetch_generation:
            return  # superseded by a newer load
        if not streaming:
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
            self._mr_link_meta = {}
            self._delivery_row_iids = {}
            # Every follow-up MR must be re-verified against GitLab on an
            # explicit Search/Refresh (see _claim_delivery_status_refresh).
            self._delivery_status_refreshed = set()
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
        # (project_id, mr_iid) keys to refresh from GitLab — one fetch
        # supplies JIRA, Title and live MR Status to every row of that MR.
        # Deduped because the same MR routinely triggers several tasks.
        jira_prefetch: list[tuple[str, int]] = []
        jira_seen: set[tuple[str, int]] = set()
        # Distinct follow-up MRs whose live GitLab state fills Trans MR Status.
        delivery_status_prefetch: list[tuple[str, int]] = []
        # Resolved once per repaint: when GitLab is unreachable (no token)
        # the JIRA / MR Status cells render "—" up front instead of a "…"
        # spinner that would never resolve.
        jira_fetchable = _jira.can_fetch()

        for i, t in enumerate(tasks):
            idx = base_offset + i + 1
            created, ended, duration = _mr_time_cells(t)

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

            state_cached = None
            if jira_key is not None:
                state_cached = _jira.get_cached_state(*jira_key)

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
            # Show last-known GitLab state immediately, then refresh it
            # from GitLab so Search/Refresh is live (opened → merged).
            mr_status_display = (
                (_jira.display_mr_state(state_cached) or "—")
                if state_cached is not None
                else ("…" if can_resolve else "—")
            )
            # ``_delivery_ref`` is present when the "Trans MR# exists" filter
            # already resolved this task — reuse it so Trans MR# paints with
            # the row instead of flickering through "…".
            delivery = t.get("_delivery_ref") or _delivery.delivery_from_task(t)
            delivery_key = None
            if delivery is not None:
                delivery_display = delivery.iid
                delivery_key = _jira._normalize_key(
                    delivery.project_id, delivery.iid)
                delivery_state = (
                    _jira.get_cached_state(*delivery_key)
                    if delivery_key is not None else None)
                if delivery_state is not None:
                    delivery_status_display = (
                        _jira.display_mr_state(delivery_state) or "—")
                elif jira_fetchable:
                    delivery_status_display = "…"
                else:
                    delivery_status_display = "—"
            elif can_resolve:
                delivery_display = "…"
                delivery_status_display = "…"
            else:
                delivery_display = "—"
                delivery_status_display = "—"
            source_url = (
                str(t.get("mr_link") or "").strip()
                or _delivery.gitlab_mr_url(raw_project, mr_iid)
            )
            delivery_url = delivery.url if delivery is not None else ""

            iid = self.mr_tree.insert(
                "", "end",
                iid=task_id or None,
                values=(
                    idx, display_project, mr_iid, mr_status_display,
                    delivery_display, delivery_status_display,
                    jira_display, title_display,
                    t.get("release", ""), t.get("status", ""),
                    src_display,
                    avg if avg is not None else "—",
                    created, ended, duration,
                ),
                tags=row_tags,
            )
            self._mr_link_meta[iid] = {
                "project": raw_project,
                "source_iid": _delivery.parse_mr_iid(mr_iid),
                "source_url": source_url,
                "delivery_iid": delivery.iid if delivery is not None else None,
                "delivery_url": delivery_url,
                "task_id": task_id,
            }
            if delivery_key is not None:
                self._delivery_row_iids.setdefault(delivery_key, []).append(iid)
                if self._claim_delivery_status_refresh(delivery_key):
                    delivery_status_prefetch.append(delivery_key)
            normalized_title = _single_line_title(title_cached)
            if normalized_title:
                self._jira_titles_by_iid[iid] = normalized_title
            if jira_key is not None:
                self._jira_row_iids.setdefault(jira_key, []).append(iid)
                # Always re-fetch on this page so MR Status is current;
                # JIRA / Title reuse the same GitLab payload.
                if can_resolve and jira_key not in jira_seen:
                    jira_seen.add(jira_key)
                    jira_prefetch.append(jira_key)
            if task_id:
                self._mr_row_iid_by_task[task_id] = iid
                if src_count is None:
                    src_prefetch_ids.append((task_id, t.get("status")))
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

        # Resolve JIRA + Title + live MR Status from the same GitLab MR
        # response. force_refresh so a previously-cached "opened" cannot
        # outlive the MR actually merging.
        if jira_prefetch:
            self._prefetch_jira_metadata(jira_prefetch)
        if delivery_status_prefetch:
            self._prefetch_delivery_status(delivery_status_prefetch)

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

        if append and not streaming:
            # We just appended one more page worth of rows; track that
            # so Prev/Next/Load More can compute the correct boundary.
            # Streaming chunks all build a single page — _on_scan_done
            # bumps the counter once for the whole scan instead.
            self.mr_extra_pages += 1

        self._refresh_pagination_controls(filtered_total)
        if not streaming:
            self.lbl_mr_status_bar.configure(text=self._t("status_ready"))

        # Re-apply the "✏️ only" view filter to the freshly rendered rows
        # (hides pending / non-edit rows; the prefetch above reveals the
        # post-edits as their checks confirm).
        if self.mr_post_edit_only_var.get():
            self._apply_post_edit_filter()

    def _refresh_pagination_controls(self, filtered_total):
        """Repaint the page label, Prev/Next/Load More and the export buttons.

        Shared by the one-shot render and the streaming scan's finalizer, so
        both agree on the page boundary maths.
        """
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
        else:
            self.btn_mr_prev.configure(state="normal" if has_prev else "disabled")
            self.btn_mr_next.configure(state="normal" if has_next else "disabled")
            self.btn_mr_load_more.configure(state="normal" if has_more else "disabled")
        self._set_mr_export_buttons_enabled(has_rows)

    # ------------------------------------------------------------------
    # Streaming scan callbacks ("Trans MR# exists"). Both run on the Tk
    # thread via after() and drop anything a superseded load queued.
    # ------------------------------------------------------------------
    def _on_scan_progress(self, gen, scanned, api_total, matched):
        """Report scan progress in the status bar and loading overlay."""
        if gen != self._fetch_generation:
            return
        self._scan_progress_text = self._t("mr_scan_progress").format(
            scanned=scanned, total=api_total, matched=matched)
        # _animate_loading repaints both surfaces on its next tick; paint now
        # so progress appears immediately rather than up to 500ms later.
        try:
            self.lbl_mr_status_bar.configure(text=self._scan_progress_text)
            self.mr_loading_overlay.configure(text=self._scan_progress_text)
        except tk.TclError:
            pass

    def _on_scan_done(self, gen, api_total, filtered_total, scanned, matched,
                      cancelled, append, streamed_any):
        """Close out a streaming scan: stop the spinner, restore Search."""
        self._scan_cancel = None
        self._set_scan_button(False)
        if gen != self._fetch_generation:
            return
        self.mr_loading = False
        self._scan_progress_text = None
        self._stop_loading_anim()
        self._set_controls_enabled(True)
        self.mr_total = api_total
        self.mr_filtered_total = filtered_total
        if append and streamed_any:
            # The whole scan appended one page worth of rows, however many
            # batches it took (see the streaming branch of _on_tasks_loaded).
            self.mr_extra_pages += 1
        if not streamed_any and not append:
            # Scanned and found nothing — clear whatever the previous search
            # left on screen rather than leaving stale rows under a "0" count.
            self._on_tasks_loaded(api_total, [], filtered_total, False, 0, gen)
        self._refresh_pagination_controls(filtered_total)
        self.lbl_mr_status_bar.configure(
            text=self._t("mr_scan_stopped" if cancelled else "mr_scan_done")
            .format(scanned=scanned, matched=matched))

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
        self._set_mr_export_buttons_enabled(has_rows)
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
    def _resolve_src_count(self, task_id, status=None):
        """Cached en-US count for *task_id*, fetching it when unknown.

        Only *final* answers are cached: a count for a task whose
        ``status`` is terminal (completed / failed / cancelled). A running
        task's results are a partial snapshot, so its number is shown but
        not cached — the next render re-asks. ``None`` (fetch failed) is
        never cached either; the cell shows "—" and Refresh retries. This
        is the same sticky-0 guard as ``ScanTasksTab._resolve_src_count``.
        """
        with self._src_count_lock:
            count = self._src_count_cache.get(task_id)
        if count is not None:
            return count
        count = mr_api.count_mr_source_strings_or_none(
            task_id, **self._api_kw())
        if count is not None and mr_api.is_terminal_task_status(status):
            with self._src_count_lock:
                self._src_count_cache[task_id] = count
        return count

    def _prefetch_src_counts(self, task_items):
        """*task_items* are ``(task_id, status)`` pairs (a bare id is also
        accepted and treated as status-unknown, i.e. never cached)."""
        items = []
        for it in task_items:
            tid, status = (it if isinstance(it, tuple) else (it, None))
            if tid:
                items.append((tid, status))
        if not items:
            return

        def _run():
            def _work(item):
                tid, status = item
                try:
                    count = self._resolve_src_count(tid, status)
                except Exception:
                    count = None
                try:
                    self.parent.after(0, self._apply_src_count, tid, count)
                except Exception:
                    pass

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(_work, items))
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
            self.mr_tree.set(iid, "src_strings",
                             "—" if count is None else count)
        except tk.TclError:
            pass

    def _on_src_counts_done(self):
        # Once the numbers have landed, re-apply an active source-string sort
        # so the final order reflects real workloads — the user may have
        # clicked the header while cells still read "…".
        if self._mr_sort and self._mr_sort[0] == "src_strings":
            self._apply_sort(*self._mr_sort)

    # ------------------------------------------------------------------
    # GitLab MR metadata prefetch — one fetch per distinct
    # (project_id, mr_iid), fanned back out to every matching row.
    # Paints JIRA, Title and live MR Status from the same payload.
    # ------------------------------------------------------------------
    def _prefetch_jira_metadata(self, keys):
        if not keys:
            return

        def _run():
            def _work(key):
                metadata = _jira.fetch_jira_metadata(
                    *key, force_refresh=True)
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
        """Paint JIRA + Title + MR Status for every row of one MR."""
        if metadata is None:
            # Close the late-failure race: a concurrent fetch may have filled
            # both caches after this worker began.
            metadata = _jira.get_cached_metadata(*key)
        jira = (metadata.jira_id if metadata is not None
                else (_jira.get_cached(*key) or ""))
        title = (metadata.title if metadata is not None
                 else (_jira.get_cached_title(*key) or ""))
        raw_state = (metadata.state if metadata is not None else None)
        if raw_state is None:
            raw_state = _jira.get_cached_state(*key)
        status_display = (
            (_jira.display_mr_state(raw_state) or "—")
            if raw_state is not None else "—"
        )
        for iid in self._jira_row_iids.get(key, ()):
            try:
                self.mr_tree.set(iid, "jira", jira or "—")
                self.mr_tree.set(iid, "mr_status", status_display)
                # Preserve an authoritative task-payload title when only its
                # missing JIRA ID required the fallback GitLab lookup.
                row_title = self._jira_titles_by_iid.get(iid) or title
                self._set_title_cell(iid, row_title)
            except tk.TclError:
                pass

    def _on_jira_prefetch_done(self):
        # If the user sorted a still-loading metadata column, fold the final
        # values into the requested order once all workers have returned.
        if self._mr_sort and self._mr_sort[0] in (
                "jira", "title", "mr_status", "delivery_mr",
                "delivery_mr_status"):
            self._apply_sort(*self._mr_sort)
        # Source-MR state is now known: merged sources may have a translation
        # import MR, and a known import MR may have a later Language Lead
        # fix MR on tranzor-mr-fix-*.
        self._prefetch_missing_delivery_mrs()

    def _prefetch_missing_delivery_mrs(self):
        """GitLab-search the original Trans MR and any later fix successor."""
        groups = {}  # (project, source_iid) → [(tree_iid, task_id, known_iid)]
        try:
            rows = list(self.mr_tree.get_children(""))
        except tk.TclError:
            return
        for tree_iid in rows:
            meta = self._mr_link_meta.get(tree_iid) or {}
            project = meta.get("project") or ""
            source_iid = meta.get("source_iid")
            known_iid = meta.get("delivery_iid")
            try:
                cell = str(self.mr_tree.set(tree_iid, "delivery_mr") or "")
                status = str(self.mr_tree.set(tree_iid, "mr_status") or "")
            except tk.TclError:
                continue
            if not project or source_iid is None:
                if not known_iid and cell == "…":
                    self._clear_delivery_cells(tree_iid)
                continue
            if known_iid:
                groups.setdefault(
                    (str(project), int(source_iid)), []
                ).append((tree_iid, meta.get("task_id") or tree_iid, known_iid))
                continue
            if cell not in ("…",):
                continue
            if status == "Merged":
                groups.setdefault(
                    (str(project), int(source_iid)), []
                ).append((tree_iid, meta.get("task_id") or tree_iid, None))
            elif status in ("Open", "Closed", "Locked", "—"):
                self._clear_delivery_cells(tree_iid)
        if not groups:
            return

        def _run():
            client = None
            try:
                client = _tpe._shared_gitlab_client()
            except Exception:
                client = None

            def _work(item):
                (project, source_iid), row_jobs = item
                try:
                    if client is None or not client.has_token():
                        mrs = []
                    else:
                        mrs = client.list_merge_requests(
                            _delivery.DELIVERY_SEARCH_TERM.format(
                                iid=source_iid),
                            project_id=project, in_field="title") or []
                except Exception:
                    mrs = []
                for tree_iid, task_id, known_iid in row_jobs:
                    picked_import = _delivery.pick_delivery_mr(
                        mrs, source_iid, task_id=task_id)
                    if known_iid:
                        import_ref = (
                            _delivery.ref_from_iid(
                                mrs, known_iid, fallback_project=project)
                            or _delivery.DeliveryRef(
                                project_id=project, iid=int(known_iid),
                                url=_delivery.gitlab_mr_url(project, known_iid))
                        )
                    else:
                        import_ref = _delivery.delivery_ref_from_mr(
                            picked_import, fallback_project=project)
                    exclude = import_ref.iid if import_ref is not None else known_iid
                    picked_fix = _delivery.pick_fix_mr(
                        mrs, source_iid, exclude_iid=exclude)
                    fix_ref = _delivery.delivery_ref_from_mr(
                        picked_fix, fallback_project=project)
                    try:
                        self.parent.after(
                            0, self._apply_follow_ups,
                            tree_iid, import_ref, fix_ref)
                    except Exception:
                        pass

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(_work, list(groups.items())))
            try:
                self.parent.after(0, self._on_delivery_prefetch_done)
            except Exception:
                pass

        threading.Thread(target=_run, name="mr-delivery-prefetch",
                         daemon=True).start()

    def _clear_delivery_cells(self, tree_iid):
        meta = self._mr_link_meta.get(tree_iid)
        if meta is not None:
            meta["delivery_iid"] = None
            meta["delivery_url"] = ""
            meta["delivery_state"] = ""
            meta["fix_iid"] = None
            meta["fix_url"] = ""
            meta["fix_state"] = ""
        try:
            self.mr_tree.set(tree_iid, "delivery_mr", "—")
            self.mr_tree.set(tree_iid, "delivery_mr_status", "—")
        except tk.TclError:
            pass

    def _apply_delivery_mr(self, tree_iid, ref):
        """Paint one Trans MR# + Trans MR Status pair. Runs on the Tk thread."""
        self._apply_follow_ups(tree_iid, ref, None)

    def _apply_follow_ups(self, tree_iid, import_ref, fix_ref=None):
        """Paint original import MR plus an optional later Language Lead fix MR."""
        meta = self._mr_link_meta.get(tree_iid)
        if meta is None:
            return
        if import_ref is None and not meta.get("delivery_iid") and fix_ref is None:
            self._clear_delivery_cells(tree_iid)
            return
        if import_ref is not None:
            meta["delivery_iid"] = import_ref.iid
            meta["delivery_url"] = import_ref.url
            meta["delivery_state"] = import_ref.state
        if (fix_ref is not None
                and fix_ref.iid != meta.get("delivery_iid")):
            meta["fix_iid"] = fix_ref.iid
            meta["fix_url"] = fix_ref.url
            meta["fix_state"] = fix_ref.state
        else:
            meta["fix_iid"] = None
            meta["fix_url"] = ""
            meta["fix_state"] = ""
        self._paint_delivery_row(tree_iid)

    def _paint_delivery_row(self, tree_iid):
        meta = self._mr_link_meta.get(tree_iid) or {}
        display = _delivery.format_trans_mr_cell(
            meta.get("delivery_iid"), meta.get("fix_iid"))
        current_iid = _delivery.current_trans_mr_iid(
            meta.get("delivery_iid"), meta.get("fix_iid"))
        current_state = meta.get("fix_state") or meta.get("delivery_state")
        if current_state:
            status_display = _jira.display_mr_state(current_state) or "—"
        elif current_iid is not None:
            status_display = "…"
        else:
            status_display = "—"
        try:
            self.mr_tree.set(tree_iid, "delivery_mr", display)
            self.mr_tree.set(tree_iid, "delivery_mr_status", status_display)
        except tk.TclError:
            return
        project = meta.get("project") or ""
        dkey = _jira._normalize_key(project, current_iid)
        if dkey is None:
            return
        rows = self._delivery_row_iids.setdefault(dkey, [])
        if tree_iid not in rows:
            rows.append(tree_iid)
        # ``current_state`` is a *last-known* value, not a live one: it comes
        # from the GitLab title search that resolved this follow-up MR, whose
        # result set is cached (GitLabClient.list_merge_requests). Trusting it
        # kept Trans MR Status pinned at "Open" for the rest of the session
        # after the translation MR merged. Verify it against GitLab the same
        # way the source MR Status column does — once per Search/Refresh.
        if current_iid is not None and self._claim_delivery_status_refresh(dkey):
            self._prefetch_delivery_status([dkey])

    def _claim_delivery_status_refresh(self, key) -> bool:
        """True the first time ``key`` needs a live-state fetch this repaint.

        One force-refresh per distinct follow-up MR per Search/Refresh: enough
        to catch opened → merged, without re-asking GitLab on every repaint
        (sorting, Load More, a late delivery-MR resolution). The ledger is
        cleared when a replace render starts. ``False`` when GitLab is
        unreachable, so no cell is left waiting on a fetch that cannot land.
        """
        if key is None or not _jira.can_fetch():
            return False
        seen = getattr(self, "_delivery_status_refreshed", None)
        if seen is None:
            seen = set()
            self._delivery_status_refreshed = seen
        if key in seen:
            return False
        seen.add(key)
        return True

    def _prefetch_delivery_status(self, keys):
        """Refresh live GitLab state for known follow-up translation MRs."""
        if not keys:
            return

        def _run():
            def _work(key):
                metadata = _jira.fetch_jira_metadata(
                    *key, force_refresh=True)
                raw = metadata.state if metadata is not None else None
                if raw is None:
                    raw = _jira.get_cached_state(*key)
                try:
                    self.parent.after(
                        0, self._apply_delivery_status, key, raw)
                except Exception:
                    pass

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(_work, keys))
            try:
                self.parent.after(0, self._on_delivery_prefetch_done)
            except Exception:
                pass

        threading.Thread(target=_run, name="mr-delivery-status-prefetch",
                         daemon=True).start()

    def _apply_delivery_status(self, key, raw_state):
        status_display = (
            (_jira.display_mr_state(raw_state) or "—")
            if raw_state is not None else "—"
        )
        want_iid = key[1] if isinstance(key, tuple) and len(key) > 1 else None
        for iid in self._delivery_row_iids.get(key, ()):
            meta = getattr(self, "_mr_link_meta", {}).get(iid) or {}
            current = _delivery.current_trans_mr_iid(
                meta.get("delivery_iid"), meta.get("fix_iid"))
            if current is None or want_iid not in (None, current):
                continue
            known = (meta.get("fix_state") if meta.get("fix_iid") == current
                     else meta.get("delivery_state"))
            if raw_state is None and known:
                # Transient fetch failure. Keep the last known state rather
                # than knocking a correct cell back to "—".
                continue
            if meta.get("fix_iid") == current:
                meta["fix_state"] = raw_state or ""
            elif meta.get("delivery_iid") == current:
                meta["delivery_state"] = raw_state or ""
            try:
                self.mr_tree.set(iid, "delivery_mr_status", status_display)
            except tk.TclError:
                pass

    def _on_delivery_prefetch_done(self):
        if self._mr_sort and self._mr_sort[0] in (
                "delivery_mr", "delivery_mr_status"):
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
    # JIRA / MR# / Trans MR# hyperlink + Title Tooltip interaction
    # ------------------------------------------------------------------
    def _col_ident(self, name):
        return f"#{self._MR_COLUMNS.index(name) + 1}"

    def _mr_tree_link_at(self, x, y):
        """Return the URL under a Treeview pointer position, if any."""
        if self.mr_tree.identify_region(x, y) != "cell":
            return ""
        column = self.mr_tree.identify_column(x)
        iid = self.mr_tree.identify_row(y)
        if not iid:
            return ""
        try:
            if column == self._col_ident("jira"):
                return _jira.jira_browse_url(self.mr_tree.set(iid, "jira"))
            meta = self._mr_link_meta.get(iid) or {}
            if column == self._col_ident("mr"):
                return str(meta.get("source_url") or "")
            if column == self._col_ident("delivery_mr"):
                return str(meta.get("fix_url") or meta.get("delivery_url") or "")
        except tk.TclError:
            return ""
        return ""

    def _jira_link_at(self, x, y):
        """Backward-compatible alias used by existing unit tests."""
        return self._mr_tree_link_at(x, y)

    def _title_tooltip_at(self, x, y):
        """Return ``(iid, text)`` for a truncated Title or a Trans MR successor."""
        if self.mr_tree.identify_region(x, y) != "cell":
            return None
        column = self.mr_tree.identify_column(x)
        iid = self.mr_tree.identify_row(y)
        if not iid:
            return None
        if column == self._col_ident("title"):
            if iid not in self._truncated_title_iids:
                return None
            title = self._jira_titles_by_iid.get(iid, "")
            return (iid, title) if title else None
        if column == self._col_ident("delivery_mr"):
            text = self._delivery_tooltip_text(iid)
            return (iid, text) if text else None
        return None

    def _delivery_tooltip_text(self, iid):
        meta = getattr(self, "_mr_link_meta", {}).get(iid) or {}
        import_iid = meta.get("delivery_iid")
        fix_iid = meta.get("fix_iid")
        if not import_iid or not fix_iid or import_iid == fix_iid:
            return ""
        import_status = (
            _jira.display_mr_state(meta.get("delivery_state")) or "—")
        fix_status = _jira.display_mr_state(meta.get("fix_state")) or "—"
        try:
            template = self._t("mr_trans_mr_tooltip")
        except Exception:
            template = (
                "Translations imported in !{import_iid} ({import_status}). "
                "Later Language Lead fixes in !{fix_iid} ({fix_status}).")
        return template.format(
            import_iid=import_iid, import_status=import_status,
            fix_iid=fix_iid, fix_status=fix_status)

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
        is_title = (iid in self._truncated_title_iids
                    and self._jira_titles_by_iid.get(iid) == title)
        is_delivery = title == self._delivery_tooltip_text(iid)
        if not is_title and not is_delivery:
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
        cursor = "hand2" if self._mr_tree_link_at(event.x, event.y) else ""
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
        url = self._mr_tree_link_at(event.x, event.y)
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
                if col == "delivery_mr":
                    parsed = _delivery.trans_mr_sort_iid(
                        self.mr_tree.set(iid, col))
                    return parsed if parsed is not None else self.mr_tree.set(
                        iid, col)
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

    def _mr_row_export_meta(self, iid):
        """Read task_id / MR# / JIRA / Created from a visible tree row."""
        tags = self.mr_tree.item(iid, "tags")
        task_id = tags[0] if tags else None
        values = self.mr_tree.item(iid, "values")
        mr_iid = ""
        jira = ""
        created = ""
        if values:
            mr_col = self._MR_COLUMNS.index("mr")
            jira_col = self._MR_COLUMNS.index("jira")
            created_col = self._MR_COLUMNS.index("created")
            if len(values) > mr_col:
                mr_iid = str(values[mr_col] or "")
            if len(values) > jira_col:
                jira = str(values[jira_col] or "")
            if len(values) > created_col:
                created = str(values[created_col] or "")
        return {
            "task_id": task_id,
            "mr_iid": mr_iid,
            "jira": jira,
            "created": created,
        }

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
            # Pull MR# and the task's Created time straight from the visible
            # row (no extra HTTP round-trip) so the export filename can be
            # stamped with both — see _build_export_filename.
            meta = self._mr_row_export_meta(sel[0])
            task_id = meta.get("task_id")
            mr_iid = meta.get("mr_iid") or ""
            mr_created = meta.get("created") or ""
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
        self._set_mr_export_buttons_enabled(False)
        self.lbl_mr_status_bar.configure(text=self._t("status_exporting"))
        threading.Thread(target=self._run_export,
                         args=(task_id, fmt, export_type, mr_iid, adv_state,
                               basic_filters, mr_created),
                         kwargs={"llm_qa": llm_qa},
                         daemon=True).start()

    def _on_export_source_xlsx(self):
        """Export unique source strings of the selected MR as a 3-column XLSX.

        Sheet title is ``{JIRA} MR!{iid}`` (e.g. ``BUG-352 MR!4103``). Columns
        are Key / en-US Value / task name, where task name is the companion
        All-Translations JSON filename. Requires a selected row — unlike
        Export Selected this never dumps the whole pipeline.
        """
        sel = self.mr_tree.selection()
        if not sel:
            self.lbl_mr_status_bar.configure(
                text=self._t("mr_source_xlsx_need_selection"))
            return
        meta = self._mr_row_export_meta(sel[0])
        if not meta.get("task_id"):
            self.lbl_mr_status_bar.configure(
                text=self._t("mr_source_xlsx_need_selection"))
            return
        adv_state = self.adv_filter.get_state() if self.adv_filter else None
        self._set_mr_export_buttons_enabled(False)
        self.lbl_mr_status_bar.configure(text=self._t("status_exporting"))
        threading.Thread(
            target=self._run_export_source_xlsx,
            args=(meta, adv_state),
            daemon=True,
        ).start()

    def _run_export_source_xlsx(self, meta, adv_state=None):
        try:
            import export_json
            task_id = meta["task_id"]
            results = mr_api.fetch_mr_results(task_id, **self._api_kw())
            if adv_state is not None:
                try:
                    if not advanced_filter.is_empty(adv_state):
                        results = {
                            **results,
                            "translations": advanced_filter.filter_translations(
                                results.get("translations") or [], adv_state),
                        }
                except Exception:
                    pass
            today = date.today().isoformat()
            env_tag = "" if self.env_key == "prod" else self.env_key
            json_name = self._build_export_filename(
                ".json",
                mr_iid=meta.get("mr_iid") or "",
                id_tag=str(task_id)[:8],
                type_tag="all",
                created=meta.get("created") or "",
                export_date=today,
                env_tag=env_tag,
            )
            xlsx_name = self._build_export_filename(
                ".xlsx",
                mr_iid=meta.get("mr_iid") or "",
                id_tag=str(task_id)[:8],
                type_tag="source",
                created=meta.get("created") or "",
                export_date=today,
                env_tag=env_tag,
            )
            rows = export_json.source_rows_from_payload(
                results, task_name=json_name)
            title = export_json.sheet_title_for_mr(
                meta.get("jira") or "", meta.get("mr_iid") or "")
            filepath = os.path.join(export_output_dir(), xlsx_name)
            saved = export_json.save_source_xlsx(
                [{"title": title, "rows": rows}], filepath)
            if not saved:
                raise RuntimeError("openpyxl is required to write XLSX")
            basename = os.path.basename(saved)
            self.parent.after(0, lambda b=basename: self.lbl_mr_status_bar.configure(
                text=self._t("status_saved").format(filename=b)))
            self.parent.after(0, lambda p=saved: reveal_in_folder(p))
        except Exception as e:
            msg = str(e)[:50]
            self.parent.after(0, lambda m=msg: self.lbl_mr_status_bar.configure(
                text=f"❌ {m}"))
        finally:
            self.parent.after(0, lambda: self._set_mr_export_buttons_enabled(True))

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
            script_dir = export_output_dir()
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
            self.parent.after(0, lambda: self._set_mr_export_buttons_enabled(True))

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

    def _clear_recent_project_rows(self):
        inner = getattr(self, "_recent_inner", None)
        if inner is None:
            return
        for child in inner.winfo_children():
            try:
                child.destroy()
            except tk.TclError:
                pass
        self._recent_name_labels = []
        self._recent_name_paths = []
        self._recent_tooltips = []

    def _show_recent_projects_loading(self):
        self._render_recent_message(self._t("summary_loading"))

    def _render_recent_message(self, text):
        inner = getattr(self, "_recent_inner", None)
        if inner is None:
            return
        self._clear_recent_project_rows()
        frame = getattr(self, "_mr_sidebar_frame", None)
        try:
            pane_w = int(str(frame.cget("width") or 0)) if frame else 0
        except (TypeError, ValueError, tk.TclError):
            pane_w = 0
        wrap = _mr_sidebar_wraplength(pane_w or _MR_SIDEBAR_MIN_PX)
        lbl = ttk.Label(
            inner, text=text, style="SummaryStatus.TLabel",
            wraplength=max(80, wrap), justify="left", anchor="w")
        lbl.pack(anchor="w", fill="x")
        self._bind_recent_mousewheel(lbl)
        self._on_recent_inner_configure()

    def _render_recent_projects(self, recent):
        """Repaint the Recently Added list. Paths wrap to the live sidebar
        width (no Treeview clipping); hover still shows the full identity."""
        self._last_recent_projects = list(recent or [])
        inner = getattr(self, "_recent_inner", None)
        if inner is None:
            return
        if not self._last_recent_projects:
            self._render_recent_message(self._t("mr_recent_empty"))
            return
        self._clear_recent_project_rows()
        try:
            from export_gui import Tooltip
        except Exception:
            Tooltip = None
        canvas_w = 0
        canvas = getattr(self, "_recent_canvas", None)
        if canvas is not None:
            try:
                canvas_w = int(canvas.winfo_width() or 0)
            except tk.TclError:
                canvas_w = 0
        name_wrap = max(80, (canvas_w or _MR_SIDEBAR_MIN_PX) - _MR_RECENT_AGE_RESERVE_PX)
        for r in self._last_recent_projects:
            pid = r.get("project_id", "") or ""
            ts = r.get("first_seen", "") or ""
            rel = self._relative_time(ts)
            row = ttk.Frame(inner, style="Summary.TFrame")
            row.pack(fill="x", pady=(0, 5))
            age = ttk.Label(
                row, text=rel, style="SummaryStatus.TLabel", anchor="ne")
            age.pack(side="right", padx=(8, 0), anchor="ne")
            font = getattr(self, "_recent_name_font", None)
            measure = font.measure if font is not None else len
            name = ttk.Label(
                row,
                text=_break_project_path(pid, name_wrap, measure),
                style="Card.TLabel",
                font=(FONT_FAMILY, 10),
                wraplength=name_wrap, justify="left", anchor="w")
            name.pack(side="left", fill="x", expand=True, anchor="w")
            self._recent_name_labels.append(name)
            self._recent_name_paths.append(pid)
            abs_ts = format_display_datetime(ts, empty="") if ts else ""
            tip = _recent_project_tooltip(pid, rel, abs_ts)
            if Tooltip is not None and tip:
                try:
                    self._recent_tooltips.append(Tooltip(name, tip))
                    if rel:
                        self._recent_tooltips.append(Tooltip(age, tip))
                except Exception:
                    pass
            self._bind_recent_mousewheel(row)
            self._bind_recent_mousewheel(name)
            self._bind_recent_mousewheel(age)
        self._apply_recent_name_wraplengths(canvas_w or None)
        self._on_recent_inner_configure()

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
                created_at = format_display_datetime(
                    log.get("created_at") or "")
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
            script_dir = export_output_dir()
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
