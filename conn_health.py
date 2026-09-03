"""
conn_health — 平台连接健康信号（Full Translation「平台状态」pill 的数据层）
=========================================================================

设计（见 Connection_Health_Indicator_PRD.md）：

* **通道 A（空闲）**：``HealthMonitor`` 后台线程每 30s 探测一次。已登录时探
  ``GET /api/v1/legacy/tasks?limit=1``——与全量导出 ``fetch_tasks()`` 完全相同
  的鉴权 + DB 会话路径，是"导出此刻会不会卡"的真实彩排；未登录降级到免鉴权
  的 ``/livez``。探针零重试、单飞、自有 Session，**绝不经过
  export_translations 的 _HTTP_GATE**——闸门被冻结请求占死时（2026-08-05 的
  事故形态），探针必须仍能报告服务器真相。
* **通道 B（导出中）**：``TelemetryBus`` 收集真实导出流量的被动遥测——最近
  响应时间戳、在途请求登记表、闸门等待观测、重试事件。2026-08-05 那种
  "2 个冻结 socket 静默 18 分钟"的 wedge，在这里表现为 oldest-inflight age
  单调攀升 + 全局响应静默。
* 纯函数 ``classify_sample`` / ``resolve_display`` / ``classify_export`` /
  ``format_conn_status`` / ``format_export_status`` 承载全部阈值与文案，
  零 tkinter、时间可注入，便于测试钉死。

硬性约束：本模块**不得 import export_translations / export_gui / tkinter**
（test_conn_health.py 有静态回归锁）。反向依赖是允许的：export_translations
以 ImportError 守卫方式引用本模块的 ``BUS``。

所有内部计时一律 ``time.monotonic()``；墙钟时间只用于展示与日志行。
"""

from __future__ import annotations

import io
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Tuple

from time_display import format_tz_label

try:
    import requests
except Exception:  # pragma: no cover — headless/CI without requests
    requests = None


# ---------------------------------------------------------------------------
# 阈值（模块常量 + 环境变量覆盖：阈值目前只有 2026-08-05 一天的实测支撑，
# 允许调参不发版）
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


# 空闲探针：正常实测 100-360ms；1s ≈ 最差正常值的 3 倍，10s ≈ 30 倍。
GREEN_MAX_S = _env_float("TRANZOR_HEALTH_GREEN_MAX_S", 1.0)
AMBER_MAX_S = _env_float("TRANZOR_HEALTH_AMBER_MAX_S", 10.0)
# 样本过期（3 个错过的 30s tick）→ 灯退灰，绝不停留在过期的绿色上。
STALE_AFTER_S = _env_float("TRANZOR_HEALTH_STALE_S", 90.0)
# 「正常*」近期不稳回看窗口——bursty 拥塞的另一半教训。
UNSTABLE_WINDOW_S = _env_float("TRANZOR_HEALTH_UNSTABLE_WINDOW_S", 600.0)
# 导出中：健康响应间隔百毫秒级；30s 静默≈百倍异常，120s 判疑似卡死。
EXPORT_SLOW_S = _env_float("TRANZOR_HEALTH_EXPORT_SLOW_S", 30.0)
EXPORT_WEDGE_S = _env_float("TRANZOR_HEALTH_EXPORT_WEDGE_S", 120.0)
# 心跳单通道（纯 MR/Scan 导出，无 Legacy 遥测）阈值放宽：大仓库 git
# clone/scan 阶段 2-3 分钟不产日志行是正常形态，不能按 HTTP 静默标准误报。
EXPORT_HB_SLOW_S = _env_float("TRANZOR_HEALTH_EXPORT_HB_SLOW_S", 60.0)
EXPORT_HB_WEDGE_S = _env_float("TRANZOR_HEALTH_EXPORT_HB_WEDGE_S", 300.0)
# 导出线程已死但完成回调可能还在 Tk 队列里——给 10s 宽限再报「线程终止」。
THREAD_DEAD_GRACE_S = _env_float("TRANZOR_HEALTH_THREAD_GRACE_S", 10.0)

PROBE_INTERVAL_S = _env_float("TRANZOR_HEALTH_PROBE_INTERVAL_S", 30.0)
PROBE_TIMEOUT = (5, 10)  # (connect, read)：read 10s 即红线阈值本身
PROBE_PATH_AUTHED = "/api/v1/legacy/tasks?limit=1"
PROBE_PATH_LIVEZ = "/livez"

