"""
GUI Tab -- Human Revisions At A Glance
=======================================
Displays human edit records from BOTH MR Pipeline and File Translation
channels, aggregated client-side using existing deployed APIs.

Detection uses the same ``_has_human_touch`` logic as quality_overview.py:
  - fixed_by_lead is set
  - reviewer_comment / reviewer_notes is non-empty
  - edit_log_count > 0

Default view: last 30 calendar days, auto-loaded on first show.
"""
import os
import platform
import threading
import tkinter as tk
from tkinter import ttk, filedialog
from datetime import date, timedelta

import export_mr_pipeline as mr_api

# ---------------------------------------------------------------------------
# Cross-platform font
# ---------------------------------------------------------------------------
if platform.system() == "Darwin":
    FONT_FAMILY = "Helvetica Neue"
else:
    FONT_FAMILY = "Segoe UI"

# ---------------------------------------------------------------------------
# i18n strings (merged into export_gui.STRINGS at import time)
# ---------------------------------------------------------------------------
STRINGS = {
    "en": {
        "tab_human_revisions":  "Human Revisions",
        "hr_date_range":        "Date",
        "hr_search":            "Refresh",
        "hr_reset":             "Reset",
        "hr_mr_title":          "MR Pipeline Human Edits",
        "hr_file_title":        "File Translation Human Edits",
        "hr_col_language":      "Language",
        "hr_col_key":           "String Key",
        "hr_col_score":         "Score",
        "hr_col_editor":        "Editor",
        "hr_col_date":          "Revised At",
        "hr_export":            "Export Human Revisions",
        "hr_fmt_html":          "HTML",
        "hr_fmt_md":            "Markdown",
        "hr_fmt_both":          "Both (ZIP)",
        "hr_exporting":         "Exporting...",
        "hr_export_done":       "Export complete",
        "hr_export_fail":       "Export failed",
        "hr_loading":           "Loading human edits (MR Pipeline + File Translation)...",
        "hr_no_data":           "No human edits found in the selected period.",
        "hr_detail_source":     "Source (en-US)",
        "hr_detail_mt":         "Machine Translation",
        "hr_detail_human":      "Human Revision",
    },
    "zh": {
        "tab_human_revisions":  "\u4eba\u5de5\u4fee\u8ba2",
        "hr_date_range":        "\u65e5\u671f",
        "hr_search":            "\u5237\u65b0",
        "hr_reset":             "\u91cd\u7f6e",
        "hr_mr_title":          "MR Pipeline \u4eba\u5de5\u4fee\u8ba2",
        "hr_file_title":        "\u6587\u4ef6\u7ffb\u8bd1 \u4eba\u5de5\u4fee\u8ba2",
        "hr_col_language":      "\u8bed\u8a00",
        "hr_col_key":           "\u5b57\u7b26\u4e32\u952e",
        "hr_col_score":         "\u5206\u6570",
        "hr_col_editor":        "\u7f16\u8f91\u8005",
        "hr_col_date":          "\u4fee\u8ba2\u65f6\u95f4",
        "hr_export":            "\u5bfc\u51fa\u4eba\u5de5\u4fee\u8ba2",
        "hr_fmt_html":          "HTML",
        "hr_fmt_md":            "Markdown",
        "hr_fmt_both":          "\u4e24\u8005 (ZIP)",
        "hr_exporting":         "\u5bfc\u51fa\u4e2d...",
        "hr_export_done":       "\u5bfc\u51fa\u5b8c\u6210",
        "hr_export_fail":       "\u5bfc\u51fa\u5931\u8d25",
        "hr_loading":           "\u52a0\u8f7d\u4e2d (MR Pipeline + \u6587\u4ef6\u7ffb\u8bd1)...",
        "hr_no_data":           "\u6240\u9009\u65f6\u95f4\u6bb5\u5185\u672a\u627e\u5230\u4eba\u5de5\u4fee\u8ba2\u8bb0\u5f55\u3002",
        "hr_detail_source":     "\u6e90\u6587\u672c (en-US)",
        "hr_detail_mt":         "\u673a\u5668\u7ffb\u8bd1",
        "hr_detail_human":      "\u4eba\u5de5\u4fee\u8ba2",
    },
}


