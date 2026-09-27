"""Tests for data_lookback: category table, UTC+8 day window, windowed paging,
per-source fetch isolation, GitLab branch resolution and the report."""
from __future__ import annotations

import os
import random
import sys
import threading
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_lookback as dl


def _ts(dt):
    """Naive-UTC ISO string, the way Tranzor serializes timestamps."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class _Pager:
    """Fake newest-first list endpoint: ``fetch(limit, offset)``."""

    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda r: r["created_at"], reverse=True)
        self.calls = []

    def __call__(self, limit, offset):
        self.calls.append((limit, offset))
        return len(self.rows), [dict(r) for r in self.rows[offset:offset + limit]]


def _rows_every(hours, start, count, **extra):
    return [dict(task_id=f"t{i}", created_at=_ts(start - timedelta(hours=hours * i)),
                 **extra) for i in range(count)]


class CategoryTableTests(unittest.TestCase):
    def test_owner_table_sizes(self):
        sizes = {cat: len(projects) for cat, projects in dl.CATEGORY_PROJECTS}
        self.assertEqual(sizes, {
            "CoreLib": 6, "RCV": 11, "WEB": 21, "Copilot": 6, "DPW": 4,
            "ENGAGE": 7, "INTEGRATION": 5, "IVA": 3, "RCW": 2})
        self.assertEqual(sum(sizes.values()), 65)
        self.assertEqual(dl.CATEGORY_ORDER, (
            "CoreLib", "RCV", "WEB", "Copilot", "DPW", "ENGAGE",
            "INTEGRATION", "IVA", "RCW"))

    def test_no_duplicates_even_case_insensitively(self):
        flat = [p for _c, ps in dl.CATEGORY_PROJECTS for p in ps]
        self.assertEqual(len(flat), len({p.casefold() for p in flat}))

    def test_exact_and_variant_matches(self):
        self.assertEqual(dl.resolve_project("CoreLib/RoomsController"),
                         ("CoreLib", "CoreLib/RoomsController"))
        for variant in ("corelib/roomscontroller", "CoreLib/RoomsController.git",
                        " /CoreLib/RoomsController/ ", "CoreLib%2FRoomsController"):
            self.assertEqual(dl.resolve_project(variant),
                             ("CoreLib", "CoreLib/RoomsController"), variant)
        self.assertEqual(dl.resolve_project("sean.zhuang/Fiji"),
                         ("RCV", "sean.zhuang/Fiji"))
        self.assertEqual(dl.resolve_project("engage-voice/frontend/agent/agent-service"),
                         ("ENGAGE", "engage-voice/frontend/agent/agent-service"))

    def test_unknown_project(self):
        self.assertEqual(dl.resolve_project("Fiji/GElectron.git"),
                         (None, "Fiji/GElectron"))
        self.assertEqual(dl.resolve_project(""), (None, ""))


class DayWindowTests(unittest.TestCase):
    def test_utc8_day_maps_to_utc_window(self):
        start, end = dl.day_window("2026-09-27")
        self.assertEqual(start, _utc(2026, 9, 26, 16))
        self.assertEqual(end, _utc(2026, 9, 27, 16))

    def test_boundaries_are_half_open(self):
        w = dl.day_window("2026-09-27")
        self.assertFalse(dl.in_window("2026-09-26T15:59:59.999999", w))
        self.assertTrue(dl.in_window("2026-09-26T16:00:00", w))
        self.assertTrue(dl.in_window("2026-09-27T15:59:59.999999", w))
        self.assertFalse(dl.in_window("2026-09-27T16:00:00", w))
        # Live row from the API: 00:33 on 9/28 in UTC+8.
        self.assertFalse(dl.in_window("2026-09-27T16:33:51.609969", w))
        self.assertTrue(dl.in_window("2026-09-27T16:33:51.609969",
                                     dl.day_window("2026-09-28")))

    def test_offsets_and_z_suffix(self):
        w = dl.day_window("2026-09-27")
        self.assertTrue(dl.in_window("2026-09-27T00:00:00+08:00", w))
        self.assertFalse(dl.in_window("2026-09-26T23:59:59+08:00", w))
        self.assertTrue(dl.in_window("2026-09-26T16:00:00Z", w))

    def test_garbage_is_outside(self):
        w = dl.day_window("2026-09-27")
        for raw in (None, "", "yesterday", 12):
            self.assertFalse(dl.in_window(raw, w))

    def test_accepts_date_and_datetime(self):
        from datetime import date
        self.assertEqual(dl.day_window(date(2026, 9, 27)),
                         dl.day_window("2026-09-27"))
        self.assertEqual(dl.coerce_day(datetime(2026, 9, 27, 23)),
                         date(2026, 9, 27))
        self.assertIsNone(dl.coerce_day("27/09/2026"))
        with self.assertRaises(ValueError):
            dl.day_window("nope")

    def test_today_utc8(self):
        from datetime import date
        self.assertEqual(dl.today_utc8(_utc(2026, 9, 27, 15, 59)),
                         date(2026, 9, 27))
        self.assertEqual(dl.today_utc8(_utc(2026, 9, 27, 16, 0)),
                         date(2026, 9, 28))
        self.assertEqual(dl.today_utc8(datetime(2026, 9, 27, 16, 0)),
                         date(2026, 9, 28))


class CollectWindowTests(unittest.TestCase):
    def setUp(self):
        # 1000 rows, one per hour, newest 2026-09-28 10:00 UTC.
        self.newest = _utc(2026, 9, 28, 10)
        self.rows = _rows_every(1, self.newest, 1000)

    def brute(self, window):
        return sorted(r["task_id"] for r in self.rows
                      if dl.in_window(r["created_at"], window))

    def test_recent_day_uses_sequential_pages_and_stops_early(self):
        pager = _Pager(self.rows)
        w = dl.day_window("2026-09-27")
        got = dl.collect_window(pager, w, page_size=50)
        self.assertEqual(sorted(r["task_id"] for r in got), self.brute(w))
        self.assertEqual(len(got), 24)
        self.assertEqual([c[0] for c in pager.calls], [50])

    def test_old_day_binary_searches_instead_of_walking(self):
        pager = _Pager(self.rows)
        w = dl.day_window("2026-08-20")
        got = dl.collect_window(pager, w, page_size=50)
        self.assertEqual(sorted(r["task_id"] for r in got), self.brute(w))
        self.assertEqual(len(got), 24)
        probes = [c for c in pager.calls if c[0] == 1]
        self.assertGreater(len(probes), 0)
        self.assertLessEqual(len(pager.calls), 1 + 11 + 2)

    def test_matches_brute_force_on_many_windows_and_page_sizes(self):
        rng = random.Random(20260927)
        rows = []
        t = self.newest
        for i in range(700):
            t -= timedelta(minutes=rng.choice([1, 5, 30, 90, 600, 2000]))
            rows.append({"task_id": f"r{i}", "created_at": _ts(t)})
        self.rows = rows
        days = sorted({(datetime.fromisoformat(r["created_at"])
                        .replace(tzinfo=timezone.utc)
                        .astimezone(dl.TZ_UTC8).date()) for r in rows})
        days += [days[0] - timedelta(days=3), days[-1] + timedelta(days=1)]
        for size in (1, 7, 50, 200):
            for d in days:
                w = dl.day_window(d)
                got = dl.collect_window(_Pager(rows), w, page_size=size)
                self.assertEqual(sorted(r["task_id"] for r in got),
                                 self.brute(w), (size, d))

    def test_rows_inserted_while_paging_are_not_lost_or_doubled(self):
        base = _Pager(self.rows)
        inserted = [False]

        def pager(limit, offset):
            result = base(limit, offset)
            if not inserted[0]:
                inserted[0] = True
                fresh = _rows_every(0.01, self.newest + timedelta(hours=1), 30)
                for r in fresh:
                    r["task_id"] = "new-" + r["task_id"]
                base.rows = fresh + base.rows
            return result

        w = dl.day_window("2026-09-26")
        got = dl.collect_window(pager, w, page_size=10)
        ids = [r["task_id"] for r in got]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sorted(ids), self.brute(w))

    def test_empty_and_unparseable(self):
        self.assertEqual(dl.collect_window(_Pager([]), dl.day_window("2026-09-27")), [])
        rows = [{"task_id": "bad", "created_at": "zzz"},
                {"task_id": "ok", "created_at": "2026-09-27T01:00:00"}]
        pager = lambda limit, offset: (2, rows[offset:offset + limit])  # noqa: E731
        got = dl.collect_window(pager, dl.day_window("2026-09-27"))
        self.assertEqual([r["task_id"] for r in got], ["ok"])

    def test_cancel(self):
        ev = threading.Event()
        ev.set()
        with self.assertRaises(dl.LookbackCancelled):
            dl.collect_window(_Pager(self.rows), dl.day_window("2026-08-20"),
                              page_size=50, cancel_event=ev)

    def test_page_guard(self):
        with self.assertRaises(RuntimeError):
            dl.collect_window(_Pager(self.rows), dl.day_window("2026-09-10"),
                              page_size=1, max_pages=3)


class FetchSourceTests(unittest.TestCase):
    def test_mr_day_asks_for_completed_and_filters(self):
        seen = []

        def fake(status, limit, offset, base_url):
            seen.append((status, base_url))
            return 3, [
                {"task_id": "a", "status": "completed",
                 "created_at": "2026-09-27T01:00:00"},
                {"task_id": "b", "status": "skipped",
                 "created_at": "2026-09-27T00:30:00"},
                {"task_id": "c", "status": "completed",
                 "created_at": "2026-09-25T00:00:00"},
            ][offset:offset + limit]

        got = dl.fetch_mr_day(dl.day_window("2026-09-27"), base_url="B",
                              fetch_tasks=fake)
        self.assertEqual([t["task_id"] for t in got], ["a"])
        self.assertTrue(all(s == ("completed", "B") for s in seen))

    def test_scan_day(self):
        def fake(status, limit, offset, base_url):
            return 1, [{"task_id": "s1", "status": "completed",
                        "created_at": "2026-09-26T16:00:00"}][offset:offset + limit]
        got = dl.fetch_scan_day(dl.day_window("2026-09-27"), fetch_tasks=fake)
        self.assertEqual([t["task_id"] for t in got], ["s1"])

    def test_bugfix_day_filters_window_and_stops_paging(self):
        newest = _utc(2026, 9, 28, 2)
        subs = [{"submission_id": f"s{i}", "project_id": "web/bui",
                 "created_at": _ts(newest - timedelta(hours=6 * i)),
                 "summary": {"aggregate_status": "applied", "total": 1}}
                for i in range(400)]
        pages = []

        def fake_get(_url, params):
            pages.append(params["page"])
            start = (params["page"] - 1) * params["page_size"]
            return {"submissions": subs[start:start + params["page_size"]],
                    "total_submissions": len(subs)}

        got = dl.fetch_bugfix_day(dl.day_window("2026-09-27"), get_fn=fake_get)
        # 4 per UTC+8 day at 6h spacing; paging stops after page 1 (100 rows
        # reach 25 days back, well past the day-before floor).
        self.assertEqual(len(got), 4)
        self.assertEqual(pages, [1])
        self.assertTrue(all(r["platform_status"] == "applied" for r in got))

    def test_error_kind(self):
        class Resp:
            def __init__(self, code):
                self.status_code = code

        class HTTPErr(Exception):
            def __init__(self, code):
                super().__init__(f"{code} Client Error")
                self.response = Resp(code)

        self.assertEqual(dl.error_kind(HTTPErr(401)), "auth")
        self.assertEqual(dl.error_kind(HTTPErr(403)), "forbidden")
        self.assertEqual(dl.error_kind(HTTPErr(500)), "error")
        self.assertEqual(dl.error_kind(RuntimeError("Authorization header required 401")), "auth")
        self.assertEqual(dl.error_kind(RuntimeError("403 Client Error: Forbidden")), "forbidden")
        self.assertEqual(dl.error_kind(RuntimeError("timeout")), "error")

    def test_error_kind_ignores_numbers_inside_urls_and_json_errors(self):
        import json
        import requests
        url = "/api/v1/tasks?limit=1&offset=7403&status=completed"
        self.assertEqual(dl.error_kind(requests.ConnectionError(
            f"Max retries exceeded with url: {url}")), "error")
        self.assertEqual(dl.error_kind(requests.Timeout(
            f"Read timed out: {url.replace('7403', '4010')}")), "error")
        with self.assertRaises(ValueError) as ctx:
            json.loads("[" * 4013 + "x")
        self.assertIn("4013", str(ctx.exception))
        self.assertEqual(dl.error_kind(ctx.exception), "error")
        self.assertEqual(dl.error_kind(RuntimeError("row 14013 failed")), "error")

    def test_fetch_day_isolates_a_failing_source(self):
        class Forbidden(Exception):
            response = type("R", (), {"status_code": 403})()

        def bad(window, **_kw):
            raise Forbidden("403 Forbidden")

        out = dl.fetch_day(
            "2026-09-27",
            fetch_mr=lambda w, **kw: [{"task_id": "m"}],
            fetch_bugfix=bad,
            fetch_scan=lambda w, **kw: [])
        self.assertEqual(out["mr"], [{"task_id": "m"}])
        self.assertIsNone(out["bugfix"])
        self.assertEqual(out["scan"], [])
        self.assertEqual(out["errors"]["bugfix"][0], "forbidden")
        self.assertNotIn("mr", out["errors"])

    def test_fetch_day_passes_window_and_propagates_cancel(self):
        windows = []

        def rec(window, **kw):
            windows.append(window)
            return []

        dl.fetch_day("2026-09-27", fetch_mr=rec, fetch_bugfix=rec, fetch_scan=rec)
        self.assertEqual(set(windows), {dl.day_window("2026-09-27")})

        def cancelled(window, **kw):
            raise dl.LookbackCancelled()

        with self.assertRaises(dl.LookbackCancelled):
            dl.fetch_day("2026-09-27", fetch_mr=cancelled, fetch_bugfix=rec,
                         fetch_scan=rec)


class _Meta:
    def __init__(self, branch, jira="", title=""):
        self.target_branch = branch
        self.jira_id = jira
        self.title = title


class MrMetaTests(unittest.TestCase):
    def test_keys_dedupe_by_canonical_project_and_iid(self):
        tasks = [
            {"project_id": "web/web", "merge_request_iid": 5},
            {"project_id": "web/web", "merge_request_iid": "5"},
            {"project_id": "WEB/WEB", "merge_request_iid": 5},
            {"project_id": "web/jedi", "merge_request_iid": 5},
            {"project_id": "web/web", "merge_request_iid": None},
            {"project_id": "", "merge_request_iid": 9},
        ]
        self.assertEqual(dl.mr_keys(tasks), [("web/web", 5), ("web/jedi", 5)])

    def test_resolve_is_fail_open_and_reports_progress(self):
        def fetch(pid, iid):
            if iid == 2:
                raise RuntimeError("404")
            if iid == 3:
                return None
            if iid == 4:
                return _Meta("  ")
            return _Meta("develop", "UIA-1", "Title")

        progress = []
        out = dl.resolve_mr_meta(
            [("p", 1), ("p", 2), ("p", 3), ("p", 4)], fetch_meta=fetch,
            max_workers=2, on_progress=lambda n, t: progress.append((n, t)))
        self.assertEqual(out[("p", 1)], {"branch": "develop", "jira": "UIA-1",
                                          "title": "Title"})
        self.assertNotIn(("p", 2), out)
        self.assertNotIn(("p", 3), out)
        self.assertIs(out[("p", 4)]["branch"], dl.UNKNOWN_BRANCH)
        self.assertEqual(sorted(progress), [(1, 4), (2, 4), (3, 4), (4, 4)])

    def test_resolve_empty(self):
        self.assertEqual(dl.resolve_mr_meta([], fetch_meta=lambda *a: None), {})


def _mr(project, iid, status="completed", created="2026-09-27T01:00:00"):
    return {"task_id": f"{project}!{iid}@{created}", "project_id": project,
            "merge_request_iid": iid, "status": status, "created_at": created}


class BuildReportTests(unittest.TestCase):
    def report(self, **kw):
        kw.setdefault("mr_tasks", [])
        kw.setdefault("bugfix_rows", [])
        kw.setdefault("scan_tasks", [])
        return dl.build_report("2026-09-27", **kw)

    @staticmethod
    def project(report, name):
        for c in report["categories"]:
            for p in c["projects"]:
                if p["project"] == name:
                    return c, p
        raise AssertionError(name)

    def test_every_table_project_is_present_in_order(self):
        rep = self.report()
        self.assertEqual([c["name"] for c in rep["categories"]],
                         list(dl.CATEGORY_ORDER))
        for (cat, plist), c in zip(dl.CATEGORY_PROJECTS, rep["categories"]):
            self.assertEqual([p["project"] for p in c["projects"]], list(plist))
            self.assertEqual(c["active_projects"], 0)
        self.assertEqual(rep["totals"]["mr_count"], 0)
        self.assertEqual(rep["totals"]["active_projects"], 0)

    def test_mr_dedupe_and_runs(self):
        rep = self.report(mr_tasks=[
            _mr("web/web", 1, created="2026-09-27T01:00:00"),
            _mr("web/web", 1, created="2026-09-27T00:10:00"),
            _mr("web/web", 1, created="2026-09-27T05:00:00"),
            _mr("web/web", 2),
            _mr("web/jedi", 1),
            _mr("web/web", 3, status="skipped"),
        ])
        cat, p = self.project(rep, "web/web")
        self.assertEqual(p["mr_count"], 2)
        self.assertEqual(p["runs"], 4)
        self.assertEqual([m["iid"] for m in p["mrs"]], [1, 2])
        self.assertEqual(p["mrs"][0]["runs"], 3)
        self.assertEqual(p["mrs"][0]["first_created"], "2026-09-27T00:10:00")
        self.assertEqual(cat["name"], "WEB")
        self.assertEqual(cat["mr_count"], 3)       # web/web ×2 + web/jedi ×1
        self.assertEqual(rep["totals"]["mr_count"], 3)
        self.assertEqual(rep["totals"]["runs"], 5)

    def test_case_variants_merge_into_the_canonical_project(self):
        rep = self.report(mr_tasks=[_mr("fiji/fiji", 7), _mr("Fiji/Fiji", 7)])
        _c, p = self.project(rep, "Fiji/Fiji")
        self.assertEqual(p["mr_count"], 1)
        self.assertEqual(rep["totals"]["unmapped_projects"], 0)

    def test_bugfix_and_scan_counts(self):
        rep = self.report(
            bugfix_rows=[
                {"project_id": "web/bui", "platform_status": "applied"},
                {"project_id": "web/bui", "platform_status": "partially_applied"},
                {"project_id": "web/bui", "platform_status": "create_failed"},
                {"project_id": "", "mr_project_id": "common/uns",
                 "platform_status": "applied"},
            ],
            scan_tasks=[
                {"project_id": "iva/iva-ui", "status": "completed"},
                {"project_id": "iva/iva-ui", "status": "failed"},
            ])
        _c, bui = self.project(rep, "web/bui")
        _c, uns = self.project(rep, "common/uns")
        cat, iva = self.project(rep, "iva/iva-ui")
        self.assertEqual((bui["bugfix"], uns["bugfix"]), (2, 1))
        self.assertEqual(iva["scan"], 1)
        self.assertTrue(iva["active"])
        self.assertEqual(cat["active_projects"], 1)
        self.assertEqual(rep["totals"]["bugfix"], 3)
        self.assertEqual(rep["totals"]["scan"], 1)
        self.assertEqual(rep["totals"]["active_projects"], 3)

    def test_unmapped_projects_get_their_own_group(self):
        rep = self.report(mr_tasks=[_mr("Fiji/GElectron", 4)],
                          scan_tasks=[{"project_id": "zzz/new", "status": "completed"}])
        last = rep["categories"][-1]
        self.assertEqual(last["name"], dl.UNMAPPED)
        self.assertFalse(last["mapped"])
        self.assertEqual([p["project"] for p in last["projects"]],
                         ["Fiji/GElectron", "zzz/new"])
        self.assertEqual(rep["totals"]["unmapped_projects"], 2)
        self.assertEqual(rep["totals"]["mr_count"], 1)
        self.assertEqual(rep["totals"]["scan"], 1)

    def test_failed_source_is_none_not_zero(self):
        rep = dl.build_report("2026-09-27", mr_tasks=[_mr("web/web", 1)],
                              bugfix_rows=None, scan_tasks=[],
                              errors={"bugfix": ("forbidden", "403")})
        self.assertIsNone(rep["totals"]["bugfix"])
        self.assertEqual(rep["totals"]["scan"], 0)
        self.assertFalse(rep["sources"]["bugfix"])
        self.assertEqual(rep["errors"]["bugfix"][0], "forbidden")
        rep = dl.build_report("2026-09-27")
        self.assertIsNone(rep["totals"]["mr_count"])
        self.assertIsNone(rep["totals"]["branch_count"])

    def test_branches_pending_then_resolved(self):
        tasks = [_mr("web/web", 1), _mr("web/web", 2), _mr("web/web", 3),
                 _mr("web/jedi", 1), _mr("RND/rwc", 9)]
        pending = self.report(mr_tasks=tasks)
        self.assertFalse(pending["branches_resolved"])
        self.assertIsNone(pending["totals"]["branch_count"])
        _c, p = self.project(pending, "web/web")
        self.assertEqual(p["branches"], Counter())

        meta = {("web/web", 1): {"branch": "develop", "jira": "UIA-1", "title": "T1"},
                ("web/web", 2): {"branch": "develop", "jira": "", "title": ""},
                ("web/web", 3): {"branch": "master", "jira": "", "title": ""},
                ("web/jedi", 1): {"branch": "develop", "jira": "", "title": ""}}
        rep = self.report(mr_tasks=tasks, mr_meta=meta)
        cat, p = self.project(rep, "web/web")
        self.assertEqual(p["branches"], Counter({"develop": 2, "master": 1}))
        self.assertEqual(p["mrs"][0]["jira"], "UIA-1")
        self.assertEqual(cat["branches"], Counter({"develop": 3, "master": 1}))
        _c, rwc = self.project(rep, "RND/rwc")
        self.assertEqual(rwc["branches"], Counter({dl.UNKNOWN_BRANCH: 1}))
        self.assertEqual(rep["totals"]["branch_count"], 2)
        self.assertEqual(rep["totals"]["unknown_branch_mrs"], 1)


class FormatTests(unittest.TestCase):
    def test_format_branches(self):
        c = Counter({"master": 1, "develop": 3, "Alpha": 1, dl.UNKNOWN_BRANCH: 2})
        self.assertEqual(dl.format_branches(c, unknown_label="(unknown)"),
                         "develop ×3, Alpha, master, (unknown) ×2")
        self.assertEqual(dl.format_branches(c, unknown_label="?", limit=2),
                         "develop ×3, Alpha, +1, ? ×2")
        self.assertEqual(dl.format_branches(Counter({dl.UNKNOWN_BRANCH: 1}),
                                            unknown_label="?"), "?")
        self.assertEqual(dl.format_branches(Counter()), "")

    def test_tsv(self):
        rep = dl.build_report(
            "2026-09-27", mr_tasks=[_mr("web/web", 1)], bugfix_rows=None,
            scan_tasks=[], mr_meta={("web/web", 1): {"branch": "develop",
                                                    "jira": "", "title": ""}})
        text = dl.report_to_tsv(rep, headers=["Cat", "Project", "MRs", "Br",
                                              "BF", "Scan"])
        lines = text.splitlines()
        self.assertEqual(lines[0], "Cat\tProject\tMRs\tBr\tBF\tScan")
        self.assertIn("WEB\t\t1\tdevelop\t—\t0", lines)
        self.assertIn("WEB\tweb/web\t1\tdevelop\t—\t0", lines)
        self.assertEqual(len(lines), 1 + 9 + 1)
        full = dl.report_to_tsv(rep, headers=["a"] * 6, include_idle=True)
        self.assertEqual(len(full.splitlines()), 1 + 9 + 65)

    def test_tsv_marks_unresolved_branches_like_the_view(self):
        rep = dl.build_report("2026-09-27", mr_tasks=[_mr("web/web", 1)],
                              bugfix_rows=[], scan_tasks=[])
        lines = dl.report_to_tsv(rep, headers=["h"] * 6,
                                 unresolved="…").splitlines()
        self.assertIn("WEB\tweb/web\t1\t…\t0\t0", lines)
        self.assertIn("CoreLib\t\t0\t\t0\t0", lines)   # no MRs → blank
        lines = dl.report_to_tsv(rep, headers=["h"] * 6).splitlines()
        self.assertIn("WEB\tweb/web\t1\t—\t0\t0", lines)


if __name__ == "__main__":
    unittest.main()
