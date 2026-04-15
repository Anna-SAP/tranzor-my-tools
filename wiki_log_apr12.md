# Tranzor Exporter — Wiki Log (since Apr 7, 2026)

---

## 📅 12 Apr 2026

**What's new since the Apr 9 update:**

- 📊 **Export result dialog** — every export action now opens a modal that morphs from a live progress log into a structured result report: output path, totals, per-product / per-language Key counts, plus 📂 *Reveal in Explorer* and 📄 *Open file* shortcuts.
- 🔎 **Selected-only filter** — new checkbox next to the Products *Filter* input lets you instantly see exactly which products are checked across a long list — a fast double-check before exporting.
- 🎯 **Unified Keys(en-US) metric** — all three exports (Export Selected / Export All / Merge to JSON) now report the same per-product Key count (distinct en-US source keys), so the numbers always agree no matter which action you took.

---

## 📅 11 Apr 2026

**What's new since the Apr 9 update:**

- 🧩 **Merge to JSON** — brand new top-bar button that merges the selected products × languages full translations into a single flat JSON file, ready for downstream translation QA and global search.
- 🔎 **Product Filter input** — type a keyword above the Products list to instantly narrow it down; check state is preserved while filtering.
- ☑️ **Checkbox multi-select** — Products and Languages lists now use ☐/☑ checkboxes, so bulk picking no longer requires Ctrl-click.
- 📦 **en-US source folder fix** — exported ZIPs now correctly include the en-US source folder when en-US is selected (previously skipped even when explicitly checked).
- ⚡ **Faster tab open** — Full Translations tab now loads a lightweight inventory on first show; heavy fetch only runs when you actually click Export.

---

## 📅 09 Apr 2026

**Updated TranzorExporter — new tab introduced 🎉**

- 🌍 **Full Translations tab** — brand new tab that exports the entire translation library in AP.zip-style structure (`<Product>/<locale>/trunk/opus_jsons/source.json`), pulling from both File Translation (Legacy) and MR Pipeline sources. Pick products + languages, then export.
