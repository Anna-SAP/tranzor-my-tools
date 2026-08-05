"""conn_health 纯函数层测试：分类阈值、迟滞、过期、GREEN*、格式化、隔离锁。"""

from __future__ import annotations

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conn_health as ch


def _sample(state=None, *, latency=0.2, error=None, status=200,
            authed=True, mono=1000.0, wall=1_700_000_000.0, triage=None):
    if state is None:
        state = ch.classify_sample(latency, error, status)
    return ch.ProbeSample(
        state=state, latency_s=latency, error=error, http_status=status,
        authed=authed, taken_mono=mono, taken_wall=wall, triage=triage)


class ClassifySampleTest(unittest.TestCase):
    def test_green_boundaries(self):
        self.assertEqual(ch.classify_sample(0.36, None, 200), ch.STATE_GREEN)
        self.assertEqual(
            ch.classify_sample(ch.GREEN_MAX_S, None, 200), ch.STATE_GREEN)

    def test_amber_boundaries(self):
        self.assertEqual(
            ch.classify_sample(ch.GREEN_MAX_S + 0.001, None, 200),
            ch.STATE_AMBER)
        self.assertEqual(
            ch.classify_sample(ch.AMBER_MAX_S, None, 200), ch.STATE_AMBER)

    def test_red_on_high_latency(self):
        self.assertEqual(
            ch.classify_sample(ch.AMBER_MAX_S + 0.5, None, 200), ch.STATE_RED)

    def test_red_on_errors_and_5xx(self):
        self.assertEqual(ch.classify_sample(None, "timeout", None), ch.STATE_RED)
        self.assertEqual(ch.classify_sample(None, "conn", None), ch.STATE_RED)
        self.assertEqual(ch.classify_sample(0.1, None, 503), ch.STATE_RED)
        self.assertEqual(ch.classify_sample(0.1, None, 500), ch.STATE_RED)
        self.assertEqual(ch.classify_sample(0.1, None, 429), ch.STATE_RED)

    def test_401_is_auth_not_red(self):
        self.assertEqual(ch.classify_sample(0.1, None, 401), ch.STATE_AUTH)

    def test_missing_latency_is_red(self):
        self.assertEqual(ch.classify_sample(None, None, None), ch.STATE_RED)


class ResolveDisplayTest(unittest.TestCase):
    def test_no_samples_is_gray_init(self):
        d = ch.resolve_display([], now_mono=0.0)
        self.assertEqual((d.state, d.reason), (ch.STATE_GRAY, ch.REASON_INIT))

    def test_stale_sample_goes_gray(self):
        smp = _sample(mono=1000.0)
        fresh = ch.resolve_display([smp], now_mono=1000.0 + ch.STALE_AFTER_S - 1)
        stale = ch.resolve_display([smp], now_mono=1000.0 + ch.STALE_AFTER_S + 1)
        self.assertEqual(fresh.state, ch.STATE_GREEN)
        self.assertEqual((stale.state, stale.reason),
                         (ch.STATE_GRAY, ch.REASON_STALE))

    def test_auth_sample_goes_gray_auth(self):
        smp = _sample(latency=0.1, status=401, mono=1000.0)
        d = ch.resolve_display([smp], now_mono=1001.0)
        self.assertEqual((d.state, d.reason), (ch.STATE_GRAY, ch.REASON_AUTH))

    def test_single_bad_sample_degrades_immediately(self):
        red = _sample(latency=0.1, status=503, mono=1000.0)
        d = ch.resolve_display([_sample(mono=990.0), red], now_mono=1001.0)
        self.assertEqual(d.state, ch.STATE_RED)

    def test_recovery_needs_two_consecutive_greens(self):
        red = _sample(latency=0.1, status=503, mono=1000.0)
        g1 = _sample(mono=1030.0)
        g2 = _sample(mono=1060.0)
        one_green = ch.resolve_display([red, g1], now_mono=1061.0)
        two_green = ch.resolve_display([red, g1, g2], now_mono=1061.0)
        self.assertEqual(one_green.state, ch.STATE_RED)
        self.assertEqual(two_green.state, ch.STATE_GREEN)

    def test_recent_instability_sets_star(self):
        red = _sample(latency=0.1, status=503, mono=1000.0)
        g1 = _sample(mono=1030.0)
        g2 = _sample(mono=1060.0)
        d = ch.resolve_display([red, g1, g2], now_mono=1061.0)
        self.assertTrue(d.star)
        self.assertEqual(d.bad_recent, 1)

    def test_auth_sample_between_red_and_green_keeps_hysteresis(self):
        # RED → 401(AUTH) → 单个绿样本：AUTH 样本没有定论，不能让它绕过
        # 2 连绿规则（迟滞洞回归锁）。
        red = _sample(latency=0.1, status=503, mono=1000.0)
        auth = _sample(latency=0.1, status=401, mono=1030.0)
        g1 = _sample(mono=1060.0)
        g2 = _sample(mono=1090.0)
        one_green = ch.resolve_display([red, auth, g1], now_mono=1061.0)
        two_green = ch.resolve_display([red, auth, g1, g2], now_mono=1091.0)
        self.assertEqual(one_green.state, ch.STATE_RED)
        self.assertEqual(two_green.state, ch.STATE_GREEN)

    def test_old_instability_outside_window_clears_star(self):
        red = _sample(latency=0.1, status=503, mono=1000.0)
        later = 1000.0 + ch.UNSTABLE_WINDOW_S + 120.0
        g1 = _sample(mono=later - 30.0)
        g2 = _sample(mono=later)
        d = ch.resolve_display([red, g1, g2], now_mono=later + 1)
        self.assertEqual(d.state, ch.STATE_GREEN)
        self.assertFalse(d.star)


