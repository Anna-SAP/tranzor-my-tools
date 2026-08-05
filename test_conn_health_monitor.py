"""HealthMonitor（注入 fetch/token_provider）与 TelemetryBus 行为测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conn_health as ch

# 测试绝不允许写用户真实的 ~/.tranzor_exporter/conn_health.log。
_ORIG_LOG_PATH = ch.LOG_PATH


def setUpModule():
    tmp = tempfile.mkdtemp(prefix="conn_health_test_")
    ch.LOG_PATH = Path(tmp) / "conn_health.log"


def tearDownModule():
    ch.LOG_PATH = _ORIG_LOG_PATH


class _FetchRecorder:
    """可编排的假 transport：records urls, plays back scripted results."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers or {}), timeout))
        result = self.results.pop(0) if self.results else (200, 0.1, None)
        if isinstance(result, Exception):
            raise result
        return result


class HealthMonitorTest(unittest.TestCase):
    def test_authed_probe_uses_legacy_tasks_endpoint(self):
        fetch = _FetchRecorder([(200, 0.12, None)])
        mon = ch.HealthMonitor(
            "http://tranzor.example", token_provider=lambda: "jwt-token",
            fetch=fetch)
        mon._step()
        url, headers, timeout = fetch.calls[0]
        self.assertEqual(
            url, "http://tranzor.example" + ch.PROBE_PATH_AUTHED)
        self.assertEqual(headers.get("Authorization"), "Bearer jwt-token")
        self.assertEqual(timeout, ch.PROBE_TIMEOUT)
        self.assertEqual(mon.samples()[-1].state, ch.STATE_GREEN)
        self.assertTrue(mon.samples()[-1].authed)

    def test_no_token_falls_back_to_livez(self):
        fetch = _FetchRecorder([(200, 0.05, None)])
        mon = ch.HealthMonitor(
            "http://tranzor.example", token_provider=lambda: None,
            fetch=fetch)
        mon._step()
        url, headers, _ = fetch.calls[0]
        self.assertEqual(url, "http://tranzor.example" + ch.PROBE_PATH_LIVEZ)
        self.assertNotIn("Authorization", headers)
        self.assertFalse(mon.samples()[-1].authed)

    def test_red_authed_probe_triggers_one_livez_triage(self):
        fetch = _FetchRecorder([(503, 0.2, None), (200, 0.03, None)])
        mon = ch.HealthMonitor(
            "http://tranzor.example", token_provider=lambda: "jwt",
            fetch=fetch)
        mon._step()
        self.assertEqual(len(fetch.calls), 2)
        self.assertTrue(fetch.calls[1][0].endswith(ch.PROBE_PATH_LIVEZ))
        sample = mon.samples()[-1]
        self.assertEqual(sample.state, ch.STATE_RED)
        self.assertEqual(sample.triage["livez_status"], 200)

    def test_green_probe_sends_exactly_one_request(self):
        fetch = _FetchRecorder([(200, 0.1, None)])
        mon = ch.HealthMonitor("http://x", token_provider=lambda: "t",
                               fetch=fetch)
        mon._step()
        self.assertEqual(len(fetch.calls), 1)

    def test_fetch_exception_becomes_conn_error_sample(self):
        fetch = _FetchRecorder([RuntimeError("boom"), RuntimeError("boom2")])
        mon = ch.HealthMonitor("http://x", token_provider=lambda: "t",
                               fetch=fetch)
        mon._step()  # must not raise
        sample = mon.samples()[-1]
        self.assertEqual(sample.state, ch.STATE_RED)
        self.assertEqual(sample.error, "conn")

    def test_token_provider_exception_falls_back_to_livez(self):
        def bad_provider():
            raise RuntimeError("no auth store")
        fetch = _FetchRecorder([(200, 0.05, None)])
        mon = ch.HealthMonitor("http://x", token_provider=bad_provider,
                               fetch=fetch)
        mon._step()
        self.assertTrue(fetch.calls[0][0].endswith(ch.PROBE_PATH_LIVEZ))

    def test_expired_token_downgrades_to_livez_until_token_changes(self):
        # 401 后同一个 token 不再打鉴权端点（平台日志刷屏缓解）；换新
        # token（重新登录）后恢复鉴权探测。
        fetch = _FetchRecorder([
            (401, 0.1, None),   # 探针 1：鉴权端点 → 401
            (200, 0.05, None),  # 探针 2：应降级为 /livez
            (200, 0.08, None),  # 探针 3：新 token → 恢复鉴权端点
        ])
        tokens = ["stale-jwt", "stale-jwt", "fresh-jwt"]
        mon = ch.HealthMonitor(
            "http://x", token_provider=lambda: tokens.pop(0), fetch=fetch)
        mon._step()
        mon._step()
        mon._step()
        urls = [c[0] for c in fetch.calls]
        self.assertTrue(urls[0].endswith(ch.PROBE_PATH_AUTHED))
        self.assertTrue(urls[1].endswith(ch.PROBE_PATH_LIVEZ))
        self.assertTrue(urls[2].endswith(ch.PROBE_PATH_AUTHED))
        self.assertEqual(mon.samples()[0].state, ch.STATE_AUTH)

    def test_start_after_stop_revives_draining_thread(self):
        # stop→start 排水竞态：旧线程还活着时 start() 必须清掉 _stop，
        # 否则旧线程稍后退出、监视器永久死亡。
        mon = ch.HealthMonitor("http://x", fetch=_FetchRecorder([]))

        class _AliveThread:
            @staticmethod
            def is_alive():
                return True

        mon._thread = _AliveThread()
        mon.stop()
        self.assertTrue(mon._stop.is_set())
        mon.start()
        self.assertFalse(mon._stop.is_set())
        self.assertIs(mon._thread.__class__, _AliveThread)

    def test_display_applies_staleness(self):
        fetch = _FetchRecorder([(200, 0.1, None)])
        mon = ch.HealthMonitor("http://x", token_provider=lambda: "t",
                               fetch=fetch)
        mon._step()
        taken = mon.samples()[-1].taken_mono
        fresh = mon.display(now_mono=taken + 1)
        stale = mon.display(now_mono=taken + ch.STALE_AFTER_S + 1)
        self.assertEqual(fresh.state, ch.STATE_GREEN)
        self.assertEqual((stale.state, stale.reason),
                         (ch.STATE_GRAY, ch.REASON_STALE))

    def test_set_paused_unpause_wakes_loop(self):
        mon = ch.HealthMonitor("http://x", fetch=_FetchRecorder([]))
        mon.set_paused(True)
        mon._wake.clear()
        mon.set_paused(False)
        self.assertTrue(mon._wake.is_set())

    def test_start_is_idempotent_and_stop_joins(self):
        fetch = _FetchRecorder([(200, 0.1, None)] * 4)
        mon = ch.HealthMonitor("http://x", token_provider=lambda: "t",
                               fetch=fetch, interval=5.0)
        mon.start()
        first_thread = mon._thread
        mon.start()
        self.assertIs(mon._thread, first_thread)
        mon.stop()
        first_thread.join(timeout=5)
        self.assertFalse(first_thread.is_alive())


