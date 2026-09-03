"""
Same Origin —— 同源（同一 MR 多次触发）翻译一致性面板（GUI Tab）
==================================================================

捕获、记录、分析 Tranzor「MR Pipeline」通道里 **同一个 Merge Request 被触发
多次翻译** 的情况：把同一 (Core product, MR#) 的多次任务聚合在一起，继承
MR Pipeline 的 ✏️ 后期修订标记，并提供「Analyze Diff」—— 一键拉取该 MR 下
各历史任务的最新译文、按语种交叉比对、逐字高亮跨任务不一致，帮助本地化经理
快速定位「一次微小源文改动→18 语种被大面积重写」这类隐患。

数据 / 逻辑层在 :mod:`same_origin`（无 Tk 依赖、可单测）；本模块只做 UI。

纯加法：不修改任何现有模块；控件只用标准 ttk + 现有 style，便于在无法可视化
测试的环境里保持稳健（同 OPUS Search tab 的稳健性约定）。
"""
from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

# i18n STRINGS —— 必须在模块顶部定义：export_gui 反向 import 本模块读取
# STRINGS 做合并，放在 from-import 之后会被静默吞掉（同 OPUS Search /
# OPUS ID Monitor 的约定）。
STRINGS = {
    "en": {
        "tab_same_origin":              "🧬 Same Origin",
        "so_hint": (
            "Same MR, triggered more than once → translation may diverge across "
            "runs. This panel groups repeat MR-pipeline tasks (Core products "
            "only) by Project & MR#. Select a group and click Analyze Diff to "
            "cross-compare every run's latest translation, locale by locale."),
        "so_scan":                      "🔄 Scan",
        "so_rescan":                    "🔄 Re-scan",
        "so_analyze":                   "🔍 Analyze Diff",
        "so_config":                    "⚙ Core products…",
        "so_status_filter":             "Status",
        "so_status_completed":          "completed",
        "so_status_all":                "(all)",
        "so_legend": (
            "✏️ = this MR contains a human post-edit (Language-Lead fix). "
            "Double-click a group — or select it and click Analyze Diff."),
        "so_col_group":                 "Project · MR# (runs)",
        "so_col_created":               "Created",
        "so_col_status":                "Status",
        "so_col_score":                 "Avg Score",
        "so_col_duration":              "Duration",
        "so_group_label":               "{project}  ·  MR#{mr}   ({n}×)",
        "so_group_release":             "Release {release}",
        "so_task_label":                "↳ {short}…",
        "so_scanning":                  "Scanning MR tasks…",
        "so_scan_done":                 "{groups} same-origin MR group(s) · {tasks} tasks scanned",
        "so_scan_done_trunc":           "{groups} group(s) · scanned {tasks}/{total} (capped)",
        "so_scan_empty":                "No Core-product MR was triggered more than once. ✓",
        "so_scan_failed":               "Scan failed: {error}",
        "so_need_select":               "Select an MR group first.",
        "so_no_core":                   "No Core products configured — open ⚙ Core products to add some.",
        # Diff dialog
        "so_diff_title":                "Analyze Diff — {project} · MR#{mr}",
        "so_diff_computing":            "Fetching each run's translations and comparing…",
        "so_diff_summary": (
            "{locales} locale(s) diverge · {divergent} inconsistent string(s) · "
            "{keys} compared · {tasks} runs"),
        "so_diff_none":                 "✓ All runs are byte-identical — no divergence across the {tasks} tasks.",
        "so_diff_insufficient":         "Couldn't compare — only {ok} of {total} runs fetched ({failed} failed). Try again.",
        "so_diff_partial":              "⚠ {failed} run(s) couldn't be fetched and were excluded. ",
        "so_diff_failed":               "Diff failed: {error}",
        "so_diff_locale_count":         "{locale}  ({n})",
        "so_diff_col_key":              "Locale / String",
        "so_diff_col_versions":         "Runs",
        "so_diff_src":                  "Source (en-US)",
        "so_diff_version":              "Run {i} · {when}",
        "so_diff_missing":              "（this string does not exist in this run）",
        "so_diff_added_removed":        "added / removed between runs",
        "so_diff_pick":                 "Pick a string on the left to see the per-run diff.",
        "so_diff_legend":               "Red = removed in a later run · Green = added in a later run",
        "so_close":                     "Close",
        # Config dialog
        "so_cfg_title":                 "Core products — Same Origin",
        "so_cfg_hint": (
            "One project_id per line (e.g. web/web). These are the products the "
            "Same Origin panel scans. Delete the config file to fall back to the "
            "built-in defaults."),
        "so_cfg_save":                  "Save & Re-scan",
        "so_cfg_reset":                 "Reset to defaults",
        "so_cfg_cancel":                "Cancel",
        "so_cfg_empty":                 "The list can't be empty.",
        "so_cfg_custom":                "customized",
        "so_cfg_default":               "defaults",
    },
    "zh": {
        "tab_same_origin":              "🧬 同源任务",
        "so_hint": (
            "同一个 MR 被触发多次 → 各次翻译可能不一致。本面板把「MR Pipeline」"
            "通道里同一 (核心产品, MR#) 的多次任务聚合在一起（仅核心产品）。"
            "选中某个分组点「Analyze Diff」，即可按语种交叉比对各次运行的最新译文。"),
        "so_scan":                      "🔄 扫描",
        "so_rescan":                    "🔄 重新扫描",
        "so_analyze":                   "🔍 分析差异",
        "so_config":                    "⚙ 核心产品…",
        "so_status_filter":             "状态",
        "so_status_completed":          "completed",
        "so_status_all":                "(全部)",
        "so_legend": (
            "✏️ = 该 MR 含人工后期修订（Language Lead fix）。"
            "双击分组，或选中后点「分析差异」。"),
        "so_col_group":                 "项目 · MR#（次数）",
        "so_col_created":               "创建时间",
        "so_col_status":                "状态",
        "so_col_score":                 "平均分",
        "so_col_duration":              "耗时",
        "so_group_label":               "{project}  ·  MR#{mr}   ({n} 次)",
        "so_group_release":             "Release {release}",
        "so_task_label":                "↳ {short}…",
        "so_scanning":                  "正在扫描 MR 任务…",
        "so_scan_done":                 "{groups} 个同源 MR 分组 · 已扫描 {tasks} 个任务",
        "so_scan_done_trunc":           "{groups} 个分组 · 已扫描 {tasks}/{total}（已截断）",
        "so_scan_empty":                "没有核心产品的 MR 被触发超过一次。✓",
        "so_scan_failed":               "扫描失败：{error}",
        "so_need_select":               "请先选中一个 MR 分组。",
        "so_no_core":                   "未配置核心产品 —— 打开「⚙ 核心产品」添加。",
        # Diff dialog
        "so_diff_title":                "分析差异 —— {project} · MR#{mr}",
        "so_diff_computing":            "正在拉取各次运行的译文并比对…",
        "so_diff_summary": (
            "{locales} 个语种有分叉 · {divergent} 条不一致 · "
            "比对 {keys} 条 · {tasks} 次运行"),
        "so_diff_none":                 "✓ 各次运行逐字节一致 —— {tasks} 个任务之间无差异。",
        "so_diff_insufficient":         "无法比对 —— {total} 次运行只拉到 {ok} 次（{failed} 次失败）。请重试。",
        "so_diff_partial":              "⚠ {failed} 次运行拉取失败、已排除比对。 ",
        "so_diff_failed":               "差异分析失败：{error}",
        "so_diff_locale_count":         "{locale}  ({n})",
        "so_diff_col_key":              "语种 / 字符串",
        "so_diff_col_versions":         "运行数",
        "so_diff_src":                  "源文 (en-US)",
        "so_diff_version":              "第 {i} 次 · {when}",
        "so_diff_missing":              "（该次运行中不存在此串）",
        "so_diff_added_removed":        "运行之间被新增 / 删除",
        "so_diff_pick":                 "在左侧选一条字符串，查看跨运行的逐字差异。",
        "so_diff_legend":               "红 = 后一次运行删去 · 绿 = 后一次运行新增",
        "so_close":                     "关闭",
        # Config dialog
        "so_cfg_title":                 "核心产品 —— 同源任务",
        "so_cfg_hint": (
            "每行一个 project_id（如 web/web）。这些是「同源任务」面板扫描的产品范围。"
            "删除该配置文件即回落到内置默认清单。"),
        "so_cfg_save":                  "保存并重新扫描",
        "so_cfg_reset":                 "恢复默认",
        "so_cfg_cancel":                "取消",
        "so_cfg_empty":                 "清单不能为空。",
        "so_cfg_custom":                "已自定义",
        "so_cfg_default":               "默认",
    },
}

