# [UNS] Source column on task detail page shows truncated preview without any indicator

| Field | Value |
|---|---|
| **Environment** | int (`tranzor-platform.int.rclabenv.com`) |
| **Sample task** | http://tranzor-platform.int.rclabenv.com/static/legacy/tasks/244 |
| **Source package** | `LOC-24627.zip` (attached) |
| **Severity** | Major — does not corrupt translation output, but makes reviewers believe the source is incomplete |

## Summary

On a UNS task detail page, the **Source** column in the Translation Results table shows only the first ~500 characters of each source unit, with no indicator that the text is truncated. Reviewers cannot tell that what they see is a preview rather than the actual source extracted by Tranzor, and reasonably conclude that source extraction has lost content.

## Steps to reproduce

1. Upload the attached `LOC-24627.zip` as a new UNS task (target languages: es-ES, fr-FR).
2. Wait for translation to finish, open the task detail page (task 244 in our case).
3. Switch to either es-ES or fr-FR tab and look at the **Source** column.

## Expected

The Source column either shows the full source, or shows a preview with a clear "truncated / N chars total / view full" indicator in the same column.

## Actual

The Source column shows text that ends abruptly (e.g. `...{{#txt "headerTitle"}}Welco`) with no ellipsis badge, no length info, no "show more" affordance, and no tooltip — looking identical to a real extraction failure. See attached screenshot.

## Evidence — source extraction itself is correct

Verified via `/api/v1/legacy/tasks/244/translations/{id}/full-text` against the original `source.json` inside the zip:

| translation_id | lang | Tranzor DB length | Zip file length | SHA-256 match |
|---|---|---|---|---|
| 590072 | es-ES | 2479 | 2479 | yes |
| 590073 | es-ES | 2549 | 2549 | yes |
| 590074 | es-ES | 2413 | 2413 | yes |
| 590075 | fr-FR | 2479 | 2479 | yes |
| 590076 | fr-FR | 2549 | 2549 | yes |
| 590077 | fr-FR | 2413 | 2413 | yes |

Translated output (`translated_text`) also contains content that appears "missing" in the Source column preview (e.g. `{{partial "footerTwoLogoAndTosAndEula"}}`, closing `</html>`), confirming the translation engine used the complete source.

Conclusion: the data is intact end-to-end; only the on-screen display is misleading.

## Impact

- All UNS tasks whose source unit length exceeds the preview limit (effectively every email template).
- No effect on translation correctness or downloaded output.
- High risk of false-positive bug reports and loss of trust in the platform's data integrity.
- Slows down LQA / TQA review because reviewers cannot compare source vs. translation without extra clicks per row.

## Attachments

- `LOC-24627.zip` — source package used to reproduce
- Screenshot of task 244 Source column vs. original `source.json`
