# [Tranzor] Task-detail keyword search is too broad on File Translation & Scan — align with MR Pipeline's per-field search

| Field | Value |
|---|---|
| Environment | int (`tranzor-platform.int.rclabenv.com`) |
| Affected channels | **File Translation (legacy)** and **Scan tasks** |
| Already correct | **MR Pipeline** (reference implementation — no change needed) |
| Sample task | http://tranzor-platform.int.rclabenv.com/static/legacy/tasks/265 |
| Persona | Linguist reviewer (LQA / TQA) |
| Type | Improvement — search precision / review efficiency |
| Severity | Minor — no data or output impact; degrades precision on the reviewer's most frequent action |

## User story

As a **linguist reviewer**, when I type a word into the task-detail search box, I want it to match the **translatable content (Source / Translation)** by default, so that I can find the strings I actually need to review — instead of being flooded with rows that only match because the word happens to appear inside the structural **Key** path (or a file path).

## Summary

Two of the three translation channels use a **single combined search box that OR-matches the term across the Key plus content** — so a common review term returns rows whose Source/Translation are unrelated, just because the term appears in the `opus_id` Key path. **The MR Pipeline channel already solves this** with separate per-field inputs, so the fix is simply to bring File Translation and Scan in line with the pattern that already ships in the same codebase.

This ticket was originally filed for File Translation only; after reviewing all three channels it now covers both affected surfaces so the team can fix them in one pass.

## Cross-channel comparison

| Channel | Search UI | Fields the term is matched against | Match semantics | Wildcards escaped | Scoped to content? |
|---|---|---|---|---|---|
| **File Translation (legacy)** | one combined box ("Search by key or text…") | `opus_id` (Key) · `unit_id` · `source_text` · `translated_text` | **OR** (one term, any field) | yes (`escape_like`) | ❌ no |
| **Scan tasks** | one combined box | `opus_id` (Key) · `source_text` · `translated_text` · **`source_file_path`** | **OR** (one term, any field) | **no** (raw `f"%{search}%"`) | ❌ no — even broader (also matches file path) |
| **MR Pipeline** | three separate inputs: Opus ID · Source text · Translation text | `opus_id`, `source_text`, `translated_text` — each its own field | **AND** (each field independent) | yes (`escape_like`) | ✅ yes |

Conclusion: **Scan has the same defect as File Translation** (and a slightly broader one, since it also ORs in `source_file_path`). **MR Pipeline does not** — it is the model to copy.

## Steps to reproduce (File Translation)

1. Open a legacy task with OPUS-style keys, e.g. task 265.
2. Pick any language tab (de-DE in the screenshot).
3. Type `alert` in the Search box.

**Actual:** rows whose Source/Translation are `Cancel` / `Email` / `Add` / `{count, plural, …}` are returned, because their **Key** contains `AlertChannels`. Only row 1 is a genuine content match.

| # | Key (`opus_id`) | Source | Matched on | Relevant to "alert"? |
|---|---|---|---|---|
| 1 | …**Alert**Settings_#@#_Subtitle | Choose who receives email **alert** notifications. | Key + Source | yes |
| 2 | …**Alert**Channels…_#@#_Cancel | Cancel | Key only | no |
| 3 | …**Alert**Channels…_#@#_label | Email | Key only | no |
| 4 | …**Alert**Channels…_#@#_Submit | Add | Key only | no |
| 5 | …**Alert**Channels…_#@#_RecipientsCount | {count, plural, one {# recipient} …} | Key only | no |

The same shape reproduces on a **Scan task** results page: searching a word that appears in a key or `source_file_path` returns content-unrelated rows.

## Expected

Searching a word returns rows where that word appears in the **content the reviewer reads** (Source and/or Translation). Matching the Key path or file path should be opt-in, not the silent default — exactly as MR Pipeline already behaves.

## Why it matters (reviewer impact)

- **Precision during LQA / TQA.** Spot-checking a term's translations is a core review action. On File Translation and Scan, any common word that also appears in a component name or file path returns a wall of irrelevant rows the reviewer must read each Key to discard.
- **Inconsistent product behavior.** The same reviewer gets clean, scoped search on MR Pipeline but noisy combined search on the other two channels — for no deliberate reason.
- **False sense of coverage.** A reviewer scanning the `alert` results may assume they have seen all alert-related copy, when the list is padded with unrelated strings.
- **Unpredictable scope.** The legacy box says "Search by key or text…" yet also silently searches `translated_text` and `unit_id`; Scan additionally searches `source_file_path`. The reviewer cannot reason about why a row matched.
- No data or output risk — purely a search-UX problem, but it taxes the highest-frequency reviewer action on these pages.

## Root cause (for the dev team)