class TelemetryBusTest(unittest.TestCase):
    def setUp(self):
        self.bus = ch.TelemetryBus()

    def test_request_lifecycle_updates_last_response(self):
        token = self.bus.request_start("http://x/api")
        snap = self.bus.snapshot()
        self.assertEqual(snap.inflight_count, 1)
        self.assertIsNone(snap.last_response_mono)
        self.bus.request_end(token, status=503)
        snap = self.bus.snapshot()
        self.assertEqual(snap.inflight_count, 0)
        # 任何 HTTP 响应（含 503）都刷新 last_response。
        self.assertIsNotNone(snap.last_response_mono)

    def test_request_close_cleans_up_without_response(self):
        token = self.bus.request_start("http://x/api")
        self.bus.request_close(token)
        snap = self.bus.snapshot()
        self.assertEqual(snap.inflight_count, 0)
        self.assertIsNone(snap.last_response_mono)
        # 幂等：重复关闭无害。
        self.bus.request_close(token)

    def test_oldest_inflight_is_minimum_start(self):
        t1 = self.bus.request_start("http://x/1")
        self.bus.request_start("http://x/2")
        snap = self.bus.snapshot()
        self.assertEqual(snap.inflight_count, 2)
        first_start = self.bus._inflight[t1][1]
        self.assertEqual(snap.oldest_inflight_mono, first_start)

    def test_gate_wait_lifecycle(self):
        token = self.bus.gate_wait_begin()
        snap = self.bus.snapshot()
        self.assertEqual(snap.gate_waiting_count, 1)
        self.assertIsNotNone(snap.oldest_gate_wait_mono)
        self.bus.gate_wait_acquired(token)
        snap = self.bus.snapshot()
        self.assertEqual(snap.gate_waiting_count, 0)
        self.assertIsNotNone(snap.last_gate_wait_s)
        # 异常路径的 close 对已 acquired 的 token 是幂等 no-op。
        self.bus.gate_wait_close(token)

    def test_gate_wait_close_covers_exception_path(self):
        token = self.bus.gate_wait_begin()
        self.bus.gate_wait_close(token)
        self.assertEqual(self.bus.snapshot().gate_waiting_count, 0)

    def test_record_retry_ring(self):
        for i in range(70):
            self.bus.record_retry(f"HTTP 503 #{i}", 2.0, retry_after="120")
        retries = self.bus.snapshot().recent_retries
        self.assertEqual(len(retries), 64)  # deque(maxlen=64)
        self.assertIn("#69", retries[-1][1])
        self.assertEqual(retries[-1][3], "120")

    def test_rebaseline_lifts_ages_without_dropping_entries(self):
        import time as _time
        token = self.bus.request_start("http://x")
        # 人工做旧：把在途起点拨回 10 分钟前（模拟睡眠期间的挂账）。
        with self.bus._lock:
            url, _start = self.bus._inflight[token]
            self.bus._inflight[token] = (url, _time.monotonic() - 600.0)
            self.bus._last_response_mono = _time.monotonic() - 600.0
        self.bus.rebaseline()
        snap = self.bus.snapshot()
        self.assertEqual(snap.inflight_count, 1)  # 条目保留
        self.assertLess(snap.now_mono - snap.oldest_inflight_mono, 5.0)
        self.assertLess(snap.now_mono - snap.last_response_mono, 5.0)

    def test_reset(self):
        self.bus.request_start("http://x")
        self.bus.gate_wait_begin()
        self.bus.record_retry("HTTP 503", 1.0)
        self.bus.reset()
        snap = self.bus.snapshot()
        self.assertEqual(snap.inflight_count, 0)
        self.assertEqual(snap.gate_waiting_count, 0)
        self.assertEqual(snap.recent_retries, ())
        self.assertIsNone(snap.last_response_mono)

    def test_concurrent_smoke(self):
        errors = []

        def worker():
            try:
                for _ in range(200):
                    token = self.bus.request_start("http://x")
                    self.bus.request_end(token, status=200)
                    g = self.bus.gate_wait_begin()
                    self.bus.gate_wait_acquired(g)
                    self.bus.snapshot()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(self.bus.snapshot().inflight_count, 0)


if __name__ == "__main__":
    unittest.main()
