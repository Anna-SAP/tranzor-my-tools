# PRD: Term Watchtower

Version: 1.0  
Status: Ready for implementation  
Date: 2026-04-28  
Repository scope: `C:\Users\susu82\Tranzor-Platform\my-tools` only  
Feature display name: **Term Watchtower**  
Internal feature name: **Terminology Watchtower**  

## 1. Background

`my-tools` is a personal sidecar toolkit for Tranzor Platform. It exists because Tranzor is a team-owned platform with a longer development cycle, while the user needs immediate daily safeguards for localization quality.

The most urgent quality gap is terminology adherence. Recent Tranzor output has shown that approved terms are not always followed. A concrete example is **"AI receptionist"**, which has fixed approved translations per locale, yet Tranzor can still output non-standard variants.

Existing `my-tools` already provides exports and quality visibility:

- File Translation export
- MR Pipeline export
- Quality Overview
- Full Translation export
- Human Revisions overview
- Missing Translation Scan tasks

Phase 1 adds **Term Watchtower**, a read-only terminology compliance workspace that detects approved terminology violations, shows actionable evidence, and exports reports for follow-up.

## 2. Product Goal

Term Watchtower must help the user answer these questions quickly:

1. Which approved terms were not followed?
2. Which locales, products, workflows, and translation items are affected?
3. What did Tranzor output?
4. What approved translation should have been used?
5. Is there enough evidence to report the issue to the Tranzor owner development team or localization stakeholders?

The feature must focus on deterministic terminology checking. It must not rely on LLM judgment for Phase 1.

## 3. Non-Goals

Phase 1 must not implement:

- Direct write-back to Tranzor
- Automatic retranslation
- Automatic fixing of translations
- Team permission management
- AI diagnosis as a required workflow
- A full custom rule engine
- SQLite-based historical trend analysis
- Scheduled background monitoring
- Changes outside the `my-tools` repository

These may be considered in later phases.

## 4. Success Criteria

Phase 1 is successful when:

- The user can import an approved terminology list.
- The user can scan Tranzor translation data from existing `my-tools` sources.
- The feature flags terminology violations with expected vs actual translations.
- Each issue includes enough context to locate the affected product, locale, key/task/source, and workflow.
- The user can filter, inspect, mark, and export issues.
- The feature remains read-only against Tranzor.
- The app remains usable even when no glossary has been imported, no issues are found, or one Tranzor source fails.

## 5. Target User

Primary user:

- A daily heavy user of Tranzor.
- Responsible for localization quality and terminology consistency.
- Needs a personal QA layer while waiting for platform-level quality gates.
- Needs evidence that can be shared with Tranzor owners, developers, or localization stakeholders.

## 6. Phase 1 Scope

### In Scope

Phase 1 must include:

- A new desktop GUI tab named **Term Watchtower**.
- Import and validation of a local terminology glossary.
- A deterministic terminology scan over Tranzor translation data available through `my-tools`.
- A terminology issue table.
- A selected issue detail panel.
- Basic issue status tracking.
- HTML and Excel evidence export.
- Empty, loading, partial failure, and success states.

### Data Sources

Phase 1 must support scanning translation data collected through the existing full translation export path.

Mandatory sources:

- Legacy/File Translation data exposed through existing full translation collection logic.
- MR Pipeline data exposed through existing full translation collection logic.
- Missing Translation Scan data exposed through existing full translation collection logic.

The implementation should reuse existing read-only collection functions where practical, especially the full translation inventory/collection path. It must not call heavy translation endpoints during app startup or tab creation.

Optional source metadata, if already available from existing functions:

- Task ID
- MR ID
- Project/product name
- Source workflow
- Quality score
- Error category
- Human revision context

The scan must still work if optional metadata is unavailable.

## 7. Functional Requirements

### FR-1: New GUI Tab

Add a new tab to the main `my-tools` GUI.

Required tab label:

```text
Term Watchtower
```

The tab must follow the existing Tkinter/ttk desktop style and must not require a browser or web server.

The tab must be lazy-loaded:

