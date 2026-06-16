"""
Advanced Filters — shared model, evaluator and GUI panel
========================================================
本模块把导出报告里那套「翻译内容三块筛选」(STRING KEY / SOURCE (EN-US) /
TRANSLATED TEXT) 抽成可复用组件，供 **MR Pipeline** 与 **Scan Tasks** 两个
GUI 面板内嵌使用。三块筛选的是「翻译内容」，而两个面板的任务列表只显示
任务/MR 元数据 —— 因此条件在「▶ Apply」时不直接过滤列表，而是**接入导出**：

  * HTML  —— 把条件注入报告 (``TF_INITIAL``)，报告打开即预填并自动应用，
            用户仍可在报告内继续增删条件 / Clear 还原。
  * Excel/JSON —— 没有交互层，导出前用本模块的 Python 求值器
            (:func:`filter_translations`) 把不匹配的字符串行先剔除。

为保证两条路径（报告里的 JS 引擎 / Python 求值器）语义**完全一致**，匹配
规则在本文件集中定义并配套单测 (``test_advanced_filter.py``)；
:func:`write_mr_html` 注入的初始状态与本模块的 :func:`to_js_initial` 同形。

设计要点
--------
* **纯逻辑零 GUI 依赖**：求值器 / 序列化函数不 import tkinter 也不 import
  export_gui，``test_advanced_filter`` 在无显示环境也能跑。GUI 面板放在文件
  下半部，tkinter 不可用时优雅降级（``AdvancedFilterPanel`` 为 ``None``）。
* **多重逻辑条件**：每个区块可有 N(≥1) 个条件行，区块头部的 AND/OR 决定这些
  条件如何合并；「+ Add」动态加行，每个附加条件行带「✕」删除。
  （图 3 参考图未体现 Add，本实现按需求补上。）

匹配语义（JS 与 Python 必须一致）
--------------------------------
单个条件:
    pos 为「必须命中」(include)，neg 为「必须不命中」(exclude)。
    条件通过  ⇔  (pos 为空 或 pos 命中) 且 (neg 为空 或 neg 未命中)。
    pos 与 neg 同时为空的条件视为「未启用」，不参与判断。
单个区块 (按 logic 合并已启用条件):
    AND → 全部已启用条件都通过；OR → 任一已启用条件通过；无已启用条件 → 通过。
跨区块: 三块之间恒为 AND（一行需同时满足 STRING KEY / SOURCE / TRANSLATED）。
"""
from __future__ import annotations

import re

# tkinter 只有 GUI 面板需要；纯逻辑（求值器/序列化）不依赖它，缺失时也能 import
# 本模块跑单测。
try:  # pragma: no cover - import guard
    import tkinter as tk
    from tkinter import ttk
except Exception:  # pragma: no cover
    tk = None
    ttk = None


