#!/usr/bin/env python3
"""
Tranzor Translation Exporter — Lightweight Desktop GUI
Uses Python built-in tkinter, zero extra dependencies.
Supports English / Chinese interface language toggle.
"""

import os
import sys
import io
import platform
import threading
import webbrowser
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import date

# ---------------------------------------------------------------------------
# 跨平台字体适配 — Mac 使用系统内置字体，Windows 使用 Segoe UI / Consolas
# ---------------------------------------------------------------------------
IS_MAC = platform.system() == "Darwin"

if IS_MAC:  # macOS
    FONT_FAMILY = "Helvetica Neue"
    FONT_MONO = "Menlo"
else:  # Windows / Linux
    FONT_FAMILY = "Segoe UI"
    FONT_MONO = "Consolas"


def open_in_browser(filepath):
    """Open a local file in the default browser — cross-platform."""
    abspath = os.path.abspath(filepath)
    if IS_MAC:
        # macOS: 'open' command works on both Apple Silicon & Intel
        subprocess.Popen(["open", abspath])
    else:
        # Windows / Linux: use file:// URI
        import pathlib
        url = pathlib.Path(abspath).as_uri()
        webbrowser.open(url)

try:
    import requests
except ImportError:
    requests = None

# Ensure sibling modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_changes
import export_translations
import gui_tabs

# Optional: Full Translation Export Tab (nested module, must not break GUI if missing)
try:
    import gui_tab_full_translations as _ft_tab_mod
except Exception as _ft_e:  # pragma: no cover
    _ft_tab_mod = None
    _ft_import_error = _ft_e
else:
    _ft_import_error = None

# ---------------------------------------------------------------------------
# Tranzor API config (reuse from export_changes)
# ---------------------------------------------------------------------------
TRANZOR_URL = "http://tranzor-platform.int.rclabenv.com"
API = f"{TRANZOR_URL}/api/v1/legacy"


