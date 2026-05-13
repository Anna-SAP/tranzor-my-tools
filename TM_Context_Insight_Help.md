# 🔬 TM & Context Insight — User Guide

> Applies to the **🔬 TM & Context Insight** tab in TranzorExporter v2.0+

---

## What does this panel do?

When Tranzor Platform produces a translation, it actually runs through a **pipeline**:
sometimes it reuses an existing translation from the memory store (cheapest path);
sometimes it reuses a translation from an earlier project pass;
sometimes it calls the LLM to translate fresh; sometimes it first fetches context
evidence and *then* calls the LLM.

Previously this pipeline was a **black box** to language experts — you only saw
the final translation, not which path produced it.

**This panel opens the black box** so you can see at a glance:

- Whether each translation came from **TM / ICE / Cached / LLM / Refined / Human**
- Whether the LLM received **Context Service** evidence when translating
- Proportions per target language, and drill-down per row

---

## 30-second quick start

1. Open TranzorExporter, click the rightmost tab **「🔬 TM & Context Insight」**
2. The panel fetches the **last 30 days** of MR Pipeline translations. Wait a few seconds for `✓ Loaded (N)`
3. Look at the **aggregate** (upper region), **rows** (lower region), and **KPIs** (right sidebar)
4. **Double-click any row** → drawer pops up showing the context snippet the LLM received for that translation

---

## Three regions, explained

### 1️⃣ Upper region · Aggregate panel

#### "Translation Source Composition"

Each row is one target language. The stacked bar shows the proportion of each source for that language.

| Column | Meaning |
|---|---|
| **Language** | Target language (e.g. `zh-CN`, `fr-FR`) |
| **Bar** | Stacked bar — left to right: TM → ICE → Cached → LLM → Refined → Human |
| **TM** | Hit from **Translation Memory** — previously-approved identical sentence, reused for free |
| **ICE** | **In-Context Exact match** — identical translation found in the same project's history |
| **Cached** | Hit from Tranzor's short-term cache (same source string was translated in a recent task) |
| **LLM** | Fresh LLM translation (the most common case) |
| **Refined** | Initial LLM output scored low; system re-ran a second pass |
| **Human** | Translation was edited by a Language Lead via the Dashboard |
| **Total translations** | Total translations for that language in this batch |

**How to read it**: if `zh-CN` shows LLM=140 and TM=0, this batch had zero TM reuse for zh-CN — everything was a fresh LLM translation.

#### "Context Service Coverage"

Same per-language grouping, but shows how many translations carried context evidence.