# ---------------------------------------------------------------------------
# i18n —— 由 export_gui 反向合并进全局 STRINGS（与各 tab 模块同一套机制）。
# 三块标题保持英文（与导出报告 / 图 3 一致，本质是字段标识，不本地化）。
# ---------------------------------------------------------------------------
STRINGS = {
    "en": {
        "af_toggle":            "🔍 Advanced Filters",
        "af_toggle_active":     "🔍 Advanced Filters ({n})",
        "af_title_string_key":  "STRING KEY",
        "af_title_source":      "SOURCE (EN-US)",
        "af_title_translated":  "TRANSLATED TEXT",
        "af_pos":               "Pos",
        "af_neg":               "Neg",
        "af_pos_ph":            "Positive keyword…",
        "af_neg_ph":            "Negative keyword (Exclude)…",
        "af_match_whole":       "Match whole",
        "af_case_pos":          "Match case (Pos)",
        "af_regex_pos":         "Regex (Pos)",
        "af_case_neg":          "Match case (Neg)",
        "af_regex_neg":         "Regex (Neg)",
        "af_and":               "AND",
        "af_or":                "OR",
        "af_add":               "+ Add",
        "af_remove":            "✕",
        "af_apply":             "▶ Apply",
        "af_clear":             "✕ Clear",
        "af_applied":           "✓ {n} content filter(s) active — applied on export",
        "af_applied_none":      "No advanced filters — exporting everything",
        "af_hint":              "Filters translation content; applied to the export (HTML pre-fills, Excel/JSON keep only matching rows).",
    },
    "zh": {
        "af_toggle":            "🔍 高级筛选",
        "af_toggle_active":     "🔍 高级筛选 ({n})",
        "af_title_string_key":  "STRING KEY",
        "af_title_source":      "SOURCE (EN-US)",
        "af_title_translated":  "TRANSLATED TEXT",
        "af_pos":               "Pos",
        "af_neg":               "Neg",
        "af_pos_ph":            "包含关键字…",
        "af_neg_ph":            "排除关键字（不含）…",
        "af_match_whole":       "全词匹配",
        "af_case_pos":          "区分大小写(Pos)",
        "af_regex_pos":         "正则(Pos)",
        "af_case_neg":          "区分大小写(Neg)",
        "af_regex_neg":         "正则(Neg)",
        "af_and":               "AND",
        "af_or":                "OR",
        "af_add":               "+ 增加条件",
        "af_remove":            "✕",
        "af_apply":             "▶ 应用",
        "af_clear":             "✕ 清空",
        "af_applied":           "✓ 已应用 {n} 个内容筛选条件 —— 导出时生效",
        "af_applied_none":      "未设高级筛选 —— 将导出全部内容",
        "af_hint":              "筛选「翻译内容」，作用于导出（HTML 预填，Excel/JSON 仅保留匹配行）。",
    },
}


# ---------------------------------------------------------------------------
# 字段定义：(field_id, i18n 标题键, 报告内 JS dataKey)
# ---------------------------------------------------------------------------
FIELDS = (
    ("string_key", "af_title_string_key", "string_key"),
    ("source",     "af_title_source",     "source_text"),
    ("translated", "af_title_translated", "translated_text"),
)
FIELD_IDS = tuple(f[0] for f in FIELDS)


def empty_condition() -> dict:
    """A blank condition with all flags off."""
    return {
        "pos": "", "neg": "",
        "match_whole": False,
        "pos_case": False, "pos_regex": False,
        "neg_case": False, "neg_regex": False,
    }


def empty_field_state() -> dict:
    return {"logic": "AND", "conditions": [empty_condition()]}


def empty_state() -> dict:
    return {fid: empty_field_state() for fid in FIELD_IDS}


# ---------------------------------------------------------------------------
# 求值器 —— 与导出报告里的 JS (testMatch / evaluateField) 必须逐位一致
# ---------------------------------------------------------------------------
def _test_match(text, keyword, case_sensitive, is_regex, match_whole):
    """Return True/False if keyword matches text; None when keyword is empty.

    Mirrors the report's JS ``testMatch``:
      * regex 编译失败 → False（不抛错），与 JS ``catch → false`` 一致；
      * 非正则时按需 ``\\b…\\b`` 全词；search 语义（非锚定），同 JS ``RegExp.test``。
    """
    if not keyword:
        return None
    try:
        if is_regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            pattern = re.compile(keyword, flags)
        elif match_whole:
            # JS ``RegExp`` ``\b`` is **ASCII-only** (word = ``[A-Za-z0-9_]``)
            # and the report compiles with no ``u`` flag. Python's ``\b`` is
            # Unicode-aware by default, so a CJK / accented keyword edge would
            # disagree (e.g. "发票" whole-word matches in Python but not in the
            # report). Force ``re.ASCII`` on the whole-word branch only to stay
            # byte-for-byte equivalent with the report engine — see
            # ``test_advanced_filter`` whole-word CJK/accented coverage.
            flags = re.ASCII if case_sensitive else (re.ASCII | re.IGNORECASE)
            pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", flags)
        else:
            flags = 0 if case_sensitive else re.IGNORECASE
            pattern = re.compile(re.escape(keyword), flags)
        return pattern.search(text or "") is not None
    except re.error:
        return False