# 颜色与 export_gui 的 TOKEN_STATUS_* 色阶一致；在此处独立定义以避免
# 反向 import（GUI 引用这里的常量即可）。
CONN_GREEN = "#4ade80"
CONN_AMBER = "#fbbf24"
CONN_RED = "#e94560"
CONN_GRAY = "#8888a0"

# 空闲态状态
STATE_GREEN = "green"
STATE_AMBER = "amber"
STATE_RED = "red"
STATE_GRAY = "gray"
STATE_AUTH = "auth"      # classify_sample 的中间值，展示层归入 GRAY

# GRAY 细分原因
REASON_INIT = "init"
REASON_STALE = "stale"
REASON_AUTH = "auth"

# 导出中状态
EXPORT_OK = "export_ok"
EXPORT_SLOW = "export_slow"
EXPORT_WEDGE = "export_wedge"
EXPORT_THREAD_DEAD = "export_thread_dead"

_STATE_COLOR = {
    STATE_GREEN: CONN_GREEN,
    STATE_AMBER: CONN_AMBER,
    STATE_RED: CONN_RED,
    STATE_GRAY: CONN_GRAY,
    EXPORT_OK: CONN_GREEN,
    EXPORT_SLOW: CONN_AMBER,
    EXPORT_WEDGE: CONN_RED,
    EXPORT_THREAD_DEAD: CONN_RED,
}


# ---------------------------------------------------------------------------
# i18n — pill / tooltip 文案（确认弹窗等 Tk 侧文案在 gui_tab_full_translations
# 的 STRINGS 里；这里只负责本模块产出的纯文本）
# ---------------------------------------------------------------------------

STRINGS = {
    "en": {
        "pill_ok":          "● Platform OK ({latency})",
        "pill_ok_star":     "● Platform OK* ({latency})",
        "pill_slow":        "● Platform slow ({latency})",
        "pill_red":         "● Platform congested",
        "pill_unreachable": "● Platform unreachable",
        "pill_unknown":     "● Status unknown",
        "pill_stale":       "● Status stale",
        "pill_auth":        "● Re-login needed",
        "pill_export_ok":   "● Export traffic OK",
        "pill_export_slow": "● Slow responses ({s}s silent)",
        "pill_export_wedge": "● No response for {m} min",
        "pill_export_dead": "● Export thread terminated",
        "pill_export_inflight": "● Request stuck for {s}s",
        "pill_hb_ok":       "● Export running (heartbeat only)",
        "pill_hb_slow":     "● No progress for {s}s",
        "pill_hb_wedge":    "● No progress for {m} min",
        "tip_hb_only":      "Legacy channel not in this run — heartbeat monitoring only",
        "pill_suffix_unauth": " (not signed in)",
        "tip_last_probe":   "Last probe {when} · {latency}",
        "tip_ep_authed":    "endpoint: legacy tasks (real export path)",
        "tip_ep_livez":     "endpoint: /livez (not signed in — basic probe)",
        "tip_error":        "last error: {err}",
        "tip_unstable":     "unstable in last 10 min ({n} bad samples)",
        "tip_triage":       "livez check: {info}",
        "tip_stuck":        "⚠ stuck request from an earlier run: {s}s in flight",
        "tip_advice_ok":    "✔ good time for a full export",
        "tip_advice_star":  "⚠ recently unstable — keep an eye on long exports",
        "tip_advice_slow":  "⚠ a full export will be slow",
        "tip_advice_red":   "✖ avoid a full export right now",
        "tip_advice_auth":  "sign in again to restore probing",
        "tip_advice_stale": "click the pill to probe now",
        "tip_inflight":     "oldest in-flight request: {s}s",
        "tip_gate":         "{n} request(s) waiting for the HTTP gate",
        "tip_retries":      "retries this run: {n} (last: {reason})",
        "tip_heartbeat":    "last progress line: {s}s ago",
    },
    "zh": {
        "pill_ok":          "● 平台正常（{latency}）",
        "pill_ok_star":     "● 平台正常*（{latency}）",
        "pill_slow":        "● 平台缓慢（{latency}）",
        "pill_red":         "● 平台拥塞/无响应",
        "pill_unreachable": "● 平台不可达",
        "pill_unknown":     "● 状态未知",
        "pill_stale":       "● 状态过期",
        "pill_auth":        "● 需重新登录",
        "pill_export_ok":   "● 导出通信正常",
        "pill_export_slow": "● 响应迟缓（已 {s}s 无响应）",
        "pill_export_wedge": "● 已 {m} 分钟无响应",
        "pill_export_dead": "● 导出线程已终止",
        "pill_export_inflight": "● 有请求已卡 {s}s",
        "pill_hb_ok":       "● 导出进行中（仅进度心跳）",
        "pill_hb_slow":     "● 已 {s}s 无进度更新",
        "pill_hb_wedge":    "● 已 {m} 分钟无进度更新",
        "tip_hb_only":      "本次未包含 Legacy 通道——仅进度心跳监测",
        "pill_suffix_unauth": "（未登录）",
        "tip_last_probe":   "上次探测 {when} · {latency}",
        "tip_ep_authed":    "探测端点：legacy tasks（导出真实路径）",
        "tip_ep_livez":     "探测端点：/livez（未登录——仅基础探测）",
        "tip_error":        "最近错误：{err}",
        "tip_unstable":     "近 10 分钟内不稳定（{n} 个异常样本）",
        "tip_triage":       "livez 分诊：{info}",
        "tip_stuck":        "⚠ 检测到残留卡死请求：已在途 {s}s",
        "tip_advice_ok":    "✔ 现在适合全量导出",
        "tip_advice_star":  "⚠ 近期不稳定——长导出请留意",
        "tip_advice_slow":  "⚠ 全量导出会明显变慢",
        "tip_advice_red":   "✖ 现在不适合全量导出",
        "tip_advice_auth":  "重新登录后恢复探测",
        "tip_advice_stale": "点击状态灯立即探测",
        "tip_inflight":     "最老在途请求：{s}s",
        "tip_gate":         "{n} 个请求在等待 HTTP 闸门",
        "tip_retries":      "本次运行重试 {n} 次（最近：{reason}）",
        "tip_heartbeat":    "上一条进度日志：{s}s 前",
    },
}


