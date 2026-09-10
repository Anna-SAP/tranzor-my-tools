"""Display-free tests for the Notebook tab-title fitter."""
from __future__ import annotations

import unittest

import tab_labels


def _measure(text: str) -> int:
    """Fake font: 7px per character, emoji/ellipsis included."""
    return 7 * len(text)


TITLES = [
    "📁 File Translation",
    "🔀 MR Pipeline",
    "📊 Quality Overview",
    "🌐 Full Translations",
    "🐛 BugFix",
]


class TestFitTabTitles(unittest.TestCase):
    def test_everything_fits_returns_titles_verbatim(self):
        out = tab_labels.fit_tab_titles(TITLES, _measure, available=10_000, overhead=20)
        self.assertEqual(out, TITLES)

    def test_empty(self):
        self.assertEqual(tab_labels.fit_tab_titles([], _measure, 100, 10), [])

    def test_truncated_row_fits_and_marks_with_ellipsis(self):
        overhead = 20
        available = 400
        out = tab_labels.fit_tab_titles(TITLES, _measure, available, overhead)
        self.assertEqual(len(out), len(TITLES))
        total = sum(_measure(t) + overhead for t in out)
        self.assertLessEqual(total, available)
        truncated = [t for t in out if t != TITLES[out.index(t)]]
        self.assertTrue(truncated, "expected at least one shortened title")
        for t in truncated:
            self.assertTrue(t.endswith(tab_labels.ELLIPSIS))
            self.assertNotEqual(t[:-1].strip(), "")

    def test_short_titles_are_kept_while_long_ones_shrink(self):
        overhead = 20
        available = 20 * 5 + 7 * (9 + 9 + 9 + 9 + 9)  # 9 chars each of text
        out = tab_labels.fit_tab_titles(TITLES, _measure, available, overhead)
        # "🐛 BugFix" is 8 chars → fits; the long ones get "…"
        self.assertEqual(out[4], "🐛 BugFix")
        for i in (0, 1, 2, 3):
            self.assertTrue(out[i].endswith(tab_labels.ELLIPSIS), out[i])
            self.assertLess(len(out[i]), len(TITLES[i]))

    def test_leading_glyph_survives_even_when_very_narrow(self):
        out = tab_labels.fit_tab_titles(TITLES, _measure, available=10, overhead=20)
        for t in out:
            self.assertTrue(t.startswith(("📁", "🔀", "📊", "🌐", "🐛")), t)
            self.assertTrue(t.endswith(tab_labels.ELLIPSIS))

    def test_truncation_prefers_longest_fitting_prefix(self):
        out = tab_labels.fit_tab_titles(["📁 File Translation"], _measure,
                                        available=7 * 8 + 10, overhead=10)
        # budget 56px → 8 chars incl. ellipsis → "📁 File…" (7 chars + …)
        self.assertEqual(out, ["📁 File…"])

    def test_rstrip_before_ellipsis(self):
        out = tab_labels.fit_tab_titles(["AB CD"], lambda s: 10 * len(s),
                                        available=40 + 5, overhead=5)
        # 40px → 4 chars incl. "…" → prefix "AB " → rstrip → "AB…"
        self.assertEqual(out, ["AB…"])


class TestTextBudget(unittest.TestCase):
    def test_infinite_when_fits(self):
        self.assertEqual(tab_labels.text_budget([10, 20, 30], 60), float("inf"))

    def test_water_filling(self):
        # 10 fits; the remaining 35px are shared by the two longer titles
        self.assertAlmostEqual(tab_labels.text_budget([10, 20, 30], 45), 17.5)
        # 10 and 20 fit; the last one gets what is left
        self.assertAlmostEqual(tab_labels.text_budget([10, 20, 30], 50), 20.0)
        # all three must shrink equally
        self.assertAlmostEqual(tab_labels.text_budget([30, 30, 30], 45), 15.0)

    def test_empty(self):
        self.assertEqual(tab_labels.text_budget([], 0), float("inf"))


if __name__ == "__main__":
    unittest.main()