class FormatConnStatusTest(unittest.TestCase):
    def test_green_en_zh(self):
        d = ch.resolve_display([_sample(mono=1000.0)], now_mono=1001.0)
        text_en, color_en, tip_en = ch.format_conn_status(d, "en")
        text_zh, color_zh, _ = ch.format_conn_status(d, "zh")
        self.assertEqual(text_en, "● Platform OK (0.2s)")
        self.assertEqual(text_zh, "● 平台正常（0.2s）")
        self.assertEqual(color_en, ch.CONN_GREEN)
        self.assertEqual(color_zh, ch.CONN_GREEN)
        self.assertIn("✔", tip_en)

    def test_green_star_marker(self):
        red = _sample(latency=0.1, status=503, mono=1000.0)
        d = ch.resolve_display(
            [red, _sample(mono=1030.0), _sample(mono=1060.0)],
            now_mono=1061.0)
        text, _, tip = ch.format_conn_status(d, "en")
        self.assertIn("OK*", text)
        self.assertIn("unstable", tip)

    def test_amber(self):
        d = ch.resolve_display([_sample(latency=3.4, mono=1000.0)],
                               now_mono=1001.0)
        text, color, _ = ch.format_conn_status(d, "zh")
        self.assertEqual(text, "● 平台缓慢（3.4s）")
        self.assertEqual(color, ch.CONN_AMBER)

    def test_red_http_vs_unreachable(self):
        http_red = ch.resolve_display(
            [_sample(latency=0.1, status=503, mono=1000.0)], now_mono=1001.0)
        conn_red = ch.resolve_display(
            [_sample(latency=None, error="conn", status=None, mono=1000.0)],
            now_mono=1001.0)
        self.assertEqual(ch.format_conn_status(http_red, "en")[0],
                         "● Platform congested")
        self.assertEqual(ch.format_conn_status(conn_red, "en")[0],
                         "● Platform unreachable")

    def test_gray_variants(self):
        init = ch.resolve_display([], now_mono=0.0)
        auth = ch.resolve_display(
            [_sample(latency=0.1, status=401, mono=1000.0)], now_mono=1001.0)
        stale = ch.resolve_display(
            [_sample(mono=1000.0)],
            now_mono=1000.0 + ch.STALE_AFTER_S + 1)
        self.assertEqual(ch.format_conn_status(init, "en")[0],
                         "● Status unknown")
        self.assertEqual(ch.format_conn_status(auth, "zh")[0], "● 需重新登录")
        self.assertEqual(ch.format_conn_status(stale, "en")[0],
                         "● Status stale")
        for d in (init, auth, stale):
            self.assertEqual(ch.format_conn_status(d, "en")[1], ch.CONN_GRAY)

    def test_livez_probe_noted_in_tooltip(self):
        d = ch.resolve_display([_sample(authed=False, mono=1000.0)],
                               now_mono=1001.0)
        _, _, tip = ch.format_conn_status(d, "en")
        self.assertIn("/livez", tip)

    def test_unauth_probe_annotates_pill_text(self):
        d = ch.resolve_display([_sample(authed=False, mono=1000.0)],
                               now_mono=1001.0)
        text_en, _, _ = ch.format_conn_status(d, "en")
        text_zh, _, _ = ch.format_conn_status(d, "zh")
        self.assertTrue(text_en.endswith("(not signed in)"))
        self.assertTrue(text_zh.endswith("（未登录）"))

    def test_gate_waiters_surface_in_idle_tooltip(self):
        d = ch.resolve_display([_sample(mono=1000.0)], now_mono=1001.0)
        snap = ch.TelemetrySnapshot(
            now_mono=1000.0, last_response_mono=None,
            oldest_inflight_mono=None, inflight_count=0,
            gate_waiting_count=3, oldest_gate_wait_mono=990.0,
            last_gate_wait_s=None, recent_retries=())
        _, _, tip = ch.format_conn_status(d, "en", telemetry=snap)
        self.assertIn("3 request(s) waiting", tip)

    def test_stuck_leftover_request_surfaces_in_tooltip(self):
        d = ch.resolve_display([_sample(mono=1000.0)], now_mono=1001.0)
        snap = ch.TelemetrySnapshot(
            now_mono=1000.0, last_response_mono=None,
            oldest_inflight_mono=1000.0 - ch.EXPORT_WEDGE_S - 60.0,
            inflight_count=1, gate_waiting_count=0,
            oldest_gate_wait_mono=None, last_gate_wait_s=None,
            recent_retries=())
        _, _, tip = ch.format_conn_status(d, "en", telemetry=snap)
        self.assertIn("stuck request", tip)


