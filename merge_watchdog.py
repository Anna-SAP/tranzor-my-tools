"""Pending-Merge watchdog (PR-D).

Lillian's hard constraint is "intervene BEFORE merge". The Worklist
(PR-A) tells her which MRs are imminent; this module is the safety net
for when she steps away — every five minutes, it refreshes the GitLab
state of every "red" MR in the cache, and pops a notification when one
slips into ``merged`` / ``closed`` without her review.

Threading shape — kept deliberately small:

- :class:`Watchdog` holds a ``threading.Event`` for cancellation and a
  worker thread that loops ``check() → sleep(interval) → check() → …``.
- All I/O lives in the worker; the UI tab only sees:
    a) a ``threading.Lock``-guarded snapshot dict (``last_status``),
       which it can render verbatim in a status banner, and
    b) an ``on_event`` callback fired *from the worker thread* with one
       ``MergeEvent`` per transition. The tab must marshal into Tk via
       ``widget.after(0, ...)`` before touching widgets.

Persistence:

- State transitions get written back to ``task_checks`` so the Worklist
  reflects "merged" within seconds of the watchdog noticing.
- We also log every event into ``sync_meta`` under a small JSON ring so
  PR-E (daily digest) can include "MRs that merged while you were
  reviewing" without having to wire its own storage.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# 默认 5 分钟。比这更勤快会给 GitLab API 多余压力且 Lillian 也消化不了；
# 更慢则错过 merge 窗口的概率上升。从配置（env）覆盖只用于开发/测试。
DEFAULT_INTERVAL_SECS = int(
    os.environ.get("TRANZOR_WATCHDOG_INTERVAL_SECS", "300")
)


# Terminal MR states —— 一旦 MR 走到这些状态，Worklist 就不再追了。
# `closed` 也算 "Lillian 错过了" —— closed without merge 可能是 author
# 放弃，但若 lily 之前正想 review 也值得提醒一下。
_TERMINAL_STATES = frozenset({"merged", "closed", "locked"})


@dataclass(frozen=True)
class MergeEvent:
    """一条状态变迁记录 —— 由 watchdog 发给 UI / 持久化层。"""
    task_id: str
    project_id: str
    mr_iid: int | None
    project_name: str
    task_name: str
    old_state: str | None
    new_state: str
    mr_web_url: str | None
    observed_at: str         # ISO timestamp，UTC

    def is_terminal(self) -> bool:
        return (self.new_state or "").lower() in _TERMINAL_STATES


class Watchdog:
    """5-min poller for red Worklist items.

    Construct, then call :meth:`start`. Pass an ``on_event`` callable to
    receive :class:`MergeEvent` instances; pass ``on_status_change`` to
    receive periodic snapshots (red MR count, last-check time) for the
    status banner.

    Cancel via :meth:`stop` —— the worker exits within at most one
    interval. ``start`` is idempotent; ``stop`` is too.
    """

    def __init__(
        self,
        *,
        interval_secs: int = DEFAULT_INTERVAL_SECS,
        on_event: Optional[Callable[[MergeEvent], None]] = None,
        on_status_change: Optional[Callable[[dict], None]] = None,
    ):
        self.interval_secs = max(30, int(interval_secs))
        self.on_event = on_event
        self.on_status_change = on_status_change
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_status: dict = {
            "running":         False,
            "red_count":       0,
            "last_checked_at": None,
            "last_error":      None,
        }

    # ------------------------------------------------------------------
    @property
    def last_status(self) -> dict:
        with self._lock:
            return dict(self._last_status)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._loop, name="merge-watchdog", daemon=True,
        )
        with self._lock:
            self._last_status["running"] = True
            self._last_status["last_error"] = None
        self._emit_status()
        self._thread.start()

    def stop(self) -> None:
        self._cancel.set()
        with self._lock:
            self._last_status["running"] = False
        self._emit_status()

    # ------------------------------------------------------------------
    # 内部 —— worker thread
    # ------------------------------------------------------------------
    def _loop(self) -> None:
        # 立刻先做一次 check，让用户启动 GUI 就能马上看到"已检查 X 条"——
        # 否则启动后要等 5 分钟才有数字，体验差。
        self._safe_check()
        while not self._cancel.is_set():
            # cancel-aware sleep —— 每秒 wake 一次。给 stop() 一个低延迟
            # 退出路径，否则关闭 GUI 时要等满 interval。
            for _ in range(self.interval_secs):
                if self._cancel.is_set():
                    return
                time.sleep(1)
            self._safe_check()

    def _safe_check(self) -> None:
        try:
            events, red_count = check_once()
        except Exception as e:
            with self._lock:
                self._last_status["last_error"] = str(e)
                self._last_status["last_checked_at"] = (
                    datetime.now(timezone.utc).isoformat(timespec="seconds")
                )
            self._emit_status()
            return

        with self._lock:
            self._last_status["red_count"] = red_count
            self._last_status["last_checked_at"] = (
                datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
            self._last_status["last_error"] = None
        self._emit_status()
        if self.on_event is not None:
            for ev in events:
                try:
                    self.on_event(ev)
                except Exception:
                    # UI 端 bug 不该让 watchdog 崩；丢一个就算了，下次循环
                    # 仍能继续。
                    pass

    def _emit_status(self) -> None:
        if self.on_status_change is None:
            return
        try:
            self.on_status_change(self.last_status)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# check_once —— 暴露成模块级函数让单测能直接打。状态机一目了然。
# ---------------------------------------------------------------------------
def check_once() -> tuple[list[MergeEvent], int]:
    """对当前 cache 中所有 tier="red" 的 MR 做一次 GitLab 状态刷新。

    返回 ``(events, red_count)``：
      - ``events``: 本轮检测到的状态变迁清单（旧→新，且至少有一边发生
        变化）。每条已经写回 ``task_checks``。
      - ``red_count``: 检测前的 red MR 数；UI 拿它显示在 status 行。

    无 GitLab 凭证时只读不改：仍然返回 ``red_count``，但 ``events`` 必
    然为空。这让"没接 GitLab 的同事"也能看到 watchdog 没在乱报警。
    """
    import tranzor_checks as tc
    import gitlab_client as gc

    # 拉所有 mr_state='opened' + tier=red 的 MR，及现存 state（行内对
    # 比"旧→新"用）。get_worklist_items 已经做了 tier 计算 —— 直接复用
    # 比重新算少一份口径漂移风险。
    items = tc.get_worklist_items(
        limit=500,
        include_grey=False, include_fully_reviewed=True,
    )
    red_items = [d for d in items if d.get("merge_tier") == "red"]
    red_count = len(red_items)

    if not red_items:
        return ([], red_count)

    try:
        client = gc.GitLabClient()
    except Exception:
        return ([], red_count)
    if not client.has_token():
        return ([], red_count)

    events: list[MergeEvent] = []
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for d in red_items:
        project_id = d.get("project_id") or ""
        mr_iid = d.get("mr_iid")
        if not project_id or not mr_iid:
            continue
        try:
            mr = client.get_merge_request(project_id, int(mr_iid))
        except Exception:
            # 单条失败不打断整轮 —— 下次 5 分钟后再试。
            continue
        new_state = (mr.get("state") or "").lower() or None
        new_url = mr.get("web_url") or d.get("mr_web_url")
        new_updated = mr.get("updated_at") or d.get("mr_updated_at")
        new_upvotes = mr.get("upvotes") if mr.get("upvotes") is not None else d.get("mr_upvotes")

        # 持久化新 state（COALESCE 已经在 _persist_task_results 处理过
        # 的同 upsert 路径，这里直接 UPDATE 更省心 —— task 已经存在）。
        try:
            tc.update_mr_state_fields(
                task_id=str(d.get("task_id") or ""),
                source_kind=str(d.get("source_kind") or "mr"),
                state=new_state,
                upvotes=new_upvotes,
                updated_at=new_updated,
                web_url=new_url,
            )
        except Exception:
            # 写库失败仍然继续发事件 —— 至少 Lillian 能看到。下次会再
            # 试着写。
            pass

        old_state = (d.get("mr_state") or "").lower() or None
        if old_state != new_state:
            events.append(MergeEvent(
                task_id=str(d.get("task_id") or ""),
                project_id=project_id,
                mr_iid=int(mr_iid),
                project_name=str(d.get("project_name") or ""),
                task_name=str(d.get("task_name") or ""),
                old_state=old_state,
                new_state=new_state or "",
                mr_web_url=new_url,
                observed_at=now_iso,
            ))

    # 持久化事件清单到 sync_meta。PR-E 的 digest 会读它。
    if events:
        try:
            tc.append_merge_events([asdict(e) for e in events])
        except Exception:
            pass
    return (events, red_count)
