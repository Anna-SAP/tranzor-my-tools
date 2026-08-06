"""Full Translations 页签清单失败面的 Tk-free 对象手术测试。

钉死 2026-08-06 修复的 GUI 语义：
- 全部数据源整体失败（典型：502/503 风暴）不得以绿色 "0 products" 收场——
  _inv_loaded 保持 False，红字说明原因，并按 15/30/60s 自动重试；
- 重试次数耗尽后停手，交还给用户（红字提示手动刷新）；
- 部分源失败时清单可用但黄字点名失败源；
- 成功/手动刷新重置自动重试状态并取消已排定的定时器。

Run:  python -m unittest test_gui_inventory_retry
"""
from __future__ import annotations

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_tab_full_translations as gui


class _RecordingParent:
    """after() 只登记不执行，返回可取消的 token。"""

    def __init__(self):
        self.scheduled = []      # (delay_ms, callback, token)
        self.cancelled = []
        self._seq = 0

    def after(self, delay, callback=None, *args):
        self._seq += 1
        token = f"after#{self._seq}"
        self.scheduled.append((delay, callback, token))
        return token

    def after_cancel(self, token):
        self.cancelled.append(token)


class _Recorder:
    def __init__(self):
        self.config = {}

    def configure(self, **kwargs):
        self.config.update(kwargs)


class _FakeTree:
    def __init__(self):
        self.rows = []

    def delete(self, *_iids):
        self.rows = []

    def get_children(self):
        return tuple(iid for iid, _v in self.rows)

    def insert(self, _parent, _index, iid=None, values=(), tags=()):
        self.rows.append((iid, values))


def _inv(products=(), locales=("en-US",), source_errors=None):
    return types.SimpleNamespace(
        products=list(products),
        locales=list(locales),
        source_errors=dict(source_errors or {}),
    )


def _product(pid="mr::proj/a", label="[MR] proj/a"):
    return {"id": pid, "label": label, "source": "mr",
            "project_id": "proj/a", "task_count": None,
            "entry_count": 5, "languages": []}


def _tab():
    tab = object.__new__(gui.FullTranslationsTab)
    tab.parent = _RecordingParent()
    tab.app = types.SimpleNamespace(lang="en")
    tab.lbl_status = _Recorder()
    tab.prod_tree = _FakeTree()
    tab.loc_tree = _FakeTree()
    tab._all_prod_iids = []
    tab._busy = False
    tab._light_inv = None
    tab._inv_loaded = False
    tab._inv_retry_attempt = 0
    tab._inv_retry_after_id = None
    tab._set_busy_calls = []
    tab._set_busy = tab._set_busy_calls.append
    tab._render_products_filter = lambda: None
    tab._t = lambda key: gui.STRINGS["en"][key]
    return tab


class TotalFailureTests(unittest.TestCase):
    def test_all_sources_failed_schedules_retry_not_green_zero(self):
        tab = _tab()
        err = ("503 Server Error: Service Temporarily Unavailable "
               "for url: http://tranzor/api/v1/legacy/tasks")
        tab._on_light_refresh_done(
            _inv(source_errors={"legacy": err, "mr": err, "scan": err}),
            None)
        self.assertFalse(tab._inv_loaded)
        self.assertEqual(tab._inv_retry_attempt, 1)
        text = tab.lbl_status.config["text"]
        self.assertIn("retrying in 15s", text)
        self.assertIn("legacy/mr/scan", text)
        # URL 部分必须被压缩掉，状态栏别拖一条长地址。
        self.assertNotIn("for url", text)
        self.assertEqual(tab.lbl_status.config["foreground"], "#e94560")
        delay, callback, _token = tab.parent.scheduled[-1]
        self.assertEqual(delay, 15000)
        self.assertEqual(callback, tab._auto_retry_refresh)

    def test_retry_delays_escalate_then_give_up(self):
        tab = _tab()
        failed = _inv(source_errors={"legacy": "503 Server Error"})
        tab._on_light_refresh_done(failed, None)
        tab._on_light_refresh_done(failed, None)
        tab._on_light_refresh_done(failed, None)
        delays = [d for d, _cb, _t in tab.parent.scheduled]
        self.assertEqual(delays, [15000, 30000, 60000])
        # 第 4 次失败：不再排定重试，红字提示手动刷新。
        tab._on_light_refresh_done(failed, None)
        self.assertEqual(len(tab.parent.scheduled), 3)
        self.assertIn("Refresh Inventory", tab.lbl_status.config["text"])

    def test_export_blocked_while_inventory_not_loaded(self):
        tab = _tab()
        tab._on_light_refresh_done(
            _inv(source_errors={"legacy": "503"}), None)
        # _do_export 的前置检查依赖 _inv_loaded——失败清单不得放行导出。
        self.assertFalse(tab._inv_loaded)

    def test_total_failure_preserves_previous_good_inventory(self):
        # 面板已有好清单时，风暴中的手动刷新不得把它清成空盘——
        # 全源失败的检查必须先于任何状态/树的破坏性改动。
        tab = _tab()
        good = _inv(products=[_product()], locales=["en-US", "de-DE"])
        tab._on_light_refresh_done(good, None)
        self.assertTrue(tab._inv_loaded)
        rows_before = list(tab.prod_tree.rows)
        tab._on_light_refresh_done(
            _inv(source_errors={"legacy": "503 Server Error"}), None)
        self.assertTrue(tab._inv_loaded)
        self.assertIs(tab._light_inv, good)
        self.assertEqual(tab.prod_tree.rows, rows_before)
        self.assertEqual(tab._inv_retry_attempt, 1)


