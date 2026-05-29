"""Daily HTML digest (PR-E).

Self-contained CLI that builds an HTML report of "what Lillian should
look at this morning" from the local ``tranzor_checks`` cache. Designed
to be wired into Windows Task Scheduler so the file pops open in her
default browser at 09:00 every weekday.

Usage::

    # Default: write next to ~/Documents and auto-open in browser.
    python my-tools/daily_digest.py

    # CI / cron — write to a specific path, skip auto-open.
    python my-tools/daily_digest.py --out C:/tmp/digest.html --no-open

The HTML is fully static (no JS bundle, no remote fonts) so it survives
being archived / emailed. All data comes from the SQLite cache —
``tranzor_checks.get_worklist_items`` for the worklist, ``get_merge_events``
for the watchdog event ring, ``get_review_summary`` for the reviewer
badge.

Rendering is split into pure functions (``render_digest_html``,
``_section_*``) so unit tests can assert on the HTML string without
spawning a browser.
"""
from __future__ import annotations

import argparse
import html
import os
import sys
import webbrowser
from datetime import datetime, timezone
from typing import Iterable


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tranzor_checks as tc


_TIER_DOT = {
    "red": "🔴", "amber": "🟡", "green": "🟢", "grey": "⚪",
}


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


# ---------------------------------------------------------------------------
# Section renderers — each returns a self-contained ``<section>...</section>``
# string. Kept tiny on purpose; HTML survives a glance-review of the file.
# ---------------------------------------------------------------------------

def _section_header(*, generated_at: str, summary: dict) -> str:
    today = generated_at[:10]
    return f"""
<section class="header">
  <h1>Tranzor Translation Review · Daily Digest</h1>
  <p class="meta">Generated {_esc(generated_at)} ·
  reviewer <strong>{_esc(summary.get('reviewer') or '—')}</strong> ·
  today {summary.get('today', 0)} · total {summary.get('total', 0)}</p>
  <p class="legend">🔴 Imminent merge · 🟡 Today · 🟢 Can wait
  · 🆕 = unregistered terms found · ✓ = reviewed count / total issues</p>
</section>
"""


def _section_worklist(items: Iterable[dict]) -> str:
    rows = []
    for d in items:
        tier = d.get("merge_tier") or "grey"
        dot = _TIER_DOT.get(tier, "⚪")
        reviewed = d.get("reviewed_count") or 0
        total_iss = d.get("total_issue_count") or 0
        reviewed_disp = f"{reviewed}/{total_iss}" if total_iss else "—"
        new_count = d.get("unregistered_term_count") or 0
        new_disp = str(new_count) if new_count else "—"
        url = d.get("mr_web_url") or ""
        task_cell = _esc(d.get("task_name") or "")
        if url:
            task_cell = (
                f'<a href="{_esc(url)}" target="_blank" rel="noopener">'
                f'{task_cell}</a>'
            )
        zh = d.get("zh_issues") or 0
        sec = d.get("secondary_issues") or 0
        oth = d.get("other_issues") or 0
        other_total = sec + oth
        rows.append(f"""
    <tr class="tier-{tier}">
      <td class="dot">{dot}</td>
      <td>{_esc(d.get('project_name') or d.get('project_id') or '-')}</td>
      <td class="num">{_esc(d.get('mr_iid') or '-')}</td>
      <td>{task_cell}</td>
      <td class="num">{zh}</td>
      <td class="num">{other_total}</td>
      <td class="num">{_esc(reviewed_disp)}</td>
      <td class="num">{_esc(new_disp)}</td>
      <td class="num">{_esc(d.get('mr_state') or '—')}</td>
    </tr>""")
    if not rows:
        body = (
            '<tr><td colspan="9" class="empty">'
            'Nothing to review right now — enjoy your morning ☕.'
            '</td></tr>'
        )
    else:
        body = "".join(rows)
    return f"""
<section>
  <h2>Today's Review Queue</h2>
  <table class="worklist">
    <thead>
      <tr>
        <th class="dot">Risk</th>
        <th>Project</th>
        <th>MR #</th>
        <th>Task</th>
        <th>zh issues</th>
        <th>other</th>
        <th>Reviewed</th>
        <th>🆕</th>
        <th>State</th>
      </tr>
    </thead>
    <tbody>{body}
    </tbody>
  </table>
</section>
"""


def _section_events(events: Iterable[dict]) -> str:
    """Recent merge-watchdog events ring. Lillian uses this to spot
    MRs that merged while she was off, and follow up if needed."""
    rows = []
    for ev in events:
        new_state = (ev.get("new_state") or "").lower()
        tier_cls = "merged" if new_state == "merged" else "closed"
        url = ev.get("mr_web_url") or ""
        mr_disp = _esc(ev.get("mr_iid") or "—")
        if url:
            mr_disp = (
                f'<a href="{_esc(url)}" target="_blank" rel="noopener">'
                f'#{mr_disp}</a>'
            )
        rows.append(f"""
    <tr class="event-{tier_cls}">
      <td class="num">{_esc(ev.get('observed_at') or '')}</td>
      <td>{_esc(ev.get('project_name') or '-')}</td>
      <td class="num">{mr_disp}</td>
      <td>{_esc(ev.get('task_name') or '')}</td>
      <td>{_esc(ev.get('old_state') or '?')} → {_esc(new_state)}</td>
    </tr>""")
    if not rows:
        return ""  # Omit empty section entirely — less noise.
    return f"""
<section>
  <h2>Recent merge events</h2>
  <p class="meta">From the watchdog ring — MRs whose GitLab state
  changed since the last check.</p>
  <table class="events">
    <thead>
      <tr>
        <th>Observed (UTC)</th>
        <th>Project</th>
        <th>MR</th>
        <th>Task</th>
        <th>Transition</th>
      </tr>
    </thead>
    <tbody>{''.join(rows)}
    </tbody>
  </table>
</section>
"""


_CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  background: #f7f8fa; color: #1f2937; margin: 0; padding: 24px;
  max-width: 1200px; margin-left: auto; margin-right: auto;
}
h1 { font-size: 22px; margin: 0 0 6px 0; }
h2 { font-size: 16px; margin: 28px 0 8px 0; color: #374151; }
section { margin-bottom: 12px; }
.meta { color: #6b7280; font-size: 13px; margin: 0 0 4px 0; }
.legend { color: #6b7280; font-size: 12px; margin: 0 0 12px 0; }
table {
  width: 100%; border-collapse: collapse; background: #fff;
  border-radius: 8px; overflow: hidden;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  font-size: 13px;
}
th { background: #1f2937; color: #fff; padding: 8px 10px; text-align: left; }
td { padding: 6px 10px; border-bottom: 1px solid #f1f3f5;
     vertical-align: top; }
tr:last-child td { border-bottom: none; }
td.num { text-align: center; }
td.dot { text-align: center; font-size: 16px; }
.empty { text-align: center; color: #6b7280; padding: 24px;
         font-style: italic; }

.worklist tr.tier-red    { background: #fff5f5; }
.worklist tr.tier-amber  { background: #fffaf0; }
.worklist tr.tier-green  { background: #f0fff4; }
.worklist tr.tier-grey   { background: #f3f4f6; color: #6b7280; }

.events tr.event-merged { background: #f0fff4; }
.events tr.event-closed { background: #fff5f5; }

a { color: #2563eb; text-decoration: none; }
a:hover { text-decoration: underline; }
"""


def render_digest_html(
    *,
    worklist_items: list[dict],
    merge_events: list[dict],
    review_summary: dict,
    generated_at: str,
) -> str:
    """Pure renderer — composes the sections into one HTML document.

    Unit tests poke this with hand-crafted inputs and assert on the
    output string; no DB, no network.
    """
    title = (
        "Tranzor Review Digest · "
        + (generated_at[:10] if generated_at else "")
    )
    parts = [
        _section_header(
            generated_at=generated_at, summary=review_summary),
        _section_worklist(worklist_items),
        _section_events(merge_events),
    ]
    body = "".join(parts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>{body}</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_out_path() -> str:
    """Default output path under the user's Documents folder.

    Falls back to the current dir if Documents doesn't exist — keeps
    the script useful inside a stripped-down container or VM.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    base = docs if os.path.isdir(docs) else os.getcwd()
    return os.path.join(base, f"Tranzor-Digest-{today}.html")


def build_digest(
    *,
    out_path: str,
    limit: int = 50,
    include_grey: bool = False,
    include_reviewed: bool = False,
    reviewer: str | None = None,
    known_terms: bool = True,
    event_limit: int = 30,
) -> str:
    """Build the digest HTML and write it to ``out_path``. Returns the
    absolute path actually written (with PermissionError fallback, the
    file may end up at ``..._1.html`` etc., same convention as
    :mod:`export_changes`)."""
    # Resolve glossary once; falls back to None on platform unreachable
    # so the digest still builds (🆕 column just shows zeros).
    known = None
    if known_terms:
        try:
            import tranzor_terminology as tt
            known = tt.load_known_term_names_lower()
        except Exception:
            known = None

    items = tc.get_worklist_items(
        limit=limit,
        include_grey=include_grey,
        include_fully_reviewed=include_reviewed,
        reviewer=reviewer,
        known_term_names_lower=known,
    )
    events = tc.get_merge_events(limit=event_limit)
    summary = tc.get_review_summary(reviewer=reviewer)
    html_text = render_digest_html(
        worklist_items=items,
        merge_events=events,
        review_summary=summary,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    base, ext = os.path.splitext(out_path)
    target = out_path
    for attempt in range(100):
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(html_text)
            return os.path.abspath(target)
        except PermissionError:
            target = f"{base}_{attempt + 1}{ext}"
    raise RuntimeError(f"Could not write digest to {out_path}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Tranzor daily review digest",
    )
    parser.add_argument(
        "--out", "-o", default=None,
        help="Output HTML path (default: ~/Documents/Tranzor-Digest-<date>.html)",
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Max MR rows in the worklist section (default 50)",
    )
    parser.add_argument(
        "--include-grey", action="store_true",
        help="Include merged / skip-translate MRs in the worklist",
    )
    parser.add_argument(
        "--include-reviewed", action="store_true",
        help="Include MRs the reviewer has already fully reviewed",
    )
    parser.add_argument(
        "--reviewer",
        help="Reviewer ID (default: $TRANZOR_REVIEWER or your OS username)",
    )
    parser.add_argument(
        "--skip-glossary", action="store_true",
        help="Skip the 🆕 unregistered-term scan (faster, omits column)",
    )
    parser.add_argument(
        "--event-limit", type=int, default=30,
        help="Max recent merge events to include (default 30)",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="Don't auto-open the HTML in a browser when done.",
    )
    args = parser.parse_args(argv)

    out = args.out or _default_out_path()
    saved = build_digest(
        out_path=out,
        limit=args.limit,
        include_grey=args.include_grey,
        include_reviewed=args.include_reviewed,
        reviewer=args.reviewer,
        known_terms=not args.skip_glossary,
        event_limit=args.event_limit,
    )
    print(f"Digest written: {saved}")
    if not args.no_open:
        try:
            webbrowser.open(f"file://{saved}", new=2)
        except Exception as e:
            print(f"Could not auto-open browser: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
