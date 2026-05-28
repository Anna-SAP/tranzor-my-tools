#!/usr/bin/env python3
"""
Tranzor Translation Exporter — Lightweight Desktop GUI
Uses Python built-in tkinter, zero extra dependencies.
Supports English / Chinese interface language toggle.
"""

# ---------------------------------------------------------------------------
# Startup instrumentation
# ---------------------------------------------------------------------------
# Cold-start has been a moving target — onefile UPX, onedir without UPX,
# Defender scans, etc. Always record per-stage timestamps to a local log so
# the next "EXE takes forever to open" complaint has data to look at instead
# of guesswork. Cost: 4-5 lines per launch in a tiny text file. The first
# entry below intentionally fires at module-import time, before any heavy
# subordinate imports — diff against later entries to see where the time
# actually goes.
import time as _time_for_boot
_BOOT_T0 = _time_for_boot.perf_counter()
_BOOT_STAGES: "list[tuple[str, float]]" = []

def _boot_mark(label: str) -> None:
    """Record a startup-stage timestamp (best-effort, never raises)."""
    try:
        _BOOT_STAGES.append((label, _time_for_boot.perf_counter() - _BOOT_T0))
    except Exception:
        pass

_boot_mark("module_import_start")

import os
import re
import sys
import io
import platform
import threading
import webbrowser
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import date

_boot_mark("stdlib_imports_done")

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


def sanitize_for_filename(name, max_len=40):
    """Coerce a free-form identifier (task name, MR title, …) into a chunk
    that is safe to embed in a filename across Windows / macOS / Linux.

    - Drops characters Windows rejects (<>:"/\\|?*) and control chars.
    - Collapses internal whitespace and runs of separators so the result
      reads cleanly when concatenated with `_` joiners.
    - Trims to ``max_len`` so a 200-char MR title can't blow past the
      Windows MAX_PATH cliff.
    - Returns "" if nothing useful survives — callers should treat that
      as "skip this segment" rather than concatenating an empty token.
    """
    if not name:
        return ""
    s = str(name).strip()
    # Strip filesystem-illegal characters and ASCII control bytes.
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "-", s)
    # Whitespace → single _ so "iva 260520" becomes "iva_260520".
    s = re.sub(r"\s+", "_", s)
    # Collapse runs of dashes/underscores/dots that the cleanup steps left.
    s = re.sub(r"[-_.]{2,}", "-", s)
    # Trim leading/trailing punctuation that would make the join look odd.
    s = s.strip("-_.")
    return s[:max_len]


def reveal_in_folder(filepath):
    """Open the OS file manager with ``filepath`` highlighted in its folder.

    Used after non-HTML exports (Excel / JSON) where the file doesn't render
    in the browser the way HTML reports do — without this the user has no
    visual cue where the file landed and has to dig around the install dir.

    Falls back to opening the containing folder on Linux (xdg-open has no
    "select this file" flag). All failures are swallowed and logged so a
    broken file manager never crashes the export flow.
    """
    abspath = os.path.abspath(filepath)
    folder = os.path.dirname(abspath)
    try:
        if IS_MAC:
            # -R reveals the file in Finder (folder opens with file selected)
            subprocess.Popen(["open", "-R", abspath])
        elif platform.system() == "Windows":
            # Explorer's /select, opens a new window with the file highlighted.
            # Both "/select,<path>" and "/select, <path>" are accepted, so the
            # list form is safe even though argv joining inserts a space.
            subprocess.Popen(["explorer", "/select,", abspath])
        else:
            # Linux: best-effort — just open the containing folder.
            subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        print(f"[reveal] failed for {abspath!r}: {e!r}")


def format_age_days(value, *, now=None) -> str:
    """Format an ISO-ish datetime as a compact "age" string.

    Returns ``""`` for missing / unparsable input so callers can drop the cell
    cleanly. Otherwise produces a coarse human-readable bucket:

        same day → ``today``
        1-30 d   → ``Nd``    (e.g. ``3d``)
        31-365 d → ``Nmo``   (e.g. ``5mo``)
        > 365 d  → ``Ny``    (e.g. ``2y``)

    Negative deltas (clock skew → future timestamp) clamp to ``today``.

    Why we surface this at all: Tranzor Platform's ``DB_SEARCH_EXPIRED_DAYS``
    default moved from 30 → 3650 (≈10 years) in commit ``ad0b263``. Cache-
    backed views now happily return rows that are years old. A raw timestamp
    tells you *when*; an Age column tells you *how stale at a glance* —
    critical for spotting "ancient cache hit vs fresh translation".

    Accepts both naive (``YYYY-MM-DD HH:MM:SS``, ``…T…``) and offset-aware
    ISO strings, and tolerates a trailing ``Z`` defensively.
    """
    if not value:
        return ""
    from datetime import datetime
    text = str(value).strip()
    if not text:
        return ""
    # Common Tranzor shape ``YYYY-MM-DD HH:MM:SS`` is already fromisoformat-
    # friendly. ISO-with-T is also fine. Only quirk is the optional trailing Z
    # which stdlib < 3.11 rejects.
    try:
        norm = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(norm)
        if dt.tzinfo is not None:
            # Convert to local naive for diffing against datetime.now().
            dt = dt.astimezone().replace(tzinfo=None)
        ref = now or datetime.now()
        delta_days = (ref - dt).days
    except Exception:
        return ""
    if delta_days <= 0:
        return "today"
    if delta_days < 31:
        return f"{delta_days}d"
    # The 30-day month / 365-day year approximation creates a nasty
    # boundary near ~360 days: ``days // 365`` is still 0 but ``days // 30``
    # has already hit 12 — a naive year branch would render "0y". Promote
    # to years once we've crossed 12 months but floor to ``1y`` to keep
    # the cell honest.
    months = delta_days // 30
    if months >= 12:
        return f"{max(1, delta_days // 365)}y"
    return f"{months}mo"


