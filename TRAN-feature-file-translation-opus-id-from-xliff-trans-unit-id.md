# [File Translation] Derive `opus_id` from XLIFF `<trans-unit id>` instead of `<file original>` path

| Field | Value |
|---|---|
| **Environment** | int (`tranzor-platform.int.rclabenv.com`) |
| **Sample task** | http://tranzor-platform.int.rclabenv.com/static/legacy/tasks/246 |
| **Task type** | File Translation (XLIFF source produced by [loki](https://git.ringcentral.com/) i18n tooling) |
| **Affected models / endpoints** | `Translation.opus_id`, `Translation.source_kvp`, `/api/v1/legacy/tasks/{id}/translations`, fix-translation `buildTarget` flow |
| **Severity** | Major (data-model semantics) — translation output itself is correct, but the row-level identifier is unusable for indexing, deduplication, fix-translation, TM/terminology backflow, and downstream audit tooling |
| **Type** | Improvement / Bug (depending on triage convention) |

## Summary

For File Translation tasks whose source is a loki-generated XLIFF 1.2 file, Tranzor is currently storing the XLIFF `<file original="…">` **file path** in `Translation.opus_id` for every row, while the genuine row-level stable identifier — the XLIFF `<trans-unit id="…">` attribute — is only surfaced in the UI's *Unit ID* column and is not promoted to `opus_id`.

This breaks the documented semantics of `opus_id` ("String ID (key)" in [`app/models/translation.py:22`](app/models/translation.py)) and diverges from the convention used by all other OPUS string-resource tasks (`opus_id = RingCentral.<namespace>.<identifier>.<hash>`, one row = one unique key per `(task_id, target_language)`).

We are asking Tranzor to switch the XLIFF ingestion so that:

- `opus_id` ← `<trans-unit>/@id`  (row-level stable key, OASIS-compliant)
- `source_file_path` / `source_kvp.sourceRelativePath` ← `<file>/@original`  (file path metadata)

so File Translation tasks expose the same `opus_id` contract as OPUS tasks.

## Background — loki is still actively used

Although the bulk of new strings now flow through the OPUS pipeline, several product teams (e.g. Service Web, ServiceWebJedi, MAA, ZoomEmailTemplates, ZoomWebPage, GwFunnels — see [`source/js/Loki/LocServiceAdapters/`](https://git.ringcentral.com/) in the loki repo) still extract resources via **loki** and submit the resulting **XLIFF 1.2** bundles to Tranzor as File Translation tasks. loki's XLIFF output places the per-string stable identifier in the standard XLIFF location `<trans-unit id>` (e.g. `['RingCX']`, `['AI Conversation Expert (ACE)']`, representing the underlying PHP/JS array key). Any Tranzor improvement here also benefits future formats that follow the same XLIFF convention (Android `strings.xml` via xliff resources, iOS `.stringsdict`, gettext PO-to-XLIFF, etc.).

## Current behavior (task 246)

XLIFF source attached (`delta_status-site_GWS-18065_26_05_26_fr-CA.xlf`):

```xml
<file original="translations/en_us/telus.php" source-language="en-US" target-language="fr-CA" datatype="plaintext">
  <body>
    <trans-unit id="[&apos;RingCX&apos;]">
      <source>RingCX</source>
      <target>RingCX</target>
    </trans-unit>
    <trans-unit id="[&apos;AI Conversation Expert (ACE)&apos;]">
      <source>AI Conversation Expert (ACE)</source>
      <target>AI Conversation Expert (ACE)</target>
    </trans-unit>
    <trans-unit id="[&apos;AI Receptionist (AIR)&apos;]">
      <source>AI Receptionist (AIR)</source>
      <target>AI Receptionist (AIR)</target>
    </trans-unit>
  </body>
</file>
```

Resulting rows on the task-detail page:

| # | Key (= `opus_id`) | Unit ID | Source | Translation |
|---|---|---|---|---|
| 1 | `translations/en_us/telus.php` | `['RingCX']` | RingCX | RingCX |
| 2 | `translations/en_us/telus.php` | `['AI Conversation Expert (ACE)']` | AI Conversation Expert (ACE) | Expert en conversation IA (ACE) |
| 3 | `translations/en_us/telus.php` | `['AI Receptionist (AIR)']` | AI Receptionist (AIR) | Réceptionniste IA (AIR) |

All three rows share the **same** `opus_id`. The composite uniqueness implied by the index [`ix_translations_task_opus_lang` on `(task_id, opus_id, target_language)`](app/models/translation.py:14) is effectively lost for this task type.

## Expected behavior

| # | Key (= `opus_id`) | source_file_path | Source | Translation |
|---|---|---|---|---|
| 1 | `['RingCX']` | `translations/en_us/telus.php` | RingCX | RingCX |
| 2 | `['AI Conversation Expert (ACE)']` | `translations/en_us/telus.php` | AI Conversation Expert (ACE) | Expert en conversation IA (ACE) |
| 3 | `['AI Receptionist (AIR)']` | `translations/en_us/telus.php` | AI Receptionist (AIR) | Réceptionniste IA (AIR) |

Each row carries a stable, unique `opus_id` within the task scope, semantically aligned with OPUS string-resource tasks. The file path is preserved on `source_file_path` / `source_kvp.sourceRelativePath` (fields that already exist on the `Translation` model).

## Evidence — Tranzor already expects this shape

From [`app/core/task_executor.py:439-449`](app/core/task_executor.py:439) (kvp normalization):

```python
# CLI format: {"key": "...", "value": "...", "pseudoHash": "...", "opusId": "..."}
# Service format: {"opusID": "...", "stringValue": "...", "source_kvp": {...}}
# ...
"opusID": kvp.get("opusId") or kvp.get("key", ""),
"source_kvp": kvp  # Preserve original kvp for buildTarget
```

The ingestion pipeline is already designed to take `opusId` (or fall back to `key`) from a kvp dict and write it to `opus_id`. The XLIFF-to-kvp adapter just needs to populate `key` with `<trans-unit>/@id` rather than `<file>/@original`.

From [`app/models/translation.py:22, 34-37`](app/models/translation.py:22):

```python
opus_id          = Column(String(500), nullable=False, index=True)  # String ID (key)
source_file_id   = Column(String(500), nullable=True)  # Source file ID for buildTarget
source_kvp       = Column(JSON,        nullable=True)  # ... includes key, pseudoHash, sourceRelativePath
source_file_path = Column(String(500), nullable=True)  # Source file relative path
```

`source_file_path` is the field intended for the file path; `opus_id` is intended for the row-level key.

## Why this matters — downstream impact

1. **Composite uniqueness is broken.** `ix_translations_task_opus_lang` no longer uniquely identifies a row for File Translation tasks; any `JOIN` or de-duplication keyed by `(task_id, opus_id, target_language)` collapses every entry into a single bucket per file.

2. **Fix-translation `buildTarget` is fragile.** [`app/api/routes/dashboard.py:904-908, 1281-1296`](app/api/routes/dashboard.py:904) reads `source_kvp.get("key", translation.opus_id)` as the lookup key for rebuilding the target file. When both `source_kvp.key` and `opus_id` are the file path, the build cannot disambiguate which line of the PHP/XLIFF to replace. The "missing source_kvp key" warning at line 908 is triggered for every row.

3. **Iteration history / re-translation lookups fail.** [`translation_pipeline.py:58, 103, 205-208, 274-281`](app/core/translation_pipeline.py:58) all use `opus_id` as the dictionary key when merging refined evaluations and iteration history into the final translations map — multiple rows with the same key will overwrite each other in memory.

4. **TM / terminology backflow misaligns.** Terminology hit lookups (`terms_map[opus_id]`) and context maps (`context_map[opus_id]`) at `translation_pipeline.py:62, 80` cannot scope hits to a specific string; every row in the file would receive the same terminology hits.

5. **External integrations.** Downstream tooling (`my-tools` exporters, SE-TQAS audit, error-fingerprint clustering, Tranzor-Bridge userscripts) all key off `opus_id` as the row identifier. Today they have to do a special-case fallback — concatenating Unit ID — for File Translation tasks. Aligning the key in Tranzor lets every downstream consumer use a single contract.

6. **UI usability.** The *Key* column repeats the same file path on every row, while the actually-distinguishing identifier sits in the secondary *Unit ID* column. Reviewers cannot sort, search, or copy-paste a meaningful row reference.

## Proposed change

In the File Translation XLIFF ingestion adapter (the code path that turns an uploaded `.xlf` into the `kvps` array consumed by `task_executor.py:_normalize_kvp` at line 446):

```python
# For each <file> in the XLIFF:
file_original = file_el.get("original", "")           # e.g. "translations/en_us/telus.php"
for tu in file_el.findall("./body/trans-unit"):
    trans_unit_id = tu.get("id", "")                  # e.g. "['RingCX']"
    source       = tu.findtext("source", "")
    target       = tu.findtext("target", "")

    kvps.append({
        "key":                trans_unit_id,          # → Translation.opus_id
        "opusId":             trans_unit_id,          # explicit, mirrors "key" for clarity
        "stringValue":        source,
        "sourceRelativePath": file_original,          # → Translation.source_file_path
        "pseudoHash":         sha1(source)[:10],      # optional, for parity with OPUS kvps
        # Preserve any XLIFF-only metadata (notes, datatype, resname, etc.) here
        # so buildTarget can losslessly reconstruct the file later.
    })
```

Implementation notes:

- **Backwards compatibility for existing rows.** Already-imported File Translation tasks (incl. task 246) carry the legacy file-path `opus_id`. Recommend either (a) a one-shot data migration that rewrites `opus_id` from `source_kvp` for File Translation tasks, or (b) leaving historical rows as-is and applying the new behavior only to tasks created after the fix lands. Option (b) is simpler and safe; downstream tools can detect "legacy file-path opus_id" via the heuristic `opus_id == source_file_path`.
- **Length budget.** `opus_id` is `String(500)`. XLIFF `trans-unit/@id` values from loki (`['key_name']`) are well under that, but defensive truncation + a 1× warning log is worth keeping.
- **Uniqueness defense.** If a malformed XLIFF contains duplicate `<trans-unit id>` within the same `<file>`, raise a validation error during ingestion rather than silently letting one row clobber another.
- **`source_kvp.sourceRelativePath` stays the file path** so the existing `buildTarget` write-back path keeps working.

## Acceptance criteria

1. Create a new File Translation task from the attached XLIFF (`delta_status-site_GWS-18065_26_05_26_fr-CA.xlf`) and confirm that each row's *Key* column shows the corresponding `<trans-unit/@id>` (e.g. `['RingCX']`), and that the *Source file path* (new or existing column / API field) shows `translations/en_us/telus.php`.
2. `SELECT count(*) FROM translations WHERE task_id = <new task> GROUP BY opus_id, target_language HAVING count(*) > 1` returns zero rows.
3. The `ix_translations_task_opus_lang` index can be relied upon as a real uniqueness lookup for File Translation tasks.
4. fix-translation buildTarget round-trip works on a File Translation task: edit one row, run buildTarget, diff the resulting PHP/XLIFF against the original — only the edited line changes.
5. The translations API response (`GET /api/v1/legacy/tasks/{id}/translations`) returns distinct `opus_id` per row, and `source_file_path` (or equivalent) carries the original file path.
6. No regression on existing OPUS string-resource tasks (opus_id continues to use the `RingCentral.<namespace>.<identifier>.<hash>` form).

## Out of scope

- Data migration of existing tasks' `opus_id` (recommended as a separate ticket if backfill is desired).
- Re-running translation on legacy File Translation tasks.
- UI redesign of the task detail page beyond the column content; widening / renaming columns may be a follow-up usability ticket.
- Changes to loki itself — loki already emits the correct XLIFF; the fix is purely on the Tranzor ingestion side.

## Attachments / References

- `delta_status-site_GWS-18065_26_05_26_fr-CA.xlf` — minimal reproducible XLIFF source (3 trans-units, fr-CA).
- Screenshot of task 246 *Translation Results* table showing collapsed Key column.
- Tranzor code references:
  - [`app/models/translation.py:14, 22, 34-37`](app/models/translation.py:14)
  - [`app/core/task_executor.py:439-449, 485-491`](app/core/task_executor.py:439)
  - [`app/core/translation_pipeline.py:58, 62, 80, 103, 205-208, 274-281`](app/core/translation_pipeline.py:58)
  - [`app/api/routes/dashboard.py:904-908, 1281-1296`](app/api/routes/dashboard.py:904)
- XLIFF 1.2 spec, §2.5.1 `trans-unit` — `id` is REQUIRED and "used to uniquely identify the `<trans-unit>` within all `<trans-unit>` and `<bin-unit>` elements within the same `<file>`".
