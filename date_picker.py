"""Self-contained dark-themed date picker — pure tkinter, zero dependencies.

应用里几处日期过滤字段（Human Revisions、TM & Context Insight）原本只能
手输 ``YYYY-MM-DD``，容易写错也不直观。这里提供一个轻量级日历弹窗 + 📅
触发按钮：点一下即可可视化选日期，同时**保留手动输入**（按钮只是补充，
不替换 Entry）。

为什么不用 ``tkcalendar``？
    - 它是第三方库，本机未安装，且会再拖入 ``babel`` 依赖；
    - 需要额外的 PyInstaller hook 才能正确打包；
    - 默认外观是亮色，套不进本应用的深色主题。
    纯 tkinter 自绘日历零依赖、不动打包、外观与现有深色卡片一致。

对外接口：
    - :func:`attach_calendar` —— 在 ``parent`` 里放一个 📅 按钮，点开锚定到
      指定 Entry 的日历弹窗。通过 ``get_value`` / ``set_value`` 回调读写当前
      值，因此同时兼容 ``Entry.insert/get`` 与 ``StringVar`` 两种写法。
    - :func:`parse_date` / :func:`shift_month` / :func:`month_weeks` —— 纯函数，
      不依赖 Tk，便于单元测试。

值格式统一为 ``%Y-%m-%d``（与两个 tab 现有字段一致）。
"""
from __future__ import annotations

import calendar
import tkinter as tk
from datetime import date, datetime

DATE_FMT = "%Y-%m-%d"

# 深色主题取色 —— 与 export_gui 的 BG_CARD / ACCENT / ACCENT_BTN 保持一致，
# 不 import export_gui（避免与 tab ↔ export_gui 的循环依赖），直接内联常量。
_POPUP_BG = "#16213e"      # 卡片底
_BORDER = "#0f3460"        # 边框 / 导航条蓝
_FG = "#e0e0e0"            # 主文字
_FG_MUTED = "#7a8199"      # 星期表头 / 非本月
_DAY_BG = "#16213e"
_DAY_ACTIVE = "#1f3a6a"    # hover
_TODAY_BG = "#243a63"      # 今天底色
_SELECTED_BG = "#e94560"   # 选中（与 ACCENT_BTN 一致）
_NAV_FG = "#ffffff"

# 星期 / 月份本地化标签。周一为一周起始（ISO）。
_WEEK_HEADERS = {
    "en": ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"],
    "zh": ["一", "二", "三", "四", "五", "六", "日"],
}
_TODAY_LABEL = {"en": "Today", "zh": "今天"}
_CLOSE_LABEL = {"en": "Close", "zh": "关闭"}
_MONTHS_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


# ---------------------------------------------------------------------------
# 纯函数（无 Tk 依赖，单元测试覆盖这里）
# ---------------------------------------------------------------------------
def parse_date(text: str | None) -> date | None:
    """把 ``YYYY-MM-DD``（或带时间的 ISO 串）解析成 :class:`date`；解析不出
    返回 ``None``。容忍前后空白与 ``YYYY-MM-DDTHH:MM`` 形式。"""
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None
    # 只取日期部分，容忍 "2026-05-20T10:00:00" / "2026-05-20 10:00"
    s = s.replace("T", " ").split(" ")[0]
    try:
        return datetime.strptime(s, DATE_FMT).date()
    except (ValueError, TypeError):
        return None


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """在 (year, month) 基础上前/后移 ``delta`` 个月，正确处理跨年。

    >>> shift_month(2026, 1, -1)
    (2025, 12)
    >>> shift_month(2026, 12, 1)
    (2027, 1)
    """
    idx = (year * 12 + (month - 1)) + delta
    return idx // 12, idx % 12 + 1


def month_weeks(year: int, month: int, firstweekday: int = 0):
    """返回该月按周分组的日期矩阵（每周 7 个整数，0 表示非本月占位）。

    ``firstweekday=0`` 即周一起始（与 :data:`_WEEK_HEADERS` 对齐）。
    """
    return calendar.Calendar(firstweekday).monthdayscalendar(year, month)


def format_month_title(year: int, month: int, lang: str = "en") -> str:
    if lang == "zh":
        return f"{year}年{month}月"
    return f"{_MONTHS_EN[month - 1]} {year}"


