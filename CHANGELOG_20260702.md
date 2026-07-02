Updated **TranzorExporter.exe** (v20260702)

What's new in 2 July update:

*Auditing a batch of translations with an LLM used to be a five-step chore: switch the format radio to JSON, switch the type to All Translations, export, find the file, then remember and type the exact skill prompt. One button now does all of it.*

- 🤖 **NEW: Send to LLM QA button** — Added next to *Export Selected* on **File Translation**, **MR Pipeline** and **Scan Tasks**. One click exports the selected task's **full-translation JSON** (the audit schema `/rc-core-products-trans-checker` expects) *regardless of the current format/type radios*, reveals the file, and **copies the LQA prompt** `/rc-core-products-trans-checker 检查附件JSON这批翻译的质量，重点关注 Critical 问题。` to the clipboard.
- 💬 **Clear hand-off** — A confirmation dialog tells you the JSON is exported and the prompt is on the clipboard, so the next move is obvious: open your LLM chat, upload the attachment, paste. If the clipboard write ever fails, the dialog shows the prompt for manual copy instead of silently doing nothing.
- 🧱 **Pure-additive & tested** — The shared logic (fixed prompt, clipboard helper, bilingual messages) lives in a standalone, unit-tested `llm_qa` module; the three tabs reuse their existing JSON export path unchanged, so nothing about the current export/format behaviour shifts.

---

*The three translation channels now present their task lists the same way.*

- 📊 **Unified task views + en-US Strings everywhere** — **File Translation**'s task list is no longer squeezed into the narrow right sidebar: it's now a **wide MR-Pipeline-style table** (#, Task ID, Task Name, Creator, Status, **en-US Strings**, Created) with the Platform stat kept in a compact sidebar and the export log as a slim bottom strip. **Scan Tasks** gains the same **en-US Strings** column. Clicking a row still fills the Task ID.
- 🔢 **en-US Strings = distinct source strings** — Like MR Pipeline, the count is distinct `opus_id` (the real linguist workload), not the strings × languages row count. Cells paint a `…` placeholder and fill in asynchronously so the list stays snappy; counts are cached per task.
- 🧱 **Pure-additive & tested** — New counters (`count_scan_source_strings`, `count_legacy_source_strings`) are unit-tested; row selection now keys off the row id (not a fixed column position), so it survives the column reshuffle.
