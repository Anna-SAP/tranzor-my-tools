"""
Full Translation Export — GUI Tab
==================================
一个独立的 ttk Tab：按产品 × 按语言导出全量翻译（AP.zip 风格）。

设计:
    - 只被 export_gui.py 在第 4 个 Tab 位置懒加载使用；
    - 不修改任何现有模块；失败时不得阻塞主 GUI 启动。

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


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------

STRINGS = {
    "en": {
        "tab_full_translations":  "🌍 Full Translations",
        "ft_title":               "Full Translation Export (by Product × Language)",
        "ft_subtitle":            "Aggregate all completed tasks and export an AP.zip-style package.",
        "ft_sources_label":       "Data Sources",
        "ft_src_legacy":          "File Translation (Legacy)",
        "ft_src_mr":              "MR Pipeline",
        "ft_refresh":             "🔄 Refresh Inventory",
        "ft_export_selected":     "📦 Export Selected",
        "ft_export_all":          "📦 Export All",
        "ft_products":            "Products (by key count)",
        "ft_locales":             "Languages",
        "ft_col_product":         "Product",
        "ft_col_keys":             "Keys",
        "ft_status_idle":         "Click 'Refresh Inventory' to load data.",
        "ft_status_loading":      "Aggregating… this may take a while.",
        "ft_status_loaded":       "Loaded {p} products · {l} languages · {n} entries",
        "ft_status_exporting":    "Writing zip…",
        "ft_status_exported":     "✓ Exported to {path}",
        "ft_err_no_inv":          "Inventory not loaded yet. Click 'Refresh Inventory' first.",
        "ft_err_no_selection":    "Please select at least one product and one language.",
        "ft_err_module":          "export_full_translations module failed to load.",
        "ft_select_all":          "Select All",
        "ft_clear_all":           "Clear",
    },
    "zh": {
        "tab_full_translations":  "🌍 全量翻译",
        "ft_title":               "全量翻译导出（按产品 × 按语言）",
        "ft_subtitle":            "聚合所有 Completed tasks，导出 AP.zip 风格数据包。",
        "ft_sources_label":       "数据源",
        "ft_src_legacy":          "File Translation（Legacy）",
        "ft_src_mr":              "MR Pipeline",
        "ft_refresh":             "🔄 刷新清单",
        "ft_export_selected":     "📦 导出选中",
        "ft_export_all":          "📦 全部导出",
        "ft_products":            "产品（按 Key 数量降序）",
        "ft_locales":             "语言",
        "ft_col_product":         "产品",
        "ft_col_keys":             "Key 数",
        "ft_status_idle":         "点击「刷新清单」以加载数据。",
        "ft_status_loading":      "聚合中…耗时可能较长。",
        "ft_status_loaded":       "已加载：{p} 个产品 · {l} 种语言 · {n} 条翻译",
        "ft_status_exporting":    "正在写 zip…",
        "ft_status_exported":     "✓ 已导出：{path}",
        "ft_err_no_inv":          "尚未加载清单，请先点击「刷新清单」。",
        "ft_err_no_selection":    "请至少选择一个产品和一种语言。",
        "ft_err_module":          "export_full_translations 模块加载失败。",
        "ft_select_all":          "全选",
        "ft_clear_all":           "清空",
    },
}


# ---------------------------------------------------------------------------
# Tab
# ---------------------------------------------------------------------------

class FullTranslationsTab:
    """Self-contained Tab widget. parent = ttk.Frame inside notebook."""

    def __init__(self, parent: ttk.Frame, app) -> None:
        self.parent = parent
        self.app = app
        self._inventory = None
        self._busy = False

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

        # Products (Treeview)
        self.lbl_prod = ttk.Label(left, text=self._t("ft_products"),
                                   style="CardBold.TLabel")
        self.lbl_prod.pack(anchor="w", pady=(0, 4))

        prod_frame = ttk.Frame(left, style="App.TFrame")
        prod_frame.pack(fill="both", expand=True)

        self.prod_tree = ttk.Treeview(
            prod_frame,
            columns=("product", "keys"),
            show="headings",
            selectmode="extended",
        )
        self.prod_tree.heading("product", text=self._t("ft_col_product"))
        self.prod_tree.heading("keys", text=self._t("ft_col_keys"))
        self.prod_tree.column("product", width=220, anchor="w")
        self.prod_tree.column("keys", width=80, anchor="e")
        prod_scroll = ttk.Scrollbar(prod_frame, orient="vertical",
                                     command=self.prod_tree.yview)
        self.prod_tree.configure(yscrollcommand=prod_scroll.set)
        self.prod_tree.pack(side="left", fill="both", expand=True)
        prod_scroll.pack(side="right", fill="y")

        prod_btns = ttk.Frame(left, style="App.TFrame")
        prod_btns.pack(fill="x", pady=(4, 0))
        self.btn_prod_all = self.app._create_button(
            prod_btns, text=self._t("ft_select_all"),
            command=lambda: self._select_all(self.prod_tree),
            style_name="SecondaryTiny", padx=10, pady=2)
        self.btn_prod_all.pack(side="left")
        self.btn_prod_clear = self.app._create_button(
            prod_btns, text=self._t("ft_clear_all"),
            command=lambda: self.prod_tree.selection_remove(
                *self.prod_tree.get_children()),
            style_name="SecondaryTiny", padx=10, pady=2)
        self.btn_prod_clear.pack(side="left", padx=(6, 0))

        # Locales (Listbox)
        self.lbl_loc = ttk.Label(right, text=self._t("ft_locales"),
                                  style="CardBold.TLabel")
        self.lbl_loc.pack(anchor="w", pady=(0, 4))

        self.loc_list = tk.Listbox(
            right, selectmode="extended",
            exportselection=False,
            bg="#0a0a1a", fg="#e0e0e0",
            selectbackground="#e94560", selectforeground="#ffffff",
            highlightthickness=1, highlightbackground="#2a2a4a",
            relief="flat", bd=0)
        self.loc_list.pack(fill="both", expand=True)

        loc_btns = ttk.Frame(right, style="App.TFrame")
        loc_btns.pack(fill="x", pady=(4, 0))
        self.btn_loc_all = self.app._create_button(
            loc_btns, text=self._t("ft_select_all"),
            command=lambda: self.loc_list.select_set(0, tk.END),
            style_name="SecondaryTiny", padx=10, pady=2)
        self.btn_loc_all.pack(side="left")
        self.btn_loc_clear = self.app._create_button(
            loc_btns, text=self._t("ft_clear_all"),
            command=lambda: self.loc_list.select_clear(0, tk.END),
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
            self.lbl_loc.configure(text=self._t("ft_locales"))
            self.prod_tree.heading("product", text=self._t("ft_col_product"))
            self.prod_tree.heading("keys", text=self._t("ft_col_keys"))
            if self._inventory is None:
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

    # ---- actions ----------------------------------------------------
    def _select_all(self, tree: ttk.Treeview) -> None:
        items = tree.get_children()
        if items:
            tree.selection_set(items)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for w in (self.btn_refresh, self.btn_export_sel, self.btn_export_all):
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _on_refresh(self) -> None:
        if _exp is None or self._busy:
            return
        sources = []
        if self.var_src_legacy.get():
            sources.append("legacy")
        if self.var_src_mr.get():
            sources.append("mr")
        if not sources:
            sources = ["legacy", "mr"]
        self._set_busy(True)
        self.lbl_status.configure(
            text=self._t("ft_status_loading"), foreground="#888")
        t = threading.Thread(
            target=self._run_refresh, args=(sources,), daemon=True)
        t.start()

    def _run_refresh(self, sources) -> None:
        try:
            inv = _exp.collect_full_translations(
                sources=sources, progress_cb=self._log)
            self.parent.after(0, self._on_refresh_done, inv, None)
        except Exception as e:
            self.parent.after(0, self._on_refresh_done, None, str(e))

    def _on_refresh_done(self, inv, err) -> None:
        self._set_busy(False)
        if err:
            self.lbl_status.configure(
                text=f"❌ {err}", foreground="#e94560")
            return
        self._inventory = inv
        # Fill product tree
        self.prod_tree.delete(*self.prod_tree.get_children())
        for product, count in inv.products_sorted_by_key_count():
            self.prod_tree.insert(
                "", "end", iid=product, values=(product, f"{count:,}"))
        # Fill locale list
        self.loc_list.delete(0, tk.END)
        for loc in inv.all_locales():
            self.loc_list.insert(tk.END, loc)
        # Default: select all
        self._select_all(self.prod_tree)
        self.loc_list.select_set(0, tk.END)

        self.lbl_status.configure(
            text=self._t("ft_status_loaded").format(
                p=len(inv.data), l=len(inv.all_locales()),
                n=inv.total_entries()),
            foreground="#2ecc71")

    def _selected_products(self):
        return list(self.prod_tree.selection())

    def _selected_locales(self):
        idxs = self.loc_list.curselection()
        return [self.loc_list.get(i) for i in idxs]

    def _on_export_all(self) -> None:
        self._do_export(all_selection=True)

    def _on_export_selected(self) -> None:
        self._do_export(all_selection=False)

    def _do_export(self, *, all_selection: bool) -> None:
        if _exp is None or self._busy:
            return
        if self._inventory is None:
            messagebox.showwarning(
                "Full Translations", self._t("ft_err_no_inv"))
            return

        if all_selection:
            products = None
            locales = None
        else:
            products = self._selected_products() or None
            locales = self._selected_locales() or None
            if not products or not locales:
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
            text=self._t("ft_status_exporting"), foreground="#888")
        t = threading.Thread(
            target=self._run_export,
            args=(out_path, products, locales),
            daemon=True,
        )
        t.start()

    def _run_export(self, out_path, products, locales) -> None:
        try:
            summary = _exp.build_ap_zip(
                self._inventory,
                out_path=out_path,
                products=products,
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
