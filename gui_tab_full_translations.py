"""
Full Translation Export — GUI Tab
==================================
按产品 × 按语言导出全量翻译（AP.zip 风格）。

加载语义（与用户需求严格对齐）:
    - **面板只是一个选择媒介**。Tab 第一次被切到时，自动调用
      :func:`export_full_translations.build_light_inventory`，仅拉取
      "产品列表 + 语言列表"两个维度（distinct 聚合查询，秒开），
      完全不触碰任何 /translations 端点。
    - 全量翻译数据**只在用户点击"Export Selected / Export All"时**才会
      被拉取，并且按当前选中的产品/语言做服务端预过滤，避免拉到不需要的任务。
    - 失败时不得阻塞主 GUI 启动。

暴露:
    FullTranslationsTab(parent, app)
    STRINGS  — 本 Tab 使用的 i18n 字符串（供 export_gui.STRINGS 合并）
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import date

try:
    import export_full_translations as _exp
except Exception:  # pragma: no cover — defensive
    _exp = None


# Unicode check glyphs used by the products / languages multi-select trees.
CHECK_OFF = "\u2610"  # ☐
CHECK_ON = "\u2611"   # ☑


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------

STRINGS = {
    "en": {
        "tab_full_translations":  "🌍 Full Translations",
        "ft_title":               "Full Translation Export (by Product × Language)",
        "ft_subtitle":            "Pick products + languages, then export — heavy data is fetched only on Export.",
        "ft_sources_label":       "Data Sources",
        "ft_src_legacy":          "File Translation (Legacy)",
        "ft_src_mr":              "MR Pipeline",
        "ft_refresh":             "🔄 Refresh Inventory",
        "ft_export_selected":     "📦 Export Selected",
        "ft_export_all":          "📦 Export All",
        "ft_merge_json":          "🧩 Merge to JSON",
        "ft_products":            "Products",
        "ft_locales":             "Languages",
        "ft_col_product":         "Product",
        "ft_col_keys":            "Keys",
        "ft_col_locale":          "Locale",
        "ft_status_idle":         "Loading inventory…",
        "ft_status_loading":      "Loading product & language inventory…",
        "ft_status_loaded":       "Inventory ready: {p} products · {l} languages",
        "ft_status_collecting":   "Fetching translations for selection…",
        "ft_status_exporting":    "Writing zip…",
        "ft_status_writing_json": "Writing merged JSON…",
        "ft_status_exported":     "✓ Exported to {path}",
        "ft_err_no_inv":          "Inventory not loaded yet. Click 'Refresh Inventory'.",
        "ft_err_no_selection":    "Please select at least one product and one language.",
        "ft_err_no_data":         "No translations matched the selection.",
        "ft_err_module":          "export_full_translations module failed to load.",
        "ft_select_all":          "Select All",
        "ft_clear_all":           "Clear",
        "ft_keys_pending":        "…",
        "ft_filter_label":        "Filter:",
        "ft_filter_hint":         "Type to filter products",
        "ft_selected_only":       "Selected only",
        "ft_progress_title":      "Export in progress",
        "ft_progress_phase_collect": "Fetching translations from Tranzor…",
        "ft_progress_phase_write":   "Writing output file…",
        "ft_progress_log_label":  "Activity log",
        "ft_result_title_ok":     "Export complete",
        "ft_result_title_err":    "Export failed",
        "ft_result_summary":      "Summary",
        "ft_result_per_product":  "Keys per product",
        "ft_result_per_locale":   "Keys per language",
        "ft_result_col_product":  "Product",
        "ft_result_col_locale":   "Language",
        "ft_result_col_keys_src": "Keys(en-US)",
        "ft_result_col_keys_loc": "Keys",
        "ft_result_open_folder":  "📂 Reveal in Explorer",
        "ft_result_open_file":    "📄 Open file",
        "ft_result_close":        "Close",
        "ft_result_lbl_path":     "Output:",
    },
    "zh": {
        "tab_full_translations":  "🌍 全量翻译",
        "ft_title":               "全量翻译导出（按产品 × 按语言）",
        "ft_subtitle":            "选择产品 + 语言后再导出 — 真正的翻译数据只在点击导出时才拉取。",
        "ft_sources_label":       "数据源",
        "ft_src_legacy":          "File Translation（Legacy）",
        "ft_src_mr":              "MR Pipeline",
        "ft_refresh":             "🔄 刷新清单",
        "ft_export_selected":     "📦 导出选中",
        "ft_export_all":          "📦 全部导出",
        "ft_merge_json":          "🧩 合并为 JSON",
        "ft_products":            "产品",
        "ft_locales":             "语言",
        "ft_col_product":         "产品",
        "ft_col_keys":            "Key 数",
        "ft_col_locale":          "语言代码",
        "ft_status_idle":         "正在加载清单…",
        "ft_status_loading":      "正在加载产品 / 语言清单…",
        "ft_status_loaded":       "清单就绪：{p} 个产品 · {l} 种语言",
        "ft_status_collecting":   "正在按选择拉取翻译数据…",
        "ft_status_exporting":    "正在写 zip…",
        "ft_status_writing_json": "正在写合并 JSON…",
        "ft_status_exported":     "✓ 已导出：{path}",
        "ft_err_no_inv":          "尚未加载清单，请点击「刷新清单」。",
        "ft_err_no_selection":    "请至少选择一个产品和一种语言。",
        "ft_err_no_data":         "选择范围内未聚合到任何翻译。",
        "ft_err_module":          "export_full_translations 模块加载失败。",
        "ft_select_all":          "全选",
        "ft_clear_all":           "清空",
        "ft_keys_pending":        "…",
        "ft_filter_label":        "过滤：",
        "ft_filter_hint":         "输入关键字过滤产品",
        "ft_selected_only":       "仅显示已选",
        "ft_progress_title":      "正在导出",
        "ft_progress_phase_collect": "正在从 Tranzor 拉取翻译数据…",
        "ft_progress_phase_write":   "正在写出文件…",
        "ft_progress_log_label":  "执行日志",
        "ft_result_title_ok":     "导出完成",
        "ft_result_title_err":    "导出失败",
        "ft_result_summary":      "汇总",
        "ft_result_per_product":  "每个产品的 Key 数",
        "ft_result_per_locale":   "每种语言的 Key 数",
        "ft_result_col_product":  "产品",
        "ft_result_col_locale":   "语言",
        "ft_result_col_keys_src": "Keys(en-US)",
        "ft_result_col_keys_loc": "Keys",
        "ft_result_open_folder":  "📂 在资源管理器中定位",
        "ft_result_open_file":    "📄 打开文件",
        "ft_result_close":        "关闭",
        "ft_result_lbl_path":     "输出文件：",
    },
}


# ---------------------------------------------------------------------------
# Helpers — reveal output file in the host OS file manager
# ---------------------------------------------------------------------------

def _reveal_in_file_manager(path: str) -> None:
    """Open the OS file manager with ``path`` selected if possible."""
    abs_path = os.path.abspath(path)
    try:
        if sys.platform == "win32":
            # explorer.exe /select,<path>  — keep "/select,<path>" as one
            # argv item so paths with spaces stay intact.
            subprocess.Popen(["explorer", f"/select,{abs_path}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", abs_path])
        else:
            # Linux / other: best-effort, open the parent directory.
            subprocess.Popen(["xdg-open", os.path.dirname(abs_path) or "."])
    except Exception:
        pass


def _open_file_with_default_app(path: str) -> None:
    abs_path = os.path.abspath(path)
    try:
        if sys.platform == "win32":
            os.startfile(abs_path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", abs_path])
        else:
            subprocess.Popen(["xdg-open", abs_path])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Export progress / result dialog
# ---------------------------------------------------------------------------

class _ExportProgressDialog:
    """Modal Toplevel that morphs from progress phase → result phase.

    Lifecycle:
        __init__       → progress phase: header, status, indeterminate
                         progressbar, scrollable log textbox.
        append_log()   → adds a line to the log (Tk-thread safe via after()).
        set_phase()    → changes the header status text.
        show_success() → swaps in the summary view (per-product / per-locale
                         breakdowns) plus Reveal / Open / Close buttons.
        show_error()   → red header + error message + Close button.

    Why one dialog instead of two: short exports flash the progress phase
    briefly and then jump to the result, while long exports keep the user
    informed; either way the user sees a single modal flow without losing
    the activity log.
    """

    def __init__(self, master, t_func, title: str) -> None:
        self.master = master
        self._t = t_func
        self._closed = False

        top = tk.Toplevel(master)
        self.top = top
        top.title(title)
        top.transient(master.winfo_toplevel())
        top.protocol("WM_DELETE_WINDOW", self._on_close_attempt)
        top.minsize(560, 380)
        try:
            top.configure(bg="#1f1f2e")
        except Exception:
            pass
        # Block parent interaction while exporting.
        try:
            top.grab_set()
        except Exception:
            pass

        # Header
        self.lbl_header = ttk.Label(
            top, text=title, style="Title.TLabel")
        self.lbl_header.pack(anchor="w", padx=14, pady=(12, 4))

        self.lbl_phase = ttk.Label(
            top, text=self._t("ft_progress_phase_collect"),
            style="Status.TLabel")
        self.lbl_phase.pack(anchor="w", padx=14, pady=(0, 8))

        # Progress phase frame -------------------------------------------
        self.frame_progress = ttk.Frame(top, style="App.TFrame")
        self.frame_progress.pack(fill="both", expand=True, padx=14)

        self.progressbar = ttk.Progressbar(
            self.frame_progress, mode="indeterminate", length=520)
        self.progressbar.pack(fill="x", pady=(0, 10))
        try:
            self.progressbar.start(80)
        except Exception:
            pass

        ttk.Label(
            self.frame_progress, text=self._t("ft_progress_log_label"),
            style="CardBold.TLabel").pack(anchor="w")
        log_box = ttk.Frame(self.frame_progress, style="App.TFrame")
        log_box.pack(fill="both", expand=True, pady=(2, 0))
        self.txt_log = tk.Text(
            log_box, height=12, wrap="none",
            bg="#11111a", fg="#cdd6f4", insertbackground="#cdd6f4",
            relief="flat", borderwidth=0)
        log_scroll = ttk.Scrollbar(log_box, orient="vertical",
                                    command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=log_scroll.set, state="disabled")
        self.txt_log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        # Result phase frame (hidden until completion) -------------------
        self.frame_result = ttk.Frame(top, style="App.TFrame")

        # Bottom button bar (replaced when transitioning phases) ---------
        self.frame_buttons = ttk.Frame(top, style="App.TFrame")
        self.frame_buttons.pack(fill="x", padx=14, pady=10)
        # No close button during the progress phase by design — completion
        # event installs Reveal / Open / Close buttons.

        self._center_on_master()

    # ---- placement -------------------------------------------------
    def _center_on_master(self) -> None:
        try:
            self.top.update_idletasks()
            mw = self.master.winfo_toplevel()
            x = mw.winfo_rootx() + (mw.winfo_width() - self.top.winfo_width()) // 2
            y = mw.winfo_rooty() + (mw.winfo_height() - self.top.winfo_height()) // 2
            self.top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except Exception:
            pass

    # ---- progress -------------------------------------------------
    def append_log(self, line: str) -> None:
        if self._closed:
            return
        try:
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", (line or "") + "\n")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        except Exception:
            pass

    def set_phase(self, text: str) -> None:
        if self._closed:
            return
        try:
            self.lbl_phase.configure(text=text)
        except Exception:
            pass

    # ---- transitions ----------------------------------------------
    def _stop_progressbar(self) -> None:
        try:
            self.progressbar.stop()
        except Exception:
            pass

    def show_success(self, summary: dict, mode: str) -> None:
        if self._closed:
            return
        self._stop_progressbar()
        try:
            self.lbl_header.configure(text=self._t("ft_result_title_ok"))
            self.lbl_phase.configure(text=self._t("ft_result_summary"))
        except Exception:
            pass

        # Tear down progress widgets and rebuild as the result view.
        try:
            self.frame_progress.pack_forget()
        except Exception:
            pass

        self._build_result_view(summary, mode, error=None)
        self._build_result_buttons(summary["out_path"])

    def show_error(self, err: str) -> None:
        if self._closed:
            return
        self._stop_progressbar()
        try:
            self.lbl_header.configure(text=self._t("ft_result_title_err"))
            self.lbl_phase.configure(
                text=str(err) or "Unknown error", foreground="#e94560")
        except Exception:
            pass
        # Keep the log visible (it usually has the traceback context),
        # just install a Close button.
        self._build_result_buttons(out_path=None)

    # ---- result view -----------------------------------------------
    def _build_result_view(self, summary: dict, mode: str, error) -> None:
        out_path = summary.get("out_path") or ""
        per_product = summary.get("per_product") or {}
        per_locale = summary.get("per_locale") or {}

        self.frame_result.pack(fill="both", expand=True, padx=14)

        # Path row
        path_row = ttk.Frame(self.frame_result, style="App.TFrame")
        path_row.pack(fill="x", pady=(0, 8))
        ttk.Label(path_row, text=self._t("ft_result_lbl_path"),
                  style="CardBold.TLabel").pack(side="left")
        ttk.Label(path_row, text=out_path,
                  style="Status.TLabel").pack(side="left", padx=(6, 0))

        # Summary line
        if mode == "json":
            line = (
                f"{self._t('ft_result_col_product')}: {len(per_product)}   "
                f"·   {self._t('ft_result_col_locale')}: {len(per_locale)}   "
                f"·   Records: {summary.get('records', 0):,}"
            )
        else:
            line = (
                f"{self._t('ft_result_col_product')}: {len(per_product)}   "
                f"·   {self._t('ft_result_col_locale')}: {len(per_locale)}   "
                f"·   Files: {summary.get('files', 0):,}   "
                f"·   Entries: {summary.get('entries', 0):,}"
            )
        ttk.Label(self.frame_result, text=line,
                  style="Status.TLabel").pack(anchor="w", pady=(0, 8))

        # Two side-by-side breakdown tables
        tables = ttk.Frame(self.frame_result, style="App.TFrame")
        tables.pack(fill="both", expand=True)

        self._make_breakdown_table(
            tables,
            title=self._t("ft_result_per_product"),
            label_col=self._t("ft_result_col_product"),
            count_col=self._t("ft_result_col_keys_src"),
            counts=per_product,
            side="left",
        )
        self._make_breakdown_table(
            tables,
            title=self._t("ft_result_per_locale"),
            label_col=self._t("ft_result_col_locale"),
            count_col=self._t("ft_result_col_keys_loc"),
            counts=per_locale,
            side="right",
        )

    def _make_breakdown_table(self, parent, *, title, label_col, count_col, counts, side) -> None:
        col = ttk.Frame(parent, style="App.TFrame")
        col.pack(side=side, fill="both", expand=True,
                 padx=(0, 6) if side == "left" else (6, 0))
        ttk.Label(col, text=title, style="CardBold.TLabel").pack(anchor="w")
        wrap = ttk.Frame(col, style="App.TFrame")
        wrap.pack(fill="both", expand=True, pady=(2, 0))
        tree = ttk.Treeview(
            wrap, columns=("label", "count"), show="headings", height=8)
        tree.heading("label", text=label_col)
        tree.heading("count", text=count_col)
        tree.column("label", width=180, anchor="w")
        tree.column("count", width=80, anchor="e")
        for label in sorted(counts.keys()):
            tree.insert("", "end", values=(label, f"{counts[label]:,}"))
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _build_result_buttons(self, out_path) -> None:
        # Replace the existing button frame contents.
        for w in self.frame_buttons.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

        if out_path:
            btn_reveal = ttk.Button(
                self.frame_buttons, text=self._t("ft_result_open_folder"),
                command=lambda p=out_path: _reveal_in_file_manager(p))
            btn_reveal.pack(side="left")
            btn_open = ttk.Button(
                self.frame_buttons, text=self._t("ft_result_open_file"),
                command=lambda p=out_path: _open_file_with_default_app(p))
            btn_open.pack(side="left", padx=(8, 0))

        btn_close = ttk.Button(
            self.frame_buttons, text=self._t("ft_result_close"),
            command=self.close)
        btn_close.pack(side="right")

    # ---- close -----------------------------------------------------
    def _on_close_attempt(self) -> None:
        # During the progress phase, ignore close to avoid orphaning the
        # background worker. After completion, _build_result_buttons gives
        # the user a real Close action.
        try:
            running = bool(self.progressbar.cget("mode") == "indeterminate"
                           and self.progressbar["value"] == 0
                           and self._closed is False)
        except Exception:
            running = not self._closed
        if running and self.frame_result.winfo_ismapped() is False:
            return
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_progressbar()
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tab
# ---------------------------------------------------------------------------

class FullTranslationsTab:
    """Self-contained Tab widget. parent = ttk.Frame inside notebook.

    Lifecycle:
        __init__         → only build widgets, no network calls
        on_first_show()  → fired by export_gui when the tab is first selected;
                           kicks off a single lightweight inventory load.
        Refresh button   → re-runs the same lightweight inventory load.
        Export buttons   → trigger the heavy /translations fetch on demand,
                           pre-filtered by current selection.
    """

    def __init__(self, parent: ttk.Frame, app) -> None:
        self.parent = parent
        self.app = app
        self._light_inv = None        # LightInventory or None — only selectors
        self._inv_loaded = False      # set True after first successful load
        self._busy = False
        self._first_show_pending = True
        # Full ordered list of product iids ever inserted into prod_tree.
        # Survives detach() so the filter can re-attach matching items, and
        # so _selected_product_ids() can read check state of items hidden
        # by the current filter.
        self._all_prod_iids: list = []
        # Active export progress dialog (None when no export is running).
        self._progress_dlg: _ExportProgressDialog | None = None

        self._build_ui()

    # ---- helpers ----------------------------------------------------
    def _t(self, key: str) -> str:
        return self.app._t(key)

    def _log(self, msg: str) -> None:
        # Append to the status label (short) and print (long).
        try:
            print(msg)
        except Exception:
            pass

    # ---- UI ---------------------------------------------------------
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.parent, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=8, pady=8)

        # Title
        self.lbl_title = ttk.Label(
            outer, text=self._t("ft_title"), style="Title.TLabel")
        self.lbl_title.pack(anchor="w")
        self.lbl_sub = ttk.Label(
            outer, text=self._t("ft_subtitle"), style="Subtitle.TLabel")
        self.lbl_sub.pack(anchor="w", pady=(2, 10))

        # Top bar: sources + refresh + export buttons
        top = ttk.Frame(outer, style="App.TFrame")
        top.pack(fill="x", pady=(0, 8))

        self.lbl_src = ttk.Label(top, text=self._t("ft_sources_label"),
                                  style="CardBold.TLabel")
        self.lbl_src.pack(side="left")

        self.var_src_legacy = tk.BooleanVar(value=True)
        self.var_src_mr = tk.BooleanVar(value=True)
        self.chk_legacy = ttk.Checkbutton(
            top, text=self._t("ft_src_legacy"),
            variable=self.var_src_legacy, style="Card.TCheckbutton")
        self.chk_legacy.pack(side="left", padx=(8, 0))
        self.chk_mr = ttk.Checkbutton(
            top, text=self._t("ft_src_mr"),
            variable=self.var_src_mr, style="Card.TCheckbutton")
        self.chk_mr.pack(side="left", padx=(8, 16))

        self.btn_refresh = self.app._create_button(
            top, text=self._t("ft_refresh"), command=self._on_refresh,
            style_name="Secondary", padx=14, pady=4)
        self.btn_refresh.pack(side="left", padx=(0, 8))

        self.btn_export_sel = self.app._create_button(
            top, text=self._t("ft_export_selected"),
            command=self._on_export_selected,
            style_name="Accent", padx=14, pady=4)
        self.btn_export_sel.pack(side="left", padx=(0, 8))

        self.btn_export_all = self.app._create_button(
            top, text=self._t("ft_export_all"),
            command=self._on_export_all,
            style_name="Success", padx=14, pady=4)
        self.btn_export_all.pack(side="left")

        self.btn_merge_json = self.app._create_button(
            top, text=self._t("ft_merge_json"),
            command=self._on_merge_json,
            style_name="Accent", padx=14, pady=4)
        self.btn_merge_json.pack(side="left", padx=(8, 0))

        # Body: two columns (products | languages)
        body = ttk.Frame(outer, style="App.TFrame")
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body, style="App.TFrame")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = ttk.Frame(body, style="App.TFrame", width=320)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        # Products (Treeview) — header row carries the section label and the
        # keyword filter input.
        prod_header = ttk.Frame(left, style="App.TFrame")
        prod_header.pack(fill="x", pady=(0, 4))
        self.lbl_prod = ttk.Label(prod_header, text=self._t("ft_products"),
                                   style="CardBold.TLabel")
        self.lbl_prod.pack(side="left")
        self.lbl_prod_filter = ttk.Label(
            prod_header, text=self._t("ft_filter_label"))
        self.lbl_prod_filter.pack(side="left", padx=(16, 6))
        self.var_prod_filter = tk.StringVar()
        self.ent_prod_filter = ttk.Entry(
            prod_header, textvariable=self.var_prod_filter)
        self.ent_prod_filter.pack(side="left", fill="x", expand=True)
        self.var_prod_filter.trace_add("write", self._on_filter_changed)
        # "Selected only" toggle: doubles as a quick way to verify what
        # the user has actually checked across a long product list.
        self.var_prod_selected_only = tk.BooleanVar(value=False)
        self.chk_prod_selected_only = ttk.Checkbutton(
            prod_header,
            text=self._t("ft_selected_only"),
            variable=self.var_prod_selected_only,
            style="Card.TCheckbutton",
            command=self._on_filter_changed,
        )
        self.chk_prod_selected_only.pack(side="left", padx=(8, 0))

        prod_frame = ttk.Frame(left, style="App.TFrame")
        prod_frame.pack(fill="both", expand=True)

        self.prod_tree = ttk.Treeview(
            prod_frame,
            columns=("check", "product", "keys"),
            show="headings",
            selectmode="browse",
        )
        self.prod_tree.heading("check", text="")
        self.prod_tree.heading("product", text=self._t("ft_col_product"))
        self.prod_tree.heading("keys", text=self._t("ft_col_keys"))
        self.prod_tree.column("check", width=32, anchor="center", stretch=False)
        self.prod_tree.column("product", width=220, anchor="w")
        self.prod_tree.column("keys", width=80, anchor="e")
        self.prod_tree.bind("<ButtonRelease-1>", self._on_prod_click)
        prod_scroll = ttk.Scrollbar(prod_frame, orient="vertical",
                                     command=self.prod_tree.yview)
        self.prod_tree.configure(yscrollcommand=prod_scroll.set)
        self.prod_tree.pack(side="left", fill="both", expand=True)
        prod_scroll.pack(side="right", fill="y")

        prod_btns = ttk.Frame(left, style="App.TFrame")
        prod_btns.pack(fill="x", pady=(4, 0))
        self.btn_prod_all = self.app._create_button(
            prod_btns, text=self._t("ft_select_all"),
            command=lambda: self._check_all(self.prod_tree, True),
            style_name="SecondaryTiny", padx=10, pady=2)
        self.btn_prod_all.pack(side="left")
        self.btn_prod_clear = self.app._create_button(
            prod_btns, text=self._t("ft_clear_all"),
            command=lambda: self._check_all(self.prod_tree, False),
            style_name="SecondaryTiny", padx=10, pady=2)
        self.btn_prod_clear.pack(side="left", padx=(6, 0))

        # Locales (Treeview with checkbox column)
        self.lbl_loc = ttk.Label(right, text=self._t("ft_locales"),
                                  style="CardBold.TLabel")
        self.lbl_loc.pack(anchor="w", pady=(0, 4))

        loc_frame = ttk.Frame(right, style="App.TFrame")
        loc_frame.pack(fill="both", expand=True)

        self.loc_tree = ttk.Treeview(
            loc_frame,
            columns=("check", "locale"),
            show="headings",
            selectmode="browse",
        )
        self.loc_tree.heading("check", text="")
        self.loc_tree.heading("locale", text=self._t("ft_col_locale"))
        self.loc_tree.column("check", width=32, anchor="center", stretch=False)
        self.loc_tree.column("locale", width=240, anchor="w")
        self.loc_tree.bind("<ButtonRelease-1>", self._on_loc_click)
        loc_scroll = ttk.Scrollbar(loc_frame, orient="vertical",
                                    command=self.loc_tree.yview)
        self.loc_tree.configure(yscrollcommand=loc_scroll.set)
        self.loc_tree.pack(side="left", fill="both", expand=True)
        loc_scroll.pack(side="right", fill="y")

        loc_btns = ttk.Frame(right, style="App.TFrame")
        loc_btns.pack(fill="x", pady=(4, 0))
        self.btn_loc_all = self.app._create_button(
            loc_btns, text=self._t("ft_select_all"),
            command=lambda: self._check_all(self.loc_tree, True),
            style_name="SecondaryTiny", padx=10, pady=2)
        self.btn_loc_all.pack(side="left")
        self.btn_loc_clear = self.app._create_button(
            loc_btns, text=self._t("ft_clear_all"),
            command=lambda: self._check_all(self.loc_tree, False),
            style_name="SecondaryTiny", padx=10, pady=2)
        self.btn_loc_clear.pack(side="left", padx=(6, 0))

        # Status bar
        self.lbl_status = ttk.Label(
            outer, text=self._t("ft_status_idle"), style="Status.TLabel")
        self.lbl_status.pack(anchor="w", pady=(10, 0))

        if _exp is None:
            self.lbl_status.configure(
                text=self._t("ft_err_module"), foreground="#e94560")
            for w in (self.btn_refresh, self.btn_export_sel, self.btn_export_all,
                      self.btn_merge_json):
                try:
                    w.configure(state="disabled")
                except Exception:
                    pass

    # ---- i18n refresh ----------------------------------------------
    def refresh_text(self) -> None:
        try:
            self.lbl_title.configure(text=self._t("ft_title"))
            self.lbl_sub.configure(text=self._t("ft_subtitle"))
            self.lbl_src.configure(text=self._t("ft_sources_label"))
            self.chk_legacy.configure(text=self._t("ft_src_legacy"))
            self.chk_mr.configure(text=self._t("ft_src_mr"))
            self.lbl_prod.configure(text=self._t("ft_products"))
            self.lbl_prod_filter.configure(text=self._t("ft_filter_label"))
            self.chk_prod_selected_only.configure(
                text=self._t("ft_selected_only"))
            self.lbl_loc.configure(text=self._t("ft_locales"))
            self.prod_tree.heading("product", text=self._t("ft_col_product"))
            self.prod_tree.heading("keys", text=self._t("ft_col_keys"))
            self.loc_tree.heading("locale", text=self._t("ft_col_locale"))
            if self._light_inv is None:
                self.lbl_status.configure(text=self._t("ft_status_idle"))
            try:
                self.btn_refresh.configure(text=self._t("ft_refresh"))
                self.btn_export_sel.configure(text=self._t("ft_export_selected"))
                self.btn_export_all.configure(text=self._t("ft_export_all"))
                self.btn_merge_json.configure(text=self._t("ft_merge_json"))
                self.btn_prod_all.configure(text=self._t("ft_select_all"))
                self.btn_prod_clear.configure(text=self._t("ft_clear_all"))
                self.btn_loc_all.configure(text=self._t("ft_select_all"))
                self.btn_loc_clear.configure(text=self._t("ft_clear_all"))
            except Exception:
                pass
        except Exception:
            pass

    # ---- public lifecycle hook --------------------------------------
    def on_first_show(self) -> None:
        """Called by export_gui the first time this tab is selected.

        Triggers exactly one lightweight inventory load. Subsequent tab
        switches do nothing — the user can hit "Refresh Inventory" to reload.
        """
        if not self._first_show_pending:
            return
        self._first_show_pending = False
        self._on_refresh()

    # ---- check helpers (multi-select via checkboxes) ----------------
    def _set_check(self, tree: ttk.Treeview, iid: str, on: bool) -> None:
        vals = list(tree.item(iid, "values"))
        if not vals:
            return
        vals[0] = CHECK_ON if on else CHECK_OFF
        tree.item(iid, values=vals)

    def _toggle_check(self, tree: ttk.Treeview, iid: str) -> None:
        vals = list(tree.item(iid, "values"))
        if not vals:
            return
        vals[0] = CHECK_OFF if vals[0] == CHECK_ON else CHECK_ON
        tree.item(iid, values=vals)

    def _check_all(self, tree: ttk.Treeview, on: bool = True) -> None:
        for iid in tree.get_children():
            self._set_check(tree, iid, on)

    def _checked_iids(self, tree: ttk.Treeview) -> list:
        out = []
        for iid in tree.get_children():
            vals = tree.item(iid, "values")
            if vals and vals[0] == CHECK_ON:
                out.append(iid)
        return out

    def _on_prod_click(self, event) -> None:
        # Header click → identify_row returns "" → no-op.
        iid = self.prod_tree.identify_row(event.y)
        if not iid:
            return
        self._toggle_check(self.prod_tree, iid)

    def _on_loc_click(self, event) -> None:
        iid = self.loc_tree.identify_row(event.y)
        if not iid:
            return
        self._toggle_check(self.loc_tree, iid)

    # ---- product filter ---------------------------------------------
    def _on_filter_changed(self, *_args) -> None:
        self._render_products_filter()

    def _render_products_filter(self) -> None:
        """Apply the active filters to prod_tree without losing check state.

        Two filters compose:
            - keyword (substring match against product label)
            - "selected only" toggle (only rows currently checked)

        Items are detached (not deleted), so their values — including the
        check glyph — survive across filter changes. ``_all_prod_iids``
        keeps the canonical insertion order so re-attached rows render in
        a stable sequence.
        """
        keyword = (self.var_prod_filter.get() or "").strip().lower()
        selected_only = bool(self.var_prod_selected_only.get())
        visible_idx = 0
        for iid in self._all_prod_iids:
            try:
                vals = self.prod_tree.item(iid, "values")
            except Exception:
                continue
            if not vals:
                continue
            label = str(vals[1]) if len(vals) >= 2 else ""
            checked = vals[0] == CHECK_ON
            keyword_ok = (not keyword) or (keyword in label.lower())
            selected_ok = (not selected_only) or checked
            try:
                if keyword_ok and selected_ok:
                    self.prod_tree.move(iid, "", visible_idx)
                    visible_idx += 1
                else:
                    self.prod_tree.detach(iid)
            except Exception:
                pass

    # ---- actions ----------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for w in (self.btn_refresh, self.btn_export_sel, self.btn_export_all,
                  self.btn_merge_json):
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _selected_sources(self):
        sources = []
        if self.var_src_legacy.get():
            sources.append("legacy")
        if self.var_src_mr.get():
            sources.append("mr")
        return sources or ["legacy", "mr"]

    # ---- inventory load (LIGHT — selectors only) --------------------
    def _on_refresh(self) -> None:
        """(Re)load the lightweight Product × Language inventory.

        Critically: this does NOT touch any /translations endpoint. It only
        hits cheap distinct/aggregate APIs to fill the selector widgets.
        """
        if _exp is None or self._busy:
            return
        sources = self._selected_sources()
        self._set_busy(True)
        self.lbl_status.configure(
            text=self._t("ft_status_loading"), foreground="#888")
        t = threading.Thread(
            target=self._run_light_refresh, args=(sources,), daemon=True)
        t.start()

    def _run_light_refresh(self, sources) -> None:
        try:
            inv = _exp.build_light_inventory(
                sources=sources, progress_cb=self._log)
            self.parent.after(0, self._on_light_refresh_done, inv, None)
        except Exception as e:
            self.parent.after(0, self._on_light_refresh_done, None, str(e))

    def _on_light_refresh_done(self, inv, err) -> None:
        self._set_busy(False)
        if err:
            self.lbl_status.configure(
                text=f"❌ {err}", foreground="#e94560")
            return

        self._light_inv = inv
        self._inv_loaded = True

        # Fill product tree — iid = stable encoded product id, all checked.
        self.prod_tree.delete(*self.prod_tree.get_children())
        self._all_prod_iids = []
        pending = self._t("ft_keys_pending")
        for p in inv.products:
            count = p.get("entry_count")
            keys_cell = f"{count:,}" if isinstance(count, int) else pending
            self.prod_tree.insert(
                "", "end", iid=p["id"],
                values=(CHECK_ON, p["label"], keys_cell))
            self._all_prod_iids.append(p["id"])

        # Honour any keyword the user typed before the inventory finished
        # loading (the entry stays editable while the background load runs).
        self._render_products_filter()

        # Fill locale tree — iid = locale code, all checked.
        self.loc_tree.delete(*self.loc_tree.get_children())
        for loc in inv.locales:
            self.loc_tree.insert(
                "", "end", iid=loc, values=(CHECK_ON, loc))

        self.lbl_status.configure(
            text=self._t("ft_status_loaded").format(
                p=len(inv.products), l=len(inv.locales)),
            foreground="#2ecc71")

    # ---- selection helpers ------------------------------------------
    def _selected_product_ids(self):
        # Iterate the canonical iid list (not get_children, which only
        # returns visible rows) so a product hidden by the active filter
        # but already checked still counts.
        out = []
        for iid in self._all_prod_iids:
            try:
                vals = self.prod_tree.item(iid, "values")
            except Exception:
                continue
            if vals and vals[0] == CHECK_ON:
                out.append(iid)
        return out

    def _selected_locales(self):
        return self._checked_iids(self.loc_tree)

    # ---- export (HEAVY — only on click) -----------------------------
    def _on_export_all(self) -> None:
        self._do_export(all_selection=True, mode="zip")

    def _on_export_selected(self) -> None:
        self._do_export(all_selection=False, mode="zip")

    def _on_merge_json(self) -> None:
        # The merged JSON is meant for downstream QA / global search; the
        # natural scope is "whatever the user has currently checked", so we
        # always go through the selection path. To merge everything they
        # can hit Select All on both panels first.
        self._do_export(all_selection=False, mode="json")

    def _do_export(self, *, all_selection: bool, mode: str = "zip") -> None:
        if _exp is None or self._busy:
            return
        if not self._inv_loaded or self._light_inv is None:
            messagebox.showwarning(
                "Full Translations", self._t("ft_err_no_inv"))
            return

        sources = self._selected_sources()

        if all_selection:
            # Use everything currently in the light inventory.
            selected_ids = self._light_inv.product_ids()
            locales = list(self._light_inv.locales) or None
        else:
            selected_ids = self._selected_product_ids()
            locales = self._selected_locales() or None
            if not selected_ids or not locales:
                messagebox.showwarning(
                    "Full Translations", self._t("ft_err_no_selection"))
                return

        legacy_filter, mr_filter = self._light_inv.split_selection(selected_ids)
        # Restrict the source list to what the user actually selected.
        effective_sources = []
        if "legacy" in sources and legacy_filter:
            effective_sources.append("legacy")
        if "mr" in sources and mr_filter:
            effective_sources.append("mr")
        if not effective_sources:
            messagebox.showwarning(
                "Full Translations", self._t("ft_err_no_selection"))
            return

        if mode == "json":
            default_name = f"MergedTranslations_{date.today().strftime('%Y%m%d')}.json"
            out_path = filedialog.asksaveasfilename(
                title="Save Merged Translations JSON",
                defaultextension=".json",
                filetypes=[("JSON files", "*.json")],
                initialfile=default_name,
            )
        else:
            default_name = f"FullTranslations_{date.today().strftime('%Y%m%d')}.zip"
            out_path = filedialog.asksaveasfilename(
                title="Save Full Translations ZIP",
                defaultextension=".zip",
                filetypes=[("Zip archives", "*.zip")],
                initialfile=default_name,
            )
        if not out_path:
            return

        self._set_busy(True)
        self.lbl_status.configure(
            text=self._t("ft_status_collecting"), foreground="#888")

        # Open the modal progress dialog. The background worker pipes
        # progress callbacks here via _dialog_log so the user can follow
        # exactly what is happening.
        self._progress_dlg = _ExportProgressDialog(
            self.parent, self._t, title=self._t("ft_progress_title"))

        t = threading.Thread(
            target=self._run_export,
            args=(out_path, mode, effective_sources, legacy_filter, mr_filter, locales),
            daemon=True,
        )
        t.start()

    def _dialog_log(self, line: str) -> None:
        """Thread-safe progress callback that fans out to dialog + console."""
        try:
            print(line)
        except Exception:
            pass
        dlg = self._progress_dlg
        if dlg is None:
            return
        try:
            self.parent.after(0, dlg.append_log, line)
        except Exception:
            pass

    def _run_export(self, out_path, mode, sources, legacy_filter, mr_filter, locales) -> None:
        """Background: heavy fetch + (zip | merged-json) build, scoped by selection."""
        dlg = self._progress_dlg
        try:
            if dlg is not None:
                self.parent.after(
                    0, dlg.set_phase, self._t("ft_progress_phase_collect"))

            heavy_inv = _exp.collect_full_translations(
                sources=sources,
                progress_cb=self._dialog_log,
                legacy_project_filter=legacy_filter or None,
                mr_project_filter=mr_filter or None,
            )
            if not heavy_inv.data:
                self.parent.after(
                    0, self._on_export_done, None, self._t("ft_err_no_data"))
                return

            writing_key = (
                "ft_status_writing_json" if mode == "json"
                else "ft_status_exporting"
            )
            self.parent.after(
                0,
                lambda: self.lbl_status.configure(
                    text=self._t(writing_key), foreground="#888"),
            )
            if dlg is not None:
                self.parent.after(
                    0, dlg.set_phase, self._t("ft_progress_phase_write"))

            # The heavy fetch is already pre-filtered by project_id; the
            # AP-style "product" axis is opus-id-derived, and the merged
            # JSON ignores product boundaries entirely. Either way pass
            # products=None and let locales drive the slicing.
            if mode == "json":
                summary = _exp.build_merged_json(
                    heavy_inv,
                    out_path=out_path,
                    products=None,
                    locales=locales,
                    progress_cb=self._dialog_log,
                )
            else:
                summary = _exp.build_ap_zip(
                    heavy_inv,
                    out_path=out_path,
                    products=None,
                    locales=locales,
                    progress_cb=self._dialog_log,
                )
            # Stash mode so _on_export_done can render the right summary view.
            summary["_mode"] = mode
            self.parent.after(0, self._on_export_done, summary, None)
        except Exception as e:
            self.parent.after(0, self._on_export_done, None, str(e))

    def _on_export_done(self, summary, err) -> None:
        self._set_busy(False)
        dlg = self._progress_dlg
        if err:
            self.lbl_status.configure(
                text=f"❌ {err}", foreground="#e94560")
            if dlg is not None:
                dlg.show_error(err)
            return
        self.lbl_status.configure(
            text=self._t("ft_status_exported").format(path=summary["out_path"]),
            foreground="#2ecc71")
        if dlg is not None:
            mode = summary.get("_mode", "zip")
            dlg.show_success(summary, mode=mode)
