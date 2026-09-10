"""Fit ttk.Notebook tab titles into the available width with ellipses.

Tk's notebook squeezes tabs proportionally when their natural widths exceed
the row and simply clips the text ("📁 File Trans", "🔀 MR Pip" …).  This
module computes, for a given pixel budget, the longest prefix of each title
that still fits so we can hand Tk texts that *do* fit, with a visible "…"
marking the truncation.  The hover tooltip in the GUI shows the full title.

Pure functions; ``measure`` is injected (``tkinter.font.Font.measure`` in the
app, a fake in tests).
"""
from __future__ import annotations

from typing import Callable, Sequence

ELLIPSIS = "…"


def _truncate(title: str, width: int, measure: Callable[[str], int],
              ellipsis: str, min_chars: int) -> str:
    """Longest ``title[:k].rstrip() + ellipsis`` whose width ≤ ``width``."""
    if len(title) <= min_chars:
        return title
    lo, hi = min_chars, len(title) - 1        # k in [min_chars, len-1]
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = title[:mid].rstrip() + ellipsis
        if measure(cand) <= width:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        best = title[:min_chars].rstrip() + ellipsis
    return best


def text_budget(natural: Sequence[int], budget: int) -> float:
    """Largest per-tab text width ``W`` with ``sum(min(w, W)) ≤ budget``.

    Water-filling: short titles keep their natural width, the remaining
    pixels are shared equally among the titles that must shrink.
    """
    n = len(natural)
    if n == 0:
        return float("inf")
    if sum(natural) <= budget:
        return float("inf")
    remaining = float(budget)
    for k, w in enumerate(sorted(natural)):
        rest = n - k
        if w * rest <= remaining:
            remaining -= w
            continue
        return remaining / rest
    return float(max(natural))


def fit_tab_titles(titles: Sequence[str], measure: Callable[[str], int],
                   available: int, overhead: int, *,
                   ellipsis: str = ELLIPSIS, min_chars: int = 2) -> list[str]:
    """Return display strings for ``titles`` so the tab row fits ``available``.

    ``overhead`` is the per-tab non-text width (padding + borders).  Titles
    that fit are returned verbatim; the rest are truncated with ``ellipsis``.
    ``min_chars`` guards the leading emoji glyph (``"📁 "``) so a truncated
    tab still shows its icon.
    """
    titles = list(titles)
    n = len(titles)
    if n == 0:
        return []
    natural = [measure(t) for t in titles]
    budget = available - n * overhead
    if sum(natural) + n * overhead <= available:
        return titles
    if budget <= 0:
        # Degenerate (window narrower than the tab chrome): shortest form.
        return [_truncate(t, 0, measure, ellipsis, min_chars) for t in titles]
    limit = text_budget(natural, budget)
    out = []
    for title, width in zip(titles, natural):
        if width <= limit:
            out.append(title)
        else:
            out.append(_truncate(title, int(limit), measure, ellipsis, min_chars))
    return out