try:
    import requests
except ImportError:
    requests = None

_boot_mark("requests_import_done")

# Ensure sibling modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_changes
import export_translations
import gui_tabs

_boot_mark("core_siblings_imported")

# Optional: Tranzor Bridge (loopback HTTP server that hands the HTML report's
# selections off to a Tampermonkey userscript on the Tranzor Platform tab).
# Failure to start (e.g. all ports busy) degrades the report to clipboard/hash
# transport — it must NEVER crash the GUI.
try:
    import tranzor_bridge
except Exception as _bridge_imp_err:  # pragma: no cover
    tranzor_bridge = None
    _bridge_import_error = _bridge_imp_err
else:
    _bridge_import_error = None

# Bridge first-time setup wizard — opens automatically the first time the
# user clicks Send to Tranzor without an installed/live userscript. Optional
# import so a broken module never blocks the rest of the GUI.
try:
    import bridge_setup_wizard as _bridge_wizard
except Exception as _bridge_wizard_imp_err:  # pragma: no cover
    _bridge_wizard = None
    _bridge_wizard_import_error = _bridge_wizard_imp_err
else:
    _bridge_wizard_import_error = None

# Optional: Full Translation Export Tab (nested module, must not break GUI if missing)
try:
    import gui_tab_full_translations as _ft_tab_mod
except Exception as _ft_e:  # pragma: no cover
    _ft_tab_mod = None
    _ft_import_error = _ft_e
else:
    _ft_import_error = None

# Optional: Human Revisions tab
try:
    import gui_tab_human_revisions as _hr_tab_mod
except Exception as _hr_e:  # pragma: no cover
    _hr_tab_mod = None
    _hr_import_error = _hr_e
else:
    _hr_import_error = None

# Optional: Scan Tasks tab (手动触发的 Missing Translation Scan 任务)
try:
    import gui_tab_scan_tasks as _st_tab_mod
except Exception as _st_e:  # pragma: no cover
    _st_tab_mod = None
    _st_import_error = _st_e
else:
    _st_import_error = None

# Optional: Term Watchtower tab (terminology compliance — Phase 1)
try:
    import gui_tab_term_watchtower as _tw_tab_mod
except Exception as _tw_e:  # pragma: no cover
    _tw_tab_mod = None
    _tw_import_error = _tw_e
else:
    _tw_import_error = None

# Optional: TM & Context Insight tab (visualizes Tranzor's TM / Context Service
# black boxes so non-technical language experts can monitor pipeline routing).
try:
    import gui_tab_tm_context_insight as _tci_tab_mod
except Exception as _tci_e:  # pragma: no cover
    _tci_tab_mod = None
    _tci_import_error = _tci_e
else:
    _tci_import_error = None

# Optional: OPUS ID Monitor tab — local SQLite-backed inventory of every
# opus_id Tranzor has produced, with incremental sync from MR + Scan APIs.
# Lets the user see "total / new today / per-project breakdown" without
# digging through individual MR exports.
try:
    import gui_tab_opus_id_monitor as _opus_tab_mod
except Exception as _opus_e:  # pragma: no cover
    _opus_tab_mod = None
    _opus_import_error = _opus_e
else:
    _opus_import_error = None

# Optional: Tranzor Checks tab — issue-level aggregation of task check
# results (Terminology Inconsistency / Parameter Format / …) with sortable
# keyword column, designed to help QA spot false positives at a glance.
try:
    import gui_tab_tranzor_checks as _tc_tab_mod
except Exception as _tc_e:  # pragma: no cover
    _tc_tab_mod = None
    _tc_import_error = _tc_e
else:
    _tc_import_error = None