- It may load saved local glossary/status files when opened.
- It must not fetch full translation text until the user clicks **Scan Now**.
- It must show progress while scanning.

### FR-2: Glossary Import

The user must be able to import a terminology glossary from CSV.

CSV encoding:

- UTF-8
- UTF-8 with BOM must also be accepted.

Required CSV columns:

```text
source_term,locale,approved_translation
```

Optional CSV columns:

```text
rule_id,source_aliases,forbidden_translations,product_scope,severity,case_sensitive,enabled,notes
```

List fields must use the pipe character `|` as the separator:

```text
AI receptionist|AI Receptionist
```

Boolean fields:

- Accepted true values: `true`, `1`, `yes`, `y`
- Accepted false values: `false`, `0`, `no`, `n`, empty

If `enabled` is empty, default to `true`.

If `severity` is empty, default to `High`.

Supported severities:

```text
Critical, High, Medium, Low
```

Invalid rows must not crash the app. The import result must show:

- Total rows
- Imported valid rules
- Skipped invalid rows
- Validation errors with row numbers

The user must be able to export a blank CSV template.

### FR-3: Glossary Persistence

After successful import, the glossary must be persisted locally so it remains available after restarting the app.

Use this local storage directory:

```text
~/.tranzor_exporter/terminology_watchtower/
```

Required local files:

```text
glossary.csv
issue_statuses.json
last_scan.json
```

Do not store Tranzor credentials in these files.

### FR-4: Term Rule Model

Each imported glossary row becomes a `TermRule`.

Required fields:

```text
rule_id: string
source_term: string
locale: string
approved_translation: string
```

Optional fields:

```text
source_aliases: list[string]
forbidden_translations: list[string]
product_scope: list[string]
severity: Critical|High|Medium|Low
case_sensitive: boolean
enabled: boolean
notes: string
```

Rules:

- If `rule_id` is provided, use it.
- If `rule_id` is empty, generate a stable ID from normalized `source_term`, normalized `locale`, `approved_translation`, and `product_scope`.
- `locale` must be normalized for matching by lowercasing and replacing `-` with `_`.
- Display may preserve the original locale style if available.
- Empty `product_scope` means the rule applies to all products.
- Disabled rules must be visible in the glossary view but ignored by scans.

### FR-5: Translation Candidate Model

The scanner operates on normalized `TranslationCandidate` records.

Required fields:

```text
candidate_id: string
product: string
locale: string
key: string
source_text: string
target_text: string
source_kind: legacy|mr|scan|file_translation|unknown
```

Optional fields:

```text
task_id: string
mr_id: string
score: number
error_category: string
reference_url: string
raw: object
```

If a source record lacks optional metadata, the UI must show `-` instead of failing.

### FR-6: Source Text Resolution

Term Watchtower must only apply a term rule when the source text contains the source term or one of its aliases.

Source text resolution order:

1. Use an explicit `source_text` field if provided by the collected data.
2. Otherwise use an English reference value for the same product/key if available.
3. Preferred English reference locales, in order:

```text
en_US, en-US, en_GB, en-GB, en
```

4. If no source text can be resolved, skip that candidate and count it as `skipped_missing_source`.

The scanner must not treat the target text as source text.

### FR-7: Text Normalization

For matching, normalize text as follows:

- Convert `None` to empty string.
- Trim leading/trailing whitespace.
- Collapse repeated whitespace into a single space.
- For case-insensitive rules, use Unicode casefold.
- Do not remove accents or diacritics.
- Do not strip punctuation.
- Do not translate or stem terms.

Default matching is case-insensitive.

If `case_sensitive` is true, do not casefold the source term, aliases, approved translation, or forbidden translations for that rule.

### FR-8: Source Term Match

A rule is applicable to a candidate only if all are true:

1. The rule is enabled.
2. Candidate locale matches the rule locale after locale normalization.
3. Candidate product is within `product_scope`, or `product_scope` is empty.
4. Candidate source text contains `source_term` or at least one `source_alias`.

Containment match is acceptable for Phase 1.

### FR-9: Target Translation Match

