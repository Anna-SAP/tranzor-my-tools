"""export_mr_pipeline HTTP 层的 5xx 退避重试 + 并发闸门 + 遥测契约。

2026-08-06 RCA：启动/清单风暴期后端网关（k8s ingress）大量回 502/503，
而 export_mr_pipeline._api_get 对 HTTP 5xx 一律不重试、无并发闸门——
raise_for_status 直接把 MR/Scan 数据链炸掉，轻量清单以"0 products"的
绿色成功伪装失败。本文件钉死向 export_translations 已验证策略对齐后的
新契约：

- 429/500/502/503/504 在单请求边界指数退避重试（jitter + Retry-After），
  次数耗尽后原样返回响应（调用方 raise_for_status 语义不变）；
- ReadTimeout 仍然快速失败（test_api_timeout.py 的既有契约，不动）；
- 所有请求经进程级 _HTTP_GATE 封顶真实并发；
- conn_health.BUS 遥测：请求生命周期 + 重试事件；遥测坏死绝不影响请求。

Run:  python -m unittest test_mr_api_resilience
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

import conn_health as ch
import export_mr_pipeline as mp

_log_tmp = None


def setUpModule():
    # 取证日志绝不写用户真实的 ~/.tranzor_exporter/。
    global _log_tmp
    _log_tmp = tempfile.TemporaryDirectory()
    ch.LOG_PATH = Path(_log_tmp.name) / "conn_health.log"


def tearDownModule():
    _log_tmp.cleanup()


class _FakeResp:
    def __init__(self, payload=None, status_code=200, headers=None):
        self._p = payload if payload is not None else {}
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            exc = RuntimeError(f"HTTP {self.status_code}")
            exc.response = self
            raise exc

    def json(self):
        return self._p

    def close(self):
        self.closed = True


class _ScriptedSession:
    """按脚本吐响应/异常的 Session 替身（get 与 post 共用一份脚本）。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.kwargs_seen = []

    def _next(self, kwargs):
        self.kwargs_seen.append(kwargs)
        item = self.script[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        return self._next(kwargs)

    def post(self, url, **kwargs):
        return self._next(kwargs)


class _ResilienceBase(unittest.TestCase):
    def setUp(self):
        self._orig_session = mp._session
        self._orig_gate = mp._HTTP_GATE
        self._orig_retries = mp.MAX_RETRIES
        ch.BUS.reset()

    def tearDown(self):
        mp._session = self._orig_session
        mp._HTTP_GATE = self._orig_gate
        mp.MAX_RETRIES = self._orig_retries
        ch.BUS.reset()


class ApiGetHttp5xxRetryTests(_ResilienceBase):
    def test_503_is_retried_at_request_boundary(self):
        responses = [
            _FakeResp(status_code=503, headers={"Retry-After": "2"}),
            _FakeResp(status_code=503),
            _FakeResp({"ok": True}),
        ]
        session = _ScriptedSession(responses)
        mp._session = session
        mp._HTTP_GATE = threading.BoundedSemaphore(1)
        mp.MAX_RETRIES = 3
        with mock.patch.object(mp.random, "uniform", return_value=0.0), \
                mock.patch.object(mp.time, "sleep") as slept:
            response = mp._api_get("http://example/dashboard/filters")

        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(session.calls, 3)
        # attempt0: max(2**0, Retry-After=2)=2; attempt1: 2**1=2
        self.assertEqual([c.args[0] for c in slept.call_args_list], [2.0, 2.0])
        self.assertTrue(responses[0].closed)
        self.assertTrue(responses[1].closed)

    def test_502_exhausted_retries_returns_last_response(self):
        # 次数耗尽后原样返回——17 个调用方的 raise_for_status 语义不变。
        responses = [_FakeResp(status_code=502) for _ in range(3)]
        session = _ScriptedSession(responses)
        mp._session = session
        mp.MAX_RETRIES = 3
        with mock.patch.object(mp.random, "uniform", return_value=0.0), \
                mock.patch.object(mp.time, "sleep") as slept:
            response = mp._api_get("http://example/tasks")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(session.calls, 3)
        self.assertEqual(slept.call_count, 2)
        # 最后一个响应必须保持可读（不能 close 后才返回）。
        self.assertFalse(responses[2].closed)

    def test_retry_after_is_capped(self):
        # 服务端一个 "Retry-After: 600" 不该把 worker 线程按住 10 分钟。
        session = _ScriptedSession([
            _FakeResp(status_code=503, headers={"Retry-After": "600"}),
            _FakeResp({"ok": True}),
        ])
        mp._session = session
        mp.MAX_RETRIES = 3
        with mock.patch.object(mp.random, "uniform", return_value=0.0), \
                mock.patch.object(mp.time, "sleep") as slept:
            mp._api_get("http://example/tasks")
        self.assertEqual([c.args[0] for c in slept.call_args_list],
                         [mp._RETRY_AFTER_CAP_S])

    def test_non_retryable_401_returns_immediately(self):
        session = _ScriptedSession([_FakeResp(status_code=401)])
        mp._session = session
        with mock.patch.object(mp.time, "sleep") as slept:
            response = mp._api_get("http://example/tasks")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(session.calls, 1)
        slept.assert_not_called()

    def test_read_timeout_still_fails_fast_with_gate(self):
        # test_api_timeout.py 的既有契约在新的闸门/遥测代码下仍然成立。
        session = _ScriptedSession(
            [requests.exceptions.ReadTimeout("slow")])
        mp._session = session
        with mock.patch.object(mp.time, "sleep") as slept:
            with self.assertRaises(requests.exceptions.ReadTimeout):
                mp._api_get("http://example/dashboard/cases")
        self.assertEqual(session.calls, 1)
        slept.assert_not_called()
        # 遥测登记表不残留脏条目。
        snap = ch.BUS.snapshot()
        self.assertEqual(snap.inflight_count, 0)
        self.assertEqual(snap.gate_waiting_count, 0)

    def test_gate_caps_true_concurrency(self):
        class _SlowSession:
            def __init__(self):
                self.active = 0
                self.max_active = 0
                self.lock = threading.Lock()

            def get(self, *_args, **_kwargs):
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.02)
                with self.lock:
                    self.active -= 1
                return _FakeResp({})

        session = _SlowSession()
        mp._session = session
        mp._HTTP_GATE = threading.BoundedSemaphore(2)
        mp.MAX_RETRIES = 1
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(
                lambda _i: mp._api_get("http://example/tasks"),
                range(12),
            ))
        self.assertLessEqual(session.max_active, 2)