_boot_mark("optional_tabs_imported")

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
        "output_fmt_json":    "JSON (for QA Audit)",
        "btn_run":            "▶  Start Export",
        "btn_open":           "📂  Open Report",
        "status_ready":       "Ready",
        "status_loading":     "Loading",
        "status_exporting":   "Exporting…",
        "status_done":        "✓ Export complete",
        "status_saved":       "✓ Saved: {filename}",
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
        "summary_refresh":    "🔄",
        "summary_refresh_tip":"Refresh task list",
        "summary_prev":       "Previous",
        "summary_prev_tip":   "Previous page",
        "summary_next":       "Next",
        "summary_next_tip":   "Next page",
        "summary_page_info":  "Page {page} / {total_pages}  ·  {start}-{end} of {total}",
        "summary_page_empty": "Page 0 / 0  ·  No tasks",
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
        "mr_task_id":       "Task ID",
        "mr_search":        "🔍 Search",
        "mr_reset":         "Reset",
        "mr_export":        "📦 Export Selected",
        "mr_sidebar_title": "📊 MR Pipeline Stats",
        "mr_stat_total":    "Total Tasks",
        "mr_stat_completed":"Completed",
        "mr_stat_failed":   "Failed",
        "mr_stat_avg_score":"Avg Score",
        "mr_recent_projects_title":"📦 Recently Added Projects",
        "mr_recent_col_project":   "Project",
        "mr_recent_col_added":     "Added",
        "mr_recent_empty":         "No data yet",
        "time_ago_now":            "just now",
        "time_ago_minutes":        "{n}m ago",
        "time_ago_hours":          "{n}h ago",
        "time_ago_days":           "{n}d ago",
        "time_ago_months":         "{n}mo ago",
        "mr_col_idx":       "#",
        "mr_col_project":   "Project",
        "mr_col_mr":        "MR#",
        "mr_col_release":   "Release",
        "mr_col_status":    "Status",
        "mr_col_avg_score": "Avg Score",
        "mr_col_created":   "Created",
        "mr_col_duration":  "Duration",
        "mr_post_edit_legend": "✏️ = MR contains at least one human-edited translation (post-edit)",
        "summary_post_edit_legend": "✏️ = task contains at least one human-edited translation (post-edit)",
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
        "output_fmt_json":    "JSON（用于 QA 审计）",
        "btn_run":            "▶  开始导出",
        "btn_open":           "📂  打开报告",
        "status_ready":       "就绪",
        "status_loading":     "加载中",
        "status_exporting":   "正在导出…",
        "status_done":        "✓ 导出完成",
        "status_saved":       "✓ 已保存：{filename}",
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
        "summary_refresh":    "🔄",
        "summary_refresh_tip":"刷新任务列表",
        "summary_prev":       "上一页",
        "summary_prev_tip":   "上一页",
        "summary_next":       "下一页",
        "summary_next_tip":   "下一页",
        "summary_page_info":  "第 {page}/{total_pages} 页  ·  {start}-{end} / {total}",
        "summary_page_empty": "第 0/0 页  ·  暂无任务",
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
        "mr_task_id":       "Task ID",
        "mr_search":        "🔍 查询",
        "mr_reset":         "重置",
        "mr_export":        "📦 导出选中",
        "mr_sidebar_title": "📊 MR Pipeline 统计",
        "mr_stat_total":    "总任务数",
        "mr_stat_completed":"已完成",
        "mr_stat_failed":   "失败",
        "mr_stat_avg_score":"平均分",
        "mr_recent_projects_title":"📦 最新支持的项目",
        "mr_recent_col_project":   "项目",
        "mr_recent_col_added":     "接入时间",
        "mr_recent_empty":         "暂无数据",
        "time_ago_now":            "刚刚",
        "time_ago_minutes":        "{n} 分钟前",
        "time_ago_hours":          "{n} 小时前",
        "time_ago_days":           "{n} 天前",
        "time_ago_months":         "{n} 个月前",
        "mr_col_idx":       "#",
        "mr_col_project":   "项目",
        "mr_col_mr":        "MR#",
        "mr_col_release":   "版本",
        "mr_col_status":    "状态",
        "mr_col_avg_score": "平均分",
        "mr_col_created":   "创建时间",
        "mr_col_duration":  "耗时",
        "mr_post_edit_legend": "✏️ = 该 MR 至少含一条经过人工编辑（post-edit）的翻译",
        "summary_post_edit_legend": "✏️ = 该任务至少含一条经过人工编辑（post-edit）的翻译",
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

# Merge in strings from the optional Human Revisions tab (non-destructive).
if _hr_tab_mod is not None:
    try:
        for _lang_code, _extra in _hr_tab_mod.STRINGS.items():
            STRINGS.setdefault(_lang_code, {}).update(_extra)
    except Exception:
        pass

# Merge in strings from the optional Scan Tasks tab (non-destructive).
if _st_tab_mod is not None:
    try:
        for _lang_code, _extra in _st_tab_mod.STRINGS.items():
            STRINGS.setdefault(_lang_code, {}).update(_extra)
    except Exception:
        pass

# Merge in strings from the optional Term Watchtower tab (non-destructive).
if _tw_tab_mod is not None:
    try:
        for _lang_code, _extra in _tw_tab_mod.STRINGS.items():
            STRINGS.setdefault(_lang_code, {}).update(_extra)
    except Exception:
        pass

# Merge in strings from the optional TM & Context Insight tab.
if _tci_tab_mod is not None:
    try:
        for _lang_code, _extra in _tci_tab_mod.STRINGS.items():
            STRINGS.setdefault(_lang_code, {}).update(_extra)
    except Exception:
        pass

# Merge in strings from the optional OPUS ID Monitor tab.
if _opus_tab_mod is not None:
    try:
        for _lang_code, _extra in _opus_tab_mod.STRINGS.items():
            STRINGS.setdefault(_lang_code, {}).update(_extra)
    except Exception:
        pass

# Merge in strings from the optional Tranzor Checks tab.
if _tc_tab_mod is not None:
    try:
        for _lang_code, _extra in _tc_tab_mod.STRINGS.items():
            STRINGS.setdefault(_lang_code, {}).update(_extra)
    except Exception:
        pass