def _default_date_range():
    """Return (start_str, end_str) for the last 30 calendar days."""
    today = date.today()
    start = today - timedelta(days=30)
    return start.isoformat(), today.isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# Tab class
# ═══════════════════════════════════════════════════════════════════════════
class HumanRevisionsTab:
    """Builds and manages the Human Revisions tab."""

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self._loading = False
        self._data = None  # result from collect_human_revisions
        self._build(parent)

    def _t(self, key):
        return self.app._t(key)

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------
    def _build(self, parent):
        outer = ttk.Frame(parent, style="App.TFrame")
        outer.pack(fill="both", expand=True)

        # Scrollable canvas (same pattern as Quality Overview tab)
        self._canvas = tk.Canvas(outer, bg="#1a1a2e", highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical",
                                  command=self._canvas.yview)
        self.scroll_frame = ttk.Frame(self._canvas, style="App.TFrame")
        self.scroll_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(
                scrollregion=self._canvas.bbox("all")),
        )
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self.scroll_frame, anchor="nw",
        )
        self._canvas.configure(yscrollcommand=scrollbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_canvas_resize(e):
            self._canvas.itemconfig(self._canvas_win, width=e.width)
        self._canvas.bind("<Configure>", _on_canvas_resize)
        self._canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._canvas.yview_scroll(
                int(-1 * (e.delta / 120)), "units"),
        )

        content = self.scroll_frame

        # ── Filter bar: date range + Refresh / Reset ──
        filt = ttk.Frame(content, style="Card.TFrame")
        filt.pack(fill="x", padx=16, pady=(8, 8))
        filt.configure(borderwidth=1, relief="solid")
        fi = ttk.Frame(filt, style="Card.TFrame")
        fi.pack(fill="x", padx=12, pady=10)

        r1 = ttk.Frame(fi, style="Card.TFrame")
        r1.pack(fill="x")

        self.lbl_date = ttk.Label(r1, text="Date", style="Card.TLabel",
                                  width=5)
        self.lbl_date.pack(side="left")

        default_start, default_end = _default_date_range()

        self.date_from = tk.Entry(
            r1, width=12, font=(FONT_FAMILY, 10),
            bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat")
        self.date_from.insert(0, default_start)
        self.date_from.pack(side="left", padx=(4, 4), ipady=3)
        ttk.Label(r1, text="\u2014", style="Card.TLabel").pack(side="left")
        self.date_to = tk.Entry(
            r1, width=12, font=(FONT_FAMILY, 10),
            bg="#0a0a1a", fg="#fff", insertbackground="#fff", relief="flat")
        self.date_to.insert(0, default_end)
        self.date_to.pack(side="left", padx=(4, 12), ipady=3)

        self.btn_search = self.app._create_button(
            r1, text="Refresh", command=self._on_refresh,
            style_name="AccentSmall", font=(FONT_FAMILY, 10, "bold"),
            bg="#e94560", fg="#fff", padx=14, pady=3)
        self.btn_search.pack(side="left", padx=(0, 6))

        self.btn_reset = self.app._create_button(
            r1, text="Reset", command=self._on_reset,
            style_name="SecondarySmall", font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=3)
        self.btn_reset.pack(side="left")

        # ── Status line ──
        self.lbl_status = ttk.Label(content, text="", style="Status.TLabel")
        self.lbl_status.pack(anchor="w", padx=16, pady=(0, 4))

        # ── MR Pipeline section ──
        self.lbl_mr_title = ttk.Label(
            content, text="MR Pipeline Human Edits (0)",
            style="SummaryTitle.TLabel")
        self.lbl_mr_title.pack(anchor="w", padx=16, pady=(8, 4))

        mr_frame = ttk.Frame(content, style="Card.TFrame")
        mr_frame.pack(fill="both", padx=16, pady=(0, 8))
        mr_frame.configure(borderwidth=1, relief="solid")

        tree_cols = ("language", "key", "score", "editor", "date")
        self.mr_tree = ttk.Treeview(
            mr_frame, columns=tree_cols, show="headings",
            style="Summary.Treeview", height=10)
        mr_vsb = ttk.Scrollbar(mr_frame, orient="vertical",
                               command=self.mr_tree.yview)
        self.mr_tree.configure(yscrollcommand=mr_vsb.set)
        self.mr_tree.pack(side="left", fill="both", expand=True)
        mr_vsb.pack(side="right", fill="y")
        self.mr_tree.bind("<Double-1>",
                          lambda e: self._on_tree_dblclick(e, "mr"))

        # ── File Translation section ──
        self.lbl_file_title = ttk.Label(
            content, text="File Translation Human Edits (0)",
            style="SummaryTitle.TLabel")
        self.lbl_file_title.pack(anchor="w", padx=16, pady=(8, 4))

        file_frame = ttk.Frame(content, style="Card.TFrame")
        file_frame.pack(fill="both", padx=16, pady=(0, 8))
        file_frame.configure(borderwidth=1, relief="solid")

        self.file_tree = ttk.Treeview(
            file_frame, columns=tree_cols, show="headings",
            style="Summary.Treeview", height=10)
        file_vsb = ttk.Scrollbar(file_frame, orient="vertical",
                                 command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=file_vsb.set)
        self.file_tree.pack(side="left", fill="both", expand=True)
        file_vsb.pack(side="right", fill="y")
        self.file_tree.bind("<Double-1>",
                            lambda e: self._on_tree_dblclick(e, "file"))

        # Configure column widths for both trees
        col_widths = {
            "language": 100, "key": 220, "score": 60,
            "editor": 130, "date": 140,
        }
        for tree in (self.mr_tree, self.file_tree):
            for c in tree_cols:
                tree.column(
                    c, width=col_widths.get(c, 100),
                    anchor="center" if c in ("score", "date") else "w")

        # ── Export bar ──
        ebar = ttk.Frame(content, style="App.TFrame")
        ebar.pack(fill="x", padx=16, pady=(4, 24))

        self.lbl_fmt = ttk.Label(ebar, text="Format:", style="Card.TLabel")
        self.lbl_fmt.pack(side="left")
        self.fmt_var = tk.StringVar(value="html")
        self.rb_html = ttk.Radiobutton(
            ebar, text="HTML", variable=self.fmt_var, value="html",
            style="Card.TRadiobutton")
        self.rb_html.pack(side="left", padx=(4, 6))
        self.rb_md = ttk.Radiobutton(
            ebar, text="Markdown", variable=self.fmt_var, value="markdown",
            style="Card.TRadiobutton")
        self.rb_md.pack(side="left", padx=(0, 6))
        self.rb_both = ttk.Radiobutton(
            ebar, text="Both (ZIP)", variable=self.fmt_var, value="both",
            style="Card.TRadiobutton")
        self.rb_both.pack(side="left")

        self.btn_export = self.app._create_button(
            ebar, text="Export Human Revisions",
            command=self._on_export,
            style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"),
            bg="#2ecc71", fg="#fff", padx=14, pady=4,
            state="disabled")
        self.btn_export.pack(side="right")

        self.lbl_export_status = ttk.Label(ebar, text="",
                                           style="Status.TLabel")
        self.lbl_export_status.pack(side="right", padx=(0, 12))

    # ------------------------------------------------------------------
    # i18n text refresh
    # ------------------------------------------------------------------
    def refresh_text(self):
        self.lbl_date.configure(text=self._t("hr_date_range"))
        self.btn_search.configure(text=self._t("hr_search"))
        self.btn_reset.configure(text=self._t("hr_reset"))

        mr_count = 0
        file_count = 0
        if self._data:
            mr_count = self._data.get("mr_pipeline", {}).get("count", 0)
            file_count = self._data.get("file_translation", {}).get("count", 0)
        self.lbl_mr_title.configure(
            text=f'{self._t("hr_mr_title")} ({mr_count})')
        self.lbl_file_title.configure(
            text=f'{self._t("hr_file_title")} ({file_count})')

        for tree in (self.mr_tree, self.file_tree):
            tree.heading("language", text=self._t("hr_col_language"))
            tree.heading("key", text=self._t("hr_col_key"))
            tree.heading("score", text=self._t("hr_col_score"))
            tree.heading("editor", text=self._t("hr_col_editor"))
            tree.heading("date", text=self._t("hr_col_date"))

        self.rb_html.configure(text=self._t("hr_fmt_html"))
        self.rb_md.configure(text=self._t("hr_fmt_md"))
        self.rb_both.configure(text=self._t("hr_fmt_both"))
        self.btn_export.configure(text=self._t("hr_export"))

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def on_first_show(self):
        """Called once when the tab is first selected."""
        self._load_data()

    def _get_date_params(self):
        params = {}
        d_from = self.date_from.get().strip()
        d_to = self.date_to.get().strip()
        if d_from:
            params["start_time"] = f"{d_from}T00:00:00"
        if d_to:
            params["end_time"] = f"{d_to}T23:59:59"
        return params

    def _update_status(self, text, color="#e94560"):
        self.lbl_status.configure(text=text, foreground=color)

    def _load_data(self):
        if self._loading:
            return
        self._loading = True
        self._update_status(self._t("hr_loading"))
        self.btn_export.configure(state="disabled")

        params = self._get_date_params()

        def _progress(msg):
            self.parent.after(0, self._update_status, msg, "#e94560")

        def _fetch():
            try:
                data = mr_api.collect_human_revisions(
                    start_time=params.get("start_time"),
                    end_time=params.get("end_time"),
                    progress_callback=_progress,
                )
                self.parent.after(0, self._render_data, data)
            except Exception as e:
                self.parent.after(
                    0, self._update_status, f"Error: {e}", "#e94560")
            finally:
                self._loading = False

        threading.Thread(target=_fetch, daemon=True).start()

    def _render_data(self, data):
        self._data = data
        total = data.get("total", 0)
        mr_info = data.get("mr_pipeline", {})
        file_info = data.get("file_translation", {})
        mr_count = mr_info.get("count", 0)
        file_count = file_info.get("count", 0)

        if total == 0:
            self._update_status(self._t("hr_no_data"), "#888")
            self._clear_trees()
            self.lbl_mr_title.configure(
                text=f'{self._t("hr_mr_title")} (0)')
            self.lbl_file_title.configure(
                text=f'{self._t("hr_file_title")} (0)')
            self.btn_export.configure(state="disabled")
            return

        self._update_status(
            f"MR Pipeline: {mr_count} | "
            f"File Translation: {file_count} | Total: {total}",
            "#2ecc71",
        )
        self.btn_export.configure(state="normal")

        self.lbl_mr_title.configure(
            text=f'{self._t("hr_mr_title")} ({mr_count})')
        self.lbl_file_title.configure(
            text=f'{self._t("hr_file_title")} ({file_count})')

        self._populate_tree(self.mr_tree, mr_info.get("items", []))
        self._populate_tree(self.file_tree, file_info.get("items", []))

    def _clear_trees(self):
        for tree in (self.mr_tree, self.file_tree):
            for item in tree.get_children():
                tree.delete(item)

    @staticmethod
    def _populate_tree(tree, items):
        for child in tree.get_children():
            tree.delete(child)
        for it in items:
            key = it.get("opus_id", "")
            if len(key) > 32:
                key = key[:30] + "\u2026"
            ts = str(it.get("revised_at") or "")[:16].replace("T", " ")
            score = it.get("machine_score")
            score_str = str(score) if score is not None else "-"
            tree.insert("", "end", values=(
                it.get("target_language", ""),
                key,
                score_str,
                it.get("editor", ""),
                ts,
            ))

    # ------------------------------------------------------------------
    # Detail popup on double-click
    # ------------------------------------------------------------------
    def _on_tree_dblclick(self, event, channel):
        tree = self.mr_tree if channel == "mr" else self.file_tree
        sel = tree.selection()
        if not sel:
            return
        idx = tree.index(sel[0])
        if not self._data:
            return
        key = "mr_pipeline" if channel == "mr" else "file_translation"
        items = self._data.get(key, {}).get("items", [])
        if idx < 0 or idx >= len(items):
            return
        self._show_detail(items[idx])

    def _show_detail(self, item):
        win = tk.Toplevel(self.parent)
        win.title(item.get("channel", "Detail"))
        win.geometry("750x520")
        win.configure(bg="#1a1a2e")

        pad = {"padx": 16, "pady": 4}

        def _add_row(parent_w, label, value, height=1):
            frame = ttk.Frame(parent_w, style="App.TFrame")
            frame.pack(fill="x", **pad)
            ttk.Label(frame, text=label, style="Card.TLabel", width=18,
                      anchor="e").pack(side="left")
            widget = tk.Text(
                frame, height=height, width=60,
                bg="#16213e", fg="#ccc", font=(FONT_FAMILY, 10),
                wrap="word", relief="flat", borderwidth=0)
            widget.insert("1.0", str(value or "-"))
            widget.configure(state="disabled")
            widget.pack(side="left", padx=(8, 0), fill="x", expand=True)

        _add_row(win, "Channel:", item.get("channel", ""))
        _add_row(win, "String Key:", item.get("opus_id", ""))
        _add_row(win, "Language:", item.get("target_language", ""))
        _add_row(win, self._t("hr_detail_source"),
                 item.get("source_text", ""), height=3)
        _add_row(win, self._t("hr_detail_mt"),
                 item.get("machine_translation", ""), height=3)
        _add_row(win, self._t("hr_detail_human"),
                 item.get("human_revision", ""), height=3)
        _add_row(win, "Score:", item.get("machine_score", "-"))
        _add_row(win, "Error Category:", item.get("error_category", "-"))
        _add_row(win, "Editor:", item.get("editor", ""))
        _add_row(win, "Revised At:",
                 str(item.get("revised_at", ""))[:16])

        btn_close = self.app._create_button(
            win, text="Close", command=win.destroy,
            style_name="SecondarySmall", font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=20, pady=4)
        btn_close.pack(pady=12)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _on_refresh(self):
        self._load_data()

    def _on_reset(self):
        self.date_from.delete(0, "end")
        self.date_to.delete(0, "end")
        default_start, default_end = _default_date_range()
        self.date_from.insert(0, default_start)
        self.date_to.insert(0, default_end)
        self._load_data()

    def _on_export(self):
        if self._loading or not self._data:
            return

        fmt = self.fmt_var.get()
        ext_map = {"html": ".html", "markdown": ".md", "both": ".zip"}
        ext = ext_map.get(fmt, ".zip")

        save_path = filedialog.asksaveasfilename(
            defaultextension=ext,
            filetypes=[
                ("HTML files", "*.html"),
                ("Markdown files", "*.md"),
                ("ZIP files", "*.zip"),
                ("All files", "*.*"),
            ],
            initialfile=f"human_revisions{ext}",
        )
        if not save_path:
            return

        self._loading = True
        self.lbl_export_status.configure(
            text=self._t("hr_exporting"), foreground="#e94560")
        self.btn_export.configure(state="disabled")

        def _do_export():
            try:
                params = self._get_date_params()
                all_items = (
                    self._data.get("mr_pipeline", {}).get("items", [])
                    + self._data.get("file_translation", {}).get("items", [])
                )
                content = _render_local_report(all_items, params, fmt)
                with open(save_path, "wb") as f:
                    f.write(content)
                self.parent.after(0, self._export_done, save_path, fmt)
            except Exception as e:
                self.parent.after(
                    0,
                    lambda: self.lbl_export_status.configure(
                        text=f"{self._t('hr_export_fail')}: {e}",
                        foreground="#e94560"),
                )
            finally:
                self._loading = False
                self.parent.after(
                    0, lambda: self.btn_export.configure(state="normal"))

        threading.Thread(target=_do_export, daemon=True).start()

    def _export_done(self, path, fmt):
        self.lbl_export_status.configure(
            text=self._t("hr_export_done"), foreground="#2ecc71")
        if fmt == "html" and os.path.exists(path):
            try:
                from export_gui import open_in_browser
                open_in_browser(path)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════
# Local report rendering (no server-side dependency)
# ═══════════════════════════════════════════════════════════════════════════

def _esc(text):
    import html
    return html.escape(str(text), quote=True)


def _render_html_report(items, params):
    from collections import defaultdict
    from datetime import datetime

    # Group items by channel, then by language
    by_channel = defaultdict(lambda: defaultdict(list))
    for item in items:
        by_channel[item.get("channel", "?")][
            item.get("target_language", "?")].append(item)

    sections = []
    for channel in ("MR Pipeline", "File Translation"):
        lang_map = by_channel.get(channel, {})
        if not lang_map:
            continue
        total_in_channel = sum(len(v) for v in lang_map.values())
        cards_html = []
        global_idx = 0
        for lang in sorted(lang_map.keys()):
            lang_items = lang_map[lang]
            cards_html.append(
                f'<h3>{_esc(lang)} ({len(lang_items)})</h3>')
            for it in lang_items:
                global_idx += 1
                badge = (
                    '<span style="background:#3498db;color:#fff;'
                    'padding:1px 6px;border-radius:4px;font-size:0.7rem;">'
                    f'{_esc(it.get("editor",""))}</span>'
                )
                cards_html.append(f"""
    <article class="revision-card">
      <div class="card-index">#{global_idx}</div>
      <table class="compare-table"><thead><tr>
        <th width="33%">Source (en-US)</th>
        <th width="33%">Machine Translation</th>
        <th width="33%">Human Revision</th>
      </tr></thead><tbody><tr>
        <td class="source">{_esc(it.get('source_text',''))}</td>
        <td class="machine">{_esc(it.get('machine_translation',''))}</td>
        <td class="human">{_esc(it.get('human_revision',''))}</td>
      </tr></tbody></table>
      <footer class="card-meta">
        {badge}
        Opus ID: <code>{_esc(it.get('opus_id',''))}</code> |
        Score: <strong>{it.get('machine_score','-')}</strong> |
        Error: <em>{_esc(it.get('error_category','-'))}</em> |
        Revised: {str(it.get('revised_at',''))[:16]}
      </footer>
    </article>""")

        sections.append(
            f'<section><h2>{_esc(channel)} ({total_in_channel})</h2>'
            f'{"".join(cards_html)}</section>')

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    time_range = (f'{params.get("start_time","...")[:10]} \u2014 '
                  f'{params.get("end_time","...")[:10]}')

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Human Revisions Report</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,
sans-serif;max-width:1200px;margin:0 auto;padding:24px;color:#1e293b;
background:#f8fafc}}
h1{{font-size:1.75rem;margin-bottom:16px;color:#0f172a}}
h2{{font-size:1.35rem;margin:32px 0 16px;padding-bottom:8px;
border-bottom:2px solid #e2e8f0;color:#1e3a5f}}
h3{{font-size:1.1rem;margin:20px 0 10px;color:#475569}}
table.meta{{border-collapse:collapse;margin-bottom:24px}}
table.meta th{{text-align:left;padding:6px 16px 6px 0;color:#64748b;
font-weight:600}}table.meta td{{padding:6px 0}}
.revision-card{{position:relative;border:1px solid #e2e8f0;
border-radius:10px;margin:14px 0;padding:18px;background:#fff;
box-shadow:0 1px 3px rgba(0,0,0,.04)}}
.card-index{{position:absolute;top:-10px;left:16px;background:#1e3a5f;
color:#fff;font-size:0.7rem;font-weight:700;padding:2px 10px;
border-radius:10px}}
.compare-table{{border-collapse:collapse;width:100%;margin-top:6px}}
.compare-table th{{background:#f1f5f9;padding:8px 12px;font-size:0.8rem;
text-align:left;color:#475569;border:1px solid #e2e8f0}}
.compare-table td{{padding:10px 12px;font-size:0.85rem;
border:1px solid #e2e8f0;white-space:pre-wrap;word-break:break-word;
vertical-align:top}}
.compare-table td.source{{background:#f8fafc}}
.compare-table td.machine{{background:#fffbeb}}
.compare-table td.human{{background:#f0fdf4}}
.card-meta{{margin-top:10px;font-size:0.75rem;color:#64748b}}
.card-meta code{{background:#f1f5f9;padding:1px 5px;border-radius:4px;
font-size:0.75rem}}.card-meta strong{{color:#1e3a5f}}
@media print{{body{{background:#fff}}.revision-card{{break-inside:avoid}}}}
</style></head><body>
<header><h1>Human Revisions Report</h1>
<table class="meta">
<tr><th>Time Range</th><td>{_esc(time_range)}</td></tr>
<tr><th>Total Revisions</th><td>{len(items)}</td></tr>
<tr><th>Generated At</th><td>{_esc(now)}</td></tr>
</table></header>
{"".join(sections)}
</body></html>""".encode("utf-8")


def _render_md_report(items, params):
    from collections import defaultdict
    from datetime import datetime

    lines = [
        "# Human Revisions Report", "",
        f"- **Time Range:** "
        f"{params.get('start_time','...')[:10]} \u2014 "
        f"{params.get('end_time','...')[:10]}",
        f"- **Total:** {len(items)} revisions",
        f"- **Generated:** "
        f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "", "---", "",
    ]

    by_channel = defaultdict(list)
    for item in items:
        by_channel[item.get("channel", "?")].append(item)

    for channel in ("MR Pipeline", "File Translation"):
        ch_items = by_channel.get(channel, [])
        if not ch_items:
            continue
        lines.append(f"## {channel} ({len(ch_items)} edits)")
        lines.append("")
        for idx, it in enumerate(ch_items, 1):
            lines.append(f"### [{idx}] {it.get('opus_id','')}")
            lines.append(
                f"- **Language:** {it.get('target_language','')}")
            lines.append(
                f"- **Source (en-US):** {it.get('source_text','')}")
            lines.append(
                f"- **Machine Translation:** "
                f"{it.get('machine_translation','')}")
            lines.append(
                f"- **Human Revision:** {it.get('human_revision','')}")
            lines.append(
                f"- **Machine Score:** {it.get('machine_score','-')}")
            lines.append(
                f"- **Error Category:** "
                f"{it.get('error_category','-')}")
            lines.append(f"- **Editor:** {it.get('editor','-')}")
            lines.append(
                f"- **Revised At:** "
                f"{str(it.get('revised_at',''))[:16]}")
            lines.append("")

    return "\n".join(lines).encode("utf-8")


def _render_local_report(items, params, fmt):
    """Render report locally -- no server needed."""
    if fmt == "html":
        return _render_html_report(items, params)
    elif fmt == "markdown":
        return _render_md_report(items, params)
    else:
        import io
        import zipfile
        html_bytes = _render_html_report(items, params)
        md_bytes = _render_md_report(items, params)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("human_revisions.html", html_bytes)
            zf.writestr("human_revisions.md", md_bytes)
        return buf.getvalue()
