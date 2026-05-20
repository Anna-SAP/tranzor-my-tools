# Tranzor Bridge — Quick Start

> **5-minute guide.** Take rows you ticked in a TranzorExporter HTML report → land directly on Tranzor's task page with **the matching rows already highlighted in Tranzor's own list**, plus a small sidebar to walk through them.
> No more copy-key-then-search loop. No upstream code changes.

---

## What it does

| Before | After |
|---|---|
| Tick 8 rows in the HTML report → open Tranzor Platform → copy a `String Key` → paste into search → fix → repeat 7 more times | Tick rows → click `↗ Send to Tranzor` → the task page (`/static/legacy/tasks/<task_id>`) opens with the matching rows **already highlighted in Tranzor's own list**, plus a sidebar that lets you `🔍 Find` (scroll + flash) → fix → `✓ Fixed` → progress is saved |

---

## One-time setup (~2 min)

### 1. Install a userscript manager

| Browser | Recommended |
|---|---|
| Chrome / Edge / Brave | **Tampermonkey** (Chrome Web Store) |
| Firefox | **Tampermonkey** or **Violentmonkey** (Add-ons) |
| Safari | **Tampermonkey** (App Store) |

### 2. Install the Tranzor Bridge userscript

1. Go to the repo: <https://github.com/Anna-SAP/tranzor-my-tools/blob/master/userscript/tranzor_bridge.user.js>
2. Click **Raw** (top-right of the file view)
3. Tampermonkey will detect the userscript header and pop an install dialog — confirm
4. Verify the listed grants include:
   - `@match http://tranzor-platform.int.rclabenv.com/*`
   - `@connect 127.0.0.1`

### 3. Verify the sidebar mounts

1. Make sure you're on the corporate network / VPN (same as for using Tranzor Platform itself).
2. Open **any Tranzor task page** in a regular browser tab, e.g. `http://tranzor-platform.int.rclabenv.com/static/legacy/tasks/227` (replace `227` with a task you have access to). The bare domain is unreachable through Squid — always use a task-specific path.
3. Look at the top-right of the page — you should see a collapsed green strip labeled **📋 Tranzor Bridge**.
4. Click it to expand. With no Exporter running, it will say `Waiting for selections from TranzorExporter…` — that's expected.

You only do steps 1–3 once. After that, every time TranzorExporter and a Tranzor Platform tab are open at the same time, they're paired automatically.

---

## Daily flow (4 clicks)

```
1. Launch TranzorExporter  →  2. Export task to HTML  →  3. Filter & tick rows
                                                                       ↓
   5. Tranzor's own list shows green stripes on the rows  ←  4. ↗ Send to Tranzor
      you picked — sidebar is just the control panel
```

### Step-by-step

1. **Launch the desktop app** (`TranzorExporter.exe` on Windows / `TranzorExporter.app` on macOS).
   - A line in the console reads `[bridge] listening on http://127.0.0.1:48217 instance_id=…` — that's the local bridge starting up.

2. **Export a translation report** as usual: enter a Task ID, choose `All Translations`, format `HTML`, click `▶ Start Export`. The HTML report auto-opens in your browser.

3. **Filter & tick.** Use the filter panel (top of the report) to narrow down to the problematic rows, then tick the checkboxes. The toolbar shows `Selected: N`.

4. **Click `↗ Send to Tranzor`** (the new green button next to `📦 Export TMX`).
   - A toast under the button confirms: `✓ Sent N item(s) via bridge (seq=…). Switching to Tranzor…`
   - The browser opens **`http://tranzor-platform.int.rclabenv.com/static/legacy/tasks/<task_id>`** — i.e. Tranzor's own per-task page for the items you selected.
   - If the items span multiple tasks, the first task's page opens and the sidebar shows a `go to task N →` link for the others.