class ApiGetTelemetryTests(_ResilienceBase):
    def test_retry_event_recorded_with_retry_after(self):
        session = _ScriptedSession([
            _FakeResp(status_code=503, headers={"Retry-After": "7"}),
            _FakeResp({"ok": True}),
        ])
        mp._session = session
        mp.MAX_RETRIES = 3
        with mock.patch.object(mp.random, "uniform", return_value=0.0), \
                mock.patch.object(mp.time, "sleep"):
            mp._api_get("http://example/tasks")
        snap = ch.BUS.snapshot()
        self.assertEqual(len(snap.recent_retries), 1)
        _mono, reason, wait, retry_after = snap.recent_retries[0]
        self.assertEqual(reason, "HTTP 503")
        self.assertEqual(wait, 7.0)
        self.assertEqual(retry_after, "7")
        self.assertEqual(snap.inflight_count, 0)
        self.assertIsNotNone(snap.last_response_mono)

    def test_inflight_registry_clean_during_backoff_sleep(self):
        # request_close/request_end 必须发生在退避 sleep 之前——睡眠期间
        # 在途表虚报会污染 wedge 侦测（oldest_inflight）。
        session = _ScriptedSession([
            requests.exceptions.ConnectionError("blip"),
            _FakeResp(status_code=503),
            _FakeResp({"ok": True}),
        ])
        mp._session = session
        mp.MAX_RETRIES = 3
        inflight_during_sleep = []

        def _spy_sleep(_wait):
            inflight_during_sleep.append(ch.BUS.snapshot().inflight_count)

        with mock.patch.object(mp.random, "uniform", return_value=0.0), \
                mock.patch.object(mp.time, "sleep", side_effect=_spy_sleep):
            mp._api_get("http://example/tasks")
        self.assertEqual(inflight_during_sleep, [0, 0])

    def test_broken_bus_never_breaks_requests(self):
        class _Broken:
            def __getattr__(self, _name):
                def _boom(*_a, **_k):
                    raise RuntimeError("telemetry down")
                return _boom

        session = _ScriptedSession([_FakeResp({"ok": True})])
        mp._session = session
        orig_bus = ch.BUS
        ch.BUS = _Broken()
        try:
            response = mp._api_get("http://example/tasks")
        finally:
            ch.BUS = orig_bus
        self.assertEqual(response.json(), {"ok": True})

    def test_conn_health_none_keeps_plain_behavior(self):
        session = _ScriptedSession([_FakeResp({"ok": True})])
        mp._session = session
        with mock.patch.object(mp, "_conn_health", None):
            response = mp._api_get("http://example/tasks")
        self.assertEqual(response.json(), {"ok": True})


