"""Searchable dropdown for readonly ttk.Combobox — pure tkinter, zero deps.

MR Pipeline 的 Project 下拉项多且名字长（``CoreLib/RoomsController`` …），
原生 popdown 只能滚动找，效率很低。这里把 readonly Combobox 的原生下拉
替换成一个自定义弹窗：**顶部固定一个搜索输入框**，下方是随关键字实时
过滤的选项列表。

为什么不用原生 popdown 塞输入框？
    ttk.Combobox 的下拉列表是 Tk 内部私有的 popdown 窗口（``f.l`` 子件），
    往里注入部件依赖内部实现、跨平台（Windows/macOS aqua）行为不一致，
    升级 Tk 随时会碎。自绘弹窗走的都是公开 API，且外观能完全贴合应用
    的深色主题（原生 popdown 是亮色的）。

交互（单选）：
    - 点击 Combobox（或焦点态按 ↓/空格）→ 弹出搜索弹窗，输入框自动聚焦；
    - 键入即过滤（大小写不敏感的包含匹配），列表实时刷新；
    - ↑/↓ 移动高亮，Enter 选中，Esc / 点击窗外关闭；
    - 选中后写回 Combobox 并派发 ``<<ComboboxSelected>>``，对既有代码透明。

交互（多选，``multi=True``）：
    - 同一套搜索弹窗，每行带 ☐/☑；点击或 Enter 切换勾选，弹窗保持打开；
    - 空选 = 「全部」（不过滤），与历史单选里的空占位项同语义；
    - 「（全部）」行与具体项目互斥：勾选项目会取消全部，点全部会清空勾选；
    - 关键字过滤后可用「全选当前」勾上所有可见项（不会在无关键字时
      把上百个项目一次性勾上 — 那等价于「全部」却变成 N 次 API）；
    - Done / Esc / 点窗外关闭；每次勾选通过 ``set_selected`` 写回。
    - 多选弹窗在提示行和列表之间有 Preset 条：芯片一键加载命名组合，
      Save 原地命名（重名则确认覆盖）；右键芯片可重命名 / 删除。

对外接口：
    - :func:`attach_search` —— 对一个 readonly ``ttk.Combobox`` 启用搜索下拉，
      选项每次打开时从 ``combobox["values"]`` 现取，天然兼容异步加载。
    - :func:`filter_options` / :func:`display_label` /
      :func:`format_selection_summary` / :func:`toggle_selected` /
      :func:`add_visible` —— 纯函数，不依赖 Tk，便于单元测试
      （见 test_searchable_combobox.py）。

弹窗骨架（无边框 Toplevel + grab_set + 点窗外关闭 + z-order 修复）与
date_picker._CalendarPopup 同一套路，颜色常量同样内联（不 import
export_gui，避免 tab ↔ export_gui 的循环依赖）。
"""
from __future__ import annotations

import tkinter as tk

import project_presets as _pp

# 深色主题取色 —— 与 export_gui 的 BG_CARD / ACCENT_BTN 保持一致（内联，
# 理由同 date_picker）。
_POPUP_BG = "#16213e"      # 卡片底
_BORDER = "#0f3460"        # 边框蓝
_FG = "#e0e0e0"            # 主文字
_FG_MUTED = "#7a8199"      # 次要文字 / 无匹配提示
_ENTRY_BG = "#0a0a1a"      # 输入框底（与应用各 Entry 一致）
_SELECTED_BG = "#e94560"   # 高亮（与 ACCENT_BTN 一致）
_MULTI_ACTIVE_BG = "#1f3a6a"  # 多选行高亮（不用粉红，避免盖住 ☑）

# 本地化标签。空字符串选项代表"不过滤"，列表里显示成占位文案而不是空行。
_ALL_LABEL = {"en": "(All)", "zh": "（全部）"}
_NO_MATCH_LABEL = {"en": "(no match)", "zh": "（无匹配）"}
_DONE_LABEL = {"en": "Done", "zh": "完成"}
_CLEAR_LABEL = {"en": "Clear", "zh": "清除"}
_CHECK_VISIBLE_LABEL = {"en": "Check visible", "zh": "全选当前"}
_MULTI_HINT = {
    "en": "Click to toggle · empty = all projects",
    "zh": "点击勾选，留空表示全部项目",
}
_SELECTED_N = {"en": "{n} selected", "zh": "已选 {n} 项"}
_SAVE_LABEL = {"en": "Save", "zh": "保存"}
_CANCEL_LABEL = {"en": "Cancel", "zh": "取消"}
_REPLACE_LABEL = {"en": "Replace", "zh": "覆盖"}
_RENAME_LABEL = {"en": "Rename", "zh": "重命名"}
_DELETE_LABEL = {"en": "Delete", "zh": "删除"}
_NAME_LABEL = {"en": "Name", "zh": "名称"}
_REPLACE_PROMPT = {"en": 'Replace "{name}"?', "zh": "覆盖「{name}」？"}
_LIMIT_HINT = {
    "en": "Limit {n} groups — delete one first",
    "zh": "最多 {n} 组，请先删除一组",
}
_DUP_HINT = {"en": "Name already used", "zh": "名称已被占用"}
_EMPTY_PRESETS = {
    "en": "Save these {n} as a named group",
    "zh": "把这 {n} 个项目存成一组",
}
_EMPTY_PRESETS_NONE = {
    "en": "Select projects, then Save",
    "zh": "先勾选项目，再保存",
}