def _s(lang: str) -> dict:
    return STRINGS.get(lang) or STRINGS["en"]


def format_latency(latency_s) -> str:
    if latency_s is None:
        return "?"
    try:
        return f"{float(latency_s):.1f}s"
    except (TypeError, ValueError):
        return "?"


# ---------------------------------------------------------------------------
# 纯函数层 — 空闲态分类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProbeSample:
    """单次探测结果。``state`` 由 :func:`classify_sample` 预先算好。"""
    state: str
    latency_s: Optional[float]
    error: Optional[str]          # None | "timeout" | "conn"
    http_status: Optional[int]
    authed: bool                  # True=legacy tasks 探针；False=/livez 降级
    taken_mono: float
    taken_wall: float
    triage: Optional[dict] = None  # 红态时的一次性 /livez 分诊结果


@dataclass(frozen=True)
class DisplayStatus:
    """迟滞/过期/星号规则应用后的展示态。"""
    state: str                    # green|amber|red|gray
    reason: Optional[str] = None  # gray 细分：init|stale|auth
    star: bool = False            # 近期不稳修饰位（仅 green 时有意义）
    latency_s: Optional[float] = None
    error: Optional[str] = None
    http_status: Optional[int] = None
    authed: bool = True
    taken_wall: Optional[float] = None
    bad_recent: int = 0
    triage: Optional[dict] = None


def classify_sample(latency_s, error=None, http_status=None) -> str:
    """单样本分类。401 归 auth（服务器无辜，不染红）。"""
    if http_status == 401:
        return STATE_AUTH
    if error in ("timeout", "conn"):
        return STATE_RED
    if http_status is not None and (http_status == 429 or http_status >= 500):
        return STATE_RED
    if latency_s is None:
        return STATE_RED
    if latency_s <= GREEN_MAX_S:
        return STATE_GREEN
    if latency_s <= AMBER_MAX_S:
        return STATE_AMBER
    return STATE_RED


