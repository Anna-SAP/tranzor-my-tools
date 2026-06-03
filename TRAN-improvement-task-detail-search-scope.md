# [Tranzor] Task-detail keyword search should let reviewers scope to translation content, not the Key path

| Field | Value |
|---|---|
| Environment | int (`tranzor-platform.int.rclabenv.com`) |
| Sample task | http://tranzor-platform.int.rclabenv.com/static/legacy/tasks/265 |
| Persona | Linguist reviewer (LQA / TQA) |
| Type | Improvement — search precision / review efficiency |
| Severity | Minor — no data or output impact; degrades precision on the reviewer's most frequent action |

## User story

As a **linguist reviewer**, when I type a word into the task-detail search box, I want it to search the **translatable content (Source / Translation)** by default, so that I can find the strings I actually need to review — instead of being flooded with rows that only match because the word happens to appear inside the structural **Key** path.

## Summary

On the legacy task-detail page, the Translation Results search box matches the typed term against the **Key (`opus_id`)** *and* **Unit ID** *and* **Source** *and* **Translation**, all OR-ed together. Tranzor OPUS keys embed UI-component names (e.g. `…AlertChannels_#@#_Email_#@#_Input_#@#_Cancel`), so a common review term like `alert` matches every string under that component — even rows whose visible content is `Cancel`, `Email`, `Add`, or an ICU plural with no relation to "alert". The reviewer has no way to restrict the search to content.

## Steps to reproduce

1. Open a legacy task with OPUS-style keys, e.g. task 265.
2. Pick any language tab (de-DE in the screenshot).
3. Type `alert` in the Search box.

## Actual

Rows whose **Source / Translation** are `Cancel` / `Email` / `Add` / `{count, plural, …}` are returned, because their **Key** contains `AlertChannels`. Only row 1 is a genuine content match.

| # | Key (`opus_id`) | Source | Matched on | Relevant to "alert"? |
|---|---|---|---|---|
| 1 | …**Alert**Settings_#@#_Subtitle | Choose who receives email **alert** notifications. | Key + Source | yes |
| 2 | …**Alert**Channels…_#@#_Cancel | Cancel | Key only | no |
| 3 | …**Alert**Channels…_#@#_label | Email | Key only | no |
| 4 | …**Alert**Channels…_#@#_Submit | Add | Key only | no |
| 5 | …**Alert**Channels…_#@#_RecipientsCount | {count, plural, one {# recipient} …} | Key only | no |

## Expected

Searching a word returns rows where that word appears in the **content the reviewer reads** (Source and/or Translation). Matching the Key path should be opt-in, not the silent default.

## Why it matters (reviewer impact)

- **Precision during LQA / TQA.** Searching a term to spot-check its translations is a core review action. Today, any common word that also appears in a component name returns a wall of irrelevant rows, and the reviewer must read each Key to discard it.
- **False sense of coverage.** A reviewer scanning the `alert` results may assume they have seen all alert-related copy, when the list is in fact padded with unrelated strings.
- **Unpredictable scope.** The box says "Search by key or text…", yet it also silently searches the **Translation** column and **Unit ID** — the reviewer cannot reason about why a given row matched.
- No data or output risk — purely a search-UX problem, but it taxes the highest-frequency reviewer action on the page.

## Root cause (for the dev team)

Server-side, in `app/core/legacy_task_repository.py` → `get_paginated_translations()` (≈ L521–528):

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

Endpoint: `GET /api/v1/legacy/tasks/{task_id}/translations?search=` (`app/api/routes/legacy_translate.py:651`). A single case-insensitive substring is OR-ed across four columns; there is no field scoping and no relevance ordering.

## Proposed improvement

Recommended (smallest change that removes the reviewer pain):

1. Add a **search-scope control** next to the box — a small dropdown / segmented chips: `Content (Source + Translation)` · `Key` · `All`.
2. **Default to `Content`.** Key search stays available for devs / LLs as an explicit choice.
3. Backend: thread a `search_fields` (or `search_scope`) param into `get_paginated_translations` and build the OR conditionally; default = `source_text` + `translated_text`.
4. Align the placeholder text and the API parameter description with the real behavior of each mode.

Alternatives (team's call, can be layered on later):

- **Field-prefix syntax** — `key:alert`, `src:alert`, `tgt:alert` for power users.
- **Match the Key leaf only** — match the segment after the last `.` / `_#@#_` rather than the whole path, so `alert` stops matching a key whose leaf is `Cancel`.
- **Relevance ordering** — rank content matches above key-only matches instead of hard-excluding the latter.

## Acceptance criteria

1. On the task-detail page, a default keyword search returns only rows whose **Source or Translation** contains the term.
2. Reviewers can still opt into **Key** search via a visible control.
3. The search-box placeholder and the API parameter description accurately state what is searched in each mode.
4. Reproduction case: searching `alert` on task 265 in `Content` mode returns row 1 (and any other genuine content hits) but **not** the `Cancel` / `Email` / `Add` rows.
5. No regression to the language / label / check-failure filters, pagination, or the global `/translations/search` page.

## Out of scope

- The cross-task global search page (`/translations/search`) — separate surface; can adopt the same pattern later.
- Fuzzy / typo-tolerant / stemming search — this ticket concerns *field scope* only.
- Any change to `opus_id` itself or to how keys are generated.

## Notes — documentation mismatch

The frontend placeholder ("Search by key or text…") and the API parameter description ("Search by opus_id or source text") both **understate** the real scope: the implementation also searches `unit_id` and `translated_text`. Worth correcting alongside this change.

## Attachments

- Screenshot of task 265, de-DE tab, search "alert" (rows 2–5 matched on the Key only).