def condition_active(cond) -> bool:
    """A condition counts only if it has a positive or negative keyword.

    Truthiness matches JS (``!tf.pos && !tf.neg``): empty string ⇒ inactive,
    任意非空（含空格）⇒ 启用。"""
    if not cond:
        return False
    return bool(cond.get("pos")) or bool(cond.get("neg"))


def evaluate_condition(text, cond) -> bool:
    """pos = required include, neg = required exclude."""
    pos_res = _test_match(text, cond.get("pos", ""), cond.get("pos_case", False),
                          cond.get("pos_regex", False), cond.get("match_whole", False))
    neg_res = _test_match(text, cond.get("neg", ""), cond.get("neg_case", False),
                          cond.get("neg_regex", False), cond.get("match_whole", False))
    pos_pass = True if pos_res is None else pos_res
    neg_pass = True if neg_res is None else (not neg_res)
    return pos_pass and neg_pass


def evaluate_field(text, field_state) -> bool:
    if not field_state:
        return True
    conds = [c for c in field_state.get("conditions", []) if condition_active(c)]
    if not conds:
        return True
    logic = (field_state.get("logic") or "AND").upper()
    results = [evaluate_condition(text, c) for c in conds]
    return any(results) if logic == "OR" else all(results)


def _field_text(translation, field_id) -> str:
    """Pull the text a field matches against from a translation row.

    报告里 ``string_key`` 取自 ``opus_id``；Python 侧的原始翻译行同样以
    ``opus_id`` 承载 string key（``string_key`` 作兜底）。"""
    if field_id == "string_key":
        return str(translation.get("opus_id") or translation.get("string_key") or "")
    if field_id == "source":
        return str(translation.get("source_text") or "")
    if field_id == "translated":
        return str(translation.get("translated_text") or "")
    return ""


def row_passes(translation, state) -> bool:
    """True iff the row satisfies all three field blocks (blocks AND together)."""
    if not state:
        return True
    for field_id, _title_key, _data_key in FIELDS:
        fs = state.get(field_id)
        if not fs:
            continue
        if not evaluate_field(_field_text(translation, field_id), fs):
            return False
    return True


def is_empty(state) -> bool:
    """True when no field has any active condition (export everything)."""
    if not state:
        return True
    for field_id in FIELD_IDS:
        fs = state.get(field_id) or {}
        for c in fs.get("conditions", []):
            if condition_active(c):
                return False
    return True


def count_active(state) -> int:
    n = 0
    for field_id in FIELD_IDS:
        fs = (state or {}).get(field_id) or {}
        for c in fs.get("conditions", []):
            if condition_active(c):
                n += 1
    return n


def filter_translations(translations, state):
    """Keep only rows matching ``state``. Returns input unchanged when empty."""
    if is_empty(state):
        return translations
    return [t for t in (translations or []) if row_passes(t, state)]


# ---------------------------------------------------------------------------
# 序列化 —— 注入导出报告 (JS) / 生成报告内只读 banner
# ---------------------------------------------------------------------------
def to_js_initial(state):
    """Convert state to the report's ``TF_INITIAL`` shape (camelCase flags).

    只输出**已启用**条件；整体为空时返回 None（报告侧据此跳过预填/自动应用）。"""
    if is_empty(state):
        return None
    out = {}
    for field_id in FIELD_IDS:
        fs = (state or {}).get(field_id) or {}
        conds = []
        for c in fs.get("conditions", []):
            if not condition_active(c):
                continue
            conds.append({
                "pos": c.get("pos", ""),
                "neg": c.get("neg", ""),
                "matchWhole": bool(c.get("match_whole")),
                "posCaseSensitive": bool(c.get("pos_case")),
                "posRegex": bool(c.get("pos_regex")),
                "negCaseSensitive": bool(c.get("neg_case")),
                "negRegex": bool(c.get("neg_regex")),
            })
        if conds:
            out[field_id] = {"logic": (fs.get("logic") or "AND").upper(),
                             "conditions": conds}
    return out or None


