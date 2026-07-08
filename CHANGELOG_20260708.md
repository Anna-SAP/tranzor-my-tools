Updated **TranzorExporter.exe** (v20260708)

What's new in 8 July update:

*The daily Full Translation export failed this morning with "No translations matched the selection" — but nothing was wrong with the selection. The platform's 7-day sign-in token had expired at 10:18, every API call was answering 401 Unauthorized, and the exporter's "warn and continue" error handling quietly aggregated zero rows and blamed the data. A login problem must never masquerade as a data problem.*

- 🔑 **Sign-in preflight on Full Translation Export** — *Refresh Inventory*, *Export Selected / Export All* and *Merge to JSON* now verify the platform token **before** touching the network. If the 7-day token is expired, the 🔑 sign-in dialog opens right away; after signing in, the action proceeds normally. No more burning minutes on a doomed run.
- ⛔ **401 fails fast, loudly and honestly** — A 401 during the run (token expiring mid-export, revoked token) now aborts immediately with a dedicated `AuthRequiredError` instead of warning per-source and exporting nothing. The GUI closes the progress dialog, explains "平台登录已过期（令牌有效期 7 天）——请重新登录后继续。", opens the sign-in dialog, and **automatically retries the same export** (same file, same selection) once you're signed in.
- ⚡ **No wasted retries on a dead token** — The per-task retry/backoff loop (up to 4 attempts, ~10s of exponential backoff each) now recognises 401 as unretryable and stops on the first attempt. Non-auth failures (timeouts, connection blips) keep the existing retry + strict-completeness behaviour unchanged.
- 🧱 **Pure-additive & tested** — 14 new unit tests (`test_export_auth_failfast.py`) pin the contract: structured + message-based 401 detection (with false-positive guards), fail-fast in the retry helper, list-level and per-task propagation, and warn-and-continue preserved for non-auth errors. All existing export/auth tests still pass.
