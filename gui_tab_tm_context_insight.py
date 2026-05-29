"""
TM & Context Insight — GUI Tab
==============================
让非技术背景的语言专家直观看到 Tranzor Platform 内部的两个"黑盒"：

1. **Translation Memory (TM)** —— 一条翻译是从 TM / ICE / 缓存 / LLM / 人工修订
   哪个环节产生的？
2. **Context Service** —— 翻译时有没有挂上下文？上下文拉取是否失败？

数据完全由现有 Tranzor HTTP API 提供，不依赖任何后端改动：
- 聚合 & 行级数据来源：GET /api/v1/dashboard/cases （MR Pipeline 范畴）
- 上下文正文：GET /api/v1/context/record/{context_id}

设计原则
--------
- v1 仅覆盖 MR Pipeline 范畴（dashboard/cases）。File Translation (Legacy) schema
  略有差异（没有 tm_match 字段），后续再扩展。
- 上半区聚合面板用 Unicode 块字符画条形图，零额外依赖。
- 下半区表格双击行 → 抽屉显示上下文 JSON。
"""
from __future__ import annotations

import os
import re
import sys
import json
import threading
import tkinter as tk
from tkinter import ttk
from datetime import date, datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_mr_pipeline as mr_api
from export_gui import FONT_FAMILY, FONT_MONO, IS_MAC

try:
    import requests
except ImportError:
    requests = None


