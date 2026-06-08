"""
GUI Tab: 🚦 翻译前预检 (Pre-Translation Check)

人工 File translation 启动前，导入 l10n portal 的 Purchase delta XLSX
(sheet 'Data'：Key=OPUS ID / Value=源文)，判断每条源串 Tranzor 是否已翻过：
  🟢 可跳过 / 🟡 需复核 / 🔴 待人工
核心判定逻辑在 ``pretranslation_check.py``；本 tab 只负责输入 / 展示 / 导出。

约定：``STRINGS`` 必须定义在 ``from export_gui import ...`` 之前 —— export_gui
反向 import 本模块来合并 STRINGS，放在 from-import 之后会被静默吞掉（按钮显示
原始 key）。覆盖判断走本地 opus_index（离线可用）；取分数 / 同步索引才需要平台
登录（透明 Bearer-JWT，见 tranzor_auth）。
"""
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ── i18n（务必在 from export_gui import 之前）──
STRINGS = {
    "en": {
        "tab_pretranslation_check": "🚦 Pre-Translation Check",
        "ptc_intro": "Import a Purchase delta XLSX (sheet 'Data': Key = OPUS ID, "
                     "Value = source). It tells you which strings Tranzor already "
                     "translated (🟢 skip), which to review (🟡), and which still "
                     "need manual translation (🔴).",
        "ptc_pick_file": "📄 Choose delta XLSX…",
        "ptc_no_file": "No file selected",
        "ptc_file_loaded": "✓ {n} strings loaded",
        "ptc_broaden": "Count File/Scan coverage as skippable too (default: MR only)",
        "ptc_sync": "🔄 Sync index",
        "ptc_run": "▶ Run Check",
        "ptc_index_status": "Local index: {n} rows",
        "ptc_login_hint": "🔑 Not signed in — scores need platform login (click 🔑 top-right)",
        "ptc_login_for_sync": "Syncing the index needs platform login — click the 🔑 "
                              "button at the top right first.",
        "ptc_syncing": "Syncing…",
        "ptc_synced": "✓ Index synced",
        "ptc_running": "Checking…",
        "ptc_stats": "Total {total}    🟢 Skip {green}    🟡 Review {amber}    🔴 Manual {red}",
        "ptc_filter_red": "Show 🔴 only",
        "ptc_export_manual": "📤 Export manual subset",
        "ptc_copy_red": "Copy 🔴 OPUS IDs",
        "ptc_exported": "✓ Exported {n} rows",
        "ptc_copied": "✓ Copied {n} OPUS IDs",
        "ptc_verdict_all_covered": "All strings are already covered by Tranzor — "
                                   "no manual translation needed.",
        "ptc_verdict_manual": "{manual} string(s) still need manual translation; "
                              "{green} can be skipped (already done by Tranzor).",
        "ptc_col_status": "", "ptc_col_opus": "OPUS ID", "ptc_col_source": "Source (en)",
        "ptc_col_needed": "Need", "ptc_col_covered": "Cov", "ptc_col_missing": "Missing langs",
        "ptc_col_mr": "MR", "ptc_col_merged": "Merged", "ptc_col_score": "Score", "ptc_col_kind": "Via",
        "ptc_reason_no_coverage": "No Tranzor coverage — needs manual translation.",
        "ptc_reason_non_mr_coverage": "Covered, but via File/Scan (not a merged MR) — review.",
        "ptc_reason_langs_missing": "Some needed languages not covered — review.",
        "ptc_reason_mr_not_merged": "Translated by an MR that isn't merged (or unconfirmable) — review.",
        "ptc_reason_score_below": "A score is below threshold — review.",
        "ptc_reason_score_unknown": "Score unconfirmed — review.",
        "ptc_reason_ok": "Covered by a merged MR, all languages, score OK — safe to skip.",
    },
    "zh": {
        "tab_pretranslation_check": "🚦 翻译前预检",
        "ptc_intro": "导入 l10n portal 的 Purchase delta XLSX（sheet 'Data'：Key=OPUS ID，"
                     "Value=源文），判断哪些串 Tranzor 已翻过（🟢 可跳过）、哪些需复核（🟡）、"
                     "哪些仍需人工翻译（🔴）。",
        "ptc_pick_file": "📄 选择 delta XLSX…",
        "ptc_no_file": "未选择文件",
        "ptc_file_loaded": "✓ 已载入 {n} 条",
        "ptc_broaden": "File/Scan 覆盖也算可跳过（默认仅 MR）",
        "ptc_sync": "🔄 同步索引",
        "ptc_run": "▶ 运行预检",
        "ptc_index_status": "本地索引：{n} 行",
        "ptc_login_hint": "🔑 未登录——取分数需登录平台（点右上 🔑）",
        "ptc_login_for_sync": "同步索引需登录平台——请先点右上角 🔑 按钮登录。",
        "ptc_syncing": "同步中…",
        "ptc_synced": "✓ 索引已同步",
        "ptc_running": "预检中…",
        "ptc_stats": "共 {total}    🟢 可跳过 {green}    🟡 复核 {amber}    🔴 待人工 {red}",
        "ptc_filter_red": "仅看 🔴",
        "ptc_export_manual": "📤 导出待人工子集",
        "ptc_copy_red": "复制 🔴 OPUS ID",
        "ptc_exported": "✓ 已导出 {n} 条",
        "ptc_copied": "✓ 已复制 {n} 个 OPUS ID",
        "ptc_verdict_all_covered": "全部已被 Tranzor 覆盖——无需启动人工翻译。",
        "ptc_verdict_manual": "{manual} 条仍需人工翻译；{green} 条可跳过（Tranzor 已翻）。",
        "ptc_col_status": "", "ptc_col_opus": "OPUS ID", "ptc_col_source": "源文(en)",
        "ptc_col_needed": "需", "ptc_col_covered": "覆", "ptc_col_missing": "缺语种",
        "ptc_col_mr": "MR", "ptc_col_merged": "合入", "ptc_col_score": "分", "ptc_col_kind": "来源",
        "ptc_reason_no_coverage": "Tranzor 无覆盖——需人工翻译。",
        "ptc_reason_non_mr_coverage": "已覆盖，但来自 File/Scan（非已合入 MR）——复核。",
        "ptc_reason_langs_missing": "部分所需语种未覆盖——复核。",
        "ptc_reason_mr_not_merged": "翻译它的 MR 未合入（或无法确认）——复核。",
        "ptc_reason_score_below": "有分数低于阈值——复核。",
        "ptc_reason_score_unknown": "分数未确认——复核。",
        "ptc_reason_ok": "已由合入的 MR 覆盖、语种齐全、分数达标——可安全跳过。",
    },
}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pretranslation_check as ptc
import opus_id_monitor as om
import tranzor_auth
from export_gui import FONT_FAMILY, IS_MAC, reveal_in_folder