| Bucket | Meaning |
|---|---|
| **Ctx✓** / **With context** | LLM received meaningful context evidence (ideal) |
| **partial** | Has a `context_id` but Context Service returned no substantive content |
| **NoCtx** / **No context** | No context at all (either Context Service isn't enabled, or no matching context for this string) |

**How to read it**: if "No context" dominates for a language, the LLM was largely translating *blind* — those translations may need extra review.

---

### 2️⃣ Lower region · Recent translations table

One row per translation, capped at 500 rows. Focus on the **Badges** column.

#### Badge dictionary

| Badge | Meaning |
|---|---|
| **TM** | From the Translation Memory |
| **ICE** | From this project's historical translations |
| **Cached** | From Tranzor's task-level cache |
| **LLM** | Fresh LLM translation |
| **Refined×N** | Re-run N times (iteration ≥ 2) |
| **Human** | Modified by a Language Lead in the Dashboard |
| **Ctx✓** | LLM received context evidence |
| **Ctx◐** | Has context_id but content was empty/weak |
| **NoCtx** | No context at all |

> 💡 A row can carry multiple badges. E.g. `TM  Refined×2  Ctx✓` means: TM hit first, but score was low so it was refined twice, and context was attached during translation.

#### Actions

- **Double-click any row** → opens the "Context Snippet" drawer; it asynchronously fetches the context JSON used for that translation (`moduleName`, `fileName`, `usage`, `placeholders`, etc.)
- If a row's badge shows **NoCtx**, the drawer will note "This translation has no context_id"

---

### 3️⃣ Right sidebar · Pipeline Routing KPIs

Nine numbers at a glance:

| KPI | Meaning |
|---|---|
| **Total** | Total translations in the current filter |
| **TM hits** | Number reused from TM |
| **ICE hits** | Number reused from ICE |
| **Cached** | Number reused from cache |
| **LLM (fresh)** | Fresh LLM translations (not refined) |
| **Refined (iter ≥ 2)** | Translations that were refined at least once |
| **Human-fixed** | Translations edited by humans |
| **With context** | Translations that carried substantive context |
| **No context** | Translations with no context at all |

**How to use it**:
- **Glance at KPIs weekly** to see if TM/ICE hits are growing (stagnation = TM isn't learning new content)
- **Compare With context vs No context** to judge whether Context Service is actually serving the projects you care about

---

## Filters

| Field | Usage |
|---|---|
| **Project** | Empty = all projects; type a project ID (e.g. `CoreLib/mthor`, `web/chc`) to filter |
| **Language** | Empty = all languages; type a language code (e.g. `zh-CN`, `fr-FR`) to filter |
| **From / To** | Date range. Default = last 30 days. Click 🔍 Refresh after changing |
| **Reset** | Restores default range and clears filters |

---

## FAQ

### Q1: What's the difference between TM, ICE, and Cached?

| Concept | Analogy |
|---|---|
| **TM (Translation Memory)** | A translation agency's accumulated "approved translation memo" — the same sentence was approved before, reuse it directly |
| **ICE (In-Context Exact)** | Limited to the **same product project** — the same String Key was translated here before |
| **Cached** | Tranzor's short-term Redis cache — the same source string was translated in a recent task, reuse it as-is |

Priority order: TM > ICE > Cached > fresh LLM. Whichever hits first wins, skipping the LLM call to save cost.

### Q2: What is Context Service and why does it matter?

Context Service is Tranzor's "**context researcher**": before the LLM translates, it scans the source code, finds **how this String Key is actually used** in the product (which module, which file, which placeholders, whether the UI is a button or a heading), and packages it as context evidence for the LLM.

LLM with context evidence → more accurate translation. Without it → ambiguity errors creep in. That's why **Ctx✓** is a good signal.

### Q3: Is "Refined×2" good or bad?

**Both sides**:
- ✅ Good: the system detected low initial quality and re-ran to improve the score (the quality gate is working)
- ⚠️ Bad: high refinement counts mean initial quality is generally poor — Context Service may not be working, or the LLM model choice may be wrong

If any language has a Refined ratio > 20%, worth flagging to the Tranzor team.

### Q4: How is the "Human-fixed" badge related to the Human Revisions tab?

- **Human-fixed badge here**: edited via Dashboard's "Language Lead Fix" flow
- **Human Revisions tab**: lists **all** human edits (fix-translation + adopted retranslate-preview), broader coverage

For full edit history and diffs, go to the **Human Revisions tab**. For a quick "is this row touched?" check, the badge here is enough.

### Q5: Why is File Translation data missing?

v1 covers **MR Pipeline only** (dashboard/cases data source). File Translation (Legacy) has a slightly different schema — no `tm_match` field, because Legacy currently doesn't go through TM.

To see translation source distribution for File Translation, **today's workaround**: use the **Full Translations** tab to view per-row `translation_type` (LLM / Cached / Manual Edit / LLM Retranslate).

### Q6: What if "No context" rate is too high?

Possible causes:
1. The project isn't onboarded to Context Service yet
2. These String Keys can't be found in source code (new strings, code not committed)
3. Context Service request timed out or failed at fetch time

**Action**: if a project shows > 80% "No context", flag it to the Tranzor platform team to check onboarding status.

---

## Known limitations (v1)

- **MR Pipeline only**: File Translation / Scan Tasks are out of scope
- **500-row table cap**: narrow the date range if you need to see more
- **Context snippets fetched on demand**: only on row double-click, to avoid N requests at tab open (1-2s delay on first double-click is normal)
- **No time series**: snapshot view only, no trend lines. Add if there's demand

---

## Feedback / requests

[GitHub Issues — Anna-SAP/tranzor-my-tools](https://github.com/Anna-SAP/tranzor-my-tools/issues)

Or contact the TranzorExporter maintainer directly.
