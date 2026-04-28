# Terminology Watchtower Mockup Brief

## Purpose

Design a mockup for **Terminology Watchtower**, a new Phase 1 capability inside `my-tools`, the user's personal companion toolkit for Tranzor Platform.

The mockup should show a practical, daily-use quality control workspace that helps a heavy Tranzor user detect and act on terminology compliance problems before they silently reach downstream localization work.

This is not a marketing page. Design the actual working application screen.

## Product Context

Tranzor Platform is an internal LLM-based translation platform used for daily localization across multiple target locales. It is intended to reduce reliance on external LSP workflows.

However, recent translation work has shown that terminology adherence is still unreliable. A concrete example is the term **"AI receptionist"**, which has fixed approved translations per locale, yet Tranzor outputs sometimes still use non-standard or inconsistent translations.

Because Tranzor is a team-owned platform with a longer development cycle, the user has independently built `my-tools` as a personal sidecar toolkit to fill urgent workflow gaps.

Existing `my-tools` capabilities include:

- File Translation export
- MR Pipeline export
- Quality Overview
- Full Translation export
- Human Revisions overview
- Missing Translation Scan tasks

The new feature should fit naturally into this existing desktop workflow as another operational tab.

## Core Problem

The user needs a reliable way to answer:

- Which approved terminology was not followed?
- Which locales and products are affected?
- What did Tranzor output?
- What should the approved translation have been?
- Where did the issue come from: File Translation, MR Pipeline, Full Translation export, or Scan Tasks?
- Can the user quickly export evidence to share with the Tranzor owner development team or localization stakeholders?

The current quality signals are too generic. A score or a broad "Terminology" error category is not enough. The user needs term-level, locale-level, evidence-backed visibility.

## Primary User

The primary user is a localization/product owner who uses Tranzor heavily every day.

They are not trying to rebuild Tranzor. They need a practical personal QA layer that helps them catch terminology risk early, inspect issues quickly, and produce clear evidence when platform-level improvements are needed.

## User Story

As a daily heavy user of Tranzor,
I want a Terminology Watchtower inside `my-tools`,
so that I can automatically detect when Tranzor translations fail to follow approved terminology,
understand the affected locales and tasks,
and export clear evidence for review or follow-up.

## Mockup Goal

Create a polished desktop-app mockup for a new tab named:

**Terminology Watchtower**

The design should feel like an internal operations dashboard: clear, dense, calm, and built for repeated daily use.

Avoid:

- Landing-page style hero sections
- Decorative gradients or abstract illustrations
- Overly large cards that reduce scanability
- AI-chat-first design
- Complex admin configuration as the first screen

Prefer:

- A compact dashboard summary
- Filterable issue table
- Strong actual-vs-expected comparison
- Clear issue severity and status
- Evidence export workflow
- A secondary glossary/rules management view

## Key UX Requirements

### 1. Terminology Risk Summary

At the top of the tab, show a concise summary of the current scan:

- Total terminology issues
- Critical issues
- Affected terms
- Affected locales
- Affected products/projects
- Last scan time
- Scan source coverage: Full Translations, MR Pipeline, File Translation, Scan Tasks

The user should immediately know whether today's translation output is safe enough to trust.

### 2. Main Issues Table

The central area should be a dense, filterable table of terminology issues.

Suggested columns:

- Severity
- Term
- Locale
- Expected translation
- Actual translation
- Source text
- Product/project
- Workflow source
- Task/MR/reference
- Status
- Last seen

Important table behaviors:

- Search by term, source text, locale, product, or actual translation
- Filter by severity, locale, product, source, and status
- Sort by severity, recency, term, or locale
- Highlight actual-vs-expected mismatch clearly

### 3. Issue Detail Panel

Selecting an issue should open a right-side detail panel or drawer.

The detail panel should show:

- Source text with the approved source term highlighted
- Actual translation with the problematic phrase highlighted
- Expected approved translation
- Locale and product context
- Workflow origin
- Score or quality category if available
- Related human revision if available
- Suggested next action

Suggested actions:

- Mark as reviewed
- Mark as reported
- Ignore this instance
- Export this issue as evidence
- Copy issue summary

Do not design direct write-back or automatic retranslation as the primary action for Phase 1.

### 4. Terminology Rules / Glossary View

Include a secondary view or tab within Terminology Watchtower for the approved terminology list.

It should show:

- Source term
- Locale
- Approved translation
- Forbidden variants, if any
- Product or domain scope
- Notes
- Last updated