_FIELD_LABELS_EN = {
    "string_key": "STRING KEY",
    "source": "SOURCE (EN-US)",
    "translated": "TRANSLATED TEXT",
}


def summary_segments(state):
    """Structured, English, human-readable description of active criteria.

    Returns list of ``(field_label, logic, [condition_text, ...])`` for the
    report banner / any textual surface. Empty list when no filters active."""
    segments = []
    for field_id in FIELD_IDS:
        fs = (state or {}).get(field_id) or {}
        conds = [c for c in fs.get("conditions", []) if condition_active(c)]
        if not conds:
            continue
        parts = []
        for c in conds:
            bits = []
            if c.get("pos"):
                tags = []
                if c.get("pos_regex"):
                    tags.append("regex")
                if c.get("pos_case"):
                    tags.append("case")
                if c.get("match_whole"):
                    tags.append("whole")
                suffix = (" [" + ",".join(tags) + "]") if tags else ""
                bits.append(f'contains "{c["pos"]}"{suffix}')
            if c.get("neg"):
                tags = []
                if c.get("neg_regex"):
                    tags.append("regex")
                if c.get("neg_case"):
                    tags.append("case")
                if c.get("match_whole"):
                    tags.append("whole")
                suffix = (" [" + ",".join(tags) + "]") if tags else ""
                bits.append(f'NOT "{c["neg"]}"{suffix}')
            parts.append(" & ".join(bits))
        segments.append((_FIELD_LABELS_EN.get(field_id, field_id),
                         (fs.get("logic") or "AND").upper(), parts))
    return segments


