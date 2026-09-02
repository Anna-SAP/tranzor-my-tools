"""
Key Origin —— GUI Tab
=====================
把粘贴进来的字符串 Key（或 UNS 模板路径）反查到源头 MR Pipeline /
File Translation 任务，打开 Tranzor 页面做 Language Lead 后期修订。

用于 Bug Fix 通道尚未覆盖的产品（目前 ``common/uns`` 显示 Unsupported）。

数据 / 逻辑层在 :mod:`key_origin`（无 Tk 依赖、可单测）；本模块只做 UI。
纯加法：不修改任何现有模块；控件只用标准 ttk + 现有 style。
"""
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import ttk

# i18n STRINGS —— 必须在模块顶部定义：export_gui 反向 import 本模块读取
# STRINGS 做合并，放在 from-import 之后会被静默吞掉。
STRINGS = {
    "en": {
        "tab_key_origin": "🔑 Key Origin",
        "ko_hint": (
            "Paste string keys (one per line) — including UNS "
            "`:::seg:::` pipeline ids and uns-app/*.hbs paths. "
            "The panel finds the MR Pipeline task that originally "
            "translated them, so you can Language-Lead-fix products "
            "whose Bug Fix channel is still Unsupported (e.g. common/uns)."),
        "ko_source": "Channel",
        "ko_source_mr": "MR Pipeline",
        "ko_source_file": "File Translation",
        "ko_source_all": "All",
        "ko_locate": "🔎 Locate origin",
        "ko_clear": "Clear",
        "ko_open": "↗ Open origin",
        "ko_copy": "Copy report",
        "ko_legend": (
            "Double-click a group to open it in Tranzor. "
            "UNS `:::seg:::N` suffixes are stripped automatically "
            "(the database stores the base key)."),
        "ko_col_group": "Origin task / Key",
        "ko_col_project": "Project",
        "ko_col_mr": "MR#",
        "ko_col_created": "Created",
        "ko_col_langs": "#Langs",
        "ko_col_kind": "Channel",
        "ko_col_match": "Match",
        "ko_group_mr": "{project}  ·  MR#{mr}   ({n} keys)",
        "ko_group_file": "File task {short}…   ({n} keys)",
        "ko_group_missing": "Not found   ({n} keys)",
        "ko_locating": "Looking up {n} key(s)…",
        "ko_need_keys": "Paste at least one key.",
        "ko_done": "{found} key(s) located · {groups} origin task(s) · {missing} missing",
        "ko_done_trunc": "{found} located · {groups} origin task(s) · truncated at {cap} keys",
        "ko_failed": "Lookup failed: {error}",
        "ko_copied": "Copied {n} line(s).",
        "ko_need_select": "Select an origin group first.",
        "ko_no_url": "No Tranzor URL for this row.",
        "ko_placeholder": (
            "common.uns.announcementsOnlyLoginInfo__email_html__7710:::seg:::10\n"
            "common.uns.new.announcementsOnlyLoginInfo__email_html__7710:::seg:::10"),
    },
    "zh": {
        "tab_key_origin": "🔑 Key 溯源",
        "ko_hint": (
            "粘贴字符串 Key（每行一条），支持 UNS 的 `:::seg:::` 分段 id "
            "以及 uns-app/*.hbs 路径。面板会找到当初译出这些 Key 的 "
            "MR Pipeline 任务，便于在 Bug Fix 通道尚不支持的产品"
            "（如 common/uns）上改走 Language Lead 后期修订。"),
        "ko_source": "通道",
        "ko_source_mr": "MR Pipeline",
        "ko_source_file": "文件翻译",
        "ko_source_all": "全部",
        "ko_locate": "🔎 查找源头",
        "ko_clear": "清空",
        "ko_open": "↗ 打开源头任务",
        "ko_copy": "复制报告",
        "ko_legend": (
            "双击分组即可在 Tranzor 中打开。"
            "UNS 的 `:::seg:::N` 后缀会自动剥掉（库表只存基 Key）。"),
        "ko_col_group": "源头任务 / Key",
        "ko_col_project": "项目",
        "ko_col_mr": "MR#",
        "ko_col_created": "创建时间",
        "ko_col_langs": "语种数",
        "ko_col_kind": "通道",
        "ko_col_match": "匹配",
        "ko_group_mr": "{project}  ·  MR#{mr}   ({n} 条 Key)",
        "ko_group_file": "文件任务 {short}…   ({n} 条 Key)",
        "ko_group_missing": "未找到   ({n} 条 Key)",
        "ko_locating": "正在查找 {n} 条 Key…",
        "ko_need_keys": "请至少粘贴一条 Key。",
        "ko_done": "定位 {found} 条 · {groups} 个源头任务 · {missing} 条未找到",
        "ko_done_trunc": "定位 {found} 条 · {groups} 个源头任务 · 已截断至 {cap} 条",
        "ko_failed": "查找失败：{error}",
        "ko_copied": "已复制 {n} 行。",
        "ko_need_select": "请先选中一个源头分组。",
        "ko_no_url": "此行没有可打开的 Tranzor 链接。",
        "ko_placeholder": (
            "common.uns.announcementsOnlyLoginInfo__email_html__7710:::seg:::10\n"
            "common.uns.new.announcementsOnlyLoginInfo__email_html__7710:::seg:::10"),
    },
}

