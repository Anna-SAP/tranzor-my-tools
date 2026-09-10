"""MR → TM Sync tab.

Paste a GitLab MR URL, extract locale-file translation fixes, probe ICE TM,
and optionally submit them through Tranzor blob-based Bug Fix (writes TM
and queues a Bug Fix MR). Logic lives in :mod:`mr_tm_sync`.
"""
from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any

import mr_tm_sync as sync

STRINGS = {
    "en": {
        "tab_mr_tm_sync": "🔁 MR → TM",
        "mts_hint": (
            "Sync locale-only Git fixes into Tranzor TM. Paste a GitLab MR "
            "URL (or IID + project), extract changed translation pairs, "
            "probe ICE TM for stale values, then optionally apply via "
            "Quality → Bug Fix (blob-based). Apply writes TM and queues a "
            "Tranzor Bug Fix MR — close that MR without merge when Git "
            "already has the fix. Default is preview-only."
        ),
        "mts_mr": "MR URL",
        "mts_project": "Project",
        "mts_iid": "IID",
        "mts_parse": "Parse MR",
        "mts_probe": "Probe ICE TM",
        "mts_export_json": "Export JSON",
        "mts_export_tmx": "Export TMX",
        "mts_bug": "Bug / Jira",
        "mts_apply": "Apply to TM",
        "mts_confirm": (
            "I understand Apply writes Tranzor TM and queues a Bug Fix MR"
        ),
        "mts_col_locale": "Locale",
        "mts_col_key": "Key",
        "mts_col_path": "File",
        "mts_col_ice": "ICE TM",
        "mts_col_status": "Status",
        "mts_ready": "Paste an MR URL and click Parse MR.",
        "mts_need_mr": "Enter a GitLab MR URL, or project + IID.",
        "mts_parsing": "Reading MR diffs and locale blobs…",
        "mts_parsed": (
            "{changed} changed pair(s) · {skipped} skipped · "
            "MR {state} · {source} → {target}"
        ),
        "mts_probing": "Probing ICE TM…",
        "mts_probed": "ICE: {stale} stale / {n} probed.",
        "mts_failed": "Failed: {error}",
        "mts_need_plan": "Parse an MR first.",
        "mts_need_confirm": "Tick the confirmation box and enter a Bug / Jira key.",
        "mts_applying": "Submitting blob-based Bug Fix…",
        "mts_applied": "Apply finished · {ok} ok · {fail} failed.",
        "mts_exported": "Wrote {path}",
        "mts_preview_old": "Before (Git old / ICE)",
        "mts_preview_new": "After (MR new)",
        "mts_stale": "stale",
        "mts_fresh": "in sync",
        "mts_unprobed": "—",
    },
    "zh": {
        "tab_mr_tm_sync": "🔁 MR → TM",
        "mts_hint": (
            "把仅改目标语言的 Git 修复同步进 Tranzor TM。粘贴 GitLab MR "
            "URL（或项目 + IID），抽出译文对，探测 ICE TM 是否仍是旧值，"
            "再按需走 Quality → Bug Fix（blob-based）写入。"
            "Apply 会写 TM 并排队一个 Tranzor Bug Fix MR；若 Git 侧已经修好，"
            "把该 MR 不合并关闭即可。默认只预览、不写入。"
        ),
        "mts_mr": "MR URL",
        "mts_project": "项目",
        "mts_iid": "IID",
        "mts_parse": "解析 MR",
        "mts_probe": "探测 ICE TM",
        "mts_export_json": "导出 JSON",
        "mts_export_tmx": "导出 TMX",
        "mts_bug": "Bug / Jira",
        "mts_apply": "写入 TM",
        "mts_confirm": "我理解 Apply 会写 Tranzor TM 并排队 Bug Fix MR",
        "mts_col_locale": "语种",
        "mts_col_key": "Key",
        "mts_col_path": "文件",
        "mts_col_ice": "ICE TM",
        "mts_col_status": "状态",
        "mts_ready": "粘贴 MR URL，然后点「解析 MR」。",
        "mts_need_mr": "请填写 GitLab MR URL，或项目 + IID。",
        "mts_parsing": "正在读取 MR diffs 与 locale 文件…",
        "mts_parsed": (
            "{changed} 条变更 · 跳过 {skipped} · "
            "MR {state} · {source} → {target}"
        ),
        "mts_probing": "正在探测 ICE TM…",
        "mts_probed": "ICE：{stale} 条陈旧 / 共 {n} 条。",
        "mts_failed": "失败：{error}",
        "mts_need_plan": "请先解析 MR。",
        "mts_need_confirm": "请勾选确认框并填写 Bug / Jira 编号。",
        "mts_applying": "正在提交 blob-based Bug Fix…",
        "mts_applied": "写入结束 · 成功 {ok} · 失败 {fail}。",
        "mts_exported": "已写入 {path}",
        "mts_preview_old": "修改前（Git 旧值 / ICE）",
        "mts_preview_new": "修改后（MR 新值）",
        "mts_stale": "陈旧",
        "mts_fresh": "已同步",
        "mts_unprobed": "—",
    },
}