class PartialFailureTests(unittest.TestCase):
    def test_partial_failure_loads_with_amber_warning(self):
        tab = _tab()
        tab._on_light_refresh_done(
            _inv(products=[_product()],
                 source_errors={"legacy": "503 Server Error: oops"}),
            None)
        self.assertTrue(tab._inv_loaded)
        self.assertEqual(tab._inv_retry_attempt, 0)
        self.assertEqual(tab.parent.scheduled, [])
        text = tab.lbl_status.config["text"]
        self.assertIn("source(s) failed: legacy", text)
        self.assertEqual(tab.lbl_status.config["foreground"], "#fbbf24")


class RecoveryTests(unittest.TestCase):
    def test_success_resets_retry_state_and_cancels_timer(self):
        tab = _tab()
        tab._on_light_refresh_done(
            _inv(source_errors={"legacy": "503"}), None)
        self.assertEqual(tab._inv_retry_attempt, 1)
        pending = tab._inv_retry_after_id
        self.assertIsNotNone(pending)
        tab._on_light_refresh_done(_inv(products=[_product()]), None)
        self.assertTrue(tab._inv_loaded)
        self.assertEqual(tab._inv_retry_attempt, 0)
        self.assertIsNone(tab._inv_retry_after_id)
        self.assertIn(pending, tab.parent.cancelled)
        self.assertIn("Inventory ready", tab.lbl_status.config["text"])
        self.assertEqual(tab.lbl_status.config["foreground"], "#2ecc71")

    def test_manual_refresh_resets_retry_state(self):
        tab = _tab()
        tab._on_light_refresh_done(
            _inv(source_errors={"legacy": "503"}), None)
        pending = tab._inv_retry_after_id
        # 手动刷新在 preflight 处停住（不起线程），但必须已重置重试状态。
        tab._preflight_platform_auth = lambda: False
        tab._on_refresh()
        self.assertEqual(tab._inv_retry_attempt, 0)
        self.assertIn(pending, tab.parent.cancelled)

    def test_auto_retry_skips_but_reschedules_when_busy(self):
        tab = _tab()
        tab._busy = True
        tab._auto_retry_refresh()
        delay, callback, _token = tab.parent.scheduled[-1]
        self.assertEqual(delay, 15000)
        self.assertEqual(callback, tab._auto_retry_refresh)


class AuthDuringRetryTests(unittest.TestCase):
    """token 在重试链中途死掉（服务端撤销等 fail-open 漏网场景）。"""

    def test_auto_401_shows_hint_without_modal_prompt(self):
        # 自动重试路径的 401 绝不弹模态登录框——红字停手。
        tab = _tab()
        tab.app._looks_like_auth_error = lambda e: "401" in str(e)
        prompts = []
        tab._prompt_sign_in = lambda: (prompts.append(1), False)[1]
        tab._on_light_refresh_done(None, "401 Unauthorized", True)
        self.assertEqual(prompts, [])
        self.assertEqual(tab.lbl_status.config["text"],
                         gui.STRINGS["en"]["ft_err_auth"])
        self.assertEqual(tab.lbl_status.config["foreground"], "#e94560")

    def test_manual_401_still_prompts(self):
        tab = _tab()
        tab.app._looks_like_auth_error = lambda e: "401" in str(e)
        prompts = []
        tab._prompt_sign_in = lambda: (prompts.append(1), False)[1]
        tab._on_light_refresh_done(None, "401 Unauthorized", False)
        self.assertEqual(prompts, [1])


class CompactErrorTests(unittest.TestCase):
    def test_url_tail_is_stripped(self):
        s = gui._compact_error(
            "503 Server Error: Service Temporarily Unavailable for url: "
            "http://tranzor-platform.int.rclabenv.com/api/v1/legacy/tasks")
        self.assertEqual(
            s, "503 Server Error: Service Temporarily Unavailable")

    def test_long_error_is_truncated(self):
        s = gui._compact_error("x" * 300)
        self.assertLessEqual(len(s), 90)

    def test_empty_error_is_safe(self):
        self.assertEqual(gui._compact_error(None), "")
        self.assertEqual(gui._compact_error(""), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
