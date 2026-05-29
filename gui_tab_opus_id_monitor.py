"""
OPUS ID Monitor — GUI Tab
=========================
"随时随地看 OPUS ID 总量 / 新增 / 按项目分布"的面板。

数据全部来自本地 SQLite 缓存（``opus_id_monitor`` 模块），首屏不依赖网络。
用户点击 "🔄 Sync" 才会向 Tranzor 拉增量；首次或选择性触发可做全量。

布局：
    顶部状态条：Last sync · Sync 按钮 · Mode 切换（incremental / full）
    summary 卡：4 张大数字（总 opus / 文件指纹 / 项目 / 今日新增）
    左：按项目分桶表（可点击列头排序）
    右：30 天每日新增（简易 Canvas 柱状图）+ 最近新增 opus_id 列表
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk
from datetime import datetime


# ---------------------------------------------------------------------------
# i18n —— **必须在 ``from export_gui import …`` 之前定义**！
#
# export_gui 在它的"optional tab import"段里会反向 import 我们这个模块
# 来读 STRINGS 做合并。如果我们把 STRINGS 放在 from-import 之后，
# 第一次合并时拿到的 module 还是 partial 状态、STRINGS 还没绑定，
# merge 在 ``except Exception: pass`` 里被静默吞掉，i18n 永远不进
# export_gui.STRINGS —— 用户看到的就是 button.text 是 i18n key 本身
# 而不是翻译。把 STRINGS 提到最顶部就能让这条路径恒等于成功。
# ---------------------------------------------------------------------------
STRINGS = {
    "en": {
        "tab_opus_monitor":         "🧬 OPUS ID Monitor",
        "opus_sync_now":            "🔄 Sync now",
        "opus_sync_full":           "Full re-sync",
        "opus_sync_cancel":         "Cancel",
        "opus_sync_now_tip":        (
            "Pull only tasks created since the last sync.\n"
            "Fast (seconds) — use this for routine refreshes.\n\n"
            "New here? Click 'Full re-sync' first to build the baseline."),
        "opus_sync_full_tip":       (
            "Re-pull ALL completed MR / Scan / File-Translation tasks\n"
            "and rebuild the local cache from scratch.\n\n"
            "Required on first run (~5-10 min depending on backend load).\n"
            "Re-run later only if you suspect cache drift."),
        "opus_sync_cancel_tip":     (
            "Abort an in-flight sync. Already-saved data stays intact;\n"
            "next sync will resume from where this one stopped."),
        "opus_last_sync":           "{elapsed} since last sync · {time}",
        "opus_last_sync_never":     "Never synced — click 'Full re-sync' first to build the baseline.",
        "opus_elapsed_just_now":    "just now",
        "opus_elapsed_minutes":     "{m}m",
        "opus_elapsed_hours":       "{h}h {m}m",
        "opus_elapsed_days":        "{d}d {h}h {m}m",
        "opus_card_total":          "OPUS IDs",
        "opus_card_files":          "Source files (path hashes)",
        "opus_card_projects":       "Projects",
        "opus_card_new_today":      "New today",
        "opus_card_new_7d":         "+{n} last 7d",
        "opus_card_new_30d":        "+{n} last 30d",
        "opus_card_rows":           "{n:,} rows total",
        "opus_breakdown_title":     "📊 Breakdown by project · double-click to drill down",
        "opus_col_project":         "Project (source)",
        "opus_col_alias":           "Alias",
        "opus_col_opus":            "OPUS IDs",
        "opus_col_files":           "Files",
        "opus_col_langs":           "Langs",
        "opus_col_last_added":      "Last added",
        "opus_trend_title":         "📈 New OPUS IDs · last 30 days",
        "opus_recent_title":        "📋 Recently added · double-click row",
        "opus_recent_col_time":     "First seen",
        "opus_recent_col_project":  "Project (source)",
        "opus_recent_col_alias":    "Alias",
        "opus_recent_col_opus":     "OPUS ID",
        "opus_status_idle":         "Idle.",
        "opus_status_loading":      "Loading from local cache…",
        "opus_status_syncing":      "Syncing… {stage} {cur}/{total}",
        "opus_status_done":         "✓ Sync done · MR +{mr} · Scan +{scan} · File +{legacy} rows",
        "opus_status_failed":       "❌ {error}",
        "opus_status_cancelled":    "⚠ Sync cancelled",
        # Drill-down dialogs
        "opus_dlg_project_title":   "Project · {project} ({source})",
        "opus_dlg_project_summary": (
            "OPUS IDs: {opus}  ·  Files: {files}  ·  Langs: {langs}  ·  "
            "Rows: {rows}\nFirst seen: {first}   Last added: {last}"),
        "opus_dlg_files_title":     "Source files in this project (top {n} by OPUS ID count)",
        "opus_dlg_files_col_pathhash":  "Path hash",
        "opus_dlg_files_col_opus":      "OPUS IDs",
        "opus_dlg_files_col_langs":     "Langs",
        "opus_dlg_files_col_last":      "Last added",
        "opus_dlg_files_col_samples":   "Sample keys",
        "opus_dlg_opus_title":      "OPUS ID detail",
        "opus_dlg_opus_full":       "Full OPUS ID",
        "opus_dlg_opus_alias":      "Alias (segment 2)",
        "opus_dlg_opus_pathhash":   "Path hash (segment 3 · md5 of source file path)",
        "opus_dlg_opus_logkey":     "Logical key (segment 4 · string-level key)",
        "opus_dlg_opus_project":    "Project",
        "opus_dlg_opus_source":     "Source pipeline",
        "opus_dlg_opus_mr":         "MR / task",
        "opus_dlg_opus_first":      "First seen by Tranzor",
        "opus_dlg_opus_first_local":"First seen by this tool",
        "opus_dlg_opus_text":       "Source text",
        "opus_dlg_opus_langs":      "Target languages ({n})",
        "opus_dlg_close":           "Close",
        "opus_dlg_copy":            "Copy",
        "opus_src_mr":              "MR",
        "opus_src_scan":            "Scan",
        "opus_src_file":            "File",
        # Breakdown search
        "opus_breakdown_search_ph": "🔍 Filter by project / alias…",
        "opus_breakdown_search_clear": "✕",
        "opus_breakdown_filter_hint": "{shown}/{total} rows shown",
        "opus_breakdown_source_label": "Source:",
        "opus_breakdown_source_any":   "(any)",
        # Sync delta dialog (post-sync new-OPUS-ID popup)
        "opus_delta_title":           "Sync result · {n} new OPUS ID(s)",
        "opus_delta_header":          (
            "Added since last sync ({since}). Showing {shown} of {total} new OPUS IDs."),
        "opus_delta_header_first":    (
            "First-time sync — every row is new. Showing {shown} of {total} OPUS IDs."),
        "opus_delta_col_time":        "First seen",
        "opus_delta_col_source":      "Source",
        "opus_delta_col_project":     "Project",
        "opus_delta_col_alias":       "Alias",
        "opus_delta_col_opus":        "OPUS ID",
        "opus_delta_close":           "Close",
        "opus_delta_empty":           "No new OPUS IDs since last sync.",
        "opus_delta_copy_all":        "📋 Copy all OPUS IDs",
        "opus_delta_copy_tip":        (
            "Copy every new OPUS ID to the clipboard, one per line.\n"
            "Includes all {n} entries — not limited by the 5,000-row display cap.\n"
            "Shortcut: Ctrl+C while this dialog is focused."),
        "opus_delta_copied_n":        "✓ Copied {n} OPUS IDs",
        # OpusDetailDialog new fields
        "opus_dlg_opus_path":       "Source file path (debug-friendly plaintext of path hash)",
        "opus_dlg_opus_path_missing": "— (not synced; re-run Full re-sync to populate)",
        "opus_dlg_opus_translations": "Translations ({n} languages)",
        "opus_dlg_opus_trans_col_lang": "Language",
        "opus_dlg_opus_trans_col_text": "Translated text",
        "opus_dlg_opus_trans_empty": "(empty — not yet synced or no translation present)",
        # Recently-added 7d window
        "opus_recent_title_n":      "📋 Recently added in 7 days ({n}) · double-click row",
        # Send-to-Tranzor: actionable warnings when userscript not live
        "opus_dlg_send_no_userscript": (
            "⚠ Pushed to bridge, but no Tranzor browser tab picked it up. "
            "Make sure Tranzor is open in your browser and the Tampermonkey "
            "userscript is installed."),
        "opus_dlg_send_setup_wizard": "Run setup wizard…",
        "opus_dlg_send_pending": "⏳ Pushed · waiting for Tranzor browser tab to pull…",
        # Send-to-Tranzor button on OpusDetailDialog
        "opus_dlg_send":            "↗ Send to Tranzor",
        "opus_dlg_send_ok":         "✓ Sent to Tranzor browser tab",
        "opus_dlg_send_no_bridge":  "⚠ Bridge not running · envelope JSON copied to clipboard",
        "opus_dlg_send_failed":     "❌ Send failed: {error}",
        "opus_dlg_copied":          "✓ Copied",
        # v4: per-source-kind missing-path messages
        "opus_dlg_opus_path_mr_missing": (
            "— MR Pipeline /results API does not expose this field. "
            "Use the Path-hash lookup tool to reverse-resolve."),
        "opus_dlg_opus_path_legacy_missing": (
            "— File Translation API does not expose this field."),
        # v4: Recent search box
        "opus_recent_search_ph":    "🔍 Filter OPUS ID / project / alias…",
        "opus_recent_filter_hint":  "{shown}/{total}",
        # v4: File detail dialog
        "opus_dlg_file_title":      "File · {label}",
        "opus_dlg_file_summary":    (
            "OPUS IDs: {opus}  ·  Langs: {langs}  ·  Rows: {rows}\n"
            "Source file path: {path}\n"
            "First seen: {first}   Last added: {last}"),
        "opus_dlg_file_opus_title": "OPUS IDs in this file (top {n})",
        "opus_dlg_file_col_logkey": "Logical key",
        "opus_dlg_file_col_langs":  "Langs",
        "opus_dlg_file_col_source": "Source text",
        "opus_dlg_file_col_added":  "Last added",
        # v4: ProjectDetailDialog source-file-path column
        "opus_dlg_files_col_path":  "Source file path",
        # v4: Path-hash lookup tool
        "opus_path_lookup_btn":     "🔎 Path-hash lookup",
        "opus_path_lookup_btn_tip": (
            "Reverse-resolve a path_hash (or compute the path_hash for a "
            "relative file path) against the local cache.\n\n"
            "Especially useful for MR-source opus_ids whose source_file_path "
            "is not exposed by the Tranzor API — paste a candidate path you "
            "suspect, and see which opus_ids belong to it."),
        "opus_path_lookup_title":   "Path-hash reverse-lookup",
        "opus_path_lookup_intro":   (
            "Paste a 32-hex path_hash to see which projects use it,\n"
            "or paste a relative file path to compute its hash and search."),
        "opus_path_lookup_input":   "Path or 32-hex hash:",
        "opus_path_lookup_go":      "Look up",
        "opus_path_lookup_no_match": "No matches in local cache.",
        "opus_path_lookup_computed": "Computed path_hash: {hash}",
        "opus_path_lookup_used_hash": "Using path_hash: {hash}",
        "opus_path_lookup_col_proj":   "Project",
        "opus_path_lookup_col_alias":  "Alias",
        "opus_path_lookup_col_source": "Source",
        "opus_path_lookup_col_opus":   "OPUS IDs",
        "opus_path_lookup_col_langs":  "Langs",
        "opus_path_lookup_col_path":   "Known source path",
        # v5: backfill button + missing-path cell hint
        "opus_backfill_btn":        "📥 Backfill paths",
        "opus_backfill_btn_tip":    (
            "Cross-source path-backfill: Tranzor's MR / Legacy APIs do NOT\n"
            "expose source_file_path — only the Scan API does. But path_hash\n"
            "is md5(path), which is source-independent. So this tool copies\n"
            "Scan-side path knowledge to MR / Legacy rows that share the\n"
            "same path_hash.\n\n"
            "Runs automatically at the end of every sync; manual button is\n"
            "for instantly applying it to existing caches without re-syncing."),
        "opus_backfill_done":       "✓ Backfill done · {filled} hashes filled · {rows} rows updated",
        "opus_backfill_running":    "⏳ Backfilling paths…",
        "opus_path_cell_empty":     "—",
        "opus_path_cell_unknown":   "— (no Scan saw this file)",
    },
    "zh": {
        "tab_opus_monitor":         "🧬 OPUS ID 监控",
        "opus_sync_now":            "🔄 立即同步",
        "opus_sync_full":           "全量重建",
        "opus_sync_cancel":         "取消",
        "opus_sync_now_tip":        (
            "仅拉取「上次同步之后」新创建的任务，秒级完成。\n"
            "适合日常刷新。\n\n"
            "首次使用请先点「全量重建」建立基线。"),
        "opus_sync_full_tip":       (
            "重新拉取所有已完成的 MR / Scan / File Translation 任务，\n"
            "从零重建本地缓存。\n\n"
            "首次使用必须执行（约 5-10 分钟，取决于后端负载）。\n"
            "怀疑缓存与真实状态有差异时也可再次执行。"),
        "opus_sync_cancel_tip":     (
            "中止正在进行的同步。已落库的数据不会丢失；\n"
            "下次同步会从中断处继续。"),
        "opus_last_sync":           "距上次同步 {elapsed} · {time}",
        "opus_last_sync_never":     "尚未同步 — 请先点「全量重建」建立基线。",
        "opus_elapsed_just_now":    "刚刚",
        "opus_elapsed_minutes":     "{m} 分钟",
        "opus_elapsed_hours":       "{h} 小时 {m} 分钟",
        "opus_elapsed_days":        "{d} 天 {h} 小时 {m} 分钟",
        "opus_card_total":          "OPUS ID 总数",
        "opus_card_files":          "源文件数（路径指纹）",
        "opus_card_projects":       "项目数",
        "opus_card_new_today":      "今日新增",
        "opus_card_new_7d":         "近 7 天 +{n}",
        "opus_card_new_30d":        "近 30 天 +{n}",
        "opus_card_rows":           "共 {n:,} 条记录",
        "opus_breakdown_title":     "📊 按项目分桶 · 双击钻取",
        "opus_col_project":         "项目（源头）",
        "opus_col_alias":           "Alias",
        "opus_col_opus":            "OPUS ID",
        "opus_col_files":           "源文件",
        "opus_col_langs":           "语言数",
        "opus_col_last_added":      "最近新增",
        "opus_trend_title":         "📈 近 30 天每日新增",
        "opus_recent_title":        "📋 最近新增 · 双击查看",
        "opus_recent_col_time":     "首次出现",
        "opus_recent_col_project":  "项目（源头）",
        "opus_recent_col_alias":    "Alias",
        "opus_recent_col_opus":     "OPUS ID",
        "opus_status_idle":         "空闲。",
        "opus_status_loading":      "正在从本地缓存加载…",
        "opus_status_syncing":      "正在同步… {stage} {cur}/{total}",
        "opus_status_done":         "✓ 同步完成 · MR +{mr} · Scan +{scan} · File +{legacy} 行",
        "opus_status_failed":       "❌ {error}",
        "opus_status_cancelled":    "⚠ 同步已取消",
        # 钻取对话框
        "opus_dlg_project_title":   "项目 · {project} ({source})",
        "opus_dlg_project_summary": (
            "OPUS ID：{opus}  ·  源文件：{files}  ·  语言：{langs}  ·  "
            "总行：{rows}\n首次出现：{first}   最近新增：{last}"),
        "opus_dlg_files_title":     "本项目下的源文件（按 OPUS ID 数 Top {n}）",
        "opus_dlg_files_col_pathhash":  "Path hash",
        "opus_dlg_files_col_opus":      "OPUS ID",
        "opus_dlg_files_col_langs":     "语言",
        "opus_dlg_files_col_last":      "最近新增",
        "opus_dlg_files_col_samples":   "样本 key",
        "opus_dlg_opus_title":      "OPUS ID 详情",
        "opus_dlg_opus_full":       "完整 OPUS ID",
        "opus_dlg_opus_alias":      "Alias（第 2 段）",
        "opus_dlg_opus_pathhash":   "Path hash（第 3 段 · 源文件路径的 md5）",
        "opus_dlg_opus_logkey":     "Logical key（第 4 段 · 字符串级 key）",
        "opus_dlg_opus_project":    "项目",
        "opus_dlg_opus_source":     "源头管线",
        "opus_dlg_opus_mr":         "MR / 任务",
        "opus_dlg_opus_first":      "Tranzor 首次出现",
        "opus_dlg_opus_first_local":"本地缓存首次记录",
        "opus_dlg_opus_text":       "源文本",
        "opus_dlg_opus_langs":      "目标语言（{n} 种）",
        "opus_dlg_close":           "关闭",
        "opus_dlg_copy":            "复制",
        "opus_src_mr":              "MR",
        "opus_src_scan":            "Scan",
        "opus_src_file":            "File",
        # Breakdown 搜索
        "opus_breakdown_search_ph": "🔍 按项目 / Alias 过滤…",
        "opus_breakdown_search_clear": "✕",
        "opus_breakdown_filter_hint": "显示 {shown}/{total} 行",
        "opus_breakdown_source_label": "来源:",
        "opus_breakdown_source_any":   "(全部)",
        # Sync 完成后的增量详情对话框
        "opus_delta_title":           "同步结果 · 本次新增 {n} 条 OPUS ID",
        "opus_delta_header":          (
            "上次同步（{since}）之后新增。共 {total} 条，展示其中 {shown} 条。"),
        "opus_delta_header_first":    (
            "首次同步 —— 全部 OPUS ID 都计为新增。共 {total} 条，展示其中 {shown} 条。"),
        "opus_delta_col_time":        "首次出现",
        "opus_delta_col_source":      "来源",
        "opus_delta_col_project":     "项目",
        "opus_delta_col_alias":       "Alias",
        "opus_delta_col_opus":        "OPUS ID",
        "opus_delta_close":           "关闭",
        "opus_delta_empty":           "本次同步无新增 OPUS ID。",
        "opus_delta_copy_all":        "📋 复制全部 OPUS ID",
        "opus_delta_copy_tip":        (
            "把本次新增的全部 OPUS ID 一次性复制到剪贴板，每行一条。\n"
            "包含全部 {n} 条 —— 不受 5,000 行渲染上限影响。\n"
            "快捷键：对话框获得焦点时按 Ctrl+C。"),
        "opus_delta_copied_n":        "✓ 已复制 {n} 条 OPUS ID",
        # OpusDetailDialog 新字段
        "opus_dlg_opus_path":       "源文件路径（path hash 的明文，debug 必看）",
        "opus_dlg_opus_path_missing": "— （未同步；点「全量重建」补齐）",
        "opus_dlg_opus_translations": "译文预览（{n} 种语言）",
        "opus_dlg_opus_trans_col_lang": "语言",
        "opus_dlg_opus_trans_col_text": "译文",
        "opus_dlg_opus_trans_empty": "（无 — 尚未同步或本无译文）",
        # 最近新增 7d 窗口
        "opus_recent_title_n":      "📋 近 7 天新增（{n} 条） · 双击查看详情",
        # Send-to-Tranzor: userscript 不活时的可操作提示
        "opus_dlg_send_no_userscript": (
            "⚠ 已推到本地 Bridge，但没有 Tranzor 浏览器 tab 来拉取。\n"
            "请确认：① Tranzor Platform 已在浏览器打开；② Tampermonkey "
            "userscript 已安装并启用。"),
        "opus_dlg_send_setup_wizard": "启动配置向导…",
        "opus_dlg_send_pending": "⏳ 已推送 · 等待 Tranzor 浏览器 tab 拉取…",
        # 详情对话框 Send-to-Tranzor 按钮
        "opus_dlg_send":            "↗ 发送到 Tranzor",
        "opus_dlg_send_ok":         "✓ 已发送到 Tranzor 浏览器 tab",
        "opus_dlg_send_no_bridge":  "⚠ Bridge 未运行 · envelope JSON 已复制到剪贴板",
        "opus_dlg_send_failed":     "❌ 发送失败：{error}",
        "opus_dlg_copied":          "✓ 已复制",
        # v4: 按源头管线分别给出"为什么是空"的诚实解释
        "opus_dlg_opus_path_mr_missing": (
            "— MR Pipeline 的 /results API 不暴露此字段。\n"
            "可用「Path-hash 反查工具」从已知路径反推。"),
        "opus_dlg_opus_path_legacy_missing": (
            "— File Translation API 不暴露此字段。"),
        # v4: 最近新增搜索框
        "opus_recent_search_ph":    "🔍 按 OPUS ID / 项目 / Alias 过滤…",
        "opus_recent_filter_hint":  "{shown}/{total}",
        # v4: 文件详情对话框
        "opus_dlg_file_title":      "源文件 · {label}",
        "opus_dlg_file_summary":    (
            "OPUS ID：{opus}  ·  语言：{langs}  ·  总行：{rows}\n"
            "源文件路径：{path}\n"
            "首次出现：{first}   最近新增：{last}"),
        "opus_dlg_file_opus_title": "本文件下的 OPUS ID（Top {n}）",
        "opus_dlg_file_col_logkey": "Logical key",
        "opus_dlg_file_col_langs":  "语言",
        "opus_dlg_file_col_source": "源文本",
        "opus_dlg_file_col_added":  "最近新增",
        # v4: ProjectDetailDialog 加源文件路径列
        "opus_dlg_files_col_path":  "源文件路径",
        # v4: Path-hash 反查工具
        "opus_path_lookup_btn":     "🔎 Path-hash 反查",
        "opus_path_lookup_btn_tip": (
            "反查 path_hash → 项目 / opus_id；或者给一个相对路径\n"
            "算出 hash 并在本地缓存里查。\n\n"
            "对 MR 来源的 opus_id 特别有用：因为 Tranzor API 不返回\n"
            "source_file_path，这是唯一能把 hash 还原为可读路径的途径。"),
        "opus_path_lookup_title":   "Path-hash 反查工具",
        "opus_path_lookup_intro":   (
            "粘贴 32 位 hex 的 path_hash → 查哪些项目用它；\n"
            "或粘贴相对路径 → 算出 hash 再查。"),
        "opus_path_lookup_input":   "路径或 32 位 hex hash：",
        "opus_path_lookup_go":      "查询",
        "opus_path_lookup_no_match": "本地缓存中无匹配。",
        "opus_path_lookup_computed": "算出的 path_hash：{hash}",
        "opus_path_lookup_used_hash": "使用 path_hash：{hash}",
        "opus_path_lookup_col_proj":   "项目",
        "opus_path_lookup_col_alias":  "Alias",
        "opus_path_lookup_col_source": "源头",
        "opus_path_lookup_col_opus":   "OPUS ID",
        "opus_path_lookup_col_langs":  "语言",
        "opus_path_lookup_col_path":   "已知源路径",
        # v5: 跨源回填按钮 + 路径缺失提示文案
        "opus_backfill_btn":        "📥 回填路径",
        "opus_backfill_btn_tip":    (
            "跨源回填源文件路径：Tranzor 的 MR / Legacy API 不暴露\n"
            "source_file_path，只有 Scan API 暴露。但 path_hash = md5(path)\n"
            "本身和『哪条管线产出』无关 —— 把 Scan 侧已知的『hash→path』\n"
            "映射，复制到所有同 path_hash 的 MR / Legacy 行上。\n\n"
            "每次同步结束自动跑一次；手动按钮用于在不重新同步的情况下\n"
            "立刻对已有缓存执行一次回填。"),
        "opus_backfill_done":       "✓ 回填完成 · 填充 {filled} 个 hash · 更新 {rows} 行",
        "opus_backfill_running":    "⏳ 正在回填路径…",
        "opus_path_cell_empty":     "—",
        "opus_path_cell_unknown":   "—（没有 Scan 任务见过这个文件）",
    },
}


# ---------------------------------------------------------------------------
# 本地 import —— 放在 STRINGS 之后；因为 export_gui 加载时会反向 import
# 我们，必须先让 STRINGS 在我们的 namespace 里存在，再触发这些 import。
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opus_id_monitor as om
# 同理，``Tooltip`` 类在 export_gui 内部的定义位置比"optional tab import"段
# 还要靠后；想用它必须懒加载（见 _build() 里的 ``from export_gui import
# Tooltip``）。这里只拿稳定在文件顶部的常量。
from export_gui import FONT_FAMILY, FONT_MONO, IS_MAC


def _fmt_iso_short(iso_str: str | None) -> str:
    """ISO 时间字符串 → '05-25 14:32' 这样人类可读的紧凑形式。"""
    if not iso_str:
        return "—"
    try:
        # 兼容带时区和不带时区两种
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return iso_str[:16]


def _humanize_elapsed(iso_str: str | None, t) -> str:
    """ISO 时间 → '2 days 4 hours 17 minutes' 这样的"逝去时长"文案。

    ``t`` 是 i18n 取词函数（self._t），决定走中文还是英文模板。
    返回三种粒度：
      - < 1 分钟 → 'just now' / '刚刚'
      - < 1 小时 → '37m' / '37 分钟'
      - < 1 天   → '2h 15m' / '2 小时 15 分钟'
      - 其他     → '3d 4h 17m' / '3 天 4 小时 17 分钟'
    粒度卡到分钟够用了；秒级抖动反而吵眼睛。
    """
    if not iso_str:
        return "—"
    try:
        from datetime import timezone as _tz
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        now = datetime.now(_tz.utc)
        delta = now - dt
        total_minutes = int(delta.total_seconds() // 60)
    except Exception:
        return "—"

    if total_minutes < 1:
        return t("opus_elapsed_just_now")
    if total_minutes < 60:
        return t("opus_elapsed_minutes").format(m=total_minutes)
    if total_minutes < 60 * 24:
        return t("opus_elapsed_hours").format(
            h=total_minutes // 60, m=total_minutes % 60)
    days = total_minutes // (60 * 24)
    rem = total_minutes % (60 * 24)
    return t("opus_elapsed_days").format(
        d=days, h=rem // 60, m=rem % 60)


def _source_label(source_kind: str, t) -> str:
    """'mr' → 'MR'；'scan' → 'Scan'；'file' → 'File'。i18n 友好。"""
    key = {"mr": "opus_src_mr", "scan": "opus_src_scan",
           "file": "opus_src_file"}.get((source_kind or "").lower())
    return t(key) if key else (source_kind or "?")


def _project_label(project_id: str, source_kind: str, t) -> str:
    """渲染成 'web/web (MR)' 形态。空 source 时回退为纯 project_id。"""
    src = _source_label(source_kind, t)
    if not project_id:
        return f"— ({src})" if src else "—"
    return f"{project_id} ({src})" if src else project_id


def _source_tag(source_kind: str) -> str:
    """source_kind → ttk.Treeview row tag name。给行加色靠这个映射。"""
    sk = (source_kind or "").lower()
    if sk == "mr":
        return "src_mr"
    if sk == "scan":
        return "src_scan"
    if sk == "file":
        return "src_file"
    return "src_unknown"


class OpusIdMonitorTab:
    """OPUS ID 监控面板。"""

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self._sync_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._first_shown = False
        self._build(parent)
        # PR-M: 首屏渲染延迟到 tab 首次可见。
        # 实测：_refresh_from_cache 渲染整个 breakdown/recent treeview，
        # 即便本 tab 不可见也会在启动后执行，单独占用 ~11s 的首次绘制
        # 时间（startup.log: first_idle +18.9s 的主因）。改由
        # ExportApp._on_tab_changed 在用户首次切到本 tab 时调
        # on_first_show()，启动完全不为它付费。

    def on_first_show(self):
        """ExportApp 在用户首次切到 OPUS tab 时调一次 —— 延迟首屏渲染。
        幂等：重复调用只渲染一次。

        查询放后台线程（大缓存下 SQLite 聚合 ~20s），渲染 marshal 回主
        线程，避免切到本 tab 时整个 GUI 冻结。先显示"加载中"给即时反馈。"""
        if self._first_shown:
            return
        self._first_shown = True
        self._refresh_source_combo()
        try:
            self.lbl_status.configure(text=self._t("opus_status_loading"))
        except Exception:
            pass

        def _work():
            data = self._query_cache_data()
            try:
                self.parent.after(0, lambda: self._apply_cache_data(data))
            except Exception:
                pass
        threading.Thread(
            target=_work, daemon=True, name="opus-first-show").start()

    def _t(self, key):
        return self.app._t(key)

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------
    def _build(self, parent):
        content = ttk.Frame(parent, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=16, pady=8)

        # ── Top bar: Sync controls + last-sync indicator ──
        topbar = ttk.Frame(content, style="App.TFrame")
        topbar.pack(fill="x", pady=(0, 8))

        self.btn_sync = self.app._create_button(
            topbar, text="", command=self._on_sync_incremental,
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#2ecc71", fg="#fff", padx=14, pady=4)
        self.btn_sync.pack(side="left")
        # 见文件头部注释：Tooltip 走懒加载避免循环 import。
        from export_gui import Tooltip
        # Hover infotip: tells first-time users what incremental sync does and
        # nudges them toward Full re-sync first (since incremental on an empty
        # cache silently does nothing — easy to mistake for "tool broken").
        self._tip_sync = Tooltip(self.btn_sync, text="")

        self.btn_sync_full = self.app._create_button(
            topbar, text="", command=self._on_sync_full,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        self.btn_sync_full.pack(side="left", padx=(8, 0))
        self._tip_sync_full = Tooltip(self.btn_sync_full, text="")

        self.btn_sync_cancel = self.app._create_button(
            topbar, text="", command=self._on_cancel,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4, state="disabled")
        self.btn_sync_cancel.pack(side="left", padx=(8, 0))
        self._tip_sync_cancel = Tooltip(self.btn_sync_cancel, text="")

        self.lbl_last_sync = ttk.Label(topbar, text="", style="Status.TLabel")
        self.lbl_last_sync.pack(side="left", padx=(16, 0))
        # Tick the "X minutes since" label every minute so it stays honest
        # even when the user is staring at it for a while.
        self._schedule_elapsed_tick()

        self.lbl_status = ttk.Label(topbar, text="", style="Status.TLabel")
        self.lbl_status.pack(side="right")

        # Path-hash 反查工具按钮 —— 给 MR 来源缺路径的兜底入口
        self.btn_path_lookup = self.app._create_button(
            topbar, text="", command=self._open_path_lookup,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        self.btn_path_lookup.pack(side="right", padx=(0, 8))
        from export_gui import Tooltip as _Tooltip
        self._tip_path_lookup = _Tooltip(self.btn_path_lookup, text="")

        # 跨源回填按钮：把 Scan 已知 path 同步到所有同 path_hash 的
        # MR/Legacy 行；同步时已自动跑，这是给"懒得重同步只想立刻补齐"
        # 的用户一条快速通路。
        self.btn_backfill = self.app._create_button(
            topbar, text="", command=self._on_backfill,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        self.btn_backfill.pack(side="right", padx=(0, 8))
        self._tip_backfill = _Tooltip(self.btn_backfill, text="")

        # ── Summary cards row ──
        cards_row = ttk.Frame(content, style="App.TFrame")
        cards_row.pack(fill="x", pady=(0, 10))

        self.card_total = _SummaryCard(cards_row, color="#4472C4")
        self.card_total.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.card_files = _SummaryCard(cards_row, color="#E67E22")
        self.card_files.pack(side="left", expand=True, fill="x", padx=6)
        self.card_projects = _SummaryCard(cards_row, color="#27AE60")
        self.card_projects.pack(side="left", expand=True, fill="x", padx=6)
        self.card_new = _SummaryCard(cards_row, color="#8E44AD")
        self.card_new.pack(side="left", expand=True, fill="x", padx=(6, 0))

        # 异常侦测一行已下线 (v0.6) —— 首次 Full re-sync 时 today=完整库存、
        # baseline=0，必然触发 critical 告警，用户每次都得无视它，纯噪音。
        # 趋势异常的语义已由"近 7 天 / 近 30 天"卡片副标题覆盖。

        # ── Main body: left = breakdown table, right = trend + recent ──
        body = ttk.Frame(content, style="App.TFrame")
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body, style="App.TFrame")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        right = ttk.Frame(body, style="App.TFrame", width=420)
        right.pack(side="right", fill="both", padx=(8, 0))
        right.pack_propagate(False)

        # Left: Per-project breakdown
        self.lbl_breakdown = ttk.Label(left, text="", style="CardBold.TLabel")
        self.lbl_breakdown.pack(anchor="w", pady=(0, 4))

        # 搜索栏 —— 输入即过滤，按项目名 / alias 子串忽略大小写匹配。
        # 项目多了之后必备：18+ 个项目时全屏滚动很烦躁。
        search_row = ttk.Frame(left, style="App.TFrame")
        search_row.pack(fill="x", pady=(0, 4))

        # Source 筛选下拉 —— 让用户只看某一类来源的项目。与 Tranzor Checks
        # 面板的 Source 下拉保持同一份枚举值 + 同款标签翻译，跨 tab 体感一致。
        self.lbl_bd_source = ttk.Label(
            search_row, text="", style="Status.TLabel")
        self.lbl_bd_source.pack(side="left")
        self.bd_source_var = tk.StringVar()
        self.cmb_bd_source = ttk.Combobox(
            search_row, textvariable=self.bd_source_var,
            width=8, state="readonly")
        self.cmb_bd_source.pack(side="left", padx=(4, 8))
        self.cmb_bd_source.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._render_breakdown())

        self.bd_search_var = tk.StringVar()
        self.ent_bd_search = tk.Entry(
            search_row, textvariable=self.bd_search_var,
            font=(FONT_FAMILY, 10),
            bg="#0a0a1a", fg="#fff", insertbackground="#fff",
            relief="flat")
        self.ent_bd_search.pack(side="left", fill="x", expand=True, ipady=4)
        self.bd_search_var.trace_add(
            "write", lambda *_: self._render_breakdown())
        # 占位文字 + 清空按钮
        self.btn_bd_search_clear = tk.Button(
            search_row, text="✕", command=lambda: self.bd_search_var.set(""),
            font=(FONT_FAMILY, 10), relief="flat",
            bg="#0f3460", fg="#ccc", padx=8, pady=0,
            activebackground="#1a3a6a", activeforeground="#fff",
            cursor="hand2")
        self.btn_bd_search_clear.pack(side="left", padx=(4, 0))
        self.lbl_bd_filter_hint = ttk.Label(
            search_row, text="", style="Status.TLabel")
        self.lbl_bd_filter_hint.pack(side="left", padx=(8, 0))
        # 自己实现 placeholder：StringVar 空时显示灰色提示
        self._bd_search_placeholder = ""

        bd_frame = ttk.Frame(left, style="App.TFrame")
        bd_frame.pack(fill="both", expand=True)
        cols = ("project", "alias", "opus", "files", "langs", "last_added")
        self.tree_breakdown = ttk.Treeview(
            bd_frame, columns=cols, show="headings",
            style="Summary.Treeview", selectmode="browse")
        widths = {"project": 240, "alias": 60, "opus": 80, "files": 70,
                  "langs": 60, "last_added": 110}
        for c in cols:
            anchor = "w" if c in ("project",) else "center"
            self.tree_breakdown.column(
                c, width=widths.get(c, 80), anchor=anchor)
            # 让列头点击触发排序
            self.tree_breakdown.heading(
                c, text="", command=lambda col=c: self._sort_breakdown(col))
        sb = ttk.Scrollbar(bd_frame, orient="vertical",
                           command=self.tree_breakdown.yview)
        self.tree_breakdown.configure(yscrollcommand=sb.set)
        self.tree_breakdown.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._breakdown_sort = ("opus", True)  # (col, desc)
        # Source-color tags —— 给行加浅底色，让用户一眼看清"这屏里 MR
        # 占比偏多 / Scan 突然多了一批"这种 pattern。配色和 Full
        # Translations tab 的源色保持同一语言（蓝=MR / 绿=Scan / 暖=File）。
        _SRC_BG = {"src_mr": "#1b2c44", "src_scan": "#1f3a2b",
                   "src_file": "#3a2e1f", "src_unknown": "#1a1a2e"}
        _SRC_FG = {"src_mr": "#cfe1ff", "src_scan": "#caf0d3",
                   "src_file": "#f0d9b8", "src_unknown": "#ccc"}
        for tag, bg in _SRC_BG.items():
            self.tree_breakdown.tag_configure(
                tag, background=bg, foreground=_SRC_FG[tag])

        # Right top: Trend chart
        self.lbl_trend = ttk.Label(right, text="", style="CardBold.TLabel")
        self.lbl_trend.pack(anchor="w", pady=(0, 4))
        self.canvas_trend = tk.Canvas(
            right, height=140, bg="#0a0a1a", highlightthickness=0)
        self.canvas_trend.pack(fill="x", pady=(0, 10))
        # 监听 resize 后重画，柱状图自适应宽度
        self.canvas_trend.bind("<Configure>", lambda _e: self._draw_trend())

        # Right bottom: Recent additions —— 7 天窗口、可滚动
        self.lbl_recent = ttk.Label(right, text="", style="CardBold.TLabel")
        self.lbl_recent.pack(anchor="w", pady=(0, 4))

        # 同款搜索框 —— 7 天可能有上千条，没搜索框找一个 opus_id 要翻页翻死
        rc_search = ttk.Frame(right, style="App.TFrame")
        rc_search.pack(fill="x", pady=(0, 4))
        self.rc_search_var = tk.StringVar()
        self.ent_rc_search = tk.Entry(
            rc_search, textvariable=self.rc_search_var,
            font=(FONT_FAMILY, 10),
            bg="#0a0a1a", fg="#fff", insertbackground="#fff",
            relief="flat")
        self.ent_rc_search.pack(side="left", fill="x", expand=True, ipady=3)
        self.rc_search_var.trace_add(
            "write", lambda *_: self._render_recent(self._recent_data))
        self.btn_rc_search_clear = tk.Button(
            rc_search, text="✕", command=lambda: self.rc_search_var.set(""),
            font=(FONT_FAMILY, 10), relief="flat",
            bg="#0f3460", fg="#ccc", padx=6, pady=0,
            activebackground="#1a3a6a", activeforeground="#fff",
            cursor="hand2")
        self.btn_rc_search_clear.pack(side="left", padx=(4, 0))
        self.lbl_rc_filter_hint = ttk.Label(
            rc_search, text="", style="Status.TLabel")
        self.lbl_rc_filter_hint.pack(side="left", padx=(6, 0))

        rc_frame = ttk.Frame(right, style="App.TFrame")
        rc_frame.pack(fill="both", expand=True)
        rcols = ("time", "project", "alias", "opus")
        # 把 height 从 10 调到 20，并 fill="both" expand=True 让它撑满
        # 右侧剩余空间。滚动条已存在，用户能拖到任何一条。
        self.tree_recent = ttk.Treeview(
            rc_frame, columns=rcols, show="headings",
            style="Summary.Treeview", selectmode="browse", height=20)
        rwidths = {"time": 75, "project": 95, "alias": 50, "opus": 180}
        for c in rcols:
            anchor = "w" if c in ("project", "opus") else "center"
            self.tree_recent.column(
                c, width=rwidths.get(c, 80), anchor=anchor)
            self.tree_recent.heading(c, text="")
        rsb = ttk.Scrollbar(rc_frame, orient="vertical",
                            command=self.tree_recent.yview)
        self.tree_recent.configure(yscrollcommand=rsb.set)
        self.tree_recent.pack(side="left", fill="both", expand=True)
        rsb.pack(side="right", fill="y")
        # Same source-color scheme on the recent list
        for tag, bg in _SRC_BG.items():
            self.tree_recent.tag_configure(
                tag, background=bg, foreground=_SRC_FG[tag])

        # 持有数据以便 sort 时直接重画，避免再查 DB
        self._trend_data: list[dict] = []
        self._breakdown_data: list[dict] = []
        self._recent_data: list[dict] = []
        # 记下 tree row → 业务键的映射，双击时拿来打开详情对话框
        self._breakdown_row_keys: dict[str, tuple[str, str]] = {}
        self._recent_row_keys: dict[str, str] = {}

        # 双击钻取
        self.tree_breakdown.bind("<Double-1>", self._on_breakdown_dbl)
        self.tree_recent.bind("<Double-1>", self._on_recent_dbl)

    # ------------------------------------------------------------------
    # i18n refresh
    # ------------------------------------------------------------------
    def refresh_text(self):
        t = self._t
        self.btn_sync.configure(text=t("opus_sync_now"))
        self.btn_sync_full.configure(text=t("opus_sync_full"))
        self.btn_sync_cancel.configure(text=t("opus_sync_cancel"))
        # Re-bind tooltip text in the current language
        self._tip_sync.set_text(t("opus_sync_now_tip"))
        self._tip_sync_full.set_text(t("opus_sync_full_tip"))
        self._tip_sync_cancel.set_text(t("opus_sync_cancel_tip"))
        self.btn_path_lookup.configure(text=t("opus_path_lookup_btn"))
        self._tip_path_lookup.set_text(t("opus_path_lookup_btn_tip"))
        self.btn_backfill.configure(text=t("opus_backfill_btn"))
        self._tip_backfill.set_text(t("opus_backfill_btn_tip"))
        self.lbl_breakdown.configure(text=t("opus_breakdown_title"))
        self.lbl_trend.configure(text=t("opus_trend_title"))
        self.lbl_recent.configure(text=t("opus_recent_title"))
        # Source 过滤下拉的标签 + 重建选项（label 跟着语言切换）
        if hasattr(self, "lbl_bd_source"):
            self.lbl_bd_source.configure(text=t("opus_breakdown_source_label"))
        self._refresh_source_combo()
        for c, key in (
            ("project", "opus_col_project"),
            ("alias", "opus_col_alias"),
            ("opus", "opus_col_opus"),
            ("files", "opus_col_files"),
            ("langs", "opus_col_langs"),
            ("last_added", "opus_col_last_added"),
        ):
            self.tree_breakdown.heading(
                c, text=t(key),
                command=lambda col=c: self._sort_breakdown(col))
        for c, key in (
            ("time", "opus_recent_col_time"),
            ("project", "opus_recent_col_project"),
            ("alias", "opus_recent_col_alias"),
            ("opus", "opus_recent_col_opus"),
        ):
            self.tree_recent.heading(c, text=t(key))
        # Card titles (set on the card widget itself)
        self.card_total.set_title(t("opus_card_total"))
        self.card_files.set_title(t("opus_card_files"))
        self.card_projects.set_title(t("opus_card_projects"))
        self.card_new.set_title(t("opus_card_new_today"))
        # Re-render dynamic labels in the new language
        self._refresh_from_cache()

    # ------------------------------------------------------------------
    # Source 筛选下拉：根据当前 i18n 重建选项 + 维护 label → source_kind 映射
    # ------------------------------------------------------------------
    def _refresh_source_combo(self):
        """重新生成 Source 下拉的 label 列表，保留用户当前选择。

        label 跟语言走（"文件" vs "File"），但 source_kind 本身是稳定的原值
        （"mr"/"scan"/"file"），过滤逻辑读 ``_bd_source_label_map`` 反查。
        """
        if not hasattr(self, "cmb_bd_source"):
            return
        t = self._t
        any_label = t("opus_breakdown_source_any")
        kinds = (("", any_label),
                 ("mr",   t("opus_src_mr")),
                 ("scan", t("opus_src_scan")),
                 ("file", t("opus_src_file")))
        labels = [lbl for _k, lbl in kinds]
        self._bd_source_label_map = {lbl: (k or None) for k, lbl in kinds}
        cur = self.bd_source_var.get()
        self.cmb_bd_source["values"] = labels
        # 当前选项还在新 label 列表里就保留，否则回退到 "全部"
        if cur not in labels:
            self.bd_source_var.set(any_label)

    # ------------------------------------------------------------------
    # 渲染：从本地 SQLite 拉数据填面板
    # ------------------------------------------------------------------
    def _query_cache_data(self):
        """4 个 SQLite 聚合查询 —— 大缓存（数十万 opus_id）上实测可达 ~20s，
        因此可被 :meth:`on_first_show` 放到后台线程跑。纯查询、不碰 Tk，
        线程安全。返回 dict；失败时返回 ``{"error": msg}``。"""
        try:
            return {
                "summary": om.get_summary(),
                "breakdown": om.get_per_project_breakdown(),
                "trend": om.get_daily_trend(days=30),
                # 7 天滚动窗口：用户要求"展示一周内的全部新增内容"
                "recent": om.get_recent_additions(days=7, hard_limit=1000),
            }
        except Exception as e:
            return {"error": str(e)}

    def _refresh_from_cache(self):
        """同步路径：查询 + 渲染。保留给 sync 完成 / 过滤变化等调用方
        （那些场景数据已热或用户有等待预期）。首次进入 tab 走的是
        :meth:`on_first_show` 的异步路径，避免冻结 UI。"""
        # 首次进入 tab 时 refresh_text 还没跑过，确保 Source 下拉至少有
        # "(全部)/MR/Scan/文件" 四个选项；之后每次 refresh_text 都会再调一次。
        self._refresh_source_combo()
        self._apply_cache_data(self._query_cache_data())

    def _apply_cache_data(self, data):
        """把 :meth:`_query_cache_data` 的结果渲染到卡片/表格/图。
        必须在主线程调用（碰 Tk widget）。"""
        if not data:
            return
        if data.get("error"):
            self.lbl_status.configure(
                text=self._t("opus_status_failed").format(
                    error=str(data["error"])[:60]))
            return
        summary = data["summary"]
        breakdown = data["breakdown"]
        trend = data["trend"]
        recent = data["recent"]

        # 卡片
        self.card_total.set_value(f"{summary['total_opus_ids']:,}")
        self.card_total.set_subtitle(
            self._t("opus_card_rows").format(n=summary["total_rows"]))
        self.card_files.set_value(f"{summary['total_path_hashes']:,}")
        self.card_files.set_subtitle(
            f"alias × {summary['total_aliases']}")
        self.card_projects.set_value(f"{summary['total_projects']:,}")
        self.card_projects.set_subtitle("")
        self.card_new.set_value(f"+{summary['new_today']:,}")
        self.card_new.set_subtitle(
            self._t("opus_card_new_7d").format(n=summary["new_7d"]) + " · "
            + self._t("opus_card_new_30d").format(n=summary["new_30d"]))

        # 上次同步时间 —— 显示 "X 天 Y 小时 Z 分钟前 · 05-25 15:58"
        # 这样用户既能感知时间流逝，也能精确看到具体什么时候同步的。
        self._last_sync_iso = summary.get("last_sync_at")
        self._render_last_sync_label()

        # 表格 + 图
        self._breakdown_data = breakdown
        self._render_breakdown()
        self._trend_data = trend
        self._draw_trend()
        self._render_recent(recent)

    def _render_breakdown(self):
        col, desc = self._breakdown_sort
        key = {
            "project": "project_id",
            "alias": "alias",
            "opus": "opus_count",
            "files": "path_count",
            "langs": "lang_count",
            "last_added": "last_added",
        }.get(col, "opus_count")
        # 1) 先应用搜索过滤 + Source 过滤
        q = (self.bd_search_var.get() or "").strip().lower()
        # source 下拉的 label 经 i18n 翻译，先把它映射回 source_kind 原值。
        src_label = (self.bd_source_var.get() or "").strip() \
            if hasattr(self, "bd_source_var") else ""
        src_kind = getattr(self, "_bd_source_label_map", {}).get(src_label)
        filtered = []
        for r in self._breakdown_data:
            if q and q not in (r.get("project_id") or "").lower() \
                    and q not in (r.get("alias") or "").lower():
                continue
            if src_kind and (r.get("source_kind") or "") != src_kind:
                continue
            filtered.append(r)
        # 2) 再排序
        rows = sorted(
            filtered,
            key=lambda r: (r.get(key) or 0) if key != "project_id"
                            and key != "alias" and key != "last_added"
                          else (r.get(key) or ""),
            reverse=desc,
        )
        # 3) 状态提示
        if hasattr(self, "lbl_bd_filter_hint"):
            self.lbl_bd_filter_hint.configure(
                text=self._t("opus_breakdown_filter_hint").format(
                    shown=len(rows), total=len(self._breakdown_data)))
        self.tree_breakdown.delete(*self.tree_breakdown.get_children())
        # 记下 iid → (project_id, source_kind)，双击展开时用得到。
        # 不再依赖列里渲染的"project_id (MR)"字符串去逆向解析（脆且歧义）。
        self._breakdown_row_keys.clear()
        t = self._t
        for r in rows:
            project = r.get("project_id", "")
            source = r.get("source_kind", "")
            label = _project_label(project, source, t)
            iid = self.tree_breakdown.insert("", "end", values=(
                label,
                r.get("alias", ""),
                f"{r.get('opus_count', 0):,}",
                f"{r.get('path_count', 0):,}",
                r.get("lang_count", 0),
                _fmt_iso_short(r.get("last_added", "")),
            ), tags=(_source_tag(source),))
            self._breakdown_row_keys[iid] = (project, source)

    def _sort_breakdown(self, col):
        cur_col, cur_desc = self._breakdown_sort
        if col == cur_col:
            self._breakdown_sort = (col, not cur_desc)
        else:
            # 默认数字列降序、文本列升序
            self._breakdown_sort = (
                col, col in ("opus", "files", "langs", "last_added"))
        self._render_breakdown()

    def _render_recent(self, recent: list[dict]):
        # _render_recent 既被首次填充也被搜索框 trace 回调调用，所以
        # 第一次传进来的 recent 是新数据要落到 self._recent_data；
        # 搜索时传进来的 recent 本来就是 self._recent_data 自身。
        if recent is not self._recent_data:
            self._recent_data = recent
        self.tree_recent.delete(*self.tree_recent.get_children())
        self._recent_row_keys.clear()
        t = self._t
        # 搜索过滤
        q = (self.rc_search_var.get() or "").strip().lower() \
            if hasattr(self, "rc_search_var") else ""
        if q:
            shown_rows = [
                r for r in self._recent_data
                if q in (r.get("opus_id") or "").lower()
                   or q in (r.get("project_id") or "").lower()
                   or q in (r.get("alias") or "").lower()
            ]
        else:
            shown_rows = self._recent_data
        # 标题动态显示当前条数（"近 7 天新增（234 条）"）
        self.lbl_recent.configure(
            text=t("opus_recent_title_n").format(n=len(self._recent_data)))
        if hasattr(self, "lbl_rc_filter_hint"):
            self.lbl_rc_filter_hint.configure(
                text=t("opus_recent_filter_hint").format(
                    shown=len(shown_rows), total=len(self._recent_data)))
        for r in shown_rows:
            opus = r.get("opus_id", "")
            # 中段太长不易看，做软截断；双击仍能拿到完整 opus_id
            disp = opus if len(opus) <= 60 else opus[:30] + "…" + opus[-25:]
            source = r.get("source_kind", "")
            iid = self.tree_recent.insert("", "end", values=(
                _fmt_iso_short(r.get("first_seen", "")),
                _project_label(r.get("project_id", ""), source, t),
                r.get("alias", ""),
                disp,
            ), tags=(_source_tag(source),))
            self._recent_row_keys[iid] = opus

    # ------------------------------------------------------------------
    # 异常侦测一行 —— 30 天日均 vs 今日，自动配色
    def _draw_trend(self):
        cv = self.canvas_trend
        cv.delete("all")
        if not self._trend_data:
            return
        w = cv.winfo_width() or 400
        h = cv.winfo_height() or 140
        pad_l, pad_r, pad_t, pad_b = 4, 4, 8, 18
        chart_w = max(40, w - pad_l - pad_r)
        chart_h = max(20, h - pad_t - pad_b)
        n = len(self._trend_data)
        max_v = max((d["new_count"] for d in self._trend_data), default=0) or 1
        bar_w = max(2, chart_w / n - 1)

        for i, d in enumerate(self._trend_data):
            v = d["new_count"]
            bh = (v / max_v) * chart_h if max_v else 0
            x0 = pad_l + i * (chart_w / n)
            x1 = x0 + bar_w
            y0 = pad_t + (chart_h - bh)
            y1 = pad_t + chart_h
            # 颜色用渐变：今天偏暖、越早越冷
            ratio = i / max(1, n - 1)
            color = "#%02x%02x%02x" % (
                int(60 + 195 * ratio),
                int(120 + 30 * ratio),
                int(220 - 40 * ratio),
            )
            cv.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
            if v > 0:
                cv.create_text(
                    (x0 + x1) / 2, y0 - 6,
                    text=str(v), fill="#ccc",
                    font=(FONT_FAMILY, 8))

        # 底部 x 轴：首日 / 中间 / 末日
        if n >= 2:
            for tick, label in (
                (0, self._trend_data[0]["date"][5:]),
                (n // 2, self._trend_data[n // 2]["date"][5:]),
                (n - 1, self._trend_data[-1]["date"][5:]),
            ):
                tx = pad_l + tick * (chart_w / n) + bar_w / 2
                cv.create_text(
                    tx, h - 6, text=label,
                    fill="#888", font=(FONT_FAMILY, 8))

    # ------------------------------------------------------------------
    # Sync — runs in background thread, UI updates via after()
    # ------------------------------------------------------------------
    def _on_sync_incremental(self):
        self._kickoff_sync(full=False)

    def _on_sync_full(self):
        self._kickoff_sync(full=True)

    def _on_cancel(self):
        self._cancel_event.set()

    # 同步期间的活体刷新节拍。3 秒一次：既能让用户看到卡片在涨，
    # 又不会因为反复查 SQLite 抢同步线程的 commit 时间。
    _LIVE_REFRESH_MS = 3000

    def _kickoff_sync(self, *, full: bool):
        if self._sync_thread and self._sync_thread.is_alive():
            return  # 不允许并发同步
        self._cancel_event.clear()
        self._set_sync_buttons(running=True)
        self.lbl_status.configure(
            text=self._t("opus_status_syncing").format(
                stage="init", cur=0, total=0))
        # 捕获本次 sync 的"新增基线" —— sync 完成后用它查 first_seen > 基线
        # 的所有 opus_id，弹窗展示。**必须在 sync 启动前**记录，否则 sync 自己
        # 写入的新行也会满足"> 新基线"条件，全部行都被当成"本次新增"。
        # 用 last_sync_at（上次 sync 落地时间戳）作为 cutoff；首次 sync 时为
        # None，data layer 会把它解释成"全部都算新增"。
        try:
            prev_summary = om.get_summary()
            self._sync_baseline_iso = prev_summary.get("last_sync_at")
        except Exception:
            self._sync_baseline_iso = None
        self._sync_thread = threading.Thread(
            target=self._run_sync, args=(full,), daemon=True)
        self._sync_thread.start()
        # 用户最大的痛点：同步期间卡片永远是 0，看上去像卡死。
        # 启动一个 after 链路，只要后台线程还活着，就每 3 秒把本地
        # SQLite 里已经落地的数据查一次刷上来。
        self.parent.after(
            self._LIVE_REFRESH_MS, self._tick_live_refresh)

    def _tick_live_refresh(self):
        """同步期间的定时刷新；线程死掉就自动停止。"""
        if not (self._sync_thread and self._sync_thread.is_alive()):
            return  # 同步线程已结束，最终刷新已由 _run_sync 触发
        try:
            self._refresh_from_cache()
        except Exception:
            pass  # 刷新失败不能影响同步本身
        self.parent.after(self._LIVE_REFRESH_MS, self._tick_live_refresh)

    # ------------------------------------------------------------------
    # "X minutes since last sync" 标签 —— 每分钟自刷
    # ------------------------------------------------------------------
    _ELAPSED_TICK_MS = 60_000  # 每分钟更新一次

    def _schedule_elapsed_tick(self):
        self.parent.after(self._ELAPSED_TICK_MS, self._tick_elapsed)

    def _tick_elapsed(self):
        try:
            self._render_last_sync_label()
        except Exception:
            pass
        self._schedule_elapsed_tick()

    def _render_last_sync_label(self):
        last = getattr(self, "_last_sync_iso", None)
        if not last:
            self.lbl_last_sync.configure(text=self._t("opus_last_sync_never"))
            return
        self.lbl_last_sync.configure(
            text=self._t("opus_last_sync").format(
                elapsed=_humanize_elapsed(last, self._t),
                time=_fmt_iso_short(last),
            ))

    # ------------------------------------------------------------------
    # 双击钻取 —— 弹出详情对话框
    # ------------------------------------------------------------------
    def _on_breakdown_dbl(self, _event=None):
        sel = self.tree_breakdown.selection()
        if not sel:
            return
        key = self._breakdown_row_keys.get(sel[0])
        if not key:
            return
        project_id, source_kind = key
        try:
            detail = om.get_project_detail(project_id, source_kind)
        except Exception as e:
            self.lbl_status.configure(
                text=self._t("opus_status_failed").format(error=str(e)[:60]))
            return
        ProjectDetailDialog(self.parent, self.app, detail)

    def _on_recent_dbl(self, _event=None):
        sel = self.tree_recent.selection()
        if not sel:
            return
        opus_id = self._recent_row_keys.get(sel[0])
        if not opus_id:
            return
        try:
            detail = om.get_opus_detail(opus_id)
        except Exception as e:
            self.lbl_status.configure(
                text=self._t("opus_status_failed").format(error=str(e)[:60]))
            return
        OpusDetailDialog(self.parent, self.app, detail)

    def _open_path_lookup(self, prefill: str = ""):
        """打开 path-hash 反查工具。可由 top-bar 按钮或 OpusDetailDialog
        里的"路径未知"行触发；prefill 用来预填一个 32 位 hash。"""
        PathHashLookupDialog(self.parent, self.app, prefill=prefill)

    def _on_backfill(self):
        """手动触发跨源路径回填 —— 不阻塞 UI（虽然典型耗时 <1s）。"""
        t = self._t
        self.lbl_status.configure(text=t("opus_backfill_running"))

        def _run():
            try:
                stats = om.backfill_missing_paths()
                msg = t("opus_backfill_done").format(
                    filled=stats.get("distinct_path_hashes_filled", 0),
                    rows=stats.get("rows_updated", 0))
                self.parent.after(0, lambda: self.lbl_status.configure(text=msg))
                # 回填完了刷新一下面板，让用户立刻看到效果
                self.parent.after(150, self._refresh_from_cache)
            except Exception as e:
                err = str(e)[:80]
                self.parent.after(0, lambda: self.lbl_status.configure(
                    text=t("opus_status_failed").format(error=err)))

        threading.Thread(target=_run, daemon=True).start()

    def _set_sync_buttons(self, *, running: bool):
        new_state = ["disabled"] if running else ["!disabled"]
        cancel_state = ["!disabled"] if running else ["disabled"]
        if IS_MAC:
            self.btn_sync.state(new_state)
            self.btn_sync_full.state(new_state)
            self.btn_sync_cancel.state(cancel_state)
        else:
            s = "disabled" if running else "normal"
            cs = "normal" if running else "disabled"
            self.btn_sync.configure(state=s)
            self.btn_sync_full.configure(state=s)
            self.btn_sync_cancel.configure(state=cs)

    def _run_sync(self, full: bool):
        try:
            def progress(stage, cur, total, **kw):
                # 后台线程只能用 after() 回主线程更新 UI
                self.parent.after(
                    0,
                    lambda s=stage, c=cur, tt=total: self.lbl_status.configure(
                        text=self._t("opus_status_syncing").format(
                            stage=s, cur=c, total=tt)))

            if full:
                result = om.sync_full(
                    progress_callback=progress,
                    cancel_event=self._cancel_event)
            else:
                result = om.sync_incremental(
                    progress_callback=progress,
                    cancel_event=self._cancel_event)

            if self._cancel_event.is_set():
                self.parent.after(0, lambda: self.lbl_status.configure(
                    text=self._t("opus_status_cancelled")))
            else:
                mr_rows = result.get("mr", {}).get("rows_inserted", 0)
                scan_rows = result.get("scan", {}).get("rows_inserted", 0)
                legacy_rows = result.get("legacy", {}).get("rows_inserted", 0)
                self.parent.after(
                    0, lambda: self.lbl_status.configure(
                        text=self._t("opus_status_done").format(
                            mr=mr_rows, scan=scan_rows, legacy=legacy_rows)))
                # 完成后弹增量详情对话框：查 first_seen > 同步前基线的
                # 全部 distinct opus_id。**只在用户主动取消之外的成功路径**
                # 触发，避免取消时还弹一个"看上去成功了"的窗口造成误解。
                baseline = getattr(self, "_sync_baseline_iso", None)
                self.parent.after(
                    150,
                    lambda b=baseline: self._show_sync_delta_dialog(b))
        except Exception as e:
            err = str(e)[:80]
            self.parent.after(0, lambda: self.lbl_status.configure(
                text=self._t("opus_status_failed").format(error=err)))
        finally:
            self.parent.after(0, lambda: self._set_sync_buttons(running=False))
            # 同步完了刷新一次面板
            self.parent.after(100, self._refresh_from_cache)

    def _show_sync_delta_dialog(self, baseline_iso: str | None):
        """弹模态对话框展示本次 sync 期间新落库的所有 opus_id。

        - 0 新增：不弹窗（status 行已显示"Sync done · 0 rows" 足够）
        - 1+ 新增：弹 ``SyncDeltaDialog``；超过 hard_limit 会在对话框头部
          注明"仅展示前 N 条"
        """
        try:
            additions = om.get_additions_since(baseline_iso)
        except Exception as e:
            # 数据查询失败不能影响主面板；status 行提示即可
            self.lbl_status.configure(
                text=self._t("opus_status_failed").format(error=str(e)[:60]))
            return
        if not additions:
            return  # 0 新增，不打扰用户
        SyncDeltaDialog(self.parent, self.app, additions, baseline_iso)


# ---------------------------------------------------------------------------
# 简易卡片组件 —— 大数字 + 标题 + 副标题
# ---------------------------------------------------------------------------
class _SummaryCard(tk.Frame):
    """风格化的指标卡，与现有 sidebar 风格保持一致。"""

    def __init__(self, master, *, color: str, **kw):
        super().__init__(master, bg="#1a1a2e",
                          highlightthickness=1,
                          highlightbackground=color, **kw)
        inner = tk.Frame(self, bg="#1a1a2e")
        inner.pack(fill="both", expand=True, padx=12, pady=10)

        self._title = tk.Label(
            inner, text="", bg="#1a1a2e", fg="#9aa0b0",
            font=(FONT_FAMILY, 9), anchor="w")
        self._title.pack(fill="x")

        self._value = tk.Label(
            inner, text="—", bg="#1a1a2e", fg=color,
            font=(FONT_FAMILY, 20, "bold"), anchor="w")
        self._value.pack(fill="x", pady=(2, 0))

        self._subtitle = tk.Label(
            inner, text="", bg="#1a1a2e", fg="#666",
            font=(FONT_FAMILY, 9), anchor="w")
        self._subtitle.pack(fill="x", pady=(2, 0))

    def set_title(self, text: str):
        self._title.configure(text=text)

    def set_value(self, text: str):
        self._value.configure(text=text)

    def set_subtitle(self, text: str):
        self._subtitle.configure(text=text)


# ---------------------------------------------------------------------------
# Sync 增量详情对话框 —— 点 "Sync now" 完成后自动弹，展示本次新增明细
# ---------------------------------------------------------------------------
class SyncDeltaDialog(tk.Toplevel):
    """同步完成后弹出的"本次新增 OPUS ID"列表。

    设计要点：
      - 非阻塞 Toplevel（用户可继续点主面板），点关闭即销毁
      - 渲染上限 = 5000 行；超出在头部告知"展示 5000 of N"，避免 Tk 卡死
      - 列与 OPUS Monitor 主面板的"最近新增"风格一致，降低视觉认知成本
    """

    _HARD_RENDER_LIMIT = 5000
    #: 复制反馈在按钮上停留多久后恢复原状（毫秒）。1.5s 够人眼读完
    #: "已复制 N 条"又不至于让人怀疑按钮卡住。
    _COPY_FEEDBACK_MS = 1500

    def __init__(self, parent, app, additions: list[dict],
                  baseline_iso: str | None):
        super().__init__(parent)
        self.app = app
        t = app._t
        total = len(additions)
        # 保留完整 opus_id 列表 —— 表格里展示的是软截断后的字符串，
        # 复制必须用原始完整值。同时只保留非空 id，避免脏数据把空行
        # 塞到剪贴板里。
        self._all_opus_ids = [
            (r.get("opus_id") or "").strip()
            for r in additions
            if (r.get("opus_id") or "").strip()
        ]
        self.title(t("opus_delta_title").format(n=total))
        self.configure(bg="#16213e")
        self.geometry("780x520")

        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=16, pady=12)

        # 头部一行说明：是否首次、共多少新增、当前渲染了多少
        shown = min(total, self._HARD_RENDER_LIMIT)
        if not baseline_iso:
            header = t("opus_delta_header_first").format(
                shown=f"{shown:,}", total=f"{total:,}")
        else:
            header = t("opus_delta_header").format(
                since=_fmt_iso_short(baseline_iso),
                shown=f"{shown:,}", total=f"{total:,}")
        tk.Label(outer, text=header, bg="#16213e", fg="#ccc",
                  font=(FONT_FAMILY, 10), justify="left",
                  anchor="w").pack(fill="x", pady=(0, 8))

        # 列表 —— 复用主面板的源色配色 / 列布局
        tbl_frame = ttk.Frame(outer, style="App.TFrame")
        tbl_frame.pack(fill="both", expand=True)
        cols = ("time", "source", "project", "alias", "opus")
        tree = ttk.Treeview(
            tbl_frame, columns=cols, show="headings",
            style="Summary.Treeview", selectmode="browse")
        widths = {"time": 110, "source": 60, "project": 200,
                  "alias": 90, "opus": 280}
        anchors = {"time": "center", "source": "center",
                    "project": "w", "alias": "center", "opus": "w"}
        for c in cols:
            tree.column(c, width=widths[c], anchor=anchors[c])
        tree.heading("time",    text=t("opus_delta_col_time"))
        tree.heading("source",  text=t("opus_delta_col_source"))
        tree.heading("project", text=t("opus_delta_col_project"))
        tree.heading("alias",   text=t("opus_delta_col_alias"))
        tree.heading("opus",    text=t("opus_delta_col_opus"))

        sb = ttk.Scrollbar(tbl_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # 源色配色 —— 与主面板保持视觉一致
        _SRC_BG = {"src_mr": "#1b2c44", "src_scan": "#1f3a2b",
                   "src_file": "#3a2e1f", "src_unknown": "#1a1a2e"}
        _SRC_FG = {"src_mr": "#cfe1ff", "src_scan": "#caf0d3",
                   "src_file": "#f0d9b8", "src_unknown": "#ccc"}
        for tag, bg in _SRC_BG.items():
            tree.tag_configure(tag, background=bg, foreground=_SRC_FG[tag])

        for r in additions[: self._HARD_RENDER_LIMIT]:
            opus = r.get("opus_id", "")
            # 长 opus_id 做软截断以免列宽爆掉
            opus_disp = opus if len(opus) <= 80 \
                else opus[:40] + "…" + opus[-30:]
            source = r.get("source_kind", "")
            tree.insert("", "end", values=(
                _fmt_iso_short(r.get("first_seen", "")),
                _source_label(source, t),
                r.get("project_id", "") or "—",
                r.get("alias", "") or "—",
                opus_disp,
            ), tags=(_source_tag(source),))

        # 底部按钮栏：左侧"复制全部"，右侧"关闭"
        btn_row = ttk.Frame(outer, style="App.TFrame")
        btn_row.pack(fill="x", pady=(10, 0))

        # 复制全部 OPUS ID —— 左侧，绿色调强调"主要的操作"
        # 注意：保存按钮原文案，复制反馈结束后用它恢复（不要在每次都
        # 重新读 i18n，否则用户切语言后会出现 1.5s 内显示错语言的尴尬）。
        self._copy_default_text = t("opus_delta_copy_all")
        self.btn_copy_all = tk.Button(
            btn_row, text=self._copy_default_text,
            command=self._copy_all_opus_ids,
            font=(FONT_FAMILY, 10),
            bg="#27AE60", fg="#fff",
            activebackground="#2ecc71", activeforeground="#fff",
            relief="flat", padx=18, pady=4, cursor="hand2")
        # 没有可复制的 id 时禁用按钮（其实 0 新增时弹窗根本不会出现，
        # 但全部 id 为空字符串的脏数据场景下保险一手）。
        if not self._all_opus_ids:
            self.btn_copy_all.configure(state="disabled")
        self.btn_copy_all.pack(side="left")

        # tooltip：靠 export_gui 的 Tooltip 类（已在主面板 widget 上使用过）
        try:
            from export_gui import Tooltip
            self._tip_copy_all = Tooltip(
                self.btn_copy_all,
                text=t("opus_delta_copy_tip").format(
                    n=len(self._all_opus_ids)))
        except Exception:
            # Tooltip 失败不能让对话框崩 —— 不是核心功能
            self._tip_copy_all = None

        btn_close = tk.Button(
            btn_row, text=t("opus_delta_close"),
            command=self.destroy,
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#fff",
            activebackground="#1a3a6a", activeforeground="#fff",
            relief="flat", padx=18, pady=4, cursor="hand2")
        btn_close.pack(side="right")

        # 键盘快捷键：
        #   Ctrl+C  —— 复制全部（Tk Treeview 上 Ctrl+C 默认无行为，
        #              我们劫持成"全选复制"语义；用户选不选行都触发同一逻辑）
        #   Esc     —— 关闭
        # 注意必须 bind 到 self（Toplevel）而不是单个 widget，让对话框
        # 任何子控件获得焦点时按 Ctrl+C 都生效。
        self.bind("<Control-c>", lambda _e: self._copy_all_opus_ids())
        self.bind("<Control-C>", lambda _e: self._copy_all_opus_ids())
        self.bind("<Escape>", lambda _e: self.destroy())
        # 主窗口居中显示
        self.transient(parent)

    # ------------------------------------------------------------------
    # 复制全部 OPUS ID 到剪贴板
    # ------------------------------------------------------------------
    def _copy_all_opus_ids(self):
        """把 self._all_opus_ids 用换行拼接后塞进剪贴板。

        关键细节：
          - ``clipboard_clear`` 之后必须 ``clipboard_append`` + ``update``，
            否则在 Windows 上窗口关闭就会丢失剪贴板内容（Tk 的剪贴板是
            "owner-based"，进程退出才真正移交给系统）。
          - 失败时尽量不要让 dialog 崩；按钮恢复原文案让用户能再点一次。
        """
        if not self._all_opus_ids:
            return
        payload = "\n".join(self._all_opus_ids)
        try:
            self.clipboard_clear()
            self.clipboard_append(payload)
            # update() 让事件循环把剪贴板真正交给系统 —— 不调用的话用户
            # 关掉窗口后剪贴板就空了，会显得"按钮没生效"。
            self.update()
        except Exception:
            return  # 罕见，例如剪贴板被其他进程独占；按钮不反馈即可
        # 临时反馈
        n = len(self._all_opus_ids)
        self.btn_copy_all.configure(
            text=self.app._t("opus_delta_copied_n").format(n=n))
        # 复制完按钮短暂禁用，避免用户连点产生大量剪贴板争用
        self.btn_copy_all.configure(state="disabled")
        self.after(self._COPY_FEEDBACK_MS, self._restore_copy_button)

    def _restore_copy_button(self):
        """复制反馈结束 → 把按钮恢复为可点击 + 原文案。"""
        # widget 可能已经被销毁（用户在反馈期间关掉了对话框）—— 不能
        # 直接访问 btn_copy_all.configure，否则 TclError。winfo_exists()
        # 检查最稳。
        try:
            if not self.btn_copy_all.winfo_exists():
                return
        except Exception:
            return
        self.btn_copy_all.configure(
            text=self._copy_default_text, state="normal")


# ---------------------------------------------------------------------------
# 钻取对话框：点击 Breakdown 行 → 该项目下所有文件 + 样本 opus_id
# ---------------------------------------------------------------------------
class ProjectDetailDialog(tk.Toplevel):
    """模态对话框：展示某 (project_id, source_kind) 的下钻数据。

    不阻塞主面板（用户可继续点别的行打开多个），靠 Toplevel 自带的
    窗口管理就够了。
    """

    def __init__(self, parent, app, detail: dict):
        super().__init__(parent)
        self.app = app
        self.detail = detail
        t = app._t
        project = detail.get("project_id", "")
        source = _source_label(detail.get("source_kind", ""), t)
        self.title(t("opus_dlg_project_title").format(
            project=project, source=source))
        self.configure(bg="#16213e")
        self.geometry("840x540")

        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=16, pady=12)

        # Summary header
        summary = detail.get("summary") or {}
        lbl = tk.Label(
            outer,
            text=t("opus_dlg_project_summary").format(
                opus=summary.get("opus_count", 0),
                files=summary.get("path_count", 0),
                langs=summary.get("lang_count", 0),
                rows=summary.get("row_count", 0),
                first=_fmt_iso_short(summary.get("first_seen")),
                last=_fmt_iso_short(summary.get("last_added")),
            ),
            bg="#16213e", fg="#ccc",
            font=(FONT_FAMILY, 10), justify="left", anchor="w")
        lbl.pack(fill="x", pady=(0, 8))

        # Per-file table
        files = detail.get("files") or []
        files_title = ttk.Label(
            outer,
            text=t("opus_dlg_files_title").format(n=len(files)),
            style="CardBold.TLabel")
        files_title.pack(anchor="w", pady=(0, 4))

        tbl_frame = ttk.Frame(outer, style="App.TFrame")
        tbl_frame.pack(fill="both", expand=True)
        # v4: 加 source path 列 + 双击钻取到 FileDetailDialog
        cols = ("pathhash", "path", "opus", "langs", "last", "samples")
        self._file_tree = tree = ttk.Treeview(
            tbl_frame, columns=cols, show="headings",
            style="Summary.Treeview", selectmode="browse")
        widths = {"pathhash": 200, "path": 230, "opus": 70, "langs": 50,
                  "last": 110, "samples": 220}
        for c in cols:
            anchor = "w" if c in ("pathhash", "path", "samples") else "center"
            tree.column(c, width=widths.get(c, 80), anchor=anchor)
        tree.heading("pathhash", text=t("opus_dlg_files_col_pathhash"))
        tree.heading("path",     text=t("opus_dlg_files_col_path"))
        tree.heading("opus",     text=t("opus_dlg_files_col_opus"))
        tree.heading("langs",    text=t("opus_dlg_files_col_langs"))
        tree.heading("last",     text=t("opus_dlg_files_col_last"))
        tree.heading("samples",  text=t("opus_dlg_files_col_samples"))

        sb = ttk.Scrollbar(tbl_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # iid → (alias, path_hash) 让双击能精确钻取（不依赖列文本解析）
        self._file_row_keys: dict[str, tuple[str, str]] = {}
        n_empty_paths = 0
        for f in files:
            # 把样本拼成一串短文本，列内能看到 3-5 个
            samples = f.get("samples") or []
            sample_keys = [
                (s.get("logical_key") or "")[:14] for s in samples[:5]
            ]
            sample_text = ", ".join(k for k in sample_keys if k)
            raw_path = f.get("source_file_path") or ""
            path = raw_path if raw_path else "—"
            if not raw_path:
                n_empty_paths += 1
            alias = f.get("alias", "")
            path_hash = f.get("path_hash", "")
            iid = tree.insert("", "end", values=(
                path_hash[:32],
                path,
                f"{f.get('opus_count', 0):,}",
                f.get("lang_count", 0),
                _fmt_iso_short(f.get("last_added")),
                sample_text,
            ))
            self._file_row_keys[iid] = (alias, path_hash)

        # 双击文件行 → 弹 FileDetailDialog（该文件下所有 opus_id 一览）
        tree.bind("<Double-1>", self._on_file_dbl)

        # 路径列有空值时，给用户一行明确解释 —— 直接告诉他这不是 bug
        # 而是上游 API 的客观限制，并指引到「回填路径」按钮。
        if n_empty_paths > 0:
            tk.Label(
                outer,
                text=(
                    f"ℹ️  {n_empty_paths}/{len(files)} files show '—' for path "
                    f"— Tranzor's MR/Legacy APIs don't expose source_file_path. "
                    f"Click '📥 Backfill paths' if you've synced a Scan task "
                    f"covering the same files."
                    if self.app.lang == "en" else
                    f"ℹ️  {n_empty_paths}/{len(files)} 个文件的路径显示为 '—' "
                    f"—— 因为 Tranzor 的 MR/Legacy API 不返回 source_file_path。"
                    f"如有 Scan 任务覆盖同一文件，点顶部「📥 回填路径」即可补齐。"
                ),
                bg="#16213e", fg="#9aa0b0",
                font=(FONT_FAMILY, 9), wraplength=820,
                justify="left", anchor="w",
            ).pack(fill="x", pady=(6, 0))

        # Close button row
        btn_row = ttk.Frame(outer, style="App.TFrame")
        btn_row.pack(fill="x", pady=(8, 0))
        close_btn = app._create_button(
            btn_row, text=t("opus_dlg_close"), command=self.destroy,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        close_btn.pack(side="right")

        # 让 ESC / 窗口关都能优雅退出
        self.bind("<Escape>", lambda _e: self.destroy())
        self.transient(parent)

    def _on_file_dbl(self, _event=None):
        """双击文件行 → FileDetailDialog 展示该文件下所有 opus_id。"""
        sel = self._file_tree.selection()
        if not sel:
            return
        key = self._file_row_keys.get(sel[0])
        if not key:
            return
        alias, path_hash = key
        try:
            fdet = om.get_file_detail(
                self.detail.get("project_id", ""),
                alias, path_hash,
                source_kind=self.detail.get("source_kind") or None,
            )
        except Exception as e:
            print(f"[file_dbl] failed: {e!r}")
            return
        FileDetailDialog(self, self.app, fdet)


# ---------------------------------------------------------------------------
# 钻取对话框：点击 File 行 → 该文件下所有 opus_id 一览
# ---------------------------------------------------------------------------
class FileDetailDialog(tk.Toplevel):
    """单文件详情：(project, alias, path_hash) 下所有 opus_id 的列表。

    对 LOC-24722 这种"同 logical_key 跨多个 path_hash" 的事故诊断特别有用：
    用户能逐字看清这个文件里到底有哪些 key。
    """

    def __init__(self, parent, app, detail: dict):
        super().__init__(parent)
        self.app = app
        self.detail = detail
        t = app._t
        summary = detail.get("summary") or {}
        path = summary.get("source_file_path") or detail.get("path_hash", "")
        # title 优先显示真实路径，没路径就退回 path_hash
        self.title(t("opus_dlg_file_title").format(label=path[:64]))
        self.configure(bg="#16213e")
        self.geometry("960x600")

        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=16, pady=12)

        # Summary
        path_display = (summary.get("source_file_path")
                         or t("opus_dlg_opus_path_missing"))
        tk.Label(
            outer,
            text=t("opus_dlg_file_summary").format(
                opus=summary.get("opus_count", 0),
                langs=summary.get("lang_count", 0),
                rows=summary.get("row_count", 0),
                path=path_display,
                first=_fmt_iso_short(summary.get("first_seen")),
                last=_fmt_iso_short(summary.get("last_added")),
            ),
            bg="#16213e", fg="#ccc",
            font=(FONT_FAMILY, 10), justify="left", anchor="w",
        ).pack(fill="x", pady=(0, 8))

        # OPUS ID list
        opus_rows = detail.get("opus_ids") or []
        ttk.Label(
            outer,
            text=t("opus_dlg_file_opus_title").format(n=len(opus_rows)),
            style="CardBold.TLabel",
        ).pack(anchor="w", pady=(0, 4))

        tbl = ttk.Frame(outer, style="App.TFrame")
        tbl.pack(fill="both", expand=True)
        cols = ("logkey", "langs", "source", "added")
        self._opus_tree = tree = ttk.Treeview(
            tbl, columns=cols, show="headings",
            style="Summary.Treeview", selectmode="browse")
        widths = {"logkey": 280, "langs": 60, "source": 460, "added": 110}
        for c in cols:
            anchor = "w" if c in ("logkey", "source") else "center"
            tree.column(c, width=widths.get(c, 80), anchor=anchor)
        tree.heading("logkey", text=t("opus_dlg_file_col_logkey"))
        tree.heading("langs",  text=t("opus_dlg_file_col_langs"))
        tree.heading("source", text=t("opus_dlg_file_col_source"))
        tree.heading("added",  text=t("opus_dlg_file_col_added"))
        sb = ttk.Scrollbar(tbl, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._opus_row_keys: dict[str, str] = {}
        for r in opus_rows:
            src = (r.get("source_text") or "")[:200]
            iid = tree.insert("", "end", values=(
                r.get("logical_key", ""),
                r.get("lang_count", 0),
                src,
                _fmt_iso_short(r.get("last_added")),
            ))
            self._opus_row_keys[iid] = r.get("opus_id", "")

        # 双击 opus 行 → 继续钻到 OpusDetailDialog（完整画像）
        tree.bind("<Double-1>", self._on_opus_dbl)

        # Close
        btn_row = ttk.Frame(outer, style="App.TFrame")
        btn_row.pack(fill="x", pady=(8, 0))
        close_btn = app._create_button(
            btn_row, text=t("opus_dlg_close"), command=self.destroy,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        close_btn.pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.transient(parent)

    def _on_opus_dbl(self, _event=None):
        sel = self._opus_tree.selection()
        if not sel:
            return
        opus_id = self._opus_row_keys.get(sel[0])
        if not opus_id:
            return
        try:
            od = om.get_opus_detail(opus_id)
        except Exception as e:
            print(f"[file→opus] failed: {e!r}")
            return
        OpusDetailDialog(self, self.app, od)


# ---------------------------------------------------------------------------
# 钻取对话框：点击 Recently added 行 → 该 opus_id 完整画像
# ---------------------------------------------------------------------------
class OpusDetailDialog(tk.Toplevel):
    """单个 opus_id 的"全身像"：完整 ID、4 段分解、所有目标语言、源文本。"""

    def __init__(self, parent, app, detail: dict):
        super().__init__(parent)
        self.app = app
        self.detail = detail
        t = app._t
        self.title(t("opus_dlg_opus_title"))
        self.configure(bg="#16213e")
        self.geometry("780x560")

        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=16, pady=12)

        opus_id = detail.get("opus_id", "")
        # 顶部：完整 opus_id + Copy 按钮（最常用的就是把它复制走）
        head = ttk.Frame(outer, style="App.TFrame")
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text=t("opus_dlg_opus_full") + ":",
                  style="CardBold.TLabel").pack(side="left")
        copy_btn = app._create_button(
            head, text=t("opus_dlg_copy"),
            command=lambda: self._copy(opus_id),
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 9),
            bg="#0f3460", fg="#ccc", padx=10, pady=2)
        copy_btn.pack(side="right")

        full_id_box = tk.Text(
            outer, height=2, wrap="word",
            bg="#0a0a1a", fg="#fff", relief="flat",
            font=(FONT_FAMILY, 10), padx=8, pady=6)
        full_id_box.insert("1.0", opus_id)
        full_id_box.configure(state="disabled")
        full_id_box.pack(fill="x", pady=(0, 8))

        # 4-段分解 + 元数据
        # path_hash 后紧跟"真实路径"一行——这是 debug "为啥 ID 变了" 的核心证据。
        # 路径空时按来源管线给不同的诚实解释：
        #   - mr   → Tranzor MR API 根本不返回这字段，让用户用反查工具
        #   - scan → 历史缓存遗留，Scan API 是有这字段的，让用户重建
        #   - file → File Translation API 也不返回
        path = detail.get("source_file_path", "")
        if path:
            path_display = path
        else:
            sk = (detail.get("source_kind") or "").lower()
            if sk == "mr":
                path_display = t("opus_dlg_opus_path_mr_missing")
            elif sk == "file":
                path_display = t("opus_dlg_opus_path_legacy_missing")
            else:
                # scan 或未知 → 老的"重建一下"文案
                path_display = t("opus_dlg_opus_path_missing")
        meta = [
            (t("opus_dlg_opus_alias"),     detail.get("alias", "")),
            (t("opus_dlg_opus_pathhash"),  detail.get("path_hash", "")),
            (t("opus_dlg_opus_path"),      path_display),
            (t("opus_dlg_opus_logkey"),    detail.get("logical_key", "")),
            (t("opus_dlg_opus_project"),
                _project_label(detail.get("project_id", ""),
                                detail.get("source_kind", ""), t)),
            (t("opus_dlg_opus_source"),
                _source_label(detail.get("source_kind", ""), t)),
            (t("opus_dlg_opus_mr"),
                str(detail.get("mr_iid") or detail.get("task_id", ""))),
            (t("opus_dlg_opus_first"),
                _fmt_iso_short(detail.get("task_created_at"))),
            (t("opus_dlg_opus_first_local"),
                _fmt_iso_short(detail.get("first_seen"))),
        ]
        meta_frame = ttk.Frame(outer, style="App.TFrame")
        meta_frame.pack(fill="x", pady=(0, 8))
        # path_hash 和 source_file_path 都用等宽体，方便和原始数据对照看
        _mono_keys = ("logkey", "path", "alias", "logical")
        # 几个"我们故意填的占位提示"——这些行的 value 要显示成灰色而不是白色，
        # 否则视觉上像真实数据，会误导用户。
        _placeholder_values = {
            t("opus_dlg_opus_path_missing"),
            t("opus_dlg_opus_path_mr_missing"),
            t("opus_dlg_opus_path_legacy_missing"),
        }
        for row_i, (label, value) in enumerate(meta):
            tk.Label(
                meta_frame, text=label + ":",
                bg="#16213e", fg="#9aa0b0",
                font=(FONT_FAMILY, 9), anchor="e",
                width=46,  # 加宽容纳 "源文件路径..." 这类长 label
            ).grid(row=row_i, column=0, sticky="e", padx=(0, 8), pady=1)
            is_mono = any(k in label.lower() for k in _mono_keys)
            is_placeholder = (value in _placeholder_values)
            tk.Label(
                meta_frame, text=value or "—",
                bg="#16213e",
                fg="#9aa0b0" if (not value or is_placeholder) else "#fff",
                font=(FONT_MONO if is_mono and not is_placeholder
                                 else FONT_FAMILY, 9),
                anchor="w",
                wraplength=520,
                justify="left",
            ).grid(row=row_i, column=1, sticky="w", pady=1)

        # 源文本（可能很长）—— 单独一行可滚动 Text
        src_label = ttk.Label(
            outer, text=t("opus_dlg_opus_text") + ":",
            style="CardBold.TLabel")
        src_label.pack(anchor="w", pady=(8, 4))
        src_box = tk.Text(
            outer, height=3, wrap="word",
            bg="#0a0a1a", fg="#fff", relief="flat",
            font=(FONT_FAMILY, 10), padx=8, pady=6)
        src_box.insert("1.0", detail.get("source_text") or "")
        src_box.configure(state="disabled")
        src_box.pack(fill="x", pady=(0, 8))

        # 译文预览表 —— 每个目标语言一行，左侧 lang code、右侧译文。
        # 这是用户能在监控面板里直接看翻译的地方，省去跳转 Tranzor 的麻烦。
        langs = detail.get("target_languages") or []
        ttk.Label(
            outer,
            text=t("opus_dlg_opus_translations").format(n=len(langs)),
            style="CardBold.TLabel").pack(anchor="w", pady=(0, 4))
        trans_frame = ttk.Frame(outer, style="App.TFrame")
        trans_frame.pack(fill="both", expand=True, pady=(0, 8))
        tcols = ("lang", "text")
        trans_tree = ttk.Treeview(
            trans_frame, columns=tcols, show="headings",
            style="Summary.Treeview", selectmode="browse", height=8)
        trans_tree.column("lang", width=80, anchor="center")
        trans_tree.column("text", width=620, anchor="w")
        trans_tree.heading("lang", text=t("opus_dlg_opus_trans_col_lang"))
        trans_tree.heading("text", text=t("opus_dlg_opus_trans_col_text"))
        tsb = ttk.Scrollbar(trans_frame, orient="vertical",
                             command=trans_tree.yview)
        trans_tree.configure(yscrollcommand=tsb.set)
        trans_tree.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        empty_marker = t("opus_dlg_opus_trans_empty")
        for L in langs:
            text = L.get("translated_text") or empty_marker
            # 单行 treeview 不能换行，过长截一下；双击源文本框已能看全
            if len(text) > 200:
                text = text[:200] + "…"
            trans_tree.insert("", "end", values=(
                L.get("target_language", ""), text))

        # 操作按钮区：左下 status 反馈条 · 右下 Send / Close
        btn_row = ttk.Frame(outer, style="App.TFrame")
        btn_row.pack(fill="x", pady=(8, 0))

        # status 条 —— Send 完了把 ok / fallback / err 文案写这里
        self._send_status = tk.Label(
            btn_row, text="", bg="#16213e", fg="#9aa0b0",
            font=(FONT_FAMILY, 9), anchor="w")
        self._send_status.pack(side="left", fill="x", expand=True)

        close_btn = app._create_button(
            btn_row, text=t("opus_dlg_close"), command=self.destroy,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        close_btn.pack(side="right")

        # Send to Tranzor —— 通过 Tranzor Bridge 把这个 opus_id 推送给
        # Tranzor Platform tab 上的 Tampermonkey 脚本，浏览器侧立刻
        # 定位到对应 MR / scan task / file translation 详情。
        # Bridge 未启动时降级为：复制 envelope JSON 到剪贴板，并提示。
        send_btn = app._create_button(
            btn_row, text=t("opus_dlg_send"), command=self._send_to_tranzor,
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#2ecc71", fg="#fff", padx=14, pady=4)
        send_btn.pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.transient(parent)

    def _copy(self, text: str):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            # 不弹 toast；按钮文案瞬时变化就够
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Send to Tranzor —— 走本地 Tranzor Bridge 推 envelope
    # ------------------------------------------------------------------
    def _build_envelope(self) -> dict:
        """构造 tranzor-bridge/handoff/v1 envelope。

        schema 参考 export_mr_pipeline.write_mr_html 里的 buildEnvelope JS
        实现：每个 (opus_id, target_language) 二元组一个 item；
        userscript 凭 ``source.kind`` + ``context.*`` 决定路由到 MR / Scan /
        File Translation 哪个详情页。
        """
        import uuid
        from datetime import timezone as _tz
        d = self.detail
        source_kind = (d.get("source_kind") or "").lower()
        kind_map = {"mr": "mr_pipeline", "scan": "scan_task",
                    "file": "file_translation"}
        kind = kind_map.get(source_kind, "mr_pipeline")
        translation_type_map = {"mr": "MR", "scan": "Scan", "file": "Legacy"}

        opus_id = d.get("opus_id", "")
        project_id = d.get("project_id", "")
        task_id = d.get("task_id", "")
        mr_id = d.get("mr_iid")
        source_text = d.get("source_text", "")
        langs = d.get("target_languages") or []

        items = [{
            "string_key": opus_id,
            "task_id": task_id,
            "task_name": "",  # 我们不存这个；userscript 侧会用其他字段
            "project_id": project_id,
            "mr_id": mr_id if kind == "mr_pipeline" else None,
            "scan_task_id": task_id if kind == "scan_task" else None,
            "language": lang.get("target_language", ""),
            "source_text": source_text,
            "translated_text": "",  # 监控面板没存译文；userscript 会再去拉
            "translation_type": translation_type_map.get(source_kind, "MR"),
        } for lang in langs] or [{
            # 极端：opus 居然没有任何 target_language 行（异常数据）
            # 也要给一个空 item，避免 envelope items 为空被 bridge 拒收
            "string_key": opus_id,
            "task_id": task_id,
            "task_name": "",
            "project_id": project_id,
            "mr_id": mr_id if kind == "mr_pipeline" else None,
            "scan_task_id": task_id if kind == "scan_task" else None,
            "language": "",
            "source_text": source_text,
            "translated_text": "",
            "translation_type": translation_type_map.get(source_kind, "MR"),
        }]

        return {
            "$schema": "tranzor-bridge/handoff/v1",
            "envelope_id": str(uuid.uuid4()),
            "created_at": datetime.now(_tz.utc).isoformat(timespec="seconds"),
            "source": {
                "tool": "TranzorExporter",
                "kind": kind,
                "report_file": "opus_id_monitor",
            },
            "context": {
                "task_id": task_id or None,
                "task_name": None,
                "project_id": project_id or None,
                "mr_id": mr_id if kind == "mr_pipeline" else None,
                "scan_task_id": task_id if kind == "scan_task" else None,
                "language": None,
            },
            "items": items,
        }

    def _send_to_tranzor(self):
        t = self.app._t
        envelope = self._build_envelope()
        bridge = getattr(self.app, "bridge", None)
        if bridge is None:
            # Bridge 没起来 —— 退化为复制 envelope JSON，让用户能粘贴到别处
            import json as _json
            try:
                self.clipboard_clear()
                self.clipboard_append(_json.dumps(
                    envelope, ensure_ascii=False, indent=2))
            except Exception:
                pass
            self._send_status.configure(
                text=t("opus_dlg_send_no_bridge"), fg="#f1c40f")
            return
        try:
            bridge.push(envelope)
        except Exception as e:
            self._send_status.configure(
                text=t("opus_dlg_send_failed").format(error=str(e)[:60]),
                fg="#ff6b6b")
            return

        # 关键修复：仅"推到本地 Bridge 成功"不代表用户能在浏览器看到。
        # userscript 是轮询拉取的；如果 Tranzor 浏览器 tab 没开或脚本没装，
        # envelope 在 inbox 里待到下次被覆盖也没人看，旧版本却给用户报
        # "✓ 已发送" — 这就是本次问题的根因。改成查 status_snapshot 的
        # ``userscript_live``：
        #   - True  → 真活着，1-2s 内一定会被拉走，报成功
        #   - False → 没活的；既要明确告知，又要给一键启动 setup wizard 的
        #             出口，让用户能立刻自救
        try:
            snap = bridge.status_snapshot()
        except Exception:
            snap = {"userscript_live": False}

        if snap.get("userscript_live"):
            self._send_status.configure(
                text=t("opus_dlg_send_ok"), fg="#2ecc71")
            return

        # Pending 状态：可能 userscript 刚装好还没首次 pull，给它 ~6s
        # 时间，到点再查一次，避免对正常用户假告警。
        self._send_status.configure(
            text=t("opus_dlg_send_pending"), fg="#f1c40f")
        self.after(6000, self._recheck_userscript_after_send)

    def _recheck_userscript_after_send(self):
        """推送后 6s 复查 userscript_live。仍未活就给可操作的兜底提示。"""
        t = self.app._t
        bridge = getattr(self.app, "bridge", None)
        if bridge is None:
            return
        try:
            snap = bridge.status_snapshot()
        except Exception:
            return
        if snap.get("userscript_live"):
            # 6s 内回血了，再次给 OK 反馈
            self._send_status.configure(
                text=t("opus_dlg_send_ok"), fg="#2ecc71")
            return

        # 仍然没活 —— 出兜底提示 + 一键启动 setup wizard
        self._send_status.configure(
            text=t("opus_dlg_send_no_userscript"), fg="#f1c40f")
        # 已经放过 wizard 按钮就别再放一个
        if getattr(self, "_wizard_btn", None) is not None:
            return
        try:
            from export_gui import _bridge_wizard
        except Exception:
            _bridge_wizard = None
        if _bridge_wizard is None:
            return
        # 把 wizard 按钮放到 status 条所在的同一行
        parent = self._send_status.master
        self._wizard_btn = self.app._create_button(
            parent, text=t("opus_dlg_send_setup_wizard"),
            command=self._open_setup_wizard,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 9),
            bg="#0f3460", fg="#ccc", padx=10, pady=2)
        # 注意：tk 子组件不能 pack 到完全填满的 Label 旁边；先 forget
        # status 再依次 pack 让它们共享水平空间。
        self._send_status.pack_forget()
        self._send_status.pack(side="left", fill="x", expand=True)
        self._wizard_btn.pack(side="left", padx=(8, 0))

    def _open_setup_wizard(self):
        """让 bridge_setup_wizard 弹出来。复用 app 上现成的方法，避免重复
        实现"判断要不要打开 / 配什么 instance_id"的逻辑。"""
        app = self.app
        # app 在初始化时已经把 wizard 挂到自身上；不同分支 API 名不同，
        # 兼容下：先看 app.open_bridge_setup_wizard，再看模块 force_open
        wizard_fn = getattr(app, "open_bridge_setup_wizard", None)
        if callable(wizard_fn):
            try:
                wizard_fn(reason="opus_monitor_send")
                return
            except Exception:
                pass
        try:
            from export_gui import _bridge_wizard
            if _bridge_wizard and hasattr(_bridge_wizard, "force_open"):
                _bridge_wizard.force_open(app)
        except Exception as e:
            self._send_status.configure(
                text=self.app._t("opus_dlg_send_failed").format(
                    error=str(e)[:60]),
                fg="#ff6b6b")


# ---------------------------------------------------------------------------
# Path-hash 反查工具
# ---------------------------------------------------------------------------
class PathHashLookupDialog(tk.Toplevel):
    """Path-hash 反查工具。

    用户场景：
      1) 看到一个 opus_id 第 3 段是 32 位 hex，想知道它对应哪个项目／文件
         → 把 hash 粘进来 → 列出本地缓存中所有命中。
      2) 怀疑 MR 来源 opus_id 的 path 是某个候选路径
         （比如改名前的 / 改名后的）→ 把候选路径粘进来 →
         我们自己算 md5(path)，再查缓存看哪个项目用了这个 hash。

    这对 LOC-24722 类"hash 漂移"事故是直接命中需求 —— 在没有上游 API
    返回 source_file_path 的情况下，用户也能靠"反推+对账"自己排查。
    """

    # 32 位 hex 的判定：path_hash 一定是 md5 → 32 个 [0-9a-f]
    _HEX_RE = __import__("re").compile(r"^[0-9a-f]{32}$")

    def __init__(self, parent, app, prefill: str = ""):
        super().__init__(parent)
        self.app = app
        t = app._t
        self.title(t("opus_path_lookup_title"))
        self.configure(bg="#16213e")
        self.geometry("900x560")

        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=16, pady=12)

        # Intro
        tk.Label(
            outer, text=t("opus_path_lookup_intro"),
            bg="#16213e", fg="#9aa0b0",
            font=(FONT_FAMILY, 10), justify="left", anchor="w",
        ).pack(fill="x", pady=(0, 8))

        # Input row
        in_row = ttk.Frame(outer, style="App.TFrame")
        in_row.pack(fill="x", pady=(0, 8))
        ttk.Label(
            in_row, text=t("opus_path_lookup_input"),
            style="CardBold.TLabel",
        ).pack(side="left", padx=(0, 8))
        self.input_var = tk.StringVar(value=prefill)
        ent = tk.Entry(
            in_row, textvariable=self.input_var,
            font=(FONT_MONO, 10),
            bg="#0a0a1a", fg="#fff", insertbackground="#fff",
            relief="flat")
        ent.pack(side="left", fill="x", expand=True, ipady=4)
        ent.focus_set()
        ent.bind("<Return>", lambda _e: self._do_lookup())
        go_btn = app._create_button(
            in_row, text=t("opus_path_lookup_go"),
            command=self._do_lookup,
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#2ecc71", fg="#fff", padx=14, pady=4)
        go_btn.pack(side="left", padx=(8, 0))

        # Computed-hash readout
        self.lbl_hash_info = tk.Label(
            outer, text="", bg="#16213e", fg="#9aa0b0",
            font=(FONT_MONO, 9), anchor="w", justify="left")
        self.lbl_hash_info.pack(fill="x", pady=(0, 6))

        # Results table
        tbl = ttk.Frame(outer, style="App.TFrame")
        tbl.pack(fill="both", expand=True)
        cols = ("proj", "alias", "source", "opus", "langs", "path")
        self._tree = tree = ttk.Treeview(
            tbl, columns=cols, show="headings",
            style="Summary.Treeview", selectmode="browse")
        widths = {"proj": 220, "alias": 70, "source": 60,
                  "opus": 80, "langs": 60, "path": 380}
        for c in cols:
            anchor = "w" if c in ("proj", "path") else "center"
            tree.column(c, width=widths.get(c, 80), anchor=anchor)
        tree.heading("proj",   text=t("opus_path_lookup_col_proj"))
        tree.heading("alias",  text=t("opus_path_lookup_col_alias"))
        tree.heading("source", text=t("opus_path_lookup_col_source"))
        tree.heading("opus",   text=t("opus_path_lookup_col_opus"))
        tree.heading("langs",  text=t("opus_path_lookup_col_langs"))
        tree.heading("path",   text=t("opus_path_lookup_col_path"))
        sb = ttk.Scrollbar(tbl, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # 状态行（无匹配提示）
        self.lbl_status = tk.Label(
            outer, text="", bg="#16213e", fg="#f1c40f",
            font=(FONT_FAMILY, 10), anchor="w")
        self.lbl_status.pack(fill="x", pady=(6, 0))

        # Close
        btn_row = ttk.Frame(outer, style="App.TFrame")
        btn_row.pack(fill="x", pady=(8, 0))
        close_btn = app._create_button(
            btn_row, text=t("opus_dlg_close"), command=self.destroy,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        close_btn.pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.transient(parent)

        # 预填了 hash → 自动跑一次
        if prefill:
            self._do_lookup()

    def _do_lookup(self):
        t = self.app._t
        raw = (self.input_var.get() or "").strip()
        self._tree.delete(*self._tree.get_children())
        self.lbl_status.configure(text="")
        if not raw:
            self.lbl_hash_info.configure(text="")
            return

        # 自动识别输入是 hash 还是 path：32 位 lowercase hex 当 hash 处理
        if self._HEX_RE.match(raw.lower()):
            path_hash = raw.lower()
            self.lbl_hash_info.configure(
                text=t("opus_path_lookup_used_hash").format(hash=path_hash))
            matches = om.lookup_path_hash(path_hash)
        else:
            res = om.lookup_path_string(raw)
            path_hash = res["path_hash"]
            self.lbl_hash_info.configure(
                text=t("opus_path_lookup_computed").format(hash=path_hash))
            matches = res["matches"]

        if not matches:
            self.lbl_status.configure(text=t("opus_path_lookup_no_match"))
            return

        n_empty = 0
        any_known_path = ""
        for m in matches:
            raw_path = m.get("source_file_path") or ""
            if not raw_path:
                n_empty += 1
            elif not any_known_path:
                any_known_path = raw_path
            self._tree.insert("", "end", values=(
                m.get("project_id", ""),
                m.get("alias", ""),
                _source_label(m.get("source_kind", ""), t),
                f"{m.get('opus_count', 0):,}",
                m.get("lang_count", 0),
                raw_path or "—",
            ))

        # 友好提示：如果有 MR/Legacy 行的 path 为空但同 hash 的另一行（通常 Scan）
        # 有 path，说明 backfill 还没跑（或新数据进来还没回填）。明确告知。
        if n_empty > 0 and any_known_path:
            self.lbl_status.configure(
                fg="#f1c40f",
                text=(
                    f"⚠ {n_empty} rows show '—'; click '📥 Backfill paths' "
                    f"to copy the known path ({any_known_path[:60]}...) into them."
                    if self.app.lang == "en" else
                    f"⚠ 有 {n_empty} 行显示 '—'；点顶部「📥 回填路径」"
                    f"即可把已知路径 ({any_known_path[:60]}...) 复制到这些行。"
                ))
        elif n_empty > 0:
            self.lbl_status.configure(
                fg="#9aa0b0",
                text=(
                    f"ℹ {n_empty} rows show '—' — no Scan task has covered "
                    f"this file yet, so Tranzor hasn't surfaced its path."
                    if self.app.lang == "en" else
                    f"ℹ {n_empty} 行显示 '—' —— 还没有 Scan 任务覆盖该文件，"
                    f"Tranzor 尚未提供它的路径。"
                ))