# ===========================================================================
# GUI Panel —— 仅在 tkinter 可用时定义
# ===========================================================================
if tk is not None:

    # 图 3 配色（slate 调色板，与导出报告 filter 面板一致）
    _PANEL_BG      = "#1e293b"
    _CARD_BG       = "#0f172a"
    _INPUT_BG      = "#1e293b"
    _BORDER        = "#334155"
    _FOCUS_BORDER  = "#4472C4"
    _TITLE_FG      = "#38bdf8"
    _TEXT_FG       = "#e2e8f0"
    _MUTED_FG      = "#94a3b8"
    _PLACEHOLDER   = "#64748b"
    _POS_BG        = "#ffffff"
    _POS_FG        = "#1e293b"
    _NEG_FG        = "#f87171"
    _NEG_BORDER    = "#7f1d1d"
    _LOGIC_ON_BG   = "#0ea5e9"
    _LOGIC_OFF_FG  = "#64748b"
    _APPLY_BG      = "#0ea5e9"
    _APPLY_HOVER   = "#0284c7"
    _CLEAR_BG      = "#334155"
    _CLEAR_HOVER   = "#475569"
    _ADD_FG        = "#38bdf8"
    _REMOVE_FG     = "#f87171"

    def _font(size, bold=False):
        try:
            from export_gui import FONT_FAMILY
        except Exception:
            FONT_FAMILY = "Segoe UI"
        return (FONT_FAMILY, size, "bold") if bold else (FONT_FAMILY, size)

    class _PlaceholderEntry(tk.Entry):
        """tk.Entry with grey placeholder text that clears on focus."""

        def __init__(self, master, placeholder="", border=_BORDER, **kw):
            self._ph = placeholder
            self._fg = kw.pop("fg", _TEXT_FG)
            self._ph_on = False
            super().__init__(
                master, fg=self._fg, bg=_INPUT_BG, insertbackground=_TEXT_FG,
                relief="flat", highlightthickness=1, highlightbackground=border,
                highlightcolor=_FOCUS_BORDER, font=_font(10), **kw)
            self.bind("<FocusIn>", self._on_focus_in)
            self.bind("<FocusOut>", self._on_focus_out)
            self._show_placeholder()

        def _show_placeholder(self):
            if not super().get():
                self._ph_on = True
                self.delete(0, "end")
                self.insert(0, self._ph)
                self.config(fg=_PLACEHOLDER)

        def _on_focus_in(self, _e=None):
            if self._ph_on:
                self._ph_on = False
                self.delete(0, "end")
                self.config(fg=self._fg)

        def _on_focus_out(self, _e=None):
            if not super().get():
                self._show_placeholder()

        def value(self) -> str:
            return "" if self._ph_on else super().get()

        def set_value(self, v):
            self._ph_on = False
            self.delete(0, "end")
            self.config(fg=self._fg)
            if v:
                self.insert(0, v)
            else:
                self._show_placeholder()

        def clear(self):
            self._ph_on = False
            self.delete(0, "end")
            self._show_placeholder()

        def set_placeholder(self, text):
            was_on = self._ph_on
            self._ph = text
            if was_on:
                self.delete(0, "end")
                self.insert(0, self._ph)
                self.config(fg=_PLACEHOLDER)
                self._ph_on = True

    def _mk_button(parent, text, command, bg, fg, hover=None, font=None,
                   padx=12, pady=3):
        btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                        activebackground=hover or bg, activeforeground=fg,
                        relief="flat", bd=0, cursor="hand2",
                        font=font or _font(10, True), padx=padx, pady=pady)
        if hover:
            btn.bind("<Enter>", lambda _e: btn.config(bg=hover))
            btn.bind("<Leave>", lambda _e: btn.config(bg=bg))
        return btn

    class _ConditionRow:
        """One Pos/Neg + options unit inside a field card."""

        def __init__(self, card, app, on_remove, removable):
            self.app = app
            self._on_remove = on_remove
            self.removable = removable
            self.frame = tk.Frame(card.cond_holder, bg=_CARD_BG)
            self.frame.pack(fill="x", pady=(0, 4))

            # Pos row
            pr = tk.Frame(self.frame, bg=_CARD_BG)
            pr.pack(fill="x", pady=(0, 3))
            self.lbl_pos = tk.Label(pr, text="Pos", bg=_POS_BG, fg=_POS_FG,
                                    font=_font(9, True), padx=6, pady=1, width=4)
            self.lbl_pos.pack(side="left")
            self.ent_pos = _PlaceholderEntry(pr, placeholder="")
            self.ent_pos.pack(side="left", fill="x", expand=True, padx=(6, 0), ipady=2)

            # Neg row
            nr = tk.Frame(self.frame, bg=_CARD_BG)
            nr.pack(fill="x", pady=(0, 3))
            self.lbl_neg = tk.Label(nr, text="Neg", bg=_CARD_BG, fg=_NEG_FG,
                                    font=_font(9, True), padx=6, pady=1, width=4,
                                    highlightthickness=1, highlightbackground=_NEG_BORDER)
            self.lbl_neg.pack(side="left")
            self.ent_neg = _PlaceholderEntry(nr, placeholder="", border=_NEG_BORDER)
            self.ent_neg.pack(side="left", fill="x", expand=True, padx=(6, 0), ipady=2)

            # Options (2-row grid so they fit a ~1/3-width card)
            opts = tk.Frame(self.frame, bg=_CARD_BG)
            opts.pack(fill="x")
            self.var_match_whole = tk.BooleanVar(value=False)
            self.var_pos_case = tk.BooleanVar(value=False)
            self.var_pos_regex = tk.BooleanVar(value=False)
            self.var_neg_case = tk.BooleanVar(value=False)
            self.var_neg_regex = tk.BooleanVar(value=False)
            self.chk_match_whole = self._mk_check(opts, self.var_match_whole)
            self.chk_pos_case = self._mk_check(opts, self.var_pos_case)
            self.chk_pos_regex = self._mk_check(opts, self.var_pos_regex)
            self.chk_neg_case = self._mk_check(opts, self.var_neg_case)
            self.chk_neg_regex = self._mk_check(opts, self.var_neg_regex)
            self.chk_match_whole.grid(row=0, column=0, sticky="w", padx=(0, 8))
            self.chk_pos_case.grid(row=0, column=1, sticky="w", padx=(0, 8))
            self.chk_pos_regex.grid(row=0, column=2, sticky="w")
            self.chk_neg_case.grid(row=1, column=0, sticky="w", padx=(0, 8))
            self.chk_neg_regex.grid(row=1, column=1, sticky="w", padx=(0, 8))

            # Remove (✕) for non-first conditions
            self.btn_remove = None
            if removable:
                self.btn_remove = tk.Button(
                    opts, text="✕", command=self._remove, bg=_CARD_BG,
                    fg=_REMOVE_FG, activebackground=_CARD_BG,
                    activeforeground="#fca5a5", relief="flat", bd=0,
                    cursor="hand2", font=_font(9, True))
                self.btn_remove.grid(row=1, column=2, sticky="e")

            # thin divider beneath each (non-last) condition; toggled by card
            self.divider = tk.Frame(self.frame, bg=_BORDER, height=1)

        def _mk_check(self, parent, var):
            return tk.Checkbutton(
                parent, text="", variable=var, bg=_CARD_BG, fg=_MUTED_FG,
                selectcolor=_INPUT_BG, activebackground=_CARD_BG,
                activeforeground=_TEXT_FG, relief="flat", highlightthickness=0,
                bd=0, font=_font(8), anchor="w", padx=0)

        def _remove(self):
            if self._on_remove:
                self._on_remove(self)

        def destroy(self):
            self.frame.destroy()

        def get_state(self) -> dict:
            return {
                "pos": self.ent_pos.value(),
                "neg": self.ent_neg.value(),
                "match_whole": bool(self.var_match_whole.get()),
                "pos_case": bool(self.var_pos_case.get()),
                "pos_regex": bool(self.var_pos_regex.get()),
                "neg_case": bool(self.var_neg_case.get()),
                "neg_regex": bool(self.var_neg_regex.get()),
            }

        def refresh_text(self, t):
            self.lbl_pos.config(text=t("af_pos"))
            self.lbl_neg.config(text=t("af_neg"))
            self.ent_pos.set_placeholder(t("af_pos_ph"))
            self.ent_neg.set_placeholder(t("af_neg_ph"))
            self.chk_match_whole.config(text=t("af_match_whole"))
            self.chk_pos_case.config(text=t("af_case_pos"))
            self.chk_pos_regex.config(text=t("af_regex_pos"))
            self.chk_neg_case.config(text=t("af_case_neg"))
            self.chk_neg_regex.config(text=t("af_regex_neg"))

    class _FieldCard:
        """One filter block (STRING KEY / SOURCE / TRANSLATED TEXT)."""

        def __init__(self, parent, app, field_id, title_key):
            self.app = app
            self.field_id = field_id
            self.title_key = title_key
            self.logic = "AND"
            self.rows: list[_ConditionRow] = []

            self.frame = tk.Frame(parent, bg=_CARD_BG, highlightthickness=1,
                                  highlightbackground=_BORDER)
            inner = tk.Frame(self.frame, bg=_CARD_BG)
            inner.pack(fill="both", expand=True, padx=12, pady=10)

            # Header: title + (AND/OR pill + Add)
            header = tk.Frame(inner, bg=_CARD_BG)
            header.pack(fill="x", pady=(0, 8))
            self.lbl_title = tk.Label(header, text="", bg=_CARD_BG, fg=_TITLE_FG,
                                      font=_font(10, True))
            self.lbl_title.pack(side="left")

            right = tk.Frame(header, bg=_CARD_BG)
            right.pack(side="right")
            self.btn_add = tk.Button(
                right, text="+ Add", command=self.add_condition, bg=_CARD_BG,
                fg=_ADD_FG, activebackground=_CARD_BG, activeforeground="#7dd3fc",
                relief="flat", bd=0, cursor="hand2", font=_font(9, True))
            self.btn_add.pack(side="right", padx=(8, 0))
            self.btn_or = tk.Button(
                right, text="OR", command=lambda: self.set_logic("OR"),
                relief="flat", bd=0, cursor="hand2", font=_font(8, True),
                padx=8, pady=2)
            self.btn_or.pack(side="right")
            self.btn_and = tk.Button(
                right, text="AND", command=lambda: self.set_logic("AND"),
                relief="flat", bd=0, cursor="hand2", font=_font(8, True),
                padx=8, pady=2)
            self.btn_and.pack(side="right")

            self.cond_holder = tk.Frame(inner, bg=_CARD_BG)
            self.cond_holder.pack(fill="x")

            self.add_condition()
            self._sync_logic_buttons()

        # -- conditions --
        def add_condition(self):
            removable = len(self.rows) > 0
            row = _ConditionRow(self, self.app, on_remove=self._remove_row,
                                removable=removable)
            self.rows.append(row)
            if hasattr(self.app, "_t"):
                row.refresh_text(self.app._t)
            self._update_dividers()

        def _remove_row(self, row):
            if len(self.rows) <= 1:
                return
            row.destroy()
            self.rows.remove(row)
            self._update_dividers()

        def _update_dividers(self):
            for i, row in enumerate(self.rows):
                if i < len(self.rows) - 1:
                    row.divider.pack(fill="x", pady=(2, 4))
                else:
                    row.divider.pack_forget()

        # -- logic --
        def set_logic(self, logic):
            self.logic = "OR" if logic == "OR" else "AND"
            self._sync_logic_buttons()

        def _sync_logic_buttons(self):
            on = (self.logic == "AND")
            self.btn_and.config(bg=_LOGIC_ON_BG if on else _CARD_BG,
                                fg="#fff" if on else _LOGIC_OFF_FG)
            self.btn_or.config(bg=_LOGIC_ON_BG if not on else _CARD_BG,
                               fg="#fff" if not on else _LOGIC_OFF_FG)

        # -- state --
        def get_state(self) -> dict:
            return {"logic": self.logic,
                    "conditions": [r.get_state() for r in self.rows]}

        def clear(self):
            for row in self.rows[1:]:
                row.destroy()
            self.rows = self.rows[:1]
            r = self.rows[0]
            r.ent_pos.clear()
            r.ent_neg.clear()
            for var in (r.var_match_whole, r.var_pos_case, r.var_pos_regex,
                        r.var_neg_case, r.var_neg_regex):
                var.set(False)
            self.set_logic("AND")
            self._update_dividers()

        def refresh_text(self, t):
            self.lbl_title.config(text=t(self.title_key))
            self.btn_add.config(text=t("af_add"))
            self.btn_and.config(text=t("af_and"))
            self.btn_or.config(text=t("af_or"))
            for row in self.rows:
                row.refresh_text(t)

    class AdvancedFilterPanel:
        """Collapsible advanced-filter panel embedded in a tab.

        on_apply(state) / on_clear() 回调让宿主 tab 更新自身状态条；面板自身也
        维护一行内联状态。``get_state()`` 返回当前条件，宿主在导出时读取并
        :func:`filter_translations` / 注入报告。
        """

        def __init__(self, parent, app, on_apply=None, on_clear=None):
            self.app = app
            self.on_apply = on_apply
            self.on_clear = on_clear
            self._expanded = False
            # Active-condition count from the last Apply (None = never applied),
            # so refresh_text can re-localise the inline status on a language
            # switch instead of leaving it in the previous language.
            self._applied_n = None

            self.outer = tk.Frame(parent, bg=app.BG)

            # Toggle bar
            bar = tk.Frame(self.outer, bg=app.BG)
            bar.pack(fill="x")
            self.btn_toggle = tk.Button(
                bar, text="🔍 Advanced Filters  ▾", command=self.toggle,
                bg=app.BG_CARD, fg="#cbd5e1", activebackground=app.ACCENT,
                activeforeground="#fff", relief="flat", bd=0, cursor="hand2",
                font=_font(10, True), padx=14, pady=4)
            self.btn_toggle.pack(side="left")
            self.lbl_status = tk.Label(bar, text="", bg=app.BG, fg=_MUTED_FG,
                                       font=_font(9))
            self.lbl_status.pack(side="left", padx=(12, 0))

            # Collapsible body
            self.body = tk.Frame(self.outer, bg=_PANEL_BG, highlightthickness=1,
                                 highlightbackground=_BORDER)
            inner = tk.Frame(self.body, bg=_PANEL_BG)
            inner.pack(fill="both", expand=True, padx=14, pady=12)

            self.lbl_hint = tk.Label(inner, text="", bg=_PANEL_BG, fg=_MUTED_FG,
                                     font=_font(8), anchor="w", justify="left")
            self.lbl_hint.pack(fill="x", pady=(0, 8))

            cards = tk.Frame(inner, bg=_PANEL_BG)
            cards.pack(fill="x")
            self.cards: list[_FieldCard] = []
            for col, (field_id, title_key, _dk) in enumerate(FIELDS):
                cards.columnconfigure(col, weight=1, uniform="afcards")
                card = _FieldCard(cards, app, field_id, title_key)
                card.frame.grid(row=0, column=col, sticky="nsew",
                                padx=(0 if col == 0 else 8, 0))
                self.cards.append(card)

            actions = tk.Frame(inner, bg=_PANEL_BG)
            actions.pack(fill="x", pady=(12, 0))
            self.btn_clear = _mk_button(
                actions, "✕ Clear", self.clear, _CLEAR_BG, "#cbd5e1",
                hover=_CLEAR_HOVER, padx=16, pady=5)
            self.btn_clear.pack(side="right")
            self.btn_apply = _mk_button(
                actions, "▶ Apply", self.apply, _APPLY_BG, "#fff",
                hover=_APPLY_HOVER, padx=16, pady=5)
            self.btn_apply.pack(side="right", padx=(0, 8))

            self.refresh_text()

        # -- layout / visibility --
        def pack(self, **kw):
            self.outer.pack(**kw)
            return self

        def grid(self, **kw):
            self.outer.grid(**kw)
            return self

        def toggle(self):
            self.collapse() if self._expanded else self.expand()

        def expand(self):
            if not self._expanded:
                self.body.pack(fill="x", pady=(8, 0))
                self._expanded = True
                self._sync_toggle_text()

        def collapse(self):
            if self._expanded:
                self.body.pack_forget()
                self._expanded = False
                self._sync_toggle_text()

        def _sync_toggle_text(self):
            t = self.app._t
            n = count_active(self.get_state())
            caret = "▴" if self._expanded else "▾"
            base = t("af_toggle_active").format(n=n) if n else t("af_toggle")
            self.btn_toggle.config(text=f"{base}  {caret}")

        # -- state --
        def get_state(self) -> dict:
            return {c.field_id: c.get_state() for c in self.cards}

        def is_active(self) -> bool:
            return not is_empty(self.get_state())

        def _render_status(self):
            """Re-render the inline status from the last Apply, in the current
            language (called by both apply() and refresh_text())."""
            t = self.app._t
            if self._applied_n is None:
                self.lbl_status.config(text="", fg=_MUTED_FG)
            elif self._applied_n:
                self.lbl_status.config(
                    text=t("af_applied").format(n=self._applied_n), fg="#34d399")
            else:
                self.lbl_status.config(text=t("af_applied_none"), fg=_MUTED_FG)

        def apply(self):
            state = self.get_state()
            self._applied_n = count_active(state)
            self._render_status()
            self._sync_toggle_text()
            if self.on_apply:
                self.on_apply(state)

        def clear(self):
            for card in self.cards:
                card.clear()
            self._applied_n = None
            self._render_status()
            self._sync_toggle_text()
            if self.on_clear:
                self.on_clear()

        def refresh_text(self):
            t = self.app._t
            self.lbl_hint.config(text=t("af_hint"))
            self.btn_apply.config(text=t("af_apply"))
            self.btn_clear.config(text=t("af_clear"))
            for card in self.cards:
                card.refresh_text(t)
            self._render_status()
            self._sync_toggle_text()
else:  # pragma: no cover - tkinter unavailable (headless test env)
    AdvancedFilterPanel = None  # type: ignore