def resolve_display(samples, now_mono: Optional[float] = None) -> DisplayStatus:
    """把样本历史折算成展示态。

    规则（PRD §4.1）：
    * 无样本 → GRAY/init；最新样本年龄 > STALE_AFTER_S → GRAY/stale
      （探针线程自身挂死时灯不许停留在过期绿色上的元防御）。
    * 任何态 → RED 单样本立即生效；RED/AMBER → GREEN 需连续 2 个绿样本
      （bursty：单点绿不可信）。
    * 展示为绿时，回看窗口内出现过异常样本 → star=True。
    """
    now = time.monotonic() if now_mono is None else now_mono
    samples = list(samples)
    if not samples:
        return DisplayStatus(state=STATE_GRAY, reason=REASON_INIT)

    latest = samples[-1]
    common = dict(
        latency_s=latest.latency_s, error=latest.error,
        http_status=latest.http_status, authed=latest.authed,
        taken_wall=latest.taken_wall, triage=latest.triage,
    )
    bad_recent = sum(
        1 for smp in samples
        if smp.state in (STATE_RED, STATE_AMBER)
        and (now - smp.taken_mono) <= UNSTABLE_WINDOW_S
    )

    if (now - latest.taken_mono) > STALE_AFTER_S:
        return DisplayStatus(state=STATE_GRAY, reason=REASON_STALE,
                             bad_recent=bad_recent, **common)
    if latest.state == STATE_AUTH:
        return DisplayStatus(state=STATE_GRAY, reason=REASON_AUTH,
                             bad_recent=bad_recent, **common)
    if latest.state in (STATE_RED, STATE_AMBER):
        return DisplayStatus(state=latest.state, bad_recent=bad_recent,
                             **common)

    # latest 是绿样本：需要"上一个有定论的样本"也是绿才升绿。AUTH/GRAY
    # 样本既不证明健康也不证明故障，直接跳过——否则 RED→401→单个绿样本
    # 就能绕过 2 连绿迟滞（bursty 教训的迟滞洞）。
    prev = None
    for smp in reversed(samples[:-1]):
        if smp.state in (STATE_GREEN, STATE_RED, STATE_AMBER):
            prev = smp
            break
    if prev is not None and prev.state in (STATE_RED, STATE_AMBER):
        return DisplayStatus(state=prev.state, bad_recent=bad_recent, **common)
    return DisplayStatus(state=STATE_GREEN, star=bad_recent > 0,
                         bad_recent=bad_recent, **common)


def format_conn_status(display: DisplayStatus, lang: str = "en",
                       telemetry=None) -> Tuple[str, str, str]:
    """空闲态 pill：(text, color, tooltip)。纯函数，无 I/O。"""
    s = _s(lang)
    latency = format_latency(display.latency_s)
    state = display.state

    if state == STATE_GREEN:
        key = "pill_ok_star" if display.star else "pill_ok"
        text = s[key].format(latency=latency)
        advice = s["tip_advice_star"] if display.star else s["tip_advice_ok"]
    elif state == STATE_AMBER:
        text = s["pill_slow"].format(latency=latency)
        advice = s["tip_advice_slow"]
    elif state == STATE_RED:
        key = "pill_unreachable" if display.error == "conn" else "pill_red"
        text = s[key]
        advice = s["tip_advice_red"]
    else:  # GRAY
        if display.reason == REASON_AUTH:
            text, advice = s["pill_auth"], s["tip_advice_auth"]
        elif display.reason == REASON_STALE:
            text, advice = s["pill_stale"], s["tip_advice_stale"]
        else:
            text, advice = s["pill_unknown"], s["tip_advice_stale"]

    # 未登录降级探针只能证明平台可达，说不了 API 路径的事——在 pill 上
    # 如实标注，别让降级绿灯冒充完整体检（PRD §8.1）。
    if not display.authed and state in (STATE_GREEN, STATE_AMBER):
        text += s["pill_suffix_unauth"]

    lines = []
    if display.taken_wall:
        when_dt = datetime.fromtimestamp(display.taken_wall)
        when = f"{when_dt.strftime('%H:%M:%S')} {format_tz_label(at=when_dt)}"
        lines.append(s["tip_last_probe"].format(when=when, latency=latency))
        lines.append(s["tip_ep_authed"] if display.authed
                     else s["tip_ep_livez"])
    if display.error or (display.http_status
                         and display.http_status >= 400):
        err = display.error or f"HTTP {display.http_status}"
        lines.append(s["tip_error"].format(err=err))
    if display.triage:
        livez_err = display.triage.get("livez_error")
        info = (livez_err if livez_err
                else format_latency(display.triage.get("livez_latency_s")))
        lines.append(s["tip_triage"].format(info=info))
    if display.bad_recent:
        lines.append(s["tip_unstable"].format(n=display.bad_recent))
    # 空闲态也扫描在途表：导出结束后残留的僵尸请求（闸门 permit 泄漏）
    # 与其它面板造成的闸门排队在这里如实暴露。
    if telemetry is not None:
        oldest = getattr(telemetry, "oldest_inflight_mono", None)
        now = getattr(telemetry, "now_mono", None)
        if oldest and now and (now - oldest) > EXPORT_WEDGE_S:
            lines.append(s["tip_stuck"].format(s=int(now - oldest)))
        waiting = getattr(telemetry, "gate_waiting_count", 0)
        if waiting:
            lines.append(s["tip_gate"].format(n=waiting))
    lines.append(advice)
    return text, _STATE_COLOR[state], "\n".join(lines)