_MAX_VISIBLE_CHIPS = 4

CHECK_OFF = "\u2610"  # ☐
CHECK_ON = "\u2611"   # ☑

_MAX_VISIBLE_ROWS = 14     # 列表最多显示行数，超出滚动
_MIN_WIDTH_CHARS = 24      # 列表最小宽度（字符）
_MAX_WIDTH_CHARS = 58      # 列表最大宽度（字符），超长项横向截断


# ---------------------------------------------------------------------------
# 纯函数（无 Tk 依赖，单元测试覆盖这里）
# ---------------------------------------------------------------------------
def filter_options(options, keyword: str | None) -> list[str]:
    """按关键字过滤选项：大小写不敏感的**包含**匹配，保持原顺序。

    - 关键字为空/全空白 → 返回全部（含空字符串占位项）；
    - 关键字非空 → 只留包含它的项；空字符串占位项不含任何关键字，
      自然被滤掉（搜索时"全部"没有意义）。

    >>> filter_options(["", "Fiji/Fiji", "web/bui"], "fiji")
    ['Fiji/Fiji']
    >>> filter_options(["", "a"], "  ")
    ['', 'a']
    """
    kw = (keyword or "").strip().lower()
    out = []
    for opt in options:
        s = str(opt)
        if not kw or kw in s.lower():
            out.append(s)
    return out


def display_label(option, lang: str = "en") -> str:
    """选项 → 列表显示文本：空字符串（"全部"占位）显示成本地化文案，
    其余原样。"""
    s = str(option)
    if not s.strip():
        return _ALL_LABEL.get(lang, _ALL_LABEL["en"])
    return s


def checked_label(option, checked: bool, lang: str = "en") -> str:
    """多选列表行：``☐ (All)`` / ``☑ Fiji/Fiji``。"""
    mark = CHECK_ON if checked else CHECK_OFF
    return f"{mark} {display_label(option, lang)}"


def format_selection_summary(selected, lang: str = "en",
                             preset_name: str | None = None) -> str:
    """把已选项目压成 Combobox 的单行展示。

    - 空 / 全空白 → ``""``（与历史「不过滤」占位一致，界面显示为空）；
    - 勾选恰好等于某个已存组合 → 显示组合名（``preset_name``）；
    - 单选 → 项目名本身（与改造前单选 Combobox 完全相同）；
    - 多选 → 本地化的 ``N selected`` / ``已选 N 项``（宽度 20 的框装不下
      两个仓库路径，打开下拉才能看具体勾选）。
    """
    items = [str(s) for s in (selected or []) if str(s).strip()]
    if not items:
        return ""
    named = (preset_name or "").strip()
    if named:
        return named
    if len(items) == 1:
        return items[0]
    lang = lang if lang in _SELECTED_N else "en"
    return _SELECTED_N[lang].format(n=len(items))


def toggle_selected(all_options, selected, option) -> list[str]:
    """切换 ``option`` 后返回**按 all_options 顺序**的新选中列表。

    - ``option`` 为空 / 全空白（「全部」）→ 永远返回 ``[]``；
    - 已在选中集里 → 去掉；否则加入；
    - 结果不含空占位、不含 all_options 以外的项。
    """
    all_s = [str(o) for o in (all_options or []) if str(o).strip()]
    current = {str(s) for s in (selected or []) if str(s).strip()}
    opt = str(option) if option is not None else ""
    if not opt.strip():
        return []
    if opt in current:
        current.discard(opt)
    else:
        current.add(opt)
    return [o for o in all_s if o in current]


def add_visible(all_options, selected, visible) -> list[str]:
    """把 ``visible`` 里的非空项并入选中集，保持 ``all_options`` 顺序。

    搜索过滤后「全选当前」用：只勾上此刻列表里看得到的项，不影响
    已被关键字藏起来的既有勾选。
    """
    all_s = [str(o) for o in (all_options or []) if str(o).strip()]
    current = {str(s) for s in (selected or []) if str(s).strip()}
    for opt in visible or []:
        s = str(opt)
        if s.strip():
            current.add(s)
    return [o for o in all_s if o in current]


