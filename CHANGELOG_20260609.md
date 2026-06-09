Updated **TranzorExporter.exe** (v20260609)

What's new in 9 June update:

*The app now adapts to Tranzor's new platform login — the API enforces server-side **Bearer-JWT** auth, so sign-in is required before any data loads.*

- 🔐 **Sign-in required — Tranzor 登录鉴权适配** — Tranzor's API now enforces Bearer-JWT auth, so the app prompts an **LDAP sign-in** on startup and attaches the token to platform calls only (GitLab untouched). The 7-day token is cached locally — **password is never stored** — with a 🔑 button to re-sign-in and an automatic retry on `401`.
- ✅ **NEW: Pre-Translation Check tab** — Import the l10n **Purchase delta XLSX** and see what Tranzor already translated before starting manual work: **🟢 skip · 🟡 review · 🔴 manual**. Offline-capable; export the manual subset or copy all 🔴 OPUS IDs.
- 📧 **Full Email tooling** — New userscripts for the Full Email viewer: one-click **bilingual PNG / Clean-view export** (no screenshot ghosting) and **synchronized dual-pane scrolling** (`Alt+S` to toggle).
- ✏️ **Better post-edit detection** — A new **"✏️ Post-edited only"** filter, a **~10× faster** probe (50-MR page ~56s → ~5s), a **source-branch scan** that catches UNS / `BATCH_FIX` fixes, and a **sticky-badge fix** on Search / Reset / Refresh.
- 🔢 **Sortable en-US Strings column** — MR Pipeline now shows the distinct en-US source-string count per task (the true workload), with click-to-sort on every header.
- 🩹 **Human Revisions by revision date** — Fixes on older tasks now surface; the Date filter means *revision* date, not task-creation date.
- 🛠️ **Send-to-Tranzor & export fixes** — Changes export opens the right MR task, All-translations ticks every selected row, and the Changes HTML export no longer hangs on a slow terminology service.
