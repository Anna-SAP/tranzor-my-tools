"""Capture a REAL Tranzor MR Pipeline snapshot for the hosted Live Demo.

Why this exists
---------------
The Live Demo (``public/index.html``) is served as a static GitLab Pages
site over ``https://``. The real Tranzor API is an internal ``http://`` host
behind Bearer-JWT auth with no CORS for the Pages origin, so the demo page
**cannot** call the live API from the browser (mixed-content + CORS + auth).

This script runs on a machine that *can* reach the API (corp network, a
valid cached token in ``~/.tranzor_exporter_auth.json``), calls the **real**
official Tranzor API via the same code path the desktop app uses
(``export_mr_pipeline`` + ``tranzor_auth``), and writes a real-data snapshot
to ``public/demo_data.json``. The demo then renders that real snapshot — every
project, MR number, release, score and string count is genuine, and clicking a
row opens a Tranzor task that actually exists.

Re-run this whenever you want to refresh the demo data, then commit
``public/demo_data.json`` and let GitLab Pages redeploy.

Usage:  python build_demo_data.py [--limit N] [--out public/demo_data.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tranzor_auth as auth
import export_mr_pipeline as mr


def _parse_iso(s):
    """Parse the server's ISO timestamps (naive, microseconds, sometimes 'Z')."""
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            return dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None


def _fmt_duration(created, updated):
    """Pipeline duration as the desktop app shows it: '43s' / '5m07s'."""
    c, u = _parse_iso(created), _parse_iso(updated)
    if not c or not u:
        return None
    secs = int((u - c).total_seconds())
    if secs < 0:
        return None
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m{secs % 60:02d}s"


def collect(target_rows):
    """Pull real MR tasks and enrich each with release / score / string count.

    Strategy: gather recent tasks across the meaningful statuses, dedupe by
    (project_id, merge_request_iid), prefer rows that carry a real release so
    the Release column is informative, then enrich the chosen ones.
    """
    auth.load()
    if not auth.has_valid_token():
        raise SystemExit(
            "No valid Tranzor token. Open the desktop app and log in once "
            "(it caches a JWT in ~/.tranzor_exporter_auth.json), then re-run."
        )
    auth.install()

    seen, picked = set(), []
    # Completed first (rich data), then a few failed/running for status variety.
    plan = [("completed", target_rows * 4), ("failed", 12), ("running", 8)]
    for status, lim in plan:
        try:
            _, tasks = mr.fetch_mr_tasks(status=status, limit=lim)
        except Exception as e:
            print(f"  warn: list {status} failed: {e!r}")
            continue
        for t in tasks:
            pid, iid = t.get("project_id"), t.get("merge_request_iid")
            if not pid or not iid:
                continue
            key = (pid, str(iid))
            if key in seen:
                continue
            seen.add(key)
            picked.append(t)

    # Prefer tasks that already expose a real release; keep newest first.
    picked.sort(key=lambda t: (t.get("release") is None, t.get("created_at") or ""),
                reverse=False)
    picked.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    with_release = [t for t in picked if t.get("release")]
    chosen = (with_release or picked)[:target_rows]

    rows = []
    for t in chosen:
        tid = t.get("task_id")
        score = None
        try:
            det = mr.fetch_mr_task_detail(tid)
            avg = det.get("average_score")
            score = round(float(avg), 1) if avg is not None else None
        except Exception:
            det = {}
        strings = None
        post_edit = False
        try:
            res = mr.fetch_mr_results(tid)
            trs = res.get("translations", [])
            strings = mr.distinct_source_string_count(trs) or None
            # iteration > 1 on any row is a reliable, cheap proxy for "was re-worked"
            post_edit = any((x.get("iteration") or 0) and x.get("iteration") > 1 for x in trs)
        except Exception:
            pass
        rows.append({
            "project_id": t.get("project_id"),
            "mr_id": str(t.get("merge_request_iid")),
            "release": t.get("release") or "unknown",
            "status": t.get("status") or "unknown",
            "strings": strings,
            "score": score,
            "created_at": t.get("created_at"),
            "duration": _fmt_duration(t.get("created_at"), t.get("updated_at")),
            "post_edit": post_edit,
        })
        print(f"  + {rows[-1]['project_id']} !{rows[-1]['mr_id']} "
              f"{rows[-1]['release']} {rows[-1]['status']} "
              f"strings={rows[-1]['strings']} score={rows[-1]['score']}")

    # age_seconds relative to the newest row → timezone-safe "Xh ago" in the UI.
    times = [_parse_iso(r["created_at"]) for r in rows if _parse_iso(r["created_at"])]
    newest = max(times) if times else None
    for r in rows:
        c = _parse_iso(r["created_at"])
        r["age_seconds"] = int((newest - c).total_seconds()) if (newest and c) else 0
        r.pop("created_at", None)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="number of rows to capture")
    ap.add_argument("--out", default=os.path.join("public", "demo_data.json"))
    args = ap.parse_args()

    print(f"Capturing real Tranzor MR Pipeline data ({mr.MR_API}) ...")
    rows = collect(args.limit)
    payload = {
        "source": "Tranzor MR Pipeline API (/api/v1/tasks) — real captured snapshot",
        "captured_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tranzor_base": mr.TRANZOR_URL,
        "row_count": len(rows),
        "rows": rows,
    }
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {len(rows)} real rows -> {out}")


if __name__ == "__main__":
    main()
