"""Full Translation 页签连接状态 pill 的 Tk-free 对象手术测试。"""

from __future__ import annotations

import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conn_health as ch
import gui_tab_full_translations as gui


class _RecordingParent:
    """after() 只登记不执行——tick 的自续订用它才不会无限递归。"""

    def __init__(self):
        self.scheduled = []

    def after(self, delay, callback, *args):
        self.scheduled.append((delay, callback, args))


class _Recorder:
    def __init__(self, raise_on_configure=False):
        self.config = {}
        self.raise_on_configure = raise_on_configure

    def configure(self, **kwargs):
        if self.raise_on_configure:
            raise RuntimeError("widget gone")
        self.config.update(kwargs)


class _FakeDialog:
    def __init__(self):
        self.conn_states = []
        self.log_lines = []
        self.errors = []

    def set_conn_state(self, text, color):
        self.conn_states.append((text, color))

    def append_log(self, line):
        self.log_lines.append(line)

    def show_error(self, err):
        self.errors.append(err)


class _FakeMonitor:
    def __init__(self, displays):
        self.displays = list(displays)
        self.paused = []
        self.probe_soon_calls = 0

    def display(self, now_mono=None):
        if len(self.displays) > 1:
            return self.displays.pop(0)
        return self.displays[0]

    def set_paused(self, paused):
        self.paused.append(paused)

    def probe_soon(self):
        self.probe_soon_calls += 1


class _FakeThread:
    def __init__(self, alive):
        self._alive = alive

    def is_alive(self):
        return self._alive


def _display(state, **kwargs):
    defaults = dict(latency_s=0.2, authed=True,
                    taken_wall=time.time())
    defaults.update(kwargs)
    return ch.DisplayStatus(state=state, **defaults)


def _tab(*, busy=False, dlg=None, monitor=None):
    tab = object.__new__(gui.FullTranslationsTab)
    tab.parent = _RecordingParent()
    tab.app = types.SimpleNamespace(lang="en", health_monitor=monitor)
    tab.lbl_conn = _Recorder()
    tab.lbl_status = _Recorder()
    tab._conn_tip = None
    tab._busy = busy
    tab._progress_dlg = dlg
    tab._export_thread = None
    tab._export_started_mono = None
    tab._last_progress_mono = None
    tab._last_wedge_alarm_mono = None
    tab._conn_recent_unstable = False
    tab._export_legacy_monitored = True
    tab._clock_anchor = (time.time(), time.monotonic())
    tab._set_busy_calls = []
    tab._set_busy = tab._set_busy_calls.append
    tab._t = lambda key: gui.STRINGS["en"][key]
    return tab


class ConnRepaintIdleTest(unittest.TestCase):
    def setUp(self):
        ch.BUS.reset()

    def tearDown(self):
        ch.BUS.reset()

    def test_green_display_paints_pill(self):
        mon = _FakeMonitor([_display(ch.STATE_GREEN)])
        tab = _tab(monitor=mon)
        tab._conn_repaint()
        self.assertIn("Platform OK", tab.lbl_conn.config["text"])
        self.assertEqual(tab.lbl_conn.config["fg"], ch.CONN_GREEN)
        # 空闲态 → 探针不暂停。
        self.assertEqual(mon.paused, [False])

    def test_red_display_paints_red(self):
        mon = _FakeMonitor([_display(ch.STATE_RED, http_status=503)])
        tab = _tab(monitor=mon)
        tab._conn_repaint()
        self.assertEqual(tab.lbl_conn.config["fg"], ch.CONN_RED)

    def test_no_monitor_is_a_noop(self):
        tab = _tab(monitor=None)
        tab._conn_repaint()  # must not raise
        self.assertEqual(tab.lbl_conn.config, {})

    def test_result_dialog_after_export_returns_to_idle_view(self):
        # 导出结束后 _progress_dlg 仍指向（结果态的）对话框，但 _busy=False
        # —— pill 必须回到空闲视图且探针恢复。
        mon = _FakeMonitor([_display(ch.STATE_GREEN)])
        tab = _tab(busy=False, dlg=_FakeDialog(), monitor=mon)
        tab._conn_repaint()
        self.assertEqual(mon.paused, [False])
        self.assertIn("Platform OK", tab.lbl_conn.config["text"])


