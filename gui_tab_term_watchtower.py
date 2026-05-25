"""
Term Watchtower — Tkinter GUI tab.

Builds on terminology_watchtower.py (pure logic). Reuses my-tools' existing
ttk style, fonts, and threading conventions. See Terminology_Watchtower_PRD.md.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import ttk, filedialog, messagebox
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terminology_watchtower as tw

try:
    import tranzor_terminology as tz_term
except Exception:
    tz_term = None
from export_gui import FONT_FAMILY, IS_MAC

# Optional: reuse existing FT collection path for live scans.
try:
    import export_full_translations as _ft_api
except Exception:
    _ft_api = None

try:
    import export_translations as _legacy_api
except Exception:
    _legacy_api = None

try:
    import export_mr_pipeline as _mr_api
except Exception:
    _mr_api = None


def _collect_single_task(task_id: str, source_kind: str, progress_cb=None):
    """Build a small FullTranslationInventory for ONE task only.

    Used by Quick Check / Scan Now when the user pastes a single Task ID
    instead of a project filter. Reuses existing read-only fetch helpers;
    no Tranzor data is mutated.
    """
    if _ft_api is None:
        raise RuntimeError("Full translation collector not available.")
    inv = _ft_api.FullTranslationInventory()
    src_locale = getattr(_ft_api, "SOURCE_LOCALE", "en-US")

    if source_kind == "legacy":
        if _legacy_api is None:
            raise RuntimeError("Legacy API not available.")
        if progress_cb:
            progress_cb(f"[Legacy] fetching task {task_id}…")
        entries = _legacy_api.fetch_all_translations(task_id) or []
        inv.ingest_entries(
            entries,
            opus_key="opus_id",
            locale_key="target_language",
            value_key="translated_text",
            source_locale=src_locale,
            source_meta={"source": "Legacy", "task_id": str(task_id)},
        )
    elif source_kind == "mr":
        if _mr_api is None:
            raise RuntimeError("MR API not available.")
        if progress_cb:
            progress_cb(f"[MR] fetching task {task_id}…")
        results = _mr_api.fetch_mr_results(task_id) or {}
        inv.ingest_entries(
            results.get("translations", []),
            opus_key="opus_id",
            locale_key="target_language",
            value_key="translated_text",
            source_locale=src_locale,
            source_meta={"source": "MR", "task_id": str(task_id)},
        )
    elif source_kind == "scan":
        if _mr_api is None:
            raise RuntimeError("MR API not available.")
        if progress_cb:
            progress_cb(f"[Scan] fetching task {task_id}…")
        results = _mr_api.fetch_scan_results(task_id) or {}
        inv.ingest_entries(
            results.get("translations", []),
            opus_key="opus_id",
            locale_key="target_language",
            value_key="translated_text",
            source_locale=src_locale,
            source_meta={"source": "Scan", "task_id": str(task_id)},
        )
    else:
        raise ValueError(f"Unsupported source kind: {source_kind}")
    return inv


# ============================================================
# i18n strings (merged into export_gui.STRINGS at startup)
# ============================================================
STRINGS = {
    "en": {
        "tab_term_watchtower":       "🛡 Term Watchtower",
        "tw_subtab_issues":          "Issues",
        "tw_subtab_glossary":        "Glossary from Import",
        "tw_btn_scan":               "▶  Scan Now for Imported Glossary",
        "tw_btn_import":             "📥  Import Glossary",
        "tw_btn_export_template":    "Export Template",
        "tw_btn_export_glossary":    "Export Glossary",
        "tw_btn_export_evidence":    "📤  Export Evidence",
        "tw_btn_quick_check":        "⚡  Quick Check",
        "tw_qc_title":               "Quick Check — Ad-hoc Term",
        "tw_qc_source":              "Source Term",
        "tw_qc_severity":            "Severity",
        "tw_qc_pairs_label":         "Locale → Approved Translation",
        "tw_qc_pairs_help":          "One pair per line. Accepts tab/comma/→/->. Paste from Excel works.",
        "tw_qc_paste_clipboard":     "Paste from clipboard",
        "tw_qc_reorg":               "🪄 Reorg",
        "tw_qc_reorg_tip":           "Smart-reformat pasted text into locale↔translation pairs",
        "tw_qc_reorg_failed":        "Could not detect locale tokens — please fix manually.",
        "tw_qc_clear":               "🧹 Clear",
        "tw_btn_tranzor_term":       "🌐  Tranzor Terminology",
        "tw_tt_title":               "Tranzor Terminology — Pick Terms to Check",
        "tw_tt_loading":              "Loading… {n}/{total}",
        "tw_tt_loaded":              "{n} terms loaded.",
        "tw_tt_filter":              "Filter",
        "tw_tt_filter_dnt":          "DNT",
        "tw_tt_dnt_any":             "(any)",
        "tw_tt_dnt_yes":             "DNT only",
        "tw_tt_dnt_no":              "Translatable only",
        "tw_tt_select_all":          "Select All",
        "tw_tt_select_none":         "Clear",
        "tw_tt_select_visible":      "Select Visible",
        "tw_tt_col_name":            "Term",
        "tw_tt_col_scope":           "Scope",
        "tw_tt_col_locales":         "Locales",
        "tw_tt_col_dnt":             "DNT",
        "tw_tt_severity":            "Severity",
        "tw_tt_run":                 "Run Scan with Selected ({n})…",
        "tw_tt_no_selection":        "Select at least one term.",
        "tw_tt_fetching_details":    "Fetching {n} term details…",
        "tw_tt_running":             "Tranzor Terminology — {n} term(s), {r} rule(s)",
        "tw_tt_error":               "Failed to load terminology: {err}",
        "tw_tt_unavailable":         "Tranzor Terminology client not available.",
        "tw_tt_skipped_dnt":         "Skipped {n} DNT term(s).",
        "tw_tt_double_click_hint":   "Tip: double-click a term to view its definition, pulled live from Tranzor Platform.",
        # ── Term Definition dialog (opened by double-click) ──
        "tw_td_title":               "Term Definition — {name}",
        "tw_td_loading":             "Loading definition…",
        "tw_td_error":               "Failed to load: {err}",
        "tw_td_empty":               "No details available.",
        "tw_td_dnt_yes":             "DNT: Yes",
        "tw_td_dnt_no":              "DNT: No",
        "tw_td_definition":          "Definition",
        "tw_td_context":             "Context",
        "tw_td_part_of_speech":      "Part of speech",
        "tw_td_reference":           "Reference",
        "tw_td_remarks":             "Remarks",
        "tw_td_notes":               "Notes",
        "tw_td_translations":        "Translations",
        "tw_td_variants":            "Variants",
        "tw_td_col_lang":            "Language",
        "tw_td_col_text":            "Text",
        "tw_td_col_remarks":         "Remarks",
        "tw_td_col_type":            "Type",
        "tw_td_col_name":            "Name",
        "tw_td_copy_btn":            "📋 Copy",
        "tw_td_copied":              "✓ Copied",
        "tw_td_open_browser":        "Open Terminology Page",
        "tw_td_open_failed":         "Failed to open page: {err}",
        "tw_td_close":               "Close",
        "tw_td_updated_at":          "Last updated",
        "tw_td_updated_at_unknown":  "—",
        "tw_td_author_unavailable":  "Tranzor Platform does not record creator / updater user identity — only the last-updated timestamp is exposed by the API.",
        "tw_qc_run":                 "Run Quick Scan…",
        "tw_qc_no_pairs":            "Provide at least one (locale, approved translation) pair.",
        "tw_qc_running":             "Quick Check on '{term}' ({n} locales)…",
        "tw_import_choose_mode":     "A glossary already exists ({n} rules).\nHow do you want to apply the new file?",
        "tw_import_mode_merge":      "Add (merge by Rule ID)",
        "tw_import_mode_replace":    "Replace existing",
        "tw_import_mode_cancel":     "Cancel",
        "tw_import_merged":          "Merged: {ok} added/updated, total now {total}.",
        "tw_qc_info_title":          "⚡ Quick Check active",
        "tw_qc_info_term":           "Term",
        "tw_qc_info_severity":       "Severity",
        "tw_qc_info_sources":        "Sources",
        "tw_qc_info_pairs":          "{n} locale → approved pair(s)",
        "tw_qc_info_disclaimer":     "Ad-hoc rules only — not saved to glossary.",
        "tw_qc_info_close":          "✕",
        "tw_qc_col_locale":          "Locale",
        "tw_qc_col_approved":        "Approved",
        "tw_scope_title":            "Scan Scope",
        "tw_scope_sources":          "Sources",
        "tw_scope_load":             "Load Project List",
        "tw_scope_loading":          "Loading project list…",
        "tw_scope_loaded":           "{n} projects · {l} locales",
        "tw_scope_search":           "Filter projects",
        "tw_scope_select_all":       "Select All",
        "tw_scope_select_none":      "Clear",
        "tw_scope_mode_label":       "Scope",
        "tw_scope_mode_all":         "All projects under selected sources",
        "tw_scope_mode_checked":     "Only checked projects",
        "tw_scope_mode_task":        "Single task ID",
        "tw_scope_task_id":          "Task ID",
        "tw_scope_task_kind":        "Task source",
        "tw_scope_btn_run":          "Start Scan",
        "tw_scope_no_projects":      "Click 'Load Project List' to populate.",
        "tw_scope_select_some":      "Check at least one project, or switch scope mode.",
        "tw_scope_task_required":    "Enter a task ID or change scope mode.",
        "tw_last_scan":              "Last scan",
        "tw_last_scan_never":        "(never)",
        "tw_filter_search":          "Search",
        "tw_filter_severity":        "Severity",
        "tw_filter_locale":          "Locale",
        "tw_filter_product":         "Product",
        "tw_filter_source":          "Source",
        "tw_filter_status":          "Status",
        "tw_filter_all":             "(all)",
        "tw_kpi_total":              "Total",
        "tw_kpi_critical":           "Critical",
        "tw_kpi_high":                "High",
        "tw_kpi_terms":              "Terms",
        "tw_kpi_locales":            "Locales",
        "tw_col_severity":           "Severity",
        "tw_col_status":             "Status",
        "tw_col_term":               "Term",
        "tw_col_locale":             "Locale",
        "tw_col_expected":           "Expected",
        "tw_col_actual":             "Actual",
        "tw_col_product":            "Product",
        "tw_col_source":             "Source",
        "tw_col_key":                "Key/Reference",
        "tw_col_issue_type":         "Issue Type",
        "tw_detail_title":           "Issue Detail",
        "tw_detail_select_hint":     "Select an issue to see details.",
        "tw_act_mark_reviewed":      "Mark Reviewed",
        "tw_act_mark_reported":      "Mark Reported",
        "tw_act_ignore":             "Ignore",
        "tw_act_copy_summary":       "Copy Summary",
        "tw_act_export_one":         "Export This Issue",
        "tw_state_no_glossary":      "No glossary imported yet. Import a CSV to start.",
        "tw_state_no_scan":          "Glossary loaded. Click Scan Now to begin.",
        "tw_state_scanning":         "Scanning…",
        "tw_state_all_clean":        "✓ Scan complete. No terminology issues found.",
        "tw_state_partial":          "⚠ Partial scan: {failed} source(s) failed.",
        "tw_glossary_count":         "{n} rules ({enabled} enabled)",
        "tw_import_done":            "Imported {ok} rules ({skipped} skipped).",
        "tw_import_errors_title":    "Import had errors",
        "tw_export_done":            "Saved to {path}",
        "tw_format_html":            "HTML report (.html)",
        "tw_format_xlsx":            "Excel report (.xlsx)",
        "tw_format_md":              "Markdown summary (.md)",
        "tw_export_scope":           "Scope",
        "tw_scope_filtered":         "Current filtered ({n})",
        "tw_scope_selected":         "Selected ({n})",
        "tw_scope_all_active":       "All active issues ({n})",
        "tw_scope_critical_high":    "All Critical + High ({n})",
        "tw_export_format":          "Format",
        "tw_export_btn_generate":    "Generate",
        "tw_export_btn_cancel":      "Cancel",
        "tw_export_dialog_title":    "Export Evidence",
        "tw_no_active_scan":         "No scan results yet. Click Scan Now first.",
        "tw_scan_choose_sources":    "Sources",
        "tw_src_legacy":             "File Translation (Legacy)",
        "tw_src_mr":                 "MR Pipeline",
        "tw_src_scan":               "Scan Tasks",
        "tw_scan_none_selected":     "Pick at least one source.",
        "tw_scan_progress":          "Scanning {phase}…",
        "tw_progress_starting":      "Starting…",
        "tw_progress_fetch":         "Fetching {phase} {scanned}/{total} · ETA {eta}",
        "tw_progress_match":         "Matching {scanned}/{total} ({pct}%) · ETA {eta}",
        "tw_progress_eta_unknown":   "calculating…",
        "tw_glossary_col_enabled":   "Enabled",
        "tw_glossary_col_rule":      "Rule ID",
        "tw_glossary_col_term":      "Source Term",
        "tw_glossary_col_locale":    "Locale",
        "tw_glossary_col_approved":  "Approved Translation",
        "tw_glossary_col_forbidden": "Forbidden",
        "tw_glossary_col_scope":     "Product Scope",
        "tw_glossary_col_severity":  "Severity",
        "tw_glossary_col_notes":     "Notes",
        "tw_glossary_hint":          "Edits via CSV import only. Export current rules to edit them.",
    },
    "zh": {
        "tab_term_watchtower":       "🛡 术语守望塔",
        "tw_subtab_issues":          "问题列表",
        "tw_subtab_glossary":        "已导入术语表",
        "tw_btn_scan":               "▶  扫描已导入术语表",
        "tw_btn_import":             "📥  导入术语表",
        "tw_btn_export_template":    "导出模板",
        "tw_btn_export_glossary":    "导出术语表",
        "tw_btn_export_evidence":    "📤  导出证据",
        "tw_btn_quick_check":        "⚡  快速检查",
        "tw_qc_title":               "快速检查 — 临时单术语",
        "tw_qc_source":              "源词",
        "tw_qc_severity":            "严重度",
        "tw_qc_pairs_label":         "语种 → 已批准译法",
        "tw_qc_pairs_help":          "每行一对。支持制表符/逗号/→/->。从 Excel 直接粘贴可用。",
        "tw_qc_paste_clipboard":     "从剪贴板粘贴",
        "tw_qc_reorg":               "🪄 重排",
        "tw_qc_reorg_tip":           "智能识别并重排粘贴内容为「语种↔译法」对",
        "tw_qc_reorg_failed":        "未能识别 locale 标记，请手动调整。",
        "tw_qc_clear":               "🧹 清空",
        "tw_btn_tranzor_term":       "🌐  Tranzor 术语库",
        "tw_tt_title":               "Tranzor 术语库 — 选择要检查的术语",
        "tw_tt_loading":              "加载中… {n}/{total}",
        "tw_tt_loaded":              "已加载 {n} 条术语。",
        "tw_tt_filter":              "过滤",
        "tw_tt_filter_dnt":          "DNT",
        "tw_tt_dnt_any":             "（全部）",
        "tw_tt_dnt_yes":             "仅 DNT",
        "tw_tt_dnt_no":              "仅可翻译",
        "tw_tt_select_all":          "全选",
        "tw_tt_select_none":         "全不选",
        "tw_tt_select_visible":      "选当前可见",
        "tw_tt_col_name":            "术语",
        "tw_tt_col_scope":           "Scope",
        "tw_tt_col_locales":         "语种数",
        "tw_tt_col_dnt":             "DNT",
        "tw_tt_severity":            "严重度",
        "tw_tt_run":                 "对所选术语运行扫描 ({n})…",
        "tw_tt_no_selection":        "至少勾选一个术语。",
        "tw_tt_fetching_details":    "拉取 {n} 个术语详情…",
        "tw_tt_running":             "Tranzor 术语库 — {n} 个术语，{r} 条规则",
        "tw_tt_error":               "加载术语库失败：{err}",
        "tw_tt_unavailable":         "Tranzor 术语库客户端不可用。",
        "tw_tt_skipped_dnt":         "已跳过 {n} 个 DNT 术语。",
        "tw_tt_double_click_hint":   "提示：双击术语条目可查看从 Tranzor 平台实时拉取的术语定义详情。",
        # ── 术语定义弹窗（双击触发） ──
        "tw_td_title":               "术语定义 — {name}",
        "tw_td_loading":             "正在加载术语定义…",
        "tw_td_error":               "加载失败：{err}",
        "tw_td_empty":               "暂无详情数据。",
        "tw_td_dnt_yes":             "DNT：是",
        "tw_td_dnt_no":              "DNT：否",
        "tw_td_definition":          "定义",
        "tw_td_context":             "上下文",
        "tw_td_part_of_speech":      "词性",
        "tw_td_reference":           "参考",
        "tw_td_remarks":             "备注",
        "tw_td_notes":               "说明",
        "tw_td_translations":        "译法",
        "tw_td_variants":            "变体",
        "tw_td_col_lang":            "语种",
        "tw_td_col_text":            "译法",
        "tw_td_col_remarks":         "备注",
        "tw_td_col_type":            "类型",
        "tw_td_col_name":            "名称",
        "tw_td_copy_btn":            "📋 复制",
        "tw_td_copied":              "✓ 已复制",
        "tw_td_open_browser":        "打开术语主页",
        "tw_td_open_failed":         "打开页面失败：{err}",
        "tw_td_close":               "关闭",
        "tw_td_updated_at":          "最后更新",
        "tw_td_updated_at_unknown":  "—",
        "tw_td_author_unavailable":  "Tranzor 平台目前不记录创建者 / 更新者的用户身份，API 仅暴露最后更新时间。",
        "tw_qc_run":                 "运行快速扫描…",
        "tw_qc_no_pairs":            "至少提供一对（locale, 已批准译法）。",
        "tw_qc_running":             "Quick Check「{term}」（{n} 个语种）…",
        "tw_import_choose_mode":     "已存在术语表（{n} 条规则）。\n请选择如何应用新文件：",
        "tw_import_mode_merge":      "累加（按 Rule ID 合并）",
        "tw_import_mode_replace":    "替换为新文件",
        "tw_import_mode_cancel":     "取消",
        "tw_import_merged":          "已合并：新增/更新 {ok} 条，当前共 {total} 条。",
        "tw_qc_info_title":          "⚡ 临时检查进行中",
        "tw_qc_info_term":           "术语",
        "tw_qc_info_severity":       "严重度",
        "tw_qc_info_sources":        "数据源",
        "tw_qc_info_pairs":          "{n} 对 locale → 译法",
        "tw_qc_info_disclaimer":     "临时规则，不会写入本地术语表。",
        "tw_qc_info_close":          "✕",
        "tw_qc_col_locale":          "语种",
        "tw_qc_col_approved":        "已批准译法",
        "tw_scope_title":            "扫描范围",
        "tw_scope_sources":          "数据源",
        "tw_scope_load":             "加载项目列表",
        "tw_scope_loading":          "正在加载项目列表…",
        "tw_scope_loaded":           "{n} 个项目 · {l} 种语言",
        "tw_scope_search":           "项目过滤",
        "tw_scope_select_all":       "全选",
        "tw_scope_select_none":      "全不选",
        "tw_scope_mode_label":       "范围",
        "tw_scope_mode_all":         "所选数据源下的全部项目",
        "tw_scope_mode_checked":     "仅勾选的项目",
        "tw_scope_mode_task":        "单个 Task ID",
        "tw_scope_task_id":          "Task ID",
        "tw_scope_task_kind":        "任务来源",
        "tw_scope_btn_run":          "开始扫描",
        "tw_scope_no_projects":      "点击「加载项目列表」拉取。",
        "tw_scope_select_some":      "至少勾选一个项目，或切换范围模式。",
        "tw_scope_task_required":    "请输入 Task ID 或切换范围模式。",
        "tw_last_scan":              "上次扫描",
        "tw_last_scan_never":        "（尚未扫描）",
        "tw_filter_search":          "搜索",
        "tw_filter_severity":        "严重度",
        "tw_filter_locale":          "语种",
        "tw_filter_product":         "产品",
        "tw_filter_source":          "来源",
        "tw_filter_status":          "状态",
        "tw_filter_all":             "（全部）",
        "tw_kpi_total":              "总数",
        "tw_kpi_critical":           "严重",
        "tw_kpi_high":               "高",
        "tw_kpi_terms":              "术语",
        "tw_kpi_locales":            "语种",
        "tw_col_severity":           "严重度",
        "tw_col_status":             "状态",
        "tw_col_term":               "术语",
        "tw_col_locale":             "语种",
        "tw_col_expected":           "已批准译法",
        "tw_col_actual":             "实际译文",
        "tw_col_product":            "产品",
        "tw_col_source":             "来源",
        "tw_col_key":                "Key/引用",
        "tw_col_issue_type":         "问题类型",
        "tw_detail_title":           "问题详情",
        "tw_detail_select_hint":     "选择一条问题查看详情。",
        "tw_act_mark_reviewed":      "标记已审阅",
        "tw_act_mark_reported":      "标记已上报",
        "tw_act_ignore":             "忽略",
        "tw_act_copy_summary":       "复制摘要",
        "tw_act_export_one":         "导出该问题",
        "tw_state_no_glossary":      "尚未导入术语表，先导入一份 CSV。",
        "tw_state_no_scan":          "术语表已加载，点击 立即扫描 开始。",
        "tw_state_scanning":         "扫描中…",
        "tw_state_all_clean":        "✓ 扫描完成，未发现术语问题。",
        "tw_state_partial":          "⚠ 部分失败：{failed} 个数据源未成功。",
        "tw_glossary_count":         "{n} 条规则（{enabled} 已启用）",
        "tw_import_done":            "已导入 {ok} 条（跳过 {skipped} 条）。",
        "tw_import_errors_title":    "导入存在错误",
        "tw_export_done":            "已保存到 {path}",
        "tw_format_html":            "HTML 报告 (.html)",
        "tw_format_xlsx":            "Excel 报表 (.xlsx)",
        "tw_format_md":              "Markdown 摘要 (.md)",
        "tw_export_scope":           "范围",
        "tw_scope_filtered":         "当前筛选 ({n} 条)",
        "tw_scope_selected":         "已选中 ({n} 条)",
        "tw_scope_all_active":       "全部活跃问题 ({n} 条)",
        "tw_scope_critical_high":    "全部严重+高 ({n} 条)",
        "tw_export_format":          "格式",
        "tw_export_btn_generate":    "生成",
        "tw_export_btn_cancel":      "取消",
        "tw_export_dialog_title":    "导出证据",
        "tw_no_active_scan":         "尚未扫描，先点击 立即扫描。",
        "tw_scan_choose_sources":    "数据源",
        "tw_src_legacy":             "File Translation (Legacy)",
        "tw_src_mr":                 "MR Pipeline",
        "tw_src_scan":               "Scan Tasks",
        "tw_scan_none_selected":     "至少选择一个数据源。",
        "tw_scan_progress":          "正在扫描 {phase}…",
        "tw_progress_starting":      "正在启动…",
        "tw_progress_fetch":         "拉取 {phase} {scanned}/{total} · 预计还需 {eta}",
        "tw_progress_match":         "匹配 {scanned}/{total}（{pct}%）· 预计还需 {eta}",
        "tw_progress_eta_unknown":   "估算中…",
        "tw_glossary_col_enabled":   "启用",
        "tw_glossary_col_rule":      "规则 ID",
        "tw_glossary_col_term":      "源词",
        "tw_glossary_col_locale":    "语种",
        "tw_glossary_col_approved":  "批准译法",
        "tw_glossary_col_forbidden": "禁用变体",
        "tw_glossary_col_scope":     "产品域",
        "tw_glossary_col_severity":  "严重度",
        "tw_glossary_col_notes":     "备注",
        "tw_glossary_hint":          "如需编辑，请导出当前规则、修改 CSV 后再导入。",
    },
}


SEVERITY_COLORS = {
    "Critical": ("#7f1d1d", "#fca5a5"),
    "High":     ("#78350f", "#fcd34d"),
    "Medium":   ("#1e3a8a", "#93c5fd"),
    "Low":      ("#374151", "#d1d5db"),
}

STATUS_COLORS = {
    "New":      "#fff",
    "Reviewed": "#a3e635",
    "Reported": "#fcd34d",
    "Ignored":  "#6b7280",
}


class TermWatchtowerTab:
    """Term Watchtower tab — Phase 1."""

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent

        self.rules: List[tw.TermRule] = []
        self.issues: List[tw.TerminologyIssue] = []
        self.filtered_issues: List[tw.TerminologyIssue] = []
        self.summary: Optional[tw.ScanSummary] = None
        self.status_store = tw.StatusStore()
        self._scan_running = False
        self._scan_thread: Optional[threading.Thread] = None
        self._selected_issue: Optional[tw.TerminologyIssue] = None

        self._issue_row_iid: Dict[str, str] = {}  # issue_id -> tree iid
        self._iid_to_issue: Dict[str, tw.TerminologyIssue] = {}

        self._build(parent)
        self._lazy_load()

    # ------------------------------------------------------------------
    def _t(self, key, **fmt):
        s = self.app._t(key)
        if fmt:
            try:
                return s.format(**fmt)
            except Exception:
                return s
        return s

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------
    def _build(self, parent):
        root = ttk.Frame(parent, style="App.TFrame")
        root.pack(fill="both", expand=True, padx=16, pady=8)

        # --- Top bar ----------------------------------------------------
        top = ttk.Frame(root, style="App.TFrame")
        top.pack(fill="x", pady=(0, 8))

        # NOTE: Use the cross-platform _create_button factory so buttons
        # render correctly on macOS too. Native Aqua tk.Button ignores
        # bg/activebackground, leaving buttons white on macOS — see
        # export_gui.MainApp._create_button for the macOS ttk fallback.
        _accent_bg = getattr(self.app, "ACCENT_BTN", "#e94560")
        _accent_hover = getattr(self.app, "ACCENT_BTN_HOVER", "#ff6b81")

        self.btn_scan = self.app._create_button(
            top, text=self._t("tw_btn_scan"), command=self._on_scan_now,
            style_name="Accent",
            font=(FONT_FAMILY, 11, "bold"),
            bg=_accent_bg, activebackground=_accent_hover,
            fg="#fff", activeforeground="#fff", padx=14, pady=4,
        )
        self.btn_scan.pack(side="left")

        self.btn_import = self.app._create_button(
            top, text=self._t("tw_btn_import"), command=self._on_import_glossary,
            style_name="Secondary",
            font=(FONT_FAMILY, 10),
            bg="#1f2a48", activebackground="#2a3a5e",
            fg="#fff", activeforeground="#fff", padx=10, pady=4,
        )
        self.btn_import.pack(side="left", padx=(8, 0))

        # Quick Check + Tranzor Terminology share the same accent-red
        # styling as Scan Now: all three are "click to start a scan"
        # actions so they should look like the same kind of button.
        self.btn_quick = self.app._create_button(
            top, text=self._t("tw_btn_quick_check"), command=self._on_quick_check,
            style_name="Accent",
            font=(FONT_FAMILY, 11, "bold"),
            bg=_accent_bg, activebackground=_accent_hover,
            fg="#fff", activeforeground="#fff", padx=14, pady=4,
        )
        self.btn_quick.pack(side="left", padx=(8, 0))

        self.btn_tranzor_term = self.app._create_button(
            top, text=self._t("tw_btn_tranzor_term"),
            command=self._on_tranzor_terminology,
            style_name="Accent",
            font=(FONT_FAMILY, 11, "bold"),
            bg=_accent_bg, activebackground=_accent_hover,
            fg="#fff", activeforeground="#fff", padx=14, pady=4,
        )
        self.btn_tranzor_term.pack(side="left", padx=(8, 0))

        self.btn_export = self.app._create_button(
            top, text=self._t("tw_btn_export_evidence"), command=self._on_export_evidence,
            style_name="Secondary",
            font=(FONT_FAMILY, 10),
            bg="#1f2a48", activebackground="#2a3a5e",
            fg="#fff", activeforeground="#fff", padx=10, pady=4,
        )
        self.btn_export.pack(side="left", padx=(8, 0))

        self.lbl_last_scan = ttk.Label(top, text="", style="Status.TLabel")
        self.lbl_last_scan.pack(side="right")

        # --- KPI strip --------------------------------------------------
        kpi = ttk.Frame(root, style="Card.TFrame")
        kpi.pack(fill="x", pady=(0, 8))
        kpi.configure(borderwidth=1, relief="solid")
        kpi_inner = ttk.Frame(kpi, style="Card.TFrame")
        kpi_inner.pack(fill="x", padx=12, pady=8)

        self.kpi_labels: Dict[str, ttk.Label] = {}
        self.kpi_titles: Dict[str, ttk.Label] = {}
        for key, title_key in (
            ("total", "tw_kpi_total"),
            ("critical", "tw_kpi_critical"),
            ("high", "tw_kpi_high"),
            ("terms", "tw_kpi_terms"),
            ("locales", "tw_kpi_locales"),
        ):
            cell = ttk.Frame(kpi_inner, style="Card.TFrame")
            cell.pack(side="left", padx=(0, 24))
            num = ttk.Label(cell, text="0", style="SummaryCount.TLabel")
            num.pack(anchor="w")
            ttl = ttk.Label(cell, text=self._t(title_key),
                            style="SummaryCountLabel.TLabel")
            ttl.pack(anchor="w")
            self.kpi_labels[key] = num
            self.kpi_titles[key] = ttl

        # Royal-blue, bold scan-status label — visually prominent so the
        # user can spot scan progress without hunting for it. Uses a
        # widget-local font/foreground rather than a named style to avoid
        # leaking the color into other Status.TLabel uses elsewhere.
        self.lbl_state = ttk.Label(kpi_inner, text="", style="Status.TLabel")
        self.lbl_state.configure(
            foreground="#3b82f6",  # royal blue ("宝蓝")
            font=(FONT_FAMILY, 11, "bold"),
        )
        self.lbl_state.pack(side="right", padx=(8, 0))

        # Determinate progress bar + ETA — shown only while a scan is
        # running. Fed by the per-task count callback in collect_full_*
        # so the bar advances every time an HTTP fetch completes (≈
        # several times per second when parallel-fetching), instead of
        # only every 10 tasks like the old text-only status line.
        self.progress_frame = ttk.Frame(kpi_inner, style="Card.TFrame")
        self.progress_bar = ttk.Progressbar(
            self.progress_frame, orient="horizontal", mode="determinate",
            length=220, maximum=100, value=0,
        )
        self.progress_bar.pack(side="left")
        self.lbl_progress_text = ttk.Label(
            self.progress_frame, text="", style="Status.TLabel",
        )
        self.lbl_progress_text.configure(
            foreground="#3b82f6", font=(FONT_FAMILY, 10),
        )
        self.lbl_progress_text.pack(side="left", padx=(8, 0))
        # Hidden by default; _launch_scan packs it on scan start.
        # Stored start time + cached counts for ETA / phase routing.
        self._scan_started_at: Optional[float] = None
        self._fetch_phase_totals: Dict[str, int] = {}
        self._fetch_phase_done: Dict[str, int] = {}

        # --- Quick Check info bar (visible only during/after a Quick Check)
        self._build_qc_info_bar(root)

        # --- Sub-tabs ---------------------------------------------------
        self.sub_nb = ttk.Notebook(root)
        self.sub_nb.pack(fill="both", expand=True)

        self.tab_issues = ttk.Frame(self.sub_nb, style="App.TFrame")
        self.sub_nb.add(self.tab_issues, text=self._t("tw_subtab_issues"))
        self._build_issues_view(self.tab_issues)

        self.tab_glossary = ttk.Frame(self.sub_nb, style="App.TFrame")
        self.sub_nb.add(self.tab_glossary, text=self._t("tw_subtab_glossary"))
        self._build_glossary_view(self.tab_glossary)

    # ------------------------------------------------------------------
    def _build_qc_info_bar(self, parent):
        """Sidebar/banner shown while a Quick Check is the active scope.

        It tells the user *exactly* which ad-hoc rules the scan is using —
        important for transparency since these rules are not in the
        persisted glossary.
        """
        self.qc_info_frame = ttk.Frame(parent, style="Card.TFrame")
        self.qc_info_frame.configure(borderwidth=1, relief="solid")
        # Not packed yet — call _show_quick_check_info to reveal.

        head = ttk.Frame(self.qc_info_frame, style="Card.TFrame")
        head.pack(fill="x", padx=10, pady=(8, 4))

        self.qc_lbl_title = ttk.Label(
            head, text=self._t("tw_qc_info_title"),
            style="CardBold.TLabel",
        )
        self.qc_lbl_title.pack(side="left")

        self.qc_lbl_meta = ttk.Label(head, text="", style="Card.TLabel")
        self.qc_lbl_meta.pack(side="left", padx=(12, 0))

        self.qc_btn_close = self.app._create_button(
            head, text=self._t("tw_qc_info_close"),
            command=self._hide_quick_check_info,
            style_name="Secondary",
            font=(FONT_FAMILY, 10),
            bg="#1f2a48", activebackground="#2a3a5e",
            fg="#fff", activeforeground="#fff", padx=6, pady=0,
        )
        self.qc_btn_close.pack(side="right")

        body = ttk.Frame(self.qc_info_frame, style="Card.TFrame")
        body.pack(fill="x", padx=10, pady=(0, 4))

        cols = ("locale", "approved")
        self.qc_tree = ttk.Treeview(body, columns=cols, show="headings",
                                    height=4, selectmode="none")
        self.qc_tree.heading("locale", text=self._t("tw_qc_col_locale"))
        self.qc_tree.heading("approved", text=self._t("tw_qc_col_approved"))
        self.qc_tree.column("locale", width=100, anchor="w")
        self.qc_tree.column("approved", width=520, anchor="w", stretch=True)
        self.qc_tree.pack(side="left", fill="x", expand=True)
        qc_vsb = ttk.Scrollbar(body, orient="vertical",
                               command=self.qc_tree.yview)
        self.qc_tree.configure(yscrollcommand=qc_vsb.set)
        qc_vsb.pack(side="right", fill="y")

        self.qc_lbl_disclaimer = ttk.Label(
            self.qc_info_frame, text=self._t("tw_qc_info_disclaimer"),
            style="Status.TLabel",
        )
        self.qc_lbl_disclaimer.pack(anchor="w", padx=10, pady=(0, 8))

    def _show_quick_check_info(self, term, severity, pairs, sources, scope_label):
        if not self.qc_info_frame.winfo_ismapped():
            self.qc_info_frame.pack(fill="x", pady=(0, 8),
                                    before=self.sub_nb)
        self.qc_tree.delete(*self.qc_tree.get_children())
        for locale, approved in pairs:
            self.qc_tree.insert("", "end", values=(locale, approved))
        # Resize tree to fit the pairs (cap at 8 rows, min 3)
        self.qc_tree.configure(height=max(3, min(8, len(pairs))))

        meta = (
            f"{self._t('tw_qc_info_term')}: {term}   "
            f"{self._t('tw_qc_info_severity')}: {severity}   "
            f"{self._t('tw_qc_info_sources')}: {scope_label or ', '.join(sources)}   "
            f"{self._t('tw_qc_info_pairs', n=len(pairs))}"
        )
        self.qc_lbl_meta.configure(text=meta)

    def _hide_quick_check_info(self):
        if self.qc_info_frame.winfo_ismapped():
            self.qc_info_frame.pack_forget()

    def _build_issues_view(self, parent):
        # Filter bar
        filt = ttk.Frame(parent, style="Card.TFrame")
        filt.pack(fill="x", padx=8, pady=(8, 4))
        filt.configure(borderwidth=1, relief="solid")
        fi = ttk.Frame(filt, style="Card.TFrame")
        fi.pack(fill="x", padx=10, pady=8)

        self.lbl_search = ttk.Label(fi, text=self._t("tw_filter_search"), style="Card.TLabel")
        self.lbl_search.pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filters())
        ent = tk.Entry(fi, textvariable=self.search_var, width=24,
                       font=(FONT_FAMILY, 10),
                       bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat")
        ent.pack(side="left", padx=(4, 12), ipady=3)

        # Severity / Locale / Product / Source / Status combos
        self.lbl_sev = ttk.Label(fi, text=self._t("tw_filter_severity"), style="Card.TLabel")
        self.lbl_sev.pack(side="left")
        self.sev_var = tk.StringVar()
        self.cmb_sev = ttk.Combobox(fi, textvariable=self.sev_var, width=10, state="readonly",
                                    values=[""] + list(tw.SEVERITIES))
        self.cmb_sev.pack(side="left", padx=(4, 12))
        self.cmb_sev.bind("<<ComboboxSelected>>", lambda *_: self._apply_filters())

        self.lbl_loc = ttk.Label(fi, text=self._t("tw_filter_locale"), style="Card.TLabel")
        self.lbl_loc.pack(side="left")
        self.loc_var = tk.StringVar()
        self.cmb_loc = ttk.Combobox(fi, textvariable=self.loc_var, width=10, state="readonly")
        self.cmb_loc.pack(side="left", padx=(4, 12))
        self.cmb_loc.bind("<<ComboboxSelected>>", lambda *_: self._apply_filters())

        self.lbl_prod = ttk.Label(fi, text=self._t("tw_filter_product"), style="Card.TLabel")
        self.lbl_prod.pack(side="left")
        self.prod_var = tk.StringVar()
        self.cmb_prod = ttk.Combobox(fi, textvariable=self.prod_var, width=14, state="readonly")
        self.cmb_prod.pack(side="left", padx=(4, 12))
        self.cmb_prod.bind("<<ComboboxSelected>>", lambda *_: self._apply_filters())

        self.lbl_src = ttk.Label(fi, text=self._t("tw_filter_source"), style="Card.TLabel")
        self.lbl_src.pack(side="left")
        self.src_var = tk.StringVar()
        self.cmb_src = ttk.Combobox(
            fi, textvariable=self.src_var, width=12, state="readonly",
            values=[""] + list(tw.SOURCE_LABELS.keys()),
        )
        self.cmb_src.pack(side="left", padx=(4, 12))
        self.cmb_src.bind("<<ComboboxSelected>>", lambda *_: self._apply_filters())

        self.lbl_status = ttk.Label(fi, text=self._t("tw_filter_status"), style="Card.TLabel")
        self.lbl_status.pack(side="left")
        self.status_var = tk.StringVar()
        self.cmb_status = ttk.Combobox(
            fi, textvariable=self.status_var, width=10, state="readonly",
            values=[""] + list(tw.STATUSES),
        )
        self.cmb_status.pack(side="left", padx=(4, 12))
        self.cmb_status.bind("<<ComboboxSelected>>", lambda *_: self._apply_filters())

        # --- Body: paned (table | detail) ---
        paned = ttk.PanedWindow(parent, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        left = ttk.Frame(paned, style="App.TFrame")
        right = ttk.Frame(paned, style="Card.TFrame", width=380)
        right.configure(borderwidth=1, relief="solid")
        paned.add(left, weight=3)
        paned.add(right, weight=2)

        # Table
        cols = ("severity", "status", "term", "locale", "expected", "actual",
                "product", "source", "key", "issue_type")
        self.tree = ttk.Treeview(left, columns=cols, show="headings",
                                 selectmode="extended", height=18)
        self._tree_cols = cols
        self.tree.heading("severity", text=self._t("tw_col_severity"))
        self.tree.heading("status", text=self._t("tw_col_status"))
        self.tree.heading("term", text=self._t("tw_col_term"))
        self.tree.heading("locale", text=self._t("tw_col_locale"))
        self.tree.heading("expected", text=self._t("tw_col_expected"))
        self.tree.heading("actual", text=self._t("tw_col_actual"))
        self.tree.heading("product", text=self._t("tw_col_product"))
        self.tree.heading("source", text=self._t("tw_col_source"))
        self.tree.heading("key", text=self._t("tw_col_key"))
        self.tree.heading("issue_type", text=self._t("tw_col_issue_type"))

        widths = {"severity": 80, "status": 80, "term": 140, "locale": 70,
                  "expected": 180, "actual": 180, "product": 100,
                  "source": 110, "key": 180, "issue_type": 180}
        for c in cols:
            self.tree.column(c, width=widths[c], anchor="w", stretch=True)

        for sev, (bg, _fg) in SEVERITY_COLORS.items():
            self.tree.tag_configure(f"sev_{sev}", background=bg, foreground="#fff")

        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select_issue)

        # Detail panel
        self._build_detail_panel(right)

    # ------------------------------------------------------------------
    def _build_detail_panel(self, parent):
        inner = ttk.Frame(parent, style="Card.TFrame")
        inner.pack(fill="both", expand=True, padx=10, pady=10)

        self.lbl_detail_title = ttk.Label(
            inner, text=self._t("tw_detail_title"),
            style="CardBold.TLabel",
        )
        self.lbl_detail_title.pack(anchor="w")

        self.lbl_detail_hint = ttk.Label(
            inner, text=self._t("tw_detail_select_hint"),
            style="Card.TLabel",
        )
        self.lbl_detail_hint.pack(anchor="w", pady=(4, 8))

        self.detail_text = tk.Text(
            inner, height=18, wrap="word",
            bg="#0a0a1a", fg="#e5e7eb",
            font=(FONT_FAMILY, 10), relief="flat",
            highlightthickness=1, highlightbackground="#2a2a4a",
            state="disabled",
        )
        self.detail_text.pack(fill="both", expand=True, pady=(0, 8))
        self.detail_text.tag_configure("h", font=(FONT_FAMILY, 10, "bold"),
                                       foreground="#94a3b8")
        self.detail_text.tag_configure("expected", background="#10271b",
                                       foreground="#a7f3d0")
        self.detail_text.tag_configure("actual", background="#3b1115",
                                       foreground="#fda4af")

        actions = ttk.Frame(inner, style="Card.TFrame")
        actions.pack(fill="x")
        for key, cmd, attr in (
            ("tw_act_mark_reviewed", lambda: self._set_status("Reviewed"), "btn_act_reviewed"),
            ("tw_act_mark_reported", lambda: self._set_status("Reported"), "btn_act_reported"),
            ("tw_act_ignore",        lambda: self._set_status("Ignored"),  "btn_act_ignore"),
            ("tw_act_copy_summary",  self._copy_summary, "btn_act_copy"),
            ("tw_act_export_one",    self._export_selected_issue, "btn_act_export_one"),
        ):
            b = self.app._create_button(
                actions, text=self._t(key), command=cmd,
                style_name="SecondarySmall",
                font=(FONT_FAMILY, 9),
                bg="#1f2a48", activebackground="#2a3a5e",
                fg="#fff", activeforeground="#fff", padx=6, pady=2,
                state="disabled",
            )
            b.pack(side="left", padx=(0, 4), pady=(2, 0))
            setattr(self, attr, b)

    # ------------------------------------------------------------------
    def _build_glossary_view(self, parent):
        bar = ttk.Frame(parent, style="App.TFrame")
        bar.pack(fill="x", padx=8, pady=(8, 4))

        self.btn_g_import = self.app._create_button(
            bar, text=self._t("tw_btn_import"), command=self._on_import_glossary,
            style_name="Secondary",
            font=(FONT_FAMILY, 10),
            bg="#1f2a48", fg="#fff", padx=10, pady=4,
        )
        self.btn_g_import.pack(side="left")

        self.btn_g_export = self.app._create_button(
            bar, text=self._t("tw_btn_export_glossary"), command=self._on_export_glossary,
            style_name="Secondary",
            font=(FONT_FAMILY, 10),
            bg="#1f2a48", fg="#fff", padx=10, pady=4,
        )
        self.btn_g_export.pack(side="left", padx=(8, 0))

        self.btn_g_template = self.app._create_button(
            bar, text=self._t("tw_btn_export_template"), command=self._on_export_template,
            style_name="Secondary",
            font=(FONT_FAMILY, 10),
            bg="#1f2a48", fg="#fff", padx=10, pady=4,
        )
        self.btn_g_template.pack(side="left", padx=(8, 0))

        self.lbl_glossary_count = ttk.Label(bar, text="", style="Status.TLabel")
        self.lbl_glossary_count.pack(side="right")

        self.lbl_glossary_hint = ttk.Label(
            parent, text=self._t("tw_glossary_hint"),
            style="Status.TLabel",
        )
        self.lbl_glossary_hint.pack(anchor="w", padx=8)

        cols = ("enabled", "rule_id", "term", "locale", "approved",
                "forbidden", "scope", "severity", "notes")
        self.gtree = ttk.Treeview(parent, columns=cols, show="headings", height=18)
        self.gtree.heading("enabled",  text=self._t("tw_glossary_col_enabled"))
        self.gtree.heading("rule_id",  text=self._t("tw_glossary_col_rule"))
        self.gtree.heading("term",     text=self._t("tw_glossary_col_term"))
        self.gtree.heading("locale",   text=self._t("tw_glossary_col_locale"))
        self.gtree.heading("approved", text=self._t("tw_glossary_col_approved"))
        self.gtree.heading("forbidden", text=self._t("tw_glossary_col_forbidden"))
        self.gtree.heading("scope",    text=self._t("tw_glossary_col_scope"))
        self.gtree.heading("severity", text=self._t("tw_glossary_col_severity"))
        self.gtree.heading("notes",    text=self._t("tw_glossary_col_notes"))

        widths = {"enabled": 60, "rule_id": 130, "term": 140, "locale": 70,
                  "approved": 200, "forbidden": 180, "scope": 120,
                  "severity": 80, "notes": 200}
        for c in cols:
            self.gtree.column(c, width=widths[c], anchor="w", stretch=True)

        gvsb = ttk.Scrollbar(parent, orient="vertical", command=self.gtree.yview)
        self.gtree.configure(yscrollcommand=gvsb.set)
        self.gtree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=4)
        gvsb.pack(side="right", fill="y", pady=4)

    # ==================================================================
    # Lazy load (no network on tab init)
    # ==================================================================
    def _lazy_load(self):
        try:
            self.rules = tw.load_glossary()
        except Exception as e:
            print(f"[Term Watchtower] glossary load failed: {e}")
            self.rules = []

        # Intentionally do NOT restore last_scan into the UI on startup.
        # The KPI numbers / state must reflect *this session's* scans only;
        # leftover totals from a previous run are confusing because the
        # underlying issue rows are not persisted. The last_scan.json file
        # is still written by each scan (per PRD FR-14) for offline
        # inspection, but we don't surface it here.
        self.summary = None
        self.issues = []
        self.filtered_issues = []

        self._refresh_glossary_view()
        self._refresh_filter_options()
        self._render_state()

    # ==================================================================
    # State / rendering
    # ==================================================================
    def _render_state(self):
        # Determine current state
        if self._scan_running:
            state = "scanning"
        elif not self.rules:
            state = "no-glossary"
        elif not self.issues and self.summary is None:
            state = "no-scan"
        elif not self.issues and self.summary is not None:
            state = "all-clean"
        else:
            state = "has-issues"

        # KPI numbers
        s = self.summary
        if s:
            self.kpi_labels["total"].configure(text=str(s.total_active_issues))
            self.kpi_labels["critical"].configure(text=str(s.critical))
            self.kpi_labels["high"].configure(text=str(s.high))
            self.kpi_labels["terms"].configure(text=str(s.affected_terms))
            self.kpi_labels["locales"].configure(text=str(s.affected_locales))
            ts = s.last_scan_at or "-"
            self.lbl_last_scan.configure(
                text=f"{self._t('tw_last_scan')}: {ts}"
            )
        else:
            for k in self.kpi_labels:
                self.kpi_labels[k].configure(text="0")
            self.lbl_last_scan.configure(
                text=f"{self._t('tw_last_scan')}: {self._t('tw_last_scan_never')}"
            )

        # State message
        msg = ""
        if state == "scanning":
            msg = self._t("tw_state_scanning")
        elif state == "no-glossary":
            msg = self._t("tw_state_no_glossary")
        elif state == "no-scan":
            msg = self._t("tw_state_no_scan")
        elif state == "all-clean":
            msg = self._t("tw_state_all_clean")
        elif state == "has-issues" and s and s.failed_sources:
            msg = self._t("tw_state_partial", failed=len(s.failed_sources))
        self.lbl_state.configure(text=msg)

        # Scan button enabled only when not running
        self.btn_scan.configure(state="disabled" if self._scan_running else "normal")

    # ------------------------------------------------------------------
    def _refresh_filter_options(self):
        locales = sorted({i.locale_display or i.locale for i in self.issues})
        products = sorted({i.product for i in self.issues})
        self.cmb_loc["values"] = [""] + locales
        self.cmb_prod["values"] = [""] + products

    # ------------------------------------------------------------------
    def _apply_filters(self):
        self.filtered_issues = tw.filter_issues(
            self.issues,
            search=self.search_var.get(),
            severity=self.sev_var.get(),
            locale=self.loc_var.get(),
            product=self.prod_var.get(),
            source_kind=self.src_var.get(),
            status=self.status_var.get(),
        )
        self._render_table()

    def _render_table(self):
        self.tree.delete(*self.tree.get_children())
        self._issue_row_iid.clear()
        self._iid_to_issue.clear()
        for i in self.filtered_issues:
            # Visual emphasis on the Actual cell — ttk.Treeview only supports
            # row-level color tags (no per-column foreground), so we rely on
            # a red ❌ marker + corner brackets to single out the offending
            # value within an otherwise uniformly styled row. The original
            # `i.actual` is preserved on the issue object — this prefix is
            # purely a display decoration, never round-tripped to exports
            # or status persistence.
            actual_display = self._format_actual_for_table(i.actual)
            row_iid = self.tree.insert(
                "", "end",
                values=(
                    i.severity, i.status, i.source_term,
                    i.locale_display or i.locale,
                    i.expected, actual_display, i.product,
                    i.source_label, i.key, i.issue_type,
                ),
                tags=(f"sev_{i.severity}",),
            )
            self._issue_row_iid[i.issue_id] = row_iid
            self._iid_to_issue[row_iid] = i

    @staticmethod
    def _format_actual_for_table(actual: str) -> str:
        """Render the Actual value with a red ❌ marker so it stands out
        as the offending cell within an issue row."""
        if not actual:
            return "❌ (empty)"
        return f"❌ 「{actual}」"

    # ==================================================================
    # Glossary view
    # ==================================================================
    def _refresh_glossary_view(self):
        self.gtree.delete(*self.gtree.get_children())
        enabled = 0
        for r in self.rules:
            if r.enabled:
                enabled += 1
            self.gtree.insert(
                "", "end",
                values=(
                    "✓" if r.enabled else "·",
                    r.rule_id,
                    r.source_term,
                    r.locale_display or r.locale,
                    r.approved_translation,
                    "|".join(r.forbidden_translations),
                    "|".join(r.product_scope),
                    r.severity,
                    r.notes,
                ),
            )
        self.lbl_glossary_count.configure(
            text=self._t("tw_glossary_count", n=len(self.rules), enabled=enabled)
        )

    # ==================================================================
    # Actions
    # ==================================================================
    def _on_import_glossary(self):
        path = filedialog.askopenfilename(
            title=self._t("tw_btn_import"),
            filetypes=[
                ("Glossary (CSV / XLSX)", "*.csv *.xlsx *.xlsm"),
                ("Excel", "*.xlsx *.xlsm"),
                ("CSV", "*.csv"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            res = tw.import_glossary(path)
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return

        if res.imported == 0 and res.errors:
            err_text = "\n".join(f"row {n}: {m}" for n, m in res.errors[:30])
            messagebox.showerror(self._t("tw_import_errors_title"), err_text)
            return

        # If we already have a glossary, ask: merge / replace / cancel.
        mode = "replace"
        if self.rules:
            mode = _ImportModeDialog(self.parent, self,
                                     existing_count=len(self.rules)).result
            if mode is None:
                return

        before = len(self.rules)
        if mode == "merge":
            self.rules = tw.merge_rules(self.rules, res.rules)
        else:
            self.rules = res.rules

        try:
            tw.save_glossary(self.rules)
        except Exception as e:
            messagebox.showwarning("Save failed", str(e))

        self._refresh_glossary_view()

        if mode == "merge":
            head = self._t("tw_import_merged",
                           ok=len(self.rules) - before, total=len(self.rules))
        else:
            head = self._t("tw_import_done", ok=res.imported, skipped=res.skipped)
        if res.errors:
            head += "\n\n" + "\n".join(f"row {n}: {m}" for n, m in res.errors[:30])
        messagebox.showinfo(self._t("tw_btn_import"), head)
        self._render_state()

    # ------------------------------------------------------------------
    def _on_tranzor_terminology(self):
        if tz_term is None:
            messagebox.showerror(self._t("tw_btn_tranzor_term"),
                                 self._t("tw_tt_unavailable"))
            return
        if _ft_api is None:
            messagebox.showerror(self._t("tw_btn_tranzor_term"),
                                 "Full translation collector not available.")
            return
        dlg = _TranzorTerminologyDialog(self.parent, self)
        if not dlg.result:
            return
        details, severity = dlg.result
        rules = tz_term.details_to_rules(details, severity=severity)
        if not rules:
            messagebox.showinfo(self._t("tw_btn_tranzor_term"),
                                self._t("tw_tt_no_selection"))
            return

        # Pick scope through the unified _ScopeDialog (same UX as Scan Now).
        scope_dlg = _ScopeDialog(self.parent, self)
        if not scope_dlg.result:
            return
        scope = scope_dlg.result

        scope_label = self._scope_label(scope)
        pairs_preview = [
            (r.locale_display or r.locale,
             f"{r.source_term} → {r.approved_translation}")
            for r in rules[:60]
        ]
        if len(rules) > 60:
            pairs_preview.append(("…", f"+{len(rules) - 60} more rules"))

        terms_in_scan = sorted({r.source_term for r in rules})
        title_text = self._t("tw_tt_running",
                             n=len(terms_in_scan), r=len(rules))

        self._launch_scan(
            rules, scope,
            progress_label=title_text,
            qc_info=dict(
                term=", ".join(terms_in_scan[:8]) +
                     (f" … (+{len(terms_in_scan) - 8})"
                      if len(terms_in_scan) > 8 else ""),
                severity=severity,
                pairs=pairs_preview,
                sources=scope.get("sources", []),
                scope_label=scope_label,
            ),
        )

    # ------------------------------------------------------------------
    def _on_quick_check(self):
        if _ft_api is None:
            messagebox.showerror(self._t("tw_btn_quick_check"),
                                 "Full translation collector not available.")
            return
        dlg = _QuickCheckDialog(self.parent, self)
        if not dlg.result:
            return
        source_term, severity, pairs = dlg.result
        ad_hoc_rules = tw.build_quick_check_rules(
            source_term, pairs, severity=severity,
        )
        if not ad_hoc_rules:
            messagebox.showinfo(self._t("tw_btn_quick_check"),
                                self._t("tw_qc_no_pairs"))
            return

        # Pick scope through the unified _ScopeDialog (same UX as Scan Now).
        scope_dlg = _ScopeDialog(self.parent, self)
        if not scope_dlg.result:
            return
        scope = scope_dlg.result

        self._launch_scan(
            ad_hoc_rules, scope,
            progress_label=self._t("tw_qc_running",
                                   term=source_term,
                                   n=len(ad_hoc_rules)),
            qc_info=dict(
                term=source_term,
                severity=severity,
                pairs=pairs,
                sources=scope.get("sources", []),
                scope_label=self._scope_label(scope),
            ),
        )

    @staticmethod
    def _scope_label(scope: Dict[str, Any]) -> str:
        """Human-readable summary of a scope dict for the info bar."""
        if scope.get("mode") == "task":
            return f"task {scope['task_id']} ({scope['task_kind']})"
        sources = scope.get("sources") or []
        mode = scope.get("mode", "all")
        if mode == "projects":
            n_proj = (len(scope.get("legacy") or []) +
                      len(scope.get("mr") or []) +
                      len(scope.get("scan") or []))
            return f"{', '.join(sources)} · {n_proj} project(s)"
        return ", ".join(sources)

    def _on_export_template(self):
        path = filedialog.asksaveasfilename(
            title=self._t("tw_btn_export_template"),
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="terminology_template.csv",
        )
        if not path:
            return
        try:
            tw.export_glossary_template(path)
            messagebox.showinfo(self._t("tw_btn_export_template"),
                                self._t("tw_export_done", path=path))
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def _on_export_glossary(self):
        if not self.rules:
            return
        path = filedialog.asksaveasfilename(
            title=self._t("tw_btn_export_glossary"),
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="glossary.csv",
        )
        if not path:
            return
        try:
            tw.export_glossary_csv(self.rules, path)
            messagebox.showinfo(self._t("tw_btn_export_glossary"),
                                self._t("tw_export_done", path=path))
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    # ------------------------------------------------------------------
    def _on_scan_now(self):
        if self._scan_running:
            return
        if not self.rules:
            messagebox.showinfo(self._t("tw_btn_scan"),
                                self._t("tw_state_no_glossary"))
            return
        if _ft_api is None:
            messagebox.showerror(self._t("tw_btn_scan"),
                                 "Full translation collector not available.")
            return

        dlg = _ScopeDialog(self.parent, self)
        if not dlg.result:
            return
        # Regular Scan Now hides any prior Quick Check info bar
        self._hide_quick_check_info()
        self._launch_scan(self.rules, dlg.result)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Progress bar helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _format_eta(seconds: float) -> str:
        """Human-friendly ETA string. Sub-minute resolution under 1m,
        ``Xm Ys`` under an hour, ``Xh Ym`` otherwise."""
        if seconds <= 0 or seconds != seconds:  # NaN guard
            return "—"
        s = int(round(seconds))
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m {s % 60}s"
        return f"{s // 3600}h {(s % 3600) // 60}m"

    def _show_progress_bar(self):
        """Pack the progress bar on the right side of kpi_inner and reset
        accumulated counters. Must be called from the Tk thread."""
        if not self.progress_frame.winfo_ismapped():
            self.progress_frame.pack(side="right", padx=(8, 0),
                                     before=self.lbl_state)
        self.progress_bar.configure(value=0)
        self.lbl_progress_text.configure(
            text=self._t("tw_progress_starting"))
        self._scan_started_at = time.monotonic()
        self._fetch_phase_totals = {}
        self._fetch_phase_done = {}

    def _hide_progress_bar(self):
        if self.progress_frame.winfo_ismapped():
            self.progress_frame.pack_forget()
        self._scan_started_at = None

    def _update_progress_fetch(self, phase: str, scanned: int, total: int):
        """Aggregate fetch progress across phases (Legacy + MR + Scan run
        sequentially, one after another). The bar reflects the *current*
        phase's percentage; the text label shows the phase name + ETA so
        the user always sees both pieces of context."""
        if total <= 0:
            return
        # Track per-phase counters so totals printed in the label reflect
        # this phase's task pool, not a stale earlier one.
        self._fetch_phase_totals[phase] = total
        self._fetch_phase_done[phase] = scanned
        pct = max(0, min(100, int(scanned * 100 / total))) if total else 0
        self.progress_bar.configure(value=pct)
        eta = self._t("tw_progress_eta_unknown")
        if self._scan_started_at is not None and scanned > 0 and total > 0:
            elapsed = time.monotonic() - self._scan_started_at
            # Project remaining time *for this phase* — a coarse but
            # honest estimate. Cross-phase ETA would be misleading because
            # /results latency varies wildly between Legacy/MR/Scan.
            per_unit = elapsed / max(1, scanned)
            remaining = per_unit * max(0, total - scanned)
            eta = self._format_eta(remaining)
        self.lbl_progress_text.configure(text=self._t(
            "tw_progress_fetch",
            phase=phase, scanned=scanned, total=total, eta=eta,
        ))

    def _update_progress_match(self, scanned: int, total: int):
        """Drive the bar from the candidate-matching phase (CPU-bound,
        runs after all fetches complete)."""
        if total <= 0:
            return
        pct = max(0, min(100, int(scanned * 100 / total)))
        self.progress_bar.configure(value=pct)
        eta = self._t("tw_progress_eta_unknown")
        if self._scan_started_at is not None and scanned > 0:
            # Approximate ETA from the matching phase's own pace —
            # ignores the (already finished) fetch elapsed time so the
            # number doesn't lurch when the phase boundary is crossed.
            now = time.monotonic()
            phase_start = getattr(self, "_match_started_at", None) or now
            if phase_start == now:
                self._match_started_at = now
            phase_elapsed = max(0.001, now - self._match_started_at)
            per_unit = phase_elapsed / max(1, scanned)
            remaining = per_unit * max(0, total - scanned)
            eta = self._format_eta(remaining)
        self.lbl_progress_text.configure(text=self._t(
            "tw_progress_match", scanned=scanned, total=total,
            pct=pct, eta=eta,
        ))

    def _launch_scan(self, rules, scope, *, progress_label=None,
                     qc_info=None):
        """Shared scan launcher.

        ``scope`` shape:
            {"mode": "all" | "projects" | "task",
             "sources": ["legacy", "mr", ...],          # for all/projects
             "legacy": [project_name, ...],              # for projects
             "mr": [...], "scan": [...],
             "task_id": "...", "task_kind": "legacy|mr|scan"}  # for task
        """
        if self._scan_running:
            return
        self._scan_running = True
        self.summary = None
        self.issues = []
        self.filtered_issues = []
        self._render_table()
        self._render_state()
        self._show_progress_bar()
        # Reset matching-phase clock; it's set fresh when the first
        # match-phase callback fires after fetching completes.
        self._match_started_at = None

        if qc_info is not None:
            self._show_quick_check_info(**qc_info)
        if progress_label:
            self.lbl_state.configure(text=progress_label)

        def _phase(msg):
            self.parent.after(0, lambda: self.lbl_state.configure(
                text=self._t("tw_scan_progress", phase=str(msg)[:80])
            ))

        def _count(phase, scanned, total):
            self.parent.after(
                0, lambda p=phase, s=scanned, t=total:
                self._update_progress_fetch(p, s, t),
            )

        def _scan_progress(scanned, total):
            if not total:
                return
            # First match-phase tick latches the phase start time so ETA
            # is computed against the matching pace, not fetch elapsed.
            if self._match_started_at is None:
                self._match_started_at = time.monotonic()
            self.parent.after(
                0, lambda s=scanned, t=total:
                self._update_progress_match(s, t),
            )

        def _worker():
            failed: List[str] = []
            covered: List[str] = []
            inv = None
            try:
                if scope["mode"] == "task":
                    inv = _collect_single_task(
                        scope["task_id"], scope["task_kind"],
                        progress_cb=_phase,
                    )
                    covered = [scope["task_kind"]]
                else:
                    sources = scope.get("sources") or ["legacy", "mr", "scan"]
                    if scope["mode"] == "projects":
                        inv = _ft_api.collect_full_translations(
                            sources=sources, progress_cb=_phase,
                            legacy_project_filter=scope.get("legacy") or None,
                            mr_project_filter=scope.get("mr") or None,
                            scan_project_filter=scope.get("scan") or None,
                            count_cb=_count,
                        )
                    else:
                        inv = _ft_api.collect_full_translations(
                            sources=sources, progress_cb=_phase,
                            count_cb=_count,
                        )
                    covered = list(sources)
            except Exception as e:
                self.parent.after(0, lambda err=e: messagebox.showerror(
                    self._t("tw_btn_scan"), f"Scan failed: {err}"
                ))

            issues: List[tw.TerminologyIssue] = []
            summary: Optional[tw.ScanSummary] = None
            if inv is not None:
                try:
                    candidates = tw.candidates_from_full_inventory(inv)
                    issues, summary = tw.scan_candidates(
                        rules, candidates,
                        status_store=self.status_store,
                        progress_cb=_scan_progress,
                    )
                    if summary is not None:
                        summary.failed_sources = failed
                        summary.sources_covered = covered
                        summary.glossary_rules = len(rules)
                        try:
                            tw.save_last_scan(summary)
                        except Exception:
                            pass
                except Exception as e:
                    self.parent.after(0, lambda err=e: messagebox.showerror(
                        self._t("tw_btn_scan"),
                        f"Scan post-processing failed: {err}"
                    ))

            def _done():
                self._scan_running = False
                self.issues = issues
                self.summary = summary
                self._refresh_filter_options()
                self._apply_filters()
                self._render_state()
                self._hide_progress_bar()

            self.parent.after(0, _done)

        self._scan_thread = threading.Thread(target=_worker, daemon=True)
        self._scan_thread.start()

    # ------------------------------------------------------------------
    def _on_select_issue(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            self._selected_issue = None
            self._set_actions_state("disabled")
            return
        # When multi-select, show the first
        issue = self._iid_to_issue.get(sel[0])
        self._selected_issue = issue
        self._render_detail(issue)
        self._set_actions_state("normal")

    def _set_actions_state(self, state):
        for attr in ("btn_act_reviewed", "btn_act_reported", "btn_act_ignore",
                     "btn_act_copy", "btn_act_export_one"):
            b = getattr(self, attr, None)
            if b is not None:
                b.configure(state=state)

    def _render_detail(self, i: tw.TerminologyIssue):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")

        def add(h, val, *tags):
            self.detail_text.insert("end", h + "\n", "h")
            self.detail_text.insert("end", (val or "(empty)") + "\n\n", *tags)

        self.detail_text.insert("end",
            f"[{i.severity}]  {i.source_term}  →  {i.locale_display or i.locale}\n",
            "h")
        self.detail_text.insert("end",
            f"Status: {i.status}   Type: {i.issue_type}\n"
            f"Issue ID: {i.issue_id}\n\n")
        add("Source text:", i.source_text)
        add("Expected:", i.expected, "expected")
        add("Actual:", i.actual, "actual")
        if i.forbidden_found:
            add("Forbidden variants found:", ", ".join(i.forbidden_found))
        add("Product / Source / Key:",
            f"{i.product}  ·  {i.source_label} ({i.source_kind})  ·  {i.key}")
        if i.task_id:
            add("Task ID:", i.task_id)
        if i.mr_id:
            add("MR ID:", i.mr_id)
        if i.rule_notes:
            add("Rule notes:", i.rule_notes)
        if i.score is not None:
            add("Score:", str(i.score))
        if i.error_category:
            add("Error category:", i.error_category)

        self.detail_text.configure(state="disabled")

    # ------------------------------------------------------------------
    def _set_status(self, new_status):
        if not self._selected_issue:
            return
        i = self._selected_issue
        try:
            self.status_store.set(i.issue_id, new_status)
        except Exception as e:
            messagebox.showerror("Status", str(e))
            return
        i.status = new_status
        # Also reflect in self.issues list
        for x in self.issues:
            if x.issue_id == i.issue_id:
                x.status = new_status
        # Re-render table; if Ignored, drop from active list
        if new_status == "Ignored":
            self.issues = [x for x in self.issues if x.issue_id != i.issue_id]
            self._selected_issue = None
            self._set_actions_state("disabled")
        self._apply_filters()
        self._render_detail(i) if self._selected_issue else None

    def _copy_summary(self):
        if not self._selected_issue:
            return
        text = tw.issue_summary_text(self._selected_issue)
        try:
            self.parent.clipboard_clear()
            self.parent.clipboard_append(text)
        except Exception:
            pass

    def _export_selected_issue(self):
        if not self._selected_issue:
            return
        self._open_export_dialog(scope="selected", preselect=[self._selected_issue])

    def _on_export_evidence(self):
        if not self.issues and not self.summary:
            messagebox.showinfo(self._t("tw_btn_export_evidence"),
                                self._t("tw_no_active_scan"))
            return
        # Selected issues: any selected rows in table
        selected: List[tw.TerminologyIssue] = []
        for iid in self.tree.selection():
            cand = self._iid_to_issue.get(iid)
            if cand:
                selected.append(cand)
        self._open_export_dialog(scope="ask", preselect=selected)

    # ------------------------------------------------------------------
    def _open_export_dialog(self, scope, preselect):
        dlg = _ExportDialog(self.parent, self, preselect_selected=preselect)
        if not dlg.result:
            return
        chosen_scope, fmt = dlg.result

        if chosen_scope == "selected":
            issues = preselect
        elif chosen_scope == "filtered":
            issues = list(self.filtered_issues)
        elif chosen_scope == "critical_high":
            issues = [i for i in self.issues if i.severity in ("Critical", "High")]
        else:  # all_active
            issues = list(self.issues)

        if not issues:
            messagebox.showinfo(self._t("tw_btn_export_evidence"),
                                self._t("tw_no_active_scan"))
            return

        ext = {"html": ".html", "xlsx": ".xlsx", "md": ".md"}[fmt]
        default_name = "term_watchtower_evidence_{}{}".format(
            datetime.now().strftime("%Y%m%d_%H%M%S"), ext,
        )

        if fmt == "html":
            # HTML reports skip the save dialog: write to a session-scoped
            # reports folder under the watchtower data dir, then open in the
            # OS default browser. The user reviews on screen; if they want
            # to keep it long-term, the file is still on disk at this path.
            reports_dir = os.path.join(tw.ensure_data_dir(), "reports")
            os.makedirs(reports_dir, exist_ok=True)
            path = os.path.join(reports_dir, default_name)
        else:
            ftypes = {
                "xlsx": [("Excel", "*.xlsx")],
                "md":   [("Markdown", "*.md")],
            }[fmt]
            path = filedialog.asksaveasfilename(
                title=self._t("tw_btn_export_evidence"),
                defaultextension=ext,
                filetypes=ftypes,
                initialfile=default_name,
            )
            if not path:
                return

        filters = self._current_filters_dict() if chosen_scope == "filtered" else None
        summary = self.summary or tw.ScanSummary()
        # Update report-time summary counts to reflect this report's scope
        rep_summary = tw.ScanSummary(**{**summary.to_dict()})

        try:
            if fmt == "html":
                tw.write_evidence_html(issues, rep_summary, path,
                                       filters_applied=filters)
            elif fmt == "xlsx":
                tw.export_evidence_xlsx(issues, rep_summary, path,
                                        filters_applied=filters)
            else:
                tw.write_evidence_markdown(issues, rep_summary, path,
                                           filters_applied=filters)
        except Exception as e:
            messagebox.showerror(self._t("tw_btn_export_evidence"), str(e))
            return

        if fmt == "html":
            # Auto-open in browser, no follow-up dialog.
            try:
                webbrowser.open("file://" + os.path.abspath(path))
            except Exception:
                pass
        else:
            messagebox.showinfo(self._t("tw_btn_export_evidence"),
                                self._t("tw_export_done", path=path))

    def _current_filters_dict(self) -> Dict[str, str]:
        return {
            "search": self.search_var.get(),
            "severity": self.sev_var.get(),
            "locale": self.loc_var.get(),
            "product": self.prod_var.get(),
            "source_kind": self.src_var.get(),
            "status": self.status_var.get(),
        }

    # ==================================================================
    # i18n refresh
    # ==================================================================
    def refresh_text(self):
        self.btn_scan.configure(text=self._t("tw_btn_scan"))
        self.btn_import.configure(text=self._t("tw_btn_import"))
        self.btn_quick.configure(text=self._t("tw_btn_quick_check"))
        self.btn_tranzor_term.configure(text=self._t("tw_btn_tranzor_term"))
        self.btn_export.configure(text=self._t("tw_btn_export_evidence"))
        for k, key in (("total", "tw_kpi_total"), ("critical", "tw_kpi_critical"),
                       ("high", "tw_kpi_high"), ("terms", "tw_kpi_terms"),
                       ("locales", "tw_kpi_locales")):
            self.kpi_titles[k].configure(text=self._t(key))

        try:
            self.sub_nb.tab(0, text=self._t("tw_subtab_issues"))
            self.sub_nb.tab(1, text=self._t("tw_subtab_glossary"))
        except Exception:
            pass

        self.lbl_search.configure(text=self._t("tw_filter_search"))
        self.lbl_sev.configure(text=self._t("tw_filter_severity"))
        self.lbl_loc.configure(text=self._t("tw_filter_locale"))
        self.lbl_prod.configure(text=self._t("tw_filter_product"))
        self.lbl_src.configure(text=self._t("tw_filter_source"))
        self.lbl_status.configure(text=self._t("tw_filter_status"))

        for col, key in (
            ("severity", "tw_col_severity"), ("status", "tw_col_status"),
            ("term", "tw_col_term"), ("locale", "tw_col_locale"),
            ("expected", "tw_col_expected"), ("actual", "tw_col_actual"),
            ("product", "tw_col_product"), ("source", "tw_col_source"),
            ("key", "tw_col_key"), ("issue_type", "tw_col_issue_type"),
        ):
            self.tree.heading(col, text=self._t(key))

        self.lbl_detail_title.configure(text=self._t("tw_detail_title"))
        if self._selected_issue is None:
            self.lbl_detail_hint.configure(text=self._t("tw_detail_select_hint"))

        self.btn_act_reviewed.configure(text=self._t("tw_act_mark_reviewed"))
        self.btn_act_reported.configure(text=self._t("tw_act_mark_reported"))
        self.btn_act_ignore.configure(text=self._t("tw_act_ignore"))
        self.btn_act_copy.configure(text=self._t("tw_act_copy_summary"))
        self.btn_act_export_one.configure(text=self._t("tw_act_export_one"))

        self.btn_g_import.configure(text=self._t("tw_btn_import"))
        self.btn_g_export.configure(text=self._t("tw_btn_export_glossary"))
        self.btn_g_template.configure(text=self._t("tw_btn_export_template"))
        self.lbl_glossary_hint.configure(text=self._t("tw_glossary_hint"))

        for col, key in (
            ("enabled",  "tw_glossary_col_enabled"),
            ("rule_id",  "tw_glossary_col_rule"),
            ("term",     "tw_glossary_col_term"),
            ("locale",   "tw_glossary_col_locale"),
            ("approved", "tw_glossary_col_approved"),
            ("forbidden","tw_glossary_col_forbidden"),
            ("scope",    "tw_glossary_col_scope"),
            ("severity", "tw_glossary_col_severity"),
            ("notes",    "tw_glossary_col_notes"),
        ):
            self.gtree.heading(col, text=self._t(key))

        self._refresh_glossary_view()
        self._render_state()


# ============================================================
# Sub-dialogs
# ============================================================

CHECK_ON = "☑"
CHECK_OFF = "☐"


class _TranzorTerminologyDialog(tk.Toplevel):
    """Browse Tranzor Platform's terminology library and pick which terms
    to scan against. Renders the read-only list lazily, with filter +
    select-all controls. On confirm, fetches selected term details in
    parallel and returns them along with the scan scope.
    """

    def __init__(self, parent, tab: TermWatchtowerTab):
        super().__init__(parent)
        self.tab = tab
        self.title(tab._t("tw_tt_title"))
        self.transient(parent)
        self.resizable(True, True)
        self.geometry("980x720")
        self.result: Optional[tuple] = None

        self._all_terms: List[Dict[str, Any]] = []  # raw list rows
        self._iid_by_id: Dict[int, str] = {}
        self._loading = False

        body = ttk.Frame(self)
        body.pack(padx=14, pady=12, fill="both", expand=True)

        # ── Header row: status + filter + select-all controls ──
        head = ttk.Frame(body)
        head.pack(fill="x")
        self.lbl_status = ttk.Label(head, text=tab._t("tw_tt_loading",
                                                       n=0, total=0),
                                    font=(FONT_FAMILY, 10, "bold"))
        self.lbl_status.pack(side="left")

        ttk.Label(head, text=tab._t("tw_tt_severity") + ":"
                  ).pack(side="right")
        self.sev_var = tk.StringVar(value="High")
        ttk.Combobox(head, textvariable=self.sev_var, width=10,
                     state="readonly",
                     values=list(tw.SEVERITIES)
                     ).pack(side="right", padx=(4, 16))

        # ── Filter row ──
        frow = ttk.Frame(body)
        frow.pack(fill="x", pady=(8, 4))
        ttk.Label(frow, text=tab._t("tw_tt_filter") + ":").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._render_terms())
        tk.Entry(frow, textvariable=self.filter_var, width=36,
                 font=(FONT_FAMILY, 10),
                 bg="#0a0a1a", fg="#fff",
                 insertbackground="#fff", relief="flat"
                 ).pack(side="left", padx=(4, 12), ipady=2)

        # DNT filter — pick "any" / "DNT only" / "Translatable only". Keys
        # are stable identifiers; the display label uses localized strings.
        ttk.Label(frow, text=tab._t("tw_tt_filter_dnt") + ":"
                  ).pack(side="left")
        self._dnt_options = (
            ("any", tab._t("tw_tt_dnt_any")),
            ("yes", tab._t("tw_tt_dnt_yes")),
            ("no",  tab._t("tw_tt_dnt_no")),
        )
        self._dnt_label_to_key = {lbl: key for key, lbl in self._dnt_options}
        self.dnt_var = tk.StringVar(value=self._dnt_options[0][1])
        cmb_dnt = ttk.Combobox(
            frow, textvariable=self.dnt_var,
            width=max(8, max(len(lbl) for _, lbl in self._dnt_options) + 2),
            state="readonly",
            values=[lbl for _, lbl in self._dnt_options],
        )
        cmb_dnt.pack(side="left", padx=(4, 12))
        cmb_dnt.bind("<<ComboboxSelected>>",
                     lambda *_: self._render_terms())

        for txt_key, cmd in (
            ("tw_tt_select_all",     lambda: self._set_all(True)),
            ("tw_tt_select_none",    lambda: self._set_all(False)),
            ("tw_tt_select_visible", self._select_visible),
        ):
            tab.app._create_button(
                frow, text=tab._t(txt_key), command=cmd,
                style_name="SecondaryTiny",
                font=(FONT_FAMILY, 9),
                bg="#1f2a48", fg="#fff", padx=8, pady=2,
            ).pack(side="left", padx=(0, 4))

        self.lbl_selected = ttk.Label(frow, text="0 selected")
        self.lbl_selected.pack(side="right")

        # ── Term list ──
        tree_wrap = ttk.Frame(body)
        tree_wrap.pack(fill="both", expand=True)
        cols = ("check", "name", "scope", "locales", "dnt")
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings",
                                 height=22, selectmode="none")
        self.tree.heading("check", text="")
        self.tree.heading("name", text=tab._t("tw_tt_col_name"))
        self.tree.heading("scope", text=tab._t("tw_tt_col_scope"))
        self.tree.heading("locales", text=tab._t("tw_tt_col_locales"))
        self.tree.heading("dnt", text=tab._t("tw_tt_col_dnt"))
        self.tree.column("check", width=36, anchor="center", stretch=False)
        self.tree.column("name", width=420, anchor="w", stretch=True)
        self.tree.column("scope", width=110, anchor="w", stretch=False)
        self.tree.column("locales", width=80, anchor="e", stretch=False)
        self.tree.column("dnt", width=60, anchor="center", stretch=False)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-Button-1>", self._on_tree_double_click)

        self.lbl_double_click = ttk.Label(
            body, text=tab._t("tw_tt_double_click_hint"),
            foreground="#7a7a8a",
        )
        self.lbl_double_click.pack(anchor="w", pady=(4, 0))

        # Scope is picked downstream via the unified _ScopeDialog so all
        # three entry points (Scan Now, Quick Check, Tranzor Terminology)
        # share the same project-list / Task ID UX.

        # ── Buttons ──
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(12, 0))
        tab.app._create_button(
            btns, text=tab._t("tw_export_btn_cancel"),
            command=self._cancel,
            style_name="Secondary",
            bg="#1f2a48", fg="#fff", padx=10, pady=4,
        ).pack(side="right")
        self.btn_run = tab.app._create_button(
            btns, text=tab._t("tw_tt_run", n=0),
            command=self._ok,
            style_name="Accent",
            bg=getattr(tab.app, "ACCENT_BTN", "#e94560"), fg="#fff",
            padx=12, pady=4, state="disabled",
        )
        self.btn_run.pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda *_: self._cancel())
        self.grab_set()
        # Kick off the lazy load on first show
        self.after(50, self._load_list)
        self.wait_window(self)

    # ---- list load --------------------------------------------------------
    def _load_list(self):
        if self._loading:
            return
        self._loading = True

        def _progress(loaded, total):
            self.after(0, lambda: self.lbl_status.configure(
                text=self.tab._t("tw_tt_loading", n=loaded, total=total)
            ))

        def _worker():
            try:
                terms = tz_term.fetch_terminology_list(progress_cb=_progress)
                self.after(0, self._on_loaded, terms, None)
            except Exception as e:
                self.after(0, self._on_loaded, None, str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_loaded(self, terms, err):
        self._loading = False
        if err is not None:
            self.lbl_status.configure(
                text=self.tab._t("tw_tt_error", err=err))
            return
        self._all_terms = terms or []
        self.lbl_status.configure(
            text=self.tab._t("tw_tt_loaded", n=len(self._all_terms)))
        self._render_terms()

    # ---- rendering --------------------------------------------------------
    def _render_terms(self):
        """Apply current filter and refresh the tree.

        Rather than re-adding rows on every keystroke (slow at 2k+ terms),
        we keep the iid map stable and just detach/reattach rows.
        """
        kw = self.filter_var.get().strip().lower()
        dnt_key = self._dnt_label_to_key.get(self.dnt_var.get(), "any")
        # Initial population
        if not self._iid_by_id and self._all_terms:
            for t in self._all_terms:
                tid = int(t.get("id") or 0)
                if not tid:
                    continue
                iid = f"t{tid}"
                self._iid_by_id[tid] = iid
                self.tree.insert(
                    "", "end", iid=iid,
                    values=(
                        CHECK_OFF,
                        t.get("name") or "",
                        t.get("scope") or "",
                        t.get("translation_count") or 0,
                        "Yes" if t.get("dnt") else "",
                    ),
                )
        # Filter by name+scope substring AND by DNT classifier
        for t in self._all_terms:
            tid = int(t.get("id") or 0)
            iid = self._iid_by_id.get(tid)
            if not iid:
                continue
            hay = ((t.get("name") or "") + " " + (t.get("scope") or "")).lower()
            text_ok = (kw in hay) if kw else True
            is_dnt = bool(t.get("dnt"))
            if dnt_key == "yes":
                dnt_ok = is_dnt
            elif dnt_key == "no":
                dnt_ok = not is_dnt
            else:
                dnt_ok = True
            visible = text_ok and dnt_ok
            try:
                if visible:
                    self.tree.reattach(iid, "", "end")
                else:
                    self.tree.detach(iid)
            except Exception:
                pass

    # ---- check handling ---------------------------------------------------
    def _on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        col = self.tree.identify_column(event.x)
        if region != "cell" or col != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        vals = list(self.tree.item(iid, "values"))
        vals[0] = CHECK_OFF if vals[0] == CHECK_ON else CHECK_ON
        self.tree.item(iid, values=vals)
        self._update_run_btn()

    def _on_tree_double_click(self, event):
        """Show the term's live definition pulled from Tranzor Platform.

        Double-click on any row column EXCEPT the check column, which
        single-click already handles. The platform SPA has no per-term
        deep link, so we render the API detail in a child dialog.
        """
        region = self.tree.identify("region", event.x, event.y)
        col = self.tree.identify_column(event.x)
        if region != "cell" or col == "#1":
            return
        iid = self.tree.identify_row(event.y)
        if not iid or not iid.startswith("t"):
            return
        try:
            tid = int(iid[1:])
        except ValueError:
            return
        try:
            vals = self.tree.item(iid, "values")
        except Exception:
            vals = ()
        preview_name = vals[1] if len(vals) > 1 else ""
        preview_scope = vals[2] if len(vals) > 2 else ""
        _TermDetailDialog(self, self.tab, tid, preview_name, preview_scope)

    def _set_all(self, on: bool):
        for iid in self._iid_by_id.values():
            try:
                vals = list(self.tree.item(iid, "values"))
                vals[0] = CHECK_ON if on else CHECK_OFF
                self.tree.item(iid, values=vals)
            except Exception:
                pass
        self._update_run_btn()

    def _select_visible(self):
        for iid in self.tree.get_children(""):
            try:
                vals = list(self.tree.item(iid, "values"))
                vals[0] = CHECK_ON
                self.tree.item(iid, values=vals)
            except Exception:
                pass
        self._update_run_btn()

    def _checked_ids(self) -> List[int]:
        out = []
        for tid, iid in self._iid_by_id.items():
            try:
                vals = self.tree.item(iid, "values")
            except Exception:
                continue
            if vals and vals[0] == CHECK_ON:
                out.append(tid)
        return out

    def _update_run_btn(self):
        n = len(self._checked_ids())
        self.lbl_selected.configure(text=f"{n} selected")
        self.btn_run.configure(text=self.tab._t("tw_tt_run", n=n),
                               state="normal" if n else "disabled")

    # ---- run --------------------------------------------------------------
    def _ok(self):
        ids = self._checked_ids()
        if not ids:
            messagebox.showinfo(self.tab._t("tw_btn_tranzor_term"),
                                self.tab._t("tw_tt_no_selection"),
                                parent=self)
            return

        self.btn_run.configure(state="disabled")
        self.lbl_status.configure(
            text=self.tab._t("tw_tt_fetching_details", n=len(ids)))

        def _progress(done, total):
            self.after(0, lambda: self.lbl_status.configure(
                text=self.tab._t("tw_tt_fetching_details", n=total) +
                     f"  {done}/{total}"
            ))

        def _worker():
            try:
                detail_map = tz_term.fetch_many_details(
                    ids, progress_cb=_progress,
                )
                details = list(detail_map.values())
                self.after(0, self._on_details_done, details, None)
            except Exception as e:
                self.after(0, self._on_details_done, None, str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_details_done(self, details, err):
        if err is not None:
            messagebox.showerror(self.tab._t("tw_btn_tranzor_term"),
                                 self.tab._t("tw_tt_error", err=err),
                                 parent=self)
            self.btn_run.configure(state="normal")
            return
        self.result = (details, self.sev_var.get())
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class _TermDetailDialog(tk.Toplevel):
    """Read-only window showing one term's full definition, fetched live
    from Tranzor Platform.

    The platform SPA renders term details inside a modal and does not
    expose a deep link per id, so opening a browser tab from
    ``tw_btn_tranzor_term``'s picker would land on a 404. Instead, this
    dialog replicates the relevant fields of the "View Term" modal
    in-app using :func:`tranzor_terminology.fetch_terminology_detail`.
    """

    def __init__(self, parent, tab: TermWatchtowerTab, term_id: int,
                 preview_name: str = "", preview_scope: str = ""):
        super().__init__(parent)
        self.tab = tab
        self.term_id = int(term_id)
        self.title(tab._t("tw_td_title", name=preview_name or f"#{term_id}"))
        self.transient(parent)
        self.resizable(True, True)
        self.geometry("720x640")

        body = ttk.Frame(self)
        body.pack(padx=14, pady=12, fill="both", expand=True)

        head = ttk.Frame(body)
        head.pack(fill="x")
        self.lbl_name = ttk.Label(
            head, text=preview_name or "…",
            font=(FONT_FAMILY, 14, "bold"),
        )
        self.lbl_name.pack(side="left", anchor="w")
        self.lbl_scope = ttk.Label(
            head, text=preview_scope or "",
            foreground="#7a7a8a",
        )
        self.lbl_scope.pack(side="right", anchor="e")

        self.lbl_meta = ttk.Label(
            body, text=tab._t("tw_td_loading"),
            foreground="#7a7a8a",
        )
        self.lbl_meta.pack(fill="x", anchor="w", pady=(2, 0))

        # "Author info" advisory — Tranzor Platform's term schema does NOT
        # record created_by / updated_by user identity (only timestamps).
        # We surface this once per dialog so users don't keep looking for
        # an "author" field that doesn't exist server-side.
        self.lbl_author_note = ttk.Label(
            body, text="",
            foreground="#7a7a8a",
            font=(FONT_FAMILY, 9, "italic"),
            wraplength=680, justify="left",
        )
        self.lbl_author_note.pack(fill="x", anchor="w", pady=(0, 8))

        canvas_wrap = ttk.Frame(body)
        canvas_wrap.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(canvas_wrap, highlightthickness=0,
                                 bg=self.cget("background"))
        sbar = ttk.Scrollbar(canvas_wrap, orient="vertical",
                             command=self._canvas.yview)
        self.content = ttk.Frame(self._canvas)
        self._content_window = self._canvas.create_window(
            (0, 0), window=self.content, anchor="nw")
        self.content.bind(
            "<Configure>",
            lambda e: self._canvas.configure(
                scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfigure(
                self._content_window, width=e.width),
        )
        self._canvas.configure(yscrollcommand=sbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        sbar.pack(side="right", fill="y")

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=14, pady=(0, 12))
        tab.app._create_button(
            btns, text=tab._t("tw_td_open_browser"),
            command=self._open_in_browser,
            style_name="Secondary",
            bg="#1f2a48", fg="#fff", padx=10, pady=4,
        ).pack(side="left")
        tab.app._create_button(
            btns, text=tab._t("tw_td_close"),
            command=self.destroy,
            style_name="Secondary",
            bg="#1f2a48", fg="#fff", padx=10, pady=4,
        ).pack(side="right")

        self.bind("<Escape>", lambda *_: self.destroy())
        threading.Thread(target=self._worker, daemon=True).start()

    # ---- detail fetch -----------------------------------------------------
    def _worker(self):
        try:
            detail = tz_term.fetch_terminology_detail(self.term_id)
            self.after(0, self._render, detail, None)
        except Exception as e:
            self.after(0, self._render, None, str(e))

    # ---- rendering --------------------------------------------------------
    def _render(self, detail, err):
        for w in self.content.winfo_children():
            w.destroy()
        if err is not None:
            self.lbl_meta.configure(
                text=self.tab._t("tw_td_error", err=err))
            return
        if not detail:
            self.lbl_meta.configure(text=self.tab._t("tw_td_empty"))
            return

        name = (detail.get("name") or "").strip() \
            or self.lbl_name.cget("text")
        scope = (detail.get("scope") or "").strip()
        code = (detail.get("code") or "").strip()
        dnt = bool(detail.get("dnt"))
        self.lbl_name.configure(text=name)
        if scope:
            self.lbl_scope.configure(text=scope)
        self.title(self.tab._t("tw_td_title", name=name))

        meta_parts = [f"ID: {self.term_id}"]
        if code:
            meta_parts.append(f"Code: {code}")
        meta_parts.append(self.tab._t("tw_td_dnt_yes")
                          if dnt else self.tab._t("tw_td_dnt_no"))
        updated_at = self._format_updated_at(detail.get("updated_at"))
        if updated_at:
            meta_parts.append(
                f"{self.tab._t('tw_td_updated_at')}: {updated_at}"
            )
        self.lbl_meta.configure(text="  ·  ".join(meta_parts))
        self.lbl_author_note.configure(
            text=self.tab._t("tw_td_author_unavailable"),
        )

        def _field(label_key, value):
            value = (value or "").strip() if isinstance(value, str) else value
            if not value:
                return
            ttk.Label(self.content,
                      text=self.tab._t(label_key) + ":",
                      font=(FONT_FAMILY, 10, "bold")
                      ).pack(anchor="w", pady=(8, 0))
            ttk.Label(self.content, text=str(value),
                      wraplength=640, justify="left"
                      ).pack(anchor="w", fill="x")

        _field("tw_td_definition", detail.get("definition"))
        _field("tw_td_context", detail.get("context"))
        _field("tw_td_part_of_speech", detail.get("part_of_speech"))
        _field("tw_td_reference", detail.get("reference"))
        _field("tw_td_remarks", detail.get("remarks"))
        _field("tw_td_notes", detail.get("notes"))

        translations = detail.get("translations") or []
        if translations:
            ttk.Label(self.content,
                      text=self.tab._t("tw_td_translations"),
                      font=(FONT_FAMILY, 10, "bold")
                      ).pack(anchor="w", pady=(12, 4))
            tr_cols = ("lang", "text", "remarks", "copy")
            tr = ttk.Treeview(self.content, columns=tr_cols,
                              show="headings",
                              height=min(len(translations) + 1, 10))
            tr.heading("lang", text=self.tab._t("tw_td_col_lang"))
            tr.heading("text", text=self.tab._t("tw_td_col_text"))
            tr.heading("remarks", text=self.tab._t("tw_td_col_remarks"))
            tr.heading("copy", text="")
            tr.column("lang", width=80, anchor="w", stretch=False)
            tr.column("text", width=300, anchor="w", stretch=True)
            tr.column("remarks", width=160, anchor="w", stretch=False)
            tr.column("copy", width=84, anchor="center", stretch=False)
            tr.pack(fill="x")
            copy_label = self.tab._t("tw_td_copy_btn")
            for t in translations:
                text = (t.get("translated_name") or "").strip()
                tr.insert("", "end", values=(
                    t.get("language_code") or "",
                    text,
                    t.get("remarks") or "",
                    copy_label if text else "",
                ))
            tr.bind("<Button-1>", self._on_translation_click)

        variants = detail.get("variants") or []
        if variants:
            ttk.Label(self.content,
                      text=self.tab._t("tw_td_variants"),
                      font=(FONT_FAMILY, 10, "bold")
                      ).pack(anchor="w", pady=(12, 4))
            v_cols = ("type", "name")
            tv = ttk.Treeview(self.content, columns=v_cols,
                              show="headings",
                              height=min(len(variants) + 1, 6))
            tv.heading("type", text=self.tab._t("tw_td_col_type"))
            tv.heading("name", text=self.tab._t("tw_td_col_name"))
            tv.column("type", width=120, anchor="w", stretch=False)
            tv.column("name", width=320, anchor="w", stretch=True)
            tv.pack(fill="x")
            for v in variants:
                tv.insert("", "end", values=(
                    v.get("type") or "",
                    v.get("name") or "",
                ))

    @staticmethod
    def _format_updated_at(value: Any) -> str:
        """Format an ISO datetime (the only audit field Tranzor exposes)
        as ``YYYY-MM-DD HH:MM:SS``. Returns ``""`` for missing / unparsable
        values so the caller can skip rendering the field entirely.

        The platform serializes ``updated_at`` as Pydantic-encoded ISO
        (e.g. ``"2026-03-06T14:23:45.123456"`` or ``"…+00:00"``). We
        tolerate a trailing ``Z`` defensively since stdlib's
        ``fromisoformat`` didn't accept it until Python 3.11.
        """
        if not value:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return datetime.fromisoformat(text).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except Exception:
            return text[:19]  # best-effort fallback

    def _open_in_browser(self):
        try:
            webbrowser.open(tz_term.terminology_app_url())
        except Exception as e:
            messagebox.showerror(self.tab._t("tw_btn_tranzor_term"),
                                 self.tab._t("tw_td_open_failed",
                                              err=str(e)),
                                 parent=self)

    # ---- per-row copy ----------------------------------------------------
    def _on_translation_click(self, event):
        """Single-click on the rightmost 'Copy' column → copy that row's
        Text to the OS clipboard and flash a transient '✓ Copied' state.
        """
        tree = event.widget
        region = tree.identify("region", event.x, event.y)
        col = tree.identify_column(event.x)
        if region != "cell" or col != "#4":
            return
        iid = tree.identify_row(event.y)
        if not iid:
            return
        try:
            vals = list(tree.item(iid, "values"))
        except Exception:
            return
        if len(vals) < 4 or not (vals[1] or "").strip():
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(str(vals[1]))
            # tkinter clipboard only persists while the app lives; on
            # most desktops this is fine because users paste right away.
        except Exception as e:
            messagebox.showerror(self.tab._t("tw_btn_tranzor_term"),
                                 str(e), parent=self)
            return
        vals[3] = self.tab._t("tw_td_copied")
        try:
            tree.item(iid, values=vals)
        except Exception:
            return
        self.after(900, lambda: self._restore_copy_label(tree, iid))

    def _restore_copy_label(self, tree, iid):
        try:
            vals = list(tree.item(iid, "values"))
        except Exception:
            return
        if len(vals) < 4:
            return
        vals[3] = (self.tab._t("tw_td_copy_btn")
                   if (vals[1] or "").strip() else "")
        try:
            tree.item(iid, values=vals)
        except Exception:
            pass


class _ScopeDialog(tk.Toplevel):
    """Pick scan scope: sources × project subset, or a single Task ID.

    The project list is populated lazily from
    ``export_full_translations.build_light_inventory`` — same lightweight
    discovery path the Full Translations tab uses, so no heavy translation
    fetches happen here.
    """

    def __init__(self, parent, tab: TermWatchtowerTab):
        super().__init__(parent)
        self.tab = tab
        self.title(tab._t("tw_scope_title"))
        self.transient(parent)
        self.resizable(True, True)
        self.geometry("780x600")
        self.result: Optional[Dict[str, Any]] = None

        self._all_iids: List[str] = []
        self._loading = False

        body = ttk.Frame(self)
        body.pack(padx=14, pady=12, fill="both", expand=True)

        # Source row
        srow = ttk.Frame(body)
        srow.pack(fill="x")
        ttk.Label(srow, text=tab._t("tw_scope_sources") + ":",
                  font=(FONT_FAMILY, 10, "bold")).pack(side="left")
        self.v_legacy = tk.BooleanVar(value=True)
        self.v_mr = tk.BooleanVar(value=True)
        self.v_scan = tk.BooleanVar(value=False)
        ttk.Checkbutton(srow, text=tab._t("tw_src_legacy"),
                        variable=self.v_legacy).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(srow, text=tab._t("tw_src_mr"),
                        variable=self.v_mr).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(srow, text=tab._t("tw_src_scan"),
                        variable=self.v_scan).pack(side="left", padx=(8, 0))

        self.btn_load = tab.app._create_button(
            srow, text=tab._t("tw_scope_load"), command=self._load_inventory,
            style_name="Secondary",
            font=(FONT_FAMILY, 10),
            bg="#1f2a48", fg="#fff", padx=10, pady=4,
        )
        self.btn_load.pack(side="right")

        # Search + select all/none
        frow = ttk.Frame(body)
        frow.pack(fill="x", pady=(8, 4))
        ttk.Label(frow, text=tab._t("tw_scope_search") + ":").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render_projects())
        tk.Entry(frow, textvariable=self.search_var, width=30,
                 font=(FONT_FAMILY, 10),
                 bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat"
                 ).pack(side="left", padx=(4, 8), ipady=2)
        tab.app._create_button(
            frow, text=tab._t("tw_scope_select_all"),
            command=lambda: self._set_all(True),
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 9), bg="#1f2a48", fg="#fff", padx=8, pady=2,
        ).pack(side="left", padx=(0, 4))
        tab.app._create_button(
            frow, text=tab._t("tw_scope_select_none"),
            command=lambda: self._set_all(False),
            style_name="SecondaryTiny",
            font=(FONT_FAMILY, 9), bg="#1f2a48", fg="#fff", padx=8, pady=2,
        ).pack(side="left")
        self.lbl_status = ttk.Label(frow, text=tab._t("tw_scope_no_projects"))
        self.lbl_status.pack(side="right")

        # Project tree
        tree_wrap = ttk.Frame(body)
        tree_wrap.pack(fill="both", expand=True)
        cols = ("check", "source", "label", "keys")
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings",
                                 height=14, selectmode="none")
        self.tree.heading("check", text="")
        self.tree.heading("source", text="Source")
        self.tree.heading("label", text="Project")
        self.tree.heading("keys", text="Keys")
        self.tree.column("check", width=36, anchor="center", stretch=False)
        self.tree.column("source", width=80, anchor="w", stretch=False)
        self.tree.column("label", width=420, anchor="w", stretch=True)
        self.tree.column("keys", width=80, anchor="e", stretch=False)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_tree_click)

        # Mode + Task ID row
        ttk.Separator(body).pack(fill="x", pady=8)
        mode_row = ttk.Frame(body)
        mode_row.pack(fill="x")
        ttk.Label(mode_row, text=tab._t("tw_scope_mode_label") + ":",
                  font=(FONT_FAMILY, 10, "bold")).pack(side="left")
        self.mode_var = tk.StringVar(value="all")
        for value, key in (
            ("all",      "tw_scope_mode_all"),
            ("checked",  "tw_scope_mode_checked"),
            ("task",     "tw_scope_mode_task"),
        ):
            ttk.Radiobutton(mode_row, text=tab._t(key),
                            variable=self.mode_var, value=value
                            ).pack(side="left", padx=(8, 0))

        task_row = ttk.Frame(body)
        task_row.pack(fill="x", pady=(8, 0))
        ttk.Label(task_row, text=tab._t("tw_scope_task_id") + ":"
                  ).pack(side="left")
        self.task_id_var = tk.StringVar()
        tk.Entry(task_row, textvariable=self.task_id_var, width=42,
                 font=(FONT_FAMILY, 10),
                 bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat"
                 ).pack(side="left", padx=(6, 12), ipady=2)
        ttk.Label(task_row, text=tab._t("tw_scope_task_kind") + ":"
                  ).pack(side="left")
        self.task_kind_var = tk.StringVar(value="legacy")
        ttk.Combobox(task_row, textvariable=self.task_kind_var, width=10,
                     state="readonly",
                     values=["legacy", "mr", "scan"]
                     ).pack(side="left", padx=(4, 0))

        # Buttons
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(12, 0))
        tab.app._create_button(
            btns, text=tab._t("tw_export_btn_cancel"),
            command=self._cancel,
            style_name="Secondary",
            bg="#1f2a48", fg="#fff", padx=10, pady=4,
        ).pack(side="right")
        tab.app._create_button(
            btns, text=tab._t("tw_scope_btn_run"), command=self._ok,
            style_name="Accent",
            bg=getattr(tab.app, "ACCENT_BTN", "#e94560"), fg="#fff",
            padx=12, pady=4,
        ).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda *_: self._cancel())
        self.grab_set()
        self.wait_window(self)

    # ---- inventory loading -------------------------------------------------
    def _selected_sources(self):
        s = []
        if self.v_legacy.get(): s.append("legacy")
        if self.v_mr.get(): s.append("mr")
        if self.v_scan.get(): s.append("scan")
        return s

    def _load_inventory(self):
        if self._loading:
            return
        sources = self._selected_sources()
        if not sources:
            messagebox.showinfo(self.tab._t("tw_scope_title"),
                                self.tab._t("tw_scan_none_selected"),
                                parent=self)
            return
        self._loading = True
        self.btn_load.configure(state="disabled")
        self.lbl_status.configure(text=self.tab._t("tw_scope_loading"))

        def _worker():
            try:
                inv = _ft_api.build_light_inventory(sources=sources)
                self.after(0, self._on_loaded, inv, None)
            except Exception as e:
                self.after(0, self._on_loaded, None, str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_loaded(self, inv, err):
        self._loading = False
        self.btn_load.configure(state="normal")
        if err:
            self.lbl_status.configure(text=f"❌ {err}")
            return
        self._inv = inv
        self.tree.delete(*self.tree.get_children())
        self._all_iids = []
        for p in inv.products:
            cnt = p.get("entry_count")
            keys = f"{cnt:,}" if isinstance(cnt, int) else "—"
            iid = p["id"]
            self.tree.insert(
                "", "end", iid=iid,
                values=(CHECK_ON, p.get("source", "—"), p["label"], keys),
            )
            self._all_iids.append(iid)
        self.lbl_status.configure(
            text=self.tab._t("tw_scope_loaded",
                             n=len(inv.products), l=len(inv.locales))
        )
        # Switching to "checked" mode is implicit: as soon as user has a list
        # they probably want to filter; default radio still says "all" so
        # nothing surprises.

    # ---- tree interactions ------------------------------------------------
    def _on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        col = self.tree.identify_column(event.x)
        if region != "cell" or col != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        vals = list(self.tree.item(iid, "values"))
        vals[0] = CHECK_OFF if vals[0] == CHECK_ON else CHECK_ON
        self.tree.item(iid, values=vals)
        # Auto-switch radio to "checked" so the user's intent matches reality
        self.mode_var.set("checked")

    def _set_all(self, on: bool):
        for iid in self._all_iids:
            vals = list(self.tree.item(iid, "values"))
            vals[0] = CHECK_ON if on else CHECK_OFF
            self.tree.item(iid, values=vals)

    def _render_projects(self):
        kw = self.search_var.get().strip().lower()
        # ttk.Treeview doesn't filter natively; detach/reattach by match.
        for iid in self._all_iids:
            try:
                vals = self.tree.item(iid, "values")
            except Exception:
                continue
            label = (vals[2] if len(vals) > 2 else "").lower()
            visible = (kw in label) if kw else True
            try:
                if visible:
                    self.tree.reattach(iid, "", "end")
                else:
                    self.tree.detach(iid)
            except Exception:
                pass

    def _checked_ids(self) -> List[str]:
        out = []
        for iid in self._all_iids:
            try:
                vals = self.tree.item(iid, "values")
            except Exception:
                continue
            if vals and vals[0] == CHECK_ON:
                out.append(iid)
        return out

    def _ok(self):
        mode = self.mode_var.get()
        sources = self._selected_sources()
        if mode != "task" and not sources:
            messagebox.showinfo(self.tab._t("tw_scope_title"),
                                self.tab._t("tw_scan_none_selected"),
                                parent=self)
            return

        if mode == "task":
            tid = self.task_id_var.get().strip()
            if not tid:
                messagebox.showinfo(self.tab._t("tw_scope_title"),
                                    self.tab._t("tw_scope_task_required"),
                                    parent=self)
                return
            self.result = {
                "mode": "task",
                "task_id": tid,
                "task_kind": self.task_kind_var.get(),
                "sources": [self.task_kind_var.get()],
            }
            self.destroy()
            return

        if mode == "checked":
            ids = self._checked_ids()
            if not ids:
                messagebox.showinfo(self.tab._t("tw_scope_title"),
                                    self.tab._t("tw_scope_select_some"),
                                    parent=self)
                return
            inv = getattr(self, "_inv", None)
            if inv is None:
                messagebox.showinfo(self.tab._t("tw_scope_title"),
                                    self.tab._t("tw_scope_no_projects"),
                                    parent=self)
                return
            legacy_ps, mr_ps, scan_ps = inv.split_selection(ids)
            self.result = {
                "mode": "projects",
                "sources": sources,
                "legacy": list(legacy_ps),
                "mr": list(mr_ps),
                "scan": list(scan_ps),
            }
        else:
            self.result = {"mode": "all", "sources": sources}
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class _ExportDialog(tk.Toplevel):
    """Evidence export modal — scope + format."""

    def __init__(self, parent, tab: TermWatchtowerTab,
                 preselect_selected: List[tw.TerminologyIssue]):
        super().__init__(parent)
        self.tab = tab
        self.title(tab._t("tw_export_dialog_title"))
        self.transient(parent)
        self.resizable(False, False)
        self.result: Optional[tuple] = None

        body = ttk.Frame(self)
        body.pack(padx=20, pady=14)

        ttk.Label(body, text=tab._t("tw_export_scope"),
                  font=(FONT_FAMILY, 11, "bold")).pack(anchor="w")

        n_filtered = len(tab.filtered_issues)
        n_selected = len(preselect_selected)
        n_active = len(tab.issues)
        n_ch = sum(1 for i in tab.issues if i.severity in ("Critical", "High"))

        self.scope_var = tk.StringVar(
            value="selected" if n_selected else "filtered"
        )
        for value, label_key, n in (
            ("selected",      "tw_scope_selected",      n_selected),
            ("filtered",      "tw_scope_filtered",      n_filtered),
            ("critical_high", "tw_scope_critical_high", n_ch),
            ("all_active",    "tw_scope_all_active",    n_active),
        ):
            rb = ttk.Radiobutton(
                body, text=tab._t(label_key, n=n),
                variable=self.scope_var, value=value,
            )
            rb.pack(anchor="w", pady=(6 if value == "selected" else 0, 0))
            if value == "selected" and n_selected == 0:
                rb.configure(state="disabled")

        ttk.Separator(body).pack(fill="x", pady=10)
        ttk.Label(body, text=tab._t("tw_export_format"),
                  font=(FONT_FAMILY, 11, "bold")).pack(anchor="w")

        self.fmt_var = tk.StringVar(value="html")
        for value, key in (
            ("html", "tw_format_html"),
            ("xlsx", "tw_format_xlsx"),
            ("md",   "tw_format_md"),
        ):
            ttk.Radiobutton(body, text=tab._t(key),
                            variable=self.fmt_var, value=value
                            ).pack(anchor="w", pady=(6 if value == "html" else 0, 0))

        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(14, 0))
        tab.app._create_button(
            btns, text=tab._t("tw_export_btn_cancel"), command=self._cancel,
            style_name="Secondary",
            bg="#1f2a48", fg="#fff", padx=10, pady=4,
        ).pack(side="right")
        tab.app._create_button(
            btns, text=tab._t("tw_export_btn_generate"), command=self._ok,
            style_name="Accent",
            bg=getattr(tab.app, "ACCENT_BTN", "#e94560"), fg="#fff",
            padx=12, pady=4,
        ).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda *_: self._cancel())
        self.grab_set()
        self.wait_window(self)

    def _ok(self):
        self.result = (self.scope_var.get(), self.fmt_var.get())
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class _ImportModeDialog(tk.Toplevel):
    """Ask user: merge into existing glossary, replace, or cancel."""

    def __init__(self, parent, tab: TermWatchtowerTab, existing_count: int):
        super().__init__(parent)
        self.tab = tab
        self.title(tab._t("tw_btn_import"))
        self.transient(parent)
        self.resizable(False, False)
        self.result: Optional[str] = None

        body = ttk.Frame(self)
        body.pack(padx=20, pady=14)

        ttk.Label(
            body,
            text=tab._t("tw_import_choose_mode", n=existing_count),
            justify="left",
        ).pack(anchor="w")

        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(14, 0))

        tab.app._create_button(
            btns, text=tab._t("tw_import_mode_cancel"),
            command=self._cancel,
            style_name="Secondary",
            bg="#1f2a48", fg="#fff", padx=10, pady=4,
        ).pack(side="right")
        tab.app._create_button(
            btns, text=tab._t("tw_import_mode_replace"),
            command=lambda: self._set("replace"),
            style_name="Secondary",
            bg="#1f2a48", fg="#fff", padx=10, pady=4,
        ).pack(side="right", padx=(0, 8))
        tab.app._create_button(
            btns, text=tab._t("tw_import_mode_merge"),
            command=lambda: self._set("merge"),
            style_name="Accent",
            bg=getattr(tab.app, "ACCENT_BTN", "#e94560"), fg="#fff",
            padx=12, pady=4,
        ).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda *_: self._cancel())
        self.grab_set()
        self.wait_window(self)

    def _set(self, mode):
        self.result = mode
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class _QuickCheckDialog(tk.Toplevel):
    """Ad-hoc single-term check: source term + per-locale approved table."""

    def __init__(self, parent, tab: TermWatchtowerTab):
        super().__init__(parent)
        self.tab = tab
        self.title(tab._t("tw_qc_title"))
        self.transient(parent)
        self.resizable(True, True)
        self.result: Optional[tuple] = None

        body = ttk.Frame(self)
        body.pack(padx=16, pady=12, fill="both", expand=True)

        # Source term row
        row = ttk.Frame(body)
        row.pack(fill="x")
        ttk.Label(row, text=tab._t("tw_qc_source") + ":",
                  font=(FONT_FAMILY, 11, "bold")).pack(side="left")
        self.term_var = tk.StringVar()
        self.ent_term = tk.Entry(row, textvariable=self.term_var, width=32,
                                 font=(FONT_FAMILY, 11),
                                 bg="#0a0a1a", fg="#fff",
                                 insertbackground="#fff", relief="flat")
        self.ent_term.pack(side="left", padx=(8, 16), ipady=3)

        ttk.Label(row, text=tab._t("tw_qc_severity") + ":").pack(side="left")
        self.sev_var = tk.StringVar(value="High")
        ttk.Combobox(row, textvariable=self.sev_var, width=10,
                     state="readonly",
                     values=list(tw.SEVERITIES)).pack(side="left", padx=(4, 0))

        # Pairs textarea title row — three content-area helpers grouped
        # together on the right (Paste / Reorg / Clear). They all act on
        # the textarea below, so they belong in the same visual cluster
        # rather than the dialog's primary action row at the bottom.
        title_row = ttk.Frame(body)
        title_row.pack(fill="x", pady=(10, 0))
        ttk.Label(title_row, text=tab._t("tw_qc_pairs_label"),
                  font=(FONT_FAMILY, 10, "bold")).pack(side="left")

        # Pack right-to-left so the visual order reads:
        # Paste from clipboard | Reorg | Clear
        self.btn_clear_pairs = tab.app._create_button(
            title_row, text=tab._t("tw_qc_clear"),
            command=self._clear_pairs,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 9),
            bg="#1f2a48", activebackground="#2a3a5e",
            fg="#fff", activeforeground="#fff", padx=10, pady=2,
        )
        self.btn_clear_pairs.pack(side="right")

        self.btn_reorg = tab.app._create_button(
            title_row, text=tab._t("tw_qc_reorg"),
            command=self._reorg_paste,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 9),
            bg="#1f2a48", activebackground="#2a3a5e",
            fg="#fff", activeforeground="#fff", padx=10, pady=2,
        )
        self.btn_reorg.pack(side="right", padx=(0, 6))

        self.btn_paste_clip = tab.app._create_button(
            title_row, text=tab._t("tw_qc_paste_clipboard"),
            command=self._paste_clipboard,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 9),
            bg="#1f2a48", activebackground="#2a3a5e",
            fg="#fff", activeforeground="#fff", padx=10, pady=2,
        )
        self.btn_paste_clip.pack(side="right", padx=(0, 6))

        ttk.Label(body, text=tab._t("tw_qc_pairs_help"),
                  foreground="#888").pack(anchor="w")
        ttk.Label(body, text=tab._t("tw_qc_reorg_tip"),
                  foreground="#888",
                  font=(FONT_FAMILY, 9, "italic")).pack(anchor="w")

        self.pairs_text = tk.Text(
            body, height=12, width=70, wrap="none",
            bg="#0a0a1a", fg="#e5e7eb",
            insertbackground="#fff",
            font=(FONT_FAMILY, 10),
            relief="flat", highlightthickness=1,
            highlightbackground="#2a2a4a",
        )
        self.pairs_text.pack(fill="both", expand=True, pady=(4, 0))

        # Inline guidance
        sample = ("# Examples — paste 2 cols from Excel, or type:\n"
                  "fr_FR\tAgent IA\n"
                  "de_DE\tKI-Agent\n"
                  "zh_CN\tAI 智能体\n")
        self.pairs_text.insert("1.0", sample)

        # Scope is picked downstream via the unified _ScopeDialog so all
        # three entry points share the same project-list / Task ID UX.

        # Buttons
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(12, 0))
        tab.app._create_button(
            btns, text=tab._t("tw_export_btn_cancel"),
            command=self._cancel,
            style_name="Secondary",
            bg="#1f2a48", fg="#fff", padx=10, pady=4,
        ).pack(side="right")
        tab.app._create_button(
            btns, text=tab._t("tw_qc_run"), command=self._ok,
            style_name="Accent",
            bg=getattr(tab.app, "ACCENT_BTN", "#e94560"), fg="#fff",
            padx=12, pady=4,
        ).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda *_: self._cancel())
        self.ent_term.focus_set()
        self.grab_set()
        self.wait_window(self)

    def _clear_pairs(self):
        """Empty the pairs textarea outright. No confirm dialog — undo
        is one paste away and the action is unambiguous."""
        self.pairs_text.delete("1.0", "end")
        self.pairs_text.focus_set()

    def _reorg_paste(self):
        text = self.pairs_text.get("1.0", "end")
        # Drop the example/comment lines automatically before reorg
        text = "\n".join(
            ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
        )
        reorged = tw.reorganize_paste(text)
        if not reorged.strip():
            messagebox.showinfo(self.tab._t("tw_qc_reorg"),
                                self.tab._t("tw_qc_reorg_failed"),
                                parent=self)
            return
        # Sanity: if reorg produced no tab/separator lines and the input also
        # had none, surface a hint instead of silently keeping garbage.
        if "\t" not in reorged and "→" not in reorged and "->" not in reorged:
            messagebox.showinfo(self.tab._t("tw_qc_reorg"),
                                self.tab._t("tw_qc_reorg_failed"),
                                parent=self)
            return
        self.pairs_text.delete("1.0", "end")
        self.pairs_text.insert("1.0", reorged)

    def _paste_clipboard(self):
        try:
            data = self.clipboard_get()
        except Exception:
            return
        if not data:
            return
        # Replace any "# Examples" header block; otherwise append
        current = self.pairs_text.get("1.0", "end").strip()
        if current.startswith("# Examples"):
            self.pairs_text.delete("1.0", "end")
            self.pairs_text.insert("1.0", data)
        else:
            self.pairs_text.insert("end", "\n" + data)

    def _ok(self):
        term = self.term_var.get().strip()
        if not term:
            messagebox.showinfo(self.tab._t("tw_btn_quick_check"),
                                self.tab._t("tw_qc_source"), parent=self)
            return
        text = self.pairs_text.get("1.0", "end")
        # Drop the example/comment lines automatically
        text = "\n".join(
            ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
        )
        pairs = tw.parse_clipboard_pairs(text)
        if not pairs:
            messagebox.showinfo(self.tab._t("tw_btn_quick_check"),
                                self.tab._t("tw_qc_no_pairs"), parent=self)
            return
        # Scope is picked downstream via _ScopeDialog (shared with the
        # other two entry points), so this dialog only collects term-level
        # input.
        self.result = (term, self.sev_var.get(), pairs)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()
