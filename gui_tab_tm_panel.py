"""
TM 面板 — GUI Tab
=================
三层翻译记忆可视化 + 多维检索：ICE TM、Shared TM（流水线溯源）、
Tranzor 记录。按逻辑键聚合、按 path_hash 分层，对应
``opus-id-tm-guide.html`` 里原系统没有的检索能力。

数据 / 逻辑层在 :mod:`tm_panel`（无 Tk 依赖、可单测）；本模块只做 UI。
纯加法：失败只让本 tab 消失，不拖垮应用。
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

STRINGS = {
    "en": {
        "tab_tm_panel": "📚 TM Panel",
        "tmp_hint": (
            "Search ICE TM, Shared TM provenance, and Tranzor records as three "
            "separate layers. Paste any OPUS ID — the default Ignore-hash mode "
            "drops the path hash and finds every identity of the same logical "
            "key. Newest-record ≠ current-branch ID; confirm that via Bug Fix "
            "→ Blob-based Fix."
        ),
        "tmp_query": "OPUS ID / key",
        "tmp_match": "Match",
        "tmp_match_ignore_hash": "Ignore hash",
        "tmp_match_fuzzy": "Fuzzy",
        "tmp_match_exact": "Exact",
        "tmp_source": "Source contains",
        "tmp_translation": "Translation contains",
        "tmp_product": "Product",
        "tmp_lang": "Language",
        "tmp_channel": "Channel",
        "tmp_channel_all": "All",
        "tmp_channel_mr": "MR",
        "tmp_channel_file": "File",
        "tmp_layer_ice": "ICE TM",
        "tmp_layer_shared": "Shared TM",
        "tmp_layer_records": "Tranzor records",
        "tmp_search": "🔎 Search",
        "tmp_clear": "Clear",
        "tmp_copy": "Copy handoff",
        "tmp_help": "❓ Help",
        "tmp_any": "(any)",
        "tmp_kpi_ice": "ICE TM store",
        "tmp_kpi_shared": "Shared TM provenance",
        "tmp_kpi_records": "Tranzor records",
        "tmp_kpi_ice_n": "{hits} hit / {miss} miss",
        "tmp_kpi_shared_n": "{n} TM-tagged rows",
        "tmp_kpi_records_n": "{rows} rows · {keys} keys · {hashes} hashes",
        "tmp_anatomy": "OPUS anatomy",
        "tmp_col_group": "Logical key / hash / language",
        "tmp_col_layers": "Layers",
        "tmp_col_source": "Source",
        "tmp_col_translation": "Translation",
        "tmp_col_task": "Task",
        "tmp_col_created": "Created",
        "tmp_family": "{alias}  ·  {key}   ({hashes} hashes · {rows} rows)",
        "tmp_hash": "{hash}   {role}",
        "tmp_role_newest": "newest record",
        "tmp_role_pasted": "pasted ID",
        "tmp_role_pasted_newest": "pasted · newest record",
        "tmp_role_other": "other hash",
        "tmp_ice_line": "ICE {type}  ·  {n} langs",
        "tmp_status_ready": "Ready",
        "tmp_status_searching": "Searching three TM layers…",
        "tmp_need_filter": (
            "Enter at least one of: OPUS ID / key / source / translation / product."
        ),
        "tmp_ice_needs_source_or_query": (
            "ICE-only search needs a source string or an OPUS ID."
        ),
        "tmp_empty": "No rows in the selected layers.",
        "tmp_done": (
            "{keys} logical key(s) · {hashes} hash variant(s) · "
            "{ice} ICE store hit(s) · {shared} Shared TM row(s)"
        ),
        "tmp_failed": "Search failed: {error}",
        "tmp_copied": "Handoff copied.",
        "tmp_copy_empty": "Search first, then copy.",
        "tmp_warn_current": (
            "Newest-record ID is not proof of the target-branch key."
        ),
        "tmp_warn_short": (
            "Short strings (≤2 words) need an exact source_id in Shared TM "
            "and ICE rule 1 — a hash change misses."
        ),
        "tmp_warn_hashes": "Same logical key has multiple path hashes.",
        "tmp_warn_ice_miss": "ICE TM returned no match for the probed items.",
        "tmp_warn_ice_content": (
            "ICE hit was content-only (rule 3, >3 words), not an ID match."
        ),
        "tmp_layer_status": "{layer}: {status}",
        "tmp_help_title": "TM Panel — Help",
        "tmp_help_close": "Close",
        "tmp_help_body": (
            "THREE LAYERS (the platform has no combined view)\n"
            "───────────────────────────────────────────────\n"
            "ICE TM     Live probe of context-service /translation-memory/match.\n"
            "           Rule 1 = OPUS ID + source CRC. Rule 3 = content-only\n"
            "           Exact Match when the source has more than 3 words.\n"
            "Shared TM  No public search API. This column shows pipeline\n"
            "           provenance (tm_match on MR cases) and File rows whose\n"
            "           translation_type is TM — not a dump of lint_*_pair.\n"
            "Records    GET /translations/search (MR + File). Ignore-hash\n"
            "           searches the logical key so every hash variant appears.\n"
            "\n"
            "IGNORE HASH (default)\n"
            "─────────────────────\n"
            "Paste a full OPUS ID. The panel keeps alias + logical key and\n"
            "throws away the 32-hex path hash, then fuzzy-searches records.\n"
            "Results group by hash. Newest-record is the latest created_at,\n"
            "NOT the current-branch ID. Confirm that with Bug Fix →\n"
            "Blob-based Fix / Legacy IDs.\n"
            "\n"
            "SHORT STRINGS\n"
            "─────────────\n"
            "≤2 words: Shared TM and ICE rule 1 require (source_id, source).\n"
            "A path-root or extension change silently misses and the pipeline\n"
            "re-translates via LLM, then writes a new hash.\n"
            "\n"
            "COPY HANDOFF\n"
            "────────────\n"
            "Copies the localization ↔ engineering template (logical key,\n"
            "newest-record ID, legacy IDs). Target commit stays blank until\n"
            "someone confirms it on the target branch.\n"
        ),
        "tmp_detail_placeholder": "Select a row to see source, translation, and ICE store output.",
        "tmp_status_ok": "ok",
        "tmp_status_error": "error",
        "tmp_status_empty": "empty",
        "tmp_status_skipped": "skipped",
        "tmp_status_unavailable": "unavailable",
        "tmp_status_no_probe": "no probe (need source)",
        "tmp_status_no_api": "no public search API",
    },
    "zh": {
        "tab_tm_panel": "📚 TM 面板",
        "tmp_hint": (
            "把 ICE TM、Shared TM 溯源、Tranzor 记录分成三层检索。"
            "粘贴任意完整 OPUS ID，默认「忽略 Hash」会丢掉路径 Hash、"
            "按逻辑键找出所有身份变体。最新记录 ≠ 目标分支当前 ID，"
            "请用 Bug Fix → Blob-based Fix 裁决。"
        ),
        "tmp_query": "OPUS ID / 逻辑键",
        "tmp_match": "匹配",
        "tmp_match_ignore_hash": "忽略 Hash",
        "tmp_match_fuzzy": "模糊",
        "tmp_match_exact": "精确",
        "tmp_source": "源文包含",
        "tmp_translation": "译文包含",
        "tmp_product": "产品",
        "tmp_lang": "语言",
        "tmp_channel": "通道",
        "tmp_channel_all": "全部",
        "tmp_channel_mr": "MR",
        "tmp_channel_file": "文件",
        "tmp_layer_ice": "ICE TM",
        "tmp_layer_shared": "Shared TM",
        "tmp_layer_records": "Tranzor 记录",
        "tmp_search": "🔎 搜索",
        "tmp_clear": "清空",
        "tmp_copy": "复制交接",
        "tmp_help": "❓ 说明",
        "tmp_any": "(全部)",
        "tmp_kpi_ice": "ICE TM 库",
        "tmp_kpi_shared": "Shared TM 溯源",
        "tmp_kpi_records": "Tranzor 记录",
        "tmp_kpi_ice_n": "命中 {hits} / 未中 {miss}",
        "tmp_kpi_shared_n": "{n} 条 TM 标记",
        "tmp_kpi_records_n": "{rows} 行 · {keys} 个逻辑键 · {hashes} 个 Hash",
        "tmp_anatomy": "OPUS 解剖",
        "tmp_col_group": "逻辑键 / Hash / 语言",
        "tmp_col_layers": "层",
        "tmp_col_source": "源文",
        "tmp_col_translation": "译文",
        "tmp_col_task": "任务",
        "tmp_col_created": "创建时间",
        "tmp_family": "{alias}  ·  {key}   ({hashes} 个 Hash · {rows} 行)",
        "tmp_hash": "{hash}   {role}",
        "tmp_role_newest": "最新记录",
        "tmp_role_pasted": "粘贴的 ID",
        "tmp_role_pasted_newest": "粘贴 · 最新记录",
        "tmp_role_other": "其他 Hash",
        "tmp_ice_line": "ICE {type}  ·  {n} 个语种",
        "tmp_status_ready": "就绪",
        "tmp_status_searching": "正在检索三层 TM…",
        "tmp_need_filter": "请至少填一个：OPUS ID / 逻辑键 / 源文 / 译文 / 产品。",
        "tmp_ice_needs_source_or_query": "仅搜 ICE 时需要源文或 OPUS ID。",
        "tmp_empty": "所选层没有结果。",
        "tmp_done": (
            "{keys} 个逻辑键 · {hashes} 个 Hash 变体 · "
            "ICE 库命中 {ice} · Shared TM {shared} 条"
        ),
        "tmp_failed": "搜索失败：{error}",
        "tmp_copied": "已复制交接模板。",
        "tmp_copy_empty": "请先搜索再复制。",
        "tmp_warn_current": "最新记录 ID 不能当作目标分支当前 Key。",
        "tmp_warn_short": (
            "短句（≤2 词）在 Shared TM 与 ICE 规则 1 都要求 source_id 精确；"
            "Hash 一变即失配。"
        ),
        "tmp_warn_hashes": "同一逻辑键出现了多个路径 Hash。",
        "tmp_warn_ice_miss": "ICE TM 对探测项返回未命中。",
        "tmp_warn_ice_content": "ICE 命中来自规则 3（>3 词按内容），不是 ID 命中。",
        "tmp_layer_status": "{layer}：{status}",
        "tmp_help_title": "TM 面板 — 说明",
        "tmp_help_close": "关闭",
        "tmp_help_body": (
            "三层记忆（原系统没有合并视图）\n"
            "────────────────────────────\n"
            "ICE TM     实时探测 context-service /translation-memory/match。\n"
            "           规则 1 = OPUS ID + 源文 CRC。规则 3 = 源文超过 3 个词\n"
            "           时可按内容 Exact Match。\n"
            "Shared TM  没有公开搜索 API。本列展示流水线写回记录表时的\n"
            "           tm_match 溯源，以及 File Translation 里 translation_type\n"
            "           = TM 的行——不是 lint_*_pair 库表本身。\n"
            "记录层     GET /translations/search（MR + 文件）。忽略 Hash 模式\n"
            "           按逻辑键搜，所有 Hash 变体一次出现。\n"
            "\n"
            "忽略 Hash（默认）\n"
            "────────────────\n"
            "粘贴完整 OPUS ID，面板只保留 alias + 逻辑键，丢掉 32 位路径 Hash，\n"
            "再对记录表做模糊搜索。结果按 Hash 分组。「最新记录」是 created_at\n"
            "最新的那条，不是目标分支当前 ID。请用 Bug Fix → Blob-based Fix /\n"
            "Legacy IDs 裁决。\n"
            "\n"
            "短句\n"
            "────\n"
            "≤2 个词：Shared TM 与 ICE 规则 1 都要求 (source_id, source) 精确。\n"
            "路径根或扩展名一变就会失配，流水线走 LLM 再以新 Hash 落库。\n"
            "\n"
            "复制交接\n"
            "────────\n"
            "复制本地化 ↔ 开发交接模板（逻辑键、最新记录 ID、历史 ID）。\n"
            "Target commit 留空，直到有人在目标分支上确认。\n"
        ),
        "tmp_detail_placeholder": "选中一行可查看源文、译文和 ICE 库返回。",
        "tmp_status_ok": "正常",
        "tmp_status_error": "出错",
        "tmp_status_empty": "空",
        "tmp_status_skipped": "跳过",
        "tmp_status_unavailable": "不可用",
        "tmp_status_no_probe": "未探测（需要源文）",
        "tmp_status_no_api": "无公开搜索 API",
    },
}

from export_gui import FONT_FAMILY, FONT_MONO  # noqa: E402
from time_display import format_display_datetime  # noqa: E402

import tm_panel as tm  # noqa: E402

_LANG_CHOICES = [
    "de-DE", "en-AU", "en-GB", "en-US", "es-ES", "es-419", "es-MX",
    "fr-FR", "fr-CA", "it-IT", "nl-NL", "pt-BR", "pt-PT", "fi-FI",
    "ko-KR", "ja-JP", "zh-CN", "zh-TW", "zh-HK",
]

_MATCH_KEYS = ("ignore_hash", "fuzzy", "exact")
_CHANNEL_KEYS = ("all", "mr", "file")
_WARN_KEYS = {
    "current_id_unverified": "tmp_warn_current",
    "short_string_id_sensitive": "tmp_warn_short",
    "multiple_hashes": "tmp_warn_hashes",
    "ice_no_hit": "tmp_warn_ice_miss",
    "ice_content_only": "tmp_warn_ice_content",
}
_STATUS_KEYS = {
    "ok": "tmp_status_ok",
    "error": "tmp_status_error",
    "empty": "tmp_status_empty",
    "skipped": "tmp_status_skipped",
    "unavailable": "tmp_status_unavailable",
    "no_probe": "tmp_status_no_probe",
    "no_api": "tmp_status_no_api",
}


def _clip(text: str, n: int = 72) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


class TmPanelTab:
    """Three-layer TM search panel."""

    _COLS = ("layers", "source", "translation", "task", "created")

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self._first_shown = False
        self._searching = False
        self._view = None
        self._row_data: dict[str, dict] = {}
        self._build(parent)
        self.refresh_text()

    def _t(self, key):
        return self.app._t(key)

    def _base_url(self) -> str:
        import export_mr_pipeline as mr_api
        return mr_api.TRANZOR_URL

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------
    def _build(self, parent):
        content = ttk.Frame(parent, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=16, pady=8)

        self.lbl_hint = ttk.Label(
            content, text="", style="Status.TLabel", wraplength=1100,
            justify="left")
        self.lbl_hint.pack(fill="x", pady=(0, 8))

        form = ttk.Frame(content, style="App.TFrame")
        form.pack(fill="x", pady=(0, 6))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        self.lbl_query = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_query.grid(row=0, column=0, sticky="e", padx=(0, 6), pady=3)
        self.var_query = tk.StringVar()
        self.ent_query = ttk.Entry(form, textvariable=self.var_query)
        self.ent_query.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=3)
        self.ent_query.bind("<Return>", lambda _e: self._on_search())
        self.lbl_match = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_match.grid(row=0, column=2, sticky="e", padx=(0, 6), pady=3)
        self.var_match = tk.StringVar(value="ignore_hash")
        self.cmb_match = ttk.Combobox(
            form, textvariable=self.var_match, state="readonly", width=16)
        self.cmb_match.grid(row=0, column=3, sticky="w", pady=3)

        self.lbl_source = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_source.grid(row=1, column=0, sticky="e", padx=(0, 6), pady=3)
        self.var_source = tk.StringVar()
        self.ent_source = ttk.Entry(form, textvariable=self.var_source)
        self.ent_source.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=3)
        self.ent_source.bind("<Return>", lambda _e: self._on_search())
        self.lbl_translation = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_translation.grid(row=1, column=2, sticky="e", padx=(0, 6), pady=3)
        self.var_translation = tk.StringVar()
        self.ent_translation = ttk.Entry(form, textvariable=self.var_translation)
        self.ent_translation.grid(row=1, column=3, sticky="ew", pady=3)
        self.ent_translation.bind("<Return>", lambda _e: self._on_search())

        self.lbl_product = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_product.grid(row=2, column=0, sticky="e", padx=(0, 6), pady=3)
        self.var_product = tk.StringVar()
        self.cmb_product = ttk.Combobox(form, textvariable=self.var_product)
        self.cmb_product.grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=3)
        self.lbl_lang = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_lang.grid(row=2, column=2, sticky="e", padx=(0, 6), pady=3)
        self.var_lang = tk.StringVar()
        self.cmb_lang = ttk.Combobox(
            form, textvariable=self.var_lang, values=[""] + _LANG_CHOICES,
            width=16)
        self.cmb_lang.grid(row=2, column=3, sticky="w", pady=3)

        bar = ttk.Frame(content, style="App.TFrame")
        bar.pack(fill="x", pady=(0, 6))

        self.lbl_channel = ttk.Label(bar, text="", style="Status.TLabel")
        self.lbl_channel.pack(side="left")
        self.var_channel = tk.StringVar(value="all")
        self.cmb_channel = ttk.Combobox(
            bar, textvariable=self.var_channel, state="readonly", width=10)
        self.cmb_channel.pack(side="left", padx=(6, 12))

        self.var_ice = tk.BooleanVar(value=True)
        self.var_shared = tk.BooleanVar(value=True)
        self.var_records = tk.BooleanVar(value=True)
        self.chk_ice = ttk.Checkbutton(bar, variable=self.var_ice)
        self.chk_ice.pack(side="left", padx=(0, 8))
        self.chk_shared = ttk.Checkbutton(bar, variable=self.var_shared)
        self.chk_shared.pack(side="left", padx=(0, 8))
        self.chk_records = ttk.Checkbutton(bar, variable=self.var_records)
        self.chk_records.pack(side="left", padx=(0, 12))

        self.btn_search = self.app._create_button(
            bar, text="", command=self._on_search, style_name="AccentSmall",
            font=(FONT_FAMILY, 10, "bold"), bg="#e94560", fg="#fff",
            padx=16, pady=4)
        self.btn_search.pack(side="left")
        self.btn_clear = self.app._create_button(
            bar, text="", command=self._on_clear, style_name="SecondarySmall",
            font=(FONT_FAMILY, 10), bg="#0f3460", fg="#ccc", padx=12, pady=4)
        self.btn_clear.pack(side="left", padx=(6, 0))
        self.btn_copy = self.app._create_button(
            bar, text="", command=self._on_copy, style_name="SecondarySmall",
            font=(FONT_FAMILY, 10), bg="#0f3460", fg="#ccc", padx=12, pady=4)
        self.btn_copy.pack(side="left", padx=(6, 0))
        self.btn_help = self.app._create_button(
            bar, text="", command=self._on_help, style_name="SecondarySmall",
            font=(FONT_FAMILY, 10), bg="#0f3460", fg="#ccc", padx=12, pady=4)
        self.btn_help.pack(side="left", padx=(6, 0))
        self.lbl_status = ttk.Label(bar, text="", style="Status.TLabel")
        self.lbl_status.pack(side="left", padx=(14, 0))

        kpi = ttk.Frame(content, style="App.TFrame")
        kpi.pack(fill="x", pady=(0, 6))
        self._kpi = {}
        for key in ("ice", "shared", "records"):
            card = ttk.Frame(kpi, style="Card.TFrame")
            card.pack(side="left", fill="x", expand=True, padx=(0, 8))
            title = ttk.Label(card, text="", style="CardBold.TLabel")
            title.pack(anchor="w", padx=10, pady=(8, 0))
            value = ttk.Label(card, text="—", style="Card.TLabel")
            value.pack(anchor="w", padx=10, pady=(0, 8))
            self._kpi[key] = (title, value)

        self.lbl_anatomy = ttk.Label(content, text="", style="Status.TLabel",
                                     wraplength=1100, justify="left")
        self.lbl_anatomy.pack(fill="x", pady=(0, 4))
        self.lbl_warn = ttk.Label(content, text="", style="Status.TLabel",
                                  wraplength=1100, justify="left")
        self.lbl_warn.pack(fill="x", pady=(0, 6))

        tree_frame = ttk.Frame(content, style="App.TFrame")
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame, columns=self._COLS, show="tree headings",
            style="Summary.Treeview", selectmode="browse", height=14)
        self.tree.column("#0", width=380, anchor="w", stretch=True)
        widths = {"layers": 110, "source": 220, "translation": 220,
                  "task": 140, "created": 150}
        for col in self._COLS:
            self.tree.column(col, width=widths.get(col, 100), anchor="w")
        self.tree.tag_configure("family", foreground="#e7ecff")
        self.tree.tag_configure("newest", foreground="#86efac")
        self.tree.tag_configure("pasted", foreground="#fbbf24")
        self.tree.tag_configure("other", foreground="#fca5a5")
        self.tree.tag_configure("ice", foreground="#7dd3fc")
        self.tree.tag_configure("row", foreground="#d1d5db")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_select)

        self.detail = tk.Text(
            content, wrap="word", height=6, bg="#0a0a1a", fg="#e4e7ef",
            insertbackground="#fff", relief="flat", font=(FONT_MONO, 9),
            padx=8, pady=6)
        self.detail.pack(fill="x", pady=(6, 0))
        self.detail.configure(state="disabled")

    # ------------------------------------------------------------------
    # i18n
    # ------------------------------------------------------------------
    def refresh_text(self):
        t = self._t
        self.lbl_hint.configure(text=t("tmp_hint"))
        self.lbl_query.configure(text=t("tmp_query"))
        self.lbl_match.configure(text=t("tmp_match"))
        self.lbl_source.configure(text=t("tmp_source"))
        self.lbl_translation.configure(text=t("tmp_translation"))
        self.lbl_product.configure(text=t("tmp_product"))
        self.lbl_lang.configure(text=t("tmp_lang"))
        self.lbl_channel.configure(text=t("tmp_channel"))
        self.chk_ice.configure(text=t("tmp_layer_ice"))
        self.chk_shared.configure(text=t("tmp_layer_shared"))
        self.chk_records.configure(text=t("tmp_layer_records"))
        self.btn_search.configure(text=t("tmp_search"))
        self.btn_clear.configure(text=t("tmp_clear"))
        self.btn_copy.configure(text=t("tmp_copy"))
        self.btn_help.configure(text=t("tmp_help"))
        self._kpi["ice"][0].configure(text=t("tmp_kpi_ice"))
        self._kpi["shared"][0].configure(text=t("tmp_kpi_shared"))
        self._kpi["records"][0].configure(text=t("tmp_kpi_records"))

        match_labels = [t(f"tmp_match_{k}") for k in _MATCH_KEYS]
        raw_match = self._match_raw()
        self.cmb_match.configure(values=match_labels)
        self.var_match.set(t(f"tmp_match_{raw_match}"))

        ch_labels = [t(f"tmp_channel_{k}") for k in _CHANNEL_KEYS]
        raw_ch = self._channel_raw()
        self.cmb_channel.configure(values=ch_labels)
        self.var_channel.set(t(f"tmp_channel_{raw_ch}"))

        self.tree.heading("#0", text=t("tmp_col_group"))
        self.tree.heading("layers", text=t("tmp_col_layers"))
        self.tree.heading("source", text=t("tmp_col_source"))
        self.tree.heading("translation", text=t("tmp_col_translation"))
        self.tree.heading("task", text=t("tmp_col_task"))
        self.tree.heading("created", text=t("tmp_col_created"))
        if not self._view:
            self._set_detail(t("tmp_detail_placeholder"))

    def _match_raw(self) -> str:
        val = (self.var_match.get() or "").strip()
        mapping = {
            self._t("tmp_match_ignore_hash"): "ignore_hash",
            self._t("tmp_match_fuzzy"): "fuzzy",
            self._t("tmp_match_exact"): "exact",
            "ignore_hash": "ignore_hash",
            "fuzzy": "fuzzy",
            "exact": "exact",
            "Ignore hash": "ignore_hash",
            "Fuzzy": "fuzzy",
            "Exact": "exact",
            "忽略 Hash": "ignore_hash",
            "模糊": "fuzzy",
            "精确": "exact",
        }
        return mapping.get(val, "ignore_hash")

    def _channel_raw(self) -> str:
        val = (self.var_channel.get() or "").strip()
        mapping = {
            self._t("tmp_channel_all"): "all",
            self._t("tmp_channel_mr"): "mr",
            self._t("tmp_channel_file"): "file",
            "all": "all", "mr": "mr", "file": "file",
            "All": "all", "MR": "mr", "File": "file",
            "全部": "all", "文件": "file",
        }
        return mapping.get(val, "all")

    def _role_label(self, role: str) -> str:
        if role == "pasted+newest":
            return self._t("tmp_role_pasted_newest")
        if "newest" in (role or "") and "pasted" in (role or ""):
            return self._t("tmp_role_pasted_newest")
        if "newest" in (role or ""):
            return self._t("tmp_role_newest")
        if "pasted" in (role or ""):
            return self._t("tmp_role_pasted")
        return self._t("tmp_role_other")

    def _status_label(self, raw: str) -> str:
        key = _STATUS_KEYS.get(raw or "", "")
        return self._t(key) if key else (raw or "")

    # ------------------------------------------------------------------
    def _busy(self, text):
        try:
            self.app._mark_busy(self.lbl_status, text)
        except Exception:
            try:
                self.lbl_status.configure(text=text)
            except Exception:
                pass

    def _idle(self, text=""):
        try:
            self.app._mark_idle(self.lbl_status, text)
        except Exception:
            try:
                self.lbl_status.configure(text=text)
            except Exception:
                pass

    def on_first_show(self):
        if self._first_shown:
            return
        self._first_shown = True
        self._idle(self._t("tmp_status_ready"))

        def _work():
            aliases = self._load_aliases()
            try:
                self.parent.after(0, lambda: self._apply_aliases(aliases))
            except Exception:
                pass
        threading.Thread(
            target=_work, daemon=True, name="tm-panel-aliases").start()

    def _load_aliases(self) -> list[str]:
        try:
            import opus_id_monitor as om
            om.init_db()
            with om._connect() as conn:
                cur = conn.execute(
                    "SELECT alias, COUNT(*) n FROM opus_index "
                    "WHERE alias IS NOT NULL AND alias <> '' "
                    "GROUP BY alias ORDER BY n DESC")
                return [r["alias"] for r in cur.fetchall()]
        except Exception:
            return []

    def _apply_aliases(self, aliases: list[str]):
        try:
            self.cmb_product.configure(values=[""] + aliases)
        except Exception:
            pass

    def _set_detail(self, text: str):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text or "")
        self.detail.configure(state="disabled")

    # ------------------------------------------------------------------
    def _on_clear(self):
        self.var_query.set("")
        self.var_source.set("")
        self.var_translation.set("")
        self.var_product.set("")
        self.var_lang.set("")
        self.var_ice.set(True)
        self.var_shared.set(True)
        self.var_records.set(True)
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._row_data.clear()
        self._view = None
        self.lbl_anatomy.configure(text="")
        self.lbl_warn.configure(text="")
        for _title, value in self._kpi.values():
            value.configure(text="—")
        self._set_detail(self._t("tmp_detail_placeholder"))
        self._idle(self._t("tmp_status_ready"))

    def _collect_intent(self) -> tm.QueryIntent:
        layers = set()
        if self.var_ice.get():
            layers.add("ice")
        if self.var_shared.get():
            layers.add("shared")
        if self.var_records.get():
            layers.add("records")
        if not layers:
            layers = set(tm.LAYERS)
        return tm.QueryIntent(
            query=self.var_query.get(),
            match_mode=self._match_raw(),
            source_text=self.var_source.get(),
            translated_text=self.var_translation.get(),
            product_line=self.var_product.get(),
            target_language=self.var_lang.get(),
            source_type=self._channel_raw(),
            layers=frozenset(layers),
        )

    def _on_search(self):
        if self._searching:
            return
        intent = self._collect_intent()
        err = tm.validate_intent(intent)
        if err:
            key = {
                "need_filter": "tmp_need_filter",
                "ice_needs_source_or_query": "tmp_ice_needs_source_or_query",
            }.get(err, "tmp_need_filter")
            self._idle(self._t(key))
            return
        self._searching = True
        self._busy(self._t("tmp_status_searching"))
        base_url = self._base_url()

        def _work():
            view = None
            fail = None
            try:
                view = tm.search_tm_layers(
                    intent,
                    search_fn=lambda **kw: tm.default_search_records(
                        base_url=base_url, **kw),
                    ice_fn=lambda items, langs: tm.default_ice_match(
                        items, langs),
                    local_fn=tm.default_local_search,
                    provenance_fn=lambda **kw: tm.default_provenance(
                        base_url=base_url, **kw),
                )
            except Exception as exc:  # noqa: BLE001
                fail = str(exc)[:160]
            try:
                self.parent.after(0, lambda: self._render(view, fail))
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True, name="tm-panel").start()

    def _render(self, view, fail):
        self._searching = False
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._row_data.clear()
        self._view = view
        if fail:
            self._idle(self._t("tmp_failed").format(error=fail))
            return
        if not view:
            self._idle(self._t("tmp_failed").format(error="empty"))
            return
        if not view.get("ok"):
            err = view.get("error") or ""
            key = {
                "need_filter": "tmp_need_filter",
                "ice_needs_source_or_query": "tmp_ice_needs_source_or_query",
            }.get(err, "")
            self._idle(self._t(key) if key else self._t("tmp_failed").format(
                error=err or "error"))
            return

        kpis = view.get("kpis") or {}
        self._kpi["ice"][1].configure(text=self._t("tmp_kpi_ice_n").format(
            hits=kpis.get("ice_store_hits", 0),
            miss=kpis.get("ice_store_misses", 0)))
        self._kpi["shared"][1].configure(text=self._t("tmp_kpi_shared_n").format(
            n=kpis.get("shared_hits", 0)))
        self._kpi["records"][1].configure(text=self._t("tmp_kpi_records_n").format(
            rows=kpis.get("record_rows", 0),
            keys=kpis.get("logical_keys", 0),
            hashes=kpis.get("hash_variants", 0)))

        self.lbl_anatomy.configure(text=self._anatomy_text(view.get("anatomy") or {}))
        warns = [self._t(_WARN_KEYS[w]) for w in (view.get("warnings") or [])
                 if w in _WARN_KEYS]
        status = view.get("status") or {}
        layer_bits = []
        for layer, label_key in (
            ("ice", "tmp_layer_ice"),
            ("shared", "tmp_layer_shared"),
            ("records", "tmp_layer_records"),
        ):
            if layer in ((view.get("intent") or {}).get("layers") or []):
                layer_bits.append(self._t("tmp_layer_status").format(
                    layer=self._t(label_key),
                    status=self._status_label(status.get(layer, "")),
                ))
        extra = []
        errors = view.get("errors") or {}
        for name, msg in errors.items():
            extra.append(f"{name}: {msg}")
        self.lbl_warn.configure(text="  ·  ".join(warns + layer_bits + extra))

        lineages = view.get("lineages") or []
        for family in lineages:
            self._insert_family(family)

        if not lineages:
            self._idle(self._t("tmp_empty"))
        else:
            self._idle(self._t("tmp_done").format(
                keys=kpis.get("logical_keys", 0),
                hashes=kpis.get("hash_variants", 0),
                ice=kpis.get("ice_store_hits", 0),
                shared=kpis.get("shared_hits", 0),
            ))
        self._set_detail(self._t("tmp_detail_placeholder"))

    def _anatomy_text(self, anatomy: dict) -> str:
        t = self._t
        parts = [t("tmp_anatomy") + ":"]
        if anatomy.get("valid"):
            parts.append(
                f"RingCentral .{anatomy.get('alias')}."
                f"{anatomy.get('path_hash')}."
                f"{anatomy.get('logical_key')}"
            )
        elif anatomy.get("raw"):
            parts.append(str(anatomy.get("raw")))
        needle = anatomy.get("needle") or ""
        if needle:
            parts.append(f"needle={needle}")
        hashes = anatomy.get("hashes") or []
        if hashes:
            bits = []
            for item in hashes:
                bits.append(
                    f"{(item.get('path_hash') or '')[:8]}… "
                    f"{self._role_label(item.get('role') or '')}×{item.get('rows') or 0}"
                )
            parts.append(" | ".join(bits))
        return "   ".join(parts)

    def _insert_family(self, family: dict):
        label = self._t("tmp_family").format(
            alias=family.get("alias") or "?",
            key=family.get("logical_key") or "",
            hashes=family.get("hash_count") or 0,
            rows=family.get("row_count") or 0,
        )
        fid = self.tree.insert(
            "", "end", text=label, values=("", "", "", "", ""),
            tags=("family",), open=True)
        self._row_data[fid] = {"kind": "family", "family": family}
        for bucket in family.get("hashes") or []:
            self._insert_hash(fid, family, bucket)

    def _insert_hash(self, parent, family, bucket):
        path_hash = bucket.get("path_hash") or "(no hash)"
        role = bucket.get("role") or "other"
        tag = "newest" if "newest" in role else (
            "pasted" if "pasted" in role else "other")
        label = self._t("tmp_hash").format(
            hash=path_hash, role=self._role_label(role))
        hid = self.tree.insert(
            parent, "end", text=label,
            values=(
                self._hash_layers(bucket),
                "",
                "",
                f"{len(bucket.get('languages') or [])} lang",
                "",
            ),
            tags=(tag,), open=True)
        self._row_data[hid] = {
            "kind": "hash", "family": family, "bucket": bucket,
        }
        layers = ((self._view or {}).get("intent") or {}).get("layers") or []
        for ice in bucket.get("ice") or []:
            if "ice" not in layers:
                break
            if not ice.get("hit"):
                continue
            trans = ice.get("translations") or {}
            iid = self.tree.insert(
                hid, "end",
                text=self._t("tmp_ice_line").format(
                    type=ice.get("match_type") or "ICE",
                    n=len(trans),
                ),
                values=(
                    "ICE TM",
                    _clip(ice.get("source_text") or ""),
                    _clip("; ".join(
                        f"{lang}={_clip(text, 24)}"
                        for lang, text in list(trans.items())[:3]
                    )),
                    ice.get("match_type") or "",
                    "",
                ),
                tags=("ice",),
            )
            self._row_data[iid] = {
                "kind": "ice", "family": family, "bucket": bucket, "ice": ice,
            }
        if "records" not in layers and "shared" not in layers:
            return
        for row in bucket.get("records") or []:
            if "records" not in layers and not (
                "shared" in layers and tm.is_shared_tm_hit(row)
            ):
                continue
            badges = []
            if row.get("tm_match") or row.get("file_tm"):
                badges.append("Shared")
            if row.get("ice_match"):
                badges.append("ICE*")
            if row.get("cached"):
                badges.append("Cache")
            if row.get("fixed_by_lead"):
                badges.append("Lead")
            if row.get("origin") == "local":
                badges.append("local")
            badges.append(str(row.get("source_type") or "rec"))
            rid = self.tree.insert(
                hid, "end",
                text=row.get("target_language") or "(lang)",
                values=(
                    " ".join(badges),
                    _clip(row.get("source_text") or ""),
                    _clip(row.get("translated_text") or ""),
                    _clip(row.get("task_name") or row.get("task_id") or "", 28),
                    format_display_datetime(row.get("created_at") or ""),
                ),
                tags=("row",),
            )
            self._row_data[rid] = {
                "kind": "record", "family": family, "bucket": bucket, "row": row,
            }

    def _hash_layers(self, bucket: dict) -> str:
        bits = []
        if any(h.get("hit") for h in (bucket.get("ice") or [])):
            bits.append("ICE")
        if bucket.get("shared_hits"):
            bits.append("Shared")
        if bucket.get("records"):
            bits.append("Rec")
        return " ".join(bits)

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        data = self._row_data.get(sel[0]) or {}
        kind = data.get("kind")
        if kind == "record":
            row = data.get("row") or {}
            lines = [
                f"OPUS ID:  {row.get('opus_id') or ''}",
                f"Hash:     {row.get('path_hash') or ''}   "
                f"({self._role_label((data.get('bucket') or {}).get('role') or '')})",
                f"Logical:  {row.get('logical_key') or ''}",
                f"Channel:  {row.get('source_type') or ''}  "
                f"task={row.get('task_id') or ''}  "
                f"MR#{row.get('mr_iid') or '-'}",
                f"Source:   {row.get('source_text') or ''}",
                f"{row.get('target_language') or ''}: {row.get('translated_text') or ''}",
                "Flags:    "
                + ", ".join(filter(None, [
                    "tm_match" if row.get("tm_match") else "",
                    "file_tm" if row.get("file_tm") else "",
                    "ice_match (pipeline)" if row.get("ice_match") else "",
                    "cached" if row.get("cached") else "",
                    f"fixed_by_lead={row.get('fixed_by_lead')}" if row.get("fixed_by_lead") else "",
                    "short-string" if row.get("short_string") else "",
                    row.get("origin") or "",
                ])),
            ]
            self._set_detail("\n".join(lines))
        elif kind == "ice":
            ice = data.get("ice") or {}
            trans = ice.get("translations") or {}
            lines = [
                f"ICE store  type={ice.get('match_type')}  "
                f"{'content-only dummy ID' if ice.get('dummy') else ice.get('opus_id')}",
                f"Source: {ice.get('source_text') or ''}",
            ]
            for lang, text in trans.items():
                lines.append(f"  {lang}: {text}")
            if not trans:
                lines.append("  (no target translations on this hit)")
            self._set_detail("\n".join(lines))
        elif kind == "hash":
            bucket = data.get("bucket") or {}
            family = data.get("family") or {}
            opus_ids = bucket.get("opus_ids") or []
            lines = [
                f"{family.get('alias')} · {family.get('logical_key')}",
                f"Hash {bucket.get('path_hash') or ''}  "
                f"({self._role_label(bucket.get('role') or '')})",
                f"Languages: {', '.join(bucket.get('languages') or [])}",
                "IDs:",
            ]
            lines.extend(f"  {oid}" for oid in opus_ids)
            self._set_detail("\n".join(lines))
        elif kind == "family":
            family = data.get("family") or {}
            self._set_detail(tm.format_handoff(
                (self._view or {}).get("anatomy") or {},
                family,
                target_language=self.var_lang.get(),
                source_text=self.var_source.get(),
            ))

    def _on_copy(self):
        view = self._view
        if not view:
            self._idle(self._t("tmp_copy_empty"))
            return
        sel = self.tree.selection()
        family = None
        if sel:
            data = self._row_data.get(sel[0]) or {}
            family = data.get("family")
        if family is None:
            lineages = view.get("lineages") or []
            family = lineages[0] if lineages else None
        text = tm.format_handoff(
            view.get("anatomy") or {},
            family,
            target_language=self.var_lang.get(),
            source_text=self.var_source.get(),
        )
        try:
            self.parent.clipboard_clear()
            self.parent.clipboard_append(text)
        except Exception:
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(text)
        self._idle(self._t("tmp_copied"))

    def _on_help(self):
        win = tk.Toplevel(self.parent)
        win.title(self._t("tmp_help_title"))
        win.geometry("720x560")
        try:
            win.configure(bg=self.app.BG)
        except Exception:
            pass
        body = tk.Text(
            win, font=(FONT_MONO, 10), bg="#0a0a1a", fg="#e4e7ef",
            relief="flat", wrap="word", padx=12, pady=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.insert("1.0", self._t("tmp_help_body"))
        body.configure(state="disabled")
        btn = self.app._create_button(
            win, text=self._t("tmp_help_close"), command=win.destroy,
            style_name="SecondarySmall", font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        btn.pack(pady=(0, 12))