class ClassifyExportTest(unittest.TestCase):
    def test_ok_below_slow_threshold(self):
        self.assertEqual(
            ch.classify_export(http_silence_s=ch.EXPORT_SLOW_S - 1,
                               progress_silence_s=999.0),
            ch.EXPORT_OK)

    def test_slow_uses_min_of_both_silences(self):
        # HTTP 静默但进度心跳还活着（MR/Scan 阶段）→ 不误报。
        self.assertEqual(
            ch.classify_export(http_silence_s=999.0, progress_silence_s=5.0),
            ch.EXPORT_OK)
        self.assertEqual(
            ch.classify_export(http_silence_s=ch.EXPORT_SLOW_S + 1,
                               progress_silence_s=ch.EXPORT_SLOW_S + 1),
            ch.EXPORT_SLOW)

    def test_wedge_needs_both_channels_silent(self):
        self.assertEqual(
            ch.classify_export(http_silence_s=ch.EXPORT_WEDGE_S + 1,
                               progress_silence_s=ch.EXPORT_WEDGE_S + 1),
            ch.EXPORT_WEDGE)
        self.assertEqual(
            ch.classify_export(http_silence_s=ch.EXPORT_WEDGE_S + 1,
                               progress_silence_s=ch.EXPORT_SLOW_S + 1),
            ch.EXPORT_SLOW)

    def test_stuck_inflight_degrades_to_slow_despite_live_traffic(self):
        # 2026-08-05 起始形态：一个 permit 冻结、响应仍在流动。
        self.assertEqual(
            ch.classify_export(http_silence_s=1.0, progress_silence_s=1.0,
                               oldest_inflight_s=ch.EXPORT_SLOW_S + 5),
            ch.EXPORT_SLOW)
        self.assertEqual(
            ch.classify_export(http_silence_s=1.0, progress_silence_s=1.0,
                               oldest_inflight_s=ch.EXPORT_SLOW_S - 5),
            ch.EXPORT_OK)

    def test_heartbeat_only_uses_relaxed_thresholds(self):
        # 纯 MR/Scan 导出：http_silence 无意义（恒等于运行时长），只看
        # 进度心跳且阈值放宽——大仓库 git 阶段 2 分钟无日志不是事故。
        self.assertEqual(
            ch.classify_export(http_silence_s=9999.0,
                               progress_silence_s=ch.EXPORT_HB_SLOW_S - 1,
                               legacy_monitored=False),
            ch.EXPORT_OK)
        self.assertEqual(
            ch.classify_export(http_silence_s=9999.0,
                               progress_silence_s=ch.EXPORT_HB_SLOW_S + 1,
                               legacy_monitored=False),
            ch.EXPORT_SLOW)
        self.assertEqual(
            ch.classify_export(http_silence_s=9999.0,
                               progress_silence_s=ch.EXPORT_HB_WEDGE_S + 1,
                               legacy_monitored=False),
            ch.EXPORT_WEDGE)

    def test_dead_thread_wins_after_grace(self):
        self.assertEqual(
            ch.classify_export(http_silence_s=0.0,
                               progress_silence_s=ch.THREAD_DEAD_GRACE_S + 1,
                               thread_alive=False),
            ch.EXPORT_THREAD_DEAD)
        # 宽限期内不误报（完成回调可能还在 Tk 队列里）。
        self.assertEqual(
            ch.classify_export(http_silence_s=0.0, progress_silence_s=1.0,
                               thread_alive=False),
            ch.EXPORT_OK)


