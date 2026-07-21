Updated **TranzorExporter.exe** (v20260721)

What's new in 21 July update:

- 🔍 **Searchable Project dropdown (MR Pipeline)** — the Project filter no longer opens the native scroll-only list. Clicking it (or pressing ↓ / space) now pops a dark-themed panel with a **search box pinned at the top**: type any keyword (`fiji`, `corelib`, `voice`, …) and the project list below live-filters with case-insensitive substring matching — no more scrolling through a hundred long repo paths like `CoreLib/RoomsController` to find one entry.
- ⌨️ **Full keyboard flow** — the search box is auto-focused on open; ↑/↓ move the highlight while you keep typing, Enter picks the highlighted project, Esc (or clicking anywhere outside) closes. Reopening pre-selects the current choice; the blank "no filter" entry shows as a proper *(All)* row instead of an empty line, and an explicit *(no match)* hint appears when nothing fits.
- 🧩 **Drop-in component** — shipped as a reusable `searchable_combobox` module (same zero-dependency dark-popup pattern as the 📅 date picker): options are re-read from the combobox on every open so the async filter load needs no extra wiring, and the standard `<<ComboboxSelected>>` event still fires for downstream code. Localized en/zh, registered in both Windows and macOS PyInstaller specs.
