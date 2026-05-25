"""
Tranzor Bridge — first-time setup wizard.

A self-contained Tk Toplevel dialog that walks the user through the two
external prerequisites Bridge needs:

  1. Install the Tampermonkey browser extension.
  2. Install the Tranzor Bridge userscript (raw .user.js URL).

Each step opens the relevant URL in the user's default browser via the
stdlib ``webbrowser`` module. Step 2 polls
:func:`tranzor_bridge.BridgeServer.status_snapshot` once per second and
auto-advances to "Done" the moment the userscript phones home with a /pull
heartbeat. This means the user never needs to read a doc, paste a URL, or
guess "did it work?" — the wizard tells them, live.

On successful completion, ``~/.tranzor_bridge/setup_complete.json`` is
written so the auto-trigger in :mod:`export_gui` won't pester the user
again on later runs (we keep re-prompting only if the recorded userscript
version falls below the current ``MIN_USERSCRIPT_VERSION``).

Layout philosophy: match the rest of my-tools' Tk dialogs — dark BG with
the existing accent-red CTA color (``ExportApp.ACCENT_BTN``) for primary
actions, secondary-blue for back/skip. No new theme tokens introduced.

Public API
----------
``BridgeSetupWizard(parent, *, bridge, app=None, lang="en")``
    Show the wizard modally. ``bridge`` is the live ``BridgeServer``
    instance; ``app`` is optional and only used to pick up the same accent
    colors / button factory the rest of the GUI uses. ``lang`` is "en" or
    "zh"; if ``app`` is provided we read ``app.lang`` instead.

``should_auto_open_wizard(bridge) -> bool``
    Cheap heuristic the main GUI calls each tick — true iff
    (a) the bridge has a pending undelivered handoff older than
    ``AUTO_TRIGGER_PENDING_SEC`` AND (b) the userscript isn't live.
    A separate "already-prompted-this-session" guard lives in the caller.

``mark_setup_complete(version) / load_setup_state()``
    Setup-state persistence on disk. Used by the wizard internally and by
    callers that want to know "did the user dismiss this once already".
"""
from __future__ import annotations

import json
import os
import sys
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Module imports — kept lightweight so the wizard can be unit-tested
# without dragging in the full Tk GUI stack.
import tranzor_bridge as tb

# External URLs used by the wizard. Both are stable, public endpoints —
# the GitHub raw URL also doubles as the userscript's @updateURL, so a
# successful "Install" click here transparently subscribes the user to
# auto-updates via Tampermonkey.
TAMPERMONKEY_URL = "https://www.tampermonkey.net/"
USERSCRIPT_RAW_URL = (
    "https://raw.githubusercontent.com/Anna-SAP/tranzor-my-tools/"
    "master/userscript/tranzor_bridge.user.js"
)

# How long an unconsumed /handoff can sit before we treat the userscript as
# "almost certainly not running" and auto-pop the wizard. The userscript
# polls every 3s when live, so 15s is a comfortable 5× safety margin.
AUTO_TRIGGER_PENDING_SEC = 15.0

# Where we remember "the user finished setup". Stored next to the bridge
# port-discovery file so they share the same state dir + permissions.
SETUP_STATE_FILENAME = "setup_complete.json"


def _state_dir() -> Path:
    """Mirror :func:`tranzor_bridge._state_dir` without poking at a private."""
    return Path.home() / ".tranzor_bridge"


def _setup_state_path() -> Path:
    return _state_dir() / SETUP_STATE_FILENAME


# ---------------------------------------------------------------------------
# Setup-state persistence
# ---------------------------------------------------------------------------

def load_setup_state() -> Dict[str, Any]:
    """Load the persisted setup-state JSON. Returns ``{}`` on any error so
    callers can treat "missing", "corrupt", and "first run" uniformly."""
    path = _setup_state_path()
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def mark_setup_complete(userscript_version: Optional[str]) -> None:
    """Persist "setup done" with the userscript version we observed. The
    auto-trigger uses the version to decide whether the next run still
    counts as completed (a future MIN_USERSCRIPT_VERSION bump may flip an
    older-but-completed setup back into "needs update")."""
    payload = {
        "completed_at": time.time(),
        "userscript_version": userscript_version or "",
        "min_version_at_completion": tb.MIN_USERSCRIPT_VERSION,
        "bridge_version": tb.BRIDGE_VERSION,
    }
    state_dir = _state_dir()
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    path = _setup_state_path()
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def is_setup_known_complete() -> bool:
    """True iff the persisted state file exists AND the recorded version
    is at or above the current ``MIN_USERSCRIPT_VERSION``. Used by the main
    GUI to decide whether to bother the user on subsequent launches."""
    s = load_setup_state()
    if not s:
        return False
    return not tb._version_lt(
        s.get("userscript_version"),
        tb.MIN_USERSCRIPT_VERSION,
    )


