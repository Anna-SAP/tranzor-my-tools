"""Timezone-aware display helpers for user-visible timestamps.

Every clock the UI (and user-facing exports) shows should carry an explicit
offset such as ``UTC+8``, so a reader never has to guess whether a naive
``YYYY-MM-DD HH:MM:SS`` is local, UTC, or something else.

Contract:

* Naive timestamps are treated as already in the display zone (the historical
  GUI contract: Tranzor's ``created_at`` is shown as-is, and ``format_age_days``
  diffs it against ``datetime.now()``).
* Timezone-aware timestamps are converted into the display zone before
  formatting, so a ``…+00:00`` value is not mis-labelled as local.
* The suffix is compact: ``UTC+8``, ``UTC-5``, ``UTC+5:30``, or ``UTC``.
"""
from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Optional

FMT_FULL = "%Y-%m-%d %H:%M:%S"
FMT_SHORT = "%m-%d %H:%M"
FMT_MINUTE = "%Y-%m-%d %H:%M"


def local_tz() -> tzinfo:
    """Host local timezone, falling back to UTC if the OS has none."""
    return datetime.now().astimezone().tzinfo or timezone.utc


def format_tz_label(*, at: Optional[datetime] = None,
                    tz: Optional[tzinfo] = None) -> str:
    """Return a compact offset label for ``tz`` (default: host local).

    ``at`` selects the DST-correct offset when the zone observes daylight
    saving. Inject ``tz`` from tests so the label does not depend on the
    machine that runs the suite.
    """
    zone = tz
    if at is None:
        dt = datetime.now(zone) if zone is not None else datetime.now().astimezone()
    elif at.tzinfo is None:
        dt = at.replace(tzinfo=zone or local_tz())
    else:
        dt = at.astimezone(zone) if zone is not None else at.astimezone()
    off = dt.utcoffset()
    if off is None:
        return "UTC"
    total = int(off.total_seconds())
    if total == 0:
        return "UTC"
    sign = "+" if total > 0 else "-"
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if minutes:
        return f"UTC{sign}{hours}:{minutes:02d}"
    return f"UTC{sign}{hours}"


def parse_iso_datetime(value) -> Optional[datetime]:
    """Tolerant parse of ISO-ish timestamps (space or T, optional Z / offset).

    Returns a ``datetime`` (naive or aware, matching the input) or ``None``
    when ``value`` is missing / unparsable. ``datetime`` inputs pass through.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except (TypeError, ValueError):
            continue
    return None


def _strip_to_clock(text: str) -> str:
    """Best-effort ``YYYY-MM-DD HH:MM:SS`` from a free-form timestamp string."""
    cleaned = text.replace("T", " ").strip()
    tail = cleaned[10:] if len(cleaned) > 10 else ""
    for sep in (".", "+", "Z"):
        idx = tail.find(sep)
        if idx >= 0:
            cleaned = cleaned[:10 + idx]
            break
    return cleaned[:19].strip()


def format_display_datetime(value, *, fmt: str = FMT_FULL, empty: str = "",
                            tz: Optional[tzinfo] = None) -> str:
    """Format ``value`` for the UI with an explicit timezone suffix.

    ``empty`` is returned for missing input so callers can choose ``""``
    (blank cell) or ``"—"`` (em-dash placeholder) without a second check.
    Unparsable input keeps a truncated clock and still gets a tz label, so
    the cell is never timezone-silent.
    """
    if value is None:
        return empty
    if isinstance(value, str) and not value.strip():
        return empty
    target = tz or local_tz()
    dt = parse_iso_datetime(value)
    if dt is None:
        fallback = _strip_to_clock(str(value))
        if not fallback:
            return empty
        return f"{fallback} {format_tz_label(tz=target)}"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=target)
    else:
        dt = dt.astimezone(target)
    return f"{dt.strftime(fmt)} {format_tz_label(at=dt, tz=target)}"


def format_display_now(*, fmt: str = FMT_FULL,
                       tz: Optional[tzinfo] = None) -> str:
    """``datetime.now()`` in the display zone, with tz suffix."""
    zone = tz or local_tz()
    return format_display_datetime(datetime.now(zone), fmt=fmt, tz=zone)