# ============================================================
# TextRedirector — forward print() to tkinter Text widget
# ============================================================
class Tooltip:
    """Lightweight hover tooltip for tk / ttk widgets. Zero-dependency."""

    def __init__(self, widget, text="", delay=450):
        self.widget = widget
        self._text = text
        self.delay = delay
        self._tip = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text):
        self._text = text or ""
        if self._tip is not None:
            for child in self._tip.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(text=self._text)

    def _schedule(self, _event=None):
        self._cancel()
        if self._text:
            self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip is not None or not self._text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        try:
            tw.wm_attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(
            tw, text=self._text,
            background="#1e2a44", foreground="#e4e7ef",
            relief="solid", borderwidth=1,
            font=(FONT_FAMILY, 9),
            padx=8, pady=4,
        ).pack()
        self._tip = tw

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


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
        self.bridge = None
        self.bridge_error = None
        self.summary_loading = False
        self.summary_tasks = []
        self.summary_total = 0
        self.summary_page = 0
        self.summary_page_size = self.SUMMARY_DEFAULT_PAGE_SIZE
        self.summary_selected_task_id = None
        self.summary_resize_job = None

        # Setup
        _boot_mark("ExportApp_init_start")
        self._setup_styles()
        _boot_mark("styles_done")
        self._build_ui()
        _boot_mark("build_ui_done")
        self._refresh_ui_text()
        _boot_mark("refresh_text_done")
        # Bridge boot moved off the main thread — port scanning + atomic
        # writes to ~/.tranzor_bridge/port.json used to cost ~100-300ms in
        # __init__ before the window could draw. The watchdog below already
        # tolerates self.bridge=None during the brief async startup window.
        self._start_bridge_async()
        # Bridge-setup auto-trigger: per-session "already prompted" guard
        # so a dismissed wizard doesn't re-pop on every poll within the same
        # session. Cross-session re-trigger is decided by the heuristic in
        # bridge_setup_wizard.should_auto_open_wizard(), not by this flag.
        self._bridge_wizard_shown_this_session = False
        self._bridge_wizard_instance = None
        self._start_bridge_watchdog()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

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

        # Lazy-load MR Pipeline / Quality Overview / Full Translations data
        # when their tabs are first selected
        self._mr_tab_initialized = False
        self._qa_tab_initialized = False
        self._ft_tab_initialized = False
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

        # --- Tab 5: Human Revisions (optional, pure additive) ---
        self.hr_tab = None
        self._hr_tab_initialized = False
        if _hr_tab_mod is not None:
            try:
                tab_hr = ttk.Frame(self.notebook, style="App.TFrame")
                self.notebook.add(tab_hr, text="")
                self.hr_tab = _hr_tab_mod.HumanRevisionsTab(tab_hr, self)
            except Exception as _e:
                print(f"[Human Revisions tab] init failed: {_e}")
                self.hr_tab = None

        # --- Tab 6: Scan Tasks (optional, pure additive) ---
        # 独立显示 Missing Translation Scan 手动触发的扫描任务，与 MR Pipeline
        # 互不干扰。放在最后以避免影响已有 tab 的动态 index 计算。
        self.st_tab = None
        self._st_tab_initialized = False
        self._st_tab_index = None
        if _st_tab_mod is not None:
            try:
                tab_st = ttk.Frame(self.notebook, style="App.TFrame")
                self.notebook.add(tab_st, text="")
                self.st_tab = _st_tab_mod.ScanTasksTab(tab_st, self)
                self._st_tab_index = self.notebook.index(tab_st)
            except Exception as _e:
                print(f"[Scan Tasks tab] init failed: {_e}")
                self.st_tab = None

        # --- Tab 7: Term Watchtower (optional, pure additive) ---
        self.tw_tab = None
        self._tw_tab_index = None
        if _tw_tab_mod is not None:
            try:
                tab_tw = ttk.Frame(self.notebook, style="App.TFrame")
                self.notebook.add(tab_tw, text="")
                self.tw_tab = _tw_tab_mod.TermWatchtowerTab(tab_tw, self)
                self._tw_tab_index = self.notebook.index(tab_tw)
            except Exception as _e:
                print(f"[Term Watchtower tab] init failed: {_e}")
                self.tw_tab = None

        # --- Tab 8: TM & Context Insight (optional, pure additive) ---
        # 可视化 Tranzor TM / Context Service 黑盒，让语言专家直观看到管线路由。
        self.tci_tab = None
        self._tci_tab_initialized = False
        self._tci_tab_index = None
        if _tci_tab_mod is not None:
            try:
                tab_tci = ttk.Frame(self.notebook, style="App.TFrame")
                self.notebook.add(tab_tci, text="")
                self.tci_tab = _tci_tab_mod.TmContextInsightTab(tab_tci, self)
                self._tci_tab_index = self.notebook.index(tab_tci)
            except Exception as _e:
                print(f"[TM & Context Insight tab] init failed: {_e}")
                self.tci_tab = None

        # --- Tab 9: OPUS ID Monitor (optional, pure additive) ---
        # 本地 SQLite 缓存 Tranzor 出过的所有 opus_id，随时随地看总量 / 新增 /
        # 按项目分布；首屏纯本地读，不依赖网络。
        self.opus_tab = None
        self._opus_tab_index = None
        if _opus_tab_mod is not None:
            try:
                tab_opus = ttk.Frame(self.notebook, style="App.TFrame")
                self.notebook.add(tab_opus, text="")
                self.opus_tab = _opus_tab_mod.OpusIdMonitorTab(tab_opus, self)
                self._opus_tab_index = self.notebook.index(tab_opus)
            except Exception as _e:
                print(f"[OPUS ID Monitor tab] init failed: {_e}")
                self.opus_tab = None

        # --- Tab 10: Tranzor Checks (optional, pure additive) ---
        # 全量任务 Checks 状态 + 错误关键词聚合，让 QA 一眼归类 Terminology /
        # Parameter Format 等问题，识别误报；本地 SQLite 缓存，首屏纯本地读。
        self.tc_tab = None
        self._tc_tab_index = None
        if _tc_tab_mod is not None:
            try:
                tab_tc = ttk.Frame(self.notebook, style="App.TFrame")
                self.notebook.add(tab_tc, text="")
                self.tc_tab = _tc_tab_mod.TranzorChecksTab(tab_tc, self)
                self._tc_tab_index = self.notebook.index(tab_tc)
            except Exception as _e:
                print(f"[Tranzor Checks tab] init failed: {_e}")
                self.tc_tab = None

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
        self.export_type_var = tk.StringVar(value="translations")
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
        self.rb_xlsx.pack(side="left", padx=(0, 16))
        # JSON 选项：透视为 {key, en-US, de-DE, ...} 供翻译 QA Skill 直接消费
        self.rb_json = ttk.Radiobutton(row2, text="",
                         variable=self.fmt_var, value="json",
                         style="Card.TRadiobutton")
        self.rb_json.pack(side="left")

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

        # Legend for the ✏️ marker the async post-edit prefetch may
        # prepend to Task Name once detail fetches return.
        self.lbl_summary_post_edit_legend = ttk.Label(
            inner, text="", style="SummaryStatus.TLabel")
        self.lbl_summary_post_edit_legend.pack(anchor="w", pady=(0, 4))

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

        # ── Pagination + refresh controls (two-row: info on top, actions below) ──
        btn_bar = ttk.Frame(inner, style="Summary.TFrame")
        btn_bar.pack(fill="x", pady=(10, 0))

        # Row 1 — page info; wraps on narrow widths so it never squeezes buttons.
        self.lbl_summary_page = ttk.Label(
            btn_bar, text="", style="SummaryStatus.TLabel",
            wraplength=280, justify="left")
        self.lbl_summary_page.pack(side="top", fill="x", anchor="w", pady=(0, 6))
        # Keep wraplength in sync with actual available width on resize.
        btn_bar.bind(
            "<Configure>",
            lambda e: self.lbl_summary_page.configure(
                wraplength=max(120, e.width - 8)))

        # Row 2 — action buttons, right-aligned via an expanding spacer.
        actions_bar = ttk.Frame(btn_bar, style="Summary.TFrame")
        actions_bar.pack(side="top", fill="x")
        ttk.Frame(actions_bar, style="Summary.TFrame").pack(
            side="left", fill="x", expand=True)

        self.btn_summary_prev = self._create_button(
            actions_bar, text="", command=self._prev_summary_page,
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 9),
            bg=self.ACCENT, fg="#ccc", activebackground="#1a3a6a",
            activeforeground="#fff", padx=10, pady=3, state="disabled")
        self.btn_summary_prev.pack(side="left", padx=(0, 6))

        self.btn_summary_next = self._create_button(
            actions_bar, text="", command=self._next_summary_page,
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 9),
            bg=self.ACCENT, fg="#ccc", activebackground="#1a3a6a",
            activeforeground="#fff", padx=10, pady=3, state="disabled")
        self.btn_summary_next.pack(side="left", padx=(0, 6))

        # Refresh is an icon-only button — text saved for tooltip / a11y.
        self.btn_refresh = self._create_button(
            actions_bar, text="", command=self._load_summary_data,
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 11),
            bg=self.ACCENT, fg="#ccc", activebackground="#1a3a6a",
            activeforeground="#fff", padx=10, pady=3)
        self.btn_refresh.pack(side="left")

        # Hover tooltips (text assigned via _refresh_ui_text for i18n).
        self._tip_summary_prev = Tooltip(self.btn_summary_prev)
        self._tip_summary_next = Tooltip(self.btn_summary_next)
        self._tip_summary_refresh = Tooltip(self.btn_refresh)

        self._update_summary_pager()

    # ── i18n: refresh all visible text ──
    def _refresh_ui_text(self):
        """Update all UI widget texts to the current language.

        Important: every per-tab ``refresh_text()`` is scheduled via ``after(0, …)``
        rather than called synchronously. The startup-log evidence from #66
        showed those nested calls (which re-run ``_refresh_from_cache`` ->
        SQLite query -> treeview rebuild for OPUS Monitor / Tranzor Checks /
        TM Context Insight / Term Watchtower) consumed ~15 s on the main
        thread during ``ExportApp.__init__``, which then blocked the first
        frame paint for another ~17 s. Deferring them lets the main window
        draw immediately and lets the tab text refresh ripple through the
        Tk event loop in the background. Language-toggle UX is unaffected —
        ``after(0, …)`` fires on the next idle, so flips remain instantaneous
        from the user's perspective.
        """
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
        self.rb_json.configure(text=self._t("output_fmt_json"))
        self.btn_run.configure(text=self._t("btn_run"))
        self.btn_open.configure(text=self._t("btn_open"))
        self.lbl_log_header.configure(text=self._t("log_header"))
        self.lbl_footer.configure(text=self._t("footer"))
        self.btn_lang.configure(text=self._t("lang_toggle"))

        # Helper: schedule a tab's refresh_text on the next idle tick so it
        # cannot stall the current frame. Wraps the call in try/except so a
        # single broken tab can't take the whole batch down.
        def _async_refresh(tab):
            def _runner():
                try:
                    tab.refresh_text()
                except Exception as exc:  # pragma: no cover
                    print(f"[refresh_text] {tab!r} failed: {exc!r}")
            self.root.after(0, _runner)

        # Notebook tab titles (synchronous — these are cheap, single calls).
        self.notebook.tab(0, text=self._t("tab_file_translation"))
        self.notebook.tab(1, text=self._t("tab_mr_pipeline"))
        self.notebook.tab(2, text=self._t("tab_quality_overview"))
        if self.ft_tab is not None:
            try:
                self.notebook.tab(3, text=self._t("tab_full_translations"))
                _async_refresh(self.ft_tab)
            except Exception:
                pass
        if self.hr_tab is not None:
            try:
                # HR tab index depends on whether Full Translations tab exists
                hr_idx = 4 if self.ft_tab is not None else 3
                self.notebook.tab(hr_idx, text=self._t("tab_human_revisions"))
                _async_refresh(self.hr_tab)
            except Exception:
                pass
        if self.st_tab is not None and self._st_tab_index is not None:
            try:
                self.notebook.tab(self._st_tab_index, text=self._t("tab_scan_tasks"))
                _async_refresh(self.st_tab)
            except Exception:
                pass
        if self.tw_tab is not None and self._tw_tab_index is not None:
            try:
                self.notebook.tab(self._tw_tab_index, text=self._t("tab_term_watchtower"))
                _async_refresh(self.tw_tab)
            except Exception:
                pass
        if self.tci_tab is not None and self._tci_tab_index is not None:
            try:
                self.notebook.tab(self._tci_tab_index, text=self._t("tab_tm_context_insight"))
                _async_refresh(self.tci_tab)
            except Exception:
                pass
        if self.opus_tab is not None and self._opus_tab_index is not None:
            try:
                self.notebook.tab(self._opus_tab_index, text=self._t("tab_opus_monitor"))
                _async_refresh(self.opus_tab)
            except Exception:
                pass
        if self.tc_tab is not None and self._tc_tab_index is not None:
            try:
                self.notebook.tab(self._tc_tab_index, text=self._t("tab_tranzor_checks"))
                _async_refresh(self.tc_tab)
            except Exception:
                pass

        # Summary panel texts
        self.lbl_summary_title.configure(text=self._t("summary_title"))
        self.lbl_total_label.configure(text=self._t("summary_total"))
        self.lbl_recent_header.configure(text=self._t("summary_recent"))
        self.lbl_summary_post_edit_legend.configure(
            text=self._t("summary_post_edit_legend"))
        self.btn_summary_prev.configure(text=self._t("summary_prev"))
        self.btn_summary_next.configure(text=self._t("summary_next"))
        self.btn_refresh.configure(text=self._t("summary_refresh"))
        self._tip_summary_prev.set_text(self._t("summary_prev_tip"))
        self._tip_summary_next.set_text(self._t("summary_next_tip"))
        self._tip_summary_refresh.set_text(self._t("summary_refresh_tip"))
        self.task_tree.heading("id", text=self._t("summary_col_id"))
        self.task_tree.heading("name", text=self._t("summary_col_name"))
        self.task_tree.heading("creator", text=self._t("summary_col_creator"))
        self._update_summary_pager()

        # MR Pipeline & Quality Overview tab texts — also async for the same
        # reason: their refresh_text re-renders sidebars / project lists.
        _async_refresh(self.mr_tab)
        _async_refresh(self.qa_tab)

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

        # Reset row mapping; populated below so the async post-edit
        # prefetch callback can patch Task Name when detail returns.
        self._summary_row_iid_by_task: dict[str, str] = {}
        prefetch_items: list[tuple[str, str]] = []

        # Late import — task_post_edit is its own module and keeps the
        # main GUI cold start cheap.
        import task_post_edit as _tpe_local

        for task in page_tasks:
            tid = task.get("id", "")
            tname = task.get("task_name", "")
            creator = task.get("created_by", "") or task.get("creator", "") or "-"
            # Synchronous render when we've already cached the answer.
            cached = (
                _tpe_local.get_cache().get("legacy", tid)
                if tid else None
            )
            display_name = (
                _tpe_local.POST_EDIT_PREFIX + tname if cached else tname
            )
            iid = self.task_tree.insert(
                "", "end",
                iid=str(tid) if tid else None,
                values=(tid, display_name, creator),
            )
            if tid:
                self._summary_row_iid_by_task[str(tid)] = iid
                if cached is None:
                    prefetch_items.append(("legacy", str(tid)))

        if prefetch_items:
            _tpe_local.prefetch_async(
                prefetch_items,
                on_result=self._on_summary_post_edit_result,
                max_workers=4,
            )

        self._restore_summary_selection_if_possible()
        self._update_summary_pager()

    def _on_summary_post_edit_result(self, kind, task_id, has_post_edit):
        """Worker-thread callback from the post-edit prefetch — marshal
        back to Tk before touching widgets."""
        if not has_post_edit:
            return
        try:
            self.task_tree.after(
                0, self._apply_summary_post_edit_prefix, str(task_id),
            )
        except Exception:
            pass

    def _apply_summary_post_edit_prefix(self, task_id):
        import task_post_edit as _tpe_local
        iid = getattr(self, "_summary_row_iid_by_task", {}).get(task_id)
        if not iid:
            return
        try:
            vals = list(self.task_tree.item(iid, "values"))
        except tk.TclError:
            return
        if len(vals) < 2:
            return
        name = vals[1] or ""
        if name.startswith(_tpe_local.POST_EDIT_PREFIX):
            return
        vals[1] = _tpe_local.POST_EDIT_PREFIX + name
        try:
            self.task_tree.item(iid, values=vals)
        except tk.TclError:
            pass

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
        """Lazy-load data when MR Pipeline / Quality Overview / Full Translations
        tab is first selected.

        For Full Translations: only the **lightweight** product+language
        inventory is loaded here (no /translations endpoints), so the panel
        is interactive within ~1–2s. Heavy translation data is fetched only
        on Export click.
        """
        tab_idx = self.notebook.index(self.notebook.select())
        if tab_idx == 1 and not self._mr_tab_initialized:
            self._mr_tab_initialized = True
            self.mr_tab.load_filters()
            self.mr_tab._load_overview()
            self.mr_tab.load_initial_tasks()
        elif tab_idx == 2 and not self._qa_tab_initialized:
            self._qa_tab_initialized = True
            self.qa_tab.load_filters()
        elif tab_idx == 3 and not self._ft_tab_initialized:
            self._ft_tab_initialized = True
            if self.ft_tab is not None:
                try:
                    self.ft_tab.on_first_show()
                except Exception:
                    pass
        else:
            # Human Revisions tab — dynamic index (4 if ft_tab exists, else 3)
            hr_idx = 4 if self.ft_tab is not None else 3
            if tab_idx == hr_idx and not self._hr_tab_initialized:
                self._hr_tab_initialized = True
                if self.hr_tab is not None:
                    try:
                        self.hr_tab.on_first_show()
                    except Exception:
                        pass
            elif (self.st_tab is not None
                  and self._st_tab_index is not None
                  and tab_idx == self._st_tab_index
                  and not self._st_tab_initialized):
                self._st_tab_initialized = True
                try:
                    self.st_tab.on_first_show()
                except Exception:
                    pass
            elif (self.tci_tab is not None
                  and self._tci_tab_index is not None
                  and tab_idx == self._tci_tab_index
                  and not self._tci_tab_initialized):
                self._tci_tab_initialized = True
                try:
                    self.tci_tab.on_first_show()
                except Exception:
                    pass

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
            ext = {"xlsx": ".xlsx", "json": ".json"}.get(fmt, ".html")
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

            bridge_info = self._bridge_info_for_export()
            # open_after=False — _on_done handles the auto-open so we don't
            # spawn two browser tabs for the same report.
            # Capture the actual saved path so the UI reflects PermissionError
            # renames (e.g. "..._1.json") instead of pointing at the stale
            # filename the GUI originally requested.
            if export_type == "translations":
                saved = export_translations.save_file(
                    rows, filepath, label, fmt,
                    bridge_info=bridge_info, open_after=False)
            else:
                saved = export_changes.save_file(
                    rows, filepath, label, fmt, open_after=False)

            self.root.after(0, self._on_done, saved or filepath, True)

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
            # Show the actual filename so the user immediately sees where the
            # export went (the old "✓ Export complete" gave no hint at all).
            basename = os.path.basename(filepath)
            self.status_label.configure(
                text=self._t("status_saved").format(filename=basename))
            lower = filepath.lower()
            if lower.endswith(".html"):
                # HTML self-renders in a browser tab — that's enough wayfinding.
                open_in_browser(filepath)
            else:
                # JSON / Excel don't render visibly: pop the containing folder
                # so the user can grab the file (drag into chat, attach to
                # email, etc.) without hunting through the install dir.
                reveal_in_folder(filepath)
        else:
            self.status_label.configure(text=self._t("status_no_data"))

    def _on_open(self):
        if self.last_output_path and os.path.exists(self.last_output_path):
            open_in_browser(self.last_output_path)

    # ------------------------------------------------------------------
    # Tranzor Bridge integration
    # ------------------------------------------------------------------
    def _start_bridge_async(self):
        """Boot the loopback bridge on a background thread so the main window
        can draw without waiting for port scans / file I/O. Until the thread
        finishes, ``self.bridge`` stays ``None`` and consumers (export flow,
        watchdog) already handle that case as the "no bridge yet" fallback.
        """
        if tranzor_bridge is None:
            self.bridge_error = f"bridge module unavailable: {_bridge_import_error!r}"
            print(f"[bridge] disabled: {self.bridge_error}")
            return
        threading.Thread(
            target=self._start_bridge_worker, daemon=True, name="bridge-startup"
        ).start()

    def _start_bridge_worker(self):
        """Background worker that actually starts the bridge. Runs at most
        once per session. Tk state is only touched via ``root.after`` so we
        stay on the main thread for any UI updates that follow."""
        try:
            bridge, err = tranzor_bridge.try_start_bridge()
        except Exception as exc:  # pragma: no cover — defensive
            self.bridge = None
            self.bridge_error = f"unexpected: {exc!r}"
            print(f"[bridge] startup raised: {exc!r}; Send-to-Tranzor will use clipboard fallback")
            return
        if err:
            self.bridge = None
            self.bridge_error = err
            print(f"[bridge] startup failed ({err}); Send-to-Tranzor will use clipboard fallback")
            return
        self.bridge = bridge
        print(
            f"[bridge] listening on http://127.0.0.1:{bridge.port}  "
            f"instance_id={bridge.instance_id}"
        )

    def _bridge_info_for_export(self):
        if self.bridge is None:
            return None
        try:
            return self.bridge.html_info()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Bridge setup auto-trigger
    # ------------------------------------------------------------------
    BRIDGE_WATCHDOG_INTERVAL_MS = 3000  # 3s — reacts within ~5s of threshold

    def _start_bridge_watchdog(self):
        """Begin a recurring poll that auto-opens the first-time setup
        wizard the moment we detect a "Send to Tranzor" click that the
        userscript clearly couldn't handle.

        The watchdog stays cheap (status_snapshot is an in-process method
        call, no I/O) and never auto-pops more than once per session. The
        cross-session decision lives in the wizard module's heuristic so
        this loop has no policy of its own.
        """
        # Guard: if the wizard module didn't import or the bridge never
        # came up, there is nothing for the watchdog to do. We still call
        # ``after`` once so a later bridge restart could pick up — but
        # this path is currently a no-op because we don't reattempt bridge
        # startup at runtime.
        if _bridge_wizard is None:
            return
        self._bridge_watchdog_tick()

    def _bridge_watchdog_tick(self):
        try:
            if (
                not self._bridge_wizard_shown_this_session
                and self.bridge is not None
                and _bridge_wizard is not None
                and _bridge_wizard.should_auto_open_wizard(self.bridge)
            ):
                self._open_bridge_setup_wizard(force=True)
        except Exception as exc:  # pragma: no cover
            # The watchdog must never bubble — a corrupt snapshot would
            # otherwise tear down the entire Tk after-loop.
            print(f"[bridge-watchdog] tick failed: {exc!r}")
        finally:
            self.root.after(
                self.BRIDGE_WATCHDOG_INTERVAL_MS,
                self._bridge_watchdog_tick,
            )

    def _open_bridge_setup_wizard(self, *, force: bool = False):
        """Open the first-time setup wizard. ``force=True`` bypasses the
        auto-trigger heuristic; callers that want the heuristic should
        instead call :func:`bridge_setup_wizard.open_wizard_if_needed`.
        """
        if _bridge_wizard is None or self.bridge is None:
            return
        # If an instance is already open (user navigating slowly), don't
        # spawn another — bring the existing one forward instead.
        if self._bridge_wizard_instance is not None:
            try:
                if self._bridge_wizard_instance.winfo_exists():
                    self._bridge_wizard_instance.lift()
                    self._bridge_wizard_instance.focus_set()
                    return
            except Exception:
                pass
            self._bridge_wizard_instance = None
        self._bridge_wizard_shown_this_session = True
        try:
            self._bridge_wizard_instance = _bridge_wizard.BridgeSetupWizard(
                self.root,
                bridge=self.bridge,
                app=self,
                lang=self.lang,
            )
        except Exception as exc:  # pragma: no cover
            print(f"[bridge-wizard] open failed: {exc!r}")
            self._bridge_wizard_instance = None

    def _on_close(self):
        try:
            if self.bridge is not None:
                self.bridge.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