For an applicable rule:

- `approved_translation` is considered present if the normalized target text contains the normalized approved translation.
- Each forbidden translation is considered present if the normalized target text contains the normalized forbidden translation.
- Empty target text is a violation when the source text contains the rule source term.

Do not use fuzzy matching in Phase 1.

### FR-10: Issue Types

The scanner must generate terminology issues using these issue types:

```text
RequiredTermMissing
ForbiddenVariantUsed
MissingTargetForTerm
```

Definitions:

- `RequiredTermMissing`: source term is present, but target text does not contain the approved translation.
- `ForbiddenVariantUsed`: target text contains at least one forbidden translation.
- `MissingTargetForTerm`: source term is present, but target text is empty.

If both `RequiredTermMissing` and `ForbiddenVariantUsed` apply to the same candidate/rule, create one issue with type:

```text
RequiredTermMissing+ForbiddenVariantUsed
```

### FR-11: Severity Calculation

Severity order:

```text
Critical > High > Medium > Low
```

Severity rules:

- If issue type is `RequiredTermMissing+ForbiddenVariantUsed`, severity is `Critical`.
- If issue type is `MissingTargetForTerm`, severity is the higher of rule severity and `High`.
- If issue type is `RequiredTermMissing`, severity is the rule severity.
- If issue type is `ForbiddenVariantUsed`, severity is the higher of rule severity and `Medium`.

### FR-12: Issue Identity

Each issue must have a stable `issue_id`.

Generate `issue_id` from:

```text
rule_id + candidate_id + issue_type
```

Do not include current target text in `issue_id`. This allows user status to persist if the text changes but the same rule/candidate remains problematic.

### FR-13: Issue Status

Each issue has one user-managed status.

Supported statuses:

```text
New
Reviewed
Reported
Ignored
```

Default status:

```text
New
```

Statuses must persist in `issue_statuses.json` by `issue_id`.

If an issue disappears in a later scan, keep its status in the JSON file but do not show it in the active issue list. Future phases may add resolved history.

### FR-14: Scan Summary

After each scan, show summary metrics:

- Total active issues
- Critical issues
- High issues
- Affected terms
- Affected locales
- Affected products
- Scanned candidates
- Skipped candidates due to missing source
- Skipped candidates due to missing glossary match
- Failed sources, if any
- Last scan timestamp

The summary must be persisted in `last_scan.json`.

### FR-15: Main Issues UI

The main screen must include:

- Header with title, last scan timestamp, and primary actions.
- Summary metrics.
- Filter bar.
- Issues table.
- Issue detail panel.

Required primary actions:

```text
Scan Now
Import Glossary
Export Evidence
```

Required filters:

```text
search
severity
locale
product
source_kind
status
```

Search must match:

- Source term
- Source text
- Approved translation
- Actual translation
- Product
- Key

Required table columns:

```text
Severity
Status
Term
Locale
Expected
Actual
Product
Source
Key/Reference
Issue Type
```

The table must remain usable with hundreds of rows. Avoid oversized cards for individual issues.

### FR-16: Issue Detail Panel

Selecting an issue must show a detail panel.

Required fields:

- Issue ID
- Severity
- Status
- Issue type
- Source term
- Locale
- Product
- Source kind
- Key/reference
- Source text
- Actual translation
- Expected approved translation
- Forbidden variants found, if any
- Rule notes, if any
- Score/error category, if available

Required actions:

```text
Mark Reviewed
Mark Reported
Ignore
Copy Summary
Export This Issue
```

`Copy Summary` must copy a concise plain-text issue summary to the clipboard.

### FR-17: Glossary View

The tab must include a glossary/rules view.

Required columns:

```text
Enabled
Rule ID
Source Term
Locale
Approved Translation
Forbidden Translations
Product Scope
Severity
Notes
```

Required actions:

```text
Import Glossary
Export Current Glossary
Export Template
```

Editing individual rows in the GUI is optional for Phase 1. If editing is not implemented, the UI must make it clear that glossary changes are made by importing an updated CSV.

### FR-18: Evidence Export