# ============================================================
# i18n
# ============================================================
STRINGS = {
    "en": {
        "tab_tm_context_insight":   "🔬 TM & Context Insight",
        # filter bar
        "tci_filter_project":       "Project",
        "tci_filter_language":      "Language",
        "tci_filter_date_start":    "From",
        "tci_filter_date_end":      "To",
        "tci_filter_refresh":       "🔍 Refresh",
        "tci_filter_reset":         "Reset",
        # aggregate panel
        "tci_agg_title":            "Translation Source Composition (MR Pipeline)",
        "tci_agg_subtitle":         "How each translation was produced — by target language",
        "tci_agg_total":            "Total translations",
        "tci_agg_no_data":          "No data in this range. Try widening the date range.",
        "tci_ctx_title":            "Context Service Coverage",
        "tci_ctx_subtitle":         "Did the LLM receive contextual evidence for the translation?",
        # row table
        "tci_row_title":            "Recent Translations — drill down",
        "tci_row_hint":             "Double-click a row to view the context snippet (if any)",
        "tci_col_idx":              "#",
        "tci_col_project":          "Project",
        "tci_col_mr":               "MR#",
        "tci_col_lang":             "Language",
        "tci_col_opus":             "String Key",
        "tci_col_source":           "Source",
        "tci_col_badges":           "Badges",
        "tci_col_score":            "Score",
        # badges
        "tci_badge_tm":             "TM",
        "tci_badge_ice":            "ICE",
        "tci_badge_cached":         "Cached",
        "tci_badge_llm":            "LLM",
        "tci_badge_refined":        "Refined",
        "tci_badge_human":          "Human",
        "tci_badge_ctx_ok":         "Ctx✓",
        "tci_badge_ctx_partial":    "partial",
        "tci_badge_ctx_none":       "NoCtx",
        # sidebar
        "tci_sidebar_title":        "📈 Pipeline Routing",
        "tci_stat_total":           "Total",
        "tci_stat_tm":              "TM hits",
        "tci_stat_ice":             "ICE hits",
        "tci_stat_cached":          "Cached",
        "tci_stat_llm":             "LLM (fresh)",
        "tci_stat_refined":         "Refined (iter ≥ 2)",
        "tci_stat_human":           "Human-fixed",
        "tci_stat_ctx_ok":          "With context",
        "tci_stat_ctx_none":        "No context",
        # context drawer
        "tci_drawer_title":         "Context Snippet",
        "tci_drawer_no_id":         "This translation has no context_id — Context Service was not consulted.",
        "tci_drawer_loading":       "Loading context from Tranzor…",
        "tci_drawer_error":         "Failed to load context: {err}",
        "tci_drawer_unsupported_id": "This context ID ({cid}) is a UUID / hash, not an integer. The Tranzor proxy route only accepts numeric IDs, so this record can't be fetched directly. (This doesn't affect the rest of the analysis.)",
        "tci_drawer_close":         "Close",
        # status
        "tci_status_ready":         "Ready",
        "tci_status_loading":       "Loading",
        "tci_status_done":          "✓ Loaded",
        "tci_status_error":         "⚠ {err}",
        # help button + modal
        "tci_help_btn":             "❓ Help",
        "tci_help_title":           "TM & Context Insight — Quick Help",
        "tci_help_close":           "Close",
        "tci_help_full_guide_hint": "See TM_Context_Insight_Help.md for the full guide.",
        "tci_help_body": (
            "WHAT THIS PANEL SHOWS\n"
            "─────────────────────\n"
            "Tranzor produces every translation through a hidden pipeline:\n"
            "TM reuse → ICE reuse → cache reuse → LLM translation → optional refinement.\n"
            "This panel exposes which path produced each translation, and whether\n"
            "the LLM received context evidence — so you can monitor pipeline\n"
            "behavior without asking the dev team.\n"
            "\n"
            "THREE REGIONS\n"
            "─────────────\n"
            "① Upper aggregate — stacked bars per target language. Read each row\n"
            "  left-to-right: TM | ICE | Cached | LLM | Refined | Human. A second\n"
            "  block shows context coverage (with / partial / none).\n"
            "\n"
            "② Lower table — one row per translation (up to 500). Look at the\n"
            "  'Badges' column to see how that row was produced.\n"
            "  DOUBLE-CLICK any row to open a drawer with the actual context\n"
            "  snippet the LLM saw at translation time.\n"
            "\n"
            "③ Right sidebar — nine KPIs: total / TM hits / ICE hits / Cached /\n"
            "  LLM-fresh / Refined / Human-fixed / With context / No context.\n"
            "\n"
            "BADGE DICTIONARY\n"
            "────────────────\n"
            "  TM        Reused from Translation Memory (approved before, free)\n"
            "  ICE       Same project's history had an identical translation\n"
            "  Cached    Tranzor cache hit (same string in a recent task)\n"
            "  LLM       Fresh LLM translation\n"
            "  Refined×N LLM output was re-run N times (low score retry)\n"
            "  Human     Edited by a Language Lead in the Dashboard\n"
            "  Ctx ✓     LLM received meaningful context evidence (best)\n"
            "  Ctx ◐     Context lookup happened but returned empty content\n"
            "  NoCtx     No context attached at all\n"
            "\n"
            "A single row can carry multiple badges. Example:\n"
            "  'TM  Refined×2  Ctx ✓'  →  TM hit first, low score so refined twice,\n"
            "                            and context was attached during translation.\n"
            "\n"
            "FILTERS\n"
            "───────\n"
            "  Project   Empty = all; or type a project ID (CoreLib/mthor, web/chc)\n"
            "  Language  Empty = all; or type a language code (zh-CN, fr-FR)\n"
            "  From/To   Date range; default is last 30 days\n"
            "  Reset     Restores defaults\n"
            "  Refresh   Re-fetches with current filters\n"
            "\n"
            "TIPS\n"
            "────\n"
            "• If 'No context' dominates a language, flag the project to the\n"
            "  Tranzor team — Context Service may not be onboarded there.\n"
            "• If 'Refined ratio' is > 20% somewhere, initial-translation quality\n"
            "  is weak; share the language + project with the platform team.\n"
            "• v1 scope: MR Pipeline only (File Translation has no tm_match field;\n"
            "  use the Full Translations tab for that data).\n"
            "• Context snippets are fetched on demand (1-2s delay on first click).\n"
        ),
    },
    "zh": {
        "tab_tm_context_insight":   "🔬 TM 与上下文洞察",
        # filter bar
        "tci_filter_project":       "项目",
        "tci_filter_language":      "语言",
        "tci_filter_date_start":    "起",
        "tci_filter_date_end":      "止",
        "tci_filter_refresh":       "🔍 刷新",
        "tci_filter_reset":         "重置",
        # aggregate panel
        "tci_agg_title":            "翻译来源构成（MR Pipeline）",
        "tci_agg_subtitle":         "每条翻译是从哪条管线产生的 — 按目标语言分行",
        "tci_agg_total":            "翻译总条数",
        "tci_agg_no_data":          "此区间无数据，请放宽日期范围。",
        "tci_ctx_title":            "Context Service 覆盖率",
        "tci_ctx_subtitle":         "LLM 翻译时是否拿到了上下文证据？",
        # row table
        "tci_row_title":            "最近翻译 — 行级钻取",
        "tci_row_hint":             "双击任意一行可查看该翻译当时使用的上下文片段",
        "tci_col_idx":              "#",
        "tci_col_project":          "项目",
        "tci_col_mr":               "MR#",
        "tci_col_lang":             "语言",
        "tci_col_opus":             "String Key",
        "tci_col_source":           "原文",
        "tci_col_badges":           "来源徽章",
        "tci_col_score":            "分数",
        # badges
        "tci_badge_tm":             "TM",
        "tci_badge_ice":            "ICE",
        "tci_badge_cached":         "缓存",
        "tci_badge_llm":            "LLM",
        "tci_badge_refined":        "精炼",
        "tci_badge_human":          "人工",
        "tci_badge_ctx_ok":         "有上下文",
        "tci_badge_ctx_partial":    "部分",
        "tci_badge_ctx_none":       "无上下文",
        # sidebar
        "tci_sidebar_title":        "📈 管线路由",
        "tci_stat_total":           "总计",
        "tci_stat_tm":              "TM 命中",
        "tci_stat_ice":             "ICE 命中",
        "tci_stat_cached":          "缓存复用",
        "tci_stat_llm":             "LLM 新译",
        "tci_stat_refined":         "精炼（迭代 ≥ 2）",
        "tci_stat_human":           "人工修订",
        "tci_stat_ctx_ok":          "携带上下文",
        "tci_stat_ctx_none":        "无上下文",
        # context drawer
        "tci_drawer_title":         "上下文片段",
        "tci_drawer_no_id":         "该翻译没有 context_id —— Context Service 未被调用。",
        "tci_drawer_loading":       "正在从 Tranzor 拉取上下文…",
        "tci_drawer_error":         "上下文加载失败：{err}",
        "tci_drawer_unsupported_id": "该上下文 ID（{cid}）是 UUID 或哈希格式，不是整数。主站代理路由仅支持数字 ID，无法直接拉取该记录。（不影响分析的其余部分。）",
        "tci_drawer_close":         "关闭",
        # status
        "tci_status_ready":         "就绪",
        "tci_status_loading":       "加载中",
        "tci_status_done":          "✓ 已加载",
        "tci_status_error":         "⚠ {err}",
        # help button + modal
        "tci_help_btn":             "❓ 帮助",
        "tci_help_title":           "TM 与上下文洞察 — 快速帮助",
        "tci_help_close":           "关闭",
        "tci_help_full_guide_hint": "完整指南见 TM_Context_Insight_Help-zh.md",
        "tci_help_body": (
            "面板做什么\n"
            "─────────\n"
            "Tranzor 给你每一条翻译时，内部走的是一条隐形管线：\n"
            "TM 复用 → ICE 复用 → 缓存复用 → LLM 翻译 → 必要时回炼。\n"
            "本面板把这条管线敞开 —— 你能看到每条翻译走的哪条路，\n"
            "以及 LLM 翻译时有没有拿到上下文证据。无须问开发团队。\n"
            "\n"
            "三个区域\n"
            "────────\n"
            "① 上半区聚合 —— 按目标语言展示堆叠条形图，从左到右是\n"
            "  TM | ICE | 缓存 | LLM | 精炼 | 人工 的占比。下面再一块\n"
            "  展示「上下文覆盖率」（有 / 部分 / 无）。\n"
            "\n"
            "② 下半区表格 —— 每条翻译一行（最多 500 行）。重点看\n"
            "  「来源徽章」列，它告诉你这一条是怎么产生的。\n"
            "  **双击任意一行** → 弹出抽屉，显示该翻译当时 LLM\n"
            "  实际拿到的上下文片段（JSON）。\n"
            "\n"
            "③ 右侧侧边栏 —— 九个 KPI：总计 / TM 命中 / ICE 命中 /\n"
            "  缓存复用 / LLM 新译 / 精炼 / 人工修订 / 携带上下文 /\n"
            "  无上下文。每周扫一眼判断管线健康度。\n"
            "\n"
            "徽章字典\n"
            "────────\n"
            "  TM        来自翻译记忆库（之前批准过，零成本复用）\n"
            "  ICE       同项目历史里有一字不差的译文\n"
            "  缓存      Tranzor 缓存命中（上个任务刚翻过同样字符串）\n"
            "  LLM       LLM 新译\n"
            "  精炼×N    LLM 译完分数低，回炼了 N 轮\n"
            "  人工      Language Lead 在 Dashboard 上修订过\n"
            "  有上下文  LLM 拿到了实质性的上下文证据（最理想）\n"
            "  Ctx ◐    查询了上下文但内容为空/弱\n"
            "  无上下文  完全没挂上下文\n"
            "\n"
            "一条翻译可同时带多个徽章。例如：\n"
            "  「TM  精炼×2  有上下文」 → 先 TM 命中，但分数低被回炼\n"
            "                          了两轮，且翻译时挂了上下文。\n"
            "\n"
            "筛选器\n"
            "──────\n"
            "  项目   留空 = 全部；或填项目 ID（CoreLib/mthor、web/chc）\n"
            "  语言   留空 = 全部；或填语言代码（zh-CN、fr-FR）\n"
            "  起/止  日期范围；默认近 30 天\n"
            "  重置   恢复默认\n"
            "  刷新   按当前筛选重新拉取\n"
            "\n"
            "小贴士\n"
            "──────\n"
            "• 如果某语言「无上下文」占大多数，跟 Tranzor 团队反馈一下\n"
            "  该项目是否接入了 Context Service。\n"
            "• 「精炼比例 > 20%」意味着初译质量普遍不高，建议把语言+\n"
            "  项目反馈给平台团队。\n"
            "• v1 仅覆盖 MR Pipeline（File Translation 没有 tm_match\n"
            "  字段，那部分数据请去 Full Translations tab 看）。\n"
            "• 上下文片段是按需拉取的，首次双击有 1~2 秒延迟正常。\n"
        ),
    },
}