# ---------------------------------------------------------------------------
# 纯函数层 — 导出中分类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExportView:
    state: str
    http_silence_s: float
    progress_silence_s: float
    oldest_inflight_s: Optional[float] = None
    inflight_count: int = 0
    gate_waiting: int = 0
    retry_count: int = 0
    last_retry_reason: Optional[str] = None
    # False = 本次导出不含 Legacy 源，没有逐请求遥测，只剩进度心跳——
    # 文案与阈值都必须如实反映（PRD §4.3 的"不假装全知"）。
    legacy_monitored: bool = True


def classify_export(*, http_silence_s: float, progress_silence_s: float,
                    thread_alive: bool = True,
                    oldest_inflight_s: Optional[float] = None,
                    legacy_monitored: bool = True) -> str:
    """导出中状态。

    卡死判据取 HTTP 静默与进度心跳静默的 **min**——两路都断气才算疑似
    卡死。进度心跳来自 _dialog_log 回调，天然覆盖不埋遥测的 MR/Scan 阶段
    （它们只要还在产出日志行就不会误报）。

    另有两条独立判据：

    * ``oldest_inflight_s``：最老在途请求超过 EXPORT_SLOW_S 即至少降级为
      黄——这是 2026-08-05 事故的**起始形态**（一个 permit 冻结、另一个
      仍有响应流动），也是其它面板流量刷新全局 last_response 时唯一
      不会被掩蔽的信号。
    * ``legacy_monitored=False``（纯 MR/Scan 导出）：无 HTTP 遥测可言，
      改用心跳单通道 + 放宽阈值（EXPORT_HB_*），避免 git 阶段的正常
      日志间隙被当成平台事故。
    """
    if not thread_alive and progress_silence_s > THREAD_DEAD_GRACE_S:
        return EXPORT_THREAD_DEAD
    if not legacy_monitored:
        if progress_silence_s > EXPORT_HB_WEDGE_S:
            return EXPORT_WEDGE
        if progress_silence_s > EXPORT_HB_SLOW_S:
            return EXPORT_SLOW
        return EXPORT_OK
    silence = min(http_silence_s, progress_silence_s)
    if silence > EXPORT_WEDGE_S:
        return EXPORT_WEDGE
    if silence > EXPORT_SLOW_S:
        return EXPORT_SLOW
    if oldest_inflight_s is not None and oldest_inflight_s > EXPORT_SLOW_S:
        return EXPORT_SLOW
    return EXPORT_OK