# ---------------------------------------------------------------------------
# Auto-trigger heuristic (called by the main GUI's bridge-watchdog loop)
# ---------------------------------------------------------------------------

def should_auto_open_wizard(bridge: Optional[tb.BridgeServer]) -> bool:
    """Cheap heuristic the main GUI invokes every few seconds.

    Returns True iff the user has just clicked "Send to Tranzor" in an
    HTML report (so an envelope is sitting in the bridge inbox), no
    userscript heartbeat has been seen recently, and the envelope has
    been waiting longer than :data:`AUTO_TRIGGER_PENDING_SEC`. The caller
    is responsible for *only-once-per-session* gating — this function
    deliberately stays stateless.

    Also returns True if the userscript IS live but its reported version
    is below :data:`tranzor_bridge.MIN_USERSCRIPT_VERSION` — that's the
    "outdated" path the wizard handles via a banner on Step 2.
    """
    if bridge is None:
        return False
    snap = bridge.status_snapshot()
    if snap["userscript_outdated"]:
        return True
    pending = snap["pending_handoff_age_sec"]
    if pending is None:
        return False
    if snap["userscript_live"]:
        return False
    return pending >= AUTO_TRIGGER_PENDING_SEC


# ---------------------------------------------------------------------------
# Wizard UI
# ---------------------------------------------------------------------------

# Bilingual strings inlined so the wizard remains importable without the
# main GUI's STRINGS dict. Keys mirror naming conventions used elsewhere
# (snake_case, dialog-specific prefix).
_STRINGS = {
    "en": {
        "title": "Tranzor Bridge — first-time setup",
        "step_label": "Step {n} of 3",
        "step1_heading": "Install Tampermonkey",
        "step1_body": (
            "Tranzor Bridge needs the Tampermonkey browser extension to "
            "talk to the Tranzor Platform tab. Click below — the "
            "Tampermonkey site will detect your browser and show the "
            "right install link."
        ),
        "step1_btn": "🌐  Open Tampermonkey download page",
        "step1_hint": (
            "After you install the extension, come back to this window "
            "and click Next."
        ),
        "step2_heading": "Install the Tranzor Bridge userscript",
        "step2_body": (
            "Click below — Tampermonkey will detect the .user.js file "
            "and prompt you to install it. Just click Install in that "
            "prompt, then return here."
        ),
        "step2_btn": "📥  Open Tranzor Bridge userscript",
        "step2_status_waiting": "🔄  Waiting for the userscript to phone home…",
        "step2_status_outdated": (
            "⚠️  Userscript detected, but version {got} is older than "
            "the required {need}. Please click the button above again "
            "and re-install."
        ),
        "step2_status_live": "✅  Heartbeat received — you're connected!",
        "step2_hint": (
            "The status above updates automatically the moment the "
            "userscript starts polling. If nothing happens within ~10 "
            "seconds, make sure you clicked Install in Tampermonkey's "
            "prompt."
        ),
        "step3_heading": "All set",
        "step3_body": (
            "Tranzor Bridge is ready. Open any HTML report from my-tools "
            "and click the Send to Tranzor button at the top right — the "
            "matching rows will be auto-ticked in the Tranzor Platform "
            "tab so Batch Retranslate works in one click."
        ),
        "step3_btn": "🚀  Done",
        "back": "◀ Back",
        "next": "Next ▶",
        "skip": "Skip for now",
        "close": "Close",
        "bridge_down_title": "Bridge not running",
        "bridge_down_body": (
            "The local bridge couldn't start, so setup can't complete "
            "right now. Send-to-Tranzor will fall back to a clipboard "
            "transport. Please restart my-tools and try again."
        ),
    },
    "zh": {
        "title": "Tranzor Bridge — 首次安装向导",
        "step_label": "第 {n} 步 / 共 3 步",
        "step1_heading": "安装 Tampermonkey 浏览器扩展",
        "step1_body": (
            "Tranzor Bridge 需要 Tampermonkey 浏览器扩展来与 Tranzor "
            "Platform 标签页通信。点击下方按钮 —— Tampermonkey 官网会"
            "自动识别你的浏览器并展示对应的安装入口。"
        ),
        "step1_btn": "🌐  打开 Tampermonkey 安装页",
        "step1_hint": "安装完成后，回到这个窗口点击「下一步」。",
        "step2_heading": "安装 Tranzor Bridge 用户脚本",
        "step2_body": (
            "点击下方按钮 —— Tampermonkey 会自动识别 .user.js 文件并"
            "弹出安装确认窗口。在弹窗里点击「安装」(Install)，然后回到"
            "这里即可。"
        ),
        "step2_btn": "📥  打开 Tranzor Bridge 用户脚本",
        "step2_status_waiting": "🔄  正在等待用户脚本上报心跳…",
        "step2_status_outdated": (
            "⚠️  已检测到用户脚本，但版本 {got} 低于所需的 {need}。"
            "请重新点击上方按钮并完成「安装」以更新到最新版。"
        ),
        "step2_status_live": "✅  心跳已收到 —— 连接成功！",
        "step2_hint": (
            "上面的状态会在用户脚本开始轮询的瞬间自动更新。如果 10 秒"
            "内没有变化，请确认 Tampermonkey 弹窗里点了「安装」按钮。"
        ),
        "step3_heading": "全部就绪",
        "step3_body": (
            "Tranzor Bridge 已就绪。打开任意 my-tools 生成的 HTML 报告，"
            "点击右上角的「Send to Tranzor」按钮 —— 对应行会在 Tranzor "
            "Platform 标签页自动勾选，一键即可发起 Batch Retranslate。"
        ),
        "step3_btn": "🚀  完成",
        "back": "◀ 上一步",
        "next": "下一步 ▶",
        "skip": "暂时跳过",
        "close": "关闭",
        "bridge_down_title": "Bridge 未启动",
        "bridge_down_body": (
            "本地 bridge 服务无法启动，目前无法完成设置。Send-to-Tranzor "
            "会降级到剪贴板模式。请重启 my-tools 后再试。"
        ),
    },
}