# Context Service proxy endpoint
CONTEXT_RECORD_URL = f"{mr_api.TRANZOR_URL}/api/v1/context/record"

# Tranzor's proxy route declares ``record_id: int``; UUIDs / md5 hashes / any
# non-numeric id will be rejected with HTTP 422 before reaching the service.
# Short-circuit those locally so the UI can show a friendly explanation and
# we don't generate noise on Tranzor's side.
_INT_CTX_ID_RE = re.compile(r"^\d+$")


class _UnsupportedContextIdError(ValueError):
    """Raised when ``context_id`` isn't a pure-integer string and therefore
    cannot be queried via the int-typed Tranzor proxy route."""


def _fetch_context_record(context_id: str, timeout: int = 15) -> dict:
    """Fetch a single context record via Tranzor's existing proxy endpoint.

    Returns the raw JSON dict, or raises on HTTP / network error. Raises
    :class:`_UnsupportedContextIdError` when ``context_id`` isn't a pure
    integer (UUID / hash / etc.) — Tranzor's route would 422 anyway, so we
    bail out before making a useless network call.
    """
    if requests is None:
        raise RuntimeError("requests package not available")
    if not _INT_CTX_ID_RE.fullmatch(str(context_id)):
        raise _UnsupportedContextIdError(str(context_id))
    resp = requests.get(f"{CONTEXT_RECORD_URL}/{context_id}", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ============================================================
# Bar drawing helpers (pure Unicode, no chart library)
# ============================================================
_BAR_BLOCK = "█"
_BAR_LIGHT = "░"
_BAR_WIDTH = 30  # cells


def _stacked_bar(counts: list, total: int, width: int = _BAR_WIDTH) -> str:
    """Render a stacked bar for ``counts`` (in order), normalized to ``total``.

    Returns a string of ``width`` cells. Uses distinct shading per segment via
    cycling block density characters.
    """
    if not total:
        return _BAR_LIGHT * width
    cells = []
    remaining = width
    for n in counts:
        chunk = int(round(n / total * width))
        chunk = min(chunk, remaining)
        cells.append(_BAR_BLOCK * chunk)
        remaining -= chunk
    if remaining > 0:
        cells.append(_BAR_LIGHT * remaining)
    return "".join(cells)


# ============================================================
# Classifier — assign each case to a single "primary source"
# ============================================================
# Priority order matters: a TM-matched row that was later refined still came
# from TM in the first place. We surface the *earliest* decisive label so the
# language expert can see "would this have hit LLM if TM were missing?".
def _classify_source(case: dict) -> str:
    """Map one case dict to a single source bucket."""
    if case.get("fixed_by_lead"):
        return "human"
    tt = (case.get("translation_type") or "").strip().lower()
    if case.get("tm_match"):
        return "tm"
    if case.get("ice_match"):
        return "ice"
    if case.get("cached"):
        return "cached"
    if tt == "manual edit":
        return "human"
    if tt == "llm retranslate":
        return "refined"
    iteration = case.get("iteration") or 0
    if iteration and iteration >= 2:
        return "refined"
    # default — plain first-pass LLM
    return "llm"


def _classify_context(case: dict) -> str:
    """Map one case dict to a context bucket."""
    if case.get("has_context_details"):
        return "ctx_ok"
    if case.get("context_id"):
        # has an id but no detail — treat as partial / not useful
        return "ctx_partial"
    return "ctx_none"


# ============================================================
# Main tab
# ============================================================
class TmContextInsightTab:
    """Builds and manages the TM & Context Insight tab content."""

    SOURCE_KEYS = ("tm", "ice", "cached", "llm", "refined", "human")
    CONTEXT_KEYS = ("ctx_ok", "ctx_partial", "ctx_none")

    # Colors for badges — match the dashboard's visual language
    BADGE_COLORS = {
        "tm":      ("#1abc9c", "#0a0a1a"),
        "ice":     ("#3498db", "#0a0a1a"),
        "cached":  ("#9b59b6", "#0a0a1a"),
        "llm":     ("#7f8c8d", "#fff"),
        "refined": ("#e67e22", "#0a0a1a"),
        "human":   ("#e94560", "#fff"),
        "ctx_ok":  ("#2ecc71", "#0a0a1a"),
        "ctx_partial": ("#f39c12", "#0a0a1a"),
        "ctx_none": ("#34495e", "#fff"),
    }

    DEFAULT_PAGE_SIZE = 100  # MRs per fetch

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self.loading = False
        self._loading_anim_id = None
        self._loading_dot_count = 0
        self._cases: list = []      # flat list of case dicts (with MR metadata)
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

        # Row 1: project + language
        r1 = ttk.Frame(fi, style="Card.TFrame")
        r1.pack(fill="x", pady=(0, 6))

        self.lbl_project = ttk.Label(r1, text="", style="Card.TLabel", width=8)
        self.lbl_project.pack(side="left")
        self.project_var = tk.StringVar()
        self.ent_project = tk.Entry(r1, textvariable=self.project_var,
                                    width=24, font=(FONT_FAMILY, 10),
                                    bg="#0a0a1a", fg="#fff",
                                    insertbackground="#fff", relief="flat")
        self.ent_project.pack(side="left", padx=(4, 12), ipady=3)

        self.lbl_lang = ttk.Label(r1, text="", style="Card.TLabel", width=8)
        self.lbl_lang.pack(side="left")
        self.lang_var = tk.StringVar()
        self.ent_lang = tk.Entry(r1, textvariable=self.lang_var,
                                 width=12, font=(FONT_FAMILY, 10),
                                 bg="#0a0a1a", fg="#fff",
                                 insertbackground="#fff", relief="flat")
        self.ent_lang.pack(side="left", padx=(4, 12), ipady=3)

        # Row 2: date range
        r2 = ttk.Frame(fi, style="Card.TFrame")
        r2.pack(fill="x", pady=(0, 6))
        self.lbl_date_start = ttk.Label(r2, text="", style="Card.TLabel", width=8)
        self.lbl_date_start.pack(side="left")
        default_start = (date.today() - timedelta(days=30)).isoformat()
        self.start_var = tk.StringVar(value=default_start)
        self.ent_start = tk.Entry(r2, textvariable=self.start_var,
                                  width=14, font=(FONT_FAMILY, 10),
                                  bg="#0a0a1a", fg="#fff",
                                  insertbackground="#fff", relief="flat")
        self.ent_start.pack(side="left", padx=(4, 12), ipady=3)

        self.lbl_date_end = ttk.Label(r2, text="", style="Card.TLabel", width=4)
        self.lbl_date_end.pack(side="left")
        self.end_var = tk.StringVar(value=date.today().isoformat())
        self.ent_end = tk.Entry(r2, textvariable=self.end_var,
                                width=14, font=(FONT_FAMILY, 10),
                                bg="#0a0a1a", fg="#fff",
                                insertbackground="#fff", relief="flat")
        self.ent_end.pack(side="left", padx=(4, 12), ipady=3)

        # Row 3: buttons
        r3 = ttk.Frame(fi, style="Card.TFrame")
        r3.pack(fill="x")
        self.btn_refresh = self.app._create_button(
            r3, text="", command=self._on_refresh,
            style_name="AccentSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#e94560", fg="#fff", padx=14, pady=3)
        self.btn_refresh.pack(side="left", padx=(0, 6))
        self.btn_reset = self.app._create_button(
            r3, text="", command=self._on_reset,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=3)
        self.btn_reset.pack(side="left")

        self.lbl_status = ttk.Label(r3, text="", style="Status.TLabel")
        self.lbl_status.pack(side="left", padx=(16, 0))

        # Help button — pinned to the right edge so it's discoverable from
        # day one and never hidden by long status text.
        self.btn_help = self.app._create_button(
            r3, text="", command=self._show_help,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#9aa0b0", padx=10, pady=3)
        self.btn_help.pack(side="right")

        # ── Upper aggregate area (two stacked cards) ──
        upper = ttk.Frame(left, style="App.TFrame")
        upper.pack(fill="x", pady=(0, 8))

        self.lbl_agg_title = ttk.Label(upper, text="", style="CardBold.TLabel",
                                       font=(FONT_FAMILY, 11, "bold"))
        self.lbl_agg_title.pack(anchor="w")
        self.lbl_agg_subtitle = ttk.Label(upper, text="", style="Status.TLabel")
        self.lbl_agg_subtitle.pack(anchor="w", pady=(0, 2))

        # Inline color legend — small colored squares + localized labels.
        # Lets first-time users instantly map bar segments to pipeline sources
        # without having to read the help guide.
        self.agg_legend_frame = ttk.Frame(upper, style="App.TFrame")
        self.agg_legend_frame.pack(anchor="w", pady=(0, 4))
        self.agg_legend_labels = self._build_legend(
            self.agg_legend_frame, self.SOURCE_KEYS
        )

        self.agg_text = tk.Text(
            upper, height=10, font=(FONT_MONO, 10),
            bg="#0a0a1a", fg="#e4e7ef",
            relief="flat", borderwidth=0,
            wrap="none", state="disabled",
        )
        self.agg_text.pack(fill="x")

        self.lbl_ctx_title = ttk.Label(upper, text="", style="CardBold.TLabel",
                                       font=(FONT_FAMILY, 11, "bold"))
        self.lbl_ctx_title.pack(anchor="w", pady=(10, 0))
        self.lbl_ctx_subtitle = ttk.Label(upper, text="", style="Status.TLabel")
        self.lbl_ctx_subtitle.pack(anchor="w", pady=(0, 2))

        self.ctx_legend_frame = ttk.Frame(upper, style="App.TFrame")
        self.ctx_legend_frame.pack(anchor="w", pady=(0, 4))
        self.ctx_legend_labels = self._build_legend(
            self.ctx_legend_frame, self.CONTEXT_KEYS
        )

        self.ctx_text = tk.Text(
            upper, height=8, font=(FONT_MONO, 10),
            bg="#0a0a1a", fg="#e4e7ef",
            relief="flat", borderwidth=0,
            wrap="none", state="disabled",
        )
        self.ctx_text.pack(fill="x")

        # Register per-segment foreground colors so the stacked bars are
        # actually decipherable — same character █ in different colors per
        # pipeline source, matching the badge palette.
        self._setup_bar_tags()

        # ── Lower row table area ──
        self.lbl_row_title = ttk.Label(left, text="", style="CardBold.TLabel",
                                       font=(FONT_FAMILY, 11, "bold"))
        self.lbl_row_title.pack(anchor="w", pady=(10, 0))
        self.lbl_row_hint = ttk.Label(left, text="", style="Status.TLabel")
        self.lbl_row_hint.pack(anchor="w", pady=(0, 4))

        tree_frame = ttk.Frame(left, style="App.TFrame")
        tree_frame.pack(fill="both", expand=True)

        cols = ("idx", "project", "mr", "lang", "opus", "source", "badges", "score")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                 style="Summary.Treeview",
                                 height=12, selectmode="browse")
        col_widths = {"idx": 40, "project": 110, "mr": 70, "lang": 70,
                      "opus": 200, "source": 240, "badges": 200, "score": 60}
        for c in cols:
            anchor = "w" if c in ("project", "opus", "source", "badges") else "center"
            self.tree.column(c, width=col_widths.get(c, 80), anchor=anchor)

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self._on_row_double_click)

        self.loading_overlay = tk.Label(
            tree_frame, text="",
            # 等待指示：亮金加粗，醒目告知"正在加载"，免得用户以为卡死。
            font=(FONT_FAMILY, 15, "bold"),
            fg="#fbbf24", bg=self.app.BG, anchor="center")

        # ── Right sidebar ──
        self._build_sidebar(right)

    def _build_sidebar(self, parent):
        panel = ttk.Frame(parent, style="Summary.TFrame")
        panel.pack(fill="both", expand=True)
        panel.configure(borderwidth=1, relief="solid")
        inner = ttk.Frame(panel, style="Summary.TFrame")
        inner.pack(fill="both", expand=True, padx=14, pady=14)

        self.lbl_sidebar_title = ttk.Label(inner, text="", style="SummaryTitle.TLabel")
        self.lbl_sidebar_title.pack(anchor="w")
        tk.Frame(inner, bg="#2a2a4a", height=1).pack(fill="x", pady=(8, 10))

        stats = ttk.Frame(inner, style="Summary.TFrame")
        stats.pack(fill="x")
        self.stat_labels = {}
        for key in ("total", "tm", "ice", "cached", "llm", "refined", "human",
                    "ctx_ok", "ctx_none"):
            row = ttk.Frame(stats, style="Summary.TFrame")
            row.pack(fill="x", pady=3)
            lbl = ttk.Label(row, text="", style="Card.TLabel")
            lbl.pack(side="left")
            val = ttk.Label(row, text="—", style="CardBold.TLabel")
            val.pack(side="right")
            self.stat_labels[key] = (lbl, val)

    # ------------------------------------------------------------------
    # i18n
    # ------------------------------------------------------------------
    def refresh_text(self):
        t = self._t
        self.lbl_project.configure(text=t("tci_filter_project"))
        self.lbl_lang.configure(text=t("tci_filter_language"))
        self.lbl_date_start.configure(text=t("tci_filter_date_start"))
        self.lbl_date_end.configure(text=t("tci_filter_date_end"))
        self.btn_refresh.configure(text=t("tci_filter_refresh"))
        self.btn_reset.configure(text=t("tci_filter_reset"))
        self.btn_help.configure(text=t("tci_help_btn"))

        self.lbl_agg_title.configure(text=t("tci_agg_title"))
        self.lbl_agg_subtitle.configure(text=t("tci_agg_subtitle"))
        self.lbl_ctx_title.configure(text=t("tci_ctx_title"))
        self.lbl_ctx_subtitle.configure(text=t("tci_ctx_subtitle"))

        # Re-localize legend chip text labels (color stays constant)
        for _chip, text_lbl, key in self.agg_legend_labels:
            text_lbl.configure(text=t(f"tci_badge_{key}"))
        for _chip, text_lbl, key in self.ctx_legend_labels:
            text_lbl.configure(text=t(f"tci_badge_{key}"))

        self.lbl_row_title.configure(text=t("tci_row_title"))
        self.lbl_row_hint.configure(text=t("tci_row_hint"))
        for col, key in (("idx", "tci_col_idx"), ("project", "tci_col_project"),
                         ("mr", "tci_col_mr"), ("lang", "tci_col_lang"),
                         ("opus", "tci_col_opus"), ("source", "tci_col_source"),
                         ("badges", "tci_col_badges"), ("score", "tci_col_score")):
            self.tree.heading(col, text=t(key))

        self.lbl_sidebar_title.configure(text=t("tci_sidebar_title"))
        for key in ("total", "tm", "ice", "cached", "llm", "refined", "human",
                    "ctx_ok", "ctx_none"):
            self.stat_labels[key][0].configure(text=t(f"tci_stat_{key}"))

        # Re-render the aggregate panels with localized labels
        if self._cases:
            self._render_aggregates(self._cases)
            self._render_rows(self._cases)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_first_show(self):
        """Lazy-load on first tab activation."""
        if not self._cases:
            self._load()

    def _on_refresh(self):
        self._load()

    def _on_reset(self):
        self.project_var.set("")
        self.lang_var.set("")
        self.start_var.set((date.today() - timedelta(days=30)).isoformat())
        self.end_var.set(date.today().isoformat())
        self._load()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def _load(self):
        if self.loading:
            return
        self.loading = True
        self._set_controls_enabled(False)
        self.loading_overlay.configure(text=self._t("tci_status_loading") + "...")
        self.loading_overlay.place(relx=0.5, rely=0.4, anchor="center")
        self._loading_dot_count = 0
        self._animate_loading()
        threading.Thread(target=self._fetch, daemon=True).start()

    def _animate_loading(self):
        if not self.loading:
            return
        self._loading_dot_count = (self._loading_dot_count % 3) + 1
        dots = "." * self._loading_dot_count
        base = self._t("tci_status_loading")
        self.lbl_status.configure(text=f"{base}{dots}")
        self.loading_overlay.configure(text=f"{base}{dots}")
        self._loading_anim_id = self.parent.after(500, self._animate_loading)

    def _stop_loading_anim(self):
        if self._loading_anim_id is not None:
            self.parent.after_cancel(self._loading_anim_id)
            self._loading_anim_id = None
        self.loading_overlay.place_forget()

    def _set_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        if IS_MAC:
            flag = ["!disabled"] if enabled else ["disabled"]
            self.btn_refresh.state(flag)
            self.btn_reset.state(flag)
        else:
            self.btn_refresh.configure(state=state)
            self.btn_reset.configure(state=state)

    def _fetch(self):
        try:
            project = self.project_var.get().strip() or None
            lang = self.lang_var.get().strip() or None
            start_str = self.start_var.get().strip() or None
            end_str = self.end_var.get().strip() or None

            # Convert ISO date to ISO datetime (start = 00:00, end = 23:59:59)
            start_time = f"{start_str}T00:00:00" if start_str else None
            end_time = f"{end_str}T23:59:59" if end_str else None

            payload = mr_api.fetch_all_dashboard_cases(
                project_id=project,
                language=[lang] if lang else None,
                start_time=start_time,
                end_time=end_time,
                page_size=self.DEFAULT_PAGE_SIZE,
            )
            flat = []
            for mr in payload.get("mrs", []):
                mr_meta = {
                    "project_id": mr.get("project_id"),
                    "mr_iid": mr.get("mr_iid"),
                }
                for case in mr.get("cases", []) or []:
                    case = dict(case)
                    case["_project_id"] = mr_meta["project_id"]
                    case["_mr_iid"] = mr_meta["mr_iid"]
                    flat.append(case)
            self.parent.after(0, self._on_loaded, flat)
        except Exception as e:
            self.parent.after(0, self._on_error, str(e))

    def _on_loaded(self, cases):
        self.loading = False
        self._stop_loading_anim()
        self._set_controls_enabled(True)
        self._cases = cases
        self.lbl_status.configure(
            text=f"{self._t('tci_status_done')} ({len(cases)})"
        )
        self._render_aggregates(cases)
        self._render_rows(cases)
        self._render_sidebar(cases)

    def _on_error(self, err):
        self.loading = False
        self._stop_loading_anim()
        self._set_controls_enabled(True)
        self.lbl_status.configure(
            text=self._t("tci_status_error").format(err=err[:60])
        )

    # ------------------------------------------------------------------
    # Bar coloring helpers
    # ------------------------------------------------------------------
    # Foreground colors per bar segment. Bright enough to read on the
    # dark #0a0a1a Text background, distinct enough to tell adjacent
    # segments apart at a glance. Aligned with BADGE_COLORS so the
    # legend (badges in the table) matches the bar colors.
    BAR_PALETTE = {
        "tm":          "#1abc9c",  # teal
        "ice":         "#3498db",  # blue
        "cached":      "#9b59b6",  # purple
        "llm":         "#bdc3c7",  # light gray — most common, kept neutral
        "refined":     "#e67e22",  # orange
        "human":       "#e94560",  # red
        "ctx_ok":      "#2ecc71",  # green
        "ctx_partial": "#f39c12",  # amber
        "ctx_none":    "#566273",  # muted slate
        "empty":       "#1f2540",  # near-background filler
    }

    def _setup_bar_tags(self):
        """Configure foreground tags on both bar Text widgets."""
        for widget in (self.agg_text, self.ctx_text):
            for key, color in self.BAR_PALETTE.items():
                widget.tag_configure(f"bar_{key}", foreground=color)

    def _build_legend(self, parent_frame, keys):
        """Pack a row of colored chip + text-label pairs into ``parent_frame``.

        Returns a list of (chip_label, text_label, key) tuples so
        ``refresh_text`` can re-localize the text labels later. Chip
        squares stay colored across language switches; only the text
        labels change.
        """
        labels = []
        bg = self.app.BG
        for key in keys:
            color = self.BAR_PALETTE.get(key, "#888")
            # Colored block — use a solid bg label so the patch reads
            # cleanly against the panel background.
            chip = tk.Label(
                parent_frame, text="  ",
                bg=color, fg=color,
                relief="flat", borderwidth=0,
                font=(FONT_FAMILY, 9),
            )
            chip.pack(side="left", padx=(0, 4), ipadx=1, ipady=0)
            text_lbl = tk.Label(
                parent_frame, text="",
                fg="#9aa0b0", bg=bg,
                font=(FONT_FAMILY, 9),
            )
            text_lbl.pack(side="left", padx=(0, 12))
            labels.append((chip, text_lbl, key))
        return labels

    def _insert_colored_bar(self, widget, segments, total, width=_BAR_WIDTH):
        """Insert a stacked bar of █ characters into ``widget``.

        ``segments`` is an iterable of (count, palette_key) tuples in the
        visual order they should appear. Remaining cells get the ``empty``
        tag so the bar always occupies exactly ``width`` columns (keeps
        right-hand columns aligned in monospace).
        """
        if not total:
            widget.insert("end", _BAR_LIGHT * width, "bar_empty")
            return
        remaining = width
        for count, key in segments:
            if count <= 0 or remaining <= 0:
                continue
            chunk = int(round(count / total * width))
            chunk = min(chunk, remaining)
            if chunk <= 0:
                continue
            widget.insert("end", _BAR_BLOCK * chunk, f"bar_{key}")
            remaining -= chunk
        if remaining > 0:
            widget.insert("end", _BAR_LIGHT * remaining, "bar_empty")

    # ------------------------------------------------------------------
    # Render: aggregate panels
    # ------------------------------------------------------------------
    def _render_aggregates(self, cases):
        # Group counts by target_language
        # source_counts[lang][key] = count
        source_counts = defaultdict(lambda: {k: 0 for k in self.SOURCE_KEYS})
        ctx_counts = defaultdict(lambda: {k: 0 for k in self.CONTEXT_KEYS})
        for case in cases:
            lang = case.get("target_language") or "?"
            source_counts[lang][_classify_source(case)] += 1
            ctx_counts[lang][_classify_context(case)] += 1

        self.agg_text.configure(state="normal")
        self.agg_text.delete("1.0", "end")
        if not source_counts:
            self.agg_text.insert("end", self._t("tci_agg_no_data") + "\n")
        else:
            header = (
                f"{'Language':<10} {'Bar':<{_BAR_WIDTH}}  "
                f"{self._t('tci_badge_tm'):>4} "
                f"{self._t('tci_badge_ice'):>4} "
                f"{self._t('tci_badge_cached'):>6} "
                f"{self._t('tci_badge_llm'):>5} "
                f"{self._t('tci_badge_refined'):>7} "
                f"{self._t('tci_badge_human'):>5}  "
                f"{self._t('tci_agg_total'):>8}\n"
            )
            self.agg_text.insert("end", header)
            self.agg_text.insert("end", "─" * (len(header) + 4) + "\n")
            for lang in sorted(source_counts.keys()):
                buckets = source_counts[lang]
                total = sum(buckets.values())
                self.agg_text.insert("end", f"{lang:<10} ")
                self._insert_colored_bar(
                    self.agg_text,
                    [(buckets[k], k) for k in self.SOURCE_KEYS],
                    total,
                )
                tail = (
                    f"  {buckets['tm']:>4} "
                    f"{buckets['ice']:>4} "
                    f"{buckets['cached']:>6} "
                    f"{buckets['llm']:>5} "
                    f"{buckets['refined']:>7} "
                    f"{buckets['human']:>5}  "
                    f"{total:>8}\n"
                )
                self.agg_text.insert("end", tail)
        self.agg_text.configure(state="disabled")

        # Context panel
        self.ctx_text.configure(state="normal")
        self.ctx_text.delete("1.0", "end")
        if not ctx_counts:
            self.ctx_text.insert("end", self._t("tci_agg_no_data") + "\n")
        else:
            header = (
                f"{'Language':<10} {'Bar':<{_BAR_WIDTH}}  "
                f"{self._t('tci_badge_ctx_ok'):>10} "
                f"{'partial':>8} "
                f"{self._t('tci_badge_ctx_none'):>8}  "
                f"{self._t('tci_agg_total'):>8}\n"
            )
            self.ctx_text.insert("end", header)
            self.ctx_text.insert("end", "─" * (len(header) + 4) + "\n")
            for lang in sorted(ctx_counts.keys()):
                buckets = ctx_counts[lang]
                total = sum(buckets.values())
                self.ctx_text.insert("end", f"{lang:<10} ")
                self._insert_colored_bar(
                    self.ctx_text,
                    [(buckets[k], k) for k in self.CONTEXT_KEYS],
                    total,
                )
                tail = (
                    f"  {buckets['ctx_ok']:>10} "
                    f"{buckets['ctx_partial']:>8} "
                    f"{buckets['ctx_none']:>8}  "
                    f"{total:>8}\n"
                )
                self.ctx_text.insert("end", tail)
        self.ctx_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Render: row table
    # ------------------------------------------------------------------
    def _render_rows(self, cases):
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Show at most 500 rows to keep the table responsive.
        max_rows = 500
        for i, case in enumerate(cases[:max_rows], start=1):
            source_key = _classify_source(case)
            ctx_key = _classify_context(case)
            badges = []
            # Source badges (mutually exclusive, but show TM/ICE/Cached even
            # if iteration shows refinement happened on top).
            if case.get("tm_match"):
                badges.append(self._t("tci_badge_tm"))
            if case.get("ice_match"):
                badges.append(self._t("tci_badge_ice"))
            if case.get("cached"):
                badges.append(self._t("tci_badge_cached"))
            if source_key == "llm":
                badges.append(self._t("tci_badge_llm"))
            if (case.get("iteration") or 0) >= 2:
                badges.append(f"{self._t('tci_badge_refined')}×{case['iteration']}")
            if case.get("fixed_by_lead"):
                badges.append(self._t("tci_badge_human"))
            # Context badge
            if ctx_key == "ctx_ok":
                badges.append(self._t("tci_badge_ctx_ok"))
            elif ctx_key == "ctx_none":
                badges.append(self._t("tci_badge_ctx_none"))
            else:
                badges.append("Ctx◐")

            source_text = (case.get("source_text") or "").replace("\n", " ")
            if len(source_text) > 60:
                source_text = source_text[:57] + "…"

            score = case.get("final_score")
            score_str = str(score) if score is not None else "—"

            self.tree.insert(
                "", "end",
                values=(
                    i,
                    case.get("_project_id", "")[:18],
                    case.get("_mr_iid", ""),
                    case.get("target_language", ""),
                    (case.get("opus_id") or "")[:24],
                    source_text,
                    "  ".join(badges),
                    score_str,
                ),
                tags=(case.get("translation_id") or "",
                      case.get("context_id") or ""),
            )

    # ------------------------------------------------------------------
    # Render: sidebar stats
    # ------------------------------------------------------------------
    def _render_sidebar(self, cases):
        total = len(cases)
        buckets = {k: 0 for k in self.SOURCE_KEYS}
        ctx_ok = 0
        ctx_none = 0
        for case in cases:
            buckets[_classify_source(case)] += 1
            ck = _classify_context(case)
            if ck == "ctx_ok":
                ctx_ok += 1
            elif ck == "ctx_none":
                ctx_none += 1

        self.stat_labels["total"][1].configure(text=str(total))
        self.stat_labels["tm"][1].configure(text=str(buckets["tm"]))
        self.stat_labels["ice"][1].configure(text=str(buckets["ice"]))
        self.stat_labels["cached"][1].configure(text=str(buckets["cached"]))
        self.stat_labels["llm"][1].configure(text=str(buckets["llm"]))
        self.stat_labels["refined"][1].configure(text=str(buckets["refined"]))
        self.stat_labels["human"][1].configure(text=str(buckets["human"]))
        self.stat_labels["ctx_ok"][1].configure(text=str(ctx_ok))
        self.stat_labels["ctx_none"][1].configure(text=str(ctx_none))

    # ------------------------------------------------------------------
    # Context drawer
    # ------------------------------------------------------------------
    def _on_row_double_click(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        tags = self.tree.item(sel[0], "tags")
        context_id = tags[1] if len(tags) >= 2 else ""

        # Find the matching case (by translation_id in tag[0])
        translation_id = tags[0] if tags else ""
        case = None
        for c in self._cases:
            if c.get("translation_id") == translation_id:
                case = c
                break

        self._show_context_drawer(case, context_id)

    def _show_context_drawer(self, case, context_id):
        win = tk.Toplevel(self.parent)
        win.title(self._t("tci_drawer_title"))
        win.geometry("780x560")
        try:
            win.configure(bg=self.app.BG)
        except Exception:
            pass

        header = ttk.Frame(win, style="Card.TFrame")
        header.pack(fill="x", padx=12, pady=(12, 6))

        if case:
            meta = (
                f"{case.get('_project_id') or ''}  "
                f"MR!{case.get('_mr_iid') or ''}  ·  "
                f"{case.get('target_language') or ''}  ·  "
                f"{(case.get('opus_id') or '')[:40]}"
            )
            ttk.Label(header, text=meta, style="CardBold.TLabel",
                      font=(FONT_FAMILY, 10, "bold")).pack(anchor="w")
            src = (case.get("source_text") or "").strip()
            tgt = (case.get("translated_text") or "").strip()
            ttk.Label(header, text=f"EN: {src[:300]}", style="Card.TLabel",
                      wraplength=720, justify="left").pack(anchor="w", pady=(4, 0))
            ttk.Label(header, text=f"→ : {tgt[:300]}", style="Card.TLabel",
                      wraplength=720, justify="left").pack(anchor="w")

        body = tk.Text(
            win, font=(FONT_MONO, 10),
            bg="#0a0a1a", fg="#e4e7ef",
            relief="flat", borderwidth=0,
            wrap="word",
        )
        body.pack(fill="both", expand=True, padx=12, pady=8)

        btn_close = self.app._create_button(
            win, text=self._t("tci_drawer_close"),
            command=win.destroy,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4,
        )
        btn_close.pack(pady=(0, 12))

        if not context_id:
            body.insert("end", self._t("tci_drawer_no_id"))
            body.configure(state="disabled")
            return

        body.insert("end", self._t("tci_drawer_loading"))
        body.configure(state="disabled")

        def _load_ctx():
            try:
                payload = _fetch_context_record(context_id)
                pretty = json.dumps(payload, indent=2, ensure_ascii=False)
                win.after(0, lambda: self._fill_drawer(body, pretty))
            except _UnsupportedContextIdError:
                win.after(0, lambda: self._fill_drawer(
                    body, self._t("tci_drawer_unsupported_id").format(cid=context_id)))
            except Exception as e:
                err = str(e)[:160]
                win.after(0, lambda: self._fill_drawer(
                    body, self._t("tci_drawer_error").format(err=err)))

        threading.Thread(target=_load_ctx, daemon=True).start()

    def _fill_drawer(self, body_widget, text):
        body_widget.configure(state="normal")
        body_widget.delete("1.0", "end")
        body_widget.insert("end", text)
        body_widget.configure(state="disabled")

    # ------------------------------------------------------------------
    # Help modal
    # ------------------------------------------------------------------
    def _show_help(self):
        """Open a self-contained help window with the panel cheat-sheet.

        Content is hard-coded into STRINGS (per language), so this works
        even after PyInstaller packaging where the markdown sibling file
        may not be present.
        """
        win = tk.Toplevel(self.parent)
        win.title(self._t("tci_help_title"))
        win.geometry("760x600")
        try:
            win.configure(bg=self.app.BG)
        except Exception:
            pass

        header = ttk.Frame(win, style="Card.TFrame")
        header.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Label(
            header,
            text=self._t("tci_help_title"),
            style="CardBold.TLabel",
            font=(FONT_FAMILY, 12, "bold"),
        ).pack(anchor="w")

        body = tk.Text(
            win, font=(FONT_MONO, 10),
            bg="#0a0a1a", fg="#e4e7ef",
            relief="flat", borderwidth=0,
            wrap="word", padx=12, pady=8,
        )
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        body.insert("end", self._t("tci_help_body"))
        body.configure(state="disabled")

        footer = ttk.Frame(win, style="Card.TFrame")
        footer.pack(fill="x", padx=12, pady=(0, 12))

        ttk.Label(
            footer,
            text=self._t("tci_help_full_guide_hint"),
            style="Status.TLabel",
        ).pack(side="left")

        btn_close = self.app._create_button(
            footer, text=self._t("tci_help_close"),
            command=win.destroy,
            style_name="SecondarySmall",
            font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4,
        )
        btn_close.pack(side="right")