def _resolve_lang(lang) -> str:
    """``lang`` 允许是字符串或返回字符串的可调用（语言切换后弹窗跟随当前
    语言）。与 date_picker._resolve_lang 同语义。"""
    try:
        value = lang() if callable(lang) else lang
    except Exception:
        value = "en"
    return value if value in _ALL_LABEL else "en"


# ---------------------------------------------------------------------------
# 弹窗
# ---------------------------------------------------------------------------
class _SearchPopup(tk.Toplevel):
    """锚定在 Combobox 下方的无边框搜索弹窗。

    顶部搜索框固定悬浮（pack 在列表之前，过滤只重绘列表），键入实时过滤。
    ``grab_set`` 把点击收进弹窗：窗内正常派发，窗外落点据此关闭 ——
    机制与 date_picker._CalendarPopup 完全一致。
    """

    _open_instance: "_SearchPopup | None" = None  # 全局单例，避免叠开多个

    def __init__(self, anchor, *, font_family, get_options, lang="en",
                 multi=False, get_selected=None, set_selected=None,
                 get_presets=None, save_presets=None):
        super().__init__(anchor.winfo_toplevel())
        self._anchor = anchor
        self._lang = _resolve_lang(lang)
        self._ff = font_family
        self._multi = bool(multi)
        self._set_selected = set_selected
        self._get_presets = get_presets
        self._save_presets = save_presets
        self._presets = []
        self._form_mode = None
        self._form_old_name = None
        self._preset_enabled = bool(self._multi and get_presets is not None)
        if self._preset_enabled:
            try:
                self._presets = list(get_presets() or [])
            except Exception:
                self._presets = []
        # 不能叫 ``_options``：会遮蔽 tkinter Misc._options 内部方法，
        # 之后任何 configure() 调用都会炸。
        raw = [str(o) for o in get_options()]
        if self._multi:
            # 多选的「全部」是空选，不占 options 槽；values 里遗留的空串丢掉。
            self._all_options = [o for o in raw if o.strip()]
        else:
            self._all_options = raw
        self._filtered = list(self._all_options)
        if self._multi:
            seed = get_selected() if get_selected else []
            self._selected = {str(s) for s in (seed or []) if str(s).strip()}
        else:
            self._selected = set()

        # 无边框 → 下拉菜单的观感；失败（个别 WM）则退化为普通 Toplevel。
        try:
            self.overrideredirect(True)
        except tk.TclError:
            pass
        self.configure(bg=_BORDER)  # 外层当 1px 边框
        try:
            self.transient(anchor.winfo_toplevel())
        except tk.TclError:
            pass

        frame = tk.Frame(self, bg=_POPUP_BG)
        frame.pack(fill="both", expand=True, padx=1, pady=1)

        # ── 顶部固定搜索行 ──
        head = tk.Frame(frame, bg=_POPUP_BG)
        head.pack(fill="x", padx=6, pady=(6, 4))
        tk.Label(head, text="\U0001F50D",  # 🔍
                 font=(self._ff, 10), bg=_POPUP_BG, fg=_FG_MUTED,
                 ).pack(side="left")
        self._query_var = tk.StringVar()
        self._entry = tk.Entry(
            head, textvariable=self._query_var, font=(self._ff, 10),
            bg=_ENTRY_BG, fg="#ffffff", insertbackground="#ffffff",
            relief="flat")
        self._entry.pack(side="left", fill="x", expand=True,
                         padx=(6, 0), ipady=3)
        self._query_var.trace_add("write", lambda *_a: self._refilter())

        if self._multi:
            tk.Label(frame, text=_MULTI_HINT[self._lang],
                     font=(self._ff, 8), bg=_POPUP_BG, fg=_FG_MUTED,
                     anchor="w").pack(fill="x", padx=8, pady=(0, 2))

        if self._preset_enabled:
            self._build_preset_bar(frame)

        # ── 过滤列表 + 滚动条 ──
        body = tk.Frame(frame, bg=_POPUP_BG)
        body.pack(fill="both", expand=True, padx=6,
                  pady=(0, 4 if self._multi else 6))
        extra = 4 if self._multi else 2  # ☐ + 空格
        width = max((len(display_label(o, self._lang)) for o in self._all_options),
                    default=0)
        select_bg = _MULTI_ACTIVE_BG if self._multi else _SELECTED_BG
        n_rows = len(self._all_options) + (1 if self._multi else 0)
        self._listbox = tk.Listbox(
            body, font=(self._ff, 10),
            height=min(_MAX_VISIBLE_ROWS, max(n_rows, 3)),
            width=min(max(width + extra, _MIN_WIDTH_CHARS), _MAX_WIDTH_CHARS),
            bg=_POPUP_BG, fg=_FG,
            selectbackground=select_bg, selectforeground="#ffffff",
            activestyle="none", relief="flat", bd=0,
            highlightthickness=0, takefocus=0)
        scroll = tk.Scrollbar(body, orient="vertical",
                              command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=scroll.set)
        self._listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        if self._multi:
            foot = tk.Frame(frame, bg=_POPUP_BG)
            foot.pack(fill="x", padx=6, pady=(0, 6))
            tk.Button(
                foot, text=_CLEAR_LABEL[self._lang],
                command=self._clear_selected, font=(self._ff, 9),
                bg=_POPUP_BG, fg=_FG_MUTED, activebackground=_MULTI_ACTIVE_BG,
                activeforeground="#ffffff", relief="flat", bd=0,
                padx=8, pady=2, cursor="hand2").pack(side="left")
            self._btn_check_visible = tk.Button(
                foot, text=_CHECK_VISIBLE_LABEL[self._lang],
                command=self._check_visible, font=(self._ff, 9),
                bg=_BORDER, fg="#ffffff", activebackground=_MULTI_ACTIVE_BG,
                activeforeground="#ffffff", relief="flat", bd=0,
                padx=8, pady=2, cursor="hand2")
            self._btn_check_visible.pack(side="left", padx=(6, 0))
            tk.Button(
                foot, text=_DONE_LABEL[self._lang],
                command=self._close, font=(self._ff, 9),
                bg=_SELECTED_BG, fg="#ffffff",
                activebackground="#c73a52", activeforeground="#ffffff",
                relief="flat", bd=0, padx=10, pady=2,
                cursor="hand2").pack(side="right")

        self._render_list(initial=True)
        self._place_below_anchor()

        self.bind("<Escape>", self._on_escape)
        # grab_set 后，窗内点击照常派发，窗外点击被重定向到本窗并落在窗体
        # 矩形之外 → 据此关闭（同 date_picker）。
        self.bind("<Button-1>", self._maybe_close_outside, add="+")
        self._entry.bind("<Return>", self._on_return)
        self._entry.bind("<Down>", lambda _e: self._move(1))
        self._entry.bind("<Up>", lambda _e: self._move(-1))
        self._listbox.bind("<ButtonRelease-1>", self._on_list_click)
        try:
            self.grab_set()
        except tk.TclError:
            pass

    # -- 可见行 --------------------------------------------------------------
    def _keyword(self) -> str:
        return (self._query_var.get() or "").strip()

    def _visible_options(self) -> list[str]:
        """当前 listbox 对应的 option 值（含多选的空「全部」行）。"""
        if not self._multi:
            return list(self._filtered)
        if not self._filtered and self._keyword():
            return []
        rows = []
        if not self._keyword():
            rows.append("")
        rows.extend(self._filtered)
        return rows

    def _is_checked(self, option: str) -> bool:
        if not str(option).strip():
            return not self._selected
        return option in self._selected

    def _ordered_selected(self) -> list[str]:
        return [o for o in self._all_options if o in self._selected]

    def _commit_live(self):
        ordered = self._ordered_selected()
        if self._set_selected is not None:
            try:
                self._set_selected(ordered)
                return
            except Exception:
                pass
        try:
            match = _pp.matching_name(ordered, self._presets)
            self._anchor.set(format_selection_summary(
                ordered, self._lang, preset_name=match))
        except tk.TclError:
            pass

    def _persist_presets(self):
        if self._save_presets is None:
            return
        try:
            self._save_presets(self._presets)
        except Exception:
            pass

    def _on_escape(self, _event=None):
        if self._form_mode:
            self._cancel_form()
            return "break"
        self._close()
        return "break"

    # -- Preset bar --------------------------------------------------------
    def _build_preset_bar(self, parent):
        wrap = tk.Frame(parent, bg=_POPUP_BG)
        wrap.pack(fill="x", padx=6, pady=(0, 4))
        bar = tk.Frame(wrap, bg=_ENTRY_BG, highlightbackground=_BORDER,
                       highlightthickness=1)
        bar.pack(fill="x")

        self._chip_bar = tk.Frame(bar, bg=_ENTRY_BG)
        self._chip_bar.pack(fill="x")
        self._btn_save = tk.Button(
            self._chip_bar, text=_SAVE_LABEL[self._lang],
            command=self._begin_save, font=(self._ff, 8),
            bg=_BORDER, fg="#ffffff",
            activebackground=_MULTI_ACTIVE_BG, activeforeground="#ffffff",
            relief="flat", bd=0, padx=8, pady=1, cursor="hand2")
        # Pack Save first so the chip row cannot squeeze it off the bar.
        self._btn_save.pack(side="right", padx=(4, 4), pady=3)
        self._chip_row = tk.Frame(self._chip_bar, bg=_ENTRY_BG)
        self._chip_row.pack(side="left", fill="x", expand=True,
                            padx=(4, 0), pady=3)

        self._form_bar = tk.Frame(bar, bg=_ENTRY_BG)
        self._form_label = tk.Label(
            self._form_bar, text=_NAME_LABEL[self._lang],
            font=(self._ff, 8), bg=_ENTRY_BG, fg=_FG_MUTED)
        self._form_label.pack(side="left", padx=(6, 4))
        self._form_var = tk.StringVar()
        self._form_entry = tk.Entry(
            self._form_bar, textvariable=self._form_var, font=(self._ff, 9),
            bg=_POPUP_BG, fg="#ffffff", insertbackground="#ffffff",
            relief="flat", width=16)
        self._form_entry.pack(side="left", fill="x", expand=True, ipady=2)
        self._form_entry.bind("<Return>", lambda _e: self._commit_form() or "break")
        self._form_entry.bind("<Escape>", lambda _e: self._on_form_escape())
        self._form_ok = tk.Button(
            self._form_bar, text=_SAVE_LABEL[self._lang],
            command=self._commit_form, font=(self._ff, 8),
            bg=_SELECTED_BG, fg="#ffffff",
            activebackground="#c73a52", activeforeground="#ffffff",
            relief="flat", bd=0, padx=8, pady=1, cursor="hand2")
        self._form_ok.pack(side="right", padx=(4, 4), pady=3)
        self._form_cancel = tk.Button(
            self._form_bar, text=_CANCEL_LABEL[self._lang],
            command=self._cancel_form, font=(self._ff, 8),
            bg=_ENTRY_BG, fg=_FG_MUTED,
            activebackground=_MULTI_ACTIVE_BG, activeforeground="#ffffff",
            relief="flat", bd=0, padx=6, pady=1, cursor="hand2")
        self._form_cancel.pack(side="right")

        self._render_presets()

    def _show_chip_bar(self):
        try:
            self._form_bar.pack_forget()
        except tk.TclError:
            pass
        self._form_mode = None
        self._form_old_name = None
        try:
            if not self._chip_bar.winfo_ismapped():
                self._chip_bar.pack(fill="x")
        except tk.TclError:
            pass
        self._render_presets()

    def _show_form(self, mode, *, prompt=None, seed="", ok_label=None):
        self._form_mode = mode
        try:
            self._chip_bar.pack_forget()
        except tk.TclError:
            pass
        self._form_label.configure(text=prompt or _NAME_LABEL[self._lang])
        show_entry = mode in ("save", "rename")
        try:
            if show_entry:
                if not self._form_entry.winfo_ismapped():
                    self._form_entry.pack(side="left", fill="x", expand=True,
                                          ipady=2)
                self._form_var.set(seed)
            else:
                self._form_entry.pack_forget()
        except tk.TclError:
            pass
        self._form_ok.configure(
            text=ok_label or _SAVE_LABEL[self._lang])
        try:
            if not self._form_bar.winfo_ismapped():
                self._form_bar.pack(fill="x")
        except tk.TclError:
            pass
        if show_entry:
            try:
                self._form_entry.focus_force()
                self._form_entry.select_range(0, "end")
            except tk.TclError:
                pass

    def _update_save_btn(self):
        if not self._preset_enabled:
            return
        try:
            self._btn_save.configure(
                state=("normal" if self._selected else "disabled"))
        except tk.TclError:
            pass

    def _chip_text(self, name: str) -> str:
        s = str(name or "").strip()
        if len(s) > 16:
            return s[:15] + "…"
        return s

    def _render_presets(self):
        if not self._preset_enabled or self._form_mode:
            return
        row = self._chip_row
        for w in row.winfo_children():
            w.destroy()
        self._update_save_btn()
        presets = list(self._presets)
        if not presets:
            n = len(self._selected)
            msg = (_EMPTY_PRESETS[self._lang].format(n=n) if n
                   else _EMPTY_PRESETS_NONE[self._lang])
            tk.Label(row, text=msg, font=(self._ff, 8),
                     bg=_ENTRY_BG, fg=_FG_MUTED, anchor="w"
                     ).pack(side="left")
            return
        match = _pp.matching_name(self._ordered_selected(), presets)
        visible = presets[:_MAX_VISIBLE_CHIPS]
        overflow = presets[_MAX_VISIBLE_CHIPS:]
        for preset in visible:
            self._add_chip(row, preset, active=(preset.get("name") == match))
        if overflow:
            mb = tk.Menubutton(
                row, text="▾", font=(self._ff, 8, "bold"),
                bg=_BORDER, fg="#ffffff",
                activebackground=_MULTI_ACTIVE_BG, activeforeground="#ffffff",
                relief="flat", bd=0, padx=6, pady=1, cursor="hand2")
            menu = tk.Menu(mb, tearoff=0, bg=_POPUP_BG, fg=_FG,
                           activebackground=_MULTI_ACTIVE_BG,
                           activeforeground="#ffffff")
            for preset in overflow:
                name = preset.get("name") or ""
                menu.add_command(
                    label=name,
                    command=lambda n=name: self._apply_preset(n))
            mb.configure(menu=menu)
            mb.pack(side="left", padx=(2, 0))

    def _add_chip(self, parent, preset, active=False):
        name = preset.get("name") or ""
        bg = _SELECTED_BG if active else _BORDER
        btn = tk.Button(
            parent, text=self._chip_text(name),
            command=lambda n=name: self._apply_preset(n),
            font=(self._ff, 8), bg=bg, fg="#ffffff",
            activebackground=_MULTI_ACTIVE_BG, activeforeground="#ffffff",
            relief="flat", bd=0, padx=6, pady=1, cursor="hand2")
        btn.pack(side="left", padx=(0, 3))
        btn.bind("<Button-3>", lambda e, n=name: self._chip_menu(e, n))

    def _chip_menu(self, event, name):
        menu = tk.Menu(self, tearoff=0, bg=_POPUP_BG, fg=_FG,
                       activebackground=_MULTI_ACTIVE_BG,
                       activeforeground="#ffffff")
        menu.add_command(label=_RENAME_LABEL[self._lang],
                         command=lambda: self._begin_rename(name))
        menu.add_command(label=_DELETE_LABEL[self._lang],
                         command=lambda: self._delete_named(name))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass
            try:
                self.grab_set()
            except tk.TclError:
                pass

    def _apply_preset(self, name):
        preset = _pp.find_preset(self._presets, name)
        if preset is None:
            return
        ids = _pp.apply_ids(preset.get("project_ids"), self._all_options)
        self._selected = set(ids)
        self._presets = _pp.touch_preset(self._presets, name)
        self._persist_presets()
        self._commit_live()
        try:
            self._query_var.set("")
        except tk.TclError:
            pass
        self._render_list()
        self._show_chip_bar()

    def _begin_save(self):
        if not self._selected:
            return
        self._form_old_name = None
        self._show_form("save")

    def _begin_rename(self, name):
        self._form_old_name = name
        self._show_form("rename", prompt=_RENAME_LABEL[self._lang], seed=name)

    def _delete_named(self, name):
        self._presets = _pp.delete_preset(self._presets, name)
        self._persist_presets()
        self._commit_live()
        self._show_chip_bar()

    def _on_form_escape(self, _event=None):
        self._cancel_form()
        return "break"

    def _cancel_form(self):
        self._show_chip_bar()
        try:
            self._entry.focus_force()
        except tk.TclError:
            pass

    def _commit_form(self):
        mode = self._form_mode
        if mode == "replace":
            self._finish_upsert(self._form_old_name)
            return
        name = (self._form_var.get() or "").strip()
        if mode == "rename":
            new, err = _pp.rename_preset(
                self._presets, self._form_old_name, name)
            if err == "duplicate":
                self._form_label.configure(text=_DUP_HINT[self._lang])
                return
            if err:
                return
            self._presets = new
            self._persist_presets()
            self._commit_live()
            self._show_chip_bar()
            return
        if mode != "save":
            return
        if _pp.find_preset(self._presets, name) is not None:
            self._form_old_name = name
            self._show_form(
                "replace",
                prompt=_REPLACE_PROMPT[self._lang].format(name=name),
                ok_label=_REPLACE_LABEL[self._lang])
            return
        self._finish_upsert(name)

    def _finish_upsert(self, name):
        new, err = _pp.upsert_preset(
            self._presets, name, self._ordered_selected())
        if err == "limit":
            self._show_form(
                "save",
                prompt=_LIMIT_HINT[self._lang].format(n=_pp.MAX_PRESETS),
                seed=name or "")
            return
        if err:
            return
        self._presets = new
        self._persist_presets()
        self._commit_live()
        self._show_chip_bar()

    # -- 渲染 --------------------------------------------------------------
    def _refilter(self):
        self._filtered = filter_options(self._all_options, self._query_var.get())
        self._render_list()

    def _render_list(self, initial=False, keep_idx=None, yview=None):
        lb = self._listbox
        lb.delete(0, "end")
        visible = self._visible_options()
        if self._multi:
            try:
                has_kw = bool(self._keyword())
                self._btn_check_visible.configure(
                    state=("normal" if has_kw and self._filtered else "disabled"))
            except tk.TclError:
                pass
        if not visible:
            # 提示行：灰字、选中态与底色相同（视觉上不可选），_pick 处会拦。
            lb.insert("end", _NO_MATCH_LABEL[self._lang])
            lb.itemconfigure(0, fg=_FG_MUTED,
                             selectbackground=_POPUP_BG,
                             selectforeground=_FG_MUTED)
            return
        for opt in visible:
            if self._multi:
                lb.insert("end", checked_label(
                    opt, self._is_checked(opt), self._lang))
            else:
                lb.insert("end", display_label(opt, self._lang))
        # 初次打开预选当前值；过滤后默认高亮第一项，Enter 即可拿走。
        idx = 0
        if keep_idx is not None:
            idx = max(0, min(int(keep_idx), len(visible) - 1))
        elif initial:
            if self._multi:
                for i, opt in enumerate(visible):
                    if opt and opt in self._selected:
                        idx = i
                        break
            else:
                current = self._anchor.get()
                if current in visible:
                    idx = visible.index(current)
        lb.selection_set(idx)
        lb.activate(idx)
        if yview is not None:
            try:
                lb.yview_moveto(yview[0])
            except (tk.TclError, IndexError, TypeError):
                lb.see(idx)
        else:
            lb.see(idx)

    # -- 行为 --------------------------------------------------------------
    def _move(self, delta):
        """↑/↓ 在过滤列表里移动高亮（焦点始终留在搜索框，可继续打字）。"""
        visible = self._visible_options()
        if not visible:
            return "break"
        cur = self._listbox.curselection()
        idx = (cur[0] if cur else -1) + delta
        idx = max(0, min(idx, len(visible) - 1))
        self._listbox.selection_clear(0, "end")
        self._listbox.selection_set(idx)
        self._listbox.activate(idx)
        self._listbox.see(idx)
        return "break"

    def _on_return(self, _event=None):
        if self._multi:
            self._toggle_active()
            return "break"
        return self._pick_active()

    def _pick_active(self, _event=None):
        visible = self._visible_options()
        if not visible:
            return "break"
        sel = self._listbox.curselection()
        self._pick(visible[sel[0] if sel else 0])
        return "break"

    def _on_list_click(self, event):
        visible = self._visible_options()
        if not visible:
            return
        idx = self._listbox.nearest(event.y)
        if not (0 <= idx < len(visible)):
            return
        if self._multi:
            self._toggle_at(idx)
        else:
            self._pick(visible[idx])

    def _toggle_active(self):
        visible = self._visible_options()
        if not visible:
            return
        sel = self._listbox.curselection()
        self._toggle_at(sel[0] if sel else 0)

    def _toggle_at(self, idx: int):
        visible = self._visible_options()
        if not (0 <= idx < len(visible)):
            return
        try:
            yview = self._listbox.yview()
        except tk.TclError:
            yview = None
        option = visible[idx]
        new = toggle_selected(self._all_options, self._ordered_selected(), option)
        self._selected = set(new)
        self._commit_live()
        self._render_list(keep_idx=idx, yview=yview)
        self._render_presets()

    def _clear_selected(self):
        self._selected.clear()
        self._commit_live()
        try:
            yview = self._listbox.yview()
            cur = self._listbox.curselection()
            keep = cur[0] if cur else 0
        except tk.TclError:
            yview, keep = None, 0
        self._render_list(keep_idx=keep, yview=yview)
        self._render_presets()

    def _check_visible(self):
        if not self._keyword() or not self._filtered:
            return
        new = add_visible(
            self._all_options, self._ordered_selected(), self._filtered)
        self._selected = set(new)
        self._commit_live()
        try:
            yview = self._listbox.yview()
            cur = self._listbox.curselection()
            keep = cur[0] if cur else 0
        except tk.TclError:
            yview, keep = None, 0
        self._render_list(keep_idx=keep, yview=yview)
        self._render_presets()

    def _pick(self, value: str):
        try:
            self._anchor.set(value)
            # 与原生选择行为对齐，绑定了该事件的调用方无感知差异。
            self._anchor.event_generate("<<ComboboxSelected>>")
        finally:
            self._close()

    def _maybe_close_outside(self, event):
        try:
            x0, y0 = self.winfo_rootx(), self.winfo_rooty()
            x1 = x0 + self.winfo_width()
            y1 = y0 + self.winfo_height()
            if not (x0 <= event.x_root <= x1 and y0 <= event.y_root <= y1):
                self._close()
        except tk.TclError:
            self._close()

    def _close(self):
        if self._multi:
            self._commit_live()
            try:
                self._anchor.event_generate("<<ComboboxSelected>>")
            except tk.TclError:
                pass
        if _SearchPopup._open_instance is self:
            _SearchPopup._open_instance = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _place_below_anchor(self):
        self.update_idletasks()
        try:
            ax, ay = self._anchor.winfo_rootx(), self._anchor.winfo_rooty()
            ah = self._anchor.winfo_height()
            w, h = self.winfo_reqwidth(), self.winfo_reqheight()
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            x = min(ax, sw - w - 4)
            y = ay + ah + 2
            if y + h > sh:           # 下方放不下 → 翻到上方
                y = max(0, ay - h - 2)
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass
        # overrideredirect 窗口没有 WM 管理的 z-order：主窗最大化时弹窗可能
        # 被压在主窗之下，配合 grab 表现为"点了没反应"。显式抬前 + 置顶 +
        # 把焦点直接压给搜索框（这正是本弹窗与日历弹窗的差异点：焦点必须
        # 落在输入框上，用户才能直接打字）。
        for _action in (
            self.lift,
            lambda: self.attributes("-topmost", True),
            self._entry.focus_force,
        ):
            try:
                _action()
            except tk.TclError:
                pass