_EMOJI = {ptc.GREEN: "🟢", ptc.AMBER: "🟡", ptc.RED: "🔴"}
_TAG = {ptc.GREEN: "v_green", ptc.AMBER: "v_amber", ptc.RED: "v_red"}


class PreTranslationCheckTab:
    _COLS = ("status", "opus", "source", "needed", "covered", "missing",
             "mr", "merged", "score", "kind")

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self._delta_path = None
        self._delta_rows = None
        self._result = None
        self._running = False
        self._first_shown = False
        self._build(parent)

    def _t(self, key):
        return self.app._t(key)

    # ── lifecycle ──
    def on_first_show(self):
        if self._first_shown:
            return
        self._first_shown = True
        self._refresh_index_status()
        self._refresh_login_hint()

    def _build(self, parent):
        content = ttk.Frame(parent, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=16, pady=8)

        # input card
        top = ttk.Frame(content, style="Card.TFrame")
        top.pack(fill="x", pady=(0, 8))
        top.configure(borderwidth=1, relief="solid")
        ti = ttk.Frame(top, style="Card.TFrame")
        ti.pack(fill="x", padx=12, pady=10)

        self.lbl_intro = ttk.Label(ti, text="", style="Card.TLabel",
                                    wraplength=980, justify="left")
        self.lbl_intro.pack(anchor="w", pady=(0, 8))

        r1 = ttk.Frame(ti, style="Card.TFrame")
        r1.pack(fill="x", pady=(0, 6))
        self.btn_pick = self.app._create_button(
            r1, text="", command=self._on_pick_file, style_name="SecondarySmall",
            font=(FONT_FAMILY, 10, "bold"), bg="#0f3460", fg="#fff", padx=14, pady=4)
        self.btn_pick.pack(side="left")
        self.lbl_file = ttk.Label(r1, text="", style="Card.TLabel")
        self.lbl_file.pack(side="left", padx=(10, 0))
        self.broaden_var = tk.BooleanVar(value=False)
        self.chk_broaden = ttk.Checkbutton(
            r1, text="", variable=self.broaden_var, style="Card.TCheckbutton")
        self.chk_broaden.pack(side="right")

        r2 = ttk.Frame(ti, style="Card.TFrame")
        r2.pack(fill="x")
        self.btn_sync = self.app._create_button(
            r2, text="", command=self._on_sync, style_name="SecondarySmall",
            font=(FONT_FAMILY, 10), bg="#0f3460", fg="#ccc", padx=12, pady=3)
        self.btn_sync.pack(side="left")
        self.lbl_index = ttk.Label(r2, text="", style="Status.TLabel")
        self.lbl_index.pack(side="left", padx=(10, 0))
        self.lbl_login = ttk.Label(r2, text="", style="Hint.TLabel")
        self.lbl_login.pack(side="left", padx=(10, 0))
        self.btn_run = self.app._create_button(
            r2, text="", command=self._on_run, style_name="AccentSmall",
            font=(FONT_FAMILY, 10, "bold"), bg="#e94560", fg="#fff",
            padx=16, pady=4, state="disabled")
        self.btn_run.pack(side="right")

        # stat / action bar
        bar = ttk.Frame(content, style="App.TFrame")
        bar.pack(fill="x", pady=(0, 6))
        self.lbl_stats = ttk.Label(bar, text="", style="CardBold.TLabel")
        self.lbl_stats.pack(side="left")
        self.filter_red_var = tk.BooleanVar(value=False)
        self.chk_filter_red = ttk.Checkbutton(
            bar, text="", variable=self.filter_red_var,
            style="Card.TCheckbutton", command=self._render_rows)
        self.chk_filter_red.pack(side="left", padx=(16, 0))
        self.btn_export = self.app._create_button(
            bar, text="", command=self._on_export, style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"), bg="#2ecc71", fg="#fff",
            padx=12, pady=3, state="disabled")
        self.btn_export.pack(side="right")
        self.btn_copy = self.app._create_button(
            bar, text="", command=self._on_copy_red, style_name="SecondarySmall",
            font=(FONT_FAMILY, 10), bg="#0f3460", fg="#ccc",
            padx=12, pady=3, state="disabled")
        self.btn_copy.pack(side="right", padx=(0, 8))
        self.lbl_status = ttk.Label(bar, text="", style="Status.TLabel")
        self.lbl_status.pack(side="right", padx=(0, 12))

        # results table
        tf = ttk.Frame(content, style="App.TFrame")
        tf.pack(fill="both", expand=True, pady=(0, 6))
        self.tree = ttk.Treeview(tf, columns=self._COLS, show="headings",
                                 style="Summary.Treeview", height=16, selectmode="browse")
        widths = {"status": 50, "opus": 260, "source": 250, "needed": 50, "covered": 50,
                  "missing": 120, "mr": 80, "merged": 78, "score": 55, "kind": 80}
        for c in self._COLS:
            anchor = "w" if c in ("opus", "source", "missing") else "center"
            self.tree.column(c, width=widths.get(c, 80), anchor=anchor)
        self.tree.tag_configure("v_green", background="#16361f", foreground="#86efac")
        self.tree.tag_configure("v_amber", background="#3a2e1f", foreground="#fde68a")
        self.tree.tag_configure("v_red", background="#3a1f24", foreground="#fca5a5")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_row_dbl)

        # verdict
        self.lbl_verdict = ttk.Label(content, text="", style="CardBold.TLabel",
                                     wraplength=1100, justify="left")
        self.lbl_verdict.pack(anchor="w", pady=(2, 0))

    def refresh_text(self):
        t = self._t
        self.lbl_intro.configure(text=t("ptc_intro"))
        self.btn_pick.configure(text=t("ptc_pick_file"))
        self.chk_broaden.configure(text=t("ptc_broaden"))
        self.btn_sync.configure(text=t("ptc_sync"))
        self.btn_run.configure(text=t("ptc_run"))
        self.chk_filter_red.configure(text=t("ptc_filter_red"))
        self.btn_export.configure(text=t("ptc_export_manual"))
        self.btn_copy.configure(text=t("ptc_copy_red"))
        for c in self._COLS:
            self.tree.heading(c, text=t("ptc_col_" + c))
        if self._delta_rows is not None:
            self.lbl_file.configure(text=t("ptc_file_loaded").format(n=len(self._delta_rows)))
        else:
            self.lbl_file.configure(text=t("ptc_no_file"))
        self._refresh_index_status()
        self._refresh_login_hint()
        if self._result:
            self._render_rows()
            self._render_summary()

    # ── helpers ──
    def _set_enabled(self, btn, on):
        if IS_MAC:
            btn.state(["!disabled"] if on else ["disabled"])
        else:
            btn.configure(state="normal" if on else "disabled")

    def _refresh_login_hint(self):
        try:
            ok = tranzor_auth.has_valid_token()
        except Exception:
            ok = False
        self.lbl_login.configure(text="" if ok else self._t("ptc_login_hint"))

    def _refresh_index_status(self):
        def _work():
            try:
                s = om.get_summary()
                n = s.get("total_opus_ids", s.get("total"))
            except Exception:
                n = None
            txt = self._t("ptc_index_status").format(
                n=(f"{n:,}" if isinstance(n, int) else "?"))
            self.parent.after(0, lambda: self.lbl_index.configure(text=txt))
        threading.Thread(target=_work, daemon=True).start()

    # ── pick file ──
    def _on_pick_file(self):
        path = filedialog.askopenfilename(
            title=self._t("ptc_pick_file"),
            filetypes=[("Excel", "*.xlsx"), ("All files", "*.*")])
        if not path:
            return

        def _work():
            try:
                rows = ptc.parse_delta_xlsx(path)
                self.parent.after(0, lambda: self._on_file_loaded(path, rows))
            except Exception as e:
                self.parent.after(0, lambda msg=str(e): messagebox.showerror(
                    "Pre-Translation Check", msg))
        threading.Thread(target=_work, daemon=True).start()

    def _on_file_loaded(self, path, rows):
        self._delta_path = path
        self._delta_rows = rows
        self._result = None
        self.lbl_file.configure(text=self._t("ptc_file_loaded").format(n=len(rows)))
        self._set_enabled(self.btn_run, bool(rows))
        self._set_enabled(self.btn_export, False)
        self._set_enabled(self.btn_copy, False)
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.lbl_stats.configure(text="")
        self.lbl_verdict.configure(text="")

    # ── sync index ──
    def _on_sync(self):
        try:
            ok = tranzor_auth.has_valid_token()
        except Exception:
            ok = False
        if not ok:
            messagebox.showinfo("Pre-Translation Check", self._t("ptc_login_for_sync"))
            return
        if self._running:
            return
        self._running = True
        self._set_enabled(self.btn_sync, False)
        self.lbl_status.configure(text=self._t("ptc_syncing"))

        def _prog(*a, **k):
            self.parent.after(0, lambda: self.lbl_status.configure(
                text=self._t("ptc_syncing")))

        def _work():
            err = None
            try:
                try:
                    om.sync_incremental(progress_callback=_prog)
                except TypeError:
                    om.sync_incremental()
            except Exception as e:
                err = str(e)

            def _done():
                self._running = False
                self._set_enabled(self.btn_sync, True)
                self.lbl_status.configure(
                    text=(f"⚠ {err[:50]}" if err else self._t("ptc_synced")))
                self._refresh_index_status()
                self._refresh_login_hint()
            self.parent.after(0, _done)
        threading.Thread(target=_work, daemon=True).start()

    # ── run check ──
    def _on_run(self):
        if self._running or not self._delta_rows:
            return
        self._refresh_login_hint()
        self._running = True
        self._set_enabled(self.btn_run, False)
        self.lbl_status.configure(text=self._t("ptc_running"))
        accept = ("mr", "file", "scan") if self.broaden_var.get() else ("mr",)
        rows = self._delta_rows

        def _work():
            try:
                res = ptc.check_delta(rows, accept_kinds=accept)
                self.parent.after(0, lambda: self._on_result(res))
            except Exception as e:
                self.parent.after(0, lambda msg=str(e): self._on_run_error(msg))
        threading.Thread(target=_work, daemon=True).start()

    def _on_run_error(self, err):
        self._running = False
        self._set_enabled(self.btn_run, True)
        self.lbl_status.configure(text=f"⚠ {err[:60]}")

    def _on_result(self, res):
        self._running = False
        self._set_enabled(self.btn_run, True)
        self._result = res
        self.lbl_status.configure(text="")
        self._render_rows()
        self._render_summary()
        self._set_enabled(self.btn_export, res["summary"]["total"] > 0)
        self._set_enabled(self.btn_copy, res["summary"]["red"] > 0)

    def _render_rows(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        if not self._result:
            return
        only_red = self.filter_red_var.get()
        for row in self._result["rows"]:
            if only_red and row["verdict"] != ptc.RED:
                continue
            src = (row.get("source_text") or "")[:60]
            score = row.get("min_score")
            self.tree.insert(
                "", "end",
                values=(_EMOJI.get(row["verdict"], ""), row["opus_id"], src,
                        len(row["needed_langs"]), len(row["covered_langs"]),
                        ", ".join(row["missing_langs"])[:40],
                        row.get("mr_iid") or "—", row.get("mr_state") or "—",
                        score if score is not None else "—",
                        row.get("source_kind") or "—"),
                tags=(_TAG.get(row["verdict"], ""),))

    def _render_summary(self):
        s = self._result["summary"]
        t = self._t
        self.lbl_stats.configure(text=t("ptc_stats").format(
            total=s["total"], green=s["green"], amber=s["amber"], red=s["red"]))
        if s["manual_needed"] == 0:
            self.lbl_verdict.configure(
                text="✅ " + t("ptc_verdict_all_covered"), foreground="#86efac")
        else:
            self.lbl_verdict.configure(
                text="⚠ " + t("ptc_verdict_manual").format(
                    manual=s["manual_needed"], green=s["green"]),
                foreground="#fde68a")

    # ── export / copy / detail ──
    def _on_export(self):
        if not self._result:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            initialfile="pretranslation_manual_subset.xlsx",
            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            n = ptc.export_manual_subset(self._result, path, include_amber=True)
            self.lbl_status.configure(text=self._t("ptc_exported").format(n=n))
            reveal_in_folder(path)
        except Exception as e:
            messagebox.showerror("Pre-Translation Check", str(e))

    def _on_copy_red(self):
        if not self._result:
            return
        reds = [r["opus_id"] for r in self._result["rows"] if r["verdict"] == ptc.RED]
        if not reds:
            return
        try:
            self.parent.clipboard_clear()
            self.parent.clipboard_append("\n".join(reds))
            self.lbl_status.configure(text=self._t("ptc_copied").format(n=len(reds)))
        except Exception:
            pass

    def _on_row_dbl(self, _event):
        sel = self.tree.selection()
        if not sel or not self._result:
            return
        vals = self.tree.item(sel[0], "values")
        if not vals:
            return
        oid = vals[1]
        row = next((r for r in self._result["rows"] if r["opus_id"] == oid), None)
        if not row:
            return
        t = self._t
        reason = t("ptc_reason_" + row.get("reason", "")) if row.get("reason") else ""
        score = row.get("min_score")
        msg = (
            f"{_EMOJI.get(row['verdict'], '')}  {oid}\n\n"
            f"{row.get('source_text', '')}\n\n"
            f"{t('ptc_col_kind')}: {row.get('source_kind') or '—'}\n"
            f"MR: {row.get('mr_iid') or '—'}  ({row.get('mr_state') or '—'})\n"
            f"{t('ptc_col_score')}: {score if score is not None else '—'}\n"
            f"{t('ptc_col_covered')}: {', '.join(row.get('covered_langs', [])) or '—'}\n"
            f"{t('ptc_col_missing')}: {', '.join(row.get('missing_langs', [])) or '—'}\n\n"
            f"→ {reason}")
        messagebox.showinfo("Pre-Translation Check", msg)