class ConnRepaintExportTest(unittest.TestCase):
    def setUp(self):
        ch.BUS.reset()

    def tearDown(self):
        ch.BUS.reset()

    def _exporting_tab(self, *, silence_s, thread=None):
        mon = _FakeMonitor([_display(ch.STATE_GREEN)])
        dlg = _FakeDialog()
        tab = _tab(busy=True, dlg=dlg, monitor=mon)
        now = time.monotonic()
        tab._export_started_mono = now - silence_s
        tab._last_progress_mono = now - silence_s
        tab._export_thread = thread
        return tab, dlg, mon

    def test_export_pauses_probe_and_mirrors_into_dialog(self):
        tab, dlg, mon = self._exporting_tab(silence_s=5.0)
        tab._conn_repaint()
        self.assertEqual(mon.paused, [True])
        self.assertEqual(len(dlg.conn_states), 1)
        text, color = dlg.conn_states[0]
        self.assertEqual(color, ch.CONN_GREEN)
        self.assertIn("Export traffic OK", text)
        self.assertEqual(dlg.log_lines, [])

    def test_wedge_raises_alarm_once_per_minute(self):
        tab, dlg, _mon = self._exporting_tab(
            silence_s=ch.EXPORT_WEDGE_S + 80.0)
        tab._conn_repaint()
        self.assertEqual(len(dlg.log_lines), 1)
        self.assertIn("min", dlg.log_lines[0])
        self.assertEqual(dlg.conn_states[-1][1], ch.CONN_RED)
        # 60s 内的第二次 repaint 不重复告警。
        tab._conn_repaint()
        self.assertEqual(len(dlg.log_lines), 1)
        # 越过 60s 重复窗口后追加下一条告警。
        tab._last_wedge_alarm_mono = time.monotonic() - 61.0
        tab._conn_repaint()
        self.assertEqual(len(dlg.log_lines), 2)

    def test_dead_thread_is_terminal_and_recovers_tab(self):
        tab, dlg, _mon = self._exporting_tab(
            silence_s=ch.THREAD_DEAD_GRACE_S + 5.0,
            thread=_FakeThread(alive=False))
        tab._conn_repaint()
        self.assertEqual(len(dlg.log_lines), 1)
        self.assertIn("terminated", dlg.log_lines[0])
        # 终态处理：对话框转 error 视图、_busy 解锁、状态栏标红。
        self.assertEqual(len(dlg.errors), 1)
        self.assertEqual(tab._set_busy_calls, [False])
        self.assertIn("terminated", tab.lbl_status.config["text"])

    def test_heartbeat_only_export_uses_progress_wording(self):
        tab, dlg, _mon = self._exporting_tab(
            silence_s=ch.EXPORT_HB_SLOW_S + 30.0)
        tab._export_legacy_monitored = False
        tab._conn_repaint()
        text, color = dlg.conn_states[-1]
        self.assertEqual(color, ch.CONN_AMBER)
        self.assertIn("progress", text)
        self.assertNotIn("response", text.lower())
        # 心跳单通道阈值放宽：同样的静默在 HB 模式下不算 wedge。
        self.assertEqual(dlg.log_lines, [])

    def test_clock_jump_resets_baselines_and_suppresses_false_wedge(self):
        tab, dlg, _mon = self._exporting_tab(
            silence_s=ch.EXPORT_WEDGE_S + 300.0)
        # 伪造睡眠：wall 走了 1000s 而 mono 只走了 400s → 差值 600s。
        tab._clock_anchor = (time.time() - 1000.0, time.monotonic() - 400.0)
        tab._conn_repaint()
        self.assertEqual(dlg.log_lines, [])  # 不许有假 wedge 告警
        # 基线已重置到当前时刻。
        self.assertLess(time.monotonic() - tab._export_started_mono, 5.0)
        self.assertEqual(dlg.conn_states[-1][1], ch.CONN_GREEN)

    def test_live_http_traffic_prevents_wedge(self):
        tab, dlg, _mon = self._exporting_tab(
            silence_s=ch.EXPORT_WEDGE_S + 80.0)
        # 一条刚完成的请求刷新了 last_response → 只可能因进度静默降级，
        # min() 语义下不会误报 wedge。
        token = ch.BUS.request_start("http://x")
        ch.BUS.request_end(token, status=200)
        tab._conn_repaint()
        self.assertEqual(dlg.log_lines, [])

    def test_alarm_resets_after_recovery(self):
        tab, dlg, _mon = self._exporting_tab(
            silence_s=ch.EXPORT_WEDGE_S + 80.0)
        tab._conn_repaint()
        self.assertEqual(len(dlg.log_lines), 1)
        # 恢复（进度心跳刷新）→ 告警计时复位。
        tab._last_progress_mono = time.monotonic()
        token = ch.BUS.request_start("http://x")
        ch.BUS.request_end(token, status=200)
        tab._conn_repaint()
        self.assertIsNone(tab._last_wedge_alarm_mono)