# ============================================================
# Entry point
# ============================================================
def _flush_boot_log() -> None:
    """Persist per-stage startup timestamps so we can diagnose slow launches
    after the fact. Best-effort: any failure here is silently swallowed —
    diagnostics must never become the reason the GUI didn't open.

    File: ``~/.tranzor_exporter/startup.log`` (rotates by simple truncation
    when it grows past 64 KB so it can't balloon over time)."""
    try:
        base = os.path.join(os.path.expanduser("~"), ".tranzor_exporter")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "startup.log")
        # Truncate if too big — we only need the last few launches to debug.
        try:
            if os.path.getsize(path) > 64 * 1024:
                with open(path, "w", encoding="utf-8") as _trunc:
                    _trunc.write("")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            import datetime as _dt
            stamp = _dt.datetime.now().isoformat(timespec="seconds")
            f.write(f"--- launch {stamp} pid={os.getpid()} ---\n")
            last = 0.0
            for label, t in _BOOT_STAGES:
                f.write(f"  {t*1000:7.0f}ms  (+{(t-last)*1000:6.0f}ms)  {label}\n")
                last = t
            f.write("\n")
    except Exception:
        pass


def main():
    _boot_mark("main_entry")
    root = tk.Tk()
    _boot_mark("tk_Tk_done")
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    app = ExportApp(root)
    _boot_mark("ExportApp_constructed")
    # Don't force a synchronous root.update() here — when there are 1000+
    # widgets across 10 tabs, that single call can take 15-20 s while Tk
    # paints everything. Instead, let mainloop draw the window naturally
    # and schedule the boot-log flush for the first idle tick inside the
    # loop. That timestamp ("first_idle_in_mainloop") tells us how long
    # Tk took to reach "ready to handle input" from the user's POV.
    def _on_first_idle():
        _boot_mark("first_idle_in_mainloop")
        _flush_boot_log()
    root.after_idle(_on_first_idle)
    root.mainloop()


if __name__ == "__main__":
    main()