class BridgeSetupWizard(tk.Toplevel):
    """Modal three-step wizard. Closes automatically on completion."""

    POLL_MS = 1000  # status_snapshot poll interval on Step 2

    def __init__(
        self,
        parent: tk.Misc,
        *,
        bridge: Optional[tb.BridgeServer],
        app: Optional[Any] = None,
        lang: str = "en",
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.app = app
        self.lang = (getattr(app, "lang", None) or lang or "en")
        if self.lang not in _STRINGS:
            self.lang = "en"
        self._s = _STRINGS[self.lang]

        self.title(self._s["title"])
        self.transient(parent)
        self.resizable(False, False)
        self.geometry("560x420")
        # Color tokens — inherit from ExportApp when available to stay in
        # visual lockstep with the rest of the GUI; otherwise fall back to
        # the same constants in case the wizard is opened standalone.
        self._bg = getattr(app, "BG", "#1a1a2e")
        self._bg_card = getattr(app, "BG_CARD", "#16213e")
        self._fg = getattr(app, "FG", "#e0e0e0")
        self._accent = getattr(app, "ACCENT_BTN", "#e94560")
        self._accent_hover = getattr(app, "ACCENT_BTN_HOVER", "#ff6b81")
        self._success = getattr(app, "SUCCESS", "#2ecc71")
        self.configure(bg=self._bg)

        self._step = 1            # 1, 2, or 3
        self._poll_job: Optional[str] = None
        self._observed_version: Optional[str] = None

        self._build()
        self._render_step()

        self.bind("<Escape>", lambda *_: self._on_close())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.grab_set()

    # ---- factory wrapper that respects ExportApp's _create_button -------
    def _btn(self, parent, **kw) -> tk.Widget:
        factory = getattr(self.app, "_create_button", None)
        if callable(factory):
            return factory(parent, **kw)
        # Standalone fallback (e.g. unit-test harness or dev launcher):
        # ttk.Button doesn't honor bg/fg, so use tk.Button explicitly.
        kw.pop("style_name", None)
        return tk.Button(parent, **kw)

    # ---- structural build (frame skeleton) -------------------------------
    def _build(self) -> None:
        outer = tk.Frame(self, bg=self._bg, padx=20, pady=18)
        outer.pack(fill="both", expand=True)

        self.lbl_step = tk.Label(
            outer, text="", bg=self._bg, fg="#7a7a8a",
            font=("Segoe UI", 9),
        )
        self.lbl_step.pack(anchor="w")

        self.lbl_heading = tk.Label(
            outer, text="", bg=self._bg, fg=self._fg,
            font=("Segoe UI", 16, "bold"),
            wraplength=520, justify="left",
        )
        self.lbl_heading.pack(anchor="w", pady=(2, 8))

        # Separator line — purely cosmetic, ties the wizard visually to the
        # card-style elements elsewhere in the app.
        tk.Frame(outer, bg=self._accent, height=2).pack(
            fill="x", pady=(0, 12)
        )

        self.lbl_body = tk.Label(
            outer, text="", bg=self._bg, fg=self._fg,
            font=("Segoe UI", 11),
            wraplength=520, justify="left",
        )
        self.lbl_body.pack(anchor="w", pady=(0, 16))

        # CTA button — the big primary action for each step. We pack it
        # once and re-configure text/command on step changes to avoid
        # widget-recreation flicker.
        self.btn_cta = self._btn(
            outer, text="", command=lambda: None,
            style_name="Accent",
            font=("Segoe UI", 12, "bold"),
            bg=self._accent, activebackground=self._accent_hover,
            fg="#fff", activeforeground="#fff", padx=16, pady=8,
        )
        self.btn_cta.pack(anchor="w", pady=(0, 12))

        # Status line — only visible on Step 2 where we need to surface
        # heartbeat detection live.
        self.lbl_status = tk.Label(
            outer, text="", bg=self._bg, fg=self._fg,
            font=("Segoe UI", 11),
            wraplength=520, justify="left",
        )
        self.lbl_status.pack(anchor="w", pady=(0, 4))

        self.lbl_hint = tk.Label(
            outer, text="", bg=self._bg, fg="#7a7a8a",
            font=("Segoe UI", 9),
            wraplength=520, justify="left",
        )
        self.lbl_hint.pack(anchor="w", pady=(0, 12))

        # Bottom row: Back / Skip / Next  (variable visibility per step).
        nav = tk.Frame(outer, bg=self._bg)
        nav.pack(fill="x", side="bottom")
        self.btn_back = self._btn(
            nav, text=self._s["back"], command=self._on_back,
            style_name="Secondary",
            font=("Segoe UI", 10),
            bg="#1f2a48", activebackground="#2a3a5e",
            fg="#fff", activeforeground="#fff", padx=10, pady=4,
        )
        self.btn_skip = self._btn(
            nav, text=self._s["skip"], command=self._on_close,
            style_name="Secondary",
            font=("Segoe UI", 10),
            bg="#1f2a48", activebackground="#2a3a5e",
            fg="#fff", activeforeground="#fff", padx=10, pady=4,
        )
        self.btn_next = self._btn(
            nav, text=self._s["next"], command=self._on_next,
            style_name="Accent",
            font=("Segoe UI", 10, "bold"),
            bg=self._accent, activebackground=self._accent_hover,
            fg="#fff", activeforeground="#fff", padx=14, pady=4,
        )
        self.btn_skip.pack(side="left")
        self.btn_next.pack(side="right")
        self.btn_back.pack(side="right", padx=(0, 8))

    # ---- step rendering --------------------------------------------------
    def _render_step(self) -> None:
        self._cancel_poll()
        n = self._step
        self.lbl_step.configure(text=self._s["step_label"].format(n=n))

        if n == 1:
            self.lbl_heading.configure(text=self._s["step1_heading"])
            self.lbl_body.configure(text=self._s["step1_body"])
            self.btn_cta.configure(
                text=self._s["step1_btn"],
                command=lambda: self._open(TAMPERMONKEY_URL),
            )
            self.lbl_status.pack_forget()
            self.lbl_hint.configure(text=self._s["step1_hint"])
            self._show_nav(back=False, next_enabled=True, next_text=self._s["next"])
        elif n == 2:
            self.lbl_heading.configure(text=self._s["step2_heading"])
            self.lbl_body.configure(text=self._s["step2_body"])
            self.btn_cta.configure(
                text=self._s["step2_btn"],
                command=lambda: self._open(USERSCRIPT_RAW_URL),
            )
            self.lbl_status.pack(anchor="w", pady=(0, 4),
                                 before=self.lbl_hint)
            self.lbl_status.configure(
                text=self._s["step2_status_waiting"], fg=self._fg,
            )
            self.lbl_hint.configure(text=self._s["step2_hint"])
            # Next stays disabled until heartbeat arrives.
            self._show_nav(back=True, next_enabled=False, next_text=self._s["next"])
            self._poll_status()
        else:  # n == 3
            self.lbl_heading.configure(text=self._s["step3_heading"])
            self.lbl_body.configure(text=self._s["step3_body"])
            self.btn_cta.configure(
                text=self._s["step3_btn"],
                command=self._on_finish,
            )
            self.lbl_status.pack_forget()
            self.lbl_hint.configure(text="")
            self._show_nav(back=True, next_enabled=False, next_text=self._s["close"])
            # The "Done" CTA is the finish action; we hide the redundant Next.
            self.btn_next.configure(state="disabled")

    def _show_nav(self, *, back: bool, next_enabled: bool, next_text: str) -> None:
        self.btn_next.configure(
            text=next_text,
            state=("normal" if next_enabled else "disabled"),
        )
        if back:
            try:
                self.btn_back.configure(state="normal")
            except Exception:
                pass
        else:
            try:
                self.btn_back.configure(state="disabled")
            except Exception:
                pass

    # ---- step 2 live polling --------------------------------------------
    def _poll_status(self) -> None:
        if self._step != 2 or self.bridge is None:
            return
        try:
            snap = self.bridge.status_snapshot()
        except Exception:
            snap = None
        if snap is not None:
            self._reflect_snapshot(snap)
        # Schedule the next tick. ``after`` returns an id we cache so we
        # can cancel cleanly on step change / dialog close.
        self._poll_job = self.after(self.POLL_MS, self._poll_status)

    def _reflect_snapshot(self, snap: Dict[str, Any]) -> None:
        if snap["userscript_outdated"]:
            self.lbl_status.configure(
                text=self._s["step2_status_outdated"].format(
                    got=snap.get("last_userscript_version") or "?",
                    need=snap["min_userscript_version"],
                ),
                fg=self._accent,
            )
            self._show_nav(back=True, next_enabled=False,
                           next_text=self._s["next"])
            return
        if snap["userscript_live"]:
            self._observed_version = snap.get("last_userscript_version")
            self.lbl_status.configure(
                text=self._s["step2_status_live"], fg=self._success,
            )
            # Heartbeat = success. Light up Next so the user can finish.
            self._show_nav(back=True, next_enabled=True,
                           next_text=self._s["next"])
            return
        self.lbl_status.configure(
            text=self._s["step2_status_waiting"], fg=self._fg,
        )

    def _cancel_poll(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None

    # ---- nav actions -----------------------------------------------------
    def _on_next(self) -> None:
        if self._step < 3:
            self._step += 1
            self._render_step()

    def _on_back(self) -> None:
        if self._step > 1:
            self._step -= 1
            self._render_step()

    def _on_finish(self) -> None:
        mark_setup_complete(self._observed_version)
        self._on_close()

    def _on_close(self) -> None:
        self._cancel_poll()
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    # ---- URL launch ------------------------------------------------------
    @staticmethod
    def _open(url: str) -> None:
        try:
            webbrowser.open(url, new=2)
        except Exception:
            # No graceful fallback — webbrowser failures usually mean the
            # OS has no default browser, which is rare on dev machines.
            # We still leave the wizard usable so the user can hand-copy
            # the URL from the docs if they hit this corner case.
            pass


# ---------------------------------------------------------------------------
# Convenience launcher for the main GUI
# ---------------------------------------------------------------------------

def open_wizard_if_needed(
    parent: tk.Misc,
    *,
    bridge: Optional[tb.BridgeServer],
    app: Optional[Any] = None,
    force: bool = False,
) -> Optional[BridgeSetupWizard]:
    """Open the wizard only when the heuristic agrees the user needs it,
    OR when ``force=True``. Returns the wizard instance if it was opened,
    else None. The caller is expected to maintain its own
    "already-opened-this-session" guard."""
    if bridge is None:
        return None
    if not force and not should_auto_open_wizard(bridge):
        return None
    return BridgeSetupWizard(parent, bridge=bridge, app=app)
