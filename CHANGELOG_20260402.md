Updated **TranzorExporter.exe** (v20260402)

What's new:

- 🗂️ **Tabbed Interface** — refactored to 3-tab layout: File Translation (legacy) · MR Pipeline · Quality Overview
- 📋 **MR Pipeline Tab** — browse, search, and export MR-triggered translation tasks by Project / Release / Status / Date; auto-loads the latest 20 tasks on open; click Refresh anytime
- 📊 **Quality Overview Tab** — aggregated quality dashboard with summary cards, bar & pie charts, per-language score table, and low-score highlights
- 🔍 **Filter & Export TMX in MR HTML reports** — same interactive filter bar and TMX export as File Translation reports: Select All, per-section checkboxes, Language / Score / String Key / Source / Translated text filters with AND/OR logic, regex, and positive/negative keywords
- ⏳ **Lazy loading** — MR Pipeline and Quality Overview data load only when their tab is first selected, preventing startup API overload
- 📐 **UI fixes** — window resized to 1280×1050; summary card height increased for full text visibility; canvas scroll width synced to prevent element truncation

> 💡 **Tip:** In the MR Pipeline HTML report, click 🔍 **Filters** to unlock powerful search — then ☑ **Select All** → 📦 **Export TMX** to batch-export translations.