# ============================================================
# i18n — All UI strings in English and Chinese
# ============================================================
STRINGS = {
    "en": {
        "window_title":       "Tranzor Translation Exporter",
        "title":              "🌐 Tranzor Translation Exporter",
        "subtitle":           "Export translation changes or all translations — HTML / Excel / TMX",
        "task_id_label":      "Task ID",
        "task_id_hint":       "Empty = All Tasks",
        "export_type_label":  "Export Type",
        "export_type_changes":"Changes",
        "export_type_all":    "All Translations",
        "output_fmt_label":   "Output Format",
        "output_fmt_html":    "HTML (Filters / TMX Export)",
        "output_fmt_xlsx":    "Excel",
        "btn_run":            "▶  Start Export",
        "btn_open":           "📂  Open Report",
        "status_ready":       "Ready",
        "status_loading":     "Loading",
        "status_exporting":   "Exporting…",
        "status_done":        "✓ Export complete",
        "status_no_data":     "Done (no data or error)",
        "log_header":         "Log Output",
        "footer":             "Tranzor Platform · Internal Tool · v2.0",
        "lang_toggle":        "中文",
        # Summary panel
        "summary_title":      "📋 Platform Task Overview",
        "summary_total":      "Total Tasks",
        "summary_recent":     "Tasks",
        "summary_loading":    "Loading…",
        "summary_error":      "⚠ Failed to load task data",
        "summary_refresh":    "🔄 Refresh",
        "summary_prev":       "Previous",
        "summary_next":       "Next",
        "summary_page_info":  "Page {page} / {total_pages}  Showing {start}-{end} of {total}",
        "summary_page_empty": "Page 0 / 0  No tasks",
        "summary_col_id":     "ID",
        "summary_col_name":   "Task Name",
        "summary_col_creator":"Creator",
        # Messages
        "err_title":          "Input Error",
        "err_task_id":        "Task ID must be a number (e.g. 53), or leave empty to export all.",
        "found_records":      "Found {count} {type} records",
        "no_records":         "No {type} records found, nothing to export.",
        "export_failed":      "❌ Export failed: {error}",
        "record_changes":     "change",
        "record_translations":"translation",
        # Tab names
        "tab_file_translation": "📁 File Translation",
        "tab_mr_pipeline":     "🔀 MR Pipeline",
        "tab_quality_overview": "📊 Quality Overview",
        # MR Pipeline tab
        "mr_project":       "Project",
        "mr_release":       "Release",
        "mr_status":        "Status",
        "mr_date_range":    "Date",
        "mr_search":        "🔍 Search",
        "mr_reset":         "Reset",
        "mr_export":        "📦 Export Selected",
        "mr_sidebar_title": "📊 MR Pipeline Stats",
        "mr_stat_total":    "Total Tasks",
        "mr_stat_completed":"Completed",
        "mr_stat_failed":   "Failed",
        "mr_stat_avg_score":"Avg Score",
        "mr_col_idx":       "#",
        "mr_col_project":   "Project",
        "mr_col_mr":        "MR#",
        "mr_col_release":   "Release",
        "mr_col_status":    "Status",
        "mr_col_avg_score": "Avg Score",
        "mr_col_created":   "Created",
        "mr_col_duration":  "Duration",
        # Quality Overview tab
        "qa_language":      "Language",
        "qa_export":        "Export Report",
        "qa_total_tasks":   "Work Items",
        "qa_total_items":   "Segments",
        "qa_avg_score":     "Avg Score",
        "qa_low_score":     "Below Threshold",
        "qa_below_rate":    "Below Threshold %",
        "qa_refined_rate":  "Refined %",
        "qa_human_rate":    "Human Touch %",
        "qa_score_dist":    "Score Distribution",
        "qa_error_dist":    "Error Category Distribution",
        "qa_trend":         "Quality Trend",
        "qa_err_by_lang":   "Errors by Language",
        "qa_lang_detail":   "By Language Breakdown",
        "qa_low_items":     "Low-Score Items",
        "qa_threshold":     "Threshold",
        "qa_mr_tab":        "MR Translation",
        "qa_file_tab":      "File Translation",
        "qa_task":          "Task",
        "qa_mr":            "MR",
        "qa_score_min":     "Min Score",
        "qa_score_max":     "Max Score",
        "qa_lang_col_language":     "Language",
        "qa_lang_col_count":        "Segments",
        "qa_lang_col_avg_score":    "Avg Score",
        "qa_lang_col_below_pct":    "Below %",
        "qa_lang_col_refined_pct":  "Refined %",
        "qa_lang_col_human_pct":    "Human %",
        "qa_lang_col_warnings":     "Warnings",
        "qa_low_col_idx":        "#",
        "qa_low_col_source_type":"Type",
        "qa_low_col_scope":      "Task/MR",
        "qa_low_col_opus_id":    "String Key",
        "qa_low_col_language":   "Language",
        "qa_low_col_source":     "Source",
        "qa_low_col_translated": "Translated",
        "qa_low_col_score":      "Score",
        "qa_low_col_error_cat":  "Error Category",
        "qa_low_col_reason":     "Reason",
    },
    "zh": {
        "window_title":       "Tranzor 翻译导出器",
        "title":              "🌐 Tranzor 翻译导出器",
        "subtitle":           "导出翻译变更记录或全部翻译，支持 HTML / Excel / TMX 格式",
        "task_id_label":      "Task ID",
        "task_id_hint":       "留空 = 全部 Task",
        "export_type_label":  "导出类型",
        "export_type_changes":"变更记录",
        "export_type_all":    "全部翻译",
        "output_fmt_label":   "输出格式",
        "output_fmt_html":    "HTML（含筛选/TMX 导出）",
        "output_fmt_xlsx":    "Excel",
        "btn_run":            "▶  开始导出",
        "btn_open":           "📂  打开报告",
        "status_ready":       "就绪",
        "status_loading":     "加载中",
        "status_exporting":   "正在导出…",
        "status_done":        "✓ 导出完成",
        "status_no_data":     "完成（无数据或出错）",
        "log_header":         "运行日志",
        "footer":             "Tranzor Platform · Internal Tool · v2.0",
        "lang_toggle":        "English",
        # Summary panel
        "summary_title":      "📋 平台任务概览",
        "summary_total":      "总任务数",
        "summary_recent":     "任务列表",
        "summary_loading":    "加载中…",
        "summary_error":      "⚠ 加载任务数据失败",
        "summary_refresh":    "🔄 刷新",
        "summary_prev":       "上一页",
        "summary_next":       "下一页",
        "summary_page_info":  "第 {page} / {total_pages} 页  显示 {start}-{end} / {total}",
        "summary_page_empty": "第 0 / 0 页  暂无任务",
        "summary_col_id":     "ID",
        "summary_col_name":   "任务名称",
        "summary_col_creator":"创建者",
        # Messages
        "err_title":          "输入错误",
        "err_task_id":        "Task ID 必须是纯数字（如 53），或留空导出全部。",
        "found_records":      "共找到 {count} 条{type}",
        "no_records":         "没有{type}，无需导出。",
        "export_failed":      "❌ 导出失败: {error}",
        "record_changes":     "变更记录",
        "record_translations":"翻译记录",
        # Tab names
        "tab_file_translation": "📁 文件翻译",
        "tab_mr_pipeline":     "🔀 MR Pipeline",
        "tab_quality_overview": "📊 质量概览",
        # MR Pipeline tab
        "mr_project":       "项目",
        "mr_release":       "版本",
        "mr_status":        "状态",
        "mr_date_range":    "日期",
        "mr_search":        "🔍 查询",
        "mr_reset":         "重置",
        "mr_export":        "📦 导出选中",
        "mr_sidebar_title": "📊 MR Pipeline 统计",
        "mr_stat_total":    "总任务数",
        "mr_stat_completed":"已完成",
        "mr_stat_failed":   "失败",
        "mr_stat_avg_score":"平均分",
        "mr_col_idx":       "#",
        "mr_col_project":   "项目",
        "mr_col_mr":        "MR#",
        "mr_col_release":   "版本",
        "mr_col_status":    "状态",
        "mr_col_avg_score": "平均分",
        "mr_col_created":   "创建时间",
        "mr_col_duration":  "耗时",
        # Quality Overview tab
        "qa_language":      "语言",
        "qa_export":        "导出报告",
        "qa_total_tasks":   "工作项",
        "qa_total_items":   "翻译段数",
        "qa_avg_score":     "平均分",
        "qa_low_score":     "低于阈值",
        "qa_below_rate":    "低于阈值 %",
        "qa_refined_rate":  "精炼率 %",
        "qa_human_rate":    "人工介入 %",
        "qa_score_dist":    "分数分布",
        "qa_error_dist":    "错误类别分布",
        "qa_trend":         "质量趋势",
        "qa_err_by_lang":   "按语言错误分布",
        "qa_lang_detail":   "按语言明细",
        "qa_low_items":     "低分条目",
        "qa_threshold":     "阈值",
        "qa_mr_tab":        "MR 翻译",
        "qa_file_tab":      "文件翻译",
        "qa_task":          "任务",
        "qa_mr":            "MR",
        "qa_score_min":     "最低分",
        "qa_score_max":     "最高分",
        "qa_lang_col_language":     "语言",
        "qa_lang_col_count":        "段数",
        "qa_lang_col_avg_score":    "平均分",
        "qa_lang_col_below_pct":    "低于阈值%",
        "qa_lang_col_refined_pct":  "精炼%",
        "qa_lang_col_human_pct":    "人工%",
        "qa_lang_col_warnings":     "告警",
        "qa_low_col_idx":        "#",
        "qa_low_col_source_type":"类型",
        "qa_low_col_scope":      "任务/MR",
        "qa_low_col_opus_id":    "String Key",
        "qa_low_col_language":   "语言",
        "qa_low_col_source":     "原文",
        "qa_low_col_translated": "译文",
        "qa_low_col_score":      "分数",
        "qa_low_col_error_cat":  "错误类别",
        "qa_low_col_reason":     "原因",
    },
}

