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
    },
}


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
            for w in (self.btn_refresh, self.btn_export_sel, self.btn_export_all):
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
        """Apply the keyword filter to prod_tree without losing check state.

        Items are detached (not deleted), so their values — including the
        check glyph — survive across filter changes. ``_all_prod_iids``
        keeps the canonical insertion order so re-attached rows render in
        a stable sequence.
        """
        keyword = (self.var_prod_filter.get() or "").strip().lower()
        visible_idx = 0
        for iid in self._all_prod_iids:
            try:
                vals = self.prod_tree.item(iid, "values")
            except Exception:
                continue
            if not vals:
                continue
            label = str(vals[1]) if len(vals) >= 2 else ""
            match = (not keyword) or (keyword in label.lower())
            try:
                if match:
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
        for w in (self.btn_refresh, self.btn_export_sel, self.btn_export_all):
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
        self._do_export(all_selection=True)

    def _on_export_selected(self) -> None:
        self._do_export(all_selection=False)

    def _do_export(self, *, all_selection: bool) -> None:
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
        t = threading.Thread(
            target=self._run_export,
            args=(out_path, effective_sources, legacy_filter, mr_filter, locales),
            daemon=True,
        )
        t.start()

    def _run_export(self, out_path, sources, legacy_filter, mr_filter, locales) -> None:
        """Background: heavy fetch + zip build, scoped by user selection."""
        try:
            heavy_inv = _exp.collect_full_translations(
                sources=sources,
                progress_cb=self._log,
                legacy_project_filter=legacy_filter or None,
                mr_project_filter=mr_filter or None,
            )
            if not heavy_inv.data:
                self.parent.after(
                    0, self._on_export_done, None, self._t("ft_err_no_data"))
                return

            self.parent.after(
                0,
                lambda: self.lbl_status.configure(
                    text=self._t("ft_status_exporting"), foreground="#888"),
            )
            summary = _exp.build_ap_zip(
                heavy_inv,
                out_path=out_path,
                products=None,        # zip-side product filter no longer needed:
                                       #   the heavy fetch is already pre-filtered
                                       #   by project_id, and the zip's "product"
                                       #   axis is opus-id-derived.
                locales=locales,
                progress_cb=self._log,
            )
            self.parent.after(0, self._on_export_done, summary, None)
        except Exception as e:
            self.parent.after(0, self._on_export_done, None, str(e))

    def _on_export_done(self, summary, err) -> None:
        self._set_busy(False)
        if err:
            self.lbl_status.configure(
                text=f"❌ {err}", foreground="#e94560")
            return
        self.lbl_status.configure(
            text=self._t("ft_status_exported").format(path=summary["out_path"]),
            foreground="#2ecc71")
