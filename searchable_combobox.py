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


def format_selection_summary(selected, lang: str = "en") -> str:
    """把已选项目压成 Combobox 的单行展示。

    - 空 / 全空白 → ``""``（与历史「不过滤」占位一致，界面显示为空）；
    - 单选 → 项目名本身（与改造前单选 Combobox 完全相同）；
    - 多选 → 本地化的 ``N selected`` / ``已选 N 项``（宽度 20 的框装不下
      两个仓库路径，打开下拉才能看具体勾选）。
    """
    items = [str(s) for s in (selected or []) if str(s).strip()]
    if not items:
        return ""
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
                 multi=False, get_selected=None, set_selected=None):
        super().__init__(anchor.winfo_toplevel())
        self._anchor = anchor
        self._lang = _resolve_lang(lang)
        self._ff = font_family
        self._multi = bool(multi)
        self._set_selected = set_selected
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

        self.bind("<Escape>", lambda _e: self._close())
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
            self._anchor.set(format_selection_summary(ordered, self._lang))
        except tk.TclError:
            pass

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
                multi=False, get_selected=None, set_selected=None):
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
        get_selected=get_selected, set_selected=set_selected)
    _SearchPopup._open_instance = popup
    return popup


def attach_search(combobox, *, font_family, lang="en", get_options=None,
                  multi=False, get_selected=None, set_selected=None):
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
                    set_selected=set_selected)
        return "break"

    combobox.bind("<Button-1>", _open)
    combobox.bind("<Down>", _open)
    combobox.bind("<space>", _open)
    return combobox
