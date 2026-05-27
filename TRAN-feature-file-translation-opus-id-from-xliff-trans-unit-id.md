# [Tranzor] [File Translation] Use XLIFF `<trans-unit id>` as `opus_id` (align with OPUS key convention)

| Field | Value |
|---|---|
| Environment | int (`tranzor-platform.int.rclabenv.com`) |
| Sample task | http://tranzor-platform.int.rclabenv.com/static/legacy/tasks/246 |
| Source | loki-generated XLIFF 1.2 (`delta_status-site_GWS-18065_26_05_26_fr-CA.xlf`) |
| Severity | Major (data-model semantics; downstream consumers rely on `opus_id` being a row-level key) |

## Summary

For File Translation tasks fed by loki-generated XLIFF, every row's **Key** (i.e. `opus_id`) is currently the **file path** (`translations/en_us/telus.php`), while the genuine per-row stable identifier — the XLIFF `<trans-unit id>` — only surfaces in the *Unit ID* column.

All rows in a single file therefore share the same `opus_id`, which diverges from the convention used by OPUS string-resource tasks (one row = one unique key).

**Ask:** flip the two — promote `<trans-unit id>` to `opus_id`, keep the file path as file-path metadata. Implementation details are left to the Tranzor team.

## Background — loki is still in active use

Several product lines (e.g. Service Web, MAA, ZoomEmailTemplates, GwFunnels) still use **loki** to extract resources and submit XLIFF 1.2 bundles to Tranzor as File Translation tasks. loki already places the per-string stable identifier in the standard XLIFF location `<trans-unit id>` (e.g. `['RingCX']`, representing the underlying PHP/JS array key). Tranzor just needs to honor it.

## Current vs Expected

Source XLIFF (excerpt):

```xml
<file original="translations/en_us/telus.php" source-language="en-US" target-language="fr-CA">
  <trans-unit id="['RingCX']"><source>RingCX</source><target>RingCX</target></trans-unit>
  <trans-unit id="['AI Conversation Expert (ACE)']"><source>…</source><target>…</target></trans-unit>
  <trans-unit id="['AI Receptionist (AIR)']"><source>…</source><target>…</target></trans-unit>
</file>
```

| Row | Current `opus_id` (task 246) | Expected `opus_id` |
|---|---|---|
| 1 | `translations/en_us/telus.php` | `['RingCX']` |
| 2 | `translations/en_us/telus.php` | `['AI Conversation Expert (ACE)']` |
| 3 | `translations/en_us/telus.php` | `['AI Receptionist (AIR)']` |

The file path (`translations/en_us/telus.php`) should be preserved as file-path metadata, not as the row key.

## Why it matters

- **Row-level uniqueness.** Every downstream consumer (TM lookup, terminology hit, iteration history, fix-translation buildTarget, my-tools exporters, audit tooling) keys off `opus_id`. With all rows sharing one key, these collapse or misalign.
- **Cross-task consistency.** Aligns File Translation keys with the OPUS `RingCentral.<namespace>.<identifier>.<hash>` convention, so one contract works everywhere.
- **UI clarity.** The *Key* column actually distinguishes rows instead of repeating the same file path.

## Acceptance criteria

1. A new File Translation task created from the attached XLIFF has a distinct `opus_id` per row, equal to the corresponding `<trans-unit id>`.
2. `(task_id, opus_id, target_language)` is genuinely unique within a task.
3. The file path (`<file original>`) is still retrievable as metadata on each translation row.
4. fix-translation / buildTarget round-trip still works on the new format.
5. No regression on existing OPUS string-resource tasks.

## Out of scope

- Backfilling historical File Translation tasks (separate ticket if needed).
- Any change to loki — its XLIFF output is already correct.

## Attachments

- `delta_status-site_GWS-18065_26_05_26_fr-CA.xlf` — minimal repro (3 trans-units, fr-CA)
- Screenshot of task 246 *Translation Results* showing the collapsed Key column
