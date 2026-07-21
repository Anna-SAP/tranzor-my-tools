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

交互：
    - 点击 Combobox（或焦点态按 ↓/空格）→ 弹出搜索弹窗，输入框自动聚焦；
    - 键入即过滤（大小写不敏感的包含匹配），列表实时刷新；
    - ↑/↓ 移动高亮，Enter 选中，Esc / 点击窗外关闭；
    - 选中后写回 Combobox 并派发 ``<<ComboboxSelected>>``，对既有代码透明。

对外接口：
    - :func:`attach_search` —— 对一个 readonly ``ttk.Combobox`` 启用搜索下拉，
      选项每次打开时从 ``combobox["values"]`` 现取，天然兼容异步加载。
    - :func:`filter_options` / :func:`display_label` —— 纯函数，不依赖 Tk，
      便于单元测试（见 test_searchable_combobox.py）。

弹窗骨架（无边框 Toplevel + grab_set + 点窗外关闭 + z-order 修复）与
date_picker._CalendarPopup 同一套路，颜色常量同样内联（不 import
export_gui，避免 tab ↔ export_gui 的循环依赖）。
"""
from __future__ import annotations

import tkinter as tk

# 深色主题取色 —— 与 export_gui 的 BG_CARD / ACCENT_BTN 保持一致（内联，
# 理由同 date_picker）。
_POPUP_BG = "#16213e"      # 卡片底
_BORDER = "#0f3460"        # 边框蓝
_FG = "#e0e0e0"            # 主文字
_FG_MUTED = "#7a8199"      # 次要文字 / 无匹配提示
_ENTRY_BG = "#0a0a1a"      # 输入框底（与应用各 Entry 一致）
_SELECTED_BG = "#e94560"   # 高亮（与 ACCENT_BTN 一致）

# 本地化标签。空字符串选项代表"不过滤"，列表里显示成占位文案而不是空行。
_ALL_LABEL = {"en": "(All)", "zh": "（全部）"}
_NO_MATCH_LABEL = {"en": "(no match)", "zh": "（无匹配）"}

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

    def __init__(self, anchor, *, font_family, get_options, lang="en"):
        super().__init__(anchor.winfo_toplevel())
        self._anchor = anchor
        self._lang = _resolve_lang(lang)
        self._ff = font_family
        # 不能叫 ``_options``：会遮蔽 tkinter Misc._options 内部方法，
        # 之后任何 configure() 调用都会炸。
        self._all_options = [str(o) for o in get_options()]
        self._filtered = list(self._all_options)

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

        # ── 过滤列表 + 滚动条 ──
        body = tk.Frame(frame, bg=_POPUP_BG)
        body.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        width = max((len(display_label(o, self._lang)) for o in self._all_options),
                    default=0)
        self._listbox = tk.Listbox(
            body, font=(self._ff, 10),
            height=min(_MAX_VISIBLE_ROWS, max(len(self._all_options), 3)),
            width=min(max(width + 2, _MIN_WIDTH_CHARS), _MAX_WIDTH_CHARS),
            bg=_POPUP_BG, fg=_FG,
            selectbackground=_SELECTED_BG, selectforeground="#ffffff",
            activestyle="none", relief="flat", bd=0,
            highlightthickness=0, takefocus=0)
        scroll = tk.Scrollbar(body, orient="vertical",
                              command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=scroll.set)
        self._listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._render_list(initial=True)
        self._place_below_anchor()

        self.bind("<Escape>", lambda _e: self._close())
        # grab_set 后，窗内点击照常派发，窗外点击被重定向到本窗并落在窗体
        # 矩形之外 → 据此关闭（同 date_picker）。
        self.bind("<Button-1>", self._maybe_close_outside, add="+")
        self._entry.bind("<Return>", self._pick_active)
        self._entry.bind("<Down>", lambda _e: self._move(1))
        self._entry.bind("<Up>", lambda _e: self._move(-1))
        self._listbox.bind("<ButtonRelease-1>", self._on_list_click)
        try:
            self.grab_set()
        except tk.TclError:
            pass

    # -- 渲染 --------------------------------------------------------------
    def _refilter(self):
        self._filtered = filter_options(self._all_options, self._query_var.get())
        self._render_list()

    def _render_list(self, initial=False):
        lb = self._listbox
        lb.delete(0, "end")
        if not self._filtered:
            # 提示行：灰字、选中态与底色相同（视觉上不可选），_pick 处会拦。
            lb.insert("end", _NO_MATCH_LABEL[self._lang])
            lb.itemconfigure(0, fg=_FG_MUTED,
                             selectbackground=_POPUP_BG,
                             selectforeground=_FG_MUTED)
            return
        for opt in self._filtered:
            lb.insert("end", display_label(opt, self._lang))
        # 初次打开预选当前值；过滤后默认高亮第一项，Enter 即可拿走。
        idx = 0
        if initial:
            current = self._anchor.get()
            if current in self._filtered:
                idx = self._filtered.index(current)
        lb.selection_set(idx)
        lb.activate(idx)
        lb.see(idx)

    # -- 行为 --------------------------------------------------------------
    def _move(self, delta):
        """↑/↓ 在过滤列表里移动高亮（焦点始终留在搜索框，可继续打字）。"""
        if not self._filtered:
            return "break"
        cur = self._listbox.curselection()
        idx = (cur[0] if cur else -1) + delta
        idx = max(0, min(idx, len(self._filtered) - 1))
        self._listbox.selection_clear(0, "end")
        self._listbox.selection_set(idx)
        self._listbox.activate(idx)
        self._listbox.see(idx)
        return "break"

    def _pick_active(self, _event=None):
        if not self._filtered:
            return "break"
        sel = self._listbox.curselection()
        self._pick(self._filtered[sel[0] if sel else 0])
        return "break"

    def _on_list_click(self, event):
        if not self._filtered:
            return
        idx = self._listbox.nearest(event.y)
        if 0 <= idx < len(self._filtered):
            self._pick(self._filtered[idx])

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
def _open_popup(combobox, *, font_family, get_options, lang):
    """打开（单例）搜索弹窗。已有打开的先关掉，避免叠加。"""
    prev = _SearchPopup._open_instance
    if prev is not None:
        try:
            prev._close()
        except Exception:
            pass
    popup = _SearchPopup(
        combobox, font_family=font_family, get_options=get_options,
        lang=_resolve_lang(lang))
    _SearchPopup._open_instance = popup
    return popup


def attach_search(combobox, *, font_family, lang="en", get_options=None):
    """对一个 readonly ``ttk.Combobox`` 启用"关键字搜索"下拉。

    点击（以及焦点态按 ↓/空格）不再弹原生 popdown，而是弹出顶部带搜索框
    的过滤列表。widget 级绑定返回 ``"break"``，按 Tk 的 bindtags 顺序会
    拦下 ttk 内部 class binding 的 Post，原生列表不会同时冒出来。

    参数
    ----
    get_options : 可调用，返回选项列表；缺省每次打开时现读
                  ``combobox["values"]``，因此异步 configure(values=…)
                  之后无需任何通知。
    lang        : ``"en"`` / ``"zh"``，或返回其一的可调用。

    Combobox 的 textvariable / ``<<ComboboxSelected>>`` 语义保持不变。
    """
    fetch = get_options or (lambda: list(combobox.cget("values")))

    def _open(_event=None):
        try:
            if "disabled" in combobox.state():
                return "break"
        except tk.TclError:
            pass
        _open_popup(combobox, font_family=font_family,
                    get_options=fetch, lang=lang)
        return "break"

    combobox.bind("<Button-1>", _open)
    combobox.bind("<Down>", _open)
    combobox.bind("<space>", _open)
    return combobox