class ConnTickTest(unittest.TestCase):
    def test_tick_reschedules_even_when_repaint_breaks(self):
        mon = _FakeMonitor([_display(ch.STATE_GREEN)])
        tab = _tab(monitor=mon)
        tab.lbl_conn = _Recorder(raise_on_configure=True)
        tab._conn_tick()  # must not raise
        delays = [d for d, _cb, _a in tab.parent.scheduled]
        self.assertEqual(delays, [gui._CONN_TICK_MS])

    def test_tick_reschedules_on_missing_conn_health(self):
        tab = _tab(monitor=None)
        orig = gui._conn_health
        gui._conn_health = None
        try:
            tab._conn_tick()
        finally:
            gui._conn_health = orig
        self.assertEqual(len(tab.parent.scheduled), 1)


class PreflightConnConfirmTest(unittest.TestCase):
    def setUp(self):
        self._orig_messagebox = gui.messagebox
        self.asked = []

    def tearDown(self):
        gui.messagebox = self._orig_messagebox

    def _patch_askyesno(self, answers):
        answers = list(answers)

        def askyesno(title, msg, **kwargs):
            self.asked.append((title, msg, kwargs))
            return answers.pop(0) if answers else True

        gui.messagebox = types.SimpleNamespace(askyesno=askyesno, NO="no")

    def test_green_passes_without_dialog(self):
        self._patch_askyesno([])
        tab = _tab(monitor=_FakeMonitor([_display(ch.STATE_GREEN)]))
        self.assertTrue(tab._preflight_conn_confirm())
        self.assertEqual(self.asked, [])

    def test_green_star_sets_unstable_note_flag(self):
        self._patch_askyesno([])
        tab = _tab(monitor=_FakeMonitor(
            [_display(ch.STATE_GREEN, star=True, bad_recent=2)]))
        self.assertTrue(tab._preflight_conn_confirm())
        self.assertTrue(tab._conn_recent_unstable)

    def test_amber_asks_and_respects_no(self):
        self._patch_askyesno([False])
        tab = _tab(monitor=_FakeMonitor([_display(ch.STATE_AMBER,
                                                  latency_s=3.4)]))
        self.assertFalse(tab._preflight_conn_confirm())
        self.assertEqual(len(self.asked), 1)
        self.assertIn("3.4s", self.asked[0][1])

    def test_red_asks_with_default_no(self):
        self._patch_askyesno([True])
        tab = _tab(monitor=_FakeMonitor(
            [_display(ch.STATE_RED, http_status=503)]))
        self.assertTrue(tab._preflight_conn_confirm())
        self.assertEqual(self.asked[0][2].get("default"), "no")

    def test_gray_asks_with_unknown_wording(self):
        self._patch_askyesno([True])
        tab = _tab(monitor=_FakeMonitor(
            [_display(ch.STATE_GRAY, reason=ch.REASON_INIT)]))
        self.assertTrue(tab._preflight_conn_confirm())
        self.assertIn("unknown", self.asked[0][1])

    def test_recheck_catches_state_flipping_red_under_open_dialog(self):
        # 第一次读到 AMBER（用户点了继续），复检读到 RED → 必须再问一次。
        self._patch_askyesno([True, False])
        tab = _tab(monitor=_FakeMonitor([
            _display(ch.STATE_AMBER, latency_s=2.0),
            _display(ch.STATE_RED, http_status=503),
        ]))
        self.assertFalse(tab._preflight_conn_confirm())
        self.assertEqual(len(self.asked), 2)
        self.assertEqual(self.asked[1][2].get("default"), "no")

    def test_no_monitor_passes(self):
        self._patch_askyesno([])
        tab = _tab(monitor=None)
        self.assertTrue(tab._preflight_conn_confirm())

    def test_early_return_paths_clear_stale_unstable_flag(self):
        # 上一次运行留下的 GREEN* 标记不得泄漏到 monitor 缺失的下一次导出。
        self._patch_askyesno([])
        tab = _tab(monitor=None)
        tab._conn_recent_unstable = True
        self.assertTrue(tab._preflight_conn_confirm())
        self.assertFalse(tab._conn_recent_unstable)


class DialogConnStateTest(unittest.TestCase):
    def test_set_conn_state_configures_label(self):
        dlg = object.__new__(gui._ExportProgressDialog)
        dlg._closed = False
        dlg.lbl_conn_state = _Recorder()
        dlg.set_conn_state("● test", "#e94560")
        self.assertEqual(dlg.lbl_conn_state.config,
                         {"text": "● test", "fg": "#e94560"})

    def test_set_conn_state_noop_after_close(self):
        dlg = object.__new__(gui._ExportProgressDialog)
        dlg._closed = True
        dlg.lbl_conn_state = _Recorder()
        dlg.set_conn_state("x", "#fff")
        self.assertEqual(dlg.lbl_conn_state.config, {})


if __name__ == "__main__":
    unittest.main()
