"""export_translations._api_get 的遥测埋点测试（零真实网络）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conn_health
import export_translations as et

import requests

# 测试绝不允许写用户真实的 ~/.tranzor_exporter/conn_health.log。
_ORIG_LOG_PATH = conn_health.LOG_PATH


def setUpModule():
    tmp = tempfile.mkdtemp(prefix="conn_health_test_")
    conn_health.LOG_PATH = Path(tmp) / "conn_health.log"


def tearDownModule():
    conn_health.LOG_PATH = _ORIG_LOG_PATH


class _FakeResp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}

    def close(self):
        pass


class _ScriptedSession:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ApiGetTelemetryTest(unittest.TestCase):
    def setUp(self):
        conn_health.BUS.reset()
        self._orig_session = et._session
        self._orig_sleep = et.time.sleep
        et.time.sleep = lambda _s: None  # 退避不真睡

    def tearDown(self):
        et._session = self._orig_session
        et.time.sleep = self._orig_sleep
        conn_health.BUS.reset()

    def test_success_updates_last_response_and_clears_inflight(self):
        et._session = _ScriptedSession([_FakeResp(200)])
        resp = et._api_get("http://x/api/v1/legacy/tasks")
        self.assertEqual(resp.status_code, 200)
        snap = conn_health.BUS.snapshot()
        self.assertEqual(snap.inflight_count, 0)
        self.assertEqual(snap.gate_waiting_count, 0)
        self.assertIsNotNone(snap.last_response_mono)

    def test_503_with_retry_after_records_retry_events(self):
        et._session = _ScriptedSession([
            _FakeResp(503, headers={"Retry-After": "120"}),
            _FakeResp(200),
        ])
        resp = et._api_get("http://x/api")
        self.assertEqual(resp.status_code, 200)
        snap = conn_health.BUS.snapshot()
        self.assertEqual(len(snap.recent_retries), 1)
        _mono, reason, _wait, retry_after = snap.recent_retries[0]
        self.assertEqual(reason, "HTTP 503")
        self.assertEqual(retry_after, "120")
        # 503 响应也是响应——last_response 必须已被刷新。
        self.assertIsNotNone(snap.last_response_mono)
        self.assertEqual(snap.inflight_count, 0)

    def test_timeout_retries_then_raises_with_clean_registry(self):
        et._session = _ScriptedSession([
            requests.exceptions.Timeout("t1"),
            requests.exceptions.Timeout("t2"),
            requests.exceptions.Timeout("t3"),
        ])
        backoff_snaps = []
        et.time.sleep = lambda _s: backoff_snaps.append(
            conn_health.BUS.snapshot())
        with self.assertRaises(requests.exceptions.Timeout):
            et._api_get("http://x/api")
        snap = conn_health.BUS.snapshot()
        # 在途登记表必须干净——wedge 侦测依赖它的准确性。
        self.assertEqual(snap.inflight_count, 0)
        self.assertEqual(snap.gate_waiting_count, 0)
        self.assertIsNone(snap.last_response_mono)
        self.assertEqual(len(snap.recent_retries), et.MAX_RETRIES - 1)
        self.assertEqual(snap.recent_retries[0][1], "Timeout")
        # 退避 sleep 期间死请求也不得滞留在登记表里虚报 oldest_inflight。
        for backoff_snap in backoff_snaps:
            self.assertEqual(backoff_snap.inflight_count, 0)

    def test_broken_telemetry_never_breaks_the_request(self):
        class _Broken:
            def __getattr__(self, _name):
                def boom(*_a, **_k):
                    raise RuntimeError("telemetry down")
                return boom

        et._session = _ScriptedSession([_FakeResp(200)])
        orig_bus = conn_health.BUS
        conn_health.BUS = _Broken()
        try:
            resp = et._api_get("http://x/api")
            self.assertEqual(resp.status_code, 200)
        finally:
            conn_health.BUS = orig_bus

    def test_conn_health_absent_keeps_cli_behavior(self):
        et._session = _ScriptedSession([_FakeResp(200)])
        orig = et._conn_health
        et._conn_health = None
        try:
            resp = et._api_get("http://x/api")
            self.assertEqual(resp.status_code, 200)
        finally:
            et._conn_health = orig


if __name__ == "__main__":
    unittest.main()