from export_gui import FONT_FAMILY, FONT_MONO  # noqa: E402

import export_mr_pipeline as mr_api  # noqa: E402
import key_origin as ko  # noqa: E402


class KeyOriginTab:
    """Key → origin-task lookup panel."""

    _COLS = ("project", "mr", "created", "langs", "kind", "match")

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self._first_shown = False
        self._locating = False
        self._row_data: dict[str, dict] = {}
        self._payload = None
        self._build(parent)
        self.refresh_text()

    def _t(self, key):
        return self.app._t(key)

    def _base_url(self) -> str:
        return mr_api.TRANZOR_URL

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------
    def _build(self, parent):
        content = ttk.Frame(parent, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=16, pady=8)

        self.lbl_hint = ttk.Label(
            content, text="", style="Status.TLabel", wraplength=1100,
            justify="left")
        self.lbl_hint.pack(fill="x", pady=(0, 8))

        text_frame = ttk.Frame(content, style="App.TFrame")
        text_frame.pack(fill="x", pady=(0, 8))
        self.text = tk.Text(
            text_frame, wrap="none", height=8, bg="#0a0a1a", fg="#fff",
            insertbackground="#fff", relief="flat", font=(FONT_MONO, 10),
            padx=8, pady=6)
        sb_in = ttk.Scrollbar(text_frame, orient="vertical",
                              command=self.text.yview)
        self.text.configure(yscrollcommand=sb_in.set)
        self.text.pack(side="left", fill="x", expand=True)
        sb_in.pack(side="right", fill="y")

        bar = ttk.Frame(content, style="App.TFrame")
        bar.pack(fill="x", pady=(0, 6))

        self.lbl_source = ttk.Label(bar, text="", style="Status.TLabel")
        self.lbl_source.pack(side="left")
        self.var_source = tk.StringVar(value="mr")
        self.cmb_source = ttk.Combobox(
            bar, textvariable=self.var_source, state="readonly", width=16,
            values=["mr", "file", "all"])
        self.cmb_source.pack(side="left", padx=(6, 0))

        self.btn_locate = self.app._create_button(
            bar, text="", command=self._on_locate, style_name="AccentSmall",
            font=(FONT_FAMILY, 10, "bold"), bg="#e94560", fg="#fff",
            padx=16, pady=4)
        self.btn_locate.pack(side="left", padx=(12, 0))

        self.btn_clear = self.app._create_button(
            bar, text="", command=self._on_clear, style_name="SecondarySmall",
            font=(FONT_FAMILY, 10), bg="#0f3460", fg="#ccc", padx=12, pady=4)
        self.btn_clear.pack(side="left", padx=(6, 0))

        self.btn_open = self.app._create_button(
            bar, text="", command=self._on_open, style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"), bg="#16a34a", fg="#fff",
            padx=14, pady=4)
        self.btn_open.pack(side="left", padx=(12, 0))

        self.btn_copy = self.app._create_button(
            bar, text="", command=self._on_copy, style_name="SecondarySmall",
            font=(FONT_FAMILY, 10), bg="#0f3460", fg="#ccc", padx=12, pady=4)
        self.btn_copy.pack(side="left", padx=(6, 0))

        self.lbl_status = ttk.Label(bar, text="", style="Status.TLabel")
        self.lbl_status.pack(side="left", padx=(14, 0))

        self.lbl_legend = ttk.Label(
            content, text="", style="Status.TLabel", wraplength=1100,
            justify="left")
        self.lbl_legend.pack(fill="x", pady=(0, 6))

        tree_frame = ttk.Frame(content, style="App.TFrame")
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame, columns=self._COLS, show="tree headings",
            style="Summary.Treeview", selectmode="browse", height=16)
        self.tree.column("#0", width=420, anchor="w", stretch=True)
        widths = {"project": 140, "mr": 70, "created": 160, "langs": 60,
                  "kind": 90, "match": 70}
        for col in self._COLS:
            anchor = "center" if col in ("mr", "langs", "match") else "w"
            self.tree.column(col, width=widths.get(col, 100), anchor=anchor)
        self.tree.tag_configure("group", foreground="#e7ecff")
        self.tree.tag_configure("missing", foreground="#fca5a5")
        self.tree.tag_configure("hit", foreground="#86efac")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_double)

    # ------------------------------------------------------------------
    # i18n
    # ------------------------------------------------------------------
    def refresh_text(self):
        t = self._t
        self.lbl_hint.configure(text=t("ko_hint"))
        self.lbl_legend.configure(text=t("ko_legend"))
        self.lbl_source.configure(text=t("ko_source"))
        self.btn_locate.configure(text=t("ko_locate"))
        self.btn_clear.configure(text=t("ko_clear"))
        self.btn_open.configure(text=t("ko_open"))
        self.btn_copy.configure(text=t("ko_copy"))
        labels = {
            "mr": t("ko_source_mr"),
            "file": t("ko_source_file"),
            "all": t("ko_source_all"),
        }
        raw = self._source_raw()
        self.cmb_source.configure(values=[
            labels["mr"], labels["file"], labels["all"]])
        self.var_source.set(labels.get(raw, labels["mr"]))
        self.tree.heading("#0", text=t("ko_col_group"))
        self.tree.heading("project", text=t("ko_col_project"))
        self.tree.heading("mr", text=t("ko_col_mr"))
        self.tree.heading("created", text=t("ko_col_created"))
        self.tree.heading("langs", text=t("ko_col_langs"))
        self.tree.heading("kind", text=t("ko_col_kind"))
        self.tree.heading("match", text=t("ko_col_match"))

    def _source_raw(self) -> str:
        val = (self.var_source.get() or "").strip()
        mapping = {
            self._t("ko_source_mr"): "mr",
            self._t("ko_source_file"): "file",
            self._t("ko_source_all"): "all",
            "mr": "mr",
            "file": "file",
            "all": "all",
            "MR Pipeline": "mr",
            "File Translation": "file",
            "All": "all",
            "文件翻译": "file",
            "全部": "all",
        }
        return mapping.get(val, "mr")

    # ------------------------------------------------------------------
    def _busy(self, text):
        try:
            self.app._mark_busy(self.lbl_status, text)
        except Exception:
            try:
                self.lbl_status.configure(text=text)
            except Exception:
                pass

    def _idle(self, text=""):
        try:
            self.app._mark_idle(self.lbl_status, text)
        except Exception:
            try:
                self.lbl_status.configure(text=text)
            except Exception:
                pass

    def on_first_show(self):
        if self._first_shown:
            return
        self._first_shown = True
        current = self.text.get("1.0", "end").strip()
        if not current:
            # Placeholder is a hint only; user still pastes real keys.
            self.text.insert("1.0", "")

    # ------------------------------------------------------------------
    def _on_clear(self):
        self.text.delete("1.0", "end")
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._row_data.clear()
        self._payload = None
        self._idle("")

    def _on_locate(self):
        if self._locating:
            return
        raw = self.text.get("1.0", "end")
        queries = ko.parse_lookup_keys(raw)
        if not queries:
            self._idle(self._t("ko_need_keys"))
            return
        source_type = self._source_raw()
        self._locating = True
        self._busy(self._t("ko_locating").format(n=len(queries)))

        def _work():
            err = None
            payload = None
            try:
                payload = self._locate_parallel(raw, source_type)
            except Exception as exc:  # noqa: BLE001
                err = self._t("ko_failed").format(error=str(exc)[:120])
            try:
                self.parent.after(0, lambda: self._render(payload, err))
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True, name="key-origin").start()

    def _locate_parallel(self, raw, source_type):
        """Run per-key searches concurrently; HTTP gate still caps sockets."""
        queries = ko.parse_lookup_keys(raw, max_keys=ko.MAX_KEYS + 1)
        truncated = len(queries) > ko.MAX_KEYS
        queries = queries[:ko.MAX_KEYS]
        base_url = self._base_url()

        def _search(**kwargs):
            return mr_api.search_translations(base_url=base_url, **kwargs)

        # Fan-out the independent per-key lookups (each key is its own
        # exact-then-fuzzy search). locate_keys itself is sequential; a
        # thin parallel wrapper keeps an 8-key paste snappy.
        results = [None] * len(queries)
        workers = min(4, max(1, len(queries)))
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="key-origin") as pool:
            futs = {
                pool.submit(
                    ko._search_one,
                    _search,
                    query,
                    source_type=source_type,
                    fallback_file=(source_type == "mr"),
                    page_size=ko.DEFAULT_PAGE_SIZE,
                    max_pages=ko.DEFAULT_MAX_PAGES,
                ): idx
                for idx, query in enumerate(queries)
            }
            for fut in as_completed(futs):
                results[futs[fut]] = fut.result()
        payload = {
            "queries": queries,
            "results": results,
            "groups": ko.group_origin_results(results, base_url=base_url),
            "found": sum(1 for r in results if r and r.get("recommended")),
            "missing": sum(1 for r in results if r and not r.get("recommended")),
            "truncated": truncated,
        }
        return payload

    def _render(self, payload, err):
        self._locating = False
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._row_data.clear()
        self._payload = payload
        if err:
            self._idle(err)
            return
        if not payload:
            self._idle(self._t("ko_failed").format(error="empty"))
            return
        by_search = {
            r["query"].search_opus_id: r
            for r in (payload.get("results") or [])
            if r and r.get("query") is not None
        }
        groups = payload.get("groups") or []
        for group in groups:
            rec = group.get("recommended")
            keys = group.get("keys") or []
            if group.get("missing"):
                label = self._t("ko_group_missing").format(n=len(keys))
                gid = self.tree.insert(
                    "", "end", text=label,
                    values=("", "", "", "", "", ""),
                    tags=("missing", "group"), open=True)
                self._row_data[gid] = {"kind": "group", "group": group}
                for query in keys:
                    kid = self.tree.insert(
                        gid, "end", text=query.search_opus_id,
                        values=("", "", "", "", "", ""),
                        tags=("missing",))
                    self._row_data[kid] = {"kind": "key", "query": query,
                                           "group": group, "hit": None}
                continue
            if rec and rec.source_type == "mr" and rec.mr_iid not in (None, ""):
                label = self._t("ko_group_mr").format(
                    project=rec.project_id or "",
                    mr=rec.mr_iid,
                    n=len(keys))
            else:
                short = (rec.task_id if rec else "")[:8]
                label = self._t("ko_group_file").format(
                    short=short, n=len(keys))
            created = (rec.created_at if rec else "")[:19].replace("T", " ")
            gid = self.tree.insert(
                "", "end", text=label,
                values=(
                    rec.project_id if rec else "",
                    rec.mr_iid if rec and rec.mr_iid is not None else "",
                    created,
                    len(rec.langs) if rec else "",
                    rec.source_type if rec else "",
                    "",
                ),
                tags=("group", "hit"), open=True)
            self._row_data[gid] = {"kind": "group", "group": group}
            for query in keys:
                hit = by_search.get(query.search_opus_id) or {}
                rec_k = hit.get("recommended") or rec
                created_k = (rec_k.created_at if rec_k else "")[:19].replace(
                    "T", " ")
                kid = self.tree.insert(
                    gid, "end", text=query.search_opus_id,
                    values=(
                        rec_k.project_id if rec_k else "",
                        rec_k.mr_iid if rec_k and rec_k.mr_iid is not None else "",
                        created_k,
                        len(rec_k.langs) if rec_k else "",
                        hit.get("source_type_used") or "",
                        hit.get("match_mode") or "",
                    ),
                    tags=("hit",))
                self._row_data[kid] = {
                    "kind": "key", "query": query, "group": group, "hit": hit,
                }
        found_groups = [g for g in groups if not g.get("missing")]
        if payload.get("truncated"):
            self._idle(self._t("ko_done_trunc").format(
                found=payload.get("found", 0),
                groups=len(found_groups),
                cap=ko.MAX_KEYS))
        else:
            self._idle(self._t("ko_done").format(
                found=payload.get("found", 0),
                groups=len(found_groups),
                missing=payload.get("missing", 0)))

    def _selected_url(self) -> str:
        sel = self.tree.selection()
        if not sel:
            return ""
        data = self._row_data.get(sel[0]) or {}
        group = data.get("group") or {}
        return group.get("url") or ""

    def _on_open(self):
        url = self._selected_url()
        if not url:
            # Fall back to the first found group when nothing is selected.
            if self._payload:
                for group in self._payload.get("groups") or []:
                    if group.get("url"):
                        url = group["url"]
                        break
        if not url:
            self._idle(self._t("ko_need_select"))
            return
        webbrowser.open_new_tab(url)

    def _on_double(self, _event=None):
        url = self._selected_url()
        if url:
            webbrowser.open_new_tab(url)

    def _on_copy(self):
        if not self._payload:
            self._idle(self._t("ko_need_select"))
            return
        text = ko.format_origin_report(self._payload)
        try:
            self.parent.clipboard_clear()
            self.parent.clipboard_append(text)
            try:
                self.parent.update()
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            self._idle(self._t("ko_failed").format(error=str(exc)[:80]))
            return
        n = len([ln for ln in text.splitlines() if ln])
        self._idle(self._t("ko_copied").format(n=n))