# ---------------------------------------------------------------------------
# 弹窗
# ---------------------------------------------------------------------------
class _CalendarPopup(tk.Toplevel):
    """锚定在某个 widget 下方的无边框日历弹窗。

    交互：上/下月导航、点日期即选中并关闭、Today 快捷、Esc 或点窗外关闭。
    用 ``grab_set`` 把点击事件收进弹窗，从而既能点选、也能在点窗外时关闭。
    """

    _open_instance: "_CalendarPopup | None" = None  # 全局单例，避免叠开多个

    def __init__(self, anchor, *, font_family, get_value, set_value,
                 lang="en"):
        super().__init__(anchor.winfo_toplevel())
        self._anchor = anchor
        self._set_value = set_value
        self._lang = lang if lang in _WEEK_HEADERS else "en"
        self._ff = font_family

        seed = parse_date(get_value() if callable(get_value) else None) \
            or date.today()
        self._view_year = seed.year
        self._view_month = seed.month
        self._selected = seed

        # 无边框 → 下拉日历的观感；失败（个别 WM）则退化为普通 Toplevel。
        try:
            self.overrideredirect(True)
        except tk.TclError:
            pass
        self.configure(bg=_BORDER)  # 外层当 1px 边框
        try:
            self.transient(anchor.winfo_toplevel())
        except tk.TclError:
            pass

        self._frame = tk.Frame(self, bg=_POPUP_BG)
        self._frame.pack(padx=1, pady=1)

        self._build_nav()
        self._grid_holder = tk.Frame(self._frame, bg=_POPUP_BG)
        self._grid_holder.pack(padx=8, pady=(2, 4))
        self._build_footer()
        self._render_grid()

        self._place_below_anchor()

        self.bind("<Escape>", lambda _e: self._close())
        # grab_set 后，窗内点击照常派发给日历按钮，窗外点击被重定向到本窗
        # 并落在窗体矩形之外 → 据此关闭。
        self.bind("<Button-1>", self._maybe_close_outside, add="+")
        try:
            self.grab_set()
        except tk.TclError:
            pass

    # -- 构造 --------------------------------------------------------------
    def _build_nav(self):
        nav = tk.Frame(self._frame, bg=_BORDER)
        nav.pack(fill="x")
        self._btn_prev = tk.Button(
            nav, text="◀", command=lambda: self._step_month(-1),
            font=(self._ff, 10, "bold"), bg=_BORDER, fg=_NAV_FG,
            activebackground=_DAY_ACTIVE, activeforeground=_NAV_FG,
            relief="flat", bd=0, padx=10, cursor="hand2")
        self._btn_prev.pack(side="left")
        self._lbl_title = tk.Label(
            nav, text="", font=(self._ff, 10, "bold"),
            bg=_BORDER, fg=_NAV_FG)
        self._lbl_title.pack(side="left", expand=True, fill="x", pady=4)
        self._btn_next = tk.Button(
            nav, text="▶", command=lambda: self._step_month(1),
            font=(self._ff, 10, "bold"), bg=_BORDER, fg=_NAV_FG,
            activebackground=_DAY_ACTIVE, activeforeground=_NAV_FG,
            relief="flat", bd=0, padx=10, cursor="hand2")
        self._btn_next.pack(side="right")

        hdr = tk.Frame(self._frame, bg=_POPUP_BG)
        hdr.pack(padx=8, pady=(6, 0))
        for i, wd in enumerate(_WEEK_HEADERS[self._lang]):
            tk.Label(hdr, text=wd, width=3, font=(self._ff, 9),
                     bg=_POPUP_BG, fg=_FG_MUTED).grid(row=0, column=i, padx=1)

    def _build_footer(self):
        foot = tk.Frame(self._frame, bg=_POPUP_BG)
        foot.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(
            foot, text=_TODAY_LABEL[self._lang],
            command=self._pick_today, font=(self._ff, 9),
            bg=_BORDER, fg=_NAV_FG, activebackground=_DAY_ACTIVE,
            activeforeground=_NAV_FG, relief="flat", bd=0,
            padx=10, pady=2, cursor="hand2").pack(side="left")
        tk.Button(
            foot, text=_CLOSE_LABEL[self._lang],
            command=self._close, font=(self._ff, 9),
            bg=_POPUP_BG, fg=_FG_MUTED, activebackground=_DAY_ACTIVE,
            activeforeground=_NAV_FG, relief="flat", bd=0,
            padx=10, pady=2, cursor="hand2").pack(side="right")

    def _render_grid(self):
        for w in self._grid_holder.winfo_children():
            w.destroy()
        self._lbl_title.configure(
            text=format_month_title(self._view_year, self._view_month,
                                    self._lang))
        today = date.today()
        weeks = month_weeks(self._view_year, self._view_month)
        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                if day == 0:
                    tk.Label(self._grid_holder, text="", width=3,
                             bg=_POPUP_BG).grid(row=r, column=c, padx=1, pady=1)
                    continue
                d = date(self._view_year, self._view_month, day)
                bg, fg = _DAY_BG, _FG
                if d == self._selected:
                    bg, fg = _SELECTED_BG, "#ffffff"
                elif d == today:
                    bg = _TODAY_BG
                btn = tk.Button(
                    self._grid_holder, text=str(day), width=3,
                    font=(self._ff, 9), bg=bg, fg=fg,
                    activebackground=_DAY_ACTIVE, activeforeground="#ffffff",
                    relief="flat", bd=0, cursor="hand2",
                    command=lambda dd=d: self._pick(dd))
                btn.grid(row=r, column=c, padx=1, pady=1)

    # -- 行为 --------------------------------------------------------------
    def _step_month(self, delta):
        self._view_year, self._view_month = shift_month(
            self._view_year, self._view_month, delta)
        self._render_grid()

    def _pick(self, d: date):
        try:
            self._set_value(d.strftime(DATE_FMT))
        finally:
            self._close()

    def _pick_today(self):
        self._pick(date.today())

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
        if _CalendarPopup._open_instance is self:
            _CalendarPopup._open_instance = None
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
        # 关键修复：overrideredirect 窗口不受窗口管理器管理，没有 z-order
        # 优先级。主窗最大化时，弹窗会被压在主窗**之下** —— 用户点 📅
        # 后“看不到日历”，加上 grab_set 把点击劫持到这个看不见的窗口，
        # 表现就是“点了没反应 / 卡住”。这正是之前的根因。显式抬到最前 +
        # 置顶 + 抢焦点，强制浮在主窗之上。
        for _action in (
            self.lift,
            lambda: self.attributes("-topmost", True),
            self.focus_force,
        ):
            try:
                _action()
            except tk.TclError:
                pass