class ApiPostRetryTests(_ResilienceBase):
    def test_post_503_retried_then_success(self):
        session = _ScriptedSession([
            _FakeResp(status_code=503),
            _FakeResp({"ok": True}),
        ])
        mp._session = session
        mp.MAX_RETRIES = 3
        with mock.patch.object(mp.random, "uniform", return_value=0.0), \
                mock.patch.object(mp.time, "sleep") as slept:
            response = mp._api_post("http://example/submit")
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(session.calls, 2)
        self.assertEqual(slept.call_count, 1)

    def test_post_502_not_retried(self):
        # POST 非幂等：502 可能发生在服务端已执行之后，不能自动重发。
        session = _ScriptedSession([_FakeResp(status_code=502)])
        mp._session = session
        with mock.patch.object(mp.time, "sleep") as slept:
            response = mp._api_post("http://example/submit")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(session.calls, 1)
        slept.assert_not_called()

    def test_post_connection_error_retries_then_raises(self):
        session = _ScriptedSession([
            requests.exceptions.ConnectionError("down")
            for _ in range(3)
        ])
        mp._session = session
        mp.MAX_RETRIES = 3
        with mock.patch.object(mp.random, "uniform", return_value=0.0), \
                mock.patch.object(mp.time, "sleep") as slept:
            with self.assertRaises(requests.exceptions.ConnectionError):
                mp._api_post("http://example/submit")
        self.assertEqual(session.calls, 3)
        self.assertEqual(slept.call_count, 2)


class HydrationSessionTests(_ResilienceBase):
    def test_facade_routes_through_api_get(self):
        sentinel = _FakeResp({"ok": True})
        with mock.patch.object(mp, "_api_get",
                               return_value=sentinel) as api_get:
            out = mp._api_get_session.get("http://example/full-text",
                                          timeout=15)
        self.assertIs(out, sentinel)
        api_get.assert_called_once_with("http://example/full-text",
                                        timeout=15)

    def test_legacy_quality_hydration_uses_gated_session(self):
        # 全文水合自带 6 并发，必须经 _api_get facade（闸门 + 5xx 重试），
        # 不能再拿裸 _session 裸奔。
        captured = {}

        def _spy_hydrate(items, **kwargs):
            captured.update(kwargs)

        with mock.patch.object(mp, "hydrate_truncated_entries",
                               side_effect=_spy_hydrate), \
                mock.patch.object(mp, "_discover_legacy_languages",
                                  return_value=["de-DE"]), \
                mock.patch.object(mp, "_fetch_legacy_quality_flat",
                                  return_value=[{"opus_id": "x"}]):
            mp.fetch_all_legacy_translations_quality(7)
        self.assertIs(captured.get("session"), mp._api_get_session)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