def format_export_status(view: ExportView,
                         lang: str = "en") -> Tuple[str, str, str]:
    """导出中 pill / 对话框状态行：(text, color, tooltip)。

    对话框在 grab_set 下是导出期唯一可见的诊断面，所以关键证据（卡住的
    在途请求秒数）直接进入主文本，不能只藏在悬停不到的 tooltip 里。
    """
    s = _s(lang)
    silence = int(min(view.http_silence_s, view.progress_silence_s))
    hb_silence = int(view.progress_silence_s)
    inflight_stuck = (view.legacy_monitored
                      and view.oldest_inflight_s is not None
                      and view.oldest_inflight_s > EXPORT_SLOW_S)
    if view.state == EXPORT_THREAD_DEAD:
        text = s["pill_export_dead"]
    elif not view.legacy_monitored:
        if view.state == EXPORT_WEDGE:
            text = s["pill_hb_wedge"].format(m=max(hb_silence // 60, 1))
        elif view.state == EXPORT_SLOW:
            text = s["pill_hb_slow"].format(s=hb_silence)
        else:
            text = s["pill_hb_ok"]
    elif view.state == EXPORT_WEDGE:
        text = s["pill_export_wedge"].format(m=max(silence // 60, 1))
    elif view.state == EXPORT_SLOW:
        if silence <= EXPORT_SLOW_S and inflight_stuck:
            # 部分卡死：响应仍在流，但有请求已卡死——把证据放上台面。
            text = s["pill_export_inflight"].format(
                s=int(view.oldest_inflight_s))
        else:
            text = s["pill_export_slow"].format(s=silence)
    else:
        text = s["pill_export_ok"]

    lines = []
    if not view.legacy_monitored:
        lines.append(s["tip_hb_only"])
    if view.oldest_inflight_s is not None:
        lines.append(s["tip_inflight"].format(s=int(view.oldest_inflight_s)))
    if view.gate_waiting:
        lines.append(s["tip_gate"].format(n=view.gate_waiting))
    if view.retry_count:
        lines.append(s["tip_retries"].format(
            n=view.retry_count, reason=view.last_retry_reason or "?"))
    lines.append(s["tip_heartbeat"].format(s=int(view.progress_silence_s)))
    return text, _STATE_COLOR[view.state], "\n".join(lines)


# ---------------------------------------------------------------------------
# 文件日志 sink — 打包 exe 是 console=False，print 全部蒸发；关键事件
# （状态迁移 / 重试 / wedge 告警）落盘到 ~/.tranzor_exporter/conn_health.log
# 供事后取证。追加写 + 1MB 轮转，绝不抛异常。
# ---------------------------------------------------------------------------

LOG_PATH = Path.home() / ".tranzor_exporter" / "conn_health.log"
LOG_MAX_BYTES = 1_000_000
_LOG_LOCK = threading.Lock()
# 关掉取证日志的应急开关（例如 CLI 批处理场景不想产生任何本地文件）。
FORENSIC_LOG_ENABLED = (os.getenv("TRANZOR_HEALTH_LOG", "1") or "1") != "0"


def log_event(text: str) -> None:
    if not FORENSIC_LOG_ENABLED:
        return
    try:
        with _LOG_LOCK:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            try:
                if (LOG_PATH.exists()
                        and LOG_PATH.stat().st_size > LOG_MAX_BYTES):
                    LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))
            except Exception:
                pass
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with io.open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{stamp} | {text}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# TelemetryBus — 进程级被动遥测（通道 B）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TelemetrySnapshot:
    now_mono: float
    last_response_mono: Optional[float]
    oldest_inflight_mono: Optional[float]
    inflight_count: int
    gate_waiting_count: int
    oldest_gate_wait_mono: Optional[float]
    last_gate_wait_s: Optional[float]
    recent_retries: tuple  # ((mono, reason, wait_s, retry_after), ...)


class TelemetryBus:
    """线程安全的被动遥测总线。

    锁内只做 dict/deque 内存操作，绝不 I/O、绝不回调。所有方法都设计成
    "调用方再包一层 try/except 也不亏"的幂等语义：遥测永远不能把一个可
    恢复的 HTTP 请求变成崩溃（与 export_translations._log_retry 同一防御
    哲学）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 0
        self._inflight: dict = {}       # token -> (url, start_mono)
        self._gate_waiting: dict = {}   # token -> start_mono
        self._last_response_mono: Optional[float] = None
        self._last_gate_wait_s: Optional[float] = None
        self._retries: deque = deque(maxlen=64)

    # -- 请求生命周期 --------------------------------------------------
    def request_start(self, url: str) -> int:
        with self._lock:
            self._next_id += 1
            token = self._next_id
            self._inflight[token] = (url, time.monotonic())
            return token

    def request_end(self, token: int, status=None) -> None:
        """收到任意 HTTP 响应（含 503）时调用——它证明服务器还活着。"""
        with self._lock:
            self._inflight.pop(token, None)
            if status is not None:
                self._last_response_mono = time.monotonic()

    def request_close(self, token: int) -> None:
        """异常路径的幂等清理：不更新 last_response。"""
        with self._lock:
            self._inflight.pop(token, None)

    # -- 闸门观测 --------------------------------------------------------
    def gate_wait_begin(self) -> int:
        with self._lock:
            self._next_id += 1
            token = self._next_id
            self._gate_waiting[token] = time.monotonic()
            return token

    def gate_wait_acquired(self, token: int) -> None:
        with self._lock:
            started = self._gate_waiting.pop(token, None)
            if started is not None:
                self._last_gate_wait_s = time.monotonic() - started

    def gate_wait_close(self, token: int) -> None:
        with self._lock:
            self._gate_waiting.pop(token, None)

    # -- 重试事件 --------------------------------------------------------
    def record_retry(self, reason: str, wait_s, retry_after=None) -> None:
        with self._lock:
            self._retries.append(
                (time.monotonic(), str(reason), wait_s, retry_after))
        # 锁外落盘：重试正是 console=False 下最需要取证的事件。
        log_event(f"retry: {reason} wait={wait_s}"
                  + (f" retry_after={retry_after}" if retry_after else ""))

    # -- 快照 ------------------------------------------------------------
    def snapshot(self) -> TelemetrySnapshot:
        with self._lock:
            now = time.monotonic()
            oldest_inflight = min(
                (start for _url, start in self._inflight.values()),
                default=None)
            oldest_gate = min(self._gate_waiting.values(), default=None)
            return TelemetrySnapshot(
                now_mono=now,
                last_response_mono=self._last_response_mono,
                oldest_inflight_mono=oldest_inflight,
                inflight_count=len(self._inflight),
                gate_waiting_count=len(self._gate_waiting),
                oldest_gate_wait_mono=oldest_gate,
                last_gate_wait_s=self._last_gate_wait_s,
                recent_retries=tuple(self._retries),
            )

    def rebaseline(self) -> None:
        """睡眠唤醒/时钟跳变后调用：把所有在途/等待起点与最近响应时间戳
        整体提到"现在"，避免把睡了一觉的 socket 判成 wedge（PRD §8.4）。
        条目本身保留——真卡死的请求会从新基线重新开始攀升。"""
        with self._lock:
            now = time.monotonic()
            for token, (url, _start) in list(self._inflight.items()):
                self._inflight[token] = (url, now)
            for token in list(self._gate_waiting):
                self._gate_waiting[token] = now
            if self._last_response_mono is not None:
                self._last_response_mono = now

    def reset(self) -> None:
        """测试隔离用。"""
        with self._lock:
            self._inflight.clear()
            self._gate_waiting.clear()
            self._last_response_mono = None
            self._last_gate_wait_s = None
            self._retries.clear()


#: 进程级单例——export_translations 的埋点与 GUI 的读取共享这一个。
BUS = TelemetryBus()


# ---------------------------------------------------------------------------
# HealthMonitor — 通道 A 主动探针
# ---------------------------------------------------------------------------

class HealthMonitor:
    """后台探针。

    * ``fetch`` / ``token_provider`` 注入式构造，测试可完全替身；默认
      fetch 使用自有 ``requests.Session``（绝不复用 export_translations
      的 session / 闸门）。
    * 单飞由结构保证：探测只发生在唯一的守护线程循环里；``probe_soon()``
      仅提前唤醒它。
    * ``set_paused(True)``（导出进行中）暂停探测——此时通道 B 的被动遥测
      是更真实的数据源，探针不给拥塞的服务器添乱。
    """

    def __init__(self, base_url: str,
                 token_provider: Optional[Callable[[], Optional[str]]] = None,
                 fetch: Optional[Callable] = None,
                 interval: float = PROBE_INTERVAL_S) -> None:
        self._base = (base_url or "").rstrip("/")
        self._token_provider = token_provider
        self._fetch = fetch or self._default_fetch
        self._interval = max(5.0, float(interval))
        self._samples: deque = deque(maxlen=32)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self._session = None
        self._last_logged_state: Optional[str] = None
        # 已知会 401 的 token：换到 /livez 降级探测，直到重新登录换出新
        # token 才恢复鉴权探针——避免过期 token 每 30s 在平台日志刷 401
        # （PRD §11 风险表的既定缓解）。
        self._auth_failed_token: Optional[str] = None

    # -- 生命周期 --------------------------------------------------------
    def start(self) -> "HealthMonitor":
        with self._lock:
            # 先无条件清 _stop：stop() 后旧线程可能仍在 15-30s 的网络等待
            # 里没退出，此时 start() 若只是 no-op，旧线程稍后看到 _stop
            # 退出，监视器就永久死了（stop→start 排水竞态）。
            self._stop.clear()
            if self._thread is not None and self._thread.is_alive():
                self._wake.set()
                return self
            self._thread = threading.Thread(
                target=self._run, name="conn-health-probe", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def probe_soon(self) -> None:
        self._wake.set()

    def set_paused(self, paused: bool) -> None:
        was = self._paused
        self._paused = bool(paused)
        if was and not self._paused:
            # 恢复时立即补一拍，别让 pill 等满一个 interval。
            self._wake.set()

    # -- 主循环 ----------------------------------------------------------
    def _run(self) -> None:  # pragma: no cover — 线程壳，逻辑在 _step
        while not self._stop.is_set():
            if not self._paused:
                try:
                    self._step()
                except Exception:
                    pass
            self._wake.wait(self._interval)
            self._wake.clear()

    def _step(self) -> None:
        """探一拍并入历史。测试直接调它，无需线程。"""
        sample = self._probe_once()
        with self._lock:
            self._samples.append(sample)
        disp = self.display()
        if disp.state != self._last_logged_state:
            log_event(
                f"probe state -> {disp.state}"
                f" (latency={format_latency(sample.latency_s)}"
                f" error={sample.error} http={sample.http_status})")
            self._last_logged_state = disp.state

    def _probe_once(self) -> ProbeSample:
        token = None
        if self._token_provider is not None:
            try:
                token = self._token_provider()
            except Exception:
                token = None
        if token and token == self._auth_failed_token:
            token = None  # 这个 token 已经 401 过——降级探测，别刷平台日志
        if token:
            url = self._base + PROBE_PATH_AUTHED
            headers = {"Authorization": f"Bearer {token}"}
            authed = True
        else:
            url = self._base + PROBE_PATH_LIVEZ
            headers = {}
            authed = False

        status, elapsed, error = self._safe_fetch(url, headers)
        state = classify_sample(elapsed, error, status)
        if authed:
            self._auth_failed_token = token if state == STATE_AUTH else None

        triage = None
        if state == STATE_RED and authed:
            # 唯一的"第二请求"，且只在已经红的时候发生：/livez 差分分诊
            # （事件循环阻塞 vs API/DB 路径拥塞 vs 平台不可达）。
            l_status, l_elapsed, l_error = self._safe_fetch(
                self._base + PROBE_PATH_LIVEZ, {})
            triage = {
                "livez_status": l_status,
                "livez_latency_s": l_elapsed,
                "livez_error": l_error,
            }

        return ProbeSample(
            state=state, latency_s=elapsed, error=error, http_status=status,
            authed=authed, taken_mono=time.monotonic(),
            taken_wall=time.time(), triage=triage)

    def _safe_fetch(self, url, headers):
        try:
            return self._fetch(url, headers, PROBE_TIMEOUT)
        except Exception:
            return None, None, "conn"

    def _default_fetch(self, url, headers, timeout):
        """(status, elapsed_s, error) — 零重试；失败本身就是信号。"""
        if requests is None:
            return None, None, "conn"
        if self._session is None:
            self._session = requests.Session()
        t0 = time.monotonic()
        try:
            resp = self._session.get(url, headers=headers, timeout=timeout)
            elapsed = time.monotonic() - t0
            status = getattr(resp, "status_code", None)
            try:
                resp.close()
            except Exception:
                pass
            return status, elapsed, None
        except requests.exceptions.Timeout:
            return None, time.monotonic() - t0, "timeout"
        except Exception:
            return None, time.monotonic() - t0, "conn"

    # -- 读取 ------------------------------------------------------------
    def display(self, now_mono: Optional[float] = None) -> DisplayStatus:
        with self._lock:
            samples = list(self._samples)
        return resolve_display(samples, now_mono)

    def samples(self):
        with self._lock:
            return list(self._samples)
