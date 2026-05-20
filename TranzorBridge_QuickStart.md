# Tranzor Bridge — Quick Start

> **5-minute guide.** Take rows you ticked in a TranzorExporter HTML report → walk through them on the Tranzor Platform tab with a fix-list sidebar.
> No more copy-key-then-search loop. No upstream code changes.

---

## What it does

| Before | After |
|---|---|
| Tick 8 rows in the HTML report → open Tranzor Platform → copy a `String Key` → paste into search → fix → repeat 7 more times | Tick rows → click `↗ Send to Tranzor` → sidebar appears on Tranzor with all 8 keys → `🔍 Find` fills the search box → fix → `✓ Fixed` → progress is saved |

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
2. Open <http://tranzor-platform.int.rclabenv.com> in a regular browser tab.
3. Look at the top-right of the page — you should see a collapsed green strip labeled **📋 Tranzor Bridge**.
4. Click it to expand. With no Exporter running, it will say `Waiting for selections from TranzorExporter…` — that's expected.

You only do steps 1–3 once. After that, every time TranzorExporter and a Tranzor Platform tab are open at the same time, they're paired automatically.

---

## Daily flow (4 clicks)

```
1. Launch TranzorExporter  →  2. Export task to HTML  →  3. Filter & tick rows
                                                                  ↓
                              5. Sidebar appears on Tranzor  ←  4. ↗ Send to Tranzor
                                                                  
```

### Step-by-step

1. **Launch the desktop app** (`TranzorExporter.exe` on Windows / `TranzorExporter.app` on macOS).
   - A line in the console reads `[bridge] listening on http://127.0.0.1:48217 instance_id=…` — that's the local bridge starting up.

2. **Export a translation report** as usual: enter a Task ID, choose `All Translations`, format `HTML`, click `▶ Start Export`. The HTML report auto-opens in your browser.

3. **Filter & tick.** Use the filter panel (top of the report) to narrow down to the problematic rows, then tick the checkboxes. The toolbar shows `Selected: N`.

4. **Click `↗ Send to Tranzor`** (the new green button next to `📦 Export TMX`).
   - A toast under the button confirms: `✓ Sent N item(s) via bridge (seq=…). Switching to Tranzor…`
   - Your Tranzor Platform tab opens (or refocuses if already open).

5. **Use the sidebar on Tranzor** (right edge of the page):

   ```
   ┌────────────────────────────────────────┐
   │ 📋 Tranzor Bridge  port 48217 · 3b3db0 │
   ├────────────────────────────────────────┤
   │ Task 216 · zh-TW · 1/8 fixed           │
   │                                        │
   │ ┌──────────────────────────────────┐   │
   │ │ settings.profile.title           │   │
   │ │ zh-TW · LLM Retranslate          │   │
   │ │ [🔍 Find] [✓ Fixed] [⤵ Skip]     │   │
   │ └──────────────────────────────────┘   │
   │ ┌──────────────────────────────────┐   │
   │ │ greet.hello   (struck through)   │   │
   │ │ ✓ Fixed                          │   │
   │ └──────────────────────────────────┘   │
   │ …                                      │
   └────────────────────────────────────────┘
   ```

   - **🔍 Find**: types the String Key into the Tranzor search box and dispatches Enter. The platform's own filter / list jumps to the matching entry.
   - **✓ Fixed**: marks the row done in your local progress (strikethrough). Toggle off to un-mark.
   - **⤵ Skip**: marks the row as skipped (won't count toward "fixed" progress, dims the row).
   - **Click the key text**: copies it to your clipboard (useful if `Find` didn't hit the right field for some reason).
   - **Click the header**: collapses / expands the sidebar.

6. **Fix the translation on Tranzor** as you normally would, then click `✓ Fixed` in the sidebar to track progress. Repeat for each key. Close the tab when done — progress is restored if you reopen it later.

---

## Troubleshooting

| Symptom | What's happening | Fix |
|---|---|---|
| Send button shows `⚠ Bridge unavailable… Copied to clipboard.` | The desktop app isn't running, or it crashed and `port.json` is stale | Make sure TranzorExporter is open. Then in the Tranzor sidebar, click the paste textarea at the bottom and `Ctrl+Shift+V` — the envelope ingests from clipboard. |
| Sidebar says "no bridge" even with TranzorExporter open | Bridge port is taken (10+ instances running, or another app on 48217–48226) | Restart TranzorExporter; if persistent, check the console for `BridgePortBusy`. The fallback transports (clipboard, URL hash) still work. |
| `🔍 Find` does nothing | Tranzor's search input selector doesn't match the default | Click the key text to copy it, then paste into Tranzor's filter manually. (We can teach the userscript a new selector — open an issue.) |
| Sidebar appears empty after I clicked Send | Userscript hasn't received the token yet. Look at the URL bar — does it contain `#tzbridge_token=…`? | If yes, refresh the page once; if no, send again from the report — the token is paired automatically on the next Send. |
| Send button is disabled (grey) | No rows are ticked, or all ticked rows are hidden by the current filter | Tick visible rows; the button enables as soon as `Selected: N` ≥ 1. |
| New rows from a fresh Send replaced my previous list | Single-slot inbox by design — each Send overwrites the prior fix-list | Finish a list before sending the next, or use the Mark Fixed/Skip state which persists per envelope ID even after replacement. |

---

## How it works (one paragraph)

When the GUI starts, it boots a tiny HTTP server on `127.0.0.1:48217` (or the next free port up to 48226) protected by a 32-byte random token. The HTML report contains the port and token, so its `↗ Send to Tranzor` button can POST the ticked rows to the bridge. The Tampermonkey userscript on the Tranzor Platform page polls the bridge every 3 seconds and renders the fix-list. The token is paired with the userscript via a one-time URL hash (`#tzbridge_token=…`) that's removed from the URL bar as soon as it's stored. Nothing leaves your laptop — the bridge is loopback-only and rejects all origins except `null`/`file://` (your report) and the Tranzor platform itself.

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
- `ROADMAP.md` line 130 — what's coming in v0.2 (per-key DOM targeting, `/ack` round-trip, deep-link probe)