def _resolve_lang(lang) -> str:
    """``lang`` 允许是字符串或返回字符串的可调用（这样语言切换后日历能跟随
    当前语言，而不是定格在 attach 那一刻）。"""
    try:
        value = lang() if callable(lang) else lang
    except Exception:
        value = "en"
    return value if value in _WEEK_HEADERS else "en"


def _open_calendar(anchor, *, font_family, get_value, set_value, lang="en"):
    """打开（单例）日历弹窗。已有打开的先关掉，避免叠加。"""
    prev = _CalendarPopup._open_instance
    if prev is not None:
        try:
            prev._close()
        except Exception:
            pass
        # 同一个按钮再次点击 → 视为收起，不再重开。
    popup = _CalendarPopup(
        anchor, font_family=font_family, get_value=get_value,
        set_value=set_value, lang=_resolve_lang(lang))
    _CalendarPopup._open_instance = popup
    return popup


# ---------------------------------------------------------------------------
# 对外便捷封装
# ---------------------------------------------------------------------------
def attach_calendar(parent, anchor, *, font_family, get_value, set_value,
                    lang="en", side="left", padx=(2, 8)):
    """在 ``parent`` 里创建并 pack 一个 📅 按钮，点击打开锚定到 ``anchor``
    （通常就是日期 Entry）下方的日历弹窗。

    参数
    ----
    get_value : 可调用，返回当前日期文本（用于定位弹窗初始月份/选中项）。
    set_value : 可调用，接收选中的 ``YYYY-MM-DD`` 字符串并写回字段。
    lang      : ``"en"`` / ``"zh"``，影响星期与月份标签。

    返回创建的 :class:`tk.Button`，调用方可保存引用以便后续刷新语言。
    """
    btn = tk.Button(
        parent, text="\U0001F4C5",  # 📅
        font=(font_family, 10), bg=_BORDER, fg="#ffffff",
        activebackground=_DAY_ACTIVE, activeforeground="#ffffff",
        relief="flat", bd=0, padx=6, pady=1, cursor="hand2",
        command=lambda: _open_calendar(
            anchor, font_family=font_family, get_value=get_value,
            set_value=set_value, lang=lang),
    )
    btn.pack(side=side, padx=padx)
    return btn
