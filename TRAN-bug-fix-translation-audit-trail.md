# [Tranzor] Language Lead fixes leave no trace — who fixed what is lost

## Details

- **Type:** Bug
- **Priority:** High
- **Labels:** Tranzor
- **Components:** Dashboard API, Data Model

---

## Description

When a Language Lead fixes a translation via the Dashboard, the GitLab commit succeeds but the database fields `fixed_by_lead`, `fixed_at`, and `fixed_text` are frequently left as **NULL**. The GitLab commit is also authored by the Tranzor service account with no operator email in the commit message. This means **there is no surviving record — in either the database or source control — of who made the fix**.

**Real-world evidence:** `integration/uif` MR #1651 — commit `06f8f30` was pushed to GitLab on 2026-04-03 as a Language Lead batch fix, yet all translation records in the DB have `fixed_by_lead = NULL`.

---

## Why This Matters

For an AI/LLM translation platform, tracking human post-edits is not optional — it is a **HITL (Human-in-the-Loop) requirement**:

- **Post-Edit Rate metrics** — cannot be computed without editor attribution
- **Continuous improvement** — human corrections should feed back into prompt/glossary refinement, but they're invisible today
- **Accountability** — managers cannot attribute fixes to specific Language Leads
- **Downstream tools** — translation change reports (e.g., TranzorExporter) show blank "Fixed By" columns, undermining transparency

---

## Root Cause

Three defects in `app/api/routes/dashboard.py`:

**1. DB update fails silently after GitLab commit succeeds (line 1534 vs 1619)**

The GitLab commit (Step 8, line 1534) is irreversible. The DB update (Step 10, line 1619) happens later with no try/except and no retry. If the DB write fails, the GitLab commit is already pushed — `fixed_by_lead` stays NULL with no recovery mechanism.

**2. Commit message has no operator identity (line 1524)**

`user_email` is available from the JWT token (line 1320) but is not included in the commit message. Since the commit is made via `GITLAB_TOKEN` (service account), the actual Language Lead is untraceable in GitLab.

**3. `commit_sha` / `commit_branch` are not persisted (model gap)**

These values are returned in the HTTP response but never written to the `translations` table — lost after the request completes.

---

## Expected Behavior

After a fix-translation, the DB should reliably contain: `fixed_by_lead`, `fixed_at`, `fixed_text`, `commit_sha`, `commit_branch`. The GitLab commit message should include the operator's email.

## Acceptance Criteria

1. `fixed_by_lead`, `fixed_at`, `fixed_text` are **reliably non-NULL** after every fix
2. `commit_sha` and `commit_branch` are persisted in the `translations` table
3. GitLab commit message includes the operator's email
4. DB failure after GitLab commit triggers retry — not silent data loss