# ---------------------------------------------------------------------------
# 对外便捷封装
# ---------------------------------------------------------------------------
def _open_popup(combobox, *, font_family, get_options, lang,
                multi=False, get_selected=None, set_selected=None,
                get_presets=None, save_presets=None):
    """打开（单例）搜索弹窗。已有打开的先关掉，避免叠加。"""
    prev = _SearchPopup._open_instance
    if prev is not None:
        try:
            prev._close()
        except Exception:
            pass
    popup = _SearchPopup(
        combobox, font_family=font_family, get_options=get_options,
        lang=_resolve_lang(lang), multi=multi,
        get_selected=get_selected, set_selected=set_selected,
        get_presets=get_presets, save_presets=save_presets)
    _SearchPopup._open_instance = popup
    return popup


def attach_search(combobox, *, font_family, lang="en", get_options=None,
                  multi=False, get_selected=None, set_selected=None,
                  get_presets=None, save_presets=None):
    """对一个 readonly ``ttk.Combobox`` 启用"关键字搜索"下拉。

    点击（以及焦点态按 ↓/空格）不再弹原生 popdown，而是弹出顶部带搜索框
    的过滤列表。widget 级绑定返回 ``"break"``，按 Tk 的 bindtags 顺序会
    拦下 ttk 内部 class binding 的 Post，原生列表不会同时冒出来。

    参数
    ----
    get_options : 可调用，返回选项列表；缺省每次打开时现读
                  ``combobox["values"]``，因此异步 configure(values=…)
                  之后无需任何通知。多选模式下会自动丢掉空字符串占位。
    lang        : ``"en"`` / ``"zh"``，或返回其一的可调用。
    multi       : True 时改为多选（勾选切换，空选 = 全部）。
    get_selected / set_selected
                : 多选时读写已选项目列表（``list[str]``）。``set_selected``
                  在每次勾选时调用，由调用方负责更新 Combobox 展示文案。
    get_presets / save_presets
                : 多选时读写命名项目组合。传入 ``get_presets`` 才会画出
                  Preset 条；``save_presets(list)`` 在增删改后立刻落盘。

    单选时 Combobox 的 textvariable / ``<<ComboboxSelected>>`` 语义保持不变。
    """
    if multi:
        fetch = get_options or (
            lambda: [v for v in combobox.cget("values") if str(v).strip()])
    else:
        fetch = get_options or (lambda: list(combobox.cget("values")))

    def _open(_event=None):
        try:
            if "disabled" in combobox.state():
                return "break"
        except tk.TclError:
            pass
        _open_popup(combobox, font_family=font_family,
                    get_options=fetch, lang=lang,
                    multi=multi, get_selected=get_selected,
                    set_selected=set_selected,
                    get_presets=get_presets, save_presets=save_presets)
        return "break"

    combobox.bind("<Button-1>", _open)
    combobox.bind("<Down>", _open)
    combobox.bind("<space>", _open)
    return combobox