class MrTmSyncTab:
    _COLS = ("locale", "key", "path", "ice", "status")

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self._plan: sync.SyncPlan | None = None
        self._hits: dict[tuple[str, str, str], Any] = {}
        self._busy = False
        self._build(parent)
        self.refresh_text()

    def _t(self, key):
        return self.app._t(key)

    def _button(self, parent, *, command, accent=False):
        from export_gui import FONT_FAMILY
        return self.app._create_button(
            parent,
            text="",
            command=command,
            style_name="AccentSmall" if accent else "SecondarySmall",
            font=(FONT_FAMILY, 10, "bold" if accent else "normal"),
            bg="#e94560" if accent else "#0f3460",
            fg="#fff" if accent else "#ccc",
            padx=12,
            pady=4,
        )

    def _build(self, parent):
        from export_gui import FONT_MONO
        content = ttk.Frame(parent, style="App.TFrame")
        content.pack(fill="both", expand=True, padx=16, pady=8)

        self.lbl_hint = ttk.Label(
            content, text="", style="Status.TLabel",
            wraplength=1450, justify="left")
        self.lbl_hint.pack(fill="x", pady=(0, 8))

        row = ttk.Frame(content, style="App.TFrame")
        row.pack(fill="x", pady=(0, 6))
        self.lbl_mr = ttk.Label(row, text="", style="Status.TLabel")
        self.lbl_mr.pack(side="left")
        self.var_mr = tk.StringVar()
        self.ent_mr = ttk.Entry(row, textvariable=self.var_mr, width=72)
        self.ent_mr.pack(side="left", padx=(6, 12), fill="x", expand=True)

        self.lbl_project = ttk.Label(row, text="", style="Status.TLabel")
        self.lbl_project.pack(side="left")
        self.var_project = tk.StringVar(value="common/uns")
        self.ent_project = ttk.Entry(row, textvariable=self.var_project, width=16)
        self.ent_project.pack(side="left", padx=(6, 8))
        self.lbl_iid = ttk.Label(row, text="", style="Status.TLabel")
        self.lbl_iid.pack(side="left")
        self.var_iid = tk.StringVar()
        self.ent_iid = ttk.Entry(row, textvariable=self.var_iid, width=8)
        self.ent_iid.pack(side="left", padx=(6, 0))

        actions = ttk.Frame(content, style="App.TFrame")
        actions.pack(fill="x", pady=(0, 6))
        self.btn_parse = self._button(actions, command=self._on_parse, accent=True)
        self.btn_parse.pack(side="left")
        self.btn_probe = self._button(actions, command=self._on_probe)
        self.btn_probe.pack(side="left", padx=(6, 0))
        self.btn_json = self._button(actions, command=self._on_export_json)
        self.btn_json.pack(side="left", padx=(6, 0))
        self.btn_tmx = self._button(actions, command=self._on_export_tmx)
        self.btn_tmx.pack(side="left", padx=(6, 0))

        self.lbl_bug = ttk.Label(actions, text="", style="Status.TLabel")
        self.lbl_bug.pack(side="left", padx=(16, 0))
        self.var_bug = tk.StringVar()
        self.ent_bug = ttk.Entry(actions, textvariable=self.var_bug, width=16)
        self.ent_bug.pack(side="left", padx=(6, 8))
        self.var_confirm = tk.BooleanVar(value=False)
        self.chk_confirm = ttk.Checkbutton(
            actions, variable=self.var_confirm, text="")
        self.chk_confirm.pack(side="left")
        self.btn_apply = self._button(actions, command=self._on_apply, accent=True)
        self.btn_apply.pack(side="left", padx=(8, 0))

        self.lbl_status = ttk.Label(content, text="", style="Status.TLabel")
        self.lbl_status.pack(fill="x", pady=(0, 6))

        tree_frame = ttk.Frame(content, style="App.TFrame")
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame, columns=self._COLS, show="headings",
            style="Summary.Treeview", selectmode="browse", height=12)
        widths = {"locale": 80, "key": 280, "path": 420, "ice": 90, "status": 90}
        for col in self._COLS:
            self.tree.heading(col, text="")
            self.tree.column(col, width=widths.get(col, 100), anchor="w")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        preview = ttk.Frame(content, style="App.TFrame")
        preview.pack(fill="both", expand=False, pady=(8, 0))
        left = ttk.Frame(preview, style="App.TFrame")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = ttk.Frame(preview, style="App.TFrame")
        right.pack(side="left", fill="both", expand=True)
        self.lbl_old = ttk.Label(left, text="", style="Status.TLabel")
        self.lbl_old.pack(anchor="w")
        self.txt_old = tk.Text(left, height=7, wrap="word", font=FONT_MONO)
        self.txt_old.pack(fill="both", expand=True)
        self.lbl_new = ttk.Label(right, text="", style="Status.TLabel")
        self.lbl_new.pack(anchor="w")
        self.txt_new = tk.Text(right, height=7, wrap="word", font=FONT_MONO)
        self.txt_new.pack(fill="both", expand=True)
        for widget in (self.txt_old, self.txt_new):
            widget.configure(state="disabled", bg="#1a1a2e", fg="#e7ecff",
                             insertbackground="#e7ecff", relief="flat")

    def refresh_text(self):
        self.lbl_hint.configure(text=self._t("mts_hint"))
        self.lbl_mr.configure(text=self._t("mts_mr"))
        self.lbl_project.configure(text=self._t("mts_project"))
        self.lbl_iid.configure(text=self._t("mts_iid"))
        self.btn_parse.configure(text=self._t("mts_parse"))
        self.btn_probe.configure(text=self._t("mts_probe"))
        self.btn_json.configure(text=self._t("mts_export_json"))
        self.btn_tmx.configure(text=self._t("mts_export_tmx"))
        self.lbl_bug.configure(text=self._t("mts_bug"))
        self.chk_confirm.configure(text=self._t("mts_confirm"))
        self.btn_apply.configure(text=self._t("mts_apply"))
        self.lbl_old.configure(text=self._t("mts_preview_old"))
        self.lbl_new.configure(text=self._t("mts_preview_new"))
        headings = {
            "locale": "mts_col_locale",
            "key": "mts_col_key",
            "path": "mts_col_path",
            "ice": "mts_col_ice",
            "status": "mts_col_status",
        }
        for col, key in headings.items():
            self.tree.heading(col, text=self._t(key))
        if not self._plan:
            self.lbl_status.configure(text=self._t("mts_ready"))

    def on_first_show(self):
        if not self.lbl_status.cget("text"):
            self.lbl_status.configure(text=self._t("mts_ready"))

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.btn_parse, self.btn_probe, self.btn_json,
                    self.btn_tmx, self.btn_apply):
            try:
                btn.configure(state=state)
            except Exception:
                pass

    def _bg(self, fn):
        if self._busy:
            return

        def runner():
            try:
                fn()
            except Exception as exc:
                self.parent.after(0, lambda: self._fail(exc))
            finally:
                self.parent.after(0, lambda: self._set_busy(False))

        self._set_busy(True)
        threading.Thread(target=runner, daemon=True).start()

    def _fail(self, exc):
        self.lbl_status.configure(text=self._t("mts_failed").format(error=exc))

    def _resolve_mr(self) -> tuple[str, int]:
        url = self.var_mr.get().strip()
        if url:
            return sync.parse_mr_url(url)
        project = self.var_project.get().strip()
        iid = self.var_iid.get().strip()
        if project and iid.isdigit():
            return project, int(iid)
        raise ValueError(self._t("mts_need_mr"))

    def _on_parse(self):
        try:
            project_id, iid = self._resolve_mr()
        except Exception as exc:
            self._fail(exc)
            return
        self.lbl_status.configure(text=self._t("mts_parsing"))

        def work():
            client = sync._build_client()
            plan = sync.extract_plan_from_mr(client, project_id, iid)
            self.parent.after(0, lambda: self._show_plan(plan))

        self._bg(work)

    def _show_plan(self, plan: sync.SyncPlan):
        self._plan = plan
        self._hits = {}
        for item in self.tree.get_children():
            self.tree.delete(item)
        for pair in plan.changed_pairs():
            self.tree.insert(
                "", "end",
                iid=_pair_iid(pair),
                values=(
                    pair.target_language,
                    pair.string_key,
                    pair.target_path,
                    self._t("mts_unprobed"),
                    "changed",
                ),
            )
        self.lbl_status.configure(text=self._t("mts_parsed").format(
            changed=len(plan.changed_pairs()),
            skipped=len(plan.skipped),
            state=plan.mr_state or "—",
            source=plan.source_branch or "—",
            target=plan.target_branch or "—",
        ))
        if plan.mr_iid and not self.var_iid.get().strip():
            self.var_iid.set(str(plan.mr_iid))
        if plan.project_id:
            self.var_project.set(plan.project_id)

    def _on_probe(self):
        if not self._plan:
            self.lbl_status.configure(text=self._t("mts_need_plan"))
            return
        self.lbl_status.configure(text=self._t("mts_probing"))
        pairs = list(self._plan.changed_pairs())

        def work():
            hits = sync.probe_ice(pairs)
            self.parent.after(0, lambda: self._show_hits(hits))

        self._bg(work)

    def _show_hits(self, hits):
        stale = 0
        for hit in hits:
            key = _pair_key(hit.pair)
            self._hits[key] = hit
            if hit.stale:
                stale += 1
            iid = _pair_iid(hit.pair)
            if self.tree.exists(iid):
                values = list(self.tree.item(iid, "values"))
                values[3] = self._t("mts_stale" if hit.stale else "mts_fresh")
                if hit.error:
                    values[3] = "error"
                self.tree.item(iid, values=values)
        self.lbl_status.configure(text=self._t("mts_probed").format(
            stale=stale, n=len(hits)))

    def _on_select(self, _event=None):
        selection = self.tree.selection()
        if not selection or not self._plan:
            return
        iid = selection[0]
        pair = next((p for p in self._plan.changed_pairs()
                     if _pair_iid(p) == iid), None)
        if pair is None:
            return
        hit = self._hits.get(_pair_key(pair))
        old = pair.old_target_text
        if hit and hit.ice_target:
            old = f"[ICE] {hit.ice_target}\n---\n[Git old] {pair.old_target_text}"
        _set_text(self.txt_old, old)
        _set_text(self.txt_new, pair.new_target_text)

    def _export_path(self, title, default_name, types):
        try:
            from export_gui import export_output_dir
            initial = export_output_dir()
        except Exception:
            initial = os.path.expanduser("~")
        return filedialog.asksaveasfilename(
            title=title,
            initialdir=initial,
            initialfile=default_name,
            defaultextension=types[0][1],
            filetypes=types,
        )

    def _on_export_json(self):
        if not self._plan:
            self.lbl_status.configure(text=self._t("mts_need_plan"))
            return
        path = self._export_path(
            "JSON", "mr_tm_sync.json",
            [("JSON", ".json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(sync.plan_to_dict(self._plan), fh,
                      ensure_ascii=False, indent=2)
        self.lbl_status.configure(text=self._t("mts_exported").format(path=path))

    def _on_export_tmx(self):
        if not self._plan:
            self.lbl_status.configure(text=self._t("mts_need_plan"))
            return
        path = self._export_path(
            "TMX", "mr_tm_sync.tmx",
            [("TMX", ".tmx")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(sync.plan_to_tmx(self._plan))
        self.lbl_status.configure(text=self._t("mts_exported").format(path=path))

    def _on_apply(self):
        if not self._plan:
            self.lbl_status.configure(text=self._t("mts_need_plan"))
            return
        bug_id = self.var_bug.get().strip()
        if not self.var_confirm.get() or not bug_id:
            self.lbl_status.configure(text=self._t("mts_need_confirm"))
            return
        self.lbl_status.configure(text=self._t("mts_applying"))
        plan = self._plan

        def work():
            token = sync._load_auth_token()
            results = sync.apply_plan(
                plan, bug_id=bug_id, token=token, dry_run=False)
            self.parent.after(0, lambda: self._show_apply(results))

        self._bg(work)

    def _show_apply(self, results):
        fail = sum(1 for item in results
                   if item.status not in ("queued", "created", "applied", "dry_run"))
        ok = len(results) - fail
        self.lbl_status.configure(
            text=self._t("mts_applied").format(ok=ok, fail=fail))
        by_path = {item.source_path: item for item in results}
        if not self._plan:
            return
        for pair in self._plan.changed_pairs():
            item = by_path.get(pair.source_path)
            if not item:
                continue
            iid = _pair_iid(pair)
            if self.tree.exists(iid):
                values = list(self.tree.item(iid, "values"))
                values[4] = item.status
                self.tree.item(iid, values=values)


def _pair_key(pair: sync.TranslationPair) -> tuple[str, str, str]:
    return (pair.target_path, pair.string_key, pair.target_language)


def _pair_iid(pair: sync.TranslationPair) -> str:
    raw = "|".join(_pair_key(pair))
    return raw[:200]


def _set_text(widget: tk.Text, value: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", value or "")
    widget.configure(state="disabled")
