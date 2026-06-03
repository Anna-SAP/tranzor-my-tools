"""Tests for the MR Pipeline table sort key.

``MRPipelineTab._apply_sort`` sorts with ``sorted(key=_mr_sort_key,
reverse=descending)``. The key has one tricky job: keep "missing" cells (the
"…" loading placeholder, the "—" no-data dash, and blanks) at the *bottom* in
both directions, while numeric columns order by magnitude. These tests
reproduce the exact call shape and lock that behaviour down.

Run:  python -m unittest test_mr_sort_key
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui_tabs import MRPipelineTab


def _sort(values, numeric, descending):
    """Mirror _apply_sort's ``sorted(..., reverse=descending)`` call."""
    return sorted(
        values,
        key=lambda v: MRPipelineTab._mr_sort_key(v, numeric, descending),
        reverse=descending,
    )


class NumericSortTests(unittest.TestCase):

    def test_ascending_orders_by_magnitude(self):
        self.assertEqual(
            _sort(["12", "3", "120"], numeric=True, descending=False),
            ["3", "12", "120"],
        )

    def test_descending_is_biggest_first(self):
        # This is the headline use case: "sort by workload" → biggest first.
        self.assertEqual(
            _sort(["12", "3", "120"], numeric=True, descending=True),
            ["120", "12", "3"],
        )

    def test_placeholders_sink_to_bottom_both_directions(self):
        asc = _sort(["12", "…", "3", "—"], numeric=True, descending=False)
        self.assertEqual(asc[:2], ["3", "12"])
        self.assertEqual(set(asc[2:]), {"…", "—"})

        desc = _sort(["12", "…", "3", "—"], numeric=True, descending=True)
        self.assertEqual(desc[:2], ["12", "3"])
        self.assertEqual(set(desc[2:]), {"…", "—"})

    def test_integer_cells_supported(self):
        # Cells filled from cache are real ints, not strings.
        self.assertEqual(
            _sort([12, 3, 120], numeric=True, descending=True),
            [120, 12, 3],
        )


class TextSortTests(unittest.TestCase):

    def test_blank_sinks_to_bottom_both_directions(self):
        asc = _sort(["unknown", "", "Release 26.3"],
                    numeric=False, descending=False)
        self.assertEqual(asc[-1], "")
        desc = _sort(["unknown", "", "Release 26.3"],
                     numeric=False, descending=True)
        self.assertEqual(desc[-1], "")

    def test_case_insensitive_ordering(self):
        self.assertEqual(
            _sort(["beta", "Alpha"], numeric=False, descending=False),
            ["Alpha", "beta"],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