# 这些常量在可选 tab 导入前已在 export_gui 中定义；此处反向 import 安全
# （export_gui 已在 sys.modules 中、属性已就绪）。
from export_gui import FONT_FAMILY, FONT_MONO, IS_MAC  # noqa: E402
from time_display import format_display_datetime  # noqa: E402

import same_origin  # noqa: E402
import task_post_edit as _tpe  # noqa: E402


def _duration_str(task: dict) -> str:
    """从 created_at / updated_at 估算任务耗时，复用 MR Pipeline 的口径。"""
    created = task.get("created_at") or ""
    updated = task.get("updated_at") or ""
    try:
        if created and updated:
            c = datetime.fromisoformat(created[:19])
            u = datetime.fromisoformat(updated[:19])
            secs = int((u - c).total_seconds())
            if secs < 0:
                return ""
            if secs < 60:
                return f"{secs}s"
            return f"{secs // 60}m{secs % 60}s"
    except Exception:
        pass
    return ""


class SameOriginTab:
    """同源任务面板。"""

    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self._first_shown = False
        self._scanning = False
        self._group_by_iid: dict[str, dict] = {}   # tree iid → group dict
        self._group_iid_by_mr: dict[str, str] = {}  # "pid::mr" → group tree iid
        self._core_products: list[str] = same_origin.load_core_products()
        self._build(parent)
        self.refresh_text()

    def _t(self, key):
        return self.app._t(key)

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

        # ── 操作条 ──
        bar = ttk.Frame(content, style="App.TFrame")
        bar.pack(fill="x", pady=(0, 6))

        self.btn_scan = self.app._create_button(
            bar, text="", command=self._on_scan, style_name="AccentSmall",
            font=(FONT_FAMILY, 10, "bold"), bg="#e94560", fg="#fff",
            padx=16, pady=4)
        self.btn_scan.pack(side="left")

        self.lbl_status_filter = ttk.Label(bar, text="", style="Status.TLabel")
        self.lbl_status_filter.pack(side="left", padx=(14, 4))
        self.var_status = tk.StringVar(value="completed")
        self.cmb_status = ttk.Combobox(
            bar, textvariable=self.var_status, state="readonly", width=12,
            values=["completed", "(all)"])
        self.cmb_status.pack(side="left")

        self.btn_analyze = self.app._create_button(
            bar, text="", command=self._on_analyze, style_name="SuccessSmall",
            font=(FONT_FAMILY, 10, "bold"), bg="#16a34a", fg="#fff",
            padx=14, pady=4)
        self.btn_analyze.pack(side="left", padx=(14, 0))

        self.btn_config = self.app._create_button(
            bar, text="", command=self._on_config, style_name="SecondarySmall",
            font=(FONT_FAMILY, 10), bg="#0f3460", fg="#ccc", padx=12, pady=4)
        self.btn_config.pack(side="left", padx=(8, 0))

        self.lbl_status = ttk.Label(bar, text="", style="Status.TLabel")
        self.lbl_status.pack(side="left", padx=(14, 0))

        self.lbl_legend = ttk.Label(
            content, text="", style="Status.TLabel", wraplength=1100,
            justify="left")
        self.lbl_legend.pack(fill="x", pady=(0, 6))

        # ── 分组树（tree + headings）──
        tree_frame = ttk.Frame(content, style="App.TFrame")
        tree_frame.pack(fill="both", expand=True)

        self._cols = ("created", "status", "score", "duration")
        self.tree = ttk.Treeview(
            tree_frame, columns=self._cols, show="tree headings",
            style="Summary.Treeview", selectmode="browse", height=18)
        self.tree.column("#0", width=440, anchor="w", stretch=True)
        widths = {"created": 185, "status": 130, "score": 80, "duration": 80}
        for c in self._cols:
            anchor = "center" if c in ("score", "duration") else "w"
            self.tree.column(c, width=widths.get(c, 100), anchor=anchor)

        # ✏️ 后期修订：暖金色（与 MR Pipeline / Scan Tasks 同一调色板，跨面板
        # 信号一致）。group 行用极淡的强调底色，和子任务行区分。
        self.tree.tag_configure(
            "post_edit", background="#3a2e1f", foreground="#fde68a")
        self.tree.tag_configure("group", foreground="#e7ecff")

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
        self.lbl_hint.configure(text=t("so_hint"))
        self.lbl_legend.configure(text=t("so_legend"))
        self.btn_scan.configure(
            text=t("so_rescan") if self.tree.get_children() else t("so_scan"))
        self.btn_analyze.configure(text=t("so_analyze"))
        self.btn_config.configure(text=self._config_btn_text())
        self.lbl_status_filter.configure(text=t("so_status_filter"))
        self.cmb_status.configure(values=[t("so_status_completed"),
                                          t("so_status_all")])
        # 把内部 raw 值映射回本地化显示
        self.var_status.set(t("so_status_completed")
                            if self._status_raw() == "completed"
                            else t("so_status_all"))
        self.tree.heading("#0", text=t("so_col_group"))
        self.tree.heading("created", text=t("so_col_created"))
        self.tree.heading("status", text=t("so_col_status"))
        self.tree.heading("score", text=t("so_col_score"))
        self.tree.heading("duration", text=t("so_col_duration"))

    def _config_btn_text(self):
        tag = (self._t("so_cfg_default")
               if same_origin.is_default_core_products(self._core_products)
               else self._t("so_cfg_custom"))
        return f"{self._t('so_config')} ({len(self._core_products)}, {tag})"

    # ------------------------------------------------------------------
    # status combobox：本地化显示 ↔ raw 值
    # ------------------------------------------------------------------
    def _status_raw(self) -> str:
        val = self.var_status.get()
        if val in (self._t("so_status_all"), "(all)", "(全部)"):
            return ""
        return "completed"

    # ------------------------------------------------------------------
    # busy / idle status helpers
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

    # ------------------------------------------------------------------
    # Lazy first-show → 自动扫描一次
    # ------------------------------------------------------------------
    def on_first_show(self):
        if self._first_shown:
            return
        self._first_shown = True
        self._on_scan()

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------
    def _on_scan(self):
        if self._scanning:
            return
        if not self._core_products:
            self._idle(self._t("so_no_core"))
            return
        self._scanning = True
        self._set_controls(False)
        self._busy(self._t("so_scanning"))
        status = self._status_raw() or None
        core = list(self._core_products)

        def _work():
            try:
                res = same_origin.scan_same_origin_groups(
                    core, status=status, progress=self._progress)
                err = None
            except Exception as e:  # noqa: BLE001
                res, err = None, str(e)[:140]
            try:
                self.parent.after(0, lambda: self._on_scanned(res, err))
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True,
                         name="same-origin-scan").start()

    def _progress(self, msg):
        # 后台线程进度 → 状态条（marshal 回 UI 线程）
        try:
            self.parent.after(0, lambda: self._busy(msg))
        except Exception:
            pass

    def _on_scanned(self, res, err):
        self._scanning = False
        self._set_controls(True)
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._group_by_iid.clear()
        self._group_iid_by_mr.clear()

        if err is not None:
            self._idle(self._t("so_scan_failed").format(error=err))
            self.btn_scan.configure(text=self._t("so_scan"))
            return

        groups = res.get("groups", [])
        prefetch_items = []
        for g in groups:
            self._insert_group(g, prefetch_items)

        # 继承 MR Pipeline 的 ✏️ 检测（MR 级）：对每个分组探测 post-edit
        if prefetch_items:
            _tpe.prefetch_async(
                prefetch_items, on_result=self._on_post_edit_result,
                max_workers=8)

        self.btn_scan.configure(
            text=self._t("so_rescan") if groups else self._t("so_scan"))

        if not groups:
            self._idle(self._t("so_scan_empty"))
        elif res.get("truncated"):
            self._idle(self._t("so_scan_done_trunc").format(
                groups=len(groups), tasks=res.get("scanned", 0),
                total=res.get("total", 0)))
        else:
            self._idle(self._t("so_scan_done").format(
                groups=len(groups), tasks=res.get("scanned", 0)))

    def _insert_group(self, g, prefetch_items):
        t = self._t
        project = g["project_id"]
        mr_iid = g["mr_iid"]
        n = g["task_count"]
        group_label = t("so_group_label").format(project=project, mr=mr_iid, n=n)
        created_latest = format_display_datetime(g.get("latest_created") or "")
        release = g.get("release", "")
        release_disp = t("so_group_release").format(release=release) if release else ""
        giid = self.tree.insert(
            "", "end", text=group_label,
            values=(created_latest, release_disp, "", ""),
            tags=("group",), open=True)
        self._group_by_iid[giid] = g
        self._group_iid_by_mr[f"{project}::{mr_iid}"] = giid

        for task in g["tasks"]:
            tid = task.get("task_id") or ""
            created = format_display_datetime(task.get("created_at") or "")
            avg = task.get("average_score")
            self.tree.insert(
                giid, "end",
                text=t("so_task_label").format(short=tid[:8]),
                values=(created, task.get("status", ""),
                        avg if avg is not None else "—",
                        _duration_str(task)))

        # post-edit 探测项：key 与 task_post_edit._fetch_mr 一致 (project, mr_iid)
        if project and mr_iid is not None:
            cache_key = (project, mr_iid)
            cached = _tpe.get_cache().get("mr", cache_key)
            if cached:
                self._apply_post_edit(project, mr_iid)
            elif cached is None:
                prefetch_items.append(("mr", cache_key))

    # ------------------------------------------------------------------
    # post-edit prefetch callback（worker thread → marshal 回 Tk）
    # ------------------------------------------------------------------
    def _on_post_edit_result(self, kind, key, has_post_edit):
        if not has_post_edit:
            return
        if isinstance(key, (tuple, list)) and len(key) == 2:
            project, mr_iid = key
        else:
            return
        try:
            self.tree.after(0, lambda: self._apply_post_edit(project, mr_iid))
        except Exception:
            pass

    def _apply_post_edit(self, project, mr_iid):
        giid = self._group_iid_by_mr.get(f"{project}::{mr_iid}")
        if not giid:
            return
        try:
            label = self.tree.item(giid, "text") or ""
            tags = list(self.tree.item(giid, "tags") or ())
        except tk.TclError:
            return
        if label.startswith(_tpe.POST_EDIT_PREFIX):
            return
        if "post_edit" not in tags:
            tags.append("post_edit")
        try:
            self.tree.item(giid, text=_tpe.POST_EDIT_PREFIX + label,
                           tags=tuple(tags))
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Analyze Diff
    # ------------------------------------------------------------------
    def _selected_group(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        if iid in self._group_by_iid:
            return self._group_by_iid[iid]
        # 选中的是子任务行 → 回溯到父分组
        parent = self.tree.parent(iid)
        return self._group_by_iid.get(parent)

    def _on_analyze(self):
        g = self._selected_group()
        if not g:
            self._idle(self._t("so_need_select"))
            return
        SameOriginDiffDialog(self.parent, self.app, g)

    def _on_double(self, _event=None):
        iid = self.tree.identify_row(_event.y) if _event else None
        if not iid:
            return
        g = self._group_by_iid.get(iid)
        if g is None:
            g = self._group_by_iid.get(self.tree.parent(iid))
        if g is not None:
            SameOriginDiffDialog(self.parent, self.app, g)

    # ------------------------------------------------------------------
    # Configure Core products
    # ------------------------------------------------------------------
    def _on_config(self):
        CoreProductsConfigDialog(self.parent, self.app, self._core_products,
                                 on_saved=self._on_core_saved)

    def _on_core_saved(self, new_list):
        self._core_products = new_list
        self.btn_config.configure(text=self._config_btn_text())
        self._on_scan()

    # ------------------------------------------------------------------
    def _set_controls(self, enabled):
        state = "normal" if enabled else "disabled"
        for btn in (self.btn_scan, self.btn_analyze, self.btn_config):
            try:
                if IS_MAC:
                    btn.state(["!disabled"] if enabled else ["disabled"])
                else:
                    btn.configure(state=state)
            except Exception:
                pass


class SameOriginDiffDialog(tk.Toplevel):
    """同组各任务最新译文的跨任务差异 —— 按语种分类、逐字高亮。"""

    def __init__(self, parent, app, group: dict):
        super().__init__(parent)
        self.app = app
        self.group = group
        t = app._t
        project = group.get("project_id", "")
        mr_iid = group.get("mr_iid", "")
        self.title(t("so_diff_title").format(project=project, mr=mr_iid))
        self.configure(bg="#16213e")
        self.geometry("1040x680")

        self._div_by_iid: dict[str, dict] = {}

        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=14, pady=10)

        # 头部
        ttk.Label(
            outer,
            text=t("so_diff_title").format(project=project, mr=mr_iid),
            style="CardBold.TLabel").pack(anchor="w")
        self.lbl_summary = ttk.Label(outer, text=t("so_diff_computing"),
                                     style="Status.TLabel")
        self.lbl_summary.pack(anchor="w", pady=(2, 8))

        body = ttk.Frame(outer, style="App.TFrame")
        body.pack(fill="both", expand=True)

        # 左：按语种分组的树
        left = ttk.Frame(body, style="App.TFrame")
        left.pack(side="left", fill="both", expand=False)
        left.configure(width=380)
        left.pack_propagate(False)
        self.tree = ttk.Treeview(
            left, columns=("versions",), show="tree headings",
            style="Summary.Treeview", selectmode="browse")
        self.tree.heading("#0", text=t("so_diff_col_key"))
        self.tree.heading("versions", text=t("so_diff_col_versions"))
        self.tree.column("#0", width=300, anchor="w", stretch=True)
        self.tree.column("versions", width=60, anchor="center")
        lsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=lsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_pick)

        # 右：逐字差异详情
        right = ttk.Frame(body, style="App.TFrame")
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.detail = tk.Text(
            right, wrap="word", bg="#0a0a1a", fg="#e7ecff", relief="flat",
            font=(FONT_FAMILY, 10), padx=10, pady=8, state="disabled")
        dsb = ttk.Scrollbar(right, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=dsb.set)
        self.detail.pack(side="left", fill="both", expand=True)
        dsb.pack(side="right", fill="y")
        # diff 上色 tag
        self.detail.tag_configure("del", foreground="#ff8a8a", overstrike=True)
        self.detail.tag_configure("ins", foreground="#7ee787")
        self.detail.tag_configure("hdr", foreground="#9aa0b0",
                                  font=(FONT_FAMILY, 9, "bold"), spacing1=6)
        self.detail.tag_configure("src", foreground="#cbd2e0",
                                  font=(FONT_MONO, 9))
        self.detail.tag_configure("muted", foreground="#9aa0b0")

        # 底部
        footer = ttk.Frame(outer, style="App.TFrame")
        footer.pack(fill="x", pady=(8, 0))
        self.lbl_legend = ttk.Label(footer, text=t("so_diff_legend"),
                                    style="Status.TLabel")
        self.lbl_legend.pack(side="left")
        close_btn = app._create_button(
            footer, text=t("so_close"), command=self.destroy,
            style_name="SecondarySmall", font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=14, pady=4)
        close_btn.pack(side="right")

        self._set_detail(t("so_diff_pick"), muted=True)
        self._start_compute()

    # ------------------------------------------------------------------
    def _start_compute(self):
        tasks = self.group.get("tasks", [])

        def _work():
            try:
                data = same_origin.compute_mr_divergences(tasks)
                err = None
            except Exception as e:  # noqa: BLE001
                data, err = None, str(e)[:140]
            try:
                self.after(0, lambda: self._render(data, err))
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True,
                         name="same-origin-diff").start()

    def _render(self, data, err):
        if not self.winfo_exists():
            return  # 对话框已被关闭，丢弃迟到的回调
        t = self.app._t
        if err is not None:
            self.lbl_summary.configure(
                text=t("so_diff_failed").format(error=err))
            return

        # 成功拉取的任务不足 2 个 → 无法比对
        if data.get("insufficient"):
            msg = t("so_diff_insufficient").format(
                ok=data.get("task_count", 0),
                total=data.get("group_task_count", 0),
                failed=data.get("failed_count", 0))
            self.lbl_summary.configure(text=msg)
            self._set_detail(msg, muted=True)
            return

        # 部分任务拉取失败：仍比对成功的，但要诚实提示已排除
        partial = ""
        if data.get("failed_count"):
            partial = t("so_diff_partial").format(failed=data.get("failed_count"))

        locales = data.get("locales", [])
        by_locale = data.get("by_locale", {})
        if not locales:
            none_msg = t("so_diff_none").format(tasks=data.get("task_count", 0))
            self.lbl_summary.configure(text=partial + none_msg)
            self._set_detail(none_msg, muted=True)
            return

        self.lbl_summary.configure(text=partial + t("so_diff_summary").format(
            locales=len(locales), divergent=data.get("total_divergent", 0),
            keys=data.get("total_keys", 0), tasks=data.get("task_count", 0)))

        for locale in locales:
            items = by_locale.get(locale, [])
            liid = self.tree.insert(
                "", "end",
                text=t("so_diff_locale_count").format(
                    locale=locale, n=len(items)),
                values=(len(items),), open=(len(locales) <= 3))
            for d in items:
                src_preview = (d.get("source_text") or "").replace("\n", " ")
                if len(src_preview) > 60:
                    src_preview = src_preview[:60] + "…"
                key_label = d.get("opus_id", "")
                # opus_id 很长，展示尾段 + 源文预览更可读
                short = key_label.split(".")[-1] if "." in key_label else key_label
                label = f"{short}  ·  {src_preview}" if src_preview else short
                versions = d.get("versions", [])
                present_n = sum(1 for v in versions if v.get("present"))
                ciid = self.tree.insert(
                    liid, "end", text=label, values=(present_n,))
                self._div_by_iid[ciid] = d

    # ------------------------------------------------------------------
    def _on_pick(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        d = self._div_by_iid.get(sel[0])
        if d is None:
            return  # 选中的是语种父节点
        self._render_diff(d)

    def _render_diff(self, d):
        t = self.app._t
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")

        # 源文
        self.detail.insert("end", t("so_diff_src") + "\n", ("hdr",))
        self.detail.insert("end", (d.get("source_text") or "—") + "\n", ("src",))

        opus_id = d.get("opus_id", "")
        self.detail.insert("end", "\nopus_id: " + opus_id + "\n", ("muted",))
        if d.get("changed_kind") == "added_removed":
            self.detail.insert(
                "end", "⚠ " + t("so_diff_added_removed") + "\n", ("muted",))

        versions = d.get("versions", [])
        prev_present_text = None
        for i, v in enumerate(versions, start=1):
            when = format_display_datetime(v.get("created_at") or "")
            self.detail.insert(
                "end", "\n" + t("so_diff_version").format(i=i, when=when) + "\n",
                ("hdr",))
            if not v.get("present"):
                self.detail.insert("end", t("so_diff_missing") + "\n", ("muted",))
                continue
            text = v.get("text") or ""
            if prev_present_text is None:
                # 第一个有内容的版本：作为基线，原样展示
                self.detail.insert("end", text + "\n")
            else:
                # 与上一个有内容的版本做逐字 diff
                for kind, run in same_origin.diff_runs(prev_present_text, text):
                    if not run:
                        continue
                    if kind == "equal":
                        self.detail.insert("end", run)
                    elif kind == "delete":
                        self.detail.insert("end", run, ("del",))
                    else:  # insert
                        self.detail.insert("end", run, ("ins",))
                self.detail.insert("end", "\n")
            prev_present_text = text

        self.detail.configure(state="disabled")

    def _set_detail(self, text, *, muted=False):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", text, ("muted",) if muted else ())
        self.detail.configure(state="disabled")


class CoreProductsConfigDialog(tk.Toplevel):
    """编辑 Core products 清单（每行一个 project_id）。"""

    def __init__(self, parent, app, current: list[str], *, on_saved):
        super().__init__(parent)
        self.app = app
        self._on_saved = on_saved
        t = app._t
        self.title(t("so_cfg_title"))
        self.configure(bg="#16213e")
        self.geometry("540x600")

        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=16, pady=12)

        ttk.Label(outer, text=t("so_cfg_hint"), style="Status.TLabel",
                  wraplength=500, justify="left").pack(fill="x", pady=(0, 8))

        text_frame = ttk.Frame(outer, style="App.TFrame")
        text_frame.pack(fill="both", expand=True)
        self.text = tk.Text(
            text_frame, wrap="none", bg="#0a0a1a", fg="#fff",
            insertbackground="#fff", relief="flat", font=(FONT_MONO, 10),
            padx=8, pady=6)
        sb = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.text.insert("1.0", "\n".join(current))

        self.lbl_status = ttk.Label(outer, text="", style="Status.TLabel")
        self.lbl_status.pack(fill="x", pady=(6, 0))

        btn_row = ttk.Frame(outer, style="App.TFrame")
        btn_row.pack(fill="x", pady=(8, 0))
        app._create_button(
            btn_row, text=t("so_cfg_cancel"), command=self.destroy,
            style_name="SecondarySmall", font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=12, pady=4).pack(side="right")
        app._create_button(
            btn_row, text=t("so_cfg_save"), command=self._save,
            style_name="AccentSmall", font=(FONT_FAMILY, 10, "bold"),
            bg="#e94560", fg="#fff", padx=14, pady=4).pack(side="right", padx=(0, 8))
        app._create_button(
            btn_row, text=t("so_cfg_reset"), command=self._reset,
            style_name="SecondarySmall", font=(FONT_FAMILY, 10),
            bg="#0f3460", fg="#ccc", padx=12, pady=4).pack(side="left")

    def _reset(self):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(same_origin.DEFAULT_CORE_PRODUCTS))

    def _save(self):
        t = self.app._t
        raw = self.text.get("1.0", "end")
        items = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        try:
            saved = same_origin.save_core_products(items)
        except ValueError:
            self.lbl_status.configure(text=t("so_cfg_empty"))
            return
        except Exception as e:  # noqa: BLE001
            self.lbl_status.configure(text=str(e)[:120])
            return
        try:
            self._on_saved(saved)
        except Exception:
            pass
        self.destroy()
