"""
OPUS 搜索 — GUI Tab
====================
本地化经理 Bug Fixing 的主入口：输入 / 筛选 OPUS ID、源文、译文或产品，
跨本地全量索引 (``opus_index.db``) 秒查「英文源 + 全语种最新译文」，替代
滞后的 loc-central ``UNS.zip`` 全量包。

数据层：:mod:`opus_search`（``search_index``）+ :mod:`opus_id_monitor`
（``get_opus_detail`` 详情）。双击结果行复用 OPUS ID Monitor 已有的
``OpusDetailDialog`` 弹窗。

数据新鲜度：今天索引来自 Tranzor 三源同步（最新但不完整）；接入 GitLab
全量基线 (Phase 1b) 后将升级为最新 + 最全。详见项目记忆
``project-loc-full-export-pivot`` 与 ``products.json``。

纯加法：不修改任何现有模块；GUI 控件只用标准 ttk + 现有 style，便于
在无法可视化测试的环境里保持稳健。
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

# i18n STRINGS —— 必须在模块顶部定义：export_gui 反向 import 本模块读取
# STRINGS 做合并，放在 from-import 之后会被静默吞掉（同 OPUS ID Monitor）。
STRINGS = {
    "en": {
        "tab_opus_search":            "🔎 OPUS Search",
        "opus_search_hint": (
            "Search the latest translations by OPUS ID / source / translation. "
            "Double-click a row to see the full source + all languages."),
        "opus_search_opusid":         "OPUS ID",
        "opus_search_match":          "Match",
        "opus_search_product":        "Product",
        "opus_search_lang":           "Target language",
        "opus_search_source":         "Source contains",
        "opus_search_translation":    "Translation contains",
        "opus_search_key":            "Key contains",
        "opus_search_btn":            "🔎 Search",
        "opus_search_clear":          "Clear",
        "opus_search_any":            "(any)",
        "opus_search_col_opus":       "OPUS ID",
        "opus_search_col_source":     "Source (en-US)",
        "opus_search_col_product":    "Product",
        "opus_search_col_langs":      "#Langs",
        "opus_search_col_kind":       "Source",
        "opus_search_count":          "{n} OPUS IDs",
        "opus_search_count_trunc":    "{n}+ OPUS IDs — refine to narrow",
        "opus_search_loading":        "Searching…",
        "opus_search_need_filter": (
            "Enter at least one of: OPUS ID / Product / Source / "
            "Translation / Key (target language alone is not enough)."),
        "opus_search_empty":          "No matches in the local index.",
        "opus_search_failed":         "Search failed: {error}",
    },
    "zh": {
        "tab_opus_search":            "🔎 OPUS 搜索",
        "opus_search_hint": (
            "按 OPUS ID / 源文 / 译文 检索最新译文；双击结果行查看完整"
            "英文源 + 全语种译文。"),
        "opus_search_opusid":         "OPUS ID",
        "opus_search_match":          "匹配",
        "opus_search_product":        "产品",
        "opus_search_lang":           "目标语言",
        "opus_search_source":         "源文包含",
        "opus_search_translation":    "译文包含",
        "opus_search_key":            "Key 包含",
        "opus_search_btn":            "🔎 搜索",
        "opus_search_clear":          "清空",
        "opus_search_any":            "(全部)",
        "opus_search_col_opus":       "OPUS ID",
        "opus_search_col_source":     "英文源 (en-US)",
        "opus_search_col_product":    "产品",
        "opus_search_col_langs":      "语种数",
        "opus_search_col_kind":       "来源",
        "opus_search_count":          "匹配 {n} 个 OPUS ID",
        "opus_search_count_trunc":    "匹配 {n}+ 个 OPUS ID —— 请加更精确条件",
        "opus_search_loading":        "搜索中…",
        "opus_search_need_filter": (
            "请至少填一个：OPUS ID / 产品 / 源文 / 译文 / Key"
            "（仅目标语言不足以收窄）。"),
        "opus_search_empty":          "本地索引中无匹配。",
        "opus_search_failed":         "搜索失败：{error}",
    },
}

# 目标语言下拉的候选（CHC 18 语 + es-MX/es-419 等库内实际出现过的）。
# 仅作便捷选择，combobox 可编辑，用户也可手输任意值。
_LANG_CHOICES = [
    "de-DE", "en-AU", "en-GB", "en-US", "es-ES", "es-419", "es-MX",
    "fr-FR", "fr-CA", "it-IT", "nl-NL", "pt-BR", "pt-PT", "fi-FI",
    "ko-KR", "ja-JP", "zh-CN", "zh-TW", "zh-HK",
]

_MATCH_VALUES = ["exact", "prefix", "contains"]


class OpusSearchTab:
    """OPUS 搜索面板。"""

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self._first_shown = False
        self._row_opus: dict[str, str] = {}   # tree iid → opus_id
        self._build(parent)
        self.refresh_text()

    def _t(self, key):
        return self.app._t(key)

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------
    def _build(self, parent):
        content = ttk.Frame(parent, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=16, pady=8)

        self.lbl_hint = ttk.Label(content, text="", style="Status.TLabel")
        self.lbl_hint.pack(fill="x", pady=(0, 8))

        # ── 查询表单（grid 两列字段）──
        form = ttk.Frame(content, style="App.TFrame")
        form.pack(fill="x", pady=(0, 8))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        # Row 0: OPUS ID + match
        self.lbl_opusid = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_opusid.grid(row=0, column=0, sticky="e", padx=(0, 6), pady=3)
        self.var_opus = tk.StringVar()
        self.ent_opus = ttk.Entry(form, textvariable=self.var_opus)
        self.ent_opus.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=3)
        self.ent_opus.bind("<Return>", lambda _e: self._on_search())
        self.lbl_match = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_match.grid(row=0, column=2, sticky="e", padx=(0, 6), pady=3)
        self.var_match = tk.StringVar(value="exact")
        self.cmb_match = ttk.Combobox(
            form, textvariable=self.var_match, values=_MATCH_VALUES,
            state="readonly", width=10)
        self.cmb_match.grid(row=0, column=3, sticky="w", padx=(0, 0), pady=3)

        # Row 1: product + language
        self.lbl_product = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_product.grid(row=1, column=0, sticky="e", padx=(0, 6), pady=3)
        self.var_product = tk.StringVar()
        self.cmb_product = ttk.Combobox(form, textvariable=self.var_product)
        self.cmb_product.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=3)
        self.cmb_product.bind("<Return>", lambda _e: self._on_search())
        self.lbl_lang = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_lang.grid(row=1, column=2, sticky="e", padx=(0, 6), pady=3)
        self.var_lang = tk.StringVar()
        self.cmb_lang = ttk.Combobox(
            form, textvariable=self.var_lang, values=[""] + _LANG_CHOICES,
            width=10)
        self.cmb_lang.grid(row=1, column=3, sticky="w", pady=3)

        # Row 2: source + translation
        self.lbl_source = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_source.grid(row=2, column=0, sticky="e", padx=(0, 6), pady=3)
        self.var_source = tk.StringVar()
        self.ent_source = ttk.Entry(form, textvariable=self.var_source)
        self.ent_source.grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=3)
        self.ent_source.bind("<Return>", lambda _e: self._on_search())
        self.lbl_translation = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_translation.grid(row=2, column=2, sticky="e", padx=(0, 6), pady=3)
        self.var_translation = tk.StringVar()
        self.ent_translation = ttk.Entry(form, textvariable=self.var_translation)
        self.ent_translation.grid(row=2, column=3, sticky="ew", pady=3)
        self.ent_translation.bind("<Return>", lambda _e: self._on_search())

        # Row 3: key contains
        self.lbl_key = ttk.Label(form, text="", style="Status.TLabel")
        self.lbl_key.grid(row=3, column=0, sticky="e", padx=(0, 6), pady=3)
        self.var_key = tk.StringVar()
        self.ent_key = ttk.Entry(form, textvariable=self.var_key)
        self.ent_key.grid(row=3, column=1, sticky="ew", padx=(0, 12), pady=3)
        self.ent_key.bind("<Return>", lambda _e: self._on_search())

        # ── 操作条：搜索 / 清空 / 计数 ──
        actions = ttk.Frame(content, style="App.TFrame")
        actions.pack(fill="x", pady=(0, 8))
        self.btn_search = ttk.Button(
            actions, text="", command=self._on_search)
        self.btn_search.pack(side="left")
        self.btn_clear = ttk.Button(
            actions, text="", command=self._on_clear)
        self.btn_clear.pack(side="left", padx=(6, 0))
        self.lbl_status = ttk.Label(actions, text="", style="Status.TLabel")
        self.lbl_status.pack(side="left", padx=(12, 0))

        # ── 结果表 ──
        res = ttk.Frame(content, style="App.TFrame")
        res.pack(fill="both", expand=True)
        self._cols = ("opus", "source", "product", "langs", "kind")
        self.tree = ttk.Treeview(
            res, columns=self._cols, show="headings",
            style="Summary.Treeview", selectmode="browse", height=18)
        widths = {"opus": 360, "source": 320, "product": 110,
                  "langs": 60, "kind": 70}
        for c in self._cols:
            anchor = "center" if c in ("langs", "kind") else "w"
            self.tree.column(c, width=widths.get(c, 100), anchor=anchor)
            self.tree.heading(c, text="")
        sb = ttk.Scrollbar(res, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_result_dbl)

    # ------------------------------------------------------------------
    # i18n
    # ------------------------------------------------------------------
    def refresh_text(self):
        t = self._t
        self.lbl_hint.configure(text=t("opus_search_hint"))
        self.lbl_opusid.configure(text=t("opus_search_opusid"))
        self.lbl_match.configure(text=t("opus_search_match"))
        self.lbl_product.configure(text=t("opus_search_product"))
        self.lbl_lang.configure(text=t("opus_search_lang"))
        self.lbl_source.configure(text=t("opus_search_source"))
        self.lbl_translation.configure(text=t("opus_search_translation"))
        self.lbl_key.configure(text=t("opus_search_key"))
        self.btn_search.configure(text=t("opus_search_btn"))
        self.btn_clear.configure(text=t("opus_search_clear"))
        headings = {
            "opus": "opus_search_col_opus",
            "source": "opus_search_col_source",
            "product": "opus_search_col_product",
            "langs": "opus_search_col_langs",
            "kind": "opus_search_col_kind",
        }
        for c, key in headings.items():
            self.tree.heading(c, text=t(key))

    # ------------------------------------------------------------------
    # Lazy first-show: 后台拉产品(alias)列表填充下拉，不阻塞 UI
    # ------------------------------------------------------------------
    def on_first_show(self):
        if self._first_shown:
            return
        self._first_shown = True

        def _work():
            aliases = self._load_aliases()
            try:
                self.parent.after(0, lambda: self._apply_aliases(aliases))
            except Exception:
                pass
        threading.Thread(
            target=_work, daemon=True, name="opus-search-aliases").start()

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

    # ------------------------------------------------------------------
    # Search
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

    def _on_search(self):
        kwargs = dict(
            opus_id=self.var_opus.get(),
            opus_match=(self.var_match.get() or "exact"),
            product=self.var_product.get(),
            target_language=self.var_lang.get(),
            source_contains=self.var_source.get(),
            translation_contains=self.var_translation.get(),
            logical_key_contains=self.var_key.get(),
            limit=500,
        )
        self._busy(self._t("opus_search_loading"))

        def _work():
            try:
                import opus_search
                res = opus_search.search_index(**kwargs)
                err = None
            except ValueError:
                res, err = None, self._t("opus_search_need_filter")
            except Exception as e:  # noqa: BLE001
                res, err = None, self._t("opus_search_failed").format(
                    error=str(e)[:80])
            try:
                self.parent.after(0, lambda: self._render(res, err))
            except Exception:
                pass
        threading.Thread(
            target=_work, daemon=True, name="opus-search").start()

    def _render(self, res, err):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._row_opus.clear()
        if err:
            self._idle(err)
            return
        results = res.get("results", [])
        for card in results:
            langs = card.get("translations", [])
            src = (card.get("source_text") or "").replace("\n", " ")
            iid = self.tree.insert("", "end", values=(
                card.get("opus_id", ""),
                src,
                card.get("alias", ""),
                len(langs),
                card.get("source_kind", ""),
            ))
            self._row_opus[iid] = card.get("opus_id", "")
        if not results:
            self._idle(self._t("opus_search_empty"))
        elif res.get("truncated"):
            self._idle(self._t("opus_search_count_trunc").format(
                n=res.get("count", len(results))))
        else:
            self._idle(self._t("opus_search_count").format(
                n=res.get("count", len(results))))

    def _on_clear(self):
        for var in (self.var_opus, self.var_product, self.var_lang,
                    self.var_source, self.var_translation, self.var_key):
            var.set("")
        self.var_match.set("exact")
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._row_opus.clear()
        self._idle("")

    def _on_result_dbl(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        opus_id = self._row_opus.get(sel[0])
        if not opus_id:
            return
        try:
            import opus_id_monitor as om
            detail = om.get_opus_detail(opus_id)
            from gui_tab_opus_id_monitor import OpusDetailDialog
            OpusDetailDialog(self.parent, self.app, detail)
        except Exception as e:  # noqa: BLE001
            self._idle(self._t("opus_search_failed").format(error=str(e)[:80]))