Suggested actions:

- Import glossary
- Add term
- Edit term
- Disable term
- Export glossary

This view should be present, but it should not dominate the first screen. The main daily workflow is issue detection and review.

### 5. Evidence Export

Design an evidence export interaction.

The user should be able to export:

- Selected issues
- All critical issues
- Current filtered results

The exported evidence should be suitable for sharing with the Tranzor owner development team.

The mockup can show an export modal with options:

- HTML report
- Excel report
- Markdown summary
- Include source text
- Include expected vs actual translations
- Include task/MR references

### 6. Empty, Loading, and Success States

Include realistic operational states:

- No glossary imported yet
- Glossary imported, no issues found
- Scan running
- Scan completed with issues
- Some sources unavailable

The "no issues found" state should feel reassuring but still show scan coverage.

## Suggested Layout

### Primary Screen: Terminology Watchtower Dashboard

Recommended structure:

1. Header row
   - Title: Terminology Watchtower
   - Last scan timestamp
   - Actions: Scan Now, Import Glossary, Export Evidence

2. Risk summary strip
   - Total issues
   - Critical
   - Affected locales
   - Affected terms
   - Scan coverage

3. Filter bar
   - Search
   - Locale
   - Product
   - Source
   - Severity
   - Status

4. Main content
   - Left/center: issue table
   - Right: selected issue detail panel

5. Secondary navigation inside the tab
   - Issues
   - Glossary
   - Reports

### Secondary Screen: Glossary / Rules

Show a compact terminology management table with import/export actions.

### Optional Screen: Evidence Report Preview

Show what the exported report will look like: summary metrics, issue list, and selected detailed evidence.

## Example Mock Data

Use realistic mock data based on the term **"AI receptionist"**.

Approved terminology examples:

| Locale | Source Term | Approved Translation |
| --- | --- | --- |
| en_GB | AI receptionist | AI Receptionist |
| de_DE | AI receptionist | KI-Telefonzentrale |
| es_ES | AI receptionist | Recepcionista con IA |
| es_419 | AI receptionist | Recepcionista con IA |
| fr_CA | AI receptionist | Réceptionniste IA |
| zh_CN | AI receptionist | AI 接待员 |
| zh_TW | AI receptionist | AI 接待員 |

Example issues for mockup only:

| Severity | Locale | Expected | Actual | Product | Source |
| --- | --- | --- | --- | --- | --- |
| Critical | fr_FR | Réceptionniste IA | Standard IA | Voice/AI | MR Pipeline |
| High | de_DE | KI-Telefonzentrale | KI-Rezeptionistin | Voice/AI | Full Translations |
| High | zh_CN | AI 接待员 | AI 前台 | Voice/AI | File Translation |
| Medium | es_ES | Recepcionista con IA | Recepcionista IA | Voice/AI | Scan Tasks |

These sample values are for UI mockup only. They should not be treated as the final approved glossary.

## Visual Direction

Design language:

- Internal productivity tool
- Quiet but sharp
- High information density
- Clear status colors
- Professional table-first layout

Color guidance:

- Use severity colors sparingly: red for critical, amber for warning, green for clean/pass
- Keep the base UI neutral
- Avoid a single-hue purple/blue dashboard look

Interaction guidance:

- Buttons should be action-oriented and compact
- Use icons for scan, import, export, filter, copy, and status where helpful
- Use badges for severity and issue status
- Use side panel or drawer for issue details

## Out of Scope for Phase 1 Mockup

Do not center the design around:

- Automatic retranslation
- Direct write-back to Tranzor
- AI-generated diagnosis as the main workflow
- Complex rule builder
- Team permissions or role management
- Full platform administration

Phase 1 should stay focused on terminology detection, inspection, and evidence export.

## Success Criteria for the Mockup

The mockup is successful if a viewer can understand within 30 seconds:

- This tool detects terminology compliance failures.
- The main user can see which terms/locales are risky today.
- Each issue clearly shows expected vs actual translation.
- The user can inspect evidence without leaving the screen.
- The user can export a report for follow-up.
- The workflow complements Tranzor instead of replacing it.

## Deliverables Requested from Design

Please produce:

1. A high-fidelity desktop mockup of the main Terminology Watchtower tab.
2. A secondary glossary/rules view.
3. An evidence export modal or report preview.
4. Realistic sample data using the "AI receptionist" terminology issue.
5. A design that can be implemented incrementally in the existing `my-tools` desktop app.