The user must be able to export:

- Selected issue
- Selected issues
- All active issues
- Current filtered issues
- All Critical and High issues

Mandatory export formats:

```text
HTML
Excel (.xlsx)
```

Optional export format:

```text
Markdown
```

Exported report must include:

- Report title
- Export timestamp
- Glossary file timestamp if available
- Scan timestamp
- Summary metrics
- Filters applied, if exporting filtered issues
- Issue table
- Detailed issue evidence

Each exported issue must include:

- Severity
- Status
- Issue type
- Source term
- Locale
- Product
- Source kind
- Key/reference
- Source text
- Actual translation
- Expected translation
- Forbidden variants found
- Rule notes

### FR-19: Empty and Error States

Required states:

1. No glossary imported
   - Show a clear empty state.
   - Primary action: Import Glossary.
   - Secondary action: Export Template.

2. Glossary imported, no scan yet
   - Show glossary summary.
   - Primary action: Scan Now.

3. Scan running
   - Show progress text and indeterminate or determinate progress where available.
   - Disable Scan Now while running.

4. Scan completed, no issues
   - Show success state.
   - Show scan coverage and skipped counts.

5. Scan completed with issues
   - Show issue table and summary.

6. Partial source failure
   - Show available results.
   - Show failed source names and error messages.
   - Do not discard successful source results.

7. Invalid glossary import
   - Show row-level validation errors.
   - Do not overwrite the existing saved glossary unless at least one valid rule is imported and the user confirms.

### FR-20: Read-Only Safety

Term Watchtower must not:

- Modify Tranzor data.
- Trigger retranslation.
- Change MR/task status.
- Commit or modify files in the parent Tranzor repository.

All Tranzor interactions must be read-only API calls already consistent with current `my-tools` behavior.

## 8. Example Glossary CSV

The implementation must support a CSV like this:

```csv
source_term,locale,approved_translation,source_aliases,forbidden_translations,product_scope,severity,case_sensitive,enabled,notes
AI receptionist,en_GB,AI Receptionist,AI receptionist|AI Receptionist,,Voice|AI,High,false,true,Approved product term
AI receptionist,de_DE,KI-Telefonzentrale,AI receptionist,KI-Rezeptionistin|AI Empfangskraft,Voice|AI,High,false,true,Approved product term
AI receptionist,es_ES,Recepcionista con IA,AI receptionist,Recepcionista IA,Voice|AI,High,false,true,Approved product term
AI receptionist,es_419,Recepcionista con IA,AI receptionist,Recepcionista IA,Voice|AI,High,false,true,Approved product term
AI receptionist,fr_CA,Réceptionniste IA,AI receptionist,Standard IA,Voice|AI,Critical,false,true,Approved product term
AI receptionist,zh_CN,AI 接待员,AI receptionist,AI 前台,Voice|AI,High,false,true,Approved product term
AI receptionist,zh_TW,AI 接待員,AI receptionist,AI 前台,Voice|AI,High,false,true,Approved product term
```

These values are examples for implementation/testing. The real glossary remains user-owned.

## 9. Recommended Implementation Shape

This section is implementation guidance, not a visual design requirement.

Recommended new files:

```text
terminology_watchtower.py
gui_tab_term_watchtower.py
```

Recommended responsibilities:

`terminology_watchtower.py`

- Glossary CSV loading and validation
- Text and locale normalization
- Translation candidate conversion
- Term rule matching
- Issue generation
- Status persistence
- HTML/Excel export helpers

`gui_tab_term_watchtower.py`

- Tkinter tab layout
- Scan controls
- Glossary import/export actions
- Issue table
- Detail panel
- Filter/search behavior
- Export dialogs

Existing file integration:

- `export_gui.py` should import and register the new tab following the optional-tab pattern already used by Full Translations, Human Revisions, and Scan Tasks.
- Reuse existing full translation collection code where possible.
- Avoid adding heavy third-party dependencies.

## 10. Performance and UX Requirements

The app must remain responsive during scans.

Minimum expectations:

- Run scan work in a background thread or equivalent non-blocking pattern consistent with existing tabs.
- Show progress text while fetching/scanning.
- Allow scan cancellation only if simple to implement; cancellation is optional for Phase 1.
- Do not fetch heavy translation data on app startup.
- Do not block the whole GUI while writing export files.

Expected Phase 1 scale:

- Hundreds to low tens of thousands of translation candidates.
- Dozens to hundreds of term rules.

The scanner should avoid unnecessary nested work where practical. A simple implementation is acceptable if it remains responsive at the expected scale.

## 11. Accessibility and Localization

The UI should support the existing English/Chinese language switch pattern if practical.

At minimum:

- English UI strings must be complete.
- Chinese UI strings should be added for visible primary controls if the current app language map is updated.
- Table content must preserve Unicode text, including accents and CJK characters.
- Exported HTML must declare UTF-8.

## 12. Acceptance Criteria

### AC-1: New Tab

Given the user opens `my-tools`, when the main GUI loads, then a **Term Watchtower** tab is available and does not perform a heavy scan on startup.

### AC-2: No Glossary State

Given no glossary has been imported, when the user opens Term Watchtower, then the UI shows a clear empty state with Import Glossary and Export Template actions.

### AC-3: Glossary Import

Given the user imports a valid UTF-8 CSV with the required columns, when import completes, then valid rules are persisted locally and visible in the glossary view.

### AC-4: Invalid Glossary Rows

Given the CSV contains invalid rows, when import completes, then row-level validation errors are shown and valid rows are still importable.

### AC-5: Term Scan

Given a glossary rule for `AI receptionist` and translations containing that source term, when the user runs Scan Now, then candidates whose target text does not contain the approved translation are listed as terminology issues.

### AC-6: Locale Normalization

Given a rule locale `zh_CN`, when a candidate locale is `zh-CN`, then the rule applies.

### AC-7: Accent Preservation

Given the approved translation is `Réceptionniste IA`, when the actual translation is `Receptionniste IA`, then Phase 1 treats this as missing the approved translation because accents are not stripped.

### AC-8: Issue Status

Given an issue is marked Reported, when the app restarts and the same issue appears again, then the issue status remains Reported.

### AC-9: Filtering

Given multiple issues exist, when the user filters by severity, locale, product, source, status, or search text, then the table updates to show only matching issues.

### AC-10: Detail Panel

Given the user selects an issue, then the detail panel shows source text, actual translation, expected translation, issue type, severity, locale, product, and source kind.

### AC-11: Evidence Export

Given active issues exist, when the user exports HTML or Excel evidence, then the report contains summary metrics and issue-level expected-vs-actual evidence.

### AC-12: Partial Source Failure

Given one Tranzor data source fails during scan, when other sources succeed, then the UI shows successful results and displays a partial failure warning.

### AC-13: Read-Only Safety

Given the user runs scans and exports, then no Tranzor data is modified and no parent repository files are changed.

## 13. Test Plan

Add tests for pure logic where possible.

Required test areas:

- CSV import with UTF-8 BOM.
- Missing required columns.
- Invalid severity.
- Locale normalization with `_` and `-`.
- Case-insensitive source matching.
- Case-sensitive rule behavior.
- Approved translation containment.
- Forbidden translation detection.
- Missing target detection.
- Accent preservation.
- Product scope matching.
- Disabled rule ignored by scanner.
- Stable issue ID generation.
- Status persistence by issue ID.
- HTML export includes UTF-8 and key issue fields.
- Excel export writes expected columns.

GUI manual verification:

- Tab loads without scan.
- Import glossary flow.
- Scan progress.
- Table filtering.
- Detail panel actions.
- HTML and Excel export.
- Empty/no issues/partial failure states.

## 14. Future Phases

Later phases may add:

- SQLite history and trend analysis.
- Daily risk inbox.
- Scheduled scan.
- Human revision learning set.
- Suggested glossary additions from repeated corrections.
- AI-generated issue summaries.
- Team-shareable reports.
- Optional Tranzor write-back if safe platform APIs become available.

None of these are required for Phase 1.