5. **The matching rows are already highlighted in Tranzor's own list.** Every selected `String Key` gets a green left stripe + soft green background directly on Tranzor's row — no need to search. The sidebar (right edge) is the control surface:

   ```
   ┌────────────────────────────────────────┐
   │ 📋 Tranzor Bridge  port 48217   ✕  »   │
   ├────────────────────────────────────────┤
   │ Task 227 · de-DE · 1/8 fixed  on task 227 │
   │ [👀 Highlighting on page]              │
   │                                        │
   │ ┌──────────────────────────────────┐   │
   │ │ settings.profile.title           │   │
   │ │ de-DE · LLM Retranslate          │   │
   │ │ [🔍 Find] [✓ Fixed] [⤵ Skip]     │   │
   │ └──────────────────────────────────┘   │
   │ ┌──────────────────────────────────┐   │
   │ │ greet.hello   (struck through)   │   │
   │ │ ✓ Fixed                          │   │
   │ └──────────────────────────────────┘   │
   │ …                                      │
   └────────────────────────────────────────┘
   ```

   - **`on task 227` badge** (green): you're on the right task page; on-page highlighting is active. If the badge is orange and reads `go to task 227 →`, click it to navigate.
   - **`👀 Highlighting on page`** toggle: turn the green stripes off if you find them visually noisy.
   - **🔍 Find**: scrolls to the row containing the String Key in Tranzor's own list and flashes it yellow for 2.4 s. (If the row isn't on the current page, falls back to filling Tranzor's search box, then to clipboard.)
   - **✓ Fixed**: marks the row done; the stripe on the page turns grey to show "already done" while you finish the rest.
   - **⤵ Skip**: marks the row as skipped (won't count toward "fixed" progress).
   - **Click the key text**: copies it to your clipboard.
   - **`»` toggle**: collapses / expands the sidebar (keeps highlights + progress).
   - **`✕` close**: hides the panel **and** clears all green stripes from Tranzor's rows. A small floating **📋** pill stays at the top-right so you can reopen it any time; sending a brand-new batch from the report also auto-reopens.

6. **Fix the translation in Tranzor's own row** as you normally would (use the platform's existing edit UI). Then click `✓ Fixed` in the sidebar to track progress — the row's stripe immediately turns grey on the page so you can see what's left. Close the tab when done; progress is restored when you reopen.

---

## Troubleshooting

| Symptom | What's happening | Fix |
|---|---|---|
| Send button shows `⚠ Bridge unavailable… Copied to clipboard.` | The desktop app isn't running, or it crashed and `port.json` is stale | Make sure TranzorExporter is open. Then in the Tranzor sidebar, expand the **Paste JSON from another report (advanced)** section at the bottom and `Ctrl+Shift+V` into the textarea — the envelope ingests from clipboard. |
| Sidebar says "no bridge" even with TranzorExporter open | Bridge port is taken (10+ instances running, or another app on 48217–48226) | Restart TranzorExporter; if persistent, check the console for `BridgePortBusy`. The fallback transports (clipboard, URL hash) still work. |
| `🔍 Find` does nothing | The row is on a different page of Tranzor's pagination, or its String Key isn't rendered as visible text | Click the key text to copy it, then use Tranzor's own search/pagination. The sidebar shows the orange `go to task → ` link when you're not on the matching task page. |
| Sent button opens a URL but Squid says `Name Error: The domain name does not exist` | You navigated to the bare `tranzor-platform.int.rclabenv.com` instead of a task URL | Make sure your envelope has a `task_id` (it always does when you exported from a single Task). Re-export if the Task ID column was empty. |
| Sidebar appears empty after I clicked Send | Userscript hasn't received the token yet. Look at the URL bar — does it contain `#tzbridge_token=…`? | If yes, refresh the page once; if no, send again from the report — the token is paired automatically on the next Send. |
| Send button is disabled (grey) | No rows are ticked, or all ticked rows are hidden by the current filter | Tick visible rows; the button enables as soon as `Selected: N` ≥ 1. |
| New rows from a fresh Send replaced my previous list | Single-slot inbox by design — each Send overwrites the prior fix-list | Finish a list before sending the next, or use the Mark Fixed/Skip state which persists per envelope ID even after replacement. |

---

## How it works (one paragraph)

When the GUI starts, it boots a tiny HTTP server on `127.0.0.1:48217` (or the next free port up to 48226) protected by a 32-byte random token. The HTML report contains the port and token, so its `↗ Send to Tranzor` button can POST the ticked rows to the bridge and navigate to `/static/legacy/tasks/<task_id>`. The Tampermonkey userscript on that page polls the bridge every 3 seconds, ingests the envelope, and walks Tranzor's own DOM with a `TreeWalker` text scan to find every selected `String Key` — each matched row gets the green stripe + soft background. The control sidebar is just a thin layer on top. The token is paired with the userscript via a one-time URL hash (`#tzbridge_token=…`) that's removed from the URL bar as soon as it's stored. Nothing leaves your laptop — the bridge is loopback-only and rejects all origins except `null`/`file://` (your report) and the Tranzor platform itself.

---

## Privacy & security at a glance

- **Loopback only**: the bridge binds `127.0.0.1`, never `0.0.0.0` — invisible to your network.
- **Fresh token every launch**: closing and reopening the desktop app rotates the token; stale reports gracefully fall back to clipboard.
- **No upstream calls**: the userscript talks to your local bridge and your Tranzor session cookie does all the auth on the platform side. The bridge never touches Tranzor's API.
- **Discovery file**: `~/.tranzor_bridge/port.json` (Unix `chmod 600`) is created on launch and deleted on close.

---

## See also

- `TranzorExporter_QuickStart.md` — main desktop app guide
- `tranzor_bridge.py` — bridge server source (~250 lines, stdlib only)
- `userscript/tranzor_bridge.user.js` — userscript source
- `ROADMAP.md` — "Tranzor Bridge" row (currently marked ✅ v0.1) and the adjacent "翻译审校工作流" / "批量重译与引导" rows for upstream-dependent v0.2 ideas
