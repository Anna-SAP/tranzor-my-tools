Updated **TranzorExporter.exe** (v20260618)

What's new in 18 June update:

*The same Merge Request can be re-translated more than once — and each run can quietly rewrite translations differently. A new panel surfaces and diffs those repeat runs so you can catch the drift the source platform doesn't warn about.*

- 🧬 **NEW: Same Origin tab** — Finds Merge Requests whose **MR-pipeline translation task ran more than once** (Core products only) and groups the runs by **Project & MR#**. Inherits the **✏️ post-edit** marker from MR Pipeline so you can see at a glance which MRs a Language Lead also hand-fixed.
- 🔍 **Analyze Diff (per locale)** — One click pulls each run's **latest translation** for that MR and cross-compares them by **locale**, highlighting **word-level differences** between runs (🟥 removed in a later run · 🟩 added). Catches the classic "one source line removed → 18 locales re-polished" inconsistency. Identical runs report "no divergence"; runs that fail to fetch are excluded (not mis-flagged), with an honest partial-result notice.
- ⚙ **Configurable Core products** — The scanned product list ships with the 26 core `project_id`s and is **editable inside the panel** (one per line). Saved to `~/.tranzor_exporter/core_products.json`; delete it to fall back to defaults — `products.json` is never touched.
- 🧱 **Pure-additive & tested** — New logic lives in a standalone, unit-tested `same_origin` layer; the tab follows the existing optional-tab pattern, lazy-loads on first open, and never blocks the rest of the app if it fails to initialise.