# Merge in strings from the optional Full Translations tab (non-destructive).
if _ft_tab_mod is not None:
    try:
        for _lang_code, _extra in _ft_tab_mod.STRINGS.items():
            STRINGS.setdefault(_lang_code, {}).update(_extra)
    except Exception:
        pass


# ============================================================
# TextRedirector — forward print() to tkinter Text widget
# ============================================================
class TextRedirector(io.TextIOBase):
    """Thread-safe stdout → Text widget redirector."""

    def __init__(self, text_widget):
        self.text_widget = text_widget

    def write(self, s):
        if s:
            self.text_widget.after(0, self._append, s)
        return len(s) if s else 0

    def _append(self, s):
        self.text_widget.configure(state="normal")
        self.text_widget.insert(tk.END, s)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state="disabled")

    def flush(self):
        pass


# ============================================================
# Main Window
# ============================================================
# ============================================================
# API helper — fetch tasks for summary panel
# ============================================================
def fetch_all_tasks_summary():
    """Fetch all tasks (no status filter) and return (total, all_tasks).
    Each task dict has: id, task_name, created_by.
    """
    if requests is None:
        raise RuntimeError("requests package not available")

    page_size = 200
    offset = 0
    total = None
    all_tasks = []
    seen_ids = set()

    while True:
        resp = requests.get(
            f"{API}/tasks",
            params={"limit": page_size, "offset": offset},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("tasks", [])

        if total is None:
            total = data.get("total")

        for task in batch:
            task_id = task.get("id")
            dedupe_key = task_id if task_id is not None else (offset, len(all_tasks))
            if dedupe_key in seen_ids:
                continue
            seen_ids.add(dedupe_key)
            all_tasks.append(task)

        if not batch:
            break

        offset += len(batch)
        if total is not None and offset >= total:
            break
        if len(batch) < page_size:
            break

    def _sort_key(task):
        task_id = task.get("id")
        try:
            return (1, int(task_id))
        except (TypeError, ValueError):
            return (0, str(task_id or ""))

    all_tasks.sort(key=_sort_key, reverse=True)
    total = max(int(total or 0), len(all_tasks))
    return total, all_tasks


class ExportApp:
    # Color scheme
    BG = "#1a1a2e"
    BG_CARD = "#16213e"
    FG = "#e0e0e0"
    ACCENT = "#0f3460"
    ACCENT_BTN = "#e94560"
    ACCENT_BTN_HOVER = "#ff6b81"
    SUCCESS = "#2ecc71"
    BORDER = "#2a2a4a"
    SUMMARY_HIGHLIGHT = "#1e2d50"  # slightly lighter than BG_CARD for rows
    SUMMARY_ROW_HEIGHT = 26
    SUMMARY_DEFAULT_PAGE_SIZE = 7

    def __init__(self, root):
        self.root = root
        self.root.geometry("1280x1050")
        self.root.resizable(True, True)
        self.root.configure(bg=self.BG)

        # Current language
        self.lang = "en"

        # State
        self.running = False
        self.last_output_path = None
        self.summary_loading = False
        self.summary_tasks = []
        self.summary_total = 0
        self.summary_page = 0
        self.summary_page_size = self.SUMMARY_DEFAULT_PAGE_SIZE
        self.summary_selected_task_id = None
        self.summary_resize_job = None

        # Setup
        self._setup_styles()
        self._build_ui()
        self._refresh_ui_text()

        # Center window
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"+{x}+{y}")
        self._recalculate_summary_page_size()
        self.root.after(250, self._recalculate_summary_page_size)

        # Auto-load legacy summary data on startup (only this – avoid concurrent API overload)
        self._load_summary_data()

        # Lazy-load MR Pipeline / Quality Overview data when tabs are first selected
        self._mr_tab_initialized = False
        self._qa_tab_initialized = False
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _t(self, key):
        """Get translated string for current language."""
        return STRINGS[self.lang].get(key, key)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Card.TFrame", background=self.BG_CARD)
        style.configure("App.TFrame", background=self.BG)

        style.configure("Title.TLabel",
                         background=self.BG, foreground="#fff",
                         font=(FONT_FAMILY, 18, "bold"))
        style.configure("Subtitle.TLabel",
                         background=self.BG, foreground="#888",
                         font=(FONT_FAMILY, 10))
        style.configure("Card.TLabel",
                         background=self.BG_CARD, foreground=self.FG,
                         font=(FONT_FAMILY, 11))
        style.configure("CardBold.TLabel",
                         background=self.BG_CARD, foreground="#fff",
                         font=(FONT_FAMILY, 11, "bold"))
        style.configure("Status.TLabel",
                         background=self.BG, foreground="#888",
                         font=(FONT_FAMILY, 9))

        # Radio button for dark theme
        style.configure("Card.TRadiobutton",
                         background=self.BG_CARD, foreground=self.FG,
                         font=(FONT_FAMILY, 11),
                         indicatorrelief="flat")
        style.map("Card.TRadiobutton",
                  background=[("active", self.ACCENT)])

        # Summary panel styles
        style.configure("Summary.TFrame", background=self.BG_CARD)
        style.configure("SummaryTitle.TLabel",
                         background=self.BG_CARD, foreground="#fff",
                         font=(FONT_FAMILY, 13, "bold"))
        style.configure("SummaryCount.TLabel",
                         background=self.BG_CARD, foreground=self.ACCENT_BTN,
                         font=(FONT_FAMILY, 28, "bold"))
        style.configure("SummaryCountLabel.TLabel",
                         background=self.BG_CARD, foreground="#888",
                         font=(FONT_FAMILY, 10))
        style.configure("SummarySection.TLabel",
                         background=self.BG_CARD, foreground="#aaa",
                         font=(FONT_FAMILY, 10, "bold"))
        style.configure("SummaryStatus.TLabel",
                         background=self.BG_CARD, foreground="#666",
                         font=(FONT_FAMILY, 9))

        # Treeview for dark theme
        style.configure("Summary.Treeview",
                         background="#0d1a30",
                         foreground=self.FG,
                         fieldbackground="#0d1a30",
                         borderwidth=0,
                         font=(FONT_FAMILY, 9),
                         rowheight=self.SUMMARY_ROW_HEIGHT)
        style.configure("Summary.Treeview.Heading",
                         background=self.ACCENT,
                         foreground="#ccc",
                         font=(FONT_FAMILY, 9, "bold"),
                         borderwidth=0)
        style.map("Summary.Treeview",
                  background=[("selected", "#1a3a6a")],
                  foreground=[("selected", "#fff")])
        style.map("Summary.Treeview.Heading",
                  background=[("active", "#1a3a6a")])

        # ── macOS-compatible ttk.Button styles ──
        if IS_MAC:
            style.configure("Accent.TButton",
                             background="#e94560", foreground="#ffffff",
                             font=(FONT_FAMILY, 12, "bold"),
                             padding=(20, 8))
            style.map("Accent.TButton",
                      background=[("active", "#ff6b81"), ("disabled", "#555555")],
                      foreground=[("disabled", "#999999")])

            style.configure("Secondary.TButton",
                             background="#0f3460", foreground="#cccccc",
                             font=(FONT_FAMILY, 10),
                             padding=(12, 4))
            style.map("Secondary.TButton",
                      background=[("active", "#1a3a6a")],
                      foreground=[("active", "#ffffff")])

            style.configure("Success.TButton",
                             background="#2ecc71", foreground="#ffffff",
                             font=(FONT_FAMILY, 12),
                             padding=(20, 8))
            style.map("Success.TButton",
                      background=[("active", "#27ae60")])

            style.configure("AccentSmall.TButton",
                             background="#e94560", foreground="#ffffff",
                             font=(FONT_FAMILY, 10, "bold"),
                             padding=(14, 3))
            style.map("AccentSmall.TButton",
                      background=[("active", "#ff6b81"), ("disabled", "#555555")],
                      foreground=[("disabled", "#999999")])

            style.configure("SuccessSmall.TButton",
                             background="#2ecc71", foreground="#ffffff",
                             font=(FONT_FAMILY, 10, "bold"),
                             padding=(14, 4))
            style.map("SuccessSmall.TButton",
                      background=[("active", "#27ae60"), ("disabled", "#555555")],
                      foreground=[("disabled", "#999999")])

            style.configure("SecondarySmall.TButton",
                             background="#0f3460", foreground="#cccccc",
                             font=(FONT_FAMILY, 10),
                             padding=(14, 3))
            style.map("SecondarySmall.TButton",
                      background=[("active", "#1a3a6a")],
                      foreground=[("active", "#ffffff")])

            style.configure("SecondaryTiny.TButton",
                             background="#0f3460", foreground="#cccccc",
                             font=(FONT_FAMILY, 9),
                             padding=(10, 3))
            style.map("SecondaryTiny.TButton",
                      background=[("active", "#1a3a6a")],
                      foreground=[("active", "#ffffff")])

    # ── Cross-platform button factory ──
    @staticmethod
    def _create_button(parent, *, text="", command=None, style_name="Secondary",
                       font=None, bg=None, fg=None, activebackground=None,
                       activeforeground=None, padx=12, pady=4, state="normal",
                       cursor="hand2", **extra_kw):
        """Create a button that renders correctly on both macOS and Windows.
        On macOS: returns ttk.Button with named style.
        On Windows: returns tk.Button with explicit bg/fg colors.
        """
        if IS_MAC:
            btn = ttk.Button(parent, text=text, command=command,
                              style=f"{style_name}.TButton", cursor=cursor)
            if state == "disabled":
                btn.state(["disabled"])
            return btn
        else:
            return tk.Button(
                parent, text=text, command=command,
                font=font or (FONT_FAMILY, 10),
                bg=bg or "#0f3460", fg=fg or "#ccc",
                activebackground=activebackground or "#1a3a6a",
                activeforeground=activeforeground or "#fff",
                relief="flat", cursor=cursor,
                bd=0, padx=padx, pady=pady, state=state,
                **extra_kw)

    def _build_ui(self):
        # ── Header ──
        header = ttk.Frame(self.root, style="App.TFrame")
        header.pack(fill="x", padx=24, pady=(16, 4))

        # Language toggle button (top-right)
        self.btn_lang = self._create_button(
            header, text="中文", command=self._toggle_lang,
            style_name="Secondary",
            font=(FONT_FAMILY, 10),
            bg=self.ACCENT, fg="#ccc", activebackground="#1a3a6a",
            activeforeground="#fff", padx=12, pady=2)
        self.btn_lang.pack(side="right", anchor="ne")

        self.lbl_title = ttk.Label(header, text="", style="Title.TLabel")
        self.lbl_title.pack(anchor="w")
        self.lbl_subtitle = ttk.Label(header, text="", style="Subtitle.TLabel")
        self.lbl_subtitle.pack(anchor="w", pady=(2, 0))

        # ── Notebook (tabbed layout) ──
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=24, pady=(8, 0))

        # --- Tab 1: File Translation (existing content) ---
        tab1 = ttk.Frame(self.notebook, style="App.TFrame")
        self.notebook.add(tab1, text="")

        # --- Tab 2: MR Pipeline ---
        tab2 = ttk.Frame(self.notebook, style="App.TFrame")
        self.notebook.add(tab2, text="")
        self.mr_tab = gui_tabs.MRPipelineTab(tab2, self)

        # --- Tab 3: Quality Overview ---
        tab3 = ttk.Frame(self.notebook, style="App.TFrame")
        self.notebook.add(tab3, text="")
        self.qa_tab = gui_tabs.QualityOverviewTab(tab3, self)

        # --- Tab 4: Full Translations (optional, pure additive) ---
        self.ft_tab = None
        if _ft_tab_mod is not None:
            try:
                tab4 = ttk.Frame(self.notebook, style="App.TFrame")
                self.notebook.add(tab4, text="")
                self.ft_tab = _ft_tab_mod.FullTranslationsTab(tab4, self)
            except Exception as _e:
                # Never let the optional tab break the main GUI.
                print(f"[Full Translations tab] init failed: {_e}")
                self.ft_tab = None

        # ═══════════════════════════════════════════
        # TAB 1 CONTENTS (File Translation — preserved)
        # ═══════════════════════════════════════════
        content = ttk.Frame(tab1, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        left = ttk.Frame(content, style="App.TFrame")
        left.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(content, style="App.TFrame", width=360)
        right.pack(side="right", fill="y", padx=(16, 0))
        right.pack_propagate(False)

        # ── Settings Card ──
        card = ttk.Frame(left, style="Card.TFrame")
        card.pack(fill="x", pady=(0, 0))
        card.configure(borderwidth=1, relief="solid")

        inner = ttk.Frame(card, style="Card.TFrame")
        inner.pack(fill="x", padx=20, pady=16)

        # Task ID row
        row1 = ttk.Frame(inner, style="Card.TFrame")
        row1.pack(fill="x", pady=(0, 12))
        self.lbl_task_id = ttk.Label(row1, text="", style="CardBold.TLabel", width=12)
        self.lbl_task_id.pack(side="left")
        self.task_var = tk.StringVar()
        self.task_entry = tk.Entry(row1, textvariable=self.task_var,
                                   font=(FONT_FAMILY, 11),
                                   bg="#0a0a1a", fg="#fff",
                                   insertbackground="#fff",
                                   relief="flat", bd=0,
                                   highlightthickness=1,
                                   highlightcolor=self.ACCENT_BTN,
                                   highlightbackground=self.BORDER)
        self.task_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(8, 0))

        self.lbl_task_hint = ttk.Label(row1, text="", style="Status.TLabel")
        self.lbl_task_hint.configure(background=self.BG_CARD)
        self.lbl_task_hint.pack(side="left", padx=(10, 0))

        # Export Type row
        row_type = ttk.Frame(inner, style="Card.TFrame")
        row_type.pack(fill="x", pady=(0, 12))
        self.lbl_export_type = ttk.Label(row_type, text="", style="CardBold.TLabel", width=12)
        self.lbl_export_type.pack(side="left")
        self.export_type_var = tk.StringVar(value="changes")
        self.rb_changes = ttk.Radiobutton(row_type, text="",
                         variable=self.export_type_var, value="changes",
                         style="Card.TRadiobutton")
        self.rb_changes.pack(side="left", padx=(8, 16))
        self.rb_translations = ttk.Radiobutton(row_type, text="",
                         variable=self.export_type_var, value="translations",
                         style="Card.TRadiobutton")
        self.rb_translations.pack(side="left")

        # Output Format row
        row2 = ttk.Frame(inner, style="Card.TFrame")
        row2.pack(fill="x")
        self.lbl_fmt = ttk.Label(row2, text="", style="CardBold.TLabel", width=12)
        self.lbl_fmt.pack(side="left")
        self.fmt_var = tk.StringVar(value="html")
        self.rb_html = ttk.Radiobutton(row2, text="",
                         variable=self.fmt_var, value="html",
                         style="Card.TRadiobutton")
        self.rb_html.pack(side="left", padx=(8, 16))
        self.rb_xlsx = ttk.Radiobutton(row2, text="",
                         variable=self.fmt_var, value="xlsx",
                         style="Card.TRadiobutton")
        self.rb_xlsx.pack(side="left")

        # ── Button Area ──
        btn_frame = ttk.Frame(left, style="App.TFrame")
        btn_frame.pack(fill="x", pady=(16, 0))

        self.btn_run = self._create_button(
            btn_frame, text="", command=self._on_run,
            style_name="Accent",
            font=(FONT_FAMILY, 12, "bold"),
            bg=self.ACCENT_BTN, fg="#fff",
            activebackground=self.ACCENT_BTN_HOVER,
            activeforeground="#fff", padx=20, pady=8)
        self.btn_run.pack(side="left")

        self.btn_open = self._create_button(
            btn_frame, text="", command=self._on_open,
            style_name="Secondary",
            font=(FONT_FAMILY, 12),
            bg=self.ACCENT, fg="#888",
            padx=20, pady=8, state="disabled")
        self.btn_open.pack(side="left", padx=(12, 0))

        self.status_label = ttk.Label(btn_frame, text="", style="Status.TLabel")
        self.status_label.pack(side="right")

        # ── Log Area ──
        log_frame = ttk.Frame(left, style="App.TFrame")
        log_frame.pack(fill="both", expand=True, pady=(12, 0))

        self.lbl_log_header = ttk.Label(log_frame, text="", style="Subtitle.TLabel")
        self.lbl_log_header.pack(anchor="w", pady=(0, 4))

        self.log_text = tk.Text(
            log_frame, height=12,
            bg="#0a0a1a", fg="#aaa",
            font=(FONT_MONO, 10),
            relief="flat", bd=0,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True)

        # ═══════════════════════════════════════════
        # RIGHT PANEL — Summary
        # ═══════════════════════════════════════════
        self._build_summary_panel(right)

        # ── Progress Bar (inside tab1) ──
        self.progress = ttk.Progressbar(tab1, mode="indeterminate",
                                         length=400)
        self.progress.pack(fill="x", padx=8, pady=(8, 4))

        # ── Footer (full width, outside notebook) ──
        self.lbl_footer = ttk.Label(self.root, text="", style="Status.TLabel")
        self.lbl_footer.pack(pady=(0, 8))

    def _build_summary_panel(self, parent):
        """Build the right-side summary panel."""
        panel = ttk.Frame(parent, style="Summary.TFrame")
        panel.pack(fill="both", expand=True)
        panel.configure(borderwidth=1, relief="solid")

        inner = ttk.Frame(panel, style="Summary.TFrame")
        inner.pack(fill="both", expand=True, padx=16, pady=16)

        # Panel title
        self.lbl_summary_title = ttk.Label(
            inner, text="", style="SummaryTitle.TLabel")
        self.lbl_summary_title.pack(anchor="w")

        # Separator
        sep1 = tk.Frame(inner, bg=self.BORDER, height=1)
        sep1.pack(fill="x", pady=(10, 12))

        # ── Total tasks stat ──
        stat_frame = ttk.Frame(inner, style="Summary.TFrame")
        stat_frame.pack(fill="x", pady=(0, 8))

        self.lbl_total_count = ttk.Label(
            stat_frame, text="—", style="SummaryCount.TLabel")
        self.lbl_total_count.pack(side="left")

        self.lbl_total_label = ttk.Label(
            stat_frame, text="", style="SummaryCountLabel.TLabel")
        self.lbl_total_label.pack(side="left", padx=(10, 0), pady=(8, 0))

        # ── Status message (loading / error) ──
        self.lbl_summary_status = ttk.Label(
            inner, text="", style="SummaryStatus.TLabel")
        self.lbl_summary_status.pack(anchor="w")

        # Separator
        sep2 = tk.Frame(inner, bg=self.BORDER, height=1)
        sep2.pack(fill="x", pady=(8, 10))

        # ── Recent tasks section header ──
        self.lbl_recent_header = ttk.Label(
            inner, text="", style="SummarySection.TLabel")
        self.lbl_recent_header.pack(anchor="w", pady=(0, 8))

        # ── Treeview for task list ──
        self.summary_tree_frame = ttk.Frame(inner, style="Summary.TFrame")
        self.summary_tree_frame.pack(fill="both", expand=True)
        self.summary_tree_frame.bind("<Configure>", self._schedule_summary_resize)

        self.task_tree = ttk.Treeview(
            self.summary_tree_frame,
            columns=("id", "name", "creator"),
            show="headings",
            style="Summary.Treeview",
            height=self.summary_page_size,
            selectmode="browse",
        )
        self.task_tree.heading("id", text="ID")
        self.task_tree.heading("name", text="Task Name")
        self.task_tree.heading("creator", text="Creator")

        self.task_tree.column("id", width=45, minwidth=40, stretch=False, anchor="center")
        self.task_tree.column("name", width=200, minwidth=100, stretch=True)
        self.task_tree.column("creator", width=80, minwidth=60, stretch=False)

        # Scrollbar
        tree_scroll = ttk.Scrollbar(
            self.summary_tree_frame, orient="vertical", command=self.task_tree.yview)
        self.task_tree.configure(yscrollcommand=tree_scroll.set)

        self.task_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        # Bind row click to fill Task ID
        self.task_tree.bind("<<TreeviewSelect>>", self._on_task_select)

        # ── Pagination + refresh controls ──
        btn_bar = ttk.Frame(inner, style="Summary.TFrame")
        btn_bar.pack(fill="x", pady=(10, 0))

        self.lbl_summary_page = ttk.Label(
            btn_bar, text="", style="SummaryStatus.TLabel")
        self.lbl_summary_page.pack(side="left")

        actions_bar = ttk.Frame(btn_bar, style="Summary.TFrame")
        actions_bar.pack(side="right")

        self.btn_summary_prev = self._create_button(
            actions_bar, text="", command=self._prev_summary_page,
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 9),
            bg=self.ACCENT, fg="#ccc", activebackground="#1a3a6a",
            activeforeground="#fff", padx=12, pady=4, state="disabled")
        self.btn_summary_prev.pack(side="left", padx=(0, 8))

        self.btn_summary_next = self._create_button(
            actions_bar, text="", command=self._next_summary_page,
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 9),
            bg=self.ACCENT, fg="#ccc", activebackground="#1a3a6a",
            activeforeground="#fff", padx=12, pady=4, state="disabled")
        self.btn_summary_next.pack(side="left", padx=(0, 8))

        self.btn_refresh = self._create_button(
            actions_bar, text="", command=self._load_summary_data,
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 9),
            bg=self.ACCENT, fg="#ccc", activebackground="#1a3a6a",
            activeforeground="#fff", padx=12, pady=4)
        self.btn_refresh.pack(side="left")

        self._update_summary_pager()

    # ── i18n: refresh all visible text ──
    def _refresh_ui_text(self):
        """Update all UI widget texts to the current language."""
        self.root.title(self._t("window_title"))
        self.lbl_title.configure(text=self._t("title"))
        self.lbl_subtitle.configure(text=self._t("subtitle"))
        self.lbl_task_id.configure(text=self._t("task_id_label"))
        self.lbl_task_hint.configure(text=self._t("task_id_hint"))
        self.lbl_export_type.configure(text=self._t("export_type_label"))
        self.rb_changes.configure(text=self._t("export_type_changes"))
        self.rb_translations.configure(text=self._t("export_type_all"))
        self.lbl_fmt.configure(text=self._t("output_fmt_label"))
        self.rb_html.configure(text=self._t("output_fmt_html"))
        self.rb_xlsx.configure(text=self._t("output_fmt_xlsx"))
        self.btn_run.configure(text=self._t("btn_run"))
        self.btn_open.configure(text=self._t("btn_open"))
        self.lbl_log_header.configure(text=self._t("log_header"))
        self.lbl_footer.configure(text=self._t("footer"))
        self.btn_lang.configure(text=self._t("lang_toggle"))

        # Notebook tab titles
        self.notebook.tab(0, text=self._t("tab_file_translation"))
        self.notebook.tab(1, text=self._t("tab_mr_pipeline"))
        self.notebook.tab(2, text=self._t("tab_quality_overview"))
        if self.ft_tab is not None:
            try:
                self.notebook.tab(3, text=self._t("tab_full_translations"))
                self.ft_tab.refresh_text()
            except Exception:
                pass

        # Summary panel texts
        self.lbl_summary_title.configure(text=self._t("summary_title"))
        self.lbl_total_label.configure(text=self._t("summary_total"))
        self.lbl_recent_header.configure(text=self._t("summary_recent"))
        self.btn_summary_prev.configure(text=self._t("summary_prev"))
        self.btn_summary_next.configure(text=self._t("summary_next"))
        self.btn_refresh.configure(text=self._t("summary_refresh"))
        self.task_tree.heading("id", text=self._t("summary_col_id"))
        self.task_tree.heading("name", text=self._t("summary_col_name"))
        self.task_tree.heading("creator", text=self._t("summary_col_creator"))
        self._update_summary_pager()

        # MR Pipeline & Quality Overview tab texts
        self.mr_tab.refresh_text()
        self.qa_tab.refresh_text()

        # Refresh status label only if not running
        if not self.running:
            if self.last_output_path:
                self.status_label.configure(text=self._t("status_done"))
            else:
                self.status_label.configure(text=self._t("status_ready"))

    def _toggle_lang(self):
        """Toggle between English and Chinese."""
        self.lang = "zh" if self.lang == "en" else "en"
        self._refresh_ui_text()

    def _schedule_summary_resize(self, event=None):
        """Debounce resize events before recalculating the summary page size."""
        if self.summary_resize_job is not None:
            self.root.after_cancel(self.summary_resize_job)
        self.summary_resize_job = self.root.after(120, self._recalculate_summary_page_size)

    def _recalculate_summary_page_size(self):
        """Adapt the number of visible summary rows to the available panel height."""
        self.summary_resize_job = None
        if not hasattr(self, "summary_tree_frame") or not self.summary_tree_frame.winfo_exists():
            return

        available_height = self.summary_tree_frame.winfo_height()
        if available_height <= 1:
            self.summary_resize_job = self.root.after(120, self._recalculate_summary_page_size)
            return

        current_size = max(1, int(self.task_tree.cget("height")))
        chrome_height = max(
            32,
            self.task_tree.winfo_reqheight() - (current_size * self.SUMMARY_ROW_HEIGHT),
        )
        usable_height = max(0, available_height - chrome_height - 4)
        page_size = max(1, usable_height // self.SUMMARY_ROW_HEIGHT)

        if page_size == self.summary_page_size:
            return

        first_visible_index = self.summary_page * self.summary_page_size
        self.summary_page_size = page_size
        self.task_tree.configure(height=self.summary_page_size)

        if self.summary_tasks:
            max_page = max(0, (len(self.summary_tasks) - 1) // self.summary_page_size)
            self.summary_page = min(first_visible_index // self.summary_page_size, max_page)
        else:
            self.summary_page = 0

        self._render_summary_page()

    def _get_summary_total_pages(self):
        if not self.summary_tasks:
            return 0
        return (len(self.summary_tasks) + self.summary_page_size - 1) // self.summary_page_size

    def _update_summary_pager(self):
        total_pages = self._get_summary_total_pages()
        if total_pages == 0:
            page_text = self._t("summary_page_empty")
            prev_enabled = False
            next_enabled = False
        else:
            start = self.summary_page * self.summary_page_size + 1
            end = min(start + self.summary_page_size - 1, len(self.summary_tasks))
            page_text = self._t("summary_page_info").format(
                page=self.summary_page + 1,
                total_pages=total_pages,
                start=start,
                end=end,
                total=len(self.summary_tasks),
            )
            prev_enabled = self.summary_page > 0
            next_enabled = self.summary_page < total_pages - 1

        self.lbl_summary_page.configure(text=page_text)
        self.btn_summary_prev.configure(state="normal" if prev_enabled else "disabled")
        self.btn_summary_next.configure(state="normal" if next_enabled else "disabled")

    def _restore_summary_selection_if_possible(self):
        if self.summary_selected_task_id is None:
            return

        selected_item = None
        for item in self.task_tree.get_children():
            values = self.task_tree.item(item, "values")
            if values and str(values[0]) == str(self.summary_selected_task_id):
                selected_item = item
                break

        if selected_item is not None:
            self.task_tree.selection_set(selected_item)
            self.task_tree.focus(selected_item)
            self.task_tree.see(selected_item)

    def _render_summary_page(self):
        total_pages = self._get_summary_total_pages()
        if total_pages == 0:
            self.summary_page = 0
            page_tasks = []
        else:
            self.summary_page = min(self.summary_page, total_pages - 1)
            start = self.summary_page * self.summary_page_size
            end = start + self.summary_page_size
            page_tasks = self.summary_tasks[start:end]

        for item in self.task_tree.get_children():
            self.task_tree.delete(item)

        for task in page_tasks:
            tid = task.get("id", "")
            tname = task.get("task_name", "")
            creator = task.get("created_by", "") or task.get("creator", "") or "-"
            self.task_tree.insert("", "end", values=(tid, tname, creator))

        self._restore_summary_selection_if_possible()
        self._update_summary_pager()

    def _prev_summary_page(self):
        if self.summary_page <= 0:
            return
        self.summary_page -= 1
        self._render_summary_page()

    def _next_summary_page(self):
        if self.summary_page >= self._get_summary_total_pages() - 1:
            return
        self.summary_page += 1
        self._render_summary_page()

    def _on_tab_changed(self, event):
        """Lazy-load data when MR Pipeline or Quality Overview tab is first selected."""
        tab_idx = self.notebook.index(self.notebook.select())
        if tab_idx == 1 and not self._mr_tab_initialized:
            self._mr_tab_initialized = True
            self.mr_tab.load_filters()
            self.mr_tab._load_overview()
            self.mr_tab.load_initial_tasks()
        elif tab_idx == 2 and not self._qa_tab_initialized:
            self._qa_tab_initialized = True
            self.qa_tab.load_filters()

    # ── Summary panel data loading ──
    def _load_summary_data(self):
        """Load task summary data in a background thread."""
        if self.summary_loading:
            return
        self.summary_loading = True
        self.lbl_summary_status.configure(
            text=self._t("summary_loading"), foreground="#666")
        self.btn_refresh.configure(state="disabled")
        t = threading.Thread(target=self._fetch_summary, daemon=True)
        t.start()

    def _fetch_summary(self):
        """Background thread: fetch tasks from API."""
        try:
            total, tasks = fetch_all_tasks_summary()
            self.root.after(0, self._on_summary_loaded, total, tasks)
        except Exception as e:
            self.root.after(0, self._on_summary_error, str(e))

    def _on_summary_loaded(self, total, all_tasks):
        """Callback when summary data loads successfully."""
        self.summary_loading = False
        self.btn_refresh.configure(state="normal")
        self.lbl_summary_status.configure(text="", foreground="#666")

        self.summary_total = total
        self.summary_tasks = all_tasks
        self.lbl_total_count.configure(text=str(total))

        total_pages = self._get_summary_total_pages()
        if total_pages == 0:
            self.summary_page = 0
        else:
            self.summary_page = min(self.summary_page, total_pages - 1)
        self._render_summary_page()

    def _on_summary_error(self, error_msg):
        """Callback when summary data fails to load."""
        self.summary_loading = False
        self.btn_refresh.configure(state="normal")
        self.lbl_summary_status.configure(
            text=self._t("summary_error"), foreground="#e94560")

    def _on_task_select(self, event):
        """When user clicks a task row, fill the Task ID entry."""
        sel = self.task_tree.selection()
        if sel:
            values = self.task_tree.item(sel[0], "values")
            if values:
                task_id = values[0]
                self.summary_selected_task_id = task_id
                self.task_var.set(str(task_id))

    # ── Event Handlers ──
    def _on_run(self):
        if self.running:
            return

        # Validate Task ID
        task_str = self.task_var.get().strip()
        task_id = None
        if task_str:
            try:
                task_id = int(task_str)
            except ValueError:
                messagebox.showwarning(self._t("err_title"), self._t("err_task_id"))
                return

        self.running = True
        self.last_output_path = None
        if IS_MAC:
            self.btn_run.state(["disabled"])
            self.btn_open.state(["disabled"])
            self.btn_open.configure(style="Secondary.TButton")
        else:
            self.btn_run.configure(state="disabled", bg="#555")
            self.btn_open.configure(state="disabled", fg="#888")
        self.progress.start(15)
        self.status_label.configure(text=self._t("status_exporting"))

        # Clear log
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

        # Run in background thread
        t = threading.Thread(target=self._run_export,
                              args=(task_id,), daemon=True)
        t.start()

    def _run_export(self, task_id):
        """Execute export in a background thread."""
        redirector = TextRedirector(self.log_text)
        old_stdout = sys.stdout
        sys.stdout = redirector

        export_type = self.export_type_var.get()
        # Capture language at start (user might toggle mid-export)
        lang = self.lang

        try:
            if export_type == "translations":
                rows = export_translations.collect_translations(task_id=task_id)
                record_label = STRINGS[lang]["record_translations"]
            else:
                rows = export_changes.collect_changes(task_id=task_id)
                record_label = STRINGS[lang]["record_changes"]

            msg = STRINGS[lang]["found_records"].format(count=len(rows), type=record_label)
            print(f"\n{msg}")

            if not rows:
                no_msg = STRINGS[lang]["no_records"].format(type=record_label)
                print(no_msg)
                self.root.after(0, self._on_done, None, False)
                return

            fmt = self.fmt_var.get()
            ext = ".xlsx" if fmt == "xlsx" else ".html"
            today_str = date.today().isoformat()

            if export_type == "translations":
                label = f"All translations (exported {today_str})"
                if task_id:
                    filename = f"tranzor_task_{task_id}_translations_{today_str}{ext}"
                else:
                    filename = f"tranzor_all_translations_{today_str}{ext}"
            else:
                label = f"All changes (exported {today_str})"
                if task_id:
                    filename = f"tranzor_task_{task_id}_{today_str}{ext}"
                else:
                    filename = f"tranzor_all_changes_{today_str}{ext}"

            script_dir = os.path.dirname(os.path.abspath(__file__))
            filepath = os.path.join(script_dir, filename)

            if export_type == "translations":
                export_translations.save_file(rows, filepath, label, fmt)
            else:
                export_changes.save_file(rows, filepath, label, fmt)

            self.root.after(0, self._on_done, filepath, True)

        except Exception as e:
            err_msg = STRINGS[lang]["export_failed"].format(error=e)
            print(f"\n{err_msg}")
            self.root.after(0, self._on_done, None, False)
        finally:
            sys.stdout = old_stdout

    def _on_done(self, filepath, success):
        """Export completion callback (main thread)."""
        self.running = False
        self.progress.stop()
        if IS_MAC:
            self.btn_run.state(["!disabled"])
        else:
            self.btn_run.configure(state="normal", bg=self.ACCENT_BTN)

        if success and filepath:
            self.last_output_path = filepath
            if IS_MAC:
                self.btn_open.state(["!disabled"])
                self.btn_open.configure(style="Success.TButton")
            else:
                self.btn_open.configure(state="normal", fg="#fff",
                                         bg=self.SUCCESS)
            self.status_label.configure(text=self._t("status_done"))
            # Auto-open HTML reports in browser
            if filepath.lower().endswith(".html"):
                open_in_browser(filepath)
        else:
            self.status_label.configure(text=self._t("status_no_data"))

    def _on_open(self):
        if self.last_output_path and os.path.exists(self.last_output_path):
            open_in_browser(self.last_output_path)


# ============================================================
# Entry point
# ============================================================
def main():
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    app = ExportApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