**File Translation (legacy)** — `app/core/legacy_task_repository.py` → `get_paginated_translations()` (≈ L521–528). Endpoint `GET /api/v1/legacy/tasks/{task_id}/translations?search=` (`legacy_translate.py:651`):

```python
if search:
    like_term = f"%{escape_like(search)}%"
    filter_conditions.append(
        (LegacySource.opus_id.ilike(like_term)) |             # Key
        (LegacySource.unit_id.ilike(like_term)) |             # Unit ID
        (LegacySource.source_text.ilike(like_term)) |         # Source
        (LegacyTranslation.translated_text.ilike(like_term))  # Translation
    )
```

**Scan tasks** — `app/api/routes/missing_translation_scan.py` → `get_scan_results()` (≈ L328–337). Endpoint `GET /missing_translation_scan/tasks/{task_id}/results?search=`:

```python
if search:
    search_pattern = f"%{search}%"           # ⚠ note: no escape_like() here
    query = query.where(
        or_(
            Translation.opus_id.ilike(search_pattern),          # Key
            Translation.source_text.ilike(search_pattern),      # Source
            Translation.translated_text.ilike(search_pattern),  # Translation
            Translation.source_file_path.ilike(search_pattern), # File path
        )
    )
```

**MR Pipeline (already correct)** — `app/api/routes/dashboard.py`, endpoints `/mrs` (L1798), `/mr-cases` (L1929), `/cases` (L2019); each takes **separate** params and applies them independently (≈ L1867–1871):

```python
if opus_id:
    trans_stmt = trans_stmt.where(Translation.opus_id.ilike(f"%{escape_like(opus_id)}%"))
if source_text:
    trans_stmt = trans_stmt.where(Translation.source_text.ilike(f"%{escape_like(source_text)}%"))
if translated_text:
    trans_stmt = trans_stmt.where(Translation.translated_text.ilike(f"%{escape_like(translated_text)}%"))
```

Frontend: MR Pipeline renders three inputs in `dashboard/src/components/FilterCard.tsx` (Opus ID / Source text / Translation text). The global cross-task page `app/api/routes/legacy_translation_search.py` is also already per-field — so the single-combined-OR-box pattern on File Translation + Scan are the only two outliers in the codebase.

## Proposed improvement

**Recommended — adopt the pattern MR Pipeline already uses**, so all three channels behave consistently and nothing has to be invented:

1. On the File Translation and Scan task-detail pages, replace the single combined box with **separate, independently-scoped inputs** — minimally **Source** and **Translation**, with **Key** (and Scan's file path) as additional optional fields — mirroring `FilterCard.tsx`.
2. Backend: split the single `search` OR into per-field conditions (or accept a `search_fields` / `search_scope` param), defaulting to content (`source_text` + `translated_text`).
3. Add `escape_like()` to the Scan search so wildcards in user input are treated literally — bringing it in line with the other two channels (see "Secondary defect").

If a single box is preferred for these pages to save space, an acceptable alternative is **one box + a scope selector** (`Content (Source + Translation)` · `Key` · `All`) defaulting to Content. Either way, the default must be content-only.

Smaller, layerable options (team's call): field-prefix syntax (`src:`, `key:`); match only the Key *leaf* (after the last `.` / `_#@#_`); relevance ordering that ranks content matches above key-only matches.

## Secondary defect — Scan search does not escape LIKE wildcards

`missing_translation_scan.py` builds `search_pattern = f"%{search}%"` **without** `escape_like()`, unlike legacy and MR Pipeline. A reviewer typing `%` or `_` gets wildcard behavior instead of a literal match. Low impact (SQLAlchemy still binds the pattern as a parameter, so it is not SQL injection), but it is an inconsistency worth fixing in the same pass.

## Acceptance criteria

1. On **File Translation** and **Scan** task-detail pages, a default keyword search returns only rows whose **Source or Translation** contains the term.
2. Reviewers can still opt into **Key** (and, for Scan, **file path**) search via a visible, separate control.
3. Reproduction case: searching `alert` on task 265 in content scope returns row 1 (and any other genuine content hits) but **not** the `Cancel` / `Email` / `Add` rows. Equivalent check passes on a Scan task.
4. Scan search escapes LIKE wildcards (`%`, `_`, `\`) via `escape_like()`.
5. Placeholders and API parameter descriptions accurately state what is searched in each mode.
6. **MR Pipeline** search is unchanged. No regression to language / label / check-failure filters, pagination, or the global `/translations/search` page.

## Out of scope

- **MR Pipeline** behavior — already correct; it is the reference, not a target of change.
- The cross-task global search page (`/translations/search`) — already per-field.
- Fuzzy / typo-tolerant / stemming search — this ticket concerns *field scope* only.
- Any change to `opus_id` itself or to how keys are generated.

## Attachments

- Screenshot of task 265, de-DE tab, search "alert" (rows 2–5 matched on the Key only).
