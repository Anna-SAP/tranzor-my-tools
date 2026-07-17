"""Regression tests for export_translations.fetch_all_translations.

The bug: the platform's ``GET /tasks/{id}/translations`` endpoint orders rows
by source-level keys (canonical_source_order, canonical_source_id,
LegacySource.id) — all identical across a key's per-language rows. Its
OFFSET/LIMIT pagination therefore has unstable tie-breaking at page boundaries:
when a boundary lands inside a key's per-language rows, some (key, language)
rows are returned twice and others are skipped. Net count matches ``total`` but
specific (key, language) translations vanish — later showing up as empty cells
after language densification.

The fix: fetch per ``target_language``. Within one language ``opus_id`` is
unique, so pagination is stable and every row is retrieved. Languages are
discovered from a probe of the first page (every key carries all languages, so
the first page reveals them) unioned with the task's configured target_languages
— so the path self-heals even when the task detail lacks target_languages, and
never drops a stray out-of-config locale that has real data.

Run:  python -m unittest test_export_translations_fetch
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_translations as et


class _FakeResp:
    def __init__(self, payload, status_code=200, headers=None):
        self._p = payload
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


def _entry(opus, lang, text="t"):
    return {"opus_id": opus, "target_language": lang,
            "source_text": f"src-{opus}", "translated_text": text,
            "translation_type": "MT"}


class _Backend:
    """Fake /translations endpoint.

    honor_filter=True  -> per-language filter works (real, fixed behaviour).
    drop_on_flat=True  -> the UNFILTERED query reproduces the boundary bug.
    short_page=N       -> the FILTERED query returns at most N rows per page
                          even when more remain (simulates a server cap below
                          the requested limit), while still reporting the true
                          total. Exercises the advance-by-len pagination.
    """

    def __init__(self, data, honor_filter=True, drop_on_flat=False,
                 short_page=None):
        self.data = data
        self.honor_filter = honor_filter
        self.drop_on_flat = drop_on_flat
        self.short_page = short_page
        self.calls = []

    def get(self, url, params=None, **kw):
        params = params or {}
        self.calls.append(dict(params))
        lang = params.get("target_language")
        limit = int(params.get("limit", 200))
        offset = int(params.get("offset", 0))

        if lang is not None and self.honor_filter:
            rows = [e for e in self.data if e["target_language"] == lang]
            total = len(rows)
            eff = min(limit, self.short_page) if self.short_page else limit
            page = rows[offset:offset + eff]
            return _FakeResp({"total": total, "entries": page})

        # Unfiltered (probe, or filter ignored): full set, optionally buggy.
        rows = list(self.data)
        total = len(rows)
        if self.drop_on_flat and limit > 1:
            page = rows[offset:offset + limit]
            if offset > 0 and page:
                page = [rows[offset - 1]] + page[:-1]  # dup prev, drop one
        else:
            page = rows[offset:offset + limit]
        return _FakeResp({"total": total, "entries": page})


class _PatchMixin(unittest.TestCase):
    def setUp(self):
        self._orig_api_get = et._api_get
        self._orig_langs = et.fetch_task_languages
        self._orig_hydrate = et.hydrate_truncated_entries
        et.hydrate_truncated_entries = lambda *a, **k: 0  # no network

    def tearDown(self):
        et._api_get = self._orig_api_get
        et.fetch_task_languages = self._orig_langs
        et.hydrate_truncated_entries = self._orig_hydrate

    def _install(self, backend, languages):
        et._api_get = backend.get
        et.fetch_task_languages = lambda task_id: list(languages)

    @staticmethod
    def _pairs(entries):
        return {(e["opus_id"], e["target_language"]) for e in entries}

    @staticmethod
    def _lang_scoped_calls(backend):
        return [c for c in backend.calls if "target_language" in c]


class TestPerLanguageFetch(_PatchMixin):
    def test_recovers_all_pairs_despite_flat_drop(self):
        # 5 keys x 3 langs = 15 rows; the flat path would drop/dup, but the
        # per-language path must recover all 15.
        langs = ["de-DE", "zh-CN", "fi-FI"]
        data = [_entry(f"K{i}", lg) for i in range(5) for lg in langs]
        backend = _Backend(data, honor_filter=True, drop_on_flat=True)
        self._install(backend, langs)
        out = et.fetch_all_translations("T")
        self.assertEqual(len(out), 15)
        self.assertEqual(len(self._pairs(out)), 15)
        for i in range(5):
            for lg in langs:
                self.assertIn((f"K{i}", lg), self._pairs(out))
        # Actual data fetches were language-scoped (the probe is not).
        self.assertTrue(self._lang_scoped_calls(backend))

    def test_empty_translations_are_filtered(self):
        langs = ["de-DE", "zh-CN"]
        data = [
            _entry("K0", "de-DE", "Hallo"),
            _entry("K0", "zh-CN", ""),      # untranslated -> dropped
            _entry("K1", "de-DE", "Welt"),
            _entry("K1", "zh-CN", "世界"),
        ]
        backend = _Backend(data, honor_filter=True)
        self._install(backend, langs)
        out = et.fetch_all_translations("T")
        self.assertEqual(self._pairs(out),
                         {("K0", "de-DE"), ("K1", "de-DE"), ("K1", "zh-CN")})

    def test_defensive_filter_drops_cross_language_leakage(self):
        # If the server ever ignores target_language, we must not pull other
        # languages into a per-language result (which would dup across calls).
        langs = ["de-DE", "zh-CN"]
        data = [_entry("K0", "de-DE"), _entry("K0", "zh-CN")]
        backend = _Backend(data, honor_filter=False)  # filter ignored
        self._install(backend, langs)
        out = et.fetch_all_translations("T")
        self.assertEqual(self._pairs(out), {("K0", "de-DE"), ("K0", "zh-CN")})
        self.assertEqual(len(out), 2)


class TestPaginationWithinLanguage(_PatchMixin):
    def test_full_pages(self):
        langs = ["de-DE"]
        data = [_entry(f"K{i:04d}", "de-DE") for i in range(450)]
        backend = _Backend(data, honor_filter=True)
        self._install(backend, langs)
        out = et.fetch_all_translations("T")
        self.assertEqual(len(out), 450)
        self.assertEqual(len({e["opus_id"] for e in out}), 450)

    def test_short_pages_recovered(self):
        # Server caps each page at 150 rows even though we ask for 200. The
        # advance-by-len pagination must still recover all 450 rows; an
        # advance-by-requested-limit (or stop-on-short-page) loop would drop
        # rows[150:200], rows[350:400], etc.
        langs = ["de-DE"]
        data = [_entry(f"K{i:04d}", "de-DE") for i in range(450)]
        backend = _Backend(data, honor_filter=True, short_page=150)
        self._install(backend, langs)
        out = et.fetch_all_translations("T")
        self.assertEqual(len(out), 450)
        self.assertEqual(len({e["opus_id"] for e in out}), 450)


class TestLanguageDiscovery(_PatchMixin):
    def test_self_heals_when_task_languages_unavailable(self):
        # task detail gives NO target_languages, but the data still carries
        # them — the probe discovers the languages and the stable per-language
        # path recovers everything (instead of the lossy flat fallback).
        langs = ["de-DE", "zh-CN", "fi-FI"]
        data = [_entry(f"K{i}", lg) for i in range(4) for lg in langs]
        backend = _Backend(data, honor_filter=True, drop_on_flat=True)
        et._api_get = backend.get
        et.fetch_task_languages = lambda task_id: []   # detail empty
        out = et.fetch_all_translations("T")
        self.assertEqual(len(self._pairs(out)), 12)
        # It did NOT silently use the lossy flat path: real language-scoped
        # requests were issued.
        self.assertTrue(self._lang_scoped_calls(backend))

    def test_out_of_config_language_is_not_dropped(self):
        # A locale present in the data but absent from the configured
        # target_languages (e.g. removed after translation) must survive,
        # because languages are discovered from the data too.
        configured = ["de-DE"]
        data = [_entry("K0", "de-DE"), _entry("K0", "zh-CN")]  # zh-CN stray
        backend = _Backend(data, honor_filter=True)
        self._install(backend, configured)
        out = et.fetch_all_translations("T")
        self.assertIn(("K0", "zh-CN"), self._pairs(out))
        self.assertIn(("K0", "de-DE"), self._pairs(out))

    def test_truly_empty_task_returns_nothing(self):
        # A successful empty probe is authoritative; do not issue the old
        # redundant limit=1 flat-fallback request.
        backend = _Backend([], honor_filter=True)
        self._install(backend, [])
        out = et.fetch_all_translations("T")
        self.assertEqual(out, [])
        self.assertFalse(any(c.get("limit") == 1 for c in backend.calls))

    def test_probe_503_is_not_swallowed_into_limit_one_fallback(self):
        calls = []
        error = RuntimeError("503 Server Error: Service Temporarily Unavailable")
        error.response = _FakeResp({}, status_code=503)

        def fail_probe(url, **kwargs):
            calls.append(dict(kwargs.get("params") or {}))
            raise error

        et._api_get = fail_probe
        et.fetch_task_languages = lambda task_id: []
        with self.assertRaisesRegex(RuntimeError, "503 Server Error"):
            et.fetch_all_translations("T")
        self.assertEqual(calls, [{"limit": 200, "offset": 0}])


class TestApiGetResilience(unittest.TestCase):
    def setUp(self):
        self._orig_session = et._session
        self._orig_gate = et._HTTP_GATE
        self._orig_retries = et.MAX_RETRIES

    def tearDown(self):
        et._session = self._orig_session
        et._HTTP_GATE = self._orig_gate
        et.MAX_RETRIES = self._orig_retries

    def test_503_is_retried_at_request_boundary(self):
        responses = [
            _FakeResp({}, status_code=503, headers={"Retry-After": "2"}),
            _FakeResp({}, status_code=503),
            _FakeResp({"ok": True}),
        ]

        class _Session:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                response = responses[self.calls]
                self.calls += 1
                return response

        session = _Session()
        et._session = session
        et._HTTP_GATE = threading.BoundedSemaphore(1)
        et.MAX_RETRIES = 3
        with mock.patch.object(et.random, "uniform", return_value=0.0), \
                mock.patch.object(et.time, "sleep") as slept:
            response = et._api_get("http://example/translations")

        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(session.calls, 3)
        self.assertEqual([c.args[0] for c in slept.call_args_list], [2.0, 2.0])
        self.assertTrue(responses[0].closed)
        self.assertTrue(responses[1].closed)

    def test_non_retryable_401_returns_immediately(self):
        class _Session:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return _FakeResp({}, status_code=401)

        session = _Session()
        et._session = session
        with mock.patch.object(et.time, "sleep") as slept:
            response = et._api_get("http://example/translations")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(session.calls, 1)
        slept.assert_not_called()

    def test_global_gate_caps_nested_request_concurrency(self):
        class _Session:
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

        session = _Session()
        et._session = session
        et._HTTP_GATE = threading.BoundedSemaphore(2)
        et.MAX_RETRIES = 1
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(
                lambda _i: et._api_get("http://example/translations"),
                range(12),
            ))
        self.assertLessEqual(session.max_active, 2)


if __name__ == "__main__":
    unittest.main()