class FormatExportStatusTest(unittest.TestCase):
    def test_wedge_text_has_minutes(self):
        view = ch.ExportView(
            state=ch.EXPORT_WEDGE, http_silence_s=250.0,
            progress_silence_s=250.0, oldest_inflight_s=240.0,
            inflight_count=2, gate_waiting=3, retry_count=4,
            last_retry_reason="HTTP 503")
        text_en, color, tip = ch.format_export_status(view, "en")
        text_zh, _, _ = ch.format_export_status(view, "zh")
        self.assertEqual(text_en, "● No response for 4 min")
        self.assertEqual(text_zh, "● 已 4 分钟无响应")
        self.assertEqual(color, ch.CONN_RED)
        self.assertIn("240", tip)          # oldest in-flight
        self.assertIn("HTTP 503", tip)     # retry reason

    def test_thread_dead_text(self):
        view = ch.ExportView(
            state=ch.EXPORT_THREAD_DEAD, http_silence_s=0.0,
            progress_silence_s=20.0)
        self.assertEqual(ch.format_export_status(view, "zh")[0],
                         "● 导出线程已终止")

    def test_ok_and_slow(self):
        ok = ch.ExportView(state=ch.EXPORT_OK, http_silence_s=1.0,
                           progress_silence_s=1.0)
        slow = ch.ExportView(state=ch.EXPORT_SLOW, http_silence_s=45.0,
                             progress_silence_s=45.0)
        self.assertEqual(ch.format_export_status(ok, "en")[1], ch.CONN_GREEN)
        text, color, _ = ch.format_export_status(slow, "en")
        self.assertEqual(color, ch.CONN_AMBER)
        self.assertIn("45", text)

    def test_partial_wedge_puts_inflight_evidence_in_main_text(self):
        # 响应仍在流但有请求卡死 → 主文本（对话框状态行可见）必须带证据，
        # 不能只藏在 grab_set 下悬停不到的 tooltip 里。
        view = ch.ExportView(
            state=ch.EXPORT_SLOW, http_silence_s=2.0, progress_silence_s=2.0,
            oldest_inflight_s=95.0)
        text_en, color, _ = ch.format_export_status(view, "en")
        text_zh, _, _ = ch.format_export_status(view, "zh")
        self.assertEqual(text_en, "● Request stuck for 95s")
        self.assertEqual(text_zh, "● 有请求已卡 95s")
        self.assertEqual(color, ch.CONN_AMBER)

    def test_heartbeat_only_wording_avoids_http_claims(self):
        slow = ch.ExportView(
            state=ch.EXPORT_SLOW, http_silence_s=9999.0,
            progress_silence_s=90.0, legacy_monitored=False)
        wedge = ch.ExportView(
            state=ch.EXPORT_WEDGE, http_silence_s=9999.0,
            progress_silence_s=360.0, legacy_monitored=False)
        ok = ch.ExportView(
            state=ch.EXPORT_OK, http_silence_s=9999.0,
            progress_silence_s=5.0, legacy_monitored=False)
        text_slow, _, tip = ch.format_export_status(slow, "en")
        text_wedge, _, _ = ch.format_export_status(wedge, "zh")
        text_ok, _, _ = ch.format_export_status(ok, "en")
        self.assertEqual(text_slow, "● No progress for 90s")
        self.assertEqual(text_wedge, "● 已 6 分钟无进度更新")
        self.assertEqual(text_ok, "● Export running (heartbeat only)")
        self.assertIn("heartbeat monitoring only", tip)
        self.assertNotIn("response", text_slow.lower())


class FormatLatencyTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(ch.format_latency(0.163), "0.2s")
        self.assertEqual(ch.format_latency(None), "?")
        self.assertEqual(ch.format_latency("bogus"), "?")


class IsolationTest(unittest.TestCase):
    """静态回归锁：conn_health 必须独立于导出模块与 Tk。

    探针如果 import export_translations，就会与 _HTTP_GATE / 共享 session
    产生耦合——闸门卡死时探针跟着失明，正是本特性要防的事故形态。
    """

    def test_module_has_no_forbidden_imports(self):
        import ast

        src_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "conn_health.py")
        with io.open(src_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0]
                                for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        for forbidden in ("export_translations", "export_gui",
                          "export_full_translations", "export_mr_pipeline",
                          "gui_tab_full_translations", "tkinter"):
            self.assertNotIn(forbidden, imported)


if __name__ == "__main__":
    unittest.main()
